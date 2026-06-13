"""Driver: verifies and optimizes the IR, then JITs it or links an executable."""

from __future__ import annotations

import argparse
import ctypes
import subprocess
import sys
from pathlib import Path

import llvmlite.binding as llvm
from llvmlite import ir

from mcc.codegen import CodeGen
from mcc.errors import LangError
from mcc.lexer import tokenize
from mcc.nodes import Program
from mcc.parser import Parser


# The project's lib/ directory, importable by bare name (the "standard
# library") unless --naked is passed.
STDLIB_DIR = Path(__file__).resolve().parent.parent / "lib"


def load_program(path: Path, search_paths: tuple[Path, ...] = (),
                 _visited: set[Path] | None = None) -> Program:
    """Parse a source file and recursively merge its `import "file";` graph.

    Imports resolve relative to the importing file first, then through the
    search-path directories in order; the .mc suffix is optional. A file
    imported more than once (including cycles) is only loaded the first time.
    """
    resolved = path.resolve()
    visited = _visited if _visited is not None else set()
    if resolved in visited:
        return Program([], [], [], [], [])
    visited.add(resolved)
    try:
        program = Parser(tokenize(resolved.read_text())).parse_program()
    except LangError as err:
        if err.source is None:
            err.source = str(resolved)
        raise
    for func in program.functions:
        func.source = str(resolved)
    for decl in program.structs:
        decl.source = str(resolved)
    for var in program.globals:
        var.source = str(resolved)
    includes, structs, functions, globals_ = [], [], [], []
    for import_path, line in program.imports:
        candidates = []
        for base in (resolved.parent, *search_paths):
            target = base / import_path
            if target.suffix != ".mc":
                target = target.with_suffix(".mc")
            candidates.append(target)
        target = next((c for c in candidates if c.is_file()), None)
        if target is None:
            tried = ", ".join(str(c) for c in candidates)
            raise LangError(f"cannot import {import_path!r}: tried {tried}", line,
                            source=str(resolved))
        imported = load_program(target, search_paths, visited)
        includes += imported.includes
        structs += imported.structs
        functions += imported.functions
        globals_ += imported.globals
    includes += program.includes
    structs += program.structs
    functions += program.functions
    globals_ += program.globals
    return Program([], list(dict.fromkeys(includes)), structs, functions, globals_)


def compile_to_ir(path: Path, search_paths: tuple[Path, ...] | None = None) -> ir.Module:
    if search_paths is None:
        search_paths = (STDLIB_DIR,)
    program = load_program(path, tuple(search_paths))
    return CodeGen(program, path.name, root_source=str(path.resolve())).generate()


def build_native_module(module: ir.Module, opt_level: int, triple: str | None = None):
    """Verify and optimize the IR for a target: the host by default, or any
    LLVM triple (e.g. aarch64-unknown-none-elf for bare metal)."""
    if triple is None:
        llvm.initialize_native_target()
        llvm.initialize_native_asmprinter()
        triple = llvm.get_default_triple()
    else:
        llvm.initialize_all_targets()
        llvm.initialize_all_asmprinters()
    target_machine = llvm.Target.from_triple(triple).create_target_machine(opt=opt_level)
    module.triple = triple
    module.data_layout = str(target_machine.target_data)
    native = llvm.parse_assembly(str(module))
    native.verify()
    if opt_level > 0:
        options = llvm.create_pipeline_tuning_options(speed_level=opt_level)
        pass_builder = llvm.create_pass_builder(target_machine, options)
        pass_builder.getModulePassManager().run(native, pass_builder)
    return native, target_machine


def main() -> int:
    cli = argparse.ArgumentParser(prog="mcc", description="Compile .mc source files with LLVM.")
    cli.add_argument("source", type=Path)
    cli.add_argument("-o", "--output", type=Path, help="output executable name")
    cli.add_argument("-O", type=int, default=2, choices=range(4), help="optimization level")
    cli.add_argument("--run", action="store_true", help="JIT-compile and run immediately")
    cli.add_argument("--emit-llvm", action="store_true", help="print LLVM IR and exit")
    cli.add_argument("-I", "--import-path", action="append", type=Path, default=[],
                     metavar="DIR", help="add a directory to the import search path (repeatable)")
    cli.add_argument("--naked", action="store_true",
                     help="do not add the standard lib/ directory to the import search path")
    cli.add_argument("--target", metavar="TRIPLE",
                     help="cross-compile for this LLVM target triple, emitting an object "
                          "file to link with that target's toolchain")
    args = cli.parse_args()

    if args.target and args.run:
        print("mcc: error: --run cannot execute cross-compiled code", file=sys.stderr)
        return 1

    search_paths = list(args.import_path)
    if not args.naked:
        search_paths.append(STDLIB_DIR)

    try:
        module = compile_to_ir(args.source, search_paths)
    except OSError as err:
        print(f"mcc: error: cannot read {args.source}: {err.strerror}", file=sys.stderr)
        return 1
    except LangError as err:
        source = Path(err.source) if err.source else args.source
        if source.is_absolute():  # imported files resolve to absolute paths
            try:
                source = source.relative_to(Path.cwd())
            except ValueError:
                pass
        print(f"{source}: error: {err}", file=sys.stderr)
        return 1

    if args.emit_llvm:
        print(module)
        return 0

    try:
        native, target_machine = build_native_module(module, args.O, args.target)
    except RuntimeError as err:
        print(f"mcc: error: {err}", file=sys.stderr)
        return 1

    if args.target:
        # No host linker for a foreign target: emit the object file and let
        # the target toolchain (e.g. aarch64-elf-gcc) link it.
        output = args.output or args.source.with_suffix(".o")
        output.write_bytes(target_machine.emit_object(native))
        print(f"wrote {output}")
        return 0

    if args.run:
        with llvm.create_mcjit_compiler(native, target_machine) as jit:
            jit.finalize_object()
            address = jit.get_function_address("main")
            return ctypes.CFUNCTYPE(ctypes.c_int32)(address)()

    output = args.output or args.source.with_suffix("")
    obj_path = output.with_suffix(".o")
    obj_path.write_bytes(target_machine.emit_object(native))
    subprocess.run(["cc", str(obj_path), "-o", str(output), "-lm"], check=True)
    obj_path.unlink()
    print(f"wrote {output}")
    return 0
