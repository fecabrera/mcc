"""Struct literals: `struct Name { field = expr, ... }`."""

import pytest

from mcc.errors import LangError
from mcc.nodes import StructLit
from helpers import compile_ir, parse, run

POINT = "struct point { x: int32; y: int32; }\n"


# --------------------------------------------------------------------- parser


def test_struct_literal_parses():
    (stmt,) = parse(
        "fn f() { let p = struct point { x = 6, y = 4 }; }"
    ).functions[0].body
    lit = stmt.value
    assert isinstance(lit, StructLit)
    assert lit.type_ref.name == "point"
    assert [name for name, _ in lit.fields] == ["x", "y"]


def test_struct_literal_trailing_comma_and_empty():
    (with_comma,) = parse(
        "fn f() { let p = struct point { x = 1, }; }"
    ).functions[0].body
    assert [name for name, _ in with_comma.value.fields] == ["x"]

    (empty,) = parse("fn f() { let p = struct point { }; }").functions[0].body
    assert empty.value.fields == []


def test_generic_struct_literal_parses():
    (stmt,) = parse(
        "fn f() { let p = struct pair<int32, uint8*> { a = 1, b = null }; }"
    ).functions[0].body
    assert [str(t) for t in stmt.value.type_ref.args] == ["int32", "uint8*"]


# -------------------------------------------------------------------- codegen


def test_struct_literal_zero_initializes_omitted_fields():
    ir_text = compile_ir(
        POINT + "fn f() -> int32 { let p = struct point { x = 9 }; return p.y; }"
    )
    # the temporary is zero-filled before the named fields are stored
    assert "zeroinitializer" in ir_text


def test_struct_literal_sets_fields():
    source = POINT + """
        fn main() -> int32 {
            let p = struct point { x = 6, y = 4 };
            return p.x * 10 + p.y;
        }
    """
    assert run(source) == 64


def test_struct_literal_omitted_field_is_zero():
    source = POINT + """
        fn main() -> int32 {
            let p = struct point { x = 7 };
            return p.x + p.y;     // y defaulted to 0
        }
    """
    assert run(source) == 7


def test_empty_struct_literal_is_all_zero():
    source = POINT + """
        fn main() -> int32 {
            let p = struct point { };
            return p.x + p.y;
        }
    """
    assert run(source) == 0


def test_generic_struct_literal_runs():
    source = """
        struct pair<A, B> { a: A; b: B; }
        fn main() -> int32 {
            let p = struct pair<int32, int32> { a = 30, b = 12 };
            return p.a + p.b;
        }
    """
    assert run(source) == 42


def test_nested_struct_literal():
    source = """
        struct point { x: int32; y: int32; }
        struct line { from: struct point; to: struct point; }
        fn main() -> int32 {
            let l = struct line {
                from = struct point { x = 1, y = 2 },
                to = struct point { x = 3, y = 4 },
            };
            return l.from.x + l.from.y + l.to.x + l.to.y;
        }
    """
    assert run(source) == 10


def test_struct_literal_as_value_through_pointer():
    source = POINT + """
        fn main() -> int32 {
            let p = struct point { x = 1, y = 2 };
            let q = &p;
            *q = struct point { x = 40, y = 2 };
            return q->x + q->y;
        }
    """
    assert run(source) == 42


def test_struct_literal_adapts_untyped_constant():
    source = """
        struct box { v: uint64; }
        fn main() -> int32 {
            let b = struct box { v = 5 };   // 5 adapts to uint64
            return b.v as int32;
        }
    """
    assert run(source) == 5


# --------------------------------------------------------------------- errors


def test_unknown_field_is_rejected():
    with pytest.raises(LangError, match="no field 'z'"):
        compile_ir(POINT + "fn f() { let p = struct point { z = 1 }; }")


def test_duplicate_field_is_rejected():
    with pytest.raises(LangError, match="set twice"):
        compile_ir(POINT + "fn f() { let p = struct point { x = 1, x = 2 }; }")


def test_non_struct_type_is_rejected():
    with pytest.raises(LangError, match="needs a struct type"):
        compile_ir("fn f() { let p = struct int32 { x = 1 }; }")
