// Passing and returning a `struct` BY VALUE across the `@extern` C boundary.
//
// mcc's own calls lay a struct out whole and hand it over as an LLVM aggregate
// (with `&`/`const &` parameters travelling by a hidden reference). That is
// self-consistent, but it is NOT how a C compiler passes a struct: the
// platform ABI classifies each aggregate into registers, or spills it to
// memory, by precise size/shape rules. So at an `@extern` call -- and only
// there -- mcc now speaks the C ABI, so a by-value struct lands where the C
// side expects it. Everything below binds real libc functions and runs.
//
// Three platform ABIs are classified: Apple/AAPCS64 (AArch64), x86-64 System V,
// and x86-64 Windows (Win64). On any other target (riscv64, unknown) an
// `@extern` that passes or returns a struct by value is a compile error
// ("...not supported on target '<triple>' yet; pass a pointer instead"), rather
// than silently emitting the wrong form. Because the classification is
// target-specific, CI cross-compiles THIS file to an object for each supported
// target instead of building it once in the main (host) example loop.
//
// The AAPCS64 rules mcc applies to an aggregate (the docs cover the x86-64
// System V eightbyte and the Win64 rules in full):
//   - a homogeneous float aggregate (all `float64`, 1-4 members) rides in FP
//     registers, e.g. `{x, y}` as a pair of doubles;
//   - otherwise 16 bytes or less rides in one or two general-purpose registers;
//   - more than 16 bytes goes indirectly (an argument by a pointer to a copy;
//     a return through a hidden `sret` pointer the caller allocates).

// libc's `div` returns its quotient and remainder together. `div_t` is two
// ints -- 8 bytes -- so AAPCS64 returns it in a single general-purpose
// register; mcc reconstructs the struct from that register on the way back.
struct div_t { quot: int32; rem: int32; }
@extern @symbol("div") fn c_div(numer: int32, denom: int32) -> struct div_t;

// `ldiv` is the `long` form: `ldiv_t` is two 64-bit fields -- 16 bytes -- so
// it comes back in a REGISTER PAIR (classified as `[2 x i64]`), still direct.
struct ldiv_t { quot: int64; rem: int64; }
@extern @symbol("ldiv") fn c_ldiv(numer: int64, denom: int64) -> struct ldiv_t;

@extern fn printf(fmt: char*, ...) -> int32;

fn main() -> int32 {
    // A small struct returned by value out of C, in one register.
    let d: struct div_t = c_div(17, 5);
    printf("div(17, 5)   = %d rem %d\n", d.quot, d.rem);    // 3 rem 2

    // A 16-byte struct returned by value, in a register pair.
    let l: struct ldiv_t = c_ldiv(1000, 7);
    printf("ldiv(1000, 7) = %lld rem %lld\n", l.quot, l.rem);  // 142 rem 6

    return 0;
}

// See also: extern.mc -- the scalar/pointer @extern basics this builds on; and
// interfaces.mc -- the mcc-to-mcc convention (raw aggregate + hidden reference)
// that stays distinct from this C-ABI crossing. The full shape matrix (GPR and
// FP-register structs, register-return and sret-return, a >16B indirect
// argument, a union member, and register exhaustion) is exercised end-to-end
// against a linked C fixture in tests/test_extern_struct_abi.py.
