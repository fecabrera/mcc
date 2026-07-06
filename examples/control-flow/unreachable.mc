import "std";

// `unreachable;` asserts that a path is never executed. The statement
// diverges like a `return`: no return is needed after (or instead of) it,
// and code past it is silently dropped (the opt-in `-Wdead-code` class
// reports such drops: see dead_code.mc). It lowers to LLVM `unreachable`,
// so actually reaching it at runtime is undefined behavior, exactly like
// C's __builtin_unreachable(): an assertion the compiler trusts, not a
// checked trap. Prerequisites: case_when.mc.

// Its idiomatic home is the `else` arm of an exhaustive `case`. Every value
// name_of() is ever given is covered by a `when`, and `else: unreachable;`
// says so; without it, the compiler would demand a dummy trailing return
// for a fall-through path that can never happen.
fn name_of(dir: int32) -> char* {   // dir is always 0..3 by construction
    case (dir) {
        when 0: return "north";
        when 1: return "east";
        when 2: return "south";
        when 3: return "west";
        else:   unreachable;        // asserts the case is exhaustive
    }
}                                   // no dummy return needed down here

fn main() -> int32 {
    // The caller holds up the assertion: `i % 4` keeps dir inside 0..3, so
    // the unreachable arm is never taken.
    let i: int32 = 0;
    while (i < 6) {
        println("step %d faces %s", i, name_of(i % 4));
        i += 1;
    }
    return 0;
}

// On a `case type` over an `any` box (whose `else:` is mandatory) the same
// arm asserts a closed universe of boxed types: see types/any.mc.
// `unreachable` is a reserved word, no longer usable as an identifier. As a
// diverging statement it also narrows: a null guard whose body is
// `unreachable;` proves the pointer like an early return does (see
// functions/nonnull_narrowing.mc). For a whole *function* that never
// returns, rather than a path that never executes, see
// functions/noreturn.mc: a @noreturn body that falls off its end gets this
// statement planted implicitly.
// See also: case_when.mc for the case statement itself.
