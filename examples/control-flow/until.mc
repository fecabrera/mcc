import "std/io";

// An `until` loop is the inverse of `while`: it repeats as long as its
// condition is *false* and stops once the condition becomes true. Reach for it
// when you are looping toward a goal rather than while some invariant holds.
// `break` / `continue` work exactly as in a `while`.

fn main() -> int32 {
    // Count down: the loop stops once `countdown == 0` becomes true.
    let countdown: int32 = 3;
    until (countdown == 0) {
        println("countdown = {}", countdown);
        countdown -= 1;
    }
    println("liftoff");

    // Loop until enough has accumulated. `continue` skips to the next check;
    // `break` would still bail out early.
    let sum: int32 = 0;
    let n: int32 = 0;
    until (sum >= 20) {
        n += 1;
        if (n % 2 == 0) { continue; }   // add only the odd numbers
        sum += n;
    }
    println("reached {} after adding odds up to {}", sum, n);

    return 0;
}

// See also: while.mc for the direct form; branching.mc for if / else.
