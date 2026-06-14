"""Built-in target facts: TARGET_OS / TARGET_ARCH and the OS_*/ARCH_* enums."""

import pytest

from mcc.codegen import CodeGen, classify_arch, classify_os
from mcc.errors import LangError
from helpers import parse, run


def compile_for(source: str, target: str) -> str:
    """Compile source to IR text as if cross-compiling for `target`."""
    return str(CodeGen(parse(source), "test", target=target).generate())


@pytest.mark.parametrize("triple, os_name", [
    ("arm64-apple-darwin25.5.0", "OS_DARWIN"),
    ("x86_64-unknown-linux-gnu", "OS_LINUX"),
    ("x86_64-pc-windows-msvc", "OS_WINDOWS"),
    ("aarch64-unknown-none-elf", "OS_NONE"),  # bare metal: no OS
    ("not-a-triple", "OS_UNKNOWN"),
])
def test_classify_os(triple, os_name):
    assert classify_os(triple) == os_name


@pytest.mark.parametrize("triple, arch_name", [
    ("x86_64-unknown-linux-gnu", "ARCH_X86_64"),
    ("amd64-pc-windows-msvc", "ARCH_X86_64"),
    ("aarch64-unknown-none-elf", "ARCH_AARCH64"),
    ("arm64-apple-darwin25.5.0", "ARCH_AARCH64"),
    ("riscv64-unknown-elf", "ARCH_RISCV64"),
    ("not-a-triple", "ARCH_UNKNOWN"),
])
def test_classify_arch(triple, arch_name):
    assert classify_arch(triple) == arch_name


def test_target_os_selects_the_matching_enum():
    # Cross-compiling for Linux makes TARGET_OS fold to OS_LINUX (2).
    ir = compile_for(
        "fn main() -> int32 { return TARGET_OS; }", "x86_64-unknown-linux-gnu"
    )
    assert "ret i32 2" in ir


def test_target_arch_for_bare_metal():
    # aarch64-unknown-none-elf: ARCH_AARCH64 (2), and OS_NONE for TARGET_OS.
    ir = compile_for(
        "fn main() -> int32 { return TARGET_ARCH; }", "aarch64-unknown-none-elf"
    )
    assert "ret i32 2" in ir
    ir = compile_for(
        "fn main() -> int32 { return TARGET_OS; }", "aarch64-unknown-none-elf"
    )
    assert "ret i32 4" in ir  # OS_NONE


def test_enum_values_are_available_as_constants():
    assert run("fn main() -> int32 { return OS_LINUX + ARCH_AARCH64; }") == 4


def test_host_target_os_compares_equal():
    # Running in-process targets the host; whatever it is, TARGET_OS must equal
    # exactly one known OS and not the others.
    assert run(
        "fn main() -> int32 { if (TARGET_OS == OS_UNKNOWN) { return 1; } return 0; }"
    ) == 0


def test_target_const_usable_as_array_size():
    assert run(
        "fn main() -> int32 { let a: int32[ARCH_AARCH64]; return len(a) as int32; }"
    ) == 2


def test_user_const_cannot_shadow_a_target_fact():
    with pytest.raises(LangError, match="constant 'TARGET_OS' already defined"):
        run("const TARGET_OS = 9;\nfn main() -> int32 { return 0; }")


def test_user_const_cannot_shadow_an_enum_value():
    with pytest.raises(LangError, match="constant 'OS_LINUX' already defined"):
        run("const OS_LINUX = 9;\nfn main() -> int32 { return 0; }")
