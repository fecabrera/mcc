import "libc/stdio";

fn main() -> int32 {
    // Values above int32's range fit in unsigned types, and division,
    // remainder, and comparisons use unsigned semantics.
    let big: uint32 = 4000000000;
    printf("big / 2 = %u\n", big / 2);

    if (big > 100) {
        puts("unsigned comparison works");
    }

    // Small unsigned types zero-extend when passed to printf.
    let byte: uint8 = 200;
    printf("byte = %u\n", byte);

    let huge: uint64 = 18000000000000000000;
    printf("huge %% 7 = %llu\n", huge % 7);

    return 0;
}
