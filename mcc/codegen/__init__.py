"""Code generation: turns a merged ``Program`` into an LLVM IR module.

This package re-exports the public API historically importable from
``mcc.codegen``. The implementation is split across:

* :mod:`mcc.codegen.types` -- the ``LangType``/``TypedValue`` type system,
  type constructors, predicates, layout, and integer-folding helpers.
* :mod:`mcc.codegen.targets` -- target facts and ``@if`` static evaluation.
* :mod:`mcc.codegen.ir_ext` -- volatile load/store IR instructions.
* :mod:`mcc.codegen.generator` -- the ``CodeGen`` walker itself.
"""

from mcc.codegen.generator import CodeGen
from mcc.codegen.ir_ext import VolatileLoad, VolatileStore
from mcc.codegen.targets import (
    TARGET_ARCH_VALUES,
    TARGET_OS_VALUES,
    classify_arch,
    classify_os,
    compute_target_facts,
    eval_static_cond,
    eval_static_value,
    target_fact_values,
)
from mcc.codegen.types import (
    LangType,
    TypedValue,
    fnv1a64,
    is_aggregate,
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
)

__all__ = [
    "CodeGen",
    "LangType",
    "TypedValue",
    "TARGET_ARCH_VALUES",
    "TARGET_OS_VALUES",
    "VolatileLoad",
    "VolatileStore",
    "classify_arch",
    "classify_os",
    "compute_target_facts",
    "eval_static_cond",
    "eval_static_value",
    "target_fact_values",
    "fnv1a64",
    "is_aggregate",
    "is_any",
    "is_array",
    "is_flexible_array",
    "is_function",
    "is_integer",
    "is_pointer",
    "is_slice",
    "is_struct",
    "is_union",
    "is_valist",
]
