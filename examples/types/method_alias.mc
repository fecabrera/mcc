import "std/io";
import "libc/math";

// Methods on type aliases and builtin types: methods register to a TYPE, and
// a `type` alias is just an alias (type_aliases.mc), so declaring or calling
// a method through an alias qualifier IS declaring or calling it for the type
// the alias names. The qualifier is chased through the alias to the canonical
// name, and both spellings register to, and call, ONE method family. There is
// no new dispatch machinery; what an alias contributes is decided by its shape:
//   - a PLAIN alias contributes its target's type arguments: with
//     `type pointf = point<float64>`, `fn pointf::m` IS the specialization
//     `fn point<float64>::m` (method_specialization.mc). Writing type
//     arguments on a plain alias (`fn pointf<float64>::m`) is the error
//     "type alias 'pointf' is not generic".
//   - a GENERIC alias applied to written arguments substitutes them through
//     its target: with `type swap<X, Y> = pair<Y, X>`, `fn swap<int32, U>::m`
//     IS the partial specialization `fn pair<U, int32>::m`
//     (method_partial_specialization.mc).
//   - a BARE generic-alias qualifier cannot DECLARE: like a bare generic
//     struct, `fn pt::m` with `type pt<T> = point<T>` is the error "type
//     alias 'pt' is generic; the method qualifier must annotate its type
//     parameter(s), e.g. 'fn pt<T>::m' or 'fn pt<float64>::m'". The one
//     exception is a FULLY-DEFAULTED alias, whose bare name is already a
//     complete type use: with `type pt<T = float64> = point<T>`, `fn pt::m`
//     IS `fn point<float64>::m`. CALLS may stay bare either way, but what a
//     bare call MEANS follows the same completeness line: an alias that is
//     NOT a complete type (`pt::m(...)`) chases the name and infers from
//     the arguments, while a COMPLETE alias (plain, or fully-defaulted)
//     INJECTS the instantiation it names -- `pointf::m(p)` is exactly
//     `point<float64>::m(p)`, pinning the receiver (docs/language.md
//     "Explicit type arguments at a qualified call").
//   - BUILTIN types are qualifiers too: `fn int32::clamp`, or with fresh type
//     parameters `fn slice<T>::first`. A builtin cannot be SPECIALIZED,
//     though; `fn slice<int32>::first` is the error "cannot specialize
//     builtin type 'slice'; spell the receiver type in the method's
//     signature instead".
//
// Prerequisites: type_aliases.mc and generic_alias.mc for alias transparency,
// method_specialization.mc and method_partial_specialization.mc for the
// specialization forms and the overload ranking every call here reuses.

struct point<T> {
    x: T;
    y: T;
}

// ---- A plain alias qualifier is a specialization spelling ----

type pointf = point<float64>;

// The generic magnitude, for any element type. Each body prints a marker so
// the runtime output proves which one dispatch picked.
fn point<T>::magnitude(const self: &point<T>) -> float64 {
    println("  [generic]   point<T>::magnitude");
    return sqrt(pow(self.x as float64, 2.0) + pow(self.y as float64, 2.0));
}

// Declared THROUGH the alias: this is exactly `fn point<float64>::magnitude`,
// a specialization outranking the generic for a point<float64> receiver.
// Writing the same signature under both spellings would be an ordinary
// duplicate-definition error -- they are one name.
fn pointf::magnitude(const self: &pointf) -> float64 {
    println("  [via alias] pointf::magnitude IS point<float64>::magnitude");
    return sqrt(pow(self.x, 2.0) + pow(self.y, 2.0));
}

// ---- A generic-alias qualifier must annotate; defaults are the exception ----

type pt<T> = point<T>;

// A DECLARATION may not name a generic alias bare: `fn pt::mk` is the error
// "type alias 'pt' is generic; the method qualifier must annotate its type
// parameter(s), e.g. 'fn pt<T>::mk' or 'fn pt<float64>::mk'", the same rule
// as a bare `fn point::mk`. Annotated, the written arguments substitute
// through: this IS `fn point<T>::mk`, one generic family under the canonical
// name.
fn pt<T>::mk(x: T, y: T) -> point<T> {
    println("  [generic]   pt<T>::mk IS point<T>::mk");
    return { x = x, y = y };
}

// The one bare-generic qualifier that still declares: a FULLY-DEFAULTED
// alias. Its bare name is already a complete type use (the defaults fill,
// exactly as in a bare type use), so `fn ptd::mk` IS the specialization
// `fn point<float64>::mk`, outranking the generic above for float64
// arguments.
type ptd<T = float64> = point<T>;

fn ptd::mk(x: float64, y: float64) -> point<float64> {
    println("  [defaulted] ptd::mk IS point<float64>::mk");
    return { x = x, y = y };
}

// ---- A generic alias with written arguments substitutes through ----

struct pair<A, B> {
    a: A;
    b: B;
}

type swap<X, Y> = pair<Y, X>;

fn pair<A, B>::pick(const self: &pair<A, B>) -> A {
    println("  [generic]   pair<A, B>::pick");
    return self.a;
}

// The written arguments substitute through the target: `swap<int32, U>` is
// `pair<U, int32>`, so this IS the partial specialization
// `fn pair<U, int32>::pick` -- it matches any pair whose SECOND position is
// int32 and beats the generic for those receivers.
fn swap<int32, U>::pick(const self: &swap<int32, U>) -> U {
    println("  [via alias] swap<int32, U>::pick IS pair<U, int32>::pick");
    return self.a;
}

// A DUPLICATE-POSITION alias is a diagonal constraint: `diag<U>` declares one
// parameter that must unify consistently, so this method matches only
// pair<X, X> receivers. The alias spelling in the signature is transparent to
// inference: `self: diag<U>` binds U straight from a pair<int32, int32>
// argument. With no generic sibling, a pair<int32, float64> receiver is
// simply rejected at the call.
type diag<T> = pair<T, T>;

fn diag<U>::same(const self: &diag<U>) -> U {
    println("  [diagonal]  diag<U>::same matches only pair<X, X>");
    return self.a + self.b;
}

// Beside a generic sibling, a receiver that DISAGREES on the diagonal falls
// through to the generic like any filtered overload. An AGREEING receiver
// picks the diagonal: the alias expands to pair<U, U>, whose repeated name is
// strictly more specialized than the open `fn pair<A, B>::trace` under the
// subsumption tie-break (this exact pair used to be the standard ambiguity
// error; see functions/overload_subsumption.mc and "Rank-tied templates:
// subsumption" in docs/language.md).
fn pair<A, B>::trace(const self: &pair<A, B>) {
    println("  [generic]   pair<A, B>::trace, diagonal filtered out");
}

fn diag<U>::trace(const self: &diag<U>) {
    println("  [diagonal]  diag<U>::trace");
}

// ---- Builtin types are qualifiers too ----

type myint = int32;

// Declared through the alias, callable as `myint::clamp` AND `int32::clamp`:
// one family, two spellings of its name.
fn myint::clamp(x: myint, lo: myint, hi: myint) -> myint {
    println("  [one body]  myint::clamp registers as int32::clamp");
    if (x < lo) { return lo; }
    if (x > hi) { return hi; }
    return x;
}

// Fresh names before the `::` still declare type parameters, so a generic
// builtin like slice works as a qualifier as well.
fn slice<T>::first(s: slice<T>) -> T {
    println("  [one body]  slice<T>::first");
    return s[0];
}

fn main() -> int32 {
    // point<int64> has no specialization: the generic runs, sqrt(9+16) = 5.00.
    let pi: point<int64> = { x = 3, y = 4 };
    println("point<int64> magnitude:");
    println(f"  |pi| = {point::magnitude(pi):.2f}");    // 5.00, via [generic]

    // A pointf receiver runs the alias-declared specialization, through
    // EITHER call spelling: sqrt(2.25 + 4) = 2.50 both times. The alias
    // spelling is more than a rename, though: pointf is a COMPLETE type, so
    // the qualifier injects the instantiation it names and PINS the
    // receiver as point<float64>. Handing it `pi` from above would be the
    // error "argument 1 of 'point::magnitude': expected point<float64>,
    // got point<int64>" -- the bare `point::` spelling, pure namespace plus
    // inference, is the one that would route pi to the generic.
    let pf: pointf = { x = 1.5, y = 2.0 };
    println("pointf magnitude, canonical call spelling:");
    println(f"  |pf| = {point::magnitude(pf):.2f}");    // 2.50, via [via alias]
    println("pointf magnitude, alias call spelling:");
    println(f"  |pf| = {pointf::magnitude(pf):.2f}");   // same body again

    // Declarations had to annotate, calls do not: both bare call spellings
    // chase the name to the one mk family, T inferred from the arguments.
    println("pt::mk / point::mk:");
    let m1 = pt::mk(1, 2);
    let m2 = point::mk(3, 4);
    println(f"  m1 = ({m1.x}, {m1.y}), m2 = ({m2.x}, {m2.y})");

    // float64 arguments select the specialization the fully-defaulted alias
    // declared; the canonical and alias spellings call the same body. (ptd
    // is complete -- its defaults fill -- so the bare `ptd::` qualifier
    // also pins point<float64>, exactly like `pointf::` above.)
    println("point::mk / ptd::mk, float64:");
    let m3 = point::mk(0.5, 1.5);
    let m4 = ptd::mk(2.5, 3.5);
    println(f"  m3 = ({m3.x:.2f}, {m3.y:.2f}), m4 = ({m4.x:.2f}, {m4.y:.2f})");

    // The second position is int32, so the alias-declared partial wins and
    // returns the first field with U = float64.
    let sw: pair<float64, int32> = { a = 2.5, b = 7 };
    println("pair<float64, int32> pick:");
    println(f"  a = {pair::pick(sw):.2f}");             // 2.50, via [via alias]

    // A CALL qualifier may spell the alias with written type arguments too:
    // full type resolution substitutes them through the target, permutation
    // included, so `swap<int32, float64>` pins pair<float64, int32> -- sw's
    // exact type -- and dispatch picks the partial again. (Name-only chasing
    // could not do this; the pin is what makes the permuted spelling mean
    // the right instantiation.)
    println("swap<int32, float64> pick, written-args alias call:");
    println(f"  a = {swap<int32, float64>::pick(sw):.2f}");     // 2.50 again

    // Second position float64: the partial is filtered out, the generic runs.
    let ge: pair<int32, float64> = { a = 7, b = 2.5 };
    println("pair<int32, float64> pick:");
    println(f"  a = {pair::pick(ge)}");                // 7, via [generic]

    // The diagonal: a pair<int32, int32> receiver binds U = int32 through the
    // alias spelling in the signature. The bare `diag::` spelling is fine at
    // a CALL: diag is generic, NOT a complete type, so the qualifier chases
    // by name and infers from the argument (nothing is pinned); only
    // declarations must annotate.
    let dd: pair<int32, int32> = { a = 21, b = 21 };
    println("pair<int32, int32> same:");
    println(f"  a + b = {pair::same(dd)}");            // 42, via [diagonal]
    println("pair<int32, int32> same, alias call spelling:");
    println(f"  a + b = {diag::same(dd)}");            // same body again

    // ge disagrees on the diagonal, so trace falls through to the generic.
    println("pair<int32, float64> trace:");
    pair::trace(ge);                                    // [generic]

    // dd agrees, and the alias-spelled diagonal wins the subsumption
    // tie-break against the open sibling (functions/overload_subsumption.mc).
    println("pair<int32, int32> trace:");
    pair::trace(dd);                                    // [diagonal]

    // Builtin qualifiers: the alias and canonical spellings call one clamp.
    println("int32::clamp / myint::clamp:");
    println(f"  int32::clamp(15, 0, 10) = {int32::clamp(15, 0, 10)}");   // 10
    println(f"  myint::clamp(-3, 0, 10) = {myint::clamp(-3, 0, 10)}");   // 0

    // A generic builtin qualifier, T inferred from the borrowed slice -- or
    // pinned: builtin families take a written instantiation at a CALL just
    // like user structs (only the DECLARATION-side specialization ban from
    // the header stands).
    let arr: int32[4] = [11, 22, 33, 44];
    println("slice<int32> first:");
    println(f"  first = {slice::first(arr as slice<int32>)}");           // 11
    println(f"  pinned = {slice<int32>::first(arr as slice<int32>)}");   // 11

    return 0;
}

// See also: method_specialization.mc for the full-specialization form a plain
// alias qualifier spells; method_partial_specialization.mc for the partial
// form a generic alias substitutes into; functions/overload_subsumption.mc
// for the subsumption tie-break the diagonal trace call rides on;
// type_aliases.mc / generic_alias.mc
// for alias transparency itself; constructors.mc for the `S(args)` sugar,
// whose alias and builtin heads ride this same qualifier chase;
// method_calls.mc for the `.method()` sugar, whose alias and builtin
// RECEIVERS ('c'.upper(), xs.first()) dispatch these same families;
// memory/slices.mc for the `as slice<T>`
// borrow feeding slice::first; systems/char_methods.mc for the stdlib
// module (std/char) built on the builtin-qualifier form shown here;
// generic_methods.mc and docs/language.md "Explicit type arguments at a
// qualified call" for the pinned call spelling behind the complete-alias
// injection and the written-args `swap<int32, float64>::pick` call.
