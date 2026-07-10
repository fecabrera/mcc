import "std/io";

// A function type can spell the hidden-reference calling conventions:
// `fn(mut int32)` types a function that writes through to the caller's own
// storage, and `fn(const struct point, const struct point) -> bool` one
// whose struct parameters travel by read-only reference. A function with
// mut or const-aggregate parameters is therefore a legal function value,
// and a call through the value passes by reference -- and enforces the same
// call-site rules -- as a direct call would.
// Prerequisites: function_pointers.mc, mut_params.mc, const_params.mc; the
// comparator alias below uses a generic type alias (types/generic_alias.mc).

// Two in-place transforms: each writes the caller's variable through the
// hidden reference, exactly as in mut_params.mc.
fn double_it(mut n: int32) { n *= 2; }
fn negate(mut n: int32)    { n = -n; }

// A @static dispatch table over the mut-taking type. A function name still
// folds to a constant address, so the constant-initializer path takes both.
@static let transforms: (fn(mut int32))[] = [double_it, negate];

// The headline use of const in a function type: one comparator alias for
// every element type. On a by-value scalar, const is not part of the
// convention and simply drops at type formation -- fn(const int32) IS
// fn(int32) -- while on a struct it records the hidden reference. So the
// same alias is inhabited at both kinds of T, by the natural function
// each kind calls for.
type cmp<T> = fn(const T, const T) -> bool;

// pick returns whichever of a/b the comparator prefers, at any T.
fn pick<T>(a: T, b: T, better: cmp<T>) -> T {
    if (better(a, b)) { return a; }
    return b;
}

// Inhabits cmp<int32>: plain by-value parameters, the const erased.
fn less(a: int32, b: int32) -> bool { return a < b; }

// Inhabits cmp<struct point>: const-struct parameters, hidden reference.
struct point { x: int32; y: int32; }

fn closer(const a: struct point, const b: struct point) -> bool {
    return a.x * a.x + a.y * a.y < b.x * b.x + b.y * b.y;
}

fn main() -> int32 {
    // The bare name of a mut-taking function is a value, and `let` infers
    // the carrying type: f is fn(mut int32) -> void.
    let f = double_it;
    let n: int32 = 21;

    // The call passes &n underneath; the callee's write lands in n.
    f(n);
    println("f(n)        -> {}", n);

    // The same call-site rules as a direct call: the argument must be the
    // caller's own writable lvalue of exactly the parameter's type. A
    // literal is rejected just as double_it(7) would be:
    //     f(7);
    // error: argument 1 of 'f' is not assignable; a mut parameter needs a
    // variable, field, element, or dereference

    // A proven-non-null pointer decays into the mut slot through the value
    // too, exactly as at a direct call (pointer_decay.mc): &n always proves.
    let p: int32* = &n;
    f(p);
    println("f(p)        -> {}", n);

    // The @static table dispatches in place, writing n each time.
    transforms[0](n);
    transforms[1](n);
    println("table       -> {}", n);

    // The convention is NOT convertible -- in either direction, with no
    // `as` hatch (unlike the @nonnull contract's strip in
    // nonnull_callbacks.mc). fn(mut int32) and fn(int32) receive their
    // argument differently at the machine level, so no call sequence
    // through the wrong type could be correct:
    //     let g: fn(int32) = double_it;
    // error: let g: expected fn(int32) -> void, got fn(mut int32) -> void
    // (a mut parameter is passed by hidden reference, a different calling
    // convention; the types are not convertible)
    //     let h = double_it as fn(int32);
    // error: cannot cast fn(mut int32) -> void to fn(int32) -> void: a mut
    // parameter is passed by hidden reference, a different calling
    // convention; the types are not convertible

    // An `as` between fn types whose mut/const shape matches stays open: a
    // same-shape signature reinterpret, like any fn-to-fn cast.
    let g = double_it as fn(mut uint32);
    let u: uint32 = 5;
    g(u);
    println("reinterpret -> {}", u);

    // cmp<int32>: the const erases, the plain `less` inhabits the alias.
    println("pick(3, 9, less)   -> {}", pick(3, 9, less));

    // cmp<struct point>: the same alias, now the hidden-reference type,
    // inhabited by the const-parameter comparator -- no copies made.
    let a = point { x = 3, y = 4 };
    let b = point { x = 6, y = 8 };
    let best = pick(a, b, closer);
    println("pick(a, b, closer) -> ({}, {})", best.x, best.y);

    // A collecting function (args...) is a value too: its type spells the
    // sugar's underlying parameter, fn(const slice<const any>) -> R, and a
    // call through the value takes that trailing slice explicitly --
    // collection is a direct-call affordance (native_variadics.mc). The one
    // convention no fn type expresses is a `-> mut T` return: a
    // mut-returning function still cannot become a value (mut_returns.mc).
    return 0;
}

// See also: mut_params.mc and const_params.mc for the conventions these
// types spell; function_pointers.mc for plain fn(...) values, tables, and
// callbacks; nonnull_callbacks.mc for the contract-carrying sibling, where
// the annotation converts contravariantly and `as` strips it (a convention
// does neither); pointer_decay.mc for the decay rule the indirect call
// reuses; types/generic_alias.mc for generic aliases like cmp<T>.
