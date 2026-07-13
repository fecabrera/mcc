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
import struct
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from dataclasses import fields as dataclass_fields
from dataclasses import is_dataclass
from dataclasses import replace as dataclasses_replace

from llvmlite import ir

from mcc.errors import WARNING_CLASSES, LangError, Note
from mcc.parser import type_ref_names
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
    Coalesce,
    CompoundAssign,
    Conditional,
    Const,
    Continue,
    Defer,
    Emit,
    EnumAccess,
    EnumDecl,
    ErrorDirective,
    ErrorName,
    Except,
    ExprStmt,
    FloatLit,
    For,
    FStrLit,
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
    ResultLit,
    Return,
    SizeOf,
    Slice,
    StaticAssert,
    StoreCall,
    StoreDeref,
    StoreIndex,
    StoreMember,
    StrLit,
    StructDecl,
    StructLit,
    Ternary,
    Try,
    TryFallback,
    TryStmt,
    TupleLit,
    TypeAlias,
    TypeName,
    TypeRef,
    Unary,
    UnionDecl,
    Unreachable,
    Var,
    While,
)

from mcc.codegen.abi import Direct, Indirect, abi_supported, classify_signature
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
    INT64,
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
    PENDING_RESULT,
    ResultPending,
    TypedValue,
    adaptable_int,
    const_of,
    field_offset,
    fnv1a64,
    fold_int_arithmetic,
    fold_untyped_shift,
    function_type,
    is_any,
    is_aggregate,
    is_array,
    is_error_decl,
    is_flexible_array,
    is_function,
    is_integer,
    is_pointer,
    is_result,
    is_slice,
    is_struct,
    is_tuple,
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


@dataclass
class ExternABI:
    """The C-ABI marshalling plan for one struct-passing ``@extern`` function.

    Built by :meth:`CodeGen.declare_extern_abi` when a declaration's signature
    carries a by-value aggregate, and consulted at the call site. Each entry of
    ``args`` is the classification of the same-indexed fixed parameter -- a
    :class:`~mcc.codegen.abi.Direct`/:class:`~mcc.codegen.abi.Indirect` for an
    aggregate, or ``None`` for a scalar/pointer that passes unchanged.

    Attributes:
        args: Per fixed parameter, its classification or ``None``.
        ret: The return classification, or ``None`` for a scalar/void return.
    """

    args: list
    ret: object = None


# The placeholder type of a constructor-sugar receiver while the family
# resolves: `point(1, 2)` on a generic struct spells no instantiation, so the
# receiver enters overload resolution as this sentinel -- it binds no type
# parameter and matches any receiver pattern -- and the winner's first
# parameter then fixes the constructed type (see gen_generic_call). The name
# renders in a no-overload signature, e.g. `point::constructor(<self>, bool)`.
CTOR_SELF = LangType("<self>", ir.IntType(8), signed=False)


@dataclass
class _CtorSelf:
    """The receiver slot of ``S(args)`` constructor sugar, not yet typed.

    Stands as argument 0 of the desugared ``S::constructor`` call when the
    written head is a bare generic (``point(1, 2)``): the instantiation is
    whatever the family's overload resolution deduces from the remaining
    arguments (and declared defaults), so the slot cannot be allocated up
    front. ``gen_generic_call`` materializes it once the winner is known and
    records it here for the caller (``gen_ctor_call``).

    Attributes:
        struct_name: The canonical type name being constructed.
        line: The call's source line, for diagnostics.
        slot: The materialized alloca, filled in by ``gen_generic_call``.
        type: The constructed ``LangType``, filled in with ``slot``.
    """

    struct_name: str
    line: int
    slot: object = None
    type: "LangType | None" = None


@dataclass
class _InheritedOrigin:
    """A resolution-only inherited-method clone's link back to its origin.

    Method inheritance enters a base family member into a derived family's
    candidate set as a CLONE -- name, receiver, parameters, and return
    rebased at the declared base instantiation -- but the clone is never
    emitted: once resolution picks it, the ORIGIN template is instantiated
    (sharing its instance cache and symbol) and the derived receiver coerces
    to the base parameter at the call boundary. This record, keyed by
    ``id(clone)`` in ``inherited_origins``, carries what that hand-off needs.

    Attributes:
        origin: The base family member the clone stands for.
        seed: ``{origin type parameter: TypeRef}`` -- the bindings the
            ``extends`` clause fixes (``T -> float64`` for ``pointf extends
            point<float64>``; the ref may name the DERIVED struct's own
            parameters when the derivation is generic).
        rename: ``{origin type parameter: clone type parameter}`` for the
            origin's leftover (method-own) parameters, renamed when they
            collide with a derived struct parameter.
        hop: Distance up the ``extends`` chain (1 = immediate base). Ranks
            below the tier and above pattern specificity, so a derived
            same-shape member shadows an inherited one.
        base_label: The base instantiation's spelling for diagnostics
            (``"point<float64>"``).
        source: The deriving struct's file, where the ``extends`` clause's
            type references resolve.
    """

    origin: Func
    seed: dict[str, TypeRef]
    rename: dict[str, str]
    hop: int
    base_label: str
    source: str | None


# The f-string sink rule: an interpolated literal may stand only where an
# @format callee's format string receives it (marshal_args and the set-path
# winner emission substitute the desugared text and splice the holes there).
# Every funnel a plain StrLit lowers through raises this instead, so a
# misplaced f-string can never silently drop its holes -- an FStrLit *is* a
# StrLit, and the isinstance predicates deliberately keep matching it.
FSTRING_MISPLACED = (
    "an f-string is only allowed as the format string of an @format call "
    "like 'println' or 'format_args'"
)


def borrows_array_literal(expr) -> bool:
    """Whether an expression is a non-empty array literal, ternary arms included.

    The syntactic side of the ``return [...] as slice<T>`` rejection: a
    literal's hidden backing array lives in the returning frame, so a view
    over it would dangle. A ternary borrows arm by arm, so any literal arm
    dangles the same way. The *empty* literal is exempt -- ``[] as slice<T>``
    is a ``{ null, 0 }`` view with no backing storage at all.

    Args:
        expr: The cast operand to inspect.

    Returns:
        ``True`` when ``expr`` is (or any ternary arm of it is) a non-empty
        array literal.
    """
    if isinstance(expr, Ternary):
        return borrows_array_literal(expr.then) or borrows_array_literal(
            expr.otherwise
        )
    return isinstance(expr, ArrayLit) and bool(expr.elements)


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


def contains_call(obj) -> bool:
    """Whether an expression subtree may emit a runtime call or store.

    Recurses like :func:`collect_addr_taken`. Used when a guard chain's
    facts are collected for a branch: an eligible bare local cannot be
    written by a call, but a narrowed *projection* lives in memory, so a
    later-evaluated operand that can call (``Call``/``CallExpr``/``@asm``)
    or run arbitrary statements (a block expression) may null the field
    after its null test and before the branch -- the earlier operand's
    path facts must not survive such an operand. ``sizeof``-style builtins
    are their own node types and emit nothing, so they do not count.

    Args:
        obj: An AST node, list, or scalar to scan.

    Returns:
        ``True`` when the subtree may call or store at runtime.
    """
    if isinstance(obj, (Call, CallExpr, Asm, BlockExpr)):
        return True
    if is_dataclass(obj):
        return any(contains_call(getattr(obj, f.name)) for f in dataclass_fields(obj))
    if isinstance(obj, (list, tuple)):
        return any(contains_call(item) for item in obj)
    return False


def _fork_defer_stack(stack: list) -> list:
    """Deep-copy a defer stack's per-scope lists (the scopes stay live)."""
    return [list(scope) for scope in stack]


@dataclass
class GenContext:
    """The per-function compilation context of a :class:`CodeGen`.

    Every attribute the generator mutates while compiling one function body
    is enumerated here, in one place, shared by its two consumers --
    :meth:`CodeGen.instantiate`'s save/restore around a monomorphized body
    and the pending generic-arm snapshot (:class:`PendingArm`) -- so a
    future fact set added to the generator cannot silently miss either.

    A field whose ``fork`` metadata carries a copier is a mutable container
    that :meth:`fork` detaches (so a snapshot is immune to later in-place
    mutation); a field without one is a value or an object shared by
    reference on purpose (the builder is re-pointed by the consumer).
    """

    builder: "ir.IRBuilder | None"
    locals: dict = dataclass_field(metadata={"fork": dict})
    ret_type: LangType = dataclass_field(metadata={})
    ret_mut: bool = dataclass_field(metadata={})
    formation_params: dict = dataclass_field(metadata={"fork": dict})
    loops: list = dataclass_field(metadata={"fork": list})
    current_variadic: bool = dataclass_field(metadata={})
    current_noreturn: "str | None" = dataclass_field(metadata={})
    scope_names: set = dataclass_field(metadata={"fork": set})
    defer_stack: list = dataclass_field(metadata={"fork": _fork_defer_stack})
    defer_marks: list = dataclass_field(metadata={"fork": list})
    block_exprs: list = dataclass_field(metadata={"fork": list})
    const_locals: set = dataclass_field(metadata={"fork": set})
    mut_locals: set = dataclass_field(metadata={"fork": set})
    nonnull_locals: set = dataclass_field(metadata={"fork": set})
    narrowed_nonnull: set = dataclass_field(metadata={"fork": set})
    narrowed_paths: set = dataclass_field(metadata={"fork": set})
    addr_taken: set = dataclass_field(metadata={"fork": set})
    type_bindings: dict = dataclass_field(metadata={"fork": dict})
    current_source: "str | None" = dataclass_field(metadata={})

    @classmethod
    def capture(cls, cg: "CodeGen") -> "GenContext":
        """Snapshot every context attribute off ``cg``, by reference."""
        return cls(**{f.name: getattr(cg, f.name) for f in dataclass_fields(cls)})

    def restore(self, cg: "CodeGen") -> None:
        """Write every captured attribute back onto ``cg``."""
        for f in dataclass_fields(self):
            setattr(cg, f.name, getattr(self, f.name))

    def fork(self) -> "GenContext":
        """A copy whose mutable containers are detached from the originals.

        ``capture`` alone shares containers with the live generator, which is
        fine for an immediate save/restore pair but not for a snapshot
        consumed later (the enclosing function keeps compiling and mutates
        them in place). Each fork also detaches from previous forks, so one
        snapshot can seed many independent restorations (one per boxed tag).
        """
        return dataclasses_replace(
            self,
            **{
                f.name: f.metadata["fork"](getattr(self, f.name))
                for f in dataclass_fields(self)
                if "fork" in f.metadata
            },
        )


@dataclass
class PendingArm:
    """A generic ``case type`` arm awaiting per-tag monomorphization.

    A ``when T* ptr:``/``when T v:`` arm cannot lower where it appears --
    the tags it matches are the whole program's boxed set, which grows as
    long as bodies are being generated. Initial lowering leaves an LLVM
    ``switch`` (defaulting to the next arm) at the arm's chain position and
    enqueues this record; :meth:`CodeGen.finalize_generic_arms` later
    compiles one body copy per matching boxed tag into fresh blocks of
    ``fn`` and adds each as a switch case.

    The record is shaped as a tag predicate (``pointer_only``) plus a body
    strategy (the per-tag monomorphized ``body``), so a future interface
    arm -- one body over a fat pointer, no per-tag copies -- can ride the
    same pending/finalize machinery with a different pair.

    Attributes:
        switch: The dispatch instruction cases are added to.
        fn: The enclosing LLVM function (late blocks append here).
        payload_ptr: The subject's payload slot, computed at case entry (it
            dominates every switch case).
        end_bb: The case's join block; a non-diverging body copy branches
            to it.
        param: The arm-scoped type parameter's name (the ``T``).
        binding: The value binding's name (the ``v``/``ptr``).
        body: The arm body's statement list (shared AST, one compile per
            tag).
        when_line: The arm's source line, for the per-tag failure note.
        pointer_only: The tag predicate -- ``True`` for ``T*`` (pointer
            tags only, ``T`` bound to the pointee), ``False`` for ``T v``
            (every tag, ``T`` bound to the boxed type itself).
        claimed: Tags this arm must not take: the case's ``seen`` set,
            shared with its concrete arms and any earlier generic arm
            (first-match-wins textual order); tags emitted here are added
            back into it.
        ctx: The per-function context snapshot at the arm's chain position
            (see :meth:`GenContext.fork`); each tag's compile restores a
            fresh fork of it.
        label: What the per-tag failure note calls the arm -- ``case type
            arm`` as written, ``with pattern`` for a desugared ``with``.
    """

    switch: ir.Instruction
    fn: ir.Function
    payload_ptr: ir.Value
    end_bb: ir.Block
    param: str
    binding: str
    body: list
    when_line: int
    pointer_only: bool
    claimed: set
    ctx: GenContext
    label: str = "case type arm"


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
        error_classes: frozenset[str] = frozenset(),
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
            error_classes: The opt-in warning classes promoted to error level
                for this build (global ``-Werror`` over the enabled classes,
                plus any ``-Werror=<class>``). A member here reads as its
                *strict* posture, which two codegen facts key off before any
                warning is printed: an ``@extern`` ``@nonnull`` declaration
                re-emits the LLVM ``nonnull``/``dereferenceable`` hint (sound
                only under unconditional caller proof), and a possibly-null
                argument to such a slot is a hard error rather than an accepted
                or warned one (see :meth:`check_nonnull_arg`).
        """
        self.program = program
        # Warning classes promoted to error level (the strict posture); see
        # the constructor doc. Consulted by mark_nonnull and check_nonnull_arg.
        self.error_classes = error_classes
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
        # Sequence for hidden locals desugarings mint (e.g. the constructor
        # sugar's receiver); the names start with a digit, so they can never
        # collide with a lexable identifier.
        self.hidden_seq = 0
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
        # Symbols whose last fixed parameter is @format: a string literal
        # bound to it is scanned at the call site, desugaring positional
        # `{n}` placeholders into the sequential runtime form (see
        # :meth:`scan_positional`). Direct-call registrations only; the
        # overload/generic path reads the winner's ``format_params`` itself.
        self.format_syms: set[str] = set()
        # Symbols declared `-> mut T`: the LLVM return type is a pointer to
        # the returned storage, and a call to one is an lvalue expression
        # (assignable, projectable, re-lendable as a mut argument). Fed by
        # the same four registration sites as mut_ref: @static, overload-set
        # member, plain concrete, and generic instance.
        self.mut_ret: set[str] = set()
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
        # Pointer-typed field projections flow-narrowed to non-null by a
        # null-check guard, keyed by their access path -- ('a', 'b', 'ptr')
        # for a->b->ptr (arrow-insensitively; see nonnull_path_of). Unlike a
        # name fact, the pointee field lives in memory a callee or any
        # through-memory store could reach, so every fact here dies at each
        # call emission and at each *p/element/field store (blanket kills),
        # while a direct write to the base variable prefix-kills just its
        # own paths (see kill_paths_rooted).
        self.narrowed_paths: set[tuple[str, ...]] = set()
        # Names whose address is taken (&x) anywhere in the current function's
        # body -- such locals are never narrowable (a stored pointer could
        # null them without naming them).
        self.addr_taken: set[str] = set()
        # Generic functions: a name maps to its overload set, distinguished
        # by parameter patterns (e.g. hash<T>(T) vs hash<T>(T*)).
        self.templates: dict[str, list[Func]] = {}
        self.template_bases: dict[int, str] = {}  # id(Func) -> mangle base
        # Closed type groups (`fn f<T: int64 | int32>`), resolved once at the
        # template's declaration: id(Func) -> {type parameter: member types}.
        # group_templates lists every grouped template (@static ones included)
        # for the eager end-of-codegen member check.
        self.group_types: dict[int, dict[str, list[LangType]]] = {}
        self.group_templates: list[Func] = []
        # Nominal `extends` bounds (`fn f<T extends shape>`), resolved once at
        # the template's declaration: id(Func) -> {type parameter: bound
        # struct}. Unlike a closed group, the satisfying set is open-ended, so
        # there is no eager enumeration -- the bound is checked lazily against
        # each deduced binding at the call/instantiation sites.
        self.bound_types: dict[int, dict[str, LangType]] = {}
        # (id(template Func), bound types) -> mangled instance name
        self.instances: dict[tuple[int, tuple[str, ...]], str] = {}
        self.struct_templates: dict[str, "StructDecl | UnionDecl"] = {}
        self.struct_types: dict[str, LangType] = {}  # mangled name -> instance
        # Enums: name -> EnumType. @static enums are file-scoped, keyed by
        # (source, name) so other files may reuse the name (like @static structs).
        self.enums: dict[str, EnumType] = {}
        self.static_enums: dict[tuple[str | None, str], EnumType] = {}
        # Declared error types keyed by their (possibly salted) nominal type
        # name, so an error value's `LangType` maps back to its `EnumType` --
        # the source for the `error_name`/`error_message` accessor tables.
        self.error_types: dict[str, EnumType] = {}
        # Cache of synthesized `(type name, display) -> ir.Function` accessors,
        # so each error declaration's name/message switch is built at most once.
        self.error_accessors: dict[tuple[str, bool], ir.Function] = {}
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
        self.static_structs: dict[tuple[str | None, str], "StructDecl | UnionDecl"] = {}
        self.symbol_bases: dict[
            tuple[str | None, str], str
        ] = {}  # static name mangling
        self.used_symbols: set[str] = set()
        # @extern declarations refer to symbols defined elsewhere; identical
        # redeclarations across files collapse onto the first one.
        self.extern_decls: set[str] = set()
        # The C-ABI call plan (an ExternABI) for each @extern that passes or
        # returns a struct by value across the C boundary -- absent for an
        # @extern whose signature is all scalars/pointers, which needs no
        # marshalling. Built once when the declaration is first seen (see
        # declare_extern_abi), consulted at the call site (gen_direct_call).
        self.extern_abi: dict[str, ExternABI] = {}
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
        # Method inheritance: a derived struct's family call merges its own
        # members with resolution-only CLONES of its base chain's members,
        # rebased at the declared base instantiation (see
        # inherited_candidates). Clone lists are cached per (source, family);
        # each clone maps back to its origin template through
        # inherited_origins -- emission always instantiates the ORIGIN (one
        # shared instance cache and symbol), coercing the receiver at the
        # call boundary.
        self.inherited_sets: dict[tuple[str | None, str], list[Func]] = {}
        self.inherited_origins: dict[int, _InheritedOrigin] = {}
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
        # The boxed-only tag registry: tag -> LangType for every type a value
        # actually boxes under somewhere in the program (any_tags above also
        # records mere `case type` arm mentions, so it cannot serve). Generic
        # case-type arms monomorphize over exactly this set; it grows for as
        # long as bodies are generated, hence the finalize fixpoint below.
        self.boxed_types: dict[int, LangType] = {}
        # Generic case-type arms awaiting per-tag monomorphization, in
        # creation order (textual order within one case, so an earlier arm
        # claims a tag first). Drained to fixpoint by finalize_generic_arms.
        self.pending_arms: list[PendingArm] = []
        # name -> (private, source file); for @private access checks
        self.func_privacy: dict[str, tuple[bool, str | None]] = {}
        # @deprecated("msg") on concrete functions: resolved symbol -> the
        # migration message, warned at every call site and function-value use.
        # (Generic templates carry the message on the Func node instead and
        # are checked when overload resolution picks them.)
        self.deprecated_syms: dict[str, str] = {}
        # True while emitting the body of a function that is itself
        # @deprecated: a deprecated function may delegate to other deprecated
        # functions (a deprecation shim among the deprecated cluster) without
        # each such call re-warning. Only the enclosing-function-is-deprecated
        # case is exempt; a live function calling a deprecated one still warns.
        self.in_deprecated_body = False
        # Per-function transitive write-effect bits, keyed by id(Func) for
        # every function with a body (a generic template takes ONE bit for
        # all its instances -- the candidate union); True means the function
        # may write memory a caller's projection fact could live in. Computed
        # once by analyze_write_effects, before any body is generated.
        self.effect_bits: dict[int, bool] = {}
        # The concrete symbols (generic instances included, added as they are
        # stamped out) whose write-effect bit is clear: a call to one
        # preserves projection facts instead of the blanket kill -- the same
        # symbol-set pattern as noreturn_syms below.
        self.fact_safe_syms: set[str] = set()
        # @noreturn functions, by resolved symbol: a direct call to one
        # terminates the caller's block (an `unreachable` right after the
        # call), so no dummy return is needed past it. Function-pointer
        # calls are deliberately absent -- the plain fn() type drops the
        # flag, so an indirect call is assumed to return. (Generic templates
        # carry the flag on the Func node instead, checked at resolution.)
        self.noreturn_syms: set[str] = set()
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
        # Whether the function being generated returns mut (`-> mut T`): its
        # `return` statements hand back an address under the formation rule
        # instead of a value. Set per body by gen_function, like ret_type.
        self.ret_mut: bool = False
        # The current function's plain (non-mut, non-const) parameters:
        # name -> whether the parameter's type is a pointer. The mut-return
        # formation walk consults this to tell a pointer parameter (a legal
        # root behind at least one hop) from a by-value parameter or local
        # (this call's frame, never a legal root). A shadowing let drops the
        # name (see bind_local).
        self.formation_params: dict[str, bool] = {}
        # The enclosing @noreturn function's name, or None: set per body by
        # gen_function, so the Return arm can reject `return` inside one.
        self.current_noreturn: str | None = None
        # Enclosing loops, innermost last: (continue target, break target,
        # defer depth). The break target is None for a folded forever-loop,
        # which by construction no break can reach.
        self.loops: list[tuple[ir.Block, ir.Block | None, int]] = []
        # Enclosing block-expressions, innermost last; each `emit` targets the
        # last. See BlockExprCtx.
        self.block_exprs: list[BlockExprCtx] = []
        # Defer bodies being generated, innermost last: (len(loops),
        # len(block_exprs)) at entry. A break/continue/emit inside a defer
        # body may only target a loop / block expression opened *inside* the
        # body (a return never may): control that jumps out of a defer body
        # would re-unwind the very scope whose defers are running. Compared
        # against at the jump statements themselves.
        self.defer_marks: list[tuple[int, int]] = []
        self.str_count = 0
        # One rodata global per distinct string contents; identical literals
        # (source strings and `typename` results alike) share bytes.
        self.string_globals: dict[str, ir.GlobalVariable] = {}
        # Anonymous rodata globals backing `@static` slice-of-array-literal
        # views (`.arr.N`), counted separately from the string pool.
        self.arr_count = 0

    def warn(self, message: str, line: int, wclass: str | None = None) -> None:
        """Record a non-fatal diagnostic on the warning channel.

        The warning is stamped with :attr:`current_source` at emission time
        (unlike errors, whose source is filled in as they unwind), collected on
        :attr:`warnings` in emission order, and never aborts generation. The
        driver prints the list as ``file: warning: line N: message`` lines
        after generation succeeds; warnings collected before a hard compile
        error are dropped with the failed build.

        Collection is unconditional even for a tagged warning: codegen never
        sees ``-W`` flags, so the list embedders read keeps every emission,
        and the driver filters disabled classes at print time.

        Args:
            message: The diagnostic text, reported verbatim.
            line: The 1-based source line the warning refers to.
            wclass: The opt-in warning class this diagnostic belongs to, or
                ``None`` (the default) for an unconditional warning that
                always prints.
        """
        assert wclass is None or wclass in WARNING_CLASSES, (
            f"unregistered warning class {wclass!r}"
        )
        self.warnings.append(Note(message, line, self.current_source, wclass))

    def warn_unchecked_deref(self, base_expr, line: int) -> None:
        """Warn under ``-Wunchecked-dereference`` when a dereference is unproven.

        The single formatter for the class: every dereference address
        formation (``*p`` as a load or a store target, ``p->field``, and the
        raw-pointer case of ``p[i]``) funnels through here with the pointer
        expression being dereferenced. The question asked is exactly the
        ``@nonnull`` proof relation (:meth:`proves_nonnull`) -- no new
        analysis, reporting instead of rejecting -- so a flow-narrowed local
        or projection, an always-non-null source, a decayed array, or a
        postfix ``!`` assertion silences the site. Slice indexing never
        reaches here (a slice's data pointer is the borrow's invariant), and
        the class never changes the code generated.

        Args:
            base_expr: The pointer expression being dereferenced (the ``*``
                operand, the ``->`` base, or the indexed base).
            line: The dereference's 1-based source line.
        """
        if not self.proves_nonnull(base_expr):
            self.warn(
                "dereference of a possibly-null pointer (narrow it with a "
                "null check or assert with postfix '!')",
                line, wclass="unchecked-dereference",
            )

    def warn_dead_code(self, prev, stmt) -> None:
        """Warn under ``-Wdead-code`` that ``stmt`` begins an unreachable region.

        The single formatter for the class: both statement-walking loops (a
        block's, and the statement-level ``@if`` live arm's) funnel through
        here when they find the builder's block already terminated, with
        ``prev`` the statement whose generation terminated it. Emitted once
        per dead region -- at its first statement -- after which the walk
        drops the rest of the region exactly as it always has, so the class
        never changes the code generated. The message names the killing
        construct by its AST class and is deliberately type-free: dead code
        is never type-checked, and a generic body must emit byte-identical
        messages per instantiation so the driver's print-time dedup collapses
        them to one diagnostic.

        Args:
            prev: The statement that terminated the block (a ``return``,
                ``break``, ``continue``, ``unreachable``, ``emit``, a call to
                a ``@noreturn`` function, a loop that never exits, or a
                construct all of whose paths diverge).
            stmt: The first unreachable statement; the warning points at its
                line.
        """
        if isinstance(prev, Return):
            what = "nothing runs after the 'return' above"
        elif isinstance(prev, Break):
            what = "nothing runs after 'break'"
        elif isinstance(prev, Continue):
            what = "nothing runs after 'continue'"
        elif isinstance(prev, Unreachable):
            what = "nothing runs after 'unreachable'"
        elif isinstance(prev, Emit):
            what = "nothing runs after 'emit'"
        elif isinstance(prev, ExprStmt):
            if isinstance(prev.expr, Except):
                # A statement-position except terminates the block when its
                # handler diverges and a diverging else closes the ok arm.
                what = "every path through the statement above diverges"
            else:
                # Only a direct call to a @noreturn function terminates the
                # block from expression-statement position.
                what = "nothing runs after a call to a @noreturn function"
        elif isinstance(prev, While):
            # A loop can only terminate the block when its constant-true
            # condition folded away the exit edge and no `break` targets it
            # (see the While arm); nothing past it can ever run.
            what = "nothing runs after a loop that never exits"
        else:
            # A statement with internal control flow -- if/case/@if/a bare
            # block -- every generated path of which diverged.
            what = "every path through the statement above diverges"
        self.warn(f"unreachable code: {what}", stmt.line, wclass="dead-code")

    def warn_unused_result(self, line: int) -> None:
        """Warn under ``-Wunused-result`` that a discarded value carries an error.

        The single formatter for the class: a bare expression statement whose
        value is a ``result<...>`` funnels through here. A dropped result drops
        the error it may carry on the floor -- the accidental-ignore hole the
        error-handling design exists to close -- so it warns unless the value
        was consumed by one of the result forms (``let`` binding, destructure,
        ``try``/``except``) or explicitly discarded (``let _ = f();``). Like
        every opt-in class it never changes the code generated.

        Args:
            line: The discarding statement's 1-based source line.
        """
        self.warn(
            "discarded result may carry an error (bind it, destructure it "
            "with 'let v, err =', handle it with 'try', or explicitly "
            "discard it with 'let _ = ...')",
            line, wclass="unused-result",
        )

    def warn_deprecated(self, name: str, msg: str | None, line: int) -> None:
        """Warn that a ``@deprecated`` function was resolved, when ``msg`` is set.

        The single formatter for deprecation warnings: every resolution point
        (direct call, generic overload pick, function value, for-in protocol)
        funnels through here, so the ``'name' is deprecated: msg`` wording is
        emitted uniformly. One suppression applies: a call made from within the
        body of a function that is itself ``@deprecated`` does not warn (see
        ``in_deprecated_body``), so a deprecation shim delegating among the
        deprecated cluster stays quiet; a live function calling a deprecated
        one still warns. The driver deduplicates repeats of one call site
        (e.g. per-instantiation re-emissions) at print time.

        Args:
            name: The name the caller used, reported repr-quoted.
            msg: The migration message, or ``None`` for "not deprecated"
                (a no-op, so lookups can be passed straight in).
            line: The call site's 1-based source line.
        """
        if msg is not None and not self.in_deprecated_body:
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

    def mark_noalias(
        self, fn: ir.Function, func: Func, params: list, arg_offset: int = 0
    ):
        """Apply ``@noalias`` by attaching LLVM's ``noalias`` argument attribute.

        Each marked parameter must be a pointer (the attribute is meaningless
        otherwise). The promise -- that the pointer does not overlap any other
        pointer the function reaches -- is unchecked, exactly C's ``restrict``;
        violating it is undefined behavior.

        Args:
            fn: The IR function whose args to annotate.
            func: The AST function carrying ``noalias_params``.
            params: The resolved parameter ``LangType``s, in order.
            arg_offset: How many hidden args precede the real parameters in
                ``fn.args`` (1 when a struct-return ``sret`` pointer leads).

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
            fn.args[i + arg_offset].add_attribute("noalias")

    def mark_nonnull(
        self, fn: ir.Function, func: Func, params: list, arg_offset: int = 0
    ):
        """Apply ``@nonnull`` by attaching LLVM argument attributes.

        Each marked parameter must be a pointer. Unlike ``@noalias``, the
        guarantee is *checked*: every call site must prove the argument
        non-null (see :meth:`check_nonnull_arg`), so the fact can be handed to
        LLVM as ``nonnull``, plus ``dereferenceable(sizeof(pointee))`` when
        the pointee is sized.

        On an ``@extern`` declaration the hint is conditional on posture. The
        default (relaxed) and ``-Wextern-nonnull`` (warn) postures accept a
        possibly-null argument -- silently or with a warning -- so caller proof
        is no longer unconditional and the ``nonnull``/``dereferenceable``
        attributes would lie; they are emitted only at the strict posture
        (``extern-nonnull`` promoted to error level), which restores that
        proof. A native ``@nonnull`` always carries the hint: its caller proof
        never relaxes.

        Args:
            fn: The IR function whose args to annotate.
            func: The AST function carrying ``nonnull_params``.
            params: The resolved parameter ``LangType``s, in order.
            arg_offset: How many hidden args precede the real parameters in
                ``fn.args`` (1 when a struct-return ``sret`` pointer leads).

        Raises:
            LangError: When ``@nonnull`` marks a non-pointer parameter.
        """
        if not func.nonnull_params:
            return
        # An extern declaration keeps the hint only under the strict posture,
        # where caller proof is unconditional again. The pointer-shape check
        # below is unconditional -- @nonnull on a non-pointer is a declaration
        # error at every posture.
        hint = not func.extern or "extern-nonnull" in self.error_classes
        for i, ((pname, _), ptype) in enumerate(zip(func.params, params)):
            if pname not in func.nonnull_params:
                continue
            if not is_pointer(ptype):
                raise LangError(
                    "@nonnull only applies to pointer parameters",
                    func.line,
                    source=func.source,
                )
            if not hint:
                continue
            fn.args[i + arg_offset].add_attribute("nonnull")
            pointee = strip_const(ptype.pointee)
            if pointee is not VOID and not is_flexible_array(pointee):
                fn.args[i + arg_offset].attributes.dereferenceable = type_size(
                    pointee
                )

    def check_noreturn_decl(self, func: Func, ret: LangType):
        """Validate a ``@noreturn`` declaration once its return type resolves.

        ``@noreturn`` is void-only: a call can then never sit in expression
        position, so terminating the caller's block right after the call is
        always safe. ``main`` is rejected -- its caller is the C runtime,
        which expects the return.

        Args:
            func: The declared function.
            ret: Its resolved return type.

        Raises:
            LangError: When a ``@noreturn`` function is ``main`` or non-void.
        """
        if not func.noreturn:
            return
        if func.name == "main":
            raise LangError("function 'main' cannot be @noreturn", func.line)
        if ret is not VOID:
            raise LangError(
                f"@noreturn function {func.name!r} must return void, not "
                f"{ret} (a call never yields a value)",
                func.line,
            )

    def check_mut_return_decl(self, func: Func, ret: LangType):
        """Validate a ``-> mut`` declaration once its return type resolves.

        A ``mut`` return references existing storage, so ``void`` (no
        storage) is rejected -- for a generic per instance, since the
        return type may name a binding. ``main`` is rejected too: the C
        runtime receives its result by value. ``@extern`` and ``@asm`` are
        already banned at parse time (the pointer-typed return would change
        the C calling convention).

        Args:
            func: The declared function.
            ret: Its resolved return type.

        Raises:
            LangError: When a ``-> mut`` function is ``main`` or void.
        """
        if not func.mut_return:
            return
        if func.name == "main":
            raise LangError(
                "function 'main' cannot return mut (the C runtime receives "
                "its result by value)",
                func.line,
            )
        if ret is VOID:
            raise LangError(
                f"function {func.name!r} cannot return mut void (there is "
                "no storage to reference)",
                func.line,
            )

    def ret_ir(self, func: Func, ret: LangType) -> ir.Type:
        """The LLVM return type for a declaration.

        A ``-> mut T`` function returns a pointer to the caller-reachable
        storage it formed; everything else returns its value.

        Args:
            func: The declared function.
            ret: Its resolved return type.

        Returns:
            The IR return type.
        """
        return ret.ir.as_pointer() if func.mut_return else ret.ir

    def mark_noreturn(self, fn: ir.Function, func: Func):
        """Apply ``@noreturn`` by attaching LLVM's ``noreturn`` attribute.

        The language-level guarantee (call sites terminate their block; the
        body cannot ``return``) is enforced separately; the attribute hands
        the fact to LLVM so the backend can drop the dead continuation.
        """
        if func.noreturn:
            fn.attributes.add("noreturn")

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

    @staticmethod
    def format_indices(func: Func) -> frozenset[int]:
        """Indices of ``func``'s ``@format`` parameters."""
        return frozenset(
            i for i, (name, _) in enumerate(func.params) if name in func.format_params
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

    @staticmethod
    def overload_salt(source: str | None) -> str | None:
        """The per-module salt for a ``@private`` overload's mangled symbol.

        A ``@private`` member of an open overload set is a candidate only
        inside its own module, so two modules may each contribute one with
        the same parameter pattern; salting the mangled symbol with the
        defining file's stem (``format(int32).util``) keeps the two from
        colliding. Unlike :meth:`static_base` the salt is a pure function of
        the source path -- no order-dependent counter -- and it strips the
        ``.mci`` suffix down to the same stem as the ``.mc``: a private
        member traveling in an interface stub (say, a helper an ``@inline``
        body calls) compiles in the consumer and must spell the exact symbol
        the defining object emitted so the ``linkonce_odr`` copies merge.

        Args:
            source: The defining file, or ``None`` for a program parsed from
                a string.

        Returns:
            The stem to salt with, or ``None`` when there is no source file
            (a single-string program has no foreign module to collide with).
        """
        if source is None:
            return None
        stem = source.rsplit("/", 1)[-1]
        return stem.removesuffix(".mci").removesuffix(".mc")

    def import_closure(self, source: str) -> set[str | None]:
        """Every file reachable from ``source`` through its ``import`` graph.

        Walks :attr:`Program.module_imports` (recorded by the driver's
        import merge). Used to derive an interface stub's symbol choice: the
        object a ``.mci`` describes was compiled seeing its own declarations
        plus whatever its own imports contributed -- and nothing the
        consumer added later.

        Args:
            source: The file to start from.

        Returns:
            The reachable source paths, ``source`` included.
        """
        seen: set[str | None] = {source}
        work = [source]
        graph = self.program.module_imports
        while work:
            for target in graph.get(work.pop(), ()):
                if target not in seen:
                    seen.add(target)
                    work.append(target)
        return seen

    def template_base(self, func: Func, groups: bool = True) -> str:
        """Spell a generic template's order-independent symbol base.

        The base is derived from the declaration alone --
        ``name<$0, $1>(patterns)`` -- with the type parameters alpha-renamed
        to positional ``$i`` placeholders in declaration order, so two
        objects that merged one overload set in different import orders spell
        identical instance symbols (declaration-order bases could hand
        *different templates'* instances one ``linkonce_odr`` symbol, a
        silent wrong merge). Every template takes a pattern base, even alone
        in its set: imported files may extend the set, so a lone template in
        one file can have unseen siblings in another.

        A defaulted parameter spells ``$i = <default>`` with the default
        rendered through the same substitution (defaults may reference
        earlier parameters), and a closed type group spells
        ``$i: member|member`` (a nominal ``extends`` bound spells
        ``$i extends struct``) before the default: two same-pattern templates
        with disjoint groups or different bounds are distinct, resolvable
        overloads, so the constraint is part of the template's identity and
        collision key. Patterns
        are the source spelling of each value parameter's type; a ``mut``
        parameter keeps its marker (``mut $0``) because a same-shape
        mut/by-value template pair is a genuine, resolvable overload -- an
        rvalue argument filters out the mut candidate -- unlike the concrete
        case, where the pair is uncallable and banned. ``const`` markers and
        the return type never distinguish template overloads (the concrete
        mangle's rules), so neither appears -- two templates of one name
        spelling one base are duplicates, caught at declaration.

        Args:
            func: The generic function template.
            groups: When ``False``, omit the type groups from the spelling --
                the group-blind *pattern* base the declare-time overlap check
                compares (see :meth:`check_group_overlap`).

        Returns:
            The mangle base, e.g. ``alloc<$0>(uint64)`` or ``hash<$0>($0*)``.
        """
        index = {t: f"${i}" for i, t in enumerate(func.type_params)}

        def subst(ref: TypeRef) -> TypeRef:
            return dataclasses_replace(
                ref,
                name=index.get(ref.name, ref.name),
                args=[subst(a) for a in ref.args],
                params=(
                    [subst(p) for p in ref.params]
                    if ref.params is not None
                    else None
                ),
                ret=subst(ref.ret) if ref.ret is not None else None,
            )

        head = []
        for tparam in func.type_params:
            piece = index[tparam]
            members = func.type_param_groups.get(tparam)
            if groups and members:
                piece += ": " + "|".join(str(subst(m)) for m in members)
            bound = func.type_param_bounds.get(tparam)
            if groups and bound is not None:
                # A nominal bound, like a closed group, is part of the
                # template's identity: `f<$0 extends point>($0)`. Two
                # same-pattern templates whose bounds differ are distinct
                # symbols (bound rejections partition them at call sites).
                piece += f" extends {subst(bound)}"
            default = func.type_param_defaults.get(tparam)
            if default is not None:
                piece += f" = {subst(default)}"
            head.append(piece)
        patterns = ", ".join(
            ("mut " if pname in func.mut_params else "") + str(subst(ptype))
            for pname, ptype in func.params
        )
        return f"{func.name}<{', '.join(head)}>({patterns})"

    def check_group_decl(self, func: Func):
        """Resolve and validate a template's closed type groups at declaration.

        Each group member must be a resolvable concrete type (the parser
        already rejected members referencing type parameters; an unknown name
        errors here), listed once (resolved comparison, so an alias
        duplicating a member is caught -- the ``case type`` duplicate-arm
        stance), and a grouped parameter's default must name a member.
        The resolved groups are cached for the call-site viability filter
        and the eager end-of-codegen member check; resolving here, where
        no instantiation's ``type_bindings`` are live, keeps a member name
        from ever meaning a binding.

        Args:
            func: The generic template being declared (``current_source`` is
                already its file, so members may name its private types).

        Raises:
            LangError: On an unresolvable member, a duplicate member, or a
                default outside its parameter's group.
        """
        if not func.type_param_groups:
            return
        resolved: dict[str, list[LangType]] = {}
        for tparam, members in func.type_param_groups.items():
            types: list[LangType] = []
            for ref in members:
                member = self.lang_type(ref, func.line)
                if member in types:
                    raise LangError(
                        f"duplicate type group member {member} for type "
                        f"parameter {tparam} of {func.name!r}",
                        func.line,
                    )
                types.append(member)
            resolved[tparam] = types
            default = func.type_param_defaults.get(tparam)
            if default is not None:
                bound = self.lang_type(default, func.line)
                if bound not in types:
                    raise LangError(
                        f"default for type parameter {tparam} of "
                        f"{func.name!r} must be a member of its type group "
                        f"({' | '.join(str(m) for m in types)}), got {bound}",
                        func.line,
                    )
        self.group_types[id(func)] = resolved
        self.group_templates.append(func)

    def check_group_overlap(self, func: Func, base: str, overloads: list[Func]):
        """Reject same-pattern templates whose type groups share a member.

        Same-pattern templates with **disjoint** groups are a resolvable
        overload set -- deduction plus the group filter picks one -- so they
        deliberately pass the duplicate-base check (the group is in the
        base). But a shared member leaves a deduction both accept, at one
        rank tier and specificity: an ambiguity at every such call, so it
        collides at declaration, cross-module like the duplicate rule. Two
        templates whose groups constrain *different* parameters overlap too
        (each leaves the other's parameter unconstrained). A fully unbounded
        same-pattern template beside bounded ones is fine -- it ranks a tier
        below, catching whatever the groups exclude.

        Args:
            func: The template being declared (its groups already resolved).
            base: ``func``'s full symbol base, for the error message.
            overloads: The already-declared same-name templates.

        Raises:
            LangError: When a same-pattern sibling's groups overlap
                ``func``'s.
        """
        if not func.type_param_groups:
            return
        pattern = self.template_base(func, groups=False)
        groups = self.group_types[id(func)]
        for prior in overloads:
            if not prior.type_param_groups:
                continue
            if self.template_base(prior, groups=False) != pattern:
                continue
            prior_groups = self.group_types[id(prior)]
            # The pair is resolvable only if some parameter position is
            # constrained to disjoint sets by both; an unbounded position
            # (no group) constrains nothing, so it intersects everything.
            for own, other in zip(func.type_params, prior.type_params):
                mine, theirs = groups.get(own), prior_groups.get(other)
                if mine is None or theirs is None:
                    continue
                if not any(m == t for m in mine for t in theirs):
                    break  # disjoint here: every call resolves
            else:
                raise LangError(
                    f"function '{base}' overlaps "
                    f"'{self.template_bases[id(prior)]}'; same-pattern "
                    "overloads need disjoint type groups",
                    func.line,
                )

    def check_bound_overlap(self, func: Func, base: str, overloads: list[Func]):
        """Reject a second bounded same-pattern template.

        The open-set counterpart of :meth:`check_group_overlap`. A closed
        group can be shown disjoint from a sibling's (their members compared),
        so disjoint-group same-pattern templates form a resolvable set. An
        ``extends`` bound is an *open* set -- any struct anywhere may later
        declare the lineage -- so two same-pattern bounded templates cannot be
        proven disjoint and are conservatively rejected at declaration, even
        when their bound structs differ. Disjoint-bound overloads are a
        deliberately deferred follow-up; v1 allows exactly one bounded
        overload beside an unbounded fallback (which ranks a tier below and
        catches whatever the bound excludes). Same-pattern is compared
        bound-blind and group-blind (the ``groups=False`` base), so a bounded
        template collides with a bounded sibling of the same value patterns.

        Args:
            func: The template being declared (its bounds already resolved).
            base: ``func``'s full symbol base, for the error message.
            overloads: The already-declared same-name templates.

        Raises:
            LangError: When a same-pattern sibling also carries a bound.
        """
        if not func.type_param_bounds:
            return
        pattern = self.template_base(func, groups=False)
        for prior in overloads:
            if not prior.type_param_bounds:
                continue
            if self.template_base(prior, groups=False) != pattern:
                continue
            raise LangError(
                f"function '{base}' overlaps "
                f"'{self.template_bases[id(prior)]}'; two same-pattern "
                "bounded overloads are not yet supported (a bound is an open "
                "set; use one bounded overload beside an unbounded fallback)",
                func.line,
            )

    def group_violation(
        self, func: Func, bindings: dict[str, LangType]
    ) -> "tuple[LangType, str] | None":
        """Check a candidate's deduced bindings against its type groups.

        Deduction is unchanged by groups; this is the post-deduction
        viability filter. A ``const``-qualified binding is compared bare
        too, matching how unification sheds the qualifier.

        Args:
            func: The candidate template.
            bindings: The deduced ``{type parameter: type}`` map (may be
                partial during a lenient trial; unbound parameters pass).

        Returns:
            ``None`` when every grouped parameter's binding is a member,
            else the offending bound type and its group's spelling (for the
            not-in-group error).
        """
        groups = self.group_types.get(id(func))
        if not groups:
            return None
        for tparam in func.type_params:
            members = groups.get(tparam)
            bound = bindings.get(tparam)
            if members is None or bound is None:
                continue
            bare = strip_const(bound)
            if all(m != bound and m != bare for m in members):
                return bound, " | ".join(str(m) for m in members)
        return None

    @staticmethod
    def group_error(name: str, bound: LangType, group: str) -> str:
        """The error message for a deduced type outside a closed type group."""
        return f"{bound} is not in the type group of {name!r} ({group})"

    def check_bound_decl(self, func: Func):
        """Resolve and validate a template's ``extends`` bounds at declaration.

        Each bound target must resolve to a concrete struct (the parser
        already rejected targets naming a type parameter; an unknown name or a
        non-struct errors here, reusing :meth:`resolve_base`'s rejection
        strings). A bounded parameter that also carries a default has that
        default checked against the bound now -- mirroring the closed-group
        member-default check -- so a violating default fails at the
        *declaration*. The resolved bounds are cached for the lazy call-site
        viability filter; resolving here, where no instantiation's
        ``type_bindings`` are live, keeps a bound name from ever meaning a
        binding.

        Unlike a closed group the satisfying set is open-ended, so there is no
        eager enumeration and no overlap check: same-pattern bounded templates
        cannot be shown disjoint, so a second bounded overload beside the first
        still collides at declaration (the duplicate-base rule) -- only an
        unbounded fallback may join, one rank tier below.

        Args:
            func: The generic template being declared (``current_source`` is
                already its file, so a bound may name its private types).

        Raises:
            LangError: On an unresolvable or non-struct bound target, or a
                default that does not satisfy its parameter's bound.
        """
        if not func.type_param_bounds:
            return
        resolved: dict[str, LangType] = {}
        for tparam, ref in func.type_param_bounds.items():
            bound = self.lang_type(ref, func.line)
            if not is_aggregate(bound):
                raise LangError(
                    f"{bound} is not a struct; cannot extend it", func.line
                )
            if is_union(bound):
                raise LangError(
                    f"a union cannot be extended, but the bound of type "
                    f"parameter {tparam} of {func.name!r} is union {bound}",
                    func.line,
                )
            resolved[tparam] = bound
            default = func.type_param_defaults.get(tparam)
            if default is not None:
                got = self.lang_type(default, func.line)
                if not self.nominal_subtype(bound, got):
                    raise LangError(
                        f"default {got} for type parameter {tparam} of "
                        f"{func.name!r} does not satisfy its bound {bound}",
                        func.line,
                    )
        self.bound_types[id(func)] = resolved

    def bound_violation(
        self, func: Func, bindings: dict[str, LangType]
    ) -> "tuple[LangType, LangType] | None":
        """Check a candidate's deduced bindings against its ``extends`` bounds.

        Deduction is unchanged by bounds; this is the post-deduction viability
        filter (the open-set sibling of :meth:`group_violation`). A binding
        satisfies its bound when it *is* the bound struct or reaches it up its
        declared ``extends`` chain -- the nominal relation, reused verbatim, so
        a layout twin that does not declare the lineage is rejected. ``const``
        is shed before the comparison, matching unification.

        Args:
            func: The candidate template.
            bindings: The deduced ``{type parameter: type}`` map (may be
                partial during a lenient trial; unbound parameters pass).

        Returns:
            ``None`` when every bounded parameter's binding satisfies its
            bound, else the offending (const-stripped) deduced type and the
            bound struct it failed (for the not-a-subtype error).
        """
        bounds = self.bound_types.get(id(func))
        if not bounds:
            return None
        for tparam in func.type_params:
            bound = bounds.get(tparam)
            deduced = bindings.get(tparam)
            if bound is None or deduced is None:
                continue
            bare = strip_const(deduced)
            if not self.nominal_subtype(bound, bare):
                return bare, bound
        return None

    @staticmethod
    def bound_error(name: str, offender: LangType, bound: LangType) -> str:
        """The error message for a deduced type outside an ``extends`` bound."""
        return f"{offender} does not satisfy the bound {bound} of {name!r}"

    def group_member_combos(self, func: Func) -> list[dict[str, LangType]]:
        """Enumerate a grouped template's eager-check binding combinations.

        The cartesian product over the grouped parameters' members, with the
        remaining parameters filled from their declared defaults. A template
        with a non-grouped, non-defaulted parameter cannot be enumerated --
        that parameter has no closed set of types -- so it is checked at its
        call sites only, like an ordinary generic.

        Args:
            func: A template with at least one closed type group.

        Returns:
            One complete ``{type parameter: type}`` map per combination
            (empty when the template cannot be enumerated).
        """
        groups = self.group_types[id(func)]
        combos: list[dict[str, LangType]] = [{}]
        for tparam in func.type_params:
            members = groups.get(tparam)
            if members is None:
                if tparam not in func.type_param_defaults:
                    return []
                continue
            combos = [{**c, tparam: m} for c in combos for m in members]
        for bindings in combos:
            self.fill_default_bindings(func, bindings, func.line)
        return combos

    def eager_group_instances(self) -> bool:
        """Instantiate every not-yet-checked type-group member combination.

        Group checking is **eager**: at the end of codegen every listed
        member of every grouped template is instantiated and fully
        type-checked whether or not it was ever called, so a member the
        body does not compile for errors at the declaration (the
        multi-type ``case type`` arm precedent). Never-called member
        instances are ordinary emitted functions -- dead code the linker
        strips in object mode, harmless under the JIT. Compiling a member
        body can box new types (feeding pending generic ``case type``
        arms), so the caller loops this against
        :meth:`finalize_generic_arms` until both are quiet.

        Returns:
            ``True`` when at least one new instance was generated.
        """
        progress = False
        for func in self.group_templates:
            for bindings in self.group_member_combos(func):
                key = (
                    id(func),
                    tuple(str(bindings[t]) for t in func.type_params),
                )
                if key in self.instances:
                    continue
                # The instantiation is requested by the declaration itself:
                # attribute the backtrace note to the template's file/line.
                outer = self.current_source
                self.current_source = func.source
                try:
                    self.instantiate(func, bindings, func.line)
                finally:
                    self.current_source = outer
                progress = True
        return progress

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
                elif isinstance(item, (StructDecl, UnionDecl)):
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

    def analyze_write_effects(self):
        """Compute every function's transitive **write-effect bit**.

        Runs once per compilation, after ``@if`` flattening and the
        declaration pass (so the name tables exist) and before any body is
        generated. A function's bit is SET when it may write memory a
        caller's projection fact could live in, under the strict recorded
        rule -- any through-memory store counts, a store to the function's
        own local struct included:

        - a ``StoreDeref``/``StoreIndex``/``StoreMember``/``StoreCall``
          statement, or a compound assignment whose target is not a bare
          name;
        - an assignment to a ``mut`` parameter (a store through the hidden
          reference into the caller's storage) or to a global;
        - anything opaque: ``@asm``, a call through a function-pointer
          value (a ``CallExpr``, or a ``Call`` whose name is locally bound
          or a ``const``), ``va_start``/``va_end``, a bodyless callee
          (``@extern``, an unpaired prototype), a protocol ``for`` loop
          (its ``_it``/``_next`` callee names depend on the iterable's
          type, which a syntactic pass cannot see -- the builtin
          ``range``/``enumerate`` counting loops emit no call and do not
          count), or -- when any struct in the program declares a
          call-bearing field default -- a struct literal or bare
          annotated ``let`` (defaults evaluate at the application site,
          outside the body's own AST);
        - a call edge to any same-name candidate whose bit is set. Edges
          are a **candidate union** over templates, overloads, concrete
          declarations, and file-scoped ``@static``\\ s per (source, name),
          so a generic template takes one bit for all its instances.

        The bits are the least fixpoint of that system, seeded optimistic
        (clear): a write-free recursion cycle stays clear, while any base
        condition anywhere in a cycle propagates to the whole cycle.
        Results land in :attr:`effect_bits` (every body-owning ``Func`` by
        id, templates included) and :attr:`fact_safe_syms` (the clear
        concrete symbols; :meth:`instantiate` adds each clear template's
        instance symbols as they are stamped out). Call emission consults
        them to skip the blanket projection-fact kill (:meth:`emit_call`);
        name facts were never killed by calls and are unaffected.
        """
        global_names = {v.name for v in self.program.globals}
        # A struct field default is an ordinary expression evaluated at each
        # application site (a literal omitting the field, or a bare
        # annotated `let` of a defaulted struct), so a call inside one is a
        # call the body walk cannot see. Rare enough to gate coarsely: when
        # any struct in the program declares a call-bearing default, every
        # struct literal and bare annotated `let` counts as opaque.
        defaults_call = any(
            contains_call(expr)
            for decl in self.program.structs
            for expr in decl.defaults.values()
        )
        live = [f for f in self.program.functions if f.removed_msg is None]
        # A prototype paired with a definition in this program is covered by
        # the definition's candidacy; pairing is per signature (matched here
        # by the parameter types' spelling -- a spelling mismatch only keeps
        # the prototype, which reads as bodyless/opaque: conservative).
        def spelling(f: Func) -> tuple[str, ...]:
            return tuple(str(t) for _, t in f.params)

        defined = {
            (f.name, spelling(f))
            for f in live
            if not f.proto and not f.extern and not f.static and not f.type_params
        }
        by_name: dict[str, list[Func]] = {}
        by_static: dict[tuple[str | None, str], list[Func]] = {}
        for f in live:
            if (
                f.proto
                and not f.static
                and not f.type_params
                and (f.name, spelling(f)) in defined
            ):
                continue
            if f.static:
                by_static.setdefault((f.source, f.name), []).append(f)
            else:
                by_name.setdefault(f.name, []).append(f)
        analyzed = [f for f in live if not f.extern and not f.proto]
        bits: dict[int, bool] = {}
        edges: dict[int, set[int]] = {}
        outer_source = self.current_source
        try:
            for func in analyzed:
                self.current_source = func.source
                writes, calls = self.scan_write_effects(
                    func, global_names, defaults_call
                )
                callees: set[int] = set()
                if not writes:
                    for name in calls:
                        cands = by_static.get(
                            (func.source, name), []
                        ) + by_name.get(name, [])
                        if not cands or any(c.proto or c.extern for c in cands):
                            # Bodyless (or undefined -- an error at the call
                            # site later): opaque.
                            writes = True
                            break
                        callees.update(id(c) for c in cands)
                bits[id(func)] = writes
                edges[id(func)] = callees
        finally:
            self.current_source = outer_source
        # Optimistic-clear least fixpoint over the call graph.
        changed = True
        while changed:
            changed = False
            for fid, callees in edges.items():
                if not bits[fid] and any(bits.get(c, True) for c in callees):
                    bits[fid] = True
                    changed = True
        self.effect_bits = bits
        for func in analyzed:
            if func.type_params or bits[id(func)]:
                continue
            # The same symbol choice the body-generation loop makes.
            symbol = self.overload_symbols.get(
                id(func),
                self.static_funcs.get((func.source, func.name), func.name),
            )
            self.fact_safe_syms.add(symbol)

    def scan_write_effects(
        self, func: Func, global_names: set[str], defaults_call: bool = False
    ):
        """Scan one function body for write-effect base conditions.

        The syntactic half of :meth:`analyze_write_effects`: a recursive
        walk over the statement and expression AST, tracking block scopes
        so a name's binding kind is known where it is used -- a ``let``
        shadow ends with its block, after which an assignment to the name
        hits the global again (whole-body tracking would miss that write).
        Compile-time ``@if`` statements contribute only their live branch
        (the dead branch is never emitted); ``defer`` bodies and block
        expressions are ordinary children and are walked like any other.

        Args:
            func: The function whose body to scan.
            global_names: Every global variable name in the program.
            defaults_call: Some struct in the program declares a
                call-bearing field default, so struct literals and bare
                annotated ``let``\\ s may evaluate a hidden call and count
                as opaque.

        Returns:
            A ``(writes, calls)`` pair: whether a base condition sets the
            bit outright, and the named calls needing candidate resolution.
        """
        writes = False
        calls: set[str] = set()

        def name_write(name: str, bound: dict):
            # An assignment's target by name: a store through a mut
            # parameter's hidden reference or to a global sets the bit; a
            # plain local, by-value parameter, or shadowing let is private
            # to this frame under the name-fact rules and stays clear.
            nonlocal writes
            kind = bound.get(name)
            if kind == "param" and name in func.mut_params:
                writes = True
            elif kind is None and name in global_names:
                writes = True

        def scan_expr(node, bound: dict):
            nonlocal writes
            if isinstance(node, Call):
                if node.name in ("va_start", "va_end") and not node.type_args:
                    writes = True  # the va intrinsics stay opaque
                elif node.name in bound or node.name in self.consts:
                    # May be an indirect call through a local or const
                    # function pointer (variables and consts shadow
                    # function names at the call site).
                    writes = True
                else:
                    calls.add(node.name)
                for arg in node.args:
                    scan_expr(arg, bound)
                return
            if isinstance(node, (CallExpr, Asm)):
                writes = True  # opaque: fn-pointer call / inline asm
            elif isinstance(node, BlockExpr):
                scan_stmts(node.body, bound)
                return
            elif isinstance(node, StructLit) and defaults_call:
                writes = True  # an omitted field may evaluate a calling default
            if isinstance(node, (list, tuple)):
                for item in node:
                    scan_expr(item, bound)
            elif is_dataclass(node) and not isinstance(node, TypeRef):
                for f in dataclass_fields(node):
                    scan_expr(getattr(node, f.name), bound)

        def scan_iterable(it, bound: dict):
            # The builtin range/enumerate counting loops emit no call (a
            # user-defined function of the name takes precedence, exactly
            # as in gen_for). Any other iterable may lower to the
            # <struct>_it/<struct>_next protocol, whose callee names
            # depend on the iterable's type: opaque. (A slice iterates
            # natively and is over-tainted here -- a recorded limitation
            # of the syntactic pass.)
            nonlocal writes
            if (
                isinstance(it, Call)
                and it.name == "range"
                and not self.callable_exists("range")
            ):
                for arg in it.args:
                    scan_expr(arg, bound)
                return
            if (
                isinstance(it, Call)
                and it.name == "enumerate"
                and not self.callable_exists("enumerate")
            ):
                for arg in it.args:
                    scan_iterable(arg, bound)
                return
            writes = True
            scan_expr(it, bound)

        def scan_stmts(stmts: list, bound: dict):
            bound = dict(bound)  # a fresh block scope
            for stmt in stmts:
                scan_stmt(stmt, bound)

        def scan_stmt(stmt, bound: dict):
            nonlocal writes
            if isinstance(stmt, Let):
                if stmt.value is not None:
                    scan_expr(stmt.value, bound)
                elif defaults_call:
                    # `let s: T;` default-initializes a defaulted struct,
                    # which may evaluate a calling default.
                    writes = True
                bound[stmt.name] = "local"
                for extra in stmt.extra:  # destructuring binders
                    bound[extra] = "local"
            elif isinstance(stmt, Assign):
                scan_expr(stmt.value, bound)
                name_write(stmt.name, bound)
            elif isinstance(stmt, CompoundAssign):
                scan_expr(stmt.value, bound)
                if isinstance(stmt.target, Var):
                    name_write(stmt.target.name, bound)
                else:
                    writes = True  # a through-memory store form
                    scan_expr(stmt.target, bound)
            elif isinstance(
                stmt, (StoreDeref, StoreIndex, StoreMember, StoreCall)
            ):
                # StoreCall included: a store through a returned reference
                # reaches caller-visible memory, so a function assigning
                # through a mut-returning call is never write-free (the
                # catch-all below would scan the call but record no write).
                writes = True
                for f in dataclass_fields(stmt):
                    scan_expr(getattr(stmt, f.name), bound)
            elif isinstance(stmt, If):
                scan_expr(stmt.cond, bound)
                scan_stmts(stmt.then, bound)
                scan_stmts(stmt.otherwise, bound)
            elif isinstance(stmt, While):
                scan_expr(stmt.cond, bound)
                scan_stmts(stmt.body, bound)
            elif isinstance(stmt, For):
                scan_iterable(stmt.iterable, bound)
                body_scope = dict(bound)
                body_scope[stmt.var] = "local"
                scan_stmts(stmt.body, body_scope)
            elif isinstance(stmt, (Block, Defer)):
                scan_stmts(stmt.body, bound)
            elif isinstance(stmt, Case):
                scan_expr(stmt.subject, bound)
                for values, body in stmt.arms:
                    scan_expr(values, bound)
                    scan_stmts(body, bound)
                scan_stmts(stmt.otherwise, bound)
            elif isinstance(stmt, CaseType):
                scan_expr(stmt.subject, bound)
                for _type_refs, name, body, _line in stmt.arms:
                    arm_scope = dict(bound)
                    arm_scope[name] = "local"
                    scan_stmts(body, arm_scope)
                scan_stmts(stmt.otherwise, bound)
            elif isinstance(stmt, Conditional):
                # Only the live branch is ever emitted; its statements run
                # inline in the current scope (matching gen_statement).
                taken = (
                    stmt.then
                    if self.eval_static_cond(stmt.cond)
                    else stmt.otherwise
                )
                for inner in taken:
                    scan_stmt(inner, bound)
            elif isinstance(stmt, (Return, Emit)):
                scan_expr(stmt.value, bound)
            elif isinstance(stmt, ExprStmt):
                scan_expr(stmt.expr, bound)
            elif isinstance(stmt, (Break, Continue, Unreachable)):
                pass
            else:
                scan_expr(stmt, bound)  # conservative catch-all

        scan_stmts(func.body, {name: "param" for name, _ in func.params})
        return writes, calls

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

    def declare_extern_abi(
        self, func: Func, ret: LangType, params: list
    ) -> "ExternABI | None":
        """Classify a struct-passing ``@extern`` for the platform C ABI.

        Returns ``None`` for a signature that is all scalars/pointers -- it
        needs no marshalling and keeps today's declaration verbatim. Otherwise
        builds the per-argument and return classifications (see
        :mod:`mcc.codegen.abi`), which the declaration lowers to coercion types
        or a hidden ``sret`` pointer and the call site marshals to.

        AArch64/AAPCS64, x86-64 System V, and x86-64 Windows (Win64) are
        implemented (see :mod:`mcc.codegen.abi`); on any other target
        (riscv64, unknown) an ``@extern`` that passes or returns an aggregate by
        value is a hard error, rather than silently emitting the raw-aggregate
        form that does not match the C ABI there.

        Args:
            func: The ``@extern`` declaration.
            ret: Its resolved return ``LangType``.
            params: Its resolved parameter ``LangType``s, in order.

        Returns:
            An :class:`ExternABI` when a by-value aggregate is present, else
            ``None``.

        Raises:
            LangError: When a by-value aggregate crosses the boundary on an
                unsupported target.
        """
        has_agg = is_aggregate(ret) or any(is_aggregate(p) for p in params)
        if not has_agg:
            return None
        target = (self.target or _host_triple()).lower()
        if not abi_supported(target):
            raise LangError(
                "passing a struct by value across the C boundary is not "
                f"supported on target {self.target or _host_triple()!r} yet; "
                "pass a pointer instead",
                func.line,
                source=func.source,
            )
        arg_classes, ret_class = classify_signature(ret, params, target)
        return ExternABI(arg_classes, ret_class)

    def extern_ret_ir(self, ret: LangType, plan: "ExternABI | None") -> ir.Type:
        """The LLVM return type for an ``@extern`` declaration under its plan.

        A struct returned in registers becomes its coercion type; a large
        struct returns ``void`` (its value travels through the ``sret``
        pointer); everything else is unchanged.
        """
        if plan is None or plan.ret is None:
            return ret.ir
        if isinstance(plan.ret, Indirect):
            return ir.VoidType()
        return plan.ret.coerce_ir

    def extern_param_irs(
        self, params: list, plan: "ExternABI | None"
    ) -> list:
        """The LLVM parameter types for an ``@extern`` declaration under its plan.

        A register aggregate becomes its coercion type and a large aggregate a
        pointer to the caller's copy; a leading ``sret`` pointer is prepended
        for a struct return. Scalar/pointer parameters keep :meth:`param_irs`.
        """
        out = self.param_irs(params)
        if plan is None:
            return out
        for i, cls in enumerate(plan.args):
            if isinstance(cls, Direct):
                out[i] = cls.coerce_ir
            elif isinstance(cls, Indirect):
                out[i] = cls.struct_ir.as_pointer()
        if isinstance(plan.ret, Indirect):
            out.insert(0, plan.ret.struct_ir.as_pointer())
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
            if (name in func.const_params and is_aggregate(ptype))
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
        hidden-reference, ``mut``, ``@nonnull``, ``@noalias``, and
        ``@format`` positions
        -- plus the same ``@private``, ``@inline``, and ``@noreturn`` flags
        (a prototype is never ``@inline``, so an ``@inline`` definition
        cannot pair with one; a ``@noreturn`` mismatch would let a stub and
        its definition disagree on call-site divergence). Parameter names
        may differ.

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
            # An @format mismatch would let a stub and its definition
            # disagree on the call-site positional desugar.
            or self.format_indices(prior) != self.format_indices(func)
            or prior.private != func.private
            or prior.inline != func.inline
            or prior.noreturn != func.noreturn
            # The resolved return types compare equal for `-> T` vs
            # `-> mut T` (mut is not part of the type), so the flag is
            # checked on its own: a mismatch would let a stub and its
            # definition disagree on the call's lvalue-ness and ABI.
            or prior.mut_return != func.mut_return
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

        Overload sets are open: the concrete side may live in any module.
        The only requirement is that the concrete side is overloadable at
        all -- ``main``, a variadic function, and a ``va_list``-taking
        function never join a set, whichever side declares first.

        Args:
            func: The incoming generic template.
            members: The name's concrete declarations, by params-key.

        Raises:
            LangError: On a non-overloadable concrete side.
        """
        if func.name == "main":
            raise LangError("function 'main' cannot be overloaded", func.line)
        for member in members.values():
            if member.variadic:
                raise LangError(
                    f"variadic function {func.name!r} cannot be overloaded",
                    func.line,
                )
            symbol = self.overload_symbols.get(id(member), member.name)
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
        explicit-slice (the resolution path judges a template by this same
        syntactic marker, see :meth:`collecting_candidate`).

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

    def collecting_candidate(self, func: Func) -> bool:
        """Report whether an overload candidate collects extra arguments.

        A concrete candidate resolves through its declared signature
        (:meth:`is_collecting_func`); a generic template's parameter types
        cannot resolve before instantiation, so the syntactic marker decides
        (:meth:`collecting_ref` -- a template hiding the marker behind an
        alias stays explicit-slice, as documented there).

        Args:
            func: The candidate declaration (template or concrete).

        Returns:
            ``True`` when calls to this candidate collect their extras.
        """
        if not func.params or func.params[-1][0] in func.mut_params:
            return False
        if func.type_params:
            return self.collecting_ref(func.params[-1][1])
        symbol = self.overload_symbols.get(id(func), func.name)
        sig = self.signatures.get(symbol)
        return sig is not None and self.is_collecting_func(func, sig[1])

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

    def check_format_decl(self, func: Func, params: list):
        """Validate an ``@format`` declaration once its parameters resolve.

        ``@format`` marks a collecting function's format string, so the
        call-site desugar (see :meth:`scan_positional`) knows which literal
        to scan and which arguments its positional placeholders select. That
        pins the shape: the marked parameter must be the ``slice<const char>``
        just before the collecting ``args...``. Checked here rather than at
        parse time so an alias spelling of the type still qualifies; a
        generic template is checked per instantiation (its parameter types
        may name type parameters).

        Args:
            func: The declared function.
            params: Its resolved parameter ``LangType``\\ s.

        Raises:
            LangError: When ``@format`` marks a parameter of a non-collecting
                function, a parameter other than the last fixed one, or a
                parameter that is not a ``slice<const char>``.
        """
        if not func.format_params:
            return
        # Mirror the call path's collection decision: a template is judged
        # by the syntactic marker (an alias spelling stays explicit-slice,
        # see collecting_candidate), a concrete function by its resolved
        # signature.
        if func.type_params:
            collects = (
                bool(func.params)
                and func.params[-1][0] not in func.mut_params
                and self.collecting_ref(func.params[-1][1])
            )
        else:
            collects = self.is_collecting_func(func, params)
        if not collects:
            raise LangError(
                "@format only applies to a collecting function's format "
                "string (declare a trailing 'args...')",
                func.line,
                source=func.source,
            )
        if (
            len(func.params) < 2
            or len(func.format_params) > 1
            or func.params[-2][0] not in func.format_params
        ):
            raise LangError(
                "@format only applies to the parameter just before the "
                "collecting 'args...'",
                func.line,
                source=func.source,
            )
        fmt = strip_const(params[-2])
        if not (
            is_slice(fmt) and fmt.args[0].const and strip_const(fmt.args[0]) == CHAR
        ):
            raise LangError(
                f"an @format parameter must be a slice<const char>, not "
                f"{params[-2]}",
                func.line,
                source=func.source,
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
        alias = Alias(
            decl.target,
            decl.private,
            decl.source,
            decl.line,
            type_params=decl.type_params,
            type_param_defaults=decl.type_param_defaults,
        )
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
        if decl.is_error:
            self.register_error(decl)
            return
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

    def register_error(self, decl: EnumDecl):
        """Register an ``error`` declaration as a nominal error type.

        Unlike an ``enum`` -- transparent, explicit-valued, duplicates legal --
        an error type is nominal (its ``int32``-backed ``LangType`` is a
        distinct type: no arithmetic, no implicit integer conversion) and its
        variants always auto-number from 1 in declaration order. Error values
        are automatic (the parser rejects an explicit ``= n``), so the values
        are dense ``1..N`` with no gaps and no duplicates -- every variant is
        non-zero by construction and zero stays the reserved, unnameable
        no-error state that makes ``if (err)`` a total check. The record rides
        the enum registries, so name clashes, ``Enum::Member`` access,
        ``@private``, and ``@static`` shadowing all behave as for an enum; a
        ``@static`` error's type takes its salted file-scoped name, like a
        ``@static`` struct.

        Args:
            decl: The error declaration to register (``is_error`` set).

        Raises:
            LangError: On a name clash or a duplicate member name.
        """
        self.current_source = decl.source
        name = (
            self.static_base(decl.name, decl.source) if decl.static else decl.name
        )
        err_type = LangType(name, ir.IntType(32), signed=True, template="error")
        enum = EnumType(
            err_type,
            {},
            decl.private,
            decl.source,
            displays=dict(decl.displays),
            display_name=decl.name,
        )
        # Keyed by the nominal type name (salted for @static), the reverse map
        # an error value's LangType uses to find its variant/display tables.
        self.error_types[name] = enum
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
        for value, (mname, _) in enumerate(decl.members, start=1):
            if mname in enum.members:
                raise LangError(
                    f"error {decl.name!r} has a duplicate member {mname!r}",
                    decl.line,
                )
            enum.members[mname] = TypedValue(
                ir.Constant(err_type.ir, value), err_type
            )

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
        label = "error" if is_error_decl(enum.underlying) else "enum"
        self.check_access(
            enum.private, enum.source, f"{label} {expr.enum!r}", expr.line
        )
        member = enum.members.get(expr.member)
        if member is None:
            raise LangError(
                f"{label} {expr.enum!r} has no member {expr.member!r}", expr.line
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
            nonnull = frozenset(i for i, p in enumerate(ref.params) if p.nonnull)
            mutref = frozenset(i for i, p in enumerate(ref.params) if p.mut)
            mutret = ref.ret is not None and ref.ret.mut
            if mutret:
                # Per use, like the @nonnull rule below, so a generic alias
                # like `type getter<T> = fn() -> mut T` is validated against
                # each binding (mirroring check_mut_return_decl's per-instance
                # placement on the declaration side).
                if ret is VOID:
                    raise LangError(
                        "a function type cannot return mut void (there is "
                        "no storage to reference)",
                        line,
                    )
                if ret.const:
                    # The parse-time compose ban, re-checked here for a
                    # const that rides in through a generic binding.
                    raise LangError(
                        "a return cannot be both mut and const "
                        "(a mut return must be writable)",
                        line,
                    )
            # Checked per use, so a generic alias like `type cb<T> =
            # fn(@nonnull T*)` is validated against each binding (mirrors
            # mark_nonnull's declaration-site rule).
            for i in sorted(nonnull):
                if not is_pointer(params[i]):
                    raise LangError(
                        "@nonnull only applies to pointer parameters", line
                    )
            # A `const` qualifier canonicalizes inside function_type: on an
            # aggregate it records the hidden-reference index, on a scalar it
            # drops -- per use, so `type cmp<T> = fn(const T, const T)` gets
            # the right convention at each binding.
            base = function_type(
                ret, params, ref.variadic, nonnull, mutref, mutret=mutret
            )
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
        elif ref.name == "tuple":
            # Any arity: `tuple<>` is the zero-sized unit (the empty struct's
            # twin) and `tuple<T>` the 1-tuple, so a future variadic's `T...`
            # expansions need no carve-out.
            base = self.tuple_type(
                tuple(self.lang_type(a, line) for a in ref.args), line
            )
        elif ref.name == "result":
            if len(ref.args) not in (1, 2):
                raise LangError(
                    f"type 'result' takes 1 or 2 type arguments, "
                    f"got {len(ref.args)}",
                    line,
                )
            base = self.result_type(
                tuple(self.lang_type(a, line) for a in ref.args), line
            )
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
            if any(strip_const(a) is VOID for a in args):
                # Rejected up front: a void field would otherwise surface as
                # a raw LLVM verifier error instead of a compile error.
                raise LangError(
                    f"struct {ref.name!r} cannot take void as a type argument",
                    line,
                )
            if len(args) < total:
                bindings = dict(zip(decl.type_params, args))
                self.fill_default_bindings(decl, bindings, line)
                args = tuple(bindings[t] for t in decl.type_params)
            base = self.instantiate_struct(decl, args, line)
        elif (enum := self.lookup_enum(ref.name)) is not None:
            label = "error" if is_error_decl(enum.underlying) else "enum"
            if ref.args:
                raise LangError(f"{label} {ref.name!r} is not generic", line)
            self.check_access(
                enum.private, enum.source, f"{label} {ref.name!r}", line
            )
            base = enum.underlying
        elif (alias := self.lookup_alias(ref.name)) is not None:
            self.check_access(
                alias.private, alias.source, f"type alias {ref.name!r}", line
            )
            # Arity is checked at the use site (a bare generic alias or a wrong
            # argument count is an error, replacing the old blanket "not
            # generic"). A shorter list is allowed only when the omitted tail is
            # entirely defaulted -- defaults are trailing, so the count suffices.
            total = len(alias.type_params)
            required = total - len(alias.type_param_defaults)
            if total == 0:
                if ref.args:
                    raise LangError(
                        f"type alias {ref.name!r} is not generic", line
                    )
            elif not required <= len(ref.args) <= total:
                expected = (
                    f"between {required} and {total}"
                    if alias.type_param_defaults
                    else f"{total}"
                )
                raise LangError(
                    f"type alias {ref.name!r} expects {expected} "
                    f"type argument(s), got {len(ref.args)}",
                    line,
                )
            if ref.name in self.resolving_aliases:
                raise LangError(
                    f"type alias {ref.name!r} refers to itself (cyclic alias)", line
                )
            # Resolve the arguments in the *use-site* context first (outer
            # bindings and source in scope), then hand over: the target resolves
            # with only the alias's own parameters bound, so an outer generic's
            # same-named parameter never leaks in. `bindings` is *replaced*, not
            # merged (save/restore, like instantiate_struct).
            args = tuple(self.lang_type(a, line) for a in ref.args)
            bindings = dict(zip(alias.type_params, args))
            if len(args) < total:
                self.fill_default_bindings(alias, bindings, line)
            self.resolving_aliases.add(ref.name)
            outer_source = self.current_source
            outer_bindings = self.type_bindings
            self.current_source = alias.source  # target may name private types
            self.type_bindings = bindings
            try:
                # The target resolves at the alias's own declaration site, so
                # an error (or backtrace frame) inside it pairs the declaring
                # file with a line in that file, not the use site's.
                base = self.lang_type(alias.target, alias.line)
            except LangError as err:
                # An error in the target belongs to the alias's file; then a
                # backtrace frame for the alias itself, so a chain that goes
                # through e.g. `string` (= list<char>) names `string`, and a
                # generic alias renders its arguments (`entry<int32>`).
                if err.source is None:
                    err.source = alias.source
                note_name = ref.name
                if args:
                    note_name += "<" + ", ".join(str(a) for a in args) + ">"
                err.notes.append(Note(f"in instantiation of {note_name}", line, outer_source))
                raise
            finally:
                self.resolving_aliases.discard(ref.name)
                self.current_source = outer_source
                self.type_bindings = outer_bindings
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

    def resolve_base(self, decl: "StructDecl | UnionDecl") -> "LangType | None":
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
        if not is_aggregate(base_type):
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
        if is_tuple(base_type):
            # Tuples are not named types (a slice, by contrast, is
            # extendable); declare a struct with the element types as fields
            # to name the shape.
            raise LangError(
                f"a tuple cannot be extended, but {decl.name!r} extends "
                f"{base_type}; declare the fields as a struct instead",
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

    def tuple_type(self, elements: tuple, line: int) -> LangType:
        """Build (and intern) the builtin ``tuple<A, B, ...>`` product type.

        A tuple is a heterogeneous, fixed-arity product realized as an
        ordinary struct with positional field names ``"0"``, ``"1"``, ... --
        so the GEP/member machinery, ``sizeof``, by-value passing, and
        hidden-reference ``const`` parameters all reuse the struct machinery
        -- tagged with the reserved template name ``"tuple"`` (see
        :func:`is_tuple`). Instances are interned per element list alongside
        the user structs in ``struct_types``: two same-shape tuples are the
        same ``LangType`` object, and a tuple never enters the nominal
        ``extends`` lineage (``base`` stays ``None``). The interning key is
        the canonical name ``TypeRef.__str__`` renders, so ``any`` tags and
        ``.mci`` stubs agree on one spelling.

        Elements are arbitrary types (padding, over-aligned structs), so the
        body goes through :meth:`set_struct_body` -- never a direct
        ``set_body`` like the fixed-shape slice. Any arity works, 0 and 1
        included: ``tuple<>`` is a zero-sized unit on the empty-struct
        precedent, and a future variadic's ``T...`` may produce either.

        Args:
            elements: The element types, in position order.
            line: Source line for diagnostics.

        Returns:
            The cached or newly built ``tuple<...>`` ``LangType``.

        Raises:
            LangError: On a ``void`` element.
        """
        mangled = "tuple<" + ", ".join(str(e) for e in elements) + ">"
        cached = self.struct_types.get(mangled)
        if cached is not None:
            return cached
        for element in elements:
            if strip_const(element) == VOID:
                raise LangError("cannot make a tuple of void", line)
        fields = tuple((str(i), e) for i, e in enumerate(elements))
        identified = self.module.context.get_identified_type(mangled)
        tuple_t = LangType(
            mangled, identified, signed=False, template="tuple",
            args=tuple(elements),
        )
        object.__setattr__(tuple_t, "fields", fields)  # frozen; excluded from eq
        self.set_struct_body(tuple_t, identified)
        self.struct_types[mangled] = tuple_t
        return tuple_t

    def result_type(self, args: tuple, line: int) -> LangType:
        """Build (and intern) the builtin ``result<T, E>`` / ``result<E>`` type.

        A result carries either the ok value or the error -- never both, never
        neither. It is realized as a struct ``{ tag: uint8, payload }`` (so
        ``sizeof``, by-value passing and returning, and ``const``-parameter
        hidden references reuse the struct machinery), tagged with the
        reserved template name ``"result"`` (see :func:`is_result`); tag 0 is
        the ok arm, 1 the error arm. The two-argument form's payload is an
        internal union of ``T`` and ``E`` (clang-style storage: the
        widest-aligned arm plus pad, exactly as a user ``union`` lays out);
        the error-only ``result<E>`` -- the language has no ``void`` type
        argument -- stores ``E`` directly. Unlike a slice or tuple the fields
        are not a surface: member access rejects (:meth:`struct_field`), so
        ``ok(...)``/``error(...)`` construction is the only producer.

        ``E`` must be a declared ``error`` type, and both arguments
        canonicalize through :func:`strip_const` (a result hands out copies,
        so a ``const`` axis would distinguish nothing). Instances are interned
        per argument list alongside the user structs in ``struct_types``.

        Args:
            args: One or two type arguments -- ``(T, E)`` or ``(E,)``.
            line: Source line for diagnostics.

        Returns:
            The cached or newly built ``result<...>`` ``LangType``.

        Raises:
            LangError: When ``E`` is not an error declaration, or ``T`` is
                ``void``.
        """
        args = tuple(strip_const(a) for a in args)
        err = args[-1]
        if not is_error_decl(err):
            raise LangError(
                f"result's error type must be an error declaration, got {err}",
                line,
            )
        if len(args) == 2 and args[0] is VOID:
            raise LangError(
                "result has no void arm; a function that can only fail "
                f"returns result<{err}>",
                line,
            )
        mangled = "result<" + ", ".join(str(a) for a in args) + ">"
        cached = self.struct_types.get(mangled)
        if cached is not None:
            return cached
        if len(args) == 2:
            # The payload union: the ok and error arms share one storage at
            # offset 0. Its LangType is a real union (types.py's union sizing
            # arms apply), bodied clang-style as the widest-aligned arm plus
            # pad -- the dual-site layout invariant's IR half, mirroring
            # instantiate_struct's union arm.
            u_fields = (("ok", args[0]), ("error", err))
            u_ident = self.module.context.get_identified_type(
                f"{mangled}.payload"
            )
            payload = LangType(
                f"{mangled}.payload", u_ident, signed=False, union=True
            )
            object.__setattr__(payload, "fields", u_fields)
            rep = max(
                (ftype for _, ftype in u_fields),
                key=lambda t: (type_align(t), type_size(t)),
            )
            elements = [rep.ir]
            pad = type_size(payload) - type_size(rep)
            if pad:
                elements.append(ir.ArrayType(ir.IntType(8), pad))
            if over_aligned(payload):
                u_ident.packed = True
            u_ident.set_body(*elements)
            object.__setattr__(payload, "elem_indices", (0, 0))
            fields = (("tag", UINT8), ("payload", payload))
        else:
            # Error-only: the payload is E itself (a later layout optimization
            # may fold the tag into E's reserved zero state; not yet).
            fields = (("tag", UINT8), ("error", err))
        identified = self.module.context.get_identified_type(mangled)
        result_t = LangType(
            mangled, identified, signed=False, template="result", args=args
        )
        object.__setattr__(result_t, "fields", fields)  # frozen; excluded from eq
        self.set_struct_body(result_t, identified)
        self.struct_types[mangled] = result_t
        return result_t

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

    def check_boxable(
        self, lang_type: LangType, line: int, *, borrow: bool = False
    ) -> LangType:
        """Validate a type against the ``any`` boxable set.

        The by-value set is primitives (the integers, ``bool``, ``char``,
        ``float64``), pointers (each pointer type gets its own tag), and
        slices. A **struct** additionally boxes -- but only ``borrow=True``,
        the call-scoped by-hidden-reference case: the payload holds a pointer
        to the caller's storage (see :meth:`gen_box_any`), so the target must
        be a ``const any`` slot that cannot outlive it (the ``slice<const
        any>`` a variadic collects into). An **owning** struct box, a union,
        or a fixed array is rejected -- by value the payload is unbounded, by
        pointer the lifetime goes implicit, so ``&value`` is the explicit
        escape. An ``any`` never boxes another ``any`` (``any`` to ``any`` is
        a plain copy, not nesting).

        Args:
            lang_type: The candidate type (possibly ``const``-qualified).
            line: Source line for diagnostics.
            borrow: Whether the box target is a call-scoped ``const any``
                by-reference position, which lets a struct box by hidden
                reference instead of being rejected.

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
            # A struct boxes by hidden reference into a call-scoped `const any`
            # (a variadic argument); an owning any of it would escape the
            # borrow, so that stays rejected with the pointer escape hatch.
            if borrow:
                return lang_type
            raise LangError(
                f"cannot box a {lang_type} into an owning any; a struct only "
                "boxes by reference into a const any (e.g. a variadic "
                "argument), or box a pointer to it (&value) instead",
                line,
            )
        if is_aggregate(lang_type):
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

    def gen_box_any(
        self,
        tv: TypedValue,
        line: int,
        *,
        borrow: bool = False,
        ref: "ir.Value | None" = None,
    ) -> TypedValue:
        """Box a value into a fresh ``any``: store the tag, then the payload.

        The box is assembled in a stack slot -- zero-filled first, so the
        payload bytes past the value are deterministic -- and loaded back as
        the 24-byte ``any`` value. A scalar/pointer/slice payload slot is
        reinterpreted as the source type by a pointer cast, the same move a
        union member store makes. A **struct** (only with ``borrow=True``)
        boxes by hidden reference instead: the payload holds a *pointer* to
        the value's storage -- the caller's own storage when ``ref`` supplies
        it (no copy), otherwise a call-scoped temporary the rvalue spills into
        -- tagged as the struct type itself (``point``, not ``point*``), so
        ``case type`` recovers it as a reference with no copy. The GEP indices
        here, the constant word layout in :meth:`const_box_any`, and the
        layout in ``types.ANY`` are the three sites of the layout invariant.

        Args:
            tv: The value to box (validated via :meth:`check_boxable`; an
                array must have been rejected before it decayed).
            line: Source line for diagnostics.
            borrow: Whether the box target is a call-scoped ``const any``
                by-reference position (lets a struct box by hidden reference).
            ref: For a struct box, a pointer to the value's existing storage
                to share directly; ``None`` spills the value to a temporary.

        Returns:
            The boxed ``any`` as a ``TypedValue``.
        """
        if tv.decayed is not None:
            # The value is the pointer an array decayed to; reject by the
            # array type, not the pointer it silently became.
            self.check_boxable(tv.decayed, line, borrow=borrow)
        boxed = self.check_boxable(tv.type, line, borrow=borrow)
        tag = self.any_tag(boxed, line)
        # Feed the boxed-only registry. Every runtime boxing site funnels
        # through this method (the coerce choke point and the variadic-extras
        # collection are its only callers) and every constant one through
        # const_box_any, so the registry records exactly the types a generic
        # case-type arm can ever see at runtime.
        self.boxed_types.setdefault(tag, boxed)
        slot = self.entry_alloca(ANY.ir, "any.box")
        self.builder.store(ir.Constant(ANY.ir, None), slot)  # zero-fill first
        tag_ptr = self.builder.gep(slot, [I32_ZERO, I32_ZERO], inbounds=True)
        self.builder.store(ir.Constant(UINT64.ir, tag), tag_ptr)
        payload_ptr = self.builder.gep(
            slot, [I32_ZERO, ir.Constant(ir.IntType(32), 1)], inbounds=True
        )
        if is_aggregate(boxed) and not is_slice(boxed):
            # Box by hidden reference: a pointer fits the 16-byte payload. An
            # rvalue with no storage of its own spills to a call-scoped temp.
            # A slice never takes this arm: its 16 bytes fill the payload
            # exactly, and the by-value box is what lets an owning any (or a
            # return) carry it past the boxing frame.
            if ref is None:
                ref = self.entry_alloca(boxed.ir)
                if over_aligned(boxed):
                    ref.align = type_align(boxed)
                self.builder.store(tv.value, ref)
            slot_ptr = self.builder.bitcast(
                payload_ptr, boxed.ir.as_pointer().as_pointer()
            )
            self.builder.store(ref, slot_ptr)
        else:
            typed_ptr = self.builder.bitcast(payload_ptr, boxed.ir.as_pointer())
            self.builder.store(tv.value, typed_ptr)
        return TypedValue(self.builder.load(slot), ANY)

    def instantiate_struct(
        self, decl: "StructDecl | UnionDecl", args: tuple[LangType, ...], line: int
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
        # Record the resolved immediate base so the nominal subtype relation can
        # walk this instance's declared `extends` lineage (see
        # :meth:`nominal_subtype`). `base_type` is concrete here even for a bare
        # parameter (`struct entry<T> extends T`) or a generic base
        # (`extends pair<K, V>`), since it resolved against this instance's
        # bindings above; `None` when the struct extends nothing.
        object.__setattr__(struct_type, "base", base_type)
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
        self.set_struct_body(struct_type, identified)
        return struct_type

    def set_struct_body(self, struct_type: LangType, identified) -> None:
        """Body a struct's LLVM identified type from its resolved fields.

        The IR half of the dual-site layout invariant: the body built here
        must agree with the fields-driven ``type_size()``/``type_align()``/
        ``field_offset()`` computations in types.py. A ``@packed`` or
        over-aligned struct (an ``@align`` here or on a nested field) departs
        from LLVM's natural rules, so its layout is spelled out with explicit
        padding; everything else takes LLVM's natural layout. Shared by user
        structs (:meth:`instantiate_struct`) and builtin tuples
        (:meth:`tuple_type`); ``struct_type.fields`` must already be set.

        Args:
            struct_type: The struct ``LangType``, its ``fields`` resolved;
                its ``elem_indices`` are recorded here.
            identified: The opaque LLVM identified type to body.
        """
        fields, packed = struct_type.fields, struct_type.packed
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

    def nominal_subtype(self, base: LangType, derived: LangType) -> bool:
        """Whether ``derived`` *is* ``base`` or reaches it up its ``extends`` chain.

        The nominal struct subtype relation. A struct participates only when it
        is the target itself or names it -- transitively -- through a declared
        ``extends`` clause, the immediate base recorded per instantiation on
        :attr:`LangType.base` by :meth:`instantiate_struct`. This walks
        ``derived``'s recorded base chain, comparing each hop to ``base`` by
        nominal identity: struct instances are interned per mangled name in
        ``struct_types``, so two types are the same brand exactly when they
        compare equal. The chain ends at ``None`` (a root struct, e.g.
        ``slice<T>``). A union never participates: its members share offset 0,
        so a shared brand would not mean a shared layout.

        ``const`` is stripped on both sides so a value read out of a ``const``
        lvalue still upcasts, and to mirror the borrow site, which strips
        ``const`` off the element before forming the target ``slice<T>`` -- so a
        ``list<T>`` borrows to both ``slice<T>`` and ``slice<const T>``.

        This replaces the structural ``is_struct_prefix`` that predated
        ``extends``. The prefix layout stays the mechanism -- base fields first,
        so the upcast is still a zero-cost reinterpret and the borrow still reads
        ``{data, length}`` straight across -- but the declared lineage, not a
        coincidentally matching field prefix, now decides participation.
        """
        if not is_struct(base) or not is_struct(derived):
            # ``is_struct`` is record-only, so a union base or derived is already
            # rejected here -- the prefix-upcast relation never spans a union.
            return False
        target = strip_const(base)
        current: "LangType | None" = strip_const(derived)
        while current is not None:
            if strip_const(current) == target:
                return True
            current = current.base
        return False

    def field_type(
        self, ftype: TypeRef, is_last: bool, decl: "StructDecl | UnionDecl"
    ) -> LangType:
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
        if not is_aggregate(owner):
            raise LangError(f"{owner} is not a struct", line)
        if is_result(owner):
            # The tag/payload layout is internal: a result is built by
            # ok(...)/error(...) only, never read a field at a time.
            raise LangError(f"a {owner} has no fields", line)
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

    def reconcile_overrides(self):
        """Resolve ``@override`` before signature registration.

        An ``@override`` replaces a same-pattern member of an open overload
        set declared in *another* module: the overridden (unannotated)
        definition is dropped from ``self.program.functions`` so only the
        override's body is emitted, under the member's shared mangled symbol
        (an override is public, so its symbol is unsalted and equals the
        target's). Runs before the registration loop -- and the emission
        pass, which both iterate ``self.program.functions`` -- so replacement
        is order-independent: the winner is chosen over the whole merged
        program, not the import prefix seen so far, and a no-match override
        is judged once the whole set is known.

        Each override needs exactly one source-visible, body-bearing,
        cross-module target of the same pattern. A second ``@override`` of one
        pattern, no target (typo), a same-file target, or a target that is
        only a prototype (its body lives in another object that already
        defines the symbol) is a compile error.

        Raises:
            LangError: On any of the above.
        """
        if not any(func.override for func in self.program.functions):
            return

        def pattern(func: Func):
            # Concrete -> the mangle's parameter list; template -> the
            # order-independent base (the same identities the duplicate
            # checks compare). @static/@extern/@removed never join a
            # cross-module set, so they are excluded before this is called.
            if func.type_params:
                return ("t", self.template_base(func))
            self.current_source = func.source
            return ("c", ", ".join(
                str(self.lang_type(t, func.line)) for _, t in func.params
            ))

        losers: set[int] = set()
        for func in self.program.functions:
            if not func.override:
                continue
            pat = pattern(func)
            others = [
                f
                for f in self.program.functions
                if f is not func
                and f.name == func.name
                and not f.extern
                and not f.static
                and f.removed_msg is None
                and pattern(f) == pat
            ]
            twin = next((f for f in others if f.override), None)
            if twin is not None:
                # Two @override of one pattern collide like any duplicate.
                err = LangError(
                    f"function {func.name!r} has two @override definitions of "
                    "one overload pattern; at most one may replace it",
                    func.line,
                )
                err.notes.append(
                    Note(
                        f"the other @override of {func.name!r} is here",
                        twin.line,
                        twin.source,
                    )
                )
                raise err
            targets = [
                f
                for f in others
                if not f.private and f.source != func.source and not f.proto
            ]
            if not targets:
                if any(f.source == func.source and not f.proto for f in others):
                    raise LangError(
                        f"@override function {func.name!r} matches a "
                        "same-pattern definition in its own file; @override "
                        "replaces a member declared in another module, not a "
                        "local one",
                        func.line,
                    )
                if any(f.proto and f.source != func.source for f in others):
                    raise LangError(
                        f"cannot @override {func.name!r}: its definition is "
                        "not source-visible (only a prototype is in scope, so "
                        "the body lives in another object that already "
                        "defines the symbol)",
                        func.line,
                    )
                raise LangError(
                    f"@override function {func.name!r} matches no existing "
                    "overload to replace",
                    func.line,
                )
            losers.update(id(f) for f in targets)
        if losers:
            self.program.functions = [
                f for f in self.program.functions if id(f) not in losers
            ]

    def struct_arg_is_param(self, ref: TypeRef, line: int) -> bool:
        """Whether a method's pre-``::`` struct argument is a fresh parameter.

        A fresh type-parameter *name* (``True``) is a bare identifier that
        resolves to no known type; anything else -- a builtin, a user struct,
        a structured ``point<int32>`` / ``int32*``, or any other resolvable
        spelling -- is a concrete *type* (``False``).

        Args:
            ref: One argument from a method's ``struct_type_args`` list.
            line: Source line for diagnostics.

        Returns:
            ``True`` when the argument is a fresh type-parameter name.
        """
        # Any structure -- generic arguments, a pointer, an array dimension, a
        # const/@nonnull/mut qualifier, or a function-pointer type -- is a
        # concrete type spelling, never a bare parameter name.
        if (
            ref.args
            or ref.stars
            or ref.dims
            or ref.const
            or ref.nonnull
            or ref.mut
            or ref.params is not None
        ):
            return False
        # A bare name is a parameter exactly when it names no known type.
        try:
            self.lang_type(ref, line)
            return False
        except LangError:
            return True

    def subst_struct_args(self, ref: TypeRef, binding: dict[str, TypeRef]) -> TypeRef:
        """Substitute a specialization's struct parameter names in a type.

        The struct's declared parameter names (``T`` in ``struct point<T>``)
        bind to the specialization's concrete arguments, so a signature written
        with ``point<T>`` or a bare ``T`` resolves to the concrete
        instantiation -- mirroring how a generic instance substitutes.

        Args:
            ref: A parameter or return ``TypeRef`` from the specialization.
            binding: ``{struct parameter name: concrete TypeRef}``.

        Returns:
            The type with the bound names replaced.
        """
        if ref.params is not None:  # a fn(...) -> ret function-pointer type
            return dataclasses_replace(
                ref,
                params=[self.subst_struct_args(p, binding) for p in ref.params],
                ret=(
                    self.subst_struct_args(ref.ret, binding)
                    if ref.ret is not None
                    else None
                ),
            )
        if ref.name in binding:
            # A bare parameter name carries no arguments of its own; keep any
            # pointer depth, array dimensions, and qualifiers written on it.
            sub = binding[ref.name]
            return dataclasses_replace(
                sub,
                stars=sub.stars + ref.stars,
                dims=sub.dims + ref.dims,
                const=sub.const or ref.const,
                nonnull=sub.nonnull or ref.nonnull,
                mut=sub.mut or ref.mut,
            )
        return dataclasses_replace(
            ref, args=[self.subst_struct_args(a, binding) for a in ref.args]
        )

    def check_struct_arg_decorations(
        self, func: Func, args: list[TypeRef], fresh: list[bool]
    ):
        """Validate the decorations on a method's pre-``::`` struct arguments.

        The parser captures a ``:`` type group, an ``extends`` bound, or a
        ``=`` default written on a bare pre-``::`` name, but cannot judge them:
        which names are fresh type parameters is only known here, against the
        registered type environment. A decoration is legal only on a fresh
        name, and the decorated list must satisfy the same declaration shape
        :meth:`Parser.parse_type_params` enforces at parse time -- trailing
        defaults, defaults referencing only earlier parameters (and, for a
        grouped parameter, no parameter at all), and group members and bound
        targets referencing no parameter -- reported with the same messages.

        Args:
            func: The method whose ``struct_arg_*`` decorations to validate.
            args: Its ``struct_type_args`` list.
            fresh: Per-argument classification from :meth:`struct_arg_is_param`.

        Raises:
            LangError: On a decorated concrete argument or any failed
                declaration-shape check.
        """
        groups = func.struct_arg_groups
        bounds = func.struct_arg_bounds
        defaults = func.struct_arg_defaults
        if not (groups or bounds or defaults):
            return
        decorated = set(groups) | set(bounds) | set(defaults)
        # A repeated fresh name (a duplicate-position alias's diagonal
        # expansion) declares one parameter; check it once, in first position.
        fresh_names = list(dict.fromkeys(a.name for a, f in zip(args, fresh) if f))
        for a, f in zip(args, fresh):
            # The parser only decorates a bare name, so a concrete argument
            # whose bare spelling carries a decoration is the decorated entry
            # itself (`fn pair<int32: int8 | int16, U>::m`).
            if not f and not (a.args or a.stars) and a.name in decorated:
                raise LangError(
                    f"struct type argument {a.name!r} names a concrete type; "
                    "a type group, 'extends' bound, or default may only "
                    "decorate a fresh type parameter",
                    func.line,
                )
        # The declaration-shape checks parse_type_params runs, over the fresh
        # names only (concrete arguments are not parameters: a default may
        # precede one, and members may spell one).
        last_default = None
        for name in fresh_names:
            if name in defaults:
                last_default = name
            elif last_default is not None:
                raise LangError(
                    f"type parameter {name!r} without a default cannot "
                    "follow a defaulted one",
                    func.line,
                )
        for i, pname in enumerate(fresh_names):
            ref = defaults.get(pname)
            if ref is None:
                continue
            bad = type_ref_names(ref) & set(fresh_names[i:])
            if bad:
                raise LangError(
                    f"default for type parameter {pname!r} references "
                    f"{min(bad)!r}, which is not declared before it",
                    func.line,
                )
        for pname, members in groups.items():
            for member in members:
                bad = type_ref_names(member) & set(fresh_names)
                if bad:
                    raise LangError(
                        f"type group member {member} for parameter "
                        f"{pname!r} references type parameter "
                        f"{min(bad)!r}; group members must be concrete "
                        "types",
                        func.line,
                    )
            ref = defaults.get(pname)
            if ref is not None:
                bad = type_ref_names(ref) & set(fresh_names)
                if bad:
                    raise LangError(
                        f"default for type parameter {pname!r} references "
                        f"{min(bad)!r}; a grouped parameter's default "
                        "must name a group member",
                        func.line,
                    )
        for pname, ref in bounds.items():
            bad = type_ref_names(ref) & set(fresh_names)
            if bad:
                raise LangError(
                    f"bound {ref} for type parameter {pname!r} references "
                    f"type parameter {min(bad)!r}; a bound must be a "
                    "concrete struct",
                    func.line,
                )

    def merge_struct_arg_decorations(self, func: Func):
        """Fold validated pre-``::`` decorations into the method's own maps.

        Struct-parameter decorations merge before the method's own, mirroring
        the ``{**struct, **method}`` order of the old parse-time merge (the
        shadow checks already guarantee the key sets are disjoint). After
        this, a bounded/grouped/defaulted struct parameter is
        indistinguishable from one declared on the method itself -- ranking
        (a group or bound lifts the template to the bounded tier), overlap
        checks, and ``.mci`` dependency scans all see it in the usual fields.

        Args:
            func: The method whose ``struct_arg_*`` maps to merge.
        """
        func.type_param_defaults = {
            **func.struct_arg_defaults,
            **func.type_param_defaults,
        }
        func.type_param_groups = {
            **func.struct_arg_groups,
            **func.type_param_groups,
        }
        func.type_param_bounds = {
            **func.struct_arg_bounds,
            **func.type_param_bounds,
        }

    def resolve_method_qualifier(self, func: Func):
        """Resolve a method's ``Type::`` qualifier, canonicalizing aliases.

        Methods register to a TYPE, and a type alias is just an alias:
        ``fn pointf::m`` with ``type pointf = point<float64>`` registers to
        the family ``point::m`` exactly as ``fn point<float64>::m`` would --
        the alias qualifier is chased (access-checked per hop, file-scoped
        ``@static`` aliases included) until it lands on a struct or a builtin
        type name, and the method's name and pre-``::`` type arguments are
        rewritten to the canonical spelling:

          * a plain alias contributes its target's type arguments
            (``pointf::m`` becomes ``point<float64>::m`` -- a specialization);
          * a generic alias applied to written pre-``::`` arguments
            substitutes them through its target, defaults honored
            (``fn swap<int32, U>::m`` with ``type swap<X, Y> = pair<Y, X>``
            becomes the partial ``fn pair<U, int32>::m``; a repeated
            parameter, as in ``type diag<T> = pair<T, T>``, repeats the fresh
            name -- the diagonal constraint);
          * a BARE generic alias qualifier is an error: a method declaration
            must annotate a generic qualifier's type parameters (``fn pf::m``
            with ``type pf<T> = point<T>`` demands ``fn pf<T>::m`` or
            ``fn pf<float64>::m``). A fully-DEFAULTED generic alias is the
            exception, exactly as in a type use: the bare name is a complete
            type (the tail fills from the defaults), so ``fn pf::m`` with
            ``type pf<T = float64> = point<T>`` is ``fn point<float64>::m``.

        The same annotation rule holds when the chase lands on a generic
        STRUCT with no arguments supplied along the way: bare ``fn point::m``
        (or ``fn pf::m`` through ``type pf = point``) is the error unless
        every struct parameter defaults, in which case the defaults fill and
        the method is the specialization at them. The method's own type
        parameters (``fn point::m<W>``) never satisfy the requirement -- the
        qualifier itself must be annotated.

        A builtin type name (``fn int32::m``) is accepted as-is: the family
        is the name string, with nothing more to resolve. Anything else --
        an enum, an undeclared name, an alias cycle, or an alias of a type
        with no bare-name spelling (a pointer, array, or function type) --
        keeps the ``no struct type`` error, reported against the qualifier
        as written.

        Args:
            func: A method (its name contains ``::``); ``current_source``
                must already be its file.

        Raises:
            LangError: On an unresolvable qualifier, a cross-file ``@private``
                alias, written type arguments on a non-generic alias, a
                generic-alias arity mismatch, or a bare generic qualifier
                whose parameters do not all default.
        """
        qualifier, method = func.name.split("::", 1)
        name = qualifier
        args = func.struct_type_args
        seen: set[str] = set()
        while (
            self.lookup_struct_decl(name) is None
            and name not in TYPES
            and name not in RESERVED_TYPE_NAMES
        ):
            alias = self.lookup_alias(name)
            if alias is None or name in seen:
                raise LangError(
                    f"no struct type {qualifier!r} for method {func.name!r}",
                    func.line,
                )
            seen.add(name)
            self.check_access(
                alias.private, alias.source, f"type alias {name!r}", func.line
            )
            target = alias.target
            if (
                target.stars
                or target.dims
                or target.const
                or target.nonnull
                or target.mut
                or target.params is not None
            ):
                # The target has no bare-name spelling to namespace on.
                raise LangError(
                    f"no struct type {qualifier!r} for method {func.name!r}",
                    func.line,
                )
            if alias.type_params:
                total = len(alias.type_params)
                required = total - len(alias.type_param_defaults)
                if args is None:
                    # A bare generic-alias qualifier must annotate its type
                    # parameters -- unless every parameter defaults, in which
                    # case the bare name is a complete type (the tail fills),
                    # exactly as a bare type use resolves.
                    if required:
                        raise LangError(
                            f"type alias {name!r} is generic; the method "
                            "qualifier must annotate its type parameter(s), "
                            f"e.g. 'fn {name}<T>::{method}' or "
                            f"'fn {name}<float64>::{method}'",
                            func.line,
                        )
                    args = []
                # Written arguments bind the alias's parameters and
                # substitute through its target; a shorter list fills
                # from trailing defaults, exactly as a type use does.
                if not required <= len(args) <= total:
                    expected = (
                        f"between {required} and {total}"
                        if alias.type_param_defaults
                        else f"{total}"
                    )
                    raise LangError(
                        f"type alias {name!r} expects {expected} "
                        f"type argument(s), got {len(args)}",
                        func.line,
                    )
                binding = dict(zip(alias.type_params, args))
                for pname in alias.type_params[len(args):]:
                    binding[pname] = self.subst_struct_args(
                        alias.type_param_defaults[pname], binding
                    )
                target = self.subst_struct_args(target, binding)
                args = list(target.args) if target.args else None
            else:
                if args is not None:
                    raise LangError(
                        f"type alias {name!r} is not generic", func.line
                    )
                if target.args:
                    args = list(target.args)
            name = target.name
        if args is None and (decl := self.lookup_struct_decl(name)) is not None:
            if decl.type_params:
                # A bare generic-struct qualifier must annotate its type
                # parameters (the method's own `<...>` list never satisfies
                # this -- the qualifier itself is what's bare). A fully
                # DEFAULTED struct is the exception, exactly as in a type
                # use: the bare name is a complete type, so the defaults
                # fill and the method is the specialization at them.
                if len(decl.type_params) > len(decl.type_param_defaults):
                    raise LangError(
                        f"struct {name!r} is generic; the method qualifier "
                        "must annotate its type parameter(s), e.g. "
                        f"'fn {name}<T>::{method}' or "
                        f"'fn {name}<float64>::{method}'",
                        func.line,
                    )
                binding: dict[str, TypeRef] = {}
                args = []
                for pname in decl.type_params:
                    arg = self.subst_struct_args(
                        decl.type_param_defaults[pname], binding
                    )
                    binding[pname] = arg
                    args.append(arg)
        if name != qualifier:
            func.name = f"{name}::{method}"
            func.alias_qualifier = qualifier
        func.struct_type_args = args

    def normalize_struct_method_args(self):
        """Classify each method's pre-``::`` struct type arguments.

        A method written ``fn Type<A, B>::m`` parses with its pre-``::`` list
        held verbatim as ``struct_type_args`` (see the parser); the choice of
        generic method vs specialization is made HERE, against the registered
        type environment (structs, aliases, and enums are all registered by
        now), so any concrete type may specialize a method:

          * every argument a fresh type-parameter NAME -> a generic method:
            the struct's parameters prepend the method's own into one uniform
            template, exactly as a merged ``fn Type<T>::m<U>`` did before.
          * every argument a concrete TYPE -> a specialization: an ordinary
            concrete overload of ``Type::m`` whose receiver, parameters, and
            return resolve with the struct's parameter names bound to those
            concrete arguments. It outranks the generic template for a matching
            receiver through the existing concrete-beats-generic ranking.
          * a MIX of the two -> a PARTIAL specialization
            (``fn pair<int32, U>::m``): the concrete positions bind their
            struct parameter names exactly like a full specialization, and the
            fresh names prepend the method's own type parameters exactly like
            a generic method -- yielding a template that matches only
            receivers whose concrete positions agree (``pair<int32, X>``).
            The existing rank tiers order the family with no new dispatch
            code: a full specialization (concrete, tier 2) beats a partial,
            and a partial's concrete positions score higher pattern
            specificity than the fully generic template's bare names, so it
            wins within tier 0. A *bounded* fresh name (a ``:`` group or
            ``extends`` bound rides in from the parser) lifts the partial to
            tier 1 -- and, symmetrically, a bounded fully-generic method
            outranks an UNbounded partial: a written commitment to a type set
            beats the open pattern, per the tier rule. Two rank-tied partials
            stay the standard ambiguity error.

        Decorations captured by the parser (``struct_arg_groups`` /
        ``struct_arg_bounds`` / ``struct_arg_defaults``) are validated here --
        only a fresh name may carry one -- with the same declaration-shape
        checks (trailing defaults, no parameter references in members, bounds,
        or defaults) and messages ``parse_type_params`` applies at parse time,
        then merge into the method's own ``type_param_*`` maps.

        Runs before every function-registration loop, so the rest of codegen
        sees only ordinary generic or concrete functions.

        Raises:
            LangError: On a specialization whose argument count does not match
                the struct's arity, a struct parameter name shadowed by a
                method's own type parameter, a partial's fresh name shadowing
                a concretely-bound struct parameter, a decoration on a
                concrete argument, or a decoration failing the declaration
                checks above.
        """
        for func in self.program.functions:
            args = func.struct_type_args
            if args is None:
                continue
            self.current_source = func.source
            qualifier = func.name.split("::", 1)[0]
            decl = self.lookup_struct_decl(qualifier)
            # gen_program resolved the qualifier before this pass, so `decl`
            # is only None for a BUILTIN type qualifier (`fn slice<T>::m`):
            # the family is the name string, with no declared parameters to
            # bind -- fresh names ride the generic path below unchanged, and
            # a concrete argument (nothing to specialize) is rejected.
            fresh = [self.struct_arg_is_param(a, func.line) for a in args]
            self.check_struct_arg_decorations(func, args, fresh)
            if all(fresh):
                # A generic method: the struct arguments are all parameter
                # names. Prepend them to the method's own parameters (order
                # matters -- it fixes the instance mangling) after the same
                # shadow check the merged parse-time path ran, and merge any
                # decorations struct-parameters-first, as that merge did.
                # A REPEATED name -- the diagonal constraint a duplicate-
                # position alias expands to (`type diag<T> = pair<T, T>`
                # makes `fn diag<U>::m` the target `pair<U, U>`) -- declares
                # one parameter: unification binds the first occurrence and
                # rejects a receiver whose later occurrences disagree.
                struct_names = list(dict.fromkeys(a.name for a in args))
                shadowed = set(struct_names) & set(func.type_params)
                if shadowed:
                    raise LangError(
                        f"method type parameter {min(shadowed)!r} shadows a type "
                        f"parameter of struct {qualifier!r}",
                        func.line,
                    )
                func.type_params = struct_names + func.type_params
                self.merge_struct_arg_decorations(func)
            elif decl is None:
                # A builtin type has no declared parameter names for a
                # concrete argument to bind: there is nothing a
                # specialization could substitute. (The signature alone
                # drives dispatch -- a concrete receiver type there already
                # outranks a generic pattern.)
                raise LangError(
                    f"cannot specialize builtin type {qualifier!r}; spell "
                    "the receiver type in the method's signature instead",
                    func.line,
                )
            elif not any(fresh):
                # A specialization: bind the struct's declared parameter names
                # to the concrete arguments and resolve the signature against
                # them. The method keeps only its OWN type parameters (none ->
                # an ordinary concrete overload).
                if len(args) != len(decl.type_params):
                    raise LangError(
                        f"specialization of struct {qualifier!r} expects "
                        f"{len(decl.type_params)} type argument(s), got "
                        f"{len(args)}",
                        func.line,
                    )
                # The interface writer re-spells a concrete specialization's
                # qualifier annotation in its stub prototype (a bare generic
                # qualifier would not re-parse), so keep the resolved list
                # past the clearing below.
                func.spec_qualifier_args = list(args)
                binding = {
                    pname: arg
                    for pname, arg in zip(decl.type_params, args)
                    if pname not in func.type_params
                }
                func.params = [
                    (pname, self.subst_struct_args(ptype, binding))
                    for pname, ptype in func.params
                ]
                func.ret_type = self.subst_struct_args(func.ret_type, binding)
            else:
                # A PARTIAL specialization: both of the above at once. The
                # concrete positions bind their struct parameter names (a full
                # specialization, restricted to those positions) and the fresh
                # names stay free, prepended to the method's own parameters (a
                # generic method, restricted to the rest) -- one template that
                # matches only receivers agreeing on the concrete positions.
                if len(args) != len(decl.type_params):
                    raise LangError(
                        f"specialization of struct {qualifier!r} expects "
                        f"{len(decl.type_params)} type argument(s), got "
                        f"{len(args)}",
                        func.line,
                    )
                fresh_names = list(
                    dict.fromkeys(a.name for a, f in zip(args, fresh) if f)
                )
                shadowed = set(fresh_names) & set(func.type_params)
                if shadowed:
                    raise LangError(
                        f"method type parameter {min(shadowed)!r} shadows a type "
                        f"parameter of struct {qualifier!r}",
                        func.line,
                    )
                # A fresh name may not reuse a struct parameter name that a
                # concrete position binds: in `struct pair<A, B>` with
                # `fn pair<int32, A>::m`, the signature's `A` must substitute
                # to int32 AND stand for the free parameter -- unsatisfiable,
                # so it is rejected like any other shadow.
                bound_names = {
                    pname for pname, f in zip(decl.type_params, fresh) if not f
                }
                captured = set(fresh_names) & bound_names
                if captured:
                    raise LangError(
                        f"type parameter {min(captured)!r} shadows a type "
                        f"parameter of struct {qualifier!r} bound to a "
                        "concrete type by the partial specialization",
                        func.line,
                    )
                binding = {
                    pname: arg
                    for pname, arg, f in zip(decl.type_params, args, fresh)
                    if not f and pname not in func.type_params
                }
                func.params = [
                    (pname, self.subst_struct_args(ptype, binding))
                    for pname, ptype in func.params
                ]
                func.ret_type = self.subst_struct_args(func.ret_type, binding)
                func.type_params = fresh_names + func.type_params
                self.merge_struct_arg_decorations(func)
            # Keep the classified annotation for method inheritance: rebasing
            # a base family member onto a deriving struct matches this
            # positional list against the `extends` clause's type arguments.
            func.qualifier_args = list(args)
            func.struct_type_args = None
            func.struct_arg_groups = {}
            func.struct_arg_bounds = {}
            func.struct_arg_defaults = {}

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
        # use), so a const's or signature's type -- or a method qualifier
        # below -- may name one.
        for alias in self.program.aliases:
            self.register_alias(alias)
        # A qualified method name `Type::method` namespaces the function to a
        # TYPE -- a struct or a builtin alike. Structs and aliases are fully
        # registered above, so resolve each qualifier here: an alias qualifier
        # canonicalizes to the type it names (registering a method for
        # `pointf` IS registering it for `point<float64>`, and vice versa),
        # and any qualifier that is an enum, an undeclared name, or an alias
        # of an unnameable type (a pointer, array, or function type) is the
        # error. `Type::` is purely a namespace: no `self` convention is
        # enforced.
        for func in self.program.functions:
            if "::" not in func.name:
                continue
            self.current_source = func.source
            self.resolve_method_qualifier(func)
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
                self.global_privacy[var.name] = (var.private, var.source)
                if var.init is not None and is_union(var_type):
                    # A union global is stored as its written member plus pad,
                    # not the union's own IR type, so its storage is created in
                    # the deferred pass once the constant's type is known.
                    deferred_static_inits.append((None, var, var_type))
                    continue
                symbol = self.static_base(var.name, var.source)
                glob = ir.GlobalVariable(self.module, var_type.ir, name=symbol)
                glob.linkage = self.shared_linkage(var.source)
                if var.init is not None:
                    deferred_static_inits.append((glob, var, var_type))
                else:
                    glob.initializer = ir.Constant(var_type.ir, None)
                self.static_globals[key] = (glob, var_type, var.volatile)
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
        # Concrete overloading: pre-group plain declarations by name (sets
        # are open -- the whole-program union) so the plain-vs-mangled symbol
        # choice is known before the first member declares. Prototypes
        # count -- a .mci stub's prototypes must derive the same symbols the
        # defining object emitted -- but a set forms only when the
        # declarations spell two or more DISTINCT parameter lists: a
        # same-signature prototype+definition pair is one member (the
        # classic forward declaration keeps its plain symbol). The symbol
        # choice is judged per declaring file:
        #   * a `.mci`-sourced declaration counts its own stub's signatures
        #     plus the public ones reachable through the stub's own import
        #     closure -- exactly what the already-compiled object it
        #     describes was built seeing. A consumer-side extension never
        #     re-derives a stub's pinned symbols;
        #   * a `.mc`-sourced declaration counts every signature visible to
        #     it: the whole-program union minus other modules' @private
        #     members (invisible, so they cannot flip a symbol choice).
        # A file whose visible set has a single signature keeps its plain,
        # C-linkable symbol; sets of two or more mangle. @extern, @static,
        # generics, and tombstones never join the concrete grouping.
        #
        # @override is reconciled first, over the whole merged program: it
        # drops each overridden (unannotated) definition from the function
        # list so the registration and emission loops below see only the
        # winner, under the member's shared mangled symbol. Running here
        # (before the visibility scan) makes replacement order-independent.
        #
        # A method's pre-`::` struct type arguments are classified first, so
        # the loops below see only ordinary generic methods (fresh names),
        # concrete specializations (concrete types), and partial
        # specializations (a mix, normalized to a generic template with the
        # concrete positions bound) -- never the raw, unclassified
        # `struct_type_args` form the parser produced.
        self.normalize_struct_method_args()
        self.reconcile_overrides()
        plain_decls: dict[str, list[Func]] = {}
        for func in self.program.functions:
            if (
                func.removed_msg is None
                and not func.extern
                and not func.static
                and not func.type_params
            ):
                plain_decls.setdefault(func.name, []).append(func)
        overload_keys: set[tuple[str | None, str]] = set()
        for name, decls in plain_decls.items():
            if len(decls) < 2:
                continue
            spelled: list[tuple[Func, tuple[str, ...]]] = []
            for f in decls:
                # Per declaration: its signature may name @private structs.
                self.current_source = f.source
                spelled.append(
                    (f, tuple(str(self.lang_type(t, f.line)) for _, t in f.params))
                )
            public_sigs = {sig for f, sig in spelled if not f.private}
            by_source: dict[str | None, set[tuple[str, ...]]] = {}
            for f, sig in spelled:
                by_source.setdefault(f.source, set()).add(sig)
            for source, own_sigs in by_source.items():
                if source is not None and source.endswith(".mci"):
                    closure = self.import_closure(source)
                    visible = own_sigs | {
                        sig
                        for f, sig in spelled
                        if not f.private and f.source in closure
                    }
                else:
                    visible = own_sigs | public_sigs
                if len(visible) > 1:
                    overload_keys.add((source, name))
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
                # Always fails for an @extern (it never collects); run for
                # the diagnostic.
                self.check_format_decl(func, params)
                self.check_noreturn_decl(func, ret)
                if func.name in self.extern_decls:
                    if self.signatures[func.name] != (
                        ret, params, func.variadic
                    ) or (func.name in self.noreturn_syms) != func.noreturn:
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
                plan = self.declare_extern_abi(func, ret, params)
                sret = plan is not None and isinstance(plan.ret, Indirect)
                fnty = ir.FunctionType(
                    self.extern_ret_ir(ret, plan),
                    self.extern_param_irs(params, plan),
                    var_arg=func.variadic,
                )
                # @symbol overrides the linker name; mcc still calls it by func.name.
                fn = ir.Function(self.module, fnty, name=func.symbol or func.name)
                if sret:
                    # The struct return travels through a hidden first pointer
                    # the caller allocates; the arg indices for the real
                    # parameters (and their attributes) shift past it.
                    fn.args[0].add_attribute("sret")
                    fn.args[0].attributes.align = plan.ret.align
                arg_offset = 1 if sret else 0
                if plan is not None:
                    # An x86-64 System V MEMORY argument is a `byval(T) align N`
                    # pointer: the struct data is copied onto the argument stack
                    # (AArch64/Win64 indirect args stay plain pointers instead).
                    for i, cls in enumerate(plan.args):
                        if isinstance(cls, Indirect) and cls.by_value:
                            a = fn.args[i + arg_offset]
                            a.add_attribute("byval")
                            a.attributes.align = cls.align
                # @noalias/@nonnull are attribute-only, so allowed on @extern.
                self.mark_noalias(fn, func, params, arg_offset)
                self.mark_nonnull(fn, func, params, arg_offset)
                self.mark_noreturn(fn, func)
                if func.noreturn:
                    self.noreturn_syms.add(func.name)
                self.funcs[func.name] = fn
                self.signatures[func.name] = (ret, params, func.variadic)
                self.nonnull_ref[func.name] = self.nonnull_indices(func)
                self.func_privacy[func.name] = (func.private, func.source)
                self.extern_decls.add(func.name)
                if plan is not None:
                    self.extern_abi[func.name] = plan
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
                    # A @static template is file-scoped and never joins an
                    # overload set, so no overlap check -- but its groups
                    # still resolve, filter calls, and get the eager check.
                    self.check_group_decl(func)
                    self.check_bound_decl(func)
                    self.static_templates[key] = func
                    continue
                symbol = self.symbol_bases[key]
                ret = self.lang_type(func.ret_type, func.line)
                params = [self.lang_type(t, func.line) for _, t in func.params]
                self.check_collecting_decl(func, params)
                self.check_format_decl(func, params)
                self.check_noreturn_decl(func, ret)
                self.check_mut_return_decl(func, ret)
                hidden = self.hidden_ref_indices(func, params)
                fnty = ir.FunctionType(
                    self.ret_ir(func, ret),
                    self.param_irs(params, hidden),
                    var_arg=func.variadic,
                )
                fn = ir.Function(self.module, fnty, name=symbol)
                self.link_shared(fn, func.source)
                self.mark_inline(fn, func)
                self.mark_noalias(fn, func, params)
                self.mark_nonnull(fn, func, params)
                self.mark_noreturn(fn, func)
                if func.noreturn:
                    self.noreturn_syms.add(symbol)
                self.funcs[symbol] = fn
                self.signatures[symbol] = (ret, params, func.variadic)
                self.hidden_ref[symbol] = hidden
                self.mut_ref[symbol] = self.mut_indices(func)
                self.nonnull_ref[symbol] = self.nonnull_indices(func)
                if func.format_params:
                    self.format_syms.add(symbol)
                if func.mut_return:
                    self.mut_ret.add(symbol)
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
                self.check_group_decl(func)
                self.check_bound_decl(func)
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
                # Every template links by a signature-derived base (instances
                # append their bindings), so the spelling is a fact of the
                # declaration alone: separately compiled objects that merged
                # one set in different import orders emit identical instance
                # symbols. Two templates spelling one base (alpha-variants
                # and return-type-only pairs -- type parameters rename to
                # positional placeholders) could never be told apart at a
                # call, so they collide here, across modules included.
                base = self.template_base(func)
                overloads = self.templates.setdefault(func.name, [])
                clash = next(
                    (
                        prior
                        for prior in overloads
                        if self.template_bases[id(prior)] == base
                    ),
                    None,
                )
                if clash is not None:
                    err = LangError(
                        f"function '{base}' already defined; overloads must "
                        "differ in parameter patterns",
                        func.line,
                    )
                    err.notes.append(
                        Note(
                            f"previous declaration of {func.name!r} is here",
                            clash.line,
                            clash.source,
                        )
                    )
                    raise err
                # Disjoint type groups make same-pattern templates a
                # resolvable set (distinct bases above); overlapping ones
                # would be ambiguous at every shared deduction, so they
                # collide at declaration, cross-module included.
                self.check_group_overlap(func, base, overloads)
                # `extends` bounds are open sets that cannot be shown
                # disjoint, so two same-pattern bounded templates collide
                # (only an unbounded fallback may join, a tier below).
                self.check_bound_overlap(func, base, overloads)
                self.template_bases[id(func)] = base
                overloads.append(func)
                continue
            if in_set:
                # A member of a concrete overload set (two or more distinct
                # parameter lists visible to this file, prototypes
                # included). Members link by a signature-derived mangled
                # symbol -- `f(int32, char*)`, the canonical str(LangType) of
                # the parameter types only: the return type never
                # distinguishes overloads, and const/mut markers and
                # @nonnull/@noalias annotations live outside the parameter
                # types, so attribute-only variants spell the same symbol and
                # collide as duplicates below. A @private member's symbol is
                # additionally salted with its file stem (it is invisible
                # outside its module, so it must never collide with another
                # module's members); its registry key carries the same salt
                # so privacy variants never alias one entry.
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
                ret = self.lang_type(func.ret_type, func.line)
                self.check_format_decl(func, params)
                self.check_noreturn_decl(func, ret)
                self.check_mut_return_decl(func, ret)
                salt = (
                    self.overload_salt(func.source) if func.private else None
                )
                entry_key = (
                    params_key if salt is None else f"{params_key}\x00{salt}"
                )
                symbol = f"{func.name}({params_key})" + (
                    f".{salt}" if salt is not None else ""
                )
                member = self.concrete_decls.get(func.name, {}).get(entry_key)
                if (
                    member is not None
                    and id(member) in self.overload_symbols
                    and self.can_pair_prototype(func, entry_key)
                ):
                    # Per-signature forward declaration inside the set: the
                    # params-key selected the pair -- cross-source too, which
                    # is how a defining module's .mc completes its own .mci
                    # stub's prototypes member by member (a @private member's
                    # salt normalizes .mci and .mc to one stem, so its stub
                    # prototype pairs too). A plain-symbol function from
                    # another module never pairs here; it is absorbed as an
                    # ordinary member below.
                    self.pair_prototype(func, entry_key, ret, params)
                    continue
                if member is not None and id(member) not in self.overload_symbols:
                    # The same pattern already stands as a plain-symbol
                    # function (another module's single, or an interface
                    # stub's pinned member): a second member with an
                    # identical parameter list would tie at every call.
                    err = LangError(
                        f"function '{func.name}({params_key})' already "
                        "defined; overloads must differ in parameter types",
                        func.line,
                    )
                    err.notes.append(
                        Note(
                            f"previous declaration of {func.name!r} is here",
                            member.line,
                            member.source,
                        )
                    )
                    raise err
                # A privacy-only variant of one signature from one file is a
                # duplicate, not a second member (the two would tie at every
                # call).
                twin_salt = self.overload_salt(func.source)
                twin = (
                    self.concrete_decls.get(func.name, {}).get(params_key)
                    if func.private
                    else self.concrete_decls.get(func.name, {}).get(
                        f"{params_key}\x00{twin_salt}"
                    )
                    if twin_salt is not None
                    else None
                )
                if twin is not None and twin.source == func.source:
                    raise LangError(
                        f"function '{func.name}({params_key})' already "
                        "defined; overloads must differ in parameter types",
                        func.line,
                    )
                plain_member = None
                if func.name in self.funcs:
                    # The name stands as a plain-symbol function. An open set
                    # absorbs a plain concrete single (an interface stub's
                    # pinned member, or a module whose own visible set is
                    # this one signature); an @extern's fixed C symbol never
                    # joins, and a global or tombstone is not a function.
                    plain_member = next(
                        (
                            m
                            for m in self.concrete_decls.get(
                                func.name, {}
                            ).values()
                            if id(m) not in self.overload_symbols
                        ),
                        None,
                    )
                if (
                    (func.name in self.funcs and plain_member is None)
                    or func.name in self.globals
                    or func.name in self.removed
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
                if symbol in self.funcs:
                    err = LangError(
                        f"function {symbol!r} already defined; overloads "
                        "must differ in parameter types",
                        func.line,
                    )
                    prior = self.concrete_decls.get(func.name, {}).get(
                        entry_key
                    )
                    if prior is not None:
                        err.notes.append(
                            Note(
                                f"previous declaration of {func.name!r} "
                                "is here",
                                prior.line,
                                prior.source,
                            )
                        )
                    raise err
                hidden = self.hidden_ref_indices(func, params)
                fnty = ir.FunctionType(
                    self.ret_ir(func, ret), self.param_irs(params, hidden)
                )
                fn = ir.Function(self.module, fnty, name=symbol)
                if not func.proto:
                    # A prototype emits an LLVM declaration (no body);
                    # linkonce_odr is only legal on definitions, so it keeps
                    # external linkage.
                    self.link_shared(fn, func.source)
                self.mark_inline(fn, func)
                self.mark_noalias(fn, func, params)
                self.mark_nonnull(fn, func, params)
                self.mark_noreturn(fn, func)
                if func.noreturn:
                    self.noreturn_syms.add(symbol)
                self.funcs[symbol] = fn
                self.signatures[symbol] = (ret, params, False)
                self.hidden_ref[symbol] = hidden
                self.mut_ref[symbol] = self.mut_indices(func)
                self.nonnull_ref[symbol] = self.nonnull_indices(func)
                if func.mut_return:
                    self.mut_ret.add(symbol)
                members = self.overloads.setdefault(func.name, [])
                if plain_member is not None and not any(
                    m is plain_member for m in members
                ):
                    # The standing plain single becomes an ordinary member:
                    # resolution iterates self.overloads, and emission falls
                    # back to the plain name for a member with no
                    # overload_symbols entry.
                    members.append(plain_member)
                members.append(func)
                self.overload_symbols[id(func)] = symbol
                self.concrete_decls.setdefault(func.name, {})[entry_key] = func
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
                or func.name in self.globals
                or func.name in self.removed
            ):
                raise LangError(f"function {func.name!r} already defined", func.line)
            set_members = self.overloads.get(func.name)
            if set_members is not None or func.name in self.templates:
                # A single plain-symbol concrete joining a standing set (open
                # sets: this file's own visible set is just this signature --
                # an interface stub's pinned member, or a module that cannot
                # see the foreign @private members that formed the set), or
                # joining generic templates (a mixed set). Either way it
                # keeps its plain symbol, and calls route through overload
                # resolution. The concrete side must still be overloadable
                # at all.
                if func.name == "main":
                    raise LangError(
                        "function 'main' cannot be overloaded", func.line
                    )
                if func.variadic:
                    raise LangError(
                        f"variadic function {func.name!r} cannot be overloaded",
                        func.line,
                    )
                if any(is_valist(p) for p in params):
                    raise LangError(
                        f"function {func.name!r} cannot be overloaded: it "
                        "takes a va_list parameter",
                        func.line,
                    )
                dup = self.concrete_decls.get(func.name, {}).get(params_key)
                if dup is not None:
                    # A member already spells this exact parameter list: a
                    # second one would tie at every call.
                    err = LangError(
                        f"function '{func.name}({params_key})' already "
                        "defined; overloads must differ in parameter types",
                        func.line,
                    )
                    err.notes.append(
                        Note(
                            f"previous declaration of {func.name!r} is here",
                            dup.line,
                            dup.source,
                        )
                    )
                    raise err
            self.check_collecting_decl(func, params)
            self.check_format_decl(func, params)
            self.concrete_decls.setdefault(func.name, {})[params_key] = func
            self.func_privacy[func.name] = (func.private, func.source)
            self.used_symbols.add(func.name)
            if set_members is not None:
                # An ordinary member from here on: resolution iterates
                # self.overloads, and emission falls back to the plain name
                # for a member with no overload_symbols entry.
                set_members.append(func)
            ret = self.lang_type(func.ret_type, func.line)
            self.check_noreturn_decl(func, ret)
            self.check_mut_return_decl(func, ret)
            hidden = self.hidden_ref_indices(func, params)
            fnty = ir.FunctionType(
                self.ret_ir(func, ret),
                self.param_irs(params, hidden),
                var_arg=func.variadic,
            )
            fn = ir.Function(self.module, fnty, name=func.name)
            if not func.proto:
                # A prototype emits an LLVM declaration (no body); linkonce_odr
                # is only legal on definitions, so it keeps external linkage.
                self.link_shared(fn, func.source)
            self.mark_inline(fn, func)
            self.mark_noalias(fn, func, params)
            self.mark_nonnull(fn, func, params)
            self.mark_noreturn(fn, func)
            if func.noreturn:
                self.noreturn_syms.add(func.name)
            self.funcs[func.name] = fn
            self.signatures[func.name] = (ret, params, func.variadic)
            self.hidden_ref[func.name] = hidden
            self.mut_ref[func.name] = self.mut_indices(func)
            self.nonnull_ref[func.name] = self.nonnull_indices(func)
            if func.format_params:
                self.format_syms.add(func.name)
            if func.mut_return:
                self.mut_ret.add(func.name)
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
            const = self.const_initializer(var.init, var_type, var.line)
            if glob is None:
                # A union global whose storage type is the constant's type (its
                # written member plus pad); create the storage now.
                key = (var.source, var.name)
                if key in self.static_globals:
                    raise LangError(
                        f"variable {var.name!r} already defined", var.line
                    )
                symbol = self.static_base(var.name, var.source)
                glob = ir.GlobalVariable(self.module, const.type, name=symbol)
                glob.linkage = self.shared_linkage(var.source)
                self.static_globals[key] = (glob, var_type, var.volatile)
            if is_aggregate(var_type):
                # A union's ad-hoc storage type and a @packed/@align aggregate
                # both have a natural LLVM alignment below the type's true
                # alignment, so pin it explicitly.
                glob.align = type_align(var_type)
            glob.initializer = const
        # Error directives run once every type, const, enum, and global is
        # known, so @static_assert conditions can fold sizeof/alignof/offsetof
        # and const/enum references; before function bodies, so a failed layout
        # assertion aborts the build without wasting work on codegen.
        self.check_directives()
        # Every declaration is registered and @if arms are resolved: compute
        # the per-function write-effect bits the call-emission sites consult
        # (a call to a proven write-free callee preserves projection facts).
        self.analyze_write_effects()
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
        # Every body is generated, so the boxed-tag registry is complete
        # (modulo what the copies themselves box -- the fixpoint's job):
        # monomorphize the deferred generic case-type arms, then eagerly
        # instantiate every closed-type-group member that no call reached.
        # A member body can box new types (new pending-arm tags) and a new
        # arm copy can instantiate new generics, so the two finalizers loop
        # against each other until both are quiet.
        progress = True
        while progress:
            self.finalize_generic_arms()
            progress = self.eager_group_instances()
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
        self.ret_mut = func.mut_return
        self.current_source = func.source
        self.current_variadic = func.variadic  # gates va_start
        self.current_noreturn = func.name if func.noreturn else None
        self.builder = ir.IRBuilder(fn.append_basic_block("entry"))
        self.locals = {}
        self.scope_names = set()  # the body block resets this, but be explicit
        self.defer_stack = []
        self.loops = []  # break/continue cannot escape into a caller's loop
        self.block_exprs = []  # emit cannot escape into a caller's block-expr
        self.defer_marks = []  # a body instantiated mid-defer is not "in" it
        hidden = self.hidden_ref_indices(func, params)
        self.const_locals = set(func.const_params)
        self.mut_locals = set(func.mut_params)
        self.nonnull_locals = set(func.nonnull_params)
        # Plain parameters, for the mut-return formation walk: a pointer
        # parameter is a legal root behind at least one hop, a by-value one
        # never is (its storage is this call's frame).
        self.formation_params = {
            pname: is_pointer(ptype)
            for (pname, _), ptype in zip(func.params, params)
            if pname not in func.mut_params and pname not in func.const_params
        }
        self.narrowed_nonnull = set()
        self.narrowed_paths = set()
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
        # A @deprecated function's own body may call other deprecated
        # functions (a shim delegating within the deprecated cluster) without
        # re-warning. Save/restore so nested instantiations emitted mid-walk
        # and the ordinary (live) case both restore correctly.
        prev_in_deprecated = self.in_deprecated_body
        self.in_deprecated_body = func.deprecated_msg is not None
        try:
            self.gen_block(func.body)
        finally:
            self.in_deprecated_body = prev_in_deprecated
        if not self.builder.block.is_terminated:
            if func.noreturn:
                # C11 _Noreturn semantics: the promise is the author's, so
                # falling off the end is undefined behavior, not an error.
                # (The canonical spin `fn spin() { while (true) {} }` never
                # gets here: constant-condition folding leaves its body
                # terminated, so the loop diverges by itself; the planted
                # unreachable covers non-loop fall-offs. Documented in
                # docs/language.md.)
                self.builder.unreachable()
            elif ret is VOID:
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
        outer_paths = set(self.narrowed_paths)
        self.scope_names = set()
        self.defer_stack.append([])
        try:
            prev = None
            for stmt in statements:
                if self.builder.block.is_terminated:
                    # Unreachable code after return/break/continue: report
                    # the region once (-Wdead-code), then drop it as always.
                    self.warn_dead_code(prev, stmt)
                    break
                self.gen_statement(stmt)
                prev = stmt
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
            self.narrowed_paths &= outer_paths

    def run_deferred_scope(self, scope: list):
        """Emit one block's deferred actions, last-registered first.

        Each body runs while the block's locals are still in scope, so it can
        refer to them.

        Each body generates under a defer mark recording the loops and block
        expressions visible at entry: a ``break``/``continue``/``emit``
        targeting one of those (or any ``return``) would jump out of the
        defer body and re-unwind the scope whose defers are running, so the
        jump statements reject it against the mark. A loop or block
        expression opened *inside* the body is past the mark and stays fair
        game.

        Args:
            scope: The list of deferred action bodies for one block.
        """
        for body in reversed(scope):
            if self.builder.block.is_terminated:
                break
            self.defer_marks.append((len(self.loops), len(self.block_exprs)))
            try:
                self.gen_block(body)
            finally:
                self.defer_marks.pop()

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

    def spill_result(self, expr) -> tuple:
        """Evaluate a ``try`` expression's operand and split it open.

        Evaluates the tested expression once, checks it is a ``result``,
        spills it to a slot, and reads the tag -- the shared head of every
        ``try`` lowering (bare, ``??``, ``except``, and the try statement;
        the form-1 destructure inlines the same steps against its own error
        wording, see :meth:`destructure_result`).

        Args:
            expr: The ``Except``, ``Try``, ``TryFallback``, or ``TryStmt``
                node (any node with ``value`` and ``line``).

        Returns:
            ``(result type, payload address, is_err)`` -- the const-stripped
            ``result<...>`` type, the payload field's address, and the
            ``i1`` tag test (true on the error arm).

        Raises:
            LangError: When the subject is not a ``result``.
        """
        subj = self.gen_expr(expr.value)
        result_t = strip_const(subj.type)
        if not is_result(result_t):
            raise LangError(
                f"try needs a result value, got {subj.type}", expr.line
            )
        slot = self.builder.alloca(result_t.ir, name="except.subject")
        if over_aligned(result_t):
            slot.align = type_align(result_t)
        self.builder.store(subj.value, slot)
        indices = result_t.elem_indices
        tag_addr = self.builder.gep(
            slot, [I32_ZERO, ir.Constant(ir.IntType(32), indices[0])],
            inbounds=True,
        )
        payload_addr = self.builder.gep(
            slot, [I32_ZERO, ir.Constant(ir.IntType(32), indices[1])],
            inbounds=True,
        )
        tag = self.builder.load(tag_addr, name="except.tag")
        is_err = self.builder.icmp_unsigned(
            "!=", tag, ir.Constant(UINT8.ir, 0)
        )
        return result_t, payload_addr, is_err

    def gen_except_handler(
        self,
        expr,
        payload_addr,
        err_t: LangType,
        ctx: "BlockExprCtx | None",
    ):
        """Generate an ``except`` clause's handler block, binder bound.

        The handler is a block-expression variant where every path may
        diverge (``gen_block_expr``'s "never emits" error does not apply --
        ``except (err) { return -1; }`` is the common handler): the caller
        presets the context's slot and type (or ``no_value``), pushes
        nothing, and decides afterwards what a fall-through means. The
        binder is a plain (non-const) copy of the error value, scoped to
        the handler only -- the binder-in-arm precedent of ``case type``'s
        ``when T t:`` -- and the context joins ``block_exprs`` so an
        ``emit`` inside targets the handler (which is also what keeps
        ``except (err) { emit fallback; }`` legal inside a ``defer`` body:
        the emit targets a block expression opened inside it).

        Args:
            expr: The ``Except`` (or ``TryStmt``) node -- binder name and
                handler body.
            payload_addr: The spilled result's payload address; the error
                arm's bytes are live here when the handler runs.
            err_t: The declared error type ``E``.
            ctx: The preset handler context (``cont_bb`` = the join block),
                or ``None`` for the try statement's handler, which is no
                ``emit`` target of its own (nothing to fill -- an ``emit``
                inside it targets an enclosing block expression, like any
                statement arm's body).
        """
        outer_locals, outer_names = dict(self.locals), self.scope_names
        self.scope_names = set()
        if ctx is not None:
            self.block_exprs.append(ctx)
        try:
            err_ptr = payload_addr
            if err_ptr.type != err_t.ir.as_pointer():
                # Arity 2: the payload is the internal union; read the error
                # arm through a cast of its address, as a union member load.
                err_ptr = self.builder.bitcast(
                    payload_addr, err_t.ir.as_pointer()
                )
            binder_slot = self.builder.alloca(err_t.ir, name=expr.binder)
            self.builder.store(self.builder.load(err_ptr), binder_slot)
            self.bind_local(expr.binder, binder_slot, err_t)
            self.gen_block(expr.handler)
        finally:
            if ctx is not None:
                self.block_exprs.pop()
            self.locals, self.scope_names = outer_locals, outer_names

    def gen_let_except(self, stmt: Let):
        """Lower ``let ret = try f() except (err) { H } [else { S }];``.

        Value position: branch on the tag. On ok, ``ret``'s slot takes the
        payload, the optional ``else`` block runs with ``ret`` in scope (the
        ok arm only -- Python's ``try``/``except``/``else``), and control
        falls through with ``ret`` live after the statement. On error the
        handler runs with the binder bound and **must** diverge (return,
        break, continue, panic) or ``emit`` a fallback that fills the same
        slot -- on that fallback path ``else`` does *not* run (a fallback is
        not an ok), but code after does, ``ret`` = the fallback. The
        handler is generated before ``ret`` is bound, so the initializer
        cannot read the name it defines; ``else`` is generated after, so it
        can.

        Also the ``return``-position lowering, through the hidden-let
        desugar in the ``Return`` arm.

        Args:
            stmt: The ``Let`` whose value is an ``Except``.

        Raises:
            LangError: When the subject is not a result, the result has no
                ok value (``result<E>``), or the handler may fall through
                without emitting.
        """
        expr = stmt.value
        result_t, payload_addr, is_err = self.spill_result(expr)
        if len(result_t.args) == 1:
            raise LangError(
                f"a {result_t} has no ok value to bind; handle it in "
                "statement position: try f() except (err) { ... };",
                expr.line,
            )
        ok_t = result_t.args[0]
        declared = ok_t
        if stmt.type_name is not None:
            declared = self.lang_type(stmt.type_name, stmt.line)
            if declared is VOID:
                raise LangError("cannot declare a void variable", stmt.line)
        err_bb = self.builder.append_basic_block("except.err")
        ok_bb = self.builder.append_basic_block("except.ok")
        join_bb = self.builder.append_basic_block("except.end")
        # The let's own slot doubles as the handler's emit target; it is
        # written in both arms and read after the join, so it lives in the
        # entry block (the block-expression discipline).
        slot = self.entry_alloca(declared.ir, stmt.name)
        if over_aligned(declared):
            slot.align = type_align(declared)
        self.builder.cbranch(is_err, err_bb, ok_bb)
        self.builder.position_at_end(err_bb)
        ctx = BlockExprCtx(
            cont_bb=join_bb,
            defer_depth=len(self.defer_stack),
            slot=slot,
            type=declared,
        )
        self.gen_except_handler(expr, payload_addr, result_t.args[1], ctx)
        if not self.builder.block.is_terminated:
            raise LangError(
                "the except handler may fall through without a value; emit "
                "a fallback or diverge (return, break, continue, panic)",
                expr.line,
            )
        self.builder.position_at_end(ok_bb)
        ok_ptr = self.builder.bitcast(payload_addr, ok_t.ir.as_pointer())
        tv = self.coerce(
            TypedValue(self.builder.load(ok_ptr), ok_t),
            declared, stmt.line, f"let {stmt.name}",
        )
        self.builder.store(tv.value, slot)
        self.bind_local(stmt.name, slot, declared)
        if expr.otherwise is not None:
            self.gen_block(expr.otherwise)
        ok_falls = not self.builder.block.is_terminated
        if ok_falls:
            self.builder.branch(join_bb)
        self.builder.position_at_end(join_bb)
        if not (ok_falls or ctx.emitted):
            # The handler diverged and a diverging else closed the ok arm:
            # nothing reaches the join, so the statement diverges too.
            self.builder.unreachable()

    def gen_except_stmt(self, expr: Except):
        """Lower statement ``try f() except (err) { H } [else { S }];``.

        No value escapes the statement, so the handler is obligation-free:
        it may fall through (log and move on), diverge, or -- over a
        two-arm result -- still ``emit`` a fallback, which is simply
        discarded. This is also the ``result<E>`` consumer (the one
        position an error-only result can be handled), where an ``emit``
        rejects instead: there is no ok value to stand in for. The
        optional ``else`` stays the ok-arm block.

        Args:
            expr: The ``Except`` node.

        Raises:
            LangError: When the subject is not a result.
        """
        result_t, payload_addr, is_err = self.spill_result(expr)
        err_bb = self.builder.append_basic_block("except.err")
        ok_bb = self.builder.append_basic_block("except.ok")
        join_bb = self.builder.append_basic_block("except.end")
        ctx = BlockExprCtx(
            cont_bb=join_bb, defer_depth=len(self.defer_stack)
        )
        if len(result_t.args) == 2:
            # A discarded fallback slot keeps emit's semantics uniform with
            # the value positions (the value must still coerce to T).
            ctx.type = result_t.args[0]
            ctx.slot = self.entry_alloca(ctx.type.ir)
            if over_aligned(ctx.type):
                ctx.slot.align = type_align(ctx.type)
        else:
            ctx.no_value = str(result_t)
        self.builder.cbranch(is_err, err_bb, ok_bb)
        self.builder.position_at_end(err_bb)
        self.gen_except_handler(expr, payload_addr, result_t.args[-1], ctx)
        err_falls = not self.builder.block.is_terminated
        if err_falls:
            self.builder.branch(join_bb)
        self.builder.position_at_end(ok_bb)
        if expr.otherwise is not None:
            self.gen_block(expr.otherwise)
        ok_falls = not self.builder.block.is_terminated
        if ok_falls:
            self.builder.branch(join_bb)
        self.builder.position_at_end(join_bb)
        if not (ok_falls or err_falls or ctx.emitted):
            self.builder.unreachable()

    def gen_except_value(self, expr: Except) -> TypedValue:
        """Lower a ``try ... except`` nested inside a larger expression.

        The ``try`` expression sits at unary level, so it composes as an
        ordinary operand (``1 + try f() except (err) { emit 0; }``, an
        argument, a condition). Same value-position semantics as the
        ``let`` form -- the handler must diverge or ``emit`` a fallback,
        the optional ``else`` runs on the ok arm (with no binding of its
        own: there is no name here) -- minus the binding.

        Args:
            expr: The ``Except`` node.

        Returns:
            The ok value or the handler's fallback as a ``TypedValue``.

        Raises:
            LangError: When the operand is not a result, the result has no
                ok value (``result<E>``), the handler may fall through
                without emitting, or every path diverges (nothing produces
                the operand's value).
        """
        result_t, payload_addr, is_err = self.spill_result(expr)
        if len(result_t.args) == 1:
            raise LangError(
                f"a {result_t} has no ok value to bind; handle it in "
                "statement position: try f() except (err) { ... };",
                expr.line,
            )
        ok_t = result_t.args[0]
        err_bb = self.builder.append_basic_block("except.err")
        ok_bb = self.builder.append_basic_block("except.ok")
        join_bb = self.builder.append_basic_block("except.end")
        slot = self.entry_alloca(ok_t.ir)
        if over_aligned(ok_t):
            slot.align = type_align(ok_t)
        self.builder.cbranch(is_err, err_bb, ok_bb)
        self.builder.position_at_end(err_bb)
        ctx = BlockExprCtx(
            cont_bb=join_bb,
            defer_depth=len(self.defer_stack),
            slot=slot,
            type=ok_t,
        )
        self.gen_except_handler(expr, payload_addr, result_t.args[1], ctx)
        if not self.builder.block.is_terminated:
            raise LangError(
                "the except handler may fall through without a value; emit "
                "a fallback or diverge (return, break, continue, panic)",
                expr.line,
            )
        self.builder.position_at_end(ok_bb)
        ok_ptr = self.builder.bitcast(payload_addr, ok_t.ir.as_pointer())
        self.builder.store(self.builder.load(ok_ptr), slot)
        if expr.otherwise is not None:
            self.gen_block(expr.otherwise)
        if self.builder.block.is_terminated:
            if not ctx.emitted:
                # A diverging else closed the ok arm and the handler never
                # emits: no path can deliver the operand's value.
                raise LangError(
                    "this try expression never produces a value: the "
                    "handler and the else block both diverge",
                    expr.line,
                )
        else:
            self.builder.branch(join_bb)
        self.builder.position_at_end(join_bb)
        return TypedValue(self.gen_load(slot), ok_t)

    def gen_try_propagate(self, expr, result_t, payload_addr, is_err):
        """Generate a bare ``try``'s error arm: return ``error(err)``.

        The propagation desugar -- ``try g()`` is
        ``try g() except (err) { return error(err); }`` -- so the enclosing
        function's return type must absorb ``E`` explicitly: a
        ``result<T2, E>`` or ``result<E>`` with the **same** declared error
        type (there are no error conversions; mapping to a different error
        type is a handler's job). The error arm binds the error value under
        a hidden non-lexable name and routes a synthesized ``return``
        through the normal return path, which owns defers and the coercion
        to the return type. Leaves the builder positioned at the ok arm.

        Args:
            expr: The ``Try`` node (for its source line).
            result_t: The operand's const-stripped ``result`` type.
            payload_addr: The spilled result's payload address.
            is_err: The ``i1`` tag test from :meth:`spill_result`.

        Raises:
            LangError: When the enclosing return type does not carry the
                same error type, or the try sits inside a defer body (its
                return could not exit the enclosing function).
        """
        err_t = result_t.args[-1]
        ret_t = strip_const(self.ret_type)
        if not (is_result(ret_t) and ret_t.args[-1] == err_t):
            raise LangError(
                f"try propagates {err_t}, but this function returns "
                f"{self.ret_type}",
                expr.line,
            )
        if self.defer_marks:
            # The same escape ban a literal `return` in a defer body hits,
            # named for the construct the user wrote.
            raise LangError(
                "try propagation inside a defer body cannot exit the "
                "enclosing function; handle the error with except",
                expr.line,
            )
        err_bb = self.builder.append_basic_block("try.err")
        ok_bb = self.builder.append_basic_block("try.ok")
        self.builder.cbranch(is_err, err_bb, ok_bb)
        self.builder.position_at_end(err_bb)
        outer_locals, outer_names = dict(self.locals), self.scope_names
        self.scope_names = set()
        try:
            err_ptr = payload_addr
            if err_ptr.type != err_t.ir.as_pointer():
                err_ptr = self.builder.bitcast(
                    payload_addr, err_t.ir.as_pointer()
                )
            hidden = "0try"  # not a lexable identifier: cannot collide
            binder_slot = self.builder.alloca(err_t.ir, name="try.propagate")
            self.builder.store(self.builder.load(err_ptr), binder_slot)
            self.bind_local(hidden, binder_slot, err_t)
            self.gen_statement(
                Return(
                    ResultLit("error", Var(hidden, expr.line), expr.line),
                    expr.line,
                )
            )
        finally:
            self.locals, self.scope_names = outer_locals, outer_names
        self.builder.position_at_end(ok_bb)

    def gen_try_value(self, expr: Try) -> TypedValue:
        """Lower a bare ``try g()`` in value position: propagate or yield T.

        Args:
            expr: The ``Try`` node.

        Returns:
            The ok payload as a ``TypedValue``.

        Raises:
            LangError: When the operand is not a result, has no ok value
                (``result<E>``), or the enclosing return type cannot absorb
                the error type.
        """
        result_t, payload_addr, is_err = self.spill_result(expr)
        if len(result_t.args) == 1:
            raise LangError(
                f"a {result_t} has no ok value; propagate it in statement "
                "position: try f();",
                expr.line,
            )
        self.gen_try_propagate(expr, result_t, payload_addr, is_err)
        ok_t = result_t.args[0]
        ok_ptr = self.builder.bitcast(payload_addr, ok_t.ir.as_pointer())
        return TypedValue(self.builder.load(ok_ptr), ok_t)

    def gen_try_discard(self, expr: Try):
        """Lower statement-position ``try f();``: propagate or continue.

        The error-only ``result<E>``'s propagation consumer -- and over an
        arity-2 result the ok value is simply discarded, like any
        expression statement's value.

        Args:
            expr: The ``Try`` node.
        """
        result_t, payload_addr, is_err = self.spill_result(expr)
        self.gen_try_propagate(expr, result_t, payload_addr, is_err)

    def gen_try_fallback(self, expr: TryFallback) -> TypedValue:
        """Lower ``try g() ?? fallback``: discard the error and default.

        Branches on the tag: the ok arm loads the payload; the error arm
        discards the error and evaluates the fallback -- lazily, only on
        that path -- coercing it to ``T`` (adapted literals route as in any
        typed sink). The emit-block form presets the result slot like an
        ``except`` handler's context, and may diverge instead of emitting.
        Nothing escapes the expression, so the enclosing return type is
        never consulted.

        Args:
            expr: The ``TryFallback`` node.

        Returns:
            The ok payload or the fallback as a ``TypedValue``.

        Raises:
            LangError: When the operand is not a result, has no ok value to
                default (``result<E>``), or the fallback block may fall
                through without emitting.
        """
        result_t, payload_addr, is_err = self.spill_result(expr)
        if len(result_t.args) == 1:
            raise LangError(
                f"a {result_t} has no ok value to default; handle it "
                "(try f() except (err) { ... };) or propagate it "
                "(try f();)",
                expr.line,
            )
        ok_t = result_t.args[0]
        err_bb = self.builder.append_basic_block("fallback.err")
        ok_bb = self.builder.append_basic_block("fallback.ok")
        join_bb = self.builder.append_basic_block("fallback.end")
        slot = self.entry_alloca(ok_t.ir)
        if over_aligned(ok_t):
            slot.align = type_align(ok_t)
        self.builder.cbranch(is_err, err_bb, ok_bb)
        self.builder.position_at_end(err_bb)
        if isinstance(expr.fallback, BlockExpr):
            # `?? { ...; emit v; }` -- the handler-context machinery with a
            # preset slot; all paths diverging is legal (the ok arm still
            # delivers the value).
            ctx = BlockExprCtx(
                cont_bb=join_bb,
                defer_depth=len(self.defer_stack),
                slot=slot,
                type=ok_t,
            )
            outer_locals, outer_names = dict(self.locals), self.scope_names
            self.scope_names = set()
            self.block_exprs.append(ctx)
            try:
                self.gen_block(expr.fallback.body)
            finally:
                self.block_exprs.pop()
                self.locals, self.scope_names = outer_locals, outer_names
            if not self.builder.block.is_terminated:
                raise LangError(
                    "the '??' fallback block may fall through without a "
                    "value; emit the fallback or diverge (return, break, "
                    "continue, panic)",
                    expr.line,
                )
        else:
            if (
                self.struct_literal_adapts(expr.fallback, ok_t)
                or self.str_literal_adapts(expr.fallback, ok_t)
                or self.array_literal_adapts(expr.fallback, ok_t)
                or self.result_literal_adapts(expr.fallback, ok_t)
            ):
                tv = self.gen_adapted_literal(expr.fallback, ok_t, expr.line)
            else:
                tv = self.coerce(
                    self.gen_expr(expr.fallback), ok_t, expr.line,
                    "'??' fallback",
                )
            self.builder.store(tv.value, slot)
            if not self.builder.block.is_terminated:
                self.builder.branch(join_bb)
        self.builder.position_at_end(ok_bb)
        ok_ptr = self.builder.bitcast(payload_addr, ok_t.ir.as_pointer())
        self.builder.store(self.builder.load(ok_ptr), slot)
        self.builder.branch(join_bb)
        self.builder.position_at_end(join_bb)
        return TypedValue(self.gen_load(slot), ok_t)

    def gen_coalesce(self, expr: Coalesce) -> TypedValue:
        """Reject a general ``??`` coalesce (every arm is reserved today).

        The production exists so the grammar is settled once: a ``??``
        directly after a bare ``try`` operand is the try's own fallback
        clause (structural, by production -- see ``parse_unary``); anything
        that reaches this node is the general operator, whose arms are not
        live yet. A ``result`` left-hand side unwraps through ``try``; a
        pointer left-hand side is the null coalesce, reserved for the
        pointer-truthiness roadmap item; everything else has no coalescing
        meaning.

        Args:
            expr: The ``Coalesce`` node.

        Raises:
            LangError: Always, per left-hand-side type.
        """
        lhs = self.gen_expr(expr.lhs)
        t = strip_const(lhs.type)
        if is_result(t):
            raise LangError(
                f"a {t} left of '??' unwraps through try: try f() ?? v",
                expr.line,
            )
        if is_pointer(t) or t is NULLT:
            raise LangError(
                "'??' on a pointer is null coalescing, which lands with "
                "the pointer-truthiness roadmap item; spell the test for "
                "now: p == null ? q : p",
                expr.line,
            )
        raise LangError(
            f"'??' coalesces pointers or supplies a try fallback; got "
            f"{lhs.type}",
            expr.line,
        )

    def gen_try_stmt(self, stmt: TryStmt):
        """Lower ``try (ret = f()) { B } except (err) { H }``.

        Branches on the tag: the ok arm binds a fresh ``ret`` to the
        payload, scoped to the block ``B`` only (invisible after the
        statement); the error arm binds ``err`` scoped to ``H``, and the
        handler is obligation-free -- it may fall through ("log and move
        on"), diverge, or do nothing. There is no ``else`` arm and no
        ``emit`` target of its own (the statement produces nothing).

        Args:
            stmt: The ``TryStmt`` node.

        Raises:
            LangError: When the head is not a result, or the result has no
                ok value to bind (``result<E>``).
        """
        result_t, payload_addr, is_err = self.spill_result(stmt)
        if len(result_t.args) == 1:
            raise LangError(
                f"a {result_t} has no ok value to bind; handle it without "
                "the binding: try f(); or try f() except (err) { ... };",
                stmt.line,
            )
        ok_t = result_t.args[0]
        err_bb = self.builder.append_basic_block("try.err")
        ok_bb = self.builder.append_basic_block("try.ok")
        join_bb = self.builder.append_basic_block("try.end")
        self.builder.cbranch(is_err, err_bb, ok_bb)
        self.builder.position_at_end(err_bb)
        self.gen_except_handler(stmt, payload_addr, result_t.args[1], None)
        err_falls = not self.builder.block.is_terminated
        if err_falls:
            self.builder.branch(join_bb)
        self.builder.position_at_end(ok_bb)
        outer_locals, outer_names = dict(self.locals), self.scope_names
        self.scope_names = set()
        try:
            slot = self.builder.alloca(ok_t.ir, name=stmt.name)
            if over_aligned(ok_t):
                slot.align = type_align(ok_t)
            ok_ptr = self.builder.bitcast(payload_addr, ok_t.ir.as_pointer())
            self.builder.store(self.builder.load(ok_ptr), slot)
            self.bind_local(stmt.name, slot, ok_t)
            self.gen_block(stmt.body)
        finally:
            self.locals, self.scope_names = outer_locals, outer_names
        ok_falls = not self.builder.block.is_terminated
        if ok_falls:
            self.builder.branch(join_bb)
        self.builder.position_at_end(join_bb)
        if not (ok_falls or err_falls):
            # Both arms diverged: nothing reaches the join, so the
            # statement diverges too.
            self.builder.unreachable()

    def gen_struct_lit(self, expr: StructLit, struct_type: LangType = None) -> TypedValue:
        """Lower a struct literal ``struct Name { field = expr, ... }``.

        Allocates a temporary, zero-initializes it (so omitted fields read as
        zero), stores each named field, and yields the struct by value. The
        field expressions are coerced to their declared types, so an untyped
        integer constant adapts as it would in an assignment. A generic struct's
        type arguments are inferred from the field values when none are given.

        A *bare* literal ``{ field = expr, ... }`` carries no type of its own;
        the caller passes the ``struct_type`` the position fixes (a typed
        ``let``/assignment/return/argument/element/field). With no such context a
        bare literal is an error -- there is nothing to build.

        Args:
            expr: The ``StructLit`` node.
            struct_type: The resolved aggregate type for a bare literal, else
                ``None`` (a named literal resolves its own type).

        Returns:
            The constructed struct as a ``TypedValue``.

        Raises:
            LangError: When the type is not a struct, a field is unknown, a field
                is given twice, or a type parameter cannot be inferred.
        """
        ref = expr.type_ref
        if ref is None and struct_type is None:
            raise LangError(
                "a bare struct literal `{ ... }` has no type here; write the type "
                "(point { ... }) or use it where one is expected -- a typed let, "
                "assignment, return, argument, element, or field",
                expr.line,
            )
        # Carry each field's RAW AST node, not a pre-evaluated value: a string
        # or array literal in a slice-typed field must reach the store step as
        # its raw node to borrow (as in a `let`, return, or argument), and its
        # field type is only known once the struct type is resolved. Reject
        # repeats in source order.
        seen: set[str] = set()
        raw_items = []  # (field name, value expr, source line)
        for fname, value_expr in expr.fields:
            if fname in seen:
                raise LangError(
                    f"field {fname!r} is set twice in the struct literal", expr.line
                )
            seen.add(fname)
            raw_items.append((fname, value_expr, value_expr.line))

        cached: dict | None = None
        decl = None if ref is None else self.lookup_struct_decl(ref.name)
        if struct_type is not None:
            pass  # a bare literal: the context already fixed the concrete type
        elif decl is not None and decl.type_params and not ref.args:
            # Generic with no explicit type args: infer from the field values.
            # A string/array literal adapting into a slice-typed field is an
            # inference non-participant (like an untyped constant, which carries
            # only a default it would adapt to) -- it borrows later, once the
            # field type is fixed by the other fields, a declared default, or
            # explicit args. So evaluate only the non-adapting fields here, cache
            # them for the store pass, and feed those to inference; the adapting
            # literals are borrowed in the store pass below. A literal against a
            # bare type parameter (``box { v = "hi" }``, field ``v: T``) does not
            # adapt, so it still participates -- ``"hi"`` binds ``T = char*``.
            patterns = dict(decl.fields)
            cached = {}
            infer_items = []  # (field name, value, line) that drive inference
            for fname, value_expr, line in raw_items:
                if self.defers_field_literal(value_expr, patterns.get(fname)):
                    continue
                tv = self.gen_expr(value_expr)
                cached[fname] = tv
                infer_items.append((fname, tv, line))
            struct_type = self.infer_struct_lit_type(decl, infer_items, expr.line)
        else:
            struct_type = self.lang_type(ref, expr.line)
        if not is_aggregate(struct_type):
            raise LangError(
                f"a struct literal needs a struct type, not {struct_type}", expr.line
            )
        if is_result(struct_type):
            # A result is struct-realized but its fields are internal; the
            # constructors are the only producers.
            raise LangError(
                f"a {struct_type} is not built from a struct literal; "
                "construct it with ok(...) or error(...)",
                expr.line,
            )
        if is_union(struct_type) and len(raw_items) > 1:
            # The members share one storage, so writing two would just
            # overwrite; the literal names the (at most one) live member.
            raise LangError("a union literal sets at most one member", expr.line)

        slot = self.builder.alloca(struct_type.ir)
        if over_aligned(struct_type):
            slot.align = type_align(struct_type)
        self.builder.store(ir.Constant(struct_type.ir, None), slot)  # zero omitted fields
        for fname, value_expr, line in raw_items:
            # A field pre-evaluated for inference reuses its cached value (so its
            # side effects ran once, in source order); every other field lowers
            # now, borrowing a string/array literal into a slice-typed field.
            if cached is not None and fname in cached:
                tv = cached[fname]
            else:
                tv = self.eval_struct_field(struct_type, fname, value_expr, line)
            self.store_struct_field(slot, struct_type, fname, tv, line, "field")
        # Fill any omitted field that declares a default; the rest keep the zero.
        for fname, default_expr in self.struct_defaults(struct_type).items():
            if fname in seen:
                continue
            tv = self.eval_struct_field(
                struct_type, fname, default_expr, default_expr.line
            )
            self.store_struct_field(
                slot, struct_type, fname, tv, default_expr.line,
                "default for field",
            )
        return TypedValue(self.builder.load(slot), struct_type)

    def gen_tuple_lit(self, expr: TupleLit, tuple_type: LangType = None) -> TypedValue:
        """Lower a tuple literal ``(a, b, ...)``.

        With a receiving tuple type (a typed ``let``, assignment, return,
        argument, element, or field -- the caller passes it), each element
        lowers against its position's type exactly like a struct-literal
        field: untyped constants adapt, and a string/array/struct/tuple
        literal in a matching position borrows or builds (see
        :meth:`eval_struct_field`). With no context the literal fixes its own
        type: every element is evaluated and an adaptable constant anchors to
        its default (``int32`` for an untyped integer), as at a call-site
        inference.

        Args:
            expr: The ``TupleLit`` node.
            tuple_type: The tuple type the position fixes, else ``None`` (the
                literal anchors its own).

        Returns:
            The constructed tuple as a ``TypedValue``.

        Raises:
            LangError: On an arity mismatch against the receiving type, or an
                element that cannot fix a type.
        """
        cached: list | None = None
        if tuple_type is not None:
            if len(expr.elements) != len(tuple_type.fields):
                raise LangError(
                    f"tuple literal has {len(expr.elements)} elements, "
                    f"but {tuple_type} has {len(tuple_type.fields)}",
                    expr.line,
                )
        else:
            # No receiving type: evaluate the elements once (in source
            # order), anchor, and intern the shape they spell.
            cached = [self.gen_expr(e) for e in expr.elements]
            for tv, element in zip(cached, expr.elements):
                if tv.type is NULLT:
                    raise LangError(
                        "a null tuple element needs a pointer type; annotate "
                        "the tuple (let t: tuple<uint8*, ...> = ...) or cast "
                        "the element (null as uint8*)",
                        element.line,
                    )
                if tv.type is VOID:
                    raise LangError(
                        "a tuple element cannot be a void value", element.line
                    )
            tuple_type = self.tuple_type(
                tuple(strip_const(tv.type) for tv in cached), expr.line
            )
        slot = self.builder.alloca(tuple_type.ir)
        if over_aligned(tuple_type):
            slot.align = type_align(tuple_type)
        # Zero-fill first so padding bytes are deterministic, as in a struct
        # literal (every position is then written).
        self.builder.store(ir.Constant(tuple_type.ir, None), slot)
        for i, value_expr in enumerate(expr.elements):
            if cached is not None:
                tv = cached[i]
            else:
                tv = self.eval_struct_field(
                    tuple_type, str(i), value_expr, value_expr.line
                )
            self.store_struct_field(
                slot, tuple_type, str(i), tv, value_expr.line, "tuple element"
            )
        return TypedValue(self.builder.load(slot), tuple_type)

    def gen_result_lit(self, expr: ResultLit, result_t: LangType) -> TypedValue:
        """Lower a result constructor ``ok(v)`` / ``ok()`` / ``error(e)``.

        The only producers of a ``result`` value, context-typed like a bare
        struct literal: the caller passes the ``result<T, E>`` /
        ``result<E>`` the position fixes (:meth:`result_literal_adapts` gates
        the routing; :meth:`gen_expr` rejects a constructor with no such
        context). The ok value coerces to ``T`` -- literal adaptation
        included, so ``ok({ x = 1 })`` builds a struct ``T`` and ``ok("hi")``
        borrows into a ``slice<char>`` ``T``. The error value coerces to
        ``E``, so any expression of the declared error type works and a raw
        integer rejects.

        Lowering: a zeroed temporary (unused payload bytes stay
        deterministic), the tag byte (0 ok, 1 error), and the value stored
        into its union arm through a cast of the payload's address -- exactly
        a union member store, never a GEP into the other arm.

        Args:
            expr: The ``ResultLit`` node.
            result_t: The resolved (const-stripped) result type the position
                fixes.

        Returns:
            The constructed result as a ``TypedValue``.

        Raises:
            LangError: On ``ok()`` where the result has an ok arm, ``ok(v)``
                where it has none, ``error()`` with no value, or a value that
                does not coerce to its arm.
        """
        arm = self.result_arm_type(
            expr.kind, expr.value is not None, result_t, expr.line
        )
        tv = None
        if expr.value is not None:
            label = f"{expr.kind} value"
            if (
                self.result_literal_adapts(expr.value, arm)
                or self.struct_literal_adapts(expr.value, arm)
                or self.str_literal_adapts(expr.value, arm)
                or self.array_literal_adapts(expr.value, arm)
            ):
                tv = self.gen_adapted_literal(expr.value, arm, expr.line)
            else:
                tv = self.gen_expr(expr.value)
            tv = self.coerce(tv, arm, expr.line, label)
        return self.emit_result_aggregate(result_t, expr.kind, tv)

    def result_arm_type(
        self, kind: str, has_value: bool, result_t: LangType, line: int
    ) -> "LangType | None":
        """Validate a constructor's arity and return the arm its value fills.

        Shared by the eager sink path (:meth:`gen_result_lit`) and the pending
        path (:meth:`finalize_pending`): ``ok(v)`` fills the ok arm of a
        ``result<T, E>``, ``error(e)`` the error arm, and the argument-less
        ``ok()`` fills nothing (the error-only ``result<E>``).

        Args:
            kind: ``"ok"`` or ``"error"``.
            has_value: Whether the constructor carried an argument.
            result_t: The resolved result type the arm belongs to.
            line: Source line for diagnostics.

        Returns:
            The arm's ``LangType``, or ``None`` for the value-less ``ok()``.

        Raises:
            LangError: On ``ok()`` where the result has an ok arm, ``ok(v)``
                where it has none, or ``error()`` with no value.
        """
        two_arms = len(result_t.args) == 2
        if kind == "ok":
            if two_arms and not has_value:
                raise LangError(
                    f"ok() takes the ok value here: a {result_t} carries one "
                    "(ok() with no value is for the error-only result<E>)",
                    line,
                )
            if not two_arms and has_value:
                raise LangError(f"a {result_t} has no ok value; write ok()", line)
            return result_t.args[0] if two_arms else None
        if not has_value:
            raise LangError(
                "error() takes the error value, e.g. error(my_error::NOT_FOUND)",
                line,
            )
        return result_t.args[-1]

    def emit_result_aggregate(
        self, result_t: LangType, kind: str, tv: "TypedValue | None"
    ) -> TypedValue:
        """Build the ``{ tag, payload }`` result value from a settled arm value.

        A zeroed temporary, the tag byte (0 ok, 1 error), and the value stored
        into its union arm through a cast of the payload's address -- exactly a
        union member store, never a GEP into the other arm. Shared by the eager
        and pending construction paths.

        Args:
            result_t: The resolved result type to build.
            kind: ``"ok"`` or ``"error"`` (fixes the tag).
            tv: The arm value already coerced to its arm type, or ``None`` for
                the value-less ``ok()``.

        Returns:
            The constructed result as a ``TypedValue``.
        """
        slot = self.builder.alloca(result_t.ir)
        if over_aligned(result_t):
            slot.align = type_align(result_t)
        self.builder.store(ir.Constant(result_t.ir, None), slot)
        indices = result_t.elem_indices
        if kind == "error":
            tag_addr = self.builder.gep(
                slot, [I32_ZERO, ir.Constant(ir.IntType(32), indices[0])],
                inbounds=True,
            )
            self.builder.store(ir.Constant(UINT8.ir, 1), tag_addr)
        if tv is not None:
            payload_addr = self.builder.gep(
                slot, [I32_ZERO, ir.Constant(ir.IntType(32), indices[1])],
                inbounds=True,
            )
            arm_addr = self.builder.bitcast(
                payload_addr, tv.type.ir.as_pointer()
            )
            self.builder.store(tv.value, arm_addr)
        return TypedValue(self.builder.load(slot), result_t)

    def gen_result_pending(self, expr: ResultLit) -> TypedValue:
        """Evaluate a bare ``ok(v)`` / ``error(e)`` into a *pending* result.

        Reached only when a constructor is not in a direct result sink (a typed
        ``let``/return/assignment/field/argument routes the node through
        :meth:`gen_result_lit` before it hits :meth:`gen_expr`). Realizes the
        builtin signatures ``ok<T, E>(v: T) -> result<T, E>`` and ``error<T,
        E>(e: E) -> result<T, E>``: the argument fixes one arm, the other stays
        a free parameter bound later -- by the sibling of a ternary
        (:meth:`unify_branches`) or the expected result type at a sink
        (:meth:`coerce`). The payload is evaluated here, in this block, so a
        ternary arm's side effects stay on its own path; the aggregate is not
        built until the result type is fixed.

        Args:
            expr: The ``ResultLit`` node.

        Returns:
            A :data:`PENDING_RESULT`-typed, adaptable ``TypedValue`` carrying a
            :class:`ResultPending`.
        """
        payload = None if expr.value is None else self.gen_expr(expr.value)
        return TypedValue(
            None,
            PENDING_RESULT,
            adaptable=True,
            result_pending=ResultPending(expr.kind, payload),
        )

    def finalize_pending(
        self, pending: ResultPending, result_t: LangType, line: int
    ) -> TypedValue:
        """Build a pending ``ok``/``error`` once its result type is known.

        The pending sibling of :meth:`gen_result_lit`: the payload was already
        evaluated (in its own block, see :meth:`gen_result_pending`), so here it
        only coerces to the arm the now-known ``result_t`` fixes and the
        aggregate is emitted. Reached from :meth:`coerce` when a pending meets
        its expected result type.

        Args:
            pending: The pending constructor.
            result_t: The resolved (const-stripped) result type to build.
            line: Source line for diagnostics.

        Returns:
            The constructed result as a ``TypedValue``.
        """
        arm = self.result_arm_type(
            pending.kind, pending.payload is not None, result_t, line
        )
        tv = pending.payload
        if tv is not None:
            tv = self.coerce(tv, arm, line, f"{pending.kind} value")
        return self.emit_result_aggregate(result_t, pending.kind, tv)

    def result_literal_adapts(self, expr, expected: LangType) -> bool:
        """Whether a result constructor ``ok(...)``/``error(...)`` adapts here.

        The result sibling of :meth:`struct_literal_adapts`: a constructor
        takes the ``result<...>`` type the position fixes -- a typed ``let``,
        an assignment, a ``return``, a function argument, or a struct field --
        and builds it (see :meth:`gen_result_lit`). The check stays coarse
        (any result target), so a wrong arity or a value of the wrong type
        reaches :meth:`gen_result_lit` and gets a precise error.

        Args:
            expr: The initializer/argument/field expression.
            expected: The type the context expects.

        Returns:
            ``True`` if ``expr`` is a ``ResultLit`` and ``expected`` is a
            ``result<...>`` type.
        """
        return isinstance(expr, ResultLit) and is_result(strip_const(expected))

    def eval_struct_field(self, struct_type, fname, value_expr, line):
        """Lower a struct-literal field value against its declared field type.

        A string or array literal whose field is a char slice / ``slice<T>``
        borrows into that field with no explicit ``as`` -- the struct-literal
        position of the same adaptation a ``let``, return, element, or argument
        allows (see :meth:`gen_borrow_slice`). Every other field lowers the
        ordinary way; :meth:`store_struct_field` then coerces the result to the
        field type.

        Args:
            struct_type: The resolved (concrete) struct/union type.
            fname: The field being filled.
            value_expr: The field's raw value expression.
            line: Source line for diagnostics.

        Returns:
            The field value as a ``TypedValue``.
        """
        _index, ftype = self.struct_field(struct_type, fname, line)
        if (
            self.struct_literal_adapts(value_expr, ftype)
            or self.str_literal_adapts(value_expr, ftype)
            or self.array_literal_adapts(value_expr, ftype)
            or self.result_literal_adapts(value_expr, ftype)
        ):
            return self.gen_adapted_literal(value_expr, ftype, line)
        return self.gen_expr(value_expr)

    def defers_field_literal(self, value_expr, pattern) -> bool:
        """Whether a generic struct-literal field sits out type inference.

        A string or array literal whose declared field pattern is a
        ``slice<...>`` is an inference non-participant: it carries no type that
        may anchor a parameter (an array literal has no element inference, a
        string literal would only bind ``char*``), so it is skipped here and
        borrowed later, once the field's type is fixed by the other fields, a
        declared default, or explicit type args -- exactly as an untyped
        constant is skipped in :meth:`infer_struct_lit_type`. The check is on
        the *declared pattern*, not a resolved type, because the struct type is
        still being inferred; a literal against a bare type parameter (``box {
        v = "hi" }``, field ``v: T``) is therefore not a slice pattern and does
        participate -- ``"hi"`` binds ``T = char*``, keeping ``box<char*>``.

        Args:
            value_expr: The field's raw value expression.
            pattern: The field's declared type ``TypeRef`` (or ``None`` when the
                literal names a field the struct does not declare).

        Returns:
            ``True`` when the field should be deferred to the borrow pass.
        """
        if not (isinstance(pattern, TypeRef) and pattern.name == "slice"):
            return False
        if isinstance(value_expr, Ternary):
            return self.defers_field_literal(
                value_expr.then, pattern
            ) and self.defers_field_literal(value_expr.otherwise, pattern)
        return isinstance(value_expr, (StrLit, ArrayLit))

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
            tv = self.eval_struct_field(
                struct_type, fname, default_expr, default_expr.line
            )
            self.store_struct_field(
                slot, struct_type, fname, tv, default_expr.line,
                "default for field",
            )

    def lookup_struct_decl(self, name: str) -> "StructDecl | UnionDecl | None":
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
        # flow-narrowed fact on the shadowed name, and for any projection
        # fact rooted at it (the path now reads through different storage).
        self.nonnull_locals.discard(name)
        self.narrowed_nonnull.discard(name)
        self.kill_paths_rooted(name)
        # A shadowing let over a plain parameter is a local: it must not
        # keep qualifying as a mut-return formation root.
        self.formation_params.pop(name, None)

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
        # A pending ok(...)/error(...) settles here: if the position expects a
        # result, its free arm is bound and the aggregate is built; otherwise
        # the constructor had no result context at all.
        if tv.result_pending is not None:
            stripped = strip_const(expected)
            if is_result(stripped):
                return self.finalize_pending(tv.result_pending, stripped, line)
            raise LangError(
                f"{tv.result_pending.kind}(...) has no result type here; use it "
                "where one is expected -- a typed let, assignment, return, "
                "argument, or field",
                line,
            )
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
        # A function value flows contravariantly along the @nonnull axis: a
        # plain value may enter an annotated slot (the contract only adds a
        # call-site proof obligation the plain function tolerates), never the
        # reverse -- a call through the plain type would skip the proof. The
        # contract lives outside the LLVM type, so the value passes through
        # retyped. The mut/const hidden-reference shape must match exactly:
        # it is a calling convention, not a contract (signatures store the
        # plain parameter types, so this gate is what keeps fn(mut char)
        # from silently retyping to fn(char) across the ABI). Flat variance:
        # fn values only, no deep variance through slices, nested fn types,
        # or any.
        if (
            is_function(expected)
            and is_function(tv.type)
            and tv.type.signature == expected.signature
            and tv.type.mutref == expected.mutref
            and tv.type.constref == expected.constref
            and tv.type.mutret == expected.mutret
            and tv.type.nonnull <= expected.nonnull
        ):
            return TypedValue(tv.value, expected)
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
        # call-site inference uses. `any` to `any` already returned above. A
        # `const any` target is a by-reference borrow position, so a struct
        # may box into it (by hidden reference); a bare (owning) `any` cannot.
        if is_any(expected):
            boxed = self.gen_box_any(tv, line, borrow=expected.const)
            if expected is ANY:
                return boxed
            return TypedValue(boxed.value, expected)  # the const any form
        self.reject_nonnull_drop(tv.type, expected, line, context)
        raise LangError(f"{context}: expected {expected}, got {tv.type}", line)

    def reject_nonnull_drop(
        self, src: LangType, expected: LangType, line: int, context: str
    ):
        """Report a function-type mismatch between two same-signature types.

        The hinted variant of the coercion failure, shared by :meth:`coerce`
        and :meth:`const_coerce`, for two function-pointer types sharing one
        underlying signature. A differing ``mut``/``const`` hidden-reference
        shape, or a differing ``mut`` return, is a calling-convention
        mismatch: the two types are simply not convertible, and the error
        says so with **no** hatch (an ``as`` here would be a miscompile
        recipe -- the callee reads a pointer where the caller passed a
        value, or vice versa). Otherwise the mismatch is the dropping
        direction of the ``@nonnull`` contravariant rule, and the error says
        why it is banned and names the explicit ``as`` hatch.

        Args:
            src: The value's function-pointer type.
            expected: The target function-pointer type it may not become.
            line: Source line for diagnostics.
            context: A label describing the site, for the error message.

        Raises:
            LangError: When the mismatch is a hidden-reference shape change or
                exactly a dropped ``@nonnull``.
        """
        if (
            is_function(expected)
            and is_function(src)
            and src.signature == expected.signature
        ):
            if src.mutref != expected.mutref or src.constref != expected.constref:
                kind = "mut" if src.mutref != expected.mutref else "const"
                raise LangError(
                    f"{context}: expected {expected}, got {src} (a {kind} "
                    "parameter is passed by hidden reference, a different "
                    "calling convention; the types are not convertible)",
                    line,
                )
            if src.mutret != expected.mutret:
                raise LangError(
                    f"{context}: expected {expected}, got {src} (a mut "
                    "return is passed as a pointer to the returned storage, "
                    "a different calling convention; the types are not "
                    "convertible)",
                    line,
                )
            raise LangError(
                f"{context}: expected {expected}, got {src} (a @nonnull "
                "contract cannot be dropped: a call through the plain type "
                "would skip the call-site null proof; cast with "
                f"'as {expected}' to strip it explicitly, making a null "
                "argument undefined behavior)",
                line,
            )

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
        intervening defers), ``unreachable``, ``defer`` registration, bare
        blocks, and expression statements.

        Args:
            stmt: The statement AST node to generate.

        Raises:
            LangError: On a type mismatch or misuse (e.g. ``break`` outside a
                loop), surfaced from the per-statement handling.
        """
        if isinstance(stmt, Return):
            # A return is always a jump out of a running defer body (there
            # are no nested functions): it would re-unwind the very scope
            # whose defers are running (see run_deferred_scope).
            if self.defer_marks:
                raise LangError(
                    "'return' inside a defer body cannot exit the "
                    "enclosing function",
                    stmt.line,
                )
            if self.current_noreturn is not None:
                raise LangError(
                    f"cannot return from @noreturn function "
                    f"{self.current_noreturn!r} (it promises never to return)",
                    stmt.line,
                )
            if isinstance(stmt.value, Except):
                # `return try f() except (err) { H } [else { S }];`: desugar to
                # a hidden let (evaluate once, run the handler/else arms, fill
                # the ok slot) followed by an ordinary return of the slot, so
                # the normal return path owns defers, coercion to the return
                # type, and the mut-return discipline. The name is not a
                # lexable identifier, so it cannot collide.
                hidden = "0except"
                self.gen_statement(Let(hidden, None, stmt.value, stmt.line))
                try:
                    # When the handler diverges and a diverging else keeps
                    # even the ok arm from falling through, the join is
                    # already unreachable and there is nothing to return.
                    if not self.builder.block.is_terminated:
                        self.gen_statement(
                            Return(Var(hidden, stmt.line), stmt.line)
                        )
                finally:
                    del self.locals[hidden]
                    self.scope_names.discard(hidden)
                return
            if stmt.value is None:
                if self.ret_type is not VOID:
                    raise LangError(f"return needs a {self.ret_type} value", stmt.line)
                self.run_defers_through(0)  # all enclosing blocks
                if not self.builder.block.is_terminated:
                    self.builder.ret_void()
            elif self.ret_mut:
                self.gen_mut_return(stmt)
            else:
                # A direct `return [...] as slice<T>` hands out a view into
                # this call's hidden backing array, which dies with the
                # return and is named by nothing else -- always a dangling
                # slice, so it is rejected up front. A named local's borrow
                # (`return xs as slice<T>`) stays legal: the caller can at
                # least reason about the local's storage.
                if (
                    isinstance(stmt.value, Cast)
                    and borrows_array_literal(stmt.value.value)
                    and is_slice(self.lang_type(stmt.value.type_name, stmt.line))
                ):
                    raise LangError(
                        "cannot return an array literal borrowed as a slice: "
                        "the view would point into this call's hidden backing "
                        "array, which dies with the return; bind the literal "
                        "to a named array or an owned list<T> that outlives "
                        "the call",
                        stmt.line,
                    )
                # Evaluate the result before the defers run, so a defer that
                # frees a buffer cannot clobber what is being returned.
                if (
                    self.struct_literal_adapts(stmt.value, self.ret_type)
                    or self.str_literal_adapts(stmt.value, self.ret_type)
                    or self.result_literal_adapts(stmt.value, self.ret_type)
                ):
                    tv = self.gen_adapted_literal(stmt.value, self.ret_type, stmt.line)
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
            # An emit inside a defer body may only target a block expression
            # opened inside that body (see run_deferred_scope and Break).
            if (
                self.defer_marks
                and len(self.block_exprs) <= self.defer_marks[-1][1]
            ):
                raise LangError(
                    "'emit' inside a defer body cannot exit the "
                    "enclosing block expression",
                    stmt.line,
                )
            ctx = self.block_exprs[-1]
            if ctx.no_value is not None:
                # A statement-position except over a result<E>: the handler
                # has no ok value to fall back to.
                raise LangError(
                    f"a {ctx.no_value} has no ok value; the except handler "
                    "has nothing to emit",
                    stmt.line,
                )
            # Evaluate the value before the defers run, so a defer cannot clobber
            # what is being emitted (as with a return value).
            if ctx.type is None:
                tv = self.gen_expr(stmt.value)
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
                # A preset type (an except handler's fallback slot) makes the
                # emit a typed sink: adapted literals route exactly as in a
                # typed let -- `emit "hi"` borrows into a slice<char> slot,
                # `emit ok(v)` builds a nested result -- before coercion.
                if (
                    self.struct_literal_adapts(stmt.value, ctx.type)
                    or self.str_literal_adapts(stmt.value, ctx.type)
                    or self.array_literal_adapts(stmt.value, ctx.type)
                    or self.result_literal_adapts(stmt.value, ctx.type)
                ):
                    tv = self.gen_adapted_literal(stmt.value, ctx.type, stmt.line)
                else:
                    tv = self.coerce(
                        self.gen_expr(stmt.value), ctx.type, stmt.line, "emit"
                    )
            ctx.emitted = True
            self.gen_store(tv.value, ctx.slot)
            self.run_defers_through(ctx.defer_depth)
            if not self.builder.block.is_terminated:
                self.builder.branch(ctx.cont_bb)
        elif isinstance(stmt, Let):
            if stmt.extra or stmt.rest:
                self.gen_destructure(stmt)
                return
            if stmt.name in self.scope_names:
                raise LangError(
                    f"variable {stmt.name!r} already declared in this scope", stmt.line
                )
            if isinstance(stmt.value, Except):
                self.gen_let_except(stmt)
                return
            if isinstance(stmt.value, Call):
                canonical = self.ctor_sugar_target(stmt.value)
                if canonical is not None:
                    # `let p = S(args);` constructs straight into p's slot --
                    # no temporary, no copy. Load-bearing beyond IR quality:
                    # the constructor's `mut self` is p's own storage, so a
                    # callee that publishes interior addresses (or a future
                    # RAII hook) observes the final object.
                    slot, built = self.gen_ctor_call(stmt.value, canonical)
                    final = built
                    if stmt.type_name is not None:
                        declared = self.lang_type(stmt.type_name, stmt.line)
                        if declared is VOID:
                            raise LangError(
                                "cannot declare a void variable", stmt.line
                            )
                        if is_array(declared):
                            raise LangError(
                                f"an array variable is initialized from an "
                                f"array literal, not a {built}",
                                stmt.line,
                            )
                        if strip_const(declared) == built:
                            final = declared  # keep a written const view
                        else:
                            # A mismatched annotation coerces (boxing into
                            # `any`, or erroring) exactly as the plain path
                            # would; the constructed slot is the temporary.
                            tv = self.coerce(
                                self.value_at(slot, built),
                                declared,
                                stmt.line,
                                f"let {stmt.name}",
                            )
                            slot = self.builder.alloca(
                                tv.type.ir, name=stmt.name
                            )
                            if over_aligned(tv.type):
                                slot.align = type_align(tv.type)
                            self.builder.store(tv.value, slot)
                            final = tv.type
                    self.bind_local(stmt.name, slot, final)
                    return
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
                if is_slice(declared):
                    # `let s: slice<int32> = [0x10, 0x1F, 0xFF];`: the literal
                    # adapts to the annotated slice (Stage 1), borrowing a
                    # hidden backing array in this frame (see gen_borrow_slice).
                    tv = self.gen_borrow_slice(stmt.value, declared, stmt.line)
                    slot = self.builder.alloca(declared.ir, name=stmt.name)
                    self.builder.store(tv.value, slot)
                    self.bind_local(stmt.name, slot, declared)
                    return
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
                if isinstance(stmt.value, FStrLit):
                    raise LangError(FSTRING_MISPLACED, stmt.line)
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
                if self.str_literal_adapts(stmt.value, declared) or (
                    self.array_literal_adapts(stmt.value, declared)
                ):
                    # `let s: slice<char> = cond ? "a" : "b";`: every arm is a
                    # string literal, so the ternary adapts arm by arm (Stage
                    # 4), each arm borrowing its constant's bytes (NUL dropped).
                    # An all-array-literal ternary adapts the same way, each
                    # arm borrowing its own backing array (Stage 1).
                    tv = self.gen_borrow_slice(stmt.value, declared, stmt.line)
                    slot = self.builder.alloca(declared.ir, name=stmt.name)
                    self.builder.store(tv.value, slot)
                    self.bind_local(stmt.name, slot, declared)
                    return
            if stmt.type_name is not None and (
                self.struct_literal_adapts(
                    stmt.value, self.lang_type(stmt.type_name, stmt.line)
                )
                or self.result_literal_adapts(
                    stmt.value, self.lang_type(stmt.type_name, stmt.line)
                )
            ):
                # `let p: point = { x = 1, y = 2 };`: the bare literal builds the
                # annotated struct (the coerce below re-adds any `const`), the
                # aggregate sibling of the slice adaptations above; likewise
                # `let t: tuple<int64, char> = (1, 'x');` builds the tuple, and
                # `let r: result<int32, my_error> = ok(1);` the result.
                declared = self.lang_type(stmt.type_name, stmt.line)
                tv = self.gen_adapted_literal(stmt.value, declared, stmt.line)
            else:
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
            elif tv.result_pending is not None:
                raise LangError(
                    f"{tv.result_pending.kind}(...) has no result type here; use "
                    "it where one is expected -- a typed let, assignment, "
                    "return, argument, or field",
                    stmt.line,
                )
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
            slot, var_type, volatile = self.var_addr(stmt.name, stmt.line)
            if var_type.const:
                raise LangError(
                    f"cannot assign to read-only variable {stmt.name!r}", stmt.line
                )

            # A reassignment may store null: any flow-narrowed fact dies with
            # the store, with every projection fact rooted at the name -- and,
            # for a struct whose storage is reachable through a pointer (an
            # address-taken local, a mut parameter, or a global), every
            # projection fact at all, since the overwrite can rewrite a field
            # some other fact reads through an alias. The kill runs after the
            # right-hand side is *judged* (it evaluates before the store, so
            # `cur = cur->next` reads through the still-narrowed name) but
            # before anything writes the target's storage.
            def kill_target_facts():
                self.narrowed_nonnull.discard(stmt.name)
                self.kill_paths_rooted(stmt.name)
                if is_aggregate(strip_const(var_type)) and (
                    stmt.name not in self.locals
                    or stmt.name in self.addr_taken
                    or stmt.name in self.mut_locals
                ):
                    self.narrowed_paths.clear()

            if (
                self.struct_literal_adapts(stmt.value, var_type)
                or self.str_literal_adapts(stmt.value, var_type)
                or self.result_literal_adapts(stmt.value, var_type)
            ):
                # `s = "hi";`: the literal repoints s at its global string
                # constant (static lifetime), the same borrow a `let`/argument
                # already does -- safe even when the target outlives the frame.
                # `p = { x = 1, y = 2 };` likewise rebuilds the struct in
                # place, so here the facts die before the rebuild starts.
                kill_target_facts()
                tv = self.gen_adapted_literal(stmt.value, var_type, stmt.line)
            else:
                tv = self.coerce(
                    self.gen_expr(stmt.value),
                    var_type,
                    stmt.line,
                    f"assignment to {stmt.name}",
                )
                kill_target_facts()
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
                        self.retract_narrowed(added)
                        then_diverged = self.builder.block.is_terminated
                    with otherwise:
                        added = self.narrow_nonnull(else_facts)
                        self.gen_block(stmt.otherwise)
                        self.retract_narrowed(added)
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
                    self.retract_narrowed(added)
                    then_diverged = self.builder.block.is_terminated
                # The C-idiomatic early guard: a diverging `if (p == null)`
                # body with no else proves p non-null for the remainder of
                # the enclosing scope (the fact ends with it -- gen_block
                # intersects on exit). Assignments inside the diverging body
                # cannot reach the remainder, so they do not invalidate it.
                if then_diverged:
                    self.narrow_nonnull(else_facts)
        elif isinstance(stmt, While):
            kind = "until" if stmt.until else "while"
            # A loop's condition and body re-run on the back edge, where a
            # later iteration may already have invalidated a fact proved
            # before the loop -- so a pre-scan drops exactly the facts the
            # loop could invalidate (an assignment, a shadowing let, or a
            # mut lend anywhere in the condition or body); the rest survive
            # the loop, and past it. Projection facts have no pre-scan yet:
            # any call or through-memory store in the loop could null the
            # field on a later iteration, so all of them drop wholesale
            # (header facts below re-prove per back edge regardless).
            self.narrowed_nonnull -= self.loop_kill_set(stmt)
            self.narrowed_paths.clear()
            # Constant-condition folding: a condition that folds to
            # always-run (`while (true)`, `while (1)`, `until (false)`, a
            # const reference) never takes its exit edge, so the cbranch
            # becomes an unconditional branch -- and when no `break` can
            # target this loop, the end block is not created at all: the
            # loop diverges, which is what lifts the missing-return /
            # missing-emit checks for code that never falls out of it and
            # funnels any trailing statements into gen_block's dead-code
            # skip. A `break` anywhere in the body (a `case` arm, a nested
            # block expression, a `defer`) keeps the end block: it is then
            # reachable and the code after the loop stays live. `return`,
            # `emit`, and `@noreturn` calls leave by their own edges and
            # never touch the end block, so they do not gate the fold.
            truth = self.const_cond_truth(stmt.cond)
            forever = truth is not None and truth != stmt.until
            breaks = contains_break(stmt.body)
            cond_bb = self.builder.append_basic_block(f"{kind}.cond")
            body_bb = self.builder.append_basic_block(f"{kind}.body")
            end_bb = None
            if not forever or breaks:
                end_bb = self.builder.append_basic_block(f"{kind}.end")
            self.builder.branch(cond_bb)
            self.builder.position_at_end(cond_bb)
            if forever:
                # The header block survives as the `continue` target (and
                # the back edge's); simplifycfg merges the trivial branch.
                self.builder.branch(body_bb)
            else:
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
            self.retract_narrowed(added)
            if not self.builder.block.is_terminated:
                self.builder.branch(cond_bb)
            # A fully folded loop (forever, no break) has no end block: the
            # builder stays in the body's terminated block, so the statement
            # counts as diverging (no trailing return/emit needed, trailing
            # statements are dead).
            if end_bb is not None:
                self.builder.position_at_end(end_bb)
                # Post-exit narrowing: the normal exit edge leaves the
                # condition false (`while`) / true (`until`), so
                # `while (p == null) { ... }` proves p after the loop no
                # matter what the body did -- unless a `break` can reach the
                # end without re-testing the condition.
                if not breaks:
                    self.narrow_nonnull(
                        self.narrowable_guard_names(
                            stmt.cond, "!=" if stmt.until else "=="
                        )
                    )
        elif isinstance(stmt, Conditional):
            # Compile-time @if: emit only the live branch's statements, inline
            # in the current scope. The dead branch is never type-checked.
            taken = stmt.then if self.eval_static_cond(stmt.cond) else stmt.otherwise
            prev = None
            for inner in taken:
                if self.builder.block.is_terminated:
                    # This loop has its own skip, so a dead tail inside a
                    # taken @if arm reports here (-Wdead-code), not in
                    # gen_block; the dead branch was never walked at all.
                    self.warn_dead_code(prev, inner)
                    break
                self.gen_statement(inner)
                prev = inner
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
            # A break inside a defer body may only target a loop opened
            # inside that body: jumping to an outer loop would re-unwind the
            # scope whose defers are being run (see run_deferred_scope).
            if self.defer_marks and len(self.loops) <= self.defer_marks[-1][0]:
                raise LangError(
                    "'break' inside a defer body cannot exit the "
                    "enclosing loop",
                    stmt.line,
                )
            self.run_defers_through(self.loops[-1][2])  # the loop body and inner
            if not self.builder.block.is_terminated:
                self.builder.branch(self.loops[-1][1])
        elif isinstance(stmt, Continue):
            if not self.loops:
                raise LangError("'continue' outside a loop", stmt.line)
            if self.defer_marks and len(self.loops) <= self.defer_marks[-1][0]:
                raise LangError(
                    "'continue' inside a defer body cannot continue the "
                    "enclosing loop",
                    stmt.line,
                )
            self.run_defers_through(self.loops[-1][2])
            if not self.builder.block.is_terminated:
                self.builder.branch(self.loops[-1][0])
        elif isinstance(stmt, Unreachable):
            # The author asserts this path never executes: terminate the
            # block (the statement diverges, so no trailing return is needed
            # and dead code after it is skipped like after a return).
            # Reaching it at runtime is undefined behavior, like C's
            # __builtin_unreachable. Defers deliberately do not run -- there
            # is no control flow to unwind on a path that never happens.
            self.builder.unreachable()
        elif isinstance(stmt, Case):
            subject = self.gen_expr(stmt.subject)
            if is_aggregate(subject.type) or subject.type is VOID:
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
            self.warn_unchecked_deref(stmt.ptr, stmt.line)
            if ptr.type.pointee.const:
                raise LangError(
                    "cannot assign through a pointer to a read-only "
                    f"{ptr.type.pointee}",
                    stmt.line,
                )
            if (
                self.struct_literal_adapts(stmt.value, ptr.type.pointee)
                or self.str_literal_adapts(stmt.value, ptr.type.pointee)
                or self.result_literal_adapts(stmt.value, ptr.type.pointee)
            ):
                # `*out = "hi";`: repoint the slice behind the pointer at the
                # literal's global constant -- static lifetime, so safe even
                # when the pointee outlives this frame. `*out = { ... };` writes
                # the built struct through the pointer.
                value = self.gen_adapted_literal(
                    stmt.value, ptr.type.pointee, stmt.line
                )
            else:
                value = self.coerce(
                    self.gen_expr(stmt.value),
                    ptr.type.pointee,
                    stmt.line,
                    "assignment through pointer",
                )
            self.gen_store(value.value, ptr.value, volatile=ptr.type.pointee.volatile)
            # A through-memory store can land in any guarded field (the
            # pointer may alias one): every projection fact dies.
            self.narrowed_paths.clear()
        elif isinstance(stmt, StoreIndex):
            base_t = self.lvalue_type(stmt.base)
            # An array or tuple element lives in the base's own storage, so a
            # const parameter's read-only-ness extends to it; a pointer or
            # slice element sits behind a pointer hop the const does not cover.
            in_storage = base_t is not None and (
                is_array(base_t) or is_tuple(strip_const(base_t))
            )
            if in_storage and self.writes_const(stmt.base):
                raise LangError(
                    "cannot assign to an element of a const parameter", stmt.line
                )
            addr, element, align, volatile = self.gen_index_addr(
                stmt.base, stmt.index, stmt.line, store=True
            )
            if element.const:
                raise LangError(
                    "cannot assign through a read-only slice<const T>", stmt.line
                )
            if (
                self.struct_literal_adapts(stmt.value, element)
                or self.str_literal_adapts(stmt.value, element)
                or self.result_literal_adapts(stmt.value, element)
            ):
                # `a[i] = "hi";`: the element is a char slice repointed at the
                # literal's global constant (static lifetime). `a[i] = { ... };`
                # stores the built struct into the element.
                value = self.gen_adapted_literal(stmt.value, element, stmt.line)
            else:
                value = self.coerce(
                    self.gen_expr(stmt.value),
                    element,
                    stmt.line,
                    "assignment to element",
                )
            self.gen_store(value.value, addr, align=align, volatile=volatile)
            self.narrowed_paths.clear()  # an element store may alias a field
        elif isinstance(stmt, StoreMember):
            if not stmt.arrow and self.writes_const(stmt.base):
                raise LangError(
                    "cannot assign to a field of a const parameter", stmt.line
                )
            addr, ftype, align, volatile = self.gen_member_addr(
                stmt.base, stmt.field, stmt.arrow, stmt.line
            )
            if (
                self.struct_literal_adapts(stmt.value, ftype)
                or self.str_literal_adapts(stmt.value, ftype)
                or self.result_literal_adapts(stmt.value, ftype)
            ):
                # `c.name = "hi";`: repoint the char-slice field at the
                # literal's global constant, the same borrow the struct-literal
                # field (`cmd { name = "hi" }`) already does. `s.origin = { ... }`
                # writes a nested struct into the field.
                value = self.gen_adapted_literal(stmt.value, ftype, stmt.line)
            else:
                value = self.coerce(
                    self.gen_expr(stmt.value),
                    ftype,
                    stmt.line,
                    f"assignment to field {stmt.field!r}",
                )
            self.gen_store(value.value, addr, align=align, volatile=volatile)
            # The stored-to field (or a union sibling, or an alias through
            # another base) may be a guarded one: every projection fact dies.
            self.narrowed_paths.clear()
        elif isinstance(stmt, StoreCall):
            # `f(s, i) = v;` -- the call's mut return is the target: address
            # it once (gen_addr's Call arm checks the resolved callee
            # actually returns mut), coerce, store through it.
            addr, t, _, _ = self.gen_addr(stmt.call, stmt.line)
            if (
                self.struct_literal_adapts(stmt.value, t)
                or self.str_literal_adapts(stmt.value, t)
                or self.result_literal_adapts(stmt.value, t)
            ):
                # `f(...) = "hi";`: repoint the char slice behind the mut
                # return at the literal's global constant (static lifetime).
                # `f(...) = { ... };` stores the built struct through it.
                value = self.gen_adapted_literal(stmt.value, t, stmt.line)
            else:
                value = self.coerce(
                    self.gen_expr(stmt.value),
                    t,
                    stmt.line,
                    "assignment through a mut return",
                )
            self.gen_store(value.value, addr)
            # A store through a returned reference can land in any guarded
            # field (the reference may alias one): every projection fact
            # dies, as at any through-memory store.
            self.narrowed_paths.clear()
        elif isinstance(stmt, TryStmt):
            self.gen_try_stmt(stmt)
        elif isinstance(stmt, ExprStmt):
            if isinstance(stmt.expr, Except):
                self.gen_except_stmt(stmt.expr)
            elif isinstance(stmt.expr, Try):
                # `try f();` -- propagate or continue; over an arity-2
                # result the ok value is discarded like any expression
                # statement's.
                self.gen_try_discard(stmt.expr)
            else:
                value = self.gen_expr(stmt.expr)
                if value.result_pending is not None:
                    # A bare `ok(...);`/`error(...);` has no result context.
                    raise LangError(
                        f"{value.result_pending.kind}(...) has no result type "
                        "here; use it where one is expected -- a typed let, "
                        "assignment, return, argument, or field",
                        stmt.line,
                    )
                # A result produced in statement position and dropped is the
                # accidental-error-discard hole: warn under -Wunused-result.
                # try/except/destructure/binding all consume elsewhere; only a
                # truly-dropped result (`f();`) reaches here.
                if is_result(strip_const(value.type)):
                    self.warn_unused_result(stmt.line)
        else:
            raise LangError(f"cannot compile statement {stmt!r}", stmt.line)

    def type_name_resolves(self, name: str) -> bool:
        """Whether a bare name resolves as a type in the current context.

        The generic-arm detection predicate: a builtin, a (generic) struct,
        an enum, a type alias, a reserved builtin type name, or an active
        enclosing type-parameter binding. Mirrors :meth:`lang_type`'s
        resolution order without instantiating anything.

        Args:
            name: The bare type name as written.

        Returns:
            ``True`` when :meth:`lang_type` would resolve the name.
        """
        return (
            name in self.type_bindings
            or name in TYPES
            or name in RESERVED_TYPE_NAMES
            or (self.current_source, name) in self.static_structs
            or name in self.struct_templates
            or self.lookup_enum(name) is not None
            or self.lookup_alias(name) is not None
        )

    def generic_arm_pattern(self, type_refs: list) -> "tuple[str, bool] | None":
        """Detect whether a ``case type`` arm introduces a generic pattern.

        The rule (no new syntax): in arm-type position, a bare name that
        resolves is a concrete arm, and an *unresolved* bare name with no
        generic arguments, array dimensions, or function shape, no ``const``
        qualifier, and zero or one ``*`` introduces an arm-scoped type
        parameter -- ``when T v:`` (every tag) or ``when T* ptr:`` (pointer
        tags). Inside a generic function, ``when T v:`` therefore stays a
        concrete arm per instantiation: the enclosing binding resolves. A
        generic arm binds exactly one pattern, so a multi-type list never
        matches (its unresolved members keep the ``unknown type`` error).
        Any other unresolved shape (e.g. two stars) falls through to that
        same error in :meth:`lang_type`.

        Args:
            type_refs: The arm's parsed type list.

        Returns:
            ``(type-parameter name, pointer_only)`` for a generic arm, else
            ``None``.
        """
        if len(type_refs) != 1:
            return None
        ref = type_refs[0]
        if (
            ref.params is None
            and not ref.args
            and not ref.dims
            and not ref.const
            and ref.stars <= 1
            and not self.type_name_resolves(ref.name)
        ):
            return ref.name, ref.stars == 1
        return None

    def gen_case_type(self, stmt: CaseType):
        """Lower a ``case type`` type-switch over an ``any`` subject.

        Rides on the same shape as the value ``case``: the subject's tag is
        loaded once, each arm compares it against the arm type's constant tag
        (an integer equality chain), and a matching arm loads the payload
        reinterpreted as its type into a fresh binding scoped to the arm. An
        ``any*`` subject auto-dereferences, per the member-access-through-
        pointer precedent. The ``else:`` arm is guaranteed by the parser --
        except for a desugared ``with`` statement (``is_with``), whose
        ``otherwise`` may be empty: an unmatched tag falls through to the
        end block, the statement's defined no-else behavior.

        A multi-type arm (``when int32, int16 n:``) treats its binding as an
        implicit generic: the shared body AST compiles once per listed type,
        the binding typed as that type per copy, and every copy is fully
        type-checked -- a listed type for which the body does not compile is
        a compile error, its note naming the offending type. Each listed
        type claims its own tag, so a duplicate within one list and a
        duplicate across arms both hit the same hard error.

        A generic arm -- ``when T* ptr:`` (every boxed pointer tag, ``T``
        bound to the pointee) or ``when T v:`` (every remaining boxed tag,
        ``T`` bound to the boxed type; pointer tags included) -- cannot
        lower here: its tag set is the whole program's boxed set, still
        growing. It leaves a ``switch`` defaulting to the next arm and
        enqueues a :class:`PendingArm` with a context snapshot;
        :meth:`finalize_generic_arms` monomorphizes the body per matching
        tag after the top-level body loop. Dispatch stays first-match-wins
        textual order, so an arm a generic arm above it subsumes (a
        concrete arm after ``T v``, a concrete pointer arm or a second
        ``T*`` after ``T*``, anything after ``T v``) is a hard
        unreachable-arm error. Because the pending body compiles out of
        band, flow-narrowed facts it could invalidate are dropped at the
        chain position (the :meth:`loop_kill_set` walk plus the blanket
        path kill), and the case is assumed to reach its end block for
        missing-return analysis.

        Args:
            stmt: The ``CaseType`` node.

        Raises:
            LangError: When the subject is not an ``any`` (or ``any*``), an
                arm's type can never be boxed, two arms name the same type
                (or one arm lists it twice), an arm is unreachable behind a
                generic arm, or a body copy fails to compile for one of its
                types.
        """
        subject = self.gen_expr(stmt.subject)
        if is_pointer(subject.type) and is_any(subject.type.pointee):
            pointee = subject.type.pointee
            subject = TypedValue(
                self.gen_load(subject.value, volatile=pointee.volatile),
                strip_const(pointee),
            )
        if not is_any(subject.type):
            if stmt.is_with:
                # The desugared `with` names the construct the user wrote;
                # on a non-any subject its `as` would be a cast, not a test.
                raise LangError(
                    f"with needs an any (or any*) subject, got "
                    f"{subject.type}; 'as' on a non-any is a cast",
                    stmt.line,
                )
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
        # The generic patterns seen so far (their source spelling), for the
        # first-match-wins reachability hard errors below.
        generic_any: "str | None" = None  # a `when T v:` arm's pattern
        generic_ptr: "str | None" = None  # a `when T* ptr:` arm's pattern
        for type_refs, name, body, when_line in stmt.arms:
            pattern = self.generic_arm_pattern(type_refs)
            if pattern is not None:
                param, pointer_only = pattern
                spelling = str(type_refs[0])
                if generic_any is not None:
                    raise LangError(
                        f"case type arm '{spelling}' is unreachable: the "
                        f"generic arm '{generic_any}' above it matches "
                        "every type",
                        when_line,
                    )
                if pointer_only and generic_ptr is not None:
                    raise LangError(
                        f"case type arm '{spelling}' is unreachable: the "
                        f"generic pointer arm '{generic_ptr}*' above it "
                        "matches every pointer type",
                        when_line,
                    )
                # Snapshot BEFORE the fact kills: facts entering the arm are
                # sound (the runtime path into it flows through their
                # establishment); only code compiled after this point must
                # not rely on facts the deferred body could invalidate.
                ctx = GenContext.capture(self).fork()
                next_bb = self.builder.append_basic_block("casetype.next")
                switch = self.builder.switch(tag, next_bb)
                self.pending_arms.append(
                    PendingArm(
                        switch=switch,
                        fn=self.builder.block.parent,
                        payload_ptr=payload_ptr,
                        end_bb=end_bb,
                        param=param,
                        binding=name,
                        body=body,
                        when_line=when_line,
                        pointer_only=pointer_only,
                        claimed=seen,
                        ctx=ctx,
                        label="with pattern" if stmt.is_with
                        else "case type arm",
                    )
                )
                self.builder.position_at_end(next_bb)
                # The deferred body compiles after this function's facts are
                # long gone, so its invalidations cannot fire during normal
                # generation: drop, at the chain position, every name fact
                # the body could kill (the loop pre-scan walker) and every
                # path fact (the call-site blanket-kill precedent).
                self.narrowed_nonnull -= self.loop_kill_set(body)
                self.narrowed_paths.clear()
                # Deferred bodies are assumed to reach the end block (the
                # accepted missing-return conservatism).
                reaches_end = True
                if pointer_only:
                    generic_ptr = param
                else:
                    generic_any = param
                continue
            # A multi-type arm is the concrete lowering looped over its list:
            # one tag test and one body copy per listed type, sharing the AST.
            for type_ref in type_refs:
                arm_type = strip_const(
                    self.check_boxable(
                        self.lang_type(type_ref, when_line),
                        when_line,
                        borrow=True,
                    )
                )
                if generic_any is not None:
                    raise LangError(
                        f"case type arm for {arm_type} is unreachable: the "
                        f"generic arm '{generic_any}' above it matches "
                        "every type",
                        when_line,
                    )
                if generic_ptr is not None and is_pointer(arm_type):
                    raise LangError(
                        f"case type arm for {arm_type} is unreachable: the "
                        f"generic pointer arm '{generic_ptr}*' above it "
                        "matches every pointer type",
                        when_line,
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
                # The binding is scoped to the arm (like a for-loop's
                # variable): a fresh copy for an inline payload, an alias of
                # the caller's storage for a by-reference struct box.
                outer_locals, outer_names = dict(self.locals), self.scope_names
                self.scope_names = set()
                try:
                    self.bind_unboxed(payload_ptr, arm_type, name)
                    self.gen_block(body)
                except LangError as err:
                    # Each copy is fully type-checked; when a shared body
                    # fails for one listed type, a note names it (the same
                    # frame idiom as instantiate, keeping the primary
                    # `file: error: line N: message` head intact).
                    if len(type_refs) > 1:
                        err.notes.append(
                            Note(
                                f"in case type arm for {arm_type}",
                                when_line,
                                self.current_source,
                            )
                        )
                    raise
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

    def finalize_generic_arms(self):
        """Monomorphize every pending generic ``case type`` arm, to fixpoint.

        Runs after the top-level body loop, when the boxed-tag registry
        holds every type the already-generated bodies box. For each pending
        arm, each boxed tag its predicate matches and no earlier arm of its
        case has claimed gets one body copy compiled into fresh blocks of
        the enclosing function and added as a switch case. Compiling a copy
        can box new types and instantiate new generics (whose bodies may
        contain further generic arms) -- both feed back into the registry
        and the pending list, so the loop repeats until a full pass adds
        nothing. Arms are visited in creation order each pass, so within
        one case an earlier arm always claims a newly boxed tag first
        (first-match-wins textual order). Termination has parity with
        recursive generic instantiation: a body boxing a derived type
        forever diverges the same way ``f<T>`` calling ``f<T*>`` does.
        """
        progress = True
        while progress:
            progress = False
            # Both lists grow while compiling copies; iterate snapshots and
            # let the next pass pick up whatever appeared.
            for pending in list(self.pending_arms):
                for arm_tag, boxed in list(self.boxed_types.items()):
                    if arm_tag in pending.claimed:
                        continue
                    if pending.pointer_only and not is_pointer(boxed):
                        continue
                    pending.claimed.add(arm_tag)
                    self.gen_pending_arm_case(pending, arm_tag, boxed)
                    progress = True

    def gen_pending_arm_case(
        self, pending: PendingArm, arm_tag: int, boxed: LangType
    ):
        """Compile one generic-arm body copy for one boxed tag.

        Restores a fresh fork of the arm's context snapshot (so ``defer``,
        loops, locals, and narrowing facts behave exactly as at the arm's
        chain position), compiles the body into late-appended blocks of the
        enclosing function under a ``type_bindings`` overlay -- the arm's
        type parameter bound to the pointee for a ``T*`` arm (the binding
        typed as the boxed pointer) or to the boxed type itself for a
        ``T v`` arm -- and adds the tag to the arm's dispatch switch. The
        copy is fully type-checked; a failure gains a note naming the
        offending type, keeping the primary error head intact.

        Args:
            pending: The pending arm record.
            arm_tag: The boxed tag this copy handles.
            boxed: The tag's boxed type.

        Raises:
            LangError: When the body does not compile for ``boxed`` (e.g. a
                call with no viable overload or instantiation for it).
        """
        outer = GenContext.capture(self)
        pending.ctx.fork().restore(self)
        arm_bb = pending.fn.append_basic_block("casetype.generic")
        self.builder = ir.IRBuilder(arm_bb)
        self.type_bindings = {
            **self.type_bindings,
            pending.param: boxed.pointee if pending.pointer_only else boxed,
        }
        try:
            # The binding recovers the payload exactly as the concrete
            # lowering above: a struct tag aliases the caller's storage, every
            # other tag is an inline copy.
            self.scope_names = set()
            self.bind_unboxed(pending.payload_ptr, boxed, pending.binding)
            self.gen_block(pending.body)
            if not self.builder.block.is_terminated:
                self.builder.branch(pending.end_bb)
        except LangError as err:
            # The error belongs to the case's file (the snapshot's source is
            # live again here); the note names the type the copy failed for.
            if err.source is None:
                err.source = self.current_source
            err.notes.append(
                Note(
                    f"in {pending.label} for {boxed}",
                    pending.when_line,
                    pending.ctx.current_source,
                )
            )
            raise
        finally:
            outer.restore(self)
        pending.switch.add_case(ir.Constant(UINT64.ir, arm_tag), arm_bb)

    def bind_unboxed(self, payload_ptr, arm_type: LangType, name: str):
        """Bind a ``case type`` arm's variable to the recovered payload.

        A struct was boxed **by hidden reference** (see :meth:`gen_box_any`),
        so its payload holds a pointer to the boxing site's storage: the
        binding aliases that storage directly -- read-only (``const``), no
        copy, the mirror of the by-reference box and the same borrow the
        variadic that produced the ``any`` already held. Every other boxable
        type is stored inline, so its binding is a fresh arm-scoped copy of
        the payload, reinterpreted as the arm's type.

        Args:
            payload_ptr: A pointer to the box's 16-byte payload slot.
            arm_type: The arm's (``const``-stripped) recovered type.
            name: The binding name to introduce into the arm's scope.
        """
        if is_aggregate(arm_type) and not is_slice(arm_type):
            ref_ptr = self.builder.bitcast(
                payload_ptr, arm_type.ir.as_pointer().as_pointer()
            )
            storage = self.builder.load(ref_ptr, name=name)
            self.bind_local(name, storage, const_of(arm_type))
            return
        typed_ptr = self.builder.bitcast(payload_ptr, arm_type.ir.as_pointer())
        var_slot = self.entry_alloca(arm_type.ir, name)
        self.builder.store(self.builder.load(typed_ptr), var_slot)
        self.bind_local(name, var_slot, arm_type)

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

        Mirrors the assignment-statement forms (``Assign``, ``StoreDeref``,
        ``StoreIndex``, ``StoreMember``, ``StoreCall``): it rejects the same
        const/read-only targets with the same diagnostics, so ``x op= y`` is
        writable exactly where ``x = y`` is.

        Args:
            target: The lvalue expression (``Var``, ``*ptr``, ``Index``,
                ``Member``, or a ``mut``-returning ``Call``).
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
            # `p += n` moves the pointer: every projection fact rooted at the
            # name dies (the fields now read different storage). The name's
            # own flow-narrowed fact survives when the target is a pointer --
            # the only compound forms a pointer admits are `+=`/`-=`, and
            # arithmetic off a non-null pointer is the same always-non-null
            # derived address `p + n` is. A non-pointer compound (`x += 1`)
            # has no fact to keep, so the discard below is scoped to it for
            # symmetry with plain assignment.
            self.kill_paths_rooted(target.name)
            slot, var_type, volatile = self.var_addr(target.name, line)
            if not is_pointer(strip_const(var_type)):
                self.narrowed_nonnull.discard(target.name)
            if var_type.const:
                raise LangError(
                    f"cannot assign to read-only variable {target.name!r}", line
                )
            return slot, var_type, None, volatile
        if isinstance(target, Unary) and target.op == "*":
            ptr = self.gen_expr(target.operand)
            if not is_pointer(ptr.type):
                raise LangError(f"cannot dereference a {ptr.type}", line)
            self.warn_unchecked_deref(target.operand, line)
            if ptr.type.pointee.const:
                raise LangError(
                    "cannot assign through a pointer to a read-only "
                    f"{ptr.type.pointee}",
                    line,
                )
            # A compound `*p op= v` is a through-memory store, like
            # StoreDeref: every projection fact dies (ditto the element
            # and member arms below).
            self.narrowed_paths.clear()
            return ptr.value, ptr.type.pointee, None, ptr.type.pointee.volatile
        if isinstance(target, Index):
            base_t = self.lvalue_type(target.base)
            # In-storage elements (array or tuple) inherit a const
            # parameter's read-only-ness, as in a plain element assignment.
            in_storage = base_t is not None and (
                is_array(base_t) or is_tuple(strip_const(base_t))
            )
            if in_storage and self.writes_const(target.base):
                raise LangError(
                    "cannot assign to an element of a const parameter", line
                )
            addr, element, align, volatile = self.gen_index_addr(
                target.base, target.index, line, store=True
            )
            if element.const:
                raise LangError(
                    "cannot assign through a read-only slice<const T>", line
                )
            self.narrowed_paths.clear()
            return addr, element, align, volatile
        if isinstance(target, Member):
            if not target.arrow and self.writes_const(target.base):
                raise LangError(
                    "cannot assign to a field of a const parameter", line
                )
            self.narrowed_paths.clear()
            return self.gen_member_addr(target.base, target.field, target.arrow, line)
        if isinstance(target, (Call, CallExpr)):
            # `f(s, i) op= v` -- the call's mut return is the target, exactly
            # as in a plain assignment through it (a through-memory store:
            # every projection fact dies). A CallExpr target (a field-held
            # callback) resolves through gen_addr's CallExpr arm the same way.
            entry = self.gen_addr(target, line)
            self.narrowed_paths.clear()
            return entry
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
        # protocol); the rest survive the loop, and past it. Projection
        # facts have no pre-scan yet and drop wholesale, as at `while`.
        self.narrowed_nonnull -= self.loop_kill_set(stmt)
        self.narrowed_paths.clear()
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
        if not is_aggregate(struct_t):
            raise LangError(
                "'for ... in' needs a struct iterable with '<struct>_it' and "
                f"'<struct>_next' functions, not {iterable.type}",
                stmt.line,
            )
        it_slot, next_fn, element, preserves = self.setup_protocol_loop(
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
            # next(&_it, &x); a write-free _next preserves projection facts
            more = self.emit_call(next_fn, [it_slot, x_slot], preserves=preserves)
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
            ``(iterator slot, next function, element type, preserves)``: the
            alloca holding the ``<struct>_it`` cursor, the instantiated
            ``<struct>_next``, the element type its out-parameter yields,
            and whether calling ``_next`` preserves projection facts (its
            write-effect bit is clear; see :meth:`emit_call`).

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

        next_fn, element, preserves = self.resolve_protocol_next(
            iterator.type, next_name, stmt.line
        )
        return it_slot, next_fn, element, preserves

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
        if not is_aggregate(struct_t):
            raise LangError(
                "enumerate() needs a struct iterable with '<struct>_it' and "
                f"'<struct>_next' functions, not {iterable.type}",
                stmt.line,
            )
        it_slot, next_fn, element, preserves = self.setup_protocol_loop(
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
            more = self.emit_call(next_fn, [it_slot, value_ptr], preserves=preserves)
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
            A ``(function, element type, preserves)`` triple: the instantiated
            ``next``, the element type it yields (its out-parameter's
            pointee), and whether its write-effect bit is clear (so calling
            it preserves projection facts; see :meth:`emit_call`).

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
            return (
                self.funcs[symbol],
                params[1].pointee,
                symbol in self.fact_safe_syms,
            )
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
        return fn, params[1].pointee, self.effect_bits.get(id(func)) is False

    def const_cond_truth(self, expr) -> bool | None:
        """The compile-time truth of a loop condition, or ``None``.

        Try-folds the expression with :meth:`eval_const` (the house constant
        folder: literals, const references, ``sizeof``, casts, constant
        arithmetic). Only a ``bool`` or integer result counts -- the types
        :meth:`gen_cond` accepts -- so anything else (a runtime value, a
        misplaced string, a private const) returns ``None`` and the caller
        falls through to the ordinary runtime path, which reports the same
        error the expression always produced.

        Args:
            expr: The condition expression.

        Returns:
            The condition's truth when it folds to a bool/integer constant,
            else ``None``.
        """
        try:
            tv = self.eval_const(expr, expr.line)
        except LangError:
            return None
        if tv.type is not BOOL and not is_integer(tv.type):
            return None
        return bool(tv.value.constant)

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
        if is_error_decl(strip_const(tv.type)):
            # A declared error is truthy against its reserved zero no-error
            # state: every variant is non-zero by construction, so `if (err)`
            # is a total check.
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
        self.retract_narrowed(added)
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
        result_type = self.unify_branches(then_tv, else_tv, expr.line)
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

    def unify_branches(
        self, then_tv: TypedValue, else_tv: TypedValue, line: int
    ) -> LangType:
        """Pick the shared type of a ternary's two arms.

        Mirrors :meth:`gen_binary`'s operand unification: equal types are kept,
        two untyped integer arms widen to the larger, and a single untyped
        constant arm (including ``null``) takes on the other arm's type.
        Otherwise the first arm's type is the target, and :meth:`coerce` either
        bridges it (e.g. a pointer to raw memory) or reports the mismatch.

        A pending ``ok``/``error`` arm (see :meth:`gen_result_pending`) is
        handled apart in :meth:`unify_result_branches`: the two arms bind each
        other's free parameter, so ``cond ? ok(v) : error(e)`` yields
        ``result<T, E>`` with no annotation.

        Args:
            then_tv: The ``then`` arm's value.
            else_tv: The ``otherwise`` arm's value.
            line: Source line for diagnostics.

        Returns:
            The concrete type both arms are coerced to before the ``phi``.
        """
        if then_tv.result_pending is not None or else_tv.result_pending is not None:
            return self.unify_result_branches(then_tv, else_tv, line)
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

    def unify_result_branches(
        self, then_tv: TypedValue, else_tv: TypedValue, line: int
    ) -> LangType:
        """Bind the free arm of a ternary whose arm(s) are pending results.

        A pending ``ok`` fixes ``T`` and leaves ``E`` free; a pending ``error``
        fixes ``E`` and leaves ``T`` free (see :meth:`gen_result_pending`). The
        two branches meet here:

        - a pending against a concrete ``result<T, E>`` takes that whole type
          (the pending finalizes to it in :meth:`gen_ternary`'s per-arm coerce);
        - a mixed ``ok``/``error`` pair supplies each other's free arm, giving
          ``result<T, E>`` (or the error-only ``result<E>`` when the ok arm is
          the value-less ``ok()``);
        - two same-kind pendings leave one arm with no source, so the target
          type must come from an annotation instead -- reported here.

        Args:
            then_tv: The ``then`` arm's value.
            else_tv: The ``otherwise`` arm's value.
            line: Source line for diagnostics.

        Returns:
            The concrete ``result<...>`` type both arms are coerced to.

        Raises:
            LangError: When the sibling is not a result, or two same-kind
                pendings leave an arm undetermined.
        """
        tp, ep = then_tv.result_pending, else_tv.result_pending
        # One pending, one settled: the settled arm must itself be a result,
        # whose type fixes the pending's free arm.
        if tp is None or ep is None:
            pending = tp if ep is None else ep
            other = else_tv if ep is None else then_tv
            concrete = strip_const(other.type)
            if not is_result(concrete):
                raise LangError(
                    f"cannot reconcile {pending.kind}(...) with {other.type}: "
                    "the other ternary arm is not a result",
                    line,
                )
            return concrete
        # Two pendings: a mixed pair determines both arms; a same-kind pair
        # cannot, since neither supplies the other's free arm.
        if tp.kind == ep.kind:
            missing = "error" if tp.kind == "ok" else "ok"
            raise LangError(
                f"cannot infer the {missing} type of this result: both arms are "
                f"{tp.kind}(...), so neither supplies it -- annotate the target "
                f"(a result<...> return or let), or lift the value out "
                f"(e.g. {tp.kind}(cond ? a : b))",
                line,
            )
        ok_p = tp if tp.kind == "ok" else ep
        err_p = ep if tp.kind == "ok" else tp
        if err_p.payload is None:
            raise LangError(
                "error() takes the error value, e.g. error(my_error::NOT_FOUND)",
                line,
            )
        err_arm = strip_const(err_p.payload.type)
        if ok_p.payload is None:
            # ok() (error-only) paired with error(e): result<E>.
            return self.result_type((err_arm,), line)
        return self.result_type((strip_const(ok_p.payload.type), err_arm), line)

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
            if isinstance(expr, FStrLit):
                raise LangError(FSTRING_MISPLACED, expr.line)
            return self.gen_string(expr.value)
        if isinstance(expr, ArrayLit):
            raise LangError(
                "an array literal is only allowed where an array or slice "
                "type receives it: an array or slice<T> variable "
                "initializer, an array/slice element, or an `as slice<T>` "
                "borrow",
                expr.line,
            )
        if isinstance(expr, StructLit):
            return self.gen_struct_lit(expr)
        if isinstance(expr, ResultLit):
            # Outside a direct result sink: build a pending value whose free arm
            # a ternary sibling or the expected type will bind (a bare use with
            # no such context is rejected at coerce / the sink guards below).
            return self.gen_result_pending(expr)
        if isinstance(expr, ErrorName):
            return self.gen_error_name(expr)
        if isinstance(expr, TupleLit):
            return self.gen_tuple_lit(expr)
        if isinstance(expr, BlockExpr):
            return self.gen_block_expr(expr)
        if isinstance(expr, Except):
            # A try...except nested inside a larger expression (an operand,
            # an argument): the plain value form. The binding forms
            # (let/return/statement) intercept the node before gen_expr.
            return self.gen_except_value(expr)
        if isinstance(expr, Try):
            # Bare propagation composes as an ordinary operand
            # (`1 + try g()`, an argument); statement position intercepts
            # the node for the error-only result<E>.
            return self.gen_try_value(expr)
        if isinstance(expr, TryFallback):
            return self.gen_try_fallback(expr)
        if isinstance(expr, Coalesce):
            return self.gen_coalesce(expr)
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
            # A dot-shaped call may be method sugar: `p.m(args)` rewrites to
            # `Type::m(p, args)` when the receiver's type registers the
            # family and no field shadows it. An unprobeable receiver (a
            # call result) evaluates once and re-dispatches on a hidden
            # local; everything else falls through to the function-pointer
            # call it always was.
            if isinstance(expr.callee, Member) and not expr.callee.arrow:
                base_t = self.dot_receiver_type(expr.callee.base)
                if base_t is None:
                    return self.gen_spilled_dot_call(expr)
                plan = self.plan_dot_call(expr, base_t)
                if plan is not None:
                    return self.gen_call(plan)
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
        if isinstance(expr, TypeName):
            # The canonical spelling is str(LangType) -- exactly the string
            # any_tag hashes -- with a top-level const stripped, matching what
            # boxing does with tags (so typename(v) is the preimage of v's tag).
            named = strip_const(self.sizeof_operand(expr.type_name, expr.line))
            return self.gen_string(str(named))
        if isinstance(expr, Len):
            # The count is a compile-time property of the operand's type: an
            # array's element count or a tuple's arity. An addressable operand
            # is typed through its address so an array does not decay first; a
            # tuple's arity needs no address at all, so an rvalue tuple (a
            # call result, a tuple slice, a literal) is evaluated instead --
            # its side effects still run. Either way the count is an adaptable
            # constant -- like writing it as a literal -- so it compares
            # against an int32 counter as readily as a uint64 one.
            if isinstance(expr.operand, (Var, Unary, Index, Member, StrLit)):
                _, lang_type, _, _ = self.gen_addr(expr.operand, expr.line)
            else:
                lang_type = self.gen_expr(expr.operand).type
            stripped = strip_const(lang_type)
            if is_tuple(stripped):
                count = len(stripped.fields)
            elif is_array(lang_type):
                count = lang_type.count
            else:
                raise LangError(
                    f"len() requires an array or tuple, got {lang_type}", expr.line
                )
            return TypedValue(ir.Constant(UINT64.ir, count), UINT64, adaptable=True)
        if isinstance(expr, Index):
            addr, element, align, volatile = self.gen_index_addr(
                expr.base, expr.index, expr.line
            )
            return self.value_at(addr, element, align=align, volatile=volatile)
        if isinstance(expr, Slice):
            return self.gen_slice(expr)
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
            if is_union(var_type) and glob.value_type != var_type.ir:
                # A union global stored as its written member plus pad: present
                # its address as the union type, so a whole-value load and
                # by-value passing see the declared layout. This is the single
                # site that normalizes the divergent storage type.
                glob = self.builder.bitcast(glob, var_type.ir.as_pointer())
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
            if base_t is not None and (
                is_array(base_t) or is_tuple(strip_const(base_t))
            ):
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
            if base_t is not None and (
                is_array(base_t) or is_tuple(strip_const(base_t))
            ):
                return self.roots_in_mut(target.base)
        return False

    # The mut-return formation error's fixed head; each rejection appends a
    # per-root-kind tail naming the offender.
    MUT_RETURN_FORMATION = (
        "a mut return must be formed from a mut or pointer parameter or a "
        "global"
    )

    def gen_mut_return(self, stmt):
        """Emit ``return expr;`` in a ``-> mut`` function: the address returns.

        Three checks precede the ``ret``: the formation rule (the lvalue's
        root must be caller-reachable -- see
        :meth:`check_mut_return_formation`), storage legality (reusing the
        ``mut``-argument rules: no ``const``, ``@volatile``, ``@packed``, or
        ``@nonnull``-parameter storage), and the exact-type rule every
        ``mut`` reference obeys (the caller writes through it, so nothing
        can adapt or widen).

        Args:
            stmt: The ``Return`` node (``stmt.value`` is not ``None``).

        Raises:
            LangError: When the expression violates the formation rule, the
                storage rules, or the exact-type rule.
        """
        self.check_mut_return_formation(stmt.value, stmt.line)
        # Address the lvalue before the defers run, so a defer cannot
        # redirect what is being returned (the same clobber rule as a value
        # return).
        addr, t, align, volatile = self.gen_addr(stmt.value, stmt.line)
        self.check_mut_storage(
            stmt.value, t, align, volatile, stmt.line, what="a mut return"
        )
        if t != self.ret_type:
            raise LangError(
                f"mut return: expected a {self.ret_type} lvalue, got {t}",
                stmt.line,
            )
        self.run_defers_through(0)
        if not self.builder.block.is_terminated:
            self.builder.ret(addr)

    def check_mut_return_formation(self, expr, line: int, crossed=False,
                                   top=True):
        """Enforce the mut-return formation rule on a ``return`` expression.

        The returned lvalue's storage must be reachable by the caller after
        this call's frame dies, without a lifetime system -- so the rule is
        strict and syntactic: the expression must be a chain of member
        accesses, element accesses, dereferences, and ``mut``-returning
        calls, rooted at

        - a ``mut`` parameter (the caller's own storage; legal even as the
          returned lvalue itself),
        - a pointer parameter behind **at least one** hop (``*p``, ``p[i]``,
          ``p->f`` reach storage the caller handed in; ``return p`` itself
          would reference the parameter's frame slot and is rejected), or
        - a global.

        Everything else is rejected: locals and by-value parameters (this
        call's frame -- even a provably-safe alias like ``let d = self.data;
        return d[i];``, which must be inlined into the return expression),
        ``const`` parameters (read-only), casts, arithmetic, calls without a
        ``mut`` return, and literals. A chain through a call requires every
        candidate behind the name to return ``mut``; the whole returned
        expression being one call defers to :meth:`gen_addr`'s post-
        resolution check instead.

        Args:
            expr: The (sub)expression being traced.
            line: Source line for diagnostics.
            crossed: Whether at least one pointer hop was taken between the
                returned lvalue and ``expr``.
            top: Whether ``expr`` is the whole returned expression.

        Raises:
            LangError: When the expression violates the formation rule.
        """
        base = self.MUT_RETURN_FORMATION
        if isinstance(expr, Var):
            name = expr.name
            if name in self.mut_locals:
                return
            if name in self.const_locals:
                raise LangError(
                    f"{base}; {name!r} is a const parameter (read-only)", line
                )
            pointer_param = self.formation_params.get(name)
            if pointer_param:
                if crossed:
                    return
                raise LangError(
                    f"{base}; returning the pointer parameter {name!r} "
                    "itself would reference this call's frame slot -- "
                    f"return what it points at (e.g. *{name})",
                    line,
                )
            if pointer_param is not None:
                raise LangError(
                    f"{base}; {name!r} is a by-value parameter (its storage "
                    "is this call's frame)",
                    line,
                )
            if name in self.locals:
                raise LangError(
                    f"{base}; {name!r} is a local (its storage dies with "
                    "this call; inline its chain into the return expression)",
                    line,
                )
            return  # a global, constant, or undefined name: gen_addr judges it
        if isinstance(expr, Member):
            self.check_mut_return_formation(
                expr.base, line, crossed=crossed or expr.arrow, top=False
            )
            return
        if isinstance(expr, Index):
            # Indexing a fixed-size array or a tuple stays in the base's own
            # storage; a pointer or slice element lives behind a pointer hop.
            base_t = self.lvalue_type(expr.base)
            in_storage = base_t is not None and (
                is_array(base_t) or is_tuple(strip_const(base_t))
            )
            self.check_mut_return_formation(
                expr.base, line, crossed=crossed or not in_storage, top=False
            )
            return
        if isinstance(expr, Unary) and expr.op == "*":
            self.check_mut_return_formation(
                expr.operand, line, crossed=True, top=False
            )
            return
        if isinstance(expr, NonnullAssert):
            # A postfix `!` asserts non-null and passes the value through
            # unchanged (no IR), so it forms the identical lvalue: the chain
            # continues into the asserted operand, preserving the pointer-hop
            # and top-of-return state. This lets an invariant-backed element
            # like `self.data![i]` be a mut return under -Wunchecked-dereference.
            self.check_mut_return_formation(
                expr.operand, line, crossed=crossed, top=top
            )
            return
        if isinstance(expr, Call):
            # The whole expression being one call is judged after overload
            # resolution (gen_addr's Call arm knows the winner); a call in
            # chain position must be a certain mut return -- its formation
            # rule then vouches for the storage the chain continues into.
            if top or self.known_mut_return_call(expr):
                return
            raise LangError(
                f"{base}; the chain passes through a call to "
                f"{expr.name!r} that does not return mut",
                line,
            )
        if isinstance(expr, CallExpr):
            # A call through a function-pointer expression (a field-held
            # callback, a parenthesized value). The whole expression being
            # the call defers to gen_addr's CallExpr arm; in chain position
            # the callee's spelled type must vouch -- `fn(...) -> mut T` --
            # exactly as a named candidate's declaration does.
            if top:
                return
            # Method sugar in chain position judges by its family, exactly
            # as a named call does (`return self.items.ref(i).x`); an
            # unprobeable receiver stays conservative and falls through to
            # the indirect-call rejection below.
            if isinstance(expr.callee, Member) and not expr.callee.arrow:
                base_t = self.dot_receiver_type(expr.callee.base)
                plan = (
                    self.plan_dot_call(expr, base_t)
                    if base_t is not None
                    else None
                )
                if plan is not None:
                    if self.known_mut_return_call(plan):
                        return
                    raise LangError(
                        f"{base}; the chain passes through a call to "
                        f"{plan.name!r} that does not return mut",
                        line,
                    )
            callee_t = self.lvalue_type(expr.callee)
            if callee_t is not None:
                callee_t = strip_const(callee_t)
            if (
                callee_t is not None
                and is_function(callee_t)
                and callee_t.mutret
            ):
                return
            raise LangError(
                f"{base}; the chain passes through an indirect call that "
                "does not return mut",
                line,
            )
        raise LangError(
            f"{base}; the returned expression must be an lvalue chain "
            "(members, elements, dereferences, and calls that return mut)",
            line,
        )

    def known_mut_return_call(self, call: Call) -> bool:
        """Whether a chain-position call certainly resolves to a mut return.

        Judged by name, before overload resolution: every candidate behind
        the name must carry ``-> mut`` (a mixed set is conservatively
        rejected -- the winner is not known while the formation rule is
        being enforced). A variable or const shadowing the name is an
        indirect call: it vouches exactly when its function-pointer type
        spells ``-> mut`` (the type's ``mutret`` bit is the same trust
        channel a direct call's registry bit is).

        Args:
            call: The ``Call`` node in chain position.

        Returns:
            ``True`` when the call is certainly a ``mut`` return.
        """
        var_type = self.var_type_of(call.name)
        if var_type is None and call.name in self.consts:
            var_type = self.consts[call.name].type
        if var_type is not None:
            var_type = strip_const(var_type)
            return is_function(var_type) and var_type.mutret
        key = (self.current_source, call.name)
        static_template = self.static_templates.get(key)
        if static_template is not None:
            return static_template.mut_return
        static_symbol = self.static_funcs.get(key)
        if static_symbol is not None:
            return static_symbol in self.mut_ret
        candidates = list(self.templates.get(call.name, ()))
        candidates += self.concrete_decls.get(call.name, {}).values()
        if "::" in call.name:
            candidates += self.inherited_candidates(call.name)
        return bool(candidates) and all(f.mut_return for f in candidates)

    def lvalue_type(self, expr) -> "LangType | None":
        """Best-effort static type of a simple lvalue, without emitting code.

        Resolves ``Var``/``Member``/``Index`` chains; returns ``None`` when the
        type cannot be determined statically. Used to tell an in-storage
        array or tuple index from a through-pointer one when checking const
        writes, and as the static probe that routes tuple indexing onto the
        member machinery (see :meth:`gen_index_addr`).

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
            if not is_aggregate(owner):
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
            if is_tuple(strip_const(base_t)):
                # A constant index names a positional field; a non-constant
                # (or out-of-bounds) one types as nothing here and gets its
                # precise error at code generation.
                try:
                    fname = self.tuple_element(strip_const(base_t), expr.index, 0)
                except LangError:
                    return None
                return self.struct_field(strip_const(base_t), fname, 0)[1]
        return None

    def dot_receiver_type(self, expr) -> "LangType | None":
        """Best-effort static type of a dot-call receiver, without IR.

        :meth:`lvalue_type` extended with cheap receiver-only arms (a char
        literal, a string literal, a cast, a dereference, a ``!``
        assertion). ``None`` -- an rvalue receiver like a call result --
        routes the dot-call through the evaluate-once spill instead (see
        :meth:`gen_spilled_dot_call`).

        Args:
            expr: The receiver expression (a ``Member``'s base).

        Returns:
            The receiver's ``LangType``, or ``None``.
        """
        t = self.lvalue_type(expr)
        if t is not None:
            return t
        if isinstance(expr, CharLit):
            return CHAR
        if isinstance(expr, StrLit) and not isinstance(expr, FStrLit):
            return self.string_array_type(expr.value)
        if isinstance(expr, Cast):
            try:
                return self.lang_type(expr.type_name, expr.line)
            except LangError:
                return None
        if isinstance(expr, Unary) and expr.op == "*":
            base = self.dot_receiver_type(expr.operand)
            if base is not None and is_pointer(strip_const(base)):
                return strip_const(base).pointee
            return None
        if isinstance(expr, NonnullAssert):
            return self.dot_receiver_type(expr.operand)
        return None

    def plan_dot_call(self, expr: CallExpr, base_t: LangType) -> "Call | None":
        """Rewrite a dot-call ``recv.m(args)`` to its method-family ``Call``.

        Fields shadow methods: when the receiver type declares a field of
        the name, ``None`` keeps today's field-call behavior (the method
        stays reachable as ``Type::m(recv, args)``). Otherwise a registered
        ``Type::m`` family rewrites to ``Call("Type::m", [recv, *args])``,
        passing the receiver expression verbatim -- so ``mut``-receiver
        legality, evaluate-once addressing, and every diagnostic are the
        desugared call's own. A pointer receiver auto-derefs one hop
        (``q.m()`` is ``S::m(*q, ...)``; ``.`` on a pointer was an error, so
        the space is free -- but a *field* of the pointee still needs ``->``
        as before). A struct or union with neither field nor method is the
        call-shape error; every other miss returns ``None`` and keeps
        today's diagnostics.

        Args:
            expr: The ``CallExpr`` whose callee is a non-arrow ``Member``.
            base_t: The receiver's probed (or spilled) type.

        Returns:
            The rewritten ``Call``, or ``None`` to keep the existing path.

        Raises:
            LangError: When a struct/union receiver has neither a field nor
                a method of the name.
        """
        method = expr.callee.field
        recv = expr.callee.base
        owner = strip_const(base_t)
        if is_pointer(owner):
            if owner is NULLT or owner.pointee is None:
                return None
            pointee = strip_const(owner.pointee)
            if is_aggregate(pointee):
                try:
                    self.struct_field(pointee, method, expr.line)
                    return None  # a field of the pointee: `->` as before
                except LangError:
                    pass
            family = f"{pointee.template or pointee.name}::{method}"
            if self.method_family_exists(family):
                return Call(
                    family,
                    [],
                    [Unary("*", recv, expr.callee.line), *expr.args],
                    expr.line,
                )
            return None
        if is_aggregate(owner) and not is_any(owner):
            if is_tuple(owner):
                return None  # positional fields only; today's diagnostics
            try:
                self.struct_field(owner, method, expr.line)
                return None  # field-first: fields shadow methods
            except LangError:
                pass
            qualifier = owner.template or owner.name
            family = f"{qualifier}::{method}"
            if self.method_family_exists(family):
                return Call(family, [], [recv, *expr.args], expr.line)
            if self.lookup_struct_decl(qualifier) is None:
                return None  # a builtin aggregate (slice, result): as before
            kind = "union" if is_union(owner) else "struct"
            raise LangError(
                f"{kind} {qualifier!r} has no field or method {method!r}",
                expr.line,
            )
        # A builtin scalar (or `any`): the family is the canonical type name,
        # e.g. `'C'.lower()` is `char::lower('C')`.
        family = f"{owner.template or owner.name}::{method}"
        if self.method_family_exists(family):
            return Call(family, [], [recv, *expr.args], expr.line)
        return None

    def gen_spilled_dot_call(self, expr: CallExpr) -> TypedValue:
        """Emit a dot-call whose receiver only types by evaluation.

        A chained receiver (``p.upper().lower()``) is a call result: evaluate
        it once, bind it to a hidden local, and re-dispatch the dot-call on
        the local. A plain rvalue spills to a **const** slot -- const is
        load-bearing: a writable temporary would launder rvalue-ness and let
        a mut-self method silently mutate a value about to be discarded, so
        ``mk().bump()`` must keep erroring. A ``mut``-returning receiver
        instead re-lends its carried lvalue (the callee's formation rule
        vouched for the storage), so ``p.ref().bump()`` writes the caller's
        storage, exactly as ``point::bump(p.ref())`` does.

        Args:
            expr: The ``CallExpr`` whose callee is a non-arrow ``Member``
                with an unprobeable receiver.

        Returns:
            The dot-call's result as a ``TypedValue``.

        Raises:
            LangError: When the receiver has no methods to attach (void,
                null, a pending result) -- the canonical no-fields error.
        """
        callee = expr.callee
        tv = self.gen_expr(callee.base)
        if tv.type is VOID or tv.type is NULLT or tv.result_pending is not None:
            # Nothing can attach; raise the canonical no-fields error.
            self.struct_field(strip_const(tv.type), callee.field, expr.line)
        if tv.lvalue is not None:
            # A mut return re-lends: the hidden local IS the caller-reachable
            # storage, writable through the reference.
            slot, held = tv.lvalue, strip_const(tv.type)
        else:
            slot = self.builder.alloca(tv.type.ir)
            if over_aligned(tv.type):
                slot.align = type_align(tv.type)
            self.builder.store(tv.value, slot)
            held = const_of(strip_const(tv.type))
        hidden = f"0recv{self.hidden_seq}"
        self.hidden_seq += 1
        self.locals[hidden] = (slot, held)
        try:
            return self.gen_expr(
                CallExpr(
                    Member(Var(hidden, callee.line), callee.field, False,
                           callee.line),
                    expr.args,
                    expr.line,
                )
            )
        finally:
            del self.locals[hidden]

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
            self.warn_unchecked_deref(expr.operand, line)
            return tv.value, tv.type.pointee, None, tv.type.pointee.volatile
        if isinstance(expr, Index):
            # store=True: an address may feed a write (assignment through a
            # member of the element, or &), so an rvalue tuple base must
            # reject rather than hand out a spilled temporary's address.
            return self.gen_index_addr(expr.base, expr.index, line, store=True)
        if isinstance(expr, Member):
            return self.gen_member_addr(expr.base, expr.field, expr.arrow, line)
        if isinstance(expr, StrLit):
            # A string literal is a uint8[N] array (NUL included) living in a
            # constant global; addressing it (for len/sizeof, or a borrow) keeps
            # the array type, while reading it as a value decays to uint8*.
            if isinstance(expr, FStrLit):
                raise LangError(FSTRING_MISPLACED, expr.line)
            glob = self.string_global(expr.value)
            return glob, self.string_array_type(expr.value), None, False
        if isinstance(expr, Call):
            # A mut-returning call is an lvalue: the raw pointer result is
            # its address, vouched for by the callee's formation rule --
            # which also rejected @packed and @volatile storage, so the
            # address is naturally aligned and non-volatile. This one arm
            # backs every lvalue surface: assignment (StoreCall), compound
            # assignment, member/element projections, and & (banned before
            # it gets here).
            tv = self.gen_call(expr)
            if tv.lvalue is None:
                raise LangError(
                    f"the call to {expr.name!r} does not return mut, so "
                    "its result is not assignable",
                    line,
                )
            return tv.lvalue, tv.type, None, False
        if isinstance(expr, CallExpr):
            # Method sugar first: a dot-call that rewrites to a family call
            # is addressed through the Call arm above -- `p.ref(i) = v` is
            # `point::ref(p, i) = v`, with the same mut-return rule. An
            # unprobeable receiver (a chained call, `a.view().at(i) = v`)
            # evaluates once through the spill path, whose mut-return
            # result carries the address exactly as the Call arm's does.
            if isinstance(expr.callee, Member) and not expr.callee.arrow:
                base_t = self.dot_receiver_type(expr.callee.base)
                if base_t is None:
                    tv = self.gen_spilled_dot_call(expr)
                    if tv.lvalue is None:
                        raise LangError(
                            f"the call to {expr.callee.field!r} does not "
                            "return mut, so its result is not assignable",
                            line,
                        )
                    return tv.lvalue, tv.type, None, False
                plan = self.plan_dot_call(expr, base_t)
                if plan is not None:
                    return self.gen_addr(plan, line)
            # A call through a function-pointer expression (a field-held
            # callback table, a parenthesized value): the value's type
            # spells the mut return, so the same lvalue rule applies --
            # and the same guarantees hold, since the callee's own body
            # passed the formation and storage checks when it compiled.
            callee = self.gen_expr(expr.callee)
            tv = self.gen_indirect_call(
                callee, expr.args, f"call to {callee.type}", expr.line
            )
            if tv.lvalue is None:
                raise LangError(
                    f"the call to a {callee.type} value does not return "
                    "mut, so its result is not assignable",
                    line,
                )
            return tv.lvalue, tv.type, None, False
        raise LangError("expression is not addressable", line)

    def gen_index_addr(
        self, base_expr, index_expr, line: int, *, store: bool = False
    ) -> tuple[ir.Value, LangType, int | None, bool]:
        """Compute the address of ``base[index]``.

        Args:
            base_expr: The indexed base expression (a pointer, array, slice,
                or tuple).
            index_expr: The index expression (an integer; a compile-time
                constant for a tuple base).
            line: Source line for diagnostics.
            store: Whether the address receives a write. An rvalue tuple base
                (a call result) then rejects instead of spilling to a
                temporary the store would silently vanish into.

        Returns:
            A ``(pointer value, element type, guaranteed alignment,
            volatile)`` tuple, as in :meth:`gen_addr`.

        Raises:
            LangError: When the base is not indexable, the index is not an
                integer, or a tuple index is not a constant in bounds.
        """
        base_t = self.lvalue_type(base_expr)
        if base_t is not None and is_tuple(strip_const(base_t)):
            # A tuple element is a struct field in disguise (positional
            # fields "0", "1", ...): resolve the constant index and reuse the
            # member machinery, packed alignment and @volatile propagation
            # included.
            fname = self.tuple_element(strip_const(base_t), index_expr, line)
            return self.gen_member_addr(base_expr, fname, False, line)
        base = self.gen_expr(base_expr)
        if is_slice(base.type):
            # A slice indexes through its `data` field into the borrowed run.
            ptr = self.builder.extract_value(base.value, base.type.elem_indices[0])
            element = base.type.fields[0][1].pointee
            index = self.gen_expr(index_expr)
            if not is_integer(index.type):
                raise LangError(f"index must be an integer, not {index.type}", line)
            return self.builder.gep(ptr, [index.value]), element, None, element.volatile
        if is_tuple(strip_const(base.type)):
            # The base types as a tuple only once evaluated (a call result or
            # another rvalue chain the static probe above cannot follow).
            owner = strip_const(base.type)
            fname = self.tuple_element(owner, index_expr, line)
            index, ftype = self.struct_field(owner, fname, line)
            if base.lvalue is not None:
                # A mut-returning call is an lvalue: project the element
                # through the returned storage address (writes included), as
                # gen_addr's Call arm does for a member projection. The
                # callee's formation rule already rejected @packed and
                # @volatile storage.
                addr = self.builder.gep(
                    base.lvalue,
                    [I32_ZERO, ir.Constant(ir.IntType(32), index)],
                    inbounds=True,
                )
                return addr, ftype, None, False
            # A plain rvalue: a read extracts through a spilled temporary; a
            # write has no caller-visible storage to land in, so it rejects
            # -- matching a struct rvalue's `f().field = v` rejection.
            if store:
                raise LangError(
                    f"cannot assign into a {base.type} value; bind it to a "
                    "variable first",
                    line,
                )
            slot = self.builder.alloca(owner.ir)
            if over_aligned(owner):
                slot.align = type_align(owner)
            self.builder.store(base.value, slot)
            addr = self.builder.gep(
                slot, [I32_ZERO, ir.Constant(ir.IntType(32), index)], inbounds=True
            )
            return addr, ftype, 1 if owner.packed else None, owner.volatile
        if not is_pointer(base.type):
            raise LangError(f"cannot index a {base.type}", line)
        self.warn_unchecked_deref(base_expr, line)
        index = self.gen_expr(index_expr)
        if not is_integer(index.type):
            raise LangError(f"index must be an integer, not {index.type}", line)
        addr = self.builder.gep(base.value, [index.value])
        return addr, base.type.pointee, None, base.type.pointee.volatile

    def tuple_element(self, tuple_type: LangType, index_expr, line: int) -> str:
        """Resolve a tuple index to its positional field name (``"0"``, ...).

        The index must fold to a compile-time integer constant -- each
        position has its own type, so a runtime index would have no single
        result type -- and is bounds-checked here, at compile time.

        Args:
            tuple_type: The (const-stripped) tuple type being indexed.
            index_expr: The index expression.
            line: Source line for diagnostics.

        Returns:
            The positional field name.

        Raises:
            LangError: On a non-constant, non-integer, or out-of-bounds index.
        """
        try:
            tv = self.eval_const(index_expr, line)
        except LangError:
            raise LangError(
                f"a tuple index must be a compile-time constant: each "
                f"position of a {tuple_type} has its own type, so a runtime "
                "index has no single result type",
                line,
            ) from None
        if not is_integer(tv.type):
            raise LangError(f"index must be an integer, not {tv.type}", line)
        count = len(tuple_type.fields)
        n = tv.value.constant
        if not 0 <= n < count:
            positions = (
                "it has no positions" if count == 0
                else f"positions 0 to {count - 1}"
            )
            raise LangError(
                f"tuple index {n} is out of bounds for {tuple_type} "
                f"({positions})",
                line,
            )
        return str(n)

    def gen_slice(self, expr: Slice) -> TypedValue:
        """Evaluate a sub-slice expression: ``base[start:end]``.

        A sub-slice of a ``slice<T>`` is a new rvalue view of the same
        storage, ``{ data + start, end - start }`` -- the same data-field
        extract, GEP, and length subtraction the equivalent struct literal
        emits. An omitted ``start`` defaults to 0 and an omitted ``end`` to
        the receiver's length; ``s[:]`` is a plain copy of the view. The
        result type is the receiver's type verbatim, so element mutability
        rides the element type (a sub-slice of ``slice<const T>`` is
        ``slice<const T>``).

        Bounds keep the house posture, unchecked: the GEP is bare (no
        ``inbounds``, matching slice indexing) and nothing validates
        ``start <= end <= length``, so an out-of-range pair is UB -- a corrupt
        view, exactly like an out-of-range ``s[i]``. ``s[n:n]`` is the defined
        empty result ``{ data + n, 0 }``: the one-past-end pointer is formed
        but never dereferenced, and it is deliberately not normalized to the
        empty literal's ``{ null, 0 }`` (no branch in the lowering).

        A tuple receiver dispatches to :meth:`gen_tuple_slice` instead: the
        same grammar with compile-time-constant bounds, narrowing to the
        smaller tuple by value. Every other non-slice receiver reaches
        sub-slicing by first becoming a slice through its existing borrow
        spelling (see :meth:`sub_slice_error`).

        Args:
            expr: The ``Slice`` node.

        Returns:
            The sub-view as a ``TypedValue``.

        Raises:
            LangError: When the receiver is not a ``slice<T>`` or a bound is
                not an integer.
        """
        base = self.gen_expr(expr.base)
        if is_tuple(strip_const(base.type)):
            return self.gen_tuple_slice(expr, base)
        if not is_slice(base.type):
            raise LangError(self.sub_slice_error(expr.base, base), expr.line)
        if expr.start is None and expr.end is None:
            # `s[:]` copies the view: a slice is a plain value, so the copy
            # is the value itself.
            return TypedValue(base.value, base.type)
        ptr = self.builder.extract_value(base.value, base.type.elem_indices[0])
        if expr.end is None:
            end = self.builder.extract_value(base.value, base.type.elem_indices[1])
        else:
            end = self.slice_bound(expr.end, expr.line)
        if expr.start is None:
            data, length = ptr, end
        else:
            start = self.slice_bound(expr.start, expr.line)
            data = self.builder.gep(ptr, [start])
            length = self.builder.sub(end, start)
        return self.make_slice(base.type, data, length)

    def slice_bound(self, bound_expr, line: int):
        """Evaluate a sub-slice bound to a 64-bit element offset.

        Bounds have index parity: any integer type is accepted and widened to
        64 bits by its own signedness (``sext`` signed, ``zext`` unsigned),
        the same treatment a GEP gives an index, so the length subtraction
        happens at the slice length's width.

        Args:
            bound_expr: The bound expression (an integer).
            line: Source line for diagnostics.

        Returns:
            The bound as a 64-bit ``ir.Value``.

        Raises:
            LangError: When the bound is not an integer.
        """
        tv = self.gen_expr(bound_expr)
        if not is_integer(tv.type):
            raise LangError(f"slice bound must be an integer, not {tv.type}", line)
        if tv.type.ir.width == 64:
            return tv.value
        extend = self.builder.sext if tv.type.signed else self.builder.zext
        return extend(tv.value, UINT64.ir)

    def gen_tuple_slice(self, expr: Slice, base: TypedValue) -> TypedValue:
        """Evaluate a constant tuple slice: ``t[n:m]`` narrows to the smaller
        tuple of positions ``n`` to ``m - 1``.

        The same half-open ``[a:b]`` grammar as a sub-slice, open ends
        included (an omitted ``start`` defaults to 0 and an omitted ``end``
        to the arity, so ``t[1:]``, ``t[:2]``, and ``t[:]`` all fold), but
        the result is a **new tuple value, not a view**: the kept positions
        are copied (the narrowed type could not alias the source layout
        anyway), built over a zeroinitializer so padding bytes are
        deterministic, as in a tuple literal. The copy semantics also mean a
        tuple slice is never a write target -- ``Slice`` sits on no lvalue
        surface.

        Bounds must fold to compile-time constants (the bounds pick the
        result type) and are checked here: ``0 <= n <= m <= arity``. Any
        result arity is legal -- ``t[1:]`` on a 2-tuple keeps the 1-tuple
        tail, and ``t[n:n]`` is the empty ``tuple<>``.

        Args:
            expr: The ``Slice`` node.
            base: The evaluated tuple receiver.

        Returns:
            The narrowed tuple as a ``TypedValue``.

        Raises:
            LangError: On a non-constant, non-integer, or out-of-bounds
                bound, or inverted bounds.
        """
        owner = strip_const(base.type)
        count = len(owner.fields)
        start = (
            0 if expr.start is None
            else self.tuple_slice_bound(owner, expr.start, expr.line)
        )
        end = (
            count if expr.end is None
            else self.tuple_slice_bound(owner, expr.end, expr.line)
        )
        if start > end:
            raise LangError(
                f"tuple slice bounds are inverted: {start} > {end}", expr.line
            )
        result_t = self.tuple_type(
            tuple(ftype for _, ftype in owner.fields[start:end]), expr.line
        )
        value = ir.Constant(result_t.ir, None)
        for pos in range(start, end):
            element = self.builder.extract_value(
                base.value, owner.elem_indices[pos]
            )
            value = self.builder.insert_value(
                value, element, result_t.elem_indices[pos - start]
            )
        return TypedValue(value, result_t)

    def tuple_slice_bound(self, tuple_type: LangType, bound_expr, line: int) -> int:
        """Fold a tuple slice bound to a compile-time position.

        The bound must fold via :meth:`eval_const` -- the bounds pick which
        positions the result keeps, so a runtime bound would have no single
        result type, the same reasoning as a tuple index -- and each bound is
        range-checked on its own, 0 to the arity inclusive: the end bound is
        exclusive in the slice, so it may equal the arity.

        Args:
            tuple_type: The (const-stripped) tuple type being sliced.
            bound_expr: The bound expression.
            line: Source line for diagnostics.

        Returns:
            The bound as a Python int.

        Raises:
            LangError: On a non-constant, non-integer, or out-of-range bound.
        """
        try:
            tv = self.eval_const(bound_expr, line)
        except LangError:
            raise LangError(
                f"a tuple slice bound must be a compile-time constant: the "
                f"bounds pick which positions of a {tuple_type} the result "
                "keeps, so a runtime bound has no single result type",
                line,
            ) from None
        if not is_integer(tv.type):
            raise LangError(f"slice bound must be an integer, not {tv.type}", line)
        count = len(tuple_type.fields)
        n = tv.value.constant
        if not 0 <= n <= count:
            raise LangError(
                f"tuple slice bound {n} is out of bounds for {tuple_type} "
                f"(bounds run 0 to {count})",
                line,
            )
        return n

    def sub_slice_error(self, base_expr, base: TypedValue) -> str:
        """Word the rejection for a non-slice sub-slice receiver.

        The single dispatch point for every non-slice, non-tuple receiver
        (a tuple slices directly, see :meth:`gen_tuple_slice`), so the
        planned indexing/slicing protocol has one place to hook user-defined
        slicer overloads into later. Owned containers get the borrow
        spelling that works today: a fixed array's brackets keep the
        ``char[N]`` NUL-drop and read-only-source rules exactly where they
        live, and a ``list<T>`` (or any slice-extending struct) may carry
        derived state beyond the view (a list's ``capacity``) that only its
        author knows how to rebuild, so it borrows too. A string literal
        stays rejected in v1 -- the borrow is its spelling as well.

        Args:
            base_expr: The receiver's AST node (to name a string literal).
            base: The evaluated receiver.

        Returns:
            The error message text.
        """
        if base.decayed is not None:
            # The value is the pointer an array decayed to; reject by the
            # array type the user wrote, not the pointer it became.
            return (
                f"cannot sub-slice {base.decayed}; borrow it first: "
                f"(arr as slice<{base.decayed.element}>)[a:b]"
            )
        if isinstance(base_expr, StrLit):
            return (
                "cannot sub-slice a string literal; borrow it first: "
                '("..." as slice<char>)[a:b]'
            )
        if is_aggregate(base.type):
            return (
                f"cannot sub-slice {base.type}; borrow it first: "
                "(xs as slice<T>)[a:b]"
            )
        return f"cannot sub-slice {base.type}; only a slice can be sub-sliced"

    def gen_destructure(self, stmt: Let):
        """Lower a destructuring ``let``: one local per bound position.

        Pure sugar over shipped projections: the source evaluates exactly
        once into a hidden local, then each binder is an ordinary ``let``
        of ``src[i]`` and the trailing rest binder one of ``src[k:]`` --
        constant indexing and slicing for a tuple (each binder copies its
        position, the rest binder the narrowed tuple tail), unchecked
        indexing and sub-slicing for a slice (the rest binder is a view of
        the same storage, and nothing checks the source is long enough).
        Recursing through :meth:`gen_statement` keeps every rule where it
        lives today: per-binder duplicate checks, loads shedding ``const``,
        the tuple bounds discipline.

        Args:
            stmt: The ``Let`` carrying ``extra``/``rest``.

        Raises:
            LangError: When the source is not a tuple or slice, or a tuple's
                arity does not match the binder count.
        """
        tv = self.gen_expr(stmt.value)
        if is_result(strip_const(tv.type)):
            self.destructure_result(stmt, tv)
            return
        names = [stmt.name, *stmt.extra]
        fixed = len(names) - 1 if stmt.rest else len(names)
        if is_tuple(strip_const(tv.type)):
            arity = len(strip_const(tv.type).fields)
            if fixed > arity or (not stmt.rest and fixed != arity):
                positions = (
                    "no positions"
                    if arity == 0
                    else f"{arity} position" + ("" if arity == 1 else "s")
                )
                binders = f"{fixed} binder" + ("" if fixed == 1 else "s")
                if stmt.rest:
                    binders += " and a rest"
                raise LangError(
                    f"cannot destructure {tv.type} into {binders} "
                    f"(it has {positions})",
                    stmt.line,
                )
        elif not is_slice(tv.type):
            raise LangError(self.destructure_error(stmt.value, tv), stmt.line)
        # Bind the evaluated source under a hidden name (not a lexable
        # identifier, so it cannot collide) so the synthesized projections
        # do not re-evaluate the expression.
        slot = self.builder.alloca(tv.type.ir, name="destructure.src")
        if over_aligned(tv.type):
            slot.align = type_align(tv.type)
        self.builder.store(tv.value, slot)
        hidden = "0destructure"
        self.bind_local(hidden, slot, tv.type)
        src = Var(hidden, stmt.line)
        for i, name in enumerate(names[:fixed]):
            self.gen_statement(
                Let(name, None, Index(src, IntLit(i, stmt.line), stmt.line), stmt.line)
            )
        if stmt.rest:
            start = IntLit(fixed, stmt.line) if fixed else None
            self.gen_statement(
                Let(names[-1], None, Slice(src, start, None, stmt.line), stmt.line)
            )
        del self.locals[hidden]
        self.scope_names.discard(hidden)

    def destructure_result(self, stmt: Let, tv: TypedValue):
        """Lower form 1, ``let ret, err = f();`` over a ``result<T, E>``.

        Exactly two binders (no rest): ``ret`` takes the ok value, ``err``
        the error. Lowered as a tag select -- **never** a raw read of the
        other union arm, whose bytes are the stored arm's, not zero: both
        slots zero-fill first, then a branch on the tag stores only the
        live arm's value into its binder. So on success ``err`` is the
        error type's all-zero value -- the reserved, unnameable no-error
        state, falsy by construction (every declared variant is non-zero),
        making ``if (err)`` a total check for **any** declared error type
        -- and on failure ``ret`` is the zero value of ``T``.

        The error-only ``result<E>`` rejects: there is no ok value to bind
        (its consumer is statement-position ``except``).

        Args:
            stmt: The destructuring ``Let``.
            tv: The already-evaluated source value.

        Raises:
            LangError: On a ``result<E>`` source, a binder count other than
                two, a rest binder, or a duplicate binder name.
        """
        result_t = strip_const(tv.type)
        if len(result_t.args) == 1:
            raise LangError(
                f"cannot destructure {tv.type}: it has no ok value; handle "
                "it with except: try f() except (err) { ... };",
                stmt.line,
            )
        names = [stmt.name, *stmt.extra]
        if stmt.rest or len(names) != 2:
            binders = f"{len(names)} binder" + ("" if len(names) == 1 else "s")
            if stmt.rest:
                binders += " and a rest"
            raise LangError(
                f"cannot destructure {tv.type} into {binders} (it binds a "
                "value and an error: let ret, err = f();)",
                stmt.line,
            )
        ok_t, err_t = result_t.args
        # Spill the source and read the tag, as an except clause does.
        src = self.builder.alloca(result_t.ir, name="destructure.src")
        if over_aligned(result_t):
            src.align = type_align(result_t)
        self.builder.store(tv.value, src)
        indices = result_t.elem_indices
        tag_addr = self.builder.gep(
            src, [I32_ZERO, ir.Constant(ir.IntType(32), indices[0])],
            inbounds=True,
        )
        payload_addr = self.builder.gep(
            src, [I32_ZERO, ir.Constant(ir.IntType(32), indices[1])],
            inbounds=True,
        )
        tag = self.builder.load(tag_addr, name="destructure.tag")
        is_err = self.builder.icmp_unsigned(
            "!=", tag, ir.Constant(UINT8.ir, 0)
        )
        # Both binders zero-fill up front (the arm the tag does not select
        # keeps the zero), then each arm stores only its own live value.
        slots = []
        for name, arm_t in ((names[0], ok_t), (names[1], err_t)):
            if name in self.scope_names:
                raise LangError(
                    f"variable {name!r} already declared in this scope",
                    stmt.line,
                )
            slot = self.entry_alloca(arm_t.ir, name)
            if over_aligned(arm_t):
                slot.align = type_align(arm_t)
            self.builder.store(ir.Constant(arm_t.ir, None), slot)
            self.bind_local(name, slot, arm_t)
            slots.append(slot)
        err_bb = self.builder.append_basic_block("destructure.err")
        ok_bb = self.builder.append_basic_block("destructure.ok")
        end_bb = self.builder.append_basic_block("destructure.end")
        self.builder.cbranch(is_err, err_bb, ok_bb)
        self.builder.position_at_end(ok_bb)
        ok_ptr = self.builder.bitcast(payload_addr, ok_t.ir.as_pointer())
        self.builder.store(self.builder.load(ok_ptr), slots[0])
        self.builder.branch(end_bb)
        self.builder.position_at_end(err_bb)
        err_ptr = self.builder.bitcast(payload_addr, err_t.ir.as_pointer())
        self.builder.store(self.builder.load(err_ptr), slots[1])
        self.builder.branch(end_bb)
        self.builder.position_at_end(end_bb)

    def destructure_error(self, base_expr, base: TypedValue) -> str:
        """Word the rejection for a non-tuple, non-slice destructuring source.

        Mirrors :meth:`sub_slice_error`: owned containers reach destructuring
        through their existing borrow spelling, so every borrow rule (the
        ``char[N]`` NUL-drop, the read-only source, a ``list<T>``'s derived
        state) stays exactly where it lives today.

        Args:
            base_expr: The source's AST node (to name a string literal).
            base: The evaluated source.

        Returns:
            The error message text.
        """
        if base.decayed is not None:
            return (
                f"cannot destructure {base.decayed}; borrow it first: "
                f"let a, b = arr as slice<{base.decayed.element}>;"
            )
        if isinstance(base_expr, StrLit):
            return (
                "cannot destructure a string literal; borrow it first: "
                'let a, b = "..." as slice<char>;'
            )
        if is_aggregate(base.type):
            return (
                f"cannot destructure {base.type}; borrow it first: "
                "let a, b = xs as slice<T>;"
            )
        return (
            f"cannot destructure {base.type}; "
            "only a tuple or slice can be destructured"
        )

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
            self.warn_unchecked_deref(base_expr, line)
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
            if isinstance(expr.operand, (Call, CallExpr)):
                # Load-bearing for mut returns: gen_addr can address a
                # mut-returning call, but the reference is consumed at the
                # call expression -- letting & capture it would break the
                # non-escape guarantee every mut reference carries. A plain
                # call result was never addressable, so nothing is lost.
                raise LangError(
                    "cannot take the address of a call result; a mut "
                    "return must not escape its full expression",
                    expr.line,
                )
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
            self.warn_unchecked_deref(expr.operand, expr.line)
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
        result = self.emit_call(inline, [tv.value for tv in inputs])
        return TypedValue(result, out) if out else TypedValue(result, VOID)

    def gen_cast(self, expr: Cast) -> TypedValue:
        """Emit an explicit ``value as type`` conversion.

        Supports pointer/function-pointer bitcasts, pointer-integer conversions,
        integer truncation/extension (by signedness), integer-to-bool, and the
        ``float64`` conversions. A cast to ``slice<T>`` is a borrow (see
        :meth:`gen_borrow_slice`); other struct casts are rejected, except the
        ``extends`` value-upcast and the tuple-to-struct (and back)
        layout-equivalent reinterpret (see :meth:`gen_tuple_struct_cast`).

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
        shape = (
            self.tuple_cast_shape(target, expr.line)
            if isinstance(expr.value, TupleLit)
            else None
        )
        if shape is not None:
            # The headline `(a, b) as A` form: the literal lowers against the
            # target's shape as against a typed `let`, so an untyped constant
            # adapts to its position's type (`(1, 2) as pair64`).
            tv = self.gen_tuple_lit(expr.value, tuple_type=shape)
        else:
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
        # One carve-out: between two function types, the mut/const
        # hidden-reference shape and the mut return must match. Same-shape
        # reinterpretation (including stripping a @nonnull contract) stays
        # open, but a shape change is a calling-convention change -- there
        # is no call sequence an `as` could make correct, so it is not
        # offered.
        if (
            is_function(src)
            and is_function(target)
            and (src.mutref != target.mutref or src.constref != target.constref)
        ):
            kind = "mut" if src.mutref != target.mutref else "const"
            raise LangError(
                f"cannot cast {src} to {target}: a {kind} parameter is "
                "passed by hidden reference, a different calling "
                "convention; the types are not convertible",
                expr.line,
            )
        if (
            is_function(src)
            and is_function(target)
            and src.mutret != target.mutret
        ):
            # A mut return is the same class of carve-out: the return
            # conventions differ (a pointer to storage versus the value),
            # so no call sequence an `as` produced could be correct.
            raise LangError(
                f"cannot cast {src} to {target}: a mut return is passed "
                "as a pointer to the returned storage, a different "
                "calling convention; the types are not convertible",
                expr.line,
            )
        src_addr = is_pointer(src) or is_function(src)
        target_addr = is_pointer(target) or is_function(target)
        if src_addr and target_addr:
            return TypedValue(self.builder.bitcast(tv.value, target.ir), target)
        if src_addr and is_integer(target) and target.ir.width == 64:
            return TypedValue(self.builder.ptrtoint(tv.value, target.ir), target)
        if is_integer(src) and target_addr:
            return TypedValue(self.builder.inttoptr(tv.value, target.ir), target)
        if is_struct(src) and is_struct(target) and self.nominal_subtype(target, src):
            # Value upcast: `target` is a base of `src` in its declared `extends`
            # lineage, so its fields are `src`'s leading prefix and it occupies
            # the same starting bytes. Round-trip
            # through memory -- store the derived value, reinterpret the slot as
            # the base, load -- which keeps any @packed/@align padding identical.
            slot = self.builder.alloca(src.ir)
            self.builder.store(tv.value, slot)
            base_ptr = self.builder.bitcast(slot, target.ir.as_pointer())
            return TypedValue(self.builder.load(base_ptr), target)
        src_t, target_t = strip_const(src), strip_const(target)
        # A declared error is nominal with a reserved zero state: nothing casts
        # *into* it (that would mint a value no member names, 0 included) --
        # error(member) is the only producer. Reading the numeric value *out*
        # stays an explicit escape (`err as int32`, or `as bool` for the
        # zero test), like any other explicit narrowing.
        if is_error_decl(target_t):
            raise LangError(
                f"cannot cast {src} to {target}; an error value is one of "
                f"{target}'s declared members",
                expr.line,
            )
        if is_error_decl(src_t) and not is_integer(target_t) and target_t is not BOOL:
            raise LangError(f"cannot cast {src} to {target}", expr.line)
        if (
            is_tuple(src_t) != is_tuple(target_t)
            and is_struct(src_t)
            and not is_slice(src_t)
            and is_struct(target_t)
        ):
            # Layout-equivalent reinterpret (tuples stage 4): exactly one side
            # is a tuple, the other a record struct. Struct-to-struct stays
            # nominal-only (the `extends` upcast above) and tuple-to-tuple is
            # never a pun, so this structural check opens no back door.
            return self.gen_tuple_struct_cast(tv, target_t, expr.line)
        if is_aggregate(src) or is_aggregate(target):
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

    def tuple_cast_shape(self, target: LangType, line: int) -> LangType | None:
        """The tuple type a tuple-literal cast operand lowers against.

        The headline layout-equivalent cast form is the literal one,
        ``(a, b) as A``, so a ``TupleLit`` operand of a cast lowers against
        the target's shape the way it would against a typed ``let``: a tuple
        target is its own shape, a record-struct target contributes its field
        types in order. Any other target gets ``None`` -- the literal then
        anchors its own type and the ordinary cast arms (or rejects) apply,
        e.g. ``(1, 2) as any`` still hits the boxing-is-implicit reject.

        Args:
            target: The resolved cast target type.
            line: Source line for diagnostics.

        Returns:
            The tuple type to lower the literal against, or ``None``.
        """
        target = strip_const(target)
        if is_tuple(target):
            return target
        if is_struct(target) and not is_slice(target) and not is_any(target):
            return self.tuple_type(
                tuple(strip_const(ftype) for _, ftype in target.fields), line
            )
        return None

    def gen_tuple_struct_cast(
        self, tv: TypedValue, target: LangType, line: int
    ) -> TypedValue:
        """Reinterpret a tuple as a layout-equivalent struct, or back.

        Exactly one of operand and target is a tuple, the other a record
        struct (the caller gates). The cast is legal when the struct is
        layout-equivalent to the tuple: the same field types in the same
        order, compared exactly -- a nested struct field needs the *same*
        struct type in that position, never a recursively-equivalent tuple --
        and no layout-changing attribute on the struct. Without ``@packed``
        or ``@align`` both sides run the identical sequential layout
        (:meth:`set_struct_body`), so an equal field sequence means equal
        size, alignment, and offsets; with either attribute the offsets or
        size diverge from the tuple's, so those structs never match. Field
        names never matter, and the ``extends`` lineage never participates:
        a derived struct matches only when its full flattened field sequence
        does.

        The value is rebuilt position by position -- extract/insert through
        both sides' ``elem_indices`` over a zero-initialized aggregate, the
        shape of a tuple slice -- so padding stays deterministic, no memory
        round-trip is needed, and any padding-element index divergence
        between the two identified types is harmless. The result is a fresh
        value copy, never ``const``.

        Args:
            tv: The evaluated operand (tuple or struct value).
            target: The resolved cast target (struct or tuple), const-stripped.
            line: Source line for diagnostics.

        Returns:
            The reinterpreted value as a ``TypedValue`` of ``target``.

        Raises:
            LangError: When the sides are not layout-equivalent, naming the
                first divergence.
        """
        src = strip_const(tv.type)
        tup, struct = (src, target) if is_tuple(src) else (target, src)
        head = f"cannot cast {src} to {target}"
        if struct.packed:
            raise LangError(
                f"{head}: {struct} is @packed, so its layout is not the "
                "tuple's",
                line,
            )
        if struct.align is not None:
            raise LangError(
                f"{head}: {struct} is @align({struct.align}), so its layout "
                "is not the tuple's",
                line,
            )
        if len(struct.fields) != len(tup.fields):
            raise LangError(
                f"{head}: {struct} has {len(struct.fields)} fields, but the "
                f"tuple has {len(tup.fields)} positions",
                line,
            )
        for pos, ((fname, ftype), (_, ptype)) in enumerate(
            zip(struct.fields, tup.fields)
        ):
            if strip_const(ftype) != strip_const(ptype):
                raise LangError(
                    f"{head}: position {pos} is {strip_const(ptype)}, but "
                    f"field {fname!r} is {strip_const(ftype)}",
                    line,
                )
        value = ir.Constant(target.ir, None)
        for pos in range(len(src.fields)):
            element = self.builder.extract_value(
                tv.value, src.elem_indices[pos]
            )
            value = self.builder.insert_value(
                value, element, target.elem_indices[pos]
            )
        return TypedValue(value, target)

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

    def array_literal_adapts(self, expr, expected: LangType) -> bool:
        """Whether an array literal adapts to ``expected`` without an ``as``.

        The array sibling of :meth:`str_literal_adapts`: an array literal
        implicitly adapts to a ``slice<T>`` from an annotated ``let``, an
        array/slice element slot (Stage 1), or a function argument (Stage 2),
        borrowing a hidden backing array in the enclosing frame (see the
        ``ArrayLit`` arm of :meth:`gen_borrow_slice`). A ``return`` still does
        not adapt -- the returned view would dangle.

        A ternary adapts when both of its arms do, exactly as for string
        literals: each arm borrows in its own branch.

        Args:
            expr: The initializer/element/argument expression.
            expected: The type the context expects.

        Returns:
            ``True`` if ``expr`` is an array literal (or a ternary of
            adapting arms) and ``expected`` is a ``slice<T>``.
        """
        if isinstance(expr, Ternary):
            return self.array_literal_adapts(
                expr.then, expected
            ) and self.array_literal_adapts(expr.otherwise, expected)
        return isinstance(expr, ArrayLit) and is_slice(expected)

    def struct_literal_adapts(self, expr, expected: LangType) -> bool:
        """Whether a bare struct literal ``{ field = expr, ... }`` adapts here.

        The aggregate sibling of :meth:`str_literal_adapts`: a *bare* struct
        literal (no written type) takes the struct/union type the position fixes
        -- a typed ``let``, an assignment, a ``return``, a function argument, an
        array/slice element, or another struct's field -- and builds it (see
        :meth:`gen_struct_lit`). Unlike a slice borrow it is a plain value copy,
        so it adapts even in a ``return`` and needs no lifetime care. The named
        form ``point { ... }`` carries its own type and never routes through
        here. A bare literal in a ternary arm does not adapt (name the arms).

        This routing check stays coarse (any aggregate target) so that in a
        fixed-type position an unknown field or wrong value type reaches
        :meth:`gen_struct_lit` and gets a precise error. Overload resolution,
        where the target is *chosen*, uses the stricter :meth:`struct_literal_fits`
        to discriminate.

        Args:
            expr: The initializer/element/argument/field expression.
            expected: The type the context expects.

        Returns:
            ``True`` if ``expr`` is a bare struct literal and ``expected`` is an
            aggregate type, or a tuple literal and ``expected`` is a tuple.
        """
        if isinstance(expr, TupleLit):
            # The tuple literal is the bare struct literal's positional twin:
            # it routes through the same sinks, building the expected tuple
            # with per-element coercion (see gen_tuple_lit). The check stays
            # coarse (any tuple target) for the same precise-error reason.
            return is_tuple(strip_const(expected))
        return (
            isinstance(expr, StructLit)
            and expr.type_ref is None
            and is_aggregate(strip_const(expected))
        )

    def struct_literal_fits(self, expr, expected: LangType) -> bool:
        """Whether a bare struct literal's fields all belong to ``expected``.

        The discriminating form of :meth:`struct_literal_adapts`, used only to
        filter overload candidates: ``{ x = 1, y = 2 }`` fits ``point`` but not a
        ``box`` of ``w``/``h``, so the call resolves to the ``point`` overload.
        A wrong *value* type is still left to :meth:`gen_struct_lit` to report at
        emission, once the winner is fixed.

        Args:
            expr: The argument expression.
            expected: The candidate parameter's resolved type.

        Returns:
            ``True`` when ``expr`` is a bare struct literal every field of which
            is a field of ``expected``.
        """
        if not self.struct_literal_adapts(expr, expected):
            return False
        if isinstance(expr, TupleLit):  # positions fit when the arity does
            return len(expr.elements) == len(strip_const(expected).fields)
        known = {fname for fname, _ in strip_const(expected).fields}
        return all(fname in known for fname, _ in expr.fields)

    def gen_adapted_literal(self, expr, expected: LangType, line: int) -> TypedValue:
        """Lower a literal that adapts to ``expected`` in a typed position.

        The single entry the value sinks share: a bare struct literal builds the
        expected aggregate (:meth:`gen_struct_lit`); a string or array literal
        borrows the expected slice (:meth:`gen_borrow_slice`). The caller has
        already checked that one of the ``*_literal_adapts`` predicates holds.

        Args:
            expr: The literal (or a ternary of slice literals).
            expected: The type the position fixes.
            line: Source line for diagnostics.

        Returns:
            The adapted value.
        """
        if isinstance(expr, FStrLit):
            # str_literal_adapts matches it (an FStrLit is a StrLit), but the
            # only position an f-string may adapt into is an @format string,
            # which substitutes the plain literal before reaching here.
            raise LangError(FSTRING_MISPLACED, expr.line)
        if isinstance(expr, ResultLit):
            return self.gen_result_lit(expr, strip_const(expected))
        if isinstance(expr, TupleLit):
            return self.gen_tuple_lit(expr, tuple_type=strip_const(expected))
        if self.struct_literal_adapts(expr, expected):
            return self.gen_struct_lit(expr, struct_type=strip_const(expected))
        return self.gen_borrow_slice(expr, expected, line)

    def defers_array_literal(self, expr) -> bool:
        """Whether an argument is an array literal (or a ternary of them).

        The overload/generic call path pre-evaluates every argument for
        inference, but an array literal cannot lower without a receiving
        ``slice<T>`` type and the winning parameter is not yet chosen. Such an
        argument is instead stood in as a ``NULLT`` placeholder -- the
        inference loop skips ``NULLT``, so literal elements contribute nothing
        to generic inference -- and borrowed only once the winner is known
        (see :meth:`literal_adapts_to_pattern` and the winner-emission arm),
        the argument-position sibling of a string literal pre-evaluating to a
        ``char*``. A ternary defers when both of its arms do.

        Args:
            expr: The raw argument expression.

        Returns:
            ``True`` if ``expr`` is an array literal or a ternary of them.
        """
        if isinstance(expr, Ternary):
            return self.defers_array_literal(expr.then) and self.defers_array_literal(
                expr.otherwise
            )
        return isinstance(expr, ArrayLit)

    def defers_struct_literal(self, expr) -> bool:
        """Whether an argument is a bare struct literal ``{ field = expr, ... }``.

        The struct sibling of :meth:`defers_array_literal`: a bare literal has no
        type of its own, so it cannot lower until overload resolution fixes the
        receiving parameter. It stands in as a ``NULLT`` placeholder (skipped by
        inference, since it can never bind a type parameter) and builds against
        the resolved parameter at emission. A named ``point { ... }`` carries its
        type and lowers eagerly, so it never defers.

        Args:
            expr: The raw argument expression.

        Returns:
            ``True`` if ``expr`` is a bare struct literal.
        """
        return isinstance(expr, StructLit) and expr.type_ref is None

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

        An array literal borrows to a view over a hidden backing array
        materialized in the enclosing function's frame (an entry-block
        alloca, so the storage lives for the whole call): ``[1, 2, 3] as
        slice<int32>`` is ``{ &backing[0], 3 }``. The length is the exact
        element count -- no NUL logic, even for ``char`` elements. The empty
        literal ``[] as slice<T>`` builds no array at all: it is the
        ``{ null, 0 }`` empty view.

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
        if isinstance(value_expr, ArrayLit):
            # An array literal: materialize a hidden backing array in the
            # function frame (an entry alloca, so it lives for the whole
            # call; a literal inside a loop reuses one slot, re-stored per
            # pass) and view it. Element coercion, nested literals, and
            # string-literal elements all ride store_list_literal. The
            # length is the exact element count -- a char literal has no
            # NUL to drop. A mutable target is fine: the backing storage is
            # fresh and nothing else names it.
            if not value_expr.elements:
                # `[] as slice<T>`: no storage to view -- the { null, 0 }
                # empty slice, as zero variadic extras synthesize.
                data = ir.Constant(target.fields[0][1].ir, None)
                return self.make_slice(target, data, ir.Constant(UINT64.ir, 0))
            arr_type = list_of(strip_const(element), len(value_expr.elements))
            slot = self.entry_alloca(arr_type.ir, "arr.lit")
            self.store_list_literal(slot, value_expr, arr_type, line)
            ptr = self.builder.gep(slot, [I32_ZERO, I32_ZERO], inbounds=True)
            length = ir.Constant(UINT64.ir, len(value_expr.elements))
            return self.make_slice(target, ptr, length)
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
        if is_pointer(owner) and is_aggregate(owner.pointee):
            owner = owner.pointee  # a list<T>* (or slice<T>*) borrows like the value
            struct_val = self.gen_load(src.value)
        # The source borrows to the slice when it *is* a slice<T> or names one
        # in its declared `extends` lineage (as list<T> does): the base's fields
        # are then its leading {data, length} prefix. The check is nominal
        # (:meth:`nominal_subtype`) -- the slice<T> must be a declared base of the
        # source, not merely a coincidental layout twin. The element may gain
        # `const` (see :meth:`check_borrow_element`); the target `prefix` strips
        # it so a list<T> borrows to both slice<T> and slice<const T>, and const
        # shares the layout, so the leading {data, length} transfer straight
        # across.
        prefix = self.slice_type(strip_const(element), line)
        if is_slice(owner) or self.nominal_subtype(prefix, owner):
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
            NUL-terminated UTF-8 bytes of ``text``. Deduplicated: identical
            contents (from any mix of source literals and ``typename``
            results) share one global.
        """
        cached = self.string_globals.get(text)
        if cached is not None:
            return cached
        data = self.string_data(text)
        list_ty = ir.ArrayType(ir.IntType(8), len(data))
        glob = ir.GlobalVariable(self.module, list_ty, name=f".str.{self.str_count}")
        self.str_count += 1
        glob.linkage = "private"
        glob.global_constant = True
        glob.unnamed_addr = True
        glob.initializer = ir.Constant(list_ty, data)
        self.string_globals[text] = glob
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

    def gen_error_name(self, expr: ErrorName) -> TypedValue:
        """Lower ``error_name(err)`` / ``error_message(err)`` to a ``char*``.

        The operand must be a declared [error](#error-declarations) value. The
        result is looked up at runtime through a per-declaration synthesized
        function (:meth:`error_accessor_fn`) keyed on the error's ``int32``
        value: ``error_name`` returns the matched variant's fully qualified
        name (``my_error::NOT_FOUND``), and ``error_message`` its declared
        display string, falling back to the bare variant identifier when the
        variant declared none. The reserved zero no-error state and any
        unreachable value gap render as the empty string.

        Args:
            expr: The ``ErrorName`` node.

        Returns:
            The rendered name as a ``char*`` ``TypedValue``.

        Raises:
            LangError: When the operand is not a declared error value.
        """
        tv = self.gen_expr(expr.operand)
        err_t = strip_const(tv.type)
        if not is_error_decl(err_t):
            what = "error_message" if expr.display else "error_name"
            raise LangError(
                f"{what}() takes a declared error value, got {tv.type}",
                expr.line,
            )
        enum = self.error_types[err_t.name]
        fn = self.error_accessor_fn(err_t.name, enum, expr.display)
        return TypedValue(self.emit_call(fn, [tv.value]), CHARPTR)

    def error_accessor_fn(
        self, type_name: str, enum: "EnumType", display: bool
    ) -> ir.Function:
        """Synthesize (once) the name/message lookup for an error declaration.

        Builds an ``internal`` ``char* (i32)`` function that switches on an
        error value and returns the string for the matching variant -- the
        qualified ``Type::VARIANT`` name for ``error_name``, the declared
        display string (or the bare variant identifier when absent) for
        ``error_message``. Error values are dense
        ``1..N``; the ``switch`` maps each to its string and its default -- the
        only value the switch does not cover, the reserved zero no-error state
        -- returns the empty string. Cached per ``(type name, display)`` so
        repeated accessor calls share one function.

        Args:
            type_name: The error's nominal (salted for ``@static``) type name.
            enum: The error declaration's ``EnumType`` (its variant table and
                display strings).
            display: ``True`` for ``error_message``, ``False`` for
                ``error_name``.

        Returns:
            The synthesized accessor function.
        """
        key = (type_name, display)
        cached = self.error_accessors.get(key)
        if cached is not None:
            return cached
        err_ir = enum.underlying.ir
        fnty = ir.FunctionType(CHARPTR.ir, [err_ir])
        kind = "message" if display else "name"
        fn = ir.Function(self.module, fnty, name=f"error.{kind}.{type_name}")
        fn.linkage = "internal"
        entry = fn.append_basic_block("entry")
        merge = fn.append_basic_block("merge")
        default_bb = fn.append_basic_block("unknown")
        builder = ir.IRBuilder(entry)
        switch = builder.switch(fn.args[0], default_bb)
        incoming: list[tuple[ir.Value, ir.Block]] = []
        qual = enum.display_name or type_name
        for mname, member in enum.members.items():
            if display:
                text = enum.displays.get(mname, mname)
            else:
                text = f"{qual}::{mname}"
            case_bb = fn.append_basic_block(f"v{member.value.constant}")
            switch.add_case(member.value, case_bb)
            case_builder = ir.IRBuilder(case_bb)
            ptr = case_builder.bitcast(self.string_global(text), CHARPTR.ir)
            case_builder.branch(merge)
            incoming.append((ptr, case_bb))
        default_builder = ir.IRBuilder(default_bb)
        empty = default_builder.bitcast(self.string_global(""), CHARPTR.ir)
        default_builder.branch(merge)
        incoming.append((empty, default_bb))
        merge_builder = ir.IRBuilder(merge)
        phi = merge_builder.phi(CHARPTR.ir)
        for value, block in incoming:
            phi.add_incoming(value, block)
        merge_builder.ret(phi)
        self.error_accessors[key] = fn
        return fn

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
            elif self.struct_literal_adapts(element, arr_type.element):
                # A bare struct-literal element builds the element's aggregate
                # type, so `let ps: point[2] = [{ x = 1, y = 2 }, { x = 3 }];`
                # needs no per-element type name; a tuple-literal element
                # builds a tuple element the same way ([(1, 2), (3, 4)]).
                tv = self.gen_adapted_literal(element, arr_type.element, line)
                self.gen_store(tv.value, slot)
            elif self.str_literal_adapts(element, arr_type.element):
                # A string-literal element adapts to a slice<char> element
                # (Stage 4 borrow-in) the way it does in a top-level slot:
                # each element borrows its string constant's bytes, NUL
                # dropped, so `let dirs: slice<char>[2] = ["bin", "usr/bin"];`
                # needs no per-element `as`.
                tv = self.gen_borrow_slice(element, arr_type.element, line)
                self.gen_store(tv.value, slot)
            elif self.array_literal_adapts(element, arr_type.element):
                # An array-literal element adapts to a slice<T> element the
                # same way (Stage 1): each element borrows its own hidden
                # backing array, so `let m: slice<int32>[2] = [[1], [2, 3]];`
                # needs no per-element `as` -- and a slice<slice<T>> target's
                # nested literals recurse through here.
                tv = self.gen_borrow_slice(element, arr_type.element, line)
                self.gen_store(tv.value, slot)
            else:
                tv = self.coerce(
                    self.gen_expr(element), arr_type.element, line, "array element"
                )
                self.gen_store(tv.value, slot)

    def const_initializer(self, expr, expected: LangType, line: int) -> ir.Constant:
        """Build a constant of a given type for a ``@static`` initializer.

        Arrays use nested literals; a ``slice<const T>`` may view an array
        literal (an anonymous constant global backs it) or a string literal;
        an ``any`` boxes its constant (see :meth:`const_box_any`); scalars may
        be any compile-time constant expression -- a literal, a ``const``
        reference, an ``as`` cast, ``sizeof``, or arithmetic -- folded via
        :meth:`eval_const`.

        Args:
            expr: The initializer expression.
            expected: The required constant type.
            line: Source line for diagnostics.

        Returns:
            The constant value.

        Raises:
            LangError: When ``expr`` is not a constant of ``expected``.
        """
        if is_any(expected) and not (
            isinstance(expr, StructLit) and expr.type_ref is None
        ):
            # An `any` global boxes its constant initializer. Checked before
            # the struct-literal arm so a *named* struct/union literal gets
            # the canonical owning-box rejection instead of a type mismatch
            # against `any`; a bare literal falls through to const_struct_lit,
            # which fields-checks it against `any` exactly as runtime does.
            return self.const_box_any(expr, line)
        if isinstance(expr, StructLit):
            # A struct or union literal folds to an aggregate constant: each
            # field recurses through here, so nested struct/array/slice fields
            # and a struct inside a union all compose. A union constant is typed
            # as its written member plus trailing pad (see
            # :meth:`const_union_lit`), not the union's own IR type.
            return self.const_struct_lit(expr, expected, line)
        if isinstance(expr, ArrayLit) and is_slice(expected):
            # A slice<const T> initialized from an array literal: the Stage 1
            # adaptation in constant form -- the elements go into an anonymous
            # private constant global (as a string literal's bytes do) and the
            # slice is a constant {pointer, length} view over it. The pointee
            # is a true constant, so a read-only view is safe even for a
            # global; a *mutable* slice<T> would open a write path into
            # rodata, so it is rejected. Reached per element through the
            # ArrayLit array recursion below, this also covers a @static
            # array of slices; a nested literal recurses through here for a
            # @static slice<const slice<const T>>.
            element = expected.fields[0][1].pointee
            if not element.const:
                raise LangError(
                    f"a @static {expected} cannot view an array literal: the "
                    f"backing array is a read-only constant; declare it "
                    f"slice<{const_of(strip_const(element))}>",
                    line,
                )
            if not expr.elements:
                # `[]`: the { null, 0 } empty view, no backing global at all.
                data = ir.Constant(expected.fields[0][1].ir, None)
                return ir.Constant(
                    expected.ir, [data, ir.Constant(UINT64.ir, 0)]
                )
            elem = strip_const(element)
            arr_ir = ir.ArrayType(elem.ir, len(expr.elements))
            glob = ir.GlobalVariable(
                self.module, arr_ir, name=f".arr.{self.arr_count}"
            )
            self.arr_count += 1
            glob.linkage = "private"
            glob.global_constant = True
            glob.unnamed_addr = True
            glob.initializer = ir.Constant(
                arr_ir,
                [self.const_initializer(e, elem, line) for e in expr.elements],
            )
            return ir.Constant(
                expected.ir,
                [
                    glob.gep([I32_ZERO, I32_ZERO]),
                    ir.Constant(UINT64.ir, len(expr.elements)),
                ],
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
        if isinstance(expr, FStrLit):
            raise LangError(FSTRING_MISPLACED, expr.line)
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

    def const_box_any(self, expr, line: int) -> ir.Constant:
        """Box a compile-time constant into an ``any`` aggregate constant.

        The constant counterpart of :meth:`gen_box_any` for a global/
        ``@static`` ``any`` initializer, built without a builder: the
        initializer folds via :meth:`eval_const` -- an untyped ``5`` anchors
        at its int32 placeholder type and a string literal folds to ``char*``,
        so both box under the same tags as at runtime -- and the value's bits
        are punned into the payload's low word. The pun matches the runtime
        zero-fill-then-store layout only on a little-endian target; every
        supported arch (see ``classify_arch``) is LE. Boxability and the tag
        are shared with the runtime path via :meth:`check_boxable` /
        :meth:`any_tag`; a global is an owning slot even when declared
        ``const any``, so the struct borrow carve-out never applies.

        Args:
            expr: The initializer expression.
            line: Source line for diagnostics.

        Returns:
            The 24-byte ``any`` constant (typed exactly ``ANY.ir``, so it
            composes into enclosing aggregate constants).

        Raises:
            LangError: When the initializer is not a compile-time constant or
                its type is outside the owning-``any`` boxable set (struct,
                union, array, bare ``null``), with the same messages as
                runtime boxing.
        """
        if isinstance(expr, StructLit):
            # A named struct/union literal: resolve its type only to phrase
            # the rejection by it (check_boxable always raises for an
            # aggregate in an owning slot).
            self.check_boxable(self.lang_type(expr.type_ref, line), line)
        if isinstance(expr, ArrayLit):
            raise LangError(f"an array literal cannot initialize a {ANY}", line)
        tv = self.eval_const(expr, line)
        boxed = self.check_boxable(tv.type, line)
        tag = self.any_tag(boxed, line)
        # Feed the boxed-only registry: a type boxed only at global scope
        # must still reach generic case-type arm monomorphization (globals
        # fold before the finalize_generic_arms fixpoint).
        self.boxed_types.setdefault(tag, boxed)
        if is_pointer(boxed):
            # A pointer payload puns via a ptrtoint constant expression
            # (legal in a global initializer). A slice never reaches here:
            # nothing eval_const folds has a slice type.
            word = tv.value.ptrtoint(UINT64.ir)
        elif boxed is FLOAT64:
            bits = struct.unpack("<Q", struct.pack("<d", tv.value.constant))[0]
            word = ir.Constant(UINT64.ir, bits)
        elif boxed.ir.width == 64:
            # A 64-bit integer already fills the word; reuse it as-is, which
            # also carries a ptrtoint-derived constant expression through.
            word = tv.value
        else:
            # Narrower integer/bool/char bits zero-extended into the low
            # word, as the runtime's zero-filled slot leaves them.
            mask = (1 << boxed.ir.width) - 1
            word = ir.Constant(UINT64.ir, tv.value.constant & mask)
        payload = ir.Constant(
            ANY.ir.elements[1], [word, ir.Constant(UINT64.ir, 0)]
        )
        return ir.Constant(ANY.ir, [ir.Constant(UINT64.ir, tag), payload])

    def const_struct_lit(
        self, expr: StructLit, expected: LangType, line: int
    ) -> ir.Constant:
        """Fold a struct or union literal to an aggregate constant.

        The constant counterpart of :meth:`gen_struct_lit` for a ``@static``
        initializer: omitted fields read as zero (or their declared default),
        and each field value recurses through :meth:`const_initializer`, so a
        nested struct, array, or slice field composes. The annotation
        ``expected`` supplies the concrete (possibly generic) type, so no
        inference from the literal is needed.

        Args:
            expr: The ``StructLit`` node.
            expected: The declared aggregate type.
            line: Source line for diagnostics.

        Returns:
            The aggregate constant. A union constant is typed as its written
            member plus trailing pad, not the union's own IR type.

        Raises:
            LangError: When ``expected`` is not a struct/union, the literal
                names a different concrete type, or a field is unknown, set
                twice, or not a compile-time constant.
        """
        if not is_aggregate(expected):
            raise LangError(f"a struct literal cannot initialize a {expected}", line)
        # Guard against a literal naming a different concrete type than the
        # annotation; skip a generic literal with no arguments, where the
        # annotation supplies them, and a bare literal, which has no name to
        # check (the annotation is its only type).
        if expr.type_ref is not None:
            decl = self.lookup_struct_decl(expr.type_ref.name)
            if not (decl is not None and decl.type_params and not expr.type_ref.args):
                named = self.lang_type(expr.type_ref, line)
                if named != expected:
                    raise LangError(
                        f"@static initializer: expected {expected}, got {named}", line
                    )
        seen: set[str] = set()
        provided: dict = {}
        for fname, value_expr in expr.fields:
            if fname in seen:
                raise LangError(
                    f"field {fname!r} is set twice in the struct literal", line
                )
            seen.add(fname)
            provided[fname] = value_expr
        known = {fname for fname, _ in expected.fields}
        for fname in provided:
            if fname not in known:
                raise LangError(f"struct {expected} has no field {fname!r}", line)
        if is_union(expected):
            return self.const_union_lit(expr, expected, line)
        # Fill each IR element in layout order: a named or defaulted field folds
        # to its constant, padding and omitted fields stay zero -- the same
        # result the runtime literal reaches by zeroing the storage first.
        values = [ir.Constant(elem, None) for elem in expected.ir.elements]
        defaults = self.struct_defaults(expected)
        for pos, (fname, ftype) in enumerate(expected.fields):
            if fname in provided:
                field_expr = provided[fname]
            elif fname in defaults:
                field_expr = defaults[fname]
            else:
                continue
            if is_flexible_array(ftype):
                raise LangError(
                    f"field {fname!r} is a flexible array member with no "
                    "storage; allocate the struct with trailing room and fill "
                    f"it through the {fname!r} pointer",
                    line,
                )
            values[expected.elem_indices[pos]] = self.const_initializer(
                field_expr, ftype, line
            )
        return ir.Constant(expected.ir, values)

    def const_union_lit(
        self, expr: StructLit, union_type: LangType, line: int
    ) -> ir.Constant:
        """Fold a union literal to a constant sized to the whole union.

        Like the runtime union literal, the (at most one) named member is
        written and the rest of the storage is zero. The written member is
        usually not the union's representative (widest) member, so the constant
        cannot take the union's own IR type; it takes an ad-hoc
        ``{member, [pad x i8]}`` struct type instead (what clang emits), and
        :meth:`var_addr` bitcasts the global back to the union type on use. When
        the member *is* the representative -- or the literal is empty -- the
        union's own IR type fits and no bitcast is needed.

        Args:
            expr: The union ``StructLit`` node (fields already validated).
            union_type: The resolved union type.
            line: Source line for diagnostics.

        Returns:
            The union constant.

        Raises:
            LangError: When the literal sets more than one member or the member
                value is not a compile-time constant.
        """
        if len(expr.fields) > 1:
            raise LangError("a union literal sets at most one member", line)
        if not expr.fields:
            # An empty `u{}` zero-fills the whole union, so its own IR type fits.
            return ir.Constant(union_type.ir, None)
        fname, value_expr = expr.fields[0]
        _, ftype = self.struct_field(union_type, fname, line)
        member = self.const_initializer(value_expr, ftype, line)
        elements = union_type.ir.elements
        if elements and member.type == elements[0]:
            # The written member is the representative: fill the union's own
            # body (the member plus any trailing @align pad element), so the
            # global keeps the union's IR type.
            parts = [member] + [ir.Constant(elem, None) for elem in elements[1:]]
            return ir.Constant(union_type.ir, parts)
        pad = type_size(union_type) - type_size(ftype)
        fields = [member.type]
        parts = [member]
        if pad:
            pad_ty = ir.ArrayType(ir.IntType(8), pad)
            fields.append(pad_ty)
            parts.append(ir.Constant(pad_ty, None))
        init_ty = ir.LiteralStructType(fields, packed=union_type.ir.packed)
        return ir.Constant(init_ty, parts)

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
            if isinstance(expr, FStrLit):
                raise LangError(FSTRING_MISPLACED, expr.line)
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
        if isinstance(expr, TypeName):
            named = strip_const(self.lang_type(expr.type_name, line))
            return TypedValue(self.const_string(str(named)), CHARPTR)
        if isinstance(expr, Len):
            # len() is a pure property of the operand's type, so it folds
            # wherever constants go -- const initializers, tuple index and
            # slice bounds -- as long as the operand is a simple lvalue the
            # static probe can type without emitting code.
            operand_t = self.lvalue_type(expr.operand)
            if operand_t is not None:
                stripped = strip_const(operand_t)
                if is_tuple(stripped):
                    count = len(stripped.fields)
                elif is_array(operand_t):
                    count = operand_t.count
                else:
                    raise LangError(
                        f"len() requires an array or tuple, got {operand_t}", line
                    )
                return TypedValue(
                    ir.Constant(UINT64.ir, count), UINT64, adaptable=True
                )
            raise LangError(
                "a const initializer must be a compile-time constant", line
            )
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
        if isinstance(expr, ResultLit):
            # Mirrors the runtime-only aggregates a global cannot hold: a
            # result is built at runtime, so const and @static initializers
            # reject it up front.
            raise LangError(
                f"a result is a runtime value; {expr.kind}(...) cannot "
                "initialize a const or @static global",
                expr.line,
            )
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
        # The @nonnull contravariant rule, mirroring coerce: a plain function
        # constant initializes an annotated slot (so a @static table of
        # fn(@nonnull ...) values accepts plain members), never the reverse.
        # The mut/const hidden-reference shape must match exactly -- it is a
        # calling convention, not a contract (see coerce).
        if (
            is_function(expected)
            and is_function(tv.type)
            and tv.type.signature == expected.signature
            and tv.type.mutref == expected.mutref
            and tv.type.constref == expected.constref
            and tv.type.mutret == expected.mutret
            and tv.type.nonnull <= expected.nonnull
        ):
            return TypedValue(tv.value, expected)
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
        self.reject_nonnull_drop(tv.type, expected, line, context)
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
        # An error member's numeric value reads out explicitly, as at runtime
        # (`my_error::A as int32`); the reverse -- minting an error from an
        # integer -- stays rejected by the fall-through (is_integer excludes
        # a declared error type on either side).
        if is_error_decl(src) and is_integer(target):
            return TypedValue(
                ir.Constant(target.ir, wrap_int(tv.value.constant, target)), target
            )
        if is_error_decl(src) and target is BOOL:
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

    def chase_alias_qualifier(self, name: str, line: int) -> str | None:
        """Chase a call-site method qualifier through type aliases, by name.

        ``pointf::magnitude(p)`` is ``point::magnitude(p)``: a CALL
        qualifier is purely a namespace hop, so only the NAME canonicalizes
        -- no type arguments are injected (dispatch still infers from the
        arguments). Unlike a declaration, a bare generic name is fine here:
        declarations register annotated, calls merely look the family up.
        Each hop is access-checked, so a cross-file ``@private`` alias
        qualifier errors like any other use.

        Args:
            name: The qualifier as written at the call.
            line: The call's line, for diagnostics.

        Returns:
            The canonical type name (a struct or builtin), or ``None`` when
            the qualifier is not alias-rooted -- an undeclared name, an enum,
            a cycle, or an alias of an unnameable type -- leaving the call to
            resolve (and report) under its written name.

        Raises:
            LangError: When a chased alias is ``@private`` to another file.
        """
        seen: set[str] = set()
        while (
            self.lookup_struct_decl(name) is None
            and name not in TYPES
            and name not in RESERVED_TYPE_NAMES
        ):
            alias = self.lookup_alias(name)
            if alias is None or name in seen:
                return None
            seen.add(name)
            self.check_access(
                alias.private, alias.source, f"type alias {name!r}", line
            )
            target = alias.target
            if (
                target.stars
                or target.dims
                or target.const
                or target.nonnull
                or target.mut
                or target.params is not None
            ):
                return None
            name = target.name
        return name

    def method_family_exists(self, family: str) -> bool:
        """Whether a qualified method name has any registered member.

        Checked before desugaring sugar into a ``Type::method`` call, so a
        missing family reports at the sugar's own altitude (a bespoke
        no-constructor / no-method error) instead of ``undefined function``.

        Args:
            family: The qualified name, e.g. ``"point::constructor"``.

        Returns:
            ``True`` when a template, overload set, plain function, or
            file-scoped ``@static`` member answers to the name.
        """
        key = (self.current_source, family)
        if (
            family in self.templates
            or family in self.overloads
            or family in self.funcs
            or key in self.static_templates
            or key in self.static_funcs
        ):
            return True
        # A derived struct exposes its base chain's families.
        return bool(self.inherited_candidates(family))

    def struct_base_ref(self, decl) -> "TypeRef | None":
        """A struct declaration's ``extends`` target, alias-normalized.

        The decl-level (``TypeRef``) mirror of :meth:`resolve_base`, for
        walking a base chain before any instantiation exists: plain aliases
        chase to their target and generic-alias applications expand
        (:meth:`dealias_pattern`), so the returned reference heads a real
        struct or builtin name whose arguments are spelled over the
        declaring struct's own type parameters.

        Args:
            decl: The ``StructDecl`` whose base to normalize.

        Returns:
            The normalized base ``TypeRef``, or ``None`` when the struct
            extends nothing, extends a bare type parameter (``extends T`` --
            no declared base family to inherit), or the reference does not
            normalize here (the instantiation reports those).
        """
        base = decl.base
        if base is None:
            return None
        type_params = list(decl.type_params)
        outer_source = self.current_source
        self.current_source = decl.source
        try:
            seen: set[str] = set()
            while True:
                if base.name in type_params:
                    return None  # `extends T`: intrusive reuse, no inheritance
                if base.stars or base.dims or base.params is not None:
                    return None  # not a struct; instantiation reports it
                if base.args:
                    expanded = self.dealias_pattern(base, type_params)
                    if expanded is not base:
                        base = expanded
                        continue  # re-judge the expansion's head
                if (
                    self.lookup_struct_decl(base.name) is not None
                    or base.name in TYPES
                    or base.name in RESERVED_TYPE_NAMES
                ):
                    return base
                alias = self.lookup_alias(base.name)
                if alias is None or alias.type_params or base.name in seen:
                    return None  # unresolvable here; instantiation reports it
                seen.add(base.name)
                base = alias.target
        finally:
            self.current_source = outer_source

    def base_chain(
        self, name: str
    ) -> "list[tuple[str, list[TypeRef], int, str | None]]":
        """Walk a struct's declared ``extends`` chain at the ``TypeRef`` level.

        Each hop's type arguments compose through the previous hop's, so a
        deep base's instantiation is spelled over the ORIGINAL struct's type
        parameters: ``a<T> extends b<T>`` over ``b<U> extends c<list<U>>``
        yields ``[("b", [T], 1), ("c", [list<T>], 2)]``.

        Args:
            name: The (derived) struct's name.

        Returns:
            ``(base name, composed type arguments, hop, declaring source)``
            per hop, nearest first; empty for a non-struct or a root. The
            chain ends at a builtin base (no declaration to walk further),
            a cycle, or an arity mismatch (the instantiation reports those).
        """
        chain: "list[tuple[str, list[TypeRef], int, str | None]]" = []
        seen = {name}
        decl = self.lookup_struct_decl(name)
        binding: "dict[str, TypeRef] | None" = None
        hop = 0
        while decl is not None:
            ref = self.struct_base_ref(decl)
            if ref is None:
                break
            if binding is not None:
                ref = self.subst_struct_args(ref, binding)
            hop += 1
            if ref.name in seen:
                break  # cyclic extends; instantiation reports it
            seen.add(ref.name)
            chain.append((ref.name, list(ref.args), hop, decl.source))
            next_decl = self.lookup_struct_decl(ref.name)
            if next_decl is None:
                break  # a builtin base (e.g. slice): the chain ends
            if len(next_decl.type_params) != len(ref.args):
                break  # arity mismatch; instantiation reports it
            binding = dict(zip(next_decl.type_params, ref.args))
            decl = next_decl
        return chain

    def inherited_candidates(self, family: str) -> list[Func]:
        """The rebased base-chain members a derived family call merges in.

        A derived struct exposes its base chain's method families: a call to
        ``pointf::magnitude`` (dot sugar included) resolves over the union of
        ``pointf``'s own members and each base hop's, the latter entering as
        resolution-only clones rebased at the declared base instantiation
        (see :meth:`rebase_member`). The merged set is one overload set --
        same-shape members shadow by hop in :meth:`call_rank`, different
        shapes overload -- and there is no cascade: each hop contributes only
        the members declared on it.

        Args:
            family: The qualified name as called, e.g.
                ``"pointf::magnitude"``.

        Returns:
            The clone list (possibly empty), cached per (source, family).
        """
        if "::" not in family:
            return []
        key = (self.current_source, family)
        cached = self.inherited_sets.get(key)
        if cached is not None:
            return cached
        qualifier, method = family.split("::", 1)
        clones: list[Func] = []
        derived = self.lookup_struct_decl(qualifier)
        if derived is not None:
            derived_params = list(derived.type_params)
            for base_name, base_args, hop, source in self.base_chain(qualifier):
                base_family = f"{base_name}::{method}"
                members = list(self.templates.get(base_family, ()))
                if base_family in self.overloads:
                    members += self.overloads[base_family]
                else:
                    members += self.concrete_decls.get(base_family, {}).values()
                label = base_name + (
                    "<" + ", ".join(str(a) for a in base_args) + ">"
                    if base_args
                    else ""
                )
                for origin in members:
                    clone = self.rebase_member(
                        origin, qualifier, derived_params, method,
                        base_name, base_args, hop, label, source,
                    )
                    if clone is not None:
                        clones.append(clone)
        self.inherited_sets[key] = clones
        return clones

    def rebase_member(
        self,
        origin: Func,
        derived_name: str,
        derived_params: list[str],
        method: str,
        base_name: str,
        base_args: "list[TypeRef]",
        hop: int,
        base_label: str,
        source: "str | None",
    ) -> "Func | None":
        """Clone a base family member onto a deriving struct, or filter it.

        The member's positional qualifier annotation
        (:attr:`Func.qualifier_args`) matches against the declared base
        instantiation's arguments: a fresh type parameter seeds (``T ->
        float64`` for ``pointf extends point<float64>``, or ``T -> U`` for a
        generic derivation), while a concrete (specialized) position must
        agree with the declared argument -- a specialization for another
        instantiation simply does not apply and filters out, as does a
        diagonal qualifier the base arguments break, or a seeded parameter
        whose type group / ``extends`` bound the concrete seed violates.

        The clone renames to the derived family, respells the receiver as
        the DERIVED type (so a concrete derivation's inherited member ranks
        concrete -- tier 2 -- and a generic one stays generic), substitutes
        the seed through the remaining parameters and the return type, and
        keeps the origin's leftover (method-own) type parameters, renamed
        away from collisions with the derived struct's. It is registered in
        ``inherited_origins`` for emission (which instantiates the ORIGIN)
        and its constraint tables are forwarded/rebased so ranking,
        viability, and subsumption see the bounds the origin declared.

        Args:
            origin: The base family member.
            derived_name: The deriving struct's name.
            derived_params: The deriving struct's type parameters.
            method: The family's method name.
            base_name: The base hop's struct (or builtin) name.
            base_args: The declared base instantiation's arguments, spelled
                over ``derived_params``.
            hop: Distance up the chain (1 = immediate base).
            base_label: The base instantiation's display spelling.
            source: The file whose ``extends`` clause spelled ``base_args``.

        Returns:
            The resolution-only clone, or ``None`` when the member does not
            apply at this instantiation.
        """
        qargs = origin.qualifier_args or []
        if len(qargs) != len(base_args):
            return None  # a bare-qualifier member of a generic builtin
        outer_source = self.current_source
        self.current_source = origin.source
        try:
            seed: "dict[str, TypeRef]" = {}
            for qa, ba in zip(qargs, base_args):
                if (
                    qa.params is None
                    and not qa.args
                    and not qa.stars
                    and not qa.dims
                    and qa.name in origin.type_params
                ):
                    prior = seed.get(qa.name)
                    if prior is not None and str(prior) != str(ba):
                        return None  # a diagonal qualifier the base breaks
                    seed[qa.name] = ba
                    continue
                # A concrete (specialized) position: the member applies only
                # when the declared base argument is that exact type.
                if self.names_type_param(ba, derived_params):
                    return None  # not provable per-declaration; filtered
                theirs = self.resolve_concrete_pattern(qa, origin)
                ours = self.resolve_ref_at(ba, source)
                if theirs is None or ours is None or theirs != ours:
                    return None
            # Constraints on seeded parameters: a concrete seed is judged
            # now (a violating member is not inherited); a seed naming a
            # derived parameter transfers the constraint onto it; anything
            # else defers to the instantiation backstop.
            ogroups = self.group_types.get(id(origin), {})
            obounds = self.bound_types.get(id(origin), {})
            clone_groups: "dict[str, list[LangType]]" = {}
            clone_bounds: "dict[str, LangType]" = {}
            group_refs: "dict[str, list[TypeRef]]" = {}
            bound_refs: "dict[str, TypeRef]" = {}
            for pname, ref in seed.items():
                group = ogroups.get(pname)
                bound = obounds.get(pname)
                if group is None and bound is None:
                    continue
                if (
                    ref.params is None
                    and not ref.args
                    and not ref.stars
                    and not ref.dims
                    and ref.name in derived_params
                ):
                    if group is not None:
                        clone_groups[ref.name] = group
                        group_refs[ref.name] = origin.type_param_groups[pname]
                    if bound is not None:
                        clone_bounds[ref.name] = bound
                        bound_refs[ref.name] = origin.type_param_bounds[pname]
                    continue
                if self.names_type_param(ref, derived_params):
                    continue  # judged per instantiation by the backstop
                resolved = self.resolve_ref_at(ref, source)
                if resolved is None:
                    continue  # ditto
                bare = strip_const(resolved)
                if group is not None and all(
                    m != resolved and m != bare for m in group
                ):
                    return None  # outside the closed group: not inherited
                if bound is not None and not self.nominal_subtype(bound, bare):
                    return None  # fails the extends bound: not inherited
            # Leftover (method-own) parameters, renamed off a collision with
            # a derived struct parameter.
            rename: "dict[str, str]" = {}
            used = set(derived_params)
            leftovers: list[str] = []
            for pname in origin.type_params:
                if pname in seed:
                    continue
                new = pname
                while new in used:
                    new += "'"
                used.add(new)
                rename[pname] = new
                leftovers.append(new)
                if pname in ogroups:
                    clone_groups[new] = ogroups[pname]
                    group_refs[new] = origin.type_param_groups[pname]
                if pname in obounds:
                    clone_bounds[new] = obounds[pname]
                    bound_refs[new] = origin.type_param_bounds[pname]
            binding: "dict[str, TypeRef]" = dict(seed)
            for old, new in rename.items():
                if new != old:
                    binding[old] = TypeRef(new)
            params: list[tuple[str, TypeRef]] = []
            for i, (pname, ptype) in enumerate(origin.params):
                head = self.dealias_pattern(ptype, origin.type_params)
                if (
                    i == 0
                    and head.stars == 0
                    and not head.dims
                    and head.params is None
                    and head.name == base_name
                    and len(head.args) == len(qargs)
                    and all(
                        str(x) == str(y) for x, y in zip(head.args, qargs)
                    )
                ):
                    # The receiver respells as the DERIVED type: the family
                    # is exposed on it, so its own spelling is the pattern.
                    params.append(
                        (
                            pname,
                            TypeRef(
                                derived_name,
                                args=[TypeRef(p) for p in derived_params],
                                const=ptype.const,
                                nonnull=ptype.nonnull,
                                mut=ptype.mut,
                            ),
                        )
                    )
                    continue
                params.append((pname, self.subst_struct_args(ptype, binding)))
            ret = self.subst_struct_args(origin.ret_type, binding)
            defaults = {
                rename[pname]: self.subst_struct_args(dref, binding)
                for pname, dref in origin.type_param_defaults.items()
                if pname not in seed
            }
            type_params = [
                p
                for p in derived_params
                if any(self.names_type_param(t, [p]) for _, t in params)
                or self.names_type_param(ret, [p])
            ] + leftovers
            clone = dataclasses_replace(
                origin,
                name=f"{derived_name}::{method}",
                type_params=type_params,
                params=params,
                ret_type=ret,
                type_param_defaults=defaults,
                type_param_groups=group_refs,
                type_param_bounds=bound_refs,
            )
            if clone_groups:
                self.group_types[id(clone)] = clone_groups
            if clone_bounds:
                self.bound_types[id(clone)] = clone_bounds
            self.inherited_origins[id(clone)] = _InheritedOrigin(
                origin, seed, rename, hop, base_label, source
            )
            return clone
        finally:
            self.current_source = outer_source

    def resolve_ref_at(
        self, ref: TypeRef, source: "str | None"
    ) -> "LangType | None":
        """Resolve a ``TypeRef`` under a given file, with no live bindings.

        Used while rebasing inherited members, where an ``extends`` clause's
        type arguments belong to the deriving struct's file.

        Args:
            ref: The reference to resolve.
            source: The file whose scope it resolves in.

        Returns:
            The resolved type, or ``None`` when it does not resolve.
        """
        outer_bindings = self.type_bindings
        outer_source = self.current_source
        self.type_bindings = {}
        self.current_source = source
        try:
            return self.lang_type(ref, 0)
        except LangError:
            return None
        finally:
            self.type_bindings = outer_bindings
            self.current_source = outer_source

    def origin_bindings(
        self, inh: _InheritedOrigin, bindings: "dict[str, LangType]", line: int
    ) -> "dict[str, LangType]":
        """An inherited winner's bindings, translated to its origin template.

        The seed references the ``extends`` clause fixed resolve under the
        clone's deduced bindings (a generic derivation's ``T -> U`` seed
        picks up the call's ``U``); the leftover parameters carry over
        through their rename.

        Args:
            inh: The clone's origin record.
            bindings: The clone's deduced bindings.
            line: The call's line, for diagnostics.

        Returns:
            The complete ``{origin type parameter: type}`` map.
        """
        outer_bindings = self.type_bindings
        outer_source = self.current_source
        self.type_bindings = bindings
        self.current_source = inh.source
        try:
            resolved: "dict[str, LangType]" = {}
            for pname in inh.origin.type_params:
                ref = inh.seed.get(pname)
                if ref is not None:
                    resolved[pname] = self.lang_type(ref, line)
                else:
                    resolved[pname] = bindings[inh.rename[pname]]
            return resolved
        finally:
            self.type_bindings = outer_bindings
            self.current_source = outer_source

    def receiver_view(
        self, pattern: TypeRef, actual: LangType, type_params: list[str]
    ) -> LangType:
        """The base-chain view a method call's receiver unifies through.

        The receiver position of a method-family call upcasts: when the
        first parameter's pattern heads a struct that is a declared base of
        the argument's ``extends`` lineage, inference and the shape filter
        see the receiver AS that base instantiation -- so
        ``point::magnitude(p)`` with a ``pointf`` receiver binds
        ``T = float64``. Receiver position only; every other argument keeps
        the explicit ``as`` upcast.

        Args:
            pattern: The candidate's first parameter pattern.
            actual: The receiver argument's type.
            type_params: The candidate's type-parameter names.

        Returns:
            The matching base instantiation, or ``actual`` unchanged.
        """
        if actual is CTOR_SELF or actual is NULLT:
            return actual
        p = self.dealias_pattern(pattern, type_params)
        if p.stars or p.dims or p.params is not None or p.name in type_params:
            return actual
        bare = strip_const(actual)
        if not is_struct(bare):
            return actual
        if bare.template == p.name or bare.name == p.name:
            return actual
        current = bare.base
        while current is not None:
            anc = strip_const(current)
            if anc.template == p.name or anc.name == p.name:
                return const_of(anc) if actual.const else anc
            current = anc.base
        return actual

    def receiver_upcast_target(
        self, t: LangType, p: LangType
    ) -> "LangType | None":
        """The base type a method receiver upcasts to, or ``None``.

        Args:
            t: The receiver argument's type.
            p: The resolved receiver parameter's type.

        Returns:
            The (const-stripped) base target when ``t`` reaches ``p`` up its
            declared ``extends`` lineage, else ``None``.
        """
        target = strip_const(p)
        bare = strip_const(t)
        if not is_struct(target) or not is_struct(bare) or target == bare:
            return None
        return target if self.nominal_subtype(target, bare) else None

    def upcast_struct_value(self, tv: TypedValue, target: LangType) -> TypedValue:
        """Prefix-copy a derived struct value as one of its declared bases.

        The value round-trips through memory -- store the derived value,
        reinterpret the slot as the base, load -- which keeps any
        ``@packed``/``@align`` padding identical (the same honest data
        slicing the ``as`` upcast performs).

        Args:
            tv: The derived struct value.
            target: The base type (a declared ancestor of ``tv.type``).

        Returns:
            The base-typed prefix value.
        """
        slot = self.builder.alloca(strip_const(tv.type).ir)
        self.builder.store(tv.value, slot)
        base_ptr = self.builder.bitcast(slot, target.ir.as_pointer())
        return TypedValue(self.builder.load(base_ptr), target)

    def upcast_hidden_ref(self, tv: TypedValue, target: LangType):
        """Spill a derived receiver value and lend it as a base reference.

        A ``const`` (hidden-reference) receiver parameter takes a pointer;
        a derived value already lowered for inference spills to a temporary
        of its OWN type, whose address then reads as the base prefix.

        Args:
            tv: The derived struct value.
            target: The base type (a declared ancestor of ``tv.type``).

        Returns:
            The temporary's address, typed as a base pointer.
        """
        src = strip_const(tv.type)
        tmp = self.entry_alloca(src.ir)
        if over_aligned(src):
            tmp.align = type_align(src)
        self.builder.store(tv.value, tmp)
        return self.builder.bitcast(tmp, target.ir.as_pointer())

    def ctor_sugar_target(self, expr: Call) -> "str | None":
        """The canonical type name a bare call would construct, or ``None``.

        The constructor sugar ``S(args)`` sits at name resolution's last
        resort: a variable, constant, file-scoped ``@static``, or function
        of the same name wins unconditionally (the checks below mirror
        :meth:`gen_call`'s resolution order), and only a leftover name that
        names a type -- a struct, union, builtin, or a type alias chain
        landing on one -- is construction.

        Args:
            expr: The ``Call`` node.

        Returns:
            The canonical type name (alias-chased), or ``None`` when the
            call is not constructor sugar.
        """
        name = expr.name
        if "::" in name:
            return None
        if self.var_type_of(name) is not None or name in self.consts:
            return None
        key = (self.current_source, name)
        if key in self.static_templates or key in self.static_funcs:
            return None
        if (
            name in self.templates
            or name in self.overloads
            or name in self.funcs
        ):
            return None
        return self.chase_alias_qualifier(name, expr.line)

    def ctor_head_is_bare(self, name: str) -> bool:
        """Whether a sugar head's written spelling pins no instantiation.

        ``point(1, 2)`` is bare (resolution infers the type arguments from
        the constructor's arguments), and so is a plain alias chain landing
        on the bare struct name; ``pointf(1, 2)`` over ``type pointf =
        point<float64>`` is not -- the alias spells the instantiation, so
        the receiver types up front (a generic alias used bare keeps the
        type-use arity error).

        Args:
            name: The written head, already known to chase to a type.

        Returns:
            ``True`` when no link of the alias chain carries type arguments.
        """
        seen: set[str] = set()
        while (
            self.lookup_struct_decl(name) is None
            and name not in TYPES
            and name not in RESERVED_TYPE_NAMES
        ):
            alias = self.lookup_alias(name)
            if alias is None or name in seen:
                return True  # unreachable: the chase already validated this
            seen.add(name)
            if alias.target.args:
                return False
            name = alias.target.name
        return True

    def ctor_zero_arg_member(self, family: str) -> bool:
        """Whether a constructor family claims the zero-argument call.

        The declared-wins rule of the implicit empty constructor: a visible
        family member that can accept exactly one argument -- the hidden
        receiver -- claims ``T()`` and resolves (or errors) normally; the
        implicit ``let t: T;`` form applies only when none can. Arity is the
        judgment: a non-collecting member with one parameter, or a
        collecting member whose fixed prefix is at most the receiver. A
        cross-module ``@private`` member is not visible here, exactly as it
        is not a candidate at the call.

        Args:
            family: The qualified name, e.g. ``"point::constructor"``.

        Returns:
            ``True`` when some visible member takes the zero-argument call.
        """
        key = (self.current_source, family)
        symbol = self.static_funcs.get(key)
        if symbol is not None:
            _, params, _ = self.signatures[symbol]
            if (
                len(params) - 1 <= 1
                if self.collecting_params(params)
                else len(params) == 1
            ):
                return True
        members = list(self.templates.get(family, ()))
        if family in self.overloads:
            members += self.overloads[family]
        else:
            members += self.concrete_decls.get(family, {}).values()
        members += self.inherited_candidates(family)
        static_template = self.static_templates.get(key)
        if static_template is not None:
            members.append(static_template)
        for func in members:
            if func.private and func.source != self.current_source:
                continue
            if self.collecting_candidate(func):
                if len(func.params) - 1 <= 1:
                    return True
            elif len(func.params) == 1:
                return True
        return False

    def gen_empty_ctor(self, expr: Call, canonical: str, decl) -> tuple:
        """Emit the implicit empty constructor: ``T()`` is ``let t: T;``.

        Allocates a slot of the (fully-spelled) type and default-initializes
        it exactly as the bare declaration would -- a struct with declared
        field defaults starts from them, anything else starts uninitialized.
        No family member runs; there may not even be one. A bare generic
        head with required parameters has nothing to infer from (there are
        no arguments), so it keeps the constructor sugar's cannot-infer
        error; a fully-defaulted generic is a complete type, as everywhere.

        Args:
            expr: The zero-argument sugar ``Call``.
            canonical: The alias-chased type name being constructed.
            decl: The struct declaration behind ``canonical``, or ``None``
                for a builtin.

        Returns:
            The ``(slot, LangType)`` pair of the default-initialized value.

        Raises:
            LangError: On a bare generic head with required type parameters,
                or a head that does not spell a complete type.
        """
        if (
            not expr.type_args
            and decl is not None
            and decl.type_params
            and len(decl.type_param_defaults) < len(decl.type_params)
            and self.ctor_head_is_bare(expr.name)
        ):
            missing = [
                p
                for p in decl.type_params
                if p not in decl.type_param_defaults
            ]
            raise LangError(
                f"cannot infer type parameter(s) {', '.join(missing)} for "
                f"'{canonical}::constructor'; spell the instantiation, e.g. "
                f"{expr.name}<int32>(...)",
                expr.line,
            )
        built = strip_const(
            self.lang_type(
                TypeRef(expr.name, args=list(expr.type_args)), expr.line
            )
        )
        if built is VOID:
            raise LangError("cannot construct a void value", expr.line)
        slot = self.builder.alloca(built.ir)
        if over_aligned(built):
            slot.align = type_align(built)
        elif is_valist(built):
            self.require_valist(expr.line)
            slot.align = self.va_list_align  # as `let v: va_list;` aligns
        if is_struct(built):
            self.init_struct_defaults(slot, built)
        return slot, built

    def gen_ctor_call(self, expr: Call, canonical: str) -> tuple:
        """Emit ``S(args)`` constructor sugar: allocate, then construct.

        The desugaring is ``let s: S; S::constructor(s, args);`` -- the slot
        is allocated (default-initialized exactly as a bare ``let s: S;``)
        and passed as the family call's first argument, so overload
        resolution, ``mut``/``const`` receiver legality, and every
        diagnostic are the desugared call's own. A fully-spelled head
        (explicit type arguments, a non-generic type, or an alias of a
        complete type) types the slot up front, letting the receiver bind
        the struct's type parameters during inference; a bare generic head
        defers the slot to resolution instead (see :class:`_CtorSelf`),
        where the arguments and declared defaults deduce the instantiation
        -- exactly as a bare qualified call ``S::constructor(s, ...)``
        infers.

        Every type also has an **implicit empty constructor**: ``T()`` with
        no arguments is ``let t: T;`` -- the slot, default-initialized
        exactly as the bare declaration, is the value. Declared members win:
        a family member that accepts just the receiver (arity one, or a
        collecting member whose fixed prefix is at most the receiver) claims
        the zero-argument call and resolves normally; only when no visible
        member can is the implicit form used -- so declaring constructors
        never suppresses it, and no ambiguity between the two ever arises.

        Args:
            expr: The sugar ``Call`` (``expr.name`` is the written head).
            canonical: The alias-chased type name being constructed.

        Returns:
            The ``(slot, LangType)`` pair of the constructed value.

        Raises:
            LangError: When no constructor family is declared for the type
                (and the call has arguments), or the family call itself
                fails to resolve.
        """
        family = f"{canonical}::constructor"
        decl = self.lookup_struct_decl(canonical)
        if not expr.args and not self.ctor_zero_arg_member(family):
            # The implicit empty constructor: no declared member takes just
            # the receiver, so `T()` is `let t: T;` -- family or no family.
            return self.gen_empty_ctor(expr, canonical, decl)
        if not self.method_family_exists(family):
            if decl is not None:
                kind = "union" if decl.union else "struct"
                hint = (
                    f"declare 'fn {canonical}::constructor(...)' or build "
                    "the value with a struct literal"
                )
            else:
                kind = "type"
                hint = f"declare 'fn {canonical}::constructor(...)'"
            raise LangError(
                f"{kind} {expr.name!r} has no constructor; {hint}", expr.line
            )
        if (
            not expr.type_args
            and decl is not None
            and decl.type_params
            # A fully-defaulted generic is a complete type when written
            # bare (`box(1)` constructs box at its defaults, exactly as
            # `let b: box;` reads) -- only a head with required parameters
            # is inference.
            and len(decl.type_param_defaults) < len(decl.type_params)
            and self.ctor_head_is_bare(expr.name)
        ):
            # A bare generic head: the receiver enters resolution as a
            # placeholder, and the winner's first parameter fixes the
            # instantiation. A family that is one plain concrete function
            # (a lone specialization) never enters the set path, so its
            # declared receiver fixes the slot directly instead -- unless
            # inherited members join it into a set after all.
            key = (self.current_source, family)
            if (
                family not in self.templates
                and family not in self.overloads
                and key not in self.static_templates
                and not self.inherited_candidates(family)
            ):
                symbol = self.static_funcs.get(key, family)
                _, params, _ = self.signatures[symbol]
                built = strip_const(params[0]) if params else None
                if built is None or not (
                    built.template == canonical or built.name == canonical
                ):
                    raise LangError(
                        f"cannot construct {expr.name!r}: "
                        f"'fn {family}' does not take the constructed "
                        f"{canonical} as its first parameter",
                        expr.line,
                    )
                return self.emit_ctor_into(expr, family, built)
            marker = _CtorSelf(canonical, expr.line)
            self.gen_call(Call(family, [], [marker, *expr.args], expr.line))
            return marker.slot, marker.type
        built = strip_const(
            self.lang_type(
                TypeRef(expr.name, args=list(expr.type_args)), expr.line
            )
        )
        return self.emit_ctor_into(expr, family, built)

    def emit_ctor_into(self, expr: Call, family: str, built: LangType) -> tuple:
        """Allocate a typed receiver slot and emit the constructor call.

        The slot rides a hidden local (an unlexable name, so it can never
        collide or be captured), giving the family call an ordinary
        addressable receiver argument -- a ``mut self`` lends it, a
        ``const self`` borrows it, a by-value ``self`` copies it, all
        through the unchanged argument machinery.

        Args:
            expr: The sugar ``Call`` supplying the constructor arguments.
            family: The resolved ``Type::constructor`` family name.
            built: The constructed type (the slot's type).

        Returns:
            The ``(slot, LangType)`` pair of the constructed value.
        """
        slot = self.builder.alloca(built.ir)
        if over_aligned(built):
            slot.align = type_align(built)
        # Default field values initialize the slot exactly as `let s: S;`
        # does; a defaultless struct stays uninitialized for the
        # constructor to fill.
        if is_struct(built):
            self.init_struct_defaults(slot, built)
        hidden = f"0ctor{self.hidden_seq}"
        self.hidden_seq += 1
        self.locals[hidden] = (slot, built)
        try:
            self.gen_call(
                Call(family, [], [Var(hidden, expr.line), *expr.args], expr.line)
            )
        finally:
            del self.locals[hidden]
        return slot, built

    def gen_call(self, expr: Call) -> TypedValue:
        """Emit a call to a named function.

        Resolves the name in order: an alias-qualified method name (rewritten
        to its canonical family and re-dispatched), the
        ``va_start``/``va_end`` builtins, a same-named variable holding a
        function pointer (called indirectly), a file-scoped ``@static``
        function or generic, then a global function or generic overload set.

        Args:
            expr: The ``Call`` node.

        Returns:
            The call's result as a ``TypedValue``.

        Raises:
            LangError: When the name is not callable, is undefined, or misuses
                generic type arguments.
        """
        # A method call through an alias qualifier canonicalizes by name --
        # `pointf::magnitude(p)` is `point::magnitude(p)` -- and re-enters
        # under the family every spelling registered to.
        if "::" in expr.name:
            qualifier, method = expr.name.split("::", 1)
            canonical = self.chase_alias_qualifier(qualifier, expr.line)
            if canonical is not None and canonical != qualifier:
                return self.gen_call(
                    dataclasses_replace(expr, name=f"{canonical}::{method}")
                )
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
        # A method family's candidate set merges the receiver type's own
        # members with its base chain's, rebased at the declared base
        # instantiation (method inheritance); a plain name inherits nothing.
        inherited = (
            self.inherited_candidates(expr.name) if "::" in expr.name else []
        )
        generic = self.templates.get(expr.name)
        if generic is not None or expr.name in self.overloads or inherited:
            # An overload set -- generic, concrete, or mixed -- resolves in
            # one order (viability, group filter, then the
            # (tier, specificity) rank) through the same pre-evaluate path.
            # A mixed set's
            # concrete side is either the mangled set or a single plain
            # function sharing the name (which also joins here when
            # inherited members make a set of a lone declaration).
            candidates = list(generic) if generic is not None else []
            if expr.name in self.overloads:
                candidates += self.overloads[expr.name]
            else:
                candidates += self.concrete_decls.get(expr.name, {}).values()
            candidates += inherited
            # Sets are open and privacy is per overload: another module's
            # @private member is not a candidate here -- the call simply
            # falls through to the members this file can see (so a foreign
            # @private overload can never win, shadow, or make a call
            # ambiguous outside its module).
            visible = [
                f
                for f in candidates
                if not f.private or f.source == self.current_source
            ]
            if not visible:
                owners = ", ".join(
                    sorted(
                        {
                            f.source.rsplit("/", 1)[-1]
                            if f.source
                            else "its file"
                            for f in candidates
                        }
                    )
                )
                raise LangError(
                    f"function {expr.name!r} is private to {owners}",
                    expr.line,
                )
            if expr.type_args and not any(f.type_params for f in visible):
                # Explicit type arguments select among the generic
                # candidates; a purely concrete set has none.
                raise LangError(
                    f"{expr.name!r} is not a generic function", expr.line
                )
            return self.gen_generic_call(expr, visible)
        if expr.name not in self.funcs:
            # Name resolution's last resort: a leftover name that names a
            # type is constructor sugar -- `point<float64>(1, 1)` desugars
            # to `let s: point<float64>; point::constructor(s, 1, 1);` and
            # evaluates to the constructed value. A same-named function
            # (or variable, constant, or @static) won above, so the sugar
            # can never shadow one.
            canonical = self.ctor_sugar_target(expr)
            if canonical is not None:
                slot, built = self.gen_ctor_call(expr, canonical)
                return self.value_at(slot, built)
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
            self.emit_call(self.va_intrinsic(expr.name), [i8ptr]), VOID
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
        # A function value is a call site in waiting: warn here, since calls
        # through the pointer are indirect and can no longer be attributed.
        self.warn_deprecated(name, self.deprecated_syms.get(symbol), line)
        ret, params, variadic = self.signatures[symbol]
        # The value's type spells the function's whole call contract --
        # @nonnull, mut, const-aggregate hidden references, and a mut
        # return -- so a call through it runs the same call-site checks,
        # passes the same by-reference arguments, and yields the same
        # lvalue-ness as a direct call. This is what lets an annotated or
        # hidden-reference or mut-returning function be a function value
        # at all.
        mutref = self.mut_ref.get(symbol, frozenset())
        return TypedValue(
            self.funcs[symbol],
            function_type(
                ret,
                tuple(params),
                variadic,
                self.nonnull_ref.get(symbol, frozenset()),
                mutref,
                self.hidden_ref.get(symbol, frozenset()) - mutref,
                mutret=symbol in self.mut_ret,
            ),
        )

    def emit_call(
        self,
        callee,
        args: list,
        preserves: bool = False,
        arg_attrs: dict | None = None,
    ) -> ir.Value:
        """Emit a call instruction, dropping projection facts unless proven safe.

        A callee can reach any non-local memory -- a global pointer, an
        address that escaped earlier, its own statics -- so a store to a
        guarded field cannot be ruled out: every ``narrowed_paths`` fact
        dies at the call, **unless** the write-effect analysis proved the
        callee transitively write-free (``preserves``; see
        :meth:`analyze_write_effects`). Name facts are untouched either way
        (an eligible local is unreachable from a callee; the ``mut``-lend
        channel invalidates at its own site). Every runtime call funnels
        through here -- direct, function-pointer, generic-instance, the
        ``for ... in`` protocol's ``_next``, ``@asm``, and the ``va_*``
        intrinsics; only the resolved-callee paths (direct, generic, and
        protocol ``_next``) ever pass ``preserves``.

        Args:
            callee: The LLVM function or callable value.
            args: The marshalled LLVM argument values.
            preserves: The callee's write-effect bit is proven clear, so
                projection facts survive the call.
            arg_attrs: Optional ``{index: attr-tuple}`` argument attributes for
                the call site -- an ``sret`` pointer for a struct-returning
                ``@extern`` -- matching the callee's declared attributes.

        Returns:
            The call instruction's result value.
        """
        result = self.builder.call(callee, args, arg_attrs=arg_attrs)
        if not preserves:
            self.narrowed_paths.clear()
        return result

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
        # A struct-passing @extern marshals its aggregates to the C ABI form
        # (coercion types, a pointer-to-copy, or a hidden sret slot); a plain
        # scalar/pointer @extern has no plan and takes the ordinary path.
        abi = self.extern_abi.get(symbol)
        sret_slot = None
        arg_attrs: dict[int, tuple] = {}
        byval_aligns: dict[int, int] = {}
        if abi is not None and isinstance(abi.ret, Indirect):
            sret_slot = self.entry_alloca(abi.ret.struct_ir)
            sret_slot.align = max(sret_slot.align or 0, abi.ret.align)
            arg_attrs[0] = ("sret",)
        if abi is not None:
            # A System V MEMORY argument passes `byval(T) align N`: mark it at
            # the call to match the declaration (the alignment is applied to the
            # call instruction's argument attribute below). The index shifts
            # past a leading sret pointer.
            offset = 1 if sret_slot is not None else 0
            for i, cls in enumerate(abi.args):
                if isinstance(cls, Indirect) and cls.by_value:
                    arg_attrs[i + offset] = ("byval",)
                    byval_aligns[i + offset] = cls.align
        args = self.marshal_args(
            expr.args,
            params,
            variadic,
            repr(expr.name),
            expr.line,
            self.hidden_ref.get(symbol, frozenset()),
            mut,
            self.nonnull_ref.get(symbol, frozenset()),
            # The trailing slice<const any> type is the collecting marker
            # (function-pointer calls stay explicit-slice; overload sets
            # collect through gen_generic_call), and a mut trailing
            # parameter never does.
            collecting=self.collecting_params(params)
            and len(params) - 1 not in mut,
            # The last fixed parameter is @format: a literal argument
            # desugars its positional placeholders at compile time.
            format=symbol in self.format_syms,
            # An @extern @nonnull slot grades a possibly-null argument by
            # posture; a native one always rejects it.
            extern=symbol in self.extern_decls,
            abi=abi,
            sret_slot=sret_slot,
            # A method-family call's receiver may upcast to a declared
            # `extends` base (`point::magnitude(p)` with a pointf receiver);
            # never for an @extern (no mcc lineage crosses the C boundary,
            # and its aggregates marshal through the ABI plan above anyway).
            receiver_upcast="::" in expr.name and abi is None,
        )
        raw = self.emit_call(
            self.funcs[symbol],
            args,
            preserves=symbol in self.fact_safe_syms,
            arg_attrs=arg_attrs or None,
        )
        for idx, align in byval_aligns.items():
            # `byval` carries an explicit alignment; set it on the call's
            # argument attribute so it renders `byval(T) align N` like the decl.
            raw.arg_attributes[idx].align = align
        if abi is not None and abi.ret is not None:
            # Reconstruct the struct return: a register return is stored back
            # into a struct slot and reloaded; a large return already sits in
            # the caller-allocated sret slot.
            result = TypedValue(
                self.reconstruct_abi_return(raw, ret, abi.ret, sret_slot), ret
            )
        elif symbol in self.mut_ret:
            # A mut return arrives as a pointer to the vouched storage: load
            # eagerly for value contexts (folded away when unused) and carry
            # the address for the lvalue surfaces.
            result = TypedValue(self.gen_load(raw), ret, lvalue=raw)
        else:
            result = TypedValue(raw, ret)
        if symbol in self.noreturn_syms:
            # The callee never returns: terminate the block, so the statement
            # diverges (no dummy return needed past it, dead code after it is
            # skipped, and a diverging guard body narrows like a return).
            # @noreturn is void-only, so the call cannot sit in expression
            # position -- terminating mid-statement is safe. Enclosing defers
            # deliberately do not run (matching C's exit); see the docs.
            self.builder.unreachable()
        return result

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
        # The type's contract runs the same call-site rules as a direct call:
        # the @nonnull proof (flow-narrowing and the postfix `!` hatch
        # included), the writable-lvalue rules for mut, and the by-reference
        # handover for mut and const-aggregate parameters. Always strict:
        # even a value of an @extern function checks unconditionally -- the
        # indirect call can no longer be attributed, so the graded extern
        # posture does not apply.
        args = self.marshal_args(
            arg_exprs,
            params,
            variadic,
            label,
            line,
            hidden=callee.type.mutref | callee.type.constref,
            mut=callee.type.mutref,
            nonnull=callee.type.nonnull,
        )
        raw = self.emit_call(callee.value, args)
        if callee.type.mutret:
            # A mut return arrives as a pointer to the vouched storage,
            # exactly as on the direct path: load eagerly for value contexts
            # (folded away when unused) and carry the address for the lvalue
            # surfaces -- assignment, projection, and mut re-lending all key
            # on `lvalue` and ride free.
            return TypedValue(self.gen_load(raw), ret, lvalue=raw)
        return TypedValue(raw, ret)

    @staticmethod
    def scan_positional(
        text: str, n_extras: int, fixed: int, label: str, line: int
    ) -> tuple[str, list[int] | None] | None:
        """Desugar positional ``{n}`` placeholders in an ``@format`` literal.

        Purely compile-time sugar over the sequential runtime form: each
        ``{n}`` selects the n-th collected argument (0-based), so
        ``println("{0}, {0}", x)`` desugars to ``println("{}, {}", x, x)``
        and the runtime parser stays sequential-only. In the positional form
        a ``:`` separates the index from the modifiers -- ``{0:x}`` desugars
        to ``{x}`` -- and the index-less ``{:mods}`` escape spells a bare
        runtime modifier that the new grammar would otherwise claim
        (``{:2}`` desugars to the ``{2}`` field width). One string commits
        to one style: manual ``{n}`` and automatic ``{}`` placeholders
        (``{:mods}`` counts as automatic) cannot mix.

        The walk replicates ``format_args``'s state machine exactly --
        ``{{``/``}}`` escape literal braces (so ``{{0}}`` renders literally),
        and its unspecified edges (an unclosed ``{``, a stray ``}``) pass
        through verbatim, never classified. Only a well-formed placeholder
        whose content is all digits, or digits before a ``:``, is positional;
        a digit-leading runtime modifier like ``{06x}`` passes through
        untouched.

        Args:
            text: The format string literal's decoded contents.
            n_extras: How many source arguments follow the format string (an
                explicit pass-through slice counts as one).
            fixed: The callee's fixed-parameter count, for numbering the
                unused-argument diagnostic.
            label: A description of the callee, for error messages.
            line: Source line of the literal, for diagnostics.

        Returns:
            ``None`` when the string needs no rewriting (automatic style,
            no escapes); otherwise ``(new_text, slot_map)`` where
            ``slot_map`` lists the selected argument index per placeholder
            in render order for the positional style, or is ``None`` for an
            automatic-style string that only had ``{:mods}`` escapes
            rewritten.

        Raises:
            LangError: On a mixed-style string, an out-of-range index, a
                collected argument no placeholder references, or a ``:``
                with no argument index before it (the colon is reserved in
                ``@format`` literals).
        """
        bracket_open = False
        bracket_closed = False
        span: list[str] = []
        span_start = -1
        # (start, end, replacement) rewrites over `text`, in order.
        rewrites: list[tuple[int, int, str]] = []
        slots: list[int] = []
        auto_text: str | None = None
        pos_text: str | None = None
        for i, c in enumerate(text):
            if c == "{":
                if bracket_open:  # `{{` (or an aborted span): a literal `{`
                    bracket_open = False
                    continue
                bracket_open = True
                span = []
                span_start = i
            elif c == "}":
                if bracket_closed:  # `}}`: a literal `}`
                    bracket_closed = False
                    continue
                if not bracket_open:
                    bracket_closed = True
                    continue
                bracket_open = False
                content = "".join(span)
                if text[span_start : i + 1] != "{" + content + "}":
                    # The span interleaved with a `}}` escape -- one of the
                    # runtime parser's unspecified edges. Pass it through
                    # verbatim, unclassified.
                    continue
                head, colon, mods = content.partition(":")
                if colon and head and not head.isdigit():
                    raise LangError(
                        "expected an argument index before ':' in format "
                        f"placeholder {{{content}}}",
                        line,
                    )
                if colon and not head:
                    # The `{:mods}` escape: a bare runtime modifier, spelled
                    # around the positional grammar. Automatic style.
                    auto_text = auto_text or "{" + content + "}"
                    rewrites.append((span_start, i + 1, "{" + mods + "}"))
                elif content and content.isdigit() or colon:
                    # `{n}` or `{n:mods}`: a positional placeholder.
                    pos_text = pos_text or "{" + content + "}"
                    index = int(head)
                    if index >= n_extras:
                        hint = (
                            ""
                            if colon
                            else f" (for a field width, write {{:{content}}})"
                        )
                        raise LangError(
                            f"positional placeholder {{{head}}} is out of "
                            f"range: {label} has {n_extras} argument(s) "
                            f"after the format string{hint}",
                            line,
                        )
                    slots.append(index)
                    rewrites.append((span_start, i + 1, "{" + mods + "}"))
                else:
                    # `{}` or a runtime modifier: automatic style, verbatim.
                    auto_text = auto_text or "{" + content + "}"
            elif bracket_open:
                span.append(c)
        if pos_text is not None and auto_text is not None:
            raise LangError(
                f"format string mixes automatic '{auto_text}' and "
                f"positional '{pos_text}' placeholders",
                line,
            )
        if pos_text is None and not rewrites:
            return None
        if pos_text is not None:
            used = set(slots)
            for j in range(n_extras):
                if j not in used:
                    raise LangError(
                        f"argument {fixed + j + 1} of {label} is never "
                        "referenced by the format string",
                        line,
                    )
        pieces, copied = [], 0
        for start, end, replacement in rewrites:
            pieces.append(text[copied:start])
            pieces.append(replacement)
            copied = end
        pieces.append(text[copied:])
        return "".join(pieces), slots if pos_text is not None else None

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
        format: bool = False,
        extern: bool = False,
        abi: "ExternABI | None" = None,
        sret_slot=None,
        receiver_upcast: bool = False,
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
            format: Whether the callee's last fixed parameter is ``@format``:
                a string literal bound to it desugars positional ``{n}``
                placeholders at compile time (see :meth:`scan_positional`),
                rewriting the literal and duplicating/reordering the
                once-evaluated extras into the collection. An f-string bound
                to it stands in as its parse-time-desugared text, its hole
                expressions becoming the collected extras (it admits no
                other extras).
            extern: Whether the callee is an ``@extern`` declaration, which
                grades a possibly-null ``@nonnull`` argument by posture instead
                of always rejecting it (see :meth:`check_nonnull_arg`).
            abi: The struct-passing C-ABI plan for an ``@extern`` callee, whose
                aggregate arguments marshal to the classified register/pointer
                form (see :meth:`lower_abi_arg`); ``None`` for every other call.
            sret_slot: The caller-allocated result slot to prepend as the hidden
                first argument when ``abi`` returns a struct indirectly.
            receiver_upcast: Whether this is a method-family call, whose
                first argument -- the receiver -- may upcast to a declared
                ``extends`` base of its type: a ``mut``/hidden reference
                lends the storage's base prefix, a by-value receiver
                prefix-copies. Never set for indirect or ``@extern`` calls.

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
        slot_map = None
        fmt = (
            arg_exprs[fixed - 1] if collecting and format and fixed >= 1 else None
        )
        if isinstance(fmt, FStrLit):
            # An f-string: parse time already desugared its text to the
            # sequential runtime form. The plain literal stands in through a
            # local copy (never an AST rewrite -- a call inside a template
            # body re-marshals per instantiation) and the hole expressions
            # splice in as the collected extras, evaluated below -- once
            # each, in source order.
            if len(arg_exprs) > fixed:
                raise LangError(
                    f"{label} takes no arguments after an f-string: the "
                    "placeholders already supply them",
                    line,
                )
            arg_exprs = arg_exprs[: fixed - 1] + [StrLit(fmt.value, fmt.line)]
            arg_exprs += [h.expr for h in fmt.holes]
        elif isinstance(fmt, StrLit):
            # An @format literal: desugar positional placeholders. The
            # rewritten literal stands in through a local copy, as above,
            # and lowers through the ordinary string-literal adaptation
            # below; the slot map reorders the once-evaluated extras into
            # the collection.
            scanned = self.scan_positional(
                fmt.value, len(arg_exprs) - fixed, fixed, label, fmt.line
            )
            if scanned is not None:
                new_text, slot_map = scanned
                arg_exprs = list(arg_exprs)
                arg_exprs[fixed - 1] = StrLit(new_text, fmt.line)
        args = []
        if sret_slot is not None:
            # The struct return's hidden pointer leads the argument list.
            args.append(sret_slot)
        # A C-variadic tail runs through the loop for its promotions; a
        # collecting tail is gathered separately below.
        head = arg_exprs[:fixed] if collecting else arg_exprs
        for i, arg_expr in enumerate(head):
            context = f"argument {i + 1} of {label}"
            if i in nonnull:
                self.check_nonnull_arg(arg_expr, context, line, extern=extern)
            abi_cls = abi.args[i] if abi is not None and i < len(abi.args) else None
            if abi_cls is not None:
                # A by-value aggregate argument to an @extern is marshalled to
                # the C ABI form (a register coercion or a pointer to a copy);
                # it is never hidden/mut/valist, so this precedes those paths.
                args.append(self.lower_abi_arg(arg_expr, params[i], abi_cls, line, context))
                continue
            if i in mut:
                # Before the string-literal adaptation: a literal is not the
                # caller's storage, so it must be rejected, not spilled.
                args.append(
                    self.mut_ref_arg(
                        arg_expr, params[i], line, context,
                        receiver=receiver_upcast and i == 0,
                    )
                )
                continue
            if i < len(params) and (
                self.struct_literal_adapts(arg_expr, params[i])
                or self.str_literal_adapts(arg_expr, params[i])
                or self.array_literal_adapts(arg_expr, params[i])
                or self.result_literal_adapts(arg_expr, params[i])
            ):
                # A string literal adapts to a char slice, an array literal to a
                # slice<T>, or a bare struct literal to the parameter's struct
                # (the implicit borrow / build). A parameter passed by hidden
                # reference (a `const` slice or struct) takes the value's
                # address, so spill the adapted value to a temporary first.
                tv = self.gen_adapted_literal(arg_expr, params[i], line)
                if i in hidden:
                    args.append(self.spill_to_temp(tv, params[i], line, context))
                else:
                    args.append(tv.value)
                continue
            if i in hidden:
                args.append(
                    self.hidden_ref_arg(
                        arg_expr, params[i], line, context,
                        receiver=receiver_upcast and i == 0,
                    )
                )
                continue
            if i < len(params) and is_valist(params[i]):
                # A va_list is handed over in its platform-specific passed form,
                # derived from its storage; coerce/decay do not apply.
                args.append(self.valist_arg(arg_expr, line))
                continue
            tv = self.gen_expr(arg_expr)
            if i < len(params):
                if receiver_upcast and i == 0:
                    # A by-value receiver prefix-copies into a declared base
                    # parameter (honest data slicing, as the `as` upcast).
                    target = self.receiver_upcast_target(tv.type, params[i])
                    if target is not None:
                        tv = self.upcast_struct_value(tv, target)
                tv = self.coerce(tv, params[i], line, f"argument {i + 1} of {label}")
                value = tv.value
            elif is_integer(tv.type) and tv.type.ir.width < 32:
                # C varargs promote small integers to int (sign- or
                # zero-extending to match the source type's signedness).
                extend = self.builder.sext if tv.type.signed else self.builder.zext
                value = extend(tv.value, INT32.ir)
            elif tv.type is BOOL:
                value = self.builder.zext(tv.value, INT32.ir)
            elif is_aggregate(tv.type):
                raise LangError(
                    "cannot pass a struct to a variadic function; pass a pointer", line
                )
            else:
                value = tv.value
            args.append(value)
        if collecting:
            extras = arg_exprs[fixed:]
            if slot_map is not None:
                # Positional desugar: evaluate each extra once, in source
                # order, then map values and expressions by slot -- a
                # duplicated slot re-boxes the same TypedValue, never
                # re-evaluates its expression.
                tvs = [self.gen_expr(e) for e in extras]
                args.append(
                    self.collect_from_values(
                        [tvs[s] for s in slot_map],
                        [extras[s] for s in slot_map],
                        params[-1],
                        fixed in hidden,
                        label,
                        line,
                    )
                )
            else:
                args.append(
                    self.collect_variadic_args(
                        extras, params[-1], fixed in hidden, label, line
                    )
                )
        return args

    def lower_abi_arg(self, arg_expr, ptype: LangType, cls, line: int, context: str):
        """Marshal one by-value aggregate argument into its C-ABI form.

        A :class:`~mcc.codegen.abi.Direct` aggregate is spilled to a slot of its
        coercion type and reloaded through that type, so the value arrives in
        the registers the ABI expects. An :class:`~mcc.codegen.abi.Indirect`
        aggregate is spilled to a fresh caller-owned copy whose pointer is
        passed. For AArch64/Win64 that pointer *is* the argument (matching
        clang's frontend-materialized copy); for an x86-64 System V MEMORY
        argument the same pointer additionally carries a ``byval`` attribute
        (added at the call site) so the backend copies the data onto the
        argument stack.

        Args:
            arg_expr: The argument expression.
            ptype: The parameter's aggregate ``LangType``.
            cls: The argument's classification.
            line: Source line for diagnostics.
            context: A label for coercion error messages.

        Returns:
            The LLVM value (a coerced register value, or a pointer to a copy).
        """
        tv = self.coerce(self.gen_expr(arg_expr), ptype, line, context)
        if isinstance(cls, Indirect):
            return self.spill_to_temp(tv, ptype, line, context)
        # Direct: store the struct into a coercion-typed slot (large enough and
        # aligned for the coercion type), then reload it in that form.
        slot = self.entry_alloca(cls.coerce_ir)
        self.builder.store(tv.value, self.builder.bitcast(slot, ptype.ir.as_pointer()))
        return self.builder.load(slot)

    def reconstruct_abi_return(self, raw, ret: LangType, cls, sret_slot):
        """Rebuild an ``@extern`` call's by-value struct return from its ABI form.

        A :class:`~mcc.codegen.abi.Direct` return arrives as a register value
        (``raw``): it is stored into a coercion-typed slot and reloaded as the
        struct. An :class:`~mcc.codegen.abi.Indirect` return was written by the
        callee into ``sret_slot``, which is loaded as the struct.

        Args:
            raw: The call instruction's result (the register value, or the void
                of an ``sret`` call).
            ret: The struct return ``LangType``.
            cls: The return classification.
            sret_slot: The caller-allocated slot for an indirect return.

        Returns:
            The reconstructed struct value.
        """
        if isinstance(cls, Indirect):
            return self.builder.load(sret_slot)
        slot = self.entry_alloca(cls.coerce_ir)
        self.builder.store(raw, slot)
        return self.builder.load(self.builder.bitcast(slot, ret.ir.as_pointer()))

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
        return self.collect_from_values(
            [self.gen_expr(e) for e in extras], extras, ptype, hidden, label, line
        )

    def collect_from_values(
        self,
        tvs: list[TypedValue],
        exprs: list,
        ptype: LangType,
        hidden: bool,
        label: str,
        line: int,
    ) -> ir.Value:
        """Form the trailing ``slice<const any>`` from already-evaluated extras.

        The boxing/slice-forming core of :meth:`collect_variadic_args`,
        shared with the overload-resolution path, whose extras were lowered
        before the winner was known -- no boxing happens before then (boxing
        is store-side and its borrow target is always ``const any``, so it
        is candidate-independent).

        Args:
            tvs: The evaluated extra arguments, in call order.
            exprs: Their source expressions (a bare struct variable shares
                its storage as the box's hidden reference).
            ptype: The trailing ``slice<const any>`` parameter type.
            hidden: Whether that parameter travels by hidden reference.
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

        if len(tvs) == 1:
            actual = strip_const(tvs[0].type)
            if is_slice(actual) and is_any(actual.args[0]):
                # Pass-through: the argument count equals the parameter count
                # and the final argument already is the trailing slice type.
                return passed(TypedValue(tvs[0].value, actual))
        boxed = [self.box_collected(tv, line, e) for tv, e in zip(tvs, exprs)]
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

    def box_collected(
        self, tv: TypedValue, line: int, arg_expr=None
    ) -> ir.Value:
        """Box one collected extra, copying an ``any`` through unchanged.

        The trailing ``slice<const any>`` is a call-scoped by-reference
        position, so a struct extra boxes by hidden reference (``borrow``):
        a bare variable's storage is shared directly (no copy), any other
        struct value spills to a call-scoped temporary inside
        :meth:`gen_box_any`.

        Args:
            tv: The evaluated extra argument.
            line: Source line for diagnostics.
            arg_expr: The extra's source expression, used to share a bare
                variable's storage as the struct box's hidden reference.

        Returns:
            The 24-byte ``any`` value to store into the collection run.

        Raises:
            LangError: When the extra is outside the boxable set (see
                :meth:`check_boxable`).
        """
        if is_any(tv.type):
            return tv.value  # any to any is a plain copy, never a nesting
        ref = None
        base = strip_const(tv.type)
        if is_aggregate(base) and not is_slice(base) and isinstance(arg_expr, Var):
            # A bare variable is side-effect-free to re-address, so share its
            # existing storage as the hidden reference rather than copy it.
            # (A slice boxes by value -- its 16 bytes are the payload.)
            addr, t, _, _ = self.gen_addr(arg_expr, line)
            if strip_const(t).ir is base.ir:
                ref = addr
        return self.gen_box_any(tv, line, borrow=True, ref=ref).value

    def narrowable_guard_names(self, cond, op: str) -> set:
        """Collect the facts a null-comparison guard flow-narrows.

        A bare comparison matches ``p <op> null`` or ``null <op> p``. Two
        shapes of ``p`` carry a fact:

        - a bare variable eligible for narrowing (a **name** fact): a plain
          pointer **local** (a global never narrows -- any call could store
          null into it) that is not a ``mut`` parameter (a callee taking two
          ``mut`` references can alias it, so per-name invalidation at the
          call would miss the write), not already ``@nonnull`` (nothing to
          narrow), and whose address is never taken anywhere in the function
          (see :func:`collect_addr_taken`);
        - a pointer-typed field projection like ``s.p`` or ``a->b->ptr``
          (a **path** fact, keyed by :meth:`nonnull_path_of`). Paths need
          none of the name exclusions -- their invalidation model is the
          blanket kill at every call and through-memory store, which covers
          aliasing wholesale -- but a ``@volatile`` owner anywhere along the
          path means the field can change between check and use, so no fact
          forms. Index expressions still carry no fact.

        ``and``/``or`` chains thread through: for the ``"!="`` query (facts
        that hold when the condition is *true*) an ``and`` unions both
        operands' facts, since both conjuncts held; for the ``"=="`` query
        (facts that hold when the condition is *false*) an ``or`` unions
        both, since both disjuncts failed. The other operator contributes
        nothing for that query -- a false ``and`` (or a true ``or``) pins
        down neither operand. One asymmetry: when the later-evaluated
        operand may call or store (see :func:`contains_call`), the earlier
        operand's *path* facts are dropped -- the field could be nulled
        between its test and the branch (a name fact has no such window:
        no call can reach an eligible local).

        Args:
            cond: The guard condition expression.
            op: The comparison to match: ``"!="`` collects the facts implied
                by the condition being true (then branch / loop body),
                ``"=="`` the facts implied by it being false (else branch /
                the remainder after a diverging then body / a loop's exit).

        Returns:
            The narrowable facts (possibly empty): variable names as
            strings, projection paths as tuples.
        """
        if isinstance(cond, Logical):
            if cond.op != ("and" if op == "!=" else "or"):
                return set()
            lhs = self.narrowable_guard_names(cond.lhs, op)
            if contains_call(cond.rhs):
                lhs = {fact for fact in lhs if isinstance(fact, str)}
            return lhs | self.narrowable_guard_names(cond.rhs, op)
        if not isinstance(cond, Binary) or cond.op != op:
            return set()
        if isinstance(cond.rhs, NullLit):
            other = cond.lhs
        elif isinstance(cond.lhs, NullLit):
            other = cond.rhs
        else:
            return set()
        if isinstance(other, Member):
            path = self.nonnull_path_of(other)
            return set() if path is None else {path}
        if not isinstance(other, Var):
            return set()
        name = other.name
        entry = self.locals.get(name)
        if entry is None or not is_pointer(entry[1]):
            return set()
        if name in self.mut_locals or name in self.nonnull_locals:
            return set()
        if name in self.addr_taken:
            return set()
        return {name}

    def nonnull_path_of(self, expr) -> "tuple[str, ...] | None":
        """The projection-fact key for a pointer-typed field chain, or None.

        A chain of ``Member`` hops rooted at a local variable maps to the
        tuple ``(base name, field, ...)``. The key is arrow-insensitive --
        ``->`` and ``.`` spell the same hop, unambiguous because a field is
        either struct-typed or pointer-typed -- and ``(*a).f`` canonicalizes
        to the same path as ``a->f`` (one deref per hop; a double deref is
        not a projection). The walk resolves owner types hop by hop, and no
        fact key exists when: the base is not a local (globals and
        call-rooted chains are excluded), any owner along the path is
        ``@volatile`` (directly or inherited via ``extends`` -- the field
        can change between check and use, mirroring the register-block
        rationale for volatile loads), a hop is not a struct field, or the
        final field is not pointer-typed. Array elements (``a->xs[0]``)
        carry no path.

        Args:
            expr: The candidate expression (any AST node).

        Returns:
            The path tuple, or ``None`` when the expression carries no fact.
        """
        fields: list[str] = []
        base = expr
        while isinstance(base, Member):
            fields.append(base.field)
            base = base.base
            # `(*a).f` is the same lvalue as `a->f`: strip a single deref so
            # both spell one path key.
            if isinstance(base, Unary) and base.op == "*":
                base = base.operand
        if not fields or not isinstance(base, Var):
            return None
        entry = self.locals.get(base.name)
        if entry is None:
            return None
        t = entry[1]
        for fname in reversed(fields):
            owner = strip_const(t)
            if is_pointer(owner):
                owner = strip_const(owner.pointee)
            if not is_aggregate(owner) or owner.volatile:
                return None
            for name, ftype in owner.fields:
                if name == fname:
                    t = ftype
                    break
            else:
                return None
        if not is_pointer(strip_const(t)):
            return None
        return (base.name, *reversed(fields))

    def narrow_nonnull(self, facts: set) -> set:
        """Record flow-narrowed non-null facts for a guard's branch.

        Args:
            facts: The facts to narrow (possibly empty): names as strings,
                projection paths as tuples.

        Returns:
            The facts actually added -- what the guard must retract again
            at branch exit (see :meth:`retract_narrowed`). A fact already
            narrowed is excluded (an outer guard's fact must survive this
            one's exit).
        """
        names = {fact for fact in facts if isinstance(fact, str)}
        paths = facts - names
        added = (names - self.narrowed_nonnull) | (paths - self.narrowed_paths)
        self.narrowed_nonnull |= names
        self.narrowed_paths |= paths
        return added

    def retract_narrowed(self, added: set):
        """Remove facts a guard added, at its branch's exit.

        Discards, rather than subtracts: a fact may already be gone (a call
        or store inside the branch blanket-killed the path facts).

        Args:
            added: What :meth:`narrow_nonnull` returned at branch entry.
        """
        for fact in added:
            if isinstance(fact, tuple):
                self.narrowed_paths.discard(fact)
            else:
                self.narrowed_nonnull.discard(fact)

    def kill_paths_rooted(self, name: str):
        """Drop every projection fact rooted at a variable name.

        The prefix kill: reassigning, compound-assigning, shadowing, or
        ``mut``-lending ``a`` retargets (or may null) what ``a`` denotes, so
        every ``a...`` path fact is stale. Facts rooted elsewhere survive --
        writing the *pointer* ``a`` cannot change another base's fields.

        Args:
            name: The base variable name.
        """
        self.narrowed_paths = {
            path for path in self.narrowed_paths if path[0] != name
        }

    def loop_kill_set(self, obj, kills: set[str] | None = None) -> set[str]:
        """Names whose flow-narrowed facts a loop could invalidate.

        A lexical pre-scan of the whole loop statement (condition and body,
        nested statements, ``defer`` bodies, and both branches of an ``@if``
        included), modeled on :func:`collect_addr_taken`. A name is killed by
        exactly the events that invalidate a narrowed fact during generation:
        an assignment (``Assign``), a compound assignment to the bare
        variable (unless it is pointer-typed: a pointer's ``+=``/``-=`` is
        arithmetic and keeps its fact, as in the compound path itself), a
        shadowing ``let`` (conservative: any redeclaration of the name), or
        lending the bare variable to a ``mut`` position of any callable
        sharing the callee's name (see :meth:`call_mut_positions`).
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
            # A compound assignment to a pointer variable is `+=`/`-=` (the
            # only forms a pointer admits): arithmetic off a non-null pointer
            # stays non-null, exactly as `p + n` proves, so the fact
            # survives -- matching the compound-assignment path itself. Any
            # other (or unknown) target type kills conservatively.
            target_type = self.var_type_of(obj.target.name)
            if target_type is None or not is_pointer(strip_const(target_type)):
                kills.add(obj.target.name)
        elif isinstance(obj, Let):
            kills.add(obj.name)
            kills.update(obj.extra)  # destructuring binders
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
        ``if (p == null) ...``), a field projection flow-narrowed the same
        way (``if (a->ptr != null) { ... }``; see :meth:`nonnull_path_of`),
        a postfix ``p!`` assertion (the programmer's explicit, unchecked
        claim), pointer arithmetic ``p + n`` / ``p - n`` (the derived address
        of ``&p[n]``, an always-non-null source like ``&p[n]`` itself), and an
        ``as`` cast to a pointer type of any of these (a
        pointer reinterpretation preserves the address; a non-pointer
        intermediate, e.g. an integer round-trip, severs the proof).

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
        if isinstance(expr, Binary) and expr.op in ("+", "-"):
            # Pointer arithmetic yields an always-non-null source, exactly as
            # `&p[n]` does (`p + n` is `&p[n]`); this also silences the
            # `-Wunchecked-dereference` warning on `*(p + n)`. Integer `+`/`-`
            # reaching a @nonnull pointer slot is caught by the type check.
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
        if isinstance(expr, (Index, Member)):
            # An array reached through a member/index chain (`grid[0]` as the
            # base of `grid[0][1]`, the field `unit.sizes`, a flexible
            # `p->data`) decays to a derived address -- a GEP off the chain's
            # base, the same always-non-null source `p + n` is. The chain is
            # judged by static type alone: array-typed steps are address
            # arithmetic, never loads.
            chain = self.static_chain_type(expr)
            if chain is not None and is_array(chain):
                return True
            if isinstance(expr, Member):
                return self.nonnull_path_of(expr) in self.narrowed_paths
        return False

    def static_chain_type(self, expr) -> "LangType | None":
        """The static type of a variable-rooted member/index chain.

        Walks ``Var``/``Member``/``Index`` steps (plus a postfix ``!``, an
        identity) syntactically, without emitting IR: a variable types as
        itself, a member as its field (through one pointee hop for ``->``),
        an index as the array element, pointee, or slice element. Anything
        else -- a call, a cast, arithmetic -- ends the walk.

        Args:
            expr: The expression rooting or continuing the chain.

        Returns:
            The chain's static type, or ``None`` when it cannot be judged
            syntactically.
        """
        if isinstance(expr, NonnullAssert):
            return self.static_chain_type(expr.operand)
        if isinstance(expr, Var):
            var_type = self.var_type_of(expr.name)
            return strip_const(var_type) if var_type is not None else None
        if isinstance(expr, Index):
            base = self.static_chain_type(expr.base)
            if base is None:
                return None
            if is_array(base):
                return strip_const(base.element)
            if is_slice(base):
                return strip_const(base.fields[0][1].pointee)
            if is_pointer(base):
                return strip_const(base.pointee)
            return None
        if isinstance(expr, Member):
            base = self.static_chain_type(expr.base)
            if base is not None and expr.arrow:
                base = strip_const(base.pointee) if is_pointer(base) else None
            if base is None or is_slice(base) or not base.fields:
                return None
            for field_name, field_type in base.fields:
                if field_name == expr.field:
                    return strip_const(field_type)
        return None

    def check_nonnull_arg(
        self, arg_expr, context: str, line: int, proven: "bool | None" = None,
        extern: bool = False,
    ):
        """Require proof that an argument to a ``@nonnull`` slot is non-null.

        The ``null`` literal is always a hard error, and a native (non-extern)
        ``@nonnull`` slot always rejects a possibly-null argument -- the callee
        body holds the parameter as a load-bearing non-null fact. An
        ``@extern`` slot instead grades the possibly-null case by posture (the
        ``extern-nonnull`` class), because a mechanical C port would otherwise
        hit a null-proof wall on every ``strcpy``/``strlen`` call:

        * **relaxed** (default): silently accepted.
        * **warn** (``-Wextern-nonnull``): a warning on the ``extern-nonnull``
          channel; the driver prints it only when the class is enabled, so a
          disabled class is exactly the relaxed posture.
        * **strict** (``-Werror=extern-nonnull`` or global ``-Werror`` with the
          class enabled): a hard error, restoring unconditional caller proof.

        Args:
            arg_expr: The argument expression.
            context: A label describing the site, for the error message.
            line: Source line for diagnostics.
            proven: The proof, when it was already judged at evaluation
                time (the generic call path pre-evaluates every argument;
                a later argument's nested call blanket-kills path facts,
                but an earlier argument's load already happened under its
                guard); ``None`` judges it here, against the current facts.
            extern: Whether the callee is an ``@extern`` declaration, selecting
                the graded posture for the possibly-null case.

        Raises:
            LangError: When the argument is the ``null`` literal, or is not
                provably non-null (see :meth:`proves_nonnull`) at a native slot
                or an extern slot under the strict posture.
        """
        if isinstance(arg_expr, NullLit):
            raise LangError(
                f"cannot pass null as {context}: the parameter is @nonnull", line
            )
        if proven is None:
            proven = self.proves_nonnull(arg_expr)
        if proven:
            return
        if extern and "extern-nonnull" not in self.error_classes:
            # relaxed or warn: never a hard error. The warning is always
            # collected (tagged extern-nonnull); the driver's class filter is
            # what makes an unenabled class silent (relaxed) and an enabled one
            # print (warn).
            self.warn(
                f"passing a possibly-null pointer as {context}: the parameter "
                "is @nonnull on an @extern declaration",
                line,
                wclass="extern-nonnull",
            )
            return
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
        context: str, line: int, proven: "bool | None" = None,
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
            proven: The proof, when it was already judged at evaluation
                time (see :meth:`check_nonnull_arg`); ``None`` judges it
                here, against the current facts.

        Raises:
            LangError: When the pointer is not provably non-null.
        """
        if proven is None:
            proven = self.proves_nonnull(arg_expr)
        if not proven:
            raise LangError(
                f"cannot pass a possibly-null {arg_type} as {context}: "
                f"decaying into a {kind} {ptype} parameter forms a reference, "
                "which is never null (narrow with a null check or assert "
                "with postfix '!')",
                line,
            )

    def hidden_ref_arg(
        self, arg_expr, ptype: LangType, line: int, context: str,
        receiver: bool = False,
    ) -> ir.Value:
        """Lower a hidden-reference (const struct) argument to a pointer.

        When the argument already has storage of the exact type, its address is
        shared directly -- no copy, which is the point of the optimization. A
        proven-non-null pointer to the parameter's type *decays*: the pointer
        value itself is forwarded as the hidden reference (see
        :meth:`decays_to`). An rvalue (or a type that still needs coercion)
        is materialized into a temporary whose address is passed instead.

        Args:
            arg_expr: The argument expression.
            ptype: The parameter's (struct) type.
            line: Source line for diagnostics.
            context: A label for coercion error messages.
            receiver: Whether this is a method-family call's receiver, which
                may upcast: a derived argument's storage (or temporary) is
                lent viewed as the declared base prefix.

        Returns:
            A pointer to the argument's storage.
        """
        if self.is_addressable_form(arg_expr):
            addr, t, align, volatile = self.gen_addr(arg_expr, line)
            if t.ir is ptype.ir:
                return addr
            if receiver:
                target = self.receiver_upcast_target(t, ptype)
                if target is not None:
                    # A derived receiver borrows as the base prefix: the
                    # same no-copy storage share, viewed as the base.
                    return self.builder.bitcast(addr, target.ir.as_pointer())
            if self.decays_to(t, ptype, mut=False):
                self.check_decay_arg(
                    arg_expr, strip_const(t), "const", ptype, context, line
                )
                return self.gen_load(addr, align=align, volatile=volatile)
            tv = TypedValue(self.gen_load(addr), t)
        else:
            tv = self.gen_expr(arg_expr)
            if tv.lvalue is not None and tv.type.ir is ptype.ir:
                # A mut-returning call's storage is shared as the hidden
                # reference directly -- the same no-copy path an addressable
                # argument takes (a const parameter promises not to write
                # through it).
                return tv.lvalue
            if receiver and tv.lvalue is not None:
                target = self.receiver_upcast_target(tv.type, ptype)
                if target is not None:
                    return self.builder.bitcast(
                        tv.lvalue, target.ir.as_pointer()
                    )
            if self.decays_to(tv.type, ptype, mut=False):
                self.check_decay_arg(
                    arg_expr, tv.type, "const", ptype, context, line
                )
                return tv.value
        if receiver:
            target = self.receiver_upcast_target(tv.type, ptype)
            if target is not None:
                # A derived rvalue receiver spills to its own temporary,
                # lent viewed as the base prefix.
                return self.upcast_hidden_ref(tv, target)
        return self.spill_to_temp(tv, ptype, line, context)

    def check_mut_storage(
        self, arg_expr, t: LangType, align: int | None, volatile: bool,
        line: int, what: str = "a mut argument",
    ):
        """Check that already-addressed storage may be lent as a ``mut`` argument.

        The legality half of :meth:`mut_ref_arg`, kept IR-free so a generic
        call can defer it until after overload resolution: ``writes_const`` is
        syntactic, and the const/volatile/alignment facts are the flags
        :meth:`gen_addr` already returned when the address was formed. A
        ``mut`` return hands out the same kind of reference, so its
        ``return`` site runs the same checks (``what`` labels the site in
        the diagnostics).

        Args:
            arg_expr: The argument expression (for the const-parameter check).
            t: The storage's type, as returned by :meth:`gen_addr`.
            align: The guaranteed alignment, as returned by :meth:`gen_addr`.
            volatile: The volatility flag, as returned by :meth:`gen_addr`.
            line: Source line for diagnostics.
            what: What the storage is being lent as, for the messages --
                ``"a mut argument"`` (the default) or ``"a mut return"``.

        Raises:
            LangError: When the storage is read-only, is ``@volatile``, sits
                at an unguaranteed (packed) alignment, or is a ``@nonnull``
                parameter (the callee could store null through the reference).
        """
        if self.writes_const(arg_expr):
            raise LangError(
                f"cannot pass a const parameter as {what}; it is read-only",
                line,
            )
        if isinstance(arg_expr, Var) and arg_expr.name in self.nonnull_locals:
            raise LangError(
                f"cannot pass a @nonnull parameter as {what}; "
                "null could be stored through the reference",
                line,
            )
        if t.const:
            raise LangError(
                f"cannot pass a read-only {t} as {what}", line
            )
        if volatile:
            raise LangError(
                f"cannot pass @volatile storage as {what}; accesses "
                "through the reference would not be volatile",
                line,
            )
        if align is not None and align < type_align(t):
            raise LangError(
                f"cannot pass a @packed field as {what}; its "
                "alignment is not guaranteed",
                line,
            )
        # Lending the storage as mut lets the callee store null through the
        # reference: any flow-narrowed fact for the name dies here, with
        # every projection fact rooted at it. This point kill is load-bearing
        # for the lent base: a mut callee writes through the hidden
        # reference, so its write-effect bit is set and the call kills all
        # path facts anyway today -- but the lend itself is the mut hand-off,
        # and the point invalidation must not depend on the blanket kill
        # staying blanket. The point invalidation is sound because & of a mut
        # parameter is banned, so the callee cannot leak the address past the
        # call (re-lending it as a further mut argument is bounded by the
        # call the same way; a function value captures a function's address,
        # never a lent argument's).
        if isinstance(arg_expr, Var):
            self.narrowed_nonnull.discard(arg_expr.name)
            self.kill_paths_rooted(arg_expr.name)

    def mut_ref_arg(
        self, arg_expr, ptype: LangType, line: int, context: str,
        receiver: bool = False,
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
            receiver: Whether this is a method-family call's receiver, which
                may upcast: a derived lvalue lends its base prefix (the same
                storage, viewed as the declared base).

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
                if receiver:
                    target = self.receiver_upcast_target(t, ptype)
                    if target is not None:
                        return self.builder.bitcast(
                            addr, target.ir.as_pointer()
                        )
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
            if tv.lvalue is not None:
                # A mut-returning call re-lends: the callee's formation rule
                # vouched for the storage (and rejected const/@volatile/
                # @packed at the return site), so no caller-side storage
                # re-check -- just the exact-type rule every mut reference
                # obeys, plus the receiver upcast (the base prefix re-lends).
                if tv.type != ptype:
                    if receiver:
                        target = self.receiver_upcast_target(tv.type, ptype)
                        if target is not None:
                            return self.builder.bitcast(
                                tv.lvalue, target.ir.as_pointer()
                            )
                    raise LangError(
                        f"{context}: expected a {ptype} lvalue, got {tv.type}",
                        line,
                    )
                return tv.lvalue
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

    def dealias_pattern(self, pattern: TypeRef, type_params: list[str]) -> TypeRef:
        """Expand a generic-alias application at a parameter pattern's head.

        A pattern spelled through a generic alias -- ``diag<U>`` with
        ``type diag<T> = pair<T, T>`` -- unifies and shape-checks as the type
        it names: the written arguments bind the alias's parameters (a
        shorter list fills from trailing defaults) and substitute through its
        target, so ``diag<U>`` matches ``pair<int32, int32>`` binding
        ``U = int32`` -- and the repeated position rejects a
        ``pair<int32, float64>`` receiver, the diagonal constraint. Pointer
        depth, array dimensions, and ``const`` written on the alias spelling
        carry onto the expansion; chasing stops at a name that is a type
        parameter, a non-alias, a plain (non-generic) alias, an arity
        mismatch (the declaration's own resolution reports those), or a
        cycle.

        Args:
            pattern: The parameter's ``TypeRef`` pattern.
            type_params: The function's type-parameter names.

        Returns:
            The expanded pattern, or ``pattern`` unchanged when its head is
            not a generic-alias application.
        """
        seen: set[str] = set()
        while (
            pattern.args
            and pattern.params is None
            and pattern.name not in type_params
            and pattern.name not in seen
            and (alias := self.lookup_alias(pattern.name)) is not None
            and alias.type_params
            and (
                len(alias.type_params) - len(alias.type_param_defaults)
                <= len(pattern.args)
                <= len(alias.type_params)
            )
        ):
            seen.add(pattern.name)
            binding = dict(zip(alias.type_params, pattern.args))
            for pname in alias.type_params[len(pattern.args):]:
                binding[pname] = self.subst_struct_args(
                    alias.type_param_defaults[pname], binding
                )
            target = self.subst_struct_args(alias.target, binding)
            pattern = dataclasses_replace(
                target,
                stars=target.stars + pattern.stars,
                dims=target.dims + pattern.dims,
                const=target.const or pattern.const,
            )
        return pattern

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
        # A generic-alias spelling unifies as the type it names:
        # `self: diag<U>` binds U from a pair<int32, int32> receiver.
        pattern = self.dealias_pattern(pattern, type_params)
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
        # An f-string can only ever bind an @format format-string slot, so a
        # candidate that would receive one anywhere else is non-viable before
        # ranking -- panic(f"x = {x}") must resolve to the @format collector
        # even though the msg-only member would win the plain-literal rank.
        # An f-string among a viable candidate's *extras* is left alone here:
        # the winner's takes-no-arguments-after-an-f-string rejection (or the
        # collected-extra funnel) reports it against the resolved callee.
        if any(isinstance(arg, FStrLit) for arg in expr.args):

            def receives_fstrings(f: Func) -> bool:
                if not self.collecting_candidate(f):
                    return False
                fixed = len(f.params) - 1
                if f.params[fixed - 1][0] not in f.format_params:
                    return False
                return all(
                    i == fixed - 1
                    for i, arg in enumerate(expr.args[:fixed])
                    if isinstance(arg, FStrLit)
                )

            candidates = [f for f in candidates if receives_fstrings(f)]
            if not candidates:
                raise LangError(FSTRING_MISPLACED, expr.line)
        # A mut argument lends the caller's storage, so its address must be
        # formed before the args are lowered to values for inference -- but
        # which overload wins (and with it which positions are mut) is not
        # known yet. Form the address wherever ANY arity-matching candidate
        # is mut and the argument denotes storage, and defer the
        # lvalue/value decision until after overload resolution. Arity
        # matching is collecting-aware: a collecting candidate takes any
        # count from its fixed prefix up, and its mut positions are all
        # fixed ones (a mut trailing slice is never a marker).
        matching = [
            f
            for f in candidates
            if (
                len(expr.args) >= len(f.params) - 1
                if self.collecting_candidate(f)
                else len(f.params) == len(expr.args)
            )
        ]
        maybe_mut = frozenset().union(*(self.mut_indices(f) for f in matching))
        arg_tvs, addrs = [], {}
        # Non-null proofs are judged per argument as it is evaluated, not at
        # the post-resolution check sites: a nested call inside a *later*
        # argument blanket-kills the path facts, but an earlier projection
        # argument's load already happened under its guard -- so
        # f(a->ptr, g()) is fine while f(g(), a->ptr) rightly fails (the
        # direct path gets the same semantics from marshal_args checking
        # interleaved with lowering).
        proofs: list[bool] = []
        for i, arg in enumerate(expr.args):
            if isinstance(arg, _CtorSelf):
                # Constructor sugar's untyped receiver: no value exists yet
                # (the slot's type is exactly what resolution is about to
                # decide), so a placeholder that binds no type parameter and
                # matches any receiver pattern stands in; the winner's first
                # parameter materializes it below.
                proofs.append(False)
                arg_tvs.append(TypedValue(None, CTOR_SELF))
                continue
            proofs.append(self.proves_nonnull(arg))
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
            elif isinstance(arg, FStrLit):
                # An f-string pre-evaluates for inference and ranking exactly
                # as its desugared literal would -- a char* into the interned
                # text -- so among the @format collectors the viability filter
                # above kept, the winner is the one the equivalent
                # plain-literal call would pick. The hole expressions stay
                # unevaluated here; winner emission substitutes the plain
                # literal and splices them in as the extras.
                arg_tvs.append(self.gen_string(arg.value))
            elif (
                self.defers_array_literal(arg)
                or self.defers_struct_literal(arg)
                or isinstance(arg, ResultLit)
            ):
                # An array or bare struct literal (or an ok()/error()
                # constructor) cannot lower without a receiving type, and the
                # winning parameter is not yet chosen.
                # Stand it in as a NULLT placeholder: the inference loop skips
                # NULLT (so a literal contributes nothing to generic inference --
                # a bare struct literal can never itself bind a type parameter),
                # and emission builds/borrows it against the resolved parameter
                # (see literal_adapts_to_pattern and the winner-emission arm) --
                # exactly as a string literal pre-evaluates to a char*.
                arg_tvs.append(TypedValue(ir.Constant(RAWPTR.ir, None), NULLT))
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
                # A collecting candidate's trailing const position holds an
                # extra (or nothing), never a decayable fixed argument.
                fixed = len(func.params) - (
                    1 if self.collecting_candidate(func) else 0
                )
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
                    if i < fixed
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
            viable, mut_failures, group_failures, bound_failures = [], [], [], []
            for func in candidates:
                bindings = self.resolve_bindings(func, expr, arg_tvs, lenient=True)
                if bindings is None:
                    continue
                # The post-deduction group filter: a candidate whose group
                # excludes the deduced binding is simply not viable -- this
                # is what partitions same-pattern disjoint-group templates.
                violation = self.group_violation(func, bindings)
                if violation is not None:
                    group_failures.append(violation)
                    continue
                # The nominal `extends` bound filter is the open-set sibling:
                # a candidate whose bound the deduced binding does not satisfy
                # is likewise not viable, leaving an unbounded fallback to win.
                bviolation = self.bound_violation(func, bindings)
                if bviolation is not None:
                    bound_failures.append(bviolation)
                    continue
                # A mut-returning call argument formed no address up front
                # (it is not an addressable form), but its carried lvalue
                # re-lends, so it keeps mut candidates viable.
                unaddressed = [
                    i
                    for i in self.mut_indices(func)
                    if i not in addrs
                    and arg_tvs[i].lvalue is None
                    # A constructor-sugar receiver is storage by construction
                    # (the slot materializes once the winner is known), so it
                    # never disqualifies a mut candidate.
                    and arg_tvs[i].type is not CTOR_SELF
                ]
                if unaddressed:
                    mut_failures.append(min(unaddressed))
                    continue
                viable.append((self.call_rank(func, arg_tvs), func, bindings))
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
                if len(group_failures) == 1 and not mut_failures:
                    # Exactly one candidate matched but for the group filter:
                    # report the deduced type against the group, not a
                    # generic shape mismatch.
                    bound, group = group_failures[0]
                    raise LangError(
                        self.group_error(expr.name, bound, group), expr.line
                    )
                if (
                    len(bound_failures) == 1
                    and not mut_failures
                    and not group_failures
                ):
                    # Exactly one candidate matched but for the bound filter:
                    # report the deduced type against the bound, not a generic
                    # shape mismatch.
                    offender, target = bound_failures[0]
                    raise LangError(
                        self.bound_error(expr.name, offender, target), expr.line
                    )
                arg_types = ", ".join(str(tv.type) for tv in arg_tvs)
                raise LangError(
                    f"no overload of {expr.name!r} with signature "
                    f"{expr.name}({arg_types})",
                    expr.line,
                )
            viable.sort(key=lambda entry: entry[0], reverse=True)
            if len(viable) > 1 and viable[0][0] == viable[1][0]:
                # A rank tie is not yet an ambiguity: among the tied cohort,
                # a candidate whose parameter pattern is strictly an instance
                # of every other's (and whose constraints imply theirs) is
                # the more specialized declaration and wins -- see
                # :meth:`subsumption_winner`. Only a cohort with no such
                # maximum stays ambiguous.
                top = viable[0][0]
                cohort = [f for key, f, _ in viable if key == top]
                winner = self.subsumption_winner(cohort)
                if winner is None:
                    # Open sets make cross-module ties possible, so cite the
                    # contenders' declaration sites.
                    err = LangError(
                        f"call to {expr.name!r} is ambiguous between overloads",
                        expr.line,
                    )
                    for rank_key, contender, _ in viable:
                        if rank_key != top:
                            break
                        # An inherited contender's note points at the ORIGIN
                        # declaration, naming the base it was inherited from.
                        contender_inh = self.inherited_origins.get(
                            id(contender)
                        )
                        label = (
                            "candidate is here (inherited from "
                            f"{contender_inh.base_label})"
                            if contender_inh is not None
                            else "candidate is here"
                        )
                        err.notes.append(
                            Note(
                                label,
                                contender.line,
                                contender.source,
                            )
                        )
                    raise err
                _, func, bindings = next(
                    entry for entry in viable if entry[1] is winner
                )
            else:
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
        inh = self.inherited_origins.get(id(func))
        if inh is not None:
            # An inherited clone is never emitted: its ORIGIN instantiates
            # (or looks up) instead -- one shared instance cache and symbol,
            # no per-derived-type code -- with the seed bindings the
            # `extends` clause fixed plus whatever the call inferred for the
            # leftover parameters. The derived receiver then coerces to the
            # base parameter at the boundary below.
            obindings = self.origin_bindings(inh, bindings, expr.line)
            if inh.origin.type_params:
                fn, ret, params = self.instantiate(
                    inh.origin, obindings, expr.line
                )
            else:
                symbol = self.overload_symbols.get(
                    id(inh.origin), inh.origin.name
                )
                fn = self.funcs[symbol]
                ret, params, _ = self.signatures[symbol]
        elif func.type_params:
            fn, ret, params = self.instantiate(func, bindings, expr.line)
        else:
            # A mangled set member, or a mixed set's single plain concrete
            # (which kept its plain symbol).
            symbol = self.overload_symbols.get(id(func), func.name)
            fn = self.funcs[symbol]
            ret, params, _ = self.signatures[symbol]
        if expr.args and isinstance(expr.args[0], _CtorSelf):
            # Constructor sugar's receiver: the winner is known, so the
            # instantiation is too -- its first parameter is the constructed
            # type. Materialize the slot the placeholder stood for and
            # rewrite the argument into the desugared call's ordinary hidden
            # receiver, so the deferred mut-legality checks and emission see
            # exactly what `S::constructor(s, args)` would.
            marker = expr.args[0]
            recv = strip_const(params[0]) if params else None
            if inh is not None and func.params:
                # An inherited constructor's origin receiver is the BASE
                # type; the constructed slot must be the DERIVED one the
                # clone's own receiver pattern spells (its boundary upcast
                # then lends the slot's base prefix to the origin).
                derived_params = self.try_param_types(func, bindings)
                recv = (
                    strip_const(derived_params[0]) if derived_params else None
                )
            if recv is None or not (
                recv.template == marker.struct_name
                or recv.name == marker.struct_name
            ):
                raise LangError(
                    f"cannot construct {marker.struct_name!r}: the resolved "
                    f"'fn {expr.name}' does not take the constructed "
                    f"{marker.struct_name} as its first parameter",
                    expr.line,
                )
            slot = self.builder.alloca(recv.ir)
            if over_aligned(recv):
                slot.align = type_align(recv)
            if is_struct(recv):
                self.init_struct_defaults(slot, recv)
            marker.slot, marker.type = slot, recv
            # The rewritten argument is never evaluated (fixed arguments
            # emit from the pre-evaluated values and formed addresses); the
            # Var only serves the syntactic post-resolution checks.
            expr.args[0] = Var(f"0ctor{self.hidden_seq}", expr.line)
            self.hidden_seq += 1
            addrs[0] = (slot, recv, None, False)
            arg_tvs[0] = self.value_at(slot, recv)
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
                        expr.args[i], strip_const(t), "mut", p, context,
                        expr.line, proven=proofs[i],
                    )
                    decayed.add(i)
                    continue
                self.check_mut_storage(expr.args[i], t, align, volatile, expr.line)
                if t != p:
                    target = (
                        self.receiver_upcast_target(t, p)
                        if i == 0 and "::" in expr.name
                        else None
                    )
                    if target is None:
                        raise LangError(
                            f"{context}: expected a {p} lvalue, got {t}",
                            expr.line,
                        )
                    # A derived receiver lends its base prefix: the same
                    # storage, viewed as the base (the layout guarantee
                    # `extends` makes), so the callee's writes land in the
                    # derived value's leading fields.
                    addrs[i] = (
                        self.builder.bitcast(
                            addrs[i][0], target.ir.as_pointer()
                        ),
                        t, align, volatile,
                    )
                continue
            # No address was formed (an rvalue): only a proven-non-null
            # pointer to the parameter's type may decay in, or a
            # mut-returning call may re-lend its carried lvalue. A string
            # literal's bytes live in a constant global, so it never does.
            tv = arg_tvs[i]
            if self.decays_to(tv.type, p, mut=True) and not isinstance(
                expr.args[i], StrLit
            ):
                self.check_decay_arg(
                    expr.args[i], tv.type, "mut", p, context, expr.line,
                    proven=proofs[i],
                )
                decayed.add(i)
                continue
            if tv.lvalue is not None:
                # A mut-return re-lend: the callee's formation rule vouched
                # for the storage, so only the exact-type rule applies (as
                # on the direct path, see mut_ref_arg) -- plus the receiver
                # upcast, which re-lends the storage's base prefix.
                if tv.type != p:
                    target = (
                        self.receiver_upcast_target(tv.type, p)
                        if i == 0 and "::" in expr.name
                        else None
                    )
                    if target is None:
                        raise LangError(
                            f"{context}: expected a {p} lvalue, got {tv.type}",
                            expr.line,
                        )
                    addrs[i] = (
                        self.builder.bitcast(
                            tv.lvalue, target.ir.as_pointer()
                        ),
                        tv.type, None, False,
                    )
                    continue
                addrs[i] = (tv.lvalue, tv.type, None, False)
                continue
            raise LangError(self.not_assignable(i, expr.name), expr.line)
        # The proof is syntactic, so it runs on the argument expressions even
        # though they were already lowered to values for binding inference --
        # against the facts recorded when each argument was evaluated.
        # A template is never @extern (externs are non-generic direct
        # symbols), so this fork stays relaxed-free; threaded for parity.
        extern = expr.name in self.extern_decls
        # The winner's collection decision mirrors resolution's
        # (collecting_candidate: the syntactic marker for a template, the
        # declared signature for a concrete member): the fixed prefix
        # marshals through the loop below, the extras form the trailing
        # slice after it -- in parity with marshal_args' head/tail split.
        collecting = self.collecting_candidate(func)
        n_fixed = len(params) - 1 if collecting else len(params)
        # An @format literal desugars its positional placeholders, in parity
        # with marshal_args: the rewritten literal stands in locally (never
        # an AST rewrite) and lowers through the mirrored literal-adaptation
        # arm below; the slot map reorders the already-evaluated extras. (The
        # original literal's char* pre-evaluation for inference goes unused.)
        fmt_lit = slot_map = fstr = None
        if (
            collecting
            and n_fixed >= 1
            and func.params[n_fixed - 1][0] in func.format_params
        ):
            fmt = expr.args[n_fixed - 1]
            if isinstance(fmt, FStrLit):
                # An f-string: the parse-time-desugared text stands in and
                # the hole expressions become the extras (collected below,
                # in parity with marshal_args' branch).
                if len(expr.args) > n_fixed:
                    raise LangError(
                        f"{expr.name!r} takes no arguments after an "
                        "f-string: the placeholders already supply them",
                        expr.line,
                    )
                fmt_lit = StrLit(fmt.value, fmt.line)
                fstr = fmt
            elif isinstance(fmt, StrLit):
                scanned = self.scan_positional(
                    fmt.value, len(expr.args) - n_fixed, n_fixed,
                    repr(expr.name), fmt.line,
                )
                if scanned is not None:
                    new_text, slot_map = scanned
                    fmt_lit = StrLit(new_text, fmt.line)
        for i in self.nonnull_indices(func):
            if i < len(expr.args) and i < n_fixed:
                self.check_nonnull_arg(
                    expr.args[i], f"argument {i + 1} of {expr.name!r}",
                    expr.line, proven=proofs[i], extern=extern,
                )
        hidden = self.hidden_ref_indices(func, params)
        args = []
        for i, (tv, p) in enumerate(zip(arg_tvs[:n_fixed], params)):
            context = f"argument {i + 1} of {expr.name!r}"
            # The desugared @format literal stands in for the original at
            # its position; every other argument is its own AST node.
            arg_node = (
                fmt_lit
                if fmt_lit is not None and i == n_fixed - 1
                else expr.args[i]
            )
            if i in mut_positions:
                # A decayed pointer is forwarded by value (it was already
                # loaded, once, when the argument was evaluated); a direct
                # lend passes the caller's storage address.
                args.append(tv.value if i in decayed else addrs[i][0])
            elif (
                self.struct_literal_adapts(arg_node, p)
                and not isinstance(arg_node, TupleLit)
                or self.str_literal_adapts(arg_node, p)
                or self.array_literal_adapts(arg_node, p)
                or self.result_literal_adapts(arg_node, p)
            ):
                # Literal adaptation, in parity with marshal_args: a string
                # literal borrows to the parameter's char slice view, an array
                # literal to its slice<T> view (each a ternary of them too), and
                # a bare struct literal builds the parameter's struct; a const
                # slice/struct parameter travels by hidden reference, so the
                # adapted value spills to a temporary first. (The char* / NULLT
                # placeholder the literal pre-evaluated to for inference goes
                # unused.) A tuple literal is NOT re-adapted here: unlike a
                # bare struct literal it anchors its own type, so it lowered
                # eagerly for inference, and rebuilding it would run its
                # element side effects twice -- the eager value flows to the
                # coerce below instead.
                tv = self.gen_adapted_literal(arg_node, p, expr.line)
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
                        arg_node, strip_const(tv.type), "const", p,
                        context, expr.line, proven=proofs[i],
                    )
                    args.append(tv.value)
                else:
                    target = (
                        self.receiver_upcast_target(tv.type, p)
                        if i == 0 and "::" in expr.name
                        else None
                    )
                    if target is not None:
                        # A derived receiver borrows as the base prefix.
                        args.append(self.upcast_hidden_ref(tv, target))
                    else:
                        args.append(
                            self.spill_to_temp(tv, p, expr.line, context)
                        )
            else:
                if i == 0 and "::" in expr.name:
                    # A by-value receiver prefix-copies into the base
                    # parameter (honest data slicing, as the `as` upcast).
                    target = self.receiver_upcast_target(tv.type, p)
                    if target is not None:
                        tv = self.upcast_struct_value(tv, target)
                args.append(self.coerce(tv, p, expr.line, context).value)
        if collecting:
            # Collection is emitted from the already-evaluated values (no
            # boxing happened before the winner was known); only a deferred
            # array/bare-struct literal extra was never evaluated, and
            # re-generating it raises the direct path's exact
            # receiver-less-literal error.
            if fstr is not None:
                # The f-string's hole expressions are the collected extras:
                # invisible to pre-evaluation and ranking, they evaluate here
                # -- once each, in source order (the deferred-literal
                # precedent below).
                extras = [self.gen_expr(h.expr) for h in fstr.holes]
                extra_exprs = [h.expr for h in fstr.holes]
            else:
                extras = []
                for j in range(n_fixed, len(arg_tvs)):
                    raw_arg = expr.args[j]
                    if isinstance(raw_arg, FStrLit):
                        # An f-string may only ever be the @format format
                        # string, never a trailing collected argument. With a
                        # plain literal in the format slot (fstr is None here)
                        # the pre-evaluation lowered this f-string to its
                        # char* for ranking, so it slipped past the viability
                        # filter's fixed-prefix guard; reject it now against
                        # the resolved callee, in parity with the direct
                        # path's gen_expr(FStrLit) rejection.
                        raise LangError(FSTRING_MISPLACED, raw_arg.line)
                    if self.defers_array_literal(
                        raw_arg
                    ) or self.defers_struct_literal(raw_arg):
                        extras.append(self.gen_expr(raw_arg))
                    else:
                        extras.append(arg_tvs[j])
                extra_exprs = expr.args[n_fixed:]
            if slot_map is not None:
                # Positional desugar: map the once-evaluated extras by slot
                # -- a duplicated slot re-boxes the same TypedValue, never
                # re-evaluates its expression (in parity with marshal_args).
                extras = [extras[s] for s in slot_map]
                extra_exprs = [extra_exprs[s] for s in slot_map]
            args.append(
                self.collect_from_values(
                    extras,
                    extra_exprs,
                    params[-1],
                    n_fixed in hidden,
                    repr(expr.name),
                    expr.line,
                )
            )
        effect_owner = inh.origin if inh is not None else func
        raw = self.emit_call(
            fn, args, preserves=self.effect_bits.get(id(effect_owner)) is False
        )
        if func.mut_return:
            # As in gen_direct_call: the eager load serves value contexts,
            # the carried address serves the lvalue surfaces.
            result = TypedValue(self.gen_load(raw), ret, lvalue=raw)
        else:
            result = TypedValue(raw, ret)
        if func.noreturn:
            # The resolved candidate (generic instance or concrete set
            # member) never returns: terminate the block, exactly as in
            # gen_direct_call.
            self.builder.unreachable()
        return result

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
            if self.group_violation(func, bindings) is not None:
                continue  # the decayed deduction is outside the group
            if self.bound_violation(func, bindings) is not None:
                continue  # the decayed deduction does not satisfy the bound
            params = self.try_param_types(func, bindings)
            if params is None:
                continue
            ok = True
            for i in self.mut_indices(func):
                if arg_tvs[i].type is CTOR_SELF:
                    continue  # a constructor-sugar receiver: storage by construction
                if i in addrs and addrs[i][1] == params[i]:
                    continue  # a direct lend of the caller's storage
                if (
                    arg_tvs[i].lvalue is not None
                    and arg_tvs[i].type == params[i]
                ):
                    continue  # a mut-return re-lend of the exact type
                if self.decays_to(
                    arg_tvs[i].type, params[i], mut=True
                ) and not isinstance(expr.args[i], StrLit):
                    continue
                ok = False
                break
            if ok:
                viable.append((self.call_rank(func, arg_tvs), func, bindings))
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
        # A collecting candidate is viable from its fixed count up, and its
        # trailing slice<const any> never unifies or shape-checks: every zip
        # below is sliced to the fixed prefix, so an extra is never paired
        # against the slice pattern (and type parameters bind from the fixed
        # arguments only).
        n_fixed = len(func.params)
        if self.collecting_candidate(func):
            n_fixed -= 1
            if len(expr.args) < n_fixed:
                if lenient:
                    return None
                raise LangError(
                    f"{expr.name!r} expects at least {n_fixed} argument(s), "
                    f"got {len(expr.args)}",
                    expr.line,
                )
        elif len(expr.args) != len(func.params):
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
                if i >= n_fixed:
                    # The collecting position is const, but its actual is an
                    # extra (or absent); extras never decay.
                    continue
                bare = strip_const(actuals[i])
                if actuals[i] is not NULLT and bare.pointee is not None:
                    actuals[i] = bare.pointee
        # A method-family call's receiver upcasts: inference and the shape
        # filter see it as the base instantiation the first parameter's
        # pattern names, when its declared `extends` lineage reaches one.
        if "::" in expr.name and actuals and n_fixed >= 1:
            actuals[0] = self.receiver_view(
                func.params[0][1], actuals[0], func.type_params
            )
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
                for (_, ptype), tv, actual in zip(
                    func.params[:n_fixed], arg_tvs, actuals
                ):
                    # A constructor-sugar receiver placeholder carries no
                    # type: like null, it never binds a parameter (the
                    # winner's own receiver pattern types the slot after
                    # resolution).
                    if tv.type is CTOR_SELF:
                        continue
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
            if expr.args and isinstance(expr.args[0], _CtorSelf):
                # Constructor sugar: the fix is spelling the instantiation
                # at the sugar head, not at the (unwritable) family call.
                raise LangError(
                    f"cannot infer type parameter(s) {', '.join(missing)} "
                    f"for {expr.name!r}; spell the instantiation, e.g. "
                    f"{expr.args[0].struct_name}<int32>(...)",
                    expr.line,
                )
            raise LangError(
                f"cannot infer type parameter(s) {', '.join(missing)} for {expr.name!r}; "
                f"specify them explicitly, e.g. {expr.name}<int32>(...)",
                expr.line,
            )
        if lenient:
            for (_, ptype), tv, actual, arg in zip(
                func.params[:n_fixed], arg_tvs, actuals, expr.args
            ):
                if actual is CTOR_SELF:
                    # The receiver placeholder matches any receiver pattern;
                    # the winner's own first parameter types the slot.
                    continue
                if self.shape_matches(
                    ptype, actual, tv.adaptable, func.type_params, expr.line
                ):
                    if tv.adaptable:
                        # An adaptable integer constant at a bare
                        # type-parameter slot is emittable only when the
                        # deduced binding is an integer type -- the generic
                        # mirror of shape_matches' concrete `is_integer`
                        # rule (mcc has no int-to-float literal adaptation).
                        # Without this, a diagonal `f(x: T, y: T)` whose T
                        # deduced float64 from another argument would
                        # "match" an int literal it can never emit,
                        # manufacturing phantom ties.
                        p = self.dealias_pattern(ptype, func.type_params)
                        if (
                            not p.stars
                            and not p.args
                            and p.name in func.type_params
                        ):
                            bound = bindings.get(p.name)
                            if bound is not None and not is_integer(
                                strip_const(bound)
                            ):
                                return None
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
        """Whether a literal argument adapts to a candidate's parameter.

        The pre-evaluate path's parity with :meth:`marshal_args`' literal
        handling: a string literal (or a ternary of literals) stays viable
        against a parameter that resolves to a ``slice<char>`` (or
        ``slice<const char>``) under the candidate's bindings, even though
        the ``char*`` it evaluated to does not match the slice shape; an array
        literal likewise stays viable against a parameter that resolves to any
        ``slice<T>`` (it pre-evaluated to a NULLT placeholder that matches no
        shape). Emission then borrows the literal (see
        :meth:`gen_borrow_slice`).

        Args:
            arg: The raw argument expression.
            func: The candidate function (its source scopes the resolution).
            ptype: The parameter's ``TypeRef`` pattern.
            bindings: The candidate's complete type-parameter bindings.
            line: Source line for diagnostics.

        Returns:
            ``True`` when the argument adapts to the resolved parameter.
        """
        if not isinstance(arg, (StrLit, ArrayLit, StructLit, Ternary, ResultLit)):
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
        return (
            self.str_literal_adapts(arg, resolved)
            or self.array_literal_adapts(arg, resolved)
            or self.struct_literal_fits(arg, resolved)
            or self.result_literal_adapts(arg, resolved)
        )

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
        # A generic-alias spelling shape-checks as the type it names, in
        # step with unify's expansion.
        pattern = self.dealias_pattern(pattern, type_params)
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
        if strip_const(peeled) == resolved and (pattern.const or not peeled.const):
            return True
        if adaptable and is_integer(resolved) and is_integer(peeled):
            return True
        return resolved == RAWPTR and is_pointer(peeled)

    def subsumes(self, a: Func, b: Func) -> bool:
        """Whether ``a`` is at least as specialized as ``b`` (``a`` ⊑ ``b``).

        The subsumption relation over template declarations: ``a``'s
        parameter pattern must be an *instance* of ``b``'s, and ``a``'s
        type-parameter constraints must *imply* ``b``'s
        (:meth:`constraints_imply`). The pattern half is a one-way match on
        the ``dealias_pattern``-normalized patterns: ``b``'s type parameters
        are wildcards binding **consistently** to sub-patterns of ``a`` --
        a repeated name must bind the same sub-pattern every time, which is
        what makes a diagonal ``f(x: T, y: T)`` an instance of the open
        ``f(x: T, y: U)`` -- while ``a``'s own parameters are opaque
        constants. A wildcard absorbs surplus pointer stars (``T`` matches
        ``int32*``, binding ``T := int32*``); concrete names need the exact
        name and equal stars; array dimensions must spell equally
        (conservative); a function-pointer pattern matches only its exact
        spelling (v1 -- differently-spelled fn types are incomparable, which
        conservatively keeps the ambiguity). ``const`` markers and return
        types are ignored (same-pattern variants already collide as
        duplicates), while arity, the collecting flag, and the ``mut``
        positions must agree outright (``mut`` markers are template
        identity, see :meth:`template_base`).

        Args:
            a: The candidate proposed as the more specialized.
            b: The candidate proposed as the more general.

        Returns:
            ``True`` when ``a``'s pattern maps into ``b``'s and ``a``'s
            constraints imply ``b``'s.
        """
        if len(a.params) != len(b.params):
            return False
        if self.collecting_candidate(a) != self.collecting_candidate(b):
            return False
        if self.mut_indices(a) != self.mut_indices(b):
            return False
        binding: dict[str, TypeRef] = {}

        def match(bp: TypeRef, ap: TypeRef) -> bool:
            bp = self.dealias_pattern(bp, b.type_params)
            ap = self.dealias_pattern(ap, a.type_params)
            if [str(d) for d in bp.dims] != [str(d) for d in ap.dims]:
                return False
            if bp.params is not None or ap.params is not None:
                return str(bp) == str(ap)
            if bp.name in b.type_params and not bp.args:
                if ap.stars < bp.stars:
                    return False
                sub = dataclasses_replace(
                    ap, stars=ap.stars - bp.stars, const=False
                )
                prior = binding.get(bp.name)
                if prior is None:
                    binding[bp.name] = sub
                    return True
                return str(prior) == str(sub)
            if bp.stars != ap.stars:
                return False
            if bp.name != ap.name or ap.name in a.type_params:
                return False
            if len(bp.args) != len(ap.args):
                return False
            return all(match(x, y) for x, y in zip(bp.args, ap.args))

        if not all(
            match(bt, at) for (_, bt), (_, at) in zip(b.params, a.params)
        ):
            return False
        return self.constraints_imply(a, b, binding)

    def constraints_imply(
        self, a: Func, b: Func, binding: "dict[str, TypeRef]"
    ) -> bool:
        """Whether ``a``'s constraints imply ``b``'s under a pattern mapping.

        The constraint half of :meth:`subsumes`: for every wildcard of ``b``
        that carries a constraint -- a [closed type group] or an ``extends``
        bound; a *default* is a fill-in, never a constraint -- the sub-pattern
        of ``a`` it bound must be guaranteed to satisfy it:

        - A **concrete** sub-pattern must satisfy the constraint directly
          (group membership / the nominal subtype relation, the same tests
          instantiation runs).
        - A bare type parameter of ``a`` must carry a constraint that
          **implies** the wildcard's: type groups by subset
          (``T: int8`` implies ``U: int8 | int16``), ``extends`` bounds by
          the declared nominal chain (``T extends derived`` implies
          ``U extends base`` when ``derived`` extends ``base``,
          transitively). A group never implies a bound nor vice versa
          (incomparable, conservative), and an *unconstrained* parameter
          implies nothing.
        - Any other sub-pattern naming one of ``a``'s type parameters is
          conservatively unprovable.

        An unconstrained wildcard is implied by anything.

        Args:
            a: The proposed more-specialized candidate.
            b: The proposed more-general candidate.
            binding: The wildcard-to-sub-pattern mapping the pattern match
                produced (``b``'s type parameter name -> ``a``'s
                sub-pattern).

        Returns:
            ``True`` when every constrained wildcard's binding provably
            satisfies its constraint.
        """
        b_groups = self.group_types.get(id(b), {})
        b_bounds = self.bound_types.get(id(b), {})
        if not b_groups and not b_bounds:
            return True
        a_groups = self.group_types.get(id(a), {})
        a_bounds = self.bound_types.get(id(a), {})
        for wildcard, sub in binding.items():
            w_group = b_groups.get(wildcard)
            w_bound = b_bounds.get(wildcard)
            if w_group is None and w_bound is None:
                continue
            if (
                sub.params is None
                and not sub.args
                and not sub.stars
                and not sub.dims
                and sub.name in a.type_params
            ):
                t_group = a_groups.get(sub.name)
                t_bound = a_bounds.get(sub.name)
                if w_group is not None and (
                    t_group is None
                    or not all(
                        any(m == n for n in w_group) for m in t_group
                    )
                ):
                    return False
                if w_bound is not None and (
                    t_bound is None
                    or not self.nominal_subtype(w_bound, t_bound)
                ):
                    return False
                continue
            if self.names_type_param(sub, a.type_params):
                return False
            resolved = self.resolve_concrete_pattern(sub, a)
            if resolved is None:
                return False
            bare = strip_const(resolved)
            if w_group is not None and all(
                m != resolved and m != bare for m in w_group
            ):
                return False
            if w_bound is not None and not self.nominal_subtype(
                w_bound, bare
            ):
                return False
        return True

    @staticmethod
    def names_type_param(ref: TypeRef, type_params: list[str]) -> bool:
        """Whether ``ref`` names one of ``type_params``, at any depth."""
        if ref.name in type_params:
            return True
        if any(
            CodeGen.names_type_param(arg, type_params) for arg in ref.args
        ):
            return True
        if ref.params is not None and any(
            CodeGen.names_type_param(p, type_params) for p in ref.params
        ):
            return True
        return ref.ret is not None and CodeGen.names_type_param(
            ref.ret, type_params
        )

    def resolve_concrete_pattern(
        self, ref: TypeRef, func: Func
    ) -> "LangType | None":
        """Resolve a concrete sub-pattern in its declaring template's scope.

        Used by :meth:`constraints_imply` to test a concrete binding against
        a wildcard's constraint. The pattern comes from ``func``'s
        declaration, so it resolves under ``func``'s source (it may name that
        file's private types) with no live type bindings.

        Args:
            ref: The sub-pattern (contains no type parameters of ``func``).
            func: The declaring template.

        Returns:
            The resolved type, or ``None`` when it does not resolve
            (conservatively unprovable).
        """
        outer_bindings = self.type_bindings
        outer_source = self.current_source
        self.type_bindings = {}
        self.current_source = func.source
        try:
            return self.lang_type(ref, func.line)
        except LangError:
            return None
        finally:
            self.type_bindings = outer_bindings
            self.current_source = outer_source

    def subsumption_winner(self, cohort: "list[Func]") -> "Func | None":
        """The rank-tied cohort's unique maximum under subsumption, if any.

        The tie-break that runs *inside* one rank-tied cohort only -- tiers
        and specificity stay supreme, so a bounded template still beats an
        unbounded one outright and no cross-tier comparison happens here.
        The winner must **strictly** subsume into every other member
        (:meth:`subsumes` one way and not the other); two members that
        mutually subsume, or that are incomparable, leave no maximum and the
        call stays the ambiguity error. Two distinct maxima are impossible:
        they would strictly subsume each other, and alpha-equivalent
        patterns already collide at declaration (:meth:`template_base`).

        Args:
            cohort: The rank-tied top candidates.

        Returns:
            The unique most-specialized member, or ``None``.
        """

        def strictly(x: Func, y: Func) -> bool:
            return self.subsumes(x, y) and not self.subsumes(y, x)

        winners = [
            f
            for f in cohort
            if all(strictly(f, g) for g in cohort if g is not f)
        ]
        return winners[0] if len(winners) == 1 else None

    def call_rank(
        self, func: Func, arg_tvs: list[TypedValue]
    ) -> tuple[int, int, int, int, int]:
        """A viable candidate's per-call sort key.

        ``(no-collect, tier, -hop, specificity, fixed count)``: a candidate
        that matches this call without collecting beats any candidate that
        must collect, as the outermost component regardless of tier -- an
        exact-arity generic beats a concrete collecting fallback (the C++
        ellipsis-ranks-worst analogue). A pass-through-shaped match counts
        as not-collecting at full specificity (see :meth:`passes_through`).
        The hop -- an inherited member's distance up the ``extends`` chain,
        0 for a member declared on the receiver type itself -- sits below
        the tier and above specificity: a derived same-shape member shadows
        an inherited one, while a base member of a better tier (an inherited
        exact/concrete match) still beats a derived generic. A collecting
        match scores specificity over its fixed prefix only -- the trailing
        ``slice<const any>`` pattern's own points must never arbitrate
        against a competing prefix -- and more fixed parameters break the
        remaining tie; equal fixed counts with a tying fixed-prefix
        specificity stay the ambiguity error. Non-collecting matches all
        share this call's arity, so their trailing count is inert and every
        pre-collection ordering is preserved.

        Args:
            func: The viable candidate.
            arg_tvs: The already-evaluated argument values.

        Returns:
            The sort key, greater meaning preferred.
        """
        tier, spec = self.rank(func)
        inh = self.inherited_origins.get(id(func))
        hop = inh.hop if inh is not None else 0
        if self.collecting_candidate(func) and not self.passes_through(
            func, arg_tvs
        ):
            fixed = len(func.params) - 1
            return (0, tier, -hop, self.specificity(func, fixed), fixed)
        return (1, tier, -hop, spec, len(func.params))

    def passes_through(self, func: Func, arg_tvs: list[TypedValue]) -> bool:
        """Whether a call to a collecting candidate is pass-through-shaped.

        Exact arity with the final argument already exactly ``slice<const
        any>`` (or ``slice<any>``, which widens): the slice hands over
        uncollected (see :meth:`collect_variadic_args`, which keeps the
        detection at emission), so for ranking the match counts as
        not-collecting at full specificity.

        Args:
            func: The collecting candidate.
            arg_tvs: The already-evaluated argument values.

        Returns:
            ``True`` for the pass-through shape.
        """
        if len(arg_tvs) != len(func.params):
            return False
        last = strip_const(arg_tvs[-1].type)
        return is_slice(last) and is_any(last.args[0])

    def rank(self, func: Func) -> tuple[int, int]:
        """An overload candidate's sort key: (tier, specificity).

        Three tiers: a concrete signature (2) beats a **bounded** generic --
        one with a closed type group or a nominal ``extends`` bound (1) --
        which beats an unbounded generic (0). The concrete tier makes "a fully
        concrete signature is maximally specific" exactly true: without it, a
        generic whose effective parameter list is all-concrete (its type
        parameter appearing only in the return type, or filled by a declared
        default) would tie an identical concrete overload under the
        pattern-specificity score. The bounded tier extends the same idea a
        step down: a group or a bound is a written commitment to a type set, so
        it beats the fully open pattern (a bounded candidate whose constraint
        excludes the deduced type was already filtered out as non-viable).
        Same-tier candidates fall back to :meth:`specificity`, and an equal
        rank stays the ambiguity error.

        Args:
            func: The candidate function.

        Returns:
            The sort key, greater meaning preferred.
        """
        if not func.type_params:
            tier = 2
        elif func.type_param_groups or func.type_param_bounds:
            tier = 1
        else:
            tier = 0
        return (tier, self.specificity(func))

    def specificity(self, func: Func, count: int | None = None) -> int:
        """Rank an overload by how specific its parameter patterns are.

        Concrete types beat structured patterns, which beat bare type
        parameters; pointer depth adds specificity.

        Args:
            func: The candidate function.
            count: When given, score only the first ``count`` parameters
                (a collecting candidate's fixed prefix).

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

        params = func.params if count is None else func.params[:count]
        return sum(score(p) for _, p in params)

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

        Raises:
            LangError: When a binding falls outside the parameter's closed
                type group or fails its ``extends`` bound -- the backstop for
                every resolution path (single-candidate calls, explicit type
                arguments, the ``for ... in`` protocol's ``_next``); overload
                sets filter these violations during candidate trials instead.
        """
        violation = self.group_violation(func, bindings)
        if violation is not None:
            raise LangError(self.group_error(func.name, *violation), line)
        bviolation = self.bound_violation(func, bindings)
        if bviolation is not None:
            raise LangError(self.bound_error(func.name, *bviolation), line)
        key = (id(func), tuple(str(bindings[t]) for t in func.type_params))
        if key in self.instances:
            mangled = self.instances[key]
            ret, params, _ = self.signatures[mangled]
            return self.funcs[mangled], ret, params
        base = self.template_bases.get(id(func)) or self.symbol_bases.get(
            (func.source, func.name), func.name
        )
        bindings_str = ", ".join(str(bindings[t]) for t in func.type_params)
        mangled = f"{base}<{bindings_str}>"
        saved = GenContext.capture(self)
        self.type_bindings = bindings
        self.current_source = func.source  # the signature may name private structs
        try:
            ret = self.lang_type(func.ret_type, func.line)
            # A generic @noreturn is validated per instance: void-ness may
            # depend on the bindings (e.g. a return type naming T). Ditto a
            # generic `-> mut T`, whose void-ness depends on the bindings
            # (though T itself can never bind to void).
            self.check_noreturn_decl(func, ret)
            self.check_mut_return_decl(func, ret)
            params = [self.lang_type(t, func.line) for _, t in func.params]
            # A generic @format is validated per instance: the marked
            # parameter's type may only now have resolved.
            self.check_format_decl(func, params)
            hidden = self.hidden_ref_indices(func, params)
            fnty = ir.FunctionType(
                self.ret_ir(func, ret), self.param_irs(params, hidden)
            )
            fn = ir.Function(self.module, fnty, name=mangled)
            # A generic instance is emitted in every object that uses it, so it
            # merges like an imported definition rather than colliding.
            self.link_shared(fn, func.source)
            self.mark_inline(fn, func)
            self.mark_noalias(fn, func, params)
            self.mark_nonnull(fn, func, params)
            self.mark_noreturn(fn, func)
            if func.noreturn:
                self.noreturn_syms.add(mangled)
            if self.effect_bits.get(id(func)) is False:
                # The template's write-effect bit is per-template (candidate
                # union), so every instance of a clear template is clear.
                self.fact_safe_syms.add(mangled)
            # Register before generating the body so recursive calls resolve
            # (a failed instantiation therefore stays memoized -- harmless
            # while compilation aborts on the first error).
            self.funcs[mangled] = fn
            self.signatures[mangled] = (ret, params, False)
            self.hidden_ref[mangled] = hidden
            self.mut_ref[mangled] = self.mut_indices(func)
            self.nonnull_ref[mangled] = self.nonnull_indices(func)
            if func.mut_return:
                self.mut_ret.add(mangled)
            self.instances[key] = mangled
            self.gen_function(func, fn, ret, params)
        except LangError as err:
            # An error inside the body belongs to the template's file, not the
            # caller's -- attribute it here, where current_source still points at
            # the instance being generated (the top level otherwise blames the
            # root module for a line in an imported library). Then record the
            # instantiation frame against the requesting file; unwinding
            # appends nested frames innermost first. The note names the
            # source-level template (name plus bindings), not the linker
            # symbol: the mangle base spells alpha-renamed parameter
            # patterns, which would read as noise in a diagnostic.
            if err.source is None:
                err.source = self.current_source
            err.notes.append(
                Note(
                    f"in instantiation of {func.name}<{bindings_str}>",
                    line,
                    saved.current_source,
                )
            )
            raise
        finally:
            saved.restore(self)
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
        # Pointer arithmetic, handled ahead of operand unification (which would
        # otherwise coerce the two sides to one type and mangle the pointer).
        # NULLT is a pointer underneath, so every arm excludes it on both sides
        # to keep `null` operands rejected; function pointers carry no pointee
        # and so match none of these arms (they keep `==`/`!=` only).
        #
        # `p + n` / `p - n`: advance by n elements, exactly as `&p[n]` -- a bare
        # typed GEP (not gen_index_addr, whose deref-warning does not apply to
        # address arithmetic). The result carries `p`'s type verbatim.
        if (
            op in ("+", "-")
            and lhs.type is not NULLT
            and rhs.type is not NULLT
            and is_pointer(lhs.type)
            and is_integer(rhs.type)
        ):
            idx = rhs.value
            if op == "-":
                # Extend n to i64 by its own signedness *before* negating: a
                # naive neg on a narrow or unsigned n would advance wrongly.
                if rhs.type.ir.width != 64:
                    extend = self.builder.sext if rhs.type.signed else self.builder.zext
                    idx = extend(idx, INT64.ir)
                idx = self.builder.neg(idx)
            return TypedValue(self.builder.gep(lhs.value, [idx]), lhs.type)
        # `p - q`: signed element distance as int64. Identical pointer types
        # only, comparing pointees with const stripped so `int32*` and
        # `const int32*` subtract without an explicit cast.
        if (
            op == "-"
            and lhs.type is not NULLT
            and rhs.type is not NULLT
            and is_pointer(lhs.type)
            and is_pointer(rhs.type)
        ):
            pointee = strip_const(lhs.type.pointee)
            if pointee != strip_const(rhs.type.pointee):
                raise LangError(
                    f"cannot subtract {rhs.type} from {lhs.type}; pointer "
                    "difference requires identical pointer types",
                    line,
                )
            diff = self.builder.sub(
                self.builder.ptrtoint(lhs.value, INT64.ir),
                self.builder.ptrtoint(rhs.value, INT64.ir),
            )
            return TypedValue(
                self.builder.sdiv(diff, ir.Constant(INT64.ir, type_size(pointee))),
                INT64,
            )
        # `n + p`: addition is pointer-left only (the pointer is the base being
        # advanced, and `n - p` has no meaning), so reject the commuted form
        # with a spelling hint rather than a bare type error.
        if (
            op == "+"
            and rhs.type is not NULLT
            and is_integer(lhs.type)
            and is_pointer(rhs.type)
        ):
            raise LangError(
                "pointer arithmetic requires the pointer on the left "
                "(write `p + n`, not `n + p`)",
                line,
            )
        # Pointer ordering (`< <= > >=`): identical pointer types only, const
        # stripped, and no `null` operand (null has no ordering). Equality
        # (`==`/`!=`, including against null) stays on the post-unification icmp
        # path below, which coerces a NULLT operand to the other side's type.
        if (
            op in ("<", "<=", ">", ">=")
            and is_pointer(lhs.type)
            and is_pointer(rhs.type)
        ):
            if lhs.type is NULLT or rhs.type is NULLT:
                raise LangError(f"operator {op!r} not supported for null", line)
            if strip_const(lhs.type.pointee) != strip_const(rhs.type.pointee):
                raise LangError(
                    f"cannot compare {lhs.type} with {rhs.type}; pointer "
                    "ordering requires identical pointer types",
                    line,
                )
            return TypedValue(
                self.builder.icmp_unsigned(op, lhs.value, rhs.value), BOOL
            )
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
            # A declared error compares for identity only: the variants are
            # named causes with no meaningful order (the values are an
            # implementation detail of the numbering).
            if is_error_decl(op_type) and op not in ("==", "!="):
                raise LangError(f"operator {op!r} not supported for {op_type}", line)
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
