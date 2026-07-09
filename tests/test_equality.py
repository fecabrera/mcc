"""lib/std/equality.mc: the equality protocol's baseline overload set.

One generic member so far: `equals<T>(slice<T>, slice<T>)` compares element
by element with `!=`. Different lengths are never equal; empty slices
compare equal. Open overload sets make the protocol extensible: a type
joins by adding an `equals` overload for itself in its own module.
"""

from helpers import run, run_path

PRELUDE = """
import "std/equality";
import "std/string";
"""


def test_char_slices_and_literals(capfd=None):
    # A string borrows in (`s as slice<char>`) and a literal adapts
    # directly, so `equals(s, "hi")` is the natural call shape.
    assert (
        run(
            PRELUDE
            + """
            fn main() -> int32 {
                let s: struct string;
                string_init(s, "hi");
                defer string_destroy(s);
                if (!equals(s as slice<char>, "hi")) return 1;
                if (equals(s as slice<char>, "ho"))  return 2;
                return 0;
            }
            """
        )
        == 0
    )


def test_different_lengths_never_equal():
    # Same prefix, different lengths: unequal in both directions.
    assert (
        run(
            PRELUDE
            + """
            fn main() -> int32 {
                let s: struct string;
                string_init(s, "hip");
                defer string_destroy(s);
                if (equals(s as slice<char>, "hi"))   return 1;
                if (equals("hi", s as slice<char>))   return 2;
                return 0;
            }
            """
        )
        == 0
    )


def test_empty_slices_compare_equal():
    assert (
        run(
            PRELUDE
            + """
            fn main() -> int32 {
                let s: struct string;
                string_init(s);
                defer string_destroy(s);
                if (!equals(s as slice<char>, "")) return 1;
                if (equals(s as slice<char>, "x")) return 2;
                return 0;
            }
            """
        )
        == 0
    )


def test_generic_over_element_types():
    # The member is generic: any element type supporting != works, and both
    # sides must view the same element type.
    assert (
        run(
            PRELUDE
            + """
            fn main() -> int32 {
                let a: int32[3] = [1, 2, 3];
                let b: int32[3] = [1, 2, 3];
                let c: int32[3] = [1, 9, 3];
                if (!equals(a as slice<int32>, b as slice<int32>)) return 1;
                if (equals(a as slice<int32>, c as slice<int32>))  return 2;
                return 0;
            }
            """
        )
        == 0
    )


def test_sub_slices_compare_by_view():
    # Sub-slicing composes: a view over the matching region compares equal.
    assert (
        run(
            PRELUDE
            + """
            fn main() -> int32 {
                let nums = [9, 1, 2, 9] as slice<const int32>;
                let want = [1, 2] as slice<const int32>;
                if (!equals(nums[1:3], want)) return 1;
                if (equals(nums[0:2], want))  return 2;
                return 0;
            }
            """
        )
        == 0
    )


def test_user_overload_joins_the_set_cross_module(tmp_path):
    # The open-sets protocol move: a user module adds an equals overload for
    # its own type and it joins the stdlib set at import merge.
    (tmp_path / "point.mc").write_text(
        """
        import "std/equality";

        struct point { x: int32; y: int32; }

        fn equals(a: struct point*, b: struct point*) -> bool {
            return a->x == b->x and a->y == b->y;
        }
        """
    )
    main = tmp_path / "main.mc"
    main.write_text(
        """
        import "point";
        import "std/equality";

        fn main() -> int32 {
            let a = struct point { x = 1, y = 2 };
            let b = struct point { x = 1, y = 2 };
            let c = struct point { x = 1, y = 3 };
            if (!equals(&a, &b)) return 1;
            if (equals(&a, &c))  return 2;
            // The stdlib member still resolves; one side must fix T (two
            // bare literals stay char* and match no member).
            if (!equals("hi" as slice<char>, "hi")) return 3;
            return 0;
        }
        """
    )
    assert run_path(main) == 0
