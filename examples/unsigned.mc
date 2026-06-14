import "std";

fn main() -> int32 {
    // Values above int32's range fit in unsigned types, and division,
    // remainder, and comparisons use unsigned semantics.
    let big: uint32 = 4000000000;
    println("big / 2 = %u", big / 2);

    if (big > 100) {
        println("unsigned comparison works");
    }

    // Small unsigned types zero-extend when passed to printf.
    let byte: uint8 = 200;
    println("byte = %u", byte);

    let huge: uint64 = 18000000000000000000;
    println("huge %% 7 = %llu", huge % 7);

    return 0;
}
