"""Compound assignment: `target op= value` means `target = target op value`,
with the target's address computed exactly once."""

import pytest

from mcc.errors import LangError
from helpers import compile_ir, run

# A program that runs `body` and returns `x` so a test can assert the result
# through main's exit status.
PROG = (
    'import "libc/stdio";\n'
    "fn main() -> int32 {{\n"
    "    let x: int32 = {start};\n"
    "    {body}\n"
    "    return x;\n"
    "}}\n"
)


def result_of(start, body):
    return run(PROG.format(start=start, body=body))


@pytest.mark.parametrize(
    "op, start, rhs, expected",
    [
        ("+=", 10, 5, 15),
        ("-=", 10, 3, 7),
        ("*=", 6, 7, 42),
        ("/=", 20, 6, 3),   # signed division truncates
        ("%=", 20, 6, 2),
        ("&=", 12, 10, 8),
        ("|=", 12, 3, 15),
        ("^=", 12, 10, 6),
        ("<<=", 3, 2, 12),
        (">>=", 40, 2, 10),
    ],
)
def test_each_operator_on_a_variable(op, start, rhs, expected):
    assert result_of(start, f"x {op} {rhs};") == expected


def test_desugars_to_read_op_write():
    assert result_of(2, "x += 3; x *= 4;") == 20


def test_signed_shift_right_is_arithmetic():
    ir_text = compile_ir(PROG.format(start=-8, body="x >>= 1;"))
    assert "ashr" in ir_text and "lshr" not in ir_text


def test_unsigned_division_uses_udiv():
    src = (
        'import "libc/stdio";\n'
        "fn main() -> int32 {\n"
        "    let x: uint32 = 20;\n"
        "    x /= 6;\n"
        "    return x as int32;\n"
        "}\n"
    )
    ir_text = compile_ir(src)
    assert "udiv" in ir_text and "sdiv" not in ir_text


def test_target_evaluated_once(capfd):
    # The index expression `next()` has a side effect; it must run once, so the
    # counter advances by one and only one element is incremented.
    src = (
        'import "std/io";\n'
        "fn next(counter: int32*) -> int32 {\n"
        "    let i = *counter;\n"
        "    *counter = i + 1;\n"
        "    return i;\n"
        "}\n"
        "fn main() -> int32 {\n"
        "    let arr: int32[3] = [10, 20, 30];\n"
        "    let counter: int32 = 1;\n"
        "    arr[next(&counter)] += 100;\n"
        '    println("{} {} {} {}", arr[0], arr[1], arr[2], counter);\n'
        "    return 0;\n"
        "}\n"
    )
    run(src)
    # arr[1] gained 100, the others are untouched, and counter advanced once.
    assert capfd.readouterr().out.strip() == "10 120 30 2"


def test_target_call_emitted_once_in_ir():
    src = (
        'import "std/io";\n'
        "fn idx() -> int32 { return 0; }\n"
        "fn main() -> int32 {\n"
        "    let arr: int32[2] = [1, 2];\n"
        "    arr[idx()] += 1;\n"
        "    return arr[0];\n"
        "}\n"
    )
    assert compile_ir(src).count('call i32 @"idx"') == 1


def test_through_pointer(capfd):
    src = (
        'import "std/io";\n'
        "fn main() -> int32 {\n"
        "    let n: int32 = 8;\n"
        "    let p = &n;\n"
        "    *p -= 3;\n"
        '    println("{}", n);\n'
        "    return 0;\n"
        "}\n"
    )
    run(src)
    assert capfd.readouterr().out.strip() == "5"


def test_struct_field_value_and_arrow(capfd):
    src = (
        'import "std/io";\n'
        "struct point { x: int32; y: int32; }\n"
        "fn main() -> int32 {\n"
        "    let p: point;\n"
        "    p.x = 4;\n"
        "    p.x *= 5;\n"
        "    let pp = &p;\n"
        "    pp->y = 3;\n"
        "    pp->y += 7;\n"
        '    println("{} {}", p.x, p.y);\n'
        "    return 0;\n"
        "}\n"
    )
    run(src)
    assert capfd.readouterr().out.strip() == "20 10"


def test_float_arithmetic(capfd):
    src = (
        'import "std/io";\n'
        "fn main() -> int32 {\n"
        "    let f: float64 = 2.0;\n"
        "    f += 0.5;\n"
        "    f *= 4.0;\n"
        '    println("{.1f}", f);\n'  # precision: the {.1f} modifier carries it now
        "    return 0;\n"
        "}\n"
    )
    run(src)
    assert capfd.readouterr().out.strip() == "10.0"


def test_rhs_is_a_full_expression():
    assert result_of(1, "x += 2 * 3 + 1;") == 8


def test_volatile_target_keeps_volatile_load_and_store():
    src = (
        'import "std/io";\n'
        "@volatile @static let flag: int32;\n"
        "fn main() -> int32 {\n"
        "    flag += 1;\n"
        "    return flag;\n"
        "}\n"
    )
    ir_text = compile_ir(src)
    assert "load volatile" in ir_text and "store volatile" in ir_text


def test_rejects_const_parameter():
    src = (
        "fn f(const x: int32) -> int32 { x += 1; return x; }\n"
        "fn main() -> int32 { return f(3); }\n"
    )
    with pytest.raises(LangError, match="cannot assign to const parameter"):
        compile_ir(src)


def test_rejects_const_field_of_const_parameter():
    src = (
        "struct point { x: int32; y: int32; }\n"
        "fn f(const p: point) -> int32 { p.x += 1; return p.x; }\n"
        "fn main() -> int32 { return 0; }\n"
    )
    with pytest.raises(LangError, match="cannot assign to a field of a const parameter"):
        compile_ir(src)


def test_rejects_constant():
    src = (
        "const C: int32 = 5;\n"
        "fn main() -> int32 { C += 1; return 0; }\n"
    )
    with pytest.raises(LangError, match="cannot assign to constant"):
        compile_ir(src)


def test_rejects_unsupported_operator_for_pointer():
    # `p += n` / `p -= n` now move the pointer (see test_pointers.py), but the
    # multiplicative and bitwise operators remain unsupported on pointers.
    src = (
        "fn main() -> int32 {\n"
        "    let n: int32 = 0;\n"
        "    let p = &n;\n"
        "    p *= 2;\n"
        "    return 0;\n"
        "}\n"
    )
    with pytest.raises(LangError, match="operand of '\\*'"):
        compile_ir(src)


def test_rejects_narrowing_result_without_cast():
    # int8 += int64 would widen the result to int64, which cannot land back in
    # the int8 slot without an explicit cast -- exactly as `x = x + y` behaves.
    src = (
        "fn main() -> int32 {\n"
        "    let x: int8 = 1;\n"
        "    let y: int64 = 2;\n"
        "    x += y;\n"
        "    return 0;\n"
        "}\n"
    )
    with pytest.raises(LangError):
        compile_ir(src)
