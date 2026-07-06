"""Code generation: walks the AST and emits LLVM IR with llvmlite.ir.

LLVM integer types carry no signedness -- `i32` is just 32 bits -- so the
distinction between int32 and uint32 lives here, in which instructions get
emitted (sdiv/udiv, signed/unsigned compares, sext/zext). `LangType` tracks
the source-level type alongside the LLVM type, and every expression evaluates
to a `TypedValue` pairing the LLVM value with its `LangType`.

Structs use LLVM identified types, instantiated (and cached) per set of type
arguments, exactly like generic functions monomorphize.
"""

from __future__ import annotations

import re
from dataclasses import fields as dataclass_fields
from dataclasses import is_dataclass
from dataclasses import replace as dataclasses_replace

from llvmlite import ir

from mcc.errors import LangError, Note
from mcc.nodes import (
    AlignOf,
    ArrayLit,
    Asm,
    Assign,
    Binary,
    Block,
    BlockExpr,
    BoolLit,
    Break,
    Call,
    CallExpr,
    Case,
    CaseType,
    Cast,
    CharLit,
    CompoundAssign,
    Conditional,
    Const,
    Continue,
    Defer,
    Emit,
    EnumAccess,
    EnumDecl,
    ErrorDirective,
    ExprStmt,
    FloatLit,
    For,
    Func,
    GlobalVar,
    If,
    Import,
    Index,
    IntLit,
    Let,
    Logical,
    Len,
    Member,
    NonnullAssert,
    NullLit,
    OffsetOf,
    Program,
    Return,
    SizeOf,
    StaticAssert,
    StoreDeref,
    StoreIndex,
    StoreMember,
    StrLit,
    StructDecl,
    StructLit,
    Ternary,
    TypeAlias,
    TypeRef,
    Unary,
    Var,
    While,
)

from mcc.codegen.ir_ext import VolatileLoad, VolatileStore
from mcc.codegen.targets import (
    compute_target_facts,
    eval_static_cond,
    eval_static_value,
    target_fact_values,
)
from mcc.codegen.types import (
    ANY,
    BOOL,
    CHAR,
    CHARPTR,
    COMPARISON_OPS,
    FLOAT64,
    I32_ZERO,
    INT32,
    NULLT,
    RAWPTR,
    RESERVED_TYPE_NAMES,
    TYPES,
    UINT8,
    UINT64,
    VOID,
    Alias,
    BlockExprCtx,
    EnumType,
    LangType,
    TypedValue,
    adaptable_int,
    const_of,
    field_offset,
    fnv1a64,
    fold_int_arithmetic,
    fold_untyped_shift,
    function_type,
    is_any,
    is_array,
    is_flexible_array,
    is_function,
    is_integer,
    is_pointer,
    is_slice,
    is_struct,
    is_union,
    is_valist,
    list_of,
    over_aligned,
    pointer_to,
    strip_const,
    type_align,
    type_size,
    wider_int_type,
    widen_to,
    wrap_int,
    _host_triple,
)


def collect_addr_taken(obj, names: set[str]) -> None:
    """Accumulate every name whose address is taken (``&name``) into ``names``.

    Recurses through dataclass fields and lists, so one pass over a function
    body (``defer`` bodies included) finds every ``&x`` on a bare variable.
    Flow-narrowing uses the result as a blanket ban: once a local's address
    exists, a stored pointer could null the variable without ever naming it,
    so per-site invalidation cannot be sound -- the name is simply never
    narrowable anywhere in the function.

    Args:
        obj: An AST node, list, or scalar to scan.
        names: The set to add address-taken names to, in place.
    """
    if isinstance(obj, Unary) and obj.op == "&" and isinstance(obj.operand, Var):
        names.add(obj.operand.name)
        return
    if is_dataclass(obj):
        for f in dataclass_fields(obj):
            collect_addr_taken(getattr(obj, f.name), names)
    elif isinstance(obj, (list, tuple)):
        for item in obj:
            collect_addr_taken(item, names)


def contains_break(obj) -> bool:
    """Whether a loop body contains a ``break`` targeting *this* loop.

    Recurses like :func:`collect_addr_taken` but does not descend into
    nested ``while``/``for`` loops -- a ``break`` inside one targets the
    inner loop, never this one. Used to gate post-exit narrowing: a loop's
    normal exit leaves from its condition, but a ``break`` jumps straight
    to the end block without re-testing it.

    Args:
        obj: An AST node, list, or scalar to scan.

    Returns:
        ``True`` when a ``break`` can exit the loop being scanned.
    """
    if isinstance(obj, Break):
        return True
    if isinstance(obj, (While, For)):
        return False  # its breaks are the inner loop's
    if is_dataclass(obj):
        return any(
            contains_break(getattr(obj, f.name)) for f in dataclass_fields(obj)
        )
    if isinstance(obj, (list, tuple)):
        return any(contains_break(item) for item in obj)
    return False


# Builtin struct templates, available in every program with no import: the
# shared `iterator<T>` cursor behind the `_it`/`_next` protocol, and the
# `pair<K, V>` element the keyed containers yield from `_next`. They are
# ordinary (not reserved) names -- a user struct with the same name takes
# precedence, exactly as a user-defined `range` function shadows the builtin
# counting loop.
BUILTIN_STRUCTS = (
    # A forward cursor over a container of type T: a borrowed pointer to the
    # container and the index of the next slot to yield, so the container must
    # outlive the iterator and must not be resized while iterating.
    StructDecl(
        name="iterator",
        type_params=["T"],
        fields=[("obj", TypeRef("T", stars=1)), ("idx", TypeRef("uint64"))],
        line=0,
    ),
    # A key/value pair -- what `set_next`/`dict_next` fill per occupied entry.
    StructDecl(
        name="pair",
        type_params=["K", "V"],
        fields=[("key", TypeRef("K")), ("value", TypeRef("V"))],
        line=0,
    ),
    # What the builtin `enumerate(obj)` yields per element: the running
    # position and the element `obj`'s `_next` produced.
    StructDecl(
        name="enumerated",
        type_params=["T"],
        fields=[("index", TypeRef("uint64")), ("value", TypeRef("T"))],
        line=0,
    ),
)


class CodeGen:
    """Lowers a merged ``Program`` to an LLVM IR module.

    Walks the AST and emits IR with ``llvmlite.ir``. Generic functions and
    structs are monomorphized on first use and cached; ``@static`` and
    ``@private`` declarations are tracked per source file for name scoping and
    access checks; and ``import``-reached or monomorphized definitions get
    mergeable linkage so identical copies collapse at link time. The many
    instance attributes set up in :meth:`__init__` hold this bookkeeping and are
    documented inline there.
    """

    def __init__(
        self,
        program: Program,
        name: str,
        root_source: str | None = None,
        target: str | None = None,
        defines: dict[str, int] | None = None,
    ):
        """Initialize the code generator.

        Args:
            program: The merged program to compile.
            name: The LLVM module name, typically the entry file's name.
            root_source: Resolved path of the entry file; its own definitions
                keep external linkage while imported ones become mergeable.
                ``None`` for a single-module JIT or the test helpers.
            target: The LLVM target triple, or ``None`` for the host; fixes the
                ``va_list`` layout.
            defines: Command-line ``-D`` names mapped to integer values, made
                available to ``@if`` conditions alongside the target facts.
        """
        self.program = program
        # Target triple (None = host); fixes the platform va_list layout.
        self.target = target
        # -D NAME[=VALUE] command-line defines, visible only in @if conditions.
        self.defines = defines or {}
        # va_list is platform-specific, so it is built lazily on first use.
        self.va_list_type: "LangType | None" = None
        self.va_list_passed_ir = None  # IR type a va_list takes as an argument
        self.va_list_align = 8
        self.va_list_supported = True  # False on a target with no known layout
        self.va_list_arch = ""
        self.current_variadic = False  # is the function being generated variadic?
        # The entry file's resolved path. Definitions from it are this
        # translation unit's own (external linkage); everything reached through
        # `import` is shared and gets merged across objects (see def_linkage).
        self.root_source = root_source
        # A private context so identified struct types don't collide across
        # separate compilations in one process (llvmlite defaults to a
        # global context).
        self.module = ir.Module(name=name, context=ir.Context())
        self.funcs: dict[str, ir.Function] = {}
        # name -> (return type, param types, variadic)
        self.signatures: dict[str, tuple[LangType, list[LangType], bool]] = {}
        # symbol -> indices of params passed by hidden reference (const
        # structs and mut parameters).
        self.hidden_ref: dict[str, frozenset[int]] = {}
        # symbol -> the subset of hidden_ref indices that are mut: the
        # argument must be the caller's own storage (never a spilled
        # temporary), so writes through the reference reach the caller.
        self.mut_ref: dict[str, frozenset[int]] = {}
        # symbol -> indices of @nonnull pointer parameters: every call site
        # must prove the argument non-null (a checked refinement, unlike the
        # unchecked @noalias promise).
        self.nonnull_ref: dict[str, frozenset[int]] = {}
        # Names of the current function's const (read-only) parameters.
        self.const_locals: set[str] = set()
        # Names of the current function's mut (write-through) parameters.
        self.mut_locals: set[str] = set()
        # Names of the current function's @nonnull parameters -- per-binding
        # non-null facts, so forwarding one to another @nonnull slot needs no
        # proof. A shadowing `let` drops the name (see bind_local).
        self.nonnull_locals: set[str] = set()
        # Plain pointer locals flow-narrowed to non-null by a null-check guard
        # (`if (p != null)` / a diverging `if (p == null)`). Unlike
        # nonnull_locals these bindings stay reassignable and addressable --
        # the fact is just dropped on any event that could null the variable
        # (see narrowable_guard_names for the invalidation rules).
        self.narrowed_nonnull: set[str] = set()
        # Names whose address is taken (&x) anywhere in the current function's
        # body -- such locals are never narrowable (a stored pointer could
        # null them without naming them).
        self.addr_taken: set[str] = set()
        # Generic functions: a name maps to its overload set, distinguished
        # by parameter patterns (e.g. hash<T>(T) vs hash<T>(T*)).
        self.templates: dict[str, list[Func]] = {}
        self.template_bases: dict[int, str] = {}  # id(Func) -> mangle base
        # (id(template Func), bound types) -> mangled instance name
        self.instances: dict[tuple[int, tuple[str, ...]], str] = {}
        self.struct_templates: dict[str, StructDecl] = {}
        self.struct_types: dict[str, LangType] = {}  # mangled name -> instance
        # Enums: name -> EnumType. @static enums are file-scoped, keyed by
        # (source, name) so other files may reuse the name (like @static structs).
        self.enums: dict[str, EnumType] = {}
        self.static_enums: dict[tuple[str | None, str], EnumType] = {}
        # Type aliases: name -> Alias (transparent; resolved lazily). @static
        # aliases are file-scoped, like @static structs/enums.
        self.type_aliases: dict[str, Alias] = {}
        self.static_type_aliases: dict[tuple[str | None, str], Alias] = {}
        # Alias names currently being resolved, to catch a cyclic alias chain.
        self.resolving_aliases: set[str] = set()
        # Mangled names whose `extends` base is currently being resolved, so a
        # cyclic `extends` (A extends B extends A) is caught instead of looping.
        self.resolving_bases: set[str] = set()
        # @static declarations: file-scoped names, keyed by (source, name)
        self.static_funcs: dict[tuple[str | None, str], str] = {}  # -> symbol
        self.static_templates: dict[tuple[str | None, str], Func] = {}
        self.static_structs: dict[tuple[str | None, str], StructDecl] = {}
        self.symbol_bases: dict[
            tuple[str | None, str], str
        ] = {}  # static name mangling
        self.used_symbols: set[str] = set()
        # @extern declarations refer to symbols defined elsewhere; identical
        # redeclarations across files collapse onto the first one.
        self.extern_decls: set[str] = set()
        # The AST node behind each concrete (non-generic, non-@static,
        # non-@extern) function in self.funcs, keyed by name and then by the
        # signature's params-key -- the canonical `str(LangType)` join over
        # the parameter types, the mangled symbol's interior. A later
        # prototype or definition pairs against the entry with its own
        # params-key (forward declarations, per signature): the entry's
        # .proto flag says whether a bodyless prototype or a definition
        # currently claims that signature.
        self.concrete_decls: dict[str, dict[str, Func]] = {}
        # Concrete overload sets: a name declared with two or more distinct
        # parameter lists in one module maps to its member Func nodes. Each
        # member's ir.Function lives in self.funcs under a signature-derived
        # mangled symbol (`f(int32, char*)`); overload_symbols maps the
        # member back to it. A name with a single signature never appears
        # here -- it keeps its plain, C-linkable symbol and the direct-call
        # fast path.
        self.overloads: dict[str, list[Func]] = {}
        self.overload_symbols: dict[int, str] = {}  # id(Func) -> mangled symbol
        self.globals: dict[str, tuple[ir.GlobalVariable, LangType, bool]] = {}
        # @static globals are file-scoped storage, keyed by (source, name) so
        # other files may reuse the name -- like @static functions.
        self.static_globals: dict[
            tuple[str | None, str], tuple[ir.GlobalVariable, LangType, bool]
        ] = {}
        # name -> (private, source file); for @private access checks
        self.global_privacy: dict[str, tuple[bool, str | None]] = {}
        # Named compile-time constants: name -> folded TypedValue (no storage is
        # emitted; references are substituted). Used as values and array sizes.
        self.consts: dict[str, TypedValue] = {}
        self.const_privacy: dict[str, tuple[bool, str | None]] = {}
        self.type_bindings: dict[str, LangType] = {}  # active type-parameter bindings
        # `any` tags handed out this compilation: tag -> canonical type name.
        # The tag is a hash (see fnv1a64), so two distinct names colliding on
        # one tag would corrupt every `case type`; recording each tag's name
        # turns a collision into a compile error at the second type's site.
        self.any_tags: dict[int, str] = {}
        # name -> (private, source file); for @private access checks
        self.func_privacy: dict[str, tuple[bool, str | None]] = {}
        # @deprecated("msg") on concrete functions: resolved symbol -> the
        # migration message, warned at every call site and function-value use.
        # (Generic templates carry the message on the Func node instead and
        # are checked when overload resolution picks them.)
        self.deprecated_syms: dict[str, str] = {}
        # @removed("msg") tombstones: function name -> the migration message.
        # Name-keyed and deliberately unresolved: a tombstone's signature is
        # never type-checked and gets no ir.Function (every use errors before
        # lowering, and the tombstone must stay valid even when its parameter
        # types were deleted along with the implementation).
        self.removed: dict[str, str] = {}
        self.current_source: str | None = None  # file owning the code being generated
        # Non-fatal diagnostics collected during generation, in emission order.
        # The driver prints them after generation succeeds (see warn()).
        self.warnings: list[Note] = []
        self.builder: ir.IRBuilder | None = None
        self.locals: dict[str, tuple[ir.AllocaInstr, LangType]] = {}
        self.scope_names: set[str] = set()  # names declared in the current block
        # One list of deferred action bodies per active block scope (innermost
        # last); each runs in LIFO order when its block exits.
        self.defer_stack: list[list[list]] = []
        self.ret_type: LangType = VOID
        # Enclosing loops, innermost last: (continue target, break target).
        self.loops: list[tuple[ir.Block, ir.Block]] = []
        # Enclosing block-expressions, innermost last; each `emit` targets the
        # last. See BlockExprCtx.
        self.block_exprs: list[BlockExprCtx] = []
        self.str_count = 0

    def warn(self, message: str, line: int) -> None:
        """Record a non-fatal diagnostic on the warning channel.

        The warning is stamped with :attr:`current_source` at emission time
        (unlike errors, whose source is filled in as they unwind), collected on
        :attr:`warnings` in emission order, and never aborts generation. The
        driver prints the list as ``file: warning: line N: message`` lines
        after generation succeeds; warnings collected before a hard compile
        error are dropped with the failed build.

        Args:
            message: The diagnostic text, reported verbatim.
            line: The 1-based source line the warning refers to.
        """
        self.warnings.append(Note(message, line, self.current_source))

    def warn_deprecated(self, name: str, msg: str | None, line: int) -> None:
        """Warn that a ``@deprecated`` function was resolved, when ``msg`` is set.

        The single formatter for deprecation warnings: every resolution point
        (direct call, generic overload pick, function value, for-in protocol)
        funnels through here, so the ``'name' is deprecated: msg`` wording is
        emitted uniformly. There is no suppression -- a call from another
        deprecated function warns too; the driver deduplicates repeats of one
        call site (e.g. per-instantiation re-emissions) at print time.

        Args:
            name: The name the caller used, reported repr-quoted.
            msg: The migration message, or ``None`` for "not deprecated"
                (a no-op, so lookups can be passed straight in).
            line: The call site's 1-based source line.
        """
        if msg is not None:
            self.warn(f"{name!r} is deprecated: {msg}", line)

    def check_removed(self, name: str, line: int) -> None:
        """Error when ``name`` is an ``@removed`` tombstone.

        The single formatter for tombstone errors: every name-resolution
        point (call, function value, for-in protocol) funnels through here,
        so the ``'name' was removed: msg`` wording is emitted uniformly. The
        check runs after variable/const and file-scoped ``@static`` lookups,
        which legitimately shadow a global function name, but before any
        signature resolution or overload dispatch -- a tombstone has nothing
        resolvable behind it.

        Args:
            name: The name the caller used, reported repr-quoted.
            line: The use site's 1-based source line.

        Raises:
            LangError: When ``name`` is registered as removed.
        """
        msg = self.removed.get(name)
        if msg is not None:
            raise LangError(f"{name!r} was removed: {msg}", line)

    def check_access(self, private: bool, source: str | None, what: str, line: int):
        """Enforce ``@private`` visibility for a referenced declaration.

        Args:
            private: Whether the referenced declaration is ``@private``.
            source: The file that defines it.
            what: A description of the declaration, for the error message.
            line: Line of the reference, for diagnostics.

        Raises:
            LangError: When a ``@private`` declaration is referenced from a file
                other than the one being generated.
        """
        if private and source != self.current_source:
            owner = source.rsplit("/", 1)[-1] if source else "its file"
            raise LangError(f"{what} is private to {owner}", line)

    def link_shared(self, fn: ir.Function, source: str | None):
        """Give a function mergeable linkage when it is shared across objects.

        The root file's own definitions keep the default external linkage (a
        genuine duplicate is a link error); a definition reached through
        ``import``, or a monomorphized generic, is copied into every object that
        uses it, so it gets ``linkonce_odr`` and the identical copies merge at
        link time instead of colliding. With no ``root_source`` (single-module
        JIT or the test helpers) there is nothing to link against, so everything
        stays external.

        Args:
            fn: The IR function to set linkage on.
            source: The file the function was defined in.
        """
        if self.root_source is not None and source != self.root_source:
            fn.linkage = "linkonce_odr"

    def mark_inline(self, fn: ir.Function, func: Func):
        """Apply ``@inline`` by attaching LLVM's ``alwaysinline`` attribute.

        The attribute is a request honored by the always-inliner, which runs
        only when optimizing (``-O0`` skips the pass pipeline), so an ``@inline``
        function still emits a standalone body that may go uninlined at ``-O0``.
        """
        if func.inline:
            fn.attributes.add("alwaysinline")

    def mark_noalias(self, fn: ir.Function, func: Func, params: list):
        """Apply ``@noalias`` by attaching LLVM's ``noalias`` argument attribute.

        Each marked parameter must be a pointer (the attribute is meaningless
        otherwise). The promise -- that the pointer does not overlap any other
        pointer the function reaches -- is unchecked, exactly C's ``restrict``;
        violating it is undefined behavior.

        Args:
            fn: The IR function whose args to annotate.
            func: The AST function carrying ``noalias_params``.
            params: The resolved parameter ``LangType``s, in order.

        Raises:
            LangError: When ``@noalias`` marks a non-pointer parameter.
        """
        if not func.noalias_params:
            return
        for i, ((pname, _), ptype) in enumerate(zip(func.params, params)):
            if pname not in func.noalias_params:
                continue
            if not is_pointer(ptype):
                raise LangError(
                    "@noalias only applies to pointer parameters",
                    func.line,
                    source=func.source,
                )
            fn.args[i].add_attribute("noalias")

    def mark_nonnull(self, fn: ir.Function, func: Func, params: list):
        """Apply ``@nonnull`` by attaching LLVM argument attributes.

        Each marked parameter must be a pointer. Unlike ``@noalias``, the
        guarantee is *checked*: every call site must prove the argument
        non-null (see :meth:`check_nonnull_arg`), so the fact can be handed to
        LLVM as ``nonnull``, plus ``dereferenceable(sizeof(pointee))`` when
        the pointee is sized.

        Args:
            fn: The IR function whose args to annotate.
            func: The AST function carrying ``nonnull_params``.
            params: The resolved parameter ``LangType``s, in order.

        Raises:
            LangError: When ``@nonnull`` marks a non-pointer parameter.
        """
        if not func.nonnull_params:
            return
        for i, ((pname, _), ptype) in enumerate(zip(func.params, params)):
            if pname not in func.nonnull_params:
                continue
            if not is_pointer(ptype):
                raise LangError(
                    "@nonnull only applies to pointer parameters",
                    func.line,
                    source=func.source,
                )
            fn.args[i].add_attribute("nonnull")
            pointee = strip_const(ptype.pointee)
            if pointee is not VOID and not is_flexible_array(pointee):
                fn.args[i].attributes.dereferenceable = type_size(pointee)

    @staticmethod
    def nonnull_indices(func: Func) -> frozenset[int]:
        """Indices of ``func``'s ``@nonnull`` parameters."""
        return frozenset(
            i for i, (name, _) in enumerate(func.params) if name in func.nonnull_params
        )

    @staticmethod
    def noalias_indices(func: Func) -> frozenset[int]:
        """Indices of ``func``'s ``@noalias`` parameters."""
        return frozenset(
            i for i, (name, _) in enumerate(func.params) if name in func.noalias_params
        )

    def shared_linkage(self, source: str | None) -> str:
        """The linkage for a file-scoped definition (a @static global).

        In a single-module build (the JIT/tests, no ``root_source``) there is
        nothing to link against, so it stays ``internal`` -- one private copy.

        Under separate compilation the global is copied into the defining
        object *and* every object that imports the module, so it must be
        ``linkonce_odr`` in all of them: the mangled ``name.file`` symbol is
        identical across objects, so the linker merges the copies into a single
        instance. (``internal`` in any one object would leave that object with
        its own private storage, splitting the variable's state -- the bug this
        avoids.)"""
        if self.root_source is None:
            return "internal"
        return "linkonce_odr"

    def static_base(self, name: str, source: str | None) -> str:
        """Mint a unique LLVM symbol for a file-scoped (``@static``) name.

        The separator is ``.`` rather than ``@``: a ``.`` cannot appear in an
        mcc identifier (so it never collides with a real name), and it is safe
        in an ELF symbol, whereas ELF's ``ld`` reads ``@`` as the
        symbol-versioning marker (``symbol@version``) and rejects a shared
        library that exports one.

        Args:
            name: The source-level name.
            source: The defining file, used to build the symbol stem.

        Returns:
            A unique symbol such as ``f.set``, disambiguated with a numeric
            suffix when needed.
        """
        stem = source.rsplit("/", 1)[-1].removesuffix(".mc") if source else "static"
        base = candidate = f"{name}.{stem}"
        counter = 1
        while candidate in self.used_symbols:
            counter += 1
            candidate = f"{base}.{counter}"
        self.used_symbols.add(candidate)
        return candidate

    def valist(self, line: int) -> LangType:
        """Return the platform's ``va_list`` type, built once per target.

        Records both the storage layout (``.ir``, what ``let ap: va_list;``
        allocates) and the form a ``va_list`` takes when passed to a function
        (``va_list_passed_ir``); these differ on every ABI -- see the table in
        the README's Variadic functions section. An architecture without a known
        layout gets a harmless ``i8*`` placeholder and
        ``va_list_supported = False``, so a binding may merely *declare* an
        extern with a ``va_list`` parameter (e.g. importing ``libc/stdio``) on
        any target, while actually *using* one -- a local, ``va_start``/
        ``va_end``, or passing it -- is rejected by :meth:`require_valist`.

        Args:
            line: Source line for diagnostics.

        Returns:
            The cached ``va_list`` ``LangType`` for the current target.
        """
        if self.va_list_type is not None:
            return self.va_list_type
        triple = (self.target or _host_triple()).lower()
        self.va_list_arch = triple.split("-")[0]
        apple = any(s in triple for s in ("apple", "darwin", "macos", "ios"))
        i8p = ir.IntType(8).as_pointer()
        ctx = self.module.context
        self.va_list_supported = True
        if self.va_list_arch in ("arm64", "aarch64") and apple:
            storage, passed, align = i8p, i8p, 8  # va_list is char*
        elif self.va_list_arch in ("arm64", "aarch64"):  # AAPCS __va_list
            st = ctx.get_identified_type("struct.__va_list")
            st.set_body(i8p, i8p, i8p, ir.IntType(32), ir.IntType(32))
            storage, passed, align = st, st.as_pointer(), 8
        elif self.va_list_arch in ("x86_64", "amd64"):  # SysV __va_list_tag[1]
            tag = ctx.get_identified_type("struct.__va_list_tag")
            tag.set_body(ir.IntType(32), ir.IntType(32), i8p, i8p)
            storage, passed, align = ir.ArrayType(tag, 1), tag.as_pointer(), 16
        else:  # unknown: declare-only
            storage, passed, align = i8p, i8p, 8
            self.va_list_supported = False
        self.va_list_type = LangType("va_list", storage, signed=False)
        self.va_list_passed_ir = passed
        self.va_list_align = align
        return self.va_list_type

    def require_valist(self, line: int):
        """Reject actually using a ``va_list`` on an unsupported target.

        Declaring an extern that merely takes one is still allowed.

        Args:
            line: Source line for diagnostics.

        Raises:
            LangError: When ``va_list`` has no known layout for the current
                target's architecture.
        """
        self.valist(line)
        if not self.va_list_supported:
            raise LangError(
                f"va_list is not supported for target architecture "
                f"{self.va_list_arch!r}",
                line,
            )

    def seed_target_consts(self):
        """Define the built-in target facts as compile-time constants.

        Seeds ``TARGET_OS`` and ``TARGET_ARCH`` (the current target's values)
        plus every ``OS_*``/``ARCH_*`` enum name before any user ``const`` is
        folded, reserving their names and letting library code select
        platform-specific bindings -- e.g. stdout's linker symbol -- at compile
        time. A bare-metal triple such as ``aarch64-unknown-none-elf`` reports
        ``OS_NONE`` / ``ARCH_AARCH64``.
        """
        values = target_fact_values(self.target)
        for name, value in values.items():
            self.consts[name] = TypedValue(
                ir.Constant(INT32.ir, value), INT32, adaptable=True
            )
            self.const_privacy[name] = (False, None)  # public, compiler-owned
        # The same facts as plain ints, for evaluating @if conditions, plus the
        # command-line -D defines. Unlike the target facts, -D names are not
        # seeded as ordinary constants: they exist only for @if (an @if name
        # with no definition is simply false, as in C's #if).
        self.target_facts = compute_target_facts(self.target, self.defines)

    def eval_static_cond(self, expr) -> bool:
        """Whether a compile-time ``@if`` branch is taken (see :func:`eval_static_cond`)."""
        return eval_static_cond(expr, self.target_facts)

    def eval_static_value(self, expr) -> int:
        """Evaluate an ``@if`` condition (see the module :func:`eval_static_value`)."""
        return eval_static_value(expr, self.target_facts)

    def flatten_conditionals(self):
        """Resolve top-level ``@if`` blocks before anything is emitted.

        Evaluates each condition over the target facts and splices the live
        branch's declarations into the program in source order, dropping the
        dead branch entirely (it is parsed but never type-checked). Branches may
        nest, so newly spliced conditionals are resolved in turn. Conditional
        ``import``\\ s are ignored here -- the driver already resolved them while
        merging, against the same conditions.
        """
        pending = list(self.program.conditionals)
        while pending:
            cond = pending.pop(0)
            taken = cond.then if self.eval_static_cond(cond.cond) else cond.otherwise
            for item in taken:
                if isinstance(item, Conditional):
                    pending.append(item)
                elif isinstance(item, StructDecl):
                    self.program.structs.append(item)
                elif isinstance(item, GlobalVar):
                    self.program.globals.append(item)
                elif isinstance(item, Const):
                    self.program.consts.append(item)
                elif isinstance(item, EnumDecl):
                    self.program.enums.append(item)
                elif isinstance(item, TypeAlias):
                    self.program.aliases.append(item)
                elif isinstance(item, (StaticAssert, ErrorDirective)):
                    self.program.directives.append(item)
                elif isinstance(item, Import):
                    pass  # already merged by the driver
                else:
                    self.program.functions.append(item)

    def check_directives(self):
        """Evaluate top-level ``@static_assert``/``@error``/``@warning`` directives.

        Each directive fires in source order. ``@error`` fails the compile
        unconditionally at its position; ``@warning`` collects a non-fatal
        diagnostic via :meth:`warn` and keeps compiling; ``@static_assert``
        folds its condition with :meth:`eval_const` (so
        ``sizeof``/``alignof``/``offsetof`` and ``const``/enum references
        resolve against the fully-registered type system) and fails when the
        condition is a zero integer or ``bool`` constant. Directives dropped
        with the dead branch of a top-level ``@if`` never reach here, so a
        guarded ``@error``/``@warning`` only fires when its branch is live.

        Raises:
            LangError: When an ``@error`` is reached, a ``@static_assert``
                condition is false, or a condition does not fold to a bool or
                integer constant.
        """
        for directive in self.program.directives:
            self.current_source = directive.source
            if isinstance(directive, ErrorDirective):
                if directive.warning:
                    self.warn(directive.message, directive.line)
                    continue
                raise LangError(directive.message, directive.line)
            value = self.eval_const(directive.cond, directive.line)
            if not isinstance(value.value, ir.Constant) or not (
                is_integer(value.type) or value.type is BOOL
            ):
                raise LangError(
                    "@static_assert condition must fold to a bool or integer "
                    "constant",
                    directive.line,
                )
            if value.value.constant == 0:
                raise LangError(
                    f"static assertion failed: {directive.message}",
                    directive.line,
                )

    def param_irs(self, params, hidden: frozenset[int] = frozenset()) -> list:
        """Map a function's parameter types to their LLVM argument types.

        A ``va_list`` lowers to the form it is passed in (a pointer on every
        ABI), not its storage layout. A hidden-reference parameter (a ``const``
        struct) lowers to a pointer to its storage rather than the value.

        Args:
            params: The parameter ``LangType``s.
            hidden: Indices of parameters passed by hidden reference.

        Returns:
            The LLVM types to use for the parameters.
        """
        out = []
        for i, p in enumerate(params):
            if is_valist(p):
                out.append(self.va_list_passed_ir)
            elif i in hidden:
                out.append(p.ir.as_pointer())
            else:
                out.append(p.ir)
        return out

    def hidden_ref_indices(self, func: Func, params: list) -> frozenset[int]:
        """Indices of ``func``'s parameters passed by hidden reference.

        A ``const`` parameter of struct type is handed over as a pointer to the
        caller's storage instead of copied by value: the callee promises not to
        mutate it, so sharing the storage is safe and avoids the copy. A
        ``mut`` parameter is a pointer to the caller's storage for **every**
        type -- on a scalar the convention change is the point, since it is the
        only way a write reaches the caller.

        Args:
            func: The function whose parameters to classify.
            params: The resolved parameter ``LangType``s, in order.

        Returns:
            The set of by-reference parameter indices.
        """
        return frozenset(
            i
            for i, ((name, _), ptype) in enumerate(zip(func.params, params))
            if (name in func.const_params and is_struct(ptype))
            or name in func.mut_params
        )

    @staticmethod
    def mut_indices(func: Func) -> frozenset[int]:
        """Indices of ``func``'s ``mut`` parameters (a subset of hidden refs)."""
        return frozenset(
            i for i, (name, _) in enumerate(func.params) if name in func.mut_params
        )

    def can_pair_prototype(self, func: Func, params_key: str | None) -> bool:
        """Whether ``func`` may pair with an earlier declaration of its
        signature.

        Forward declarations: a concrete function may be declared twice --
        same file or cross-file -- when at least one of the pair is a bodyless
        prototype (prototype+definition in either order, or two prototypes).
        Pairing is **per signature**: the params-key selects the earlier
        declaration, so a prototype with a different parameter list never
        pairs -- it joins the name's overload set instead. This is only the
        shape test that routes such a pair away from the duplicate-definition
        error; whether the rest of the declaration actually matches is
        checked by :meth:`pair_prototype`. Never true across kinds: a name
        held by an ``@extern``, a generic template, or an ``@removed``
        tombstone has no :attr:`concrete_decls` entry, so pairing against it
        stays a duplicate-definition error.

        Args:
            func: The incoming (not yet declared) function.
            params_key: The resolved params-key of ``func``'s signature
                (``None`` for a generic or ``@static`` function).

        Returns:
            ``True`` when the pair should be checked and paired.
        """
        if params_key is None:
            return False
        prior = self.concrete_decls.get(func.name, {}).get(params_key)
        return (
            prior is not None
            and not func.static
            and not func.type_params
            and (func.proto or prior.proto)
        )

    def pair_prototype(self, func: Func, params_key: str, ret, params):
        """Check ``func`` against the declaration it pairs with, and absorb it.

        The pair (see :meth:`can_pair_prototype`) shares a params-key by
        construction; the rest must match exactly: same return type (which
        preserves the return-type-only drift error -- overloads may not
        differ solely in return type) *and* the same derived conventions --
        hidden-reference, ``mut``, ``@nonnull``, and ``@noalias`` positions
        -- plus the same ``@private`` and ``@inline`` flags (a prototype is
        never ``@inline``, so an ``@inline`` definition cannot pair with
        one). Parameter names may differ.

        On a match, an incoming prototype is simply discarded -- the earlier
        declaration stands, including proto+proto collapse. An incoming
        definition takes the signature over: its body will generate into the
        prototype's already-declared ``ir.Function`` and it applies
        :meth:`link_shared` for its own source (the prototype skipped it --
        linkage is a property of where the *definition* lives). For a plain
        (non-set) name it also re-keys ``func_privacy`` to its own file (a
        module's ``@private`` helper must stay callable from its ``.mc``
        when the ``.mci`` prototype registered first) and its ``@deprecated``
        state wins over the prototype's; a set member replaces the prototype
        in :attr:`overloads`, so resolution sees the definition's flags.

        Args:
            func: The incoming function; ``self.current_source`` must already
                be its source (signatures may name private structs).
            params_key: The shared signature's params-key.
            ret: ``func``'s resolved return type.
            params: ``func``'s resolved parameter types.

        Raises:
            LangError: When the return type or conventions differ, with a
                note citing the earlier declaration's site.
        """
        name = func.name
        prior = self.concrete_decls[name][params_key]
        # A set member's registry entries live under its mangled symbol; a
        # plain function's under its name.
        symbol = self.overload_symbols.get(id(prior), name)
        if (
            self.signatures[symbol] != (ret, params, func.variadic)
            or self.hidden_ref[symbol] != self.hidden_ref_indices(func, params)
            or self.mut_ref[symbol] != self.mut_indices(func)
            or self.nonnull_ref[symbol] != self.nonnull_indices(func)
            or self.noalias_indices(prior) != self.noalias_indices(func)
            or prior.private != func.private
            or prior.inline != func.inline
        ):
            if func.proto and prior.proto:
                message = f"conflicting prototypes for {name!r}"
            else:
                message = f"definition of {name!r} does not match its prototype"
            err = LangError(message, func.line, source=func.source)
            err.notes.append(
                Note(
                    f"previous declaration of {name!r} is here",
                    prior.line,
                    prior.source,
                )
            )
            raise err
        if func.proto:
            return  # checked against the standing declaration, then discarded
        # The definition completes a prototype: the signature's ir.Function
        # already exists (identical signature), so body generation fills it
        # in later.
        self.concrete_decls[name][params_key] = func
        self.link_shared(self.funcs[symbol], func.source)
        if id(prior) in self.overload_symbols:
            # A set member: resolution iterates self.overloads and emission
            # keys off overload_symbols, so the definition stands in for the
            # discarded prototype in both.
            members = self.overloads[name]
            members[members.index(prior)] = func
            self.overload_symbols[id(func)] = symbol
            return
        self.func_privacy[name] = (func.private, func.source)
        if func.deprecated_msg is not None:
            self.deprecated_syms[name] = func.deprecated_msg
        else:
            self.deprecated_syms.pop(name, None)

    def check_mixed_set(self, func: Func, members: dict[str, Func]):
        """Validate a generic template joining a concrete name (a mixed set).

        A template may share its name with a concrete function or concrete
        set only from its own module (all overloads of a name live in one
        defining module), and only when the concrete side is overloadable at
        all -- ``main``, a variadic function, and a ``va_list``-taking
        function never join a set, whichever side declares first.

        Args:
            func: The incoming generic template.
            members: The name's concrete declarations, by params-key.

        Raises:
            LangError: On a cross-module mix or a non-overloadable concrete
                side.
        """
        if func.name == "main":
            raise LangError("function 'main' cannot be overloaded", func.line)
        if next(iter(members.values())).source != func.source:
            raise LangError(
                f"function {func.name!r} already defined", func.line
            )
        for member in members.values():
            if member.variadic:
                raise LangError(
                    f"variadic function {func.name!r} cannot be overloaded",
                    func.line,
                )
            symbol = self.overload_symbols.get(id(member), member.name)
            if self.is_collecting_func(member, self.signatures[symbol][1]):
                raise LangError(
                    f"collecting function {func.name!r} cannot be overloaded",
                    func.line,
                )
            if any(is_valist(p) for p in self.signatures[symbol][1]):
                raise LangError(
                    f"function {func.name!r} cannot be overloaded: it "
                    "takes a va_list parameter",
                    func.line,
                )

    @staticmethod
    def collecting_params(params) -> bool:
        """Report whether a resolved parameter list marks a collecting function.

        The marker is the trailing parameter *type* alone: exactly
        ``slice<const any>``, which the ``args...`` sugar desugars to. A call
        to such a function boxes each extra argument into a caller-stack
        ``any`` and passes a read-only slice over the run (see
        :meth:`collect_variadic_args`). Function-pointer types carry no
        marker, so calls through ``fn(...)`` values stay explicit-slice.

        Args:
            params: The resolved parameter ``LangType``\\ s.

        Returns:
            ``True`` when the last parameter is a ``slice<const any>``.
        """
        if not params:
            return False
        last = params[-1]
        return (
            is_slice(last)
            and last.args[0].const
            and strip_const(last.args[0]) is ANY
        )

    def is_collecting_func(self, func: Func, params) -> bool:
        """Report whether a declared function collects extra arguments.

        The type is the marker (:meth:`collecting_params`), with one
        exclusion: a ``mut`` trailing parameter never collects -- ``mut``
        lends the caller's own writable storage, which collection can never
        synthesize -- so such a function stays explicit-slice.

        Args:
            func: The declaration.
            params: Its resolved parameter ``LangType``\\ s.

        Returns:
            ``True`` when calls to ``func`` collect their extra arguments.
        """
        return (
            self.collecting_params(params)
            and func.params[-1][0] not in func.mut_params
        )

    @staticmethod
    def collecting_ref(ref: TypeRef) -> bool:
        """Report whether a parameter ``TypeRef`` spells ``slice<const any>``.

        The syntactic twin of :meth:`collecting_params` for generic
        templates, whose parameter types cannot resolve at declaration time
        (they may name type parameters). An alias spelling is not followed
        here; a template hiding the marker behind an alias simply stays
        explicit-slice until generics learn collection.

        Args:
            ref: The parameter's declared type.

        Returns:
            ``True`` for a literal ``slice<const any>`` spelling.
        """
        return (
            ref.name == "slice"
            and not ref.stars
            and not ref.dims
            and len(ref.args) == 1
            and ref.args[0].name == "any"
            and ref.args[0].const
            and not ref.args[0].stars
            and not ref.args[0].dims
            and not ref.args[0].args
        )

    def check_template_collecting(self, func: Func):
        """Reject a generic template shaped as a collecting function.

        Stage-1 rule: collection runs only through the direct-call path, so a
        collecting function cannot share a generic name -- the pre-evaluate
        path's arity-based viability would be ambiguous against the
        last-position rule. This is the explicit diagnostic that replaces the
        confusing arity error a call would otherwise hit.

        Args:
            func: The incoming generic template.

        Raises:
            LangError: When the trailing parameter spells ``slice<const
                any>`` (and is not ``mut``).
        """
        if (
            func.params
            and self.collecting_ref(func.params[-1][1])
            and func.params[-1][0] not in func.mut_params
        ):
            raise LangError(
                "a generic function cannot be a collecting function "
                "(native variadic collection does not reach generics yet)",
                func.line,
            )

    def check_collecting_decl(self, func: Func, params: list):
        """Reject collecting-function shapes whose calls could never collect.

        Args:
            func: The declared function.
            params: Its resolved parameter ``LangType``\\ s.

        Raises:
            LangError: For a collecting ``@extern`` (C sees no slice), a
                collecting ``main`` (the C runtime calls it), or a collecting
                function that also declares C varargs.
        """
        if not self.is_collecting_func(func, params):
            return
        if func.extern:
            raise LangError(
                "an @extern function cannot be a collecting function "
                "(C sees no slice<const any>; declare C varargs with '...')",
                func.line,
            )
        if func.name == "main":
            raise LangError(
                "function 'main' cannot be a collecting function", func.line
            )
        if func.variadic:
            raise LangError(
                "a collecting function cannot also take C varargs; drop the '...'",
                func.line,
            )

    def lookup_enum(self, name: str) -> "EnumType | None":
        """Resolve an enum name, preferring a file-scoped ``@static`` one.

        Args:
            name: The enum's name.

        Returns:
            The matching ``EnumType``, or ``None`` when no enum has that name in
            scope. A same-named ``@static`` enum in the current file shadows a
            global one, exactly as ``@static`` structs do.
        """
        static = self.static_enums.get((self.current_source, name))
        return static if static is not None else self.enums.get(name)

    def lookup_alias(self, name: str) -> "Alias | None":
        """Resolve a type-alias name, preferring a file-scoped ``@static`` one.

        Args:
            name: The alias's name.

        Returns:
            The matching ``Alias``, or ``None`` when no alias has that name in
            scope.
        """
        static = self.static_type_aliases.get((self.current_source, name))
        return static if static is not None else self.type_aliases.get(name)

    def register_alias(self, decl: TypeAlias):
        """Record a type alias for later (lazy) resolution.

        The target is not resolved here -- it is on each use, in
        :meth:`lang_type` -- so an alias may name a type declared later.

        Args:
            decl: The alias declaration.

        Raises:
            LangError: When the name clashes with a built-in type or another
                type/alias in the same scope.
        """
        self.current_source = decl.source
        alias = Alias(decl.target, decl.private, decl.source, decl.line)
        if decl.static:
            key = (decl.source, decl.name)
            if key in self.static_type_aliases or key in self.static_structs:
                raise LangError(f"type {decl.name!r} already defined", decl.line)
            self.static_type_aliases[key] = alias
        else:
            if (
                decl.name in TYPES
                or decl.name in self.type_aliases
                or decl.name in self.enums
                or decl.name in self.struct_templates
            ):
                raise LangError(f"type {decl.name!r} already defined", decl.line)
            self.type_aliases[decl.name] = alias

    def register_enum(self, decl: EnumDecl):
        """Resolve an enum's underlying type and fold its member constants.

        Registers the enum before folding its members so a member's value may
        reference an earlier member of the same enum (``B = A + 1``). Each member
        is folded as a compile-time constant and coerced to the underlying type,
        which it then carries (non-adaptable, like a typed ``const``).

        When the ``:`` slot names another enum directly (a bare name -- no
        pointer ``*``, generic arguments, array dims, or ``const``), the new
        enum *derives* from it: it copies the base's member table and adopts
        its underlying type, then folds its own members on top. Anything else
        in the slot -- a pointer to an enum, a ``type`` alias to one, a plain
        type -- keeps its usual meaning as a bare underlying type, with no
        member merge.

        Args:
            decl: The enum declaration to register.

        Raises:
            LangError: On a name clash, a duplicate member, a member that
                redefines an inherited one, or a member value that is not a
                constant of the underlying type.
        """
        self.current_source = decl.source
        underlying = (
            self.lang_type(decl.underlying, decl.line)
            if decl.underlying is not None
            else INT32
        )
        if underlying is VOID:
            raise LangError(f"enum {decl.name!r} cannot have a void type", decl.line)
        base = None
        uref = decl.underlying
        if (
            uref is not None
            and uref.params is None
            and not uref.args
            and uref.stars == 0
            and not uref.dims
            and not uref.const
        ):
            base = self.lookup_enum(uref.name)
            # A base only counts when lang_type actually resolved the slot to
            # this enum (its arm returns the enum's own underlying object);
            # otherwise something else -- e.g. a @static struct -- shadows the
            # name and the slot keeps its plain-underlying meaning.
            if base is not None and underlying is not base.underlying:
                base = None
        enum = EnumType(underlying, {}, decl.private, decl.source)
        if base is not None:
            # Copy the entries rather than sharing the TypedValues: the planned
            # nominal-enums feature will re-type inherited members here.
            for mname, member in base.members.items():
                enum.members[mname] = TypedValue(member.value, member.type)
        inherited = frozenset(enum.members)
        if decl.static:
            key = (decl.source, decl.name)
            if key in self.static_enums or key in self.static_structs:
                raise LangError(f"type {decl.name!r} already defined", decl.line)
            self.static_enums[key] = enum
        else:
            if (
                decl.name in TYPES
                or decl.name in self.enums
                or decl.name in self.struct_templates
            ):
                raise LangError(f"type {decl.name!r} already defined", decl.line)
            self.enums[decl.name] = enum
        for mname, vexpr in decl.members:
            if mname in inherited:
                raise LangError(
                    f"enum {decl.name!r} redefines member {mname!r} "
                    f"inherited from {uref.name!r}",
                    decl.line,
                )
            if mname in enum.members:
                raise LangError(
                    f"enum {decl.name!r} has a duplicate member {mname!r}", decl.line
                )
            value = self.eval_const(vexpr, decl.line)
            value = self.const_coerce(
                value, underlying, decl.line, f"enum member {decl.name}::{mname}"
            )
            enum.members[mname] = TypedValue(value.value, underlying)

    def resolve_enum_access(self, expr: EnumAccess) -> TypedValue:
        """Resolve an ``Enum::Member`` to its folded constant value.

        Args:
            expr: The ``EnumAccess`` node.

        Returns:
            The member's constant ``TypedValue``.

        Raises:
            LangError: On an unknown enum, a missing member, or a ``@private``
                enum referenced from another file.
        """
        enum = self.lookup_enum(expr.enum)
        if enum is None:
            raise LangError(f"unknown enum {expr.enum!r}", expr.line)
        self.check_access(enum.private, enum.source, f"enum {expr.enum!r}", expr.line)
        member = enum.members.get(expr.member)
        if member is None:
            raise LangError(
                f"enum {expr.enum!r} has no member {expr.member!r}", expr.line
            )
        return member

    def lang_type(self, ref: TypeRef, line: int) -> LangType:
        """Resolve a parsed ``TypeRef`` to a ``LangType``.

        Handles function-pointer types, active type-parameter bindings, the
        built-in scalar types, ``va_list``, and (generic) structs, then applies
        pointer ``*`` depth and fixed-array dimensions.

        Args:
            ref: The parsed type reference.
            line: Source line for diagnostics.

        Returns:
            The resolved ``LangType``.

        Raises:
            LangError: On an unknown type, a wrong generic arity, a ``void``
                pointer, or another malformed type.
        """
        if ref.params is not None:  # a fn(...) -> ret function-pointer type
            ret = self.lang_type(ref.ret, line)
            params = tuple(self.lang_type(p, line) for p in ref.params)
            base = function_type(ret, params, ref.variadic)
            for _ in range(ref.stars):
                base = pointer_to(base)
            return self.apply_dims(base, ref.dims, line)
        if ref.name in self.type_bindings and not ref.args:
            base = self.type_bindings[ref.name]
        elif ref.name in TYPES:
            if ref.args:
                raise LangError(f"type {ref.name!r} is not generic", line)
            base = TYPES[ref.name]
        elif ref.name == "va_list":
            if ref.args:
                raise LangError("type 'va_list' is not generic", line)
            base = self.valist(line)
        elif ref.name == "any":
            if ref.args:
                raise LangError("type 'any' is not generic", line)
            base = ANY
        elif ref.name == "slice":
            if len(ref.args) != 1:
                raise LangError(
                    f"type 'slice' takes 1 type argument, got {len(ref.args)}", line
                )
            base = self.slice_type(self.lang_type(ref.args[0], line), line)
        elif (
            self.current_source,
            ref.name,
        ) in self.static_structs or ref.name in self.struct_templates:
            decl = self.static_structs.get((self.current_source, ref.name))
            if decl is None:
                decl = self.struct_templates[ref.name]
                self.check_access(
                    decl.private, decl.source, f"struct {ref.name!r}", line
                )
            # A shorter argument list is allowed only when the omitted tail is
            # entirely defaulted (defaults are trailing, so the count check
            # suffices); bare `range` with `<T = int64>` is range<int64>.
            total = len(decl.type_params)
            required = total - len(decl.type_param_defaults)
            if not required <= len(ref.args) <= total:
                expected = (
                    f"between {required} and {total}"
                    if decl.type_param_defaults
                    else f"{total}"
                )
                raise LangError(
                    f"struct {ref.name!r} expects {expected} "
                    f"type argument(s), got {len(ref.args)}",
                    line,
                )
            args = tuple(self.lang_type(a, line) for a in ref.args)
            if len(args) < total:
                bindings = dict(zip(decl.type_params, args))
                self.fill_default_bindings(decl, bindings, line)
                args = tuple(bindings[t] for t in decl.type_params)
            base = self.instantiate_struct(decl, args, line)
        elif (enum := self.lookup_enum(ref.name)) is not None:
            if ref.args:
                raise LangError(f"enum {ref.name!r} is not generic", line)
            self.check_access(enum.private, enum.source, f"enum {ref.name!r}", line)
            base = enum.underlying
        elif (alias := self.lookup_alias(ref.name)) is not None:
            if ref.args:
                raise LangError(f"type alias {ref.name!r} is not generic", line)
            self.check_access(
                alias.private, alias.source, f"type alias {ref.name!r}", line
            )
            if ref.name in self.resolving_aliases:
                raise LangError(
                    f"type alias {ref.name!r} refers to itself (cyclic alias)", line
                )
            self.resolving_aliases.add(ref.name)
            outer_source = self.current_source
            self.current_source = alias.source  # target may name private types
            try:
                # The target resolves at the alias's own declaration site, so
                # an error (or backtrace frame) inside it pairs the declaring
                # file with a line in that file, not the use site's.
                base = self.lang_type(alias.target, alias.line)
            except LangError as err:
                # An error in the target belongs to the alias's file; then a
                # backtrace frame for the alias itself, so a chain that goes
                # through e.g. `string` (= list<char>) names `string`.
                if err.source is None:
                    err.source = alias.source
                err.notes.append(
                    Note(f"in instantiation of {ref.name}", line, outer_source)
                )
                raise
            finally:
                self.resolving_aliases.discard(ref.name)
                self.current_source = outer_source
        else:
            raise LangError(f"unknown type {ref.name!r}", line)
        if ref.const:
            base = const_of(base)
        if ref.stars and base is VOID:
            raise LangError("no void pointers; use uint8* for raw memory", line)
        for _ in range(ref.stars):
            base = pointer_to(base)
        return self.apply_dims(base, ref.dims, line)

    def apply_dims(self, base: LangType, dims: list, line: int) -> LangType:
        """Wrap a base type in fixed-size array types, innermost dimension last.

        ``int32[3][4]`` becomes ``[3 x [4 x i32]]``.

        Args:
            base: The element base type.
            dims: The dimensions, outermost first -- an integer size or the
                ``str`` name of an integer ``const``.
            line: Source line for diagnostics.

        Returns:
            The array ``LangType``, or ``base`` when ``dims`` is empty.

        Raises:
            LangError: On an array of ``void`` or an inferred ``[]`` here.
        """
        if dims and base is VOID:
            raise LangError("cannot make an array of void", line)
        for size in reversed(dims):
            if size is None:
                raise LangError(
                    "an inferred array size [] is only allowed on an initialized "
                    "variable's outermost dimension",
                    line,
                )
            if isinstance(size, str):  # a const name, e.g. int32[N]
                size = self.const_dim(size, line)
            elif not isinstance(size, int):  # a constant expression, e.g. int32[N+1]
                size = self.eval_dim(size, line)
            base = list_of(base, size)
        return base

    def eval_dim(self, expr, line: int) -> int:
        """Fold a constant-expression array size to a positive integer.

        Args:
            expr: The dimension expression (e.g. ``N + 1`` or ``2 * SIZE``).
            line: Source line for diagnostics.

        Returns:
            The positive integer size.

        Raises:
            LangError: When the expression is not a constant integer, or is less
                than 1.
        """
        tv = self.eval_const(expr, line)
        if not isinstance(tv.value, ir.Constant) or not is_integer(tv.type):
            raise LangError(
                "an array size must be a constant integer expression", line
            )
        size = tv.value.constant
        if size < 1:
            raise LangError(f"array size must be at least 1, not {size}", line)
        return size

    def const_dim(self, name: str, line: int) -> int:
        """Resolve a ``const`` used as an array size to its integer value.

        Args:
            name: The constant's name.
            line: Source line for diagnostics.

        Returns:
            The positive integer size.

        Raises:
            LangError: When the name is unknown, not an integer constant, or
                less than 1.
        """
        const = self.consts.get(name)
        if const is None:
            raise LangError(
                f"unknown array size {name!r}; expected an integer constant", line
            )
        self.check_access(*self.const_privacy[name], f"constant {name!r}", line)
        if not is_integer(const.type):
            raise LangError(
                f"array size {name!r} must be an integer constant, not {const.type}",
                line,
            )
        size = const.value.constant
        if size < 1:
            raise LangError(f"array size must be at least 1, not {size}", line)
        return size

    def list_type_for(self, ref: TypeRef, value, line: int) -> LangType:
        """Resolve a declared type, inferring an outer ``[]`` from a literal.

        Fills an inferred outermost dimension from the length of an
        array-literal initializer; only the outermost dimension may be inferred.

        Args:
            ref: The declared type reference.
            value: The initializer expression.
            line: Source line for diagnostics.

        Returns:
            The resolved ``LangType``.

        Raises:
            LangError: When a non-outermost dimension is ``[]`` or the
                initializer is not an array literal.
        """
        if ref.dims and ref.dims[0] is None:
            if any(d is None for d in ref.dims[1:]):
                raise LangError(
                    "only the outermost array dimension can be inferred", line
                )
            if isinstance(value, ArrayLit):
                outer = len(value.elements)
            elif isinstance(value, StrLit):
                outer = len(self.string_data(value.value))  # bytes, NUL included
            else:
                raise LangError(
                    "an inferred array size [] needs an array-literal or "
                    "string-literal initializer",
                    line,
                )
            ref = dataclasses_replace(ref, dims=[outer, *ref.dims[1:]])
        return self.lang_type(ref, line)

    def resolve_base(self, decl: StructDecl) -> "LangType | None":
        """Resolve a struct's ``extends`` base to its struct type, or ``None``.

        Called with the deriving struct's type bindings and source already in
        scope, so a generic base (``extends pair<K, V>``) resolves its
        arguments against the instance being built.

        Raises:
            LangError: When the base is not a struct.
        """
        if decl.base is None:
            return None
        base_type = self.lang_type(decl.base, decl.line)
        if not is_struct(base_type):
            raise LangError(
                f"{base_type} is not a struct; cannot extend it",
                decl.line,
                source=decl.source,
            )
        if is_union(base_type):
            raise LangError(
                f"a union cannot be extended, but {decl.name!r} extends "
                f"union {base_type}",
                decl.line,
                source=decl.source,
            )
        return base_type

    def slice_type(self, element: LangType, line: int) -> LangType:
        """Build (and intern) the builtin ``slice<T>`` view type for ``element``.

        A slice is a 2-word, non-owning view ``{ data: T*; length: uint64 }`` over
        a contiguous run of ``T``. It is realized as an ordinary struct -- so
        field access, ``sizeof``, and by-value passing all reuse the struct
        machinery -- tagged with the reserved template name ``"slice"`` (see
        :func:`is_slice`). Instances are interned per element type, alongside the
        user structs in ``struct_types``.

        ``slice<T>`` and ``slice<const T>`` are distinct types (the element-const
        axis) but share one LLVM layout: ``const`` is IR-identical to ``T``, so
        the identified type is keyed by the mutable element. A mutable slice then
        flows into its read-only form with no conversion (see :meth:`coerce`).

        Args:
            element: The element type ``T`` (possibly ``const T``).
            line: Source line for diagnostics.

        Returns:
            The cached or newly built ``slice<T>`` ``LangType``.

        Raises:
            LangError: On ``slice<void>``.
        """
        if strip_const(element) == VOID:
            raise LangError("cannot make a slice of void", line)
        mangled = f"slice<{element}>"
        if mangled in self.struct_types:
            return self.struct_types[mangled]
        fields = (("data", pointer_to(element)), ("length", UINT64))
        # Key the LLVM layout by the mutable element so slice<T> and
        # slice<const T> share one identified type; body it only on first sight.
        identified = self.module.context.get_identified_type(
            f"slice<{strip_const(element)}>"
        )
        if identified.is_opaque:
            identified.set_body(*(ftype.ir for _, ftype in fields))
        slice_t = LangType(
            mangled, identified, signed=False, template="slice", args=(element,)
        )
        object.__setattr__(slice_t, "fields", fields)  # frozen; excluded from eq
        object.__setattr__(slice_t, "elem_indices", (0, 1))
        self.struct_types[mangled] = slice_t
        return slice_t

    def any_tag(self, lang_type: LangType, line: int) -> int:
        """Compute a boxable type's ``any`` tag, checking for hash collisions.

        The tag is the FNV-1a hash of the type's canonical name (see
        :func:`fnv1a64`); every boxing site and ``case type`` arm funnels
        through here, so two distinct type names hashing to one tag within a
        compilation are caught instead of corrupting the type-switch.

        Args:
            lang_type: The boxable type (already validated, ``const``
                stripped).
            line: Source line for diagnostics.

        Returns:
            The 64-bit tag as a Python integer.

        Raises:
            LangError: On an in-compile tag collision.
        """
        name = str(lang_type)
        tag = fnv1a64(name)
        known = self.any_tags.setdefault(tag, name)
        if known != name:
            raise LangError(
                f"any type tags collide: {known!r} and {name!r} hash to the "
                "same 64-bit id",
                line,
            )
        return tag

    def check_boxable(self, lang_type: LangType, line: int) -> LangType:
        """Validate a type against the ``any`` boxable set.

        The v1 set is primitives (the integers, ``bool``, ``char``,
        ``float64``), pointers (each pointer type gets its own tag), and
        slices. Structs, unions, and arrays are rejected -- by value the
        payload is unbounded, by pointer the lifetime goes implicit, so
        ``&value`` is the explicit escape. An ``any`` never boxes another
        ``any`` (``any`` to ``any`` is a plain copy, not nesting).

        Args:
            lang_type: The candidate type (possibly ``const``-qualified).
            line: Source line for diagnostics.

        Returns:
            The ``const``-stripped type to tag and box.

        Raises:
            LangError: When the type is outside the boxable set.
        """
        lang_type = strip_const(lang_type)
        if lang_type is ANY:
            raise LangError(
                "cannot box an any in an any; an any never nests", line
            )
        if lang_type is NULLT:
            raise LangError(
                "cannot box a bare null in an any; give it a pointer type "
                "first (e.g. null as uint8*)",
                line,
            )
        if is_slice(lang_type) or is_pointer(lang_type):
            return lang_type
        if is_array(lang_type):
            raise LangError(
                f"cannot box a {lang_type} in an any; box a pointer to its "
                "first element (&value[0]) instead",
                line,
            )
        if is_struct(lang_type):
            raise LangError(
                f"cannot box a {lang_type} in an any; box a pointer to it "
                "(&value) instead",
                line,
            )
        if (
            is_integer(lang_type)
            or lang_type is BOOL
            or lang_type is CHAR
            or lang_type is FLOAT64
        ):
            return lang_type
        raise LangError(f"cannot box a {lang_type} in an any", line)

    def gen_box_any(self, tv: TypedValue, line: int) -> TypedValue:
        """Box a value into a fresh ``any``: store the tag, then the payload.

        The box is assembled in a stack slot -- zero-filled first, so the
        payload bytes past the value are deterministic -- and loaded back as
        the 24-byte ``any`` value. The payload slot is reinterpreted as the
        source type by a pointer cast, the same move a union member store
        makes. The GEP indices here and the layout in ``types.ANY`` are the
        two sites of the dual-site layout invariant.

        Args:
            tv: The value to box (already validated via
                :meth:`check_boxable`; an array must have been rejected
                before it decayed).
            line: Source line for diagnostics.

        Returns:
            The boxed ``any`` as a ``TypedValue``.
        """
        if tv.decayed is not None:
            # The value is the pointer an array decayed to; reject by the
            # array type, not the pointer it silently became.
            self.check_boxable(tv.decayed, line)
        boxed = self.check_boxable(tv.type, line)
        tag = self.any_tag(boxed, line)
        slot = self.entry_alloca(ANY.ir, "any.box")
        self.builder.store(ir.Constant(ANY.ir, None), slot)  # zero-fill first
        tag_ptr = self.builder.gep(slot, [I32_ZERO, I32_ZERO], inbounds=True)
        self.builder.store(ir.Constant(UINT64.ir, tag), tag_ptr)
        payload_ptr = self.builder.gep(
            slot, [I32_ZERO, ir.Constant(ir.IntType(32), 1)], inbounds=True
        )
        typed_ptr = self.builder.bitcast(payload_ptr, boxed.ir.as_pointer())
        self.builder.store(tv.value, typed_ptr)
        return TypedValue(self.builder.load(slot), ANY)

    def instantiate_struct(
        self, decl: StructDecl, args: tuple[LangType, ...], line: int
    ) -> LangType:
        """Return the struct instance for a set of type arguments.

        Creates the LLVM identified type and resolves field types on first use,
        registering the instance before resolving fields so self-referential
        structs (e.g. ``node<T>`` holding a ``node<T>*``) can refer to
        themselves. ``@packed``/``@align`` structs get an explicitly laid-out
        body with padding. An error while resolving the body gains an
        ``in instantiation of ...`` backtrace note naming this request site;
        a cached instance skips capture, so an error chain always reports the
        first triggering path (as C++/Rust do).

        Args:
            decl: The struct template declaration.
            args: The type arguments to instantiate with.
            line: Source line of the instantiation site, for the backtrace
                note when the body fails to resolve.

        Returns:
            The cached or newly built struct ``LangType``.

        Raises:
            LangError: When ``@align`` is below the struct's natural alignment.
        """
        mangled = self.symbol_bases.get((decl.source, decl.name), decl.name)
        if args:
            mangled += "<" + ", ".join(str(a) for a in args) + ">"
        if mangled in self.struct_types:
            return self.struct_types[mangled]
        if mangled in self.resolving_bases:
            raise LangError(
                f"struct {decl.name!r} cannot extend itself (cyclic 'extends')",
                decl.line,
                source=decl.source,
            )
        outer = self.type_bindings
        outer_source = self.current_source
        self.type_bindings = dict(zip(decl.type_params, args))
        self.current_source = decl.source  # fields/base may name private structs
        # Resolve an `extends` base up front (with the bindings above in scope,
        # so a generic base like pair<K, V> binds against this instance): its
        # fields are spliced in front of this struct's own (so a pointer to this
        # struct is layout-compatible with a pointer to the base), and its
        # @packed/@align/@volatile are inherited, so both must be known before
        # the body is laid out.
        self.resolving_bases.add(mangled)
        try:
            base_type = self.resolve_base(decl)
            align, packed, volatile = decl.align, decl.packed, decl.volatile
            if base_type is not None:
                volatile = volatile or base_type.volatile
                aligns = [a for a in (decl.align, base_type.align) if a is not None]
                align = max(aligns) if aligns else None
                if base_type.packed:
                    packed = True  # packing changes offsets, so it must match
                elif decl.packed:
                    raise LangError(
                        "an extending struct cannot be @packed unless its base is",
                        decl.line,
                        source=decl.source,
                    )
            identified = self.module.context.get_identified_type(mangled)
            struct_type = LangType(
                mangled,
                identified,
                signed=False,
                template=decl.name,
                args=args,
                align=align,
                packed=packed,
                volatile=volatile,
                union=decl.union,
            )
            # Register before resolving fields so self-referential structs
            # (e.g. node<T> holding a node<T>*) can refer to themselves.
            self.struct_types[mangled] = struct_type
            fields = tuple(
                (fname, self.field_type(ftype, i == len(decl.fields) - 1, decl))
                for i, (fname, ftype) in enumerate(decl.fields)
            )
            if base_type is not None:
                if base_type.fields and is_flexible_array(base_type.fields[-1][1]):
                    raise LangError(
                        f"cannot extend struct {base_type.name!r}: it ends in a "
                        "flexible array member, which must stay the last field",
                        decl.line,
                        source=decl.source,
                    )
                inherited = {fname for fname, _ in base_type.fields}
                for fname, _ in fields:
                    if fname in inherited:
                        raise LangError(
                            f"field {fname!r} is already defined in base struct "
                            f"{base_type.name!r}",
                            decl.line,
                            source=decl.source,
                        )
                fields = base_type.fields + fields  # base fields first: the prefix
        except LangError as err:
            # An error inside the template's base/fields belongs to the
            # template's file, not the requester's -- attribute it here, where
            # current_source still points at the declaring file. Then record
            # the instantiation frame against the requesting file (the failed
            # instance stays registered -- deliberate, for self-reference --
            # which is harmless while compilation aborts on the first error).
            if err.source is None:
                err.source = self.current_source
            err.notes.append(
                Note(f"in instantiation of {mangled}", line, outer_source)
            )
            raise
        finally:
            self.resolving_bases.discard(mangled)
            self.type_bindings = outer
            self.current_source = outer_source
        natural = (
            1 if packed else max((type_align(ftype) for _, ftype in fields), default=1)
        )
        if align is not None and align < natural:
            # current_source was restored to the instantiating file above,
            # but decl.line belongs to the declaring file.
            raise LangError(
                f"@align({align}) is below struct {decl.name!r}'s "
                f"natural alignment of {natural}",
                decl.line,
                source=decl.source,
            )
        object.__setattr__(
            struct_type, "fields", fields
        )  # frozen; fields excluded from eq
        # Merge the base's field defaults into this instance's, resolved here
        # because only the instantiation knows the base: a bare parameter as
        # the base (`struct entry<T> extends T`) has no declaration to recurse
        # into by name -- the base struct exists once T is bound. A derived
        # default overrides a base one of the same name.
        defaults = (
            dict(getattr(base_type, "defaults", {})) if base_type is not None else {}
        )
        defaults.update(decl.defaults)
        object.__setattr__(struct_type, "defaults", defaults)
        if decl.union:
            # A union's members all share the storage at offset 0. Body the
            # identified type as the most-aligned member plus explicit pad
            # bytes up to the union's size (what clang emits for a C union),
            # so LLVM's natural size and alignment agree with type_size() and
            # type_align(). Member access never GEPs to an element -- it casts
            # the union's address to the member type -- so elem_indices are
            # all zeros. A @packed/@align union departs from the natural
            # alignment exactly like a struct: its body is packed and the
            # existing over_aligned() machinery aligns the storage by hand.
            rep = max(
                (ftype for _, ftype in fields),
                key=lambda t: (type_align(t), type_size(t)),
                default=None,
            )
            elements = [] if rep is None else [rep.ir]
            pad = type_size(struct_type) - (0 if rep is None else type_size(rep))
            if pad:
                elements.append(ir.ArrayType(ir.IntType(8), pad))
            if packed or over_aligned(struct_type):
                identified.packed = True
            identified.set_body(*elements)
            object.__setattr__(struct_type, "elem_indices", (0,) * len(fields))
            return struct_type
        if packed or over_aligned(struct_type):
            # @packed and @align depart from LLVM's natural layout, so spell
            # the layout out: a packed body with explicit padding, keeping
            # field offsets and the LLVM size in agreement with type_size().
            elements, indices, offset = [], [], 0
            for _, ftype in fields:
                pad = 0 if packed else -offset % type_align(ftype)
                if pad:
                    elements.append(ir.ArrayType(ir.IntType(8), pad))
                indices.append(len(elements))
                elements.append(ftype.ir)
                offset += pad + type_size(ftype)
            tail = type_size(struct_type) - offset
            if tail:
                elements.append(ir.ArrayType(ir.IntType(8), tail))
            identified.packed = True
            identified.set_body(*elements)
        else:
            indices = range(len(fields))
            identified.set_body(*(ftype.ir for _, ftype in fields))
        object.__setattr__(struct_type, "elem_indices", tuple(indices))
        return struct_type

    def is_struct_prefix(self, base: LangType, derived: LangType) -> bool:
        """Whether ``base``'s fields are the leading prefix of ``derived``'s.

        That is exactly how ``extends`` lays a struct out (base fields first), so
        a ``derived`` value can be narrowed to ``base`` -- the prefix occupies
        the same starting bytes. Compared by field name and type, so the check
        also covers transitive ``extends`` chains and same-layout
        specializations. A union never participates: its members share offset
        0, so a matching field list does not mean a matching layout.
        """
        if base.fields is None or derived.fields is None:
            return False
        if base.union or derived.union:
            return False
        n = len(base.fields)
        return n <= len(derived.fields) and derived.fields[:n] == base.fields

    def field_type(self, ftype: TypeRef, is_last: bool, decl: StructDecl) -> LangType:
        """Resolve a struct field's type, lowering a flexible array member.

        A trailing field written ``field: T[]`` is a flexible array member: an
        inferred dimension (``None``) only legal here, on the struct's last
        field, and as its sole dimension. It lowers to a zero-length array
        ``[0 x T]``, which adds nothing to ``sizeof`` and decays to a ``T*`` at
        the struct's tail (see :meth:`value_at`). Every other field resolves
        normally.

        Args:
            ftype: The field's parsed type.
            is_last: Whether this is the struct's last declared field.
            decl: The owning declaration, for error attribution.

        Returns:
            The resolved field ``LangType``.

        Raises:
            LangError: When an inferred ``[]`` is misused (not last, not the sole
                dimension, or over a ``void`` element).
        """
        if None not in ftype.dims:
            return self.lang_type(ftype, decl.line)
        if decl.union:
            raise LangError(
                "a union cannot contain a flexible array member",
                decl.line,
                source=decl.source,
            )
        if ftype.dims != [None] or not is_last:
            raise LangError(
                "a flexible array member 'field: T[]' must be the struct's last "
                "field, with '[]' as its only array dimension",
                decl.line,
                source=decl.source,
            )
        element = self.lang_type(dataclasses_replace(ftype, dims=[]), decl.line)
        if element is VOID:
            raise LangError(
                "cannot make a flexible array of void", decl.line, source=decl.source
            )
        return list_of(element, 0)

    def struct_field(
        self, owner: LangType, fname: str, line: int
    ) -> tuple[int, LangType]:
        """Look up a struct field by name.

        Args:
            owner: The struct type.
            fname: The field name.
            line: Source line for diagnostics.

        Returns:
            A ``(LLVM element index, field type)`` pair.

        Raises:
            LangError: When ``owner`` is not a struct or has no such field.
        """
        if not is_struct(owner):
            raise LangError(f"{owner} is not a struct", line)
        for index, (name, ftype) in enumerate(owner.fields):
            if name == fname:
                return owner.elem_indices[index], ftype
        raise LangError(f"struct {owner} has no field {fname!r}", line)

    def generate(self) -> ir.Module:
        """Generate the IR module, attaching a source file to any error.

        Returns:
            The emitted LLVM IR module.

        Raises:
            LangError: On any compile error; its ``source`` is filled in with
                the file being generated when not already set.
        """
        try:
            return self.gen_program()
        except LangError as err:
            if err.source is None:
                err.source = self.current_source
            raise

    def gen_program(self) -> ir.Module:
        """Emit the whole module from the merged program.

        Resolves compile-time ``@if`` (seeding the target facts and flattening
        live branches), then registers structs, folds consts, declares globals
        and function signatures (handling ``@extern``, ``@static``, generic
        overload sets, and concrete overload sets under their mangled
        symbols), and finally generates a body for every non-generic,
        non-extern function.

        Returns:
            The completed LLVM IR module.

        Raises:
            LangError: On unresolved imports or any semantic error -- redefined
                names, conflicting externs, and the like.
        """
        if self.program.imports:
            raise LangError(
                "imports must be resolved before code generation (compile via the driver)",
                self.program.imports[0][1],
            )
        # Resolve compile-time @if first: seed the target facts its conditions
        # read, then splice each live branch's declarations into the program so
        # the loops below see a flat, target-specific set of declarations.
        self.seed_target_consts()
        self.flatten_conditionals()
        for decl in self.program.structs:
            self.current_source = decl.source
            if decl.name in TYPES or decl.name in RESERVED_TYPE_NAMES:
                raise LangError(f"type {decl.name!r} already defined", decl.line)
            if decl.static:
                key = (decl.source, decl.name)
                if key in self.static_structs:
                    raise LangError(f"type {decl.name!r} already defined", decl.line)
                self.static_structs[key] = decl
                self.symbol_bases[key] = self.static_base(decl.name, decl.source)
                continue
            if decl.name in self.struct_templates:
                raise LangError(f"type {decl.name!r} already defined", decl.line)
            self.struct_templates[decl.name] = decl
            self.used_symbols.add(decl.name)
        # The builtin struct templates (`iterator<T>`, `pair<K, V>`) fill in
        # after user structs, so a user declaration of the same name wins.
        for decl in BUILTIN_STRUCTS:
            if decl.name not in self.struct_templates:
                self.struct_templates[decl.name] = decl
                self.used_symbols.add(decl.name)
        # Type aliases are registered next (records only; resolved lazily on
        # use), so a const's or signature's type may name one.
        for alias in self.program.aliases:
            self.register_alias(alias)
        # Constants are folded before globals, so a global's type (or a later
        # const) may use one as an array size. They are evaluated in source
        # order, so a const may reference any declared earlier (as in C). The
        # built-in target facts were seeded above, so user consts may use them
        # (and may not shadow them).
        #
        # A const (or @static global) whose initializer names a function holds
        # that function's address -- a link-time constant -- but functions are
        # not declared until below, so its folding is deferred until they are.
        # A function-valued const can never be an array size, so nothing folded
        # here depends on it.
        func_names = {f.name for f in self.program.functions}
        deferred_consts = []
        deferred_const_names: set[str] = set()
        for const in self.program.consts:
            self.current_source = const.source
            if const.name in self.consts or const.name in deferred_const_names:
                raise LangError(f"constant {const.name!r} already defined", const.line)
            if self.initializer_names_function(
                const.value, func_names | deferred_const_names
            ):
                deferred_consts.append(const)
                deferred_const_names.add(const.name)
                continue
            self.consts[const.name] = self.fold_const_value(const)
            self.const_privacy[const.name] = (const.private, const.source)
        # Enums are registered after consts, so a member's value may use one;
        # they are folded in source order, so a member may reference any enum
        # member declared earlier (including earlier members of the same enum).
        for decl in self.program.enums:
            self.register_enum(decl)
        # An @static initializer may name a function (a constant function
        # pointer), so its evaluation waits until functions are declared below.
        deferred_static_inits = []
        deferred_static_globals = []
        for var in self.program.globals:
            self.current_source = var.source  # the type may name private structs
            # An unannotated @static global whose initializer names a function
            # infers its type from that function, which is not declared yet, so
            # defer the whole declaration until functions exist (below).
            if (
                var.static
                and var.type_name is None
                and self.initializer_names_function(
                    var.init, func_names | deferred_const_names
                )
            ):
                deferred_static_globals.append(var)
                continue
            if var.type_name is not None:
                # An initializer can supply an inferred outermost [] dimension.
                var_type = self.list_type_for(var.type_name, var.init, var.line)
            else:
                # An @static let with no annotation infers its type from the
                # initializer, like a local `let`. (The parser guarantees one is
                # present.) Functions are not declared yet, so an initializer
                # naming a function must be annotated instead.
                if isinstance(var.init, ArrayLit):
                    raise LangError(
                        "an array literal needs a type annotation, e.g. "
                        f"@static let {var.name}: int32[3] = [...]",
                        var.line,
                    )
                inferred = self.eval_const(var.init, var.line)
                if inferred.adaptable:
                    raise LangError(
                        f"type of {var.name!r} is ambiguous: the initializer is an "
                        f"untyped constant; annotate it "
                        f"(@static let {var.name}: int32 = ...) or cast the value",
                        var.line,
                    )
                var_type = inferred.type
            if var_type is VOID:
                raise LangError(
                    f"cannot declare a void variable {var.name!r}", var.line
                )
            if var.static:
                # File-scoped storage with its own definition; the mangled
                # symbol has internal linkage. An initializer must be constant;
                # without one the storage is zero-initialized.
                key = (var.source, var.name)
                if key in self.static_globals:
                    raise LangError(f"variable {var.name!r} already defined", var.line)
                symbol = self.static_base(var.name, var.source)
                glob = ir.GlobalVariable(self.module, var_type.ir, name=symbol)
                glob.linkage = self.shared_linkage(var.source)
                if var.init is not None:
                    deferred_static_inits.append((glob, var, var_type))
                else:
                    glob.initializer = ir.Constant(var_type.ir, None)
                self.static_globals[key] = (glob, var_type, var.volatile)
                self.global_privacy[var.name] = (var.private, var.source)
                continue
            if var.name in self.globals:
                if self.globals[var.name][1] != var_type:
                    raise LangError(
                        f"conflicting extern declarations for {var.name!r}", var.line
                    )
                continue
            if var.name in self.funcs:
                raise LangError(f"variable {var.name!r} already defined", var.line)
            # @symbol overrides the linker name; mcc still refers to it by var.name.
            glob = ir.GlobalVariable(
                self.module, var_type.ir, name=var.symbol or var.name
            )
            self.globals[var.name] = (glob, var_type, var.volatile)
            self.global_privacy[var.name] = (var.private, var.source)
            self.used_symbols.add(var.name)
        # Concrete overloading: pre-group plain declarations by (source, name)
        # so the plain-vs-mangled symbol choice is known before the first
        # member declares. Prototypes count -- a .mci stub's prototypes must
        # derive the same symbols the defining object emitted -- but a set
        # forms only when the declarations spell two or more DISTINCT
        # parameter lists: a same-signature prototype+definition pair is one
        # member (the classic forward declaration keeps its plain symbol).
        # A name with a single signature keeps its plain, C-linkable symbol;
        # only same-module sets of two or more mangle. @extern, @static,
        # generics, and tombstones never join the concrete grouping.
        plain_decls: dict[tuple[str | None, str], list[Func]] = {}
        for func in self.program.functions:
            if (
                func.removed_msg is None
                and not func.extern
                and not func.static
                and not func.type_params
            ):
                plain_decls.setdefault((func.source, func.name), []).append(func)
        overload_keys: set[tuple[str | None, str]] = set()
        for key, decls in plain_decls.items():
            if len(decls) < 2:
                continue
            self.current_source = key[0]  # signatures may name private structs
            sigs = {
                tuple(str(self.lang_type(t, f.line)) for _, t in f.params)
                for f in decls
            }
            if len(sigs) > 1:
                overload_keys.add(key)
        declared: set[tuple[str | None, str]] = set()
        for func in self.program.functions:
            if func.removed_msg is not None:
                # An @removed tombstone registers its name and message only.
                # It deliberately skips the declare path: the signature is
                # never resolved (no lang_type, no ir.Function, no entry in
                # signatures/hidden_ref) because every use errors before
                # lowering, and the tombstone must stay valid even when its
                # parameter types were deleted with the implementation. A body,
                # if the author kept one around, is never generated either.
                self.current_source = func.source
                key = (func.source, func.name)
                if func.name in self.templates:
                    # A live generic sharing the name would otherwise join an
                    # overload set and coexist silently.
                    raise LangError(
                        f"function {func.name!r} cannot be both @removed and "
                        "live: a tombstone replaces the whole overload set",
                        func.line,
                    )
                if (
                    func.name in self.funcs
                    or func.name in self.overloads
                    or func.name in self.globals
                    or func.name in self.removed
                    or key in declared
                ):
                    raise LangError(
                        f"function {func.name!r} already defined", func.line
                    )
                declared.add(key)
                self.removed[func.name] = func.removed_msg
                continue
            if func.extern:
                self.current_source = func.source  # signatures may name private structs
                ret = self.lang_type(func.ret_type, func.line)
                params = [self.lang_type(t, func.line) for _, t in func.params]
                self.check_collecting_decl(func, params)
                if func.name in self.extern_decls:
                    if self.signatures[func.name] != (ret, params, func.variadic):
                        raise LangError(
                            f"conflicting extern declarations for {func.name!r}",
                            func.line,
                        )
                    continue
                if (
                    func.name in self.funcs
                    or func.name in self.templates
                    or func.name in self.overloads
                    or func.name in self.globals
                    or func.name in self.removed
                ):
                    raise LangError(
                        f"function {func.name!r} already defined", func.line
                    )
                fnty = ir.FunctionType(
                    ret.ir, self.param_irs(params), var_arg=func.variadic
                )
                # @symbol overrides the linker name; mcc still calls it by func.name.
                fn = ir.Function(self.module, fnty, name=func.symbol or func.name)
                # @noalias/@nonnull are attribute-only, so allowed on @extern.
                self.mark_noalias(fn, func, params)
                self.mark_nonnull(fn, func, params)
                self.funcs[func.name] = fn
                self.signatures[func.name] = (ret, params, func.variadic)
                self.nonnull_ref[func.name] = self.nonnull_indices(func)
                self.func_privacy[func.name] = (func.private, func.source)
                self.extern_decls.add(func.name)
                self.used_symbols.add(func.name)
                if func.deprecated_msg is not None:
                    self.deprecated_syms[func.name] = func.deprecated_msg
                continue
            key = (func.source, func.name)
            self.current_source = func.source  # signatures may name private structs
            in_set = (
                key in overload_keys and not func.static and not func.type_params
            )
            is_overloadable = bool(func.type_params or in_set) and not func.static
            # The resolved parameter list doubles as the pairing key and the
            # mangled symbol's interior (generic and @static functions have
            # neither -- their parameters may name type parameters).
            params = params_key = None
            if not func.static and not func.type_params:
                params = [self.lang_type(t, func.line) for _, t in func.params]
                params_key = ", ".join(str(p) for p in params)
            if not is_overloadable:
                # A same-file redeclaration is allowed only as a pairable
                # forward declaration (at least one of the two a prototype);
                # the pair is checked in the concrete branch below.
                if key in declared and not self.can_pair_prototype(
                    func, params_key
                ):
                    prior = self.concrete_decls.get(func.name, {}).get(
                        params_key
                    )
                    if (
                        prior is not None
                        and not func.proto
                        and not prior.proto
                        and prior.source == func.source
                    ):
                        # Two definitions of one signature: return-type-only,
                        # marker-only, and annotation-only variants all spell
                        # the same parameter list, so the message names the
                        # shared signature.
                        raise LangError(
                            f"function '{func.name}({params_key})' already "
                            "defined; overloads must differ in parameter "
                            "types",
                            func.line,
                        )
                    raise LangError(
                        f"function {func.name!r} already defined", func.line
                    )
                declared.add(key)
            if func.static:
                self.symbol_bases[key] = self.static_base(func.name, func.source)
                if func.type_params:
                    self.check_template_collecting(func)
                    self.static_templates[key] = func
                    continue
                symbol = self.symbol_bases[key]
                ret = self.lang_type(func.ret_type, func.line)
                params = [self.lang_type(t, func.line) for _, t in func.params]
                self.check_collecting_decl(func, params)
                hidden = self.hidden_ref_indices(func, params)
                fnty = ir.FunctionType(
                    ret.ir, self.param_irs(params, hidden), var_arg=func.variadic
                )
                fn = ir.Function(self.module, fnty, name=symbol)
                self.link_shared(fn, func.source)
                self.mark_inline(fn, func)
                self.mark_noalias(fn, func, params)
                self.mark_nonnull(fn, func, params)
                self.funcs[symbol] = fn
                self.signatures[symbol] = (ret, params, func.variadic)
                self.hidden_ref[symbol] = hidden
                self.mut_ref[symbol] = self.mut_indices(func)
                self.nonnull_ref[symbol] = self.nonnull_indices(func)
                self.static_funcs[key] = symbol
                if func.deprecated_msg is not None:
                    self.deprecated_syms[symbol] = func.deprecated_msg
                continue
            if func.type_params:
                # Generic: no code yet -- instances are stamped out per call.
                # Several templates may share a name (an overload set), and a
                # template may join a concrete function or concrete set from
                # its own module (a mixed set: concrete candidates beat a
                # generic on an exact match via the rank tier). Cross-module
                # joining stays an error -- all overloads of a name live in
                # one defining module -- as does sharing a name with an
                # @extern (its C symbol is fixed).
                self.check_template_collecting(func)
                members = self.concrete_decls.get(func.name)
                if members:
                    self.check_mixed_set(func, members)
                elif func.name in self.funcs or func.name in self.overloads:
                    raise LangError(
                        f"function {func.name!r} already defined", func.line
                    )
                if func.name in self.removed:
                    # Joining an overload set with a tombstone would let the
                    # live overload coexist with the removal silently.
                    raise LangError(
                        f"function {func.name!r} cannot be both @removed and "
                        "live: a tombstone replaces the whole overload set",
                        func.line,
                    )
                overloads = self.templates.setdefault(func.name, [])
                if overloads:
                    base = f"{func.name}#{len(overloads)}"
                    while base in self.used_symbols:
                        base += "'"
                else:
                    base = func.name
                self.template_bases[id(func)] = base
                self.used_symbols.add(base)
                overloads.append(func)
                continue
            if in_set:
                # A member of a concrete overload set (two or more distinct
                # parameter lists on one name in one module, prototypes
                # included). Members link by a signature-derived mangled
                # symbol -- `f(int32, char*)`, the canonical str(LangType) of
                # the parameter types only: the return type never
                # distinguishes overloads, and const/mut markers and
                # @nonnull/@noalias annotations live outside the parameter
                # types, so attribute-only variants spell the same symbol and
                # collide as duplicates below.
                if func.name == "main":
                    # JIT and cc both resolve the plain `main` symbol.
                    raise LangError(
                        "function 'main' cannot be overloaded", func.line
                    )
                if func.variadic:
                    # The viability filter matches arity exactly; C-style
                    # variadics revisit when native variadics land.
                    raise LangError(
                        f"variadic function {func.name!r} cannot be overloaded",
                        func.line,
                    )
                if self.is_collecting_func(func, params):
                    # A collecting candidate would make arity-based viability
                    # ambiguous against the last-position rule; a later stage
                    # lifts this.
                    raise LangError(
                        f"collecting function {func.name!r} cannot be overloaded",
                        func.line,
                    )
                ret = self.lang_type(func.ret_type, func.line)
                member = self.concrete_decls.get(func.name, {}).get(params_key)
                if (
                    member is not None
                    and id(member) in self.overload_symbols
                    and self.can_pair_prototype(func, params_key)
                ):
                    # Per-signature forward declaration inside the set: the
                    # params-key selected the pair -- cross-source too, which
                    # is how a defining module's .mc completes its own .mci
                    # stub's prototypes member by member. A plain-symbol
                    # function from another module never pairs here (a set
                    # cannot extend a foreign single); it falls through to
                    # the collision error below.
                    self.pair_prototype(func, params_key, ret, params)
                    continue
                prior = self.overloads.get(func.name)
                templates = self.templates.get(func.name)
                if (
                    func.name in self.funcs
                    or func.name in self.globals
                    or func.name in self.removed
                    # All overloads of a name live in one defining module
                    # (its .mci counts as that module): a new signature from
                    # another file cannot extend the set...
                    or (prior is not None and prior[0].source != func.source)
                    # ...and a mixed generic/concrete set is same-module too.
                    or (templates is not None and templates[0].source != func.source)
                ):
                    raise LangError(
                        f"function {func.name!r} already defined", func.line
                    )
                if any(is_valist(p) for p in params):
                    # The pre-evaluate path cannot marshal a va_list (its
                    # passed form is derived from storage, not a value).
                    raise LangError(
                        f"function {func.name!r} cannot be overloaded: it "
                        "takes a va_list parameter",
                        func.line,
                    )
                symbol = f"{func.name}({params_key})"
                if symbol in self.funcs:
                    raise LangError(
                        f"function {symbol!r} already defined; overloads "
                        "must differ in parameter types",
                        func.line,
                    )
                hidden = self.hidden_ref_indices(func, params)
                fnty = ir.FunctionType(ret.ir, self.param_irs(params, hidden))
                fn = ir.Function(self.module, fnty, name=symbol)
                if not func.proto:
                    # A prototype emits an LLVM declaration (no body);
                    # linkonce_odr is only legal on definitions, so it keeps
                    # external linkage.
                    self.link_shared(fn, func.source)
                self.mark_inline(fn, func)
                self.mark_noalias(fn, func, params)
                self.mark_nonnull(fn, func, params)
                self.funcs[symbol] = fn
                self.signatures[symbol] = (ret, params, False)
                self.hidden_ref[symbol] = hidden
                self.mut_ref[symbol] = self.mut_indices(func)
                self.nonnull_ref[symbol] = self.nonnull_indices(func)
                self.overloads.setdefault(func.name, []).append(func)
                self.overload_symbols[id(func)] = symbol
                self.concrete_decls.setdefault(func.name, {})[params_key] = func
                self.used_symbols.add(symbol)
                continue
            if self.can_pair_prototype(func, params_key):
                # Forward declaration: check the pair, then absorb it -- the
                # signature's ir.Function (and registry entries) already stand.
                ret = self.lang_type(func.ret_type, func.line)
                self.pair_prototype(func, params_key, ret, params)
                continue
            if (
                func.name in self.funcs
                or func.name in self.overloads
                or func.name in self.globals
                or func.name in self.removed
            ):
                raise LangError(f"function {func.name!r} already defined", func.line)
            templates = self.templates.get(func.name)
            if templates is not None:
                # A single concrete declaration joining same-module generic
                # templates forms a mixed set; it keeps its plain symbol (the
                # symbol choice counts concrete signatures alone), and calls
                # route through overload resolution. The concrete side must
                # still be overloadable at all.
                if templates[0].source != func.source:
                    raise LangError(
                        f"function {func.name!r} already defined", func.line
                    )
                if func.name == "main":
                    raise LangError(
                        "function 'main' cannot be overloaded", func.line
                    )
                if func.variadic:
                    raise LangError(
                        f"variadic function {func.name!r} cannot be overloaded",
                        func.line,
                    )
                if self.is_collecting_func(func, params):
                    raise LangError(
                        f"collecting function {func.name!r} cannot be overloaded",
                        func.line,
                    )
                if any(is_valist(p) for p in params):
                    raise LangError(
                        f"function {func.name!r} cannot be overloaded: it "
                        "takes a va_list parameter",
                        func.line,
                    )
            self.check_collecting_decl(func, params)
            self.concrete_decls.setdefault(func.name, {})[params_key] = func
            self.func_privacy[func.name] = (func.private, func.source)
            self.used_symbols.add(func.name)
            ret = self.lang_type(func.ret_type, func.line)
            hidden = self.hidden_ref_indices(func, params)
            fnty = ir.FunctionType(
                ret.ir, self.param_irs(params, hidden), var_arg=func.variadic
            )
            fn = ir.Function(self.module, fnty, name=func.name)
            if not func.proto:
                # A prototype emits an LLVM declaration (no body); linkonce_odr
                # is only legal on definitions, so it keeps external linkage.
                self.link_shared(fn, func.source)
            self.mark_inline(fn, func)
            self.mark_noalias(fn, func, params)
            self.mark_nonnull(fn, func, params)
            self.funcs[func.name] = fn
            self.signatures[func.name] = (ret, params, func.variadic)
            self.hidden_ref[func.name] = hidden
            self.mut_ref[func.name] = self.mut_indices(func)
            self.nonnull_ref[func.name] = self.nonnull_indices(func)
            if func.deprecated_msg is not None:
                # A proto registers too: @deprecated on an interface stub's
                # prototype warns the importer's call sites.
                self.deprecated_syms[func.name] = func.deprecated_msg
        # Functions are declared now, so consts and @static globals that name a
        # function can be folded to its address. Consts come first, since a
        # deferred global's initializer may reference one.
        for const in deferred_consts:
            self.current_source = const.source
            self.consts[const.name] = self.fold_const_value(const)
            self.const_privacy[const.name] = (const.private, const.source)
        for var in deferred_static_globals:
            self.current_source = var.source
            self.declare_static_global(var)
        for glob, var, var_type in deferred_static_inits:
            self.current_source = var.source
            glob.initializer = self.const_initializer(var.init, var_type, var.line)
        # Error directives run once every type, const, enum, and global is
        # known, so @static_assert conditions can fold sizeof/alignof/offsetof
        # and const/enum references; before function bodies, so a failed layout
        # assertion aborts the build without wasting work on codegen.
        self.check_directives()
        for func in self.program.functions:
            if (
                not func.type_params
                and not func.extern
                and not func.proto
                and func.removed_msg is None
            ):
                # A proto is skipped: its bodyless ir.Function is already an
                # LLVM declaration; the definition lives in another object.
                # An @removed tombstone is skipped too: it was never declared,
                # and a body the author kept around is dead.
                symbol = self.overload_symbols.get(
                    id(func),
                    self.static_funcs.get((func.source, func.name), func.name),
                )
                ret, params, _ = self.signatures[symbol]
                self.gen_function(func, self.funcs[symbol], ret, params)
        return self.module

    def initializer_names_function(self, expr, names: set[str]) -> bool:
        """Whether a const/``@static`` initializer refers to a deferred name.

        ``names`` holds every function (and already-deferred const) whose value
        is not available until functions are declared. A bare name reference to
        one -- directly, or as an element of an array literal -- means this
        initializer must be folded in that later pass too.

        Args:
            expr: The initializer expression (may be ``None``).
            names: The names whose folding is deferred.

        Returns:
            ``True`` when the initializer references a deferred name.
        """
        if isinstance(expr, Var):
            return expr.name in names
        if isinstance(expr, ArrayLit):
            return any(
                self.initializer_names_function(e, names) for e in expr.elements
            )
        return False

    def fold_const_value(self, const) -> TypedValue:
        """Fold a ``const`` initializer, coercing to its annotation if given.

        Args:
            const: The ``Const`` declaration.

        Returns:
            The folded value, coerced to the declared type when one is written.
        """
        value = self.eval_const(const.value, const.line)
        if const.type_name is not None:
            declared = self.lang_type(const.type_name, const.line)
            value = self.const_coerce(value, declared, const.line, f"const {const.name}")
        return value

    def declare_static_global(self, var):
        """Declare an unannotated ``@static`` global whose type is inferred.

        Used in the post-function-declaration pass for a global whose
        initializer names a function: the type comes from that function value,
        and the storage is created and initialized to its address.

        Args:
            var: The ``GlobalVar`` declaration.

        Raises:
            LangError: When the inferred type is ambiguous or ``void``, or the
                global is already defined.
        """
        inferred = self.eval_const(var.init, var.line)
        if inferred.adaptable:
            raise LangError(
                f"type of {var.name!r} is ambiguous: the initializer is an "
                f"untyped constant; annotate it "
                f"(@static let {var.name}: int32 = ...) or cast the value",
                var.line,
            )
        var_type = inferred.type
        if var_type is VOID:
            raise LangError(f"cannot declare a void variable {var.name!r}", var.line)
        key = (var.source, var.name)
        if key in self.static_globals:
            raise LangError(f"variable {var.name!r} already defined", var.line)
        symbol = self.static_base(var.name, var.source)
        glob = ir.GlobalVariable(self.module, var_type.ir, name=symbol)
        glob.linkage = self.shared_linkage(var.source)
        glob.initializer = self.const_initializer(var.init, var_type, var.line)
        self.static_globals[key] = (glob, var_type, var.volatile)
        self.global_privacy[var.name] = (var.private, var.source)

    def gen_function(
        self, func: Func, fn: ir.Function, ret: LangType, params: list[LangType]
    ):
        """Emit the body of one already-declared function.

        Sets up the entry block, spills parameters to allocas, generates the
        body, and supplies an implicit ``return`` for ``void`` and for ``main``.

        Args:
            func: The AST function node.
            fn: The IR function to fill in.
            ret: The resolved return type.
            params: The resolved parameter types.

        Raises:
            LangError: When a non-void, non-``main`` function may fall off its
                end without returning.
        """
        self.ret_type = ret
        self.current_source = func.source
        self.current_variadic = func.variadic  # gates va_start
        self.builder = ir.IRBuilder(fn.append_basic_block("entry"))
        self.locals = {}
        self.scope_names = set()  # the body block resets this, but be explicit
        self.defer_stack = []
        self.loops = []  # break/continue cannot escape into a caller's loop
        self.block_exprs = []  # emit cannot escape into a caller's block-expr
        hidden = self.hidden_ref_indices(func, params)
        self.const_locals = set(func.const_params)
        self.mut_locals = set(func.mut_params)
        self.nonnull_locals = set(func.nonnull_params)
        self.narrowed_nonnull = set()
        self.addr_taken = set()
        collect_addr_taken(func.body, self.addr_taken)
        for i, ((pname, _), ptype, arg) in enumerate(zip(func.params, params, fn.args)):
            arg.name = pname
            if i in hidden:
                # The value arrives as a pointer to the caller's storage; bind
                # the local straight to it (no copy). For const the promise not
                # to mutate makes sharing safe; for mut the sharing is the
                # point -- reads load through it exactly like an alloca slot,
                # and writes land in the caller's variable.
                self.locals[pname] = (arg, ptype)
                continue
            slot = self.builder.alloca(arg.type, name=pname)
            if over_aligned(ptype):
                slot.align = type_align(ptype)
            self.builder.store(arg, slot)
            self.locals[pname] = (slot, ptype)
        self.gen_block(func.body)
        if not self.builder.block.is_terminated:
            if ret is VOID:
                self.builder.ret_void()
            elif func.name == "main":
                self.builder.ret(ir.Constant(ret.ir, 0))
            else:
                raise LangError(
                    f"function {func.name!r} may end without a return", func.line
                )

    def gen_block(self, statements: list):
        """Generate a block of statements in its own name scope.

        Outer names stay visible, names declared here vanish at the end (an
        inner declaration may shadow an outer one), and the block's ``defer``
        actions run in LIFO order if control reaches the end. Allocas live for
        the whole function regardless, so this governs only name visibility.

        Args:
            statements: The statements to emit.
        """
        # Each block is its own scope: outer names stay visible, names declared
        # here vanish at the end, and an inner declaration may shadow an outer
        # one (the outer binding is restored on exit). Allocas live for the
        # whole function regardless, so this only governs name visibility.
        outer_locals, outer_names = dict(self.locals), self.scope_names
        outer_narrowed = set(self.narrowed_nonnull)
        self.scope_names = set()
        self.defer_stack.append([])
        try:
            for stmt in statements:
                if self.builder.block.is_terminated:
                    break  # unreachable code after return/break/continue
                self.gen_statement(stmt)
            # Reached the end without diverging: run this block's defers (LIFO).
            # An early return/break/continue already ran them on its own path.
            if not self.builder.block.is_terminated:
                self.run_deferred_scope(self.defer_stack[-1])
        finally:
            self.defer_stack.pop()
            self.locals, self.scope_names = outer_locals, outer_names
            # Narrowed facts established inside the block (early-guard
            # narrowing, shadowed names) end with it; invalidations from
            # inside persist outward. Intersecting achieves both.
            self.narrowed_nonnull &= outer_narrowed

    def run_deferred_scope(self, scope: list):
        """Emit one block's deferred actions, last-registered first.

        Each body runs while the block's locals are still in scope, so it can
        refer to them.

        Args:
            scope: The list of deferred action bodies for one block.
        """
        for body in reversed(scope):
            if self.builder.block.is_terminated:
                break
            self.gen_block(body)

    def run_defers_through(self, depth: int):
        """Unwind deferred scopes from the innermost down to a given depth.

        Emits each in LIFO order -- used when control jumps out of several
        blocks at once (a ``return`` unwinds all; ``break``/``continue`` to the
        loop body). The stack is snapshotted first, since emitting a body pushes
        scopes.

        Args:
            depth: The depth to stop above; scopes at indices ``>= depth`` are
                unwound.
        """
        for scope in reversed([list(s) for s in self.defer_stack[depth:]]):
            if self.builder.block.is_terminated:
                break
            self.run_deferred_scope(scope)

    def entry_alloca(self, ir_type, name: str = ""):
        """Allocate a slot in the function's entry block.

        Unlike an ordinary local -- declared and used in source order, so its
        alloca naturally dominates its uses -- a block-expression's result slot
        is written in branch arms and read at the block's end. Putting it in the
        entry block guarantees it dominates every such use.

        Args:
            ir_type: The LLVM type to allocate.
            name: An optional name for the slot.

        Returns:
            The alloca instruction.
        """
        # goto_entry_block restores the current insertion point on exit, so the
        # in-progress block being generated is left undisturbed.
        with self.builder.goto_entry_block():
            return self.builder.alloca(ir_type, name=name)

    def gen_block_expr(self, expr: BlockExpr) -> TypedValue:
        """Lower a block-expression ``{ ...; emit v; }`` to a value.

        The trivial ``{ emit E; }`` is just ``E`` -- evaluated directly so an
        untyped constant stays adaptable (``let n: uint64 = { emit 1; };``). A
        larger block runs its statements in a fresh scope and yields the value an
        ``emit`` hands out, lowered through a result slot and a continuation
        block (see :class:`BlockExprCtx`). Like a function, it must ``emit`` on
        the path that reaches its end; a branch-only ``emit`` needs a trailing
        one, since (as with ``if``/``else`` and ``return``) the two arms are not
        treated as a guaranteed value.

        Args:
            expr: The ``BlockExpr`` node.

        Returns:
            The block's value as a ``TypedValue``.

        Raises:
            LangError: When the block may fall off its end without emitting, or
                never emits at all.
        """
        body = expr.body
        if len(body) == 1 and isinstance(body[0], Emit):
            return self.gen_expr(body[0].value)
        cont_bb = self.builder.append_basic_block("blockexpr.end")
        ctx = BlockExprCtx(cont_bb=cont_bb, defer_depth=len(self.defer_stack))
        self.block_exprs.append(ctx)
        try:
            self.gen_block(body)
        finally:
            self.block_exprs.pop()
        if not self.builder.block.is_terminated:
            raise LangError("block expression may end without an emit", expr.line)
        self.builder.position_at_end(cont_bb)
        if ctx.type is None:
            # Every path diverged (e.g. via return) without an emit, so the
            # block has no value and its continuation is unreachable.
            raise LangError("block expression never emits a value", expr.line)
        return TypedValue(self.gen_load(ctx.slot), ctx.type)

    def gen_struct_lit(self, expr: StructLit) -> TypedValue:
        """Lower a struct literal ``struct Name { field = expr, ... }``.

        Allocates a temporary, zero-initializes it (so omitted fields read as
        zero), stores each named field, and yields the struct by value. The
        field expressions are coerced to their declared types, so an untyped
        integer constant adapts as it would in an assignment. A generic struct's
        type arguments are inferred from the field values when none are given.

        Args:
            expr: The ``StructLit`` node.

        Returns:
            The constructed struct as a ``TypedValue``.

        Raises:
            LangError: When the type is not a struct, a field is unknown, a field
                is given twice, or a type parameter cannot be inferred.
        """
        ref = expr.type_ref
        # Evaluate each field's value once, in source order, rejecting repeats.
        seen: set[str] = set()
        items = []  # (field name, value, source line)
        for fname, value_expr in expr.fields:
            if fname in seen:
                raise LangError(
                    f"field {fname!r} is set twice in the struct literal", expr.line
                )
            seen.add(fname)
            items.append((fname, self.gen_expr(value_expr), value_expr.line))

        decl = self.lookup_struct_decl(ref.name)
        if decl is not None and decl.type_params and not ref.args:
            struct_type = self.infer_struct_lit_type(decl, items, expr.line)
        else:
            struct_type = self.lang_type(ref, expr.line)
        if not is_struct(struct_type):
            raise LangError(
                f"a struct literal needs a struct type, not {struct_type}", expr.line
            )
        if is_union(struct_type) and len(items) > 1:
            # The members share one storage, so writing two would just
            # overwrite; the literal names the (at most one) live member.
            raise LangError("a union literal sets at most one member", expr.line)

        slot = self.builder.alloca(struct_type.ir)
        if over_aligned(struct_type):
            slot.align = type_align(struct_type)
        self.builder.store(ir.Constant(struct_type.ir, None), slot)  # zero omitted fields
        for fname, tv, line in items:
            self.store_struct_field(slot, struct_type, fname, tv, line, "field")
        # Fill any omitted field that declares a default; the rest keep the zero.
        for fname, default_expr in self.struct_defaults(struct_type).items():
            if fname in seen:
                continue
            self.store_struct_field(
                slot, struct_type, fname, self.gen_expr(default_expr),
                default_expr.line, "default for field",
            )
        return TypedValue(self.builder.load(slot), struct_type)

    def store_struct_field(self, slot, struct_type, fname, tv, line, what):
        """Coerce ``tv`` to field ``fname``'s type and store it into ``slot``."""
        index, ftype = self.struct_field(struct_type, fname, line)
        if is_flexible_array(ftype):
            raise LangError(
                f"{what} {fname!r} is a flexible array member with no storage; "
                "allocate the struct with trailing room and fill it through "
                f"the {fname!r} pointer",
                line,
            )
        if is_union(struct_type):
            # Every union member lives at offset 0: address it by casting the
            # union's storage to the member type instead of a GEP.
            addr = self.builder.bitcast(slot, ftype.ir.as_pointer())
        else:
            addr = self.builder.gep(
                slot, [I32_ZERO, ir.Constant(ir.IntType(32), index)], inbounds=True
            )
        value = self.coerce(tv, ftype, line, f"{what} {fname!r}")
        self.builder.store(value.value, addr)

    def struct_defaults(self, struct_type) -> dict:
        """Return a struct instance's default field values, including inherited.

        ``extends`` lays base fields first, so a derived struct's literal can
        rely on the base's defaults too; a derived default overrides a base one
        of the same name. The merged map is resolved per instance in
        :meth:`instantiate_struct` rather than by walking declarations here,
        because a bare parameter as the base (``struct entry<T> extends T``)
        has no base declaration to recurse into by name -- the base struct is
        only known once ``T`` is bound.

        Args:
            struct_type: The resolved struct ``LangType``.

        Returns:
            A ``{field name: default-value expression}`` map (empty for types
            that carry no defaults, e.g. slices).
        """
        return getattr(struct_type, "defaults", {})

    def init_struct_defaults(self, slot, struct_type):
        """Default-initialize a plainly-declared struct ``let s: struct T;``.

        When the struct declares any default field values (its own or inherited),
        zero the storage and store each default -- the same result as the empty
        literal ``struct T { }``. A struct with no defaults is left untouched, so
        it keeps the uninitialized behavior of a bare ``let``.

        Args:
            slot: The variable's alloca.
            struct_type: The resolved struct ``LangType``.
        """
        defaults = self.struct_defaults(struct_type)
        if not defaults:
            return
        self.builder.store(ir.Constant(struct_type.ir, None), slot)  # zero first
        for fname, default_expr in defaults.items():
            self.store_struct_field(
                slot, struct_type, fname, self.gen_expr(default_expr),
                default_expr.line, "default for field",
            )

    def lookup_struct_decl(self, name: str) -> "StructDecl | None":
        """Find a struct declaration by name, preferring a file-scoped one.

        A same-named ``@static`` struct in the current file shadows a global
        template, exactly as :meth:`lang_type` resolves struct types.

        Args:
            name: The struct's name.

        Returns:
            The matching ``StructDecl``, or ``None`` when no struct has that
            name in scope.
        """
        decl = self.static_structs.get((self.current_source, name))
        if decl is not None:
            return decl
        return self.struct_templates.get(name)

    def infer_struct_lit_type(self, decl, items, line: int) -> LangType:
        """Infer a generic struct's type arguments from a literal's fields.

        Unifies each provided field's value type against the field's declared
        pattern -- ``start: T`` against an ``int32`` value binds ``T = int32`` --
        the same way a generic function call infers from its arguments. Only
        *typed* (non-adaptable) field values bind a parameter; an untyped
        constant carries no real type (just a default it would adapt to), so
        letting it anchor ``T`` would be the same forbidden guess as ``let a =
        0``. It still adapts later, in :meth:`store_struct_field`, once a typed
        field, explicit type argument, or declared default has fixed the
        parameter. ``null`` never contributes either. A declared default
        (``struct range<T = int64>``) fills a parameter the typed fields left
        unbound; a parameter still unbound after that is an error, just as the
        untyped ``let`` is ambiguous -- resolve it with explicit type args, a
        typed field value, or a declared default.

        Args:
            decl: The generic struct's declaration.
            items: The literal's ``(field name, value, line)`` triples.
            line: Source line of the literal, for diagnostics.

        Returns:
            The instantiated (monomorphized) struct ``LangType``.

        Raises:
            LangError: When a field is unknown or a type parameter cannot be
                inferred from the given fields.
        """
        self.check_access(decl.private, decl.source, f"struct {decl.name!r}", line)
        patterns = dict(decl.fields)
        for fname, _tv, fline in items:
            if fname not in patterns:
                raise LangError(f"struct {decl.name!r} has no field {fname!r}", fline)
        bindings: dict[str, LangType] = {}
        context = f"struct literal {decl.name!r}"
        for fname, tv, fline in items:
            # Skip untyped constants (adaptable) and null: neither carries a type
            # that may anchor a parameter -- only typed values bind it.
            if not tv.adaptable and tv.type is not NULLT:
                self.unify(
                    patterns[fname], tv.type, decl.type_params,
                    bindings, True, context, fline,
                )
        # Declared defaults fill whatever the typed fields left unbound; an
        # untyped field value then adapts to the filled type in
        # store_struct_field, exactly as it would to an inferred one.
        self.fill_default_bindings(decl, bindings, line)
        missing = [t for t in decl.type_params if t not in bindings]
        if missing:
            raise LangError(
                f"cannot infer type parameter(s) {', '.join(missing)} for struct "
                f"{decl.name!r} from its fields; specify them explicitly, e.g. "
                f"struct {decl.name}<int32> {{ ... }}",
                line,
            )
        args = tuple(bindings[t] for t in decl.type_params)
        return self.instantiate_struct(decl, args, line)

    def bind_local(self, name: str, slot, lang_type: LangType):
        """Record a local in the current scope.

        The name shadows any outer one until the enclosing block ends;
        redeclaring it in the same block is an error (checked by the caller
        against ``scope_names``).

        Args:
            name: The local variable's name.
            slot: The alloca holding the variable.
            lang_type: The variable's type.
        """
        self.locals[name] = (slot, lang_type)
        self.scope_names.add(name)
        # A shadowing binding is a fresh, possibly-null variable: the shadowed
        # @nonnull parameter's fact must not transfer to it. Ditto for a
        # flow-narrowed fact on the shadowed name.
        self.nonnull_locals.discard(name)
        self.narrowed_nonnull.discard(name)

    def coerce(
        self, tv: TypedValue, expected: LangType, line: int, context: str
    ) -> TypedValue:
        """Check a value against an expected type, adapting untyped constants.

        An adaptable integer constant may take on any integer type its value
        fits into (so ``let x: uint64 = 5;`` works), and ``null`` adapts to any
        pointer or function-pointer type. Any pointer coerces to ``uint8*`` (raw
        memory, like C's ``void*``). Other values never convert implicitly --
        use ``as``.

        Args:
            tv: The value to check or adapt.
            expected: The required type.
            line: Source line for diagnostics.
            context: A label describing the site, for the error message.

        Returns:
            ``tv`` unchanged, or a new ``TypedValue`` adapted to ``expected``.

        Raises:
            LangError: When the value cannot match ``expected``, or an adaptable
                constant is out of range.
        """
        # Range-check adaptable integer constants first, before the
        # same-type early return below. Their type is only a default placeholder
        # (the narrowest of int32/int64/uint64 that fits the value), so a value
        # too big for `expected` must be caught here -- even when the types match
        # -- or it silently truncates at IR emission. A `char` *constant* adapts
        # the same way (a one-byte literal landing in a uint8/int slot); a char
        # *variable* is not a constant, so it stays strict (an explicit `as`).
        if (
            (tv.adaptable or tv.type == CHAR)
            and isinstance(tv.value, ir.Constant)
            and is_integer(tv.type)
            and is_integer(expected)
            and isinstance(tv.value.constant, int)
        ):
            width = expected.ir.width
            if expected.signed:
                lo, hi = -(1 << (width - 1)), 1 << (width - 1)
            else:
                lo, hi = 0, 1 << width
            if lo <= tv.value.constant < hi:
                return TypedValue(ir.Constant(expected.ir, tv.value.constant), expected)
            raise LangError(
                f"constant {tv.value.constant} is out of range for {expected}", line
            )
        if tv.type == expected:
            return tv
        if tv.type is NULLT and (is_pointer(expected) or is_function(expected)):
            return TypedValue(ir.Constant(expected.ir, None), expected)
        if expected == RAWPTR and is_pointer(tv.type):
            return TypedValue(self.builder.bitcast(tv.value, RAWPTR.ir), RAWPTR)
        # A value initializes a read-only `const T` target by adding const; the
        # representation is unchanged, so the value passes through retyped.
        if expected.const and strip_const(expected) == tv.type:
            return TypedValue(tv.value, expected)
        # A mutable slice<T> widens to its read-only slice<const T> form: adding
        # const is always safe, and the two share one LLVM layout, so the value
        # passes through unchanged. (Never the reverse -- that would drop const.)
        if (
            is_slice(expected)
            and is_slice(tv.type)
            and expected.args[0] == const_of(tv.type.args[0])
        ):
            return TypedValue(tv.value, expected)
        # An `any` target boxes the value implicitly, right here at the coerce
        # choke point, so assignment, argument passing, return, and stores all
        # box the same way. An adaptable literal was not captured by the
        # integer branch above (`any` is not an integer type), so it anchors
        # at its default placeholder type -- `5` boxes as int32, the same rule
        # call-site inference uses. `any` to `any` already returned above.
        if is_any(expected):
            boxed = self.gen_box_any(tv, line)
            if expected is ANY:
                return boxed
            return TypedValue(boxed.value, expected)  # the const any form
        raise LangError(f"{context}: expected {expected}, got {tv.type}", line)

    def widen_operand(
        self, tv: TypedValue, target: LangType, line: int, context: str
    ) -> TypedValue:
        """Coerce a binary operand, allowing lossless same-signedness widening.

        Unlike :meth:`coerce` (used for assignment, ``return``, and arguments,
        which keep widening explicit), arithmetic widens a narrower integer to a
        wider one of the same signedness so an expression like ``a + b * c`` need
        not cast every term. Narrowing and mixed signedness still raise, via the
        fallback to :meth:`coerce`.

        Args:
            tv: The operand to widen or check.
            target: The type to bring it to.
            line: Source line for diagnostics.
            context: A label describing the site, for the error message.

        Returns:
            ``tv`` widened to ``target`` (or unchanged when already that type).
        """
        if (
            is_integer(tv.type)
            and is_integer(target)
            and tv.type.signed == target.signed
            and tv.type.ir.width < target.ir.width
        ):
            if isinstance(tv.value, ir.Constant):  # fold without a builder
                return TypedValue(ir.Constant(target.ir, tv.value.constant), target)
            extend = self.builder.sext if target.signed else self.builder.zext
            return TypedValue(extend(tv.value, target.ir), target)
        return self.coerce(tv, target, line, context)

    def gen_load(
        self, addr, *, align: int | None = None, volatile: bool = False, name: str = ""
    ) -> ir.Instruction:
        """Emit a load, using a volatile load when requested.

        Args:
            addr: The pointer to load from.
            align: Explicit alignment, or ``None`` for the default.
            volatile: Whether the load must not be optimized away.
            name: An optional name for the result value.

        Returns:
            The load instruction.
        """
        if not volatile:
            return self.builder.load(addr, name=name, align=align)
        instr = VolatileLoad(self.builder.block, addr, name=name)
        instr.align = align
        self.builder._insert(instr)
        return instr

    def gen_store(
        self, value, addr, *, align: int | None = None, volatile: bool = False
    ):
        """Emit a store, using a volatile store when requested.

        Args:
            value: The value to store.
            addr: The pointer to store into.
            align: Explicit alignment, or ``None`` for the default.
            volatile: Whether the store must not be optimized away.
        """
        if not volatile:
            self.builder.store(value, addr, align=align)
            return
        instr = VolatileStore(self.builder.block, value, addr)
        instr.align = align
        self.builder._insert(instr)

    def gen_statement(self, stmt):
        """Emit code for one statement node.

        Dispatches on the statement type: returns (with ``defer`` unwinding),
        ``let`` and the assignment/store forms, control flow (``if``,
        ``while``/``until``, ``case``, ``for``), ``break``/``continue`` (running
        intervening defers), ``defer`` registration, bare blocks, and expression
        statements.

        Args:
            stmt: The statement AST node to generate.

        Raises:
            LangError: On a type mismatch or misuse (e.g. ``break`` outside a
                loop), surfaced from the per-statement handling.
        """
        if isinstance(stmt, Return):
            if stmt.value is None:
                if self.ret_type is not VOID:
                    raise LangError(f"return needs a {self.ret_type} value", stmt.line)
                self.run_defers_through(0)  # all enclosing blocks
                if not self.builder.block.is_terminated:
                    self.builder.ret_void()
            else:
                # Evaluate the result before the defers run, so a defer that
                # frees a buffer cannot clobber what is being returned.
                if self.str_literal_adapts(stmt.value, self.ret_type):
                    tv = self.gen_borrow_slice(stmt.value, self.ret_type, stmt.line)
                else:
                    tv = self.gen_expr(stmt.value)
                    if tv.type is VOID:
                        # `return f();` where f is void: there is no void value to
                        # return (matching `let x = f();`). Call f as a statement
                        # and use a bare `return;` instead.
                        raise LangError("cannot return a void value", stmt.line)
                    tv = self.coerce(tv, self.ret_type, stmt.line, "return value")
                self.run_defers_through(0)
                if not self.builder.block.is_terminated:
                    self.builder.ret(tv.value)
        elif isinstance(stmt, Emit):
            if not self.block_exprs:
                raise LangError(
                    "emit outside a block expression; did you mean return?", stmt.line
                )
            ctx = self.block_exprs[-1]
            # Evaluate the value before the defers run, so a defer cannot clobber
            # what is being emitted (as with a return value).
            tv = self.gen_expr(stmt.value)
            if ctx.type is None:
                # The first emit fixes the block's type and result slot. An
                # untyped constant resolves to its own type (int32/float64);
                # `null` has no inferable type, so it must be cast.
                if tv.type is NULLT:
                    raise LangError(
                        "cannot infer the type of `emit null`; cast it, "
                        "e.g. emit null as uint8*",
                        stmt.line,
                    )
                ctx.type = tv.type
                ctx.slot = self.entry_alloca(ctx.type.ir)
            else:
                tv = self.coerce(tv, ctx.type, stmt.line, "emit")
            self.gen_store(tv.value, ctx.slot)
            self.run_defers_through(ctx.defer_depth)
            if not self.builder.block.is_terminated:
                self.builder.branch(ctx.cont_bb)
        elif isinstance(stmt, Let):
            if stmt.name in self.scope_names:
                raise LangError(
                    f"variable {stmt.name!r} already declared in this scope", stmt.line
                )
            if stmt.value is None:  # let x: T; -- uninitialized, like a C local
                declared = self.lang_type(stmt.type_name, stmt.line)
                if declared is VOID:
                    raise LangError("cannot declare a void variable", stmt.line)
                slot = self.builder.alloca(declared.ir, name=stmt.name)
                if over_aligned(declared):
                    slot.align = type_align(declared)
                elif is_valist(declared):
                    self.require_valist(stmt.line)
                    slot.align = self.va_list_align  # ABI alignment for va_start
                # A struct that declares default field values is default-
                # initialized here (zero, then its defaults) rather than left
                # uninitialized -- so `let c: struct config;` matches
                # `let c = struct config { };`. Structs with no defaults keep the
                # uninitialized behavior, as does every other type.
                if is_struct(declared):
                    self.init_struct_defaults(slot, declared)
                self.bind_local(stmt.name, slot, declared)
                return
            if isinstance(stmt.value, ArrayLit):  # let xs: T[N] = [...]
                if stmt.type_name is None:
                    raise LangError(
                        "an array literal needs a type annotation, "
                        "e.g. let xs: int32[3] = [...]",
                        stmt.line,
                    )
                declared = self.list_type_for(stmt.type_name, stmt.value, stmt.line)
                if not is_array(declared):
                    raise LangError(
                        f"an array literal cannot initialize a {declared}", stmt.line
                    )
                slot = self.builder.alloca(declared.ir, name=stmt.name)
                self.store_list_literal(slot, stmt.value, declared, stmt.line)
                self.bind_local(stmt.name, slot, declared)
                return
            # A string literal is a uint8[N] byte array (NUL included). Bound to
            # an array variable (declared `uint8[...]` or left to inference), it
            # materializes as an owned copy -- so `len` works and it can be
            # mutated. Declared as a pointer (`let s: uint8* = "..."`, or a
            # pointer alias), it keeps the old decay to a uint8* into the shared
            # constant (no copy), which the generic path below handles.
            if isinstance(stmt.value, StrLit):
                if stmt.type_name is None:
                    declared = self.string_array_type(stmt.value.value)
                else:
                    declared = self.list_type_for(stmt.type_name, stmt.value, stmt.line)
                if is_array(declared):
                    declared = self.string_array_let_type(
                        declared, stmt.value.value, stmt.line
                    )
                    slot = self.builder.alloca(declared.ir, name=stmt.name)
                    self.store_string_literal(
                        slot, stmt.value.value, declared, stmt.line
                    )
                    self.bind_local(stmt.name, slot, declared)
                    return
                if self.str_literal_adapts(stmt.value, declared):
                    # `let s: slice<char> = "..."`: the literal adapts to a char
                    # slice (Stage 4), borrowing the constant's bytes (NUL dropped).
                    tv = self.gen_borrow_slice(stmt.value, declared, stmt.line)
                    slot = self.builder.alloca(declared.ir, name=stmt.name)
                    self.builder.store(tv.value, slot)
                    self.bind_local(stmt.name, slot, declared)
                    return
            if isinstance(stmt.value, Ternary) and stmt.type_name is not None:
                declared = self.lang_type(stmt.type_name, stmt.line)
                if self.str_literal_adapts(stmt.value, declared):
                    # `let s: slice<char> = cond ? "a" : "b";`: every arm is a
                    # string literal, so the ternary adapts arm by arm (Stage
                    # 4), each arm borrowing its constant's bytes (NUL dropped).
                    tv = self.gen_borrow_slice(stmt.value, declared, stmt.line)
                    slot = self.builder.alloca(declared.ir, name=stmt.name)
                    self.builder.store(tv.value, slot)
                    self.bind_local(stmt.name, slot, declared)
                    return
            tv = self.gen_expr(stmt.value)
            if stmt.type_name is not None:
                declared = self.lang_type(stmt.type_name, stmt.line)
                if declared is VOID:
                    raise LangError("cannot declare a void variable", stmt.line)
                if is_array(declared):
                    raise LangError(
                        f"an array variable is initialized from an array literal, "
                        f"not a {tv.type}",
                        stmt.line,
                    )
                tv = self.coerce(tv, declared, stmt.line, f"let {stmt.name}")
            elif tv.adaptable:
                raise LangError(
                    f"type of {stmt.name!r} is ambiguous: the value is an untyped "
                    f"constant; annotate the variable "
                    f"(let {stmt.name}: int32 = ...) or cast the value (... as int32)",
                    stmt.line,
                )
            elif tv.type is VOID:
                raise LangError(
                    f"cannot assign a void value to {stmt.name!r}", stmt.line
                )
            slot = self.builder.alloca(tv.type.ir, name=stmt.name)
            if over_aligned(tv.type):
                slot.align = type_align(tv.type)
            self.builder.store(tv.value, slot)
            self.bind_local(stmt.name, slot, tv.type)
            # Fact-seeding through let: a pointer local initialized from a
            # provably non-null value (`let q = p!`, `let q = p` under a
            # guard, `let s: uint8* = "..."`) starts flow-narrowed, under
            # the usual eligibility rules (never a name whose address is
            # taken somewhere, nor one shadowing a mut parameter).
            if (
                is_pointer(tv.type)
                and stmt.name not in self.addr_taken
                and stmt.name not in self.mut_locals
                and self.proves_nonnull(stmt.value)
            ):
                self.narrowed_nonnull.add(stmt.name)
        elif isinstance(stmt, Assign):
            if stmt.name in self.const_locals:
                raise LangError(
                    f"cannot assign to const parameter {stmt.name!r}", stmt.line
                )
            if stmt.name in self.nonnull_locals:
                raise LangError(
                    f"cannot assign to @nonnull parameter {stmt.name!r}; "
                    "reassignment could drop the non-null guarantee",
                    stmt.line,
                )
            # A reassignment may store null: any flow-narrowed fact dies here.
            self.narrowed_nonnull.discard(stmt.name)
            slot, var_type, volatile = self.var_addr(stmt.name, stmt.line)
            if var_type.const:
                raise LangError(
                    f"cannot assign to read-only variable {stmt.name!r}", stmt.line
                )
            tv = self.coerce(
                self.gen_expr(stmt.value),
                var_type,
                stmt.line,
                f"assignment to {stmt.name}",
            )
            self.gen_store(tv.value, slot, volatile=volatile)
        elif isinstance(stmt, CompoundAssign):
            self.gen_compound_assign(stmt)
        elif isinstance(stmt, If):
            cond = self.gen_cond(stmt.cond)
            # Flow-narrowing: `if (p != null)` proves p non-null in the then
            # branch; `if (p == null)` proves it in the else branch (at most
            # one of the two matches on a bare comparison, since the operator
            # differs; `and`/`or` chains thread through the collector).
            then_facts = self.narrowable_guard_names(stmt.cond, "!=")
            else_facts = self.narrowable_guard_names(stmt.cond, "==")
            if stmt.otherwise:
                with self.builder.if_else(cond) as (then, otherwise):
                    with then:
                        added = self.narrow_nonnull(then_facts)
                        self.gen_block(stmt.then)
                        self.narrowed_nonnull -= added
                        then_diverged = self.builder.block.is_terminated
                    with otherwise:
                        added = self.narrow_nonnull(else_facts)
                        self.gen_block(stmt.otherwise)
                        self.narrowed_nonnull -= added
                        else_diverged = self.builder.block.is_terminated
                # When both arms diverge (return/emit/break), the merge block
                # the builder now sits in is unreachable; terminate it so the
                # statement counts as diverging too -- no trailing return needed.
                if then_diverged and else_diverged:
                    self.builder.unreachable()
            else:
                with self.builder.if_then(cond):
                    added = self.narrow_nonnull(then_facts)
                    self.gen_block(stmt.then)
                    self.narrowed_nonnull -= added
                    then_diverged = self.builder.block.is_terminated
                # The C-idiomatic early guard: a diverging `if (p == null)`
                # body with no else proves p non-null for the remainder of
                # the enclosing scope (the fact ends with it -- gen_block
                # intersects on exit). Assignments inside the diverging body
                # cannot reach the remainder, so they do not invalidate it.
                if then_diverged:
                    self.narrowed_nonnull |= else_facts
        elif isinstance(stmt, While):
            kind = "until" if stmt.until else "while"
            # A loop's condition and body re-run on the back edge, where a
            # later iteration may already have invalidated a fact proved
            # before the loop -- so a pre-scan drops exactly the facts the
            # loop could invalidate (an assignment, a shadowing let, or a
            # mut lend anywhere in the condition or body); the rest survive
            # the loop, and past it.
            self.narrowed_nonnull -= self.loop_kill_set(stmt)
            cond_bb = self.builder.append_basic_block(f"{kind}.cond")
            body_bb = self.builder.append_basic_block(f"{kind}.body")
            end_bb = self.builder.append_basic_block(f"{kind}.end")
            self.builder.branch(cond_bb)
            self.builder.position_at_end(cond_bb)
            cond = self.gen_cond(stmt.cond)
            if stmt.until:
                self.builder.cbranch(cond, end_bb, body_bb)
            else:
                self.builder.cbranch(cond, body_bb, end_bb)
            self.builder.position_at_end(body_bb)
            # Header narrowing: the body only runs with the condition true
            # (`while`) / false (`until`), so `while (p != null)` proves p at
            # the top of every iteration -- no kill set needed, the header
            # re-proves on each back edge (a mid-body invalidation still
            # drops the fact for the rest of that iteration, as anywhere).
            header = self.narrowable_guard_names(
                stmt.cond, "==" if stmt.until else "!="
            )
            added = self.narrow_nonnull(header)
            # Record the defer depth so break/continue unwind the body's defers.
            self.loops.append((cond_bb, end_bb, len(self.defer_stack)))
            try:
                self.gen_block(stmt.body)
            finally:
                self.loops.pop()
            self.narrowed_nonnull -= added
            if not self.builder.block.is_terminated:
                self.builder.branch(cond_bb)
            self.builder.position_at_end(end_bb)
            # Post-exit narrowing: the normal exit edge leaves the condition
            # false (`while`) / true (`until`), so `while (p == null) { ... }`
            # proves p after the loop no matter what the body did -- unless a
            # `break` can reach the end without re-testing the condition.
            if not contains_break(stmt.body):
                self.narrowed_nonnull |= self.narrowable_guard_names(
                    stmt.cond, "!=" if stmt.until else "=="
                )
        elif isinstance(stmt, Conditional):
            # Compile-time @if: emit only the live branch's statements, inline
            # in the current scope. The dead branch is never type-checked.
            taken = stmt.then if self.eval_static_cond(stmt.cond) else stmt.otherwise
            for inner in taken:
                if self.builder.block.is_terminated:
                    break
                self.gen_statement(inner)
        elif isinstance(stmt, Block):
            self.gen_block(stmt.body)  # a bare { } -- its own scope
        elif isinstance(stmt, For):
            self.gen_for(stmt)
        elif isinstance(stmt, Defer):
            # Register only; the body runs when the enclosing block exits.
            self.defer_stack[-1].append(stmt.body)
        elif isinstance(stmt, Break):
            if not self.loops:
                raise LangError("'break' outside a loop", stmt.line)
            self.run_defers_through(self.loops[-1][2])  # the loop body and inner
            if not self.builder.block.is_terminated:
                self.builder.branch(self.loops[-1][1])
        elif isinstance(stmt, Continue):
            if not self.loops:
                raise LangError("'continue' outside a loop", stmt.line)
            self.run_defers_through(self.loops[-1][2])
            if not self.builder.block.is_terminated:
                self.builder.branch(self.loops[-1][0])
        elif isinstance(stmt, Case):
            subject = self.gen_expr(stmt.subject)
            if is_struct(subject.type) or subject.type is VOID:
                raise LangError(f"cannot match a {subject.type} in a case", stmt.line)
            end_bb = self.builder.append_basic_block("case.end")
            reaches_end = False
            for value_exprs, body in stmt.arms:
                # An arm matches if the subject equals any of its values.
                cond = None
                for value_expr in value_exprs:
                    value = self.gen_expr(value_expr)
                    eq = self.gen_equals(subject, value, value_expr.line)
                    cond = eq if cond is None else self.builder.or_(cond, eq)
                arm_bb = self.builder.append_basic_block("case.arm")
                next_bb = self.builder.append_basic_block("case.next")
                self.builder.cbranch(cond, arm_bb, next_bb)
                self.builder.position_at_end(arm_bb)
                self.gen_block(body)  # no fall-through: each arm exits to the end
                if not self.builder.block.is_terminated:
                    self.builder.branch(end_bb)
                    reaches_end = True
                self.builder.position_at_end(next_bb)
            self.gen_block(stmt.otherwise)  # the else arm, or empty
            if not self.builder.block.is_terminated:
                # An empty otherwise falls through here, so a case without an
                # exhaustive else always reaches the end.
                self.builder.branch(end_bb)
                reaches_end = True
            self.builder.position_at_end(end_bb)
            # Every arm and the else diverged: the end is unreachable, so the
            # case counts as diverging (no trailing return/emit needed).
            if not reaches_end:
                self.builder.unreachable()
        elif isinstance(stmt, CaseType):
            self.gen_case_type(stmt)
        elif isinstance(stmt, StoreDeref):
            ptr = self.gen_expr(stmt.ptr)
            if not is_pointer(ptr.type):
                raise LangError(f"cannot dereference a {ptr.type}", stmt.line)
            if ptr.type.pointee.const:
                raise LangError(
                    "cannot assign through a pointer to a read-only "
                    f"{ptr.type.pointee}",
                    stmt.line,
                )
            value = self.coerce(
                self.gen_expr(stmt.value),
                ptr.type.pointee,
                stmt.line,
                "assignment through pointer",
            )
            self.gen_store(value.value, ptr.value, volatile=ptr.type.pointee.volatile)
        elif isinstance(stmt, StoreIndex):
            base_t = self.lvalue_type(stmt.base)
            if base_t is not None and is_array(base_t) and self.writes_const(stmt.base):
                raise LangError(
                    "cannot assign to an element of a const parameter", stmt.line
                )
            addr, element = self.gen_index_addr(stmt.base, stmt.index, stmt.line)
            if element.const:
                raise LangError(
                    "cannot assign through a read-only slice<const T>", stmt.line
                )
            value = self.coerce(
                self.gen_expr(stmt.value), element, stmt.line, "assignment to element"
            )
            self.gen_store(value.value, addr, volatile=element.volatile)
        elif isinstance(stmt, StoreMember):
            if not stmt.arrow and self.writes_const(stmt.base):
                raise LangError(
                    "cannot assign to a field of a const parameter", stmt.line
                )
            addr, ftype, align, volatile = self.gen_member_addr(
                stmt.base, stmt.field, stmt.arrow, stmt.line
            )
            value = self.coerce(
                self.gen_expr(stmt.value),
                ftype,
                stmt.line,
                f"assignment to field {stmt.field!r}",
            )
            self.gen_store(value.value, addr, align=align, volatile=volatile)
        elif isinstance(stmt, ExprStmt):
            self.gen_expr(stmt.expr)
        else:
            raise LangError(f"cannot compile statement {stmt!r}", stmt.line)

    def gen_case_type(self, stmt: CaseType):
        """Lower a ``case type`` type-switch over an ``any`` subject.

        Rides on the same shape as the value ``case``: the subject's tag is
        loaded once, each arm compares it against the arm type's constant tag
        (an integer equality chain), and a matching arm loads the payload
        reinterpreted as its type into a fresh binding scoped to the arm. An
        ``any*`` subject auto-dereferences, per the member-access-through-
        pointer precedent. The ``else:`` arm is guaranteed by the parser.

        Args:
            stmt: The ``CaseType`` node.

        Raises:
            LangError: When the subject is not an ``any`` (or ``any*``), an
                arm's type can never be boxed, or two arms name the same type.
        """
        subject = self.gen_expr(stmt.subject)
        if is_pointer(subject.type) and is_any(subject.type.pointee):
            pointee = subject.type.pointee
            subject = TypedValue(
                self.gen_load(subject.value, volatile=pointee.volatile),
                strip_const(pointee),
            )
        if not is_any(subject.type):
            raise LangError(
                f"case type needs an any (or any*), got {subject.type}",
                stmt.line,
            )
        # Spill the box to a slot: the tag reads from index 0, and each arm
        # reinterprets the payload at index 1 by a pointer cast (the same
        # GEP indices as gen_box_any -- the dual-site layout invariant).
        slot = self.entry_alloca(ANY.ir, "casetype.subject")
        self.builder.store(subject.value, slot)
        tag_ptr = self.builder.gep(slot, [I32_ZERO, I32_ZERO], inbounds=True)
        tag = self.builder.load(tag_ptr, name="casetype.tag")
        payload_ptr = self.builder.gep(
            slot, [I32_ZERO, ir.Constant(ir.IntType(32), 1)], inbounds=True
        )
        end_bb = self.builder.append_basic_block("casetype.end")
        reaches_end = False
        seen: set[int] = set()
        for type_ref, name, body, when_line in stmt.arms:
            arm_type = strip_const(
                self.check_boxable(self.lang_type(type_ref, when_line), when_line)
            )
            arm_tag = self.any_tag(arm_type, when_line)
            if arm_tag in seen:
                raise LangError(
                    f"duplicate case type arm for {arm_type}", when_line
                )
            seen.add(arm_tag)
            cond = self.builder.icmp_unsigned(
                "==", tag, ir.Constant(UINT64.ir, arm_tag)
            )
            arm_bb = self.builder.append_basic_block("casetype.arm")
            next_bb = self.builder.append_basic_block("casetype.next")
            self.builder.cbranch(cond, arm_bb, next_bb)
            self.builder.position_at_end(arm_bb)
            # The binding is a fresh copy of the payload, reinterpreted as the
            # arm's type and scoped to the arm (like a for-loop's variable).
            typed_ptr = self.builder.bitcast(payload_ptr, arm_type.ir.as_pointer())
            var_slot = self.entry_alloca(arm_type.ir, name)
            self.builder.store(self.builder.load(typed_ptr), var_slot)
            outer_locals, outer_names = dict(self.locals), self.scope_names
            self.scope_names = set()
            try:
                self.bind_local(name, var_slot, arm_type)
                self.gen_block(body)
            finally:
                self.locals, self.scope_names = outer_locals, outer_names
            if not self.builder.block.is_terminated:
                self.builder.branch(end_bb)
                reaches_end = True
            self.builder.position_at_end(next_bb)
        self.gen_block(stmt.otherwise)  # the mandatory else arm
        if not self.builder.block.is_terminated:
            self.builder.branch(end_bb)
            reaches_end = True
        self.builder.position_at_end(end_bb)
        # Every arm and the else diverged: the type-switch diverges too.
        if not reaches_end:
            self.builder.unreachable()

    def gen_compound_assign(self, stmt: CompoundAssign):
        """Lower ``target op= value`` to a load, an ``op``, and a store back.

        The target's address is computed once (so a complex lvalue like
        ``arr[next()]`` runs its side effects a single time), the current value
        is read from it, combined with the right-hand side via the same rules
        as ``target op value``, and stored back -- coerced to the target's type,
        exactly as a plain assignment would be.

        Args:
            stmt: The ``CompoundAssign`` node.

        Raises:
            LangError: On a read-only target or operands the operator does not
                support.
        """
        addr, elem_type, align, volatile = self.compound_target_addr(
            stmt.target, stmt.line
        )
        current = TypedValue(
            self.gen_load(addr, align=align, volatile=volatile),
            strip_const(elem_type),
        )
        rhs = self.gen_expr(stmt.value)
        result = self.apply_binary(current, rhs, stmt.op, stmt.line)
        stored = self.coerce(
            result, elem_type, stmt.line, f"{stmt.op}= assignment"
        )
        self.gen_store(stored.value, addr, align=align, volatile=volatile)

    def compound_target_addr(
        self, target, line: int
    ) -> tuple[ir.Value, LangType, int | None, bool]:
        """Address a compound-assignment target, enforcing read-only rules.

        Mirrors the four assignment-statement forms (``Assign``,
        ``StoreDeref``, ``StoreIndex``, ``StoreMember``): it rejects the same
        const/read-only targets with the same diagnostics, so ``x op= y`` is
        writable exactly where ``x = y`` is.

        Args:
            target: The lvalue expression (``Var``, ``*ptr``, ``Index``, or
                ``Member``).
            line: Source line for diagnostics.

        Returns:
            A ``(pointer, element type, guaranteed alignment, volatile)`` tuple,
            as in :meth:`gen_addr`.

        Raises:
            LangError: On a read-only or otherwise invalid target.
        """
        if isinstance(target, Var):
            if target.name in self.const_locals:
                raise LangError(
                    f"cannot assign to const parameter {target.name!r}", line
                )
            if target.name in self.nonnull_locals:
                raise LangError(
                    f"cannot assign to @nonnull parameter {target.name!r}; "
                    "reassignment could drop the non-null guarantee",
                    line,
                )
            # `p += n` moves the pointer; any flow-narrowed fact dies here.
            self.narrowed_nonnull.discard(target.name)
            slot, var_type, volatile = self.var_addr(target.name, line)
            if var_type.const:
                raise LangError(
                    f"cannot assign to read-only variable {target.name!r}", line
                )
            return slot, var_type, None, volatile
        if isinstance(target, Unary) and target.op == "*":
            ptr = self.gen_expr(target.operand)
            if not is_pointer(ptr.type):
                raise LangError(f"cannot dereference a {ptr.type}", line)
            if ptr.type.pointee.const:
                raise LangError(
                    "cannot assign through a pointer to a read-only "
                    f"{ptr.type.pointee}",
                    line,
                )
            return ptr.value, ptr.type.pointee, None, ptr.type.pointee.volatile
        if isinstance(target, Index):
            base_t = self.lvalue_type(target.base)
            if base_t is not None and is_array(base_t) and self.writes_const(
                target.base
            ):
                raise LangError(
                    "cannot assign to an element of a const parameter", line
                )
            addr, element = self.gen_index_addr(target.base, target.index, line)
            if element.const:
                raise LangError(
                    "cannot assign through a read-only slice<const T>", line
                )
            return addr, element, None, element.volatile
        if isinstance(target, Member):
            if not target.arrow and self.writes_const(target.base):
                raise LangError(
                    "cannot assign to a field of a const parameter", line
                )
            return self.gen_member_addr(target.base, target.field, target.arrow, line)
        raise LangError("invalid assignment target", line)

    def gen_for(self, stmt: For):
        """Lower ``for x in obj { body }`` to the per-struct it/next protocol.

        With ``obj`` of struct type ``S`` (after stripping pointers), emits
        roughly::

            { let _it = S_it(obj); let x: T;
              while (S_next(&_it, &x)) { body } }

        Dispatch is by name on the iterable's struct, so each container provides
        ``S_it``/``S_next`` (which may be generic). The iterator is a
        compiler-held temporary -- never a named local -- so it cannot collide
        with user code, and the element variable lives in a fresh block scope,
        gone once the loop ends. The element type ``T`` is read from the resolved
        ``S_next``'s out-parameter.

        ``S_it`` takes the container by pointer, so a struct *value* iterable is
        borrowed automatically: ``for x in r`` iterates a snapshot (the value is
        copied to a stack slot whose address is passed), while ``for x in &r``
        iterates ``r`` by reference. A pointer iterable passes straight through.
        Because the slot is a function-scoped alloca, the iterator's back-pointer
        never dangles -- so even an rvalue (``for x in make_range()``) is safe.

        Args:
            stmt: The ``For`` node to lower.

        Raises:
            LangError: When the iterable is not a struct, or its ``S_it`` /
                matching ``S_next`` is not in scope.
        """
        # Like `while`: the body re-runs on the back edge, where a later
        # iteration may already have invalidated a fact proved before the
        # loop -- a pre-scan drops exactly the facts the loop could
        # invalidate, for every `for` variant (range/enumerate/slice/
        # protocol); the rest survive the loop, and past it.
        self.narrowed_nonnull -= self.loop_kill_set(stmt)
        # `for x in range(...)` is a builtin counting loop, lowered directly to
        # a counter with no struct or protocol calls. A user-defined `range`
        # function, if any, takes precedence.
        it = stmt.iterable
        if (
            isinstance(it, Call)
            and it.name == "range"
            and not self.callable_exists("range")
        ):
            self.gen_for_range(stmt, it)
            return
        # `for e in enumerate(obj)` is likewise a builtin: the underlying loop
        # plus a position counter, yielding an `enumerated<T>` per element. A
        # user-defined `enumerate` function, if any, takes precedence.
        if (
            isinstance(it, Call)
            and it.name == "enumerate"
            and not self.callable_exists("enumerate")
        ):
            self.gen_for_enumerate(stmt, it)
            return
        # Dispatch on the iterable's struct: `for x in obj` calls
        # `<Struct>_it(obj)` then `<Struct>_next(&it, &x)`. Evaluate the iterable
        # once (it may be effectful) and read its struct name off the type.
        iterable = self.gen_expr(stmt.iterable)
        struct_t = iterable.type
        while is_pointer(struct_t):
            struct_t = struct_t.pointee
        if is_slice(struct_t):
            self.gen_for_slice(stmt, iterable, struct_t)
            return
        if not is_struct(struct_t):
            raise LangError(
                "'for ... in' needs a struct iterable with '<struct>_it' and "
                f"'<struct>_next' functions, not {iterable.type}",
                stmt.line,
            )
        it_slot, next_fn, element = self.setup_protocol_loop(
            stmt, iterable, struct_t, "'for ... in'"
        )

        # A fresh scope for the element variable and the loop's defers.
        outer_locals, outer_names = dict(self.locals), self.scope_names
        self.scope_names = set()
        self.defer_stack.append([])
        try:
            x_slot = self.builder.alloca(element.ir, name=stmt.var)
            if over_aligned(element):
                x_slot.align = type_align(element)
            self.bind_local(stmt.var, x_slot, element)

            cond_bb = self.builder.append_basic_block("for.cond")
            body_bb = self.builder.append_basic_block("for.body")
            end_bb = self.builder.append_basic_block("for.end")
            self.builder.branch(cond_bb)
            self.builder.position_at_end(cond_bb)
            more = self.builder.call(next_fn, [it_slot, x_slot])  # next(&_it, &x)
            self.builder.cbranch(more, body_bb, end_bb)
            self.builder.position_at_end(body_bb)
            self.loops.append((cond_bb, end_bb, len(self.defer_stack)))
            try:
                self.gen_block(stmt.body)
            finally:
                self.loops.pop()
            if not self.builder.block.is_terminated:
                self.builder.branch(cond_bb)
            self.builder.position_at_end(end_bb)
        finally:
            self.defer_stack.pop()
            self.locals, self.scope_names = outer_locals, outer_names

    def setup_protocol_loop(
        self, stmt: For, iterable: TypedValue, struct_t: LangType, what: str
    ):
        """Set up an ``_it``/``_next`` protocol loop over ``iterable``.

        The shared front half of ``for x in obj`` and ``enumerate(obj)``:
        dispatches by the struct's name to ``<struct>_it``, borrows the
        iterable (a struct value is snapshot to a stack slot so its address can
        be taken -- ``for x in r`` behaves like ``for x in &r`` -- while a
        pointer passes straight through; the slot is a function-scoped alloca,
        so the iterator's back-pointer never dangles, even for an rvalue), and
        resolves the matching ``<struct>_next``.

        Args:
            stmt: The ``For`` statement being lowered.
            iterable: The already-evaluated iterable.
            struct_t: The iterable's struct type, pointers stripped.
            what: The construct named in diagnostics (``"'for ... in'"`` or
                ``"enumerate()"``).

        Returns:
            ``(iterator slot, next function, element type)``: the alloca
            holding the ``<struct>_it`` cursor, the instantiated
            ``<struct>_next``, and the element type its out-parameter yields.

        Raises:
            LangError: When ``<struct>_it`` or a matching ``<struct>_next`` is
                not in scope.
        """
        base = struct_t.template or struct_t.name
        it_name, next_name = f"{base}_it", f"{base}_next"
        if not self.callable_exists(it_name):
            raise LangError(
                f"{what} needs a {it_name!r} function for {struct_t}; "
                "none is in scope",
                stmt.line,
            )
        if is_pointer(iterable.type):
            arg = iterable
        else:
            struct_slot = self.builder.alloca(iterable.type.ir, name="for.src")
            self.builder.store(iterable.value, struct_slot)
            arg = TypedValue(struct_slot, pointer_to(iterable.type))
        # Pass the pointer through a hidden local so the `_it` call (routed
        # through normal overload/generic resolution) does not re-evaluate the
        # expression. The name cannot be a real identifier.
        src_slot = self.builder.alloca(arg.type.ir, name="for.iterable")
        self.builder.store(arg.value, src_slot)
        hidden = "0for.iterable"
        self.bind_local(hidden, src_slot, arg.type)
        iterator = self.gen_call(Call(it_name, [], [Var(hidden, stmt.line)], stmt.line))
        del self.locals[hidden]
        self.scope_names.discard(hidden)
        it_slot = self.builder.alloca(iterator.type.ir, name="for.iter")
        self.builder.store(iterator.value, it_slot)

        next_fn, element = self.resolve_protocol_next(
            iterator.type, next_name, stmt.line
        )
        return it_slot, next_fn, element

    def gen_for_slice(
        self,
        stmt: For,
        iterable: TypedValue,
        slice_t: LangType,
        enumerated: bool = False,
    ):
        """Lower ``for x in s { body }`` over a builtin ``slice<T>``.

        Unlike a library container, a slice iterates natively -- no
        ``_it``/``_next`` -- walking its ``data`` from index ``0`` up to
        ``length``::

            { let i = 0; let x: T;
              while (i < s.length) { x = s.data[i]; body; i = i + 1; } }

        The index counter and the slice's pointer/length are compiler-held
        temporaries; ``x`` lives in a fresh block scope, gone once the loop ends.
        A ``continue`` runs through the step block, so it still advances ``i``.

        With ``enumerated`` (a ``for e in enumerate(s)`` loop), the variable is
        an ``enumerated<T>`` instead: the native walk already keeps the
        position, so its ``index`` field is the loop counter itself -- no
        second counter -- and ``value`` takes the element.

        Args:
            stmt: The ``For`` node to lower.
            iterable: The already-evaluated slice (or pointer(s) to one).
            slice_t: The iterable's ``slice<T>`` type, pointers stripped.
            enumerated: Yield ``enumerated<T>`` elements for the builtin
                ``enumerate``, rather than bare ``T`` s.
        """
        # Materialize the slice value, loading through any pointers, then read
        # its pointer and length once up front (the slice does not change shape
        # mid-loop, so the bound is fixed at entry).
        value, vtype = iterable.value, iterable.type
        while is_pointer(vtype):
            value = self.gen_load(value)
            vtype = vtype.pointee
        ptr = self.builder.extract_value(value, slice_t.elem_indices[0])
        length = self.builder.extract_value(value, slice_t.elem_indices[1])
        # The loop variable is a fresh copy of each element, so it is mutable
        # even when iterating a slice<const T>.
        element = strip_const(slice_t.fields[0][1].pointee)
        if enumerated:
            var_type, index_pos, value_pos = self.enumerated_type(
                element, stmt.line
            )
        else:
            var_type = element

        idx_slot = self.builder.alloca(UINT64.ir, name="for.idx")
        self.builder.store(ir.Constant(UINT64.ir, 0), idx_slot)

        # A fresh scope for the element variable and the loop's defers.
        outer_locals, outer_names = dict(self.locals), self.scope_names
        self.scope_names = set()
        self.defer_stack.append([])
        try:
            x_slot = self.builder.alloca(var_type.ir, name=stmt.var)
            if over_aligned(var_type):
                x_slot.align = type_align(var_type)
            self.bind_local(stmt.var, x_slot, var_type)
            if enumerated:
                index_ptr = self.builder.gep(
                    x_slot, [I32_ZERO, ir.Constant(ir.IntType(32), index_pos)],
                    inbounds=True,
                )
                value_ptr = self.builder.gep(
                    x_slot, [I32_ZERO, ir.Constant(ir.IntType(32), value_pos)],
                    inbounds=True,
                )
            else:
                value_ptr = x_slot

            cond_bb = self.builder.append_basic_block("for.cond")
            body_bb = self.builder.append_basic_block("for.body")
            step_bb = self.builder.append_basic_block("for.step")
            end_bb = self.builder.append_basic_block("for.end")
            self.builder.branch(cond_bb)
            self.builder.position_at_end(cond_bb)
            idx = self.builder.load(idx_slot)
            more = self.builder.icmp_unsigned("<", idx, length)
            self.builder.cbranch(more, body_bb, end_bb)
            self.builder.position_at_end(body_bb)
            if enumerated:
                self.builder.store(idx, index_ptr)
            elem_addr = self.builder.gep(ptr, [idx])
            self.builder.store(self.gen_load(elem_addr), value_ptr)
            # `continue` lands on the step block, so it advances `i` like a
            # normal iteration; `break` exits to the end.
            self.loops.append((step_bb, end_bb, len(self.defer_stack)))
            try:
                self.gen_block(stmt.body)
            finally:
                self.loops.pop()
            if not self.builder.block.is_terminated:
                self.builder.branch(step_bb)
            self.builder.position_at_end(step_bb)
            nxt = self.builder.add(
                self.builder.load(idx_slot), ir.Constant(UINT64.ir, 1)
            )
            self.builder.store(nxt, idx_slot)
            self.builder.branch(cond_bb)
            self.builder.position_at_end(end_bb)
        finally:
            self.defer_stack.pop()
            self.locals, self.scope_names = outer_locals, outer_names

    def gen_for_range(self, stmt: For, call: Call):
        """Lower ``for i in range(end)`` / ``for i in range(start, end)``.

        ``range`` is a builtin, not a library type: it emits a direct counting
        loop over the half-open interval ``[start, end)`` (``start`` defaults to
        0), with no struct built and no ``_it``/``_next`` calls -- so at any
        optimization level there is no runtime footprint beyond the counter::

            { let _i = start; let x: T;
              while (_i < end) { x = _i; body; _i = _i + 1; } }

        The element type ``T`` comes from an explicit ``range<T>(...)`` argument
        or is inferred from the bounds (their integer width and signedness); the
        loop variable ``x`` is a fresh copy of the counter each turn, so
        assigning to it inside the body does not perturb the iteration.

        Args:
            stmt: The ``For`` node whose iterable is the ``range`` call.
            call: The ``range(...)`` call expression.

        Raises:
            LangError: On the wrong argument count, a non-integer bound, or
                bounds whose types disagree.
        """
        if len(call.args) == 1:
            start_expr, end_expr = None, call.args[0]
        elif len(call.args) == 2:
            start_expr, end_expr = call.args[0], call.args[1]
        else:
            raise LangError(
                "range() takes 1 or 2 arguments: range(end) or range(start, end)",
                stmt.line,
            )

        if call.type_args:
            if len(call.type_args) != 1:
                raise LangError("range() takes a single type argument", stmt.line)
            elem = self.lang_type(call.type_args[0], stmt.line)
            end = self.coerce(self.gen_expr(end_expr), elem, stmt.line, "range end")
            start = (
                self.coerce(self.gen_expr(start_expr), elem, stmt.line, "range start")
                if start_expr is not None
                else TypedValue(ir.Constant(elem.ir, 0), elem)
            )
        else:
            end = self.gen_expr(end_expr)
            start = self.gen_expr(start_expr) if start_expr is not None else None
            # Infer the element type: a typed bound fixes it; two untyped bounds
            # widen to the larger. The other bound then coerces to it (a typed
            # mismatch is a coercion error, as it should be).
            if start is None:
                elem = end.type
            elif not start.adaptable:
                elem = start.type
            elif not end.adaptable:
                elem = end.type
            else:
                elem = wider_int_type(start.type, end.type)
            end = self.coerce(end, elem, stmt.line, "range end")
            start = (
                self.coerce(start, elem, stmt.line, "range start")
                if start is not None
                else TypedValue(ir.Constant(elem.ir, 0), elem)
            )

        if not is_integer(elem):
            raise LangError(f"range() needs integer bounds, not {elem}", stmt.line)

        # The bound is fixed at entry; the counter lives in a hidden slot so the
        # loop variable can be a fresh copy the body may freely reassign.
        end_val = end.value
        idx_slot = self.builder.alloca(elem.ir, name="range.idx")
        self.builder.store(start.value, idx_slot)

        # A fresh scope for the loop variable and the loop's defers.
        outer_locals, outer_names = dict(self.locals), self.scope_names
        self.scope_names = set()
        self.defer_stack.append([])
        try:
            x_slot = self.builder.alloca(elem.ir, name=stmt.var)
            self.bind_local(stmt.var, x_slot, elem)

            cond_bb = self.builder.append_basic_block("range.cond")
            body_bb = self.builder.append_basic_block("range.body")
            step_bb = self.builder.append_basic_block("range.step")
            end_bb = self.builder.append_basic_block("range.end")
            self.builder.branch(cond_bb)
            self.builder.position_at_end(cond_bb)
            idx = self.builder.load(idx_slot)
            cmp = (
                self.builder.icmp_signed("<", idx, end_val)
                if elem.signed
                else self.builder.icmp_unsigned("<", idx, end_val)
            )
            self.builder.cbranch(cmp, body_bb, end_bb)
            self.builder.position_at_end(body_bb)
            self.builder.store(idx, x_slot)  # fresh copy of the counter
            # `continue` lands on the step block, so it still advances the
            # counter; `break` exits to the end.
            self.loops.append((step_bb, end_bb, len(self.defer_stack)))
            try:
                self.gen_block(stmt.body)
            finally:
                self.loops.pop()
            if not self.builder.block.is_terminated:
                self.builder.branch(step_bb)
            self.builder.position_at_end(step_bb)
            nxt = self.builder.add(
                self.builder.load(idx_slot), ir.Constant(elem.ir, 1)
            )
            self.builder.store(nxt, idx_slot)
            self.builder.branch(cond_bb)
            self.builder.position_at_end(end_bb)
        finally:
            self.defer_stack.pop()
            self.locals, self.scope_names = outer_locals, outer_names

    def enumerated_type(self, element: LangType, line: int):
        """Instantiate ``enumerated<element>`` and locate its two fields.

        Returns:
            ``(struct type, index field position, value field position)``.

        Raises:
            LangError: When a user struct shadows ``enumerated`` with a shape
                the builtin loop cannot fill.
        """
        decl = self.struct_templates.get("enumerated")
        if decl is None or len(decl.type_params) != 1:
            raise LangError(
                "enumerate() needs the builtin 'enumerated<T>' struct, but a "
                "user declaration shadows it with a different shape",
                line,
            )
        elem_struct = self.instantiate_struct(decl, (element,), line)
        index_pos, _ = self.struct_field(elem_struct, "index", line)
        value_pos, vtype = self.struct_field(elem_struct, "value", line)
        if vtype != element:
            raise LangError(
                f"enumerate() cannot yield {elem_struct}: its 'value' field is "
                f"{vtype}, not the element type {element}",
                line,
            )
        return elem_struct, index_pos, value_pos

    def gen_for_enumerate(self, stmt: For, call: Call):
        """Lower ``for e in enumerate(obj) { body }``.

        ``enumerate`` is a builtin like ``range``: it runs ``obj``'s ordinary
        iteration (the ``_it``/``_next`` protocol, or a slice's native walk)
        while keeping a position counter, and yields an ``enumerated<T>``
        (``{ index: uint64; value: T }``) per element::

            { let _it = S_it(obj); let e: enumerated<T>; let _n: uint64 = 0;
              while (S_next(&_it, &e.value)) { e.index = _n; _n += 1; body } }

        ``obj`` is borrowed exactly like a bare ``for x in obj`` (a struct
        value is snapshot to a stack slot, a pointer passes through), and
        ``_next`` writes straight into the element's ``value`` field -- no
        extra copy per turn. The counter bumps as each element is yielded, so
        a ``continue`` does not skip a position. A user-defined ``enumerate``
        function takes precedence over the builtin.

        Args:
            stmt: The ``For`` node whose iterable is the ``enumerate`` call.
            call: The ``enumerate(...)`` call expression.

        Raises:
            LangError: On the wrong argument count, explicit type arguments,
                a non-iterable argument, or `enumerate(range(...))`.
        """
        if call.type_args:
            raise LangError("enumerate() takes no type arguments", stmt.line)
        if len(call.args) != 1:
            raise LangError(
                "enumerate() takes exactly one iterable argument", stmt.line
            )
        inner = call.args[0]
        if (
            isinstance(inner, Call)
            and inner.name == "range"
            and not self.callable_exists("range")
        ):
            raise LangError(
                "enumerate() over the builtin range is redundant -- the counter "
                "is the value; iterate the range directly", stmt.line,
            )

        iterable = self.gen_expr(inner)
        struct_t = iterable.type
        while is_pointer(struct_t):
            struct_t = struct_t.pointee
        if is_slice(struct_t):
            self.gen_for_slice(stmt, iterable, struct_t, enumerated=True)
            return
        if not is_struct(struct_t):
            raise LangError(
                "enumerate() needs a struct iterable with '<struct>_it' and "
                f"'<struct>_next' functions, not {iterable.type}",
                stmt.line,
            )
        it_slot, next_fn, element = self.setup_protocol_loop(
            stmt, iterable, struct_t, "enumerate()"
        )
        elem_struct, index_pos, value_pos = self.enumerated_type(element, stmt.line)

        counter_slot = self.builder.alloca(UINT64.ir, name="enum.idx")
        self.builder.store(ir.Constant(UINT64.ir, 0), counter_slot)

        # A fresh scope for the element variable and the loop's defers.
        outer_locals, outer_names = dict(self.locals), self.scope_names
        self.scope_names = set()
        self.defer_stack.append([])
        try:
            e_slot = self.builder.alloca(elem_struct.ir, name=stmt.var)
            if over_aligned(elem_struct):
                e_slot.align = type_align(elem_struct)
            self.bind_local(stmt.var, e_slot, elem_struct)
            index_ptr = self.builder.gep(
                e_slot, [I32_ZERO, ir.Constant(ir.IntType(32), index_pos)],
                inbounds=True,
            )
            value_ptr = self.builder.gep(
                e_slot, [I32_ZERO, ir.Constant(ir.IntType(32), value_pos)],
                inbounds=True,
            )

            cond_bb = self.builder.append_basic_block("for.cond")
            body_bb = self.builder.append_basic_block("for.body")
            end_bb = self.builder.append_basic_block("for.end")
            self.builder.branch(cond_bb)
            self.builder.position_at_end(cond_bb)
            # next(&_it, &e.value) fills the value field in place.
            more = self.builder.call(next_fn, [it_slot, value_ptr])
            self.builder.cbranch(more, body_bb, end_bb)
            self.builder.position_at_end(body_bb)
            # The position is claimed as soon as the element is yielded, so a
            # `continue` (which re-enters at cond) never skips an index.
            idx = self.builder.load(counter_slot)
            self.builder.store(idx, index_ptr)
            self.builder.store(
                self.builder.add(idx, ir.Constant(UINT64.ir, 1)), counter_slot
            )
            self.loops.append((cond_bb, end_bb, len(self.defer_stack)))
            try:
                self.gen_block(stmt.body)
            finally:
                self.loops.pop()
            if not self.builder.block.is_terminated:
                self.builder.branch(cond_bb)
            self.builder.position_at_end(end_bb)
        finally:
            self.defer_stack.pop()
            self.locals, self.scope_names = outer_locals, outer_names

    def callable_exists(self, name: str) -> bool:
        """Report whether a callable named ``name`` is in scope.

        Args:
            name: The function name to look up.

        Returns:
            ``True`` if a generic template, a concrete function, or a file-scoped
            ``@static`` of that name is visible from the current source.
        """
        key = (self.current_source, name)
        return (
            name in self.templates
            or name in self.overloads
            or name in self.funcs
            or key in self.static_templates
            or key in self.static_funcs
        )

    def resolve_protocol_next(self, iter_type: LangType, next_name: str, line: int):
        """Resolve the ``<struct>_next`` that consumes an iterator type.

        ``next`` is dispatched on the iterator alone, so the element type can be
        learned before the loop variable is declared. A non-generic
        ``<struct>_next`` is matched by its resolved signature; generic ones are
        matched by unifying the iterator-pointer parameter.

        Args:
            iter_type: The iterator type returned by ``<struct>_it``.
            next_name: The expected function name (``<struct>_next``).
            line: Source line for diagnostics.

        Returns:
            A ``(function, element type)`` pair: the instantiated ``next`` and
            the element type it yields (its out-parameter's pointee).

        Raises:
            LangError: When no viable ``next`` exists, the choice is ambiguous,
                or the chosen ``next`` has the wrong signature.
        """
        want = pointer_to(iter_type)
        key = (self.current_source, next_name)
        # A concrete (non-generic) <struct>_next: match its resolved signature.
        if next_name not in self.templates and key not in self.static_templates:
            symbol = self.static_funcs.get(key)
            if symbol is None:
                # `_next` is resolved here, outside gen_call, so the tombstone
                # check must ride along (`_it` goes through gen_call as usual).
                self.check_removed(next_name, line)
            if symbol is None and next_name in self.funcs:
                symbol = next_name
            if symbol is None:
                raise LangError(
                    f"'for ... in' needs a {next_name!r} for {iter_type}; "
                    "none is in scope",
                    line,
                )
            ret, params, _ = self.signatures[symbol]
            if len(params) != 2 or params[0] != want:
                raise LangError(
                    f"{next_name!r} does not iterate a {iter_type} (for ... in)",
                    line,
                )
            if ret is not BOOL:
                raise LangError(f"{next_name!r} must return bool", line)
            if not is_pointer(params[1]):
                raise LangError(
                    f"{next_name!r} second parameter must be an out-pointer", line
                )
            # `_next` is resolved here, outside gen_call, so the deprecation
            # check must ride along (`_it` goes through gen_call as usual).
            self.warn_deprecated(next_name, self.deprecated_syms.get(symbol), line)
            return self.funcs[symbol], params[1].pointee
        candidates = list(self.templates.get(next_name, []))
        static = self.static_templates.get(key)
        if static is not None:
            candidates.append(static)
        viable = []
        for func in candidates:
            if len(func.params) != 2:
                continue
            bindings: dict[str, LangType] = {}
            try:
                self.unify(
                    func.params[0][1],
                    want,
                    func.type_params,
                    bindings,
                    True,
                    "for-loop 'next'",
                    line,
                )
            except LangError:
                continue
            if any(t not in bindings for t in func.type_params):
                continue
            if not self.shape_matches(
                func.params[0][1], want, False, func.type_params, line
            ):
                continue
            viable.append((self.specificity(func), func, bindings))
        if not viable:
            raise LangError(
                f"no {next_name!r} overload iterates a {iter_type} (for ... in)", line
            )
        viable.sort(key=lambda entry: entry[0], reverse=True)
        if len(viable) > 1 and viable[0][0] == viable[1][0]:
            raise LangError(f"ambiguous {next_name!r} for {iter_type}", line)
        _, func, bindings = viable[0]
        fn, ret, params = self.instantiate(func, bindings, line)
        if ret is not BOOL:
            raise LangError(f"{next_name!r} must return bool", line)
        if not is_pointer(params[1]):
            raise LangError(
                f"{next_name!r} second parameter must be an out-pointer", line
            )
        self.warn_deprecated(next_name, func.deprecated_msg, line)
        return fn, params[1].pointee

    def gen_cond(self, expr) -> ir.Value:
        """Evaluate an expression as an ``i1`` condition.

        A ``bool`` is used directly; an integer is compared against zero, as in
        C.

        Args:
            expr: The condition expression.

        Returns:
            The ``i1`` value of the condition.

        Raises:
            LangError: When the expression is neither a bool nor an integer.
        """
        tv = self.gen_expr(expr)
        if tv.type is BOOL:
            return tv.value
        if is_integer(tv.type):
            return self.builder.icmp_signed("!=", tv.value, ir.Constant(tv.type.ir, 0))
        raise LangError("condition must be a bool or integer", expr.line)

    def gen_logical(self, expr: Logical) -> TypedValue:
        """Emit a short-circuiting ``and`` / ``or``.

        Each operand is tested like a condition (bool or integer) and the result
        is a ``bool``. The right operand is evaluated only when the left does not
        already decide the answer.

        Args:
            expr: The ``Logical`` node.

        Returns:
            The ``bool`` result as a ``TypedValue``.
        """
        lhs = self.gen_cond(expr.lhs)
        lhs_block = self.builder.block
        rhs_block = self.builder.append_basic_block(f"{expr.op}.rhs")
        end_block = self.builder.append_basic_block(f"{expr.op}.end")
        # `and` runs the rhs only when lhs is true; `or` only when lhs is false.
        if expr.op == "and":
            self.builder.cbranch(lhs, rhs_block, end_block)
        else:
            self.builder.cbranch(lhs, end_block, rhs_block)
        self.builder.position_at_end(rhs_block)
        # The rhs only runs when the lhs was true (`and`) / false (`or`), so
        # the lhs's null-check facts hold while it evaluates:
        # `p != null and use(p)` proves p for the call.
        added = self.narrow_nonnull(
            self.narrowable_guard_names(expr.lhs, "!=" if expr.op == "and" else "==")
        )
        rhs = self.gen_cond(expr.rhs)
        self.narrowed_nonnull -= added
        rhs_block = self.builder.block  # the rhs may have added blocks of its own
        self.builder.branch(end_block)
        self.builder.position_at_end(end_block)
        phi = self.builder.phi(BOOL.ir)
        # When short-circuited, the answer is the lhs's deciding value: false
        # for `and` (lhs was false), true for `or` (lhs was true).
        phi.add_incoming(ir.Constant(BOOL.ir, expr.op == "or"), lhs_block)
        phi.add_incoming(rhs, rhs_block)
        return TypedValue(phi, BOOL)

    def gen_ternary(self, expr: Ternary) -> TypedValue:
        """Emit a ``cond ? then : otherwise`` conditional expression.

        Only the selected arm runs: the condition branches to one of two blocks,
        each evaluates its arm, and a ``phi`` in the join block merges the
        results. The arms are unified like binary operands -- equal types are
        kept, an untyped constant arm adapts to the other's type (two untyped
        integers widen to the larger), and ``null`` adapts to a pointer arm --
        and each arm is coerced to that type in its own block before the merge.

        Args:
            expr: The ``Ternary`` node.

        Returns:
            The selected arm's value as a ``TypedValue``.

        Raises:
            LangError: When the two arms have irreconcilable types.
        """
        cond = self.gen_cond(expr.cond)
        then_bb = self.builder.append_basic_block("ternary.then")
        else_bb = self.builder.append_basic_block("ternary.else")
        end_bb = self.builder.append_basic_block("ternary.end")
        self.builder.cbranch(cond, then_bb, else_bb)
        # Evaluate both arms (each may append blocks of its own) before deciding
        # the result type, since the unification looks at both.
        self.builder.position_at_end(then_bb)
        then_tv = self.gen_expr(expr.then)
        then_end = self.builder.block
        self.builder.position_at_end(else_bb)
        else_tv = self.gen_expr(expr.otherwise)
        else_end = self.builder.block
        result_type = self.unify_branches(then_tv, else_tv)
        # Coerce each arm to the shared type at the end of its own block, so any
        # adapting (or a pointer-to-rawptr bitcast) lands on the right path.
        self.builder.position_at_end(then_end)
        then_val = self.coerce(then_tv, result_type, expr.line, "ternary branch").value
        self.builder.branch(end_bb)
        self.builder.position_at_end(else_end)
        else_val = self.coerce(else_tv, result_type, expr.line, "ternary branch").value
        self.builder.branch(end_bb)
        self.builder.position_at_end(end_bb)
        phi = self.builder.phi(result_type.ir)
        phi.add_incoming(then_val, then_end)
        phi.add_incoming(else_val, else_end)
        return TypedValue(phi, result_type)

    def unify_branches(self, then_tv: TypedValue, else_tv: TypedValue) -> LangType:
        """Pick the shared type of a ternary's two arms.

        Mirrors :meth:`gen_binary`'s operand unification: equal types are kept,
        two untyped integer arms widen to the larger, and a single untyped
        constant arm (including ``null``) takes on the other arm's type.
        Otherwise the first arm's type is the target, and :meth:`coerce` either
        bridges it (e.g. a pointer to raw memory) or reports the mismatch.

        Args:
            then_tv: The ``then`` arm's value.
            else_tv: The ``otherwise`` arm's value.

        Returns:
            The concrete type both arms are coerced to before the ``phi``.
        """
        if then_tv.type == else_tv.type:
            return then_tv.type
        if (
            then_tv.adaptable
            and else_tv.adaptable
            and is_integer(then_tv.type)
            and is_integer(else_tv.type)
        ):
            return wider_int_type(then_tv.type, else_tv.type)
        if else_tv.adaptable and not then_tv.adaptable:
            return then_tv.type
        if then_tv.adaptable and not else_tv.adaptable:
            return else_tv.type
        return then_tv.type

    def gen_equals(self, subject: TypedValue, value: TypedValue, line: int) -> ir.Value:
        """Emit an ``i1`` for ``subject == value`` (to test a ``when`` arm).

        The subject is the authoritative type: a ``when`` value adapts (or must
        coerce) to it, unless the subject is itself an untyped constant. Equality
        is sign-agnostic, so integers, pointers, and bools share an integer
        compare while ``float64`` uses an ordered float compare.

        Args:
            subject: The matched subject value.
            value: The arm's candidate value.
            line: Source line for diagnostics.

        Returns:
            The ``i1`` comparison result.
        """
        if subject.type != value.type:
            if subject.adaptable and not value.adaptable:
                subject = self.coerce(subject, value.type, line, "case subject")
            else:
                value = self.coerce(value, subject.type, line, "when value")
        if subject.type is FLOAT64:
            return self.builder.fcmp_ordered("==", subject.value, value.value)
        return self.builder.icmp_unsigned("==", subject.value, value.value)

    def gen_expr(self, expr) -> TypedValue:
        """Evaluate an expression to a ``TypedValue``.

        Dispatches on the expression type: literals, variables (and names that
        resolve to a constant or a function value), calls, unary/binary/logical
        operators, casts, ``sizeof``/``len``, indexing, and member access.

        Args:
            expr: The expression AST node.

        Returns:
            The evaluated value paired with its type.

        Raises:
            LangError: On an ill-typed or unsupported expression.
        """
        if isinstance(expr, IntLit):
            return adaptable_int(expr.value)
        if isinstance(expr, CharLit):
            return TypedValue(ir.Constant(CHAR.ir, expr.value), CHAR)
        if isinstance(expr, FloatLit):
            return TypedValue(ir.Constant(FLOAT64.ir, expr.value), FLOAT64)
        if isinstance(expr, BoolLit):
            return TypedValue(ir.Constant(BOOL.ir, int(expr.value)), BOOL)
        if isinstance(expr, NullLit):
            return TypedValue(ir.Constant(RAWPTR.ir, None), NULLT, adaptable=True)
        if isinstance(expr, StrLit):
            return self.gen_string(expr.value)
        if isinstance(expr, ArrayLit):
            raise LangError(
                "an array literal is only allowed as a variable initializer", expr.line
            )
        if isinstance(expr, StructLit):
            return self.gen_struct_lit(expr)
        if isinstance(expr, BlockExpr):
            return self.gen_block_expr(expr)
        if isinstance(expr, Var):
            # A name that is not a variable may be a constant or a function used
            # as a value.
            if self.var_type_of(expr.name) is None:
                const = self.consts.get(expr.name)
                if const is not None:
                    self.check_access(
                        *self.const_privacy[expr.name],
                        f"constant {expr.name!r}",
                        expr.line,
                    )
                    return const
                fv = self.func_value(expr.name, expr.line)
                if fv is not None:
                    return fv
            slot, var_type, volatile = self.var_addr(expr.name, expr.line)
            return self.value_at(slot, var_type, volatile=volatile, name=expr.name)
        if isinstance(expr, Call):
            return self.gen_call(expr)
        if isinstance(expr, CallExpr):
            callee = self.gen_expr(expr.callee)
            return self.gen_indirect_call(
                callee, expr.args, f"call to {callee.type}", expr.line
            )
        if isinstance(expr, Unary):
            return self.gen_unary(expr)
        if isinstance(expr, NonnullAssert):
            return self.gen_nonnull_assert(expr)
        if isinstance(expr, Logical):
            return self.gen_logical(expr)
        if isinstance(expr, Ternary):
            return self.gen_ternary(expr)
        if isinstance(expr, Binary):
            return self.gen_binary(expr)
        if isinstance(expr, Cast):
            return self.gen_cast(expr)
        if isinstance(expr, EnumAccess):
            return self.resolve_enum_access(expr)
        if isinstance(expr, Asm):
            return self.gen_asm(expr)
        if isinstance(expr, SizeOf):
            size = type_size(self.sizeof_operand(expr.type_name, expr.line))
            return TypedValue(ir.Constant(UINT64.ir, size), UINT64)
        if isinstance(expr, AlignOf):
            align = type_align(self.sizeof_operand(expr.type_name, expr.line))
            return TypedValue(ir.Constant(UINT64.ir, align), UINT64)
        if isinstance(expr, OffsetOf):
            struct_type = self.lang_type(expr.type_name, expr.line)
            off = field_offset(struct_type, expr.field, expr.line)
            return TypedValue(ir.Constant(UINT64.ir, off), UINT64)
        if isinstance(expr, Len):
            # The element count is a compile-time property of the array's type;
            # read it through the address so the array does not decay first. It
            # is an adaptable constant -- like writing the literal count -- so it
            # compares against an int32 counter as readily as a uint64 one.
            _, lang_type, _, _ = self.gen_addr(expr.operand, expr.line)
            if not is_array(lang_type):
                raise LangError(f"len() requires an array, got {lang_type}", expr.line)
            return TypedValue(
                ir.Constant(UINT64.ir, lang_type.count), UINT64, adaptable=True
            )
        if isinstance(expr, Index):
            addr, element = self.gen_index_addr(expr.base, expr.index, expr.line)
            return self.value_at(addr, element, volatile=element.volatile)
        if isinstance(expr, Member):
            if not expr.arrow and not isinstance(
                expr.base, (Var, Member, Index, Unary)
            ):
                # Field of a non-addressable struct value, e.g. f().field.
                base = self.gen_expr(expr.base)
                index, ftype = self.struct_field(base.type, expr.field, expr.line)
                if is_union(base.type):
                    # A union member is read through the storage, not by
                    # element index: spill the value and cast the slot to the
                    # member type.
                    slot = self.builder.alloca(base.type.ir)
                    if over_aligned(base.type):
                        slot.align = type_align(base.type)
                    self.builder.store(base.value, slot)
                    addr = self.builder.bitcast(slot, ftype.ir.as_pointer())
                    # A @packed union's storage guarantees no alignment.
                    return self.value_at(
                        addr, ftype, align=1 if base.type.packed else None
                    )
                return TypedValue(self.builder.extract_value(base.value, index), ftype)
            addr, ftype, align, volatile = self.gen_member_addr(
                expr.base, expr.field, expr.arrow, expr.line
            )
            return self.value_at(addr, ftype, align=align, volatile=volatile)
        raise LangError(f"cannot compile expression {expr!r}", expr.line)

    def value_at(
        self, addr, lang_type: LangType, *, align=None, volatile=False, name=""
    ) -> TypedValue:
        """Load the value held at an address.

        An array decays to a pointer to its first element (C array-to-pointer
        decay), so indexing, passing it as a pointer argument, and assigning it
        all go through the pointer; every other type is loaded normally. A loaded
        value is an independent copy, so it sheds any ``const`` qualifier: only
        the lvalue (a write target, e.g. ``s[i]``) stays read-only.

        Args:
            addr: The address to read.
            lang_type: The type stored at ``addr``.
            align: Explicit alignment, or ``None`` for the default.
            volatile: Whether the load must not be optimized away.
            name: An optional name for the result value.

        Returns:
            The loaded value (or decayed pointer) as a ``TypedValue``.
        """
        if is_array(lang_type):
            first = self.builder.gep(addr, [I32_ZERO, I32_ZERO], inbounds=True)
            # `decayed` remembers the array type, so a boxing site can reject
            # the array by name instead of silently boxing the decayed pointer.
            return TypedValue(
                first, pointer_to(lang_type.element), decayed=lang_type
            )
        return TypedValue(
            self.gen_load(addr, align=align, volatile=volatile, name=name),
            strip_const(lang_type),
        )

    def var_addr(self, name: str, line: int) -> tuple[ir.Value, LangType, bool]:
        """Resolve a variable's storage slot.

        Checks a local alloca, then a file-scoped ``@static`` global, then an
        ``@extern`` global, so locals shadow globals and a file's own
        ``@static`` shadows a same-named extern.

        Args:
            name: The variable name.
            line: Source line for diagnostics.

        Returns:
            A ``(pointer value, variable type, volatile)`` tuple; ``volatile`` is
            set for ``@volatile`` globals and for variables of ``@volatile``
            struct types.

        Raises:
            LangError: When the name is a constant (not assignable) or undefined.
        """
        if name in self.locals:
            slot, var_type = self.locals[name]
            return slot, var_type, var_type.volatile
        static = self.static_globals.get((self.current_source, name))
        entry = static or self.globals.get(name)
        if entry is not None:
            private, source = self.global_privacy[name]
            self.check_access(private, source, f"variable {name!r}", line)
            glob, var_type, volatile = entry
            return glob, var_type, volatile or var_type.volatile
        if name in self.consts:
            raise LangError(f"cannot assign to constant {name!r}", line)
        raise LangError(f"undefined variable {name!r}", line)

    def writes_const(self, target) -> bool:
        """Whether a store to ``target`` lands in a const parameter's storage.

        The write stays in the parameter's own storage along a chain of value
        member accesses (``.``) and in-storage array indexing. A ``->``, a
        ``*``, or indexing through a pointer crosses into separate storage,
        which a ``const`` parameter does not protect.

        Args:
            target: The lvalue being written.

        Returns:
            ``True`` when the write would mutate a const parameter.
        """
        if isinstance(target, Var):
            return target.name in self.const_locals
        if isinstance(target, Member) and not target.arrow:
            return self.writes_const(target.base)
        if isinstance(target, Index):
            base_t = self.lvalue_type(target.base)
            if base_t is not None and is_array(base_t):
                return self.writes_const(target.base)
        return False

    def roots_in_mut(self, target) -> bool:
        """Whether an lvalue's storage is (part of) a ``mut`` parameter's.

        The dual of :meth:`writes_const`'s traversal: the address stays inside
        the parameter's storage along value member accesses (``.``) and
        in-storage array indexing, which is exactly what ``&`` must not leak.
        A ``->``, a ``*``, or indexing through a pointer crosses into separate
        storage the non-escape guarantee does not cover.

        Args:
            target: The lvalue whose address is being taken.

        Returns:
            ``True`` when the address derives from a mut parameter.
        """
        if isinstance(target, Var):
            return target.name in self.mut_locals
        if isinstance(target, Member) and not target.arrow:
            return self.roots_in_mut(target.base)
        if isinstance(target, Index):
            base_t = self.lvalue_type(target.base)
            if base_t is not None and is_array(base_t):
                return self.roots_in_mut(target.base)
        return False

    def lvalue_type(self, expr) -> "LangType | None":
        """Best-effort static type of a simple lvalue, without emitting code.

        Resolves ``Var``/``Member``/``Index`` chains; returns ``None`` when the
        type cannot be determined statically. Used only to tell an in-storage
        array index from a through-pointer one when checking const writes.

        Args:
            expr: The lvalue expression.

        Returns:
            The lvalue's ``LangType``, or ``None``.
        """
        if isinstance(expr, Var):
            return self.var_type_of(expr.name)
        if isinstance(expr, Member):
            base_t = self.lvalue_type(expr.base)
            if base_t is None:
                return None
            owner = base_t.pointee if (expr.arrow and is_pointer(base_t)) else base_t
            if not is_struct(owner):
                return None
            try:
                _, ftype = self.struct_field(owner, expr.field, 0)
            except LangError:
                return None
            return ftype
        if isinstance(expr, Index):
            base_t = self.lvalue_type(expr.base)
            if base_t is None:
                return None
            if is_array(base_t):
                return base_t.element
            if is_pointer(base_t):
                return base_t.pointee
        return None

    def sizeof_operand(self, ref: TypeRef, line: int) -> LangType:
        """Resolve a ``sizeof`` operand to the type whose size is wanted.

        The operand is normally a type, but a bare name that is a variable in
        scope is taken as that variable -- ``sizeof(v)`` is the size of ``v``'s
        type -- as in C. The operand is never evaluated, so there are no side
        effects. A name that is not a variable (or any non-trivial type form)
        resolves as a type.

        Args:
            ref: The parsed operand ``TypeRef``.
            line: Source line for diagnostics.

        Returns:
            The ``LangType`` whose size ``sizeof`` should report.
        """
        if not ref.stars and not ref.dims and not ref.args and ref.params is None:
            var_type = self.var_type_of(ref.name)
            if var_type is not None:
                return var_type
        return self.lang_type(ref, line)

    def var_type_of(self, name: str) -> "LangType | None":
        """Return a name's type if it is a variable in scope, else ``None``.

        Checks locals, ``@static`` globals, and ``@extern`` globals, so a bare
        name that is not a variable can fall back to being a function value.

        Args:
            name: The name to look up.

        Returns:
            The variable's type, or ``None`` when ``name`` is not a variable.
        """
        if name in self.locals:
            return self.locals[name][1]
        static = self.static_globals.get((self.current_source, name))
        if static is not None:
            return static[1]
        if name in self.globals:
            return self.globals[name][1]
        return None

    def gen_addr(self, expr, line: int) -> tuple[ir.Value, LangType, int | None, bool]:
        """Compute the address of an lvalue expression.

        Handles a variable, ``*deref``, an element, or a struct field.

        Args:
            expr: The lvalue expression.
            line: Source line for diagnostics.

        Returns:
            A ``(pointer value, pointee type, guaranteed alignment, volatile)``
            tuple. The alignment is ``None`` when the address is naturally
            aligned for its type and ``1`` when it may not be (a ``@packed``
            field, directly or through nesting); ``volatile`` is ``True`` when
            accesses must not be optimized away.

        Raises:
            LangError: When the expression is not addressable.
        """
        if isinstance(expr, Var):
            slot, var_type, volatile = self.var_addr(expr.name, line)
            return slot, var_type, None, volatile
        if isinstance(expr, Unary) and expr.op == "*":
            tv = self.gen_expr(expr.operand)
            if not is_pointer(tv.type):
                raise LangError(f"cannot dereference a {tv.type}", line)
            return tv.value, tv.type.pointee, None, tv.type.pointee.volatile
        if isinstance(expr, Index):
            addr, element = self.gen_index_addr(expr.base, expr.index, line)
            return addr, element, None, element.volatile
        if isinstance(expr, Member):
            return self.gen_member_addr(expr.base, expr.field, expr.arrow, line)
        if isinstance(expr, StrLit):
            # A string literal is a uint8[N] array (NUL included) living in a
            # constant global; addressing it (for len/sizeof, or a borrow) keeps
            # the array type, while reading it as a value decays to uint8*.
            glob = self.string_global(expr.value)
            return glob, self.string_array_type(expr.value), None, False
        raise LangError("expression is not addressable", line)

    def gen_index_addr(
        self, base_expr, index_expr, line: int
    ) -> tuple[ir.Value, LangType]:
        """Compute the address of ``base[index]``.

        Args:
            base_expr: The indexed base expression (a pointer or array).
            index_expr: The index expression (an integer).
            line: Source line for diagnostics.

        Returns:
            A ``(pointer value, element type)`` pair.

        Raises:
            LangError: When the base is not indexable or the index is not an
                integer.
        """
        base = self.gen_expr(base_expr)
        if is_slice(base.type):
            # A slice indexes through its `data` field into the borrowed run.
            ptr = self.builder.extract_value(base.value, base.type.elem_indices[0])
            element = base.type.fields[0][1].pointee
            index = self.gen_expr(index_expr)
            if not is_integer(index.type):
                raise LangError(f"index must be an integer, not {index.type}", line)
            return self.builder.gep(ptr, [index.value]), element
        if not is_pointer(base.type):
            raise LangError(f"cannot index a {base.type}", line)
        index = self.gen_expr(index_expr)
        if not is_integer(index.type):
            raise LangError(f"index must be an integer, not {index.type}", line)
        addr = self.builder.gep(base.value, [index.value])
        return addr, base.type.pointee

    def gen_member_addr(
        self, base_expr, fname: str, arrow: bool, line: int
    ) -> tuple[ir.Value, LangType, int | None, bool]:
        """Compute the address of ``base.field`` or ``base->field``.

        Args:
            base_expr: The struct value or pointer expression.
            fname: The field name.
            arrow: ``True`` for ``->`` (through a pointer), ``False`` for ``.``.
            line: Source line for diagnostics.

        Returns:
            A ``(pointer, field type, guaranteed alignment, volatile)`` tuple, as
            in :meth:`gen_addr`.

        Raises:
            LangError: When ``->`` is used on a non-pointer, or the field is
                unknown.
        """
        if arrow:
            base = self.gen_expr(base_expr)
            if not is_pointer(base.type):
                raise LangError(
                    f"'->' requires a struct pointer, got {base.type}", line
                )
            owner, base_addr = base.type.pointee, base.value
            base_align, base_volatile = None, False
        else:
            base_addr, owner, base_align, base_volatile = self.gen_addr(base_expr, line)
        if is_any(owner):
            # The tag/payload layout is internal; reading it would be an
            # unchecked unwrap, which v1 has none of outside `case type`.
            raise LangError(
                "an any has no fields; recover its value with case type", line
            )
        index, ftype = self.struct_field(owner, fname, line)
        if is_union(owner):
            # Every union member lives at offset 0: address it by casting the
            # union's storage to the member type instead of a GEP.
            addr = self.builder.bitcast(base_addr, ftype.ir.as_pointer())
        else:
            addr = self.builder.gep(
                base_addr, [I32_ZERO, ir.Constant(ir.IntType(32), index)],
                inbounds=True,
            )
        # A @packed owner (or a base that already sits at a packed offset)
        # gives no alignment guarantee; loads and stores must say so, or
        # LLVM would assume the field type's natural alignment.
        if owner.packed:
            align = 1
        elif base_align is not None:
            align = min(base_align, type_align(ftype))
        else:
            align = None
        # @volatile propagates from the owner down through nested fields.
        return addr, ftype, align, owner.volatile or base_volatile

    def gen_unary(self, expr: Unary) -> TypedValue:
        """Emit a unary operation: ``-``, ``~``, ``!``, ``*`` (deref), or ``&``.

        Negation and bitwise complement on a literal fold so the resulting
        constants stay adaptable; ``&`` yields a plain pointer (without any
        reduced packed alignment -- unsafe to dereference elsewhere, exactly
        as in C).

        Args:
            expr: The ``Unary`` node.

        Returns:
            The result as a ``TypedValue``.

        Raises:
            LangError: On dereferencing a non-pointer, negating or complementing
                an unsupported type, or ``!`` on a non-bool.
        """
        # Fold minus on literals so negative constants stay constants (and can
        # still coerce to other integer types).
        if expr.op == "-" and isinstance(expr.operand, IntLit):
            return adaptable_int(-expr.operand.value)
        if expr.op == "~" and isinstance(expr.operand, IntLit):
            return adaptable_int(~expr.operand.value)
        if expr.op == "-" and isinstance(expr.operand, FloatLit):
            return TypedValue(ir.Constant(FLOAT64.ir, -expr.operand.value), FLOAT64)
        if expr.op == "&":
            if self.writes_const(expr.operand):
                raise LangError(
                    "cannot take the address of a const parameter; it is read-only",
                    expr.line,
                )
            if self.roots_in_mut(expr.operand):
                raise LangError(
                    "cannot take the address of a mut parameter; "
                    "its reference must not escape the call",
                    expr.line,
                )
            if (
                isinstance(expr.operand, Var)
                and expr.operand.name in self.nonnull_locals
            ):
                raise LangError(
                    "cannot take the address of a @nonnull parameter; "
                    "null could be stored through it",
                    expr.line,
                )
            # The pointer type does not carry the (possibly reduced)
            # alignment: taking the address of a packed field and
            # dereferencing it elsewhere is unsafe, exactly as in C.
            addr, lang_type, _, _ = self.gen_addr(expr.operand, expr.line)
            return TypedValue(addr, pointer_to(lang_type))
        tv = self.gen_expr(expr.operand)
        if expr.op == "*":
            if not is_pointer(tv.type):
                raise LangError(f"cannot dereference a {tv.type}", expr.line)
            return TypedValue(
                self.gen_load(tv.value, volatile=tv.type.pointee.volatile),
                tv.type.pointee,
            )
        if expr.op == "-":
            if is_integer(tv.type) and tv.type.signed:
                return TypedValue(self.builder.neg(tv.value), tv.type)
            if tv.type is FLOAT64:
                return TypedValue(self.builder.fneg(tv.value), tv.type)
            raise LangError(f"cannot negate a {tv.type}", expr.line)
        if expr.op == "~":
            if is_integer(tv.type):
                return TypedValue(self.builder.not_(tv.value), tv.type)
            raise LangError(f"cannot apply '~' to a {tv.type}", expr.line)
        if tv.type is not BOOL:
            raise LangError("'!' requires a bool operand", expr.line)
        return TypedValue(self.builder.not_(tv.value), BOOL)

    def gen_nonnull_assert(self, expr: NonnullAssert) -> TypedValue:
        """Evaluate a postfix non-null assertion: ``p!``.

        The assertion is purely static: the operand's value passes through
        unchanged and no instructions are emitted -- no runtime check, ever.
        Its only effect is on the compile-time proof (:meth:`proves_nonnull`
        accepts it), so a heap or returned pointer can cross into a
        ``@nonnull`` slot. Asserting a pointer that is actually null is
        undefined behavior. Asserting the ``null`` literal -- always wrong --
        is rejected outright, as is a non-pointer operand.

        Args:
            expr: The ``NonnullAssert`` node.

        Returns:
            The operand's value, unchanged.

        Raises:
            LangError: When the operand is the ``null`` literal or not a
                pointer.
        """
        if isinstance(expr.operand, NullLit):
            raise LangError("cannot assert null as non-null", expr.line)
        tv = self.gen_expr(expr.operand)
        if not is_pointer(tv.type):
            raise LangError(
                f"postfix '!' asserts a pointer non-null, but the operand "
                f"is a {tv.type}",
                expr.line,
            )
        return tv

    def gen_asm(self, expr: Asm) -> TypedValue:
        """Emit an inline-assembly call from an ``@asm(...)`` expression.

        Builds an LLVM ``InlineAsm`` with an ``=r`` output (when a return type
        is given) and an ``r`` input per operand, then rewrites the template's
        friendly operand names to LLVM's numbering: ``$out`` is the output and
        ``$0``, ``$1``, ... are the inputs. A register modifier may follow in
        braces -- ``${out:w}``, ``${0:w}`` -- and is passed through to LLVM
        (e.g. on aarch64 a bare operand is the 64-bit ``x`` register and ``:w``
        selects the 32-bit ``w`` name, exactly as in C inline asm). Any
        ``@clobbers(...)`` names are appended to the constraint string as
        ``~{name}`` entries. Following GCC, an asm with no output is treated as
        having side effects (implicitly volatile) so it is not reordered or
        removed; one with an output is assumed pure.

        Operands and the output must be integers or pointers (the ``r``
        register class); floats and structs are rejected.

        Args:
            expr: The ``Asm`` node.

        Returns:
            The output as a ``TypedValue``, or ``void`` for an output-less asm.

        Raises:
            LangError: On a non-register operand/output type, or a template
                operand reference that names a missing input or ``$out``.
        """
        out = None
        if expr.out_type is not None:
            out = self.lang_type(expr.out_type, expr.line)
            if out is VOID:
                out = None
            elif not (is_integer(out) or is_pointer(out)):
                raise LangError(
                    f"an @asm output must be an integer or pointer, not {out}",
                    expr.line,
                )

        inputs = [self.gen_expr(a) for a in expr.inputs]
        for tv in inputs:
            if not (is_integer(tv.type) or is_pointer(tv.type)):
                raise LangError(
                    f"an @asm operand must be an integer or pointer, not {tv.type}",
                    expr.line,
                )

        constraints = (
            (["=r"] if out else [])
            + ["r"] * len(inputs)
            + [f"~{{{c}}}" for c in expr.clobbers]
        )
        offset = 1 if out else 0

        def rewrite(m: re.Match) -> str:
            ref = m.group(1) or m.group(2)  # bare $0 / $out, or braced ${0:w}
            modifier = m.group(3) or ""  # the ":w" etc., if braced
            if ref == "out":
                if not out:
                    raise LangError(
                        "@asm uses '$out' but has no output (add '-> type')",
                        expr.line,
                    )
                llvm_index = 0
            else:
                index = int(ref)
                if index >= len(inputs):
                    raise LangError(
                        f"@asm references operand ${index} but only "
                        f"{len(inputs)} were given",
                        expr.line,
                    )
                llvm_index = index + offset
            return f"${{{llvm_index}{modifier}}}" if modifier else f"${llvm_index}"

        template = re.sub(
            r"\$(?:(out|\d+)|\{(out|\d+)(:[A-Za-z]+)?\})", rewrite, expr.template
        )
        ret_ir = out.ir if out else ir.VoidType()
        fnty = ir.FunctionType(ret_ir, [tv.type.ir for tv in inputs])
        inline = ir.InlineAsm(
            fnty, template, ",".join(constraints), side_effect=out is None
        )
        result = self.builder.call(inline, [tv.value for tv in inputs])
        return TypedValue(result, out) if out else TypedValue(result, VOID)

    def gen_cast(self, expr: Cast) -> TypedValue:
        """Emit an explicit ``value as type`` conversion.

        Supports pointer/function-pointer bitcasts, pointer-integer conversions,
        integer truncation/extension (by signedness), integer-to-bool, and the
        ``float64`` conversions. A cast to ``slice<T>`` is a borrow (see
        :meth:`gen_borrow_slice`); other struct casts are rejected, except the
        ``extends`` value-upcast.

        Args:
            expr: The ``Cast`` node.

        Returns:
            The converted value as a ``TypedValue``.

        Raises:
            LangError: On an unsupported conversion (e.g. involving a struct).
        """
        target = self.lang_type(expr.type_name, expr.line)
        if is_slice(target):
            # A "borrow" cast: build a non-owning view over an owned list<T> or
            # T[N]. Resolved before the operand is read, so an array keeps its
            # static length instead of decaying to a bare pointer.
            return self.gen_borrow_slice(expr.value, target, expr.line)
        tv = self.gen_expr(expr.value)
        src = tv.type
        if src == target:
            return TypedValue(tv.value, target)
        # No unwrap outside `case type` in v1: with no checked-failure
        # mechanism, an `as` on an any would be either a tag-ignoring pun or
        # a new trap. Boxing needs no cast either -- it is implicit.
        if is_any(src):
            raise LangError(
                f"cannot cast an any to {target}; recover its value with "
                "case type",
                expr.line,
            )
        if is_any(target):
            raise LangError(
                f"cannot cast {src} to any; boxing is implicit, assign or "
                "pass the value directly",
                expr.line,
            )
        # A function value is a pointer underneath (LLVM `ret (args)*`), so it
        # casts like one: between pointer kinds, and to/from a 64-bit integer
        # address -- exactly as a function name converts to an address in C.
        src_addr = is_pointer(src) or is_function(src)
        target_addr = is_pointer(target) or is_function(target)
        if src_addr and target_addr:
            return TypedValue(self.builder.bitcast(tv.value, target.ir), target)
        if src_addr and is_integer(target) and target.ir.width == 64:
            return TypedValue(self.builder.ptrtoint(tv.value, target.ir), target)
        if is_integer(src) and target_addr:
            return TypedValue(self.builder.inttoptr(tv.value, target.ir), target)
        if is_struct(src) and is_struct(target) and self.is_struct_prefix(target, src):
            # Value upcast: `target` is the leading prefix of `src` (via
            # `extends`), so it occupies the same starting bytes. Round-trip
            # through memory -- store the derived value, reinterpret the slot as
            # the base, load -- which keeps any @packed/@align padding identical.
            slot = self.builder.alloca(src.ir)
            self.builder.store(tv.value, slot)
            base_ptr = self.builder.bitcast(slot, target.ir.as_pointer())
            return TypedValue(self.builder.load(base_ptr), target)
        if is_struct(src) or is_struct(target):
            raise LangError(f"cannot cast {src} to {target}", expr.line)
        if isinstance(src.ir, ir.IntType) and target is BOOL:
            zero = ir.Constant(src.ir, 0)
            return TypedValue(self.builder.icmp_signed("!=", tv.value, zero), BOOL)
        if isinstance(src.ir, ir.IntType) and isinstance(target.ir, ir.IntType):
            src_width, dst_width = src.ir.width, target.ir.width
            if dst_width == src_width:
                value = tv.value  # same bits, different signedness
            elif dst_width < src_width:
                value = self.builder.trunc(tv.value, target.ir)
            elif src.signed:
                value = self.builder.sext(tv.value, target.ir)
            else:
                value = self.builder.zext(tv.value, target.ir)
            return TypedValue(value, target)
        if isinstance(src.ir, ir.IntType) and target is FLOAT64:
            convert = self.builder.sitofp if src.signed else self.builder.uitofp
            return TypedValue(convert(tv.value, target.ir), target)
        if src is FLOAT64 and is_integer(target):
            convert = self.builder.fptosi if target.signed else self.builder.fptoui
            return TypedValue(convert(tv.value, target.ir), target)
        raise LangError(f"cannot cast {src} to {target}", expr.line)

    def make_slice(self, target: LangType, ptr, length) -> TypedValue:
        """Assemble a ``slice<T>`` value from a pointer and a length.

        Args:
            target: The ``slice<T>`` type to build.
            ptr: The ``T*`` pointer to the borrowed run's first element.
            length: The element count, an ``i64``.

        Returns:
            The assembled slice as a ``TypedValue``.
        """
        agg = ir.Constant(target.ir, None)
        agg = self.builder.insert_value(agg, ptr, target.elem_indices[0])
        agg = self.builder.insert_value(agg, length, target.elem_indices[1])
        return TypedValue(agg, target)

    def check_borrow_element(
        self, src_elem: LangType, value_expr, src_label: str, target: LangType, line: int
    ):
        """Validate a borrow source's element against the target slice's element.

        The underlying element types must match. ``const`` may only be *added* --
        a mutable source borrows to a ``slice<const T>`` (read-only is the safe,
        common view) -- never dropped: a read-only source (a ``const`` element,
        or a ``const`` parameter/variable) cannot become a mutable ``slice<T>``,
        which would reopen a write path.

        Args:
            src_elem: The source's element type (possibly ``const``).
            value_expr: The borrowed expression, to detect a ``const`` parameter.
            src_label: How to name the source in an error message.
            target: The ``slice<T>`` being produced.
            line: Source line for diagnostics.

        Raises:
            LangError: On an element-type mismatch or a const-dropping borrow.
        """
        target_elem = target.fields[0][1].pointee
        if strip_const(src_elem) != strip_const(target_elem):
            raise LangError(
                f"cannot borrow {src_label} as {target}: element type is "
                f"{strip_const(src_elem)}, not {strip_const(target_elem)}",
                line,
            )
        if (src_elem.const or self.writes_const(value_expr)) and not target_elem.const:
            raise LangError(
                f"cannot borrow read-only {src_label} as the mutable {target}; "
                f"borrow as slice<{const_of(strip_const(target_elem))}>",
                line,
            )

    def str_literal_adapts(self, expr, expected: LangType) -> bool:
        """Whether a string literal adapts to ``expected`` without an ``as``.

        Stage 4 "borrow-in": a string literal (a ``char[N]``) implicitly adapts
        to a ``slice<char>`` (or its read-only ``slice<const char>`` form) from
        context -- a function argument, a ``let``/return slot -- the way an
        untyped constant takes its type from context. The borrow drops the
        trailing NUL (the text), and the global stays NUL-terminated so the same
        literal still serves as a ``char*``/``uint8*`` for C. This is the
        implicit form of the Stage 1 ``"..." as slice<char>`` borrow.

        A ternary adapts when both of its arms do (``cond ? "yes" : "no"``,
        nested ternaries included): each arm borrows in its own branch, so the
        merged value is already the expected slice.

        Args:
            expr: The argument/initializer expression.
            expected: The type the context expects.

        Returns:
            ``True`` if ``expr`` is a string literal (or a ternary of adapting
            arms) and ``expected`` is a ``slice<char>`` or ``slice<const
            char>``.
        """
        if isinstance(expr, Ternary):
            return self.str_literal_adapts(expr.then, expected) and self.str_literal_adapts(
                expr.otherwise, expected
            )
        return (
            isinstance(expr, StrLit)
            and is_slice(expected)
            and strip_const(expected.args[0]) == CHAR
        )

    def gen_borrow_slice(self, value_expr, target: LangType, line: int) -> TypedValue:
        """Lower a borrow ``value as slice<T>`` into a non-owning view.

        A fixed array ``T[N]`` borrows to ``{first-element pointer, N}`` (read
        through its address, so the static length survives the array's usual
        decay). A ``char[N]`` is NUL-terminated text, so its borrow drops the
        trailing terminator -- length ``N - 1`` -- giving the text without the
        NUL (the buffer keeps it, so it still serves as a ``char*``); a
        ``uint8[N]`` raw buffer keeps every byte. A ``slice<T>`` borrows to
        itself, and any struct that ``extends slice<T>`` (such as an owned
        ``list<T>``) borrows to its leading ``slice<T>`` -- the struct-prefix
        relationship, so no field name is assumed -- dropping the extra fields
        (a list's ``capacity``). A ternary borrows arm by arm -- each arm
        lowers in its own branch and the views merge in a phi -- so
        ``flag ? "y" : "yes"`` is a ``slice<char>`` carrying the chosen
        literal's own length.

        A mutable source may borrow to its read-only ``slice<const T>`` form
        (adding ``const`` is safe); a read-only source -- a ``const`` element or
        a ``const`` parameter/variable -- borrows only to ``slice<const T>``, so
        the conversion preserves immutability (see :meth:`check_borrow_element`).

        Args:
            value_expr: The owned value being borrowed.
            target: The ``slice<T>`` view type to produce.
            line: Source line for diagnostics.

        Returns:
            The borrowed view as a ``TypedValue``.

        Raises:
            LangError: When the source cannot be borrowed as ``target`` (wrong
                shape), its element type does not match ``T``, or a read-only
                source would borrow to a mutable slice.
        """
        if isinstance(value_expr, Ternary):
            # Borrowing distributes over a ternary: each arm borrows to the
            # target in its own block (so a literal's static length survives),
            # and the two views merge in a phi. Only the taken arm runs.
            cond = self.gen_cond(value_expr.cond)
            then_bb = self.builder.append_basic_block("borrow.then")
            else_bb = self.builder.append_basic_block("borrow.else")
            end_bb = self.builder.append_basic_block("borrow.end")
            self.builder.cbranch(cond, then_bb, else_bb)
            self.builder.position_at_end(then_bb)
            then_tv = self.gen_borrow_slice(value_expr.then, target, line)
            then_end = self.builder.block
            self.builder.branch(end_bb)
            self.builder.position_at_end(else_bb)
            else_tv = self.gen_borrow_slice(value_expr.otherwise, target, line)
            else_end = self.builder.block
            self.builder.branch(end_bb)
            self.builder.position_at_end(end_bb)
            phi = self.builder.phi(target.ir)
            phi.add_incoming(then_tv.value, then_end)
            phi.add_incoming(else_tv.value, else_end)
            return TypedValue(phi, target)
        element = target.fields[0][1].pointee
        # T[N] (or a string literal, a uint8[N]): take the first-element pointer
        # and the static length, which would otherwise decay away once the value
        # is read. Reached through the address, so the array type survives.
        src_t = self.lvalue_type(value_expr)
        if src_t is not None and is_flexible_array(src_t):
            raise LangError(
                "cannot borrow a flexible array member as a slice; its length is "
                "not known statically -- index it through its pointer instead",
                line,
            )
        if isinstance(value_expr, StrLit) or (src_t is not None and is_array(src_t)):
            addr, owner, _, _ = self.gen_addr(value_expr, line)
            self.check_borrow_element(owner.element, value_expr, str(owner), target, line)
            ptr = self.builder.gep(addr, [I32_ZERO, I32_ZERO], inbounds=True)
            # A char[N] is NUL-terminated text: drop the terminator so the view
            # spans the string, not the trailing NUL. A uint8[N] is a raw byte
            # buffer, kept whole.
            is_text = strip_const(element) == CHAR
            length = owner.count - 1 if is_text else owner.count
            return self.make_slice(target, ptr, ir.Constant(UINT64.ir, length))
        src = self.gen_expr(value_expr)
        owner, struct_val = src.type, src.value
        if is_pointer(owner) and is_struct(owner.pointee):
            owner = owner.pointee  # a list<T>* (or slice<T>*) borrows like the value
            struct_val = self.gen_load(src.value)
        # The source borrows to the slice when it *is* a slice<T> or *extends*
        # one (as list<T> does): its leading fields are then exactly that slice.
        # The check is by struct compatibility -- the slice<T> is the layout
        # prefix of the source (:meth:`is_struct_prefix`) -- so it follows the
        # `extends` chain without hardcoding any field names. The element may
        # gain `const` (see :meth:`check_borrow_element`); const shares the
        # layout, so the leading {data, length} transfer straight across.
        prefix = self.slice_type(strip_const(element), line)
        if is_slice(owner) or self.is_struct_prefix(prefix, owner):
            self.check_borrow_element(
                owner.fields[0][1].pointee, value_expr, str(src.type), target, line
            )
            if is_slice(owner):
                return TypedValue(struct_val, target)
            ptr = self.builder.extract_value(struct_val, owner.elem_indices[0])
            length = self.builder.extract_value(struct_val, owner.elem_indices[1])
            return self.make_slice(target, ptr, length)
        raise LangError(
            f"cannot borrow {src.type} as {target}; borrow an array, a slice, or "
            "a value that extends slice<T> (such as an owned list<T>)",
            line,
        )

    def string_data(self, text: str) -> bytearray:
        """The bytes a string literal occupies: its UTF-8 plus a NUL terminator.

        Args:
            text: The string contents.

        Returns:
            The NUL-terminated UTF-8 bytes.
        """
        return bytearray(text.encode("utf8") + b"\0")

    def string_array_type(self, text: str) -> LangType:
        """The ``char[N]`` array type a string literal denotes.

        ``N`` counts the trailing NUL, so the bytes stay a valid C string when
        the array decays to a ``char*``.

        Args:
            text: The string contents.

        Returns:
            The ``char[N]`` ``LangType``.
        """
        return list_of(CHAR, len(self.string_data(text)))

    def string_global(self, text: str) -> ir.GlobalVariable:
        """Create a private constant global holding a string's bytes.

        Args:
            text: The string contents.

        Returns:
            A private, unnamed, constant ``GlobalVariable`` holding the
            NUL-terminated UTF-8 bytes of ``text``.
        """
        data = self.string_data(text)
        list_ty = ir.ArrayType(ir.IntType(8), len(data))
        glob = ir.GlobalVariable(self.module, list_ty, name=f".str.{self.str_count}")
        self.str_count += 1
        glob.linkage = "private"
        glob.global_constant = True
        glob.unnamed_addr = True
        glob.initializer = ir.Constant(list_ty, data)
        return glob

    def gen_string(self, text: str) -> TypedValue:
        """Emit a ``char*`` pointing at a string literal's bytes.

        A string literal is a ``char[N]``; read as a value it decays to a
        ``char*`` (which coerces to ``uint8*`` like any pointer, so the libc
        string functions still take it).

        Args:
            text: The string contents.

        Returns:
            A ``char*`` ``TypedValue`` to the string's first byte.
        """
        return TypedValue(
            self.builder.bitcast(self.string_global(text), CHARPTR.ir), CHARPTR
        )

    def const_string(self, text: str) -> ir.Constant:
        """Build a constant ``uint8*`` to a string's first byte.

        For use in ``@static`` initializers, where no builder is available.

        Args:
            text: The string contents.

        Returns:
            A constant pointer to the string's first byte.
        """
        return self.string_global(text).gep([I32_ZERO, I32_ZERO])

    def string_array_let_type(self, declared: LangType, text: str, line: int) -> LangType:
        """Validate an array type a string-literal ``let`` is bound to.

        The element type must be ``char`` (text) or ``uint8`` (raw bytes), and
        the array must be large enough to hold the literal's bytes (its NUL
        included); a larger ``[M]`` is zero-filled past the string.

        Args:
            declared: The resolved array type the literal is bound to.
            text: The string contents.
            line: Source line for diagnostics.

        Returns:
            ``declared`` unchanged, once validated.

        Raises:
            LangError: When the element type is not ``char``/``uint8`` or the
                array is too small to hold the string.
        """
        if declared.element not in (CHAR, UINT8):
            raise LangError(
                f"a string literal initializes a char/uint8 array or a "
                f"char*/uint8*, not a {declared}",
                line,
            )
        need = len(self.string_data(text))
        if declared.count < need:
            raise LangError(
                f"string literal needs {need} bytes (its NUL included) but the "
                f"array holds only {declared.count}",
                line,
            )
        return declared

    def store_string_literal(self, addr, text: str, arr_type: LangType, line: int):
        """Copy a string literal's bytes into an owned ``uint8[N]`` array.

        Stores the literal's NUL-terminated bytes, zero-filling any array slots
        past the string (an oversized ``uint8[M]`` annotation).

        Args:
            addr: The array's storage address.
            text: The string contents.
            arr_type: The ``uint8[N]`` array type being filled.
            line: Source line for diagnostics.
        """
        data = self.string_data(text)
        data.extend(b"\0" * (arr_type.count - len(data)))  # zero-pad an oversize buffer
        self.builder.store(ir.Constant(arr_type.ir, data), addr)

    def store_list_literal(self, addr, lit, arr_type: LangType, line: int):
        """Fill an array's storage from an array literal, element by element.

        Each element may be any expression; nested literals recurse for
        multi-dimensional arrays.

        Args:
            addr: The array's storage address.
            lit: The ``ArrayLit`` providing the elements.
            arr_type: The array type being filled.
            line: Source line for diagnostics.

        Raises:
            LangError: When the literal's shape or length does not match
                ``arr_type``.
        """
        if not isinstance(lit, ArrayLit):
            raise LangError(f"expected {arr_type.count} array elements", line)
        if len(lit.elements) != arr_type.count:
            raise LangError(
                f"array literal has {len(lit.elements)} elements, expected {arr_type.count}",
                line,
            )
        for i, element in enumerate(lit.elements):
            slot = self.builder.gep(
                addr, [I32_ZERO, ir.Constant(ir.IntType(32), i)], inbounds=True
            )
            if is_array(arr_type.element):
                self.store_list_literal(slot, element, arr_type.element, line)
            elif self.str_literal_adapts(element, arr_type.element):
                # A string-literal element adapts to a slice<char> element
                # (Stage 4 borrow-in) the way it does in a top-level slot:
                # each element borrows its string constant's bytes, NUL
                # dropped, so `let dirs: slice<char>[2] = ["bin", "usr/bin"];`
                # needs no per-element `as`.
                tv = self.gen_borrow_slice(element, arr_type.element, line)
                self.gen_store(tv.value, slot)
            else:
                tv = self.coerce(
                    self.gen_expr(element), arr_type.element, line, "array element"
                )
                self.gen_store(tv.value, slot)

    def const_initializer(self, expr, expected: LangType, line: int) -> ir.Constant:
        """Build a constant of a given type for a ``@static`` initializer.

        Arrays use nested literals; scalars may be any compile-time constant
        expression -- a literal, a ``const`` reference, an ``as`` cast,
        ``sizeof``, or arithmetic -- folded via :meth:`eval_const`.

        Args:
            expr: The initializer expression.
            expected: The required constant type.
            line: Source line for diagnostics.

        Returns:
            The constant value.

        Raises:
            LangError: When ``expr`` is not a constant of ``expected``, or
                ``expected`` is a union (not supported yet).
        """
        if is_union(expected):
            raise LangError(
                "a global union initializer is not supported yet; "
                "assign the member at runtime instead",
                line,
            )
        if is_any(expected):
            # Boxing a constant needs the const-initializer path to build the
            # tagged 24-byte aggregate; until then, the same shape as the
            # global union initializer gap above.
            raise LangError(
                "a global any initializer is not supported yet; "
                "assign the value at runtime instead",
                line,
            )
        if isinstance(expr, ArrayLit):
            if not is_array(expected):
                raise LangError(
                    f"an array literal cannot initialize a {expected}", line
                )
            if len(expr.elements) != expected.count:
                raise LangError(
                    f"array literal has {len(expr.elements)} elements, "
                    f"expected {expected.count}",
                    line,
                )
            return ir.Constant(
                expected.ir,
                [
                    self.const_initializer(e, expected.element, line)
                    for e in expr.elements
                ],
            )
        if isinstance(expr, StrLit) and expected in (CHARPTR, RAWPTR):
            # A char*/uint8* static initialized from a string literal: a constant
            # pointer to the shared bytes (the IR pointer is i8* either way).
            ptr = self.const_string(expr.value)
            return ptr if expected == CHARPTR else ptr.bitcast(expected.ir)
        if isinstance(expr, StrLit) and self.str_literal_adapts(expr, expected):
            # A slice<char>/slice<const char> initialized from a string
            # literal: the Stage 4 borrow-in in constant form -- a constant
            # {pointer, length} view into the shared NUL-terminated bytes,
            # with the NUL dropped from the length (the text, as at runtime).
            # The pointee is a true constant global, so the view is safe even
            # for a global: no backing-storage or lifetime question. Reached
            # per element through the ArrayLit recursion above, this also
            # covers a @static array of slices.
            length = len(self.string_data(expr.value)) - 1
            return ir.Constant(
                expected.ir,
                [self.const_string(expr.value), ir.Constant(UINT64.ir, length)],
            )
        if isinstance(expr, StrLit) and is_array(expected):
            # A char[N]/uint8[N] initialized from a string literal: the bytes
            # inline, zero-filled past the string (an oversize buffer).
            self.string_array_let_type(expected, expr.value, line)
            data = self.string_data(expr.value)
            data.extend(b"\0" * (expected.count - len(data)))
            return ir.Constant(expected.ir, data)
        if isinstance(expr, NullLit) and is_pointer(expected):
            return ir.Constant(expected.ir, None)
        if isinstance(expr, (IntLit, CharLit)) and is_integer(expected):
            return self.coerce(
                self.gen_const_scalar(expr), expected, line, "initializer"
            ).value
        if isinstance(expr, FloatLit) and expected is FLOAT64:
            return ir.Constant(FLOAT64.ir, expr.value)
        # Any other constant expression -- a const reference, an `as` cast,
        # `sizeof`, or arithmetic -- is folded like a `const` initializer and
        # coerced to the declared type.
        return self.const_coerce(
            self.eval_const(expr, line), expected, line, "@static initializer"
        ).value

    def gen_const_scalar(self, expr) -> TypedValue:
        """Build an adaptable constant for an integer or char literal.

        Used outside a function body (no builder is available), for
        :meth:`const_initializer`.

        Args:
            expr: An ``IntLit`` or ``CharLit``.

        Returns:
            The literal as a ``TypedValue``.
        """
        if isinstance(expr, CharLit):
            return TypedValue(ir.Constant(CHAR.ir, expr.value), CHAR)
        return adaptable_int(expr.value)

    def eval_const(self, expr, line: int) -> TypedValue:
        """Fold a ``const`` initializer to a constant ``TypedValue``.

        Handles literals, references to other consts, ``sizeof``, numeric casts,
        and integer/float arithmetic; an untyped integer result stays adaptable,
        like a literal. Anything that needs the runtime is an error. Built
        without a builder, since consts are folded before any function.

        Args:
            expr: The initializer expression.
            line: Source line for diagnostics.

        Returns:
            The folded value as a ``TypedValue`` wrapping an ``ir.Constant``.

        Raises:
            LangError: When the expression is not a compile-time constant.
        """
        if isinstance(expr, IntLit):
            return adaptable_int(expr.value)
        if isinstance(expr, CharLit):
            return TypedValue(ir.Constant(CHAR.ir, expr.value), CHAR)
        if isinstance(expr, FloatLit):
            return TypedValue(ir.Constant(FLOAT64.ir, expr.value), FLOAT64)
        if isinstance(expr, BoolLit):
            return TypedValue(ir.Constant(BOOL.ir, int(expr.value)), BOOL)
        if isinstance(expr, NullLit):
            return TypedValue(ir.Constant(RAWPTR.ir, None), NULLT, adaptable=True)
        if isinstance(expr, StrLit):
            return TypedValue(self.const_string(expr.value), CHARPTR)
        if isinstance(expr, SizeOf):
            size = type_size(self.lang_type(expr.type_name, line))
            return TypedValue(ir.Constant(UINT64.ir, size), UINT64)
        if isinstance(expr, AlignOf):
            align = type_align(self.lang_type(expr.type_name, line))
            return TypedValue(ir.Constant(UINT64.ir, align), UINT64)
        if isinstance(expr, OffsetOf):
            struct_type = self.lang_type(expr.type_name, line)
            off = field_offset(struct_type, expr.field, line)
            return TypedValue(ir.Constant(UINT64.ir, off), UINT64)
        if isinstance(expr, Var):
            const = self.consts.get(expr.name)
            if const is not None:
                self.check_access(
                    *self.const_privacy[expr.name], f"constant {expr.name!r}", expr.line
                )
                return const
            # A bare function name folds to its address (a constant function
            # pointer), so a @static table of functions can be initialized.
            fv = self.func_value(expr.name, expr.line)
            if fv is not None:
                return fv
            raise LangError(
                f"{expr.name!r} is not a constant; a const initializer must be "
                "a compile-time constant",
                expr.line,
            )
        if isinstance(expr, EnumAccess):
            return self.resolve_enum_access(expr)
        if isinstance(expr, Unary):
            return self.eval_const_unary(expr)
        if isinstance(expr, Cast):
            return self.eval_const_cast(expr)
        if isinstance(expr, Binary):
            return self.eval_const_binary(expr)
        if isinstance(expr, Ternary):
            cond = self.eval_const(expr.cond, line)
            if not isinstance(cond.value, ir.Constant) or not (
                is_integer(cond.type) or cond.type is BOOL
            ):
                raise LangError(
                    "a const ternary needs a constant bool or integer condition",
                    expr.line,
                )
            chosen = expr.then if cond.value.constant else expr.otherwise
            return self.eval_const(chosen, line)
        raise LangError("a const initializer must be a compile-time constant", line)

    def const_coerce(
        self, tv: TypedValue, expected: LangType, line: int, context: str
    ) -> TypedValue:
        """Coerce a constant to an expected type without a builder.

        The constant-evaluation counterpart of :meth:`coerce`: handles equality,
        ``null`` to a pointer, and adaptable integer narrowing.

        Args:
            tv: The constant value to coerce.
            expected: The required type.
            line: Source line for diagnostics.
            context: A label for the error message.

        Returns:
            ``tv`` unchanged, or a new constant adapted to ``expected``.

        Raises:
            LangError: When the constant cannot match ``expected`` or is out of
                range.
        """
        if tv.type == expected:
            return tv
        if tv.type is NULLT and (is_pointer(expected) or is_function(expected)):
            return TypedValue(ir.Constant(expected.ir, None), expected)
        # A char*/any pointer constant coerces to uint8* (raw memory), so a
        # string literal still initializes a uint8* const.
        if expected == RAWPTR and is_pointer(tv.type):
            return TypedValue(tv.value.bitcast(RAWPTR.ir), RAWPTR)
        if (
            (tv.adaptable or tv.type == CHAR)
            and is_integer(tv.type)
            and is_integer(expected)
            and isinstance(tv.value.constant, int)
        ):
            width = expected.ir.width
            lo, hi = (
                (-(1 << (width - 1)), 1 << (width - 1))
                if expected.signed
                else (0, 1 << width)
            )
            if lo <= tv.value.constant < hi:
                return TypedValue(ir.Constant(expected.ir, tv.value.constant), expected)
            raise LangError(
                f"constant {tv.value.constant} is out of range for {expected}", line
            )
        raise LangError(f"{context}: expected {expected}, got {tv.type}", line)

    def eval_const_unary(self, expr: Unary) -> TypedValue:
        """Fold a unary operation on a constant operand.

        Args:
            expr: The ``Unary`` node.

        Returns:
            The folded ``TypedValue``.

        Raises:
            LangError: When the operator is not constant for the operand type.
        """
        operand = self.eval_const(expr.operand, expr.line)
        if expr.op == "-" and is_integer(operand.type):
            return TypedValue(
                ir.Constant(
                    operand.type.ir, wrap_int(-operand.value.constant, operand.type)
                ),
                operand.type,
                adaptable=operand.adaptable,
            )
        if expr.op == "-" and operand.type is FLOAT64:
            return TypedValue(ir.Constant(FLOAT64.ir, -operand.value.constant), FLOAT64)
        if expr.op == "!" and operand.type is BOOL:
            return TypedValue(
                ir.Constant(BOOL.ir, int(not operand.value.constant)), BOOL
            )
        raise LangError(
            f"operator {expr.op!r} is not a compile-time constant for {operand.type}",
            expr.line,
        )

    def eval_const_cast(self, expr: Cast) -> TypedValue:
        """Fold an ``as`` cast on a constant operand.

        Handles the numeric conversions and the pointer conversions that LLVM
        permits as constant expressions (integer <-> pointer, pointer bitcasts).

        Args:
            expr: The ``Cast`` node.

        Returns:
            The converted constant ``TypedValue``.

        Raises:
            LangError: On a cast not allowed in a constant (e.g. involving a
                struct or float-to-pointer).
        """
        tv = self.eval_const(expr.value, expr.line)
        target = self.lang_type(expr.type_name, expr.line)
        src = tv.type
        if src == target:
            return TypedValue(tv.value, target)
        if is_integer(src) and is_integer(target):
            return TypedValue(
                ir.Constant(target.ir, wrap_int(tv.value.constant, target)), target
            )
        if is_integer(src) and target is BOOL:
            return TypedValue(ir.Constant(BOOL.ir, int(tv.value.constant != 0)), BOOL)
        if is_integer(src) and target is FLOAT64:
            return TypedValue(
                ir.Constant(FLOAT64.ir, float(tv.value.constant)), FLOAT64
            )
        if src is FLOAT64 and is_integer(target):
            return TypedValue(
                ir.Constant(target.ir, wrap_int(int(tv.value.constant), target)), target
            )
        # Pointer conversions fold to LLVM constant expressions (legal in a
        # global initializer): integer -> pointer, pointer <-> pointer, and
        # pointer -> 64-bit integer -- the same rules as gen_cast.
        src_addr = is_pointer(src) or is_function(src)
        target_addr = is_pointer(target) or is_function(target)
        if is_integer(src) and target_addr:
            return TypedValue(tv.value.inttoptr(target.ir), target)
        if src_addr and target_addr:
            return TypedValue(tv.value.bitcast(target.ir), target)
        if src_addr and is_integer(target) and target.ir.width == 64:
            return TypedValue(tv.value.ptrtoint(target.ir), target)
        raise LangError(f"cannot cast {src} to {target} in a constant", expr.line)

    def eval_const_binary(self, expr: Binary) -> TypedValue:
        """Fold a binary operation on constant operands.

        Adapts an untyped operand to the other's type, then folds comparisons,
        integer arithmetic (via :func:`fold_int_arithmetic`), and the basic
        float operations.

        Args:
            expr: The ``Binary`` node.

        Returns:
            The folded ``TypedValue``.

        Raises:
            LangError: On mismatched operand types or a non-constant operation
                (division by zero, out-of-range shift, unsupported operator).
        """
        lhs = self.eval_const(expr.lhs, expr.line)
        rhs = self.eval_const(expr.rhs, expr.line)
        if lhs.type != rhs.type:
            if (
                lhs.adaptable
                and rhs.adaptable
                and is_integer(lhs.type)
                and is_integer(rhs.type)
            ):
                # Two untyped constants of different default widths: widen both
                # to the larger so neither narrows (e.g. 1 + 5000000000).
                wide = wider_int_type(lhs.type, rhs.type)
                lhs, rhs = widen_to(lhs, wide), widen_to(rhs, wide)
            elif rhs.adaptable:
                rhs = self.const_coerce(
                    rhs, lhs.type, expr.line, f"operand of {expr.op!r}"
                )
            elif lhs.adaptable:
                lhs = self.const_coerce(
                    lhs, rhs.type, expr.line, f"operand of {expr.op!r}"
                )
            else:
                raise LangError(
                    f"operands of {expr.op!r} have different types: "
                    f"{lhs.type} and {rhs.type}",
                    expr.line,
                )
        op_type = lhs.type
        a, b = lhs.value.constant, rhs.value.constant
        if expr.op in COMPARISON_OPS and (
            is_integer(op_type) or op_type in (BOOL, FLOAT64)
        ):
            result = {
                "==": a == b,
                "!=": a != b,
                "<": a < b,
                "<=": a <= b,
                ">": a > b,
                ">=": a >= b,
            }[expr.op]
            return TypedValue(ir.Constant(BOOL.ir, int(result)), BOOL)
        if is_integer(op_type):
            if expr.op == "<<" and lhs.adaptable:
                widened = fold_untyped_shift(a, b)
                if widened is not None:
                    return widened
            folded = fold_int_arithmetic(expr.op, a, b, op_type)
            if folded is None:
                raise LangError(
                    f"{expr.op!r} is not a compile-time constant here "
                    "(division by zero or out-of-range shift)",
                    expr.line,
                )
            return TypedValue(
                ir.Constant(op_type.ir, folded),
                op_type,
                adaptable=lhs.adaptable and rhs.adaptable,
            )
        if op_type is FLOAT64 and expr.op in ("+", "-", "*", "/"):
            result = {"+": a + b, "-": a - b, "*": a * b, "/": a / b}[expr.op]
            return TypedValue(ir.Constant(FLOAT64.ir, result), FLOAT64)
        raise LangError(
            f"operator {expr.op!r} is not a compile-time constant for {op_type}",
            expr.line,
        )

    def gen_call(self, expr: Call) -> TypedValue:
        """Emit a call to a named function.

        Resolves the name in order: the ``va_start``/``va_end`` builtins, a
        same-named variable holding a function pointer (called indirectly), a
        file-scoped ``@static`` function or generic, then a global function or
        generic overload set.

        Args:
            expr: The ``Call`` node.

        Returns:
            The call's result as a ``TypedValue``.

        Raises:
            LangError: When the name is not callable, is undefined, or misuses
                generic type arguments.
        """
        # va_start/va_end are builtins, not real functions: they lower to LLVM
        # intrinsics over the va_list's address.
        if expr.name in ("va_start", "va_end") and not expr.type_args:
            return self.gen_va_builtin(expr)
        # A variable (local, parameter, @static or @extern global) shadows any
        # same-named function. A function-pointer one is called indirectly;
        # anything else is simply not callable.
        var_type = self.var_type_of(expr.name)
        if var_type is not None:
            if not is_function(var_type):
                raise LangError(
                    f"{expr.name!r} is not callable; it is a {var_type}", expr.line
                )
            if expr.type_args:
                raise LangError(f"{expr.name!r} is not a generic function", expr.line)
            callee = self.gen_expr(Var(expr.name, expr.line))
            return self.gen_indirect_call(callee, expr.args, repr(expr.name), expr.line)
        # A const may hold a function pointer (e.g. `const f = some_fn;`); call
        # it indirectly through that value, like a variable.
        const = self.consts.get(expr.name)
        if const is not None:
            if not is_function(const.type):
                raise LangError(
                    f"{expr.name!r} is not callable; it is a {const.type}", expr.line
                )
            if expr.type_args:
                raise LangError(f"{expr.name!r} is not a generic function", expr.line)
            self.check_access(
                *self.const_privacy[expr.name], f"constant {expr.name!r}", expr.line
            )
            return self.gen_indirect_call(const, expr.args, repr(expr.name), expr.line)
        # File-scoped (@static) names shadow the global namespace.
        key = (self.current_source, expr.name)
        if key in self.static_templates:
            return self.gen_generic_call(expr, [self.static_templates[key]])
        if key in self.static_funcs:
            return self.gen_direct_call(expr, self.static_funcs[key])
        # An @removed tombstone errors before any resolution or dispatch: the
        # name is dead even though nothing resolvable remains behind it, and an
        # explicit-type-arg call must error before instantiation is attempted.
        # (A variable, const, or file-scoped @static legitimately shadows a
        # global function name -- all checked above.)
        self.check_removed(expr.name, expr.line)
        generic = self.templates.get(expr.name)
        if generic is not None or expr.name in self.overloads:
            # An overload set -- generic, concrete, or mixed -- resolves in
            # one order (viability, then the (is-concrete, specificity)
            # rank) through the same pre-evaluate path. A mixed set's
            # concrete side is either the mangled set or a single plain
            # function sharing the name (same module, by construction).
            candidates = list(generic) if generic is not None else []
            if expr.name in self.overloads:
                candidates += self.overloads[expr.name]
            elif generic is not None:
                candidates += self.concrete_decls.get(expr.name, {}).values()
            if expr.type_args and generic is None:
                # Explicit type arguments select among the generic
                # candidates; a purely concrete set has none.
                raise LangError(
                    f"{expr.name!r} is not a generic function", expr.line
                )
            return self.gen_generic_call(expr, candidates)
        if expr.name not in self.funcs:
            raise LangError(
                f"undefined function {expr.name!r} (missing import?)", expr.line
            )
        private, source = self.func_privacy.get(expr.name, (False, None))
        self.check_access(private, source, f"function {expr.name!r}", expr.line)
        return self.gen_direct_call(expr, expr.name)

    def valist_arg(self, expr, line: int) -> ir.Value:
        """Lower a ``va_list`` argument to the value passed on this ABI.

        A ``va_list`` parameter already holds the passed form, so it is simply
        reloaded. A local or global names its storage, from which the passed
        form is derived: load the cursor (scalar ``va_list``, e.g. Apple arm64),
        decay to the first tag (array ``va_list``, x86-64 SysV), or pass the
        address itself (struct ``va_list``, AArch64 AAPCS) -- a pointer on every
        ABI.

        Args:
            expr: The argument expression, which must have ``va_list`` type.
            line: Source line for diagnostics.

        Returns:
            The LLVM value to pass for the ``va_list``.

        Raises:
            LangError: When the argument is not a ``va_list`` or ``va_list`` is
                unsupported on the target.
        """
        addr, t, _, _ = self.gen_addr(expr, line)
        if not is_valist(t):
            raise LangError(f"expected a va_list argument, got {t}", line)
        self.require_valist(line)
        # A va_list parameter already arrives in its passed form (a pointer to
        # the caller's storage) and is spilled to a slot holding that pointer,
        # so forwarding it is a plain reload of the slot. A local or global
        # instead names its own storage, from which the passed form is derived
        # below. (A scalar cursor is itself a pointer, so its slot also matches
        # the passed form and is reloaded here -- the same result either way.)
        if addr.type.pointee == self.va_list_passed_ir:
            return self.gen_load(addr)
        storage = t.ir
        if isinstance(storage, ir.ArrayType):
            return self.builder.gep(addr, [I32_ZERO, I32_ZERO], inbounds=True)
        return addr  # struct storage: pass its address

    def gen_va_builtin(self, expr: Call) -> TypedValue:
        """Emit ``va_start(ap, last)`` or ``va_end(ap)`` via the LLVM intrinsics.

        The intrinsic takes only the ``va_list``'s address; the named ``last``
        parameter is accepted for C familiarity but unused.

        Args:
            expr: The ``Call`` node for the builtin.

        Returns:
            A ``void`` ``TypedValue``.

        Raises:
            LangError: On the wrong arity, ``va_start`` outside a variadic
                function, a non-``va_list`` argument, or an unsupported target.
        """
        arity = 2 if expr.name == "va_start" else 1
        if len(expr.args) != arity:
            form = "va_start(ap, last_named_param)" if arity == 2 else "va_end(ap)"
            raise LangError(f"{form} takes {arity} argument(s)", expr.line)
        if expr.name == "va_start" and not self.current_variadic:
            raise LangError(
                "va_start is only valid inside a variadic function", expr.line
            )
        addr, t, _, _ = self.gen_addr(expr.args[0], expr.line)
        if not is_valist(t):
            raise LangError(f"{expr.name} requires a va_list, got {t}", expr.line)
        self.require_valist(expr.line)
        i8ptr = self.builder.bitcast(addr, RAWPTR.ir)
        return TypedValue(
            self.builder.call(self.va_intrinsic(expr.name), [i8ptr]), VOID
        )

    def va_intrinsic(self, kind: str) -> ir.Function:
        """Return the ``llvm.va_start`` / ``llvm.va_end`` intrinsic.

        Args:
            kind: ``"va_start"`` or ``"va_end"``.

        Returns:
            The ``void(i8*)`` intrinsic function, declared once on first use.
        """
        name = "llvm.va_start" if kind == "va_start" else "llvm.va_end"
        fn = self.funcs.get(name)
        if fn is None:
            fnty = ir.FunctionType(ir.VoidType(), [RAWPTR.ir])
            fn = ir.Function(self.module, fnty, name=name)
            self.funcs[name] = fn
        return fn

    def func_value(self, name: str, line: int) -> "TypedValue | None":
        """Resolve a bare function name used as a value.

        Yields the function's address typed as a function pointer. Only a single
        monomorphic function qualifies -- a generic or overloaded name has no one
        address.

        Args:
            name: The function name.
            line: Source line for diagnostics.

        Returns:
            The function pointer as a ``TypedValue``, or ``None`` when ``name``
            is not a function at all (so the caller can report it as a variable).

        Raises:
            LangError: When the name is generic, or ``@private`` and referenced
                from another file.
        """
        key = (self.current_source, name)
        if key in self.static_templates or name in self.templates:
            raise LangError(
                f"{name!r} is generic; a function value needs a single function", line
            )
        symbol = self.static_funcs.get(key)
        if symbol is None:
            # No file-scoped shadow: a removed name errors here too -- a
            # function value is a call site in waiting.
            self.check_removed(name, line)
            if name in self.overloads:
                raise LangError(
                    f"{name!r} is overloaded; a function value needs a "
                    "single function",
                    line,
                )
        if symbol is None and name in self.funcs:
            private, source = self.func_privacy.get(name, (False, None))
            self.check_access(private, source, f"function {name!r}", line)
            symbol = name
        if symbol is None:
            return None
        if self.hidden_ref.get(symbol):
            # The function takes a const struct or mut parameter by hidden
            # pointer, an ABI a plain fn(...) -> R pointer type cannot express.
            kind = "mut" if self.mut_ref.get(symbol) else "const struct"
            raise LangError(
                f"cannot take a function value of {name!r}: it has {kind} "
                "parameters (passed by hidden reference)",
                line,
            )
        if self.nonnull_ref.get(symbol):
            # A plain fn(...) type cannot carry the @nonnull contract, and a
            # call through the pointer would skip the call-site proof check.
            raise LangError(
                f"cannot take a function value of {name!r}: it has @nonnull "
                "parameters, which a plain function type cannot express",
                line,
            )
        # A function value is a call site in waiting: warn here, since calls
        # through the pointer are indirect and can no longer be attributed.
        self.warn_deprecated(name, self.deprecated_syms.get(symbol), line)
        ret, params, variadic = self.signatures[symbol]
        return TypedValue(
            self.funcs[symbol], function_type(ret, tuple(params), variadic)
        )

    def gen_direct_call(self, expr: Call, symbol: str) -> TypedValue:
        """Emit a direct call to a known, non-generic function.

        Args:
            expr: The ``Call`` node.
            symbol: The resolved LLVM symbol to call.

        Returns:
            The call's result as a ``TypedValue``.

        Raises:
            LangError: When generic type arguments are given for a non-generic
                function, or an argument fails to coerce.
        """
        if expr.type_args:
            raise LangError(f"{expr.name!r} is not a generic function", expr.line)
        self.warn_deprecated(expr.name, self.deprecated_syms.get(symbol), expr.line)
        ret, params, variadic = self.signatures[symbol]
        mut = self.mut_ref.get(symbol, frozenset())
        args = self.marshal_args(
            expr.args,
            params,
            variadic,
            repr(expr.name),
            expr.line,
            self.hidden_ref.get(symbol, frozenset()),
            mut,
            self.nonnull_ref.get(symbol, frozenset()),
            # The trailing slice<const any> type is the collecting marker;
            # only the direct-call path collects (function-pointer calls
            # stay explicit-slice), and a mut trailing parameter never does.
            collecting=self.collecting_params(params)
            and len(params) - 1 not in mut,
        )
        return TypedValue(self.builder.call(self.funcs[symbol], args), ret)

    def gen_indirect_call(
        self, callee: TypedValue, arg_exprs: list, label: str, line: int
    ) -> TypedValue:
        """Emit a call through a function-pointer value.

        The callee may be a variable, a parameter, or any expression of
        function-pointer type (e.g. a struct field).

        Args:
            callee: The function-pointer value to call.
            arg_exprs: The argument expressions.
            label: A description of the callee, for error messages.
            line: Source line for diagnostics.

        Returns:
            The call's result as a ``TypedValue``.

        Raises:
            LangError: When ``callee`` is not callable or an argument is wrong.
        """
        if not is_function(callee.type):
            raise LangError(f"cannot call a value of type {callee.type}", line)
        ret, params, variadic = callee.type.signature
        args = self.marshal_args(arg_exprs, params, variadic, label, line)
        return TypedValue(self.builder.call(callee.value, args), ret)

    def marshal_args(
        self,
        arg_exprs: list,
        params,
        variadic: bool,
        label: str,
        line: int,
        hidden: frozenset[int] = frozenset(),
        mut: frozenset[int] = frozenset(),
        nonnull: frozenset[int] = frozenset(),
        collecting: bool = False,
    ) -> list:
        """Evaluate and coerce a call's arguments against the parameter types.

        Applies C varargs promotions (small integers and bools widen to
        ``int32``) past a variadic tail, and hands a ``va_list`` over in its
        platform-specific passed form. For a collecting callee, every
        argument past the fixed parameters is instead boxed into the trailing
        ``slice<const any>`` (see :meth:`collect_variadic_args`).

        Args:
            arg_exprs: The argument expressions.
            params: The callee's parameter types.
            variadic: Whether the callee takes varargs.
            label: A description of the callee, for error messages.
            line: Source line for diagnostics.
            hidden: Indices of parameters passed by hidden reference (const
                structs and mut parameters), handed over as a pointer to the
                argument's storage.
            mut: The subset of ``hidden`` that is ``mut``: the argument must
                be the caller's own writable storage, never a temporary.
            nonnull: Indices of ``@nonnull`` parameters: the argument must be
                provably non-null (see :meth:`check_nonnull_arg`).
            collecting: Whether the callee's trailing ``slice<const any>``
                parameter collects the extra arguments (native variadics).

        Returns:
            The marshalled LLVM argument values.

        Raises:
            LangError: On a wrong argument count, a coercion failure, or passing
                a struct to a variadic function.
        """
        fixed = len(params) - 1 if collecting else len(params)
        if collecting:
            if len(arg_exprs) < fixed:
                raise LangError(
                    f"{label} expects at least {fixed} argument(s), "
                    f"got {len(arg_exprs)}",
                    line,
                )
        elif len(arg_exprs) < len(params) or (
            len(arg_exprs) > len(params) and not variadic
        ):
            raise LangError(
                f"{label} expects {len(params)} argument(s), got {len(arg_exprs)}", line
            )
        args = []
        # A C-variadic tail runs through the loop for its promotions; a
        # collecting tail is gathered separately below.
        head = arg_exprs[:fixed] if collecting else arg_exprs
        for i, arg_expr in enumerate(head):
            context = f"argument {i + 1} of {label}"
            if i in nonnull:
                self.check_nonnull_arg(arg_expr, context, line)
            if i in mut:
                # Before the string-literal adaptation: a literal is not the
                # caller's storage, so it must be rejected, not spilled.
                args.append(self.mut_ref_arg(arg_expr, params[i], line, context))
                continue
            if i < len(params) and self.str_literal_adapts(arg_expr, params[i]):
                # A string literal adapts to a char slice from the parameter type
                # (Stage 4). A `const` slice parameter is passed by hidden
                # reference, so spill the borrowed view to a temporary first.
                tv = self.gen_borrow_slice(arg_expr, params[i], line)
                if i in hidden:
                    args.append(self.spill_to_temp(tv, params[i], line, context))
                else:
                    args.append(tv.value)
                continue
            if i in hidden:
                args.append(self.hidden_ref_arg(arg_expr, params[i], line, context))
                continue
            if i < len(params) and is_valist(params[i]):
                # A va_list is handed over in its platform-specific passed form,
                # derived from its storage; coerce/decay do not apply.
                args.append(self.valist_arg(arg_expr, line))
                continue
            tv = self.gen_expr(arg_expr)
            if i < len(params):
                tv = self.coerce(tv, params[i], line, f"argument {i + 1} of {label}")
                value = tv.value
            elif is_integer(tv.type) and tv.type.ir.width < 32:
                # C varargs promote small integers to int (sign- or
                # zero-extending to match the source type's signedness).
                extend = self.builder.sext if tv.type.signed else self.builder.zext
                value = extend(tv.value, INT32.ir)
            elif tv.type is BOOL:
                value = self.builder.zext(tv.value, INT32.ir)
            elif is_struct(tv.type):
                raise LangError(
                    "cannot pass a struct to a variadic function; pass a pointer", line
                )
            else:
                value = tv.value
            args.append(value)
        if collecting:
            args.append(
                self.collect_variadic_args(
                    arg_exprs[fixed:], params[-1], fixed in hidden, label, line
                )
            )
        return args

    def collect_variadic_args(
        self, extras: list, ptype: LangType, hidden: bool, label: str, line: int
    ) -> ir.Value:
        """Gather a call's extra arguments into the trailing ``slice<const any>``.

        The native variadic model: each extra boxes into a caller-stack
        ``any`` -- entry allocas with function lifetime, so ``defer`` bodies
        and calls inside loops are safe -- and a read-only slice over the run
        is what travels, allocation-free. The pass-through rule keeps
        explicit-slice calls meaning what they always did: a lone extra that
        is already exactly ``slice<const any>`` (or ``slice<any>``, which
        widens) hands over uncollected, so it never double-boxes. Zero extras
        synthesize an empty ``{ null, 0 }`` slice. An extra that is already
        an ``any`` copies in as-is (an ``any`` never nests); everything else
        boxes through :meth:`gen_box_any`, so a struct or array extra hits
        the standard escape-hatch rejection.

        Args:
            extras: The argument expressions past the fixed parameters.
            ptype: The trailing ``slice<const any>`` parameter type.
            hidden: Whether that parameter travels by hidden reference (a
                ``const`` parameter, as the ``args...`` sugar declares).
            label: A description of the callee, for error messages.
            line: Source line for diagnostics.

        Returns:
            The LLVM value to pass for the trailing parameter.
        """
        context = f"the collected arguments of {label}"

        def passed(tv: TypedValue) -> ir.Value:
            # A const slice parameter travels by hidden reference, so the
            # formed view spills to a temporary (spill_to_temp coerces, which
            # widens a slice<any> pass-through to the const form).
            if hidden:
                return self.spill_to_temp(tv, ptype, line, context)
            return self.coerce(tv, ptype, line, context).value

        if len(extras) == 1:
            tv = self.gen_expr(extras[0])
            actual = strip_const(tv.type)
            if is_slice(actual) and is_any(actual.args[0]):
                # Pass-through: the argument count equals the parameter count
                # and the final argument already is the trailing slice type.
                return passed(TypedValue(tv.value, actual))
            boxed = [self.box_collected(tv, line)]
        else:
            boxed = [self.box_collected(self.gen_expr(e), line) for e in extras]
        if not boxed:
            data = ir.Constant(ptype.fields[0][1].ir, None)
            return passed(self.make_slice(ptype, data, ir.Constant(UINT64.ir, 0)))
        slot = self.entry_alloca(ir.ArrayType(ANY.ir, len(boxed)), "varargs.box")
        for i, value in enumerate(boxed):
            elem = self.builder.gep(
                slot, [I32_ZERO, ir.Constant(ir.IntType(32), i)], inbounds=True
            )
            self.builder.store(value, elem)
        data = self.builder.gep(slot, [I32_ZERO, I32_ZERO], inbounds=True)
        return passed(
            self.make_slice(ptype, data, ir.Constant(UINT64.ir, len(boxed)))
        )

    def box_collected(self, tv: TypedValue, line: int) -> ir.Value:
        """Box one collected extra, copying an ``any`` through unchanged.

        Args:
            tv: The evaluated extra argument.
            line: Source line for diagnostics.

        Returns:
            The 24-byte ``any`` value to store into the collection run.

        Raises:
            LangError: When the extra is outside the boxable set (see
                :meth:`check_boxable`).
        """
        if is_any(tv.type):
            return tv.value  # any to any is a plain copy, never a nesting
        return self.gen_box_any(tv, line).value

    def narrowable_guard_names(self, cond, op: str) -> set[str]:
        """Collect the locals a null-comparison guard flow-narrows.

        A bare comparison matches ``p <op> null`` or ``null <op> p`` where
        ``p`` is a bare variable eligible for narrowing: a plain pointer
        **local** (a global never narrows -- any call could store null into
        it) that is not a ``mut`` parameter (a callee taking two ``mut``
        references can alias it, so per-name invalidation at the call would
        miss the write), not already ``@nonnull`` (nothing to narrow), and
        whose address is never taken anywhere in the function (see
        :func:`collect_addr_taken`).

        ``and``/``or`` chains thread through: for the ``"!="`` query (facts
        that hold when the condition is *true*) an ``and`` unions both
        operands' facts, since both conjuncts held; for the ``"=="`` query
        (facts that hold when the condition is *false*) an ``or`` unions
        both, since both disjuncts failed. The other operator contributes
        nothing for that query -- a false ``and`` (or a true ``or``) pins
        down neither operand. Member/index expressions still carry no
        per-name fact.

        Args:
            cond: The guard condition expression.
            op: The comparison to match: ``"!="`` collects the facts implied
                by the condition being true (then branch / loop body),
                ``"=="`` the facts implied by it being false (else branch /
                the remainder after a diverging then body / a loop's exit).

        Returns:
            The narrowable variable names (possibly empty).
        """
        if isinstance(cond, Logical):
            if cond.op != ("and" if op == "!=" else "or"):
                return set()
            return self.narrowable_guard_names(
                cond.lhs, op
            ) | self.narrowable_guard_names(cond.rhs, op)
        if not isinstance(cond, Binary) or cond.op != op:
            return set()
        if isinstance(cond.lhs, Var) and isinstance(cond.rhs, NullLit):
            name = cond.lhs.name
        elif isinstance(cond.rhs, Var) and isinstance(cond.lhs, NullLit):
            name = cond.rhs.name
        else:
            return set()
        entry = self.locals.get(name)
        if entry is None or not is_pointer(entry[1]):
            return set()
        if name in self.mut_locals or name in self.nonnull_locals:
            return set()
        if name in self.addr_taken:
            return set()
        return {name}

    def narrow_nonnull(self, names: set[str]) -> set[str]:
        """Record flow-narrowed non-null facts for a guard's branch.

        Args:
            names: The names to narrow (possibly empty).

        Returns:
            The names actually added -- what the guard must remove again at
            branch exit. A name already narrowed is excluded (an outer
            guard's fact must survive this one's exit).
        """
        added = names - self.narrowed_nonnull
        self.narrowed_nonnull |= added
        return added

    def loop_kill_set(self, obj, kills: set[str] | None = None) -> set[str]:
        """Names whose flow-narrowed facts a loop could invalidate.

        A lexical pre-scan of the whole loop statement (condition and body,
        nested statements, ``defer`` bodies, and both branches of an ``@if``
        included), modeled on :func:`collect_addr_taken`. A name is killed by
        exactly the events that invalidate a narrowed fact during generation:
        an assignment (``Assign``), a compound assignment to the bare
        variable, a shadowing ``let`` (conservative: any redeclaration of
        the name), or lending the bare variable to a ``mut`` position of any
        callable sharing the callee's name (see :meth:`call_mut_positions`).
        Passing the name as a plain, ``const``, or ``@nonnull`` argument,
        ``*p = x``, and member/index stores do not kill -- none can change
        which address the variable holds. Facts that survive the kill set
        hold in the condition, the body, and past the loop's exit.

        Args:
            obj: The loop's AST node (or any subtree during recursion).
            kills: The accumulator during recursion; leave ``None``.

        Returns:
            The set of killed names.
        """
        if kills is None:
            kills = set()
        if isinstance(obj, Assign):
            kills.add(obj.name)
        elif isinstance(obj, CompoundAssign) and isinstance(obj.target, Var):
            kills.add(obj.target.name)
        elif isinstance(obj, Let):
            kills.add(obj.name)
        elif isinstance(obj, Call):
            for i in self.call_mut_positions(obj.name):
                if i < len(obj.args) and isinstance(obj.args[i], Var):
                    kills.add(obj.args[i].name)
        if is_dataclass(obj):
            for f in dataclass_fields(obj):
                self.loop_kill_set(getattr(obj, f.name), kills)
        elif isinstance(obj, (list, tuple)):
            for item in obj:
                self.loop_kill_set(item, kills)
        return kills

    def call_mut_positions(self, name: str) -> set[int]:
        """Argument indices any callable named ``name`` takes as ``mut``.

        Resolves by name only -- the union across the concrete function, every
        generic overload, and this file's ``@static`` function or template of
        that name -- because the kill-set pre-scan runs before overload
        resolution. Over-approximating is safe (an extra kill is merely
        conservative); a call through a function pointer needs no entry,
        since a function with ``mut`` parameters cannot become a function
        value.

        Args:
            name: The callee name as written at the call site.

        Returns:
            The union of ``mut`` parameter indices (possibly empty).
        """
        positions: set[int] = set()
        # concrete_decls covers every plain declaration -- overload-set
        # members included -- keyed per signature.
        for member in self.concrete_decls.get(name, {}).values():
            positions |= self.mut_indices(member)
        for overload in self.templates.get(name, ()):
            positions |= self.mut_indices(overload)
        key = (self.current_source, name)
        static_template = self.static_templates.get(key)
        if static_template is not None:
            positions |= self.mut_indices(static_template)
        static_symbol = self.static_funcs.get(key)
        if static_symbol is not None:
            positions |= self.mut_ref.get(static_symbol, frozenset())
        return positions

    def proves_nonnull(self, expr) -> bool:
        """Whether an argument expression is provably non-null.

        The proof is syntactic, over the always-non-null sources: ``&x`` (the
        address of named storage), a string or array literal (the address of
        fresh storage), an array variable decaying to a pointer (local,
        ``@static``, or global -- all named storage), a ``@nonnull``
        parameter of the current function (the guarantee travels
        transitively), a plain pointer local flow-narrowed by a null-check
        guard (``if (p != null) { ... }`` or a diverging
        ``if (p == null) ...``), a postfix ``p!`` assertion (the
        programmer's explicit, unchecked claim), and an ``as`` cast to a
        pointer type of any of these (a pointer reinterpretation preserves
        the address; a non-pointer intermediate, e.g. an integer round-trip,
        severs the proof).

        Args:
            expr: The argument expression.

        Returns:
            ``True`` when the expression cannot evaluate to null.
        """
        if isinstance(expr, NonnullAssert):
            return True
        if isinstance(expr, (StrLit, ArrayLit)):
            return True
        if isinstance(expr, Unary) and expr.op == "&":
            return True
        if isinstance(expr, Cast):
            # Resolve the target (an alias like `type cstr = uint8*` counts),
            # so the proof threads through pointer-to-pointer casts only.
            return is_pointer(
                self.lang_type(expr.type_name, expr.line)
            ) and self.proves_nonnull(expr.value)
        if isinstance(expr, Var):
            if expr.name in self.nonnull_locals or expr.name in self.narrowed_nonnull:
                return True
            var_type = self.var_type_of(expr.name)
            return var_type is not None and is_array(var_type)
        return False

    def check_nonnull_arg(self, arg_expr, context: str, line: int):
        """Require proof that an argument to a ``@nonnull`` slot is non-null.

        Args:
            arg_expr: The argument expression.
            context: A label describing the site, for the error message.
            line: Source line for diagnostics.

        Raises:
            LangError: When the argument is the ``null`` literal or is not
                provably non-null (see :meth:`proves_nonnull`).
        """
        if isinstance(arg_expr, NullLit):
            raise LangError(
                f"cannot pass null as {context}: the parameter is @nonnull", line
            )
        if not self.proves_nonnull(arg_expr):
            raise LangError(
                f"cannot pass a possibly-null pointer as {context}: the "
                "parameter is @nonnull (pass &x, a string or array literal, "
                "an array, a @nonnull parameter, a pointer narrowed by a "
                "null check, or assert with postfix '!')",
                line,
            )

    def decays_to(self, arg_type: LangType, ptype: LangType, mut: bool) -> bool:
        """Whether a pointer argument type may decay into a hidden-ref slot.

        Pointer decay forwards a proven-non-null ``T*`` into a ``const T``
        (struct) or ``mut T`` parameter -- the slot already travels as a
        hidden reference, so the pointer value stands in for the usual
        ``&lvalue``. Exactly one level: the pointee itself must be the
        parameter's type (the pointee of a ``T**`` is ``T*``, so a double
        pointer only decays into ``const``/``mut T*``, never twice). A
        ``mut`` slot needs a mutable pointee -- the callee writes through
        it -- while a ``const`` slot also accepts a pointer to a ``const``
        pointee.

        Args:
            arg_type: The argument's type.
            ptype: The (resolved) parameter type of the ``const``/``mut``
                slot.
            mut: ``True`` for a ``mut`` slot, ``False`` for a ``const`` one.

        Returns:
            ``True`` when the argument may decay into the slot.
        """
        if arg_type is NULLT:
            return False
        bare = strip_const(arg_type)
        if bare.pointee is None:
            return False
        if mut:
            return bare.pointee == ptype
        return strip_const(bare.pointee) == strip_const(ptype)

    def check_decay_arg(
        self, arg_expr, arg_type: LangType, kind: str, ptype: LangType,
        context: str, line: int,
    ):
        """Require proof that a pointer decaying into ``const``/``mut`` is non-null.

        A decay is a two-sided promise: the callee's ``const``/``mut``
        keyword promises reference discipline, and the caller must promise
        the pointer is non-null -- the reference formed from it is never
        null by construction. The proof is the shipped ``@nonnull``
        machinery (:meth:`proves_nonnull`).

        Args:
            arg_expr: The argument expression.
            arg_type: The argument's (pointer) type, for the message.
            kind: ``"const"`` or ``"mut"``, the receiving slot's marker.
            ptype: The parameter type the pointer decays into.
            context: A label describing the site, for the error message.
            line: Source line for diagnostics.

        Raises:
            LangError: When the pointer is not provably non-null.
        """
        if not self.proves_nonnull(arg_expr):
            raise LangError(
                f"cannot pass a possibly-null {arg_type} as {context}: "
                f"decaying into a {kind} {ptype} parameter forms a reference, "
                "which is never null (narrow with a null check or assert "
                "with postfix '!')",
                line,
            )

    def hidden_ref_arg(
        self, arg_expr, ptype: LangType, line: int, context: str
    ) -> ir.Value:
        """Lower a hidden-reference (const struct) argument to a pointer.

        When the argument already has storage of the exact type, its address is
        shared directly -- no copy, which is the point of the optimization. A
        proven-non-null pointer to the parameter's type *decays*: the pointer
        value itself is forwarded as the hidden reference (see
        :meth:`decays_to`). An rvalue (or a type that still needs coercion,
        e.g. an ``extends`` upcast) is materialized into a temporary whose
        address is passed instead.

        Args:
            arg_expr: The argument expression.
            ptype: The parameter's (struct) type.
            line: Source line for diagnostics.
            context: A label for coercion error messages.

        Returns:
            A pointer to the argument's storage.
        """
        if self.is_addressable_form(arg_expr):
            addr, t, align, volatile = self.gen_addr(arg_expr, line)
            if t.ir is ptype.ir:
                return addr
            if self.decays_to(t, ptype, mut=False):
                self.check_decay_arg(
                    arg_expr, strip_const(t), "const", ptype, context, line
                )
                return self.gen_load(addr, align=align, volatile=volatile)
            tv = TypedValue(self.gen_load(addr), t)
        else:
            tv = self.gen_expr(arg_expr)
            if self.decays_to(tv.type, ptype, mut=False):
                self.check_decay_arg(
                    arg_expr, tv.type, "const", ptype, context, line
                )
                return tv.value
        return self.spill_to_temp(tv, ptype, line, context)

    def check_mut_storage(
        self, arg_expr, t: LangType, align: int | None, volatile: bool, line: int
    ):
        """Check that already-addressed storage may be lent as a ``mut`` argument.

        The legality half of :meth:`mut_ref_arg`, kept IR-free so a generic
        call can defer it until after overload resolution: ``writes_const`` is
        syntactic, and the const/volatile/alignment facts are the flags
        :meth:`gen_addr` already returned when the address was formed.

        Args:
            arg_expr: The argument expression (for the const-parameter check).
            t: The storage's type, as returned by :meth:`gen_addr`.
            align: The guaranteed alignment, as returned by :meth:`gen_addr`.
            volatile: The volatility flag, as returned by :meth:`gen_addr`.
            line: Source line for diagnostics.

        Raises:
            LangError: When the storage is read-only, is ``@volatile``, sits
                at an unguaranteed (packed) alignment, or is a ``@nonnull``
                parameter (the callee could store null through the reference).
        """
        if self.writes_const(arg_expr):
            raise LangError(
                "cannot pass a const parameter as a mut argument; it is read-only",
                line,
            )
        if isinstance(arg_expr, Var) and arg_expr.name in self.nonnull_locals:
            raise LangError(
                "cannot pass a @nonnull parameter as a mut argument; "
                "null could be stored through the reference",
                line,
            )
        if t.const:
            raise LangError(
                f"cannot pass a read-only {t} as a mut argument", line
            )
        if volatile:
            raise LangError(
                "cannot pass @volatile storage as a mut argument; accesses "
                "through the reference would not be volatile",
                line,
            )
        if align is not None and align < type_align(t):
            raise LangError(
                "cannot pass a @packed field as a mut argument; its "
                "alignment is not guaranteed",
                line,
            )
        # Lending the storage as mut lets the callee store null through the
        # reference: any flow-narrowed fact for the name dies here. The point
        # invalidation is sound because & of a mut parameter is banned and a
        # function with mut parameters cannot become a function value, so the
        # callee cannot leak the address past the call.
        if isinstance(arg_expr, Var):
            self.narrowed_nonnull.discard(arg_expr.name)

    def mut_ref_arg(
        self, arg_expr, ptype: LangType, line: int, context: str
    ) -> ir.Value:
        """Lower a ``mut`` argument to a pointer to the caller's storage.

        The argument must be the caller's own writable lvalue of exactly
        ``ptype`` -- the callee writes through the pointer, so no coercion
        (not even an adapting literal) is possible -- or a proven-non-null
        pointer to ``ptype``, which *decays*: the pointer value itself is
        forwarded (see :meth:`decays_to`). A decayed pointer may be an
        rvalue (the pointee is real storage even when the pointer expression
        is a temporary), and :meth:`check_mut_storage` does not apply to it:
        the const/volatile/packed facts describe the pointer's own storage,
        not the pointee's. The pointer is passed by value, so a
        flow-narrowed non-null fact about it survives the call (contrast the
        direct-lend invalidation in :meth:`check_mut_storage`). A string
        literal never decays -- its bytes live in a constant global, which a
        ``mut`` callee could write through.

        Args:
            arg_expr: The argument expression.
            ptype: The parameter's type.
            line: Source line for diagnostics.
            context: A label for error messages.

        Returns:
            A pointer to the argument's storage (or the decayed pointer).

        Raises:
            LangError: When the argument is not a writable lvalue of exactly
                ``ptype`` and not a provably non-null pointer to ``ptype``.
        """
        if self.is_addressable_form(arg_expr):
            addr, t, align, volatile = self.gen_addr(arg_expr, line)
            if self.decays_to(t, ptype, mut=True):
                self.check_decay_arg(
                    arg_expr, strip_const(t), "mut", ptype, context, line
                )
                return self.gen_load(addr, align=align, volatile=volatile)
            self.check_mut_storage(arg_expr, t, align, volatile, line)
            if t != ptype:
                raise LangError(
                    f"{context}: expected a {ptype} lvalue, got {t}", line
                )
            return addr
        if not isinstance(arg_expr, StrLit):
            tv = self.gen_expr(arg_expr)
            if self.decays_to(tv.type, ptype, mut=True):
                self.check_decay_arg(
                    arg_expr, tv.type, "mut", ptype, context, line
                )
                return tv.value
        raise LangError(
            f"{context} is not assignable; a mut parameter needs a "
            "variable, field, element, or dereference",
            line,
        )

    def spill_to_temp(
        self, tv: TypedValue, ptype: LangType, line: int, context: str
    ) -> ir.Value:
        """Coerce a value to ``ptype`` and store it in a fresh stack temporary.

        Used to give an rvalue (or a generic argument already lowered to a
        value) the storage a hidden-reference parameter needs.

        Args:
            tv: The argument value.
            ptype: The parameter's (struct) type.
            line: Source line for diagnostics.
            context: A label for coercion error messages.

        Returns:
            A pointer to the temporary holding the coerced value.
        """
        tv = self.coerce(tv, ptype, line, context)
        tmp = self.entry_alloca(ptype.ir)
        if over_aligned(ptype):
            tmp.align = type_align(ptype)
        self.builder.store(tv.value, tmp)
        return tmp

    @staticmethod
    def is_addressable_form(expr) -> bool:
        """Report whether an expression denotes storage (an lvalue).

        These are the forms :meth:`gen_addr` accepts: a variable, a field, an
        index, or a dereference.
        """
        return isinstance(expr, (Var, Member, Index)) or (
            isinstance(expr, Unary) and expr.op == "*"
        )

    def unify(
        self,
        pattern: TypeRef,
        actual: LangType,
        type_params: list[str],
        bindings: dict[str, LangType],
        strict: bool,
        context: str,
        line: int,
    ):
        """Match a parameter pattern against an argument type, binding params.

        For example, ``list<T>*`` against ``list<int32>*`` binds ``T =
        int32``.

        Args:
            pattern: The parameter's ``TypeRef`` pattern.
            actual: The argument's resolved type.
            type_params: The function's type-parameter names.
            bindings: The accumulating ``{name: type}`` map, updated in place.
            strict: When ``True``, two typed arguments that disagree about the
                same parameter are reported as a conflict; non-strict matches
                (untyped constants, or parameters fixed by explicit type
                arguments) never override or conflict with an existing binding.
            context: A label for the error message.
            line: Source line for diagnostics.

        Raises:
            LangError: On a strict conflict for a type parameter.
        """
        peeled = actual
        for _ in range(pattern.stars):
            if not is_pointer(peeled):
                return
            peeled = peeled.pointee
        if pattern.const:
            # A `const T` pattern infers T from the actual's underlying type: the
            # const is a qualifier on the pattern, and a mutable argument widens
            # in. So `slice<const T>` against slice<const uint8> -- or against a
            # mutable slice<uint8> -- both bind T = uint8.
            peeled = strip_const(peeled)
        if pattern.name in type_params and not pattern.args:
            bound = bindings.get(pattern.name)
            if bound is None:
                bindings[pattern.name] = peeled
            elif strict and bound != peeled:
                raise LangError(
                    f"conflicting types for type parameter {pattern.name} in {context}: "
                    f"{bound} vs {peeled}",
                    line,
                )
            return
        if (
            pattern.args
            and peeled.template == pattern.name
            and len(peeled.args) == len(pattern.args)
        ):
            for sub_pattern, sub_actual in zip(pattern.args, peeled.args):
                self.unify(
                    sub_pattern,
                    sub_actual,
                    type_params,
                    bindings,
                    strict,
                    context,
                    line,
                )

    def gen_generic_call(self, expr: Call, candidates: list[Func]) -> TypedValue:
        """Resolve and emit a call to a generic function or overload set.

        Infers type-parameter bindings from the arguments; with several
        candidates, keeps the viable ones and picks the most specific parameter
        pattern (``T*`` beats ``T``, and so on), then instantiates the chosen
        template and emits the call.

        Args:
            expr: The ``Call`` node.
            candidates: The generic overload set to dispatch over.

        Returns:
            The call's result as a ``TypedValue``.

        Raises:
            LangError: When no overload matches, the choice is ambiguous, a type
                parameter binds to ``void``, or access is denied.
        """
        # A mut argument lends the caller's storage, so its address must be
        # formed before the args are lowered to values for inference -- but
        # which overload wins (and with it which positions are mut) is not
        # known yet. Form the address wherever ANY arity-matching candidate
        # is mut and the argument denotes storage, and defer the
        # lvalue/value decision until after overload resolution.
        matching = [f for f in candidates if len(f.params) == len(expr.args)]
        maybe_mut = frozenset().union(*(self.mut_indices(f) for f in matching))
        arg_tvs, addrs = [], {}
        for i, arg in enumerate(expr.args):
            if i in maybe_mut and self.denotes_storage(arg):
                addr, t, align, volatile = self.gen_addr(arg, expr.line)
                addrs[i] = (addr, t, align, volatile)
                # Inference sees the stored value, loaded once through the
                # already-computed address. The load carries the storage's
                # flags: a @volatile lvalue is a legal argument when a
                # non-mut overload wins, and its read must stay volatile.
                # Like any loaded value, it sheds a const qualifier; the
                # storage's own const-ness is judged post-resolution.
                arg_tvs.append(
                    TypedValue(
                        self.gen_load(addr, align=align, volatile=volatile),
                        strip_const(t),
                    )
                )
            else:
                arg_tvs.append(self.gen_expr(arg))
        if len(candidates) == 1:
            func = candidates[0]
            try:
                bindings = self.resolve_bindings(func, expr, arg_tvs, lenient=False)
            except LangError:
                # Direct inference failed; a pointer argument at a const/mut
                # slot may still bind through its pointee (the decay
                # reading). Re-raise the direct error when it does not.
                bindings = self.resolve_bindings(
                    func, expr, arg_tvs, lenient=True, decay=True
                )
                if bindings is None:
                    raise
            else:
                # A direct reading is never emittable at an unaddressed mut
                # position (the parameter resolved to the argument's own
                # type, never its pointee), so when such a position holds a
                # pointer, prefer the decay reading when one exists --
                # `mut x: T` with an rvalue `int32*` reads as T = int32.
                # Likewise at any const/mut position whose pattern can only
                # be a struct (`mut self: dict<V>`): the direct reading of a
                # pointer argument can never instantiate to the pointer's
                # own type, so only the decay reading is emittable -- even
                # when a mismatched binding from another argument (an
                # untyped literal's int32 leaning) let the direct pass
                # "succeed".
                mut_positions = self.mut_indices(func)
                if any(
                    arg_tvs[i].type is not NULLT
                    and strip_const(arg_tvs[i].type).pointee is not None
                    and (
                        (i in mut_positions and i not in addrs)
                        or self.only_decay_pattern(
                            func.params[i][1], func.type_params
                        )
                    )
                    for i in self.decay_indices(func)
                ):
                    decayed_bindings = self.resolve_bindings(
                        func, expr, arg_tvs, lenient=True, decay=True
                    )
                    if decayed_bindings is not None:
                        bindings = decayed_bindings
                    else:
                        # The direct reading cannot be emitted at the
                        # triggering position, so the decay reading's
                        # failure is the call's real error (a genuine
                        # conflict, not a missing address); re-resolve
                        # strictly to raise it.
                        self.resolve_bindings(
                            func, expr, arg_tvs, lenient=False, decay=True
                        )
        else:
            # Overload set: keep the viable candidates and pick the one with
            # the most specific parameter patterns (T* beats T, and so on).
            # An rvalue argument (no address was formed) rules out any
            # candidate that is mut at its position; an lvalue rules nothing
            # out, so a same-shape mut/non-mut pair stays ambiguous.
            viable, mut_failures = [], []
            for func in candidates:
                bindings = self.resolve_bindings(func, expr, arg_tvs, lenient=True)
                if bindings is None:
                    continue
                unaddressed = [i for i in self.mut_indices(func) if i not in addrs]
                if unaddressed:
                    mut_failures.append(min(unaddressed))
                    continue
                viable.append((self.rank(func), func, bindings))
            if not viable:
                # Two-tier viability: decay readings enter resolution only
                # when no candidate matched the pointer type directly, so
                # `f(x: T*)` beside `f(mut x: T)` stays unambiguous.
                viable = self.decay_viable(candidates, expr, arg_tvs, addrs)
            if not viable:
                if len(mut_failures) == 1:
                    # Exactly one candidate matched but for an rvalue in a
                    # mut position: report that, not a generic mismatch.
                    raise LangError(
                        self.not_assignable(mut_failures[0], expr.name), expr.line
                    )
                arg_types = ", ".join(str(tv.type) for tv in arg_tvs)
                raise LangError(
                    f"no overload of {expr.name!r} matches argument types ({arg_types})",
                    expr.line,
                )
            viable.sort(key=lambda entry: entry[0], reverse=True)
            if len(viable) > 1 and viable[0][0] == viable[1][0]:
                raise LangError(
                    f"call to {expr.name!r} is ambiguous between overloads", expr.line
                )
            _, func, bindings = viable[0]
        # The winner is known; a mixed overload set warns only when resolution
        # picks a deprecated candidate.
        self.warn_deprecated(expr.name, func.deprecated_msg, expr.line)
        self.check_access(
            func.private, func.source, f"function {expr.name!r}", expr.line
        )
        for tparam, bound in bindings.items():
            if bound is VOID:
                raise LangError(
                    f"cannot bind type parameter {tparam} to {bound}", expr.line
                )
        # The winner is known: run the deferred mut legality checks against
        # the chosen signature (all IR-free -- the address and its
        # const/volatile/alignment facts were recorded when it was formed).
        # Instantiation comes first: identifying a decayed position needs
        # the resolved parameter types. A concrete winner has no instance to
        # stamp out -- its mangled ir.Function was declared up front.
        if func.type_params:
            fn, ret, params = self.instantiate(func, bindings, expr.line)
        else:
            # A mangled set member, or a mixed set's single plain concrete
            # (which kept its plain symbol).
            symbol = self.overload_symbols.get(id(func), func.name)
            fn = self.funcs[symbol]
            ret, params, _ = self.signatures[symbol]
        mut_positions = self.mut_indices(func)
        decayed: set[int] = set()
        for i in sorted(mut_positions):
            context = f"argument {i + 1} of {expr.name!r}"
            p = params[i]
            if i in addrs:
                # The caller's storage address was formed before inference;
                # the instantiated parameter type must match it exactly --
                # unless the storage holds a pointer to it, which decays.
                _, t, align, volatile = addrs[i]
                if self.decays_to(t, p, mut=True):
                    self.check_decay_arg(
                        expr.args[i], strip_const(t), "mut", p, context, expr.line
                    )
                    decayed.add(i)
                    continue
                self.check_mut_storage(expr.args[i], t, align, volatile, expr.line)
                if t != p:
                    raise LangError(
                        f"{context}: expected a {p} lvalue, got {t}", expr.line
                    )
                continue
            # No address was formed (an rvalue): only a proven-non-null
            # pointer to the parameter's type may decay in. A string
            # literal's bytes live in a constant global, so it never does.
            tv = arg_tvs[i]
            if self.decays_to(tv.type, p, mut=True) and not isinstance(
                expr.args[i], StrLit
            ):
                self.check_decay_arg(
                    expr.args[i], tv.type, "mut", p, context, expr.line
                )
                decayed.add(i)
                continue
            raise LangError(self.not_assignable(i, expr.name), expr.line)
        # The proof is syntactic, so it runs on the argument expressions even
        # though they were already lowered to values for binding inference.
        for i in self.nonnull_indices(func):
            if i < len(expr.args):
                self.check_nonnull_arg(
                    expr.args[i], f"argument {i + 1} of {expr.name!r}", expr.line
                )
        hidden = self.hidden_ref_indices(func, params)
        args = []
        for i, (tv, p) in enumerate(zip(arg_tvs, params)):
            context = f"argument {i + 1} of {expr.name!r}"
            if i in mut_positions:
                # A decayed pointer is forwarded by value (it was already
                # loaded, once, when the argument was evaluated); a direct
                # lend passes the caller's storage address.
                args.append(tv.value if i in decayed else addrs[i][0])
            elif self.str_literal_adapts(expr.args[i], p):
                # String-literal-to-slice adaptation, in parity with
                # marshal_args: the literal (or ternary of literals) borrows
                # to the parameter's char slice view; a const slice parameter
                # travels by hidden reference, so the view spills to a
                # temporary first. (The char* the literal pre-evaluated to
                # for inference goes unused.)
                tv = self.gen_borrow_slice(expr.args[i], p, expr.line)
                if i in hidden:
                    args.append(self.spill_to_temp(tv, p, expr.line, context))
                else:
                    args.append(tv.value)
            elif i in hidden:
                # The args are already lowered to values for binding
                # inference; a const hidden-reference parameter takes a
                # pointer, so a proven-non-null pointer to the parameter's
                # type decays -- forwarded as the hidden reference itself --
                # and anything else spills to a temporary (no shared-storage
                # optimization on the generic path).
                if self.decays_to(tv.type, p, mut=False):
                    self.check_decay_arg(
                        expr.args[i], strip_const(tv.type), "const", p,
                        context, expr.line,
                    )
                    args.append(tv.value)
                else:
                    args.append(self.spill_to_temp(tv, p, expr.line, context))
            else:
                args.append(self.coerce(tv, p, expr.line, context).value)
        return TypedValue(self.builder.call(fn, args), ret)

    def denotes_storage(self, arg) -> bool:
        """Whether a possibly-``mut`` generic argument's address may be formed.

        A bare name in lvalue *form* may still denote no storage -- a constant,
        or a function used as a value -- and must be evaluated as a value
        instead (:meth:`gen_addr` would reject it).

        Args:
            arg: The argument expression.

        Returns:
            ``True`` when ``arg`` is an addressable form denoting storage.
        """
        if not self.is_addressable_form(arg):
            return False
        return not (isinstance(arg, Var) and self.var_type_of(arg.name) is None)

    @staticmethod
    def not_assignable(position: int, name: str) -> str:
        """The error message for an rvalue argument in a ``mut`` position."""
        return (
            f"argument {position + 1} of {name!r} is not assignable; a mut "
            "parameter needs a variable, field, element, or dereference"
        )

    def decay_viable(
        self, candidates: list[Func], expr: Call, arg_tvs: list[TypedValue], addrs
    ) -> list:
        """Second-tier overload viability: pointer-decay readings.

        Consulted only when no candidate matched the argument types directly,
        so an exact pointer match always beats a decayed one. Each candidate
        is re-resolved with pointer arguments at ``const``/``mut`` positions
        unifying through their pointees; a candidate stays viable when every
        ``mut`` position receives either the caller's storage of the exact
        parameter type or a pointer that decays into it (string literals
        excluded -- their bytes are a constant global).

        Args:
            candidates: The generic overload set.
            expr: The ``Call`` node.
            arg_tvs: The already-evaluated argument values.
            addrs: The pre-formed ``{position: (addr, type, align, volatile)}``
                map for maybe-``mut`` lvalue arguments.

        Returns:
            ``(specificity, func, bindings)`` entries for the viable decay
            readings (possibly empty).
        """
        viable = []
        for func in candidates:
            bindings = self.resolve_bindings(
                func, expr, arg_tvs, lenient=True, decay=True
            )
            if bindings is None:
                continue
            params = self.try_param_types(func, bindings)
            if params is None:
                continue
            ok = True
            for i in self.mut_indices(func):
                if i in addrs and addrs[i][1] == params[i]:
                    continue  # a direct lend of the caller's storage
                if self.decays_to(
                    arg_tvs[i].type, params[i], mut=True
                ) and not isinstance(expr.args[i], StrLit):
                    continue
                ok = False
                break
            if ok:
                viable.append((self.rank(func), func, bindings))
        return viable

    def fill_default_bindings(self, decl, bindings: dict[str, LangType], line: int):
        """Fill still-unbound type parameters from their declared defaults.

        Resolves each unbound defaulted parameter's default ``TypeRef`` in the
        *definition's* context: the defining file becomes the current source
        (a default may name that file's ``@private`` types) and the bindings
        accumulated so far are in scope, so ``<T, U = T*>`` resolves against
        the already-bound ``T``. Parameters without a default stay unbound,
        for the caller's existing cannot-infer diagnostics.

        Args:
            decl: The generic ``Func`` or ``StructDecl`` declaring the
                defaults.
            bindings: The ``{type parameter: type}`` map, updated in place.
            line: Source line of the use site, for the backtrace note when a
                default fails to resolve.

        Raises:
            LangError: When a default's type fails to resolve; the error is
                attributed to the declaring file, with a note at the use site.
        """
        if not decl.type_param_defaults:
            return
        outer_bindings = self.type_bindings
        outer_source = self.current_source
        self.type_bindings = bindings
        self.current_source = decl.source
        tparam = None
        try:
            for tparam in decl.type_params:
                default = decl.type_param_defaults.get(tparam)
                if default is None or tparam in bindings:
                    continue
                bindings[tparam] = self.lang_type(default, decl.line)
        except LangError as err:
            # An error inside the default belongs to the declaring file; then
            # a backtrace frame names the parameter, attributed to the use
            # site (mirroring alias and instantiation frames).
            if err.source is None:
                err.source = decl.source
            err.notes.append(
                Note(
                    f"in default for type parameter {tparam} of {decl.name}",
                    line,
                    outer_source,
                )
            )
            raise
        finally:
            self.type_bindings = outer_bindings
            self.current_source = outer_source

    @staticmethod
    def only_decay_pattern(pattern, type_params: list[str]) -> bool:
        """Whether a pointer argument at this const/mut position can only decay.

        True when the parameter pattern's direct reading is never a pointer
        type: no stars, and not a bare type parameter (a bare ``T`` may bind
        the pointer type itself -- ``mut x: T`` with an ``int32*`` lvalue
        legitimately mutates the caller's pointer). ``struct dict<V>`` and
        concrete struct/scalar patterns qualify; ``T`` and any ``...*``
        pattern do not.
        """
        return pattern.stars == 0 and (
            bool(pattern.args) or pattern.name not in type_params
        )

    @staticmethod
    def decay_indices(func: Func) -> frozenset[int]:
        """Indices of ``func``'s parameters a pointer argument might decay into.

        The ``const`` and ``mut`` positions: only those travel as hidden
        references (a ``const`` scalar does not, but whether the resolved
        type is a struct is not known until after inference, so eligibility
        is judged post-resolution).
        """
        return frozenset(
            i
            for i, (name, _) in enumerate(func.params)
            if name in func.mut_params or name in func.const_params
        )

    def try_param_types(
        self, func: Func, bindings: dict[str, LangType]
    ) -> "list[LangType] | None":
        """Resolve a candidate's parameter types under trial bindings.

        Used by the decay tier of overload resolution, where per-position
        viability needs the concrete parameter types before any candidate is
        instantiated.

        Args:
            func: The candidate generic function.
            bindings: The complete trial ``{type parameter: type}`` map.

        Returns:
            The resolved parameter ``LangType``s, or ``None`` when a
            parameter type fails to resolve under these bindings.
        """
        outer_bindings = self.type_bindings
        outer_source = self.current_source
        self.type_bindings = bindings
        self.current_source = func.source
        try:
            return [self.lang_type(t, func.line) for _, t in func.params]
        except LangError:
            return None
        finally:
            self.type_bindings = outer_bindings
            self.current_source = outer_source

    def resolve_bindings(
        self,
        func: Func,
        expr: Call,
        arg_tvs: list[TypedValue],
        lenient: bool,
        decay: bool = False,
    ) -> dict[str, LangType] | None:
        """Determine the type-parameter bindings for calling a generic function.

        Inference takes typed values first, then declared type-parameter
        defaults for whatever is still unbound, then untyped constants (whose
        ``int32`` default should not win over a typed value -- or a declared
        default -- bound to the same parameter); ``null`` carries no type
        information and never participates. Explicit type arguments may omit
        a fully-defaulted tail, which fills from the defaults alone.
        Disagreement between typed arguments is a conflict unless the parameters
        were fixed explicitly (then plain coercion errors point at the bad
        argument).

        Args:
            func: The candidate generic function.
            expr: The ``Call`` node.
            arg_tvs: The already-evaluated argument values.
            lenient: When ``True`` (an overload trial), any failure returns
                ``None`` instead of raising, and argument shapes must match the
                parameter patterns.
            decay: When ``True``, a pointer argument at a ``const``/``mut``
                position unifies through its **pointee** -- the decay
                reading, tried only after the direct reading fails --
                exactly one level down (``list<int32>*`` against
                ``mut self: list<T>`` binds ``T = int32``; the pointee of a
                ``T**`` is itself a pointer, so a double pointer never sheds
                two levels).

        Returns:
            The ``{type parameter: type}`` bindings, or ``None`` when lenient and
            the candidate does not match.

        Raises:
            LangError: On a non-lenient mismatch (arity, inference, or shape).
        """
        if len(expr.args) != len(func.params):
            if lenient:
                return None
            raise LangError(
                f"{expr.name!r} expects {len(func.params)} argument(s), got {len(expr.args)}",
                expr.line,
            )
        bindings: dict[str, LangType] = {}
        if expr.type_args:
            # A shorter list is allowed only when the omitted tail is entirely
            # defaulted (defaults are trailing, so the count check suffices);
            # the tail then fills from the defaults alone, never inference.
            total = len(func.type_params)
            required = total - len(func.type_param_defaults)
            if not required <= len(expr.type_args) <= total:
                if lenient:
                    return None
                expected = (
                    f"between {required} and {total}"
                    if func.type_param_defaults
                    else f"{total}"
                )
                raise LangError(
                    f"{expr.name!r} expects {expected} type argument(s), "
                    f"got {len(expr.type_args)}",
                    expr.line,
                )
            for tparam, targ in zip(func.type_params, expr.type_args):
                bindings[tparam] = self.lang_type(targ, expr.line)
        # The decay reading: at a const/mut position, a pointer argument's
        # pointee stands in for its type, both for unification and for the
        # lenient shape filter (the loaded pointer is what would be
        # forwarded, so the pointee is what the parameter pattern sees).
        actuals = [tv.type for tv in arg_tvs]
        if decay:
            for i in self.decay_indices(func):
                bare = strip_const(actuals[i])
                if actuals[i] is not NULLT and bare.pointee is not None:
                    actuals[i] = bare.pointee
        try:
            if expr.type_args and len(expr.type_args) < len(func.type_params):
                self.fill_default_bindings(func, bindings, expr.line)
            # Typed values first (strict: two typed arguments must agree),
            # then declared defaults for whatever is still unbound, then
            # untyped constants -- which never override an existing binding,
            # so a default beats an untyped literal's int32 leaning and the
            # literal adapts to it.
            for adaptable_pass in (False, True):
                strict = not adaptable_pass and not expr.type_args
                for (_, ptype), tv, actual in zip(func.params, arg_tvs, actuals):
                    if tv.adaptable == adaptable_pass and tv.type is not NULLT:
                        self.unify(
                            ptype,
                            actual,
                            func.type_params,
                            bindings,
                            strict,
                            f"call to {expr.name!r}",
                            expr.line,
                        )
                if not adaptable_pass:
                    self.fill_default_bindings(func, bindings, expr.line)
        except LangError:
            if lenient:
                return None
            raise
        missing = [t for t in func.type_params if t not in bindings]
        if missing:
            if lenient:
                return None
            raise LangError(
                f"cannot infer type parameter(s) {', '.join(missing)} for {expr.name!r}; "
                f"specify them explicitly, e.g. {expr.name}<int32>(...)",
                expr.line,
            )
        if lenient:
            for (_, ptype), tv, actual, arg in zip(
                func.params, arg_tvs, actuals, expr.args
            ):
                if self.shape_matches(
                    ptype, actual, tv.adaptable, func.type_params, expr.line
                ):
                    continue
                if self.literal_adapts_to_pattern(
                    arg, func, ptype, bindings, expr.line
                ):
                    # A string literal evaluated to a char* for inference,
                    # but adapts to a char slice parameter at emission (the
                    # marshal_args parity arm), so the candidate stays
                    # viable.
                    continue
                return None
        return bindings

    def literal_adapts_to_pattern(
        self,
        arg,
        func: Func,
        ptype: TypeRef,
        bindings: dict[str, LangType],
        line: int,
    ) -> bool:
        """Whether a string-literal argument adapts to a candidate's parameter.

        The pre-evaluate path's parity with :meth:`marshal_args`' literal
        handling: a string literal (or a ternary of literals) stays viable
        against a parameter that resolves to a ``slice<char>`` (or
        ``slice<const char>``) under the candidate's bindings, even though
        the ``char*`` it evaluated to does not match the slice shape.
        Emission then borrows the literal (see :meth:`gen_borrow_slice`).

        Args:
            arg: The raw argument expression.
            func: The candidate function (its source scopes the resolution).
            ptype: The parameter's ``TypeRef`` pattern.
            bindings: The candidate's complete type-parameter bindings.
            line: Source line for diagnostics.

        Returns:
            ``True`` when the argument adapts to the resolved parameter.
        """
        if not isinstance(arg, (StrLit, Ternary)):
            return False
        outer_bindings = self.type_bindings
        outer_source = self.current_source
        self.type_bindings = bindings
        self.current_source = func.source
        try:
            resolved = self.lang_type(ptype, line)
        except LangError:
            return False
        finally:
            self.type_bindings = outer_bindings
            self.current_source = outer_source
        return self.str_literal_adapts(arg, resolved)

    def shape_matches(
        self,
        pattern: TypeRef,
        actual: LangType,
        adaptable: bool,
        type_params: list[str],
        line: int,
    ) -> bool:
        """Report whether an argument type structurally fits a parameter pattern.

        Used only to filter overload candidates.

        Args:
            pattern: The parameter's ``TypeRef`` pattern.
            actual: The argument's resolved type.
            adaptable: Whether the argument is an adaptable untyped constant.
            type_params: The function's type-parameter names.
            line: Source line for diagnostics.

        Returns:
            ``True`` when ``actual`` can match ``pattern``.
        """
        peeled = actual
        for _ in range(pattern.stars):
            if not is_pointer(peeled):
                return False
            peeled = peeled.pointee
            adaptable = False
        if pattern.name in type_params and not pattern.args:
            return True
        if pattern.args:
            return (
                peeled.template == pattern.name
                and len(peeled.args) == len(pattern.args)
                and all(
                    self.shape_matches(p, a, False, type_params, line)
                    for p, a in zip(pattern.args, peeled.args)
                )
            )
        try:
            resolved = self.lang_type(TypeRef(pattern.name), line)
        except LangError:
            return False
        if peeled == resolved:
            return True
        if adaptable and is_integer(resolved) and is_integer(peeled):
            return True
        return resolved == RAWPTR and is_pointer(peeled)

    def rank(self, func: Func) -> tuple[bool, int]:
        """An overload candidate's sort key: (is-concrete, specificity).

        The leading tier makes "a fully concrete signature is maximally
        specific" exactly true: without it, a generic whose effective
        parameter list is all-concrete (its type parameter appearing only in
        the return type, or filled by a declared default) would tie an
        identical concrete overload under the pattern-specificity score.
        Same-tier candidates fall back to :meth:`specificity`, and an equal
        rank stays the ambiguity error.

        Args:
            func: The candidate function.

        Returns:
            The sort key, greater meaning preferred.
        """
        return (not func.type_params, self.specificity(func))

    def specificity(self, func: Func) -> int:
        """Rank an overload by how specific its parameter patterns are.

        Concrete types beat structured patterns, which beat bare type
        parameters; pointer depth adds specificity.

        Args:
            func: The candidate function.

        Returns:
            The summed specificity score across the parameters.
        """

        def score(pattern: TypeRef) -> int:
            """Score one parameter pattern's specificity."""
            value = pattern.stars
            if pattern.args:
                value += 4 + sum(score(a) for a in pattern.args)
            elif pattern.name not in func.type_params:
                value += 8
            return value

        return sum(score(p) for _, p in func.params)

    def instantiate(
        self, func: Func, bindings: dict[str, LangType], line: int
    ) -> tuple[ir.Function, LangType, list[LangType]]:
        """Return the monomorphized instance of a generic function.

        Generates (and caches) the instance for ``bindings`` on first use,
        registering it before emitting the body so recursive calls resolve. The
        instance gets mergeable linkage, like an imported definition. An error
        inside the body gains an ``in instantiation of ...`` backtrace note
        naming this request site; a cached instance skips capture, so an error
        chain always reports the first triggering path (as C++/Rust do).

        Args:
            func: The generic function template.
            bindings: The type-parameter bindings to instantiate with.
            line: Source line of the instantiation site (the call), for the
                backtrace note when the body fails to compile.

        Returns:
            A ``(function, return type, param types)`` tuple for the instance.
        """
        key = (id(func), tuple(str(bindings[t]) for t in func.type_params))
        if key in self.instances:
            mangled = self.instances[key]
            ret, params, _ = self.signatures[mangled]
            return self.funcs[mangled], ret, params
        base = self.template_bases.get(id(func)) or self.symbol_bases.get(
            (func.source, func.name), func.name
        )
        mangled = f"{base}<{', '.join(str(bindings[t]) for t in func.type_params)}>"
        outer_bindings = self.type_bindings
        outer_source = self.current_source
        saved = (
            self.builder,
            self.locals,
            self.ret_type,
            self.loops,
            self.current_variadic,
            self.scope_names,
            self.defer_stack,
            self.block_exprs,
            self.const_locals,
            self.mut_locals,
            self.nonnull_locals,
            self.narrowed_nonnull,
            self.addr_taken,
        )
        self.type_bindings = bindings
        self.current_source = func.source  # the signature may name private structs
        try:
            ret = self.lang_type(func.ret_type, func.line)
            params = [self.lang_type(t, func.line) for _, t in func.params]
            hidden = self.hidden_ref_indices(func, params)
            fnty = ir.FunctionType(ret.ir, self.param_irs(params, hidden))
            fn = ir.Function(self.module, fnty, name=mangled)
            # A generic instance is emitted in every object that uses it, so it
            # merges like an imported definition rather than colliding.
            self.link_shared(fn, func.source)
            self.mark_inline(fn, func)
            self.mark_noalias(fn, func, params)
            self.mark_nonnull(fn, func, params)
            # Register before generating the body so recursive calls resolve
            # (a failed instantiation therefore stays memoized -- harmless
            # while compilation aborts on the first error).
            self.funcs[mangled] = fn
            self.signatures[mangled] = (ret, params, False)
            self.hidden_ref[mangled] = hidden
            self.mut_ref[mangled] = self.mut_indices(func)
            self.nonnull_ref[mangled] = self.nonnull_indices(func)
            self.instances[key] = mangled
            self.gen_function(func, fn, ret, params)
        except LangError as err:
            # An error inside the body belongs to the template's file, not the
            # caller's -- attribute it here, where current_source still points at
            # the instance being generated (the top level otherwise blames the
            # root module for a line in an imported library). Then record the
            # instantiation frame against the requesting file; unwinding
            # appends nested frames innermost first.
            if err.source is None:
                err.source = self.current_source
            err.notes.append(
                Note(f"in instantiation of {mangled}", line, outer_source)
            )
            raise
        finally:
            (
                self.builder,
                self.locals,
                self.ret_type,
                self.loops,
                self.current_variadic,
                self.scope_names,
                self.defer_stack,
                self.block_exprs,
                self.const_locals,
                self.mut_locals,
                self.nonnull_locals,
                self.narrowed_nonnull,
                self.addr_taken,
            ) = saved
            self.type_bindings = outer_bindings
            self.current_source = outer_source
        return fn, ret, params

    def gen_binary(self, expr: Binary) -> TypedValue:
        """Emit a binary arithmetic, bitwise, shift, or comparison operation.

        Adapts an untyped constant operand to the other side's type, then emits
        the signedness-correct instruction (e.g. ``sdiv``/``udiv``, signed vs
        unsigned compares) for integers, pointers, or ``float64``.

        Args:
            expr: The ``Binary`` node.

        Returns:
            The result as a ``TypedValue``.

        Raises:
            LangError: On operands of mismatched or unsupported types for the
                operator.
        """
        lhs = self.gen_expr(expr.lhs)
        rhs = self.gen_expr(expr.rhs)
        return self.apply_binary(lhs, rhs, expr.op, expr.line)

    def apply_binary(
        self, lhs: TypedValue, rhs: TypedValue, op: str, line: int
    ) -> TypedValue:
        """Combine two evaluated operands with a binary operator.

        The operand-unification and instruction-selection core of
        :meth:`gen_binary`, factored out so a compound assignment
        (``target op= value``) can reuse it with a freshly loaded left operand.

        Args:
            lhs: The left operand, already evaluated.
            rhs: The right operand, already evaluated.
            op: The operator token (e.g. ``"+"``, ``"<<"``, ``"=="``).
            line: Source line for diagnostics.

        Returns:
            The result as a ``TypedValue``.

        Raises:
            LangError: On operands of mismatched or unsupported types for the
                operator.
        """
        if lhs.type != rhs.type:
            ctx = f"operand of {op!r}"
            both_int = is_integer(lhs.type) and is_integer(rhs.type)
            # An untyped constant operand adapts to the other side's type.
            if rhs.adaptable and not lhs.adaptable:
                rhs = self.coerce(rhs, lhs.type, line, ctx)
            elif lhs.adaptable and not rhs.adaptable:
                lhs = self.coerce(lhs, rhs.type, line, ctx)
            elif lhs.adaptable and rhs.adaptable and both_int:
                # Two untyped constants: widen to the larger, staying adaptable
                # (so neither narrows and the result can still fold/adapt).
                wide = wider_int_type(lhs.type, rhs.type)
                lhs, rhs = widen_to(lhs, wide), widen_to(rhs, wide)
            elif both_int and lhs.type.signed == rhs.type.signed:
                # Two typed integers of the same signedness: widen the narrower
                # to the wider; the result is the wider type. This applies only
                # within an expression -- crossing into a typed slot (assignment,
                # return, argument) still needs an explicit cast. (Mixed
                # signedness falls through to the type error below.)
                wide = wider_int_type(lhs.type, rhs.type)
                lhs = self.widen_operand(lhs, wide, line, ctx)
                rhs = self.widen_operand(rhs, wide, line, ctx)
            else:
                lhs = self.coerce(lhs, rhs.type, line, ctx)
        op_type = lhs.type
        if op in COMPARISON_OPS:
            if is_pointer(op_type) or is_function(op_type):
                if op not in ("==", "!="):
                    raise LangError(
                        f"operator {op!r} not supported for {op_type}", line
                    )
                return TypedValue(
                    self.builder.icmp_unsigned(op, lhs.value, rhs.value), BOOL
                )
            if isinstance(op_type.ir, ir.IntType):
                icmp = (
                    self.builder.icmp_signed
                    if op_type.signed
                    else self.builder.icmp_unsigned
                )
                return TypedValue(icmp(op, lhs.value, rhs.value), BOOL)
            if op_type is FLOAT64:
                return TypedValue(
                    self.builder.fcmp_ordered(op, lhs.value, rhs.value), BOOL
                )
        elif is_integer(op_type):
            # Fold constant operands so expressions like 10 * sizeof(int64)
            # remain constants (and can still adapt to other integer types).
            if isinstance(lhs.value, ir.Constant) and isinstance(
                rhs.value, ir.Constant
            ):
                if op == "<<" and lhs.adaptable:
                    widened = fold_untyped_shift(lhs.value.constant, rhs.value.constant)
                    if widened is not None:
                        return widened
                folded = fold_int_arithmetic(
                    op, lhs.value.constant, rhs.value.constant, op_type
                )
                if folded is not None:
                    return TypedValue(
                        ir.Constant(op_type.ir, folded),
                        op_type,
                        adaptable=lhs.adaptable and rhs.adaptable,
                    )
            ops = {
                "+": self.builder.add,
                "-": self.builder.sub,
                "*": self.builder.mul,
                "/": self.builder.sdiv if op_type.signed else self.builder.udiv,
                "%": self.builder.srem if op_type.signed else self.builder.urem,
                "&": self.builder.and_,
                "|": self.builder.or_,
                "^": self.builder.xor,
                "<<": self.builder.shl,
                ">>": self.builder.ashr if op_type.signed else self.builder.lshr,
            }
            return TypedValue(ops[op](lhs.value, rhs.value), op_type)
        elif op_type is FLOAT64 and op != "%":
            ops = {
                "+": self.builder.fadd,
                "-": self.builder.fsub,
                "*": self.builder.fmul,
                "/": self.builder.fdiv,
            }
            return TypedValue(ops[op](lhs.value, rhs.value), op_type)
        raise LangError(f"operator {op!r} not supported for {op_type}", line)
