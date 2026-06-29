import "std";

// `case` matches a value against a series of `when` arms, with an optional
// `else:` default. There is no fall-through: a matching arm runs only its
// own statements and then the case is done.
fn name_of(digit: int32) -> char* {
    let label: char* = "many";
    case (digit) {
        when 0: label = "zero";
        when 1: label = "one";
        when 2: label = "two";
        else:   label = "lots";
    }
    return label;
}

// The subject can be any type comparable with `==`, including `char`
// characters. `when` values may be any expression of the subject's type.
fn kind_of(c: char) -> char* {
    let label: char* = "other";
    case (c) {
        when '0': label = "zero-char";
        when ' ': label = "space";
    }
    return label;
}

// A `when` arm may list several comma-separated values and matches if the
// subject equals any of them -- handy for grouping cases that share a body.
fn classify(c: char) -> char* {
    let label: char* = "consonant";
    case (c) {
        when 'a', 'e', 'i', 'o', 'u':      label = "vowel";
        when '0', '1', '2', '3', '4',
             '5', '6', '7', '8', '9':      label = "digit";
        when ' ', '\t', '\n':              label = "space";
    }
    return label;
}

fn main() -> int32 {
    let i: int32 = 0;
    while (i < 4) {
        println("%d is %s", i, name_of(i));
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
            else:   print("%d ", n);
        }
    }
    println("");

    println("'0' -> %s, ' ' -> %s", kind_of('0'), kind_of(' '));

    // One arm per group, each covering several characters at once.
    let text: char* = "i9 x";
    let j: uint64 = 0;
    while (text[j] != '\0') {
        println("'%c' is a %s", text[j], classify(text[j]));
        j = j + 1;
    }
    return 0;
}
