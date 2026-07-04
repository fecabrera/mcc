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
    // (An `as` cast to a pointer type keeps these proofs, aliases of pointer
    // types included; a non-pointer intermediate like `&x as uint64 as T*`
    // severs the proof. And a `let` bound to a proven source, like
    // `let p: int32* = &x;`, starts proven itself: see nonnull_narrowing.mc.)

    // Everything else is a compile error until proven:
    // first(null);           // error: cannot pass null
    // A heap or returned T* carries no proof either. A null-check `if` guard
    // narrows a plain local into a proof at no cost: see
    // nonnull_narrowing.mc. For pointers with no invariant the compiler can
    // see, the postfix `p!` assertion is the escape hatch: see
    // nonnull_assert.mc.

    println("a = %d, b = %d, head = %c", a, b, c as int32);
    println("sum = %d", a + b + (c as int32 - 64)); // 40 + 1 + 1 = 42
    return 0;
}

// @nonnull is orthogonal to @noalias and the two may sit on one parameter
// (`@noalias @nonnull p: T*`): non-null is checked by the compiler, no-overlap
// is a promise the caller keeps. See also: nonnull_narrowing.mc for the
// null-check guards that narrow a plain T* into a proof; nonnull_assert.mc
// for the postfix `p!` assertion that lets unproven pointers cross;
// noalias.mc; memory/pointers.mc for the pointer basics.
