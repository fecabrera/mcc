"""Shared helpers: compile source strings through the pipeline stages."""

import ctypes
from pathlib import Path

import llvmlite.binding as llvm

from mcc.codegen import CodeGen
from mcc.driver import STDLIB_DIR, build_native_module, compile_to_ir, merge_imports
from mcc.lexer import tokenize
from mcc.nodes import Program
from mcc.parser import Parser


def parse(source: str) -> Program:
    return Parser(tokenize(source)).parse_program()


def _resolve(source: str) -> Program:
    """Parse a source string and merge any `import "...";` it declares against
    the standard lib/ directory, so tests can pull in libc bindings and the
    standard library the way real programs do. The string's own declarations
    keep source=None (external linkage), as before."""
    program = parse(source)
    if not program.imports:
        return program
    return merge_imports(program, STDLIB_DIR, (STDLIB_DIR,))


def compile_ir(source: str, target: str | None = None) -> str:
    """Compile source to LLVM IR text (unoptimized, unverified).

    A ``target`` triple fixes the compilation target (e.g. to exercise a
    platform ABI deterministically regardless of the host); the default follows
    the host.
    """
    return str(CodeGen(_resolve(source), "test", target=target).generate())


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
    return _execute(CodeGen(_resolve(source), "test").generate())


def run_path(path: Path) -> int:
    """Like run(), but compiles from a file so `import` directives resolve."""
    return _execute(compile_to_ir(path))


def compile_files(directory: Path, files: dict[str, str], entry: str = "main.mc"):
    """Write a multi-file corpus into ``directory`` and compile its entry file.

    The shared spelling of the write-the-files-then-compile fixture the
    multi-file suites need (imports between sibling tmp files can't go
    through the string helpers above). Returns the compiled module.
    """
    for name, text in files.items():
        (directory / name).write_text(text)
    return compile_to_ir(directory / entry)
