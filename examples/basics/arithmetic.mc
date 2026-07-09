import "std/io";
import "libc/stdlib";

fn main() -> int32 {
    let a: int32 = 17;
    let b: int32 = 5;

    println("a + b  = {}", a + b);
    println("a - b  = {}", a - b);
    println("a * b  = {}", a * b);
    println("a / b  = {}", a / b);   // integer division truncates: 3
    println("a % b  = {}", a % b);  // remainder: 2

    // Standard precedence; parentheses override.
    println("2 + 3 * 4   = {}", 2 + 3 * 4);
    println("(2 + 3) * 4 = {}", (2 + 3) * 4);

    // Unary minus, and abs() from stdlib.h.
    let neg = -a;
    println("neg      = {}", neg);
    println("abs(neg) = {}", abs(neg));

    // Comparisons produce bool values.
    let bigger = a > b;
    if (bigger) {
        println("a > b");
    }
    if (a != b) {
        println("a != b");
    }

    // `!` negates a bool.
    if (!(a < b)) {
        println("a is not less than b");
    }

    // float64 arithmetic (no % for floats).
    let x = 2.5;
    let y = 0.5;
    println("x + y = {}", x + y);
    println("x / y = {}", x / y);
    println("x > y = {}", x > y);

    return 0;
}
