import "std/io";

// `tuple<A, B, ...>` is the builtin heterogeneous fixed-arity product: an
// ad-hoc struct without a name, positions instead of field names, the same
// layout the struct with those field types would have. It is built by the
// paren literal `(a, b)` (a top-level comma; `(x)` stays plain grouping, so
// the 1-tuple is `(x,)` and the empty tuple `()`), read by position with a
// compile-time-constant index, narrowed to a smaller tuple with a constant
// slice `t[n:m]`, destructured into named locals with `let a, b = t;`, and
// converted with `as` to and from the struct sharing its exact layout.
// Its headline job is multiple return values.
// Prerequisites: structs.mc and struct_literals.mc (a tuple element coerces
// exactly like a struct-literal field), arrays.mc for the index syntax.

// The headline: two results, no out-param, no one-off struct declaration.
fn divmod(a: int32, b: int32) -> tuple<int32, int32> {
    return (a / b, a % b);
}

// Two same-shape tuples are the same type, across functions and modules, so
// tuples pass and return by value like structs, and a `const &` tuple
// parameter travels by hidden reference (elements then read-only).
fn manhattan(const p: &tuple<int32, int32>) -> int32 {
    return p[0] + p[1];
}

// Generic inference recurses through the shape: A and B are deduced from the
// argument's positions. (See generics.mc.)
fn fst<A, B>(t: tuple<A, B>) -> A {
    return t[0];
}

// Tuples are not named types (`extends tuple<...>` is rejected); naming one
// is the type alias's job, and the alias works anywhere the written type does.
type polar = tuple<int64, float64>;

// A named struct with a pair's exact shape: the cast target below.
struct point {
    x: int32;
    y: int32;
}

fn main() -> int32 {
    // Multiple return values, unpacked by position. The index must fold to a
    // constant (each position has its own type, so a runtime index would
    // have no single result type) and is bounds-checked at compile time:
    // t[2] on this two-tuple is a compile error, not UB.
    let t = divmod(7, 2);
    println(f"divmod(7, 2)  = ({t[0]}, {t[1]})");

    // With no context the literal fixes its own type, untyped integers
    // anchoring to their int32 default: u is tuple<int32, char>.
    let u = (10, 'x');
    println(f"u             = ({u[0]}, {u[1]})");

    // In a tuple-typed position each element lowers against its position's
    // type exactly like a struct-literal field: untyped constants adapt, and
    // a string literal borrows into a slice position with no `as`.
    let big: tuple<int64, int64> = (1, 2);
    let v: tuple<slice<const char>, int32> = ("hi", 2);
    println(f"big           = ({big[0]}, {big[1]})");
    println(f"v             = ({v[0]}, {v[1]})");

    // Elements are lvalues: writes, compound assignment, and whole-value
    // copies all work like struct fields. The uninitialized
    // `let w: tuple<...>;` declares like a struct, filled later.
    let w: tuple<int32, int32>;
    w[0] = 10;
    w[1] = 20;
    w[0] += 5;
    let copy = w;
    copy[1] = 99;                             // a copy: w is untouched
    println(f"w             = ({w[0]}, {w[1]})");
    println(f"manhattan(w)  = {manhattan(w)}");
    println(f"fst(u)        = {fst(u)}");

    // Tuples nest, indexed position by position; a trailing comma is
    // allowed, as in array and struct literals.
    let nested = ((1, 2), 3,);
    println(f"nested[0][1]  = {nested[0][1]}");

    // Slicing is compile-time too: s[n:m] narrows to positions n..m-1, the
    // same half-open [a:b] grammar as sub-slicing on slices, open ends
    // folding against the arity. Unlike a sub-slice the result is a NEW
    // tuple, positions copied out, not a view: it works on an rvalue base
    // and is never a write target (s[1:3] = ... is rejected). Bounds must
    // fold to constants (they pick the result type, like indices) and are
    // checked at compile time (0 <= n <= m <= arity).
    let s = (1, 'x', 2.5, 4);
    let mid = s[1:3];                         // mid is tuple<char, float64>
    println(f"mid           = ({mid[0]}, {mid[1]})");
    println(f"s[1:][2]      = {s[1:][2]}");  // the open tail, then indexed
    println(f"divmod[:][0]  = {divmod(9, 4)[:][0]}");   // rvalue base copy

    // len(t) is the arity -- the same builtin, and the same compile-time,
    // context-adapting constant, as an array's len. It folds in constant
    // expressions, so it composes with the constant bounds above:
    // s[len(s) - 1] is the last position. Arity is a property of the type
    // alone, so an rvalue operand (a call result, a slice) works too.
    println(f"len(s)        = {len(s) as int32}");
    println(f"s[len(s)-1]   = {s[len(s) - 1]}");
    println(f"len(divmod)   = {len(divmod(9, 4)) as int32}");

    // Arity runs all the way down: a slice keeping one position is the
    // 1-tuple (tuple<float64> here), and `()` is the empty tuple `tuple<>`
    // -- a zero-sized unit value like an empty struct, useful when generic
    // code needs a T that carries nothing (len(unit) is 0).
    let single = s[2:3];
    let unit: tuple<> = ();
    println(f"single[0]     = {single[0]}");
    println(f"sizeof(unit)  = {sizeof(unit) as int32}");
    println(f"len(unit)     = {len(unit) as int32}");

    // Destructuring binds positions to names: comma-separated binders, no
    // parens, one ordinary local per position. This is the headline in its
    // final form, multiple return values bound by name at the call site,
    // with the source (call included) evaluated exactly once. Each binder
    // takes its position's type, so annotations are rejected, and without a
    // rest binder the count must equal the arity exactly: `let a, b = s;`
    // on the 4-tuple above is a compile error, not a partial bind.
    let q, r = divmod(9, 4);
    println(f"q, r          = {q}, {r}");

    // A trailing `...` makes the last binder a rest binder taking the tail:
    // `first = s[0]`, `rest = s[1:]`, the constant slice above, so on a
    // tuple the tail is a COPIED smaller tuple, narrowing uniformly (a
    // pair's tail is the 1-tuple, a 1-tuple's is tuple<>). Fixed binders
    // then number at most the arity, naming every position leaves the empty
    // tail, and a lone `let all... = s;` is the whole copy.
    let first, rest... = s;               // rest: tuple<char, float64, int32>
    println(f"first         = {first}");
    println(f"rest[0]       = {rest[0]}");

    // `as` converts a tuple to any struct with the same field types in the
    // same order (a fresh value copy, not a view). Field names never matter;
    // the match is exact and one level deep, and a @packed or @align struct
    // never qualifies. The literal form lowers its elements against the
    // fields like a typed let, so 3 and 4 adapt here.
    let pt = (3, 4) as point;
    println(f"pt            = ({pt.x}, {pt.y})");

    // And back: `as tuple<...>` turns a struct into its positional form,
    // composing with destructuring to consume an existing struct by
    // position. The casts chain (round-trip), but only this pairing
    // converts: struct-to-struct stays nominal, `extends` only (see
    // extends.mc), and tuple-to-tuple stays rejected.
    let x, y = pt as tuple<int32, int32>;
    println(f"x, y          = {x}, {y}");

    // The alias in action; 5 adapts to int64 per position, as above.
    let p: polar = (5, 0.5);
    println(f"polar         = ({p[0]}, {p[1]})");

    // Arrays of tuples: each element literal adapts against the element type.
    let grid: tuple<int32, int32>[2] = [(1, 2), (3, 4)];
    println(f"grid diagonal = {grid[0][0] + grid[1][1]}");

    // The layout is the struct layout, padding included: int32 + int64 pads
    // the first field's slot to 8, so the pair is 16 bytes.
    println(f"sizeof        = {sizeof(tuple<int32, int64>) as int32}");

    // A whole tuple has no format overload of its own, so it renders the
    // generic typename fallback.
    println(f"t             = {t}");

    return 0;
}

// See also: structs.mc for the named counterpart these ride on (declare a
// struct when the positions deserve field names, and cast when a value needs
// to cross between the two forms, as above); struct_literals.mc for the
// per-field adaptation rules tuple elements follow; type_aliases.mc for the
// alias mechanism naming `polar` above; any_struct_boxing.mc for how a tuple
// follows the struct rule under `any`, boxing by hidden reference into a
// `const any` and recovered by a `case type` arm; memory/sub_slices.mc for
// the same [a:b] grammar on slices, where the result is a borrowed view of
// the same storage rather than a copied-out value; that difference carries
// into destructuring, where a slice's rest binder views the tail this one
// copies.
