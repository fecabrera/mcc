import "std/io";

// Flow-narrowing across loops: how the null-check facts from
// nonnull_narrowing.mc behave when a `while`/`until`/`for` sits between the
// guard and the use. A loop body re-runs on the back edge, where a later
// iteration may already have nulled a pointer, so at loop entry the compiler
// pre-scans the whole loop (condition and body, nested statements, defer
// bodies, both branches of an @if) and kills exactly the facts the loop
// could invalidate: an assignment to the name, a shadowing `let`, or lending
// the bare name as a mut argument. Every other fact survives, both inside
// the loop and past its exit.
// Prerequisites: nonnull.mc and nonnull_narrowing.mc.
fn first(@nonnull p: int32*) -> int32 {
    return *p; // no null check needed: every caller proved p non-null
}

// The guard-then-loop idiom. The loop below never touches p, so the early
// guard's fact survives into the body and past the loop, with no in-body
// guard and no `p!` hatch.
fn sum_repeated(p: int32*, times: int32) -> int32 {
    if (p == null) return -1;
    let total: int32 = 0;
    let i: int32 = 0;
    while (i < times) {
        total += first(p);   // ok: this loop cannot invalidate p
        i += 1;
    }
    return total + first(p); // ok: the surviving fact outlives the loop too
}

// A loop that reassigns the pointer kills its fact at entry. Re-guard inside
// the body instead: a body-local guard re-establishes the fact on every
// iteration, and the reassignment only kills it until the next pass.
fn drain(p: int32*) -> int32 {
    if (p == null) return -1;
    let seen: int32 = 0;
    let rounds: int32 = 0;
    while (rounds < 2) {
        // seen = first(p); // error: cannot pass a possibly-null pointer
        if (p != null) {
            seen += first(p); // ok: proven fresh each iteration
        }
        p = null;             // this assignment is what killed the outer fact
        rounds += 1;
    }
    return seen;
}

// Loop-header narrowing: the condition itself is a guard. `while (cur != null)`
// (or `until (cur == null)`) proves cur at the top of every iteration, and a
// mid-body reassignment is fine because the back edge re-tests the condition.
fn sum_until_null(slots: int32**) -> int32 {
    let total: int32 = 0;
    let i: int32 = 0;
    let cur: int32* = slots![0];
    while (cur != null) {
        total += first(cur); // ok: the header proves cur on every pass
        i += 1;
        cur = slots![i];     // fine: re-proven before the next use
    }
    return total;
}

// The post-exit proof: a loop that can only leave through its condition
// proves the exit direction after it. Whatever this body does to p, the only
// way past the `while` is p != null. (A `break` in the body disables this:
// it reaches the code below without re-testing the condition.)
fn acquire(preferred: int32*, fallback: int32*) -> int32 {
    let p: int32* = preferred;
    while (p == null) {
        p = fallback;        // keep trying until something non-null lands
    }
    return first(p);         // ok: p is proven non-null after the loop
}

fn main() -> int32 {
    let x: int32 = 1;
    let y: int32 = 2;
    let z: int32 = 4;

    println("sum_repeated(&x, 3) = {}", sum_repeated(&x, 3));
    println("drain(&y) = {}", drain(&y));

    let slots: int32*[4] = [&x, &y, &z, null];
    println("sum_until_null = {}", sum_until_null(slots));

    println("acquire(null, &z) = {}", acquire(null, &z));
    return 0;
}

// See also: nonnull_narrowing.mc for the guard shapes that create these
// facts; nonnull_projections.mc for field facts, which every loop drops
// wholesale at entry (bind the field to a name to carry it across);
// nonnull_assert.mc for the `p!` hatch where no guard fits;
// memory/nonnull_heap_buffers.mc for the guard-then-loop idiom against the
// stdlib's @nonnull contracts.
