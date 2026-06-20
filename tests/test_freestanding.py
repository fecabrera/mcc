"""--freestanding: don't assume a hosted C library.

Without it, LLVM's libcall optimizer rewrites standard-named calls (a
user printf into puts/putchar, etc.), synthesizing references to symbols a
bare-metal program never defines. --freestanding marks every definition
"no-builtins" to stop that.
"""

import pathlib
import tempfile

from mcc.driver import STDLIB_DIR, build_native_module, compile_to_ir

# A user-defined printf whose calls LLVM would rewrite into puts/putchar.
SRC = """
@extern fn sink(fd: int64, p: uint8*, n: uint64) -> int64;
fn printf(format: uint8*, ...) -> int32 { sink(1, format, 1); return 0; }
fn main() -> int32 {
    printf("hi\\n");      // -> puts at -O2
    printf("%c", 65);     // -> putchar at -O2
    return 0;
}
"""

TARGET = "aarch64-unknown-none-elf"


def ir_for(freestanding: bool) -> str:
    d = pathlib.Path(tempfile.mkdtemp())
    (d / "t.mc").write_text(SRC)
    return str(compile_to_ir(d / "t.mc", (STDLIB_DIR,), TARGET, None, freestanding))


def optimized_ir(freestanding: bool) -> str:
    d = pathlib.Path(tempfile.mkdtemp())
    (d / "t.mc").write_text(SRC)
    module = compile_to_ir(d / "t.mc", (STDLIB_DIR,), TARGET, None, freestanding)
    native, _ = build_native_module(module, 2, TARGET)
    return str(native)


def test_flag_tags_definitions_no_builtins():
    out = ir_for(freestanding=True)
    assert 'define i32 @"printf"(i8* %"format", ...) "no-builtins"' in out
    assert 'define i32 @"main"() "no-builtins"' in out


def test_extern_declarations_are_not_tagged():
    # The attribute belongs on callers (definitions); a bodyless extern makes
    # no calls and must stay a plain declaration.
    out = ir_for(freestanding=True)
    declares = [line for line in out.splitlines() if line.startswith("declare")]
    assert any('@"sink"' in line for line in declares)
    assert all("no-builtins" not in line for line in declares)


def test_default_build_synthesizes_libcalls():
    # The behavior --freestanding exists to prevent: printf rewritten to
    # puts/putchar by the optimizer.
    out = optimized_ir(freestanding=False)
    assert "puts" in out or "putchar" in out


def test_freestanding_suppresses_libcall_synthesis():
    out = optimized_ir(freestanding=True)
    assert "puts" not in out and "putchar" not in out
