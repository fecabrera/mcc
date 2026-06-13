"""--general-regs-only: keep generated code off the FP/SIMD registers, the
equivalent of gcc's -mgeneral-regs-only (useful for kernel/interrupt code)."""

import pytest

from mcc.driver import (
    build_native_module,
    compile_to_ir,
    restrict_to_general_regs,
)

AARCH64 = "aarch64-unknown-none-elf"

# A struct copy the aarch64 backend lowers to a 128-bit SIMD load/store by
# default, so it shows the difference the flag makes.
STRUCT_COPY = """
struct vec { a: int64; b: int64; c: int64; d: int64; }
fn copy(dst: struct vec*, src: struct vec*) {
    *dst = *src;
}
"""


def compile_module(tmp_path, source):
    path = tmp_path / "t.mc"
    path.write_text(source)
    return compile_to_ir(path, ())


def uses_vector_reg(asm: str) -> bool:
    # aarch64 SIMD/FP registers are q0.. / v0.. / d0.. -- look for them as
    # register operands (preceded by whitespace or a comma).
    import re
    return re.search(r"[\s,](?:q|v|d)\d", asm) is not None


def test_attribute_is_added_to_definitions_only(tmp_path):
    module = compile_module(tmp_path, STRUCT_COPY + "@extern fn ext(x: int64);\n")
    restrict_to_general_regs(module, AARCH64)
    text = str(module)
    # the definition carries the feature attribute...
    (define,) = [ln for ln in text.splitlines() if ln.startswith("define")]
    assert '"target-features"="-fp-armv8,-neon"' in define
    # ...but the extern declaration does not.
    (declare,) = [ln for ln in text.splitlines() if ln.startswith("declare")]
    assert "target-features" not in declare


def test_unknown_architecture_is_rejected():
    from llvmlite import ir
    with pytest.raises(RuntimeError, match="not supported for target 'sparc'"):
        restrict_to_general_regs(ir.Module(), "sparc-unknown-none")


def test_default_aarch64_build_uses_simd(tmp_path):
    module = compile_module(tmp_path, STRUCT_COPY)
    native, tm = build_native_module(module, 2, AARCH64, general_regs_only=False)
    assert uses_vector_reg(tm.emit_assembly(native))


def test_general_regs_only_build_avoids_simd(tmp_path):
    module = compile_module(tmp_path, STRUCT_COPY)
    native, tm = build_native_module(module, 2, AARCH64, general_regs_only=True)
    assert not uses_vector_reg(tm.emit_assembly(native))
