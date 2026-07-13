import "std/io";

// Methods: a function namespaced to a struct, written `fn Type::name(...)` and
// called by its explicit qualified name, `Type::name(args)`. This is the
// FOUNDATIONAL slice -- the explicit-call form. The receiver is just an
// ordinary parameter: `Type::` is purely a namespace here, so mcc enforces NO
// `self` convention (no required receiver, no required name, no required first
// type) -- the ONLY rule is that the qualifier (`rect`, `point`) is a declared
// struct. The `.method()` call sugar has shipped -- `r.area()` desugars to
// exactly the calls written here (see method_calls.mc) -- and a method named
// `constructor` makes its type callable (`rect(2, 3)`, see constructors.mc);
// dynamic dispatch is still to come. This file sticks to the explicit
// qualified form, the one every dot call desugars into.

struct rect {
    w: int32;
    h: int32;
}

// A method that reads its receiver and returns a value. `self` is a plain
// parameter -- the name is a convention, not a keyword.
fn rect::area(self: rect) -> int32 {
    return self.w * self.h;
}

// `mut self` is an ordinary `mut` parameter: a mutation through it is visible
// to the caller after the call, no receiver machinery involved.
fn rect::scale(mut self: rect, factor: int32) {
    self.w = self.w * factor;
    self.h = self.h * factor;
}

// The qualified name keys an overload set just like a plain name does: these
// two `rect::grow` signatures dispatch by argument type.
fn rect::grow(mut self: rect, by: int32) {
    self.w += by;
    self.h += by;
}

fn rect::grow(mut self: rect, dw: int32, dh: int32) {
    self.w += dw;
    self.h += dh;
}

struct point {
    x: int32;
    y: int32;
}

// A method name lives under its own struct's namespace, so `point::area` and
// `rect::area` never collide -- they are simply different functions.
fn point::area(self: point) -> int32 {
    return self.x * self.y;
}

fn main() -> int32 {
    let r: rect = { w = 3, h = 4 };
    println("area = {}", rect::area(r));       // 12

    // `scale` mutates `r` in place through `mut self`.
    rect::scale(r, 2);
    println("scaled = {} x {}, area = {}", r.w, r.h, rect::area(r));  // 6 x 8, 48

    // Overload resolution picks the matching `rect::grow` by arity.
    rect::grow(r, 1);       // both sides + 1
    rect::grow(r, 10, 0);   // width + 10 only
    println("grown = {} x {}", r.w, r.h);      // 17 x 9

    // `point::area` is a different method that happens to share the name.
    let p: point = { x = 5, y = 6 };
    println("point area = {}", point::area(p));  // 30

    return 0;
}

// See also: generic_methods.mc for methods on a GENERIC struct
// (`fn point<T>::name`), the next slice built directly on this one; structs.mc
// for structs, pointers, and generics; overloading.mc (under functions/) for
// the overload resolution these methods reuse; constructors.mc for the
// `constructor` method family that makes the type itself callable;
// method_calls.mc for the `.method()` call sugar built on the qualified call
// form here (`r.area()` is `rect::area(r)`, receiver passed verbatim).
