"""Named compile-time constants: const NAME [: type] = value;

Constants have no storage -- references are folded in -- and an integer const
can size an array. The initializer must be a compile-time constant expression.
"""

import pytest

from mcc.driver import compile_to_ir
from mcc.errors import LangError
from mcc.nodes import Const
from helpers import _execute, compile_ir, parse, run


def test_const_parses():
    program = parse("const DEBUG = 1;")
    (const,) = program.consts
    assert isinstance(const, Const) and const.name == "DEBUG" and const.type_name is None


def test_value_is_substituted():
    assert run("const DEBUG = 7; fn main() -> int32 { return DEBUG; }") == 7


def test_no_storage_is_emitted():
    # A const is folded in, not a global variable -- no symbol is defined.
    ir_text = compile_ir("const DEBUG = 7; fn main() -> int32 { return DEBUG; }")
    assert '@"DEBUG"' not in ir_text
    assert "i32 7" in ir_text


def test_untyped_int_const_is_adaptable():
    # No annotation: the value keeps adapting like a literal, here to uint64.
    source = """
    const MAX = 1024;
    fn main() -> int32 {
        let n: uint64 = MAX;
        return (n / 2) as int32;
    }
    """
    assert run(source) == 512


def test_typed_const():
    source = """
    const MAX: uint64 = 1024;
    fn main() -> int32 { return (MAX / 4) as int32; }
    """
    assert run(source) == 256


def test_const_sizes_a_local_array():
    source = """
    const N = 5;
    fn main() -> int32 {
        let buf: int32[N];
        let i: int32 = 0;
        while (i < N) { buf[i] = i * i; i = i + 1; }
        return buf[4] + len(buf) as int32;   // 16 + 5
    }
    """
    assert run(source) == 21


def test_const_sizes_a_static_array():
    source = """
    const N = 4;
    @static let table: int32[N];
    fn main() -> int32 {
        table[3] = 9;
        return table[3] + len(table) as int32;   // 9 + 4
    }
    """
    assert run(source) == 13


def test_const_sizes_a_multidim_array():
    source = """
    const ROWS = 2;
    const COLS = 3;
    fn main() -> int32 {
        let grid: int32[ROWS][COLS];
        grid[1][2] = 7;
        return grid[1][2] + (len(grid) * 10 + len(grid[0])) as int32;   // 7 + 23
    }
    """
    assert run(source) == 30


def test_const_arithmetic_and_references():
    source = """
    const A = 3;
    const B = A * 4 + 1;        // 13
    const SZ = sizeof(int64);   // 8
    fn main() -> int32 { return (B + SZ) as int32; }
    """
    assert run(source) == 21


def test_const_cast_folds():
    # 300 truncated to a uint8 is 44, at compile time.
    assert run("const X = 300 as uint8; fn main() -> int32 { return X as int32; }") == 44


def test_char_const():
    assert run("const NL = '\\n'; fn main() -> int32 { return NL as int32; }") == 10


def test_float_const():
    source = """
    const PI: float64 = 3.5;
    fn main() -> int32 { return (PI * 2.0) as int32; }
    """
    assert run(source) == 7


def test_bool_const_in_a_condition():
    source = """
    const ENABLED = true;
    fn main() -> int32 {
        if (ENABLED) { return 1; }
        return 0;
    }
    """
    assert run(source) == 1


def test_comparison_const():
    assert run("const BIG = 5 > 3; fn main() -> int32 { if (BIG) { return 1; } return 0; }") == 1


def test_string_const(capfd):
    source = (
        "#include <stdio.h>\n"
        'const GREETING = "hi, const";\n'
        'fn main() -> int32 { printf("%s\\n", GREETING); return 0; }\n'
    )
    run(source)
    assert capfd.readouterr().out == "hi, const\n"


# --- errors ---

def test_cannot_assign_to_a_const():
    with pytest.raises(LangError, match="cannot assign to constant 'X'"):
        compile_ir("const X = 1; fn main() -> int32 { X = 2; return X; }")


def test_initializer_must_be_constant():
    with pytest.raises(LangError, match="must be a compile-time constant"):
        compile_ir(
            "fn side() -> int32 { return 1; }\n"
            "const X = side();\n"
            "fn main() -> int32 { return 0; }"
        )


def test_duplicate_const_is_an_error():
    with pytest.raises(LangError, match="constant 'X' already defined"):
        compile_ir("const X = 1; const X = 2; fn main() -> int32 { return 0; }")


def test_const_array_size_must_be_positive():
    with pytest.raises(LangError, match="array size must be at least 1"):
        compile_ir("const N = 0; fn main() -> int32 { let b: int32[N]; return 0; }")


def test_unknown_const_array_size():
    with pytest.raises(LangError, match="unknown array size 'BOGUS'"):
        compile_ir("fn main() -> int32 { let b: int32[BOGUS]; return 0; }")


def test_non_integer_const_array_size():
    with pytest.raises(LangError, match="must be an integer constant"):
        compile_ir('const S = "x"; fn main() -> int32 { let b: int32[S]; return 0; }')


def test_typed_const_out_of_range():
    with pytest.raises(LangError, match="out of range for uint8"):
        compile_ir("const X: uint8 = 300; fn main() -> int32 { return 0; }")


def test_typed_const_type_mismatch():
    with pytest.raises(LangError, match="expected int32\\*, got int32"):
        compile_ir("const P: int32* = 5; fn main() -> int32 { return 0; }")


def test_static_and_const_cannot_combine():
    with pytest.raises(LangError, match="do not apply"):
        compile_ir("@static const X = 1; fn main() -> int32 { return 0; }")


# --- across files ---

def test_imported_const(tmp_path):
    (tmp_path / "config.mc").write_text("const WIDTH = 80;\n")
    (tmp_path / "main.mc").write_text(
        'import "config";\n'
        "fn main() -> int32 { let row: int32[WIDTH]; return len(row) as int32; }\n"
    )
    assert _execute(compile_to_ir(tmp_path / "main.mc", ())) == 80


def test_private_const_is_not_importable(tmp_path):
    (tmp_path / "config.mc").write_text("@private const SECRET = 42;\n")
    (tmp_path / "main.mc").write_text(
        'import "config";\nfn main() -> int32 { return SECRET; }\n'
    )
    with pytest.raises(LangError, match="constant 'SECRET' is private"):
        compile_to_ir(tmp_path / "main.mc", ())
