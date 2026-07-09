"""C struct-passing ABI at the ``@extern`` boundary (AArch64, x86-64).

Two halves: fast in-process checks of the classified IR shape and the
unsupported-target gating error (compiled for a fixed target, no run), and
linked round-trip tests that compile a C fixture, link it with an mcc program,
and run the result -- the only true oracle for ABI correctness. A linked test
runs only on a host whose native C ABI matches the one mcc classifies for: the
AArch64 matrix on an AArch64 host, the x86-64 System V matrix on an x86-64 host.

The IR-shape tests run on any host (they compile for a fixed ``--target`` and
never run the code), so they cover AArch64/AAPCS64, x86-64 System V, and x86-64
Windows (Win64) everywhere. Win64 has no linked round-trip -- there is no
Windows CI runner -- so its classification is verified by IR shape only, never
against a real link.
"""

import platform
import subprocess
import sys
from pathlib import Path

import pytest

from mcc.errors import LangError
from helpers import compile_ir, run

ROOT = Path(__file__).resolve().parents[1]
AARCH64 = "arm64-apple-darwin"
X86_64_SYSV = "x86_64-unknown-linux-gnu"
X86_64_WIN = "x86_64-pc-windows-msvc"
ON_AARCH64_HOST = platform.machine().lower() in ("arm64", "aarch64")
ON_X86_64_HOST = platform.machine().lower() in ("x86_64", "amd64")

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


def call_ir(decls: str, body: str, target: str = AARCH64) -> str:
    return compile_ir(
        DECLS + decls + "\nfn main() -> int32 {\n" + body + "\n}\n", target=target
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


# --- classified declaration IR shape (x86-64 System V) ------------------------
#
# SysV classifies each aggregate into eightbytes, coercing an INTEGER eightbyte
# to i64 and an SSE eightbyte to double; a MEMORY aggregate (>16 bytes, or one
# demoted for want of registers) passes `byval`. These compile for a fixed
# --target, so they run on any host.


def test_sysv_small_int_struct_is_one_integer_eightbyte():
    # {int32,int32} is one 8-byte eightbyte, both INTEGER -> a single i64.
    ir_text = ir_for("@extern fn f(s: struct S2) -> int32;", target=X86_64_SYSV)
    assert 'declare i32 @"f"(i64 ' in ir_text


def test_sysv_two_integer_eightbytes_coerce_to_an_i64_struct():
    # {int32,int32,int32} is 12 bytes -> two INTEGER eightbytes -> {i64, i64}.
    ir_text = ir_for("@extern fn f(s: struct S3) -> int32;", target=X86_64_SYSV)
    assert 'declare i32 @"f"({i64, i64} ' in ir_text


def test_sysv_double_pair_coerces_to_two_sse_eightbytes():
    # {double,double}: two SSE eightbytes -> {double, double} (xmm0/xmm1).
    ir_text = ir_for("@extern fn f(p: struct Point) -> float64;", target=X86_64_SYSV)
    assert 'declare double @"f"({double, double} ' in ir_text


def test_sysv_mixed_int_and_double_is_integer_then_sse():
    # {int32 @0, double @8}: eightbyte 0 INTEGER, eightbyte 1 SSE -> {i64, double}.
    ir_text = ir_for("@extern fn f(m: struct Mixed) -> int32;", target=X86_64_SYSV)
    assert 'declare i32 @"f"({i64, double} ' in ir_text


def test_sysv_union_merges_to_an_integer_eightbyte():
    # A union overlays its members in one eightbyte; INTEGER+SSE merge to
    # INTEGER, so union {int,double} passes in a single i64.
    ir_text = ir_for("@extern fn f(u: union U) -> int32;", target=X86_64_SYSV)
    assert 'declare i32 @"f"(i64 ' in ir_text


def test_sysv_double_array_field_gives_two_sse_eightbytes():
    # An array of doubles is walked element by element: float64[2] is two SSE
    # eightbytes -> {double, double} (not an HFA rule -- SysV has no HFA).
    ir_text = compile_ir(
        "struct DA { xs: float64[2]; }\n"
        "@extern fn f(m: struct DA) -> float64;\n"
        "fn main() -> int32 { return 0; }\n",
        target=X86_64_SYSV,
    )
    assert 'declare double @"f"({double, double} ' in ir_text


def test_sysv_int_array_field_gives_two_integer_eightbytes():
    # int32[4] is 16 bytes -> two INTEGER eightbytes -> {i64, i64}.
    ir_text = compile_ir(
        "struct IA { xs: int32[4]; }\n"
        "@extern fn f(m: struct IA) -> int32;\n"
        "fn main() -> int32 { return 0; }\n",
        target=X86_64_SYSV,
    )
    assert 'declare i32 @"f"({i64, i64} ' in ir_text


def test_sysv_packed_field_straddling_an_eightbyte_goes_to_memory():
    # A @packed int64 at offset 1 spans bytes 1-8, crossing the eightbyte
    # boundary; an unaligned field puts the whole aggregate in memory (byval),
    # rather than miscompiling it into registers.
    ir_text = compile_ir(
        "@packed struct PK { a: int8; b: int64; }\n"
        "@extern fn f(m: struct PK) -> int32;\n"
        "fn main() -> int32 { return 0; }\n",
        target=X86_64_SYSV,
    )
    assert 'byval(%"PK")' in ir_text


def test_sysv_over_16_byte_struct_passes_byval():
    # >16 bytes is a MEMORY argument: a `byval(T) align N` pointer, so the data
    # is copied onto the argument stack (unlike AArch64's plain pointer).
    ir_text = ir_for("@extern fn f(b: struct Big) -> int64;", target=X86_64_SYSV)
    assert 'declare i64 @"f"(%"Big"* byval(%"Big") align 8 ' in ir_text


def test_sysv_large_return_uses_sret():
    # >16 bytes returned -> a hidden sret pointer, function returns void.
    ir_text = ir_for("@extern fn f() -> struct Big;", target=X86_64_SYSV)
    assert 'declare void @"f"(%"Big"* sret(%"Big")' in ir_text


def test_sysv_register_return_is_eightbyte_coerced():
    # A {double,double} return comes back in xmm0/xmm1, coerced not sret.
    ir_text = ir_for("@extern fn f() -> struct Point;", target=X86_64_SYSV)
    assert 'declare {double, double} @"f"()' in ir_text


def test_sysv_register_aggregate_demotes_when_int_registers_run_low():
    # THE crux: after five integer arguments (rdi..r8), one GPR remains, but a
    # two-eightbyte {i64,i64} aggregate needs two -- SysV never straddles it, so
    # the frontend demotes the WHOLE aggregate to a byval memory argument. (The
    # LLVM backend would otherwise split it one-register-one-stack, ABI-wrong.)
    ir_text = ir_for(
        "@extern fn f(a: int64, b: int64, c: int64, d: int64, e: int64, "
        "s: struct S3) -> int32;",
        target=X86_64_SYSV,
    )
    assert 'byval(%"S3")' in ir_text


def test_sysv_four_int_args_leave_room_for_a_two_eightbyte_aggregate():
    # One fewer integer argument (four) leaves two GPRs free, exactly enough:
    # the same aggregate now rides in registers as {i64, i64}, no byval.
    ir_text = ir_for(
        "@extern fn f(a: int64, b: int64, c: int64, d: int64, "
        "s: struct S3) -> int32;",
        target=X86_64_SYSV,
    )
    assert '{i64, i64} ' in ir_text
    assert "byval" not in ir_text


def test_sysv_sret_return_consumes_the_first_integer_register():
    # The sret pointer occupies rdi, so only five GPRs remain for arguments:
    # four integer arguments plus a two-eightbyte aggregate now overflow (need
    # 1+4+2 = 7 > 6), demoting the aggregate to byval. Without the sret return
    # the same four-int case fits (see the test above) -- proving the sret
    # pointer is counted.
    ir_text = ir_for(
        "@extern fn f(a: int64, b: int64, c: int64, d: int64, "
        "s: struct S3) -> struct Big;",
        target=X86_64_SYSV,
    )
    assert 'sret(%"Big")' in ir_text
    assert 'byval(%"S3")' in ir_text


def test_sysv_sse_registers_are_accounted_separately():
    # Eight double arguments exhaust xmm0-7; a {double,double} aggregate then
    # needs two SSE registers with none left, so it demotes to byval -- SSE
    # accounting is independent of the still-plentiful integer registers.
    ir_text = ir_for(
        "@extern fn f(a: float64, b: float64, c: float64, d: float64, "
        "e: float64, g: float64, h: float64, i: float64, "
        "p: struct Point) -> int32;",
        target=X86_64_SYSV,
    )
    assert 'byval(%"Point")' in ir_text


def test_sysv_direct_argument_marshals_and_call_carries_byval(tmp_path):
    # Call-site marshalling on any host: a MEMORY argument's byval attribute is
    # emitted at the call to match the declaration (byval(T) align N).
    ir_text = call_ir(
        "@extern fn f(b: struct Big) -> int64;",
        "let b: struct Big = Big { a = 1, b = 2, c = 3 };\n"
        "    return f(b) as int32;",
        target=X86_64_SYSV,
    )
    assert 'call i64 @"f"(%"Big"* byval(%"Big") align 8 ' in ir_text


# --- classified declaration IR shape (x86-64 Windows / Win64) ------------------
#
# Win64 gives aggregates no SSE: a struct of exactly 1/2/4/8 bytes rides in one
# integer register (even a float struct), any other size is indirect. There is
# no Windows CI runner, so these IR-shape tests are the ONLY coverage -- Win64
# classification is never verified against a real link.


def test_win64_eight_byte_struct_is_one_integer_register():
    # {int32,int32} is 8 bytes -> a single i64.
    ir_text = ir_for("@extern fn f(s: struct S2) -> int32;", target=X86_64_WIN)
    assert 'declare i32 @"f"(i64 ' in ir_text


def test_win64_float_struct_still_uses_an_integer_register():
    # {double,double} is 16 bytes -> not a register size -> indirect (a plain
    # pointer to a caller copy, no byval). Win64 never puts a struct in SSE.
    ir_text = ir_for("@extern fn f(p: struct Point) -> float64;", target=X86_64_WIN)
    assert 'declare double @"f"(%"Point"* ' in ir_text
    assert "byval" not in ir_text


def test_win64_odd_size_struct_passes_indirectly():
    # {int32,int32,int32} is 12 bytes -> not 1/2/4/8 -> a plain pointer.
    ir_text = ir_for("@extern fn f(s: struct S3) -> int32;", target=X86_64_WIN)
    assert 'declare i32 @"f"(%"S3"* ' in ir_text
    assert "byval" not in ir_text


def test_win64_small_return_comes_back_in_a_register():
    # An 8-byte return fits a register: coerced to i64, not sret.
    ir_text = ir_for("@extern fn f() -> struct S2;", target=X86_64_WIN)
    assert 'declare i64 @"f"()' in ir_text


def test_win64_return_larger_than_eight_bytes_uses_sret():
    # A 12-byte return is not 1/2/4/8 bytes -> a hidden sret pointer.
    ir_text = ir_for("@extern fn f() -> struct S3;", target=X86_64_WIN)
    assert 'declare void @"f"(%"S3"* sret(%"S3")' in ir_text


# --- unsupported-target gating ------------------------------------------------


@pytest.mark.parametrize(
    "target",
    ["riscv64-unknown-linux", "wasm32-unknown-unknown"],
)
def test_struct_by_value_extern_is_rejected_on_unsupported_targets(target):
    # AArch64 and x86-64 are supported; riscv64 and unknown targets are not.
    with pytest.raises(
        LangError,
        match="passing a struct by value across the C boundary is not supported",
    ):
        ir_for("@extern fn f(s: struct S2) -> int32;", target=target)


def test_struct_return_extern_is_rejected_on_an_unsupported_target():
    with pytest.raises(
        LangError,
        match="passing a struct by value across the C boundary is not supported",
    ):
        ir_for("@extern fn f() -> struct Big;", target="riscv64-unknown-linux")


def test_gating_message_names_the_target():
    # The error is target-specific: it quotes the triple it rejected.
    with pytest.raises(LangError, match="'riscv64-unknown-linux'"):
        ir_for("@extern fn f(s: struct S2) -> int32;", target="riscv64-unknown-linux")


@pytest.mark.parametrize("target", [X86_64_SYSV, X86_64_WIN, AARCH64])
def test_by_value_struct_extern_compiles_on_supported_targets(target):
    # The former gate is lifted for x86-64 (System V and Windows) as well as
    # AArch64: a by-value-struct @extern now compiles for all three.
    ir_text = ir_for("@extern fn f(s: struct S2) -> int32;", target=target)
    assert 'declare i32 @"f"(' in ir_text


def test_scalar_extern_still_compiles_on_an_unsupported_target():
    # The gate is specific to by-value aggregates: scalar/pointer externs are
    # unaffected on every target, even ones without a struct ABI.
    ir_text = ir_for(
        "@extern fn f(p: struct S2*) -> int32;", target="riscv64-unknown-linux"
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
/* Five integer args, then a two-eightbyte struct: on x86-64 System V five GPRs
   are spoken for, one remains, and struct S3 needs two -- so the frontend must
   demote it whole to a byval memory argument (the tightest register-accounting
   case; the LLVM backend would otherwise split it one-register-one-stack). */
int demote(long a,long b,long c,long d,long e, struct S3 s){
    return (a+b+c+d+e == 15 && s.a == 111 && s.b == 222 && s.c == 333) ? 55 : 9;
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
@extern fn demote(a: int64, b: int64, c: int64, d: int64, e: int64,
                  s: struct S3) -> int32;

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
    let s3d: struct S3 = S3 { a = 111, b = 222, c = 333 };
    let dm: int32 = demote(1, 2, 3, 4, 5, s3d);          // 55

    if (sw.a == 22 and sw.b == 11 and arg_ok == 1 and s3s == 6
        and d == 4.0 and vs.z == 6.0 and mo == 1 and bs == 303
        and ug == 1234 and ex == 42 and dm == 55) {
        return 99;
    }
    return 1;
}
"""


def _round_trip(tmp_path) -> int:
    # Compile the C fixture, link it with the mcc program, run, and report the
    # exit status. The C source and the mcc source are ABI-agnostic: each
    # compiler classifies for the host's own ABI, so the same matrix validates
    # whichever ABI the host toolchain speaks (AArch64 or x86-64 System V).
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
    return subprocess.run([exe]).returncode


# The full shape matrix crosses a real C boundary: struct args in GPRs and FP/SSE
# registers, register-return and sret-return, a >16B indirect argument, a union
# member, a fully-exhausted argument list, and the tight register-accounting
# demotion (five ints then a two-eightbyte struct). Each function verifies the
# value it received (or returns one the caller checks); main returns 99 only if
# every shape crossed intact. The test runs once per host ABI it can validate.


@pytest.mark.skipif(
    not ON_AARCH64_HOST, reason="AArch64 C ABI round-trip requires an AArch64 host"
)
def test_struct_abi_round_trips_through_linked_c_aarch64(tmp_path):
    assert _round_trip(tmp_path) == 99


@pytest.mark.skipif(
    not ON_X86_64_HOST, reason="x86-64 System V C ABI round-trip requires an x86-64 host"
)
def test_struct_abi_round_trips_through_linked_c_sysv(tmp_path):
    # Runs on the ubuntu (x86-64) CI leg; skipped on the macOS arm64 dev host.
    # This is the only linked oracle for the SysV eightbyte coercions and the
    # byval demotion -- Win64 has no runner and stays IR-shape-tested only.
    assert _round_trip(tmp_path) == 99


# The libc div/ldiv/lldiv bindings are the real-world payoff of struct-by-value
# @extern support: each returns its quotient and remainder together. These call
# real libc through the JIT, so they exercise the host's ABI (AArch64 here, SysV
# on the ubuntu leg) end to end -- div_t is one 8-byte GPR, ldiv_t/lldiv_t are a
# 16-byte register pair.
def test_libc_div_family_returns_structs_by_value():
    assert run(
        """
        import "libc/stdlib";
        fn main() -> int32 {
            let d: struct div_t = div(17, 5);
            let l: struct ldiv_t = ldiv(1000, 7);
            let ll: struct lldiv_t = lldiv(45, 6);
            if (d.quot != 3) return 10;
            if (d.rem != 2) return 11;
            if (l.quot != 142) return 12;
            if (l.rem != 6) return 13;
            if (ll.quot != 7) return 14;
            if (ll.rem != 3) return 15;
            return 42;
        }
        """
    ) == 42
