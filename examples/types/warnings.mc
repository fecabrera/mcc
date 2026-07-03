import "std";

// `@warning("message")` is the non-fatal member of the error directive family:
// where a reached `@error` fails the compile, a reached `@warning` is collected
// and printed to stderr once generation succeeds, and the build carries on (the
// executable, object, or IR is still produced; under `--run` warnings print
// before the program executes). Like its fatal siblings it lives at the top
// level, and it earns its keep guarded by a compile-time `@if`: flagging a
// suspect build configuration without rejecting it.
//
// Prerequisites: types/static_assert.mc (`@static_assert` / `@error` and the
// dead-branch `@if` guard), control-flow/conditional.mc (`@if`). New here is
// the `-D` flag, which defines a name for `@if` conditions: `-DNAME` reads as
// 1 (or `-DNAME=VALUE`), and a name never defined reads as 0. So on a plain
// build every branch below is dead and this file compiles warning-clean, which
// matters because CI builds every example with `-Werror`.
//
// Fire the warnings by hand from the repo root:
//
//   pipenv run python -m mcc examples/types/warnings.mc --run -DFAST_MATH
//     examples/types/warnings.mc: warning: line N: FAST_MATH: float results may differ across targets
//     (then the program runs normally)
//
//   pipenv run python -m mcc examples/types/warnings.mc --run -DFAST_MATH -DDEBUG
//     (both warnings print, in source order)
//
//   pipenv run python -m mcc examples/types/warnings.mc -DFAST_MATH -Werror -o /tmp/warn
//     examples/types/warnings.mc: error: line N: FAST_MATH: float results may differ across targets [-Werror]
//     (exit status 1, no executable written)

// A build knob: -DFAST_MATH opts into faster, less reproducible float math.
// The configuration is legal, just worth flagging on every build that uses it,
// so a warning fits where an `@error` would be wrong.
@if (FAST_MATH) {
    const MATH_MODE = "fast";
    @warning("FAST_MATH: float results may differ across targets");
} @else {
    const MATH_MODE = "strict";
}

// Directives fire in source order, and the warning channel collects every one
// it reaches before printing, so a second live `@warning` adds a second line
// rather than replacing the first. `-Werror` promotes the whole batch: each
// collected warning still prints, rendered as an error line with a trailing
// [-Werror] marker, and the build then fails with no outputs written.
@if (FAST_MATH and DEBUG) {
    @warning("FAST_MATH under DEBUG: failures will not reproduce in release");
}

fn main() -> int32 {
    // On a plain build both branches above were dead: nothing was collected,
    // nothing printed, and MATH_MODE folded to "strict".
    println("float math mode: %s", MATH_MODE);
    return 0;
}

// See also: static_assert.mc (`@static_assert` and `@error`, the fatal
// directives), deprecated.mc (`@deprecated` function warnings on this same
// channel), control-flow/conditional.mc (`@if` compile-time selection).
