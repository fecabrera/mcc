import "std/io";

// Constant-condition loop folding. A loop whose condition folds to true at
// compile time -- `while (true)`, `while (1)`, its dual `until (false)`, a
// `const` reference, constant arithmetic -- emits no exit edge; with no
// `break` in the body it has no exit block at all, so the loop DIVERGES,
// exactly like a `return`. The payoff is two checks that stop demanding
// dummy code: a function may end in such a loop with no trailing return,
// and a block expression may end in one with no trailing emit.
//
// Prerequisites: while.mc (break / continue), until.mc,
// block_expressions.mc (emit), functions/noreturn.mc (@noreturn).

// THE FUNCTION LIFT. This poll-style server loop can only leave through the
// `return` inside the body, and the compiler now knows it: no "may end
// without a return" error, no dummy return after the loop. `return` leaves
// by its own edge and never gates the fold; neither do `emit`, `continue`,
// or a call to a @noreturn function.
fn next_request(calls: int32) -> int32 {
    while (true) {
        calls += 1;
        if (calls >= 3) {
            return calls * 100;   // the only way out
        }
    }
}   // no trailing return: nothing can fall out of a break-free forever loop

// THE DUAL SPELLING. `until (false)` folds identically: "loop until false
// becomes true" never stops. Same divergence, same lift.
fn drain(level: int32) -> int32 {
    let steps: int32 = 0;
    until (false) {
        if (level <= 0) { return steps; }
        level -= 1;
        steps += 1;
    }
}

// Not just the literal spellings: any condition the constant folder proves
// always-run diverges the same way, a `const` reference included.
const FOREVER = true;

fn main() -> int32 {
    println(f"next_request(0) = {next_request(0)}");
    println(f"drain(5) = {drain(5)}");

    // THE BLOCK-EXPRESSION LIFT. A block expression may now end in a
    // forever loop that `emit`s from inside; this shape used to be the
    // error "block expression may end without an emit". The condition here
    // is the const above, folded to true like the literal form.
    let first: int32 = {
        let candidate: int32 = 40;
        while (FOREVER) {
            candidate += 1;
            if (candidate % 7 == 0) { emit candidate; }
        }
    };
    println(f"first multiple of 7 past 40 = {first}");

    // THE GATE. One `break` anywhere in the body -- inside a `case` arm, a
    // nested block expression, even a `defer` -- keeps the exit block, and
    // the fold is off: code after the loop is live again (this println
    // runs), and a function ending in such a loop needs its trailing
    // return again, because the loop can fall through. Only a break in a
    // *nested* loop never gates: it targets the inner loop, not this one.
    let i: int32 = 0;
    while (true) {
        i += 1;
        if (i == 4) { break; }   // the gate: this loop has an exit again
    }
    println(f"counted to {i}");   // live and reachable, no warning

    return 0;
}

// With the exit edge gone, nothing placed after a break-free forever loop
// could ever run; the opt-in `-Wdead-code` class reports such a tail as
// "unreachable code: nothing runs after a loop that never exits". This
// file keeps every statement live -- see dead_code.mc for the class in
// action. Two non-goals: the never-runs duals `while (false)` /
// `until (true)` are not folded away (their bodies stay type-checked, like
// `if (false)`), and `for` loops are untouched. A @noreturn spin body
// needs no help anymore either: `@noreturn fn spin() { while (true) {} }`
// diverges by the loop itself, with no planted `unreachable` left in its
// IR (see functions/noreturn.mc).
// See also: while.mc; until.mc; block_expressions.mc; dead_code.mc;
// functions/noreturn.mc.
