import "std/io";

// The stdlib `panic` / `assert` pair from std/io: the @noreturn guard
// pattern noreturn.mc builds by hand, packaged. `panic(msg)` writes
// `panic: <msg>` VERBATIM to standard error and aborts the process with
// abort(): SIGABRT, exit status 134 under a shell. Braces are not
// placeholders in that arm (`panic("hello {}")` prints the braces), so
// runtime text always passes through safely. `panic(fmt, args...)` is a
// @format collector: `{}` placeholders render through std/format first,
// f-strings included, and `panic(f"x = {x}")` is the idiomatic spelling.
// `assert(cond, ...)` mirrors both arms with the prefix
// `assertion failed: ` when cond is false, and does nothing when it holds.
// On the panic path defers do NOT run and no atexit handlers fire; pending
// standard output is flushed first, so program output survives the abort.
// This program deliberately ENDS by panicking. That is safe here because
// CI only compiles the examples, it never runs them. Prerequisites:
// noreturn.mc, nonnull_narrowing.mc.

// `panic` is @noreturn, so an else-less null guard whose body panics
// diverges, proving p non-null for the rest of the scope: the same shape
// as noreturn.mc's `if (p == null) abort();` guard, with a report attached.
fn checked_first(p: int32*) -> int32 {
    if (p == null) {
        panic("checked_first(): null input");
    }
    // Note the spelling: `assert(p != null, "...")` would compile, but it
    // does NOT narrow p. Facts never flow through a call, only through a
    // guard the compiler can see, so the narrowing idiom is the panic
    // guard above.
    return *p;
}

fn main() -> int32 {
    let x: int32 = 42;

    // Passing asserts are silent: nothing prints, execution continues.
    // Both arms are always enabled (no NDEBUG stripping yet), and the
    // format arm's arguments are evaluated even when the condition holds.
    assert(x > 0, "x must be positive");            // verbatim message arm
    assert(x == 42, "x = {}, expected 42", x);      // format collector arm

    println("checked_first(&x) = {}", checked_first(&x));

    // This defer never runs: abort() is not a block exit, so the panic
    // below leaves it unrun (the same rule as exit(), see
    // control-flow/defer.mc). The println above still appears, because
    // panic flushes pending standard output before aborting.
    defer println("defer: never printed");

    // A trailing panic satisfies missing-return analysis like a `return`,
    // so this int32 function needs no dummy return after it. Expect
    // `panic: x = 42, giving up` on standard error, then SIGABRT.
    panic(f"x = {x}, giving up");
}

// See also: noreturn.mc (the hand-rolled panic-style helper this pair
// packages, and the @noreturn rules panic rides on);
// nonnull_narrowing.mc for the guard shapes that narrow;
// systems/formatting.mc for the `{}` modifiers, positional `{n}`, and
// f-string machinery behind the collector arms;
// control-flow/defer.mc for what a normal block exit runs that a panic
// does not. An owned `string` passes the verbatim arm as
// `panic(str as slice<char>)`. Full reference: docs/language.md,
// "Panic and assert".
