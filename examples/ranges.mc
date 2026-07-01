import "std";
import "range";

// `range<T>` is a half-open integer interval [start, end) from the standard
// library. Set its `start` and `end`, then iterate it with `for ... in` over
// the `range_it`/`range_next` protocol -- a counting loop without a manual
// index variable.

// Returns a range; used below to show that a value (even a temporary) iterates
// directly, with no `&`.
fn upto(n: int32) -> struct range<int32> {
    return struct range<int32> { end = n };
}

fn main() -> int32 {
    // Count 0, 1, 2, 3, 4 (end is excluded). A struct value iterates directly:
    // `for i in r` borrows it automatically. Write `for i in &r` to iterate the
    // range by reference instead -- for a plain counting loop the two are the
    // same.
    let r = struct range<int32> { start = 0, end = 5 };
    for i in r {
        println("i = %d", i);
    }

    // A returned range has no address to take, but iterates all the same -- it
    // is materialized behind the scenes. Sums 10..15 (10 + 11 + 12 + 13 + 14).
    let sum: int32 = 0;
    for n in upto(15) {
        if (n < 10) { continue; }
        sum += n;
    }
    println("sum of [10, 15) = %d", sum);

    // range is generic over the integer type; here a wider int64 range, with
    // break/continue working as in any loop.
    let big = struct range<int64> { end = 100 };  // start defaults to 0
    for k in &big {
        if (k % 2 != 0) { continue; }   // even values only
        if (k > 8) { break; }           // stop past 8
        println("even k = %lld", k);
    }

    return 0;
}
