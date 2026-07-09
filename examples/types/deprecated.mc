import "std/io";

// `@deprecated("message")` is a declaration attribute on a function: the
// function stays fully callable and the program still builds and runs, but
// every call site emits a warning on the same channel as `@warning`, pointing
// at the caller's file and line with the migration message:
//
//   file: warning: line N: 'clamp_int' is deprecated: use clamp instead
//
// It also fires when the function is taken as a value (a call site in
// waiting), and it combines with `@private`, `@static`, `@extern`, `@inline`,
// and `@asm`. Functions only for now; types, enums, and globals come later.
//
// Prerequisites: warnings.mc (the warning channel, `-D` defines, `-Werror`),
// functions/function_pointers.mc (fn values, which warn here too). The old
// calls below sit in a `-D`-gated `@if` branch so a plain build compiles
// warning-clean, which matters because CI builds every example with
// `-Werror`. Fire the warnings by hand from the repo root:
//
//   pipenv run python -m mcc examples/types/deprecated.mc --run -DOLD_API
//     examples/types/deprecated.mc: warning: line N: 'clamp_int' is deprecated: use clamp instead
//     (one line per call site, then the program runs normally, same output)
//
//   pipenv run python -m mcc examples/types/deprecated.mc --run -DOLD_API -Werror
//     examples/types/deprecated.mc: error: line N: 'clamp_int' is deprecated: use clamp instead [-Werror]
//     (exit status 1, the program does not run)

// The current API.
fn clamp(x: int32, lo: int32, hi: int32) -> int32 {
    if (x < lo) { return lo; }
    if (x > hi) { return hi; }
    return x;
}

// The old name lives on as a forwarder, so code written against it still
// builds while it migrates; `@deprecated` is what nudges it along, one
// warning per call site. Only calls TO a deprecated function warn: the
// forwarder's own call to `clamp` is silent.
@deprecated("use clamp instead")
fn clamp_int(x: int32, lo: int32, hi: int32) -> int32 {
    return clamp(x, lo, hi);
}

// A codebase mid-migration, modeled with a compile-time switch: `-DOLD_API`
// selects the branch still on the old name. Both branches compute the same
// thing, so the program's output never changes; only the warnings do.
@if (OLD_API) {
    fn saturate(x: int32) -> int32 {
        // A direct call warns at its own line...
        let v = clamp_int(x, 0, 255);
        // ...and so does binding the function as a value, warned at the
        // point the value is formed, since a later indirect call through
        // `f` cannot be attributed back to `clamp_int`.
        let f: fn(int32, int32, int32) -> int32 = clamp_int;
        return f(v, 0, 100);
    }
} @else {
    fn saturate(x: int32) -> int32 {
        let v = clamp(x, 0, 255);
        let f: fn(int32, int32, int32) -> int32 = clamp;
        return f(v, 0, 100);
    }
}

fn main() -> int32 {
    println("saturate(300) = %d", saturate(300));
    println("saturate(-7)  = %d", saturate(-7));
    return 0;
}

// The standard library uses this for its four renamed memory forwarders:
// copy_bytes, copy_items, set_bytes, and set_items each warn with their
// replacement (bytecopy, copy, bytefill, fill). Repeats of one call site are
// folded at print time (one warning per file/line/message, so a call inside
// a generic body reports once across instantiations); docs/language.md
// "Deprecated functions" has the full rules, including the `.mci` interface
// round-trip for shipped libraries.

// See also: removed.mc (the lifecycle's terminal step: the `@removed`
// tombstone that turns these warnings into hard errors once the
// implementation is pulled), warnings.mc (the warning channel and `-Werror`),
// functions/function_pointers.mc (fn values).
