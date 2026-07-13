"""The builtin slice<T> view: the type, `.length`, indexing, `for ... in`, and
the explicit `as` borrow from an owned list<T> or a fixed array T[N]."""

import pytest

from mcc.errors import LangError
from mcc.nodes import TypeRef
from helpers import compile_ir, parse, run


def test_slice_type_parses():
    (func,) = parse("fn f(s: slice<int32>) {}").functions
    t = func.params[0][1]
    assert isinstance(t, TypeRef) and t.name == "slice"
    assert t.args == [TypeRef("int32")]
    assert str(t) == "slice<int32>"


def test_slice_is_two_words():
    # { ptr: T*; length: uint64 } -- a pointer plus a uint64.
    source = "fn main() -> int32 { return sizeof(slice<int32>) as int32; }"
    assert run(source) == 16


def test_borrow_array_length_and_index():
    source = """
    fn main() -> int32 {
        let xs: int32[4];
        xs[0] = 10; xs[1] = 20; xs[2] = 30; xs[3] = 40;
        let s = xs as slice<int32>;
        return (s.length as int32) + s[0] + s[3];   // 4 + 10 + 40
    }
    """
    assert run(source) == 54


def test_index_write_through_slice_hits_array():
    source = """
    fn main() -> int32 {
        let xs: int32[3];
        xs[0] = 1; xs[1] = 2; xs[2] = 3;
        let s = xs as slice<int32>;
        s[1] = 99;            // writes through the borrow
        return xs[1];
    }
    """
    assert run(source) == 99


def test_borrow_list_reads_data_and_length():
    source = """
    import "std/list";
    fn main() -> int32 {
        let xs = list<int32>(4);
        xs.push(7);
        xs.push(8);
        xs.push(9);
        let s = xs as slice<int32>;
        let got = (s.length as int32) + s[0] + s[1] + s[2];   // 3 + 24
        return got;
    }
    """
    assert run(source) == 27


def test_for_in_slice_value():
    source = """
    fn main() -> int32 {
        let xs: int32[5];
        let i: int32 = 0;
        while (i < 5) { xs[i] = i + 1; i = i + 1; }
        let total: int32 = 0;
        for v in xs as slice<int32> { total = total + v; }
        return total;   // 1+2+3+4+5
    }
    """
    assert run(source) == 15


def test_for_in_slice_pointer():
    source = """
    fn main() -> int32 {
        let xs: int32[3];
        xs[0] = 2; xs[1] = 4; xs[2] = 6;
        let s = xs as slice<int32>;
        let total: int32 = 0;
        for v in &s { total = total + v; }
        return total;   // 12
    }
    """
    assert run(source) == 12


def test_for_in_slice_break_and_continue():
    source = """
    fn main() -> int32 {
        let xs: int32[6];
        let i: int32 = 0;
        while (i < 6) { xs[i] = i; i = i + 1; }   // 0 1 2 3 4 5
        let s = xs as slice<int32>;
        let total: int32 = 0;
        for v in s {
            if (v == 1) { continue; }   // skip 1
            if (v == 4) { break; }      // stop before 4
            total = total + v;          // 0 + 2 + 3
        }
        return total;
    }
    """
    assert run(source) == 5


def test_slice_passed_by_value_to_function(capfd):
    source = """
    import "libc/stdio";
    fn first(s: slice<int32>) -> int32 { return s[0]; }
    fn main() -> int32 {
        let xs: int32[2];
        xs[0] = 41; xs[1] = 0;
        printf("%d\\n", first(xs as slice<int32>));
        return 0;
    }
    """
    assert run(source) == 0
    assert capfd.readouterr().out == "41\n"


def test_empty_slice_iterates_zero_times():
    source = """
    import "std/list";
    fn main() -> int32 {
        let xs = list<int32>(4);                 // length 0
        let s = xs as slice<int32>;
        let count: int32 = 0;
        for v in s { count = count + 1; }
        return count;
    }
    """
    assert run(source) == 0


def test_slice_of_struct_elements():
    source = """
    struct point { x: int32; y: int32; }
    fn main() -> int32 {
        let pts: struct point[2];
        pts[0].x = 3; pts[0].y = 4;
        pts[1].x = 5; pts[1].y = 6;
        let s = pts as slice<struct point>;
        let total: int32 = 0;
        for p in s { total = total + p.x + p.y; }   // 3+4+5+6
        return total;
    }
    """
    assert run(source) == 18


def test_slice_of_void_is_rejected():
    with pytest.raises(LangError, match="cannot make a slice of void"):
        compile_ir("fn f(s: slice<void>) {}")


def test_slice_wrong_arity_is_rejected():
    with pytest.raises(LangError, match="'slice' takes 1 type argument"):
        compile_ir("fn f(s: slice<int32, int32>) {}")


def test_struct_cannot_shadow_slice():
    with pytest.raises(LangError, match="type 'slice' already defined"):
        compile_ir("struct slice<T> { p: T*; }")


def test_borrow_element_type_mismatch_is_rejected():
    with pytest.raises(LangError, match="element type"):
        compile_ir(
            """
            fn main() -> int32 {
                let xs: int64[2];
                let s = xs as slice<int32>;   // int64 elements, not int32
                return 0;
            }
            """
        )


def test_borrow_non_container_is_rejected():
    with pytest.raises(LangError, match="cannot borrow"):
        compile_ir(
            """
            fn main() -> int32 {
                let n: int32 = 5;
                let s = n as slice<int32>;
                return 0;
            }
            """
        )


def test_layout_twin_without_extends_borrow_rejected():
    # `buf` is laid out exactly like slice<char> ({T*, integer}) but does not
    # `extends slice<char>`, so it no longer borrows: the borrow follows the
    # declared `extends` lineage, not a coincidentally matching shape.
    with pytest.raises(LangError, match="cannot borrow"):
        compile_ir(
            """
            struct buf { data: char*; length: uint64; }
            fn main() -> int32 {
                let b: struct buf;
                let s = b as slice<char>;
                return 0;
            }
            """
        )


def test_list_borrows_to_both_mutable_and_const_slice():
    # The nominal borrow still spans the element-const axis: list<T> `extends`
    # slice<T>, so the same owned list borrows to slice<T> and -- with const
    # stripped off the target element to reach the same declared base --
    # slice<const T>.
    source = """
    import "std/list";
    fn main() -> int32 {
        let xs = list<int32>(4);
        xs.push(10);
        xs.push(20);
        let m = xs as slice<int32>;
        let c = xs as slice<const int32>;
        let got = m[0] + c[1];   // 10 + 20
        return got;
    }
    """
    assert run(source) == 30


# ----------------------------------------------- the element-const axis (Stage 3)


def test_const_slice_type_parses():
    (func,) = parse("fn f(s: slice<const uint8>) {}").functions
    t = func.params[0][1]
    assert t.name == "slice"
    assert t.args == [TypeRef("uint8", const=True)]
    assert str(t) == "slice<const uint8>"


def test_read_and_iterate_const_slice():
    source = """
    fn sum(s: slice<const int32>) -> int32 {
        let total: int32 = 0;
        for v in s { total = total + v; }
        return total;
    }
    fn main() -> int32 {
        let xs: int32[3];
        xs[0] = 1; xs[1] = 2; xs[2] = 3;
        return sum(xs as slice<const int32>);   // 6
    }
    """
    assert run(source) == 6


def test_mutable_slice_widens_to_const_argument():
    # A mutable slice<int32> passes where slice<const int32> is expected.
    source = """
    fn first(s: slice<const int32>) -> int32 { return s[0]; }
    fn main() -> int32 {
        let xs: int32[2];
        xs[0] = 42; xs[1] = 0;
        let m = xs as slice<int32>;
        return first(m);
    }
    """
    assert run(source) == 42


def test_mutable_slice_widens_on_assignment():
    source = """
    fn main() -> int32 {
        let xs: int32[2];
        xs[0] = 7; xs[1] = 0;
        let m = xs as slice<int32>;
        let r: slice<const int32> = m;   // implicit widen
        return r[0];
    }
    """
    assert run(source) == 7


def test_write_through_const_slice_is_rejected():
    with pytest.raises(LangError, match="read-only slice<const T>"):
        compile_ir(
            """
            fn main() -> int32 {
                let xs: int32[2];
                xs[0] = 1; xs[1] = 2;
                let s = xs as slice<const int32>;
                s[0] = 9;
                return 0;
            }
            """
        )


def test_dropping_const_in_borrow_is_rejected():
    with pytest.raises(LangError, match="read-only.*as the mutable"):
        compile_ir(
            """
            fn bad(s: slice<const int32>) -> int32 {
                let m = s as slice<int32>;   // would reopen a write path
                return 0;
            }
            """
        )


def test_const_parameter_borrows_only_read_only():
    # A const parameter is read-only, so it cannot borrow to a mutable slice.
    with pytest.raises(LangError, match="read-only.*as the mutable"):
        compile_ir(
            """
            fn f(const xs: int32[3]) -> int32 {
                let s = xs as slice<int32>;
                return 0;
            }
            """
        )


def test_const_parameter_borrows_to_const_slice():
    # ...but borrowing it as a read-only slice is fine.
    compile_ir(
        """
        fn f(const xs: int32[3]) -> int32 {
            let s = xs as slice<const int32>;
            return s[0];
        }
        """
    )


def test_element_read_from_const_slice_is_mutable():
    # A loaded element is an independent copy, so it is freely assignable.
    source = """
    fn main() -> int32 {
        let xs: int32[2];
        xs[0] = 10; xs[1] = 20;
        let s = xs as slice<const int32>;
        let c = s[0];
        c = c + 5;
        return c;   // 15
    }
    """
    assert run(source) == 15


def test_for_variable_over_const_slice_is_mutable():
    source = """
    fn main() -> int32 {
        let xs: int32[3];
        xs[0] = 1; xs[1] = 2; xs[2] = 3;
        let s = xs as slice<const int32>;
        let total: int32 = 0;
        for v in s {
            v = v * 2;            // the loop variable is a mutable copy
            total = total + v;
        }
        return total;            // 2+4+6
    }
    """
    assert run(source) == 12


def test_const_char_slice_drops_nul_terminator():
    # A read-only text view of a string still drops the trailing NUL.
    source = """
    fn main() -> int32 {
        let s = "hello" as slice<const char>;
        return s.length as int32;   // 5, not 6
    }
    """
    assert run(source) == 5


def test_const_local_cannot_be_reassigned():
    with pytest.raises(LangError, match="read-only variable"):
        compile_ir("fn main() -> int32 { let x: const int32 = 5; x = 6; return 0; }")


def test_const_local_initializes_from_mutable_value():
    source = "fn main() -> int32 { let x: const int32 = 41; return x + 1; }"
    assert run(source) == 42


def test_const_slice_element_type_mismatch_is_rejected():
    with pytest.raises(LangError, match="element type"):
        compile_ir(
            """
            fn main() -> int32 {
                let xs: int64[2];
                let s = xs as slice<const int32>;   // int64 elements, not int32
                return 0;
            }
            """
        )


def test_generic_infers_through_const_slice_element():
    # A `const T` parameter pattern infers T from the element's underlying type,
    # consistent with another parameter that fixes T mutably (here via list<T>*).
    source = """
    import "std/list";
    fn copy_first<T>(@nonnull self: struct list<T>*, const arr: slice<const T>) {
        list<T>::constructor(self, arr.length);
        for el in arr { self.push(el); }
    }
    fn main() -> int32 {
        let xs: int32[3];
        xs[0] = 4; xs[1] = 5; xs[2] = 6;
        let dst: struct list<int32>;
        copy_first(&dst, xs as slice<const int32>);   // T = int32, not const int32
        let view = dst as slice<int32>;
        let got = (view.length as int32) + view[0];
        list<int32>::destructor(&dst);
        return got;   // 3 + 4
    }
    """
    assert run(source) == 7


def test_generic_const_slice_accepts_mutable_argument():
    # A mutable slice<int32> widens into a `slice<const T>` parameter, binding
    # T = int32 from the underlying element.
    source = """
    fn first<T>(s: slice<const T>) -> T { return s[0]; }
    fn main() -> int32 {
        let xs: int32[2];
        xs[0] = 99; xs[1] = 0;
        return first(xs as slice<int32>);
    }
    """
    assert run(source) == 99


def test_slice_and_const_slice_are_distinct_types():
    # slice<T> and slice<const T> share a layout but are different types: the
    # const form will not coerce back to the mutable one.
    with pytest.raises(LangError, match="read-only.*as the mutable"):
        compile_ir(
            """
            fn take_mut(s: slice<int32>) -> int32 { return s[0]; }
            fn f(s: slice<const int32>) -> int32 { return take_mut(s as slice<int32>); }
            """
        )


# ----------------------------------------- string-literal borrow-in (Stage 4)


def test_string_literal_adapts_to_const_char_slice_argument():
    # A string literal adapts to a slice<const char> parameter without an `as`;
    # the borrow drops the trailing NUL (the text).
    source = """
    fn count(s: slice<const char>) -> int32 { return s.length as int32; }
    fn main() -> int32 { return count("hello"); }   // 5, not 6
    """
    assert run(source) == 5


def test_string_literal_adapts_to_mutable_char_slice_argument():
    source = """
    fn count(s: slice<char>) -> int32 { return s.length as int32; }
    fn main() -> int32 { return count("hello world"); }   // 11
    """
    assert run(source) == 11


def test_string_literal_adapts_in_let():
    source = """
    fn main() -> int32 {
        let m: slice<char> = "hello world";
        let c: slice<const char> = "hi";
        return (m.length as int32) * 100 + (c.length as int32);   // 1102
    }
    """
    assert run(source) == 1102


def test_string_literal_adapts_in_return():
    source = """
    fn label() -> slice<const char> { return "abcd"; }
    fn main() -> int32 { return label().length as int32; }   // 4
    """
    assert run(source) == 4


def test_adapted_slice_indexes_the_text():
    source = """
    fn first(s: slice<const char>) -> int32 { return s[0] as int32; }
    fn main() -> int32 { return first("A"); }   // 65
    """
    assert run(source) == 65


def test_string_literal_does_not_adapt_to_uint8_slice():
    # A string literal is char[N], so it does not adapt to a byte slice; that
    # would need an explicit, raw view.
    with pytest.raises(LangError, match="expected slice<const uint8>"):
        compile_ir(
            """
            fn n(s: slice<const uint8>) -> int32 { return 0; }
            fn main() -> int32 { return n("hi"); }
            """
        )


def test_typed_value_still_needs_explicit_borrow():
    # Only literals adapt; a typed char array still converts with `as`.
    with pytest.raises(LangError, match="expected slice<const char>, got char\\*"):
        compile_ir(
            """
            fn n(s: slice<const char>) -> int32 { return 0; }
            fn main() -> int32 {
                let owned = "hi";        // char[3]
                return n(owned);         // no implicit borrow of a typed value
            }
            """
        )


def test_string_literal_adapts_to_const_slice_parameter():
    # A `const slice<const char>` parameter is passed by hidden reference; a
    # string literal must still adapt (the borrowed view is spilled to a temp).
    source = """
    fn shout(const s: slice<const char>) -> int32 { return s.length as int32; }
    fn main() -> int32 { return shout("hello world"); }   // 11
    """
    assert run(source) == 11


# ------------------------------------ ternaries of string literals (Stage 4)


def test_ternary_of_string_literals_adapts_at_argument():
    # A ternary whose arms are both string literals adapts to a char slice the
    # way a bare literal does: each arm borrows in its own branch, so the
    # merged slice carries the chosen literal's own length.
    source = """
    fn count(s: slice<char>) -> int32 { return s.length as int32; }
    fn main() -> int32 { return count(false ? "y" : "yes"); }   // 3
    """
    assert run(source) == 3


def test_ternary_of_string_literals_adapts_to_const_slice_parameter():
    # The hidden-reference spill applies to the merged view too.
    source = """
    fn count(const s: slice<const char>) -> int32 { return s.length as int32; }
    fn main() -> int32 { return count(true ? "ab" : "c"); }   // 2
    """
    assert run(source) == 2


def test_ternary_of_string_literals_adapts_in_let():
    source = """
    fn pick(flag: bool) -> int32 {
        let s: slice<char> = flag ? "y" : "yes";
        return s.length as int32;
    }
    fn main() -> int32 { return pick(true) * 10 + pick(false); }   // 13
    """
    assert run(source) == 13


def test_ternary_of_string_literals_adapts_in_return():
    source = """
    fn label(flag: bool) -> slice<const char> { return flag ? "on" : "off"; }
    fn main() -> int32 {
        return (label(true).length * 10 + label(false).length) as int32;   // 23
    }
    """
    assert run(source) == 23


def test_nested_ternary_of_string_literals_adapts():
    source = """
    fn name(n: int32) -> int32 {
        let s: slice<char> = n == 0 ? "zero" : n == 1 ? "one" : "many";
        return s.length as int32;
    }
    fn main() -> int32 { return name(0) * 100 + name(1) * 10 + name(2); }   // 434
    """
    assert run(source) == 434


def test_ternary_borrow_distributes_over_array_arms():
    # An explicit borrow distributes the same way: each owned-array arm borrows
    # in its own branch, keeping its static length.
    source = """
    fn main() -> int32 {
        let a: char[3] = "hi";
        let b: char[6] = "hello";
        let flag = false;
        let v = (flag ? a : b) as slice<char>;
        return v.length as int32;   // 5
    }
    """
    assert run(source) == 5


def test_ternary_with_non_literal_arm_does_not_adapt():
    # Only literals adapt: one typed arm makes the whole ternary a char*.
    with pytest.raises(LangError, match="expected slice<const char>, got char\\*"):
        compile_ir(
            """
            fn n(s: slice<const char>) -> int32 { return 0; }
            fn main() -> int32 {
                let owned = "hi";            // char[3]
                return n(true ? owned : "hi");
            }
            """
        )


def test_unannotated_let_ternary_stays_char_pointer():
    # Without a slice annotation there is no expected type: both literal arms
    # decay and the ternary is a char*, as before.
    source = """
    fn main() -> int32 {
        let s = true ? "a" : "bc";
        return *s as int32;   // 'a' == 97
    }
    """
    assert run(source) == 97


# ------------------------------------ string-literal elements (Stage 4, nested)


def test_string_literal_elements_adapt_in_array_literal():
    # Each string-literal element of an array of slices adapts without an `as`,
    # borrowing its string constant's bytes with the NUL dropped.
    source = """
    fn main() -> int32 {
        let dirs: slice<char>[2] = ["bin", "usr/bin"];
        if (dirs[0].length != 3) { return 1; }   // NUL dropped
        if (dirs[1].length != 7) { return 2; }
        if (dirs[0][0] != 'b') { return 3; }
        if (dirs[1][3] != '/') { return 4; }
        return 0;
    }
    """
    assert run(source) == 0


def test_string_literal_elements_adapt_to_const_char_slice_elements():
    source = """
    fn main() -> int32 {
        let names: slice<const char>[3] = ["a", "bb", "ccc"];
        return (names[0].length + names[1].length + names[2].length) as int32;
    }
    """
    assert run(source) == 6


def test_string_literal_elements_adapt_in_nested_array():
    # The element adaptation reaches through the nested-literal recursion.
    source = """
    fn main() -> int32 {
        let grid: slice<const char>[2][2] = [["a", "bb"], ["ccc", "dddd"]];
        return (grid[0][1].length * 10 + grid[1][1].length) as int32;   // 24
    }
    """
    assert run(source) == 24


def test_string_literal_elements_mix_with_explicit_borrow():
    # A literal element adapts; an explicit `as` element keeps working beside it.
    source = """
    fn main() -> int32 {
        let both: slice<char>[2] = ["x" as slice<char>, "yz"];
        return (both[0].length * 10 + both[1].length) as int32;   // 12
    }
    """
    assert run(source) == 12


def test_static_array_of_slices_from_string_literals():
    # A @static array of slices: each element becomes a constant {pointer,
    # length} view into its string global -- safe, the pointee is a constant.
    source = """
    @static let tags: slice<const char>[3] = ["a", "bb", "ccc"];
    fn main() -> int32 {
        if (tags[2].length != 3) { return 1; }
        if (tags[1][0] != 'b') { return 2; }
        return 0;
    }
    """
    assert run(source) == 0


def test_static_scalar_slice_from_string_literal():
    # The scalar form falls out of the same constant-initializer arm.
    source = """
    @static let greeting: slice<const char> = "hello";
    fn main() -> int32 { return greeting.length as int32; }   // 5, not 6
    """
    assert run(source) == 5


def test_static_slice_initializer_is_a_constant_struct():
    # The @static initializer is a true constant: a getelementptr into the
    # string global plus the i64 byte length (NUL dropped), no runtime code.
    ir_text = compile_ir(
        """
        @static let tag: slice<const char> = "abc";
        fn main() -> int32 { return tag.length as int32; }
        """
    )
    assert "getelementptr ([4 x i8], [4 x i8]* @\".str.0\", i32 0, i32 0), i64 3" in ir_text


def test_typed_element_still_needs_explicit_borrow():
    # Only literals adapt in element position too; a typed char array element
    # still converts with `as`.
    with pytest.raises(LangError, match="array element: expected slice<char>, got char\\*"):
        compile_ir(
            """
            fn main() -> int32 {
                let owned = "hi";                    // char[3]
                let xs: slice<char>[1] = [owned];    // no implicit borrow
                return 0;
            }
            """
        )


def test_string_literal_element_does_not_adapt_to_uint8_slice():
    # A string literal is char[N]: it does not adapt to a byte-slice element.
    with pytest.raises(LangError, match="array element: expected slice<const uint8>"):
        compile_ir(
            """
            fn main() -> int32 {
                let xs: slice<const uint8>[1] = ["hi"];
                return 0;
            }
            """
        )


# --- Array literals adapt to slice<T> (Stage 1) ---


def test_array_literal_borrows_with_as():
    # The explicit spelling: `[...] as slice<T>` materializes a hidden
    # backing array in the frame and views it -- {&backing[0], count}.
    source = """
    fn main() -> int32 {
        let s = [0x10, 0x1F, 0xFF] as slice<int32>;
        return (s.length as int32) + s[0] - 16;   // 3 + 16 - 16
    }
    """
    assert run(source) == 3


def test_array_literal_adapts_in_annotated_let():
    # The implicit spelling: a slice<T> annotation adapts the literal, the
    # way a string literal adapts to slice<char>.
    source = """
    fn main() -> int32 {
        let nums: slice<int32> = [0x10, 0x1F, 0xFF];
        let total: int32 = 0;
        for v in nums { total = total + v; }
        return total - 47;   // 16 + 31 + 255 - 47 == 255
    }
    """
    assert run(source) == 255


def test_mutable_write_through_bound_slice():
    # A mutable slice<T> target is allowed: the backing storage is fresh and
    # nothing else names it. Two copies of the view alias one backing array.
    source = """
    fn main() -> int32 {
        let s: slice<int32> = [1, 2, 3];
        let t = s;
        t[0] = 40;            // writes the shared backing array
        return s[0] + s[2];   // 40 + 3
    }
    """
    assert run(source) == 43


def test_array_literal_to_const_slice():
    # A read-only view over the fresh storage; writes are rejected as for
    # any slice<const T>.
    source = """
    fn main() -> int32 {
        let s: slice<const int32> = [7, 8];
        return s[0] + (s.length as int32);   // 7 + 2
    }
    """
    assert run(source) == 9


def test_array_literal_cast_in_argument_position():
    # A bare literal argument does not adapt (later stage), but the explicit
    # `as` works in an argument slot -- including a const slice parameter,
    # which travels by hidden reference.
    source = """
    fn sum(s: slice<const int32>) -> int32 {
        let total: int32 = 0;
        for v in s { total = total + v; }
        return total;
    }
    fn main() -> int32 { return sum([1, 2, 3] as slice<const int32>); }
    """
    assert run(source) == 6


def test_array_literal_adapts_in_bare_argument():
    # Stage 2: a bare array literal adapts to a slice<T> parameter with no
    # `as` cast. A const slice parameter travels by hidden reference, so the
    # borrowed view spills to a temporary first.
    source = """
    fn sum(s: slice<const int32>) -> int32 {
        let total: int32 = 0;
        for v in s { total = total + v; }
        return total;
    }
    fn main() -> int32 { return sum([1, 2, 3]); }
    """
    assert run(source) == 6


def test_array_literal_argument_to_plain_slice_is_writable():
    # A plain (non-mut) slice<T> parameter with mutable elements accepts a
    # literal: the backing array is fresh writable storage, so the view is
    # mutable (uniform-allow, matching the string-literal family).
    source = """
    fn bump(s: slice<int32>) -> int32 {
        s[0] = s[0] + 40;
        return s[0] + s[1];
    }
    fn main() -> int32 { return bump([1, 2]); }   // 41 + 2
    """
    assert run(source) == 43


def test_empty_array_literal_argument():
    # `[]` in argument position builds no backing array: the { null, 0 } view.
    source = """
    fn count(s: slice<int32>) -> int32 { return s.length as int32; }
    fn main() -> int32 { return count([]); }
    """
    assert run(source) == 0


def test_array_literal_argument_to_overload_set():
    # A second overload flips the name onto the overload-set path; the literal
    # must still adapt there (the parity trap the string family already hit).
    source = """
    fn take(s: slice<int32>) -> int32 { return 100 + s.length as int32; }
    fn take(x: int32) -> int32 { return x; }
    fn main() -> int32 { return take([1, 2, 3]); }   // slice overload: 103
    """
    assert run(source) == 103


def test_array_literal_argument_prefers_slice_over_pointer():
    # The overload-collision case: f(int32*) beside f(slice<int32>) called with
    # a literal must pick the slice -- an array literal never adapts to a
    # pointer (shape_matches rejects the pointer candidate).
    source = """
    fn f(p: int32*) -> int32 { return 1; }
    fn f(s: slice<int32>) -> int32 { return 2; }
    fn main() -> int32 { return f([1, 2, 3]); }   // slice: 2
    """
    assert run(source) == 2


def test_ternary_of_array_literals_argument():
    # A ternary whose arms are both array literals adapts arm by arm: each
    # borrows in its own branch, so the chosen arm's exact length survives.
    source = """
    fn count(s: slice<int32>) -> int32 { return s.length as int32; }
    fn main() -> int32 {
        return count(true ? [1] : [2, 3]) + count(false ? [1] : [2, 3]);
    }
    """
    assert run(source) == 3   # 1 (then arm) + 2 (else arm)


def test_generic_array_literal_argument_with_explicit_type():
    # A bare literal contributes nothing to inference, so a generic slice<T>
    # parameter needs T from an explicit type argument (or a companion arg).
    source = """
    fn count<T>(s: slice<T>) -> int32 { return s.length as int32; }
    fn main() -> int32 { return count<int32>([4, 5, 6]); }
    """
    assert run(source) == 3


def test_generic_array_literal_argument_cannot_infer_t():
    # Without an explicit type argument the literal cannot anchor T: element
    # anchoring is not in scope for this stage.
    with pytest.raises(LangError, match="cannot infer type parameter"):
        compile_ir(
            """
            fn count<T>(s: slice<T>) -> int32 { return 0; }
            fn main() -> int32 { return count([1, 2, 3]); }
            """
        )


def test_mut_slice_parameter_still_rejects_literal():
    # Uniform-allow applies to non-mut parameters only. A `mut slice<T>`
    # parameter demands the caller's own writable storage, which a literal is
    # not, so it stays rejected.
    with pytest.raises(
        LangError, match="only allowed where an array or slice type receives it"
    ):
        compile_ir(
            """
            fn f(mut s: slice<int32>) -> int32 { return 0; }
            fn main() -> int32 { return f([1, 2, 3]); }
            """
        )


def test_assignment_from_literal_still_rejected():
    # `s = [1, 2];` is a later stage too; only initializers adapt today.
    with pytest.raises(
        LangError, match="only allowed where an array or slice type receives it"
    ):
        compile_ir(
            """
            fn main() -> int32 {
                let s: slice<int32> = [];
                s = [1, 2];
                return 0;
            }
            """
        )


def test_empty_literal_both_spellings():
    # `[]` builds no backing array at all: the { null, 0 } view (the same
    # empty slice zero variadic extras synthesize). No zero-length array
    # type is ever constructed.
    source = """
    fn main() -> int32 {
        let a: slice<int32> = [];
        let b = [] as slice<int32>;
        return (a.length + b.length) as int32;
    }
    """
    assert run(source) == 0


def test_array_literal_elements_adapt_to_slice_elements():
    # Element position: a slice<int32>[2] array fills from nested literals,
    # each element borrowing its own backing array.
    source = """
    fn main() -> int32 {
        let m: slice<int32>[2] = [[1, 2], [3, 4]];
        return m[0][1] * 10 + m[1][0];   // 23
    }
    """
    assert run(source) == 23


def test_nested_slice_of_slices():
    # slice<slice<T>>: the outer literal's elements adapt to slice<int32>
    # through the same element gate, so the whole shape needs one `as`.
    source = """
    fn main() -> int32 {
        let n = [[5, 6], [7]] as slice<slice<int32>>;
        return (n.length as int32) * 100 + n[0][1] * 10 + n[1][0];   // 267
    }
    """
    assert run(source) == 267


def test_string_elements_in_slice_of_char_slices():
    # String-literal elements keep adapting inside an adapted outer literal:
    # slice<slice<char>> from plain strings, lengths NUL-free.
    source = """
    fn main() -> int32 {
        let names = ["ab", "cde"] as slice<slice<char>>;
        return (names.length + names[0].length + names[1].length) as int32;
    }
    """
    assert run(source) == 7


def test_element_type_mismatch_rejected():
    # Elements coerce one by one through the usual array-element rule.
    with pytest.raises(LangError, match="array element: expected int32, got float64"):
        compile_ir(
            """
            fn main() -> int32 {
                let s: slice<int32> = [1, 2.5];
                return 0;
            }
            """
        )


def test_ternary_of_array_literals_adapts():
    # A ternary adapts arm by arm (each arm borrows its own backing array in
    # its own branch), in both the let and the `as` spellings.
    source = """
    fn pick(flag: bool) -> int32 {
        let s: slice<int32> = flag ? [1] : [2, 3];
        let t = (flag ? [9] : [8, 7, 6]) as slice<int32>;
        return (s.length * 10 + t.length) as int32;
    }
    fn main() -> int32 { return pick(false) * 100 + pick(true); }
    """
    assert run(source) == 2311   # false: 23, true: 11


def test_char_literal_slice_keeps_exact_count():
    # The char asymmetry, pinned from both sides: a char array literal has no
    # NUL, so its borrow keeps every element (length 2)...
    source = """
    fn main() -> int32 {
        let s = ['h', 'i'] as slice<char>;
        return s.length as int32;
    }
    """
    assert run(source) == 2


def test_char_array_two_step_borrow_drops_presumed_nul():
    # ...while the two-step form binds a char[2] first, and a char[N] *array*
    # borrow presumes NUL-terminated text, dropping one trailing byte.
    source = """
    fn main() -> int32 {
        let cs: char[2] = ['h', 'i'];
        let s = cs as slice<char>;
        return s.length as int32;
    }
    """
    assert run(source) == 1


def test_literal_in_loop_reuses_one_slot():
    # One entry-block backing slot per literal occurrence, re-stored each
    # pass: a view captured in iteration N observes iteration N+1's store,
    # like any loop local.
    source = """
    fn main() -> int32 {
        let captured: slice<int32> = [];
        let i: int32 = 0;
        while (i < 3) {
            let s: slice<int32> = [i, i * 10];
            if (i == 0) { captured = s; }
            i = i + 1;
        }
        return captured[1];   // the last pass stored [2, 20]
    }
    """
    assert run(source) == 20


def test_return_of_literal_borrow_rejected():
    # `return [...] as slice<T>` would view this frame's dead backing array;
    # nothing else names it, so it is always dangling and rejected up front.
    with pytest.raises(LangError, match="cannot return an array literal borrowed"):
        compile_ir(
            """
            fn f() -> slice<int32> { return [1, 2] as slice<int32>; }
            fn main() -> int32 { return 0; }
            """
        )


def test_return_of_ternary_literal_borrow_rejected():
    # A ternary arm dangles the same way.
    with pytest.raises(LangError, match="cannot return an array literal borrowed"):
        compile_ir(
            """
            fn f(flag: bool) -> slice<int32> {
                return (flag ? [1] : [2, 3]) as slice<int32>;
            }
            fn main() -> int32 { return 0; }
            """
        )


def test_return_of_named_array_borrow_still_legal():
    # Only the direct literal spelling is rejected; a named local's borrow
    # keeps compiling as before (the caller can reason about the local).
    source = """
    fn f() -> slice<int32> {
        let xs: int32[2] = [4, 5];
        return xs as slice<int32>;   // still dangles at runtime, but named
    }
    fn main() -> int32 { return 0; }
    """
    compile_ir(source)


def test_static_const_slice_from_array_literal():
    # The @static route: the elements land in an anonymous private constant
    # global and the slice is a constant {pointer, length} view over it.
    source = """
    @static let g: slice<const int32> = [10, 20, 30];
    fn main() -> int32 {
        return g[2] + (g.length as int32);   // 30 + 3
    }
    """
    assert run(source) == 33


def test_static_slice_initializer_is_constant_view():
    # No runtime code: a getelementptr into the .arr global plus the count.
    ir_text = compile_ir(
        """
        @static let g: slice<const int32> = [1, 2];
        fn main() -> int32 { return g.length as int32; }
        """
    )
    assert (
        'getelementptr ([2 x i32], [2 x i32]* @".arr.0", i32 0, i32 0), i64 2'
        in ir_text
    )


def test_static_mutable_slice_from_literal_rejected():
    # The backing constant is rodata: a mutable @static view would open a
    # write path into it, so the message points at slice<const T>.
    with pytest.raises(LangError, match="declare it slice<const int32>"):
        compile_ir(
            """
            @static let g: slice<int32> = [1, 2];
            fn main() -> int32 { return 0; }
            """
        )


def test_static_empty_const_slice():
    source = """
    @static let g: slice<const int32> = [];
    fn main() -> int32 { return g.length as int32; }
    """
    assert run(source) == 0


def test_unannotated_let_of_array_literal_stays_ambiguous():
    # No inference from elements yet (a later stage): a bare let still asks
    # for the annotation.
    with pytest.raises(LangError, match="array literal needs a type annotation"):
        compile_ir("fn main() -> int32 { let v = [1, 2]; return 0; }")


def test_format_renders_adapted_literal(capfd):
    # Protocol composition: the format module's generic slice<T> renderer
    # receives an adapted literal like any other slice.
    run(
        """
        import "std/format";
        import "std/string";
        import "libc/stdio";
        fn main() -> int32 {
            let s = string();
            format(s, [0x10, 0x1F] as slice<const int32>, "");
            printf("|%.*s|\\n", s.length as int32, s.data);
            return 0;
        }
        """
    )
    assert capfd.readouterr().out == "|[16, 31]|\n"


# --- Sub-slicing: s[start:end] -----------------------------------------------


def test_sub_slice_all_four_forms():
    # s[a:b], s[a:], s[:b], and s[:]; values are read back through each
    # sub-view, so the data pointer and the length are both exercised.
    source = """
    fn main() -> int32 {
        let nums = [10, 20, 30, 40] as slice<int32>;
        let mid = nums[1:3];      // { &nums[1], 2 } -> 20, 30
        let tail = nums[1:];      // end defaults to nums.length
        let head = nums[:2];      // start defaults to 0
        let all = nums[:];        // a plain copy of the view
        let got = (mid.length as int32) * 1000
            + (tail.length as int32) * 100
            + (head.length as int32) * 10
            + (all.length as int32);
        return got + mid[0] + mid[1] + tail[2] + head[0] + all[3];
    }
    """
    # lengths 2/3/2/4 = 2324; elements 20 + 30 + 40 + 10 + 40 = 140
    assert run(source) == 2464


def test_sub_slice_full_copy_is_same_view():
    # s[:] copies the view itself: same data pointer, same length.
    source = """
    fn main() -> int32 {
        let nums = [1, 2, 3] as slice<int32>;
        let copy = nums[:];
        if (copy.data != nums.data) { return 1; }
        if (copy.length != nums.length) { return 2; }
        return 0;
    }
    """
    assert run(source) == 0


def test_write_through_sub_slice_hits_original_storage():
    # A sub-slice views the same storage, so a write through it lands in the
    # array the receiver borrowed.
    source = """
    fn main() -> int32 {
        let xs: int32[4];
        xs[0] = 1; xs[1] = 2; xs[2] = 3; xs[3] = 4;
        let s = xs as slice<int32>;
        let mid = s[1:3];
        mid[0] = 99;              // s[1], i.e. xs[1]
        return xs[1];
    }
    """
    assert run(source) == 99


def test_sub_slice_of_const_slice_keeps_const():
    # The result type is the receiver's type verbatim, so a sub-slice of
    # slice<const T> is slice<const T> and writes through it stay rejected.
    with pytest.raises(LangError, match="cannot assign through a read-only slice<const T>"):
        compile_ir(
            """
            fn main() -> int32 {
                let ro = [1, 2, 3] as slice<const int32>;
                ro[0:2][0] = 5;
                return 0;
            }
            """
        )


def test_sub_slice_bounds_have_index_parity():
    # Any integer type serves as a bound (widened by its own signedness, like
    # a GEP index), so an int32 bound works and mixes with the uint64 default
    # or an explicit s.length end.
    source = """
    fn main() -> int32 {
        let nums = [10, 20, 30, 40, 50] as slice<int32>;
        let i: int32 = 1;
        let t = nums[i:];                 // int32 start, defaulted uint64 end
        let u = nums[i:nums.length];      // int32 start, uint64 end
        let b: uint8 = 2;
        let v = nums[b:4];                // uint8 start
        return (t.length as int32) * 100 + (u.length as int32) * 10
            + (v.length as int32) + t[0] / 20;   // 4, 4, 2, 20/20
    }
    """
    assert run(source) == 443


def test_sub_slice_non_integer_bound_rejected():
    with pytest.raises(LangError, match="slice bound must be an integer, not float64"):
        compile_ir(
            """
            fn main() -> int32 {
                let s = [1, 2] as slice<int32>;
                let x = s[1.5:];
                return 0;
            }
            """
        )


def test_sub_slice_chains():
    # A sub-slice is an ordinary slice value, so it sub-slices again.
    source = """
    fn main() -> int32 {
        let nums = [10, 20, 30, 40] as slice<int32>;
        let s = nums[1:][1:];     // { &nums[2], 2 }
        return (s.length as int32) * 100 + s[0];
    }
    """
    assert run(source) == 230


def test_sub_slice_empty_keeps_real_data_pointer():
    # s[n:n] is the defined empty result { &s.data[n], 0 }: the one-past-end
    # pointer is formed (not normalized to the empty literal's { null, 0 })
    # but never dereferenced.
    source = """
    fn main() -> int32 {
        let nums = [1, 2, 3] as slice<int32>;
        let e = nums[2:2];
        if (e.length != 0) { return 1; }
        if (e.data == null) { return 2; }
        for x in e { return 3; }  // zero passes
        return 0;
    }
    """
    assert run(source) == 0


def test_for_in_over_sub_slice():
    # A sub-slice is a plain slice value, so `for ... in` iterates it.
    source = """
    fn main() -> int32 {
        let nums = [10, 20, 30, 40] as slice<int32>;
        let total: int32 = 0;
        for x in nums[1:3] {
            total += x;
        }
        return total;
    }
    """
    assert run(source) == 50


def test_sub_slice_as_argument():
    # A sub-slice passes like any slice value: as a mutable view (writes land
    # in the shared storage) and as a const parameter.
    source = """
    fn bump(xs: slice<int32>) { xs[0] = 77; }
    fn total(const xs: slice<int32>) -> int32 {
        let sum: int32 = 0;
        for x in xs { sum += x; }
        return sum;
    }
    fn main() -> int32 {
        let xs: int32[3];
        xs[0] = 1; xs[1] = 2; xs[2] = 3;
        let s = xs as slice<int32>;
        bump(s[1:]);              // writes xs[1]
        return total(s[1:]) + xs[1];   // (77 + 3) + 77
    }
    """
    assert run(source) == 157


def test_format_renders_sub_slice(capfd):
    # Protocol composition: the format module's generic slice<T> renderer
    # receives a sub-slice like any other slice value.
    run(
        """
        import "std/format";
        import "std/string";
        import "libc/stdio";
        fn main() -> int32 {
            let s = string();
            let nums = [1, 2, 3, 4] as slice<const int32>;
            format(s, nums[1:3], "");
            printf("|%.*s|\\n", s.length as int32, s.data);
            return 0;
        }
        """
    )
    assert capfd.readouterr().out == "|[2, 3]|\n"


def test_sub_slice_ternary_start_binds_greedily():
    # A full expression parses before the slice `:` is considered, so a
    # ternary start consumes its own `:`: s[flag ? 1 : 2 : 3] is
    # start = (flag ? 1 : 2), end = 3.
    source = """
    fn pick(flag: bool) -> int32 {
        let nums = [10, 20, 30, 40] as slice<int32>;
        let s = nums[flag ? 1 : 2 : 3];
        return (s.length as int32) * 100 + s[0];
    }
    fn main() -> int32 { return pick(true) * 1000 + pick(false) / 10; }
    """
    # true: { &nums[1], 2 } -> 220; false: { &nums[2], 1 } -> 130
    assert run(source) == 220013


def test_sub_slice_of_borrowed_literal():
    # Composition with the array-literal borrow: the literal becomes a slice
    # first, then sub-slices as one.
    source = """
    fn main() -> int32 {
        let s = ([1, 2, 3] as slice<int32>)[1:];
        return (s.length as int32) * 10 + s[0];
    }
    """
    assert run(source) == 22


def test_sub_slice_no_step_form():
    # `::` lexes as one token, so a step never parses as two slice colons.
    with pytest.raises(LangError, match="unexpected token '::'"):
        compile_ir(
            """
            fn main() -> int32 {
                let s = [1, 2] as slice<int32>;
                let x = s[::2];
                return 0;
            }
            """
        )
    with pytest.raises(LangError, match=r"expected '\]', got '::'"):
        compile_ir(
            """
            fn main() -> int32 {
                let s = [1, 2] as slice<int32>;
                let x = s[1::2];
                return 0;
            }
            """
        )


def test_sub_slice_is_not_an_lvalue():
    # An rvalue view: not an assignment target, not compound-assignable, and
    # not addressable.
    with pytest.raises(LangError, match="invalid assignment target"):
        compile_ir(
            """
            fn main() -> int32 {
                let s = [1, 2] as slice<int32>;
                s[0:1] = 9;
                return 0;
            }
            """
        )
    with pytest.raises(LangError, match="invalid assignment target"):
        compile_ir(
            """
            fn main() -> int32 {
                let s = [1, 2] as slice<int32>;
                s[0:1] += 9;
                return 0;
            }
            """
        )
    with pytest.raises(LangError, match="expression is not addressable"):
        compile_ir(
            """
            fn main() -> int32 {
                let s = [1, 2] as slice<int32>;
                let p = &s[0:1];
                return 0;
            }
            """
        )


def test_sub_slice_array_receiver_rejected_with_borrow_hint():
    # Only slices sub-slice; an owned array reaches it through the borrow.
    with pytest.raises(
        LangError,
        match=r"cannot sub-slice int32\[4\]; borrow it first: "
        r"\(arr as slice<int32>\)\[a:b\]",
    ):
        compile_ir(
            """
            fn main() -> int32 {
                let a: int32[4];
                let x = a[1:2];
                return 0;
            }
            """
        )


def test_sub_slice_list_receiver_rejected_with_borrow_hint():
    # list<T> may carry derived state beyond the view (its capacity), so it
    # borrows first, like lst[i] not indexing today.
    with pytest.raises(
        LangError,
        match=r"cannot sub-slice list<int32>; borrow it first: "
        r"\(xs as slice<T>\)\[a:b\]",
    ):
        compile_ir(
            """
            import "std/list";
            fn main() -> int32 {
                let xs: struct list<int32>;
                let x = xs[0:1];
                return 0;
            }
            """
        )


def test_sub_slice_string_literal_receiver_rejected():
    # A literal stays rejected in v1: the borrow is its slice spelling too.
    with pytest.raises(
        LangError,
        match=r'cannot sub-slice a string literal; borrow it first: '
        r'\("\.\.\." as slice<char>\)\[a:b\]',
    ):
        compile_ir(
            """
            fn main() -> int32 {
                let x = "hello"[1:3];
                return 0;
            }
            """
        )


def test_sub_slice_pointer_receiver_rejected():
    # A bare pointer has no length to default and no borrow spelling; v1
    # keeps slice-making from a raw pointer to the explicit struct literal.
    with pytest.raises(LangError, match="cannot sub-slice char\\*; only a slice can be sub-sliced"):
        compile_ir(
            """
            fn f(p: char*) -> int32 {
                let x = p[0:1];
                return 0;
            }
            fn main() -> int32 { return 0; }
            """
        )


def test_sub_slice_not_a_compile_time_constant():
    # Runtime-expression only: no sub-slicing in const initializers (or @if /
    # @static, which share the constant evaluator).
    with pytest.raises(LangError, match="a const initializer must be a compile-time constant"):
        compile_ir(
            """
            const C = ([1, 2] as slice<int32>)[0:1];
            fn main() -> int32 { return 0; }
            """
        )


# --- Destructuring: let first, rest... = s ------------------------------------
#
# The tuple rest binder over a slice source: `let a, b, rst... = s;` is
# `a = s[0]; b = s[1]; rst = s[2:]` -- unchecked indexing plus the sub-slice
# view, so no code validates the source is long enough (exactly like s[i]),
# and the rest binder shares the source's storage.

def test_destructure_a_slice():
    source = """
    fn main() -> int32 {
        let nums = [10, 20, 30, 40] as slice<int32>;
        let a, b, rst... = nums;
        return a + b + rst[0] + (rst.length as int32);   // 10+20+30+2
    }
    """
    assert run(source) == 62


def test_slice_rest_binder_is_a_view():
    # The rest binder views the same storage: writes reach the base.
    source = """
    fn main() -> int32 {
        let nums = [1, 2, 3] as slice<int32>;
        let a, rst... = nums;
        rst[0] = 40;
        return nums[1] + a + nums[2];   // 40 + 1 + 3
    }
    """
    assert run(source) == 44


def test_slice_lone_rest_binder_copies_the_view():
    source = """
    fn main() -> int32 {
        let nums = [40, 2] as slice<int32>;
        let r... = nums;
        return r[0] + r[1];
    }
    """
    assert run(source) == 42


def test_slice_rest_binder_may_be_empty():
    # Binding every element leaves the defined empty view { &s[n], 0 }.
    source = """
    fn main() -> int32 {
        let nums = [9] as slice<int32>;
        let a, rst... = nums;
        return a * 10 + (rst.length as int32);
    }
    """
    assert run(source) == 90


def test_destructure_call_slice_source_evaluates_once():
    source = """
    @static let calls: int32 = 0;
    fn view(s: slice<int32>) -> slice<int32> { calls = calls + 1; return s; }
    fn main() -> int32 {
        let arr: int32[3] = [4, 5, 6];
        let a, rst... = view(arr as slice<int32>);
        return calls * 100 + a * 10 + rst[1];   // 100 + 40 + 6
    }
    """
    assert run(source) == 146


def test_destructure_read_only_slice_keeps_element_const():
    # slice<const T> destructures; the rest view is still read-only.
    with pytest.raises(
        LangError, match="cannot assign through a read-only slice<const T>"
    ):
        compile_ir(
            """
            fn main() -> int32 {
                let arr: int32[3] = [1, 2, 3];
                let s = arr as slice<const int32>;
                let a, rst... = s;
                rst[0] = 9;
                return a;
            }
            """
        )


def test_destructure_array_source_rejected():
    # Owned containers borrow first, exactly like sub-slicing.
    with pytest.raises(
        LangError,
        match="cannot destructure int32\\[3\\]; borrow it first: "
        "let a, b = arr as slice<int32>;",
    ):
        compile_ir(
            """
            fn main() -> int32 {
                let arr: int32[3] = [1, 2, 3];
                let a, rst... = arr;
                return a;
            }
            """
        )


def test_destructure_list_source_rejected():
    # A slice-extending struct may carry derived state (a list's capacity)
    # beyond the view; it borrows first too.
    with pytest.raises(
        LangError,
        match="cannot destructure list<int32>; borrow it first: "
        "let a, b = xs as slice<T>;",
    ):
        compile_ir(
            """
            import "std/list";
            fn main() -> int32 {
                let xs = list<int32>(4);
                let a, rst... = xs;
                return 0;
            }
            """
        )


def test_destructure_string_literal_rejected():
    with pytest.raises(
        LangError,
        match='cannot destructure a string literal; borrow it first: '
        'let a, b = "..." as slice<char>;',
    ):
        compile_ir('fn main() -> int32 { let a, rst... = "hi"; return 0; }')


# ------------------------------ string-literal assignment (Stage 4, assignment)
#
# A string literal repoints an existing char-slice lvalue at its global string
# constant (static lifetime, so safe even when the target outlives the frame),
# the same borrow the let/argument/element/field positions already do. It
# reaches every assignment lvalue form: plain name, deref, index, member, and a
# mut return. Array literals stay rejected here (a frame-local backing would
# dangle past a longer-lived target).


def test_string_literal_assignment_to_char_slice():
    # Plain assignment: `s = "hi";` reborrows, dropping the NUL, so the length
    # is the new literal's, not the old one's.
    source = """
    fn main() -> int32 {
        let s: slice<char> = "hi";
        s = "hello";
        return s.length as int32;   // 5, not 2
    }
    """
    assert run(source) == 5


def test_string_literal_assignment_to_const_char_slice():
    source = """
    fn main() -> int32 {
        let s: slice<const char> = "a";
        s = "abcd";
        return (s.length as int32) * 10 + (s[0] as int32 - 'a' as int32);   // 40
    }
    """
    assert run(source) == 40


def test_string_literal_assignment_through_deref():
    # `*out = "hi";` repoints the slice behind the pointer; the pointee's const
    # check reads the whole-type const (not the element const), so a
    # slice<const char>* target is fine.
    source = """
    fn set(out: slice<const char>*) { *out = "world"; }
    fn main() -> int32 {
        let s: slice<const char> = "x";
        set(&s);
        return s.length as int32;   // 5
    }
    """
    assert run(source) == 5


def test_string_literal_assignment_to_slice_element():
    source = """
    fn main() -> int32 {
        let arr: slice<char>[2] = ["x", "y"];
        arr[0] = "first";
        return (arr[0].length as int32) * 10 + (arr[1].length as int32);   // 51
    }
    """
    assert run(source) == 51


def test_string_literal_assignment_to_struct_field():
    # The headline gap-closer: `c.name = "hi"` (member assign) now works, the
    # same adaptation the struct literal `cmd { name = "hi" }` already allowed.
    source = """
    struct cmd { name: slice<const char>; }
    fn main() -> int32 {
        let c: cmd = cmd { name = "ls" };
        c.name = "grep";
        return c.name.length as int32;   // 4
    }
    """
    assert run(source) == 4


def test_string_literal_assignment_through_mut_return():
    source = """
    fn pick(xs: slice<char>*, i: int32) -> mut slice<char> { return xs[i]; }
    fn main() -> int32 {
        let arr: slice<char>[2] = ["a", "b"];
        pick(arr, 1) = "second";
        return arr[1].length as int32;   // 6
    }
    """
    assert run(source) == 6


def test_ternary_of_string_literals_adapts_in_assignment():
    # The ternary rides along for free: str_literal_adapts recurses on the arms.
    source = """
    fn main() -> int32 {
        let s: slice<char> = "hi";
        let flag: bool = false;
        s = flag ? "y" : "yes";
        return s.length as int32;   // 3
    }
    """
    assert run(source) == 3


def test_static_slice_reassigned_from_string_literal():
    # A runtime reassignment of a @static char-slice global: the initializer
    # stays a constant view, the reassignment reborrows at runtime.
    source = """
    @static let g: slice<const char> = "x";
    fn main() -> int32 {
        g = "yy";
        return g.length as int32;   // 2
    }
    """
    assert run(source) == 2


def test_array_literal_assignment_rejected():
    # Array-literal assignment stays a compile error at every lvalue form: the
    # frame-local backing would dangle past a longer-lived target. The generic
    # array-literal message fires (no bespoke dangle message here).
    for lvalue in ("s = [1, 2, 3];", "*out = [1, 2, 3];", "c.xs = [1, 2, 3];"):
        with pytest.raises(
            LangError, match="an array literal is only allowed where an array or slice"
        ):
            compile_ir(
                """
                struct box { xs: slice<int32>; }
                fn main() -> int32 {
                    let s: slice<int32> = [0];
                    let out: slice<int32>* = &s;
                    let c: box = box { xs = [9] };
                    """
                + lvalue
                + """
                    return 0;
                }
                """
            )
