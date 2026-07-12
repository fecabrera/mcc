import "std/io";

// Generic-struct methods: a method may be namespaced to a GENERIC struct, with
// the struct's type parameters written BEFORE the `::` -- `fn point<T>::name`.
// Everything from methods.mc carries over (the receiver is an ordinary
// parameter, and the call spells out its qualifier), with one addition: the
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
// That is how `T` enters the picture -- it is inferred from the call argument,
// never spelled at the call site (`point::sum(pi)` binds `T` from `pi`; an
// explicit `point<int32>::sum(...)` does not parse). A bare `self: point`
// would leave `T` mentioned nowhere in the signature, so the call could not
// infer it. Here `const self` reads the receiver without copying it.
fn point<T>::sum(const self: point<T>) -> T {
    return self.x + self.y;
}

// `mut self` works the same as in methods.mc: the write lands back in the
// caller's value. `factor` is `T` too, so it monomorphizes alongside the
// receiver.
fn point<T>::scale(mut self: point<T>, factor: T) {
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
// `fn box<T>::labeled<T>(...)` is a compile error.
fn box<T>::labeled<U>(const self: box<T>, label: U) -> tuple<U, T> {
    return (label, self.value);
}

fn main() -> int32 {
    // `T` is inferred as int32 from `pi`; this call keys the int32 instance.
    let pi: point<int32> = { x = 3, y = 4 };
    println("int sum = {}", point::sum(pi));            // 7

    // A point<float64> keys a SEPARATE, distinct point::sum -- monomorphization
    // gives one function per element type. (float64 prints via %f, so the
    // `:.2f` specifier keeps the output clean.)
    let pf: point<float64> = { x = 1.5, y = 2.0 };
    println("float sum = {:.2f}", point::sum(pf));      // 3.50

    // `mut self` mutates each instance in place, through its own instantiation.
    point::scale(pi, 10);
    println("scaled int = ({}, {})", pi.x, pi.y);       // 30, 40
    point::scale(pf, 2.0);
    println("scaled float = ({:.2f}, {:.2f})", pf.x, pf.y);  // 3.00, 4.00

    // `box::labeled` binds T from the box (int32) and U from the label
    // (float64) in one call; it returns a tuple<U, T> we destructure.
    let b: box<int32> = { value = 7 };
    let lbl, v = box::labeled(b, 9.5);
    println("labeled = {:.2f}, {}", lbl, v);            // 9.50, 7

    return 0;
}

// Still to come, as in methods.mc: the `.method()` call sugar, constructors and
// destructors, dynamic dispatch, non-struct receivers, and explicit type args
// at a `::` call (`point<float64>::sum(...)`) are all future work; today every
// call spells out its qualifier and infers its type arguments.
//
// See also: method_specialization.mc, which builds on this file to give ONE
// instantiation (`fn point<float64>::name`) its own concrete body that outranks
// the generic; methods.mc for the non-generic foundation these build on;
// structs.mc and generics.mc for generic structs and inference; tuples.mc for
// the `tuple<U, T>` that `box::labeled` returns and its destructuring.
