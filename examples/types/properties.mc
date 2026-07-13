import "std/io";
import "std/stack";

// @property: a method reachable through field syntax. `s.length` calls
// `stack<T>::length(s)` -- the annotation says "read me like a field", so a
// zero-argument accessor drops its parentheses at the call site. Nothing else
// changes: it is still an ordinary method (the call spelling `s.length()`
// stays valid beside the field spelling), so overload machinery, inheritance,
// and pointer auto-deref all carry through. A @property takes ONLY its
// receiver and returns a value.
//
// Prerequisites: method_calls.mc for the dot-call desugar a property extends,
// and functions/mut_returns.mc for the `-> mut` accessors the settable form
// below rides on.

struct temperature {
    celsius: int32;
}

// A read-only property: a `const self` accessor computed from a field. Reads
// as `t.fahrenheit`, no parentheses.
@property
fn temperature::fahrenheit(const self: temperature) -> int32 {
    return self.celsius * 9 / 5 + 32;
}

// A SETTABLE property: returning `-> mut int32` re-lends the field's storage
// (functions/mut_returns.mc), so `v.value` is an assignable lvalue and
// `v.value = x` is just `vec2::value(v, ...) = x` through the mut return.
struct cell {
    n: int32;
}

@property
fn cell::value(mut self: cell) -> mut int32 {
    return self.n;
}

// A @property is inherited through `extends` like any other method.
struct labelled_cell extends cell {
    label: char;
}

// A generic property resolves per instantiation, exactly as a generic method.
struct pair<T> {
    a: T;
    b: T;
}

@property
fn pair<T>::first(const self: pair<T>) -> T {
    return self.a;
}

fn main() -> int32 {
    // Read a computed property like a field -- this is the headline: an
    // f-string hole takes it like any expression.
    let t = temperature { celsius = 100 };
    println("{}C is {}F", t.celsius, t.fahrenheit);     // 100C is 212F
    println(f"{t.fahrenheit}");                         // 212

    // Both spellings reach the same method; the call form stays valid.
    println("call spelling: {}", t.fahrenheit());       // 212

    // A `-> mut` property is an lvalue: assignable and compound-assignable,
    // reading and writing through the same accessor.
    let c = cell { n = 5 };
    println("c.value = {}", c.value);                   // 5
    c.value = 40;                                       // property write
    c.value += 2;                                       // compound: 42
    println("after writes: {}", c.value);               // 42

    // Inheritance: the derived type reaches the base's property by field
    // syntax too.
    let lc = labelled_cell { n = 7, label = 'x' };
    println("inherited: {} ({})", lc.value, lc.label);  // 7 (x)

    // A pointer receiver auto-derefs one hop, like a dot call.
    let p = &c;
    println("through a pointer: {}", p.value);          // 42

    // A generic property binds T from the receiver.
    let q = pair<int32> { a = 9, b = 3 };
    println("q.first = {}", q.first);                   // 9

    // A stdlib property: std/stack marks stack<T>::length @property, so a
    // stack's element count reads like a field.
    let s = stack<int32>(2);
    s.push(1);
    s.push(2);
    s.push(3);
    println("stack length = {}", s.length);             // 3

    return 0;
}

// A real field of the name shadows a property (the field wins at `s.field`,
// and the method stays reachable only as `Type::field(s)`); a name that is
// neither field nor property keeps the plain "has no field" diagnostic. See
// method_calls.mc for the same field-first rule on the call spelling.
