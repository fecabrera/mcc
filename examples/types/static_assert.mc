import "std/io";

// `@static_assert` and `@error` are top-level compile-time directives, checked
// during code generation once every type, constant, and enum is known but
// before any function body is compiled. They turn a bad build into a compile
// error before it ever links.
//
// Prerequisites: basics/constants.mc (`const`, `sizeof`), types/structs.mc
// (`offsetof`, layout), types/enums.mc (`Enum::Member`),
// control-flow/conditional.mc (`@if`).
//
// This file compiles because every `@static_assert` below PASSES (a passing
// assert is silent, emitting no code) and the single `@error` sits in a dead
// `@if` branch. A firing directive would fail the build.

// An on-wire packet header. `@static_assert` pins its layout, so an accidental
// field, reorder, or padding change is caught here at compile time, not as a
// corrupt packet at runtime.
struct Header {
    magic:   uint32;
    version: uint16;
    flags:   uint16;
    length:  uint32;
}

// Layout guards. `sizeof` / `alignof` / `offsetof` fold to compile-time uint64
// constants, exactly as a `const` initializer would, so the condition can use
// them. `length` sits after the 4-byte magic and the two uint16 fields, at
// offset 8.
@static_assert(sizeof(struct Header) == 12, "Header must stay 12 bytes on the wire");
@static_assert(offsetof(struct Header, length) == 8, "length must follow magic, version, flags");

// A size/alignment check: the 4-byte fields keep the header 4-byte aligned, so
// an array of headers needs no inter-element padding.
@static_assert(alignof(struct Header) == 4, "Header must stay 4-byte aligned");

// A `const`-based check. MAX_PAYLOAD sizes a fixed buffer elsewhere; assert it
// is a power of two so the bit-masking that indexes the buffer stays valid.
const MAX_PAYLOAD = 1024;
@static_assert((MAX_PAYLOAD & (MAX_PAYLOAD - 1)) == 0, "MAX_PAYLOAD must be a power of two");

// An `Enum::Member`-based check. The protocol version the code is written
// against must be the one this build encodes.
enum Version: uint16 {
    V1 = 1,
    V2 = 2,
}
@static_assert(Version::V2 == 2, "protocol version drifted");

// `@error` is the unconditional twin: reaching it always fails the compile. On
// its own it would break every build, so guard it with an `@if` -- the dead
// branch is dropped, so the error only fires when its branch is live. Top-level
// `@if` requires braces. This program supports only x86-64 and arm64; on any
// other target the `@else` below goes live and rejects the build with a clear
// message instead of miscompiling.
@if (TARGET_ARCH == ARCH_X86_64) {
    const ARCH_NAME = "x86-64";
} @else @if (TARGET_ARCH == ARCH_AARCH64) {
    const ARCH_NAME = "arm64";
} @else {
    // Dead for every target the suite builds on, so it never fires here. On an
    // unsupported target this branch would be live and the compile would stop
    // with: error: line N: this target is not supported
    @error("this target is not supported");
}

fn main() -> int32 {
    // The directives above produced no code: passing asserts are invisible at
    // runtime. Had any condition been false, this file would not have compiled
    // at all -- so reaching `main` proves every invariant held.
    println("Header is {} bytes, {}-byte aligned, built for {}",
            sizeof(struct Header), alignof(struct Header), ARCH_NAME);
    println("protocol version {}, max payload {}", Version::V2, MAX_PAYLOAD);
    return 0;
}

// See also: warnings.mc (@warning, the non-fatal sibling directive, and the
// -Werror flag), types/structs.mc (offsetof and layout constants),
// types/enums.mc (Enum::Member), control-flow/conditional.mc (@if compile-time
// selection).
