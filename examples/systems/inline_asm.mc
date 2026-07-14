import "std/io";

// `@asm` drops to raw machine instructions when nothing higher-level will do.
// It is inherently target-specific, so it pairs with `@if` on TARGET_ARCH; the
// fallbacks below keep this example runnable on any host (`--run`), while an
// aarch64 host actually exercises the assembly.

// `@asm fn` is sugar for a function whose body is a single `@asm(...)` over its
// parameters: the params are the inputs ($0, $1, ...), the return type is the
// output ($out), and you do NOT write `ret` -- the epilogue returns. On aarch64
// a bare operand is the 64-bit `x` register; the `:w` modifier picks the 32-bit
// `w` name (exactly like `%w` in C inline asm).
@if (TARGET_ARCH == ARCH_AARCH64) {
    @asm fn byteswap32(value: uint32) -> uint32 {
        "rev ${out:w}, ${0:w}"
    }
} @else {
    // Portable fallback so non-aarch64 hosts still run this example.
    fn byteswap32(value: uint32) -> uint32 {
        return ((value & 0xFF) << 24) | ((value & 0xFF00) << 8)
             | ((value >> 8) & 0xFF00) | ((value >> 24) & 0xFF);
    }
}

fn main() -> int32 {
    println(f"byteswap32(0x11223344) = {byteswap32(0x11223344):x}");   // 44332211

    // The `@asm(...)` expression form: operands in parentheses, an optional
    // `-> type` output, one string literal per instruction. `@if` as a
    // statement does not open a scope, so `sum` is visible afterwards.
    let a: int64 = 20;
    let b: int64 = 22;
    @if (TARGET_ARCH == ARCH_AARCH64) {
        let sum = @asm(a, b) -> int64 { "add $out, $0, $1" };
    } @else {
        let sum = a + b;
    }
    println(f"a + b = {sum}");

    return 0;
}
