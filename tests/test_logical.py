"""Short-circuiting `and` / `or` operators."""

from mcc.nodes import Binary, Logical
from helpers import compile_ir, parse, run


def first_expr(body):
    (func,) = parse("fn main() { x = " + body + "; }").functions
    return func.body[0].value


def test_and_binds_tighter_than_or():
    # a or b and c  ==  a or (b and c)
    expr = first_expr("a or b and c")
    assert isinstance(expr, Logical) and expr.op == "or"
    assert isinstance(expr.rhs, Logical) and expr.rhs.op == "and"


def test_comparisons_bind_tighter_than_and_or():
    # a > 0 or b < 0  ==  (a > 0) or (b < 0), no parens needed
    expr = first_expr("a > 0 or b < 0")
    assert isinstance(expr, Logical) and expr.op == "or"
    assert isinstance(expr.lhs, Binary) and expr.lhs.op == ">"
    assert isinstance(expr.rhs, Binary) and expr.rhs.op == "<"


def test_result_is_bool():
    source = """
    fn main() -> int32 {
        let t: bool = true and true;
        let f: bool = true and false;
        return (t as int32) * 10 + (f as int32);   // 10
    }
    """
    assert run(source) == 10


def test_truth_table_and():
    source = """
    fn main() -> int32 {
        let bits: int32 = 0;
        if (true  and true)  { bits = bits + 1; }   // +1
        if (true  and false) { bits = bits + 2; }
        if (false and true)  { bits = bits + 4; }
        return bits;
    }
    """
    assert run(source) == 1


def test_truth_table_or():
    source = """
    fn main() -> int32 {
        let bits: int32 = 0;
        if (false or false) { bits = bits + 1; }
        if (true  or false) { bits = bits + 2; }    // +2
        if (false or true)  { bits = bits + 4; }    // +4
        return bits;
    }
    """
    assert run(source) == 6


def test_precedence_against_comparisons_at_runtime():
    # sign_test(a, b): a > 0 or (a < 0 and b < 0)
    source = """
    fn sign_test(a: int32, b: int32) -> bool {
        return a > 0 or a < 0 and b < 0;
    }
    fn main() -> int32 {
        let r: int32 = 0;
        if (sign_test(5, 0))   { r = r + 1; }     // a>0          -> +1
        if (sign_test(-2, -3)) { r = r + 2; }     // a<0 and b<0  -> +2
        if (sign_test(-2, 3))  { r = r + 4; }     // a<0 b>=0     -> no
        return r;
    }
    """
    assert run(source) == 3


def test_and_short_circuits(capfd):
    # The right operand must not run when the left is false.
    run(
        """
        import "libc/stdio";
        fn loud() -> bool { printf("ran "); return true; }
        fn main() -> int32 {
            if (false and loud()) {}
            if (true and loud()) {}    // only this one runs loud()
            return 0;
        }
        """
    )
    assert capfd.readouterr().out == "ran "


def test_or_short_circuits(capfd):
    run(
        """
        import "libc/stdio";
        fn loud() -> bool { printf("ran "); return false; }
        fn main() -> int32 {
            if (true or loud()) {}
            if (false or loud()) {}    // only this one runs loud()
            return 0;
        }
        """
    )
    assert capfd.readouterr().out == "ran "


def test_integer_operands_allowed():
    # and/or accept any condition: bool or integer (non-zero is true).
    source = """
    fn main() -> int32 {
        let flags: int32 = 6;
        if (flags and 1) { return 1; }   // 6 is non-zero, 1 is non-zero
        return 0;
    }
    """
    assert run(source) == 1


def test_chained_and_or_emits_phi():
    ir_text = compile_ir(
        "fn main() -> int32 {\n"
        "    let r: bool = (1 < 2) and (3 < 4) or (5 < 6);\n"
        "    return r as int32;\n"
        "}"
    )
    assert "= phi" in ir_text  # short-circuit merges via a phi node
