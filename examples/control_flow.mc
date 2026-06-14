import "libc/stdio";

fn main() -> int32 {
    // if / else if / else
    let n: int32 = 7;
    if (n > 10) {
        puts("big");
    } else if (n > 5) {
        puts("medium");
    } else {
        puts("small");
    }

    // Any integer works as a condition: non-zero is true, as in C.
    let flag: int32 = 1;
    if (flag) {
        puts("non-zero is true");
    }

    // `and` / `or` are the logical operators (no && / ||). They short-circuit
    // and bind looser than comparisons, so this needs no inner parentheses.
    if (n > 5 and n < 10) {
        puts("n is in (5, 10)");
    }
    if (n < 0 or n > 5) {
        puts("n is negative or big");
    }

    // while loops
    let i: int32 = 0;
    while (i < 5) {
        printf("i = %d\n", i);
        i = i + 1;
    }

    // `until` is the inverse of `while`: it loops as long as the condition
    // is false and stops once it becomes true.
    let countdown: int32 = 3;
    until (countdown == 0) {
        printf("countdown = %d\n", countdown);
        countdown = countdown - 1;
    }

    // Loops nest; here is a small multiplication table.
    let row: int32 = 1;
    while (row <= 3) {
        let col: int32 = 1;
        while (col <= 3) {
            printf("%2d ", row * col);
            col = col + 1;
        }
        putchar(10);  // newline
        row = row + 1;
    }

    // `break` leaves the innermost loop; `continue` jumps to its next
    // iteration. Here: sum the odd numbers from 1, stopping after 9.
    let sum: int32 = 0;
    let k: int32 = 0;
    while (true) {
        k = k + 1;
        if (k > 10) { break; }
        if (k % 2 == 0) { continue; }
        sum = sum + k;
    }
    printf("sum of odds 1..9 = %d\n", sum);

    return 0;
}
