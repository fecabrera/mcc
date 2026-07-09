import "std/io";

// Concrete function overloading: plain (non-generic) functions sharing a
// name form an overload set, and the call picks the member whose parameter
// list fits the arguments. The motivating shape is a constructor-flavored
// family: one `init` name, several ways to seed it. This file keeps the
// whole set in one module; sets are open, so members may also come from
// different modules (open_overloads.mc).
// Builds on functions.mc and mut_params.mc (what `mut self` means);
// generic overload sets, which resolve in the same order, are covered in
// mut_overloads.mc, and a set mixing concrete members with a generic
// template in mixed_overloads.mc.

struct counter {
    value: int32;
    step: int32;
}

// Three members differing in ARITY: the argument count alone selects one.
fn counter_init(mut self: struct counter) {              // zeroed
    self.value = 0;
    self.step = 1;
}

fn counter_init(mut self: struct counter, start: int32) {  // seeded
    self.value = start;
    self.step = 1;
}

fn counter_init(mut self: struct counter, start: int32, step: int32) {
    self.value = start;
    self.step = step;
}

// A member differing in argument TYPE at the same arity as the seeded one:
// resolution looks at what the argument is, not just how many there are.
// A string literal at the call site still adapts to a slice<const char>
// parameter exactly as it would at a non-overloaded call.
fn counter_init(mut self: struct counter, label: slice<const char>) {
    self.value = label.length as int32;   // seed from the label, why not
    self.step = 1;
}

// Members must differ in parameter types. Adding a variant that differs
// only in return type, only in const/mut markers, or only in @nonnull /
// @noalias annotations is a duplicate definition:
//     error: function 'counter_init(counter, int32)' already defined;
//     overloads must differ in parameter types
// Width-only pairs (an int32 member beside an int64 one) do compile, but
// an untyped literal call like f(0) is then ambiguous; `0 as int64` or a
// typed variable disambiguates.

fn show(name: char*, const c: struct counter) {
    println("{}: value={} step={}", name, c.value, c.step);
}

fn main() -> int32 {
    let a: struct counter;
    let b: struct counter;
    let c: struct counter;
    let d: struct counter;

    counter_init(a);                // 1 argument:  the zeroed member
    counter_init(b, 100);           // 2 arguments: the seeded member
    counter_init(c, 100, 25);       // 3 arguments: seeded with a step
    counter_init(d, "retries");     // 2 arguments again, but the second is
                                    // text: type picks the slice member

    show("a", a);
    show("b", b);
    show("c", c);
    show("d", d);

    return 0;
}

// One implementation note: a name with a single definition keeps its plain
// C-linkable symbol; only a set's members take signature-derived symbols
// like "counter_init(counter, int32)". Not overloadable: main, variadic
// functions, va_list parameters, @extern/@symbol, and @static functions.
// An overloaded name is also not a function value (`let g = counter_init;`
// has no single address to take).

// See also: mut_overloads.mc (generic overload sets and the resolution
// order), mixed_overloads.mc (a generic template joining a concrete set),
// open_overloads.mc (extending a set from another module),
// mut_params.mc (mut parameters), types/structs.mc (struct types, covered
// later in the tour). Full rules: docs/language.md, "Function overloading".
