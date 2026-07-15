import "std/io";

// A function type can spell the write-through reference convention:
// `fn(&int32)` types a function that writes through to the caller's own
// storage. A function with a reference parameter is therefore a legal
// function value, and a call through the value passes by hidden reference --
// and enforces the same call-site rules -- as a direct call would. A by-value
// `const` parameter, by contrast, is NOT part of the type: since Phase B of
// the `&`-reference redesign `const` erases from a function type entirely
// (`fn(const T)` IS `fn(T)`, for a struct T as much as a scalar), so this file
// closes on one comparator alias inhabited by plain by-value functions.
// Prerequisites: function_pointers.mc, reference_params.mc, const_params.mc; the
// comparator alias below uses a generic type alias (types/generic_alias.mc).

// Two in-place transforms: each writes the caller's variable through the
// hidden reference, exactly as in reference_params.mc.
fn double_it(n: &int32) { n *= 2; }
fn negate(n: &int32)    { n = -n; }

// A @static dispatch table over the reference-taking type. A function name still
// folds to a constant address, so the constant-initializer path takes both.
@static let transforms: (fn(&int32))[] = [double_it, negate];

// The headline use of const in a function type: one comparator alias for
// every element type. `const` is not part of a function type on ANY kind of
// T -- it simply drops at type formation, fn(const int32) IS fn(int32) and
// fn(const point) IS fn(point) (the Phase B rule; only `const &T` stays a
// distinct convention). So the same alias is inhabited at both scalar and
// struct T, by an ordinary by-value comparator of the matching element type.
type cmp<T> = fn(const T, const T) -> bool;

// pick returns whichever of a/b the comparator prefers, at any T.
fn pick<T>(a: T, b: T, better: cmp<T>) -> T {
    if (better(a, b)) { return a; }
    return b;
}

// Inhabits cmp<int32>: plain by-value parameters, the const erased.
fn less(a: int32, b: int32) -> bool { return a < b; }

// Inhabits cmp<struct point>: const struct parameters. Since Phase B a
// by-value `const T` erases from the function type just like a scalar's does
// (`cmp<point>` is `fn(point, point)`), so a plain const-struct comparator
// inhabits the alias -- no hidden reference required.
struct point { x: int32; y: int32; }

fn closer(const a: struct point, const b: struct point) -> bool {
    return a.x * a.x + a.y * a.y < b.x * b.x + b.y * b.y;
}

fn main() -> int32 {
    // The bare name of a reference-taking function is a value, and `let` infers
    // the carrying type: f is fn(&int32) -> void.
    let f = double_it;
    let n: int32 = 21;

    // The call passes &n underneath; the callee's write lands in n.
    f(n);
    println(f"f(n)        -> {n}");

    // The same call-site rules as a direct call: the argument must be the
    // caller's own writable lvalue of exactly the parameter's type. A
    // literal is rejected just as double_it(7) would be:
    //     f(7);
    // error: argument 1 of 'f' is not assignable; a reference parameter needs a
    // variable, field, element, or dereference

    // A proven-non-null pointer decays into the reference slot through the value
    // too, exactly as at a direct call (pointer_decay.mc): &n always proves.
    let p: int32* = &n;
    f(p);
    println(f"f(p)        -> {n}");

    // The @static table dispatches in place, writing n each time.
    transforms[0](n);
    transforms[1](n);
    println(f"table       -> {n}");

    // The convention is NOT convertible -- in either direction, with no
    // `as` hatch (unlike the @nonnull contract's strip in
    // nonnull_callbacks.mc). fn(&int32) and fn(int32) receive their
    // argument differently at the machine level, so no call sequence
    // through the wrong type could be correct:
    //     let g: fn(int32) = double_it;
    // error: let g: expected fn(int32) -> void, got fn(&int32) -> void
    // (a reference parameter is passed by hidden reference, a different calling
    // convention; the types are not convertible)
    //     let h = double_it as fn(int32);
    // error: cannot cast fn(&int32) -> void to fn(int32) -> void: a
    // reference parameter is passed by hidden reference, a different calling
    // convention; the types are not convertible

    // An `as` between fn types whose reference/const shape matches stays open: a
    // same-shape signature reinterpret, like any fn-to-fn cast.
    let g = double_it as fn(&uint32);
    let u: uint32 = 5;
    g(u);
    println(f"reinterpret -> {u}");

    // cmp<int32>: the const erases, the plain `less` inhabits the alias.
    println(f"pick(3, 9, less)   -> {pick(3, 9, less)}");

    // cmp<struct point>: the same alias is fn(point, point) here too (const
    // erased), inhabited by the plain by-value const-struct comparator.
    let a = point { x = 3, y = 4 };
    let b = point { x = 6, y = 8 };
    let best = pick(a, b, closer);
    println(f"pick(a, b, closer) -> ({best.x}, {best.y})");

    // A collecting function (args...) is a value too: its type spells the
    // sugar's underlying parameter, fn(const slice<const any>) -> R, and a
    // call through the value takes that trailing slice explicitly --
    // collection is a direct-call affordance (native_variadics.mc). The
    // return convention rides in the type the same way: fn(...) -> &T
    // types a reference-returning function, and a call through the value is the
    // same lvalue a direct call is (reference_return_callbacks.mc).
    return 0;
}

// See also: reference_params.mc and const_params.mc for the conventions these
// types spell; function_pointers.mc for plain fn(...) values, tables, and
// callbacks; nonnull_callbacks.mc for the contract-carrying sibling, where
// the annotation converts contravariantly and `as` strips it (a convention
// does neither); pointer_decay.mc for the decay rule the indirect call
// reuses; reference_return_callbacks.mc for the return-side convention,
// fn(...) -> &T values written through; types/generic_alias.mc for
// generic aliases like cmp<T>.
