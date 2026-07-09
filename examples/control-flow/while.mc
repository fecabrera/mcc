import "std/io";

// A `while` loop repeats its body as long as the condition holds -- the basic
// counting loop. `break` leaves the loop early; `continue` skips to the next
// iteration. Loops nest freely.

fn main() -> int32 {
    // Count 0..4.
    let i: int32 = 0;
    while (i < 5) {
        println("i = %d", i);
        i += 1;
    }

    // Nested loops: a small multiplication table.
    let row: int32 = 1;
    while (row <= 3) {
        let col: int32 = 1;
        while (col <= 3) {
            print("%2d ", row * col);
            col += 1;
        }
        println("");                   // newline after each row
        row += 1;
    }

    // `break` and `continue`: sum the odd numbers 1..9, stopping past 10.
    let sum: int32 = 0;
    let k: int32 = 0;
    while (true) {
        k += 1;
        if (k > 10) { break; }         // leave the loop
        if (k % 2 == 0) { continue; }  // skip the even numbers
        sum += k;
    }
    println("sum of odds 1..9 = %d", sum);

    return 0;
}

// See also: until.mc for the inverse loop; ranges.mc for `for i in range(...)`
// counting loops; iteration.mc for `for x in` over a container.
