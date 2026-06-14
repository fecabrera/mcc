import "std";
import "array";

// `for x in obj` walks anything that provides the iter/next protocol -- here
// the growable array from lib/array.mc. The element type is inferred from
// `next`, the loop variable `x` is scoped to the loop, and break/continue
// work as in any loop.

fn main() -> int32 {
    let nums: struct array<int32>;
    array_init(&nums, 4);
    defer array_destroy(&nums);          // freed however main exits

    let i: int32 = 1;
    while (i <= 6) {
        array_append(&nums, i * i);      // 1 4 9 16 25 36
        i = i + 1;
    }

    // Sum the squares, stopping once they exceed 20.
    let sum: int32 = 0;
    for sq in &nums {
        if (sq % 2 == 0) { continue; }   // skip the even squares
        if (sq > 20) { break; }          // and stop past 20
        println("odd square: %d", sq);
        sum = sum + sq;
    }
    println("sum of odd squares <= 20: %d", sum);   // 1 + 9 = 10

    // A bare { } block is its own scope -- a place for a short-lived helper
    // and its cleanup, without leaking names into the rest of the function.
    {
        let scratch: uint8* = alloc<uint8>(8);
        defer dealloc(scratch);
        scratch[0] = 'h';
        scratch[1] = 'i';
        scratch[2] = 0;
        println("%s", scratch);
    }   // scratch is freed and out of scope here

    return 0;
}
