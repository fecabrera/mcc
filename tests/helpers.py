"""Shared helpers: compile source strings through the pipeline stages."""

import ctypes
from pathlib import Path

import llvmlite.binding as llvm

from mcc.codegen import CodeGen
from mcc.driver import build_native_module, compile_to_ir
from mcc.lexer import tokenize
from mcc.nodes import Program
from mcc.parser import Parser


def parse(source: str) -> Program:
    return Parser(tokenize(source)).parse_program()


def compile_ir(source: str) -> str:
    """Compile source to LLVM IR text (unoptimized, unverified)."""
    return str(CodeGen(parse(source), "test").generate())


def _execute(module) -> int:
    native, target_machine = build_native_module(module, opt_level=2)
    with llvm.create_mcjit_compiler(native, target_machine) as jit:
        jit.finalize_object()
        main = ctypes.CFUNCTYPE(ctypes.c_int32)(jit.get_function_address("main"))
        status = main()
    # printf writes through libc's buffered stdout; under pytest that fd is a
    # pipe (fully buffered), so flush before the test reads it.
    ctypes.CDLL(None).fflush(None)
    return status


def run(source: str) -> int:
    """JIT-compile source in-process, call main, and return its exit status.

    printf output goes to the real stdout file descriptor, so tests can
    observe it with pytest's capfd fixture.
    """
    return _execute(CodeGen(parse(source), "test").generate())


def run_path(path: Path) -> int:
    """Like run(), but compiles from a file so `import` directives resolve."""
    return _execute(compile_to_ir(path))
