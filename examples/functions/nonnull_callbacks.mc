import "std/io";
import "libc/string"; // strlen: an @extern carrying libc's @nonnull contract

// A function type can spell the @nonnull contract (see nonnull.mc) per
// parameter: `fn(@nonnull int32*) -> int32` is the type of a function whose
// argument must be provably non-null. The contract rides the value, so a
// @nonnull function is a legal callback, and a call through the value runs
// the same call-site null proof as a direct call -- indirection loses none
// of the checking.
// Prerequisites: nonnull.mc, nonnull_assert.mc, function_pointers.mc.

fn first(@nonnull p: int32*) -> int32 {
    return *p; // no null check: every route here, direct or indirect, proved p
}

// A null-tolerant twin with the plain, unannotated type.
fn fallback(p: int32*) -> int32 {
    if (p == null) { return -1; }
    return *p;
}

// An annotated callback parameter. Inside, the @nonnull q is itself proof
// (exactly as at a direct call), so cb(q) re-lends with no re-check.
fn apply(cb: fn(@nonnull int32*) -> int32, @nonnull q: int32*) -> int32 {
    return cb(q);
}

// A @static dispatch table over the annotated type. A function name still
// folds to a constant address, and the contravariant lift (below) lets the
// plain `fallback` sit beside `first`: the constant-initializer path takes
// both.
@static let handlers: (fn(@nonnull int32*) -> int32)[] = [first, fallback];

fn main() -> int32 {
    let x: int32 = 40;

    // The bare name of a @nonnull function is a value, and `let` infers the
    // annotated type: f is fn(@nonnull int32*) -> int32.
    let f = first;

    // A call through f runs the same proof as first(&x): &x always proves.
    // An unproven pointer is the same compile error a direct call gives, and
    // the same cures apply -- a null-check guard narrows it
    // (nonnull_narrowing.mc), and the postfix hatch asserts it, `f(q!)`
    // (nonnull_assert.mc).
    println("f(&x) = {}", f(&x));

    // Assignability along the contract is contravariant. A plain function
    // lifts into an annotated slot: the annotation only adds a call-site
    // obligation, which a function that tolerates null meets trivially.
    f = fallback;
    println("f(&x) = {}", f(&x)); // the call still proves: prints 40

    // The reverse would let calls skip the proof, so it is rejected:
    //     let g: fn(int32*) -> int32 = first;
    // error: let g: expected fn(int32*) -> int32, got
    // fn(@nonnull int32*) -> int32 (a @nonnull contract cannot be dropped: a
    // call through the plain type would skip the call-site null proof; cast
    // with 'as fn(int32*) -> int32' to strip it explicitly, making a null
    // argument undefined behavior)

    // The hint names the hatch: `as` strips the contract as a free bitcast.
    // Calls through the result skip the proof entirely, so an actually-null
    // argument would be undefined behavior, exactly like `p!` -- which is why
    // k only ever sees the provably-good &x here.
    let k = first as fn(int32*) -> int32;
    println("k(&x) = {}", k(&x));

    // The lift covers every annotated slot, argument position included, so
    // apply() takes the checked and the tolerant callback alike...
    println("apply(first)    = {}", apply(first, &x));
    println("apply(fallback) = {}", apply(fallback, &x));

    // ...and the mixed @static table dispatches, proving at the indexed call.
    println("handlers[0](&x) = {}", handlers[0](&x));
    println("handlers[1](&x) = {}", handlers[1](&x));

    // The extern asymmetry: strlen's binding declares @nonnull, and while a
    // *direct* extern call is graded by the -Wextern-nonnull posture (see
    // systems/extern_nonnull.mc), a value of it carries the contract and
    // checks strictly. An indirect call can no longer be attributed to an
    // extern declaration, so there is no posture to grade it by.
    let n = strlen; // inferred: fn(@nonnull char*) -> uint64
    println("n(\"callback\") = {}", n("callback")); // a string literal proves
    return 0;
}

// See also: nonnull.mc for the @nonnull parameter contract and its proof
// sources; nonnull_assert.mc for the `p!` hatch this type reuses at indirect
// calls; function_pointers.mc for the plain fn(...) types, callbacks, and
// dispatch tables the contract rides on; systems/extern_nonnull.mc for the
// graded posture that direct extern calls keep.
