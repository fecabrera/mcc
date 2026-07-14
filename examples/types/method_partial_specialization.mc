import "std/io";

// Partial specialization: a method namespaced to a generic struct may MIX
// concrete types and fresh type parameters before the `::`, as in
// `fn pair<int32, U>::describe`. The concrete positions bind, the fresh names
// stay free, and the method becomes a template matching only receivers that
// agree on the concrete positions. This lifts the all-or-nothing rule from
// method_specialization.mc: full, partial, and fully generic bodies now
// coexist in one overload set.
//
// Dispatch is still the EXISTING overload ranking, no new machinery:
//   - A full specialization is a concrete overload, the top tier.
//   - A partial and the fully generic method share the open-template tier,
//     where the partial's concrete positions score higher pattern
//     specificity than bare parameter names.
//   - A partial whose concrete positions disagree with the receiver is
//     filtered out like any non-viable overload: the call simply falls
//     through to the generic.
//
// Prerequisites: method_specialization.mc for the full-specialization form
// and the concrete-beats-generic ranking, generic_methods.mc for methods on
// a generic struct, and functions/mixed_overloads.mc for the
// (is-concrete, specificity) rank that does the work here.

struct pair<A, B> {
    a: A;
    b: B;
}

// The fully GENERIC describe: matches any pair whatsoever. Each body prints a
// marker so the runtime output proves which one dispatch picked.
fn pair<T, U>::describe(const self: pair<T, U>) {
    println("  [generic] pair<T, U>::describe");
}

// The PARTIAL: the first position is pinned to int32, the second stays free.
// It matches every pair<int32, X>, and beats the generic for those receivers
// on pattern specificity (a concrete position outscores a bare name).
fn pair<int32, U>::describe(const self: pair<int32, U>) {
    println(f"  [partial] pair<int32, U>::describe, a = {self.a}");
}

// The FULL specialization: all positions concrete, so it is an ordinary
// concrete overload and outranks both templates for this exact receiver.
fn pair<int32, int8>::describe(const self: pair<int32, int8>) {
    println("  [full]    pair<int32, int8>::describe, a = {}, b = {}".format(
            self.a, self.b as int32));
}

// Fresh names are REAL type parameters: they are inferred at the call and
// prepend the method's own `<...>` list, so a partial may add method type
// parameters too. Here `U` comes from the receiver and `W` from `label`.
fn pair<int32, U>::tag<W>(const self: pair<int32, U>, label: W) -> W {
    println(f"  [partial] pair<int32, U>::tag<W>, b = {self.b}");
    return label;
}

// A fresh position may be BOUNDED, exactly as in an ordinary declaration
// list: this partial only matches a pair<int32, X> whose X is in the closed
// group (see type_groups.mc). Any other second argument fails the group
// filter and the call falls through to the generic width below. Bounding
// also interacts with the ranking tiers -- a bounded generic method would
// beat an UNBOUNDED partial -- see `Partial specialization` in
// docs/language.md for that rule.
fn pair<T, U>::width(const self: pair<T, U>) -> int32 {
    println("  [generic] pair<T, U>::width");
    return -1;
}

fn pair<int32, U: int8 | int16>::width(const self: pair<int32, U>) -> int32 {
    println("  [bounded partial] pair<int32, U: int8 | int16>::width");
    return sizeof(U) as int32;
}

fn main() -> int32 {
    // All three describe bodies are viable names for these calls; the
    // receiver's type arguments decide which one wins.
    let full: pair<int32, int8> = { a = 1, b = 2 };
    println("pair<int32, int8>:");
    pair::describe(full);                        // [full]: top tier, concrete

    let part: pair<int32, int64> = { a = 3, b = 4 };
    println("pair<int32, int64>:");
    pair::describe(part);                        // [partial]: int32 position agrees

    // The first position is int64, so the partial and the full specialization
    // are both filtered out: only the generic matches.
    let gen: pair<int64, float64> = { a = 5, b = 6.5 };
    println("pair<int64, float64>:");
    pair::describe(gen);                         // [generic]: nothing else viable

    // `tag` binds U = int64 from the receiver and W = float64 from the label,
    // both inferred at the call.
    println("pair<int32, int64> tag:");
    println(f"  tagged = {pair::tag(part, 9.5):.2f}");  // 9.50, via [partial]

    // The bounded partial admits int8 (in the group) but not int64, which
    // falls through to the generic width.
    println("pair<int32, int8> width:");
    println(f"  width = {pair::width(full)}");  // 1, via [bounded partial]
    println("pair<int32, int64> width:");
    println(f"  width = {pair::width(part)}");  // -1, via [generic]: int64 fails the group
    return 0;
}

// See also: method_specialization.mc for full specialization and the ranking
// this extends; generic_methods.mc for the `fn point<T>::name` form and
// method type parameters; functions/mixed_overloads.mc for the
// (is-concrete, specificity) rank; type_groups.mc for the closed group
// bounding the `width` partial; method_alias.mc for a generic `type` alias
// substituting its written arguments into this partial form
// (`fn swap<int32, U>::pick` with `type swap<X, Y> = pair<Y, X>`).
