#include <stdio.h>

// `case` matches a value against a series of `when` arms, with an optional
// `else:` default. There is no fall-through: a matching arm runs only its
// own statements and then the case is done.
fn name_of(digit: int32) -> uint8* {
    let label: uint8* = "many";
    case (digit) {
        when 0: label = "zero";
        when 1: label = "one";
        when 2: label = "two";
        else:   label = "lots";
    }
    return label;
}

// The subject can be any type comparable with `==`, including uint8
// characters. `when` values may be any expression of the subject's type.
fn kind_of(c: uint8) -> uint8* {
    let label: uint8* = "other";
    case (c) {
        when '0': label = "zero-char";
        when ' ': label = "space";
    }
    return label;
}

fn main() -> int32 {
    let i: int32 = 0;
    while (i < 4) {
        printf("%d is %s\n", i, name_of(i));
        i = i + 1;
    }

    // `break` and `continue` inside an arm act on the enclosing loop, not the
    // case -- the no-fall-through semantics mean break is never needed to end
    // an arm.
    let n: int32 = 0;
    while (n < 10) {
        n = n + 1;
        case (n % 3) {
            when 0: continue;        // skip multiples of 3
            else:   printf("%d ", n);
        }
    }
    putchar(10);

    printf("'0' -> %s, ' ' -> %s\n", kind_of('0'), kind_of(' '));
    return 0;
}
