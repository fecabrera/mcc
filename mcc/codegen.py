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
from dataclasses import dataclass, field, replace as dataclasses_replace

from llvmlite import ir

from mcc.errors import LangError
from mcc.nodes import (
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
    Cast,
    CharLit,
    Conditional,
    Const,
    Continue,
    Defer,
    Emit,
    EnumAccess,
    EnumDecl,
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
    NullLit,
    Program,
    Return,
    SizeOf,
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


@dataclass(frozen=True)
class LangType:
    """A source-level type paired with its LLVM representation.

    LLVM integer types carry no signedness, so ``signed`` and the optional
    fields below record the source-level distinctions (pointers, structs,
    arrays, function pointers) that the IR type alone does not. Struct types are
    interned per mangled name; the layout attributes marked ``compare=False``
    are excluded from equality/hash so recursive structs do not loop during
    comparison.

    Attributes:
        name: The source-level type name, e.g. ``"int32"`` or ``"list<T>*"``.
        ir: The corresponding ``llvmlite.ir`` type.
        signed: Whether an integer type is signed; meaningful only for ints.
        pointee: The pointed-to type for a pointer, else ``None``.
        template: Struct template name, used for unification.
        args: Struct type arguments.
        fields: ``(field name, LangType)`` pairs for a struct type, else
            ``None``; excluded from equality/hash.
        align: ``@align(N)`` override for a struct, else ``None``.
        packed: ``@packed`` -- fields at unpadded offsets, alignment 1.
        volatile: ``@volatile`` -- loads/stores must not be elided, merged, or
            reordered.
        elem_indices: LLVM element index of each field; padding elements in an
            explicitly laid-out struct shift these.
        signature: ``(return type, param types, variadic)`` for a
            function-pointer type; part of equality, so structurally equal
            function types match.
        element: Element type of a fixed-size array, else ``None``.
        count: Length of a fixed-size array, else ``None``.
    """

    name: str
    ir: ir.Type
    signed: bool = True
    pointee: "LangType | None" = None
    template: str | None = None
    args: tuple = ()
    fields: tuple | None = field(default=None, compare=False)
    align: int | None = field(default=None, compare=False)
    packed: bool = field(default=False, compare=False)
    volatile: bool = field(default=False, compare=False)
    elem_indices: tuple | None = field(default=None, compare=False)
    signature: tuple | None = None
    element: "LangType | None" = None
    count: int | None = None

    def __str__(self) -> str:
        """Return the type's source-level name."""
        return self.name


@dataclass
class TypedValue:
    """An evaluated expression: an LLVM value with its source-level type.

    Attributes:
        value: The LLVM value the expression produced.
        type: The value's ``LangType``.
        adaptable: ``True`` for constants without a definite type yet (bare
            integer literals, constant arithmetic on them, and ``null``); such
            values may still take on a compatible type, while everything else
            keeps its type unless explicitly cast.
    """

    value: ir.Value
    type: LangType
    adaptable: bool = False


@dataclass
class BlockExprCtx:
    """The state an ``emit`` needs to fill in its block-expression's value.

    A block-expression is lowered like an inlined function: a result slot in
    the entry block, written by each ``emit`` and read once at the end, plus a
    continuation block every ``emit`` branches to. The slot and its type are
    created lazily on the first ``emit`` (the block's type is its emit type);
    later emits coerce to it.

    Attributes:
        cont_bb: The continuation block; every ``emit`` branches here.
        defer_depth: The ``defer_stack`` depth on entry, so an ``emit`` unwinds
            exactly the block's own deferred scopes.
        slot: The result alloca, or ``None`` until the first ``emit``.
        type: The block's value type, or ``None`` until the first ``emit``.
    """

    cont_bb: ir.Block
    defer_depth: int
    slot: object = None
    type: "LangType | None" = None


@dataclass
class EnumType:
    """A resolved ``enum``: its underlying type and folded members.

    Attributes:
        underlying: The ``LangType`` the enum aliases and its members carry.
        members: Member name -> its folded constant ``TypedValue``.
        private: ``@private`` -- usable only within ``source``.
        source: The file the enum was declared in.
    """

    underlying: LangType
    members: dict
    private: bool
    source: "str | None"


@dataclass
class Alias:
    """A resolved ``type`` alias: its target type plus visibility.

    Attributes:
        target: The aliased ``TypeRef``, resolved lazily on each use.
        private: ``@private`` -- usable only within ``source``.
        source: The file the alias was declared in.
    """

    target: TypeRef
    private: bool
    source: "str | None"


def pointer_to(lang_type: LangType) -> LangType:
    """Build the pointer type ``T*`` for a type ``T``.

    Args:
        lang_type: The pointee type.

    Returns:
        A ``LangType`` for a pointer to ``lang_type``.
    """
    return LangType(
        lang_type.name + "*", lang_type.ir.as_pointer(), signed=False, pointee=lang_type
    )


def function_type(ret: LangType, params: tuple, variadic: bool = False) -> LangType:
    """Build a function-pointer type, e.g. ``fn(int32, int32) -> int32``.

    Its LLVM type is a pointer to the LLVM function type, so a value of it is
    callable directly.

    Args:
        ret: The return type.
        params: The parameter types, in order.
        variadic: Whether the function takes C-style varargs.

    Returns:
        A ``LangType`` for the function-pointer type.
    """
    fnty = ir.FunctionType(ret.ir, [p.ir for p in params], var_arg=variadic)
    name = "fn(" + ", ".join(p.name for p in params) + ") -> " + ret.name
    return LangType(
        name, fnty.as_pointer(), signed=False, signature=(ret, tuple(params), variadic)
    )


def list_of(element: LangType, count: int) -> LangType:
    """Build a fixed-size array type, e.g. ``int32[10]``.

    In value contexts an array decays to a pointer to its first element (see
    :meth:`CodeGen.value_at`).

    Args:
        element: The element type.
        count: The number of elements.

    Returns:
        A ``LangType`` for the fixed-size array.
    """
    return LangType(
        f"{element.name}[{count}]",
        ir.ArrayType(element.ir, count),
        signed=False,
        element=element,
        count=count,
    )


VOID = LangType("void", ir.VoidType())
BOOL = LangType("bool", ir.IntType(1), signed=False)
FLOAT64 = LangType("float64", ir.DoubleType())

TYPES = {"void": VOID, "bool": BOOL, "float64": FLOAT64}
for _width in (8, 16, 32, 64):
    TYPES[f"int{_width}"] = LangType(f"int{_width}", ir.IntType(_width), signed=True)
    TYPES[f"uint{_width}"] = LangType(f"uint{_width}", ir.IntType(_width), signed=False)

INT32 = TYPES["int32"]
INT64 = TYPES["int64"]
UINT8 = TYPES["uint8"]
UINT64 = TYPES["uint64"]
# uint8* doubles as the "raw memory" pointer (C's void*/char*); string
# literals have this type, and any pointer implicitly coerces to it.
RAWPTR = pointer_to(TYPES["uint8"])
# The type of a bare `null`: a pointer that adapts to any pointer type.
NULLT = LangType("null", RAWPTR.ir, signed=False, pointee=TYPES["uint8"])

# Builtin type names that are not in TYPES (they are generic or platform-
# resolved) but are still reserved, so a user struct cannot shadow them.
RESERVED_TYPE_NAMES = frozenset({"slice", "va_list"})

POINTER_SIZE = 8  # bytes; native codegen targets 64-bit platforms

# Compile-time facts about the target, exposed to source as the built-in
# integer constants TARGET_OS and TARGET_ARCH (see seed_target_consts). The
# OS_*/ARCH_* names below are also defined as constants, so code can compare
# `TARGET_OS == OS_DARWIN` to select platform-specific bindings. The numeric
# values are an ABI between the compiler and library code -- keep them stable.
TARGET_OS_VALUES = {
    "OS_UNKNOWN": 0,
    "OS_DARWIN": 1,
    "OS_LINUX": 2,
    "OS_WINDOWS": 3,
    "OS_NONE": 4,  # freestanding: bare metal, no operating system
}
TARGET_ARCH_VALUES = {
    "ARCH_UNKNOWN": 0,
    "ARCH_X86_64": 1,
    "ARCH_AARCH64": 2,
    "ARCH_RISCV64": 3,
}


def classify_os(triple: str) -> str:
    """Classify the OS component of an LLVM triple.

    Args:
        triple: The LLVM target triple.

    Returns:
        The ``OS_*`` name for the triple's operating system; a triple with no
        OS (e.g. ``aarch64-unknown-none-elf`` for bare metal) reports
        ``OS_NONE``.
    """
    if any(s in triple for s in ("darwin", "macos", "ios", "apple")):
        return "OS_DARWIN"
    if "linux" in triple:
        return "OS_LINUX"
    if any(s in triple for s in ("windows", "win32", "mingw", "msvc")):
        return "OS_WINDOWS"
    if "none" in triple:
        return "OS_NONE"
    return "OS_UNKNOWN"


def classify_arch(triple: str) -> str:
    """Classify the architecture component of an LLVM triple.

    Args:
        triple: The LLVM target triple.

    Returns:
        The ``ARCH_*`` name for the triple's architecture.
    """
    arch = triple.split("-", 1)[0]
    if arch in ("x86_64", "amd64"):
        return "ARCH_X86_64"
    if arch in ("aarch64", "arm64"):
        return "ARCH_AARCH64"
    if arch == "riscv64":
        return "ARCH_RISCV64"
    return "ARCH_UNKNOWN"


def target_fact_values(target: str | None) -> dict[str, int]:
    """The built-in target facts for a triple: ``TARGET_*`` and the enum names.

    Args:
        target: An LLVM target triple, or ``None`` for the host.

    Returns:
        A name -> value map of ``TARGET_OS``/``TARGET_ARCH`` plus every
        ``OS_*``/``ARCH_*`` constant.
    """
    triple = (target or _host_triple()).lower()
    values = {**TARGET_OS_VALUES, **TARGET_ARCH_VALUES}
    values["TARGET_OS"] = TARGET_OS_VALUES[classify_os(triple)]
    values["TARGET_ARCH"] = TARGET_ARCH_VALUES[classify_arch(triple)]
    return values


def compute_target_facts(
    target: str | None, defines: dict[str, int] | None = None
) -> dict[str, int]:
    """The facts an ``@if`` condition sees: the target facts plus ``-D`` defines.

    Used both by code generation and by the driver, which resolves conditional
    imports before code generation runs.

    Args:
        target: An LLVM target triple, or ``None`` for the host.
        defines: Command-line ``-D`` names mapped to integer values.

    Returns:
        A name -> value map combining the target facts and the defines.
    """
    return {**target_fact_values(target), **(defines or {})}


def eval_static_value(expr, facts: dict[str, int]) -> int:
    """Evaluate an ``@if`` condition to an integer against ``facts``.

    Only ``facts`` names (an undefined one reads as 0), integer/bool literals,
    comparisons, logical ``and``/``or``/``!``, and integer arithmetic are
    allowed -- nothing that needs the runtime.

    Args:
        expr: The constant expression to evaluate.
        facts: The target facts and ``-D`` defines in effect.

    Returns:
        The integer value of the expression.

    Raises:
        LangError: On a disallowed operator, division by zero, or a
            non-constant expression.
    """
    if isinstance(expr, IntLit) or isinstance(expr, CharLit):
        return expr.value
    if isinstance(expr, BoolLit):
        return int(expr.value)
    if isinstance(expr, Var):
        # A target fact or -D define resolves to its value; any other name is
        # false, as in C's #if -- so @if(FEATURE) with no -DFEATURE in effect
        # takes the @else branch instead of erroring.
        return facts.get(expr.name, 0)
    if isinstance(expr, Unary):
        v = eval_static_value(expr.operand, facts)
        if expr.op == "!":
            return int(not v)
        if expr.op == "-":
            return -v
        raise LangError(
            f"operator {expr.op!r} is not allowed in an @if condition", expr.line
        )
    if isinstance(expr, Logical):
        if expr.op == "and":
            return int(
                bool(eval_static_value(expr.lhs, facts))
                and bool(eval_static_value(expr.rhs, facts))
            )
        return int(
            bool(eval_static_value(expr.lhs, facts))
            or bool(eval_static_value(expr.rhs, facts))
        )
    if isinstance(expr, Binary):
        a = eval_static_value(expr.lhs, facts)
        b = eval_static_value(expr.rhs, facts)
        if expr.op in COMPARISON_OPS:
            return int(
                {
                    "==": a == b,
                    "!=": a != b,
                    "<": a < b,
                    "<=": a <= b,
                    ">": a > b,
                    ">=": a >= b,
                }[expr.op]
            )
        if expr.op in ("/", "%") and b == 0:
            raise LangError("division by zero in an @if condition", expr.line)
        ops = {
            "+": lambda: a + b,
            "-": lambda: a - b,
            "*": lambda: a * b,
            "/": lambda: int(a / b) if (a < 0) != (b < 0) else a // b,
            "%": lambda: a - b * (int(a / b) if (a < 0) != (b < 0) else a // b),
            "&": lambda: a & b,
            "|": lambda: a | b,
            "^": lambda: a ^ b,
            "<<": lambda: a << b,
            ">>": lambda: a >> b,
        }
        if expr.op in ops:
            return ops[expr.op]()
    if isinstance(expr, Ternary):
        chosen = expr.then if eval_static_value(expr.cond, facts) else expr.otherwise
        return eval_static_value(chosen, facts)
    raise LangError(
        "an @if condition must be a constant expression over the target facts",
        getattr(expr, "line", 0),
    )


def eval_static_cond(expr, facts: dict[str, int]) -> bool:
    """Whether an ``@if`` condition holds: its value is nonzero, as in C's #if.

    Args:
        expr: The condition expression.
        facts: The target facts and ``-D`` defines in effect.

    Returns:
        ``True`` when the condition evaluates nonzero.
    """
    return eval_static_value(expr, facts) != 0


I32_ZERO = ir.Constant(ir.IntType(32), 0)


# llvmlite.ir has no volatile flag on memory instructions, so patch the
# printed form; llvmlite renders modules to IR text before LLVM parses them,
# making the textual form authoritative.
class VolatileLoad(ir.LoadInstr):
    """A load instruction rendered with the ``volatile`` flag.

    llvmlite.ir has no volatile flag on memory instructions, so the printed IR
    text -- which is authoritative, as LLVM parses it -- is patched directly.
    """

    def descr(self, buf):
        """Append this instruction's IR text with ``load`` made volatile.

        Args:
            buf: The output buffer list llvmlite appends rendered text to.
        """
        inner: list[str] = []
        super().descr(inner)
        buf.append("".join(inner).replace("load ", "load volatile ", 1))


class VolatileStore(ir.StoreInstr):
    """A store instruction rendered with the ``volatile`` flag.

    The companion of :class:`VolatileLoad`; see its note on why the printed IR
    is patched.
    """

    def descr(self, buf):
        """Append this instruction's IR text with ``store`` made volatile.

        Args:
            buf: The output buffer list llvmlite appends rendered text to.
        """
        inner: list[str] = []
        super().descr(inner)
        buf.append("".join(inner).replace("store ", "store volatile ", 1))


def is_integer(lang_type: LangType) -> bool:
    """Report whether a type is one of the ``intN``/``uintN`` types.

    Args:
        lang_type: The type to test.

    Returns:
        ``True`` for the sized integer types, but not ``bool`` (an ``i1``
        underneath) or pointers.
    """
    return (
        isinstance(lang_type.ir, ir.IntType)
        and lang_type is not BOOL
        and lang_type.pointee is None
    )


def is_pointer(lang_type: LangType) -> bool:
    """Report whether a type is a pointer.

    Args:
        lang_type: The type to test.

    Returns:
        ``True`` if the type has a pointee.
    """
    return lang_type.pointee is not None


def is_function(lang_type: LangType) -> bool:
    """Report whether a type is a function-pointer type.

    Args:
        lang_type: The type to test.

    Returns:
        ``True`` if the type carries a function signature.
    """
    return lang_type.signature is not None


def is_array(lang_type: LangType) -> bool:
    """Report whether a type is a fixed-size array.

    Args:
        lang_type: The type to test.

    Returns:
        ``True`` if the type has an element type.
    """
    return lang_type.element is not None


def is_struct(lang_type: LangType) -> bool:
    """Report whether a type is a struct.

    Args:
        lang_type: The type to test.

    Returns:
        ``True`` if the type has a field list.
    """
    return lang_type.fields is not None


def is_slice(lang_type: LangType) -> bool:
    """Report whether a type is a builtin ``slice<T>`` view.

    A slice is realized as an ordinary struct (so field access and ``sizeof``
    reuse the struct machinery), tagged with the reserved template name
    ``"slice"`` that only :meth:`CodeGen.slice_type` produces, so the name is an
    unambiguous marker.

    Args:
        lang_type: The type to test.

    Returns:
        ``True`` if the type is a ``slice<T>``.
    """
    return is_struct(lang_type) and lang_type.template == "slice"


def is_valist(lang_type: LangType) -> bool:
    """Report whether a type is the platform ``va_list`` type.

    Only :meth:`CodeGen.valist` builds one and ``lang_type()`` reserves the
    name, so the name is an unambiguous marker.

    Args:
        lang_type: The type to test.

    Returns:
        ``True`` if the type is ``va_list``.
    """
    return lang_type.name == "va_list"


def _host_triple() -> str:
    """Return the host target triple.

    Used to pick the native ``va_list`` layout when no ``--target`` is given.
    The LLVM binding layer is imported lazily so codegen has no hard dependency
    on it when ``va_list`` is unused.

    Returns:
        The host's default LLVM target triple.
    """
    import llvmlite.binding as llvm

    return llvm.get_default_triple()


def type_align(lang_type: LangType) -> int:
    """Compute a type's alignment in bytes.

    Honors ``@packed`` (alignment 1, raised by ``@align``) and ``@align(N)``
    overrides, and takes a struct's alignment from its most-aligned field
    otherwise.

    Args:
        lang_type: The type to measure.

    Returns:
        The alignment in bytes.
    """
    if is_pointer(lang_type):
        return POINTER_SIZE
    if is_array(lang_type):
        return type_align(lang_type.element)
    if is_struct(lang_type):
        if lang_type.packed:
            return max(1, lang_type.align or 1)
        natural = max((type_align(ft) for _, ft in lang_type.fields), default=1)
        return max(natural, lang_type.align or 1)
    if isinstance(lang_type.ir, ir.IntType):
        return max(1, lang_type.ir.width // 8)
    return 8  # float64


def over_aligned(lang_type: LangType) -> bool:
    """Report whether a struct needs an explicitly spelled-out layout.

    True when a struct's alignment exceeds what LLVM would compute from its IR
    type alone -- an ``@align`` override, here or on a nested field -- so the
    layout must be laid out by hand (and allocas aligned manually) rather than
    left to LLVM's natural rules.

    Args:
        lang_type: The type to test.

    Returns:
        ``True`` for an over-aligned struct, ``False`` otherwise.
    """
    if not is_struct(lang_type):
        return False
    if lang_type.packed:  # its IR body is packed (alignment 1) already
        return (lang_type.align or 1) > 1
    return lang_type.align is not None or any(
        over_aligned(ftype) for _, ftype in lang_type.fields
    )


def type_size(lang_type: LangType) -> int:
    """Compute a type's size in bytes, as ``sizeof()`` reports it.

    Matches LLVM's natural layout on 64-bit targets, including struct padding.

    Args:
        lang_type: The type to measure.

    Returns:
        The size in bytes.
    """
    if is_pointer(lang_type):
        return POINTER_SIZE
    if is_array(lang_type):
        return lang_type.count * type_size(lang_type.element)
    if is_struct(lang_type):
        offset = 0
        for _, ftype in lang_type.fields:
            if not lang_type.packed:
                align = type_align(ftype)
                offset = (offset + align - 1) // align * align
            offset += type_size(ftype)
        align = type_align(lang_type)
        return (offset + align - 1) // align * align
    if isinstance(lang_type.ir, ir.IntType):
        return max(1, lang_type.ir.width // 8)
    return 8  # float64


COMPARISON_OPS = ("==", "!=", "<", "<=", ">", ">=")


def fold_int_arithmetic(op: str, a: int, b: int, lang_type: LangType) -> int | None:
    """Evaluate ``a op b`` at compile time, wrapped to the type's range.

    Uses C semantics: division truncates toward zero and ``>>`` is arithmetic
    for signed types.

    Args:
        op: The binary operator token (e.g. ``"+"``, ``"<<"``).
        a: The left operand value.
        b: The right operand value.
        lang_type: The result type, whose width and signedness fix the wrap.

    Returns:
        The folded value wrapped to ``lang_type``'s range, or ``None`` when it
        cannot fold (division by zero, an out-of-range shift).
    """
    width = lang_type.ir.width
    if op in ("/", "%") and b == 0:
        return None  # leave division by zero to the runtime instruction
    if op in ("<<", ">>") and not 0 <= b < width:
        return None  # out-of-range shifts are poison; leave to the runtime
    if op in ("+", "-", "*"):
        value = {"+": a + b, "-": a - b, "*": a * b}[op]
    elif op in ("/", "%"):
        quotient = abs(a) // abs(b) * (1 if (a >= 0) == (b >= 0) else -1)
        value = quotient if op == "/" else a - b * quotient
    else:
        # Python's >> is arithmetic; stored constants are already in the
        # type's range, so this matches ashr/lshr per signedness.
        value = {"&": a & b, "|": a | b, "^": a ^ b, "<<": a << b, ">>": a >> b}[op]
    if lang_type.signed:
        half = 1 << (width - 1)
        return (value + half) % (1 << width) - half
    return value % (1 << width)


def wrap_int(value: int, lang_type: LangType) -> int:
    """Wrap a Python integer into a type's range (two's complement).

    Mirrors what a narrowing or signedness-changing cast does at runtime.

    Args:
        value: The integer to wrap.
        lang_type: The target integer type whose width/signedness fix the wrap.

    Returns:
        ``value`` reduced to ``lang_type``'s representable range.
    """
    width = lang_type.ir.width
    if lang_type.signed:
        half = 1 << (width - 1)
        return (value + half) % (1 << width) - half
    return value % (1 << width)


def int_literal_type(value: int) -> LangType:
    """The default type of an untyped integer constant: the smallest that fits.

    An untyped integer literal (and constant arithmetic on such literals) takes
    the narrowest of ``int32``, ``int64``, ``uint64`` that holds its value, so a
    small literal like ``7`` stays ``int32`` -- matching C's ``int`` for varargs
    such as ``printf("%d", 7)`` -- while a value past ``int32`` widens instead of
    silently truncating. It remains adaptable, so it still coerces to any integer
    type its value fits.

    Args:
        value: The constant's value (already negated for a leading ``-``).

    Returns:
        ``INT32``, ``INT64``, or ``UINT64``.
    """
    if -(1 << 31) <= value < (1 << 31):
        return INT32
    if -(1 << 63) <= value < (1 << 63):
        return INT64
    return UINT64  # a top-bit-set 64-bit value (a mask, a high address)


def wider_int_type(a: LangType, b: LangType) -> LangType:
    """The integer type two adaptable operands widen to before combining.

    Picks the greater width; at equal width (``int64`` vs ``uint64``) it picks
    the unsigned one, which represents every value either side can hold.

    Args:
        a: One operand's type.
        b: The other operand's type.

    Returns:
        The wider of the two integer types.
    """
    if a.ir.width != b.ir.width:
        return a if a.ir.width > b.ir.width else b
    return a if not a.signed else b


def adaptable_int(value: int) -> "TypedValue":
    """An untyped integer constant tagged with its default type.

    See :func:`int_literal_type` for how the default width is chosen.

    Args:
        value: The constant's value.

    Returns:
        An adaptable ``TypedValue`` wrapping the value.
    """
    lang_type = int_literal_type(value)
    return TypedValue(ir.Constant(lang_type.ir, value), lang_type, adaptable=True)


def widen_to(tv: "TypedValue", target: LangType) -> "TypedValue":
    """Re-tag an adaptable integer constant with a wider type, staying adaptable.

    Used to bring two untyped operands of different default widths to a common
    type before folding, so neither narrows (which could overflow). The value is
    wrapped to ``target`` for the rare negative-into-unsigned case, matching a
    cast's two's-complement reinterpretation.

    Args:
        tv: The adaptable integer constant to widen.
        target: The wider type to re-tag it with.

    Returns:
        The same value as an adaptable constant of ``target``.
    """
    return TypedValue(
        ir.Constant(target.ir, wrap_int(tv.value.constant, target)),
        target,
        adaptable=True,
    )


def fold_untyped_shift(a: int, b: int) -> "TypedValue | None":
    """Fold ``a << b`` for an untyped-constant left operand as exact integer math.

    An untyped constant has no real width to overflow, so the shift is computed
    in full precision and the result picks its narrowest fitting type -- ``1 <<
    40`` is ``int64``, ``1 << 63`` is ``uint64``. This is why ``let x: uint64 =
    1 << 40;`` needs no cast.

    Args:
        a: The left operand's value.
        b: The shift amount.

    Returns:
        The widened constant, or ``None`` when ``b`` is negative or the result
        exceeds 64 bits (left to the normal shift handling, which errors).
    """
    if b < 0:
        return None
    shifted = a << b
    if -(1 << 63) <= shifted < (1 << 64):
        return adaptable_int(shifted)
    return None


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
        # symbol -> indices of params passed by hidden reference (const structs).
        self.hidden_ref: dict[str, frozenset[int]] = {}
        # Names of the current function's const (read-only) parameters.
        self.const_locals: set[str] = set()
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
        # name -> (private, source file); for @private access checks
        self.func_privacy: dict[str, tuple[bool, str | None]] = {}
        self.current_source: str | None = None  # file owning the code being generated
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
                elif isinstance(item, Import):
                    pass  # already merged by the driver
                else:
                    self.program.functions.append(item)

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
        mutate it, so sharing the storage is safe and avoids the copy.

        Args:
            func: The function whose parameters to classify.
            params: The resolved parameter ``LangType``s, in order.

        Returns:
            The set of by-reference parameter indices.
        """
        return frozenset(
            i
            for i, ((name, _), ptype) in enumerate(zip(func.params, params))
            if name in func.const_params and is_struct(ptype)
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
        alias = Alias(decl.target, decl.private, decl.source)
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

        Args:
            decl: The enum declaration to register.

        Raises:
            LangError: On a name clash, a duplicate member, or a member value
                that is not a constant of the underlying type.
        """
        self.current_source = decl.source
        underlying = (
            self.lang_type(decl.underlying, decl.line)
            if decl.underlying is not None
            else INT32
        )
        if underlying is VOID:
            raise LangError(f"enum {decl.name!r} cannot have a void type", decl.line)
        enum = EnumType(underlying, {}, decl.private, decl.source)
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
            base = function_type(ret, params)
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
            if len(ref.args) != len(decl.type_params):
                raise LangError(
                    f"struct {ref.name!r} expects {len(decl.type_params)} "
                    f"type argument(s), got {len(ref.args)}",
                    line,
                )
            args = tuple(self.lang_type(a, line) for a in ref.args)
            base = self.instantiate_struct(decl, args)
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
                base = self.lang_type(alias.target, line)
            finally:
                self.resolving_aliases.discard(ref.name)
                self.current_source = outer_source
        else:
            raise LangError(f"unknown type {ref.name!r}", line)
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
        return base_type

    def slice_type(self, element: LangType, line: int) -> LangType:
        """Build (and intern) the builtin ``slice<T>`` view type for ``element``.

        A slice is a 2-word, non-owning view ``{ ptr: T*; length: uint64 }`` over
        a contiguous run of ``T``. It is realized as an ordinary struct -- so
        field access, ``sizeof``, and by-value passing all reuse the struct
        machinery -- tagged with the reserved template name ``"slice"`` (see
        :func:`is_slice`). Instances are interned per element type, alongside the
        user structs in ``struct_types``.

        Args:
            element: The element type ``T``.
            line: Source line for diagnostics.

        Returns:
            The cached or newly built ``slice<T>`` ``LangType``.

        Raises:
            LangError: On ``slice<void>``.
        """
        if element is VOID:
            raise LangError("cannot make a slice of void", line)
        mangled = f"slice<{element}>"
        if mangled in self.struct_types:
            return self.struct_types[mangled]
        fields = (("ptr", pointer_to(element)), ("length", UINT64))
        identified = self.module.context.get_identified_type(mangled)
        identified.set_body(*(ftype.ir for _, ftype in fields))
        slice_t = LangType(
            mangled, identified, signed=False, template="slice", args=(element,)
        )
        object.__setattr__(slice_t, "fields", fields)  # frozen; excluded from eq
        object.__setattr__(slice_t, "elem_indices", (0, 1))
        self.struct_types[mangled] = slice_t
        return slice_t

    def instantiate_struct(
        self, decl: StructDecl, args: tuple[LangType, ...]
    ) -> LangType:
        """Return the struct instance for a set of type arguments.

        Creates the LLVM identified type and resolves field types on first use,
        registering the instance before resolving fields so self-referential
        structs (e.g. ``node<T>`` holding a ``node<T>*``) can refer to
        themselves. ``@packed``/``@align`` structs get an explicitly laid-out
        body with padding.

        Args:
            decl: The struct template declaration.
            args: The type arguments to instantiate with.

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
            )
            # Register before resolving fields so self-referential structs
            # (e.g. node<T> holding a node<T>*) can refer to themselves.
            self.struct_types[mangled] = struct_type
            fields = tuple(
                (fname, self.lang_type(ftype, decl.line))
                for fname, ftype in decl.fields
            )
        finally:
            self.resolving_bases.discard(mangled)
            self.type_bindings = outer
            self.current_source = outer_source
        if base_type is not None:
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
        specializations.
        """
        if base.fields is None or derived.fields is None:
            return False
        n = len(base.fields)
        return n <= len(derived.fields) and derived.fields[:n] == base.fields

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
        and function signatures (handling ``@extern``, ``@static``, and generic
        overload sets), and finally generates a body for every non-generic,
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
        # Type aliases are registered next (records only; resolved lazily on
        # use), so a const's or signature's type may name one.
        for alias in self.program.aliases:
            self.register_alias(alias)
        # Constants are folded before globals, so a global's type (or a later
        # const) may use one as an array size. They are evaluated in source
        # order, so a const may reference any declared earlier (as in C). The
        # built-in target facts were seeded above, so user consts may use them
        # (and may not shadow them).
        for const in self.program.consts:
            self.current_source = const.source
            if const.name in self.consts:
                raise LangError(f"constant {const.name!r} already defined", const.line)
            value = self.eval_const(const.value, const.line)
            if const.type_name is not None:
                declared = self.lang_type(const.type_name, const.line)
                value = self.const_coerce(
                    value, declared, const.line, f"const {const.name}"
                )
            self.consts[const.name] = value
            self.const_privacy[const.name] = (const.private, const.source)
        # Enums are registered after consts, so a member's value may use one;
        # they are folded in source order, so a member may reference any enum
        # member declared earlier (including earlier members of the same enum).
        for decl in self.program.enums:
            self.register_enum(decl)
        # An @static initializer may name a function (a constant function
        # pointer), so its evaluation waits until functions are declared below.
        deferred_static_inits = []
        for var in self.program.globals:
            self.current_source = var.source  # the type may name private structs
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
        declared: set[tuple[str | None, str]] = set()
        for func in self.program.functions:
            if func.extern:
                self.current_source = func.source  # signatures may name private structs
                ret = self.lang_type(func.ret_type, func.line)
                params = [self.lang_type(t, func.line) for _, t in func.params]
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
                    or func.name in self.globals
                ):
                    raise LangError(
                        f"function {func.name!r} already defined", func.line
                    )
                fnty = ir.FunctionType(
                    ret.ir, self.param_irs(params), var_arg=func.variadic
                )
                # @symbol overrides the linker name; mcc still calls it by func.name.
                self.funcs[func.name] = ir.Function(
                    self.module, fnty, name=func.symbol or func.name
                )
                self.signatures[func.name] = (ret, params, func.variadic)
                self.func_privacy[func.name] = (func.private, func.source)
                self.extern_decls.add(func.name)
                self.used_symbols.add(func.name)
                continue
            key = (func.source, func.name)
            is_overloadable = func.type_params and not func.static
            if not is_overloadable:
                if key in declared:
                    raise LangError(
                        f"function {func.name!r} already defined", func.line
                    )
                declared.add(key)
            self.current_source = func.source  # signatures may name private structs
            if func.static:
                self.symbol_bases[key] = self.static_base(func.name, func.source)
                if func.type_params:
                    self.static_templates[key] = func
                    continue
                symbol = self.symbol_bases[key]
                ret = self.lang_type(func.ret_type, func.line)
                params = [self.lang_type(t, func.line) for _, t in func.params]
                hidden = self.hidden_ref_indices(func, params)
                fnty = ir.FunctionType(
                    ret.ir, self.param_irs(params, hidden), var_arg=func.variadic
                )
                fn = ir.Function(self.module, fnty, name=symbol)
                self.link_shared(fn, func.source)
                self.mark_inline(fn, func)
                self.funcs[symbol] = fn
                self.signatures[symbol] = (ret, params, func.variadic)
                self.hidden_ref[symbol] = hidden
                self.static_funcs[key] = symbol
                continue
            if func.type_params:
                # Generic: no code yet -- instances are stamped out per call.
                # Several templates may share a name (an overload set).
                if func.name in self.funcs:
                    raise LangError(
                        f"function {func.name!r} already defined", func.line
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
            if (
                func.name in self.funcs
                or func.name in self.templates
                or func.name in self.globals
            ):
                raise LangError(f"function {func.name!r} already defined", func.line)
            self.func_privacy[func.name] = (func.private, func.source)
            self.used_symbols.add(func.name)
            ret = self.lang_type(func.ret_type, func.line)
            params = [self.lang_type(t, func.line) for _, t in func.params]
            hidden = self.hidden_ref_indices(func, params)
            fnty = ir.FunctionType(
                ret.ir, self.param_irs(params, hidden), var_arg=func.variadic
            )
            fn = ir.Function(self.module, fnty, name=func.name)
            self.link_shared(fn, func.source)
            self.mark_inline(fn, func)
            self.funcs[func.name] = fn
            self.signatures[func.name] = (ret, params, func.variadic)
            self.hidden_ref[func.name] = hidden
        # Functions are declared now, so @static initializers that reference one
        # can be folded to its address.
        for glob, var, var_type in deferred_static_inits:
            self.current_source = var.source
            glob.initializer = self.const_initializer(var.init, var_type, var.line)
        for func in self.program.functions:
            if not func.type_params and not func.extern:
                symbol = self.static_funcs.get((func.source, func.name), func.name)
                ret, params, _ = self.signatures[symbol]
                self.gen_function(func, self.funcs[symbol], ret, params)
        return self.module

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
        for i, ((pname, _), ptype, arg) in enumerate(zip(func.params, params, fn.args)):
            arg.name = pname
            if i in hidden:
                # The struct arrives as a pointer to the caller's storage; bind
                # the local straight to it (no copy). The const guarantee makes
                # sharing safe; reads go through it exactly like an alloca slot.
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

        slot = self.builder.alloca(struct_type.ir)
        if over_aligned(struct_type):
            slot.align = type_align(struct_type)
        self.builder.store(ir.Constant(struct_type.ir, None), slot)  # zero omitted fields
        for fname, tv, line in items:
            self.store_struct_field(slot, struct_type, fname, tv, line, "field")
        # Fill any omitted field that declares a default; the rest keep the zero.
        for fname, default_expr in self.struct_defaults(decl).items():
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
        addr = self.builder.gep(
            slot, [I32_ZERO, ir.Constant(ir.IntType(32), index)], inbounds=True
        )
        value = self.coerce(tv, ftype, line, f"{what} {fname!r}")
        self.builder.store(value.value, addr)

    def struct_defaults(self, decl) -> dict:
        """Collect a struct's default field values, including inherited ones.

        ``extends`` lays base fields first, so a derived struct's literal can
        rely on the base's defaults too; a derived default overrides a base one
        of the same name.

        Args:
            decl: The struct declaration, or ``None``.

        Returns:
            A ``{field name: default-value expression}`` map (empty when
            ``decl`` is ``None``).
        """
        if decl is None:
            return {}
        merged = {}
        if decl.base is not None:
            merged.update(self.struct_defaults(self.lookup_struct_decl(decl.base.name)))
        merged.update(decl.defaults)
        return merged

    def init_struct_defaults(self, slot, struct_type, ref, line: int):
        """Default-initialize a plainly-declared struct ``let s: struct T;``.

        When the struct declares any default field values (its own or inherited),
        zero the storage and store each default -- the same result as the empty
        literal ``struct T { }``. A struct with no defaults is left untouched, so
        it keeps the uninitialized behavior of a bare ``let``.

        Args:
            slot: The variable's alloca.
            struct_type: The resolved struct ``LangType``.
            ref: The declared ``TypeRef`` (its name finds the declaration).
            line: Source line for diagnostics.
        """
        defaults = self.struct_defaults(self.lookup_struct_decl(ref.name))
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
        field or explicit type argument has fixed the parameter. ``null`` never
        contributes either. A parameter left unbound is an error, just as the
        untyped ``let`` is ambiguous -- resolve it with explicit type args, a
        typed field value, or (eventually) a declared default.

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
        missing = [t for t in decl.type_params if t not in bindings]
        if missing:
            raise LangError(
                f"cannot infer type parameter(s) {', '.join(missing)} for struct "
                f"{decl.name!r} from its fields; specify them explicitly, e.g. "
                f"struct {decl.name}<int32> {{ ... }}",
                line,
            )
        args = tuple(bindings[t] for t in decl.type_params)
        return self.instantiate_struct(decl, args)

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
        # -- or it silently truncates at IR emission.
        if (
            tv.adaptable
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
                    self.init_struct_defaults(slot, declared, stmt.type_name, stmt.line)
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
        elif isinstance(stmt, Assign):
            if stmt.name in self.const_locals:
                raise LangError(
                    f"cannot assign to const parameter {stmt.name!r}", stmt.line
                )
            slot, var_type, volatile = self.var_addr(stmt.name, stmt.line)
            tv = self.coerce(
                self.gen_expr(stmt.value),
                var_type,
                stmt.line,
                f"assignment to {stmt.name}",
            )
            self.gen_store(tv.value, slot, volatile=volatile)
        elif isinstance(stmt, If):
            cond = self.gen_cond(stmt.cond)
            if stmt.otherwise:
                with self.builder.if_else(cond) as (then, otherwise):
                    with then:
                        self.gen_block(stmt.then)
                        then_diverged = self.builder.block.is_terminated
                    with otherwise:
                        self.gen_block(stmt.otherwise)
                        else_diverged = self.builder.block.is_terminated
                # When both arms diverge (return/emit/break), the merge block
                # the builder now sits in is unreachable; terminate it so the
                # statement counts as diverging too -- no trailing return needed.
                if then_diverged and else_diverged:
                    self.builder.unreachable()
            else:
                with self.builder.if_then(cond):
                    self.gen_block(stmt.then)
        elif isinstance(stmt, While):
            kind = "until" if stmt.until else "while"
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
            # Record the defer depth so break/continue unwind the body's defers.
            self.loops.append((cond_bb, end_bb, len(self.defer_stack)))
            try:
                self.gen_block(stmt.body)
            finally:
                self.loops.pop()
            if not self.builder.block.is_terminated:
                self.builder.branch(cond_bb)
            self.builder.position_at_end(end_bb)
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
        elif isinstance(stmt, StoreDeref):
            ptr = self.gen_expr(stmt.ptr)
            if not is_pointer(ptr.type):
                raise LangError(f"cannot dereference a {ptr.type}", stmt.line)
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

        Args:
            stmt: The ``For`` node to lower.

        Raises:
            LangError: When the iterable is not a struct, or its ``S_it`` /
                matching ``S_next`` is not in scope.
        """
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
        base = struct_t.template or struct_t.name
        it_name, next_name = f"{base}_it", f"{base}_next"
        if not self.callable_exists(it_name):
            raise LangError(
                f"'for ... in' needs a {it_name!r} function for {struct_t}; "
                "none is in scope",
                stmt.line,
            )
        # Pass the already-evaluated iterable through a hidden local so the
        # `_it` call (routed through normal overload/generic resolution) does not
        # re-evaluate the expression. The name cannot be a real identifier.
        src_slot = self.builder.alloca(iterable.type.ir, name="for.src")
        self.builder.store(iterable.value, src_slot)
        hidden = "0for.iterable"
        self.bind_local(hidden, src_slot, iterable.type)
        iterator = self.gen_call(Call(it_name, [], [Var(hidden, stmt.line)], stmt.line))
        del self.locals[hidden]
        self.scope_names.discard(hidden)
        it_slot = self.builder.alloca(iterator.type.ir, name="for.iter")
        self.builder.store(iterator.value, it_slot)

        next_fn, element = self.resolve_protocol_next(
            iterator.type, next_name, stmt.line
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

    def gen_for_slice(self, stmt: For, iterable: TypedValue, slice_t: LangType):
        """Lower ``for x in s { body }`` over a builtin ``slice<T>``.

        Unlike a library container, a slice iterates natively -- no
        ``_it``/``_next`` -- walking its ``ptr`` from index ``0`` up to
        ``length``::

            { let i = 0; let x: T;
              while (i < s.length) { x = s.ptr[i]; body; i = i + 1; } }

        The index counter and the slice's pointer/length are compiler-held
        temporaries; ``x`` lives in a fresh block scope, gone once the loop ends.
        A ``continue`` runs through the step block, so it still advances ``i``.

        Args:
            stmt: The ``For`` node to lower.
            iterable: The already-evaluated slice (or pointer(s) to one).
            slice_t: The iterable's ``slice<T>`` type, pointers stripped.
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
        element = slice_t.fields[0][1].pointee

        idx_slot = self.builder.alloca(UINT64.ir, name="for.idx")
        self.builder.store(ir.Constant(UINT64.ir, 0), idx_slot)

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
            step_bb = self.builder.append_basic_block("for.step")
            end_bb = self.builder.append_basic_block("for.end")
            self.builder.branch(cond_bb)
            self.builder.position_at_end(cond_bb)
            idx = self.builder.load(idx_slot)
            more = self.builder.icmp_unsigned("<", idx, length)
            self.builder.cbranch(more, body_bb, end_bb)
            self.builder.position_at_end(body_bb)
            elem_addr = self.builder.gep(ptr, [idx])
            self.builder.store(self.gen_load(elem_addr), x_slot)
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
        fn, ret, params = self.instantiate(func, bindings)
        if ret is not BOOL:
            raise LangError(f"{next_name!r} must return bool", line)
        if not is_pointer(params[1]):
            raise LangError(
                f"{next_name!r} second parameter must be an out-pointer", line
            )
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
        rhs = self.gen_cond(expr.rhs)
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
            return TypedValue(ir.Constant(UINT8.ir, expr.value), UINT8)
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
        all go through the pointer; every other type is loaded normally.

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
            return TypedValue(first, pointer_to(lang_type.element))
        return TypedValue(
            self.gen_load(addr, align=align, volatile=volatile, name=name), lang_type
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
            # A slice indexes through its `ptr` field into the borrowed run.
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
        index, ftype = self.struct_field(owner, fname, line)
        addr = self.builder.gep(
            base_addr, [I32_ZERO, ir.Constant(ir.IntType(32), index)], inbounds=True
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

    def gen_borrow_slice(self, value_expr, target: LangType, line: int) -> TypedValue:
        """Lower a borrow ``value as slice<T>`` into a non-owning view.

        A fixed array ``T[N]`` borrows to ``{first-element pointer, N}`` (read
        through its address, so the static length survives the array's usual
        decay). A ``uint8[N]`` is treated as a NUL-terminated string, so its
        borrow drops the trailing terminator -- length ``N - 1`` -- giving the
        text without the NUL (the buffer keeps it, so it still serves as a
        ``uint8*``). An owned ``list<T>`` -- any struct with a ``T*`` ``data``
        field and an integer ``length`` -- borrows to ``{data, length}``,
        dropping its ``capacity``. A ``slice<T>`` borrows to itself.

        Args:
            value_expr: The owned value being borrowed.
            target: The ``slice<T>`` view type to produce.
            line: Source line for diagnostics.

        Returns:
            The borrowed view as a ``TypedValue``.

        Raises:
            LangError: When the source cannot be borrowed as ``target`` (wrong
                shape) or its element type does not match ``T``.
        """
        element = target.fields[0][1].pointee
        # T[N] (or a string literal, a uint8[N]): take the first-element pointer
        # and the static length, which would otherwise decay away once the value
        # is read. Reached through the address, so the array type survives.
        src_t = self.lvalue_type(value_expr)
        if isinstance(value_expr, StrLit) or (src_t is not None and is_array(src_t)):
            addr, owner, _, _ = self.gen_addr(value_expr, line)
            if owner.element != element:
                raise LangError(
                    f"cannot borrow {owner} as {target}: element type is "
                    f"{owner.element}, not {element}",
                    line,
                )
            ptr = self.builder.gep(addr, [I32_ZERO, I32_ZERO], inbounds=True)
            # A uint8[N] is a NUL-terminated string: drop the terminator so the
            # view spans the text, not the trailing NUL.
            length = owner.count - 1 if element is UINT8 else owner.count
            return self.make_slice(target, ptr, ir.Constant(UINT64.ir, length))
        src = self.gen_expr(value_expr)
        owner, struct_val = src.type, src.value
        if is_pointer(owner) and is_struct(owner.pointee):
            owner = owner.pointee  # a list<T>* (or slice<T>*) borrows like the value
            struct_val = self.gen_load(src.value)
        if is_slice(owner):
            if owner.fields[0][1].pointee != element:
                raise LangError(f"cannot borrow {src.type} as {target}", line)
            return TypedValue(struct_val, target)
        if not is_struct(owner):
            raise LangError(
                f"cannot borrow {src.type} as {target}; borrow an owned list, "
                "an array, or a matching slice",
                line,
            )
        by_name = {name: i for i, (name, _) in enumerate(owner.fields)}
        if "data" not in by_name or "length" not in by_name:
            raise LangError(
                f"cannot borrow {src.type} as {target}; expected the data/length "
                "fields of an owned list",
                line,
            )
        data_t = owner.fields[by_name["data"]][1]
        length_t = owner.fields[by_name["length"]][1]
        if not is_pointer(data_t) or data_t.pointee != element:
            got = data_t.pointee if is_pointer(data_t) else data_t
            raise LangError(
                f"cannot borrow {src.type} as {target}: element type is "
                f"{got}, not {element}",
                line,
            )
        if not is_integer(length_t):
            raise LangError(
                f"cannot borrow {src.type} as {target}: 'length' is "
                f"{length_t}, not an integer",
                line,
            )
        ptr = self.builder.extract_value(struct_val, owner.elem_indices[by_name["data"]])
        length = self.builder.extract_value(
            struct_val, owner.elem_indices[by_name["length"]]
        )
        if length_t.ir.width < 64:
            length = self.builder.zext(length, UINT64.ir)
        elif length_t.ir.width > 64:
            length = self.builder.trunc(length, UINT64.ir)
        return self.make_slice(target, ptr, length)

    def string_data(self, text: str) -> bytearray:
        """The bytes a string literal occupies: its UTF-8 plus a NUL terminator.

        Args:
            text: The string contents.

        Returns:
            The NUL-terminated UTF-8 bytes.
        """
        return bytearray(text.encode("utf8") + b"\0")

    def string_array_type(self, text: str) -> LangType:
        """The ``uint8[N]`` array type a string literal denotes.

        ``N`` counts the trailing NUL, so the bytes stay a valid C string when
        the array decays to a ``uint8*``.

        Args:
            text: The string contents.

        Returns:
            The ``uint8[N]`` ``LangType``.
        """
        return list_of(UINT8, len(self.string_data(text)))

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
        """Emit a ``uint8*`` pointing at a string literal's bytes.

        Args:
            text: The string contents.

        Returns:
            A ``RAWPTR`` (``uint8*``) ``TypedValue`` to the string's first byte.
        """
        return TypedValue(
            self.builder.bitcast(self.string_global(text), RAWPTR.ir), RAWPTR
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

        The element type must be ``uint8`` and the array must be large enough to
        hold the literal's bytes (its NUL included); a larger ``uint8[M]`` is
        zero-filled past the string.

        Args:
            declared: The resolved array type the literal is bound to.
            text: The string contents.
            line: Source line for diagnostics.

        Returns:
            ``declared`` unchanged, once validated.

        Raises:
            LangError: When the element type is not ``uint8`` or the array is too
                small to hold the string.
        """
        if declared.element is not UINT8:
            raise LangError(
                f"a string literal initializes a uint8 array or a uint8*, "
                f"not a {declared}",
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
            LangError: When ``expr`` is not a constant of ``expected``.
        """
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
        if isinstance(expr, StrLit) and expected == RAWPTR:
            return self.const_string(expr.value)
        if isinstance(expr, StrLit) and is_array(expected):
            # A uint8[N] initialized from a string literal: the bytes inline,
            # zero-filled past the string (an oversize buffer).
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
            return TypedValue(ir.Constant(UINT8.ir, expr.value), UINT8)
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
            return TypedValue(ir.Constant(UINT8.ir, expr.value), UINT8)
        if isinstance(expr, FloatLit):
            return TypedValue(ir.Constant(FLOAT64.ir, expr.value), FLOAT64)
        if isinstance(expr, BoolLit):
            return TypedValue(ir.Constant(BOOL.ir, int(expr.value)), BOOL)
        if isinstance(expr, NullLit):
            return TypedValue(ir.Constant(RAWPTR.ir, None), NULLT, adaptable=True)
        if isinstance(expr, StrLit):
            return TypedValue(self.const_string(expr.value), RAWPTR)
        if isinstance(expr, SizeOf):
            size = type_size(self.lang_type(expr.type_name, line))
            return TypedValue(ir.Constant(UINT64.ir, size), UINT64)
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
        if (
            tv.adaptable
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
        # File-scoped (@static) names shadow the global namespace.
        key = (self.current_source, expr.name)
        if key in self.static_templates:
            return self.gen_generic_call(expr, [self.static_templates[key]])
        if key in self.static_funcs:
            return self.gen_direct_call(expr, self.static_funcs[key])
        if expr.name in self.templates:
            return self.gen_generic_call(expr, self.templates[expr.name])
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
        if symbol is None and name in self.funcs:
            private, source = self.func_privacy.get(name, (False, None))
            self.check_access(private, source, f"function {name!r}", line)
            symbol = name
        if symbol is None:
            return None
        if self.hidden_ref.get(symbol):
            # The function takes a const struct by hidden pointer, an ABI a
            # plain fn(struct) -> R pointer type cannot express.
            raise LangError(
                f"cannot take a function value of {name!r}: it has const struct "
                "parameters (passed by hidden reference)",
                line,
            )
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
        ret, params, variadic = self.signatures[symbol]
        args = self.marshal_args(
            expr.args,
            params,
            variadic,
            repr(expr.name),
            expr.line,
            self.hidden_ref.get(symbol, frozenset()),
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
    ) -> list:
        """Evaluate and coerce a call's arguments against the parameter types.

        Applies C varargs promotions (small integers and bools widen to
        ``int32``) past a variadic tail, and hands a ``va_list`` over in its
        platform-specific passed form.

        Args:
            arg_exprs: The argument expressions.
            params: The callee's parameter types.
            variadic: Whether the callee takes varargs.
            label: A description of the callee, for error messages.
            line: Source line for diagnostics.
            hidden: Indices of parameters passed by hidden reference (const
                structs), handed over as a pointer to the argument's storage.

        Returns:
            The marshalled LLVM argument values.

        Raises:
            LangError: On a wrong argument count, a coercion failure, or passing
                a struct to a variadic function.
        """
        if len(arg_exprs) < len(params) or (
            len(arg_exprs) > len(params) and not variadic
        ):
            raise LangError(
                f"{label} expects {len(params)} argument(s), got {len(arg_exprs)}", line
            )
        args = []
        for i, arg_expr in enumerate(arg_exprs):
            if i in hidden:
                args.append(
                    self.hidden_ref_arg(
                        arg_expr, params[i], line, f"argument {i + 1} of {label}"
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
        return args

    def hidden_ref_arg(
        self, arg_expr, ptype: LangType, line: int, context: str
    ) -> ir.Value:
        """Lower a hidden-reference (const struct) argument to a pointer.

        When the argument already has storage of the exact type, its address is
        shared directly -- no copy, which is the point of the optimization. An
        rvalue (or a type that still needs coercion, e.g. an ``extends`` upcast)
        is materialized into a temporary whose address is passed instead.

        Args:
            arg_expr: The argument expression.
            ptype: The parameter's (struct) type.
            line: Source line for diagnostics.
            context: A label for coercion error messages.

        Returns:
            A pointer to the argument's storage.
        """
        if self.is_addressable_form(arg_expr):
            addr, t, _, _ = self.gen_addr(arg_expr, line)
            if t.ir is ptype.ir:
                return addr
            tv = TypedValue(self.gen_load(addr), t)
        else:
            tv = self.gen_expr(arg_expr)
        return self.spill_to_temp(tv, ptype, line, context)

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
        arg_tvs = [self.gen_expr(arg) for arg in expr.args]
        if len(candidates) == 1:
            func = candidates[0]
            bindings = self.resolve_bindings(func, expr, arg_tvs, lenient=False)
        else:
            # Overload set: keep the viable candidates and pick the one with
            # the most specific parameter patterns (T* beats T, and so on).
            viable = []
            for func in candidates:
                bindings = self.resolve_bindings(func, expr, arg_tvs, lenient=True)
                if bindings is not None:
                    viable.append((self.specificity(func), func, bindings))
            if not viable:
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
        self.check_access(
            func.private, func.source, f"function {expr.name!r}", expr.line
        )
        for tparam, bound in bindings.items():
            if bound is VOID:
                raise LangError(
                    f"cannot bind type parameter {tparam} to {bound}", expr.line
                )
        fn, ret, params = self.instantiate(func, bindings)
        hidden = self.hidden_ref_indices(func, params)
        args = []
        for i, (tv, p) in enumerate(zip(arg_tvs, params)):
            context = f"argument {i + 1} of {expr.name!r}"
            if i in hidden:
                # The args are already lowered to values for binding inference;
                # a hidden-reference parameter takes a pointer, so spill to a
                # temporary (no shared-storage optimization on the generic path).
                args.append(self.spill_to_temp(tv, p, expr.line, context))
            else:
                args.append(self.coerce(tv, p, expr.line, context).value)
        return TypedValue(self.builder.call(fn, args), ret)

    def resolve_bindings(
        self, func: Func, expr: Call, arg_tvs: list[TypedValue], lenient: bool
    ) -> dict[str, LangType] | None:
        """Determine the type-parameter bindings for calling a generic function.

        Inference takes typed values first, then untyped constants (whose
        ``int32`` default should not win over a typed value bound to the same
        parameter); ``null`` carries no type information and never participates.
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
            if len(expr.type_args) != len(func.type_params):
                if lenient:
                    return None
                raise LangError(
                    f"{expr.name!r} expects {len(func.type_params)} type argument(s), "
                    f"got {len(expr.type_args)}",
                    expr.line,
                )
            for tparam, targ in zip(func.type_params, expr.type_args):
                bindings[tparam] = self.lang_type(targ, expr.line)
        try:
            for adaptable_pass in (False, True):
                strict = not adaptable_pass and not expr.type_args
                for (_, ptype), tv in zip(func.params, arg_tvs):
                    if tv.adaptable == adaptable_pass and tv.type is not NULLT:
                        self.unify(
                            ptype,
                            tv.type,
                            func.type_params,
                            bindings,
                            strict,
                            f"call to {expr.name!r}",
                            expr.line,
                        )
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
            for (_, ptype), tv in zip(func.params, arg_tvs):
                if not self.shape_matches(
                    ptype, tv.type, tv.adaptable, func.type_params, expr.line
                ):
                    return None
        return bindings

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
        self, func: Func, bindings: dict[str, LangType]
    ) -> tuple[ir.Function, LangType, list[LangType]]:
        """Return the monomorphized instance of a generic function.

        Generates (and caches) the instance for ``bindings`` on first use,
        registering it before emitting the body so recursive calls resolve. The
        instance gets mergeable linkage, like an imported definition.

        Args:
            func: The generic function template.
            bindings: The type-parameter bindings to instantiate with.

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
            # Register before generating the body so recursive calls resolve.
            self.funcs[mangled] = fn
            self.signatures[mangled] = (ret, params, False)
            self.hidden_ref[mangled] = hidden
            self.instances[key] = mangled
            self.gen_function(func, fn, ret, params)
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
        if lhs.type != rhs.type:
            ctx = f"operand of {expr.op!r}"
            both_int = is_integer(lhs.type) and is_integer(rhs.type)
            # An untyped constant operand adapts to the other side's type.
            if rhs.adaptable and not lhs.adaptable:
                rhs = self.coerce(rhs, lhs.type, expr.line, ctx)
            elif lhs.adaptable and not rhs.adaptable:
                lhs = self.coerce(lhs, rhs.type, expr.line, ctx)
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
                lhs = self.widen_operand(lhs, wide, expr.line, ctx)
                rhs = self.widen_operand(rhs, wide, expr.line, ctx)
            else:
                lhs = self.coerce(lhs, rhs.type, expr.line, ctx)
        op_type = lhs.type
        if expr.op in COMPARISON_OPS:
            if is_pointer(op_type) or is_function(op_type):
                if expr.op not in ("==", "!="):
                    raise LangError(
                        f"operator {expr.op!r} not supported for {op_type}", expr.line
                    )
                return TypedValue(
                    self.builder.icmp_unsigned(expr.op, lhs.value, rhs.value), BOOL
                )
            if isinstance(op_type.ir, ir.IntType):
                icmp = (
                    self.builder.icmp_signed
                    if op_type.signed
                    else self.builder.icmp_unsigned
                )
                return TypedValue(icmp(expr.op, lhs.value, rhs.value), BOOL)
            if op_type is FLOAT64:
                return TypedValue(
                    self.builder.fcmp_ordered(expr.op, lhs.value, rhs.value), BOOL
                )
        elif is_integer(op_type):
            # Fold constant operands so expressions like 10 * sizeof(int64)
            # remain constants (and can still adapt to other integer types).
            if isinstance(lhs.value, ir.Constant) and isinstance(
                rhs.value, ir.Constant
            ):
                if expr.op == "<<" and lhs.adaptable:
                    widened = fold_untyped_shift(lhs.value.constant, rhs.value.constant)
                    if widened is not None:
                        return widened
                folded = fold_int_arithmetic(
                    expr.op, lhs.value.constant, rhs.value.constant, op_type
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
            return TypedValue(ops[expr.op](lhs.value, rhs.value), op_type)
        elif op_type is FLOAT64 and expr.op != "%":
            ops = {
                "+": self.builder.fadd,
                "-": self.builder.fsub,
                "*": self.builder.fmul,
                "/": self.builder.fdiv,
            }
            return TypedValue(ops[expr.op](lhs.value, rhs.value), op_type)
        raise LangError(f"operator {expr.op!r} not supported for {op_type}", expr.line)
