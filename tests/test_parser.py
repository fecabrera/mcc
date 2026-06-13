import pytest

from mcc.errors import LangError
from mcc.nodes import (
    Assign, Binary, Break, Call, CharLit, Continue, ExprStmt, If, Let, Return,
    StrLit, Unary, Var,
)
from helpers import parse


def parse_main_body(body_source):
    program = parse("fn main() { " + body_source + " }")
    return program.functions[0].body


def first_expr(expr_source):
    (stmt,) = parse_main_body(expr_source + ";")
    assert isinstance(stmt, ExprStmt)
    return stmt.expr


def test_includes_collected():
    program = parse("#include <stdio.h>\n#include <stdlib.h>\nfn main() {}")
    assert program.includes == ["stdio.h", "stdlib.h"]


def test_function_signature():
    (func,) = parse("fn add(a: int32, b: int64) -> int32 { return a; }").functions
    assert func.name == "add"
    assert [(n, str(ty)) for n, ty in func.params] == [("a", "int32"), ("b", "int64")]
    assert str(func.ret_type) == "int32"
    assert func.type_params == []


def test_return_type_defaults_to_void():
    (func,) = parse("fn f() {}").functions
    assert str(func.ret_type) == "void"


def test_generic_type_parameters():
    (func,) = parse("fn pair<T, U>(a: T, b: U) {}").functions
    assert func.type_params == ["T", "U"]


def test_multiplication_binds_tighter_than_addition():
    expr = first_expr("1 + 2 * 3")
    assert isinstance(expr, Binary) and expr.op == "+"
    assert isinstance(expr.rhs, Binary) and expr.rhs.op == "*"


def test_comparison_binds_looser_than_addition():
    expr = first_expr("1 + 2 < 4")
    assert isinstance(expr, Binary) and expr.op == "<"


def test_parentheses_override_precedence():
    expr = first_expr("(1 + 2) * 3")
    assert isinstance(expr, Binary) and expr.op == "*"
    assert isinstance(expr.lhs, Binary) and expr.lhs.op == "+"


def test_unary_minus():
    expr = first_expr("-x")
    assert isinstance(expr, Unary) and expr.op == "-"
    assert isinstance(expr.operand, Var)


def test_hex_literals_decoded():
    assert first_expr("0xFF").value == 255
    assert first_expr("0X09000000").value == 150994944
    assert first_expr("10").value == 10  # leading-zero decimals stay decimal
    assert first_expr("010").value == 10


def test_char_literals_decoded():
    assert first_expr("'A'").value == 65
    assert first_expr('\'"\'').value == 34   # a double quote
    assert first_expr(r"'\n'").value == 10
    assert first_expr(r"'\0'").value == 0
    assert first_expr(r"'\''").value == 39   # an escaped single quote
    assert first_expr(r"'\\'").value == 92   # an escaped backslash
    assert isinstance(first_expr("'A'"), CharLit)


def test_char_literal_must_be_a_single_byte():
    with pytest.raises(LangError, match="not a single byte"):
        parse("fn main() { let c: uint8 = '€'; }")


def test_string_escapes_decoded():
    expr = first_expr(r'f("a\n\t\\")')
    assert isinstance(expr.args[0], StrLit)
    assert expr.args[0].value == "a\n\t\\"


def test_assignment_vs_expression_statement():
    assign, call = parse_main_body("x = 1; f(x);")
    assert isinstance(assign, Assign) and assign.name == "x"
    assert isinstance(call, ExprStmt) and isinstance(call.expr, Call)


def test_let_with_and_without_annotation():
    with_type, without = parse_main_body("let a: uint8 = 1; let b = 2;")
    assert isinstance(with_type, Let) and str(with_type.type_name) == "uint8"
    assert isinstance(without, Let) and without.type_name is None


def test_let_without_initializer():
    (stmt,) = parse_main_body("let buf: uint8*;")
    assert isinstance(stmt, Let)
    assert str(stmt.type_name) == "uint8*"
    assert stmt.value is None


def test_uninitialized_let_requires_a_type():
    with pytest.raises(LangError, match="uninitialized variable needs a type"):
        parse("fn main() { let x; }")


def test_else_if_chain_nests():
    (stmt,) = parse_main_body("if (a) { f(); } else if (b) { g(); } else { h(); }")
    assert isinstance(stmt, If)
    (nested,) = stmt.otherwise
    assert isinstance(nested, If)
    assert nested.otherwise  # the final else


def test_generic_call_type_args():
    expr = first_expr("sum<int32, uint8>(1, 2)")
    assert isinstance(expr, Call)
    assert [str(a) for a in expr.type_args] == ["int32", "uint8"]
    assert len(expr.args) == 2


def test_less_than_is_not_type_args():
    expr = first_expr("a < b")
    assert isinstance(expr, Binary) and expr.op == "<"


def test_comparison_with_generic_call_on_rhs():
    expr = first_expr("a < sum<int32>(1, 2)")
    assert isinstance(expr, Binary) and expr.op == "<"
    assert isinstance(expr.rhs, Call)
    assert [str(a) for a in expr.rhs.type_args] == ["int32"]


def test_until_is_a_negated_while():
    while_stmt, until_stmt = parse_main_body("while (a) { f(); } until (a) { f(); }")
    assert not while_stmt.until
    assert until_stmt.until


def test_break_and_continue_statements():
    # Loop-context checking happens in codegen, not here.
    brk, cont = parse_main_body("break; continue;")
    assert isinstance(brk, Break)
    assert isinstance(cont, Continue)


def test_return_without_value():
    (stmt,) = parse_main_body("return;")
    assert isinstance(stmt, Return) and stmt.value is None


def test_missing_semicolon_is_error():
    with pytest.raises(LangError, match="expected ';'"):
        parse("fn main() { let x = 1 }")


def test_unbalanced_paren_is_error():
    with pytest.raises(LangError):
        parse("fn main() { f(1; }")
