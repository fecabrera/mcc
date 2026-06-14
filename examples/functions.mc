import "libc/stdio";

// Functions may be defined in any order: all signatures are collected in a
// first pass, so main can call everything below it.
fn main() -> int32 {
    greet();
    printf("gcd(252, 105) = %d\n", gcd(252, 105));
    printf("fib(10)       = %d\n", fib(10));
    if (is_even(10)) {
        puts("10 is even");
    }
    return 0;
}

// Omitting `-> type` means the function returns void.
fn greet() {
    puts("hello from a void function");
}

// Parameters are mutable locals, so iterative algorithms can reassign them.
fn gcd(a: int32, b: int32) -> int32 {
    while (b != 0) {
        let t = b;
        b = a % b;
        a = t;
    }
    return a;
}

// Recursion works.
fn fib(n: int32) -> int32 {
    if (n < 2) {
        return n;
    }
    return fib(n - 1) + fib(n - 2);
}

// So does mutual recursion.
fn is_even(n: int32) -> bool {
    if (n == 0) {
        return true;
    }
    return is_odd(n - 1);
}

fn is_odd(n: int32) -> bool {
    if (n == 0) {
        return false;
    }
    return is_even(n - 1);
}
