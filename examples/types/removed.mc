import "std/io";
import "libc/stdio";   // printf for the %g float rendering

// `@removed("message")` is the terminal state of the availability lifecycle,
// one step past `@deprecated`: a function goes from available, to
// `@deprecated(msg)` (warns, still callable), to `@removed(msg)` (a hard
// compile error at every call site), to finally deleted (the name gone, a
// bare unknown-function error). The declaration is a tombstone: the
// implementation is gone, so it is written bodiless, and it exists so that
// pulling an implementation still gives callers a targeted message for a
// release cycle:
//
//   file: error: line N: 'max_of' was removed: use largest instead
//
// Unlike `@deprecated`, this rides the error channel, not the warning one: a
// live call aborts the build like any compile error, and there is nothing
// for `-Werror` to promote, since an uncalled tombstone compiles clean and
// warns nothing. That is why this file builds clean under CI's `-Werror`:
// the only calls to `max_of` sit in a `-D`-gated `@if` branch that a plain
// build drops dead.
//
// Prerequisites: deprecated.mc (the lifecycle's previous step and this same
// `-D`-gated old-API pattern), warnings.mc (`-D` defines). Fire the error by
// hand from the repo root:
//
//   pipenv run python -m mcc examples/types/removed.mc --run -DOLD_API
//     examples/types/removed.mc: error: line N: 'max_of' was removed: use largest instead
//     (exit status 1: the build aborts at the first removed use, nothing runs)

// The current API.
fn largest<T>(a: T, b: T) -> T {
    if (a > b) { return a; }
    return b;
}

// The tombstone. Bodiless, because the implementation is gone; and this is
// the one place a generic function may go bodiless, since a tombstone never
// instantiates. Its signature is parsed but never resolved (only the name
// and the message register), so it stays valid even if its parameter types
// were deleted along with the body. One tombstone speaks for the whole
// former name: a live definition or overload of `max_of` next to it is a
// compile error at declaration time.
@removed("use largest instead")
fn max_of<T>(a: T, b: T) -> T;

// Code still on the old name no longer builds. `-DOLD_API` selects this
// branch, and compilation stops at the first removed use, reported at the
// caller's file and line. Errors abort immediately, so unlike the warnings
// in deprecated.mc, the second use below never gets its own report.
@if (OLD_API) {
    fn saturate_low(x: int32) -> int32 {
        let v = max_of(x, 0);        // the build aborts here
        return max_of<int32>(v, 0);  // explicit type args would error the
                                     // same way, before any instantiation
    }
}

fn main() -> int32 {
    // Migrated code builds and runs as ever; an uncalled tombstone costs
    // nothing and generates nothing.
    println(f"largest(3, 7)     = {largest(3, 7)}");
    printf("largest(2.5, 1.5) = %g\n", largest(2.5, 1.5));
    return 0;
}

// The error fires wherever the name would resolve to the removed function:
// a direct call, taking the function as a value (`let f: fn(...) = old;`),
// or a `for ... in` loop whose `_it`/`_next` protocol functions are removed.
// `@removed` combines with `@private` and `@extern`; it rejects
// `@deprecated` (removal is the step after deprecation, keep one), `@inline`,
// `@asm`, and `@static`. Functions only for now, like `@deprecated`;
// docs/language.md "Removed functions" has the full rules, including the
// `.mci` interface round-trip for shipped libraries.

// See also: deprecated.mc (the previous lifecycle step: warnings instead of
// errors, and the origin of the `-D`-gated old-API pattern), warnings.mc
// (`-D` defines and the warning channel this attribute deliberately skips),
// static_assert.mc (`@error`, the other build-aborting directive).
