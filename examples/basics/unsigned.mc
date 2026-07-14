import "std/io";

fn main() -> int32 {
    // Values above int32's range fit in unsigned types, and division,
    // remainder, and comparisons use unsigned semantics.
    let big: uint32 = 4000000000;
    println(f"big / 2 = {big / 2}");

    if (big > 100) {
        println("unsigned comparison works");
    }

    // Small unsigned types zero-extend when passed to printf.
    let byte: uint8 = 200;
    println(f"byte = {byte}");

    let huge: uint64 = 18000000000000000000;
    println(f"huge % 7 = {huge % 7}");

    return 0;
}
