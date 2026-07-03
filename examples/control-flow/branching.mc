import "std";

// `if` / `else if` / `else` branch at runtime. The condition is any integer:
// non-zero is true, zero is false, as in C -- there is no separate bool to
// reach for. `and` / `or` are the logical operators (there is no `&&` / `||`);
// they short-circuit and bind looser than comparisons, so a compound condition
// needs no inner parentheses.

fn main() -> int32 {
    let n: int32 = 7;

    // A three-way branch: the first true arm runs, the rest are skipped.
    if (n > 10) {
        println("big");
    } else if (n > 5) {
        println("medium");            // this arm runs
    } else {
        println("small");
    }

    // Any integer is a condition on its own: non-zero is true.
    let flag: int32 = 1;
    if (flag) {
        println("non-zero is true");
    }

    // `and` short-circuits: a false left means the right is never evaluated.
    if (n > 5 and n < 10) {
        println("n is in (5, 10)");
    }

    // `or` short-circuits the other way: a true left skips the right.
    if (n < 0 or n > 5) {
        println("n is negative or big");
    }

    return 0;
}

// See also: loops.mc for while / until / break / continue; case_when.mc for
// multi-way dispatch without an if/else-if chain.
