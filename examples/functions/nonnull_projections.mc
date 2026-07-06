import "std";

// Flow-narrowing for field projections: the null-check guards from
// nonnull_narrowing.mc also prove a pointer-typed struct field non-null, so
// a checked field crosses into a @nonnull parameter (see nonnull.mc) with no
// postfix `!` assertion. The fact is keyed by the access path, not by its
// spelling: `(*b).data` and `b->data` are one fact, and deeper paths like
// `o->buf->data` prove the same way. Because the field itself lives in
// memory that other code can reach, a projection fact dies far more eagerly
// than a name fact; binding the field to a local is the idiom that carries
// the proof across calls and loops.
// Prerequisites: nonnull.mc, nonnull_narrowing.mc, nonnull_loops.mc, and
// struct field access (types/structs.mc).
struct buffer {
    data: int32*;
    size: int32;
}

fn first(@nonnull p: int32*) -> int32 {
    return *p; // no null check needed: every caller proved the field non-null
}

// The positive guard, on a field instead of a bare name. `if (b.data != null)`
// proves b.data inside the then branch, and the proven projection crosses the
// @nonnull slot directly. Any local base works: a plain local, a value
// parameter like this one, or a mut/@nonnull parameter.
fn peek(b: struct buffer) -> int32 {
    if (b.data != null) {
        return first(b.data); // ok: the guarded field satisfies @nonnull
    }
    return -1;
}

// and/or chains thread names and projections together: one diverging guard
// proves the base pointer and its field for the remainder, in guard order
// (b is already proven by the time b->data is tested).
fn read(b: struct buffer*) -> int32 {
    if (b == null or b->data == null) {
        return -1;
    }

    // A projection fact dies at EVERY function call: any callee could reach
    // the field through an escaped or global pointer and null it. (It also
    // dies at every through-memory store such as `*p = v`, `a[i] = v`, or
    // `s.f = v`, wholesale at loop entry, and when the base is reassigned,
    // shadowed, or lent as a mut argument.) So the first crossing below is
    // fine, and a second would not be:
    let a = first(b->data);        // ok: the fact is alive at this call
    // return a + first(b->data); // error: cannot pass a possibly-null
    //                            // pointer (the call above killed the fact)
    return a;
}

// The idiom for a checked field that must cross calls or loops: bind it
// while the fact is alive. `let q = b->data;` under the guard seeds a *name*
// fact for q (see nonnull_narrowing.mc), and name facts survive calls and
// any loop that leaves q alone (see nonnull_loops.mc).
fn sum(b: struct buffer*) -> int32 {
    if (b == null or b->data == null) {
        return -1;
    }
    let q = b->data;         // bound before any call, so q starts proven
    let total: int32 = 0;
    let i: int32 = 0;
    while (i < b->size) {
        total += first(q);   // ok: q's name fact survives every iteration
        i += 1;
    }
    return total + first(q); // and it survives past the loop too
}

fn main() -> int32 {
    let x: int32 = 42;
    let buf = struct buffer { data = &x, size = 3 };
    let none = struct buffer { data = null, size = 0 };

    println("peek(buf) = %d, peek(none) = %d", peek(buf), peek(none));
    println("read(&buf) = %d, read(&none) = %d", read(&buf), read(&none));
    println("sum(&buf) = %d", sum(&buf));
    return 0;
}

// Projection facts form at the same guard sites as name facts (the then
// branch, the diverging early guard, while headers and post-exit conditions,
// and/or chains) but are choosier about the path. The base must be a local
// variable: globals and array elements like bs[0].data never form a fact,
// and a @volatile owner anywhere along the path never does either (the field
// could change between the check and the use). A guard whose later operand
// contains a call, as in `if (b->data != null and check())`, forms no fact
// for the same reason calls kill facts: check() runs after the null test and
// could null the field before the branch. Where no guard fits, `b->data!` is
// still the hatch (see nonnull_assert.mc).
// See also: nonnull_narrowing.mc for the guard shapes and name facts;
// nonnull_loops.mc for name facts crossing loops; nonnull_assert.mc for the
// `!` escape hatch; types/structs.mc for struct field access.
