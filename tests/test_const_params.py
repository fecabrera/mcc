"""const parameters: read-only params, with structs passed by hidden reference.

A `const` parameter cannot be mutated in the body. A const struct is handed
over as a pointer to the caller's storage instead of copied by value -- value
semantics without the copy, since the callee promises not to write it.
"""

import pytest

from mcc.errors import LangError
from helpers import compile_ir, parse, run

BIG = "struct big { a: int64; b: int64; c: int64; }\n"


# --- parsing ---


def test_const_param_parses_onto_the_func():
    (fn,) = parse("fn f(const s: struct big, n: int64) {}").functions
    assert fn.const_params == {"s"}


# --- ABI: struct const param is a pointer ---


def test_const_struct_param_is_passed_by_pointer():
    ir = compile_ir(BIG + "fn sum(const s: struct big) -> int64 { return s.a; }")
    assert 'define i64 @"sum"(%"big"* ' in ir


def test_non_const_struct_param_stays_by_value():
    ir = compile_ir(BIG + "fn sum(s: struct big) -> int64 { return s.a; }")
    assert 'define i64 @"sum"(%"big" ' in ir


def test_const_scalar_param_stays_by_value():
    # const on a scalar only makes it read-only; it is not a hidden reference.
    ir = compile_ir("fn f(const n: int64) -> int64 { return n; }")
    assert 'define i64 @"f"(i64 ' in ir


# --- runtime behavior ---


def test_const_struct_call_runs():
    src = (
        BIG
        + """
    fn sum(const s: struct big) -> int64 { return s.a + s.b + s.c; }
    fn main() -> int32 {
        let v: struct big;
        v.a = 1; v.b = 2; v.c = 3;
        return sum(v) as int32;
    }
    """
    )
    assert run(src) == 6


def test_rvalue_argument_is_materialized():
    # A by-value struct result has no storage; it is spilled to a temp.
    src = (
        BIG
        + """
    fn make() -> struct big { let v: struct big; v.a = 10; v.b = 20; v.c = 0; return v; }
    fn sum(const s: struct big) -> int64 { return s.a + s.b + s.c; }
    fn main() -> int32 { return sum(make()) as int32; }
    """
    )
    assert run(src) == 30


def test_const_param_forwards_to_another_const_param():
    src = (
        BIG
        + """
    fn inner(const s: struct big) -> int64 { return s.a + s.b + s.c; }
    fn outer(const s: struct big) -> int64 { return inner(s); }
    fn main() -> int32 {
        let v: struct big;
        v.a = 4; v.b = 5; v.c = 6;
        return outer(v) as int32;
    }
    """
    )
    assert run(src) == 15


def test_generic_const_struct_param():
    src = """
    struct pair<T> { a: T; b: T; }
    fn first<T>(const p: struct pair<T>) -> T { return p.a; }
    fn main() -> int32 {
        let p: struct pair<int32>;
        p.a = 7; p.b = 8;
        return first(p);
    }
    """
    assert run(src) == 7


def test_const_pointer_param_may_mutate_its_pointee():
    # const on a pointer parameter freezes the pointer, not what it points at.
    src = """
    struct b { x: int64; }
    fn set(const s: struct b*) { s->x = 5; }
    fn main() -> int32 {
        let v: struct b;
        set(&v);
        return v.x as int32;
    }
    """
    assert run(src) == 5


# --- immutability errors ---


@pytest.mark.parametrize(
    "body, message",
    [
        ("s.a = 0;", "cannot assign to a field of a const parameter"),
        ("let p = &s;", "cannot take the address of a const parameter"),
    ],
)
def test_const_struct_param_is_read_only(body, message):
    with pytest.raises(LangError, match=message):
        compile_ir(BIG + f"fn f(const s: struct big) {{ {body} }}")


def test_const_scalar_param_cannot_be_assigned():
    with pytest.raises(LangError, match="cannot assign to const parameter 'n'"):
        compile_ir("fn f(const n: int64) { n = 0; }")


def test_const_struct_list_element_is_read_only():
    with pytest.raises(
        LangError, match="cannot assign to an element of a const parameter"
    ):
        compile_ir("struct b { t: uint8[4]; }\nfn f(const s: struct b) { s.t[0] = 9; }")


# --- restrictions ---


def test_const_param_rejected_on_extern():
    with pytest.raises(LangError, match="const parameters are not allowed on @extern"):
        parse("@extern fn c(const s: struct big);")


def test_function_value_of_const_struct_fn():
    # A const-struct function is a legal function value: the inferred type
    # spells the hidden-reference convention (`fn(const struct big)`), so the
    # slot holds a pointer-taking function and calls through it pass the
    # argument's address.
    src = (
        BIG
        + """
    fn sum(const s: struct big) -> int64 { return s.a; }
    fn main() -> int32 { let f = sum; let v: struct big; return f(v) as int32; }
    """
    )
    out = compile_ir(src)
    assert 'i64 (%"big"*)*' in out
