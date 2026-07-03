import "std";

// `@nonnull` on a pointer parameter is a *checked* "definitely non-null"
// refinement over the nullable-by-default `T*`: the callee is statically
// guaranteed a non-null argument, so it can skip the defensive re-check.
// Unlike `@noalias`, the promise is not the caller's to break -- every call
// site must prove the argument non-null, or the program does not compile.
fn first(@nonnull p: int32*) -> int32 {
    return *p; // no null check needed: the compiler guaranteed it
}

fn head(@nonnull s: uint8*) -> uint8 {
    return *s;
}

// The guarantee travels transitively: a @nonnull parameter is itself proof,
// so forwarding it to another @nonnull slot needs no re-check.
fn outer(@nonnull p: int32*) -> int32 {
    return first(p);
}

fn main() -> int32 {
    let x: int32 = 40;
    let pair: int32[2] = [1, 2];

    // The always-non-null sources cross into a @nonnull slot directly:
    let a = outer(&x);        // &x -- the address of named storage
    let b = first(pair);      // an array decaying to a pointer
    let c = head("A");        // a string literal

    // Everything else is a compile error until proven:
    // first(null);           // error: cannot pass null
    // let p: int32* = &x;
    // first(p);              // error: a plain int32* carries no proof
    // (For heap or returned pointers with no proof to offer, the postfix
    // `p!` assertion is the escape hatch: see nonnull_assert.mc.)

    println("a = %d, b = %d, head = %c", a, b, c as int32);
    println("sum = %d", a + b + (c as int32 - 64)); // 40 + 1 + 1 = 42
    return 0;
}

// @nonnull is orthogonal to @noalias and the two may sit on one parameter
// (`@noalias @nonnull p: T*`): non-null is checked by the compiler, no-overlap
// is a promise the caller keeps. See also: nonnull_assert.mc for the postfix
// `p!` assertion that lets unproven pointers cross; noalias.mc;
// memory/pointers.mc for the pointer basics.
