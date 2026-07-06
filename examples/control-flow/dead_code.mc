import "std";

// `-Wdead-code`, the second opt-in warning class (the framework itself is
// toured in types/unchecked_dereference.mc). The generator has always
// silently dropped statements it can prove unreachable; this class reports
// each drop instead of hiding it: one warning per dead region, at the
// region's first statement, naming the construct that killed it. Like
// every opt-in class it never changes the code generated, so this file
// runs identically with or without the flag, and its live dead code is
// CI-safe: the class is off by default, and a bare `-Werror` build is
// unaffected by a disabled class.
//
// Prerequisites: while.mc (break/continue), unreachable.mc, defer.mc,
// block_expressions.mc (emit), functions/noreturn.mc (@noreturn), and
// types/unchecked_dereference.mc for the flag mechanics (-W<name>, -Wall,
// the unknown-class error, -Werror promotion), not re-taught here.
//
// Fire the class by hand from the repo root:
//
//   pipenv run python -m mcc examples/control-flow/dead_code.mc --run
//     (silent: the class is off by default, and dropping the code is legal)
//
//   pipenv run python -m mcc examples/control-flow/dead_code.mc --run -Wdead-code
//     examples/control-flow/dead_code.mc: warning: line N: unreachable code: nothing runs after the 'return' above [-Wdead-code]
//     ... one line per dead region below (eight in this file), then the
//     program runs normally with its output unchanged. Under -Werror each
//     line promotes to `error: ... [-Werror=dead-code]` and the build
//     fails; `-Wall` enables this class along with the rest.

// THE BASIC KILLER: `return`. Everything after it in the block is one dead
// region, and the region gets ONE warning, at its first statement; the
// second dead line below adds nothing to the report.
fn classify(n: int32) -> int32 {
    if (n < 0) {
        return -1;
        println("negative");    // warns: nothing runs after the 'return' above
        n = 0;                  // same region: no second warning
    }
    return 1;
}

// `break` and `continue` kill the rest of their block the same way. Each
// starts its own region, so this function reports twice.
fn first_multiple(of: int32, limit: int32) -> int32 {
    let i: int32 = 1;
    while (i <= limit) {
        if (i % of == 0) {
            break;
            println("found");   // warns: nothing runs after 'break'
        }
        i += 1;
        continue;
        i += 100;               // warns: nothing runs after 'continue'
    }
    return i;
}

// `unreachable` diverges like a return (unreachable.mc), so code past it
// is dead too. Reaching the statement would be UB; the *report* is about
// the line after it, which can never run at all.
fn low_digit(n: int32) -> int32 {
    if (n >= 0) {
        return n % 10;
    }
    unreachable;                // callers only pass non-negatives
    return -1;                  // warns: nothing runs after 'unreachable'
}

// A direct call to a @noreturn function (functions/noreturn.mc) diverges
// at the call site, killing the rest of the block. libc's `exit` inside
// give_up is one; give_up itself is one for its callers.
@noreturn fn give_up(code: int32) {
    println("giving up");
    exit(code);
}

fn ensure_positive(n: int32) -> int32 {
    if (n <= 0) {
        give_up(1);
        println("rescued");     // warns: nothing runs after a call to a @noreturn function
    }
    return n;
}

// A statement is itself a killer when EVERY generated path through it
// diverges: this if/else returns on both arms, so the tail is dead. Same
// rule for a `case` whose arms all diverge, a bare block, and the taken
// branch of an `@if`. (One diverging arm is not enough: remove the `else`
// arm and the tail turns live and warning-free.)
fn magnitude(n: int32) -> int32 {
    if (n >= 0) {
        return n;
    } else {
        return -n;
    }
    println("checked");         // warns: every path through the statement above diverges
}

// A `defer` in a dead region is dead code like any other statement: it
// warns, and its body NEVER runs, because a dropped defer is never
// registered in the first place. Only "live defer" prints below.
fn cleanup_demo() {
    defer println("live defer");
    println("cleanup_demo body");
    return;
    defer println("dead defer");    // warns: nothing runs after the 'return' above
}

fn main() -> int32 {
    println("classify(-5) = %d", classify(-5));
    println("first_multiple(3, 10) = %d", first_multiple(3, 10));
    println("low_digit(47) = %d", low_digit(47));
    println("ensure_positive(5) = %d", ensure_positive(5));
    println("magnitude(-8) = %d", magnitude(-8));
    cleanup_demo();

    // Inside a block expression, `emit` is the divergence: it ends the
    // block with its value, so trailing statements are dead.
    let n: int32 = 21;
    let doubled: int32 = {
        emit n * 2;
        println("emitted");     // warns: nothing runs after 'emit'
    };
    println("doubled = %d", doubled);

    // THE DELIBERATE NON-CASE. Code after `while (true)` does NOT warn,
    // even when `break` is the only way out: the generator still emits the
    // loop's exit edge, so the tail is structurally reachable today. The
    // constant-condition loop folding item on the roadmap will extend the
    // class here.
    let i: int32 = 0;
    while (true) {
        i += 1;
        if (i == 4) {
            break;
        }
    }
    println("counted to %d", i);    // live: no warning

    return 0;
}

// See also: types/unchecked_dereference.mc for the opt-in class framework
// this class rides (-W<name>, -Wall, the unknown-class hard error, and the
// [-Werror=<name>] promotion); unreachable.mc and functions/noreturn.mc
// for the diverging constructs themselves; defer.mc for what a *live*
// defer does at block exit.
