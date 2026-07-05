"""libmc/string.mc: a text string, `type string = list<char>`.

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
            let c: char;
            string_get(&s, 1, c);
            printf("len=%llu first_then_second=", s.length);   // inherited field
            let f: char;
            string_get(&s, 0, f);
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
            string_append(&a, b as slice<char>);   // a becomes "hi!?"
            for c in &a { printf("%c", c); }
            printf(" len=%llu\\n", a.length);
            string_destroy(&a);
            string_destroy(&b);
            return 0;
        }
        """
    )
    assert capfd.readouterr().out == "hi!? len=4\n"


def test_string_append_array_copies_until_nul(capfd):
    # string_append_array is the char* form of string_append: it walks a
    # NUL-terminated C string byte by byte (no length known up front).
    run(
        """
        import "string";
        import "libc/stdio";
        fn main() -> int32 {
            let s: struct string;
            string_from_array(s, "hey");
            string_append_array(s, " you");
            for c in &s { printf("%c", c); }
            printf(" len=%llu\\n", s.length);
            string_destroy(s);
            return 0;
        }
        """
    )
    assert capfd.readouterr().out == "hey you len=7\n"


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


def test_direct_receiver_through_the_alias(capfd):
    # The post-migration idiom: a local string passes directly, no `&`. Every
    # @inline wrapper re-lends its mut/const self into the list_* slots
    # through the `type string = list<char>` alias.
    run(
        """
        import "string";
        import "libc/stdio";
        fn main() -> int32 {
            let s: struct string;
            string_init(s);
            string_push(s, 'h');
            string_push(s, 'i');
            string_set(s, 0, 'H');
            let c: char;
            string_get(s, 1, c);            // const self, mut out
            printf("%c len=%llu\\n", c, s.length);
            string_reset(s);
            printf("reset len=%llu\\n", s.length);
            string_destroy(s);
            return 0;
        }
        """
    )
    assert capfd.readouterr().out == "i len=2\nreset len=0\n"


def test_string_eq_and_duplicate():
    # string_eq and string_duplicate take their right-hand side as a
    # slice<char>: a string borrows in with `as`, and a literal adapts
    # directly (Stage 4), so string_eq(a, "hi") needs no ceremony.
    assert run(
        """
        import "string";
        fn main() -> int32 {
            let a: struct string;
            string_from_array(a, "hi");
            if (!string_eq(a, "hi")) return 4;   // a literal compares directly
            let b: struct string;
            string_duplicate(b, a as slice<char>);
            if (!string_eq(a, b as slice<char>)) return 1;  // equal after the deep copy
            string_push(b, '!');
            if (string_eq(a, b as slice<char>)) return 2;   // lengths differ now
            string_push(a, '?');                 // a = "hi?", b = "hi!"
            if (string_eq(a, b as slice<char>)) return 3;   // same length, bytes differ
            string_destroy(a);
            string_destroy(b);
            return 0;
        }
        """
    ) == 0


def test_string_duplicate_from_literal_drops_the_nul():
    # string_duplicate builds from any slice<char>, and a string literal
    # adapts to that slot directly, dropping its trailing NUL.
    assert run(
        """
        import "string";
        fn main() -> int32 {
            let s: struct string;
            string_duplicate(s, "hey");
            let n = s.length as int32;
            string_destroy(s);
            return n;
        }
        """
    ) == 3


def test_heap_string_pointer_decays_after_guard():
    # A heap string* reaches the receiver slots through the usual @nonnull
    # proof: one null guard after the allocation covers the later calls.
    assert run(
        """
        import "string";
        import "memory";
        fn main() -> int32 {
            let p = alloc<struct string>(1);
            if (p == null) return 1;
            string_init(p);
            string_push(p, 'x');
            string_push(p, 'y');
            let c: char;
            string_get(p, 1, c);
            let n = p->length as int32;
            string_destroy(p);
            dealloc(p);
            return n * 10 + ((c == 'y') ? 1 : 0);   // 21
        }
        """
    ) == 21


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
    # A char[N] is NUL-terminated text, so the borrow drops the terminator:
    # the slice spans the 3 text bytes, length 3, no trailing NUL.
    src = """
    fn main() -> int32 {
        let s: char[] = "abc";           // char[4]
        let view = s as slice<char>;
        let count: int32 = 0;
        let total: int32 = 0;
        for c in view { count = count + 1; total = total + (c as int32); }
        return (view.length as int32) * 1000 + count * 100 + total;
    }
    """
    # length 3, 3 iterations, 'a'+'b'+'c' = 294  ->  3*1000 + 3*100 + 294
    assert run(src) == 3594


def test_string_literal_borrows_as_slice_directly():
    # A string literal carries its char[N] type, so it borrows without a binding;
    # the NUL is dropped, so the slice spans the 5 text bytes.
    src = """
    fn main() -> int32 {
        let view = "hello" as slice<char>;
        let total: int32 = 0;
        for c in view { total = total + (c as int32); }
        return (view.length as int32) * 1000 + total;   // 5000 + 'h'+'e'+'l'+'l'+'o'
    }
    """
    assert run(src) == 5000 + 104 + 101 + 108 + 108 + 111


def test_uint8_array_borrow_keeps_every_byte():
    # A uint8[N] is a raw byte buffer, not text: its slice keeps every byte,
    # including a trailing NUL (no string stopgap).
    src = """
    fn main() -> int32 {
        let s: uint8[3];
        s[0] = 1; s[1] = 2; s[2] = 3;
        let view = s as slice<uint8>;
        let total: int32 = 0;
        for b in view { total = total + (b as int32); }
        return (view.length as int32) * 1000 + total;   // 3000 + 6
    }
    """
    assert run(src) == 3006


def test_string_argument_still_works(capfd):
    # A string literal in argument position decays to char*, which coerces to
    # uint8* like any pointer, so the libc string functions still take it.
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
