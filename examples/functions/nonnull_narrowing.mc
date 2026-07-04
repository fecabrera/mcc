import "std";

// Flow-narrowing: a null-check `if` guard proves a plain `T*` local non-null,
// so idiomatic checked code crosses into a @nonnull parameter (see nonnull.mc)
// with no postfix `p!` assertion. Two guard shapes narrow, one per helper
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
// body always diverges (return, break, continue, or every nested path
// returning) proves p for the remainder of the enclosing scope.
fn get(p: int32*) -> int32 {
    if (p == null) {
        return 0;
    }
    return first(p);     // ok: the early guard already handled null
}

fn main() -> int32 {
    let x: int32 = 42;

    // Both helpers take a plain int32*, so null is a legal argument; the
    // guards inside pick the path at runtime while proving the non-null
    // path at compile time.
    println("show(&x) = %d, show(null) = %d", show(&x), show(null));
    println("get(&x) = %d, get(null) = %d", get(&x), get(null));
    return 0;
}

// Narrowing is deliberately conservative: only bare local pointer variables
// narrow. Globals, mut parameters, and member/index expressions like s.p or
// a[i] never do; taking &p anywhere in the function disables narrowing of p;
// the fact dies on reassigning p, passing p as a mut argument, or a shadowing
// `let p`; and all facts drop at loop entry, so guard inside the loop body
// (a body-local guard re-establishes the fact each iteration). Where
// narrowing cannot see the invariant, the postfix `p!` assertion is the
// escape hatch: see nonnull_assert.mc.
// See also: nonnull.mc for @nonnull itself and the always-non-null sources;
// nonnull_assert.mc for the `p!` escape hatch;
// memory/nonnull_heap_buffers.mc for both idioms working together against
// the stdlib's @nonnull contracts (guard the straight line, assert in
// loops).
