import "std/io";

// Subsumption: the last arbiter for rank-tied generic overloads. Resolution
// runs viability, then rank, then subsumption, then ambiguity. When viable
// templates tie on rank (same tier, same pattern specificity), the tie is no
// longer automatically ambiguous: the member whose parameter pattern is
// strictly an INSTANCE of every other member's, and whose type-parameter
// constraints IMPLY theirs, is the more specialized declaration and wins.
// Only a cohort with no such maximum is still
//     error: call to 'f' is ambiguous between overloads
// Builds on mixed_overloads.mc (the (tier, specificity) rank this arbiter
// runs after and never reorders) and types/generics.mc; the constraint
// section uses closed type groups (types/type_groups.mc, covered later in
// the tour).

// ---- The diagonal beats the open pattern ----

// `same<T>` demands two arguments of ONE type; `same<T, U>` accepts any two.
// Both unbounded, equal specificity: for agreeing arguments this pair used
// to be the ambiguity error. Now (T, T) is an instance of (T, U) -- the open
// pattern's wildcards map onto it consistently (T := T, U := T) -- while the
// reverse fails (the diagonal's single T cannot stand for two independent
// wildcards at once), so the diagonal is strictly more specialized and wins
// whenever it is viable.
fn same<T>(x: T, y: T) -> int32 { return 1; }        // the diagonal
fn same<T, U>(x: T, y: U) -> int32 { return 2; }     // the open pattern

// A longer chain orders the same way: for three agreeing arguments the full
// diagonal is an instance of BOTH looser patterns, the cohort's unique
// maximum. Two agreeing plus one different lands on the partial diagonal,
// which in turn subsumes into the fully open member.
fn triple<T>(x: T, y: T, z: T) -> int32 { return 1; }
fn triple<T, U>(x: T, y: T, z: U) -> int32 { return 2; }
fn triple<T, U, V>(x: T, y: U, z: V) -> int32 { return 3; }

// ---- Viability runs first: a literal keeps only integer deductions ----

struct point<T> {
    x: T;
    y: T;
}

// A diagonal member ties x and y to the element type itself...
fn point<T>::set(mut self: point<T>, x: T, y: T) {
    println("  [diagonal]   x and y are already T");
    self.x = x;
    self.y = y;
}

// ...and a converting sibling accepts any agreeing pair and casts. For
// agreeing arguments of the element type both are viable and the diagonal
// wins the tie, exactly as above.
fn point<T>::set<U>(mut self: point<T>, x: U, y: U) {
    println("  [converting] casting U to the element type");
    self.x = x as T;
    self.y = y as T;
}

// ---- Constraints participate: groups imply by subset ----

// Same shape, both bounded, equal specificity -- but the diagonal's group is
// a SUBSET of the open pattern's, so its constraint implies theirs and the
// tie resolves to it for two int8 arguments. int16 arguments never tie: the
// diagonal's group filters it out at viability and the open member is simply
// the only candidate left. (`extends` bounds imply along the declared
// nominal chain the same way; a group never implies a bound nor vice versa.)
fn fits<T: int8>(x: T, y: T) -> int32 { return 1; }
fn fits<A: int8 | int16, B: int8 | int16>(x: A, y: B) -> int32 { return 2; }

fn main() -> int32 {
    let a: int32 = 1;
    let b: int32 = 2;
    let f: float64 = 2.5;

    // Agreeing arguments pick the diagonal; disagreeing ones leave only the
    // open member, no tie to break.
    println(f"same(a, b)      -> member {same(a, b)}");         // 1
    println(f"same(a, f)      -> member {same(a, f)}");         // 2

    // The three-way chain: full diagonal, partial diagonal, fully open.
    println(f"triple(a, b, a) -> member {triple(a, b, a)}");    // 1
    println(f"triple(a, b, f) -> member {triple(a, b, f)}");    // 2
    println(f"triple(a, f, b) -> member {triple(a, f, b)}");    // 3

    // Untyped integer literals on a float64 point: the diagonal deduces
    // T = float64 at the literal slots, and mcc has no int-to-float literal
    // adaptation, so it drops out at VIABILITY -- no tie ever forms. The
    // converting member deduces the integer U = int32 and casts.
    let p: point<float64> = { x = 0.0, y = 0.0 };
    println("point<float64>, agreeing float64 arguments:");
    point::set(p, 1.5, 2.5);                                     // [diagonal]
    println("point<float64>, integer literals:");
    point::set(p, 1, 2);                                         // [converting]
    println(f"  p.x = {p.x}, p.y = {p.y}");   // 1.000000, 2.000000

    // On an integer point the same literals deduce an integer binding, so
    // the diagonal stays viable and wins the tie as usual.
    let q: point<int32> = { x = 0, y = 0 };
    println("point<int32>, integer literals:");
    point::set(q, 3, 4);                                         // [diagonal]

    // The tighter-group diagonal wins the int8 tie by constraint subset.
    let s8: int8 = 5;
    let t8: int8 = 6;
    let s16: int16 = 300;
    println(f"fits(s8, t8)    -> member {fits(s8, t8)}");       // 1
    println(f"fits(s16, s16)  -> member {fits(s16, s16)}");     // 2

    return 0;
}

// What subsumption does NOT rescue -- the winner must strictly subsume into
// EVERY other tied member, so genuinely incomparable cohorts keep the
// standard ambiguity error:
//   - Forks: the partial diagonals `fork<T, U>(x: T, y: T, z: U)` and
//     `fork<T, U>(x: T, y: U, z: U)` are mutually non-subsuming, so
//     fork(a, a, a) is still
//         error: call to 'fork' is ambiguous between overloads
//   - Pattern and constraint directions in conflict: a LOOSER-bounded
//     diagonal against a tighter-bounded open pattern (subset the wrong way,
//     or a group facing an `extends` bound; an unconstrained parameter
//     implies nothing).
//   - Rank-tied partial specializations like `pair<int32, U>` vs
//     `pair<T, int8>`: each holds a concrete type where the other holds a
//     wildcard (types/method_partial_specialization.mc).
// And it never reorders across the rank: a bounded open pattern still beats
// an unbounded diagonal outright, tier over everything (mixed_overloads.mc).

// See also: mixed_overloads.mc (the tier/specificity rank), mut_overloads.mc
// (mut markers are template identity, so a mut/non-mut same-shape pair stays
// ambiguous for an lvalue), types/type_groups.mc (closed groups),
// types/method_alias.mc (an alias-spelled diagonal, `type diag<T> =
// pair<T, T>`, ordering the same way against an open method). Full rules:
// docs/language.md, "Rank-tied templates: subsumption" under "Function
// overloading".
