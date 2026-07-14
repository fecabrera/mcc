import "std/io";

// The stdlib `panic` / `assert` pair from std/io: the @noreturn guard
// pattern noreturn.mc builds by hand, packaged. `panic(msg)` writes its
// message VERBATIM to standard error and aborts the process with abort():
// SIGABRT, exit status 134 under a shell. `assert(cond, msg)` panics with
// the prefix `assertion failed: ` when cond is false, and does nothing
// when it holds. Each is ONE member -- there is no `panic(fmt, args...)`
// collector -- taking a single `T extends slice<const char>` message (the
// same const-covariant family print takes), so a literal, a slice of
// either constness, and an owned string -- with it an f-string or a
// .format rendering -- all bind directly. Braces are never placeholders
// (`panic("hello {}")` prints the braces), so runtime text always passes
// through safely.
//
// The blessed style is a PLAIN VERBATIM message. A formatted one
// compiles -- `panic(f"x = {x}")` renders to an owned string like
// anywhere else (types/fstring_values.mc) -- but panic diverges, so the
// statement-end drop that would clean the temporary up never runs: the
// rendering LEAKS on the dying path, harmless only because the process
// is aborting. (The removed @format collector always leaked the same
// allocation invisibly inside its own body; the value spelling makes the
// cost explicit at the call site.) Format on the way down only when the
// dynamic value earns its place in the message; the drop stamps below
// make both sides visible.
//
// On the panic path defers do NOT run and no atexit handlers fire;
// pending standard output is flushed first, so program output survives
// the abort. This program deliberately ENDS by panicking. That is safe
// here because CI only compiles the examples, it never runs them.
// Prerequisites: noreturn.mc, nonnull_narrowing.mc, and (for the message
// lifetimes) types/fstring_values.mc.

// A drop-stamped value for the message holes: its destructor prints, so
// the output shows exactly which renderings were cleaned up -- and which
// one never was.
struct probe {
    id: int32;
}

fn probe::constructor(mut self: probe, id: int32) {
    self.id = id;
}

fn probe::destructor(mut self: probe) {
    println(f"drop {self.id}");
}

fn mk(id: int32) -> own probe {
    return probe(id);
}

fn format(mut str: string, const value: probe, const modifier: slice<char>) {
    format(str, value.id, modifier);
}

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

    // Passing asserts continue silently -- except that the message is
    // BUILT whether or not the condition holds (always enabled, no NDEBUG
    // stripping yet): the f-string renders on every pass, and when the
    // assertion holds its temporaries are destroyed at statement end,
    // which is what the `drop 1` line is. The verbatim literal builds
    // nothing, which is why hot-path asserts prefer one.
    assert(x > 0, "x must be positive");           // blessed: verbatim
    assert(x == 42, f"x = {x}, probe {mk(1)}");    // renders, then drop 1

    println(f"checked_first(&x) = {checked_first(&x)}");

    // This defer never runs: abort() is not a block exit, so the panic
    // below leaves it unrun (the same rule as exit(), see
    // control-flow/defer.mc). The println above still appears, because
    // panic flushes pending standard output before aborting.
    defer println("defer: never printed");

    // The format-explicitly finale, knowing the cost: the message renders
    // (probe 9 is built into it), panic diverges, and the statement-end
    // drop never runs -- no `drop 9` ever appears, the leak on the dying
    // path. A trailing panic satisfies missing-return analysis like a
    // `return`, so this int32 function needs no dummy return after it.
    // Expect `x = 42, giving up (probe 9)` on standard error, then
    // SIGABRT.
    panic(f"x = {x}, giving up (probe {mk(9)})");
}

// See also: noreturn.mc (the hand-rolled panic-style helper this pair
// packages, and the @noreturn rules panic rides on);
// nonnull_narrowing.mc for the guard shapes that narrow;
// types/fstring_values.mc for the string-value rendering and the drop
// schedule the messages ride; systems/formatting.mc for the modifier
// grammar inside the holes; control-flow/defer.mc for what a normal
// block exit runs that a panic does not. Full reference:
// docs/language.md, "Panic and assert".
