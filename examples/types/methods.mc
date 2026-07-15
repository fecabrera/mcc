import "std/io";

// Methods: a function namespaced to a struct, written `fn Type::name(...)` and
// called by its explicit qualified name, `Type::name(args)`. This is the
// FOUNDATIONAL slice -- the explicit-call form. A method's first parameter named
// `self` is its RECEIVER, and a receiver must be reference-shaped:
//   * `const self: &T` -- reads the receiver
//   * `self: &T`       -- mutates it in place (visible to the caller)
// A by-value copy receiver (`self: T`) is rejected by construction: it would
// copy the receiver, which slices a derived value and can never be a dynamic-
// dispatch entry. Callers never write the `&` -- an ordinary value argument
// (`rect::area(r)`) forms the hidden reference automatically. The `.method()`
// call sugar has shipped -- `r.area()` desugars to exactly the calls written
// here (see method_calls.mc) -- and a method named `constructor` makes its type
// callable (`rect(2, 3)`, see constructors.mc); dynamic dispatch is still to
// come. This file sticks to the explicit qualified form, the one every dot call
// desugars into.

struct rect {
    w: int32;
    h: int32;
}

// A read-only method: `const self: &T` reads the receiver and returns a value.
// The reference is formed at the call, so `rect::area(r)` passes a plain value.
fn rect::area(const self: &rect) -> int32 {
    return self.w * self.h;
}

// A mutating method: a plain `self: &rect` writes through to the caller's
// object, so the change is visible after the call.
fn rect::scale(self: &rect, factor: int32) {
    self.w = self.w * factor;
    self.h = self.h * factor;
}

// The qualified name keys an overload set just like a plain name does: these
// two `rect::grow` signatures dispatch by argument type.
fn rect::grow(self: &rect, by: int32) {
    self.w += by;
    self.h += by;
}

fn rect::grow(self: &rect, dw: int32, dh: int32) {
    self.w += dw;
    self.h += dh;
}

struct point {
    x: int32;
    y: int32;
}

// A method name lives under its own struct's namespace, so `point::area` and
// `rect::area` never collide -- they are simply different functions.
fn point::area(const self: &point) -> int32 {
    return self.x * self.y;
}

fn main() -> int32 {
    let r: rect = { w = 3, h = 4 };
    println(f"area = {rect::area(r)}");       // 12

    // `scale` mutates `r` in place through its `self: &rect` receiver.
    rect::scale(r, 2);
    println(f"scaled = {r.w} x {r.h}, area = {rect::area(r)}");  // 6 x 8, 48

    // Overload resolution picks the matching `rect::grow` by arity.
    rect::grow(r, 1);       // both sides + 1
    rect::grow(r, 10, 0);   // width + 10 only
    println(f"grown = {r.w} x {r.h}");      // 17 x 9

    // `point::area` is a different method that happens to share the name.
    let p: point = { x = 5, y = 6 };
    println(f"point area = {point::area(p)}");  // 30

    return 0;
}

// See also: generic_methods.mc for methods on a GENERIC struct
// (`fn point<T>::name`), the next slice built directly on this one; structs.mc
// for structs, pointers, and generics; overloading.mc (under functions/) for
// the overload resolution these methods reuse; constructors.mc for the
// `constructor` method family that makes the type itself callable;
// method_calls.mc for the `.method()` call sugar built on the qualified call
// form here (`r.area()` is `rect::area(r)`, receiver passed verbatim).
