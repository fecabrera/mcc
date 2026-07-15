import "std/io";

// Flow-narrowing: a null-check `if` guard proves a plain `T*` local non-null,
// so idiomatic checked code crosses into a @nonnull parameter (see nonnull.mc)
// with no postfix `p!` assertion. Three guard shapes narrow, one per helper
// below. The proof is purely compile-time: the guard's branch is the only
// code emitted, the narrowing itself adds no instructions.
// Prerequisites: nonnull.mc.
fn first(@nonnull p: int32*) -> int32 {
    return *p; // no null check needed: every caller proved p non-null
}

// Shape 1: the positive guard. `if (p != null)` proves p inside the then
// branch. (With an else, `if (p == null) {A} else {B}` symmetrically
// proves p in B.)
fn show(p: int32*) -> int32 {
    if (p != null) {
        return first(p); // ok: p is narrowed inside the guarded branch
    }
    return -1;           // outside the guard p is unproven again
}

// Shape 2: the C-idiomatic early guard. An else-less `if (p == null)` whose
// body always diverges (return, break, continue, a @noreturn call like
// abort(), an `unreachable;`, or every nested path diverging) proves p for
// the remainder of the enclosing scope. The abort-guard form is shown in
// noreturn.mc.
fn get(p: int32*) -> int32 {
    if (p == null) {
        return 0;
    }
    return first(p);     // ok: the early guard already handled null
}

// Shape 3: and/or chains thread through both shapes. A positive `and` guard
// proves every operand it pins down inside the then branch; a diverging
// `or` guard proves them all for the remainder. (The other directions, a
// true `or` or a false `and`, pin down neither operand, so they prove
// nothing.) Short-circuiting itself narrows too: in `p != null and first(p)`
// the right operand only runs when the left held, so p is proven while it
// evaluates.
fn add(p: int32*, q: int32*) -> int32 {
    if (p == null or q == null) {
        return -1;
    }
    return first(p) + first(q); // ok: both proven for the remainder
}

fn max_of(p: int32*, q: int32*) -> int32 {
    if (p != null and q != null and first(p) > first(q)) {
        return first(p);        // ok: both proven in the then branch
    }
    return -1;
}

fn main() -> int32 {
    let x: int32 = 42;

    // Both helpers take a plain int32*, so null is a legal argument; the
    // guards inside pick the path at runtime while proving the non-null
    // path at compile time.
    println(f"show(&x) = {show(&x)}, show(null) = {show(null)}");
    println(f"get(&x) = {get(&x)}, get(null) = {get(null)}");

    let y: int32 = 7;
    println(f"add(&x, &y) = {add(&x, &y)}, add(&x, null) = {add(&x, null)}");
    println(f"max_of(&x, &y) = {max_of(&x, &y)}");
    return 0;
}

// Narrowing is deliberately conservative: these per-name facts attach only
// to bare local pointer variables. Globals and index expressions like a[i]
// never narrow, and a reference parameter never carries a name fact (an aliasing
// callee could null it without naming it here); taking &p anywhere in the
// function disables narrowing of p; and the fact dies on anything that
// could null the variable: reassigning p, passing p as a reference argument, or a
// shadowing `let p`. Field projections like s.p and b->data narrow too, as
// access-path facts with a stricter invalidation model: see
// nonnull_projections.mc. A loop drops exactly
// the facts it could invalidate, and no others: see nonnull_loops.mc for the
// full loop story (guard-then-loop, loop-header guards, post-exit proofs).
// Facts also seed through `let`: a pointer binding whose initializer is
// proven (`let q = p;` under a guard, `let p = &x;`, `let q = p!;`) starts
// narrowed under the same rules. Where narrowing cannot see the invariant,
// the postfix `p!` assertion is the escape hatch: see nonnull_assert.mc.
// See also: nonnull.mc for @nonnull itself and the always-non-null sources;
// nonnull_loops.mc for narrowed facts crossing loops;
// nonnull_projections.mc for the same guards proving struct fields;
// nonnull_assert.mc for the `p!` escape hatch;
// memory/nonnull_heap_buffers.mc for the one-guard migration of heap
// buffers across the stdlib's @nonnull contracts;
// types/unchecked_dereference.mc for the opt-in `-Wunchecked-dereference`
// class, which reuses this proof relation to report (not reject) every
// dereference these guards leave unproven.
