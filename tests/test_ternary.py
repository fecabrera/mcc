"""The ternary conditional expression: cond ? then : otherwise."""

import pytest

from mcc.errors import LangError
from mcc.nodes import Ternary
from helpers import compile_ir, parse, run


def test_parses_into_a_ternary_node():
    (func,) = parse(
        "fn main() -> int32 { return 1 > 0 ? 4 : 5; }"
    ).functions
    ret = func.body[0]
    assert isinstance(ret.value, Ternary)
    assert ret.value.then.value == 4
    assert ret.value.otherwise.value == 5


def test_selects_the_true_arm():
    assert run("fn main() -> int32 { return 1 > 0 ? 7 : 9; }") == 7


def test_selects_the_false_arm():
    assert run("fn main() -> int32 { return 1 < 0 ? 7 : 9; }") == 9


def test_an_integer_condition_is_truthy_like_c():
    # A non-zero integer is true, zero is false -- no bool needed.
    assert run("fn main() -> int32 { let n: int32 = 3; return n ? 1 : 0; }") == 1
    assert run("fn main() -> int32 { let n: int32 = 0; return n ? 1 : 0; }") == 0


def test_picks_the_larger_of_two():
    source = """
    fn max(a: int32, b: int32) -> int32 { return a > b ? a : b; }
    fn main() -> int32 { return max(7, 3) + max(2, 11); }
    """
    assert run(source) == 7 + 11


def test_is_right_associative():
    # a ? b : c ? d : e  parses as  a ? b : (c ? d : e), a sign() ladder.
    source = """
    fn sign(x: int32) -> int32 { return x > 0 ? 1 : x < 0 ? -1 : 0; }
    fn main() -> int32 { return (sign(5) + 1) * 100 + (sign(-5) + 1) * 10 + sign(0); }
    """
    assert run(source) == 200 + 0 + 0  # 2*100 + 0*10 + 0


def test_binds_looser_than_arithmetic():
    # 1 + 1 ? ... is (1 + 1) ? ...; arms are full expressions too.
    assert run("fn main() -> int32 { return 0 + 0 ? 1 + 1 : 2 + 3; }") == 5


def test_only_the_selected_arm_runs():
    # The untaken arm's side effects must not happen.
    source = """
    @static let hits: int32 = 0;
    fn bump() -> int32 { hits = hits + 1; return 1; }
    fn main() -> int32 {
        let v: int32 = 1 > 0 ? 42 : bump();
        return v + hits;   // bump() never ran, so hits stays 0
    }
    """
    assert run(source) == 42


def test_untyped_arm_adapts_to_the_other():
    # A typed uint8 arm fixes the type; the untyped constant arm adapts to it.
    source = """
    fn pick(flag: bool, c: uint8) -> uint8 { return flag ? c : 0; }
    fn main() -> int32 { return pick(true, 65) as int32; }
    """
    assert run(source) == 65


def test_two_untyped_arms_widen():
    # Both arms untyped: the result type is the wider of the two (int64 here),
    # so a value past int32 survives. Unlike a bare constant, the result is a
    # runtime value, so it is the concrete wider type rather than adaptable.
    source = """
    fn main() -> int32 {
        let n: int64 = 1 > 0 ? 4294967296 : 0;   // 1 << 32
        return (n >> 32) as int32;
    }
    """
    assert run(source) == 1


def test_null_adapts_to_a_pointer_arm():
    source = """
    fn choose(use: bool, p: int32*) -> int32* { return use ? p : null; }
    fn main() -> int32 {
        let x: int32 = 5;
        let r: int32* = choose(true, &x);
        return *r;
    }
    """
    assert run(source) == 5


def test_float_arms():
    source = """
    fn main() -> int32 {
        let f: float64 = 1 > 0 ? 2.5 : 4.5;
        return f as int32;
    }
    """
    assert run(source) == 2


def test_mismatched_arms_are_rejected():
    with pytest.raises(LangError, match="ternary branch"):
        compile_ir(
            "fn main() -> int32 {\n"
            "    let f: float64 = 1.0;\n"
            "    let x: int32 = 4;\n"
            "    return (1 > 0 ? f : x) as int32;\n"
            "}"
        )


def test_folds_in_a_const():
    source = """
    const N: int32 = 1 > 0 ? 100 : 200;
    fn main() -> int32 { return N; }
    """
    assert run(source) == 100


def test_const_ternary_needs_a_constant_condition():
    # The condition folds to a pointer (a string), not a bool or integer.
    with pytest.raises(LangError, match="constant bool or integer condition"):
        compile_ir(
            'const N: int32 = "x" ? 1 : 2;\n'
            "fn main() -> int32 { return N; }"
        )


def test_selects_a_branch_in_an_at_if():
    # A ternary is allowed inside an @if condition (evaluated at compile time).
    source = """
    @if (1 > 0 ? 1 : 0) {
        fn main() -> int32 { return 1; }
    } @else {
        fn main() -> int32 { return 0; }
    }
    """
    assert run(source) == 1
