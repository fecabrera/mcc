import "std/io";

// Flow-narrowing for field projections: the null-check guards from
// nonnull_narrowing.mc also prove a pointer-typed struct field non-null, so
// a checked field crosses into a @nonnull parameter (see nonnull.mc) with no
// postfix `!` assertion. The fact is keyed by the access path, not by its
// spelling: `(*b).data` and `b->data` are one fact, and deeper paths like
// `o->buf->data` prove the same way. Because the field itself lives in
// memory that other code can reach, a projection fact dies more eagerly
// than a name fact: stores, loops, and calls that might write memory all
// kill it. Calls the compiler proves write-free are the exception, shown
// below; binding the field to a local is the idiom everywhere else.
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

    // Calls used to kill every projection fact, since any callee could reach
    // the field through an escaped or global pointer and null it. Now the
    // compiler computes a per-function write effect, transitively through
    // the call graph: a call to a callee proven write-free preserves
    // projection facts, and every other call still kills them. first's whole
    // body is `return *p;`, a load and no store, so it is write-free and the
    // guarded field crosses it as many times as needed:
    let a = first(b->data);    // ok: the fact is alive at this call
    return a + first(b->data); // still ok: the write-free call preserved it
}

// "Write-free" is strict and transitive. A function is tainted by any
// through-memory store (`*p = v`, `a[i] = v`, `s.f = v`, or their compound
// forms; even a store to the function's own local struct counts), by
// assigning a mut parameter or a global, by anything opaque (@asm, a call
// through a function-pointer value, va_start, a bodyless @extern callee, a
// `for ... in` protocol loop; the builtin range/enumerate counting loops are
// fine), and by calling any name with a tainted same-name candidate. tally
// writes a global, so calling it kills the fact. println is the same story:
// it bottoms out in @extern printf.
@static let calls: int32 = 0;

fn tally() -> int32 {
    calls += 1;
    return calls;
}

fn audit(b: struct buffer*) -> int32 {
    if (b == null or b->data == null) {
        return -1;
    }
    let a = first(b->data);       // ok: the fact is alive at this call
    let n = tally();              // a writing call: the fact dies here
    // return a + n + first(b->data); // error: cannot pass a possibly-null
    //                                // pointer (tally() killed the fact)
    return a + n;
}

// The idiom for a checked field that must cross a writing call or a loop:
// bind it while the fact is alive. `let q = b->data;` under the guard seeds
// a *name* fact for q (see nonnull_narrowing.mc), and name facts survive
// every call and any loop that leaves q alone (see nonnull_loops.mc). Loops
// are the sharper motivation now: loop entry still drops projection facts
// wholesale, so `first(b->data)` inside this loop would not compile even
// though first is write-free. (A projection fact also still dies at the
// caller's own through-memory stores and when the base is reassigned,
// shadowed, or lent as a mut argument; only the call kill was refined.)
fn sum(b: struct buffer*) -> int32 {
    if (b == null or b->data == null) {
        return -1;
    }
    let q = b->data;         // bound while the fact is alive, so q starts proven
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

    println(f"peek(buf) = {peek(buf)}, peek(none) = {peek(none)}");
    println(f"read(&buf) = {read(&buf)}, read(&none) = {read(&none)}");
    println(f"audit(&buf) = {audit(&buf)}, audit(&none) = {audit(&none)}");
    println(f"sum(&buf) = {sum(&buf)}");
    return 0;
}

// Projection facts form at the same guard sites as name facts (the then
// branch, the diverging early guard, while headers and post-exit conditions,
// and/or chains) but are choosier about the path. The base must be a local
// variable: globals and array elements like bs[0].data never form a fact,
// and a @volatile owner anywhere along the path never does either (the field
// could change between the check and the use). A guard whose later operand
// contains a call, as in `if (b->data != null and check())`, forms no fact
// even when check() is provably write-free: formation is a syntactic rule,
// conservative about anything that runs after the null test. The write-effect
// analysis refines only where facts die at calls, never where they form.
// Where no guard fits, `b->data!` is still the hatch (see nonnull_assert.mc).
// See also: nonnull_narrowing.mc for the guard shapes and name facts;
// nonnull_loops.mc for name facts crossing loops; nonnull_assert.mc for the
// `!` escape hatch; memory/nonnull_heap_buffers.mc for a guarded field
// surviving the stdlib's write-free crc32; types/structs.mc for struct
// field access.
