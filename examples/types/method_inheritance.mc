import "std/io";

// Method inheritance through `extends`: a derived struct exposes its base
// chain's method families, constructors included. A family call on the
// derived type (dot sugar or the qualified spelling) resolves over the
// MERGED set of the derived type's own members plus every base hop's, the
// latter rebased at the declared base instantiation: on
// `struct pointf extends point<float64>`, the inherited diagonal
// constructor acts as a concrete (float64, float64) member of pointf. The
// overload rank gains one component, (no-collect, tier, -hop, specificity,
// fixed): the TIER beats the hop (an inherited concrete match beats a
// derived generic), and the HOP beats specificity (a derived same-shape
// member shadows an inherited one, a nearer base's shadows a farther one).
// Each constructor and describe body prints a marker so the runtime output
// proves which member resolution picked.
//
// Prerequisites: extends.mc for the prefix layout and upcasts the receiver
// rides on, constructors.mc for the diagonal/converting family and the
// `S(args)` head, method_calls.mc for the dot sugar; generic_extends.mc for
// the generic derivation at the end.

// ---- The base family ----

struct point<T> {
    x: T;
    y: T;
}

// The diagonal constructor. On pointf below it is INHERITED, rebased to a
// concrete (float64, float64) member of pointf's own family.
fn point<T>::constructor(self: &point<T>, x: T, y: T) {
    println("  [diagonal]   point<T>::constructor");
    self.x = x;
    self.y = y;
}

fn point<T>::sum(const self: &point<T>) -> T {
    return self.x + self.y;
}

// A reference-self method: a const or reference receiver lends the base prefix IN
// PLACE, so on a derived receiver these stores land in the derived value's
// leading fields. (A by-value `self: point<T>` receiver would prefix-copy
// instead, the same slicing the `as` value upcast performs.)
fn point<T>::translate(self: &point<T>, dx: T, dy: T) {
    self.x += dx;
    self.y += dy;
}

fn point<T>::describe(const self: &point<T>) {
    println("  [base]    point<T>::describe");
}

// ---- A concrete derivation ----

// pointf inherits every point member rebased at point<float64>.
struct pointf extends point<float64> {}

// A derived CONVERTING constructor: a different signature simply OVERLOADS
// the merged family (there is no C++-style name hiding).
fn pointf::constructor<U>(self: &pointf, x: U, y: U) {
    println("  [converting] pointf::constructor<U>");
    self.x = x as float64;
    self.y = y as float64;
}

// A derived SAME-SHAPE member shadows the inherited one, so it is an
// OVERRIDE and must carry `@override`: a derived method whose signature
// pattern matches an inherited base member (here point<T>::describe rebased
// to pointf) hides it at resolution (the hop beats specificity), and the
// marker makes that intent explicit. Dropping it is a compile error
// ("shadows the inherited base member ... and must be marked @override"),
// and conversely marking a member that shadows nothing is also an error
// ("overrides no inherited base member"). A different signature -- like the
// converting constructor above -- merely OVERLOADS and takes no marker.
@override fn pointf::describe(const self: &pointf) {
    println("  [derived] pointf::describe");
}

// ---- A transitive derivation, chaining its constructor ----

struct point3f extends pointf {
    z: float64 = -1.0;    // field default; watch the inherited ctor keep it
}

fn point3f::constructor(self: &point3f, x: float64, y: float64, z: float64) {
    // The receiver position upcasts, and ONLY the receiver (a derived value
    // in any other slot still needs an explicit `as`), so a derived
    // constructor CHAINS by calling a base's directly, then fills its own.
    point::constructor(self, x, y);
    self.z = z;
}

// ---- A generic derivation stays generic ----

// pd<T> inherits point<T>'s members with the receiver binding T.
struct pd<T> extends point<T> {}

fn main() -> int32 {
    // The acceptance shape. Float literals: the inherited diagonal is a
    // concrete (float64, float64) member on pointf and beats the derived
    // generic <U> (the tier beats the hop). Int literals never adapt to the
    // diagonal's float64 slots, so there the converting <U> wins and casts.
    println("pointf(1.0, 1.0):");
    let p = pointf(1.0, 1.0);
    println("pointf(1, 1):");
    let q = pointf(1, 1);
    println(f"  p = ({p.x:.2f}, {p.y:.2f}), q = ({q.x:.2f}, {q.y:.2f})");

    // An inherited method via the dot call: p.sum() resolves point<T>::sum
    // rebased at float64, exactly as if pointf had declared it.
    println(f"p.sum() = {p.sum():.2f}");                     // 2.00

    // An inherited reference-self method writes through: the receiver lends its
    // base prefix in place, so the stores land in p's own leading fields.
    p.translate(2.0, 3.0);
    println(f"translated: ({p.x:.2f}, {p.y:.2f})");        // 3.00, 4.00

    // Shadowing: the derived same-shape describe wins on pointf. The base
    // body is not hidden, only outranked: the explicit base-qualified call
    // reaches it, its receiver upcasting like any method-family receiver.
    p.describe();               // [derived]
    point::describe(p);         // [base]

    // Constructor chaining: point3f's own constructor runs the inherited
    // diagonal on its base prefix (the [diagonal] marker below is that
    // chained call), then fills z.
    println("point3f(1.5, 2.5, 3.5):");
    let t = point3f(1.5, 2.5, 3.5);
    println(f"  t = ({t.x:.2f}, {t.y:.2f}, {t.z:.2f}), sum {t.sum():.2f}");

    // Nearer shadows farther: on point3f, pointf's describe (hop 1) shadows
    // point's (hop 2).
    t.describe();               // [derived]

    // The whole merged family is callable on point3f, so two float
    // arguments construct one too, and the tier still beats the hop: the
    // hop-2 concrete diagonal outranks the hop-1 converting <U>. An
    // inherited constructor never sees the derived type's added fields;
    // z keeps its `let s: S;` field default.
    println("point3f(4.0, 5.0):");
    let u = point3f(4.0, 5.0);
    println(f"  u = ({u.x:.2f}, {u.y:.2f}), z = {u.z:.2f}");  // z = -1.00

    // A generic derivation: the inherited members stay generic, bare-head
    // constructor inference included. Int literals lean int32, so this
    // builds a pd<int32>, not the origin's point<int32>.
    println("pd(1, 2):");
    let g = pd(1, 2);
    println(f"  g.sum() = {g.sum()}, a {typename(g)}");    // 3, pd<int32>

    return 0;
}

// Not shown: an inherited method's RETURN type stays spelled at the base
// (a `fn point<T>::flipped(...) -> point<T>` returns a point<float64> on
// pointf, never a pointf); base-family SPECIALIZATIONS
// (method_specialization.mc) are inherited only where the extends clause
// names their instantiation (`fn point<int32>::m` never appears on pointf);
// and the bare-type-parameter base of memory/intrusive_list.mc
// (`struct entry<T> extends T`) inherits nothing, the payload's methods
// staying behind the explicit upcast. Under the hood there is one origin
// instance per base instantiation, not one per derived type: p.sum() and
// t.sum() both call the same point::sum<float64> symbol through a receiver
// cast.
//
// This file resolves inherited families STATICALLY, on the derived type.
// polymorphic_views.mc is the dynamic-dispatch sequel: the same `@override`
// that shadows an inherited member here becomes the table slot a base-typed
// reference routes through at runtime.
//
// See also: polymorphic_views.mc for dynamic dispatch through base views;
// extends.mc and generic_extends.mc for the prefix layout and upcast rules
// the receiver position reuses; constructors.mc for the diagonal/converting
// ranking inside one type; method_calls.mc for the dot sugar these calls ride;
// memory/intrusive_list.mc for the non-participating bare `extends T`.
