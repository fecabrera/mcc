import "std";

// Closed type groups: a pipe-separated group after a generic parameter,
// `fn f<T: int32 | int16 | int8>(x: T)`, is the closed set of types T may
// instantiate to. Deduction is unchanged; the group is a post-deduction
// filter, so a call whose deduced T falls outside it is a compile error at
// the call site naming both the type and the group:
//     error: int8 is not in the type group of 'f' (int64 | int32)
// The payoff is overload partitioning: same-pattern templates with DISJOINT
// groups form a resolvable overload set. This is the function-declaration
// counterpart of the multi-type `case type` arms in case_type_groups.mc:
// the same signed/unsigned formatter grouping, moved onto the declarations,
// with no box and no `case type` needed.
// Prerequisites: generics.mc (deduction, monomorphization) and
// functions/mixed_overloads.mc (the concrete-beats-generic rank this
// feature refines).

// The partition: one bounded template per sign family, same name, same
// parameter pattern. Deduction runs first, then the group filter picks the
// member. Overlapping groups (say, int32 listed in both) would instead
// collide at the declaration:
//     error: ... same-pattern overloads need disjoint type groups
fn show<T: int32 | int16 | int8>(x: T) {
    println("signed   %d", x as int32);
}

fn show<T: uint32 | uint16 | uint8>(x: T) {
    println("unsigned %u", x as uint32);
}

// Ranking gains a middle tier: concrete beats bounded beats unbounded. This
// concrete member takes the exact int32 call away from the signed template
// above...
fn show(x: int32) {
    println("signed   %d (the concrete fast path)", x);
}

// ...and this unbounded template ranks below both bounded ones, catching
// only what every group excludes.
fn show<T>(x: T) {
    println("ungrouped (%d bytes)", sizeof(T) as int32);
}

// Group members are checked EAGERLY: every listed member is instantiated
// and fully type-checked at end of codegen whether or not it is ever
// called, so a member the body does not compile for errors at the
// declaration, with a note naming the instantiation. That matches the
// per-copy checking of multi-type `case type` arms.

// A group composes with a default, and the default must name a group
// member. The priority order from generic_defaults.mc is unchanged: the
// declared default beats the untyped literal's int32 leaning, so width(0)
// measures int64.
fn width<T: int64 | int32 = int64>(x: T) -> int32 {
    return sizeof(T) as int32;
}

fn main() -> int32 {
    // The same values case_type_groups.mc routes through an `any` box; here
    // the static types alone pick the member.
    let n: int32 = 300;
    let h: int16 = -12;
    let b: int8 = 3;
    show(n);            // exact int32: the concrete fast path wins
    show(h);            // deduced int16: the signed group claims it
    show(b);            // deduced int8: same member, another instantiation

    let big: uint32 = 4000000000;   // fits uint32, not int32
    let s: uint16 = 9;
    show(big);          // the unsigned group's template
    show(s);

    // An explicit type argument passes the same filter (and, as in
    // mixed_overloads.mc, selects among the generic candidates only).
    show<int16>(7);

    // No group lists float64, so the unbounded catch-all takes it. Without
    // that member this call would be:
    //     error: no overload of 'show' matches
    show(2.5);

    println("width(0) = %d  (the group's default anchored the literal)",
            width(0));
    return 0;
}

// Group members are concrete types only: no `T*` patterns, no referencing
// another type parameter. The group joins the template's symbol and renders
// in `.mci` interface stubs, so an imported set partitions identically.

// See also: case_type_groups.mc (the `case type` counterpart of this
// grouping), generic_defaults.mc (the default priority order a grouped
// default follows), functions/mixed_overloads.mc (the two-tier rank the
// bounded tier slots into), systems/formatting.mc (the stdlib formatter
// shipping this exact signed/unsigned partition). Full rules:
// docs/language.md, "Closed type groups".
