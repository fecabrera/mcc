"""Driver: verifies and optimizes the IR, then JITs it or links an executable."""

from __future__ import annotations

import argparse
import ctypes
import os
import subprocess
import sys
from pathlib import Path

import llvmlite.binding as llvm
from llvmlite import ir

from mcc.codegen import CodeGen
from mcc.errors import LangError
from mcc.lexer import tokenize
from mcc.nodes import Conditional, Program
from mcc.parser import Parser


def _find_stdlib() -> Path:
    """Locate the bundled lib/ standard-library directory.

    The directory is importable by bare name (the "standard library") unless
    ``--naked`` is passed. It lives beside the package when installed as a
    wheel (mcc/lib) and at the repo root in a source checkout (../lib); the
    ``$MCC_STDLIB`` environment variable overrides both.

    Returns:
        The first candidate directory that exists, falling back to the
        source-checkout path (../lib) when none is found yet.
    """
    override = os.environ.get("MCC_STDLIB")
    if override:
        return Path(override)
    here = Path(__file__).resolve().parent
    for candidate in (here / "lib", here.parent / "lib"):
        if candidate.is_dir():
            return candidate
    return here.parent / "lib"


STDLIB_DIR = _find_stdlib()


def _stamp_conditionals(conditionals, source: str) -> None:
    """Record the owning file on declarations inside top-level ``@if`` blocks.

    Recurses through nested conditionals, stamping both the ``then`` and
    ``otherwise`` branches so each declaration's source survives the flattening
    that codegen performs later.

    Args:
        conditionals: The top-level ``Conditional`` nodes to walk.
        source: Path of the file these conditionals were parsed from.
    """
    for cond in conditionals:
        for item in (*cond.then, *cond.otherwise):
            if isinstance(item, Conditional):
                _stamp_conditionals([item], source)
            else:
                item.source = source


def _stamp_sources(program: Program, source: str) -> None:
    """Record the owning file on every declaration in a parsed program.

    Functions, structs, globals, consts, and the declarations inside top-level
    ``@if`` branches each get ``source`` set, so ``@private`` access checks and
    linkage can tell which file a declaration came from.

    Args:
        program: The freshly parsed program to annotate, modified in place.
        source: Path of the file ``program`` was parsed from.
    """
    for func in program.functions:
        func.source = source
    for decl in program.structs:
        decl.source = source
    for var in program.globals:
        var.source = source
    for const in program.consts:
        const.source = source
    # A top-level @if's branches hold declarations too; stamp them so their
    # source survives flattening in codegen.
    _stamp_conditionals(program.conditionals, source)


def merge_imports(program: Program, base_dir: Path,
                  search_paths: tuple[Path, ...] = (),
                  visited: set[Path] | None = None,
                  source: str | None = None) -> Program:
    """Merge a parsed program's ``import "file";`` graph into one program.

    Each import resolves relative to ``base_dir`` first, then through the
    search-path directories in order; the ``.mc`` suffix is optional. A file
    imported more than once (including via cycles) is loaded only the first
    time. Imported declarations are placed before the importing program's own.

    Args:
        program: The parsed program whose imports to resolve.
        base_dir: Directory the program was loaded from; imports resolve
            relative to it first.
        search_paths: Additional directories to search, in order, after
            ``base_dir``.
        visited: Set of already-loaded absolute paths, shared across the
            recursion to break cycles; created when ``None``.
        source: Path of the importing file, used for error reporting; ``None``
            for a program parsed from a string.

    Returns:
        A new ``Program`` with the import list resolved and every imported
        declaration merged in.

    Raises:
        LangError: When an imported file cannot be found on any search path.
    """
    if visited is None:
        visited = set()
    structs, functions = [], []
    globals_, consts, conditionals = [], [], []
    for import_path, line in program.imports:
        candidates = []
        for base in (base_dir, *search_paths):
            target = base / import_path
            if target.suffix != ".mc":
                target = target.with_suffix(".mc")
            candidates.append(target)
        target = next((c for c in candidates if c.is_file()), None)
        if target is None:
            tried = ", ".join(str(c) for c in candidates)
            raise LangError(f"cannot import {import_path!r}: tried {tried}", line,
                            source=source)
        imported = load_program(target, search_paths, visited)
        structs += imported.structs
        functions += imported.functions
        globals_ += imported.globals
        consts += imported.consts
        conditionals += imported.conditionals
    structs += program.structs
    functions += program.functions
    globals_ += program.globals
    consts += program.consts
    conditionals += program.conditionals
    return Program([], structs, functions, globals_, consts, conditionals)


def load_program(path: Path, search_paths: tuple[Path, ...] = (),
                 _visited: set[Path] | None = None) -> Program:
    """Parse a source file and recursively merge its import graph.

    A file already present in ``_visited`` resolves to an empty program, so a
    file shared by several imports (or a cycle) is parsed only once.

    Args:
        path: The source file to load.
        search_paths: Directories to search for imports, in order.
        _visited: Set of already-loaded absolute paths, shared across the
            recursion; created when ``None``.

    Returns:
        The program parsed from ``path`` with all of its imports merged in.

    Raises:
        LangError: On a lex/parse error or a failed import; the error's
            ``source`` is filled in with this file when not already set.
    """
    resolved = path.resolve()
    visited = _visited if _visited is not None else set()
    if resolved in visited:
        return Program([], [], [], [], [], [])
    visited.add(resolved)
    try:
        program = Parser(tokenize(resolved.read_text())).parse_program()
    except LangError as err:
        if err.source is None:
            err.source = str(resolved)
        raise
    _stamp_sources(program, str(resolved))
    return merge_imports(program, resolved.parent, search_paths, visited,
                         source=str(resolved))


def compile_to_ir(path: Path, search_paths: tuple[Path, ...] | None = None,
                  target: str | None = None) -> ir.Module:
    """Load a source file and lower it to an LLVM IR module.

    Args:
        path: The entry source file to compile.
        search_paths: Import search-path directories; defaults to just the
            bundled standard library when ``None``.
        target: An LLVM target triple to build for, or ``None`` for the host.

    Returns:
        The generated, unverified LLVM IR module.
    """
    if search_paths is None:
        search_paths = (STDLIB_DIR,)
    program = load_program(path, tuple(search_paths))
    return CodeGen(program, path.name, root_source=str(path.resolve()),
                   target=target).generate()


# For each architecture, the LLVM subtarget features that -- when turned off
# (a leading '-') -- keep generated code off the floating-point/SIMD register
# file, plus '+soft-float' where the backend needs it. This is the equivalent
# of gcc's -mgeneral-regs-only: it stops the compiler from quietly using vector
# registers (e.g. to copy a struct) in code, such as an interrupt handler, that
# must not touch FP state. Keyed by the architecture in an LLVM triple.
GENERAL_REGS_ONLY_FEATURES = {
    "aarch64": "-fp-armv8,-neon",
    "arm64": "-fp-armv8,-neon",
    "x86_64": "-mmx,-sse,-sse2,-sse3,-ssse3,-sse4.1,-sse4.2,-avx,-avx2,"
              "-avx512f,+soft-float",
    "i386": "-mmx,-sse,-sse2,-sse3,-ssse3,-sse4.1,-sse4.2,-avx,-avx2,"
            "-avx512f,+soft-float",
    "riscv64": "-f,-d,-v",
    "riscv32": "-f,-d,-v",
}


def restrict_to_general_regs(module: ir.Module, triple: str) -> None:
    """Bar the FP/SIMD registers from every defined function in a module.

    Tags each definition with a ``target-features`` attribute that disables the
    floating-point/SIMD register file for ``triple``'s architecture -- mcc's
    equivalent of gcc's ``-mgeneral-regs-only``. Extern declarations (no body)
    are left untouched.

    Args:
        module: The IR module to annotate, modified in place.
        triple: The LLVM target triple whose architecture selects the features.

    Raises:
        RuntimeError: When the architecture has no known general-regs-only
            feature set.
    """
    arch = triple.split("-")[0]
    features = GENERAL_REGS_ONLY_FEATURES.get(arch)
    if features is None:
        raise RuntimeError(f"--general-regs-only is not supported for target {arch!r}")
    attribute = f'"target-features"="{features}"'
    for func in module.functions:
        if func.blocks:  # a definition, not an extern declaration
            # The attribute is a key=value string, which llvmlite's validated
            # add() rejects; FunctionAttributes is a plain set, so add it raw.
            set.add(func.attributes, attribute)


def build_native_module(module: ir.Module, opt_level: int, triple: str | None = None,
                        general_regs_only: bool = False):
    """Verify and optimize an IR module for a target machine.

    Initializes the backend, attaches the target's triple and data layout,
    optionally restricts the code to general-purpose registers, verifies the
    module, and runs LLVM's optimization pipeline when ``opt_level`` is above 0.

    Args:
        module: The IR module to finalize.
        opt_level: Optimization level 0-3; 0 skips the pass pipeline.
        triple: An LLVM target triple (e.g. ``aarch64-unknown-none-elf`` for
            bare metal), or ``None`` to build for the host.
        general_regs_only: When ``True``, bar the FP/SIMD registers via
            :func:`restrict_to_general_regs`.

    Returns:
        A tuple ``(native, target_machine)`` of the parsed, verified, optimized
        module and the target machine it was built for.
    """
    cross = triple is not None
    if triple is None:
        llvm.initialize_native_target()
        llvm.initialize_native_asmprinter()
        triple = llvm.get_default_triple()
    else:
        llvm.initialize_all_targets()
        llvm.initialize_all_asmprinters()
    if cross:
        # Cross targets are freestanding objects linked at a fixed address (e.g.
        # a bare-metal kernel). The small code model + static relocations give
        # plain ADRP+ADD/LDR addressing -- what aarch64-elf-gcc emits -- instead
        # of the JIT default (large code model: absolute movz/movk for locals,
        # GOT indirection for externs), which a no-loader image cannot satisfy.
        target_machine = llvm.Target.from_triple(triple).create_target_machine(
            opt=opt_level, reloc="static", codemodel="small")
    else:
        target_machine = llvm.Target.from_triple(triple).create_target_machine(opt=opt_level)
    module.triple = triple
    module.data_layout = str(target_machine.target_data)
    if general_regs_only:
        restrict_to_general_regs(module, triple)
    native = llvm.parse_assembly(str(module))
    native.verify()
    if opt_level > 0:
        options = llvm.create_pipeline_tuning_options(speed_level=opt_level)
        pass_builder = llvm.create_pass_builder(target_machine, options)
        pass_builder.getModulePassManager().run(native, pass_builder)
    return native, target_machine


def main() -> int:
    """Run the ``mcc`` command-line interface.

    Parses arguments, compiles the source file to IR, and then -- depending on
    the flags -- prints the IR (``--emit-llvm``), emits an object file for a
    cross target (``--target``), JIT-executes ``main`` (``--run``), or links a
    native executable with ``cc``. Compile and I/O errors are reported as
    ``file: error: ...`` on stderr.

    Returns:
        A process exit status: 0 on success, 1 on a reported error, or the
        value returned by the program's ``main`` under ``--run``.
    """
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
    cli.add_argument("--general-regs-only", action="store_true",
                     help="generate code that uses only general-purpose registers, never "
                          "the floating-point/SIMD ones (like gcc's -mgeneral-regs-only)")
    args = cli.parse_args()

    if args.target and args.run:
        print("mcc: error: --run cannot execute cross-compiled code", file=sys.stderr)
        return 1

    search_paths = list(args.import_path)
    if not args.naked:
        search_paths.append(STDLIB_DIR)

    try:
        module = compile_to_ir(args.source, search_paths, args.target)
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
        native, target_machine = build_native_module(
            module, args.O, args.target, args.general_regs_only)
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
