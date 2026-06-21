"""Fixed-size array types: T[N], indexing, sizeof, and pointer decay."""

import pytest

from mcc.errors import LangError
from mcc.nodes import ArrayLit, TypeRef
from helpers import compile_ir, parse, run, run_path


def test_list_type_parses():
    (func,) = parse("fn f(g: int32[10]) {}").functions
    t = func.params[0][1]
    assert isinstance(t, TypeRef) and t.name == "int32" and t.dims == [10]
    assert str(t) == "int32[10]"


def test_multidim_list_type_parses():
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


def test_list_decays_to_pointer_when_passed():
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


def test_list_in_a_struct_field():
    source = """
    struct line { points: int32[4]; count: int32; }
    fn main() -> int32 {
        let l: struct line;
        l.count = 2;
        l.points[0] = 10;
        l.points[1] = 20;
        return l.points[0] + l.points[1] + l.count;
    }
    """
    assert run(source) == 32


def test_list_size_must_be_positive():
    with pytest.raises(LangError, match="array size must be at least 1"):
        parse("fn main() { let x: int32[0]; }")


def test_list_stack_allocation_in_ir():
    ir_text = compile_ir(
        "fn main() -> int32 { let buf: int32[8]; buf[0] = 1; return buf[0]; }"
    )
    assert "alloca [8 x i32]" in ir_text


def test_pointer_to_list_type_is_rejected():
    with pytest.raises(LangError, match="pointer to an array type is not supported"):
        parse("fn main() { let p: (int32[4])*; }")


# --- array literals ---


def test_list_literal_parses():
    (func,) = parse("fn main() { let xs: int32[2] = [1, 2]; }").functions
    lit = func.body[0].value
    assert isinstance(lit, ArrayLit) and len(lit.elements) == 2


def test_local_list_literal():
    source = """
    fn main() -> int32 {
        let xs: int32[4] = [10, 20, 30, 40];
        return xs[0] + xs[3];
    }
    """
    assert run(source) == 50


def test_list_literal_with_runtime_elements():
    source = """
    fn main() -> int32 {
        let n: int32 = 5;
        let xs: int32[3] = [n, n * 2, n + 1];
        return xs[0] + xs[1] + xs[2];     // 5 + 10 + 6
    }
    """
    assert run(source) == 21


def test_inferred_outer_dimension():
    source = """
    fn main() -> int32 {
        let xs: int32[] = [3, 1, 4, 1, 5, 9];
        return xs[5];                      // sizeof not needed; just index
    }
    """
    assert run(source) == 9


def test_nested_literal_and_multidim():
    source = """
    fn main() -> int32 {
        let grid: int32[2][2] = [[1, 2], [3, 4]];
        return grid[0][0] + grid[0][1] + grid[1][0] + grid[1][1];
    }
    """
    assert run(source) == 10


def test_trailing_comma_allowed():
    source = "fn main() -> int32 { let xs: int32[2] = [7, 8,]; return xs[1]; }"
    assert run(source) == 8


def test_static_list_literal_of_strings(capfd):
    # The motivating example: a static command table with an inferred row count.
    source = r"""
    import "libc/stdio";
    @static let cmds: uint8*[][2] = [
        ["help", "show help"],
        ["quit", "exit"],
    ];
    fn main() -> int32 {
        printf("%s=%s %s=%s\n", cmds[0][0], cmds[0][1], cmds[1][0], cmds[1][1]);
        return 0;
    }
    """
    run(source)
    assert capfd.readouterr().out == "help=show help quit=exit\n"


def test_static_list_literal_is_a_constant_global():
    ir_text = compile_ir(
        "@static let xs: int32[] = [2, 3, 5];\nfn main() -> int32 { return xs[2]; }"
    )
    assert "internal global [3 x i32] [i32 2, i32 3, i32 5]" in ir_text


def test_literal_length_must_match():
    with pytest.raises(LangError, match="array literal has 2 elements, expected 3"):
        compile_ir("fn main() -> int32 { let a: int32[3] = [1, 2]; return 0; }")


def test_element_type_is_checked():
    with pytest.raises(LangError, match="array element: expected int32, got uint8"):
        compile_ir('fn main() -> int32 { let a: int32[2] = ["x", "y"]; return 0; }')


def test_static_initializer_must_be_constant():
    with pytest.raises(LangError, match="must be a compile-time constant"):
        compile_ir(
            "fn side() -> int32 { return 1; }\n"
            "@static let a: int32[2] = [side(), 1];\n"
            "fn main() -> int32 { return 0; }"
        )


def test_only_outermost_dimension_inferred():
    with pytest.raises(LangError, match="outermost"):
        compile_ir("fn main() -> int32 { let a: int32[2][] = [[1], [2]]; return 0; }")


def test_list_literal_needs_a_type_annotation():
    with pytest.raises(LangError, match="array literal needs a type annotation"):
        compile_ir("fn main() -> int32 { let a = [1, 2, 3]; return 0; }")


def test_list_literal_only_as_initializer():
    with pytest.raises(LangError, match="only allowed as a variable initializer"):
        compile_ir("fn f(p: int32*) {}\nfn main() -> int32 { f([1, 2, 3]); return 0; }")


# --- len() ---


def test_len_of_array():
    source = """
    fn main() -> int32 {
        let xs: int32[7];
        return len(xs) as int32;
    }
    """
    assert run(source) == 7


def test_len_of_inferred_array():
    source = """
    fn main() -> int32 {
        let xs: int32[] = [2, 3, 5, 7, 11, 13];
        return len(xs) as int32;
    }
    """
    assert run(source) == 6


def test_len_dimensions_of_multidim():
    source = """
    fn main() -> int32 {
        let grid: int32[3][4];
        return (len(grid) * 10 + len(grid[0])) as int32;   // 34
    }
    """
    assert run(source) == 34


def test_len_as_a_loop_bound():
    source = """
    fn main() -> int32 {
        let xs: int32[] = [4, 8, 15, 16, 23, 42];
        let sum: int32 = 0;
        let i: uint64 = 0;
        while (i < len(xs)) { sum = sum + xs[i]; i = i + 1; }
        return sum;     // 108
    }
    """
    assert run(source) == 108


def test_len_adapts_to_an_int32_counter():
    # len is an adaptable constant, so it compares against int32 without a cast.
    source = """
    fn main() -> int32 {
        let xs: int32[5];
        let i: int32 = 0;
        while (i < len(xs)) { i = i + 1; }
        return i;
    }
    """
    assert run(source) == 5


def test_len_is_a_compile_time_constant():
    ir_text = compile_ir(
        "fn main() -> int32 { let xs: int32[5]; return len(xs) as int32; }"
    )
    assert "i64 5" in ir_text  # the count is a constant, not a runtime computation


def test_len_requires_an_array():
    with pytest.raises(LangError, match=r"len\(\) requires an array, got int32\*"):
        compile_ir(
            "fn main() -> int32 { let p: int32* = null; return len(p) as int32; }"
        )
