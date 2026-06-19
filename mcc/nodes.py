"""AST node classes produced by the parser and consumed by codegen."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class TypeRef:
    """A type as written in source.

    Captures a base name, its generic arguments, and pointer depth, so
    ``struct array<T>**`` is ``TypeRef("array", [TypeRef("T")], 2)``. A
    function-pointer type ``fn(A, B) -> R`` sets ``params`` and ``ret`` instead
    of ``args``; its ``name`` is ``"fn"`` and ``stars`` still applies, as in
    ``fn(...) -> R*``.

    Attributes:
        name: The base type name (``"fn"`` for a function-pointer type).
        args: Generic type arguments, e.g. the ``T`` in ``array<T>``.
        stars: Pointer depth -- the number of trailing ``*``.
        params: Parameter types for a ``fn(...) -> ret`` type, else ``None``.
        ret: Return type for a function-pointer type, else ``None``.
        dims: Fixed array sizes, outermost first, so ``int32[3][4]`` is
            ``dims=[3, 4]``. A ``None`` entry is an inferred ``[]`` (allowed
            only as the outermost dimension of an initialized array); a ``str``
            entry names an integer ``const`` resolved to its value during code
            generation.
    """

    name: str
    args: list["TypeRef"] = field(default_factory=list)
    stars: int = 0
    params: list["TypeRef"] | None = None  # set for fn(...) -> ret types
    ret: "TypeRef | None" = None
    dims: list[int | str | None] = field(default_factory=list)  # array sizes, outermost first

    def __str__(self) -> str:
        """Render the type back to its source spelling.

        Returns:
            The type as it would be written, including generic arguments,
            trailing ``*``, and array dimensions.
        """
        if self.params is not None:
            text = "fn(" + ", ".join(str(p) for p in self.params) + ") -> " + str(self.ret)
        else:
            text = self.name
            if self.args:
                text += "<" + ", ".join(str(a) for a in self.args) + ">"
        dims = "".join(f"[{'' if d is None else d}]" for d in self.dims)
        return text + "*" * self.stars + dims


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
    """

    imports: list[tuple[str, int]]
    structs: list["StructDecl"]
    functions: list["Func"]
    globals: list["GlobalVar"]
    consts: list["Const"] = field(default_factory=list)
    conditionals: list["Conditional"] = field(default_factory=list)


@dataclass
class StructDecl:
    """A ``struct`` type declaration.

    Attributes:
        name: The struct's name.
        type_params: Generic type parameters, e.g. the ``T`` in
            ``struct array<T>``.
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
        source: Defining file, stamped by the driver.
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
    source: str | None = None


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
        extern: ``@extern`` -- a declaration only; defined elsewhere.
        variadic: Extern only -- a trailing ``...`` for C-style varargs.
        inline: ``@inline`` -- emit with LLVM's ``alwaysinline`` so the body is
            inlined at every call site when optimizing.
        symbol: ``@symbol("...")`` -- the linker name, when not ``name``.
        source: Defining file, stamped by the driver.
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
    variadic: bool = False
    inline: bool = False
    symbol: str | None = None
    source: str | None = None


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
    """A character literal: ``'a'``, ``'\\n'``, ``'\\0'`` -- a one-byte ``uint8``.

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
