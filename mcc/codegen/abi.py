"""C-ABI classification for ``@extern`` struct passing (AArch64, x86-64).

mcc's own calls use a raw-aggregate convention (LLVM lays the struct out and
passes it whole, with ``const``/``mut`` parameters travelling by a hidden
reference). That convention is self-consistent but does *not* match the
platform C ABI, so a ``struct`` handed to or returned from an ``@extern`` C
function would not land where the C side expects it. This module classifies an
aggregate the way a C compiler does, so the ``@extern`` call boundary -- and
only that boundary -- speaks the C ABI.

Three ABIs are covered, dispatched on the target's (arch, os) by
:func:`classify_signature`:

* **AArch64/AAPCS64** (any OS) -- classified *context-free*: each aggregate on
  its own, independent of the registers earlier arguments consumed. This
  mirrors clang's ``AArch64ABIInfo``; the LLVM AArch64 backend performs the
  NGRN/NSRN/NSAA allocation (including spilling a register-class aggregate to
  the stack) from the coerced IR types, so the frontend needs no register
  accounting.
* **x86-64 System V** (non-Windows x86-64) -- eightbyte classification, then a
  left-to-right register-accounting pass. Unlike AAPCS64, SysV *does* require
  the frontend to replicate clang's accounting: the LLVM backend will not
  demote a register-class aggregate to memory when the argument registers run
  low (it would split a ``{i64,i64}`` one-register-one-stack, which is
  ABI-wrong), so :func:`classify_signature` reclassifies a register aggregate
  that does not fit the *remaining* registers to a ``byval`` memory argument.
* **x86-64 Windows (Win64)** -- classified context-free: an aggregate of 1, 2,
  4, or 8 bytes rides in a single integer register (Win64 gives aggregates no
  SSE, so a float-containing struct still uses a GPR), any other size goes
  indirectly (a plain pointer to a caller copy for an argument, ``sret`` for a
  return larger than 8 bytes).

Every other target (riscv64, unknown) is rejected by the caller before it
reaches here.

The classifier reports one of:

* :class:`Direct` -- pass/return the aggregate coerced to a small LLVM type
  that occupies registers.
* :class:`Indirect` -- the aggregate travels through memory: an argument is
  passed as a pointer to a caller-owned copy (``by_value`` distinguishes a
  plain pointer from a ``byval`` stack copy), and a return is written through a
  hidden ``sret`` pointer.

Sizes and alignments come from mcc's own :func:`type_size`/:func:`type_align`
(which honor ``@packed``/``@align``), never from the LLVM data layout -- under
the JIT the data layout is not reliably populated.
"""

from __future__ import annotations

from dataclasses import dataclass

import llvmlite.ir as ir

from mcc.codegen.targets import classify_arch, classify_os
from mcc.codegen.types import (
    LangType,
    field_offset,
    is_aggregate,
    is_array,
    is_struct,
    is_union,
    type_align,
    type_size,
)

# AAPCS64 passes an aggregate of 16 bytes or less in general-purpose registers;
# anything larger (that is not a homogeneous float aggregate) goes indirect.
GPR_LIMIT = 16
# A homogeneous float aggregate occupies up to four consecutive FP registers.
MAX_HFA_MEMBERS = 4

# x86-64 System V: six integer argument registers (rdi/rsi/rdx/rcx/r8/r9) and
# eight SSE argument registers (xmm0-7).
SYSV_INT_REGS = 6
SYSV_SSE_REGS = 8


@dataclass(frozen=True)
class Direct:
    """The aggregate is passed/returned in registers, coerced to ``coerce_ir``.

    Attributes:
        coerce_ir: The LLVM type the struct is bitcast through at the boundary
            -- ``i64`` or ``[2 x i64]`` for the AArch64 GPR case, ``[N x float]``
            / ``[N x double]`` for a homogeneous float aggregate, an ``iN`` for
            the Win64 integer form, or an ``i64``/``double``/``{i64,i64}``/
            ``{double,double}``/``{i64,double}``/``{double,i64}`` eightbyte
            coercion for x86-64 System V.
    """

    coerce_ir: ir.Type


@dataclass(frozen=True)
class Indirect:
    """The aggregate travels through memory (a pointer arg, or an ``sret`` return).

    Attributes:
        align: The aggregate's alignment, for the ``sret``/pointer slot.
        struct_ir: The aggregate's own LLVM struct/array type.
        by_value: When ``True`` (an x86-64 System V MEMORY argument), the
            argument is a ``byval(T) align N`` pointer -- the struct data is
            copied onto the argument stack rather than passed as a plain
            pointer. When ``False`` (AArch64 and Win64), it is a plain pointer
            to a caller-owned copy. Ignored for a return (always ``sret``).
    """

    align: int
    struct_ir: ir.Type
    by_value: bool = False


# --- AArch64 / AAPCS64 (context-free) -----------------------------------------


def _fp_members(lang_type: LangType) -> list[ir.Type] | None:
    """The fundamental floating members of an aggregate, in order, or ``None``.

    Walks a composite recursively -- through nested structs and fixed-size
    arrays -- collecting each leaf's LLVM type. Returns ``None`` the moment a
    non-floating leaf (an integer, a pointer) or a ``union`` is reached: a union
    overlays its members, so it can never be a homogeneous float aggregate.
    """
    if is_union(lang_type):
        return None
    if is_struct(lang_type):
        out: list[ir.Type] = []
        for _, ftype in lang_type.fields:
            sub = _fp_members(ftype)
            if sub is None:
                return None
            out.extend(sub)
        return out
    if is_array(lang_type):
        sub = _fp_members(lang_type.element)
        if sub is None:
            return None
        return sub * lang_type.count
    if isinstance(lang_type.ir, (ir.FloatType, ir.DoubleType)):
        return [lang_type.ir]
    return None


def _hfa(lang_type: LangType) -> tuple[ir.Type, int] | None:
    """Classify a homogeneous float aggregate, returning ``(base, count)``.

    An HFA/HVA is a composite whose every leaf member (recursively, arrays
    included) is the *same* fundamental floating type -- all ``float`` or all
    ``double`` -- with one to four such members. Returns ``None`` for anything
    else (a mixed aggregate, more than four members, or one with no float
    members at all).
    """
    members = _fp_members(lang_type)
    if not members or not (1 <= len(members) <= MAX_HFA_MEMBERS):
        return None
    base = members[0]
    if any(type(m) is not type(base) for m in members):
        return None
    return base, len(members)


def classify_aggregate(lang_type: LangType) -> Direct | Indirect:
    """Classify a struct/union aggregate for the AAPCS64 C boundary.

    The classification is the same whether the aggregate is an argument or a
    return value; the caller realizes an :class:`Indirect` differently for the
    two directions (a pointer-to-copy for an argument, an ``sret`` slot for a
    return).

    Args:
        lang_type: The aggregate ``LangType`` (a struct or union) to classify.

    Returns:
        A :class:`Direct` when the aggregate rides in registers, else an
        :class:`Indirect`.
    """
    hfa = _hfa(lang_type)
    if hfa is not None:
        base, count = hfa
        return Direct(ir.ArrayType(base, count))
    size = type_size(lang_type)
    if size <= GPR_LIMIT:
        if size <= 8:
            return Direct(ir.IntType(64))
        return Direct(ir.ArrayType(ir.IntType(64), 2))
    return Indirect(type_align(lang_type), lang_type.ir)


# --- x86-64 System V (eightbyte classification + register accounting) ---------

# The four SysV eightbyte classes we need (no X87/X87UP/COMPLEX_X87: mcc has no
# long double, __int128, or vectors, so those never arise).
_NO_CLASS = 0
_INTEGER = 1
_SSE = 2
_MEMORY = 3


def _merge_eightbyte(a: int, b: int) -> int:
    """Merge two SysV classes falling in the same eightbyte (post-merger rule)."""
    if a == b:
        return a
    if a == _NO_CLASS:
        return b
    if b == _NO_CLASS:
        return a
    if a == _MEMORY or b == _MEMORY:
        return _MEMORY
    if a == _INTEGER or b == _INTEGER:
        return _INTEGER
    return _SSE


def _sysv_leaves(lang_type: LangType, base: int):
    """Yield ``(offset, size, class)`` for each scalar leaf of an aggregate.

    Recurses structs (adding each field's offset), arrays (each element in
    turn), and unions (every member overlaid at the same offset). A floating
    leaf is ``_SSE``; an integer, pointer, or bool leaf is ``_INTEGER``.
    Offsets and sizes come from mcc's own layout, so ``@packed``/``@align`` are
    reflected.
    """
    if is_union(lang_type):
        for _, ftype in lang_type.fields:
            yield from _sysv_leaves(ftype, base)
        return
    if is_struct(lang_type):
        for name, ftype in lang_type.fields:
            yield from _sysv_leaves(ftype, base + field_offset(lang_type, name, 0))
        return
    if is_array(lang_type):
        esize = type_size(lang_type.element)
        for i in range(lang_type.count):
            yield from _sysv_leaves(lang_type.element, base + i * esize)
        return
    cls = _SSE if isinstance(lang_type.ir, (ir.FloatType, ir.DoubleType)) else _INTEGER
    yield base, type_size(lang_type), cls


def _sysv_eightbytes(lang_type: LangType) -> list[int] | None:
    """The SysV eightbyte classes of a register-class aggregate, or ``None``.

    Returns ``None`` when the aggregate is passed in memory: larger than two
    eightbytes, or a leaf straddles an eightbyte boundary (a ``@packed``/
    misaligned field), or any eightbyte classifies MEMORY. Otherwise a list of
    ``_INTEGER``/``_SSE`` per eightbyte (a padding-only eightbyte reads as
    ``_INTEGER``, matching clang's GPR pass of trailing padding).
    """
    size = type_size(lang_type)
    if size == 0 or size > GPR_LIMIT:
        return None
    n = (size + 7) // 8
    classes = [_NO_CLASS] * n
    for offset, sz, cls in _sysv_leaves(lang_type, 0):
        first = offset // 8
        last = (offset + sz - 1) // 8
        if last >= n:
            return None
        if first != last:
            # A leaf spanning two eightbytes is unaligned (only reachable via
            # @packed/@align); SysV puts an unaligned aggregate in memory.
            return None
        classes[first] = _merge_eightbyte(classes[first], cls)
    if any(c == _MEMORY for c in classes):
        return None
    return [_INTEGER if c == _NO_CLASS else c for c in classes]


def _sysv_coerce(classes: list[int]) -> ir.Type:
    """The register-coercion LLVM type for a list of SysV eightbyte classes."""
    parts = [ir.DoubleType() if c == _SSE else ir.IntType(64) for c in classes]
    return parts[0] if len(parts) == 1 else ir.LiteralStructType(parts)


def _sysv_classify_aggregate(lang_type: LangType) -> Direct | Indirect:
    """Classify one x86-64 System V aggregate, ignoring register accounting.

    A register-class aggregate becomes a :class:`Direct` with its eightbyte
    coercion; a MEMORY aggregate becomes an :class:`Indirect` (``by_value`` for
    an argument -- the caller decides ``sret`` for a return).
    """
    classes = _sysv_eightbytes(lang_type)
    if classes is None:
        return Indirect(type_align(lang_type), lang_type.ir, by_value=True)
    return Direct(_sysv_coerce(classes))


def _sysv_scalar_is_sse(lang_type: LangType) -> bool:
    """Whether a scalar parameter consumes an SSE register (a float/double)."""
    return isinstance(lang_type.ir, (ir.FloatType, ir.DoubleType))


def _sysv_signature(ret: LangType, params: list) -> tuple[list, object]:
    """Classify an x86-64 System V signature with register accounting.

    Returns ``(arg_classes, ret_class)``. Each aggregate parameter is
    eightbyte-classified, then a left-to-right pass threads the remaining
    integer/SSE argument registers: a register-class aggregate that does not fit
    the *remaining* registers is demoted whole to a ``byval`` memory argument
    (all-or-nothing -- SysV never straddles an aggregate across registers and
    stack). A hidden ``sret`` return pointer consumes the first integer
    register.
    """
    ret_class = _sysv_classify_return(ret) if is_aggregate(ret) else None
    int_used = 1 if isinstance(ret_class, Indirect) else 0
    sse_used = 0
    arg_classes: list = []
    for p in params:
        if not is_aggregate(p):
            if _sysv_scalar_is_sse(p):
                sse_used += 1
            else:
                int_used += 1
            arg_classes.append(None)
            continue
        cls = _sysv_classify_aggregate(p)
        if isinstance(cls, Direct):
            need_int, need_sse = _sysv_register_need(cls)
            if int_used + need_int <= SYSV_INT_REGS and sse_used + need_sse <= SYSV_SSE_REGS:
                int_used += need_int
                sse_used += need_sse
                arg_classes.append(cls)
            else:
                # Not enough registers remain: pass the whole aggregate in
                # memory (byval), never split across registers and the stack.
                arg_classes.append(Indirect(type_align(p), p.ir, by_value=True))
        else:
            arg_classes.append(cls)
    return arg_classes, ret_class


def _sysv_register_need(cls: Direct) -> tuple[int, int]:
    """The ``(neededInt, neededSSE)`` register count of a Direct SysV aggregate."""
    coerce = cls.coerce_ir
    parts = coerce.elements if isinstance(coerce, ir.LiteralStructType) else [coerce]
    int_need = sum(1 for p in parts if not isinstance(p, ir.DoubleType))
    sse_need = sum(1 for p in parts if isinstance(p, ir.DoubleType))
    return int_need, sse_need


def _sysv_classify_return(ret: LangType) -> Direct | Indirect:
    """Classify an x86-64 System V struct return.

    A register-class return is eightbyte-coerced (into rax/rdx and xmm0/xmm1 --
    at most two eightbytes, so it always fits); a MEMORY return uses ``sret``.
    """
    classes = _sysv_eightbytes(ret)
    if classes is None:
        return Indirect(type_align(ret), ret.ir)
    return Direct(_sysv_coerce(classes))


# --- x86-64 Windows (Win64, context-free) -------------------------------------

# Win64 passes/returns an aggregate whose size is exactly a power-of-two word
# (1/2/4/8 bytes) in a single integer register; any other size is indirect.
_WIN64_REG_SIZES = (1, 2, 4, 8)


def _win64_classify_aggregate(lang_type: LangType) -> Direct | Indirect:
    """Classify one Win64 aggregate (identical for an argument or a return).

    Size 1/2/4/8 rides in one integer register (Win64 gives aggregates no SSE,
    so a float-containing struct still uses a GPR), coerced to that ``iN``. Any
    other size is indirect: the caller realizes it as a plain pointer to a copy
    for an argument, or an ``sret`` pointer for a return over 8 bytes.
    """
    size = type_size(lang_type)
    if size in _WIN64_REG_SIZES:
        return Direct(ir.IntType(size * 8))
    return Indirect(type_align(lang_type), lang_type.ir)


def _win64_signature(ret: LangType, params: list) -> tuple[list, object]:
    """Classify a Win64 signature (each aggregate mapped independently)."""
    arg_classes = [
        _win64_classify_aggregate(p) if is_aggregate(p) else None for p in params
    ]
    ret_class = _win64_classify_aggregate(ret) if is_aggregate(ret) else None
    return arg_classes, ret_class


# --- dispatch -----------------------------------------------------------------


def abi_supported(target: str) -> bool:
    """Whether the target's C struct-passing ABI is implemented.

    AArch64 (any OS) and x86-64 (System V and Windows) are supported; riscv64
    and unknown targets are not -- a by-value-struct ``@extern`` on one is a
    compile error at the declaration.
    """
    return classify_arch(target) in ("ARCH_AARCH64", "ARCH_X86_64")


def classify_signature(ret: LangType, params: list, target: str) -> tuple[list, object]:
    """Classify a struct-passing ``@extern`` signature for its target's C ABI.

    Dispatches on the target's ``(arch, os)`` to the AArch64, x86-64 System V,
    or Win64 classifier (see the module docstring). The caller has already
    verified the target is supported via :func:`abi_supported`.

    Args:
        ret: The resolved return ``LangType``.
        params: The resolved parameter ``LangType``s, in order.
        target: The lowercased LLVM target triple.

    Returns:
        A ``(arg_classes, ret_class)`` pair: ``arg_classes`` holds a
        :class:`Direct`/:class:`Indirect` for each aggregate parameter and
        ``None`` for a scalar/pointer, and ``ret_class`` the return's
        classification or ``None`` for a scalar/void return.
    """
    arch = classify_arch(target)
    if arch == "ARCH_AARCH64":
        arg_classes = [classify_aggregate(p) if is_aggregate(p) else None for p in params]
        ret_class = classify_aggregate(ret) if is_aggregate(ret) else None
        return arg_classes, ret_class
    if classify_os(target) == "OS_WINDOWS":
        return _win64_signature(ret, params)
    return _sysv_signature(ret, params)
