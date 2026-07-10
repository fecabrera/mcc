"""lib/std/format.mc: the formatting protocol's baseline overload set.

Every member appends `value`'s rendering to a `string`, steered by a
`slice<char>` modifier (a string literal adapts directly, so
`format(s, 255 as int32, "x")` works as-is): an unbounded `format<T>`
typename fallback, closed signed/unsigned integer groups funneling into
concrete workers, concretes for float64/bool/char/char*/slice<char>/
slice<char*>, and a generic slice<T> bracketed list-renderer. Open overload
sets make the protocol extensible: one `format` overload in a user module
makes that type printable.
"""

import pytest

from mcc.errors import LangError
from helpers import compile_ir, run, run_path

# Shared prelude: imports plus a printer that dumps a string's bytes between
# pipes (the buffer is not NUL-terminated, so print exactly `length` bytes).
PRELUDE = """
import "std/format";
import "std/string";
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
            let s: struct string;
            string_init(s);
            format(s, -4 as int8, "");   string_push(s, ' ');
            format(s, -4 as int16, "");  string_push(s, ' ');
            format(s, -4 as int32, "");  string_push(s, ' ');
            format(s, -4 as int64, "");  string_push(s, ' ');
            format(s, 123456789 as int32, "");
            show(s);
            string_destroy(s);
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
            let s: struct string;
            string_init(s);
            format(s, 250 as uint8, "");   string_push(s, ' ');
            format(s, 65535 as uint16, ""); string_push(s, ' ');
            format(s, 18446744073709551615 as uint64, "");
            show(s);
            string_destroy(s);
            return 0;
        }
        """
    )
    assert capfd.readouterr().out == "|250 65535 18446744073709551615|\n"


def test_integer_modifiers(capfd):
    # "x"/"X" render hex, "b" binary, "p" pointer-style. A negative value
    # renders sign-and-magnitude -- the modifier applies to |value|, so its
    # "x" is '-' plus the magnitude's digits, never a two's-complement
    # pattern -- pinned as the intended behavior.
    run(
        PRELUDE
        + """
        fn main() -> int32 {
            let s: struct string;
            string_init(s);
            format(s, 255 as uint8, "x");   string_push(s, ' ');
            format(s, 255 as int64, "X"); string_push(s, ' ');
            format(s, -4 as int32, "x");    string_push(s, ' ');
            format(s, 5 as int32, "b");     string_push(s, ' ');
            format(s, 42 as int64, "p");
            show(s);
            string_destroy(s);
            return 0;
        }
        """
    )
    assert capfd.readouterr().out == "|ff FF -4 101 0x2a|\n"


def test_integer_width_and_zero_padding(capfd):
    # The [0][width][base] modifier grammar. A space width counts the whole
    # field (sign and 0x included); a zero width counts the digits alone,
    # the sign and 0x prefix sitting outside the zeros. int64's minimum
    # renders exactly: its magnitude is taken by two's-complement negation
    # in uint64 space, so the value with no int64 magnitude still works.
    run(
        PRELUDE
        + """
        fn main() -> int32 {
            let s: struct string;
            string_init(s);
            format(s, 255 as int32, "8x");    string_push(s, '/');
            format(s, 255 as int32, "08x");   string_push(s, '/');
            format(s, -42 as int32, "08p");   string_push(s, '/');
            format(s, -42 as int64, "8");     string_push(s, '/');
            format(s, -9223372036854775807 as int64 - 1, "x");
            show(s);
            string_destroy(s);
            return 0;
        }
        """
    )
    expected = "      ff/000000ff/-0x0000002a/     -42/-8000000000000000"
    assert capfd.readouterr().out == f"|{expected}|\n"


def test_string_field_widths_and_null(capfd):
    # The [N][s][N] string grammar: digits before the s right-align the
    # text in an N-wide field (a bare N works too), digits after it
    # left-align; text at or past the width appends unpadded. char*
    # delegates through a strlen-measured slice -- both string members
    # share the grammar -- and a null char* renders (null), not UB.
    run(
        PRELUDE
        + """
        fn main() -> int32 {
            let s: struct string;
            string_init(s);
            format(s, "hi", "6s");         string_push(s, '/');
            format(s, "hi", "s6");         string_push(s, '/');
            format(s, "hi", "6");          string_push(s, '/');
            format(s, "hi", "1s");         string_push(s, '/');
            format(s, null as char*, "");
            show(s);
            string_destroy(s);
            return 0;
        }
        """
    )
    assert capfd.readouterr().out == "|    hi/hi    /    hi/hi/(null)|\n"


def test_modifier_from_a_string_borrows_in(capfd):
    # A modifier built at runtime is a `string`; it borrows into the
    # slice<char> parameter explicitly, `m as slice<char>`.
    run(
        PRELUDE
        + """
        fn main() -> int32 {
            let m: struct string;
            string_init(m, "x");
            let s: struct string;
            string_init(s);
            format(s, 255 as int32, m as slice<char>);
            show(s);
            string_destroy(s);
            string_destroy(m);
            return 0;
        }
        """
    )
    assert capfd.readouterr().out == "|ff|\n"


def test_float64_renders_fixed_point(capfd):
    run(
        PRELUDE
        + """
        fn main() -> int32 {
            let s: struct string;
            string_init(s);
            format(s, 3.5, "");
            show(s);
            string_destroy(s);
            return 0;
        }
        """
    )
    assert capfd.readouterr().out == "|3.500000|\n"


def test_bool_modifiers(capfd):
    # Default true/false; "y" renders y/n, "yes" renders yes/no.
    run(
        PRELUDE
        + """
        fn main() -> int32 {
            let s: struct string;
            string_init(s);
            format(s, true, "");  string_push(s, ' ');
            format(s, false, ""); string_push(s, ' ');
            format(s, true, "y");     string_push(s, ' ');
            format(s, false, "y");    string_push(s, ' ');
            format(s, true, "yes");   string_push(s, ' ');
            format(s, false, "yes");
            show(s);
            string_destroy(s);
            return 0;
        }
        """
    )
    assert capfd.readouterr().out == "|true false y n yes no|\n"


def test_char_and_c_string(capfd):
    # A bare string literal as the VALUE decays to char* and lands on the
    # char* member -- pinned: it renders its bytes rather than colliding
    # with slice<char>.
    run(
        PRELUDE
        + """
        fn main() -> int32 {
            let s: struct string;
            string_init(s);
            format(s, 'Z', "");
            format(s, "-hello" as char*, "");
            format(s, "-lit", "");
            show(s);
            string_destroy(s);
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
            let s: struct string;
            string_init(s);
            let a: int32[3] = [10, 255, 3];
            format(s, a as slice<int32>, ""); string_push(s, ' ');
            format(s, a as slice<int32>, "x");
            show(s);
            string_destroy(s);
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
            let s: struct string;
            string_init(s);
            let a: int32[3] = [1, 2, 3];
            let b: int32[2] = [4, 5];
            let rows: slice<int32>[2] = [a as slice<int32>, b as slice<int32>];
            format(s, rows as slice<slice<int32>>, "");
            show(s);
            string_destroy(s);
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
            let s: struct string;
            string_init(s);
            let word = "hi";
            format(s, word as slice<char>, "");
            show(s);
            string_destroy(s);
            return 0;
        }
        """
    )
    assert capfd.readouterr().out == "|hi|\n"


def test_slice_of_c_strings_renders_quoted_list(capfd):
    # The concrete slice<char*> member beats the generic list-renderer, so
    # C strings render as a quoted bracketed list rather than unquoted
    # through the char* member. The modifier is ignored ("x" changes
    # nothing), and a single element gets no separator.
    run(
        PRELUDE
        + """
        fn main() -> int32 {
            let s: struct string;
            string_init(s);
            let cmds: char*[2] = ["ls", "cat"];
            format(s, cmds as slice<char*>, ""); string_push(s, ' ');
            format(s, cmds as slice<char*>, "x");  string_push(s, ' ');
            let one: char*[1] = ["rm"];
            format(s, one as slice<char*>, "");
            show(s);
            string_destroy(s);
            return 0;
        }
        """
    )
    expected = '["ls", "cat"] ["ls", "cat"] ["rm"]'
    assert capfd.readouterr().out == f"|{expected}|\n"


def test_nested_c_string_slices_compose_with_generic_renderer(capfd):
    # slice<slice<char*>> lands on the generic list-renderer, whose per
    # element calls re-enter the set and hit the concrete slice<char*>
    # member: quoted lists nest inside a plain bracketed list.
    run(
        PRELUDE
        + """
        fn main() -> int32 {
            let s: struct string;
            string_init(s);
            let a: char*[2] = ["ls", "cat"];
            let b: char*[1] = ["rm"];
            let rows: slice<char*>[2] = [a as slice<char*>, b as slice<char*>];
            format(s, rows as slice<slice<char*>>, "");
            show(s);
            string_destroy(s);
            return 0;
        }
        """
    )
    expected = '[["ls", "cat"], ["rm"]]'
    assert capfd.readouterr().out == f"|{expected}|\n"


def test_typename_fallback_for_unformattable_types(capfd):
    # A type no member covers falls to the unbounded format<T>, which renders
    # the type's name in angle brackets instead of a value.
    run(
        PRELUDE
        + """
        fn main() -> int32 {
            let s: struct string;
            string_init(s);
            let p: uint8* = null;
            format(s, p, "");
            show(s);
            string_destroy(s);
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
                let s: struct string;
                string_init(s);
                format(s, 42, "");
                return 0;
            }
            """
        )


def test_user_overload_joins_the_set_cross_module(tmp_path, capfd):
    # The open-sets protocol move: a user module makes its own type printable
    # by declaring one format overload -- taking the protocol's slice<char>
    # modifier -- and it joins the stdlib set at import merge, including
    # recursing back into the set for its fields.
    (tmp_path / "point.mc").write_text(
        """
        import "std/format";
        import "std/string";

        struct point { x: int32; y: int32; }

        fn format(mut str: string, value: struct point*, const modifier: slice<char>) {
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
        import "std/format";
        import "std/string";
        import "libc/stdio";

        fn show(const s: string) {
            printf("|%.*s|\\n", s.length as int32, s.data);
        }

        fn main() -> int32 {
            let s: struct string;
            string_init(s);
            let p = struct point { x = 3, y = 255 };
            format(s, &p, ""); string_push(s, ' ');
            format(s, &p, "x");
            show(s);
            string_destroy(s);
            return 0;
        }
        """
    )
    assert run_path(main) == 0
    assert capfd.readouterr().out == "|(3, 255) (3, ff)|\n"
