import "std/io";

// Polymorphic base views: a method call through a BASE-TYPED REFERENCE
// dispatches to the runtime object's own `@override`. mcc has no `class`
// keyword and no vtable pointer in the object -- the dispatch table rides in
// the REFERENCE, not the value, so objects keep their exact byte layout and
// value semantics. A reference `&A` / `const &A` becomes a two-word fat
// pointer {object, table} exactly when `A` is extended somewhere the forming
// site can see; passing a derived value into a fat `&A` slot forms the view
// implicitly (a reference upcast is a view, never a copy -- a by-value
// argument would still need an explicit `as`, and that slices).
//
// This is the DYNAMIC-DISPATCH sequel to method_inheritance.mc, which
// resolves a derived type's inherited method families STATICALLY. Here the
// very same `@override` that shadows an inherited member (Stage 1) becomes
// the table slot a base view routes through at runtime.
//
// Prerequisites: extends.mc for the prefix layout the upcast reads;
// method_inheritance.mc for `@override` and the merged family; const_params.mc
// for the `const &A` hidden-reference view the receiver rides.

// ---- A 3-level chain, one overridden family ----

struct base   { id: int32; }
struct middle extends base  { rank:  int32; }
struct leaf   extends middle { power: int32; }

// `speak` is introduced on base and OVERRIDDEN down the chain. The
// `@override` marker (Stage 1) is what makes the family dispatch: it earns a
// fixed table slot at base, reassigned to each override down the chain. Every
// body reads self.id -- the inherited base field -- to prove the calls all
// land on the SAME object, only through different dispatch.
fn base::speak(const self: &base) {
    println(f"  base::speak   (id {self.id})");
}
@override fn middle::speak(const self: &middle) {
    println(f"  middle::speak (id {self.id})");
}
@override fn leaf::speak(const self: &leaf) {
    println(f"  leaf::speak   (id {self.id})");
}

// `tag` is introduced on base and NEVER overridden, so it has no slot: a call
// through any base view stays an ordinary DIRECT call to this one body.
fn base::tag(const self: &base) {
    println(f"  base::tag     (id {self.id})");
}

// A base method that calls an overridden family on `self` RE-DISPATCHES:
// `self` is itself a fat `&base`, so it carries the runtime object's table
// through and the inner speak() reaches the derived override.
fn base::announce(const self: &base) {
    println(f"  announce (id {self.id}) ->");
    self.speak();
}

// ---- Free functions over the base view ----

// THE HEADLINE: a function that knows only `base`. Passing a `leaf` forms a
// fat view (object + leaf's table), so the base-typed call dispatches to
// leaf::speak -- NOT base::speak.
fn describe(const it: &base) {
    it.speak();                 // dynamic: routes through it's runtime table

    // COPY-ON-READ is prefix extraction: copying a value OUT of the view
    // yields a plain, byte-exact `base` that carries NO table. A call on the
    // copy binds to its STATIC type, so behavioral slicing is impossible.
    // Contrast this line's output with the dispatched call just above.
    let copy: base = it;
    copy.speak();               // static: always base::speak
}

// Dispatch through a MID-CHAIN view: a `&middle` still reaches leaf's override.
fn describe_mid(const it: &middle) {
    it.speak();
}

// A never-overridden family through a base view stays a direct call.
fn show_tag(const it: &base) {
    it.tag();
}

// Re-dispatch through a base method reached via the view.
fn show_announce(const it: &base) {
    it.announce();
}

// A REFERENCE RETURN carries the view (SIE-183): `-> &base` hands back the
// same two-word {object, table} pair, so a forwarded view keeps its RUNTIME
// type across the hop -- relay(v).speak() dispatches leaf::speak, and the
// result re-lends onward (or re-returns, as relay2 shows) with the table
// intact. Contrast copy-on-read above: reading a VALUE out drops the view,
// forwarding a REFERENCE keeps it. That includes `let`: references are not
// storable, so `let r = relay(v);` binds a prefix-extracted COPY (static
// dispatch) -- the view survives only while the result stays an expression
// (chained, re-lent, or re-returned).
fn relay(x: &base) -> &base {
    return x;                   // forwards the incoming view, table and all
}

fn relay2(x: &base) -> &base {
    return relay(x);            // a returned view re-returns intact
}

// The RETURN POSITION upcasts too (SIE-186): a DERIVED lvalue returns as a
// declared base reference, forming the view at the return site exactly as an
// argument would on entry -- the storage reinterprets as the base prefix and
// the view carries the DERIVED table, so the caller's dispatch reaches the
// runtime type. Same asymmetry as arguments: a reference upcasts (never
// slices), a by-value `-> base` return of a leaf still needs the explicit
// `as`.
fn first(x: &leaf, y: &leaf) -> &base {
    return x;                   // a leaf lvalue, returned as &base
}

// COVARIANT OVERRIDE RETURNS ride on that upcast: an `@override` may declare
// a return that is a declared descendant of the base member's reference
// return. The narrowing is SPELLING-LEVEL -- through a base view the shared
// slot still hands back the base-shaped view (carrying the runtime table),
// while a static call on a concrete receiver types the result as the
// override's own `&middle`/`&leaf`, so derived fields are reachable without
// a cast.
fn base::me(self: &base) -> &base {
    return self;
}
@override fn middle::me(self: &middle) -> &middle {
    return self;
}
@override fn leaf::me(self: &leaf) -> &leaf {
    return self;
}

// ... and covariance extends to GENERIC hierarchies (SIE-189): `-> &sack<T>`
// over `-> &box<T>` resolves at no pre-body pass, so the narrowing is judged
// at the TEMPLATE level -- the return's declared `extends` chain, checked
// once for every instantiation -- and each concrete instantiation's slot
// then adapts independently (sack<int32>'s thunk widens with sack<int32>'s
// own table).
struct box<T>  { v: T; }
struct sack<T> extends box<T> { extra: T; }

fn box<T>::what(const self: &box<T>)  { println("  box::what"); }
@override fn sack<T>::what(const self: &sack<T>) { println("  sack::what"); }

fn box<T>::me(self: &box<T>) -> &box<T> { return self; }
@override fn sack<T>::me(self: &sack<T>) -> &sack<T> { return self; }

fn poke(x: &box<int32>) {
    x.me().what();              // dynamic: the covariant slot, per instance
}

// ---- Overload resolution sees the view ----

// A derived argument reaches EVERY overload whose reference position is a
// declared base, so the call resolves instead of demanding an exact
// spelling -- and among the viable candidates, the NEARER base wins: a
// `leaf` picks prefer(&middle) over prefer(&base) (fewer extends hops).
// Distances compare per position: a candidate must be no farther at every
// reference position to win on the conversion (nearer here, farther there
// is an ambiguity error, settled with an explicit `as`).
fn prefer(const it: &base) -> int32 { return 1; }
fn prefer(const it: &middle) -> int32 { return 2; }

// Generic inference works through the same view: a derived argument binds a
// `&cell<T>` parameter's T from its declared base instantiation, so an
// `fcell` infers T = float64 -- no `as`, no explicit type argument.
struct cell<T> { v: T; }
struct fcell extends cell<float64> { tag: int32; }

fn value_of<T>(const c: &cell<T>) -> T {
    return c.v;
}

fn main() -> int32 {
    let v: leaf = { id = 3, rank = 2, power = 1 };

    // Dynamic dispatch through the base view: leaf's override runs, then the
    // extracted copy's call falls back to base -- the contrast made visible.
    println("describe(leaf):");
    describe(v);                // leaf::speak, then base::speak (the copy)

    // A real `middle` value through the same base view dispatches to middle:
    // dispatch follows the runtime object, not the reference's static type.
    // `v as middle` is a by-value upcast (a data slice to a genuine middle).
    println("describe(middle):");
    let m: middle = v as middle;
    describe(m);                // middle::speak, then base::speak (the copy)

    // A mid-chain `&middle` view still reaches the leaf override.
    println("describe_mid(leaf):");
    describe_mid(v);            // leaf::speak

    // The never-overridden family: base's one body runs whatever the runtime
    // type, because the call has no table slot to route through.
    println("show_tag(leaf):");
    show_tag(v);                // base::tag

    // Re-dispatch: base::announce calls speak() on `self`, which carries the
    // leaf table through, so the inner call reaches leaf::speak.
    println("show_announce(leaf):");
    show_announce(v);           // announce header, then leaf::speak

    // A returned reference is still the view: two relay hops later, the
    // method call on the result dispatches the runtime type.
    println("relay2(leaf).speak():");
    relay2(v).speak();          // leaf::speak, through two returned views

    // Return-position upcast: first() returns a leaf lvalue as `&base`, and
    // the formed view dispatches the leaf override.
    println("first(leaf, leaf).speak():");
    let w: leaf = { id = 4, rank = 0, power = 0 };
    first(v, w).speak();        // leaf::speak, through the upcast view

    // Covariant returns, both faces. Statically the result narrows to the
    // receiver's own spelling -- `v.me()` is a `&leaf`, so a leaf field
    // assigns through it without a cast ...
    v.me().power = 9;
    println(f"v.me().power = {v.power}");           // 9, wrote through
    // ... and dynamically, `me()` through the base view still returns the
    // base-shaped view with the runtime table: the chained call dispatches.
    println("relay(leaf).me().speak():");
    relay(v).me().speak();      // leaf::speak, covariance through the slot

    // Generic-hierarchy covariance, the same two faces per instantiation:
    // dispatched through a &box<int32> view the slot hands back the
    // base-shaped view (sack::what runs), and a static call on the concrete
    // sack narrows to `&sack<int32>` -- the derived field writes through.
    let s: sack<int32> = { v = 1, extra = 2 };
    println("poke(sack<int32>):");
    poke(s);                    // sack::what, through the covariant slot
    s.me().extra = 8;
    println(f"s.me().extra = {s.extra}");           // 8

    // POINTER DECAY composes with the view (deref-then-view): a proven
    // `leaf*` at the fat `&base` slot is exactly describe(*p) -- the pointer
    // sheds its one level, the pointee lends its base prefix, and the view
    // carries LEAF's table. A stack value and a heap pointer call the same
    // function identically, extended bases included; the implicit decay
    // keeps the null proof obligation (`&v` is its own proof -- see
    // functions/pointer_decay.mc).
    println("describe(leaf*):");
    let p: leaf* = &v;
    describe(p);                // leaf::speak, then base::speak (the copy)

    // Overload resolution ranks the view: the leaf reaches both prefer
    // overloads, and the nearer base (middle, one hop) beats the farther
    // one (base, two hops).
    println(f"prefer(leaf) picks overload {prefer(v)}");    // 2

    // Generic inference through the view: T = float64, from fcell's
    // declared base instantiation.
    let fc: fcell = { v = 2.5, tag = 7 };
    if (value_of(fc) == 2.5) {
        println("value_of(fcell) infers T = float64");
    }

    return 0;
}

// See also: method_inheritance.mc for the static half of the story (the
// merged family and where `@override` is required); extends.mc for the prefix
// layout the fat view's object pointer and the copy's prefix extraction both
// read; functions/override.mc for the cross-module face of `@override`.
//
// Not shown: fatness is a property of the BASE TYPE, committed at `extends`
// time and uniform across all of its references, independent of whether any
// family is overridden (introducing the first override never changes a
// reference's width) -- an un-extended struct's references stay one word, so
// ordinary container methods pay nothing (leaf::me above returns a plain
// thin `&leaf` for exactly that reason; the slot widens it back to the
// base-shaped view only on the dispatch path). A fat reference (parameter
// or return) may not yet appear in a function-pointer type, and destructors
// are not dispatched yet (the table holds no destructor slot); both are
// later stages.
