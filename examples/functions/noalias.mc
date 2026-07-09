import "std/io";

// `@noalias` on a pointer parameter is a promise, kept by the caller, that the
// pointer does not overlap any other pointer the function reaches -- C's
// `restrict`. It changes no ABI; it only lets the optimizer assume the regions
// are disjoint, so a copy loop can skip the runtime overlap check and be
// recognized as a bulk move. The promise is unchecked: passing overlapping
// pointers is undefined behavior.
fn blit(@noalias dst: uint8*, @noalias src: uint8*, n: uint64) {
    for i in range(n) {
        dst![i] = src![i];
    }
}

fn main() -> int32 {
    let src: uint8[5] = [10 as uint8, 20 as uint8, 30 as uint8, 40 as uint8, 50 as uint8];
    let dst: uint8[5] = [0 as uint8, 0 as uint8, 0 as uint8, 0 as uint8, 0 as uint8];

    // dst and src are distinct arrays, so the @noalias promise holds.
    blit(&dst[0], &src[0], 5);

    let sum: uint64 = 0;
    for i in range(5) {
        sum += dst[i] as uint64;
    }
    println("copied %llu bytes, sum = %llu", 5 as uint64, sum);

    return 0;
}

// See also: nonnull.mc for @nonnull, the checked complement of this unchecked
// promise (the two compose: `@noalias @nonnull p: T*`); const_params.mc and
// mut_params.mc for the value-parameter modifiers; memory/pointers.mc for the
// pointer basics these build on.
