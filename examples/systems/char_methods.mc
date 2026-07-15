import "std/io";
import "std/char";

// The std/char module: character classification and case conversion as
// methods ON the builtin `char` type, thin @inline wrappers over libc's
// <ctype.h>. Seven predicates -- char::is_alpha, is_alnum, is_digit,
// is_hex, is_space, is_upper, is_lower, each `(const self: &char) -> bool`
// -- and two conversions, char::upper / char::lower
// (`(const self: &char) -> char`, a character with no other case comes
// back unchanged). This file is about the LIBRARY; the language feature
// it stands on (methods whose qualifier is a builtin type) is
// types/method_alias.mc.
//
// Prerequisites: types/methods.mc for the qualified `Type::name(args)`
// call form, basics/literals.mc for char literals, memory/slices.mc for
// the `as slice<char>` borrow the scans below iterate.

// One row of the classification table: every predicate applied to one
// character. The receiver is an ordinary `const self` argument; this file
// keeps the explicit `char::is_alpha(c)` spelling, and the `.method()`
// sugar (`c.is_alpha()`, see types/method_calls.mc) dispatches the same
// family either way.
fn describe(c: char) {
    println("  '{}'  alpha={} digit={} alnum={} hex={} space={} upper={} lower={}".format(
            c, char::is_alpha(c), char::is_digit(c), char::is_alnum(c),
            char::is_hex(c), char::is_space(c), char::is_upper(c),
            char::is_lower(c)));
}

// Case-insensitive equality, the classic use of char::lower: fold both
// sides to one case and compare. String literals at the call adapt to the
// slice<const char> parameters on their own (functions/overloading.mc).
fn eq_ignore_case(const a: &slice<const char>, const b: &slice<const char>) -> bool {
    if (a.length != b.length) return false;
    for i in range(a.length) {
        if (char::lower(a[i]) != char::lower(b[i])) return false;
    }
    return true;
}

// char::is_hex admits both letter cases, so validating a "0x" literal is
// a prefix check plus one predicate per remaining character.
fn is_hex_literal(const s: &slice<const char>) -> bool {
    if (s.length < 3 or s[0] != '0' or s[1] != 'x') return false;
    for i in range(2, s.length) {
        if (!char::is_hex(s[i])) return false;
    }
    return true;
}

fn main() -> int32 {
    // ---- Classification ----
    // Each predicate asks libc's ctype tables, so the answers agree with
    // isalpha() and friends. Note 'f' is a letter AND a hex digit, and
    // '7' is the only sample where digit implies alnum without alpha.
    println("classification:");
    describe('m');
    describe('Q');
    describe('7');
    describe('f');
    describe(' ');
    describe('!');

    // ---- Case conversion ----
    // upper/lower return the converted character; anything without the
    // other case (digits, punctuation, space) is returned unchanged.
    println("conversion:");
    println("  upper('q') = '{}', lower('Q') = '{}'".format(
            char::upper('q'), char::lower('Q')));
    println("  upper('7') = '{}', upper('!') = '{}'  (non-letters unchanged)".format(
            char::upper('7'), char::upper('!')));
    // The dot spelling calls the same method (types/method_calls.mc).
    println("  'q'.upper() = '{}'  (dot-call sugar)".format('q'.upper()));

    // Folding case per character gives case-insensitive comparison.
    println("eq_ignore_case:");
    println("  \"Sydney\" == \"SYDNEY\" -> {}".format(eq_ignore_case("Sydney", "SYDNEY")));
    println("  \"Sydney\" == \"Sidney\" -> {}".format(eq_ignore_case("Sydney", "Sidney")));

    // And is_hex validates a whole token one character at a time.
    println("is_hex_literal:");
    println("  \"0x7F3a\" -> {}".format(is_hex_literal("0x7F3a")));
    println("  \"0x7G\"   -> {}".format(is_hex_literal("0x7G")));

    // ---- A classifying scan ----
    // The motivating shape: borrow text as a char slice and classify it
    // one character at a time.
    let msg = "Flight 714 to Sydney, gate B6.";
    let letters: int32 = 0;
    let digits: int32 = 0;
    let spaces: int32 = 0;
    let other: int32 = 0;
    for c in msg as slice<char> {   // the borrow's length drops the NUL
        if (char::is_alpha(c))      letters += 1;
        else if (char::is_digit(c)) digits += 1;
        else if (char::is_space(c)) spaces += 1;
        else                        other += 1;
    }
    println(f"scan \"{msg as slice<const char>}\":");
    println(f"  {letters=} {digits=} {spaces=} {other=}");

    // ---- Case conversion in place ----
    // The conversions compose with writes through a borrowed slice: one
    // char::upper per element shouts the message, punctuation untouched.
    let quiet: char[20] = "please, no shouting";
    let s = quiet as slice<char>;
    for i in range(s.length) {
        s[i] = char::upper(s[i]);
    }
    println(f"shouted: {s}");

    return 0;
}

// See also: types/method_alias.mc for the builtin-qualifier language
// feature these nine methods are declared with; types/method_calls.mc for
// the `.method()` sugar that makes them read `c.is_alpha()`;
// systems/formatting.mc for
// the other stdlib overload set println leans on here; types/strings.mc
// for the char[N] text arrays the scans borrow from.
