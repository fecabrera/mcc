import "std/io";

fn main() -> int32 {
    // `let` declares a variable. A bare integer constant has no definite
    // type, so it must be given one -- with an annotation or an `as` cast;
    // `let answer = 42;` alone is a compile error.
    let answer: int32 = 42;
    let answer2 = 42 as int32;

    // Values that already carry a type infer fine: float and bool literals,
    // typed expressions, casts, sizeof, and call results.
    let pi = 3.14159;       // a decimal point always means float64
    let yes = true;         // bool
    let doubled = answer * 2;  // int32, from answer

    // Integer constants adapt to any integer type their value fits in --
    // out-of-range is a compile error.
    let byte: uint8 = 255;
    let short: int16 = -30000;
    let big: int64 = 9000000000;
    let huge: uint64 = 18000000000000000000;

    println("answer = {}", answer);
    println("pi     = {}", pi);
    println("yes    = {}", yes);
    println("byte   = {}", byte);
    println("short  = {}", short);
    println("big    = {}", big);
    println("huge   = {}", huge);

    // Variables are mutable; assignment keeps the declared type.
    answer = answer * 2;
    println("answer = {}", answer);

    // A declaration may omit the initializer if it has a type annotation.
    // Like a C local, it holds garbage until assigned -- useful when the
    // value is decided by branches.
    let parity: char*;
    if (answer % 2 == 0) {
        parity = "even";
    } else {
        parity = "odd";
    }
    println("answer is {}", parity);

    return 0;
}
