import "libc/stdio";
import "libc/stdlib";

fn main() -> int32 {
    let a: int32 = 17;
    let b: int32 = 5;

    printf("a + b  = %d\n", a + b);
    printf("a - b  = %d\n", a - b);
    printf("a * b  = %d\n", a * b);
    printf("a / b  = %d\n", a / b);   // integer division truncates: 3
    printf("a %% b  = %d\n", a % b);  // remainder: 2

    // Standard precedence; parentheses override.
    printf("2 + 3 * 4   = %d\n", 2 + 3 * 4);
    printf("(2 + 3) * 4 = %d\n", (2 + 3) * 4);

    // Unary minus, and abs() from stdlib.h.
    let neg = -a;
    printf("neg      = %d\n", neg);
    printf("abs(neg) = %d\n", abs(neg));

    // Comparisons produce bool values.
    let bigger = a > b;
    if (bigger) {
        puts("a > b");
    }
    if (a != b) {
        puts("a != b");
    }

    // `!` negates a bool.
    if (!(a < b)) {
        puts("a is not less than b");
    }

    // float64 arithmetic (no % for floats).
    let x = 2.5;
    let y = 0.5;
    printf("x + y = %f\n", x + y);
    printf("x / y = %f\n", x / y);
    printf("x > y = %d\n", x > y);

    return 0;
}
