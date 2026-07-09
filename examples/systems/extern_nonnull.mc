// `@nonnull` (functions/nonnull.mc) on an `@extern` declaration is a promise
// about foreign C code whose body the compiler cannot see, so its call-site
// enforcement is GRADED by three postures over the opt-in `-Wextern-nonnull`
// class, unlike a native `@nonnull` (which keeps a flat hard error at every
// posture and never joins the class). The relaxed default lets a mechanical C
// port build with no flag; a codebase reaches for the stricter postures on
// the C boundary deliberately.
//
//   relaxed (default, no flag): a possibly-null argument is silently accepted
//     (and no LLVM nonnull/dereferenceable hint rides the extern declare).
//   warn (`-Wextern-nonnull`, or `-Wall`): the possibly-null argument warns,
//     tagged `[-Wextern-nonnull]`; still no hint.
//   strict (`-Werror=extern-nonnull`, or global `-Werror` with the class
//     enabled): the possibly-null argument is a hard error, and the LLVM hint
//     IS emitted on the declare (sound again because caller proof is now
//     unconditional).
//
// Two things never grade: the `null` LITERAL into an annotated extern slot is
// ALWAYS a hard error (equally broken C, never porting noise), and a native
// `@nonnull` possibly-null argument is ALWAYS a hard error (its callee body
// holds the parameter as a load-bearing fact).
//
// This file keeps a LIVE possibly-null site (length_of below) on purpose, to
// teach the graded crossing. Because the class is off by default, the plain
// build compiles it clean and `main` runs. CI's main example loop now compiles
// every file at `-Werror -Wextern-nonnull` (the class ENABLED), which would
// promote this live site to a hard error, so CI builds THIS demo in a
// dedicated step at plain `-Werror` (the class off, its documented relaxed
// default), exactly as a warning-class demo cannot build with its own class
// promoted to error. The two other opt-in-class demos,
// types/unchecked_dereference.mc and control-flow/dead_code.mc, still ride the
// main loop clean: only extern-nonnull is enabled there, so their classes stay
// off and their live sites stay silent. This file shares their default-off
// shape but needs the carve-out because its class is the one CI turns on.
//
// Prerequisites: systems/extern.mc (the `@extern` boundary), functions/
// nonnull.mc and nonnull_narrowing.mc (the proof relation and the null-check
// guards), and types/unchecked_dereference.mc for the flag mechanics
// (-W<name>, -Wall, the unknown-class error, `-Werror=<class>` promotion),
// not re-taught here.
//
// Fire the postures by hand from the repo root:
//
//   pipenv run python -m mcc examples/systems/extern_nonnull.mc --run
//     (relaxed: silent, the class is off by default; the program runs)
//
//   pipenv run python -m mcc examples/systems/extern_nonnull.mc --run -Wextern-nonnull
//     examples/systems/extern_nonnull.mc: warning: line N: passing a possibly-null pointer as argument 1 of 'strlen': the parameter is @nonnull on an @extern declaration [-Wextern-nonnull]
//     (warn: one line for the possibly-null site, then the program runs;
//      `-Wall` enables the class and reports the same)
//
//   pipenv run python -m mcc examples/systems/extern_nonnull.mc -Werror=extern-nonnull -o /tmp/en
//     examples/systems/extern_nonnull.mc: error: line N: cannot pass a possibly-null pointer as argument 1 of 'strlen': the parameter is @nonnull (pass &x, a string or array literal, an array, a @nonnull parameter, a pointer narrowed by a null check, or assert with postfix '!')
//     (strict: the possibly-null site is now an error; exit status 1, no
//      executable written. Selective `-Werror=<class>` promotes JUST this one
//      class without a whole-build `-Werror`, and works for any registered
//      class, e.g. `-Werror=unchecked-dereference`. An unknown name is a hard
//      CLI error: mcc: error: unknown warning class 'name'. A global
//      `-Werror -Wextern-nonnull` reaches strict the other way.)

// The foreign declarations. `@nonnull` names the promise the C side already
// relies on: strlen and puts both dereference their argument with no
// null-check of their own. The annotation ships unconditionally in source
// (and in a `.mci` stub); only its enforcement varies per build.
@extern fn strlen(@nonnull s: char*) -> uint64;
@extern fn printf(fmt: char*, ...) -> int32;

// THE POSSIBLY-NULL SITE. `text` is a plain `char*` parameter, so it carries
// no proof; passing it to strlen's @nonnull slot is the graded crossing. At
// relaxed this compiles clean (no diagnostic at all), which is why the file
// is CI-safe; at warn it warns here; at strict it is an error here.
fn length_of(text: char*) -> uint64 {
    return strlen(text);
}

// THE PROVEN SITE. A diverging early null-check narrows `text` to a proof for
// the remainder of the scope (nonnull_narrowing.mc, shape 2), so this
// crossing is clean at EVERY posture, warn and strict included. Any of the
// standard @nonnull proofs would do the same: a string literal, `&x`, an
// array, another @nonnull parameter, or a postfix `p!` assertion.
fn length_checked(text: char*) -> uint64 {
    if (text == null) {
        return 0;
    }
    return strlen(text);        // proven: never a diagnostic
}

fn main() -> int32 {
    // `msg` is seeded from a string literal, so it starts proven; the calls
    // that pass it are fine. The graded site lives inside length_of's body,
    // where the plain `char*` parameter is unproven.
    let msg: char* = "hello, extern";
    printf("length_of      = %llu\n", length_of(msg));
    printf("length_checked = %llu\n", length_checked(msg));

    // A string literal is proven directly, so this crosses at every posture.
    printf("literal        = %llu\n", strlen("world"));

    // The two ungraded rejections, shown as comments because each is a hard
    // error at every posture and would break the build:
    //   strlen(null);            // error: cannot pass null ... the parameter is @nonnull
    //   let p: char* = msg;      // (a plain local, unproven if not narrowed)
    //   native_nonnull(p);       // a NATIVE @nonnull slot: always a hard error,
    //                            //   never the graded extern warning
    return 0;
}

// See also: functions/nonnull.mc for @nonnull itself and the always-non-null
// proof sources, functions/nonnull_narrowing.mc for the null-check guards used
// above; systems/extern.mc for the `@extern` boundary and variadics;
// types/unchecked_dereference.mc and control-flow/dead_code.mc for the two
// other opt-in warning classes riding the same -W<name> / -Werror framework.
