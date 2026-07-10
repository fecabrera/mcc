import "std/io";

// Native variadics: a trailing `slice<const any>` parameter marks a
// COLLECTING function, and `fn f(args...)` is pure sugar for
// `fn f(const args: slice<const any>)`. At the call site every extra
// argument (past the fixed parameters) is boxed into a caller-stack `any`,
// allocation-free, and the callee receives a read-only slice over them.
// The callee walks it with `for` and recovers values with `case type`.
// Collectors are ordinary overload members too: they can share a name with
// other functions, and a generic collector binds its type parameters from
// the fixed arguments. Prerequisites: types/any.mc (the box and `case
// type`), memory/slices.mc, overloading.mc, and types/generic_case_arms.mc
// (the `when T` arm acc uses below); variadic.mc covers the opaque C `...`
// these replace for mcc-to-mcc calls.

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

// Collecting functions can be overloaded. Ranking is two rules:
// a candidate that matches WITHOUT collecting beats any that must
// collect, and between collecting candidates, more fixed parameters
// wins. These three make one `log` set.
fn log(args...) {
    println("log: {} bare extras", args.length);
}

fn log(level: int32, args...) {
    println("log[{}]: {} extras", level, args.length);
}

fn log(level: int32, tag: char*) {
    println("log[{}] exact: {}", level, tag);
}

// A collector can share a generic name too. T binds from the FIXED
// arguments only; the extras are type-erased boxes, so an extra of the
// wrong type lands in the else arm, it never converts to T.
fn acc<T>(seed: T, args...) -> T {
    let total: T = seed;
    for a in args {
        case type (a) {
            when T v: total = total + v;
            else:     println("acc: skipped a non-{} extra", typename(T));
        }
    }
    return total;
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

    // The overload set. No int32 first argument here, so only the bare
    // collector is viable: both values become extras.
    log("free", true);

    // Both collectors fit this call; more fixed parameters wins, so 7
    // binds `level` instead of being boxed as a third extra.
    log(7, "up", 1);

    // Exact arity, nothing to collect: the (int32, char*) overload
    // beats both collectors outright.
    log(7, "ready");

    // The generic collector: T = int32 comes from the seed alone. The
    // extras 1 and 2 match the T arm; 2.5 was boxed as float64 when the
    // call was made, so it is skipped, not converted.
    println("acc = {}", acc(0, 1, 2, 2.5));

    // A second instantiation: the float64 seed binds T = float64, and
    // now 2.5 is exactly a T.
    println("acc = {}", acc(1.0, 2.5));

    // Still explicit: a fn(...) value's type carries no collecting
    // marker, so a call through one passes the slice<const any> itself.
    return 0;
}

// See also: variadic.mc for the C `...` form (opaque, forward-only);
// overloading.mc and mixed_overloads.mc for the ranking the collectors
// slot into (matching without collecting is the outermost component);
// types/any.mc for the full boxable set and `case type`;
// types/any_struct_boxing.mc for a struct argument boxing by hidden reference
// into this `slice<const any>`, recovered with no copy; the docs section
// "Native variadic arguments" for the pass-through, ranking, and boxing rules.
