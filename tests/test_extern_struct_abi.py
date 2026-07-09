"""C struct-passing ABI at the ``@extern`` boundary (AArch64/AAPCS64).

Two halves: fast in-process checks of the classified IR shape and the
non-AArch64 gating error (compiled for a fixed target, no run), and linked
round-trip tests that compile a C fixture, link it with an mcc program, and run
the result -- the only true oracle for ABI correctness. The linked tests run
only on an AArch64 host, where the toolchain's C ABI is the one mcc classifies
for.
"""

import platform
import subprocess
import sys
from pathlib import Path

import pytest

from mcc.errors import LangError
from helpers import compile_ir

ROOT = Path(__file__).resolve().parents[1]
AARCH64 = "arm64-apple-darwin"
ON_AARCH64_HOST = platform.machine().lower() in ("arm64", "aarch64")

# Struct declarations reused across the IR-shape cases.
DECLS = """
struct S2 { a: int32; b: int32; }
struct S3 { a: int32; b: int32; c: int32; }
struct Point { x: float64; y: float64; }
struct V3 { x: float64; y: float64; z: float64; }
struct Mixed { a: int32; b: float64; }
struct Big { a: int64; b: int64; c: int64; }
union U { i: int32; d: float64; }
"""


def ir_for(decl: str, target: str = AARCH64) -> str:
    """Compile DECLS plus one @extern declaration and a call, for a target."""
    body = (
        DECLS
        + decl
        + "\nfn main() -> int32 { return 0; }\n"
    )
    return compile_ir(body, target=target)


# --- classified declaration IR shape (AArch64) --------------------------------


def test_small_int_struct_passes_in_one_gpr():
    # {int32,int32} is 8 bytes -> a single i64 register.
    ir_text = ir_for("@extern fn f(s: struct S2) -> int32;")
    assert 'declare i32 @"f"(i64 ' in ir_text


def test_two_gpr_int_struct_passes_as_i64_pair():
    # {int32,int32,int32} is 12 bytes -> [2 x i64].
    ir_text = ir_for("@extern fn f(s: struct S3) -> int32;")
    assert 'declare i32 @"f"([2 x i64] ' in ir_text


def test_two_double_hfa_passes_in_fp_registers():
    # {double,double} is a homogeneous float aggregate -> [2 x double].
    ir_text = ir_for("@extern fn f(p: struct Point) -> float64;")
    assert 'declare double @"f"([2 x double] ' in ir_text


def test_three_double_hfa_over_16_bytes_stays_in_fp_registers():
    # 24 bytes, but a 3-member HFA rides in FP registers, not indirectly.
    ir_text = ir_for("@extern fn f(v: struct V3) -> float64;")
    assert 'declare double @"f"([3 x double] ' in ir_text


def test_float_array_field_counts_toward_the_hfa():
    # An array of doubles is a run of HFA members: float64[4] is a 4-double HFA.
    ir_text = compile_ir(
        "struct M4 { xs: float64[4]; }\n"
        "@extern fn f(m: struct M4) -> float64;\n"
        "fn main() -> int32 { return 0; }\n",
        target=AARCH64,
    )
    assert 'declare double @"f"([4 x double] ' in ir_text


def test_int_array_field_makes_the_aggregate_a_gpr_struct():
    # A non-float array member disqualifies the HFA: int32[4] is 16 bytes and
    # rides in a GPR pair, not FP registers.
    ir_text = compile_ir(
        "struct IA { xs: int32[4]; }\n"
        "@extern fn f(m: struct IA) -> int32;\n"
        "fn main() -> int32 { return 0; }\n",
        target=AARCH64,
    )
    assert 'declare i32 @"f"([2 x i64] ' in ir_text


def test_mixed_int_and_float_struct_uses_gprs_not_an_hfa():
    # {int32,double} is 16 bytes but not homogeneous -> GPR pair, never FP.
    ir_text = ir_for("@extern fn f(m: struct Mixed) -> int32;")
    assert 'declare i32 @"f"([2 x i64] ' in ir_text


def test_union_is_never_an_hfa():
    # A union overlays its members, so even one holding a double passes in a
    # GPR (by size, 8 bytes -> i64), never as a float aggregate.
    ir_text = ir_for("@extern fn f(u: union U) -> int32;")
    assert 'declare i32 @"f"(i64 ' in ir_text


def test_large_struct_argument_passes_by_pointer_without_byval():
    # >16 bytes -> indirect: a pointer to a caller-owned copy. Matches clang's
    # AArch64 lowering, which does not use the byval attribute.
    ir_text = ir_for("@extern fn f(b: struct Big) -> int64;")
    assert 'declare i64 @"f"(%"Big"* ' in ir_text
    assert "byval" not in ir_text


def test_large_struct_return_uses_an_sret_pointer():
    # >16 bytes returned -> a hidden sret pointer, function returns void.
    ir_text = ir_for("@extern fn f() -> struct Big;")
    assert 'declare void @"f"(%"Big"* sret(%"Big")' in ir_text


def test_small_struct_return_comes_back_in_a_register():
    ir_text = ir_for("@extern fn f() -> struct S2;")
    assert 'declare i64 @"f"()' in ir_text


def test_scalar_only_extern_is_left_unchanged():
    # No aggregate crosses the boundary, so no ABI plan and no coercion.
    ir_text = ir_for("@extern fn f(x: int32, p: int64*) -> int32;")
    assert 'declare i32 @"f"(i32 %".1", i64* %".2")' in ir_text


# --- call-site marshalling IR (AArch64) ---------------------------------------
#
# These call the externs (not just declare them), so the call-site marshalling
# and return reconstruction are exercised during codegen on any host -- the
# linked round-trip below is AArch64-only, so these keep that path covered
# everywhere.


def call_ir(decls: str, body: str) -> str:
    return compile_ir(
        DECLS + decls + "\nfn main() -> int32 {\n" + body + "\n}\n", target=AARCH64
    )


def test_direct_struct_argument_is_spilled_and_reloaded_as_its_coercion():
    ir_text = call_ir(
        "@extern fn f(s: struct S2) -> int32;",
        "let s: struct S2 = S2 { a = 1, b = 2 };\n    return f(s);",
    )
    # The struct is stored through a coercion-typed slot and reloaded as i64.
    assert 'bitcast i64* %".' in ir_text
    assert 'call i32 @"f"(i64 ' in ir_text


def test_direct_struct_return_is_stored_and_reloaded_as_the_struct():
    ir_text = call_ir(
        "@extern fn f() -> struct S2;",
        "let r: struct S2 = f();\n    return r.a;",
    )
    assert '%".' in ir_text and 'call i64 @"f"()' in ir_text
    # The i64 result is stored to a slot then bitcast back to the struct.
    assert 'bitcast i64* %".' in ir_text


def test_indirect_struct_argument_passes_a_plain_pointer_to_a_copy():
    ir_text = call_ir(
        "@extern fn f(b: struct Big) -> int64;",
        "let b: struct Big = Big { a = 1, b = 2, c = 3 };\n"
        "    return f(b) as int32;",
    )
    assert 'call i64 @"f"(%"Big"* ' in ir_text
    assert "byval" not in ir_text


def test_indirect_struct_return_allocates_the_sret_slot_at_the_call():
    ir_text = call_ir(
        "@extern fn f() -> struct Big;",
        "let b: struct Big = f();\n    return b.a as int32;",
    )
    # The call returns void and receives the caller's slot as the sret pointer.
    assert 'call void @"f"(%"Big"* sret(%"Big") ' in ir_text


# --- non-AArch64 gating -------------------------------------------------------


@pytest.mark.parametrize(
    "target",
    ["x86_64-unknown-linux-gnu", "riscv64-unknown-linux", "x86_64-pc-windows-msvc"],
)
def test_struct_by_value_extern_is_rejected_off_aarch64(target):
    with pytest.raises(
        LangError,
        match="passing a struct by value across the C boundary is not supported",
    ):
        ir_for("@extern fn f(s: struct S2) -> int32;", target=target)


def test_struct_return_extern_is_rejected_off_aarch64():
    with pytest.raises(
        LangError,
        match="passing a struct by value across the C boundary is not supported",
    ):
        ir_for("@extern fn f() -> struct Big;", target="x86_64-unknown-linux-gnu")


def test_scalar_extern_still_compiles_off_aarch64():
    # The gate is specific to by-value aggregates: scalar/pointer externs are
    # unaffected on every target.
    ir_text = ir_for(
        "@extern fn f(p: struct S2*) -> int32;", target="x86_64-unknown-linux-gnu"
    )
    assert 'declare i32 @"f"(%"S2"* ' in ir_text


# --- classifier unit (a shape mcc source cannot yet spell) --------------------


def test_heterogeneous_float_aggregate_is_not_an_hfa():
    # mcc has only float64, so a mixed float/double aggregate is unreachable
    # from source -- but the classifier's homogeneity rule still guards it: a
    # heterogeneous float aggregate is not an HFA and falls back to the GPR
    # (by-size) classification. Built directly to exercise that contract.
    import llvmlite.ir as ir

    from mcc.codegen.abi import Direct, classify_aggregate
    from mcc.codegen.types import LangType

    f32 = LangType("float32", ir.FloatType())
    f64 = LangType("float64", ir.DoubleType())
    body = ir.LiteralStructType([ir.FloatType(), ir.DoubleType()])
    mixed = LangType("mixed", body, fields=(("a", f32), ("b", f64)))

    result = classify_aggregate(mixed)
    assert isinstance(result, Direct)
    assert result.coerce_ir == ir.ArrayType(ir.IntType(64), 2)  # GPR pair, not FP


# --- linked round-trip through a C fixture (the ABI oracle) --------------------

FIXTURE_C = r"""
struct S2  { int a; int b; };
struct S3  { int a; int b; int c; };
struct Point { double x; double y; };
struct V3  { double x; double y; double z; };
struct Mixed { int a; double b; };
struct Big { long a; long b; long c; };
union  U   { int i; double d; };

int    s2_swap_ok(struct S2 s)   { return (s.a == 11 && s.b == 22); }
struct S2 s2_swap(struct S2 s)   { struct S2 r; r.a = s.b; r.b = s.a; return r; }
int    s3_sum(struct S3 s)       { return s.a + s.b + s.c; }
double pt_sum(struct Point p)    { return p.x + p.y; }
struct V3 v3_scale(struct V3 v, double k){ struct V3 r={v.x*k,v.y*k,v.z*k}; return r; }
int    mixed_ok(struct Mixed m)  { return (m.a == 7 && m.b == 3.5); }
struct Big big_make(long base)   { struct Big r; r.a=base; r.b=base+1; r.c=base+2; return r; }
long   big_sum(struct Big b)     { return b.a + b.b + b.c; }
int    union_get(union U u)      { return u.i; }
/* Eight GPR args exhaust x0-x7, so the struct must spill to the stack. */
int exhaust(long a,long b,long c,long d,long e,long f,long g,long h, struct S2 s){
    return (a+b+c+d+e+f+g+h == 36 && s.a == 11 && s.b == 22) ? 42 : 7;
}
"""

ABI_PROGRAM = """
struct S2 { a: int32; b: int32; }
struct S3 { a: int32; b: int32; c: int32; }
struct Point { x: float64; y: float64; }
struct V3 { x: float64; y: float64; z: float64; }
struct Mixed { a: int32; b: float64; }
struct Big { a: int64; b: int64; c: int64; }
union U { i: int32; d: float64; }

@extern fn s2_swap_ok(s: struct S2) -> int32;
@extern fn s2_swap(s: struct S2) -> struct S2;
@extern fn s3_sum(s: struct S3) -> int32;
@extern fn pt_sum(p: struct Point) -> float64;
@extern fn v3_scale(v: struct V3, k: float64) -> struct V3;
@extern fn mixed_ok(m: struct Mixed) -> int32;
@extern fn big_make(base: int64) -> struct Big;
@extern fn big_sum(b: struct Big) -> int64;
@extern fn union_get(u: union U) -> int32;
@extern fn exhaust(a: int64, b: int64, c: int64, d: int64, e: int64,
                   f: int64, g: int64, h: int64, s: struct S2) -> int32;

fn main() -> int32 {
    let s: struct S2 = S2 { a = 11, b = 22 };
    // Argument into C, small struct returned out of C.
    let sw: struct S2 = s2_swap(s);
    let arg_ok: int32 = s2_swap_ok(s);              // 1
    let s3: struct S3 = S3 { a = 1, b = 2, c = 3 };
    let s3s: int32 = s3_sum(s3);                     // 6
    let p: struct Point = Point { x = 1.5, y = 2.5 };
    let d: float64 = pt_sum(p);                      // 4.0
    let v: struct V3 = V3 { x = 1.0, y = 2.0, z = 3.0 };
    let vs: struct V3 = v3_scale(v, 2.0);            // z = 6.0
    let m: struct Mixed = Mixed { a = 7, b = 3.5 };
    let mo: int32 = mixed_ok(m);                     // 1
    let big: struct Big = big_make(100);             // sret {100,101,102}
    let bs: int64 = big_sum(big);                    // 303
    let u: union U = U { i = 1234 };
    let ug: int32 = union_get(u);                    // 1234
    let ex: int32 = exhaust(1, 2, 3, 4, 5, 6, 7, 8, s);  // 42

    if (sw.a == 22 and sw.b == 11 and arg_ok == 1 and s3s == 6
        and d == 4.0 and vs.z == 6.0 and mo == 1 and bs == 303
        and ug == 1234 and ex == 42) {
        return 99;
    }
    return 1;
}
"""


@pytest.mark.skipif(
    not ON_AARCH64_HOST, reason="C ABI round-trip requires an AArch64 host toolchain"
)
def test_struct_abi_round_trips_through_linked_c(tmp_path):
    # The full shape matrix crosses a real C boundary: struct args in GPRs and
    # FP registers, register-return and sret-return, a >16B indirect argument,
    # a union member, and a register-exhaustion case. Each function verifies
    # the value it received (or returns one the caller checks); main returns 99
    # only if every shape crossed intact.
    fixture = tmp_path / "abi_fixture.c"
    fixture.write_text(FIXTURE_C)
    obj = tmp_path / "abi_fixture.o"
    assert subprocess.run(
        ["cc", "-c", str(fixture), "-o", str(obj)], capture_output=True
    ).returncode == 0

    src = tmp_path / "abi.mc"
    src.write_text(ABI_PROGRAM)
    exe = tmp_path / "abi"
    result = subprocess.run(
        [sys.executable, "-m", "mcc", str(src), str(obj), "-o", str(exe)],
        cwd=ROOT, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    assert subprocess.run([exe]).returncode == 99
