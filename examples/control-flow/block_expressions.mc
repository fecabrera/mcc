import "std/io";

// A block expression is a `{ ... }` used where a value is expected. It runs
// its statements in their own scope and yields a value with `emit` -- like an
// inlined, single-use, anonymous function. Temporaries declared inside stay
// inside; only the emitted value escapes.

// Compose a 64-bit value from two halves without leaking `hi`/`lo` into the
// surrounding function -- they exist only for the length of the block.
fn pack(hi: uint32, lo: uint32) -> uint64 {
    let value: uint64 = {
        let h = hi as uint64;
        let l = lo as uint64;
        emit (h << 32) | l;
    };
    return value;
}

// `emit` is to a block what `return` is to a function: it must emit on the
// path that reaches the end, so branch-only emits need a trailing one.
fn classify(n: int32) -> char* {
    return {
        if (n == 0) { emit "zero"; }
        if (n < 0)  { emit "negative"; }
        emit "positive";
    };
}

fn main() -> int32 {
    // The trivial { emit e; } is just `e`, so an untyped constant still adapts
    // to the annotated type.
    let one: uint64 = { emit 1; };
    println("one = %llu", one);

    println("pack(0xABCD, 0x1234) = 0x%llX", pack(0xABCD, 0x1234));

    println("classify(0)  = %s", classify(0));
    println("classify(-7) = %s", classify(-7));
    println("classify(42) = %s", classify(42));

    // Block expressions are ordinary expressions: usable as operands, in calls,
    // and nested inside one another.
    let sum: int32 = { emit 20; } + { emit 22; };
    println("sum = %d", sum);

    // A defer inside a block runs when the block yields, before the value
    // leaves -- just like a function's defers run before its return.
    let n: int32 = {
        defer print("(cleanup) ");
        let scratch: int32 = 6 * 7;
        emit scratch;
    };
    println("n = %d", n);

    return 0;
}
