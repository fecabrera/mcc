"""Inline assembly: the @asm(...) expression and the @asm fn sugar."""

import platform

import pytest

from mcc.errors import LangError
from mcc.nodes import Asm, ExprStmt, Return, Var
from helpers import compile_ir, parse, run

# The runtime tests emit aarch64 mnemonics, so they only execute on arm64.
ON_AARCH64 = platform.machine() in ("arm64", "aarch64")
aarch64_only = pytest.mark.skipif(not ON_AARCH64, reason="needs an aarch64 host")


# --------------------------------------------------------------------- parser

def test_asm_fn_desugars_to_return_asm():
    (fn,) = parse('@asm fn rev(a: uint32) -> uint32 { "rev ${out:w}, ${0:w}" }').functions
    (stmt,) = fn.body
    assert isinstance(stmt, Return) and isinstance(stmt.value, Asm)
    asm = stmt.value
    assert asm.template == "rev ${out:w}, ${0:w}"
    assert [v.name for v in asm.inputs] == ["a"] and all(isinstance(v, Var) for v in asm.inputs)
    assert asm.out_type is not None and str(asm.out_type) == "uint32"


def test_asm_void_fn_desugars_to_exprstmt():
    (fn,) = parse('@asm fn pause() { "yield" }').functions
    (stmt,) = fn.body
    assert isinstance(stmt, ExprStmt) and isinstance(stmt.expr, Asm)
    assert stmt.expr.out_type is None


def test_asm_body_joins_lines_with_newline():
    (fn,) = parse(
        '@asm fn two(a: int64) -> int64 {\n'
        '    "mov $out, $0"\n'
        '    "add $out, $out, #1"\n'
        '}'
    ).functions
    assert fn.body[0].value.template == "mov $out, $0\nadd $out, $out, #1"


def test_asm_expression_parses_inside_a_function():
    (fn,) = parse(
        'fn f(x: int64, y: int64) -> int64 {\n'
        '    return @asm(x, y) -> int64 { "add $out, $0, $1" };\n'
        '}'
    ).functions
    asm = fn.body[0].value
    assert isinstance(asm, Asm) and len(asm.inputs) == 2


def test_empty_asm_body_is_rejected():
    with pytest.raises(LangError, match="needs at least one instruction line"):
        parse('@asm fn f(a: int64) -> int64 { }')


# -------------------------------------------------------------------- codegen

ADD = 'fn f(x: int64, y: int64) -> int64 { return @asm(x, y) -> int64 { "add $out, $0, $1" }; }'


def test_asm_emits_inline_call_with_constraints():
    ir = compile_ir(ADD)
    # one output (=r) + two inputs (r), template renumbered out->$0, inputs->$1,$2.
    assert 'asm  "add $0, $1, $2", "=r,r,r"' in ir


def test_value_asm_is_pure():
    assert "sideeffect" not in compile_ir(ADD)


def test_void_asm_is_sideeffect():
    ir = compile_ir('fn g() { @asm() { "nop" }; }')
    assert 'asm sideeffect "nop", ""' in ir


def test_width_modifier_is_passed_through():
    ir = compile_ir('@asm fn rev(a: uint32) -> uint32 { "rev ${out:w}, ${0:w}" }')
    assert "rev ${0:w}, ${1:w}" in ir


@pytest.mark.parametrize(
    "source, message",
    [
        ('fn f(a: float64) -> int64 { return @asm(a) -> int64 { "fmov $out, $0" }; }',
         "@asm operand must be an integer or pointer"),
        ('fn f() -> float64 { return @asm() -> float64 { "fmov $out, #0" }; }',
         "@asm output must be an integer or pointer"),
        ('fn f() { @asm() { "mov $out, #0" }; }',
         r"\$out' but has no output"),
        ('fn f(a: int64) { @asm(a) { "mov $3, $0" }; }',
         r"references operand \$3 but only 1 were given"),
    ],
)
def test_asm_codegen_errors(source, message):
    with pytest.raises(LangError, match=message):
        compile_ir(source)


@pytest.mark.parametrize(
    "source, message",
    [
        ('@asm @extern fn f(a: int64) -> int64;', "@asm only applies to functions"),
        ('@asm struct S { x: int32; }', "@asm only applies to functions"),
        ('@asm fn f(a: int64, ...) -> int64 { "nop" }', "cannot be variadic"),
    ],
)
def test_asm_parser_errors(source, message):
    with pytest.raises(LangError, match=message):
        parse(source)


# ------------------------------------------------------------------ execution

@aarch64_only
def test_asm_fn_runs():
    assert run(
        '@asm fn rev(a: uint32) -> uint32 { "rev ${out:w}, ${0:w}" }\n'
        'fn main() -> int32 {\n'
        '    if (rev(0x11223344) != 0x44332211) { return 1; }\n'
        '    return 0;\n'
        '}'
    ) == 0


@aarch64_only
def test_asm_expression_runs():
    assert run(
        'fn main() -> int32 {\n'
        '    let x: int64 = 20; let y: int64 = 22;\n'
        '    return @asm(x, y) -> int64 { "add $out, $0, $1" } as int32;\n'
        '}'
    ) == 42


@aarch64_only
def test_void_asm_runs():
    assert run('fn main() -> int32 { @asm() { "nop" }; return 0; }') == 0
