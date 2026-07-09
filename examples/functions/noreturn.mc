import "std/io";

// `@noreturn` marks a void function that never returns to its caller: it
// exits, aborts, or loops forever. The compiler then treats every direct
// call as diverging, like a `return`: no dummy return is needed after it,
// code past it is silently dropped (reported by the opt-in `-Wdead-code`
// class: see control-flow/dead_code.mc), and a null guard whose body is one
// narrows the pointer (the same rules as nonnull_narrowing.mc). libc's
// `exit`, `abort`, and `_Exit` (import "std/io" or "libc/stdlib") ship
// annotated. Prerequisites: functions.mc, nonnull.mc, nonnull_narrowing.mc.

// A panic-style helper: report and exit. `@noreturn` must be void (a call
// can then never sit in expression position), and a `return` inside the
// body is the compile error "cannot return from @noreturn function 'fail'
// (it promises never to return)".
@noreturn fn fail(msg: char*, code: int32) {
    println("fatal: {}", msg);
    exit(code);   // itself @noreturn: nothing runs past this line
}

// Divergence terminates the caller's block. This `if` arm ends in fail(),
// so past the guard the index is known good; anything written after the
// fail() call would be dropped, exactly like code after a `return`.
fn read_byte(data: uint8*, size: uint64, i: uint64) -> uint8 {
    if (i >= size) {
        fail("index out of range", 2);
    }
    return data![i];
}

fn first(@nonnull p: int32*) -> int32 {
    return *p;
}

// The C-idiomatic abort guard. An else-less `if (p == null)` whose body is
// a @noreturn call diverges, so it proves p non-null for the rest of the
// scope, exactly like the early-return guard of nonnull_narrowing.mc: the
// checked call to first() below compiles with no `p!` assertion.
fn checked_first(p: int32*) -> int32 {
    if (p == null) abort();
    return first(p);   // ok: the aborting guard already handled null
}

// Falling off the end of a @noreturn body is not an error: the promise is
// the author's, and the compiler plants an `unreachable` at the end, so
// actually reaching it is undefined behavior (C11 _Noreturn semantics).
// That is what makes the canonical spin form legal with no trailing
// anything -- the loop simply never falls through.
@noreturn fn spin() {
    while (true) {}
}

fn main() -> int32 {
    // The diverging paths exist to be dead: every call below stays on the
    // returning side, so the program runs to completion and exits 0.
    let bytes: uint8[4] = [10, 20, 30, 40];
    println("read_byte(bytes, 4, 2) = {}", read_byte(bytes, 4, 2));

    let x: int32 = 42;
    println("checked_first(&x) = {}", checked_first(&x));
    return 0;
}

// A @noreturn call is not a block exit, so enclosing defers do NOT run on
// that path: `exit(1);` leaves them unrun, matching C, where exit() never
// unwinds the calling stack (see control-flow/defer.mc). And `&fail` is
// allowed, but the plain fn(char*, int32) type drops the flag, so a call
// through the pointer is assumed to return (see function_pointers.mc).
// See also: control-flow/unreachable.mc for the statement form, a *path*
// that never executes rather than a function that never returns;
// nonnull_narrowing.mc for the guard shapes the abort guard slots into;
// forward_declarations.mc for the prototype pairing rules (a prototype
// must agree with its definition on @noreturn, as must an @extern
// redeclaration).
