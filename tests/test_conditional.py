"""Compile-time @if: conditional compilation over the target facts."""

import pytest

from mcc.codegen import CodeGen
from mcc.errors import LangError
from helpers import parse, run, run_path


def run_for(source: str, target: str) -> str:
    """Compile to IR text as if cross-compiling for `target`."""
    return str(CodeGen(parse(source), "test", target=target).generate())


def ir_with_defines(source: str, defines: dict[str, int]) -> str:
    """Compile to IR text with the given `-D` defines in effect."""
    return str(CodeGen(parse(source), "test", defines=defines).generate())


# --- top-level @if selects declarations ---

def test_toplevel_if_selects_a_function():
    src = """
    @if (TARGET_OS == OS_LINUX) {
        fn pick() -> int32 { return 1; }
    } @else {
        fn pick() -> int32 { return 2; }
    }
    fn main() -> int32 { return pick(); }
    """
    # Cross-compiling for Linux takes the first branch; otherwise the @else.
    assert "ret i32 1" in run_for(src, "x86_64-unknown-linux-gnu")
    assert "ret i32 2" in run_for(src, "aarch64-unknown-none-elf")


def test_toplevel_if_selects_a_const():
    src = """
    @if (TARGET_ARCH == ARCH_AARCH64) {
        const WORD = 8;
    } @else {
        const WORD = 4;
    }
    fn main() -> int32 { return WORD; }
    """
    assert "ret i32 8" in run_for(src, "aarch64-unknown-none-elf")
    assert "ret i32 4" in run_for(src, "x86_64-unknown-linux-gnu")


def test_toplevel_if_selects_a_struct_and_global():
    src = """
    @if (TARGET_OS == OS_DARWIN) {
        struct Box { x: int32; }
        @static let n: int32 = 1;
    } @else {
        struct Box { x: int64; }
        @static let n: int32 = 2;
    }
    fn main() -> int32 {
        let b: struct Box;
        b.x = 0;
        return n;
    }
    """
    mac = run_for(src, "arm64-apple-darwin")
    assert '%"Box" = type {i32}' in mac and "global i32 1" in mac
    lin = run_for(src, "x86_64-unknown-linux-gnu")
    assert '%"Box" = type {i64}' in lin and "global i32 2" in lin


def test_dead_branch_is_not_type_checked():
    # The Linux branch calls an undefined function; on a non-Linux target it is
    # dropped without ever being compiled, so it is not an error.
    src = """
    @if (TARGET_OS == OS_LINUX) {
        fn f() -> int32 { return undefined_nonsense(); }
    } @else {
        fn f() -> int32 { return 42; }
    }
    fn main() -> int32 { return f(); }
    """
    assert "ret i32 42" in run_for(src, "aarch64-unknown-none-elf")


def test_else_if_chain():
    src = """
    @if (TARGET_ARCH == ARCH_X86_64) {
        fn arch() -> int32 { return 1; }
    } @else @if (TARGET_ARCH == ARCH_AARCH64) {
        fn arch() -> int32 { return 2; }
    } @else {
        fn arch() -> int32 { return 0; }
    }
    fn main() -> int32 { return arch(); }
    """
    assert "ret i32 1" in run_for(src, "x86_64-unknown-linux-gnu")
    assert "ret i32 2" in run_for(src, "aarch64-unknown-none-elf")
    assert "ret i32 0" in run_for(src, "riscv64-unknown-elf")


def test_nested_toplevel_if():
    src = """
    @if (TARGET_OS == OS_DARWIN) {
        @if (TARGET_ARCH == ARCH_AARCH64) {
            fn v() -> int32 { return 64; }
        } @else {
            fn v() -> int32 { return 32; }
        }
    } @else {
        fn v() -> int32 { return 0; }
    }
    fn main() -> int32 { return v(); }
    """
    assert "ret i32 64" in run_for(src, "arm64-apple-darwin")
    assert "ret i32 0" in run_for(src, "x86_64-unknown-linux-gnu")


def test_if_without_else_drops_the_block():
    src = """
    @if (TARGET_OS == OS_LINUX) {
        @extern fn only_on_linux() -> int32;
    }
    fn main() -> int32 { return 0; }
    """
    assert "only_on_linux" not in run_for(src, "arm64-apple-darwin")
    assert "only_on_linux" in run_for(src, "x86_64-unknown-linux-gnu")


def test_symbol_alias_selected_by_platform():
    # The motivating case: stdout's linker symbol differs by OS.
    src = """
    struct FILE {}
    @if (TARGET_OS == OS_DARWIN) {
        @extern @symbol("__stdoutp") let stdout: struct FILE*;
    } @else {
        @extern @symbol("stdout") let stdout: struct FILE*;
    }
    fn main() -> int32 { if (stdout == null) { return 1; } return 0; }
    """
    mac = run_for(src, "arm64-apple-darwin")
    assert '@"__stdoutp" = external global' in mac
    lin = run_for(src, "x86_64-unknown-linux-gnu")
    assert '@"stdout" = external global' in lin and "__stdoutp" not in lin


# --- statement-level @if ---

def test_statement_if_picks_a_branch():
    src = """
    fn main() -> int32 {
        @if (TARGET_ARCH == ARCH_AARCH64) { return 7; } @else { return 8; }
    }
    """
    assert "ret i32 7" in run_for(src, "arm64-apple-darwin")
    assert "ret i32 8" in run_for(src, "x86_64-unknown-linux-gnu")


def test_statement_if_inlines_into_current_scope():
    # A binding declared in the live branch is visible afterwards: @if does not
    # open a runtime scope, it just selects statements inline. The condition is
    # true on any real target, so the result does not depend on the host arch.
    src = """
    fn main() -> int32 {
        @if (TARGET_ARCH != ARCH_UNKNOWN) { let x = 5 as int32; } @else { let x = 9 as int32; }
        return x;
    }
    """
    assert run(src) == 5


def test_statement_dead_branch_is_not_compiled():
    src = """
    fn main() -> int32 {
        @if (TARGET_OS == OS_LINUX) {
            return undefined_thing();
        } @else {
            return 3;
        }
    }
    """
    assert "ret i32 3" in run_for(src, "arm64-apple-darwin")


# --- conditions ---

def test_logical_and_or_not_in_conditions():
    src = """
    @if (TARGET_ARCH == ARCH_X86_64 or TARGET_ARCH == ARCH_AARCH64) {
        fn supported() -> int32 { return 1; }
    } @else {
        fn supported() -> int32 { return 0; }
    }
    fn main() -> int32 { return supported(); }
    """
    assert "ret i32 1" in run_for(src, "arm64-apple-darwin")
    assert "ret i32 0" in run_for(src, "riscv64-unknown-elf")

    notted = """
    @if (!(TARGET_OS == OS_NONE)) {
        fn hosted() -> int32 { return 1; }
    } @else {
        fn hosted() -> int32 { return 0; }
    }
    fn main() -> int32 { return hosted(); }
    """
    assert "ret i32 0" in run_for(notted, "aarch64-unknown-none-elf")
    assert "ret i32 1" in run_for(notted, "arm64-apple-darwin")


def test_arithmetic_and_truthiness_in_conditions():
    # A bare nonzero value is true; arithmetic and bitwise ops are allowed.
    src = """
    @if (ARCH_AARCH64 * 2 - 4 + (1 << 1) & 7) {
        fn t() -> int32 { return 1; }
    } @else {
        fn t() -> int32 { return 0; }
    }
    fn main() -> int32 { return t(); }
    """
    # ARCH_AARCH64 == 2: 2*2-4 + (2) & 7 = (0 + 2) & 7 = 2 -> truthy
    assert run(src) == 1


# --- errors ---

def test_user_const_in_a_condition_reads_as_zero():
    # A user const is not an @if fact; like any name without a -D it reads as 0
    # (as in C's #if), so the @else branch is taken rather than erroring.
    src = ("const X = 1;\n"
           "@if (X) { fn f() -> int32 { return 1; } }\n"
           "@else { fn f() -> int32 { return 2; } }\n"
           "fn main() -> int32 { return f(); }")
    assert run(src) == 2


# --- command-line -D defines ---

def test_define_makes_a_condition_true():
    src = """
    @if (DEBUG) {
        fn build() -> int32 { return 1; }
    } @else {
        fn build() -> int32 { return 2; }
    }
    fn main() -> int32 { return build(); }
    """
    assert "ret i32 1" in ir_with_defines(src, {"DEBUG": 1})
    assert "ret i32 2" in ir_with_defines(src, {})  # absent: falsy, takes @else


def test_define_with_an_integer_value():
    src = """
    @if (LEVEL >= 2) {
        fn lvl() -> int32 { return 1; }
    } @else {
        fn lvl() -> int32 { return 0; }
    }
    fn main() -> int32 { return lvl(); }
    """
    assert "ret i32 1" in ir_with_defines(src, {"LEVEL": 3})
    assert "ret i32 0" in ir_with_defines(src, {"LEVEL": 1})


def test_define_is_not_an_ordinary_constant():
    # -D names exist only for @if; using one as a value is still undefined.
    with pytest.raises(LangError, match="undefined variable 'WIDTH'"):
        ir_with_defines("fn main() -> int32 { return WIDTH; }", {"WIDTH": 4})


def test_stray_else_at_top_level():
    with pytest.raises(LangError, match="@else without a matching @if"):
        parse("@else { fn f() {} }")


def test_stray_else_as_a_statement():
    with pytest.raises(LangError, match="@else without a matching @if"):
        parse("fn main() -> int32 { @else { return 0; } }")


def test_if_cannot_be_combined_with_annotations():
    with pytest.raises(LangError, match="cannot be combined with other annotations"):
        parse("@private @if (TARGET_OS == OS_DARWIN) { fn f() {} }")


def test_import_inside_if_is_rejected():
    with pytest.raises(LangError, match="import is not allowed inside @if"):
        parse('@if (TARGET_OS == OS_DARWIN) { import "x"; }')


def test_disallowed_operator_in_condition():
    with pytest.raises(LangError, match="must be a constant expression"):
        run('@if ("text") { fn f() {} }\nfn main() -> int32 { return 0; }')


def test_division_by_zero_in_condition():
    with pytest.raises(LangError, match="division by zero in an @if condition"):
        run("@if (1 / 0) { fn f() {} }\nfn main() -> int32 { return 0; }")


# --- through the driver (import resolution + source stamping) ---

def test_toplevel_if_in_an_imported_file(tmp_path):
    # The host (the test's own machine) is never OS_NONE, so the @else branch's
    # function is the one compiled, and main can call it across the import.
    (tmp_path / "lib.mc").write_text("""
    @if (TARGET_OS == OS_NONE) {
        fn answer() -> int32 { return 0; }
    } @else {
        fn answer() -> int32 { return 42; }
    }
    """)
    main = tmp_path / "main.mc"
    main.write_text('import "lib";\nfn main() -> int32 { return answer(); }')
    assert run_path(main) == 42


def test_private_in_a_branch_stays_private(tmp_path):
    # A @private declaration selected by a top-level @if keeps its file scope:
    # source stamping must survive flattening.
    (tmp_path / "lib.mc").write_text("""
    @if (TARGET_ARCH == ARCH_AARCH64) {
        @private fn secret() -> int32 { return 1; }
    } @else {
        @private fn secret() -> int32 { return 2; }
    }
    """)
    main = tmp_path / "main.mc"
    main.write_text('import "lib";\nfn main() -> int32 { return secret(); }')
    with pytest.raises(LangError, match="function 'secret' is private"):
        run_path(main)
