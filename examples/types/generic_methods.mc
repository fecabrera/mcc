import "std/io";

// Generic-struct methods: a method may be namespaced to a GENERIC struct, with
// the struct's type parameters written BEFORE the `::` -- `fn point<T>::name`.
// Everything from methods.mc carries over (the receiver is an ordinary
// parameter; this file keeps the explicit qualified calls), with one
// addition: the
// method belongs to the struct TEMPLATE, so it monomorphizes to one function
// per element type, exactly like a generic function does.
//
// Prerequisites: methods.mc for the non-generic method foundation, and
// structs.mc / generics.mc for generic structs and type inference.

struct point<T> {
    x: T;
    y: T;
}

// The receiver type is EXPLICIT: `self: point<T>` must name its type argument.
// That is how `T` enters the picture -- a bare call infers it from the
// argument (`point::sum(pi)` binds `T` from `pi`), while a call whose
// qualifier spells the instantiation PINS it: `point<float64>::sum(pf)` fixes
// the receiver type up front, and a receiver of another instantiation is the
// ordinary coercion error ("argument 1 of 'point::sum': expected point<int32>,
// got point<float64>"). A bare `self: point` would leave `T` mentioned
// nowhere in the signature, so a bare call could not infer it. Here
// `const self` reads the receiver without copying it.
fn point<T>::sum(const self: &point<T>) -> T {
    return self.x + self.y;
}

// A method need not take the receiver at all -- but then a bare call has no
// argument to infer `T` from, so the WRITTEN qualifier is its only callable
// spelling: `point<float64>::origin()` supplies T by pinning it.
fn point<T>::origin() -> point<T> {
    return { x = 0 as T, y = 0 as T };
}

// `reference self` works the same as in methods.mc: the write lands back in the
// caller's value. `factor` is `T` too, so it monomorphizes alongside the
// receiver.
fn point<T>::scale(self: &point<T>, factor: T) {
    self.x = self.x * factor;
    self.y = self.y * factor;
}

struct box<T> {
    value: T;
}

// A method may declare its OWN type parameters, after the method name. The
// struct's `<T>` and the method's `<U>` merge into a single template, and BOTH
// are inferred from the call arguments (`T` from the receiver, `U` from
// `label`). A method type parameter may NOT shadow one of the struct's:
// `fn box<T>::labeled<T>(...)` is a compile error. And while a CALL may pin
// the struct's list (`box<int32>::labeled(b, 9.5)`), the method's own
// parameters stay inference-only: `box<int32>::labeled<float64>(...)` is the
// parse error "type arguments after 'labeled' are not supported; the
// qualifier's list names the struct instantiation and a method's own type
// parameters are inferred".
fn box<T>::labeled<U>(const self: &box<T>, label: U) -> tuple<U, T> {
    return (label, self.value);
}

fn main() -> int32 {
    // `T` is inferred as int32 from `pi`; this call keys the int32 instance.
    let pi: point<int32> = { x = 3, y = 4 };
    println(f"int sum = {point::sum(pi)}");            // 7

    // A point<float64> keys a SEPARATE, distinct point::sum -- monomorphization
    // gives one function per element type. (float64 prints via %f, so the
    // `:.2f` specifier keeps the output clean.)
    let pf: point<float64> = { x = 1.5, y = 2.0 };
    println(f"float sum = {point::sum(pf):.2f}");      // 3.50

    // Or spell the instantiation instead of inferring it: the qualifier's
    // type arguments PIN the receiver, and the call reaches the same
    // point<float64> instance as the inferred call above.
    println(f"pinned sum = {point<float64>::sum(pf):.2f}");   // 3.50

    // The no-receiver member: nothing to infer from, so the pin is the only
    // way to call it.
    let o = point<float64>::origin();
    println(f"origin = ({o.x:.2f}, {o.y:.2f})");     // 0.00, 0.00

    // `reference self` mutates each instance in place, through its own instantiation.
    point::scale(pi, 10);
    println(f"scaled int = ({pi.x}, {pi.y})");       // 30, 40
    point::scale(pf, 2.0);
    println(f"scaled float = ({pf.x:.2f}, {pf.y:.2f})");  // 3.00, 4.00

    // `box::labeled` binds T from the box (int32) and U from the label
    // (float64) in one call; it returns a tuple<U, T> we destructure.
    let b: box<int32> = { value = 7 };
    let lbl, v = box::labeled(b, 9.5);
    println(f"labeled = {lbl:.2f}, {v}");            // 9.50, 7

    return 0;
}

// Much of what was future work here has shipped: the `.method()` call sugar
// (`pi.sum()` is `point::sum(pi)`, with T inferred from the receiver exactly
// as at the bare qualified calls above -- see method_calls.mc), constructors
// and destructors (`point<float64>(1.5, 2.5)`, see constructors.mc and
// destructors.mc), and non-struct qualifiers (builtins and aliases, see
// method_alias.mc). Only dynamic dispatch remains future work. Note the two
// meanings of a written qualifier: at a CALL, `point<float64>::sum(pf)` pins
// the receiver instantiation of the generic member (as above); in a
// DECLARATION, `fn point<float64>::name` is a specialization with its own
// body (method_specialization.mc) -- and a pinned call that matches a
// declared specialization dispatches to it through the ordinary ranking.
//
// See also: method_specialization.mc, which builds on this file to give ONE
// instantiation (`fn point<float64>::name`) its own concrete body that outranks
// the generic; methods.mc for the non-generic foundation these build on;
// structs.mc and generics.mc for generic structs and inference; tuples.mc for
// the `tuple<U, T>` that `box::labeled` returns and its destructuring;
// constructors.mc and destructors.mc for the pinned spelling's driving use
// case, chaining a generic constructor or destructor at the enclosing T;
// docs/language.md "Explicit type arguments at a qualified call" for the
// full pinning rules.
