"""Subtarget feature flags driven through the shared target-features attribute:
--general-regs-only (off the FP/SIMD registers, like gcc's -mgeneral-regs-only)
and --strict-align (no unaligned accesses, like gcc's -mstrict-align). Both are
merged into the single target-features attribute LLVM honors per function."""

import pytest

from mcc.driver import (
    apply_target_features,
    build_native_module,
    compile_to_ir,
    general_regs_features,
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


# --- the shared target-features mechanism / --strict-align -----------------


def define_line(module) -> str:
    return next(ln for ln in str(module).splitlines() if ln.startswith("define"))


def test_general_regs_features_string():
    assert general_regs_features(AARCH64) == "-fp-armv8,-neon"


def test_general_regs_features_rejects_unknown_arch():
    with pytest.raises(RuntimeError, match="not supported for target 'sparc'"):
        general_regs_features("sparc-unknown-none")


def test_apply_target_features_merges_into_one_attribute(tmp_path):
    # LLVM honors a single target-features per function: one combined attribute,
    # never two.
    module = compile_module(tmp_path, STRUCT_COPY + "@extern fn ext(x: int64);\n")
    apply_target_features(module, "-fp-armv8,-neon,+strict-align")
    define = define_line(module)
    assert define.count('"target-features"=') == 1
    assert '"target-features"="-fp-armv8,-neon,+strict-align"' in define
    # extern declarations stay untouched.
    (declare,) = [ln for ln in str(module).splitlines() if ln.startswith("declare")]
    assert "target-features" not in declare


def test_strict_align_adds_feature(tmp_path):
    module = compile_module(tmp_path, STRUCT_COPY)
    build_native_module(module, 0, AARCH64, strict_align=True)
    define = define_line(module)
    assert '"target-features"="+strict-align"' in define


def test_strict_align_and_general_regs_combine(tmp_path):
    # Both flags together collapse into one attribute carrying every feature.
    module = compile_module(tmp_path, STRUCT_COPY)
    build_native_module(
        module, 0, AARCH64, general_regs_only=True, strict_align=True
    )
    define = define_line(module)
    assert define.count('"target-features"=') == 1
    assert '"target-features"="-fp-armv8,-neon,+strict-align"' in define


def test_no_feature_flags_leaves_module_untagged(tmp_path):
    module = compile_module(tmp_path, STRUCT_COPY)
    build_native_module(module, 0, AARCH64)
    assert "target-features" not in str(module)
