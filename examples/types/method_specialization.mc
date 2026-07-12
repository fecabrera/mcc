import "std/io";
import "libc/math";

// Method specialization: a method may give a concrete body for ONE
// instantiation of a generic struct, written `fn Type<Concrete>::method(...)`.
// It coexists with the generic `fn Type<T>::method(...)` and OUTRANKS it for a
// matching receiver. There is no new dispatch machinery here: a specialization
// registers as an ordinary CONCRETE overload of the qualified name
// `Type::method`, and the existing concrete-beats-generic overload ranking
// (see functions/overloading.mc) picks it whenever the receiver matches.
//
// Rules worth keeping in mind:
//   - A `point<float64>` receiver runs the specialization; a `point<int64>`
//     (or any other) receiver falls through to the generic. Concrete outranks
//     generic by that same existing ranking.
//   - This file keeps the pre-`::` arguments ALL concrete, and ANY concrete
//     type may specialize: a builtin (`point<float64>`), a user struct
//     (`holder<widget>` below), or a structured type (`box<int32>`). Mixing
//     concrete types with fresh type parameters, like `fn pair<int32, U>::m`,
//     is a PARTIAL specialization: see method_partial_specialization.mc.
//   - A generic base is not required. A lone `fn box<int32>::only(...)` with no
//     `fn box<T>::only(...)` beside it is simply a concrete namespaced overload.
//
// Prerequisites: generic_methods.mc for methods on a generic struct (the
// `fn point<T>::name` form and monomorphization), and functions/overloading.mc
// for the concrete-beats-generic ranking that does the dispatch here.

struct point<T> {
    x: T;
    y: T;
}

// The GENERIC magnitude: it works for any element type by widening each field
// with `as float64` before handing it to the libc math routines. The print
// marker lets the runtime output prove this body ran.
fn point<T>::magnitude(const self: point<T>) -> float64 {
    println("  [generic]     point<T>::magnitude, widening with 'as float64'");
    return sqrt(pow(self.x as float64, 2.0) + pow(self.y as float64, 2.0));
}

// The SPECIALIZATION for `point<float64>`: the fields are already float64, so
// this body skips the casts entirely. It outranks the generic above for a
// float64 receiver -- same qualified name `point::magnitude`, but concrete wins.
fn point<float64>::magnitude(const self: point<float64>) -> float64 {
    println("  [specialized] point<float64>::magnitude, no casts needed");
    return sqrt(pow(self.x, 2.0) + pow(self.y, 2.0));
}

// "Any concrete type" is not limited to builtins. `widget` is a user struct,
// and `holder<widget>` is a perfectly good specialization key. A `holder<int32>`
// receiver still uses the generic describe below.
struct widget {
    id: int32;
}

struct holder<T> {
    item: T;
}

fn holder<T>::describe(const self: holder<T>) {
    println("  [generic]     holder<T>::describe");
}

// Specialization keyed on a USER STRUCT argument, `holder<widget>`.
fn holder<widget>::describe(const self: holder<widget>) {
    println("  [specialized] holder<widget>::describe, widget id = {}", self.item.id);
}

fn main() -> int32 {
    // point<int64> has no specialization, so the generic body runs (watch the
    // marker), widening 3 and 4 to float64: sqrt(9 + 16) = 5.00.
    let pi: point<int64> = { x = 3, y = 4 };
    println("point<int64>:");
    println(f"  |pi| = {point::magnitude(pi):.2f}");   // 5.00, via [generic]

    // point<float64> matches the specialization, so the concrete body runs:
    // sqrt(2.25 + 4) = 2.50. Same call spelling, different body.
    let pf: point<float64> = { x = 1.5, y = 2.0 };
    println("point<float64>:");
    println(f"  |pf| = {point::magnitude(pf):.2f}");    // 2.50, via [specialized]

    // holder<int32> -> generic describe; holder<widget> -> the specialization.
    let hi: holder<int32> = { item = 42 };
    println("holder<int32>:");
    holder::describe(hi);                               // [generic]

    let hw: holder<widget> = { item = { id = 7 } };
    println("holder<widget>:");
    holder::describe(hw);                               // [specialized]

    return 0;
}

// See also: method_partial_specialization.mc, which builds on this file to
// mix concrete types and fresh type parameters before the `::`
// (`fn pair<int32, U>::m`); generic_methods.mc for the generic-method form
// this specializes; methods.mc for the non-generic method foundation;
// functions/overloading.mc for the concrete-beats-generic ranking that
// selects the specialization; structs.mc / generics.mc for generic structs
// and inference.
