"""lib/string.mc: a byte string built as `string extends array<uint8>`.

Exercises the @inline wrappers (which upcast to array<uint8> explicitly) and
`for c in &s`, which dispatches to string_it/string_next by name.
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
            string_append(&s, 'h');
            string_append(&s, 'i');
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
            string_append(&s, 'a');
            string_append(&s, 'b');
            string_append(&s, 'c');
            for c in &s { printf("%c", c); }
            printf("\\n");
            string_destroy(&s);
            return 0;
        }
        """
    )
    assert capfd.readouterr().out == "abc\n"


def test_for_in_non_struct_rejected():
    with pytest.raises(LangError, match="needs a struct iterable"):
        run("fn main() -> int32 { for x in 5 { } return 0; }")


def test_for_in_without_protocol_function_rejected():
    with pytest.raises(LangError, match="'foo_it'"):
        run("struct foo { x: int32; }\n"
            "fn main() -> int32 { let f: struct foo; for x in &f { } return 0; }")
