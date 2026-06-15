"""Cross-compiled objects must use the small code model + static relocations.

A freestanding image (e.g. a bare-metal kernel) is linked at a fixed address
with no loader to fill a GOT. The JIT defaults -- large code model (absolute
movz/movk for locals) plus PIC (GOT indirection for externs) -- produce
addressing such an image cannot satisfy, which showed up as @static globals
reading back as zero. Cross builds must instead emit plain ADRP+ADD/LDR, the
model aarch64-elf-gcc uses for freestanding code.
"""

from mcc.driver import build_native_module, compile_to_ir

AARCH64 = "aarch64-unknown-none-elf"

# A global read *and* written, so the optimizer cannot fold it to a constant
# and drop the reference -- the access must survive to show its addressing.
GLOBALS = """
@static let s: int64;
@extern let e: int64;
fn store(v: int64) { s = v; e = v; }
fn load() -> int64 { return s + e; }
"""


def cross_asm(tmp_path, source: str) -> str:
    path = tmp_path / "t.mc"
    path.write_text(source)
    module = compile_to_ir(path, ())
    native, tm = build_native_module(module, 2, AARCH64)
    return tm.emit_assembly(native)


def test_cross_uses_adrp_based_addressing(tmp_path):
    # Small code model reaches globals via ADRP (+ :lo12:/:got_lo12:) ...
    asm = cross_asm(tmp_path, GLOBALS)
    assert "adrp" in asm


def test_cross_has_no_large_model_absolute_moves(tmp_path):
    # ... not the large code model's movz/movk absolute build (:abs_g0:/:abs_g1:
    # ...), which is what the JIT default produced and what a fixed-load image
    # read back as garbage.
    asm = cross_asm(tmp_path, GLOBALS)
    assert ":abs_g" not in asm
