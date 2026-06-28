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
