"""The `char` type: a distinct one-byte text type, ABI-compatible with uint8.

Character literals default to char but adapt to an integer slot; a char *value*
is strict (an explicit `as` to/from uint8). char* coerces to uint8* like any
pointer. String literals are char[N], and a char[N] borrows to a slice<char>
that drops the trailing NUL, while a uint8[N] keeps every byte.
"""

import pytest

from mcc.errors import LangError
from mcc.nodes import TypeRef
from helpers import compile_ir, parse, run


def test_char_literal_defaults_to_char():
    # No annotation: 'a' is a char (not an ambiguous untyped constant).
    assert run("fn main() -> int32 { let c = 'a'; return c as int32; }") == 97


def test_char_type_parses():
    (func,) = parse("fn f(c: char) {}").functions
    assert func.params[0][1] == TypeRef("char")


def test_char_literal_adapts_to_uint8():
    assert run("fn main() -> int32 { let b: uint8 = 'A'; return b as int32; }") == 65


def test_char_literal_adapts_to_int32():
    assert run("fn main() -> int32 { let n: int32 = 'a'; return n; }") == 97


def test_char_constant_adapts_in_array_literal():
    # Char literals fit a uint8 array (raw bytes), adapting element by element.
    src = """
    fn main() -> int32 {
        let x: uint8[3] = ['a', 'b', 'c'];
        return (x[0] as int32) + (x[2] as int32);   // 97 + 99
    }
    """
    assert run(src) == 196


def test_char_value_to_uint8_needs_cast():
    with pytest.raises(LangError, match="expected uint8, got char"):
        compile_ir(
            "fn main() -> int32 { let c: char = 'a'; let u: uint8 = c; return 0; }"
        )


def test_uint8_value_to_char_needs_cast():
    with pytest.raises(LangError, match="expected char, got uint8"):
        compile_ir(
            "fn main() -> int32 { let u: uint8 = 5; let c: char = u; return 0; }"
        )


def test_char_value_casts_to_uint8_explicitly():
    src = """
    fn main() -> int32 {
        let c: char = 'a';
        let u: uint8 = c as uint8;
        return u as int32;
    }
    """
    assert run(src) == 97


def test_char_is_distinct_from_uint8_in_generics():
    # A char argument will not satisfy a uint8-bound type parameter (and the
    # reverse): the two are distinct types.
    with pytest.raises(LangError, match="conflicting types"):
        compile_ir(
            """
            import "std/list";
            fn main() -> int32 {
                let xs: struct list<uint8>;
                list_init(&xs, 2);
                list_push(&xs, 'a');   // 'a' is char, xs holds uint8
                return 0;
            }
            """
        )


def test_char_arithmetic_and_compare():
    src = """
    fn main() -> int32 {
        let c: char = '7';
        let d = c - '0';          // char arithmetic -> char
        let ok = (c == '7');
        return (d as int32) * 10 + (ok as int32);   // 7*10 + 1
    }
    """
    assert run(src) == 71


def test_char_is_one_byte():
    assert run("fn main() -> int32 { return sizeof(char) as int32; }") == 1


def test_string_literal_is_char_array():
    # "hi" is char[3] (h, i, NUL); len counts the NUL.
    assert run('fn main() -> int32 { let s = "hi"; return len(s) as int32; }') == 3


def test_char_pointer_coerces_to_uint8_pointer(capfd):
    # A string literal decays to char*, which a uint8* parameter accepts.
    src = """
    import "libc/stdio";
    fn shout(msg: uint8*) { printf("%s\\n", msg); }
    fn main() -> int32 { shout("hi"); return 0; }
    """
    assert run(src) == 0
    assert capfd.readouterr().out == "hi\n"


def test_for_in_string_literal_yields_chars():
    src = """
    fn main() -> int32 {
        let total: int32 = 0;
        for c in "abc" as slice<char> { total = total + (c as int32); }
        return total;   // 97 + 98 + 99
    }
    """
    assert run(src) == 294
