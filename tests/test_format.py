"""libmc/format.mc: the formatting protocol's baseline overload set.

Every member appends `value`'s rendering to a `string`, steered by a
`modifier` string: an unbounded `format<T>` typename fallback, closed
signed/unsigned integer groups funneling into concrete workers, concretes
for float64/bool/char/char*/slice<char>, and a generic slice<T> bracketed
list-renderer. Open overload sets make the protocol extensible: one
`format` overload in a user module makes that type printable.
"""

import pytest

from mcc.errors import LangError
from helpers import compile_ir, run, run_path

# Shared prelude: imports plus a printer that dumps a string's bytes between
# pipes (the buffer is not NUL-terminated, so print exactly `length` bytes).
PRELUDE = """
import "format";
import "string";
import "libc/stdio";

fn show(const s: string) {
    printf("|%.*s|\\n", s.length as int32, s.data);
}
"""


def test_signed_integers_render_decimal(capfd):
    # The closed signed group (int32|int16|int8) sign-extends into the int64
    # worker, so a negative narrow value renders its true value at every width.
    run(
        PRELUDE
        + """
        fn main() -> int32 {
            let none: struct string;
            string_init(none);
            let s: struct string;
            string_init(s);
            format(s, -4 as int8, none);   string_push(s, ' ');
            format(s, -4 as int16, none);  string_push(s, ' ');
            format(s, -4 as int32, none);  string_push(s, ' ');
            format(s, -4 as int64, none);  string_push(s, ' ');
            format(s, 123456789 as int32, none);
            show(s);
            string_destroy(s);
            string_destroy(none);
            return 0;
        }
        """
    )
    assert capfd.readouterr().out == "|-4 -4 -4 -4 123456789|\n"


def test_unsigned_integers_render_decimal(capfd):
    # The closed unsigned group covers all four widths; uint64's top bit is
    # not a sign, so the maximum renders in full.
    run(
        PRELUDE
        + """
        fn main() -> int32 {
            let none: struct string;
            string_init(none);
            let s: struct string;
            string_init(s);
            format(s, 250 as uint8, none);   string_push(s, ' ');
            format(s, 65535 as uint16, none); string_push(s, ' ');
            format(s, 18446744073709551615 as uint64, none);
            show(s);
            string_destroy(s);
            string_destroy(none);
            return 0;
        }
        """
    )
    assert capfd.readouterr().out == "|250 65535 18446744073709551615|\n"


def test_integer_modifiers(capfd):
    # ":x"/":X" render hex, ":p" pointer-style. A negative int32 was already
    # sign-extended to int64 by the group overload, so its ":x" is the full
    # 64-bit two's-complement pattern -- pinned as the intended behavior.
    run(
        PRELUDE
        + """
        fn main() -> int32 {
            let hex: struct string;
            string_init(hex, ":x");
            let hexup: struct string;
            string_init(hexup, ":X");
            let ptr: struct string;
            string_init(ptr, ":p");
            let s: struct string;
            string_init(s);
            format(s, 255 as uint8, hex);   string_push(s, ' ');
            format(s, 255 as int64, hexup); string_push(s, ' ');
            format(s, -4 as int32, hex);    string_push(s, ' ');
            format(s, 42 as int64, ptr);
            show(s);
            string_destroy(s);
            string_destroy(hex);
            string_destroy(hexup);
            string_destroy(ptr);
            return 0;
        }
        """
    )
    assert capfd.readouterr().out == "|ff FF fffffffffffffffc 0x2a|\n"


def test_float64_renders_fixed_point(capfd):
    run(
        PRELUDE
        + """
        fn main() -> int32 {
            let none: struct string;
            string_init(none);
            let s: struct string;
            string_init(s);
            format(s, 3.5, none);
            show(s);
            string_destroy(s);
            string_destroy(none);
            return 0;
        }
        """
    )
    assert capfd.readouterr().out == "|3.500000|\n"


def test_bool_modifiers(capfd):
    # Default true/false; ":y" renders y/n, ":yes" renders yes/no.
    run(
        PRELUDE
        + """
        fn main() -> int32 {
            let none: struct string;
            string_init(none);
            let y: struct string;
            string_init(y, ":y");
            let yes: struct string;
            string_init(yes, ":yes");
            let s: struct string;
            string_init(s);
            format(s, true, none);  string_push(s, ' ');
            format(s, false, none); string_push(s, ' ');
            format(s, true, y);     string_push(s, ' ');
            format(s, false, y);    string_push(s, ' ');
            format(s, true, yes);   string_push(s, ' ');
            format(s, false, yes);
            show(s);
            string_destroy(s);
            string_destroy(none);
            string_destroy(y);
            string_destroy(yes);
            return 0;
        }
        """
    )
    assert capfd.readouterr().out == "|true false y n yes no|\n"


def test_char_and_c_string(capfd):
    # A bare string literal decays to char* and lands on the char* member --
    # pinned: it renders its bytes rather than colliding with slice<char>.
    run(
        PRELUDE
        + """
        fn main() -> int32 {
            let none: struct string;
            string_init(none);
            let s: struct string;
            string_init(s);
            format(s, 'Z', none);
            format(s, "-hello" as char*, none);
            format(s, "-lit", none);
            show(s);
            string_destroy(s);
            string_destroy(none);
            return 0;
        }
        """
    )
    assert capfd.readouterr().out == "|Z-hello-lit|\n"


def test_slice_renders_bracketed_list(capfd):
    # slice<T> renders a bracketed comma-separated list; the modifier is
    # applied to every element.
    run(
        PRELUDE
        + """
        fn main() -> int32 {
            let none: struct string;
            string_init(none);
            let hex: struct string;
            string_init(hex, ":x");
            let s: struct string;
            string_init(s);
            let a: int32[3] = [10, 255, 3];
            format(s, a as slice<int32>, none); string_push(s, ' ');
            format(s, a as slice<int32>, hex);
            show(s);
            string_destroy(s);
            string_destroy(none);
            string_destroy(hex);
            return 0;
        }
        """
    )
    assert capfd.readouterr().out == "|[10, 255, 3] [a, ff, 3]|\n"


def test_nested_slices_render_nested_lists(capfd):
    # Elements format back through the overload set, so slice<slice<T>>
    # nests bracketed lists.
    run(
        PRELUDE
        + """
        fn main() -> int32 {
            let none: struct string;
            string_init(none);
            let s: struct string;
            string_init(s);
            let a: int32[3] = [1, 2, 3];
            let b: int32[2] = [4, 5];
            let rows: slice<int32>[2] = [a as slice<int32>, b as slice<int32>];
            format(s, rows as slice<slice<int32>>, none);
            show(s);
            string_destroy(s);
            string_destroy(none);
            return 0;
        }
        """
    )
    assert capfd.readouterr().out == "|[[1, 2, 3], [4, 5]]|\n"


def test_slice_of_char_renders_as_text(capfd):
    # The concrete slice<char> member beats the generic list-renderer, so
    # text renders as its bytes, not "[h, i]".
    run(
        PRELUDE
        + """
        fn main() -> int32 {
            let none: struct string;
            string_init(none);
            let s: struct string;
            string_init(s);
            let word = "hi";
            format(s, word as slice<char>, none);
            show(s);
            string_destroy(s);
            string_destroy(none);
            return 0;
        }
        """
    )
    assert capfd.readouterr().out == "|hi|\n"


def test_typename_fallback_for_unformattable_types(capfd):
    # A type no member covers falls to the unbounded format<T>, which renders
    # the type's name in angle brackets instead of a value.
    run(
        PRELUDE
        + """
        fn main() -> int32 {
            let none: struct string;
            string_init(none);
            let s: struct string;
            string_init(s);
            let p: uint8* = null;
            format(s, p, none);
            show(s);
            string_destroy(s);
            string_destroy(none);
            return 0;
        }
        """
    )
    assert capfd.readouterr().out == "|<uint8*>|\n"


def test_untyped_integer_literal_is_ambiguous():
    # Pinned ergonomics quirk (current behavior, not a promise): an untyped
    # integer literal adapts to both the int64 worker and the char member,
    # which tie -- callers must type the value (`42 as int32`).
    with pytest.raises(LangError, match="call to 'format' is ambiguous"):
        compile_ir(
            PRELUDE
            + """
            fn main() -> int32 {
                let none: struct string;
                string_init(none);
                let s: struct string;
                string_init(s);
                format(s, 42, none);
                return 0;
            }
            """
        )


def test_bare_literal_modifier_is_rejected():
    # Pinned ergonomics quirk (current behavior, not a promise): the modifier
    # is a `const string` (a struct), and only slices adapt from a bare
    # literal, so `":x"` stays a char* and no member matches -- modifiers
    # must be built with string_init.
    with pytest.raises(
        LangError,
        match=r"no overload of 'format' with signature "
        r"format\(list<char>, int32, char\*\)",
    ):
        compile_ir(
            PRELUDE
            + """
            fn main() -> int32 {
                let s: struct string;
                string_init(s);
                format(s, 1 as int32, ":x");
                return 0;
            }
            """
        )


def test_user_overload_joins_the_set_cross_module(tmp_path, capfd):
    # The open-sets protocol move: a user module makes its own type printable
    # by declaring one format overload, and it joins the stdlib set at import
    # merge -- including recursing back into the set for its fields.
    (tmp_path / "point.mc").write_text(
        """
        import "format";
        import "string";

        struct point { x: int32; y: int32; }

        fn format(mut str: string, value: struct point*, const modifier: string) {
            string_push(str, '(');
            format(str, value->x, modifier);
            string_append(str, ", ");
            format(str, value->y, modifier);
            string_push(str, ')');
        }
        """
    )
    main = tmp_path / "main.mc"
    main.write_text(
        """
        import "point";
        import "format";
        import "string";
        import "libc/stdio";

        fn show(const s: string) {
            printf("|%.*s|\\n", s.length as int32, s.data);
        }

        fn main() -> int32 {
            let none: struct string;
            string_init(none);
            let hex: struct string;
            string_init(hex, ":x");
            let s: struct string;
            string_init(s);
            let p = struct point { x = 3, y = 255 };
            format(s, &p, none); string_push(s, ' ');
            format(s, &p, hex);
            show(s);
            string_destroy(s);
            string_destroy(none);
            string_destroy(hex);
            return 0;
        }
        """
    )
    assert run_path(main) == 0
    assert capfd.readouterr().out == "|(3, 255) (3, ff)|\n"
