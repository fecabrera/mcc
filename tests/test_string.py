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
