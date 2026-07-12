"""Core type system for code generation.

The ``LangType``/``TypedValue`` pairing that tracks source-level types alongside
their LLVM representation, plus the type constructors, predicates, layout
(size/alignment/offset) computations, and compile-time integer folding helpers
shared across code generation.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace as dataclasses_replace

from llvmlite import ir

from mcc.errors import LangError
from mcc.nodes import TypeRef


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
        union: ``True`` for a ``union`` -- an aggregate whose fields all share
            one storage at offset 0 (see :func:`is_union`); excluded from
            equality/hash like the other layout attributes (the interned name
            is the identity).
        elem_indices: LLVM element index of each field; padding elements in an
            explicitly laid-out struct shift these. All zeros for a union,
            whose member access bypasses field indices (a pointer cast to the
            member type instead of a GEP).
        signature: ``(return type, param types, variadic)`` for a
            function-pointer type; part of equality, so structurally equal
            function types match.
        nonnull: Indices of the ``@nonnull`` parameters of a function-pointer
            type -- the per-parameter non-null contract a function value
            carries, checked at calls through the value. Spelled into the
            interned ``name`` (so two function types differing only in the
            contract are distinct), and excluded from equality/hash like the
            other derived attributes -- the name is the identity.
        mutref: Indices of the ``mut`` parameters of a function-pointer type --
            passed as a pointer to the caller's storage, so a call through the
            value enforces the same writable-lvalue rules as a direct call.
            Spelled into the ``name`` (identity) and reflected in the LLVM
            parameter type (a pointer); excluded from equality/hash like
            ``nonnull``.
        constref: Indices of the ``const``-aggregate parameters of a
            function-pointer type -- passed by hidden reference like ``mut``,
            but read-only. Spelled into the ``name`` and reflected in the LLVM
            parameter type; excluded from equality/hash like ``nonnull``.
        mutret: ``True`` for a function-pointer type with a ``mut`` return
            (``fn(...) -> mut T``) -- the call returns a pointer to
            caller-reachable storage, so a call through the value is an
            lvalue expression, exactly like a direct call to a
            ``-> mut`` function. Spelled into the ``name`` (identity) and
            reflected in the LLVM return type (a pointer); excluded from
            equality/hash like ``nonnull``.
        element: Element type of a fixed-size array, else ``None``.
        count: Length of a fixed-size array, else ``None``.
        const: ``True`` for a read-only ``const T`` view -- IR-identical to
            ``T``, but a distinct type whose lvalue cannot be assigned to. The
            element-mutability axis of a ``slice<const T>``.
        mutable: For a ``const`` type, its original mutable ``T`` (the exact
            interned object), so :func:`strip_const` restores it by identity --
            keeping the many ``is FLOAT64``/``is UINT8`` checks valid on a value
            read out of a ``const`` lvalue. ``None`` for a mutable type.
        base: The immediate ``extends`` base of a struct, resolved per
            instantiation, else ``None`` (a struct with no base, or a non-struct).
            Records the declared lineage the nominal subtype relation walks (see
            :meth:`CodeGen.nominal_subtype`); excluded from equality/hash like the
            other layout attributes -- the interned name is the identity.
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
    union: bool = field(default=False, compare=False)
    elem_indices: tuple | None = field(default=None, compare=False)
    signature: tuple | None = None
    nonnull: frozenset = field(default=frozenset(), compare=False)
    mutref: frozenset = field(default=frozenset(), compare=False)
    constref: frozenset = field(default=frozenset(), compare=False)
    mutret: bool = field(default=False, compare=False)
    element: "LangType | None" = None
    count: int | None = None
    const: bool = False
    mutable: "LangType | None" = field(default=None, compare=False)
    base: "LangType | None" = field(default=None, compare=False)

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
        decayed: The original fixed-size array type when this value is the
            pointer an array decayed to (see :meth:`CodeGen.value_at`), else
            ``None``. Boxing into an ``any`` consults it so a decayed array is
            rejected by its array type instead of silently boxing the pointer.
        lvalue: For the result of a ``mut``-returning call, the returned
            storage address (the raw pointer the call yielded; ``value``
            holds the eagerly loaded pointee). ``None`` for everything else.
            The lvalue surfaces -- assignment, projection, re-lending as a
            ``mut`` argument -- consume it; value contexts use ``value`` and
            the unused load folds away.
    """

    value: ir.Value
    type: LangType
    adaptable: bool = False
    decayed: "LangType | None" = None
    lvalue: "ir.Value | None" = None


@dataclass
class BlockExprCtx:
    """The state an ``emit`` needs to fill in its block-expression's value.

    A block-expression is lowered like an inlined function: a result slot in
    the entry block, written by each ``emit`` and read once at the end, plus a
    continuation block every ``emit`` branches to. The slot and its type are
    created lazily on the first ``emit`` (the block's type is its emit type);
    later emits coerce to it.

    An ``except`` handler rides the same context with the slot and type
    *preset* (the handler fills the ok value's slot, whose type the tested
    result fixes), so its emits coerce to the known type -- and adapted
    literals route -- instead of fixing one lazily.

    Attributes:
        cont_bb: The continuation block; every ``emit`` branches here.
        defer_depth: The ``defer_stack`` depth on entry, so an ``emit`` unwinds
            exactly the block's own deferred scopes.
        slot: The result alloca, or ``None`` until the first ``emit``.
        type: The block's value type, or ``None`` until the first ``emit``.
        emitted: Whether any ``emit`` targeted this context -- whether the
            continuation block is reachable from the handler's arm (an
            ``except`` in value position needs to know; a plain block
            expression reads ``type`` instead).
        no_value: For a statement-position ``except`` over a ``result<E>``:
            the result type's name, so an ``emit`` in the handler rejects
            (there is no ok value to fall back to). ``None`` everywhere else.
    """

    cont_bb: ir.Block
    defer_depth: int
    slot: object = None
    type: "LangType | None" = None
    emitted: bool = False
    no_value: "str | None" = None


@dataclass
class EnumType:
    """A resolved ``enum`` (or ``error``): its underlying type and folded members.

    A declared ``error`` type rides the same record: ``underlying`` is then the
    error's own nominal ``LangType`` (see :func:`is_error_decl`) rather than a
    plain integer type, so the name resolves to the nominal type and each
    member carries it.

    Attributes:
        underlying: The ``LangType`` the enum aliases and its members carry.
        members: Member name -> its folded constant ``TypedValue``.
        private: ``@private`` -- usable only within ``source``.
        source: The file the enum was declared in.
        displays: ``{variant name: display string}`` for an error declaration's
            ``NAME = "display"`` variants (stored for the planned rendering
            stage); empty otherwise.
        display_name: The error declaration's user-written name, before any
            ``@static`` salting -- the qualifier ``error_name`` prefixes each
            variant with (``my_error::NOT_FOUND``). ``None`` for a plain enum.
    """

    underlying: LangType
    members: dict
    private: bool
    source: "str | None"
    displays: dict = field(default_factory=dict)
    display_name: "str | None" = None


@dataclass
class Alias:
    """A resolved ``type`` alias: its target type plus visibility.

    A generic alias also carries its type-parameter list and their defaults;
    a use site binds the parameters, then resolves the target through them
    (the alias stays transparent -- no instance of its own is minted).

    Attributes:
        target: The aliased ``TypeRef``, resolved lazily on each use.
        private: ``@private`` -- usable only within ``source``.
        source: The file the alias was declared in.
        line: The line the alias was declared on -- where its target
            resolves, for diagnostics and instantiation backtraces.
        type_params: Generic type parameters, e.g. the ``T`` in ``entry<T>``.
            Empty for a plain alias.
        type_param_defaults: ``{type parameter: TypeRef}`` for parameters
            declared ``<T = type>``.
    """

    target: TypeRef
    private: bool
    source: "str | None"
    line: int
    type_params: list = field(default_factory=list)
    type_param_defaults: dict = field(default_factory=dict)


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


def const_of(lang_type: LangType) -> LangType:
    """Return the read-only ``const T`` form of a type (idempotent).

    The result is IR-identical to ``lang_type`` -- ``const`` is a source-level
    distinction only -- but a distinct ``LangType`` whose lvalues are not
    assignable. Used for the element of a ``slice<const T>``.

    Args:
        lang_type: The type to qualify.

    Returns:
        The ``const`` form, or ``lang_type`` unchanged if already ``const``.
    """
    if lang_type.const:
        return lang_type
    return dataclasses_replace(
        lang_type, name=f"const {lang_type.name}", const=True, mutable=lang_type
    )


def strip_const(lang_type: LangType) -> LangType:
    """Return the mutable form of a type, dropping any ``const`` qualifier.

    A value loaded out of a ``const`` lvalue is an independent copy, so it sheds
    the read-only qualifier; the loop variable of ``for x in slice<const T>`` is
    likewise a mutable copy. The result is the exact original mutable type (see
    :attr:`LangType.mutable`), so identity checks against the interned built-ins
    still hold.

    Args:
        lang_type: The type to unqualify.

    Returns:
        The mutable form, or ``lang_type`` unchanged if not ``const``.
    """
    return lang_type.mutable if lang_type.const else lang_type


def function_type(
    ret: LangType,
    params: tuple,
    variadic: bool = False,
    nonnull: frozenset = frozenset(),
    mutref: frozenset = frozenset(),
    constref: frozenset = frozenset(),
    mutret: bool = False,
) -> LangType:
    """Build a function-pointer type, e.g. ``fn(int32, int32) -> int32``.

    Its LLVM type is a pointer to the LLVM function type, so a value of it is
    callable directly.

    The one canonicalization point for the parameter qualifiers: a ``const``
    qualifier on a parameter type is stripped here -- on an aggregate it
    records the hidden-reference index (the calling convention it implies),
    on a scalar it simply drops (``fn(const int32)`` *is* ``fn(int32)``: a
    by-value copy is read-only to the caller either way). Both producers --
    a spelled ``fn(...)`` type resolving possibly-``const`` parameter types,
    and a function value deriving the sets from a declaration's registries --
    funnel through here, so the two spellings build one identical type.

    Args:
        ret: The return type.
        params: The parameter types, in order.
        variadic: Whether the function takes C-style varargs.
        nonnull: Indices of ``@nonnull`` parameters -- the non-null contract
            the type carries, spelled into its name (so
            ``fn(@nonnull char*) -> void`` is distinct from its plain form)
            and checked at calls through a value of the type.
        mutref: Indices of ``mut`` parameters -- passed as a pointer to the
            caller's own storage, spelled into the name and the LLVM type.
        constref: Indices of ``const`` aggregate parameters -- passed by
            hidden (read-only) reference; unioned with the indices derived
            from ``const``-qualified aggregate types in ``params``.
        mutret: ``True`` for a ``-> mut`` return -- the call returns a
            pointer to the returned storage (never erased: a ``mut`` return
            always changes the return convention), spelled into the name and
            reflected in the LLVM return type.

    Returns:
        A ``LangType`` for the function-pointer type.
    """
    stripped = []
    derived = set(constref)
    for i, p in enumerate(params):
        if p.const:
            p = strip_const(p)
            if is_aggregate(p):
                derived.add(i)  # a scalar's const drops instead (see above)
        stripped.append(p)
    params = tuple(stripped)
    constref = frozenset(derived)
    hidden = mutref | constref
    fnty = ir.FunctionType(
        ret.ir.as_pointer() if mutret else ret.ir,
        [p.ir.as_pointer() if i in hidden else p.ir for i, p in enumerate(params)],
        var_arg=variadic,
    )
    parts = [
        ("@nonnull " if i in nonnull else "")
        + ("mut " if i in mutref else "")
        + ("const " if i in constref else "")
        + p.name
        for i, p in enumerate(params)
    ]
    if variadic:
        parts.append("...")
    name = (
        "fn(" + ", ".join(parts) + ") -> "
        + ("mut " if mutret else "")
        + ret.name
    )
    return LangType(
        name,
        fnty.as_pointer(),
        signed=False,
        signature=(ret, params, variadic),
        nonnull=nonnull,
        mutref=mutref,
        constref=constref,
        mutret=mutret,
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

# `char` is a distinct one-byte text type: ABI-identical to uint8 (an unsigned
# i8), but a separate type, so a NUL-terminated string (char) is told apart from
# a raw byte buffer (uint8). Character/string literals are char/char[N], and a
# char[N] borrows to a slice<char> that drops the trailing NUL.
CHAR = LangType("char", ir.IntType(8), signed=False)
TYPES["char"] = CHAR

# `byte` is a transparent builtin alias for uint8 -- the raw one-byte unit of
# memory. It reads as intent at a raw-memory boundary (bytecopy, memcpy, the
# allocators) without being a distinct type: it resolves to the interned uint8
# object, so `byte` and `uint8` are interchangeable everywhere.
TYPES["byte"] = TYPES["uint8"]

INT32 = TYPES["int32"]
INT64 = TYPES["int64"]
UINT8 = TYPES["uint8"]
UINT64 = TYPES["uint64"]
# uint8* doubles as the "raw memory" pointer (C's void*/char*); any pointer
# implicitly coerces to it.
RAWPTR = pointer_to(TYPES["uint8"])
# char* -- the decayed form of a string literal; coerces to uint8* like any
# pointer, so it still serves the libc string functions.
CHARPTR = pointer_to(CHAR)
# The type of a bare `null`: a pointer that adapts to any pointer type.
NULLT = LangType("null", RAWPTR.ir, signed=False, pointee=TYPES["uint8"])

# `any` -- the builtin tagged box: `{ tag: uint64; payload: 16 bytes, align 8 }`,
# 24 bytes total, the payload sized so a slice (2 words) fits by value. Realized
# as a struct (so sizeof, by-value copies, and const-parameter hidden references
# reuse the struct machinery) and interned as this single constant: only the
# reserved-name resolution arm in `CodeGen.lang_type` hands it out, so identity
# (`t is ANY`) is the marker. The tag is the FNV-1a hash of the boxed type's
# canonical name (see :func:`fnv1a64`); the layout here, the GEP indices in
# the generator's boxing/`case type` code, and the constant word layout in
# `CodeGen.const_box_any` are the three sites of the layout invariant.
_ANY_PAYLOAD = list_of(UINT64, 2)
ANY = LangType(
    "any",
    ir.LiteralStructType([UINT64.ir, _ANY_PAYLOAD.ir]),
    signed=False,
)
object.__setattr__(ANY, "fields", (("tag", UINT64), ("payload", _ANY_PAYLOAD)))
object.__setattr__(ANY, "elem_indices", (0, 1))

# Builtin type names that are not in TYPES (they are generic or platform-
# resolved) but are still reserved, so a user struct cannot shadow them.
RESERVED_TYPE_NAMES = frozenset({"slice", "va_list", "any", "tuple", "result"})

POINTER_SIZE = 8  # bytes; native codegen targets 64-bit platforms


I32_ZERO = ir.Constant(ir.IntType(32), 0)


COMPARISON_OPS = ("==", "!=", "<", "<=", ">", ">=")


def is_integer(lang_type: LangType) -> bool:
    """Report whether a type is one of the ``intN``/``uintN`` types.

    Args:
        lang_type: The type to test.

    Returns:
        ``True`` for the sized integer types, but not ``bool`` (an ``i1``
        underneath), pointers, or a declared ``error`` type (``int32``-backed
        but nominal: arithmetic and implicit integer conversion must reject).
    """
    return (
        isinstance(lang_type.ir, ir.IntType)
        and lang_type is not BOOL
        and lang_type.pointee is None
        and lang_type.template is None
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


def is_flexible_array(lang_type: LangType) -> bool:
    """Report whether a type is a flexible array member's ``[0 x T]``.

    A trailing struct field ``field: T[]`` lowers to a zero-length array: it adds
    nothing to ``sizeof`` and decays to a ``T*`` at the struct's tail. A literal
    ``[0]`` size is rejected by the parser, so a zero count is an unambiguous
    marker for a flexible array member.

    Args:
        lang_type: The type to test.

    Returns:
        ``True`` for a zero-length array type.
    """
    return is_array(lang_type) and lang_type.count == 0


def is_aggregate(lang_type: LangType) -> bool:
    """Report whether a type is a named aggregate -- a struct or a union.

    Both carry a field list, so the field-generic behavior they share --
    by-value copies, ``sizeof``, ``const``-parameter hidden references, member
    lookup -- keys off this predicate. The narrower :func:`is_struct` (record
    only) and :func:`is_union` split the two where layout or the struct-only
    forms (``extends``, prefix upcast, sequential layout) diverge.

    Args:
        lang_type: The type to test.

    Returns:
        ``True`` if the type has a field list.
    """
    return lang_type.fields is not None


def is_struct(lang_type: LangType) -> bool:
    """Report whether a type is a record ``struct`` -- an aggregate, not a union.

    Deliberately excludes unions so a struct-only code path (sequential layout,
    ``extends``, prefix upcast) can never silently accept one; use
    :func:`is_aggregate` for behavior a union shares with a struct.

    Args:
        lang_type: The type to test.

    Returns:
        ``True`` if the type has a field list and is not a union.
    """
    return lang_type.fields is not None and not lang_type.union


def is_union(lang_type: LangType) -> bool:
    """Report whether a type is a ``union``.

    A union is an aggregate riding on the struct machinery (:func:`is_aggregate`
    is also true for it), so aggregate-generic behavior -- by-value copies,
    ``sizeof``, ``const``-parameter hidden references -- applies unchanged, while
    layout and member access branch on this predicate. It is *not* a
    :func:`is_struct` (that predicate is record-only).

    Args:
        lang_type: The type to test.

    Returns:
        ``True`` if the type is a union.
    """
    return lang_type.fields is not None and lang_type.union


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


def is_tuple(lang_type: LangType) -> bool:
    """Report whether a type is a builtin ``tuple<A, B, ...>`` product.

    A tuple is realized as an ordinary struct with positional field names
    ``"0"``, ``"1"``, ... (so member access, ``sizeof``, and by-value passing
    reuse the struct machinery), tagged with the reserved template name
    ``"tuple"`` that only :meth:`CodeGen.tuple_type` produces, so the name is
    an unambiguous marker.

    Args:
        lang_type: The type to test.

    Returns:
        ``True`` if the type is a ``tuple<A, B, ...>``.
    """
    return is_struct(lang_type) and lang_type.template == "tuple"


def is_result(lang_type: LangType) -> bool:
    """Report whether a type is a builtin ``result<T, E>`` / ``result<E>``.

    A result is realized as a struct (a tag plus its payload) so ``sizeof``,
    by-value passing, and ``const``-parameter hidden references reuse the
    struct machinery, tagged with the reserved template name ``"result"``
    that only :meth:`CodeGen.result_type` produces. Unlike a slice or tuple
    its fields are an internal layout, not a surface: member access rejects
    (see :meth:`CodeGen.struct_field`), keeping ``ok(...)``/``error(...)``
    the only producers.

    Args:
        lang_type: The type to test.

    Returns:
        ``True`` if the type is a ``result<...>``.
    """
    return is_struct(lang_type) and lang_type.template == "result"


def is_error_decl(lang_type: LangType) -> bool:
    """Report whether a type is a declared ``error`` type.

    An error type is nominal and ``int32``-backed: its IR is an ``i32`` but it
    is deliberately not an :func:`is_integer` (no arithmetic, no implicit
    conversion). Only :meth:`CodeGen.register_error` builds one, tagging it
    with the reserved template name ``"error"``; the missing field list tells
    it apart from a user struct that happens to be named ``error``.

    Args:
        lang_type: The type to test.

    Returns:
        ``True`` if the type is a declared error type.
    """
    return lang_type.template == "error" and lang_type.fields is None


def is_any(lang_type: LangType) -> bool:
    """Report whether a type is the builtin ``any`` box (or its ``const`` form).

    Only the reserved-name resolution arm hands out the interned :data:`ANY`
    constant, so identity is the marker; the ``const any`` form points back at
    it through :attr:`LangType.mutable`.

    Args:
        lang_type: The type to test.

    Returns:
        ``True`` if the type is ``any`` or ``const any``.
    """
    return lang_type is ANY or lang_type.mutable is ANY


def fnv1a64(name: str) -> int:
    """Hash a canonical type name to its 64-bit ``any`` tag (FNV-1a).

    Registry-free by design: a sequential whole-program registry would break
    under precompiled objects (a prebuilt ``.o``'s boxed ``any``\\ s would carry
    the producer's ids), while the hash is deterministic across compilations,
    folds to a constant, and lowers ``case type`` onto the integer-equality
    ``case`` codegen. In-compile collisions are detected and errored (see
    :meth:`CodeGen.any_tag`).

    Args:
        name: The canonical type name (its ``str(LangType)`` form).

    Returns:
        The 64-bit FNV-1a hash of the name's UTF-8 bytes.
    """
    tag = 0xCBF29CE484222325  # FNV-1a 64-bit offset basis
    for byte in name.encode("utf-8"):
        tag = ((tag ^ byte) * 0x100000001B3) % (1 << 64)  # FNV prime
    return tag


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
    if is_aggregate(lang_type):
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
    if not is_aggregate(lang_type):
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
    if is_union(lang_type):
        # Members share one storage: the largest, rounded to the alignment.
        largest = max((type_size(ftype) for _, ftype in lang_type.fields), default=0)
        align = type_align(lang_type)
        return (largest + align - 1) // align * align
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


def field_offset(struct_type: LangType, fname: str, line: int) -> int:
    """Compute the byte offset of a field within a struct, as ``offsetof`` reports.

    Walks the fields accumulating the same offsets :func:`type_size` does --
    padding each field to its alignment, except in a ``@packed`` struct where
    fields sit at consecutive bytes -- so the result matches the emitted layout.

    Args:
        struct_type: The struct type.
        fname: The field whose offset is wanted.
        line: Source line for diagnostics.

    Returns:
        The field's byte offset.

    Raises:
        LangError: When ``struct_type`` is not a struct or has no such field.
    """
    if not is_aggregate(struct_type):
        raise LangError(f"offsetof needs a struct, not {struct_type}", line)
    if is_result(struct_type):
        # A result's tag/payload layout is internal, not a surface.
        raise LangError(f"a {struct_type} has no fields", line)
    if is_union(struct_type):
        # Every member sits at offset 0; only validate that the field exists.
        if any(name == fname for name, _ in struct_type.fields):
            return 0
        raise LangError(f"struct {struct_type} has no field {fname!r}", line)
    offset = 0
    for name, ftype in struct_type.fields:
        if not struct_type.packed:
            align = type_align(ftype)
            offset = (offset + align - 1) // align * align
        if name == fname:
            return offset
        offset += type_size(ftype)
    raise LangError(f"struct {struct_type} has no field {fname!r}", line)


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
