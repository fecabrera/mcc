import "std";
import "range";

// `range<T>` is a half-open integer interval [start, end) from the standard
// library. Set its `start` and `end`, then iterate it with `for ... in` over
// the `range_it`/`range_next` protocol -- a counting loop without a manual
// index variable.

fn main() -> int32 {
    // Count 0, 1, 2, 3, 4 (end is excluded).
    let r: struct range<int32>;
    r.start = 0;
    r.end = 5;
    for i in &r {
        println("i = %d", i);
    }

    // The bounds are ordinary values, so they can come from anywhere. Here a
    // second range sums 10..15 (i.e. 10 + 11 + 12 + 13 + 14).
    let span: struct range<int32>;
    span.start = 10;
    span.end = 15;
    let sum: int32 = 0;
    for n in &span {
        sum = sum + n;
    }
    println("sum of [10, 15) = %d", sum);

    // range is generic over the integer type; here a wider int64 range, with
    // break/continue working as in any loop.
    let big: struct range<int64>;
    big.start = 0;
    big.end = 100;
    for k in &big {
        if (k % 2 != 0) { continue; }   // even values only
        if (k > 8) { break; }           // stop past 8
        println("even k = %lld", k);
    }

    return 0;
}
