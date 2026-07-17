"""lib/std/slice: `constructor`, the char-slice `format` entry point, and
slice `equals`.

Pins the WIP surface of lib/std/slice.mc (and the string.mc members that
pair with it), reached through `import "std/slice"` -- slice.mc imports
string.mc and declares `slice::format`, so that one import brings up the
whole surface. (Importing `std/string` alone does NOT: `string::format`
delegates to `slice::format`, which lives in slice.mc, so the char-slice
formatter must be in scope.)

Three families:

- `fn slice<T>::constructor(self: &slice<T>, @nonnull data: T*, length: S)
  where S: int64 | uint64 | int32 | uint32` -- builds a view from a raw
  pointer and an integer length (stored as `uint64`). Constructor sugar
  `slice<T>(data, length)` desugars to the family call; the head needs an
  explicit element type because `slice` is generic with no defaults.
- `fn slice::format(@format const self: slice<const char>, args...) -> own
  string` -- a format-string entry point: `"{}"` holes are filled from the
  variadic args through the `std/format` overload set, `{modifier}` carries
  a format modifier, `{{`/`}}` escape a literal brace, and the result is an
  owned string the caller adopts. `string::format` is the `string`-receiver
  delegate. A bare string-literal receiver reaches it via the
  string-literal-to-`slice<const char>` dot-call adaptation.
- `fn slice<T>::equals(const self: &slice<T>, const str: slice<T>) -> bool`
  -- element-by-element slice equality (different lengths never equal), plus
  the `slice<const char>`-vs-`string` overload that bridges to `string`.
"""

import pytest

from mcc.errors import LangError
from helpers import compile_ir, run


# --- slice<T>::constructor: pointer + length view construction ----------------


def test_slice_constructor_sugar_sets_data_and_length():
    assert run(
        """
        import "std/slice";
        fn main() -> int32 {
            let xs: int32[3];
            xs[0] = 10; xs[1] = 20; xs[2] = 30;
            let s = slice<int32>(&xs[0], 3);
            return (s.length as int32) + s[0] + s[2];   // 3 + 10 + 30
        }
        """
    ) == 43


def test_slice_constructor_explicit_family_call():
    assert run(
        """
        import "std/slice";
        fn main() -> int32 {
            let xs: int32[3];
            xs[0] = 10; xs[1] = 20; xs[2] = 30;
            let s: slice<int32>;
            slice::constructor(s, &xs[0], 3);
            return (s.length as int32) + s[0] + s[2];
        }
        """
    ) == 43


def test_slice_constructor_write_through_hits_storage():
    assert run(
        """
        import "std/slice";
        fn main() -> int32 {
            let xs: int32[2];
            xs[0] = 1; xs[1] = 2;
            let s = slice<int32>(&xs[0], 2);
            s[1] = 99;
            return xs[1];
        }
        """
    ) == 99


def test_slice_constructor_for_in_iterates():
    assert run(
        """
        import "std/slice";
        fn main() -> int32 {
            let xs: int32[4] = [1, 2, 3, 4];
            let s = slice<int32>(&xs[0], 4);
            let total: int32 = 0;
            for v in s { total += v; }
            return total;
        }
        """
    ) == 10


def test_slice_constructor_empty_length_iterates_zero_times():
    assert run(
        """
        import "std/slice";
        fn main() -> int32 {
            let xs: int32[1] = [0];
            let s = slice<int32>(&xs[0], 0);
            let count: int32 = 0;
            for v in s { count += 1; }
            return count;
        }
        """
    ) == 0


def test_slice_constructor_accepts_each_integer_length_type():
    # S is a type group: int32, uint32, int64, and uint64 all reach the
    # family and are stored as uint64 in the view.
    assert run(
        """
        import "std/slice";
        fn main() -> int32 {
            let xs: int32[1] = [7];
            let a = slice<int32>(&xs[0], 1 as int32);
            let b = slice<int32>(&xs[0], 1 as uint32);
            let c = slice<int32>(&xs[0], 1 as int64);
            let d = slice<int32>(&xs[0], 1 as uint64);
            return a[0] + b[0] + c[0] + d[0];   // 28
        }
        """
    ) == 28


def test_slice_constructor_sub_slice_composes():
    assert run(
        """
        import "std/slice";
        fn main() -> int32 {
            let xs: int32[4] = [10, 20, 30, 40];
            let s = slice<int32>(&xs[0], 4);
            let mid = s[1:3];
            return mid[0] + (mid.length as int32);   // 20 + 2
        }
        """
    ) == 22


def test_slice_constructor_passes_as_a_function_argument():
    assert run(
        """
        import "std/slice";
        fn sum(xs: slice<int32>) -> int32 {
            let total: int32 = 0;
            for v in xs { total += v; }
            return total;
        }
        fn main() -> int32 {
            let xs: int32[3] = [4, 5, 6];
            let s = slice<int32>(&xs[0], 3);
            return sum(s);
        }
        """
    ) == 15


def test_slice_constructor_char_ptr_elements():
    # The argv-shaped use case: a slice over char* elements views contiguous
    # pointer storage and indexes through to the pointees.
    assert run(
        """
        import "std/slice";
        fn main() -> int32 {
            let a = "a";
            let b = "bb";
            let argv: char*[2];
            argv[0] = a;
            argv[1] = b;
            let args = slice<char*>(&argv[0], 2);
            if (args.length != 2) { return 1; }
            if (args[0][0] != 'a') { return 2; }
            if (args[1][0] != 'b') { return 3; }
            return 0;
        }
        """
    ) == 0


def test_slice_constructor_casts_length_to_uint64():
    ir_text = compile_ir(
        """
        import "std/slice";
        fn main() -> int32 {
            let xs: int32[2];
            let n: int32 = 2;
            let s = slice<int32>(&xs[0], n);
            return s.length as int32;
        }
        """
    )
    assert (
        'define void @"slice::constructor<$0, $1: int64|uint64|int32|uint32>'
        "(&slice<$0>, $0*, $1)<int32, int32>"
    ) in ir_text
    assert 'sext i32 %"length.2" to i64' in ir_text


def test_slice_constructor_bare_head_needs_a_type_argument():
    with pytest.raises(LangError, match=r"type 'slice' takes 1 type argument, got 0"):
        compile_ir(
            """
            import "std/slice";
            fn main() -> int32 {
                let xs: int32[2];
                let s = slice(&xs[0], 2);
                return 0;
            }
            """
        )


def test_slice_constructor_rejects_non_integer_length():
    with pytest.raises(
        LangError,
        match=r"float64 is not in the type group of 'slice::constructor' "
        r"\(int64 \| uint64 \| int32 \| uint32\)",
    ):
        compile_ir(
            """
            import "std/slice";
            fn main() -> int32 {
                let xs: int32[2];
                let s = slice<int32>(&xs[0], 2.0);
                return 0;
            }
            """
        )


def test_slice_constructor_rejects_null_data():
    with pytest.raises(
        LangError,
        match=r"cannot pass a possibly-null pointer as argument 2 of "
        r"'slice::constructor': the parameter is @nonnull",
    ):
        compile_ir(
            """
            import "std/slice";
            fn main() -> int32 {
                let p: int32* = null;
                let s = slice<int32>(p, 0);
                return 0;
            }
            """
        )


def test_slice_constructor_const_element_rejects_mutable_pointer():
    # T binds from both the receiver slot and the data pointer; a mutable
    # int32* cannot instantiate slice<const int32> through the sugar head.
    with pytest.raises(
        LangError,
        match=r"conflicting types for type parameter T in call to "
        r"'slice::constructor': const int32 vs int32",
    ):
        compile_ir(
            """
            import "std/slice";
            fn main() -> int32 {
                let xs: int32[3] = [1, 2, 3];
                let s = slice<const int32>(&xs[0], 3);
                return 0;
            }
            """
        )


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
