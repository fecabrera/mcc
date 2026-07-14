import "std/io";

// A type parameter list after the function name makes a function generic;
// the parameters can stand in anywhere a type is expected.
fn max<T>(a: T, b: T) -> T {
    if (a > b) {
        return a;
    }
    return b;
}

fn clamp<T>(x: T, lo: T, hi: T) -> T {
    if (x < lo) return lo;
    if (x > hi) return hi;
    return x;
}

// Generic functions can recurse.
fn power<T>(base: T, exp: T) -> T {
    if (exp == 0) {
        return 1;
    }
    return base * power(base, exp - 1);
}

// Multiple type parameters.
fn first<A, B>(a: A, b: B) -> A {
    return a;
}

fn main() -> int32 {
    // Explicit instantiation...
    println(f"max<int32>   = {max<int32>(3, 9)}");
    println(f"max<float64> = {max<float64>(2.5, 1.5)}");

    // ...or inferred from the arguments (typed variables win over literals).
    let big: int64 = 9000000000;
    println(f"max inferred = {max(big, 7)}");

    // Each instantiation compiles to its own function (monomorphization),
    // so uint8 math really happens in 8 bits with unsigned comparisons.
    println(f"clamp<uint8> = {clamp<uint8>(200, 0, 100)}");

    println(f"power<int64>(2, 40) = {power<int64>(2, 40)}");
    println(f"first(7, 1.5)       = {first(7, 1.5)}");

    return 0;
}

// Monomorphization also shapes diagnostics: if a generic body fails to compile
// for some T (say, calling max on a struct with no `>`), the error is followed
// by "note: ... in instantiation of max<T>" lines tracing the chain back to
// your call site. See "Instantiation backtraces" in docs/language.md.

// See also: generic_defaults.mc for `<T = int64>` type-parameter defaults.
