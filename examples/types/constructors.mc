import "std/io";

// Constructors: a method named `constructor` makes its type callable.
// `S(args)` is sugar for exactly
//     let s: S;                 // allocate and default-initialize the slot
//     S::constructor(s, args);  // construct in place
// and the expression evaluates to s. The desugaring is exact: overload
// resolution, privacy, and every diagnostic are the family call's own
// (positions count the hidden receiver, so a bad first written argument
// reports "argument 2"). `let p = S(args);` binds the constructed slot
// directly, no temporary and no copy, so a `mut self` constructor writes
// p's own storage. The head follows type-use spelling -- explicit type
// arguments, a complete type bare, a transparent alias -- plus one form
// unique to calls: a BARE generic head (`point(1.5, 2.5)`) infers the
// instantiation from the constructor's arguments.
//
// Without a declared constructor the call is an error, never a cast:
// "struct 'point' has no constructor; declare 'fn point::constructor(...)'
// or build the value with a struct literal" -- and for a builtin,
// "type 'int32' has no constructor; declare 'fn int32::constructor(...)'".
// Struct literals (struct_literals.mc) remain the no-constructor spelling.
//
// Prerequisites: methods.mc and generic_methods.mc for method families and
// their inference, struct_literals.mc for field defaults, and
// functions/overloading.mc for the overload resolution every head reuses.

// ---- A non-generic struct: its bare name is the head ----

struct counter {
    n: int32;
    step: int32 = 1;    // field default; already in place when the ctor runs
}

fn counter::constructor(mut self: counter, n: int32) {
    self.n = n;
    // self.step is untouched: the slot default-initializes exactly as a
    // bare `let c: counter;` does, so the constructor starts from step = 1
    // and only fills what it wants to.
}

// ---- A generic struct: explicit, inferring, and alias heads ----

struct point<T> {
    x: T;
    y: T;
}

// The diagonal constructor: both arguments are the element type. Each body
// prints a marker so the runtime output proves which overload resolution
// picked.
fn point<T>::constructor(mut self: point<T>, x: T, y: T) {
    println("  [diagonal]   point<T>::constructor");
    self.x = x;
    self.y = y;
}

// A CONVERTING constructor coexists in the same family: its own per-call
// type parameter <U> accepts any argument type and casts into place.
fn point<T>::constructor<U>(mut self: point<T>, x: U, y: U) {
    println("  [converting] point<T>::constructor<U>");
    self.x = x as T;
    self.y = y as T;
}

type pointf = point<float64>;   // aliases are transparent at the head too

// ---- A fully-defaulted generic: complete, so its bare name works ----

struct box<T = int64> {
    v: T;
}

fn box<T>::constructor(mut self: box<T>, v: T) {
    self.v = v;
}

// ---- Any type with a declared constructor family is constructible ----

// Builtins included: this declaration is what makes `char(65)` a call.
// Without it the head is the has-no-constructor error above; the sugar
// never falls back to a cast.
fn char::constructor(mut self: char, code: int32) {
    self = code as char;
}

// Constructor calls are ordinary expressions: argument position here, and
// return position in mk below, work exactly like the let form.
fn sum1(p: point<float64>) -> float64 {
    return p.x + p.y;
}

fn mk(v: float64) -> point<float64> {
    return point<float64>(v, v);
}

fn main() -> int32 {
    // A bare non-generic head. `let` binds the constructed slot directly:
    // the constructor's `mut self` wrote c's own storage.
    println("counter(41):");
    let c = counter(41);
    c.n += c.step;
    println("  c.n = {}, c.step = {}", c.n, c.step);      // 42, 1 (default)

    // Explicit type arguments type the receiver up front: T = float64 is
    // pinned, so the diagonal is NON-VIABLE for int literals (an int
    // literal does not adapt to a float64 slot) and the converting ctor
    // wins, casting 1 to 1.0.
    println("point<float64>(1, 1):");
    let a = point<float64>(1, 1);
    println("  a = ({:.2f}, {:.2f})", a.x, a.y);

    // A bare generic head spells no instantiation: the receiver enters
    // resolution as a placeholder and the arguments deduce it. float64
    // arguments bind T = float64, and the diagonal outranks the converting
    // ctor (concrete positions beat the per-call <U>).
    println("point(1.5, 2.5):");
    let b = point(1.5, 2.5);
    println("  b = ({:.2f}, {:.2f}), a {}", b.x, b.y, typename(b));

    // Adaptable int literals lean int32, exactly as ordinary inference does.
    println("point(1, 2):");
    let i = point(1, 2);
    println("  i = ({}, {}), a {}", i.x, i.y, typename(i));

    // An alias head chases to the type it names: pointf(3, 4) constructs
    // point<float64>, and the pinned T again routes the int literals to the
    // converting ctor.
    println("pointf(3, 4):");
    let pf = pointf(3, 4);
    println("  pf = ({:.2f}, {:.2f}), a {}", pf.x, pf.y, typename(pf));

    // A fully-defaulted generic written bare is a complete type (as in
    // `let bx: box;`): the defaults fill first, so box(1) constructs
    // box<int64> and the argument adapts to int64 instead of leaning int32.
    println("box(1):");
    let bx = box(1);
    println("  bx.v = {}, a {}", bx.v, typename(bx));

    // Expression and return positions.
    println("sum1(point<float64>(1.5, 2.5)) and mk(2.0):");
    let s = sum1(point<float64>(1.5, 2.5));
    let m = mk(2.0);
    println("  sum1 = {:.2f}, mk(2.0).x = {:.2f}", s, m.x);

    // A builtin head over the declared char family.
    println("char(65):");
    let ch = char(65);
    println("  ch = {}", ch);                             // A

    // The desugared spelling stays first-class alongside the sugar.
    println("point::constructor(d, 7, 9):");
    let d: point<int32>;
    point::constructor(d, 7, 9);
    println("  d = ({}, {})", d.x, d.y);

    return 0;
}

// Name resolution is unchanged: a same-named function, variable, or constant
// wins unconditionally, so declaring `fn point(v: int32)` beside the struct
// would keep calling the function -- the sugar sits where the call would
// otherwise be "undefined function". And `Type::` still enforces no self
// convention: the sugar is a dumb desugar, so a `const self` "constructor"
// compiles and simply initializes nothing, and a non-void constructor's
// return value is discarded by `S(args)`.
//
// See also: methods.mc and generic_methods.mc for the method families the
// sugar calls into; method_calls.mc for `recv.method(args)`, the call-side
// sibling of this head-side sugar (both are exact desugars into the same
// families); method_alias.mc for the alias chasing behind the pointf
// head and for builtin qualifiers like `char::`; struct_literals.mc for the
// no-constructor spelling and the field defaults the slot starts from;
// functions/overloading.mc and mixed_overloads.mc for the ranking that picks
// between the diagonal and converting constructors; method_inheritance.mc
// for constructors merging into a derived type's family through `extends`
// (this diagonal/converting pair returns there, split across a base and its
// extender).
