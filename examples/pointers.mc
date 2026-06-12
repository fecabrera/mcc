#include <stdio.h>
import "memory";

fn fill_squares(nums: int32*, n: int32) {
    let i: int32 = 0;
    while (i < n) {
        nums[i] = i * i;
        i = i + 1;
    }
}

fn main() -> int32 {
    // Heap allocation through the generic alloc<T> from lib/memory.mc.
    let nums = alloc<int32>(5);
    fill_squares(nums, 5);

    let i: int32 = 0;
    while (i < 5) {
        printf("nums[%d] = %d\n", i, nums[i]);
        i = i + 1;
    }
    dealloc(nums);

    // Address-of, dereference, and assignment through a pointer.
    let x: int32 = 42;
    let p = &x;
    printf("*p = %d\n", *p);
    *p = 99;
    printf("x  = %d\n", x);

    // sizeof reports a type's size in bytes; pointers are 8.
    printf("sizeof(int32)  = %llu\n", sizeof(int32));
    printf("sizeof(uint8)  = %llu\n", sizeof(uint8));
    printf("sizeof(int32*) = %llu\n", sizeof(int32*));

    // `as` casts: between integer types (truncate / extend), to and from
    // float64, and between pointer types.
    printf("300 as uint8     = %u\n", 300 as uint8);
    printf("-1 as int64      = %lld\n", -1 as int64);
    printf("2 as float64     = %f\n", 2 as float64);
    printf("3.99 as int32    = %d\n", 3.99 as int32);
    let bytes = nums as uint8*;  // freed above; just demonstrating the cast
    let addr_is_zero = (0 as uint8*) as uint64 == 0;
    printf("null addr test   = %d\n", addr_is_zero);

    return 0;
}
