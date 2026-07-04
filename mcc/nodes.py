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
    """

    name: str
    args: list["TypeRef"] = field(default_factory=list)
    stars: int = 0
    params: list["TypeRef"] | None = None  # set for fn(...) -> ret types
    ret: "TypeRef | None" = None
    dims: list = field(default_factory=list)  # array sizes, outermost first
    const: bool = False  # a leading `const` read-only qualifier
    variadic: bool = False  # a trailing `...` in a fn(...) type's parameters

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
            if self.args:
                text += "<" + ", ".join(str(a) for a in self.args) + ">"
        dims = "".join(f"[{render_dim(d)}]" for d in self.dims)
        return ("const " if self.const else "") + text + "*" * self.stars + dims


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
    """

    imports: list[tuple[str, int]]
    structs: list["StructDecl"]
    functions: list["Func"]
    globals: list["GlobalVar"]
    consts: list["Const"] = field(default_factory=list)
    conditionals: list["Conditional"] = field(default_factory=list)
    enums: list["EnumDecl"] = field(default_factory=list)
    aliases: list["TypeAlias"] = field(default_factory=list)
    directives: list["StaticAssert | ErrorDirective"] = field(
        default_factory=list
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
class EnumDecl:
    """An ``enum`` declaration: a named set of compile-time constants.

    ``enum Color: int32 { Red = 0, Green = 1, ... }`` introduces ``Color`` as a
    type aliasing its underlying type, plus a constant ``Color::Red`` of that
    type for each member. The underlying type may be any type; a member's value
    is any expression that folds to a constant of it.

    Attributes:
        name: The enum's name, usable both as a type and as the ``::`` scope.
        underlying: The underlying ``TypeRef``, or ``None`` to default to
            ``int32``.
        members: ``(name, value expression)`` pairs in declaration order.
        line: Source line for diagnostics.
        private: ``@private`` -- usable only within its source file.
        static: ``@static`` -- file-scoped name other files may reuse.
        source: Defining file, stamped by the driver.
    """

    name: str
    underlying: TypeRef | None
    members: list[tuple[str, object]]
    line: int
    private: bool = False
    static: bool = False
    source: str | None = None
    span: tuple[int, int] | None = field(default=None, compare=False)


@dataclass
class TypeAlias:
    """A ``type <name> = <type>;`` declaration -- a transparent type name.

    The alias is structural, not a new distinct type: ``type cb = fn(int32) ->
    int32;`` makes ``cb`` interchangeable with the function-pointer type it names.

    Attributes:
        name: The alias name, usable anywhere a type is.
        target: The aliased type.
        line: Source line for diagnostics.
        private: ``@private`` -- usable only within its source file.
        static: ``@static`` -- file-scoped name other files may reuse.
        source: Defining file, stamped by the driver.
        span: Source byte span, for the interface generator.
    """

    name: str
    target: TypeRef
    line: int
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
        deprecated_msg: The ``@deprecated("...")`` migration message, or
            ``None``. Every call site (and function-value use) emits a
            warning carrying it; the function stays callable.
        removed_msg: The ``@removed("...")`` migration message, or ``None``.
            The declaration is a tombstone: every call site (and
            function-value use) is a hard compile error carrying it. The
            signature is never resolved and a body, if any, is never
            generated.
        type_param_defaults: ``{type parameter: TypeRef}`` for parameters
            declared ``<T = type>``. The default fills a parameter that is
            neither given explicitly nor inferred from a *typed* argument.
            Defaults are trailing-only and may reference only earlier type
            parameters (both enforced at parse time).
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
    deprecated_msg: str | None = None
    removed_msg: str | None = None
    type_param_defaults: dict[str, TypeRef] = field(default_factory=dict)
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

    Attributes:
        name: The variable's name.
        type_name: Optional type annotation, inferred from ``value`` when
            ``None``.
        value: The initializer, or ``None`` for ``let x: T;`` (declared but
            uninitialized).
        line: Source line for diagnostics.
    """

    name: str
    type_name: TypeRef | None
    value: object | None
    line: int


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
class StructLit:
    """A struct literal ``struct Name[<args>] { field = expr, ... }``.

    Produces a struct value with the listed fields set and every omitted field
    zero-initialized.

    Attributes:
        type_ref: The struct type (its name and any generic arguments).
        fields: ``(field name, value expression)`` pairs, in source order.
        line: Source line for diagnostics.
    """

    type_ref: TypeRef
    fields: list
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
class Len:
    """A ``len(array expression)`` yielding the element count.

    Attributes:
        operand: The array expression whose length is taken.
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
