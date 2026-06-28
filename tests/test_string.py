"""libmc/string.mc: a byte string, `type string = list<uint8>`.

Exercises the @inline list wrappers (string_push/string_append/string_get/...,
which forward straight to the list_* functions through the transparent alias)
and `for c in &s`, which dispatches to string_it/string_next by name.
"""

import pytest

from mcc.errors import LangError
from helpers import run


def test_base_operations(capfd):
    run(
        """
        import "string";
        import "libc/stdio";
        fn main() -> int32 {
            let s: struct string;
            string_init(&s);
            string_push(&s, 'h');
            string_push(&s, 'i');
            string_set(&s, 0, 'H');
            let c: uint8;
            string_get(&s, 1, &c);
            printf("len=%llu first_then_second=", s.length);   // inherited field
            let f: uint8;
            string_get(&s, 0, &f);
            printf("%c%c\\n", f, c);
            string_destroy(&s);
            return 0;
        }
        """
    )
    assert capfd.readouterr().out == "len=2 first_then_second=Hi\n"


def test_for_in_iterates_characters(capfd):
    run(
        """
        import "string";
        import "libc/stdio";
        fn main() -> int32 {
            let s: struct string;
            string_init(&s);
            string_push(&s, 'a');
            string_push(&s, 'b');
            string_push(&s, 'c');
            for c in &s { printf("%c", c); }
            printf("\\n");
            string_destroy(&s);
            return 0;
        }
        """
    )
    assert capfd.readouterr().out == "abc\n"


def test_string_append_concatenates(capfd):
    run(
        """
        import "string";
        import "libc/stdio";
        fn main() -> int32 {
            let a: struct string;
            string_init(&a);
            string_push(&a, 'h');
            string_push(&a, 'i');
            let b: struct string;
            string_init(&b);
            string_push(&b, '!');
            string_push(&b, '?');
            string_append(&a, &b);          // a becomes "hi!?"
            for c in &a { printf("%c", c); }
            printf(" len=%llu\\n", a.length);
            string_destroy(&a);
            string_destroy(&b);
            return 0;
        }
        """
    )
    assert capfd.readouterr().out == "hi!? len=4\n"


def test_string_from_array_copies_until_nul(capfd):
    run(
        """
        import "string";
        import "libc/stdio";
        fn main() -> int32 {
            let s: struct string;
            string_from_array(&s, "hello");   // copies bytes up to the NUL
            for c in &s { printf("%c", c); }
            printf(" len=%llu\\n", s.length);
            string_destroy(&s);
            return 0;
        }
        """
    )
    assert capfd.readouterr().out == "hello len=5\n"


def test_for_in_non_struct_rejected():
    with pytest.raises(LangError, match="needs a struct iterable"):
        run("fn main() -> int32 { for x in 5 { } return 0; }")


def test_for_in_without_protocol_function_rejected():
    with pytest.raises(LangError, match="'foo_it'"):
        run(
            "struct foo { x: int32; }\n"
            "fn main() -> int32 { let f: struct foo; for x in &f { } return 0; }"
        )


# --- string literals are uint8[N] byte arrays (NUL included) ---------------


def test_unannotated_string_let_is_an_array_with_len():
    # `let s = "hi"` is an owned uint8[3] (h, i, NUL): len/sizeof see the array.
    src = """
    fn main() -> int32 {
        let s = "hi";
        return (len(s) + sizeof(s)) as int32;   // 3 + 3
    }
    """
    assert run(src) == 6


def test_len_of_string_literal():
    assert run('fn main() -> int32 { return len("hello") as int32; }') == 6  # +NUL


def test_string_array_is_mutable():
    src = """
    fn main() -> int32 {
        let s = "hi";
        s[0] = 'H';                 // owned copy, so writable
        return s[0] as int32;       // 'H'
    }
    """
    assert run(src) == 72


def test_inferred_size_from_string(capfd):
    src = """
    import "libc/stdio";
    fn main() -> int32 {
        let s: uint8[] = "hello";   // infers uint8[6]
        printf("%llu %s\\n", len(s), &s[0]);
        return 0;
    }
    """
    assert run(src) == 0
    assert capfd.readouterr().out == "6 hello\n"


def test_oversize_array_is_zero_filled(capfd):
    src = """
    import "libc/stdio";
    fn main() -> int32 {
        let s: uint8[8] = "hi";     // bytes past "hi\\0" are zero
        printf("%s %llu\\n", &s[0], len(s));
        return 0;
    }
    """
    assert run(src) == 0
    assert capfd.readouterr().out == "hi 8\n"


def test_too_small_array_is_rejected():
    with pytest.raises(LangError, match="needs 3 bytes"):
        run('fn main() -> int32 { let s: uint8[2] = "hi"; return 0; }')


def test_pointer_annotation_decays(capfd):
    # `let s: uint8*` keeps the old pointer-to-constant behavior (no copy).
    src = """
    import "libc/stdio";
    fn main() -> int32 {
        let s: uint8* = "hello";
        printf("%s\\n", s);
        return 0;
    }
    """
    assert run(src) == 0
    assert capfd.readouterr().out == "hello\n"


def test_cast_to_pointer_decays():
    src = """
    fn main() -> int32 {
        let s = "hi";
        let p = s as uint8*;        // decay the owned array
        return p[1] as int32;       // 'i'
    }
    """
    assert run(src) == 105


def test_literal_cast_to_pointer():
    assert run('fn main() -> int32 { return ("hi" as uint8*)[0] as int32; }') == 104


def test_string_array_borrows_as_slice():
    src = """
    fn main() -> int32 {
        let s: uint8[] = "abc";          // uint8[4]
        let total: int32 = 0;
        for c in s as slice<uint8> { total = total + (c as int32); }
        return total;                    // 'a'+'b'+'c'+NUL = 97+98+99+0
    }
    """
    assert run(src) == 294


def test_string_argument_still_works(capfd):
    # A string literal in argument position decays to uint8*, as before.
    src = """
    import "libc/stdio";
    fn shout(msg: uint8*) { printf("%s!\\n", msg); }
    fn main() -> int32 { shout("hi"); return 0; }
    """
    assert run(src) == 0
    assert capfd.readouterr().out == "hi!\n"


def test_static_string_array_initializer():
    src = """
    @static let greeting: uint8[] = "hi";
    fn main() -> int32 {
        greeting[0] = 'H';
        return (greeting[0] + greeting[1]) as int32;   // 'H' + 'i'
    }
    """
    assert run(src) == 72 + 105
