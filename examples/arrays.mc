#include <stdio.h>

// `@static` makes a file-scoped variable with its own zero-initialized
// storage that persists for the whole program -- here, a histogram buffer.
@static let counts: int32[10];

// An array passed to a function decays to a pointer to its first element,
// so the parameter is a plain int32*.
fn sum(xs: int32*, n: int32) -> int32 {
    let total: int32 = 0;
    let i: int32 = 0;
    while (i < n) {
        total = total + xs[i];
        i = i + 1;
    }
    return total;
}

fn main() -> int32 {
    // A fixed-size array local lives on the stack. Index it with [].
    let squares: int32[6];
    let i: int32 = 0;
    while (i < 6) {
        squares[i] = i * i;
        i = i + 1;
    }
    printf("squares[5] = %d, sum = %d\n", squares[5], sum(squares, 6));
    printf("sizeof(int32[6]) = %llu bytes\n", sizeof(int32[6]));

    // Tally some digits into the static histogram, then print it.
    let digits: int32[7];
    digits[0] = 3; digits[1] = 1; digits[2] = 4; digits[3] = 1;
    digits[4] = 5; digits[5] = 9; digits[6] = 1;
    i = 0;
    while (i < 7) {
        counts[digits[i]] = counts[digits[i]] + 1;   // counts is the @static buffer
        i = i + 1;
    }
    printf("digit 1 appears %d times\n", counts[1]);

    // Arrays nest for multiple dimensions (row-major).
    let grid: int32[2][3];
    grid[0][0] = 7;
    grid[1][2] = 8;
    printf("grid corners: %d %d\n", grid[0][0], grid[1][2]);
    return 0;
}
