"""Variadic function definitions and va_list forwarding.

mcc can define variadic functions and forward their arguments to C's v*
functions (vsnprintf, vfprintf, ...) through a platform va_list. Reading
individual args with va_arg is intentionally not supported.
"""

import pytest

from mcc.driver import STDLIB_DIR, compile_to_ir
from mcc.errors import LangError
from helpers import compile_ir, parse, run

# A forwarding wrapper used by several tests.
WRAPPER = r"""
import "libc/stdio";
@extern fn vsnprintf(str: uint8*, size: uint64, format: uint8*, args: va_list) -> int32;

fn logf(fmt: uint8*, ...) -> int32 {
    let buf: uint8[256];
    let ap: va_list;
    va_start(ap, fmt);
    let n = vsnprintf(&buf[0], 256, fmt, ap);
    va_end(ap);
    puts(&buf[0]);
    return n;
}
"""

# Forwarding a va_list *parameter* on, rather than a local: the parameter
# already arrives in its passed form, so the receiver must reload its slot
# instead of treating it as storage. Struct (AArch64) and array (x86-64)
# va_lists once mistyped this by a pointer level.
PARAM_WRAPPER = r"""
import "libc/stdio";
@extern fn vsnprintf(str: uint8*, size: uint64, format: uint8*, args: va_list) -> int32;

fn vlogf(fmt: uint8*, ap: va_list) -> int32 {
    let buf: uint8[256];
    let n = vsnprintf(&buf[0], 256, fmt, ap);
    puts(&buf[0]);
    return n;
}

fn logf(fmt: uint8*, ...) -> int32 {
    let ap: va_list;
    va_start(ap, fmt);
    let n = vlogf(fmt, ap);
    va_end(ap);
    return n;
}
"""


def compile_for(source, target):
    import tempfile, pathlib
    d = pathlib.Path(tempfile.mkdtemp())
    (d / "t.mc").write_text(source)
    return str(compile_to_ir(d / "t.mc", (STDLIB_DIR,), target))


# --- parsing / definitions ---

def test_variadic_definition_parses():
    (func,) = parse("fn f(x: int32, ...) { return; }").functions
    assert func.variadic and not func.extern


def test_variadic_function_is_emitted_var_arg():
    ir_text = compile_ir("fn f(x: int32, ...) -> int32 { return x; }")
    assert "define i32 @\"f\"(i32 %\"x\", ...)" in ir_text


# --- end-to-end forwarding (host) ---

def test_forwarding_through_vsnprintf(capfd):
    source = WRAPPER + r"""
    fn main() -> int32 {
        let n = logf("%s = %d (0x%X)", "answer", 42, 255);
        printf("[%d]\n", n);
        return 0;
    }
    """
    run(source)
    assert capfd.readouterr().out == "answer = 42 (0xFF)\n[18]\n"


def test_forwarding_a_va_list_parameter(capfd):
    # The middle function receives the va_list as a parameter and forwards it
    # again, so the passed-form slot must be reloaded, not taken by address.
    source = PARAM_WRAPPER + r"""
    fn main() -> int32 {
        logf("%s = %d (0x%X)", "answer", 42, 255);
        return 0;
    }
    """
    run(source)
    assert capfd.readouterr().out == "answer = 42 (0xFF)\n"


@pytest.mark.parametrize("target", [
    "aarch64-unknown-none-elf",   # struct va_list (passed by address)
    "x86_64-unknown-linux-gnu",   # array va_list (decays to a tag pointer)
])
def test_forwarding_a_va_list_parameter_cross_abi(target):
    # Regression: forwarding a va_list parameter once mistyped the argument by
    # a pointer level on the struct and array ABIs. It must now type-check.
    compile_for(PARAM_WRAPPER, target)


# --- platform va_list layout ---

def test_apple_arm64_va_list_is_a_pointer():
    ir_text = compile_for(WRAPPER, "arm64-apple-darwin")
    assert '%"ap" = alloca i8*' in ir_text


def test_x86_64_va_list_is_the_tag_array():
    ir_text = compile_for(WRAPPER, "x86_64-unknown-linux-gnu")
    assert '%"struct.__va_list_tag" = type {i32, i32, i8*, i8*}' in ir_text
    assert 'alloca [1 x %"struct.__va_list_tag"], align 16' in ir_text


def test_aarch64_va_list_is_the_struct():
    ir_text = compile_for(WRAPPER, "aarch64-unknown-none-elf")
    assert '%"struct.__va_list" = type {i8*, i8*, i8*, i32, i32}' in ir_text
    assert 'alloca %"struct.__va_list"' in ir_text


def test_va_start_and_va_end_are_intrinsics():
    ir_text = compile_for(WRAPPER, "arm64-apple-darwin")
    assert "declare void @\"llvm.va_start\"(i8*" in ir_text
    assert "declare void @\"llvm.va_end\"(i8*" in ir_text


# --- errors ---

def test_va_start_outside_a_variadic_function():
    with pytest.raises(LangError, match="va_start is only valid inside a variadic"):
        compile_ir("fn f() { let ap: va_list; va_start(ap, ap); }")


def test_va_start_arity():
    with pytest.raises(LangError, match=r"va_start\(ap, last_named_param\) takes 2"):
        compile_ir("fn f(x: int32, ...) { let ap: va_list; va_start(ap); }")


def test_va_end_arity():
    with pytest.raises(LangError, match=r"va_end\(ap\) takes 1"):
        compile_ir("fn f(x: int32, ...) { let ap: va_list; va_start(ap, x); va_end(ap, x); }")


def test_va_start_requires_a_va_list():
    with pytest.raises(LangError, match="va_start requires a va_list"):
        compile_ir("fn f(x: int32, ...) { let y: int32; va_start(y, x); }")


def test_unsupported_architecture():
    with pytest.raises(LangError, match="va_list is not supported for target architecture"):
        compile_for("fn f(x: int32, ...) { let ap: va_list; va_start(ap, x); }",
                    "riscv64-unknown-elf")
