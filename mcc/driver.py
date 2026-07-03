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

from mcc.codegen import (
    CodeGen,
    TARGET_ARCH_VALUES,
    TARGET_OS_VALUES,
    compute_target_facts,
    eval_static_cond,
)
from mcc.errors import LangError
from mcc.lexer import tokenize
from mcc.nodes import Conditional, Import, Program
from mcc.parser import Parser


def _find_stdlib() -> Path:
    """Locate the standard-library *source* directory (``libmc/``).

    These are the ``.mc`` sources, imported by bare name (the "standard
    library") unless ``--nostdlib`` is passed, and compiled in from source like
    any other import. (Shipping the stdlib as a precompiled native library is a
    planned change; see the roadmap.) It lives beside the package when installed
    as a wheel (mcc/libmc) and at the repo root in a source checkout (../libmc);
    the ``$MCC_STDLIB`` environment variable overrides both.

    Returns:
        The first candidate directory that exists, falling back to the
        source-checkout path (../libmc) when none is found yet.
    """
    override = os.environ.get("MCC_STDLIB")
    if override:
        return Path(override)
    here = Path(__file__).resolve().parent
    for candidate in (here / "libmc", here.parent / "libmc"):
        if candidate.is_dir():
            return candidate
    return here.parent / "libmc"


STDLIB_DIR = _find_stdlib()  # the stdlib sources, compiled in from source


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
    for enum in program.enums:
        enum.source = source
    for alias in program.aliases:
        alias.source = source
    for directive in program.directives:
        directive.source = source
    # A top-level @if's branches hold declarations too; stamp them so their
    # source survives flattening in codegen.
    _stamp_conditionals(program.conditionals, source)


def _import_candidates(base: Path, import_path: str) -> list[Path]:
    """Resolve one ``import`` path within a directory to candidate files.

    A source file is ``.mc``; a generated interface stub (see
    ``--emit-interface``) is ``.mci``. A bare ``import "foo"`` tries ``foo.mc``
    first, then ``foo.mci`` -- so a checkout with sources resolves to them, while
    a consumer that received only an object plus its interface falls back to the
    stub. An explicit ``.mc``/``.mci`` suffix is taken as written.

    Args:
        base: The directory to resolve against.
        import_path: The import path as written in source.

    Returns:
        The candidate paths to try, in priority order.
    """
    target = base / import_path
    if target.suffix in (".mc", ".mci"):
        return [target]
    return [target.with_suffix(".mc"), target.with_suffix(".mci")]


def _conditional_imports(conditionals: list, facts: dict[str, int]) -> list:
    """The imports from the taken branch of each ``@if``, recursively.

    Conditional imports are resolved against the same target facts code
    generation will use, so the imports and the branch's declarations agree on
    which branch is live.

    Args:
        conditionals: The top-level ``@if`` blocks to scan.
        facts: The target facts and ``-D`` defines in effect.

    Returns:
        The ``(path, line)`` imports nested in live branches, in order.
    """
    out = []
    for cond in conditionals:
        taken = cond.then if eval_static_cond(cond.cond, facts) else cond.otherwise
        for item in taken:
            if isinstance(item, Import):
                out.append((item.path, item.line))
            elif isinstance(item, Conditional):
                out.extend(_conditional_imports([item], facts))
    return out


def merge_imports(program: Program, base_dir: Path,
                  search_paths: tuple[Path, ...] = (),
                  facts: dict[str, int] | None = None,
                  visited: set[Path] | None = None,
                  source: str | None = None) -> Program:
    """Merge a parsed program's ``import "file";`` graph into one program.

    Each import resolves relative to ``base_dir`` first, then through the
    search-path directories in order; the ``.mc``/``.mci`` suffix is optional. A
    file imported more than once (including via cycles) is loaded only the first
    time. Imported declarations are placed before the importing program's own.
    Imports nested in a top-level ``@if`` are resolved too, for the branch
    ``facts`` select.

    Args:
        program: The parsed program whose imports to resolve.
        base_dir: Directory the program was loaded from; imports resolve
            relative to it first.
        search_paths: Additional directories to search, in order, after
            ``base_dir``.
        facts: The target facts and ``-D`` defines for evaluating conditional
            imports; the host facts when ``None``.
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
    if facts is None:
        facts = compute_target_facts(None, None)
    if visited is None:
        visited = set()
    structs, functions = [], []
    globals_, consts, conditionals, enums, aliases = [], [], [], [], []
    directives = []
    imports = program.imports + _conditional_imports(program.conditionals, facts)
    for import_path, line in imports:
        candidates = []
        for base in (base_dir, *search_paths):
            candidates.extend(_import_candidates(base, import_path))
        target = next((c for c in candidates if c.is_file()), None)
        if target is None:
            tried = ", ".join(str(c) for c in candidates)
            raise LangError(f"cannot import {import_path!r}: tried {tried}", line,
                            source=source)
        imported = load_program(target, search_paths, facts, visited)
        structs += imported.structs
        functions += imported.functions
        globals_ += imported.globals
        consts += imported.consts
        conditionals += imported.conditionals
        enums += imported.enums
        aliases += imported.aliases
        directives += imported.directives
    structs += program.structs
    functions += program.functions
    globals_ += program.globals
    consts += program.consts
    conditionals += program.conditionals
    enums += program.enums
    aliases += program.aliases
    directives += program.directives
    return Program([], structs, functions, globals_, consts, conditionals, enums,
                   aliases, directives)


def load_program(path: Path, search_paths: tuple[Path, ...] = (),
                 facts: dict[str, int] | None = None,
                 _visited: set[Path] | None = None) -> Program:
    """Parse a source file and recursively merge its import graph.

    A file already present in ``_visited`` resolves to an empty program, so a
    file shared by several imports (or a cycle) is parsed only once.

    Args:
        path: The source file to load.
        search_paths: Directories to search for imports, in order.
        facts: The target facts and ``-D`` defines for conditional imports; the
            host facts when ``None``.
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
    return merge_imports(program, resolved.parent, search_paths, facts, visited,
                         source=str(resolved))


def compile_to_ir(path: Path, search_paths: tuple[Path, ...] | None = None,
                  target: str | None = None,
                  defines: dict[str, int] | None = None,
                  freestanding: bool = False) -> ir.Module:
    """Load a source file and lower it to an LLVM IR module.

    Args:
        path: The entry source file to compile.
        search_paths: Import search-path directories; defaults to just the
            bundled standard library when ``None``.
        target: An LLVM target triple to build for, or ``None`` for the host.
        defines: Command-line ``-D`` names mapped to integer values, made
            available to ``@if`` conditions.
        freestanding: When true, mark definitions ``"no-builtins"`` so LLVM
            does not assume a hosted C library (see :func:`mark_freestanding`).

    Returns:
        The generated, unverified LLVM IR module.
    """
    if search_paths is None:
        search_paths = (STDLIB_DIR,)
    facts = compute_target_facts(target, defines)
    program = load_program(path, tuple(search_paths), facts)
    module = CodeGen(program, path.name, root_source=str(path.resolve()),
                     target=target, defines=defines).generate()
    if freestanding:
        mark_freestanding(module)
    return module


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


def general_regs_features(triple: str) -> str:
    """Return the general-regs-only subtarget feature string for ``triple``.

    Args:
        triple: The LLVM target triple whose architecture selects the features.

    Raises:
        RuntimeError: When the architecture has no known general-regs-only
            feature set.
    """
    arch = triple.split("-")[0]
    features = GENERAL_REGS_ONLY_FEATURES.get(arch)
    if features is None:
        raise RuntimeError(f"--general-regs-only is not supported for target {arch!r}")
    return features


def apply_target_features(module: ir.Module, features: str) -> None:
    """Tag every defined function in ``module`` with ``target-features``.

    LLVM honors only one ``target-features`` attribute per function, so callers
    must pass a single comma-joined feature string rather than tagging twice.
    Extern declarations (no body) are left untouched.

    Args:
        module: The IR module to annotate, modified in place.
        features: A comma-separated subtarget feature list (e.g.
            ``"-fp-armv8,-neon,+strict-align"``).
    """
    attribute = f'"target-features"="{features}"'
    for func in module.functions:
        if func.blocks:  # a definition, not an extern declaration
            # The attribute is a key=value string, which llvmlite's validated
            # add() rejects; FunctionAttributes is a plain set, so add it raw.
            set.add(func.attributes, attribute)


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
    apply_target_features(module, general_regs_features(triple))


def mark_freestanding(module: ir.Module) -> None:
    """Tell LLVM not to assume a hosted C library.

    Tags every definition with the ``"no-builtins"`` attribute, the IR-level
    equivalent of ``-ffreestanding`` / ``-fno-builtin``. Without it, LLVM's
    libcall optimizer recognizes standard-named functions (``printf``,
    ``memcpy``, ...) and may rewrite a call into another it assumes exists --
    e.g. ``printf("x\\n")`` into ``puts``, or ``printf("%c", c)`` into
    ``putchar`` -- synthesizing references to symbols a freestanding program
    never defined. Extern declarations (no body) make no calls, so they are
    left untouched.

    Args:
        module: The IR module to annotate, modified in place.
    """
    for func in module.functions:
        if func.blocks:  # a definition, not an extern declaration
            # A bare string attribute, which llvmlite's validated add() rejects;
            # FunctionAttributes is a plain set, so add it raw.
            set.add(func.attributes, '"no-builtins"')


def build_native_module(module: ir.Module, opt_level: int, triple: str | None = None,
                        general_regs_only: bool = False, strict_align: bool = False):
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
        strict_align: When ``True``, forbid the backend from emitting unaligned
            memory accesses (the ``+strict-align`` feature, mcc's equivalent of
            gcc's ``-mstrict-align``). Required for bare-metal targets with the
            MMU off, where all RAM is Device memory and an unaligned wide load or
            store traps as an alignment fault.

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
    # The integrated assembler needs an asm parser to lower inline asm. llvmlite
    # only exposes the native one, so inline asm works for the host arch -- which
    # also covers a cross --target of the same architecture (e.g. an aarch64 host
    # building an aarch64 bare-metal object), but not a foreign arch.
    llvm.initialize_native_asmparser()
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
    # LLVM honors a single target-features attribute per function, so merge the
    # requested feature sets into one comma-joined string before tagging.
    features = []
    if general_regs_only:
        features.append(general_regs_features(triple))
    if strict_align:
        features.append("+strict-align")
    if features:
        apply_target_features(module, ",".join(features))
    native = llvm.parse_assembly(str(module))
    native.verify()
    if opt_level > 0:
        options = llvm.create_pipeline_tuning_options(speed_level=opt_level)
        pass_builder = llvm.create_pass_builder(target_machine, options)
        pass_builder.getModulePassManager().run(native, pass_builder)
    return native, target_machine


# Names owned by the compiler as @if target facts; -D may not redefine them.
RESERVED_DEFINES = (set(TARGET_OS_VALUES) | set(TARGET_ARCH_VALUES)
                    | {"TARGET_OS", "TARGET_ARCH"})


def parse_defines(items: list[str]) -> dict[str, int]:
    """Parse ``-D NAME[=VALUE]`` options into a name-to-integer mapping.

    A bare ``NAME`` defines it as 1; ``NAME=VALUE`` parses VALUE as an integer
    (C-style ``0x``/``0o``/``0b`` prefixes allowed).

    Args:
        items: The raw ``-D`` argument strings.

    Returns:
        Each defined name mapped to its integer value.

    Raises:
        ValueError: On a malformed name, a value that is not an integer, or a
            name that collides with a built-in target fact.
    """
    defines: dict[str, int] = {}
    for item in items:
        name, sep, value = item.partition("=")
        if not name.isidentifier():
            raise ValueError(f"invalid -D name {name!r}")
        if name in RESERVED_DEFINES:
            raise ValueError(f"-D{name} would redefine a built-in target fact")
        if sep:
            try:
                defines[name] = int(value, 0)
            except ValueError:
                raise ValueError(f"-D{name}: {value!r} is not an integer")
        else:
            defines[name] = 1
    return defines


def emit_interface(source: Path, search_paths: tuple[Path, ...],
                   target: str | None, defines: dict[str, int],
                   output: Path | None) -> int:
    """Write a ``.mci`` interface stub for ``source`` and return an exit status.

    Compiles the source the same way as a normal build (so ``@if`` is resolved
    and constants/enums are folded for the chosen target), then renders its
    public surface as an importable stub. Compile and I/O errors are reported as
    ``file: error: ...`` on stderr.

    Args:
        source: The entry source file to describe.
        search_paths: Import search-path directories.
        target: An LLVM target triple, or ``None`` for the host; selects which
            ``@if`` branches and constant values the interface reflects.
        defines: Command-line ``-D`` values for ``@if`` conditions.
        output: The ``.mci`` path to write, or ``None`` for ``source.mci``.

    Returns:
        0 on success, 1 on a reported error.
    """
    from mcc.interface import render_interface

    try:
        text = source.read_text()
        imports = Parser(tokenize(text)).parse_program().imports
        program = load_program(source, search_paths, compute_target_facts(target, defines))
        cg = CodeGen(program, source.name, root_source=str(source.resolve()),
                     target=target, defines=defines)
        cg.generate()
        stub = render_interface(cg, text, imports)
    except OSError as err:
        print(f"mcc: error: cannot read {source}: {err.strerror}", file=sys.stderr)
        return 1
    except LangError as err:
        where = Path(err.source) if err.source else source
        if where.is_absolute():
            try:
                where = where.relative_to(Path.cwd())
            except ValueError:
                pass
        print(f"{where}: error: {err}", file=sys.stderr)
        return 1

    out = output or source.with_suffix(".mci")
    out.write_text(stub)
    return 0


def main() -> int:
    """Run the ``mcc`` command-line interface.

    Parses arguments, compiles the source file to IR, and then -- depending on
    the flags -- prints the IR (``--emit-llvm``), emits an object file for a
    cross target (``--target``), JIT-executes ``main`` (``--run``), or links a
    native executable with ``cc``, forwarding any extra object/archive inputs
    and ``-l``/``-L`` options to the link. Compile and I/O errors are reported
    as ``file: error: ...`` on stderr.

    Returns:
        A process exit status: 0 on success, 1 on a reported error, or the
        value returned by the program's ``main`` under ``--run``.
    """
    cli = argparse.ArgumentParser(prog="mcc", description="Compile .mc source files with LLVM.")
    cli.add_argument("source", type=Path, nargs="+", metavar="source",
                     help="the .mc file to compile (exactly one); any other input "
                          "(a .o object, .a archive, or shared library) is forwarded "
                          "to the linker")
    cli.add_argument("-o", "--output", type=Path, help="output executable name")
    cli.add_argument("-l", dest="libs", action="append", default=[], metavar="NAME",
                     help="link against a library, forwarded to cc as -lNAME (repeatable)")
    cli.add_argument("-L", dest="lib_dirs", action="append", type=Path, default=[],
                     metavar="DIR",
                     help="add a library search path, forwarded to cc as -LDIR (repeatable)")
    cli.add_argument("-c", "--compile", action="store_true",
                     help="compile to an object file (.o) without linking")
    cli.add_argument("-O", type=int, default=2, choices=range(4), help="optimization level")
    cli.add_argument("--run", action="store_true", help="JIT-compile and run immediately")
    cli.add_argument("--emit-llvm", action="store_true", help="print LLVM IR and exit")
    cli.add_argument("--emit-interface", action="store_true",
                     help="write a .mci interface stub (@extern prototypes plus full "
                          "types, constants, and generic/@inline functions) and exit")
    cli.add_argument("-I", "--import-path", action="append", type=Path, default=[],
                     metavar="DIR", help="add a directory to the import search path (repeatable)")
    cli.add_argument("--nostdlib", action="store_true",
                     help="do not add the standard lib/ directory to the import search path")
    cli.add_argument("--target", metavar="TRIPLE",
                     help="cross-compile for this LLVM target triple, emitting an object "
                          "file to link with that target's toolchain")
    cli.add_argument("--general-regs-only", action="store_true",
                     help="generate code that uses only general-purpose registers, never "
                          "the floating-point/SIMD ones (like gcc's -mgeneral-regs-only)")
    cli.add_argument("--strict-align", action="store_true",
                     help="never emit unaligned memory accesses (like gcc's "
                          "-mstrict-align); required for bare-metal targets with the MMU "
                          "off, where unaligned wide loads/stores trap as alignment faults")
    cli.add_argument("--freestanding", action="store_true",
                     help="do not assume a hosted C library: stop LLVM from rewriting "
                          "standard-named calls such as printf into puts/putchar/etc. "
                          "(the -ffreestanding equivalent, for bare-metal/kernel builds)")
    cli.add_argument("-D", "--define", action="append", default=[], metavar="NAME[=VALUE]",
                     help="define a name for @if conditions; NAME alone is 1, NAME=VALUE "
                          "sets an integer (repeatable). An @if name with no -D reads as 0")
    args = cli.parse_args()

    # Split the positionals: exactly one .mc source; everything else (objects,
    # archives, shared libraries) is handed to the linker untouched.
    sources = [p for p in args.source if p.suffix == ".mc"]
    link_inputs = [p for p in args.source if p.suffix != ".mc"]
    if len(sources) != 1:
        print(f"mcc: error: expected exactly one .mc source file, got {len(sources)}",
              file=sys.stderr)
        return 1
    args.source = sources[0]

    if args.target and args.run:
        print("mcc: error: --run cannot execute cross-compiled code", file=sys.stderr)
        return 1

    if args.compile and args.run:
        print("mcc: error: --run cannot be combined with -c (compile only)",
              file=sys.stderr)
        return 1

    if link_inputs or args.libs or args.lib_dirs:
        skips_link = ("--run" if args.run else "-c" if args.compile
                      else "--target" if args.target
                      else "--emit-llvm" if args.emit_llvm
                      else "--emit-interface" if args.emit_interface else None)
        if skips_link:
            print(f"mcc: error: -l, -L, and extra link inputs apply only when "
                  f"linking an executable, not with {skips_link}", file=sys.stderr)
            return 1
        for extra in link_inputs:
            if not extra.exists():
                print(f"mcc: error: cannot read {extra}: No such file or directory",
                      file=sys.stderr)
                return 1

    try:
        defines = parse_defines(args.define)
    except ValueError as err:
        print(f"mcc: error: {err}", file=sys.stderr)
        return 1

    search_paths = list(args.import_path)
    if not args.nostdlib:
        search_paths.append(STDLIB_DIR)

    if args.emit_interface:
        return emit_interface(args.source, tuple(search_paths), args.target, defines,
                              args.output)

    try:
        module = compile_to_ir(args.source, search_paths, args.target, defines,
                               args.freestanding)
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
            module, args.O, args.target, args.general_regs_only, args.strict_align)
    except RuntimeError as err:
        print(f"mcc: error: {err}", file=sys.stderr)
        return 1

    if args.target:
        # No host linker for a foreign target: emit the object file and let
        # the target toolchain (e.g. aarch64-elf-gcc) link it.
        output = args.output or args.source.with_suffix(".o")
        output.write_bytes(target_machine.emit_object(native))
        return 0

    if args.compile:
        # Compile only: emit a native object for the host and stop, leaving the
        # link to a later mcc/cc invocation (e.g. a library shipped beside its
        # .mci interface).
        output = args.output or args.source.with_suffix(".o")
        output.write_bytes(target_machine.emit_object(native))
        return 0

    if args.run:
        with llvm.create_mcjit_compiler(native, target_machine) as jit:
            jit.finalize_object()
            address = jit.get_function_address("main")
            return ctypes.CFUNCTYPE(ctypes.c_int32)(address)()

    output = args.output or args.source.with_suffix("")
    obj_path = output.with_suffix(".o")
    if any(extra.resolve() == obj_path.resolve() for extra in link_inputs):
        # The intermediate object is deleted after the link; never let a link
        # input silently become (and then lose) it.
        print(f"mcc: error: link input {obj_path} collides with the intermediate "
              f"object; rename it or pick another -o", file=sys.stderr)
        return 1
    obj_path.write_bytes(target_machine.emit_object(native))
    link_cmd = ["cc", str(obj_path), *map(str, link_inputs), "-o", str(output)]
    link_cmd += [f"-L{directory}" for directory in args.lib_dirs]
    link_cmd += [f"-l{name}" for name in args.libs]
    if "m" not in args.libs:
        link_cmd.append("-lm")
    status = subprocess.run(link_cmd).returncode
    obj_path.unlink()
    if status != 0:
        print("mcc: error: linking failed (see the cc diagnostics above)",
              file=sys.stderr)
        return 1
    return 0
