"""lib/std/slice: the char-slice `format` entry point and slice `equals`.

Pins the WIP surface of lib/std/slice.mc (and the string.mc members that
pair with it), reached through `import "std/slice"` -- slice.mc imports
string.mc and declares `slice::format`, so that one import brings up the
whole surface. (Importing `std/string` alone does NOT: `string::format`
delegates to `slice::format`, which lives in slice.mc, so the char-slice
formatter must be in scope.)

Two families:

- `fn slice::format(@format const self: slice<const char>, args...) -> own
  string` -- a format-string entry point: `"{}"` holes are filled from the
  variadic args through the `std/format` overload set, `{modifier}` carries
  a format modifier, `{{`/`}}` escape a literal brace, and the result is an
  owned string the caller adopts. `string::format` is the `string`-receiver
  delegate. A bare string-literal receiver reaches it via the
  string-literal-to-`slice<const char>` dot-call adaptation.
- `fn slice<T>::equals(const self: slice<T>, const str: slice<T>) -> bool`
  -- element-by-element slice equality (different lengths never equal), plus
  the `slice<const char>`-vs-`string` overload that bridges to `string`.
"""

import pytest

from helpers import run


# --- slice::format: the char-slice format entry point ---------------------------

def test_format_passes_plain_text_through(capfd):
    assert run(
        """
        import "std/io";
        import "std/slice";
        fn main() -> int32 {
            writeln("hello".format());
            return 0;
        }
        """
    ) == 0
    assert capfd.readouterr().out == "hello\n"


def test_format_fills_a_hole_from_an_argument(capfd):
    assert run(
        """
        import "std/io";
        import "std/slice";
        fn main() -> int32 {
            writeln("n={}".format(42 as int32));
            return 0;
        }
        """
    ) == 0
    assert capfd.readouterr().out == "n=42\n"


def test_format_fills_holes_in_order(capfd):
    assert run(
        """
        import "std/io";
        import "std/slice";
        fn main() -> int32 {
            writeln("{}+{}".format(1 as int32, 2 as int32));
            return 0;
        }
        """
    ) == 0
    assert capfd.readouterr().out == "1+2\n"


def test_format_carries_a_modifier(capfd):
    # `{x}` passes the "x" modifier through to the std/format overload, so an
    # int renders in hex.
    assert run(
        """
        import "std/io";
        import "std/slice";
        fn main() -> int32 {
            writeln("{x}".format(255 as int32));
            return 0;
        }
        """
    ) == 0
    assert capfd.readouterr().out == "ff\n"


def test_format_escapes_doubled_braces(capfd):
    # `{{` and `}}` emit a single literal brace each.
    assert run(
        """
        import "std/io";
        import "std/slice";
        fn main() -> int32 {
            writeln("{{}}".format());
            return 0;
        }
        """
    ) == 0
    assert capfd.readouterr().out == "{}\n"


def test_format_skips_holes_past_the_argument_count(capfd):
    # A hole with no matching argument produces nothing (the surrounding text
    # is still emitted): `a{}b{}c` with one arg is "a1bc".
    assert run(
        """
        import "std/io";
        import "std/slice";
        fn main() -> int32 {
            writeln("a{}b{}c".format(1 as int32));
            return 0;
        }
        """
    ) == 0
    assert capfd.readouterr().out == "a1bc\n"


def test_format_renders_a_string_argument(capfd):
    assert run(
        """
        import "std/io";
        import "std/slice";
        fn main() -> int32 {
            writeln("[{}]".format("hi"));
            return 0;
        }
        """
    ) == 0
    assert capfd.readouterr().out == "[hi]\n"


def test_format_renders_a_bool_argument(capfd):
    assert run(
        """
        import "std/io";
        import "std/slice";
        fn main() -> int32 {
            writeln("{}".format(true));
            return 0;
        }
        """
    ) == 0
    assert capfd.readouterr().out == "true\n"


def test_string_literal_receiver_reaches_format(capfd):
    # The headline interplay: a bare string literal adapts to
    # slice<const char> so `.format(...)` resolves with no explicit borrow.
    assert run(
        """
        import "std/io";
        import "std/slice";
        fn main() -> int32 {
            let s = "{}{}".format("hello", "world");
            writeln(s);
            return 0;
        }
        """
    ) == 0
    assert capfd.readouterr().out == "helloworld\n"


def test_string_receiver_format_delegates(capfd):
    # `string::format` is the string-receiver delegate to the slice formatter.
    assert run(
        """
        import "std/io";
        import "std/slice";
        fn main() -> int32 {
            let fmt = string("v={}");
            writeln(fmt.format(9 as int32));
            return 0;
        }
        """
    ) == 0
    assert capfd.readouterr().out == "v=9\n"


def test_format_of_an_empty_string_is_empty(capfd):
    assert run(
        """
        import "std/io";
        import "std/slice";
        fn main() -> int32 {
            writeln("".format());
            return 0;
        }
        """
    ) == 0
    assert capfd.readouterr().out == "\n"


def test_format_ignores_arguments_with_no_holes(capfd):
    # A format string with no `{}` consumes no arguments; extras are dropped.
    assert run(
        """
        import "std/io";
        import "std/slice";
        fn main() -> int32 {
            writeln("hi".format(1 as int32));
            return 0;
        }
        """
    ) == 0
    assert capfd.readouterr().out == "hi\n"


def test_format_results_are_independent_owned_strings(capfd):
    # Each format call returns its own `own string`; two coexist and are each
    # adopted/cleaned up by their binding let.
    assert run(
        """
        import "std/io";
        import "std/slice";
        fn main() -> int32 {
            let a = "{}".format(1 as int32);
            let b = "{}".format(2 as int32);
            writeln(a);
            writeln(b);
            return 0;
        }
        """
    ) == 0
    assert capfd.readouterr().out == "1\n2\n"


# --- slice<T>::equals: element-by-element slice equality ------------------------

def test_slice_equals_true_for_equal_runs():
    assert run(
        """
        import "std/slice";
        fn main() -> int32 {
            let a: int32[3] = [1, 2, 3];
            let b: int32[3] = [1, 2, 3];
            return (a as slice<int32>).equals(b as slice<int32>) ? 0 : 1;
        }
        """
    ) == 0


def test_slice_equals_false_on_a_differing_element():
    assert run(
        """
        import "std/slice";
        fn main() -> int32 {
            let a: int32[3] = [1, 2, 3];
            let b: int32[3] = [1, 9, 3];
            return (a as slice<int32>).equals(b as slice<int32>) ? 1 : 0;
        }
        """
    ) == 0


def test_slice_equals_false_on_differing_length():
    assert run(
        """
        import "std/slice";
        fn main() -> int32 {
            let a: int32[3] = [1, 2, 3];
            let b: int32[2] = [1, 2];
            return (a as slice<int32>).equals(b as slice<int32>) ? 1 : 0;
        }
        """
    ) == 0


def test_slice_equals_true_for_two_empty_slices():
    # Same length (zero) and no differing element: empty compares equal.
    assert run(
        """
        import "std/slice";
        fn main() -> int32 {
            let a: int32[1] = [0];
            let e = (a as slice<int32>)[0:0];
            return e.equals((a as slice<int32>)[0:0]) ? 0 : 1;
        }
        """
    ) == 0


# --- slice<const char> vs string ------------------------------------------------

def test_char_slice_equals_string_true():
    assert run(
        """
        import "std/slice";
        fn main() -> int32 {
            let s = string("hi");
            return ("hi" as slice<const char>).equals(s) ? 0 : 1;
        }
        """
    ) == 0


def test_char_slice_equals_string_false():
    assert run(
        """
        import "std/slice";
        fn main() -> int32 {
            let s = string("ho");
            return ("hi" as slice<const char>).equals(s) ? 1 : 0;
        }
        """
    ) == 0
