import "std";
import "memory";

fn fill_squares(nums: int32*, n: int32) {
    let i: int32 = 0;
    while (i < n) {
        nums[i] = i * i;
        i += 1;
    }
}

fn main() -> int32 {
    // Heap allocation through the generic alloc<T> from libmc/memory.mc.
    let nums = alloc<int32>(5);
    fill_squares(nums, 5);

    let i: int32 = 0;
    while (i < 5) {
        println("nums[%d] = %d", i, nums[i]);
        i += 1;
    }
    dealloc(nums);

    // Address-of, dereference, and assignment through a pointer.
    let x: int32 = 42;
    let p = &x;
    println("*p = %d", *p);
    *p = 99;
    println("x  = %d", x);

    // sizeof reports a type's size in bytes; pointers are 8.
    println("sizeof(int32)  = %llu", sizeof(int32));
    println("sizeof(uint8)  = %llu", sizeof(uint8));
    println("sizeof(int32*) = %llu", sizeof(int32*));

    // sizeof also takes a variable -- the size of its type, no type spelled out.
    // The operand is not evaluated, so this never touches x or p.
    println("sizeof(x)      = %llu", sizeof(x));    // x is int32 -> 4
    println("sizeof(p)      = %llu", sizeof(p));    // p is int32* -> 8

    // `as` casts: between integer types (truncate / extend), to and from
    // float64, and between pointer types.
    println("300 as uint8     = %u", 300 as uint8);
    println("-1 as int64      = %lld", -1 as int64);
    println("2 as float64     = %f", 2 as float64);
    println("3.99 as int32    = %d", 3.99 as int32);
    
    let bytes = nums as byte*;   // `byte` is the alias for uint8: raw memory
    let addr_is_zero = (0 as uint8*) as uint64 == 0;
    println("null addr test   = %d", addr_is_zero);

    return 0;
}

// See also: functions/nonnull.mc and functions/noalias.mc for the @nonnull and
// @noalias promises a pointer parameter can carry; systems/byte_scan.mc for
// pointer arithmetic (`p + n`, `p - q`, the `while (p < end)` scan loop).
