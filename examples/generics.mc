import "libc/stdio";

// A type parameter list after the function name makes a function generic;
// the parameters can stand in anywhere a type is expected.
fn max<T>(a: T, b: T) -> T {
    if (a > b) {
        return a;
    }
    return b;
}

fn clamp<T>(x: T, lo: T, hi: T) -> T {
    if (x < lo) {
        return lo;
    }
    if (x > hi) {
        return hi;
    }
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
    printf("max<int32>   = %d\n", max<int32>(3, 9));
    printf("max<float64> = %f\n", max<float64>(2.5, 1.5));

    // ...or inferred from the arguments (typed variables win over literals).
    let big: int64 = 9000000000;
    printf("max inferred = %lld\n", max(big, 7));

    // Each instantiation compiles to its own function (monomorphization),
    // so uint8 math really happens in 8 bits with unsigned comparisons.
    printf("clamp<uint8> = %u\n", clamp<uint8>(200, 0, 100));

    printf("power<int64>(2, 40) = %lld\n", power<int64>(2, 40));
    printf("first(7, 1.5)       = %d\n", first(7, 1.5));

    return 0;
}
