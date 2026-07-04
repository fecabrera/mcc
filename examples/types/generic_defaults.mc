import "std";

// Generic type-parameter defaults: `<T = int64>` declares a fallback type
// for a parameter that is neither given explicitly nor inferred from a
// typed argument. The priority order is the feature:
//     explicit type argument > typed-value inference
//         > declared default > untyped-constant anchoring
// Prerequisites: generics.mc (inference, monomorphization) and
// struct_literals.mc (field-based type-argument inference).

// Without the default, size(0) would anchor T to the untyped literal's
// int32 leaning; with it, the default wins and the literal adapts.
fn size<T = int64>(x: T) -> int64 {
    return sizeof(T) as int64;
}

// A default may reference earlier parameters. Defaults are trailing-only:
// once a parameter has one, every parameter after it needs one too
// (`<T = int32, U>` is a compile error).
fn ref_width<T, U = T*>(x: T, y: U) -> int64 {
    return sizeof(U) as int64;
}

// Generic structs take defaults the same way...
struct span<T = int64> {
    start: T;
    stop:  T;
}

// ...and a written type may omit a fully-defaulted tail.
struct pair<A, B = int8> {
    a: A;
    b: B;
}

fn main() -> int32 {
    // The priority ladder, low to high, on one function:
    println("size(0)          = %lld  (declared default: T = int64)", size(0));
    println("size(0 as int16) = %lld  (typed value beats the default)", size(0 as int16));
    println("size<int8>(0)    = %lld  (explicit beats everything)", size<int8>(0));

    // U falls back to T*, so with T = int8 it is int8*: a pointer's width.
    let v = 5 as int8;
    println("ref_width        = %lld  (U defaulted to T* = int8*)", ref_width(v, &v));

    // In a written type, bare `span` means span<int64>; the same works in
    // sizeof and extends.
    let s: span;
    s.start = 0;
    s.stop  = 16;
    println("bare span        : stop = %lld, sizeof = %lld",
            s.stop, sizeof(span) as int64);

    // pair<int32> fills the omitted tail from its default: pair<int32, int8>.
    println("pair<int32>      : sizeof = %lld  (B filled as int8)",
            sizeof(pair<int32>) as int64);

    // A struct literal with no typed field for T uses the default too:
    // both fields are untyped constants, so this is a span<int64>.
    let r = struct span { start = 0, stop = 10 };
    println("literal span     : start width = %lld", size(r.start));

    // A typed field still wins, exactly like call-site inference.
    let small = struct span { start = 1 as int16, stop = 10 };
    println("typed field wins : start width = %lld", size(small.start));

    return 0;
}

// See also: generics.mc, struct_literals.mc.
