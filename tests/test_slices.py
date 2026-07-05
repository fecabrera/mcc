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
    import "list";
    fn main() -> int32 {
        let xs: struct list<int32>;
        list_init(&xs, 4);
        list_push(&xs, 7);
        list_push(&xs, 8);
        list_push(&xs, 9);
        let s = xs as slice<int32>;
        let got = (s.length as int32) + s[0] + s[1] + s[2];   // 3 + 24
        list_destroy(&xs);
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
    import "list";
    fn main() -> int32 {
        let xs: struct list<int32>;
        list_init(&xs, 4);                 // length 0
        let s = xs as slice<int32>;
        let count: int32 = 0;
        for v in s { count = count + 1; }
        list_destroy(&xs);
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
    import "list";
    fn copy_first<T>(@nonnull self: struct list<T>*, const arr: slice<const T>) {
        list_init(self, arr.length);
        for el in arr { list_push(self, el); }
    }
    fn main() -> int32 {
        let xs: int32[3];
        xs[0] = 4; xs[1] = 5; xs[2] = 6;
        let dst: struct list<int32>;
        copy_first(&dst, xs as slice<const int32>);   // T = int32, not const int32
        let view = dst as slice<int32>;
        let got = (view.length as int32) + view[0];
        list_destroy(&dst);
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
