"""Shared helpers: compile source strings through the pipeline stages."""

import ctypes
from pathlib import Path

import llvmlite.binding as llvm

from mcc.codegen import CodeGen
from mcc.driver import (
    RUNTIME_DIR,
    STDLIB_DIR,
    _prelude_imports,
    build_native_module,
    compile_to_ir,
    merge_imports,
)
from mcc.lexer import tokenize
from mcc.nodes import Program
from mcc.parser import Parser


def parse(source: str) -> Program:
    return Parser(tokenize(source)).parse_program()


def _imports_std(program: Program) -> bool:
    """Whether the program imports a high-level `std/` module.

    The runtime prelude backs the `std/` library (formatting, slices, char
    helpers), so a source that reaches for `std/` needs it. Sources that pull in
    only `libc/` bindings or a local test module are self-contained and stay
    minimal -- matching how codegen/precedence unit tests isolate a snippet."""
    return any(path == "std" or path.startswith("std/")
               for path, _line in program.imports)


def _resolve(source: str) -> Program:
    """Parse a source string and merge any `import "...";` it declares against
    the standard lib/ directory, so tests can pull in libc bindings and the
    standard library the way real programs do. The string's own declarations
    keep source=None (external linkage), as before.

    A source that imports a `std/` module also gets the implicit runtime prelude
    (the `runtime/*.mc` modules), exactly like a real build without `--nostdlib`,
    so the standard library's runtime-backed features resolve. Sources that
    import nothing, or only `libc/`/local modules, stay minimal -- no prelude --
    matching the isolation most small tests want."""
    program = parse(source)
    if not program.imports:
        return program
    if _imports_std(program):
        program.imports = _prelude_imports(RUNTIME_DIR) + program.imports
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
    """Like run(), but compiles from a file so `import` directives resolve.

    Applies the implicit runtime prelude when the entry file reaches for a
    `std/` module (see :func:`_imports_std`), like a real build; a file that
    imports only `libc/`/local modules stays minimal."""
    prelude = _imports_std(parse(path.read_text()))
    return _execute(compile_to_ir(path, prelude=prelude))
