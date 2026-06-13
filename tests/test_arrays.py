"""Fixed-size array types: T[N], indexing, sizeof, and pointer decay."""

import pytest

from mcc.errors import LangError
from mcc.nodes import TypeRef
from helpers import compile_ir, parse, run


def test_array_type_parses():
    (func,) = parse("fn f(g: int32[10]) {}").functions
    t = func.params[0][1]
    assert isinstance(t, TypeRef) and t.name == "int32" and t.dims == [10]
    assert str(t) == "int32[10]"


def test_multidim_array_type_parses():
    (func,) = parse("fn f(g: int32[2][3]) {}").functions
    assert func.params[0][1].dims == [2, 3]
    assert str(func.params[0][1]) == "int32[2][3]"


def test_declare_index_and_read():
    source = """
    fn main() -> int32 {
        let buf: int32[5];
        let i: int32 = 0;
        while (i < 5) { buf[i] = i * i; i = i + 1; }
        return buf[4] + buf[2];     // 16 + 4
    }
    """
    assert run(source) == 20


def test_sizeof_array():
    source = """
    fn main() -> int32 {
        return (sizeof(int32[5]) + sizeof(uint8[3]) + sizeof(int32[2][3])) as int32;
    }
    """
    # 20 + 3 + 24
    assert run(source) == 47


def test_array_decays_to_pointer_when_passed():
    source = """
    fn total(p: int32*, n: int32) -> int32 {
        let s: int32 = 0;
        let i: int32 = 0;
        while (i < n) { s = s + p[i]; i = i + 1; }
        return s;
    }
    fn main() -> int32 {
        let xs: int32[4];
        xs[0] = 1; xs[1] = 2; xs[2] = 3; xs[3] = 4;
        return total(xs, 4);     // 10, array decays to int32*
    }
    """
    assert run(source) == 10


def test_address_of_element():
    source = """
    fn set(p: int32*) { *p = 99; }
    fn main() -> int32 {
        let xs: int32[3];
        xs[1] = 0;
        set(&xs[1]);
        return xs[1];
    }
    """
    assert run(source) == 99


def test_multidim_indexing():
    source = """
    fn main() -> int32 {
        let grid: int32[2][3];
        grid[0][0] = 5;
        grid[1][2] = 7;
        return grid[0][0] + grid[1][2];
    }
    """
    assert run(source) == 12


def test_array_in_a_struct_field():
    source = """
    struct line { points: int32[4]; len: int32; }
    fn main() -> int32 {
        let l: struct line;
        l.len = 2;
        l.points[0] = 10;
        l.points[1] = 20;
        return l.points[0] + l.points[1] + l.len;
    }
    """
    assert run(source) == 32


def test_array_size_must_be_positive():
    with pytest.raises(LangError, match="array size must be at least 1"):
        parse("fn main() { let x: int32[0]; }")


def test_array_stack_allocation_in_ir():
    ir_text = compile_ir(
        "fn main() -> int32 { let buf: int32[8]; buf[0] = 1; return buf[0]; }"
    )
    assert "alloca [8 x i32]" in ir_text


def test_pointer_to_array_type_is_rejected():
    with pytest.raises(LangError, match="pointer to an array type is not supported"):
        parse("fn main() { let p: (int32[4])*; }")
