"""Target facts and ``@if`` static evaluation for conditional compilation.

Classifies an LLVM target triple into ``OS_*``/``ARCH_*`` facts and evaluates
``@if`` conditions against those facts (plus ``-D`` defines) at compile time.
Used both by code generation and by the driver, which resolves conditional
imports before code generation runs.
"""

from __future__ import annotations

from mcc.errors import LangError
from mcc.nodes import Binary, BoolLit, CharLit, IntLit, Logical, Ternary, Unary, Var

from mcc.codegen.types import COMPARISON_OPS, _host_triple


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
