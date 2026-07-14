import "std/io";

// `range` is a builtin: `for i in range(start, end)` counts over the half-open
// interval [start, end), and `for i in range(end)` counts from 0. It lowers to
// a plain counting loop -- no struct is built and nothing is allocated, so it
// costs no more than a hand-written `while`. No import is needed.

fn main() -> int32 {
    // Count 0, 1, 2, 3, 4 (end is excluded).
    for i in range(5) {
        println(f"i = {i}");
    }

    // A two-argument range sets the start. Sums 10..15 (10 + 11 + 12 + 13 + 14).
    let sum: int32 = 0;
    for n in range(10, 15) {
        sum += n;
    }
    println(f"sum of [10, 15) = {sum}");

    // The bounds are ordinary expressions, and `break`/`continue` work as in any
    // loop. The element type is inferred from the bounds -- here `int32` from the
    // literals; `range<int64>(n)` (or an int64 bound) would make `k` an int64.
    for k in range(100) {
        if (k % 2 != 0) { continue; }   // even values only
        if (k > 8) { break; }           // stop past 8
        println(f"even k = {k}");
    }

    return 0;
}
