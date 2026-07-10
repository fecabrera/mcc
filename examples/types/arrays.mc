import "std/io";

// An array dimension can be any constant integer expression -- a `const`,
// `sizeof`, arithmetic over them -- not just a literal. `counts` is sized for
// every decimal digit, written as a constant expression.
const DIGITS = 10;

// `@static` makes a file-scoped variable with its own zero-initialized
// storage that persists for the whole program -- here, a histogram buffer.
@static let counts: int32[DIGITS];

// A static lookup table built from an array literal. The outer dimension is
// left as [] and inferred from the literal; @static initializers must be
// constant, so the strings live in read-only data.
@static let cmds: char*[][2] = [
    ["help", "show this help"],
    ["quit", "exit the program"],
    ["ls",   "list the files"],
];

// An array passed to a function decays to a pointer to its first element,
// so the parameter is a plain int32*.
fn sum(xs: int32*, n: int32) -> int32 {
    let total: int32 = 0;
    let i: int32 = 0;
    while (i < n) {
        total += xs![i];
        i += 1;
    }
    return total;
}

fn main() -> int32 {
    // A fixed-size array local lives on the stack. Index it with [].
    let squares: int32[6];
    let i: int32 = 0;
    while (i < 6) {
        squares[i] = i * i;
        i += 1;
    }
    println("squares[5] = {}, sum = {}", squares[5], sum(squares, 6));
    println("sizeof(int32[6]) = {} bytes", sizeof(int32[6]));

    // A constant expression sizes this one: room for a digit histogram plus a
    // spare slot. len() reports the computed size (DIGITS + 1 == 11).
    let padded: int32[DIGITS + 1];
    println("padded holds {} ints", len(padded));

    // An array literal initializes in one place; the size can be inferred.
    let digits: int32[] = [3, 1, 4, 1, 5, 9, 1];
    i = 0;
    while (i < 7) {
        counts[digits[i]] += 1;   // counts is the @static buffer
        i += 1;
    }
    println("digit 1 appears {} times", counts[1]);

    // Print the static lookup table. len() gives the row count -- the size
    // that was inferred from the literal -- so nothing is hard-coded. It is an
    // adaptable constant, so it compares against this int32 counter directly.
    i = 0;
    while (i < len(cmds)) {
        println("  {s6} {}", cmds[i][0], cmds[i][1]);  // {s6}: left-aligned, 6 wide
        i += 1;
    }

    // Nested literals build multi-dimensional arrays (row-major).
    let grid: int32[2][2] = [[1, 2], [3, 4]];
    println("grid corners: {} {}", grid[0][0], grid[1][1]);
    return 0;
}
