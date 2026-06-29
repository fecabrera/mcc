import "std";

fn main() -> int32 {
    // Hexadecimal integer literals: 0x / 0X, behaving exactly like decimal
    // ones -- untyped constants that adapt to context. Handy for bit masks
    // and memory-mapped addresses.
    let mask: uint32 = 0xFF;
    let high: uint32 = 0xDEAD0000;
    let flags: uint32 = high | mask;
    println("0xFF = %u, combined = 0x%X", mask, flags);

    // A leading-zero decimal stays decimal: 010 is ten, not eight.
    let ten: int32 = 010;
    println("010 = %d", ten);

    // A character literal is an untyped constant that defaults to the one-byte
    // `char` text type, with the same escapes as strings ('a', '\n', '\0',
    // '\'', '\\'). It also adapts to an integer slot when the value fits.
    let a: char = 'A';
    let newline: char = '\n';
    println("'A' = %d, '\\n' = %d", a as int32, newline as int32);

    // A char does arithmetic and comparison against other char literals --
    // here, converting a digit character to its value.
    let digit: char = '7';
    println("'7' - '0' = %d", (digit - '0') as int32);

    // Walk a string (a char*), classifying each char against character literals.
    let text: char* = "Hi 9!";
    let i: uint64 = 0;
    while (text[i] != '\0') {
        let c: char = text[i];
        if (c >= '0') {
            if (c <= '9') {
                println("%c is a digit", c);
            }
        }
        i = i + 1;
    }
    return 0;
}
