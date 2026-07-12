"""AST node classes produced by the parser and consumed by codegen."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class TypeRef:
    """A type as written in source.

    Captures a base name, its generic arguments, and pointer depth, so
    ``struct list<T>**`` is ``TypeRef("list", [TypeRef("T")], 2)``. A
    function-pointer type ``fn(A, B) -> R`` sets ``params`` and ``ret`` instead
    of ``args``; its ``name`` is ``"fn"`` and ``stars`` still applies, as in
    ``fn(...) -> R*``.

    Attributes:
        name: The base type name (``"fn"`` for a function-pointer type).
        args: Generic type arguments, e.g. the ``T`` in ``list<T>``.
        stars: Pointer depth -- the number of trailing ``*``.
        params: Parameter types for a ``fn(...) -> ret`` type, else ``None``.
        ret: Return type for a function-pointer type, else ``None``.
        dims: Fixed array sizes, outermost first, so ``int32[3][4]`` is
            ``dims=[3, 4]``. A ``None`` entry is an inferred ``[]`` (allowed
            only as the outermost dimension of an initialized array); a ``str``
            entry names an integer ``const``; anything more complex is kept as
            its expression node. All are resolved to a positive integer during
            code generation.
        const: A leading ``const`` qualifier -- a read-only type, as in the
            element of a ``slice<const T>``.
        nonnull: A leading ``@nonnull`` annotation -- meaningful only in the
            parameter position of a ``fn(...)`` type, where it spells the
            per-parameter non-null contract the function value carries, as in
            ``fn(@nonnull char*) -> void``.
        mut: A leading ``mut`` keyword -- meaningful only inside a ``fn(...)``
            type: in a parameter position it spells a by-reference writable
            parameter, as in ``fn(mut char) -> void``; on the ``ret`` type it
            spells a ``mut`` return, as in ``fn(uint64) -> mut char``.
    """

    name: str
    args: list["TypeRef"] = field(default_factory=list)
    stars: int = 0
    params: list["TypeRef"] | None = None  # set for fn(...) -> ret types
    ret: "TypeRef | None" = None
    dims: list = field(default_factory=list)  # array sizes, outermost first
    const: bool = False  # a leading `const` read-only qualifier
    variadic: bool = False  # a trailing `...` in a fn(...) type's parameters
    nonnull: bool = False  # a leading `@nonnull` (fn-type parameter position)
    mut: bool = False  # a leading `mut` (fn-type parameter or return position)

    def __str__(self) -> str:
        """Render the type back to its source spelling.

        Returns:
            The type as it would be written, including a leading ``const``,
            generic arguments, trailing ``*``, and array dimensions.
        """
        if self.params is not None:
            parts = [str(p) for p in self.params]
            if self.variadic:
                parts.append("...")
            text = "fn(" + ", ".join(parts) + ") -> " + str(self.ret)
        else:
            text = self.name
            if self.args or self.name == "tuple":
                # The empty tuple renders its canonical `tuple<>` spelling.
                text += "<" + ", ".join(str(a) for a in self.args) + ">"
        dims = "".join(f"[{render_dim(d)}]" for d in self.dims)
        return (
            ("@nonnull " if self.nonnull else "")
            + ("mut " if self.mut else "")
            + ("const " if self.const else "")
            + text
            + "*" * self.stars
            + dims
        )


def render_dim(d) -> str:
    """Render an array dimension back to its source spelling.

    A dimension is ``None`` (an inferred ``[]``), an integer literal, the
    ``str`` name of a ``const``, or a constant expression node.
    """
    if d is None:
        return ""
    if isinstance(d, (int, str)):
        return str(d)
    return render_const_expr(d)


def render_const_expr(e) -> str:
    """Best-effort render of a constant expression (used in array dimensions)."""
    if isinstance(e, IntLit):
        return str(e.value)
    if isinstance(e, BoolLit):
        return "true" if e.value else "false"
    if isinstance(e, Var):
        return e.name
    if isinstance(e, EnumAccess):
        return f"{e.enum}::{e.member}"
    if isinstance(e, SizeOf):
        return f"sizeof({e.type_name})"
    if isinstance(e, AlignOf):
        return f"alignof({e.type_name})"
    if isinstance(e, OffsetOf):
        return f"offsetof({e.type_name}, {e.field})"
    if isinstance(e, Unary):
        return f"{e.op}{render_const_expr(e.operand)}"
    if isinstance(e, Cast):
        return f"{render_const_expr(e.value)} as {e.type_name}"
    if isinstance(e, Binary):
        return f"({render_const_expr(e.lhs)} {e.op} {render_const_expr(e.rhs)})"
    if isinstance(e, Ternary):
        return (
            f"({render_const_expr(e.cond)} ? {render_const_expr(e.then)} "
            f": {render_const_expr(e.otherwise)})"
        )
    return "?"


@dataclass
class Program:
    """A whole compilation unit's top-level declarations.

    Attributes:
        imports: ``(path, line)`` pairs for each ``import``; resolved and
            merged away by the driver.
        structs: Struct declarations.
        functions: Function definitions and ``@extern`` declarations.
        globals: Top-level variables (``@extern`` or ``@static``).
        consts: Named compile-time constants.
        conditionals: Top-level ``@if`` blocks selecting whole declarations.
        enums: Enumeration declarations.
        aliases: Type-alias declarations.
        directives: Top-level ``@static_assert``/``@error``/``@warning``
            directives, checked during code generation after types and
            constants are known.
        module_imports: Per-file import graph recorded while the driver
            merges the ``import`` tree: each key is a file's resolved path
            (``None`` for a program parsed from a string) and its value the
            resolved paths that file imports directly. Code generation walks
            it to find the signatures an interface stub's defining object
            could see (open overload sets derive a stub's plain-vs-mangled
            symbol choice from the stub's own import closure).
    """

    imports: list[tuple[str, int]]
    structs: list["StructDecl | UnionDecl"]
    functions: list["Func"]
    globals: list["GlobalVar"]
    consts: list["Const"] = field(default_factory=list)
    conditionals: list["Conditional"] = field(default_factory=list)
    enums: list["EnumDecl"] = field(default_factory=list)
    aliases: list["TypeAlias"] = field(default_factory=list)
    directives: list["StaticAssert | ErrorDirective"] = field(
        default_factory=list
    )
    module_imports: dict[str | None, tuple[str, ...]] = field(
        default_factory=dict
    )


@dataclass
class StructDecl:
    """A ``struct`` type declaration.

    Attributes:
        name: The struct's name.
        type_params: Generic type parameters, e.g. the ``T`` in
            ``struct list<T>``.
        fields: ``(name, type)`` pairs in declaration order.
        line: Source line for diagnostics.
        base: The ``extends Base`` struct this one specializes, or ``None``.
            Its fields are spliced in front of ``fields`` (so a pointer to this
            struct is layout-compatible with a pointer to the base), and its
            ``@packed``/``@align``/``@volatile`` attributes are inherited.
        private: ``@private`` -- usable only within its source file.
        static: ``@static`` -- file-scoped name other files may reuse.
        align: ``@align(N)`` -- raised alignment, a power of two, or ``None``.
        packed: ``@packed`` -- no padding between fields; alignment 1.
        volatile: ``@volatile`` -- field accesses cannot be optimized away.
        defaults: ``{field name: default-value expression}`` for fields declared
            ``name: type = expr;``. A struct literal uses a field's default when
            the field is omitted (falling back to zero when there is none).
        union: ``True`` for a ``union`` declaration -- the members share one
            storage (all at offset 0) instead of being laid out sequentially.
            Unions ride on the struct machinery; ``base``, ``defaults``, and
            flexible array members are rejected for them.
        source: Defining file, stamped by the driver.
        type_param_defaults: ``{type parameter: TypeRef}`` for parameters
            declared ``<T = type>``. A defaulted parameter may be omitted from
            a written type or left uninferred by a literal's typed fields; the
            default fills it. Defaults are trailing-only and may reference only
            earlier type parameters (both enforced at parse time).
    """

    name: str
    type_params: list[str]
    fields: list[tuple[str, TypeRef]]
    line: int
    base: TypeRef | None = None
    private: bool = False
    static: bool = False
    align: int | None = None
    packed: bool = False
    volatile: bool = False
    defaults: dict = field(default_factory=dict)
    union: bool = False
    source: str | None = None
    type_param_defaults: dict[str, TypeRef] = field(default_factory=dict)
    span: tuple[int, int] | None = field(default=None, compare=False)


@dataclass
class UnionDecl:
    """A ``union`` type declaration -- its own node, parallel to ``StructDecl``.

    A union carries the same declaration attributes a struct does, minus the
    struct-only forms that are already rejected for it at parse time: it has no
    ``extends`` base and no member defaults. Splitting it off ``StructDecl``
    means ``isinstance(u, StructDecl)`` is false, so a struct-only code path
    (sequential layout, ``extends``, prefix upcast) can never silently accept a
    union -- the type kind, not a runtime ``union`` flag, now enforces it.

    The shared aggregate-instantiation path in code generation reads
    :attr:`union`, :attr:`base`, and :attr:`defaults` uniformly across both node
    kinds; a union answers them intrinsically (a union, no base, no defaults), so
    that path stays a single body without branching on the node type.

    Attributes:
        name: The union's name.
        type_params: Generic type parameters, e.g. the ``T`` in ``union u<T>``.
        fields: ``(name, type)`` pairs -- the union's members, all sharing one
            storage at offset 0.
        line: Source line for diagnostics.
        private: ``@private`` -- usable only within its source file.
        static: ``@static`` -- file-scoped name other files may reuse.
        align: ``@align(N)`` -- raised alignment, a power of two, or ``None``.
        packed: ``@packed`` -- alignment 1.
        volatile: ``@volatile`` -- member accesses cannot be optimized away.
        source: Defining file, stamped by the driver.
        type_param_defaults: ``{type parameter: TypeRef}`` for parameters
            declared ``<T = type>`` (trailing-only, earlier-parameter references).
    """

    name: str
    type_params: list[str]
    fields: list[tuple[str, TypeRef]]
    line: int
    private: bool = False
    static: bool = False
    align: int | None = None
    packed: bool = False
    volatile: bool = False
    source: str | None = None
    type_param_defaults: dict[str, TypeRef] = field(default_factory=dict)
    span: tuple[int, int] | None = field(default=None, compare=False)

    @property
    def union(self) -> bool:
        """A union is intrinsically a union (the shared-path discriminant)."""
        return True

    @property
    def base(self) -> None:
        """A union has no ``extends`` base (rejected at parse time)."""
        return None

    @property
    def defaults(self) -> dict:
        """A union has no member defaults (rejected at parse time)."""
        return {}


@dataclass
class EnumDecl:
    """An ``enum`` declaration: a named set of compile-time constants.

    ``enum Color: int32 { Red = 0, Green = 1, ... }`` introduces ``Color`` as a
    type aliasing its underlying type, plus a constant ``Color::Red`` of that
    type for each member. The underlying type may be any type; a member's value
    is any expression that folds to a constant of it.

    An ``error`` declaration (``error my_error { NOT_FOUND, ... }``) rides the
    same chassis with ``is_error`` set: the members become the variants of a
    nominal, ``int32``-backed error type (auto-numbered from 1; a member's
    value expression may be ``None``), and a variant may carry a display
    string in ``displays`` instead of a value.

    Attributes:
        name: The enum's name, usable both as a type and as the ``::`` scope.
        underlying: The underlying ``TypeRef``, or ``None`` to default to
            ``int32``. Always ``None`` for an error declaration.
        members: ``(name, value expression)`` pairs in declaration order. For
            an error declaration the expression is ``None`` when the variant
            auto-numbers.
        line: Source line for diagnostics.
        private: ``@private`` -- usable only within its source file.
        static: ``@static`` -- file-scoped name other files may reuse.
        source: Defining file, stamped by the driver.
        is_error: ``True`` for an ``error`` declaration.
        displays: ``{variant name: display string}`` for error variants
            declared ``NAME = "display"``. Empty for plain enums.
    """

    name: str
    underlying: TypeRef | None
    members: list[tuple[str, object]]
    line: int
    private: bool = False
    static: bool = False
    source: str | None = None
    span: tuple[int, int] | None = field(default=None, compare=False)
    is_error: bool = False
    displays: dict = field(default_factory=dict)


@dataclass
class TypeAlias:
    """A ``type <name> = <type>;`` declaration -- a transparent type name.

    The alias is structural, not a new distinct type: ``type cb = fn(int32) ->
    int32;`` makes ``cb`` interchangeable with the function-pointer type it names.

    A generic alias carries a type-parameter list (``type entry<T> = pair<char*,
    T>;``), naming a family of types. It stays transparent: ``entry<int32>`` *is*
    ``pair<char*, int32>``, expanded in the type resolver, minting no
    monomorphized artifact of its own.

    Attributes:
        name: The alias name, usable anywhere a type is.
        target: The aliased type.
        line: Source line for diagnostics.
        type_params: Generic type parameters, e.g. the ``T`` in ``entry<T>``.
            Empty for a plain alias.
        type_param_defaults: ``{type parameter: TypeRef}`` for parameters
            declared ``<T = type>``. Trailing-only and may reference only
            earlier parameters (both enforced at parse time), as on functions
            and structs.
        private: ``@private`` -- usable only within its source file.
        static: ``@static`` -- file-scoped name other files may reuse.
        source: Defining file, stamped by the driver.
        span: Source byte span, for the interface generator.
    """

    name: str
    target: TypeRef
    line: int
    type_params: list[str] = field(default_factory=list)
    type_param_defaults: dict[str, TypeRef] = field(default_factory=dict)
    private: bool = False
    static: bool = False
    source: str | None = None
    span: tuple[int, int] | None = field(default=None, compare=False)


@dataclass
class Func:
    """A function definition or an ``@extern`` declaration.

    Attributes:
        name: The function's name.
        type_params: Generic type parameters, e.g. the ``T`` in ``fn sum<T>``.
        params: ``(name, type)`` pairs for the parameters.
        ret_type: The declared return type.
        body: The statement list; empty for an ``@extern`` declaration.
        line: Source line for diagnostics.
        private: ``@private`` -- callable only within its source file.
        static: ``@static`` -- file-scoped name other files may reuse.
        extern: ``@extern`` -- a declaration only; defined elsewhere with the
            C calling convention.
        proto: A bodyless ``fn`` prototype -- a concrete mcc function defined
            in another object, called with the mcc convention (``mut``/``const``
            hidden references included). Interface stubs emit these.
        variadic: Extern only -- a trailing ``...`` for C-style varargs.
        inline: ``@inline`` -- emit with LLVM's ``alwaysinline`` so the body is
            inlined at every call site when optimizing.
        symbol: ``@symbol("...")`` -- the linker name, when not ``name``.
        source: Defining file, stamped by the driver.
        const_params: Names of parameters declared ``const`` -- read-only in the
            body; a ``const`` struct is passed by a hidden pointer rather than
            copied.
        mut_params: Names of parameters declared ``mut`` -- passed by hidden
            reference to the caller's storage (whatever the type), writable
            through the reference, with ``&`` on them rejected so the
            reference cannot escape.
        noalias_params: Names of pointer parameters declared ``@noalias`` --
            promised not to overlap any other pointer the function reaches
            (C's ``restrict``), lowered to LLVM's ``noalias`` argument
            attribute. Unchecked: overlapping arguments are undefined behavior.
        nonnull_params: Names of pointer parameters declared ``@nonnull`` --
            statically guaranteed non-null. Checked: every call site must
            prove the argument non-null, and the callee may pass the
            parameter onward as proof. Lowered to LLVM's ``nonnull`` (plus
            ``dereferenceable``) argument attributes.
        format_params: Names of parameters declared ``@format`` -- the format
            string of a collecting function (``print``/``println``-style).
            Purely compile-time: a string *literal* bound to the parameter is
            scanned at the call site, desugaring positional ``{n}``
            placeholders into the sequential runtime form by duplicating or
            reordering the collected arguments. Valid only on the
            ``slice<const char>`` parameter just before the collecting
            ``args...`` (checked once the signature resolves).
        noreturn: ``@noreturn`` -- the function never returns to its caller
            (it exits, aborts, or loops forever). Void-only. A call site's
            block is terminated right after the call (so no dummy return is
            needed past it), falling off the end of the body is undefined
            behavior (an auto-``unreachable``, C11 ``_Noreturn`` semantics),
            and a ``return`` statement in the body is a compile error.
        mut_return: ``-> mut T`` -- the function returns an lvalue: a
            reference to caller-reachable storage of type ``T``, lowered as
            a pointer return. The call expression is assignable, a base for
            projections, and re-lendable as a ``mut`` argument; in value
            context it loads. The body's ``return`` may only form the
            reference from a ``mut``/pointer parameter or a global (never
            this call's own frame). A flag on the function, not part of the
            return ``TypeRef`` -- ``mut`` is not a type.
        deprecated_msg: The ``@deprecated("...")`` migration message, or
            ``None``. Every call site (and function-value use) emits a
            warning carrying it; the function stays callable.
        removed_msg: The ``@removed("...")`` migration message, or ``None``.
            The declaration is a tombstone: every call site (and
            function-value use) is a hard compile error carrying it. The
            signature is never resolved and a body, if any, is never
            generated.
        override: ``@override`` -- this definition replaces a same-pattern
            member of an open overload set declared in *another* module.
            The overridden (unannotated) definition is dropped before
            registration, so only this body is emitted under the shared
            mangled symbol. Requires exactly one source-visible, body-bearing,
            cross-module target of the same pattern; no target, a same-file
            target, or a second ``@override`` of one pattern is a compile
            error.
        type_param_defaults: ``{type parameter: TypeRef}`` for parameters
            declared ``<T = type>``. The default fills a parameter that is
            neither given explicitly nor inferred from a *typed* argument.
            Defaults are trailing-only and may reference only earlier type
            parameters (both enforced at parse time).
        type_param_groups: ``{type parameter: [TypeRef, ...]}`` for parameters
            declared with a closed type group (``<T: int64 | int32>``): the
            pipe-separated members are the only types the parameter may
            instantiate to. Members are concrete types (never type
            parameters -- enforced at parse time); a grouped parameter's
            default must name a member (checked at declaration).
        type_param_bounds: ``{type parameter: TypeRef}`` for parameters
            declared with a nominal bound (``<T extends shape>``): the
            parameter may instantiate only to the bound struct or a struct in
            its declared ``extends`` lineage. Open-ended (unlike a closed
            group), so checking is lazy per instantiation; the bound target is
            a concrete struct (never a type parameter -- enforced at parse
            time), a parameter cannot carry both a bound and a group, and a
            bounded parameter's default must satisfy the bound (checked at
            declaration).
        struct_type_args: For a method ``fn Type<...>::m``, the raw pre-``::``
            type-reference list held verbatim for codegen to classify -- every
            argument a fresh type-parameter name is a generic method (the
            struct's parameters prepend the method's own into one template),
            every argument a concrete type is a specialization (a concrete
            overload of ``Type::m`` outranking the generic for a matching
            receiver), and a mix is a partial specialization (a template
            matching only receivers whose concrete positions agree). ``None``
            for a non-method; cleared to ``None`` once codegen classifies it.
        struct_arg_groups: ``{argument name: [TypeRef, ...]}`` for pre-``::``
            arguments decorated with a closed type group
            (``fn pair<int32, U: int8 | int16>::m``). Decorations may only
            sit on a bare name; whether that name is a fresh type parameter
            (required -- decorating a concrete type is an error) is decided by
            codegen alongside the ``struct_type_args`` classification, as are
            the declaration-shape checks ``parse_type_params`` runs at parse
            time. Cleared with ``struct_type_args``.
        struct_arg_bounds: ``{argument name: TypeRef}`` for pre-``::``
            arguments decorated with an ``extends`` bound; same rules as
            ``struct_arg_groups``.
        struct_arg_defaults: ``{argument name: TypeRef}`` for pre-``::``
            arguments decorated with a ``=`` default; same rules as
            ``struct_arg_groups``.
        alias_qualifier: For a method declared through a type-alias qualifier
            (``fn pointf::m`` with ``type pointf = point<float64>``), the
            alias name as written. Codegen canonicalizes ``name`` to the
            aliased type's family (``point::m``), so the original spelling is
            kept here for the interface writer: a generic method travels
            verbatim from its source span, which still spells the alias, so
            the ``.mci`` dependency scan must pull the alias declaration in.
            ``None`` when the qualifier was written canonically.
    """

    name: str
    type_params: list[str]
    params: list[tuple[str, TypeRef]]
    ret_type: TypeRef
    body: list
    line: int
    private: bool = False
    static: bool = False
    extern: bool = False
    proto: bool = False
    variadic: bool = False
    inline: bool = False
    symbol: str | None = None
    source: str | None = None
    const_params: set[str] = field(default_factory=set)
    mut_params: set[str] = field(default_factory=set)
    noalias_params: set[str] = field(default_factory=set)
    nonnull_params: set[str] = field(default_factory=set)
    format_params: set[str] = field(default_factory=set)
    noreturn: bool = False
    mut_return: bool = False
    deprecated_msg: str | None = None
    removed_msg: str | None = None
    override: bool = False
    type_param_defaults: dict[str, TypeRef] = field(default_factory=dict)
    type_param_groups: dict[str, list[TypeRef]] = field(default_factory=dict)
    type_param_bounds: dict[str, TypeRef] = field(default_factory=dict)
    struct_type_args: list[TypeRef] | None = None
    struct_arg_groups: dict[str, list[TypeRef]] = field(default_factory=dict)
    struct_arg_bounds: dict[str, TypeRef] = field(default_factory=dict)
    struct_arg_defaults: dict[str, TypeRef] = field(default_factory=dict)
    alias_qualifier: str | None = None
    span: tuple[int, int] | None = field(default=None, compare=False)


@dataclass
class GlobalVar:
    """A top-level variable: ``@extern`` (defined elsewhere) or ``@static``.

    Attributes:
        name: The variable's name.
        type_name: The declared type, or ``None`` for an ``@static`` variable
            whose type is inferred from its initializer.
        line: Source line for diagnostics.
        private: ``@private`` -- usable only within its source file.
        volatile: ``@volatile`` -- accesses cannot be optimized away.
        static: ``@static`` -- file-scoped storage, zero-initialized.
        init: ``@static`` initializer (a constant expression), or ``None``.
        symbol: ``@symbol("...")`` -- the linker name, when not ``name``.
        source: Declaring/defining file, stamped by the driver.
    """

    name: str
    type_name: TypeRef | None
    line: int
    private: bool = False
    volatile: bool = False
    static: bool = False
    init: object | None = None
    symbol: str | None = None
    source: str | None = None
    span: tuple[int, int] | None = field(default=None, compare=False)


@dataclass
class Const:
    """A named compile-time constant: ``const NAME [: type] = value;``.

    Attributes:
        name: The constant's name.
        type_name: Optional type annotation; ``None`` means the value's own
            type is used.
        value: The constant expression, folded at compile time.
        line: Source line for diagnostics.
        private: ``@private`` -- usable only within its source file.
        source: Defining file, stamped by the driver.
    """

    name: str
    type_name: TypeRef | None
    value: object
    line: int
    private: bool = False
    source: str | None = None
    span: tuple[int, int] | None = field(default=None, compare=False)


@dataclass
class Let:
    """A local variable declaration: ``let name [: type] [= value];``.

    Also carries destructuring: ``let a, b = t;`` binds one local per
    position of a tuple or slice source, and a trailing ``...`` marks the
    last binder as the rest binder taking the tail (``let a, rest... = t;``).
    A destructuring ``let`` never has a type annotation (each binder takes
    its position's type) and always has an initializer.

    Attributes:
        name: The variable's name (the first binder when destructuring).
        type_name: Optional type annotation, inferred from ``value`` when
            ``None``.
        value: The initializer, or ``None`` for ``let x: T;`` (declared but
            uninitialized).
        line: Source line for diagnostics.
        extra: Binders after the first, in order (empty for a plain ``let``).
        rest: Whether the last binder (``extra[-1]``, or ``name`` when
            ``extra`` is empty) is the trailing-``...`` rest binder.
    """

    name: str
    type_name: TypeRef | None
    value: object | None
    line: int
    extra: list[str] = field(default_factory=list)
    rest: bool = False


@dataclass
class Assign:
    """An assignment to a named variable: ``name = value;``.

    Attributes:
        name: The target variable's name.
        value: The expression assigned.
        line: Source line for diagnostics.
    """

    name: str
    value: object
    line: int


@dataclass
class CompoundAssign:
    """A compound assignment: ``target op= value;`` for an arithmetic,
    bitwise, or shift operator.

    ``target op= value`` means ``target = target op value``, but the target's
    address is computed only once -- so any side effects in a complex lvalue
    (e.g. ``arr[next()] += 1``) happen a single time.

    Attributes:
        target: The lvalue expression written: a ``Var``, ``*ptr`` (a ``Unary``
            with ``op == "*"``), ``base[index]`` (an ``Index``), or a member
            (a ``Member``) -- the same forms a plain assignment accepts.
        op: The base binary operator, without the trailing ``=`` (e.g. ``"+"``,
            ``"<<"``).
        value: The right-hand side expression.
        line: Source line for diagnostics.
    """

    target: object
    op: str
    value: object
    line: int


@dataclass
class Return:
    """A ``return [value];`` statement.

    Attributes:
        value: The returned expression, or ``None`` for a bare ``return``.
        line: Source line for diagnostics.
    """

    value: object | None
    line: int


@dataclass
class If:
    """An ``if`` / ``else`` statement.

    Attributes:
        cond: The condition expression.
        then: Statements run when ``cond`` is true.
        otherwise: The ``else`` statements, empty when absent.
        line: Source line for diagnostics.
    """

    cond: object
    then: list
    otherwise: list
    line: int


@dataclass
class While:
    """A ``while`` (or ``until``) loop.

    Attributes:
        cond: The loop condition expression.
        body: The loop body statements.
        line: Source line for diagnostics.
        until: When ``True``, this is an ``until`` loop, running while the
            condition is false.
    """

    cond: object
    body: list
    line: int
    until: bool = False


@dataclass
class Break:
    """A ``break`` statement leaving the innermost loop.

    Attributes:
        line: Source line for diagnostics.
    """

    line: int


@dataclass
class Continue:
    """A ``continue`` statement jumping to the innermost loop's next iteration.

    Attributes:
        line: Source line for diagnostics.
    """

    line: int


@dataclass
class Unreachable:
    """An ``unreachable;`` statement asserting a path is never reached.

    Lowers to LLVM ``unreachable``: the block is terminated, so no trailing
    ``return`` is needed after it and the arm counts as diverging. Actually
    reaching it at runtime is undefined behavior (like C's
    ``__builtin_unreachable``).

    Attributes:
        line: Source line for diagnostics.
    """

    line: int


@dataclass
class Defer:
    """A ``defer { ... }`` whose body runs when the enclosing block exits.

    Attributes:
        body: Statements to run at scope exit, in LIFO order across defers.
        line: Source line for diagnostics.
    """

    body: list
    line: int


@dataclass
class For:
    """A ``for var in iterable { ... }`` loop over the iter/next protocol.

    Attributes:
        var: The loop variable bound to each element.
        iterable: The expression providing ``iter``/``next``.
        body: The loop body statements.
        line: Source line for diagnostics.
    """

    var: str
    iterable: object
    body: list
    line: int


@dataclass
class Block:
    """A bare ``{ ... }`` statement introducing its own scope.

    Attributes:
        body: The block's statements.
        line: Source line for diagnostics.
    """

    body: list
    line: int


@dataclass
class BlockExpr:
    """A block used in expression position: ``{ ...; emit value; }``.

    Runs its statements in their own scope and yields the value handed to
    ``emit``, like an inlined, single-use, anonymous function -- temporaries
    declared inside do not leak to the enclosing scope. A block must ``emit``
    on the path that reaches its end (branch-only ``emit``\\ s need a trailing
    one, exactly as a function needs a trailing ``return``).

    Attributes:
        body: The block's statements, ending in an ``emit``.
        line: Source line for diagnostics.
    """

    body: list
    line: int


@dataclass
class Emit:
    """An ``emit value;`` -- the value of the nearest enclosing block-expression.

    Plays the role ``return`` plays for a function: it fills in the block's
    value and transfers control to the block's end. It is an error outside a
    block-expression.

    Attributes:
        value: The expression yielded as the block's value.
        line: Source line for diagnostics.
    """

    value: object
    line: int


@dataclass
class Import:
    """An ``import "file";`` appearing inside a top-level ``@if`` branch.

    A plain top-of-file import is collected directly into ``Program.imports``;
    this node carries one nested in a conditional, which the driver resolves
    once the branch's condition is evaluated against the target facts.

    Attributes:
        path: The imported file's path, as written (without the quotes).
        line: Source line for diagnostics.
    """

    path: str
    line: int


@dataclass
class Conditional:
    """An ``@if (cond) { ... } @else { ... }`` compile-time selection.

    Attributes:
        cond: A constant expression over the target facts.
        then: Items kept when ``cond`` holds -- declarations or statements.
        otherwise: The ``@else`` items, same shape as ``then``; empty when
            absent.
        line: Source line for diagnostics.
    """

    cond: object
    then: list
    otherwise: list
    line: int


@dataclass
class StaticAssert:
    """A ``@static_assert(cond, "msg");`` compile-time check.

    The condition is folded by ``eval_const`` during code generation (not at
    parse time), so it may use ``sizeof``/``alignof``/``offsetof``/``const``
    references, which need the type system. The compile fails with ``message``
    when the condition folds to a false (zero) integer or ``bool`` constant;
    any nonzero constant passes. Used to validate struct layouts, alignment
    requirements, or type sizes before linking.

    Attributes:
        cond: The condition expression, folded at code-generation time.
        message: The failure message, reported verbatim.
        line: Source line for diagnostics.
        source: Defining file, stamped by the driver.
    """

    cond: object
    message: str
    line: int
    source: str | None = None


@dataclass
class ErrorDirective:
    """An ``@error("msg");`` or ``@warning("msg");`` diagnostic directive.

    ``@error`` fails the compile at its position with ``message``; ``@warning``
    is its non-fatal twin, collecting a warning instead of aborting. Both are
    most useful guarded by a top-level ``@if``
    (``@if (!TARGET_OS) { @error("unsupported OS"); }``), where the dead
    branch is dropped and only a live one fires.

    Attributes:
        message: The diagnostic message, reported verbatim.
        line: Source line for diagnostics.
        source: Defining file, stamped by the driver.
        warning: ``True`` for ``@warning`` (collect, keep compiling);
            ``False`` for ``@error`` (abort).
    """

    message: str
    line: int
    source: str | None = None
    warning: bool = False


@dataclass
class Case:
    """A ``case (subject) { when v: ... else: ... }`` with no fall-through.

    Attributes:
        subject: The value matched against each arm.
        arms: ``(value expressions, body statements)`` for each ``when``; an
            arm matches if the subject equals any of its comma-separated values.
        otherwise: The ``else:`` body, empty when absent.
        line: Source line for diagnostics.
    """

    subject: object
    arms: list
    otherwise: list
    line: int


@dataclass
class CaseType:
    """A ``case type (a) { when int32 n: ... else: ... }`` type-switch.

    Matches an ``any`` subject's tag against each arm's types; the matching
    arm's binding holds the recovered value, scoped to that arm. ``type`` is a
    contextual keyword (the plain ``case`` grammar expects ``(`` next), each
    arm names one or more comma-separated types over one binding and must
    bind a name, and ``else`` is required -- the set of types an ``any`` can
    hold is open. A multi-type arm's binding is an implicit generic: the
    shared body compiles once per listed type, the binding typed as that
    type per copy. An arm whose single bare type name does not resolve is a
    *generic arm* (decided at codegen, not here): ``when T* ptr:`` matches
    every boxed pointer tag and ``when T v:`` every remaining boxed tag,
    the body monomorphized once per matching tag.

    The ``with`` statement (``with (t = v as T) body; else other;``) is pure
    sugar over this node: the parser desugars it to a single-arm ``CaseType``
    whose ``otherwise`` is the ``else`` body -- or empty, since a lone
    ``with`` has defined fall-through -- with ``is_with`` set so diagnostics
    name the construct the user wrote.

    Attributes:
        subject: The matched expression; must be an ``any`` (an ``any*``
            auto-dereferences).
        arms: ``(list of TypeRefs, binding name, body statements, line)`` per
            ``when``.
        otherwise: The mandatory ``else:`` body (possibly-empty ``else``
            body for a desugared ``with``).
        line: Source line for diagnostics.
        is_with: Whether this node was written as a ``with`` statement, so
            errors say ``with`` rather than ``case type``.
    """

    subject: object
    arms: list
    otherwise: list
    line: int
    is_with: bool = False


@dataclass
class ExprStmt:
    """An expression evaluated for its side effects, e.g. a call.

    Attributes:
        expr: The expression to evaluate.
        line: Source line for diagnostics.
    """

    expr: object
    line: int


@dataclass
class StoreDeref:
    """A store through a pointer: ``*ptr = value;``.

    Attributes:
        ptr: The pointer expression to write through.
        value: The expression to store.
        line: Source line for diagnostics.
    """

    ptr: object
    value: object
    line: int


@dataclass
class StoreIndex:
    """An indexed store: ``base[index] = value;``.

    Attributes:
        base: The array or pointer expression.
        index: The index expression.
        value: The expression to store.
        line: Source line for diagnostics.
    """

    base: object
    index: object
    value: object
    line: int


@dataclass
class StoreMember:
    """A field store: ``base.field = value;`` or ``base->field = value;``.

    Attributes:
        base: The struct value or pointer expression.
        field: The field name written.
        arrow: ``True`` for ``->`` (through a pointer), ``False`` for ``.``.
        value: The expression to store.
        line: Source line for diagnostics.
    """

    base: object
    field: str
    arrow: bool
    value: object
    line: int


@dataclass
class StoreCall:
    """A store through a ``mut``-returning call: ``f(s, i) = value;``.

    The call's ``-> mut T`` result is the target lvalue: the callee's
    returned reference is addressed once and the value is stored through
    it. A call to a function without a ``mut`` return is rejected at
    codegen (a plain result is not assignable).

    Attributes:
        call: The ``Call`` (or ``CallExpr``, for a call through a
            function-pointer expression such as a struct field) whose
            ``mut`` return is the assignment target.
        value: The expression to store.
        line: Source line for diagnostics.
    """

    call: object
    value: object
    line: int


@dataclass
class IntLit:
    """An integer literal -- an untyped constant that adapts to context.

    Attributes:
        value: The integer value.
        line: Source line for diagnostics.
    """

    value: int
    line: int


@dataclass
class CharLit:
    """A character literal: ``'a'``, ``'\\n'``, ``'\\0'`` -- a one-byte ``char``.

    Attributes:
        value: The byte value of the character.
        line: Source line for diagnostics.
    """

    value: int
    line: int


@dataclass
class FloatLit:
    """A floating-point literal of type ``float64``.

    Attributes:
        value: The literal's value.
        line: Source line for diagnostics.
    """

    value: float
    line: int


@dataclass
class BoolLit:
    """A ``true`` / ``false`` boolean literal.

    Attributes:
        value: The boolean value.
        line: Source line for diagnostics.
    """

    value: bool
    line: int


@dataclass
class StrLit:
    """A string literal of type ``uint8*``.

    Attributes:
        value: The decoded string contents.
        line: Source line for diagnostics.
    """

    value: str
    line: int


@dataclass
class FStrHole:
    """One ``{expr}`` placeholder of an interpolated f-string literal.

    Attributes:
        expr: The hole's parsed expression.
        label: For the inspector form ``{expr=}``, the hole's verbatim source
            text up to the modifier (the expression's spelling, the ``=``, and
            any whitespace around it) -- spliced into the format text ahead of
            the placeholder; ``None`` for a plain hole.
        modifier: The runtime modifier text after the hole's ``:``, or ``""``
            when the hole has none.
    """

    expr: object
    label: str | None
    modifier: str


@dataclass
class FStrLit(StrLit):
    """An interpolated string literal ``f"x = {x}"``.

    Parse-time sugar over the sequential format runtime: ``value`` holds the
    already-desugared format text (each hole reduced to its ``{modifier}``
    placeholder, literal braces and inspector labels ``{{``/``}}``-escaped)
    and ``holes`` the interpolated expressions, in order. Legal only as the
    format string of an ``@format`` call, where the holes splice in as the
    collected arguments; every other sink rejects it (the ``StrLit`` funnels
    guard). A literal with no holes (``f"{{}}"``, ``f"no holes"``) still
    builds this node with an empty ``holes`` list, keeping its f-string
    identity so the ``@format``-only rule governs it too -- it never degrades
    to a plain ``StrLit`` that a verbatim overload could bind.

    Attributes:
        holes: The ``FStrHole`` records, one per placeholder, in source order
            (empty when the literal interpolates nothing).
    """

    holes: list


@dataclass
class NullLit:
    """The ``null`` pointer constant, adapting to any pointer type.

    Attributes:
        line: Source line for diagnostics.
    """

    line: int


@dataclass
class ArrayLit:
    """An array literal ``[e0, e1, ...]``, nested for multiple dimensions.

    Attributes:
        elements: The element expressions (themselves ``ArrayLit`` when
            nested).
        line: Source line for diagnostics.
    """

    elements: list
    line: int


@dataclass
class TupleLit:
    """A tuple literal ``(e0, e1, ...)``.

    A parenthesized expression with at least one top-level comma; a plain
    ``(x)`` stays grouping and never builds this node. In a tuple-typed
    position (a typed ``let``/assignment/return/argument/element/field) each
    element lowers against its position's type, the way a bare ``StructLit``
    adapts; with no context the literal fixes its own ``tuple<...>`` type,
    untyped constants anchoring to their defaults.

    Attributes:
        elements: The element expressions, in position order.
        line: Source line for diagnostics.
    """

    elements: list
    line: int


@dataclass
class StructLit:
    """A struct literal ``struct Name[<args>] { field = expr, ... }``.

    Produces a struct value with the listed fields set and every omitted field
    zero-initialized. A *bare* literal ``{ field = expr, ... }`` leaves
    ``type_ref`` ``None`` and takes its struct type from context (a typed
    ``let``/assignment/return/argument/element/field), the way ``[...]`` and
    ``"..."`` adapt.

    Attributes:
        type_ref: The struct type (its name and any generic arguments), or
            ``None`` for a bare, type-inferred literal.
        fields: ``(field name, value expression)`` pairs, in source order.
        line: Source line for diagnostics.
    """

    type_ref: "TypeRef | None"
    fields: list
    line: int


@dataclass
class ResultLit:
    """A ``result`` constructor: ``ok(v)``, ``ok()``, or ``error(e)``.

    The only ways to build a ``result`` value. Context-typed like a bare
    ``StructLit``: the position must fix a ``result<T, E>`` / ``result<E>``
    type (a typed ``let``/assignment/return/argument/field); with no such
    context the constructor is an error. ``ok()`` with no value is legal only
    for the error-only ``result<E>``; ``error(e)`` always takes the error
    value. ``ok`` and ``error`` are not keywords -- the parser claims them
    only when directly followed by ``(``, so both stay ordinary identifiers
    elsewhere.

    Attributes:
        kind: ``"ok"`` or ``"error"``.
        value: The wrapped value expression, or ``None`` for ``ok()``.
        line: Source line for diagnostics.
    """

    kind: str
    value: object | None
    line: int


@dataclass
class ErrorName:
    """An error accessor: ``error_name(err)`` or ``error_message(err)``.

    Renders a declared ``error`` value to a ``char*`` string at runtime.
    ``error_name`` yields the variant's fully qualified name (e.g.
    ``"my_error::NOT_FOUND"``); ``error_message`` yields the variant's declared
    display string when it has one, else falls back to the bare identifier
    (e.g. ``"NOT_FOUND"``) -- so a message is never empty for a real variant.
    Both funnel through a compiler-synthesized per-declaration lookup function.
    Like ``ok``/``error``, the names are claimed only when directly followed by
    ``(``, so they stay ordinary identifiers elsewhere.

    Attributes:
        operand: The error-typed expression to render.
        display: ``True`` for ``error_message`` (display-or-identifier),
            ``False`` for ``error_name`` (the qualified name).
        line: Source line for diagnostics.
    """

    operand: object
    display: bool
    line: int


@dataclass
class Except:
    """A ``try`` expression: ``try expr except (err) { H } [else { S }]``.

    ``try`` binds the call chain that follows (a unary-level prefix, so the
    whole form composes as an operand); the ``except`` handler is one of its
    three endings (see :class:`Try` for bare propagation and
    :class:`TryFallback` for the ``??`` default -- a try takes exactly one
    clause). Branches on the result's tag: on error the handler
    ``H`` runs with ``binder`` bound to the error value (a plain copy,
    scoped to ``H``); on ok the optional ``else`` block ``S`` runs. Where a
    value escapes, ``H`` must diverge or ``emit`` a fallback for it; as a
    whole expression statement it is obligation-free. ``S`` is the ok arm
    only (Python's ``try``/``except``/``else``): it is skipped on the
    handler's emit-fallback path. The binding forms (a ``let`` initializer,
    a ``return`` value, an expression statement) special-case the node when
    it is the whole expression; anywhere else it lowers as a plain value.

    Attributes:
        value: The tested ``try`` operand (must evaluate to a ``result``).
        binder: The error binding's name, scoped to ``handler``.
        handler: The handler block's statements.
        otherwise: The ``else`` block's statements, or ``None`` when absent.
        line: Source line for diagnostics.
    """

    value: object
    binder: str
    handler: list
    otherwise: list | None
    line: int


@dataclass
class Try:
    """A bare ``try expr``: propagate the error up the call stack.

    On error, the enclosing function returns ``error(err)`` -- so its return
    type must be a ``result`` carrying the **same** declared error type
    (``result<T2, E>`` or ``result<E>``); anything else is a compile error at
    the try site naming both types. On ok the expression yields the payload
    ``T`` (arity 2); an error-only ``result<E>`` yields no value, so the bare
    form over one is legal only as a whole expression statement
    (``try f();``, the propagate-or-continue consumer).

    Attributes:
        value: The tested operand (must evaluate to a ``result``).
        line: Source line for diagnostics.
    """

    value: object
    line: int


@dataclass
class TryFallback:
    """A ``try expr ?? fallback``: discard the error, supply a default.

    On error the error value is discarded and the fallback is evaluated --
    lazily, only on that path -- and coerced to ``T``; on ok the fallback
    never runs. Nothing escapes the expression, so the enclosing return type
    is never consulted (legal in ``main``). The fallback is the try's own
    clause, consumed by the try production itself, and its right-hand side is
    a greedy low-precedence expression -- ``??`` binds looser than the
    ternary and every binary operator, so ``try f() ?? 2 + 1`` is
    ``try f() ?? (2 + 1)`` and ``try f() ?? p ?? q`` is ``try f() ?? (p ?? q)``
    (the inner ``p ?? q`` a general :class:`Coalesce`); parenthesize to
    operate on the unwrapped value (``(try f() ?? 0) + 1``). A leading ``{``
    is instead an emit-block ``{ ...; emit v; }`` that may diverge. An
    error-only ``result<E>`` has no ok value to default, so it rejects.

    Attributes:
        value: The tested operand (must evaluate to a ``result``).
        fallback: The default expression (a ``BlockExpr`` for the block form).
        line: Source line for diagnostics.
    """

    value: object
    fallback: object
    line: int


@dataclass
class Coalesce:
    """A general ``lhs ?? rhs`` coalesce chain link.

    The ``??`` production outside a ``try``'s own fallback clause: it binds
    **looser** than the ternary and every binary operator (the
    lowest-precedence expression form, just above assignment) and chains
    **right**-associatively, so ``p ?? q + 1`` is ``p ?? (q + 1)`` and
    ``p ?? q ?? r`` is ``p ?? (q ?? r)``. Today every arm rejects at codegen:
    a ``result`` left of ``??`` unwraps through ``try``, and the pointer
    null-coalescing arm is reserved for the pointer-truthiness roadmap item;
    the production exists so the grammar is settled once.

    Attributes:
        lhs: The tested left operand.
        rhs: The fallback (a greedy low-precedence expression, or a
            ``BlockExpr`` for the emit-block form).
        line: Source line for diagnostics.
    """

    lhs: object
    rhs: object
    line: int


@dataclass
class TryStmt:
    """The ``try`` statement: ``try (ret = f()) { B } except (err) { H }``.

    Binds a fresh ``ret`` (no ``let``, the ``with``-head spelling) scoped to
    the block ``B``, which runs on ok; on error the handler ``H`` runs with
    ``err`` bound (scoped to ``H``) and is obligation-free -- it may fall
    through, diverge, or do nothing. There is no ``else`` arm: the block
    already is the no-error arm. Arity 2 only (an error-only ``result<E>``
    has no value to bind). Statement position disambiguates on
    ``try ( IDENT =``; anything else after a statement-position ``try`` is
    an expression statement.

    Attributes:
        name: The ok binding's name, scoped to ``body``.
        value: The tested head expression (must evaluate to a ``result``).
        body: The ok block's statements (``name`` in scope).
        binder: The error binding's name, scoped to ``handler``.
        handler: The handler block's statements.
        line: Source line for diagnostics.
    """

    name: str
    value: object
    body: list
    binder: str
    handler: list
    line: int


@dataclass
class Var:
    """A reference to a named variable, constant, or function.

    Attributes:
        name: The referenced name.
        line: Source line for diagnostics.
    """

    name: str
    line: int


@dataclass
class Call:
    """A call to a named function: ``name<type_args>(args)``.

    Attributes:
        name: The callee's name.
        type_args: Explicit generic arguments, e.g. ``sum<int32>(...)``.
        args: The argument expressions.
        line: Source line for diagnostics.
    """

    name: str
    type_args: list[TypeRef]
    args: list
    line: int


@dataclass
class CallExpr:
    """A call through a function-pointer expression: ``callee(args...)``.

    Attributes:
        callee: The expression yielding a function pointer.
        args: The argument expressions.
        line: Source line for diagnostics.
    """

    callee: object
    args: list
    line: int


@dataclass
class Logical:
    """A short-circuiting ``and`` / ``or``; the result is ``bool``.

    Attributes:
        op: The operator, ``"and"`` or ``"or"``.
        lhs: The left operand.
        rhs: The right operand, evaluated only when needed.
        line: Source line for diagnostics.
    """

    op: str
    lhs: object
    rhs: object
    line: int


@dataclass
class Ternary:
    """A conditional expression: ``cond ? then : otherwise``.

    Evaluates ``cond`` and yields one arm or the other, never both. The two
    arms must agree on a type: equal types are kept, an untyped constant arm
    adapts to the other's type (two untyped integer arms widen to the larger),
    and ``null`` adapts to a pointer arm. Unlike a statement ``if``, this is an
    expression, so it always produces a value.

    Attributes:
        cond: The condition expression (a bool or integer, as in ``if``).
        then: The expression yielded when ``cond`` is true.
        otherwise: The expression yielded when ``cond`` is false.
        line: Source line for diagnostics.
    """

    cond: object
    then: object
    otherwise: object
    line: int


@dataclass
class Unary:
    """A unary operation: ``-``, ``!``, ``*`` (deref), or ``&`` (address-of).

    Attributes:
        op: The operator token.
        operand: The operand expression.
        line: Source line for diagnostics.
    """

    op: str
    operand: object
    line: int


@dataclass
class Cast:
    """An explicit conversion: ``value as type``.

    Attributes:
        value: The expression being converted.
        type_name: The target type.
        line: Source line for diagnostics.
    """

    value: object
    type_name: TypeRef
    line: int


@dataclass
class NonnullAssert:
    """A postfix non-null assertion: ``p!``.

    A purely static, zero-cost assertion that a pointer expression is
    non-null -- the escape hatch into a ``@nonnull`` parameter slot. It
    evaluates to its operand unchanged (no code is emitted); asserting a
    pointer that is actually null is undefined behavior.

    Attributes:
        operand: The pointer expression being asserted.
        line: Source line for diagnostics.
    """

    operand: object
    line: int


@dataclass
class Asm:
    """An inline-assembly expression: ``@asm(in0, ...) [-> type] { "line"... }``.

    The body is one or more bare string literals -- one instruction each --
    joined with newlines into ``template``. Operands are written ``$out`` for
    the single output (present iff ``out_type`` is set) and ``$0``, ``$1``, ...
    for the inputs in order; the compiler rewrites them to LLVM's operand
    numbering and builds the constraint string.

    Attributes:
        template: The asm template, body lines joined with ``\\n``.
        inputs: The input operand expressions, in order.
        out_type: The output/return type, or ``None`` for a void asm statement.
        line: Source line for diagnostics.
        clobbers: Registers and flags clobbered by the asm (e.g. ``"memory"``,
            ``"cc"``, ``"x0"``), from an optional ``@clobbers(...)`` clause.
    """

    template: str
    inputs: list
    out_type: TypeRef | None
    line: int
    clobbers: list[str] = field(default_factory=list)


@dataclass
class SizeOf:
    """A ``sizeof(type)`` expression -- a compile-time ``uint64``.

    Attributes:
        type_name: The type whose size is taken.
        line: Source line for diagnostics.
    """

    type_name: TypeRef
    line: int


@dataclass
class AlignOf:
    """An ``alignof(type)`` expression -- a compile-time ``uint64``.

    Like ``sizeof``, a bare name in scope is taken as that variable, so
    ``alignof(v)`` is the alignment of ``v``'s type.

    Attributes:
        type_name: The type whose alignment is taken.
        line: Source line for diagnostics.
    """

    type_name: TypeRef
    line: int


@dataclass
class OffsetOf:
    """An ``offsetof(struct S, field)`` expression -- a compile-time ``uint64``.

    The byte offset of ``field`` within struct ``S``, honoring padding,
    ``@packed``, and ``@align``.

    Attributes:
        type_name: The struct type.
        field: The field name whose offset is taken.
        line: Source line for diagnostics.
    """

    type_name: TypeRef
    field: str
    line: int


@dataclass
class TypeName:
    """A ``typename(type)`` expression -- a compile-time string literal.

    Folds to the operand type's canonical spelling -- the same ``str(LangType)``
    string the ``any`` tags hash -- emitted as an ordinary deduplicated rodata
    string literal (a ``char*``). Like ``sizeof``, a bare name that is a
    variable in scope is taken as that variable, so ``typename(v)`` names
    ``v``'s static type; the operand is never evaluated. A top-level ``const``
    strips, matching what boxing does with tags.

    Attributes:
        type_name: The type (or in-scope variable) whose name is taken.
        line: Source line for diagnostics.
    """

    type_name: TypeRef
    line: int


@dataclass
class Len:
    """A ``len(expression)`` yielding an array's element count or a tuple's arity.

    Attributes:
        operand: The array or tuple expression whose length is taken.
        line: Source line for diagnostics.
    """

    operand: object
    line: int


@dataclass
class Index:
    """An index expression: ``base[index]``.

    Attributes:
        base: The array or pointer expression.
        index: The index expression.
        line: Source line for diagnostics.
    """

    base: object
    index: object
    line: int


@dataclass
class Slice:
    """A sub-slice expression: ``base[start:end]``.

    Either bound may be omitted: ``start`` defaults to 0 and ``end`` to the
    receiver's length, so ``s[1:]``, ``s[:2]``, and ``s[:]`` all parse here.
    Deliberately a separate node from :class:`Index`: a slice is always an
    rvalue (a sub-slice is a view, a tuple slice a copied value), so every
    lvalue surface (assignment targets, compound assignment, ``&``) excludes
    it by not matching this node.

    Attributes:
        base: The sliced expression (a ``slice<T>`` or ``tuple<...>`` value).
        start: The start-bound expression, or ``None`` when omitted.
        end: The end-bound expression, or ``None`` when omitted.
        line: Source line for diagnostics.
    """

    base: object
    start: object | None
    end: object | None
    line: int


@dataclass
class Member:
    """A field access: ``base.field`` or ``base->field``.

    Attributes:
        base: The struct value or pointer expression.
        field: The field name read.
        arrow: ``True`` for ``->`` (through a pointer), ``False`` for ``.``.
        line: Source line for diagnostics.
    """

    base: object
    field: str
    arrow: bool
    line: int


@dataclass
class EnumAccess:
    """A scoped enum member: ``Enum::Member``.

    Resolves to the member's compile-time constant value, typed as the enum's
    underlying type.

    Attributes:
        enum: The enum's name.
        member: The member name.
        line: Source line for diagnostics.
    """

    enum: str
    member: str
    line: int


@dataclass
class Binary:
    """A binary operation: arithmetic, bitwise, shift, or comparison.

    Attributes:
        op: The operator token.
        lhs: The left operand.
        rhs: The right operand.
        line: Source line for diagnostics.
    """

    op: str
    lhs: object
    rhs: object
    line: int
