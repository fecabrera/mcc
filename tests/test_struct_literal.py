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


def test_generic_struct_literal_infers_type_args():
    source = """
        struct pair<A, B> { a: A; b: B; }
        fn main() -> int32 {
            let n: int64 = 30;
            let p = struct pair { a = n, b = 12 };   // A=int64, B inferred from 12
            return (p.a + p.b) as int32;
        }
    """
    assert run(source) == 42


def test_inference_typed_field_pins_parameter():
    source = """
        struct box<T> { lo: T; hi: T; }
        fn main() -> int32 {
            let n: int64 = 40;
            let b = struct box { lo = 2, hi = n };   // lo adapts to int64 from hi
            return (b.lo + b.hi) as int32;
        }
    """
    assert run(source) == 42


def test_inference_conflict_is_rejected():
    source = """
        struct box<T> { lo: T; hi: T; }
        fn f(a: int32, b: int64) { let x = struct box { lo = a, hi = b }; }
    """
    with pytest.raises(LangError, match="conflicting types for type parameter T"):
        compile_ir(source)


def test_uninferable_parameter_is_rejected():
    source = """
        struct holder<T> { tag: int32; item: T; }
        fn f() { let h = struct holder { tag = 1 }; }
    """
    with pytest.raises(LangError, match="cannot infer type parameter.*T"):
        compile_ir(source)


def test_unknown_field_on_generic_literal_is_rejected():
    source = """
        struct box<T> { lo: T; hi: T; }
        fn f() { let h = struct box { nope = 1 }; }
    """
    with pytest.raises(LangError, match="no field 'nope'"):
        compile_ir(source)


# --------------------------------------------------------- default member values


def test_field_default_parses():
    (decl,) = parse("struct cfg { cap: int32 = 16; name: uint8*; }").structs
    assert decl.defaults.keys() == {"cap"}
    assert "name" not in decl.defaults


def test_omitted_field_uses_its_default():
    source = """
        struct cfg { cap: int32 = 16; flag: int32 = 1; }
        fn main() -> int32 {
            let c = struct cfg { };       // both defaulted
            return c.cap + c.flag;        // 16 + 1
        }
    """
    assert run(source) == 17


def test_provided_field_overrides_default():
    source = """
        struct cfg { cap: int32 = 16; }
        fn main() -> int32 {
            let c = struct cfg { cap = 5 };
            return c.cap;
        }
    """
    assert run(source) == 5


def test_default_with_generic_and_inference():
    source = """
        struct range<T> { start: T = 0; end: T; }
        fn main() -> int32 {
            let r = struct range { end = 5 };   // start defaults to 0; T from end
            return (r.end - r.start) as int32;
        }
    """
    assert run(source) == 5


def test_inherited_field_default():
    source = """
        struct base { tag: int32 = 7; }
        struct derived extends base { extra: int32 = 9; }
        fn main() -> int32 {
            let d = struct derived { };
            return d.tag + d.extra;     // 7 + 9
        }
    """
    assert run(source) == 16


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
