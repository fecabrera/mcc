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
//     IS `fn point<float64>::m`. CALLS may stay bare either way; `pt::m(...)`
//     chases the name and calls the `point::m` family.
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
fn point<T>::magnitude(const self: point<T>) -> float64 {
    println("  [generic]   point<T>::magnitude");
    return sqrt(pow(self.x as float64, 2.0) + pow(self.y as float64, 2.0));
}

// Declared THROUGH the alias: this is exactly `fn point<float64>::magnitude`,
// a specialization outranking the generic for a point<float64> receiver.
// Writing the same signature under both spellings would be an ordinary
// duplicate-definition error -- they are one name.
fn pointf::magnitude(const self: pointf) -> float64 {
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

fn pair<A, B>::pick(const self: pair<A, B>) -> A {
    println("  [generic]   pair<A, B>::pick");
    return self.a;
}

// The written arguments substitute through the target: `swap<int32, U>` is
// `pair<U, int32>`, so this IS the partial specialization
// `fn pair<U, int32>::pick` -- it matches any pair whose SECOND position is
// int32 and beats the generic for those receivers.
fn swap<int32, U>::pick(const self: swap<int32, U>) -> U {
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

fn diag<U>::same(const self: diag<U>) -> U {
    println("  [diagonal]  diag<U>::same matches only pair<X, X>");
    return self.a + self.b;
}

// Beside a generic sibling, a receiver that DISAGREES on the diagonal falls
// through to the generic like any filtered overload. (An AGREEING receiver
// would be the standard ambiguity error here -- repeated names score no extra
// pattern specificity over the open `fn pair<A, B>::trace`, so main only
// calls trace with a mismatched receiver. See "Methods on type aliases and
// builtin types" in docs/language.md.)
fn pair<A, B>::trace(const self: pair<A, B>) {
    println("  [generic]   pair<A, B>::trace, diagonal filtered out");
}

fn diag<U>::trace(const self: diag<U>) {
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
    // EITHER call spelling: sqrt(2.25 + 4) = 2.50 both times.
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
    println("  m1 = ({}, {}), m2 = ({}, {})", m1.x, m1.y, m2.x, m2.y);

    // float64 arguments select the specialization the fully-defaulted alias
    // declared; the canonical and alias spellings call the same body.
    println("point::mk / ptd::mk, float64:");
    let m3 = point::mk(0.5, 1.5);
    let m4 = ptd::mk(2.5, 3.5);
    println(f"  m3 = ({m3.x:.2f}, {m3.y:.2f}), m4 = ({m4.x:.2f}, {m4.y:.2f})");

    // The second position is int32, so the alias-declared partial wins and
    // returns the first field with U = float64.
    let sw: pair<float64, int32> = { a = 2.5, b = 7 };
    println("pair<float64, int32> pick:");
    println(f"  a = {pair::pick(sw):.2f}");             // 2.50, via [via alias]

    // Second position float64: the partial is filtered out, the generic runs.
    let ge: pair<int32, float64> = { a = 7, b = 2.5 };
    println("pair<int32, float64> pick:");
    println("  a = {}", pair::pick(ge));                // 7, via [generic]

    // The diagonal: a pair<int32, int32> receiver binds U = int32 through the
    // alias spelling in the signature. The bare `diag::` spelling is fine at
    // a CALL: call qualifiers chase by name; only declarations must annotate.
    let dd: pair<int32, int32> = { a = 21, b = 21 };
    println("pair<int32, int32> same:");
    println("  a + b = {}", pair::same(dd));            // 42, via [diagonal]
    println("pair<int32, int32> same, alias call spelling:");
    println("  a + b = {}", diag::same(dd));            // same body again

    // ge disagrees on the diagonal, so trace falls through to the generic.
    println("pair<int32, float64> trace:");
    pair::trace(ge);                                    // [generic]

    // Builtin qualifiers: the alias and canonical spellings call one clamp.
    println("int32::clamp / myint::clamp:");
    println("  int32::clamp(15, 0, 10) = {}", int32::clamp(15, 0, 10));   // 10
    println("  myint::clamp(-3, 0, 10) = {}", myint::clamp(-3, 0, 10));   // 0

    // A generic builtin qualifier, T inferred from the borrowed slice.
    let arr: int32[4] = [11, 22, 33, 44];
    println("slice<int32> first:");
    println("  first = {}", slice::first(arr as slice<int32>));           // 11

    return 0;
}

// See also: method_specialization.mc for the full-specialization form a plain
// alias qualifier spells; method_partial_specialization.mc for the partial
// form a generic alias substitutes into; type_aliases.mc / generic_alias.mc
// for alias transparency itself; memory/slices.mc for the `as slice<T>`
// borrow feeding slice::first; systems/char_methods.mc for the stdlib
// module (std/char) built on the builtin-qualifier form shown here.
