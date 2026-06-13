#include <stdio.h>

fn main() -> int32 {
    // Hexadecimal integer literals: 0x / 0X, behaving exactly like decimal
    // ones -- untyped constants that adapt to context. Handy for bit masks
    // and memory-mapped addresses.
    let mask: uint32 = 0xFF;
    let high: uint32 = 0xDEAD0000;
    let flags: uint32 = high | mask;
    printf("0xFF = %u, combined = 0x%X\n", mask, flags);

    // A leading-zero decimal stays decimal: 010 is ten, not eight.
    let ten: int32 = 010;
    printf("010 = %d\n", ten);

    // Character literals are uint8 -- the byte value of one character, with
    // the same escapes as strings ('a', '\n', '\0', '\'', '\\').
    let a: uint8 = 'A';
    let newline: uint8 = '\n';
    printf("'A' = %d, '\\n' = %d\n", a, newline);

    // Being a plain byte, a character does arithmetic and comparison like any
    // uint8 -- here, converting a digit character to its value.
    let digit: uint8 = '7';
    printf("'7' - '0' = %d\n", digit - '0');

    // Walk a string, classifying each byte against character literals.
    let text: uint8* = "Hi 9!";
    let i: uint64 = 0;
    while (text[i] != '\0') {
        let c: uint8 = text[i];
        if (c >= '0') {
            if (c <= '9') { printf("%c is a digit\n", c); }
        }
        i = i + 1;
    }
    return 0;
}
