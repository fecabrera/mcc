"""AArch64 AAPCS64 C-ABI classification for ``@extern`` struct passing.

mcc's own calls use a raw-aggregate convention (LLVM lays the struct out and
passes it whole, with ``const``/``mut`` parameters travelling by a hidden
reference). That convention is self-consistent but does *not* match the
platform C ABI, so a ``struct`` handed to or returned from an ``@extern`` C
function would not land where the C side expects it. This module classifies an
aggregate the way a C compiler does on Apple/AAPCS64, so the ``@extern`` call
boundary -- and only that boundary -- speaks the C ABI.

The classification is intentionally *context-free* (each aggregate is
classified on its own, independent of the registers earlier arguments consumed).
This mirrors clang's ``AArch64ABIInfo``: unlike the x86-64 SysV ABI, AAPCS64
does not demote a register-eligible aggregate to memory when the argument
registers run out -- the LLVM AArch64 backend performs the NGRN/NSRN/NSAA
allocation (including spilling a register-class aggregate to the stack) from the
context-free coerced IR types. See :mod:`mcc.codegen.generator` for the call
sites that consume these results.

The classifier reports one of:

* :class:`Direct` -- pass/return the aggregate coerced to a small LLVM type
  that occupies registers (an ``i64``/``[2 x i64]`` GPR form, or an
  ``[N x float]``/``[N x double]`` FP form for a homogeneous float aggregate).
* :class:`Indirect` -- the aggregate is too big for registers: an argument is
  passed as a pointer to a caller-owned copy, and a return is written through a
  hidden ``sret`` pointer.

Sizes and alignments come from mcc's own :func:`type_size`/:func:`type_align`
(which honor ``@packed``/``@align``), never from the LLVM data layout -- under
the JIT the data layout is not reliably populated.
"""

from __future__ import annotations

from dataclasses import dataclass

import llvmlite.ir as ir

from mcc.codegen.types import (
    LangType,
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


@dataclass(frozen=True)
class Direct:
    """The aggregate is passed/returned in registers, coerced to ``coerce_ir``.

    Attributes:
        coerce_ir: The LLVM type the struct is bitcast through at the boundary
            -- ``i64`` or ``[2 x i64]`` for the GPR case, or ``[N x float]`` /
            ``[N x double]`` for a homogeneous float aggregate.
    """

    coerce_ir: ir.Type


@dataclass(frozen=True)
class Indirect:
    """The aggregate is passed by pointer (arg) or via ``sret`` (return).

    Attributes:
        align: The aggregate's alignment, for the ``sret``/pointer slot.
        struct_ir: The aggregate's own LLVM struct/array type.
    """

    align: int
    struct_ir: ir.Type


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


# Argument and return classification are identical today; the two names keep
# the call sites self-documenting and leave room to diverge later.
classify_arg = classify_aggregate
classify_return = classify_aggregate
