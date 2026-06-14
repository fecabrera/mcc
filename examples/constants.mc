import "std";

// `const` declares a named compile-time constant -- mcc's typed answer to
// C's `#define`. It has no storage; every use is folded in when compiling.
const WIDTH  = 16;
const HEIGHT = 4;

// The initializer is a constant expression: literals, other constants,
// sizeof, casts, and arithmetic, all evaluated at compile time.
const CELLS     = WIDTH * HEIGHT;            // 64
const ROW_BYTES = WIDTH * sizeof(int32);     // 64 bytes

// An annotation pins the type; without one, an integer const stays adaptable
// (it takes on whatever integer type the context needs, like a literal).
const MAX_LEVEL: uint8 = 9;

// A string const works too -- the bytes live in read-only data.
const TITLE = "grid";

// An integer const can size an array, here a @static file-scoped buffer.
@static let grid: int32[CELLS];

fn main() -> int32 {
    // Fill the grid: cell (row, col) gets row * WIDTH + col.
    let row: int32 = 0;
    while (row < HEIGHT) {
        let col: int32 = 0;
        while (col < WIDTH) {
            grid[row * WIDTH + col] = row * WIDTH + col;
            col = col + 1;
        }
        row = row + 1;
    }

    println("%s: %d cells, %d bytes per row", TITLE, CELLS, ROW_BYTES);
    println("last cell = %d, max level = %d",
           grid[CELLS - 1], MAX_LEVEL as int32);

    // len() reports the size that the const fixed -- nothing is hard-coded.
    println("grid holds %llu ints", len(grid));
    return 0;
}
