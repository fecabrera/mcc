import "std/io";

// Nominal type-parameter bounds: `fn f<T extends shape>(x: T)` constrains a
// generic parameter to a struct AND the structs in its declared `extends`
// lineage. Deduction is unchanged; the bound is a post-deduction filter, so a
// call whose deduced T is not shape or one of its `extends` descendants is a
// compile error at the call site naming both the type and the bound:
//     error: label does not satisfy the bound shape of 'describe'
// The bound is NOMINAL, reusing the same subtype relation as the upcast in
// extends.mc: a struct that merely shares shape's field prefix but does not
// `extends shape` is rejected, where a structural rule would have accepted it.
// Bounds are the open-set sibling of the closed groups in type_groups.mc: a
// group lists a fixed set of types, a bound names a struct whose lineage is
// open-ended (any struct, anywhere, may later `extends` it).
// Prerequisites: extends.mc (the nominal `extends` lineage this bounds over),
// generics.mc (deduction), type_groups.mc (the sibling constraint and the
// bounded overload tier).

// A small hierarchy: shape, a circle that extends it, and a disc two hops up.
struct shape  { area: int32; }
struct circle extends shape  { r: int32; }
struct disc   extends circle { fill: int32; }

// The bounded template: T may be shape or anything up its `extends` lineage,
// so the body may read the base's fields through a T value. The bound rides
// the same monomorphization every generic uses -- one instance per T.
fn describe<T extends shape>(x: T*) -> int32 {
    return x!->area;
}

// Overload ranking gains a middle tier: concrete beats BOUNDED beats
// unbounded (the same tier type_groups.mc's grouped templates sit in). This
// bounded template claims subtypes of shape...
fn tag<T extends shape>(x: T*) -> int32 {
    return 1;
}
// ...and this unbounded fallback, a tier below, catches everything the bound
// excludes (a bounded candidate whose bound the deduced type fails is simply
// not viable, so the fallback wins).
fn tag<T>(x: T*) -> int32 {
    return 0;
}

// A bound composes with a default, which must itself satisfy the bound
// (checked at the declaration). Here T defaults to circle, an `extends shape`
// struct, so a no-argument call measures circle.
fn footprint<T extends shape = circle>() -> int32 {
    return sizeof(T) as int32;
}

fn main() -> int32 {
    let s = shape  { area = 3 };
    let c = circle { area = 10, r = 4 };
    let d = disc   { area = 25, r = 2, fill = 1 };

    // Every value in shape's lineage satisfies `extends shape`: the bound
    // struct itself, a direct subtype, and a transitive one.
    println("areas: shape {}, circle {}, disc {}",
            describe(&s), describe(&c), describe(&d));

    // The bounded overload claims the subtypes; a struct outside the lineage
    // falls through to the unbounded fallback. (Passing an outside struct to
    // `describe` instead would be a compile error at the call:
    //     error: label does not satisfy the bound shape of 'describe'.)
    let label = "hi";
    println("tag: circle {} (bounded), string-ptr {} (fallback)",
            tag(&c), tag(&label));

    // The default anchored the measurement to circle.
    println("footprint<circle> = {} bytes", footprint());
    return 0;
}

// The bound joins the template's symbol base (`describe<$0 extends shape>`,
// see docs/language.md "Template symbols") and renders in `.mci` interface
// stubs, so a re-imported bounded template enforces identically. Two bounded
// same-pattern overloads are not yet supported (an open set cannot be shown
// disjoint the way closed groups can) -- one bounded overload beside an
// unbounded fallback is the v1 shape.

// See also: extends.mc (the nominal lineage a bound ranges over),
// type_groups.mc (the closed-set sibling and the bounded overload tier),
// generic_extends.mc (a generic base in the lineage). Full rules:
// docs/language.md, "Bounds".
