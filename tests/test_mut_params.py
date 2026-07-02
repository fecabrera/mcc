"""``mut`` parameters: write-through hidden references and the non-escape rules."""

import pytest

from mcc.errors import LangError
from helpers import compile_ir, parse, run


# --------------------------------------------------------------------- parser


def test_mut_param_parses():
    (func,) = parse("fn set(mut out: int32) { out = 7; }").functions
    assert func.mut_params == {"out"}
    assert func.const_params == set()


def test_mut_and_const_params_coexist():
    (func,) = parse(
        "fn f(a: int32, const b: struct s, mut c: int32) {}"
    ).functions
    assert func.const_params == {"b"} and func.mut_params == {"c"}


def test_const_mut_combination_rejected():
    with pytest.raises(LangError, match="a parameter cannot be both const and mut"):
        parse("fn f(const mut n: int32) {}")


def test_mut_rejected_on_extern():
    message = "mut parameters are not allowed on @extern functions"
    with pytest.raises(LangError, match=message):
        parse("@extern fn f(mut n: int32);")


def test_mut_rejected_on_asm():
    message = "mut parameters are not allowed on @asm functions"
    with pytest.raises(LangError, match=message):
        parse('@asm fn f(mut n: int32) { "nop" }')


def test_mut_keyword_is_reserved():
    with pytest.raises(LangError):
        parse("fn main() -> int32 { let mut = 1; return 0; }")


# ---------------------------------------------------------------- convention


def test_mut_scalar_lowers_to_pointer_parameter():
    ir_text = compile_ir(
        "fn set(mut out: int32) { out = 7; }\n"
        "fn main() -> int32 { let x: int32 = 0; set(x); return x; }"
    )
    assert 'define void @"set"(i32* ' in ir_text


def test_mut_argument_shares_callers_storage():
    ir_text = compile_ir(
        "fn set(mut out: int32) { out = 7; }\n"
        "fn main() -> int32 { let x: int32 = 0; set(x); return x; }"
    )
    # The call passes the variable's own alloca, not a spilled temporary.
    assert 'call void @"set"(i32* %"x")' in ir_text


# ------------------------------------------------------------- write-through


def test_mut_scalar_write_reaches_caller():
    assert run(
        "fn set(mut out: int32) { out = 7; }\n"
        "fn main() -> int32 { let x: int32 = 0; set(x); return x; }"
    ) == 7


def test_mut_rvalue_use_reads_current_value():
    assert run(
        "fn bump(mut n: int32) -> int32 { n = n + 1; return n * 10; }\n"
        "fn main() -> int32 { let x: int32 = 4; return bump(x) + x; }"
    ) == 55  # returns 50, and the caller's x is now 5


def test_mut_compound_assignment_writes_through():
    assert run(
        "fn double(mut n: int32) { n *= 2; }\n"
        "fn main() -> int32 { let x: int32 = 21; double(x); return x; }"
    ) == 42


def test_mut_struct_field_projection():
    assert run(
        "struct point { x: int32; y: int32; }\n"
        "fn mirror(mut p: struct point) {\n"
        "    let t = p.x;\n"
        "    p.x = p.y;\n"
        "    p.y = t;\n"
        "}\n"
        "fn main() -> int32 {\n"
        "    let p = point { x = 1, y = 2 };\n"
        "    mirror(p);\n"
        "    return p.x * 10 + p.y;\n"
        "}"
    ) == 21


def test_mut_array_element_write():
    assert run(
        "fn fill(mut a: int32[3]) { a[0] = 1; a[1] = 2; a[2] = 3; }\n"
        "fn main() -> int32 {\n"
        "    let a: int32[3] = [0, 0, 0];\n"
        "    fill(a);\n"
        "    return a[0] * 100 + a[1] * 10 + a[2];\n"
        "}"
    ) == 123


def test_mut_pointer_param_repoints_callers_pointer():
    assert run(
        "fn repoint(mut p: int32*, target: int32*) { p = target; }\n"
        "fn main() -> int32 {\n"
        "    let a: int32[2] = [7, 9];\n"
        "    let p = &a[0];\n"
        "    repoint(p, &a[1]);\n"
        "    return *p;\n"
        "}"
    ) == 9


def test_mut_write_inside_defer_reaches_caller():
    assert run(
        "fn f(mut n: int32) -> int32 {\n"
        "    defer n = 42;\n"
        "    return n;\n"
        "}\n"
        "fn main() -> int32 {\n"
        "    let x: int32 = 5;\n"
        "    let before = f(x);\n"
        "    return x * 10 + before;\n"
        "}"
    ) == 425


def test_mut_relending_and_recursion():
    assert run(
        "fn bump(mut n: int32) { n += 1; }\n"
        "fn twice(mut n: int32) { bump(n); bump(n); }\n"
        "fn count(mut n: int32, steps: int32) {\n"
        "    if (steps == 0) { return; }\n"
        "    n += 1;\n"
        "    count(n, steps - 1);\n"
        "}\n"
        "fn main() -> int32 {\n"
        "    let x: int32 = 0;\n"
        "    twice(x);\n"
        "    count(x, 3);\n"
        "    return x;\n"
        "}"
    ) == 5


def test_mut_field_of_by_value_param_is_allowed():
    # `s` is the callee's own mutable copy; writing a field of it through a
    # mut argument is consistent with `s.field = x` being legal there.
    assert run(
        "struct s { x: int32; }\n"
        "fn set(mut n: int32) { n = 9; }\n"
        "fn touch(v: struct s) -> int32 { set(v.x); return v.x; }\n"
        "fn main() -> int32 {\n"
        "    let v = s { x = 1 };\n"
        "    let got = touch(v);\n"
        "    return got * 10 + v.x;\n"
        "}"
    ) == 91  # the callee's copy changed; the caller's did not


def test_mut_aliasing_two_params_same_variable():
    # Both references share the storage, like two C pointers; last write wins.
    assert run(
        "fn f(mut a: int32, mut b: int32) { a = 1; b = 2; }\n"
        "fn main() -> int32 { let x: int32 = 0; f(x, x); return x; }"
    ) == 2


# ------------------------------------------------------------------ generics


def test_generic_swap():
    assert run(
        "fn swap<T>(mut a: T, mut b: T) { let t = a; a = b; b = t; }\n"
        "fn main() -> int32 {\n"
        "    let u: int32 = 3;\n"
        "    let v: int32 = 9;\n"
        "    swap(u, v);\n"
        "    let f = 1.5;\n"
        "    let g = 2.5;\n"
        "    swap(f, g);\n"
        "    return u * 10 + (g < f ? 1 : 0);\n"
        "}"
    ) == 91


def test_generic_mut_struct():
    assert run(
        "struct box<T> { value: T; }\n"
        "fn clear<T>(mut b: struct box<T>) { b.value = 0 as T; }\n"
        "fn main() -> int32 {\n"
        "    let b = box { value = 7 as int32 };\n"
        "    clear(b);\n"
        "    return b.value;\n"
        "}"
    ) == 0


def test_generic_mut_type_must_match_instantiation():
    # The mut param's concrete type doesn't match the argument's storage;
    # inference (from `b`) succeeds, so the exact-lvalue check fires.
    with pytest.raises(LangError, match="expected a int64 lvalue, got int32"):
        compile_ir(
            "fn f<T>(mut a: int64, b: T) {}\n"
            "fn main() -> int32 {\n"
            "    let x: int32 = 1;\n"
            "    let y: int64 = 2;\n"
            "    f(x, y);\n"
            "    return 0;\n"
            "}"
        )


def test_generic_overloads_must_agree_on_mut():
    message = "overloads of 'f' disagree on which parameters are mut"
    with pytest.raises(LangError, match=message):
        compile_ir(
            "fn f<T>(mut a: T) {}\n"
            "fn f<T>(a: T*) {}\n"
            "fn main() -> int32 { let x: int32 = 1; f(x); return 0; }"
        )


# ---------------------------------------------------------------- rejections


def test_mut_argument_must_be_an_lvalue():
    message = (
        "argument 1 of 'f' is not assignable; a mut parameter needs a "
        "variable, field, element, or dereference"
    )
    with pytest.raises(LangError, match=message):
        compile_ir("fn f(mut n: int32) {}\nfn main() -> int32 { f(3); return 0; }")


def test_mut_argument_rejects_call_result():
    with pytest.raises(LangError, match="is not assignable"):
        compile_ir(
            "fn g() -> int32 { return 1; }\n"
            "fn f(mut n: int32) {}\n"
            "fn main() -> int32 { f(g()); return 0; }"
        )


def test_mut_argument_rejects_const_param():
    message = "cannot pass a const parameter as a mut argument; it is read-only"
    with pytest.raises(LangError, match=message):
        compile_ir(
            "fn f(mut n: int32) {}\n"
            "fn g(const c: int32) { f(c); }\n"
            "fn main() -> int32 { return 0; }"
        )


def test_mut_argument_requires_exact_type():
    with pytest.raises(LangError, match="expected a int64 lvalue, got int32"):
        compile_ir(
            "fn f(mut n: int64) {}\n"
            "fn main() -> int32 { let x: int32 = 1; f(x); return 0; }"
        )


def test_mut_argument_rejects_signedness_mismatch():
    # int32 and uint32 share the same LLVM i32; sharing storage would
    # silently alias the wrong signedness, so the LangTypes must match.
    with pytest.raises(LangError, match="expected a uint32 lvalue, got int32"):
        compile_ir(
            "fn f(mut n: uint32) {}\n"
            "fn main() -> int32 { let x: int32 = 1; f(x); return 0; }"
        )


def test_address_of_mut_param_rejected():
    message = "cannot take the address of a mut parameter"
    with pytest.raises(LangError, match=message):
        compile_ir(
            "fn f(mut n: int32) { let p = &n; }\nfn main() -> int32 { return 0; }"
        )


def test_address_of_mut_param_field_rejected():
    with pytest.raises(LangError, match="cannot take the address of a mut parameter"):
        compile_ir(
            "struct s { x: int32; }\n"
            "fn f(mut v: struct s) { let p = &v.x; }\n"
            "fn main() -> int32 { return 0; }"
        )


def test_mut_function_is_not_a_function_value():
    message = "cannot take a function value of 'f'"
    with pytest.raises(LangError, match=message):
        compile_ir(
            "fn f(mut n: int32) {}\n"
            "fn main() -> int32 { let g = f; return 0; }"
        )


def test_mut_argument_rejects_volatile_storage():
    message = "cannot pass @volatile storage as a mut argument"
    with pytest.raises(LangError, match=message):
        compile_ir(
            "@extern @volatile let r: int32;\n"
            "fn f(mut n: int32) {}\n"
            "fn main() -> int32 { f(r); return 0; }"
        )


def test_mut_argument_rejects_packed_field():
    message = "cannot pass a @packed field as a mut argument"
    with pytest.raises(LangError, match=message):
        compile_ir(
            "@packed struct w { a: uint8; n: int32; }\n"
            "fn f(mut n: int32) {}\n"
            "fn main() -> int32 { let v: struct w; f(v.n); return 0; }"
        )


def test_string_literal_does_not_adapt_to_mut_slice():
    with pytest.raises(LangError, match="is not assignable"):
        compile_ir(
            'fn f(mut s: slice<char>) {}\nfn main() -> int32 { f("hi"); return 0; }'
        )


def test_mut_argument_rejects_const_slice_element():
    with pytest.raises(LangError, match="cannot pass a read-only const "):
        compile_ir(
            "fn f(mut c: char) {}\n"
            "fn g(s: slice<const char>) { f(s[0]); }\n"
            "fn main() -> int32 { return 0; }"
        )
