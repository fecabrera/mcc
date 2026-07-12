import "std/io";

// String-literal elements adapt to char-slice element types: an array literal
// like ["bin", "usr/bin"] fills a slice<char>[2] with borrowed
// {pointer, length} views of the string constants, no per-element `as`. The
// borrow drops the NUL, so each length is the text length ("bin" is 3). That
// makes a string lookup table one line per entry.
//
// Prerequisites: strings.mc (string literals, the NUL-dropping borrow) and
// memory/slices.mc (the slice<T> view itself).

// In a @static initializer the adaptation is a compile-time constant: each
// element becomes a {pointer, length} pair aimed into the read-only string
// data. (Contrast arrays.mc, whose @static table stores bare char* with no
// lengths.)
@static let levels: slice<const char>[3] = ["debug", "warn", "error"];

// A scalar @static slice adapts the same way.
@static let prompt: slice<const char> = "> ";

fn main() -> int32 {
    // A local table: each literal adapts to its slice<char> element.
    let dirs: slice<char>[2] = ["bin", "usr/bin"];

    // Index it at runtime like any array. print takes a slice, so entries
    // pass straight through; .length is the NUL-free text length.
    let i: uint64 = 0;
    while (i < len(dirs)) {
        print(dirs[i]);
        println(": {} chars", dirs[i].length);   // 3, then 7
        i += 1;
    }

    // The @static table indexes the same way.
    print(prompt);
    println(levels[1]);                            // "> warn"
    println("levels[2] is {} chars", levels[2].length);   // 5
    return 0;
}

// See also: strings.mc, arrays.mc, memory/slices.mc.
