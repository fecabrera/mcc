import "std/io";

// Native variadics: a trailing `slice<const any>` parameter marks a
// COLLECTING function, and `fn f(args...)` is pure sugar for
// `fn f(const args: slice<const any>)`. At the call site every extra
// argument (past the fixed parameters) is boxed into a caller-stack `any`,
// allocation-free, and the callee receives a read-only slice over them.
// The callee walks it with `for` and recovers values with `case type`.
// Prerequisites: types/any.mc (the box and `case type`) and memory/slices.mc;
// variadic.mc covers the opaque C `...` these replace for mcc-to-mcc calls.

// The sugar form. `label` is a fixed parameter; everything after it collects.
fn sum(label: char*, args...) -> int32 {
    let n: int32 = 0;
    for a in args {
        case type (a) {
            when int32 v: n = n + v;
            when char* s: println("{}: note {}", label, s);
            else:         println("{}: an extra with no arm", label);
        }
    }
    return n;
}

// The explicit spelling is the same marker: the TYPE is what marks a
// collecting function, not the sugar. This collects exactly like sum does.
fn count(args: slice<const any>) -> uint64 {
    return args.length;
}

fn main() -> int32 {
    // Three extras, each boxed where the call is made: the two int32s hit
    // the first arm, the string literal lands under the char* tag.
    println("total = {}", sum("mix", 1, 2, "three"));

    // Zero extras: args is an empty slice (length 0), the loop runs 0 times.
    println("empty = {}", sum("empty"));

    // The explicit form, with and without extras.
    println("count = {}", count(4, 5, 6));
    println("none  = {}", count());

    // v1: a collecting function cannot be overloaded or share a generic
    // name, and a call through a fn(...) value passes the slice explicitly.
    return 0;
}

// See also: variadic.mc for the C `...` form (opaque, forward-only);
// types/any.mc for the full boxable set and `case type`;
// types/any_struct_boxing.mc for a struct argument boxing by hidden reference
// into this `slice<const any>`, recovered with no copy; the docs section
// "Native variadic arguments" for the pass-through and boxing rules.
