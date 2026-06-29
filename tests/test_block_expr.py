"""Block-expressions: { ...; emit value; } as an inlined, value-yielding scope."""

import pytest

from mcc.errors import LangError
from mcc.nodes import BlockExpr, Emit
from helpers import compile_ir, parse, run


def test_parses_into_a_block_expr_node():
    (func,) = parse(
        "fn main() -> int32 { let x: int32 = { emit 1; }; return x; }"
    ).functions
    let = func.body[0]
    assert isinstance(let.value, BlockExpr)
    assert isinstance(let.value.body[0], Emit)


def test_trivial_block_is_its_value():
    assert run("fn main() -> int32 { return { emit 42; }; }") == 42


def test_trivial_emit_stays_adaptable():
    # { emit <untyped constant> } adapts to the annotated type, like the bare
    # constant would -- a uint64 too wide for int32 still works.
    assert run(
        "fn main() -> int32 {\n"
        "    let n: uint64 = { emit 4294967296; };   // 1 << 32\n"
        "    return (n >> 32) as int32;\n"
        "}"
    ) == 1


def test_computes_with_local_temporaries():
    source = """
    fn main() -> int32 {
        let value: uint64 = {
            let hi: uint64 = 0xABCD;
            let lo: uint64 = 0x1234;
            emit (hi << 32) | lo;
        };
        return (value >> 32) as int32;     // 0xABCD
    }
    """
    assert run(source) == 0xABCD


def test_temporaries_do_not_leak():
    with pytest.raises(LangError, match="undefined variable 't'"):
        compile_ir(
            "fn main() -> int32 { let v: int32 = { let t: int32 = 5; emit t; }; return t; }"
        )


def test_conditional_emit_with_a_trailing_emit():
    source = """
    fn sign(x: int32) -> int32 {
        return {
            if (x > 0) { emit 1; }
            if (x < 0) { emit -1; }
            emit 0;
        };
    }
    fn main() -> int32 { return (sign(-5) + 1) * 10 + sign(7) + sign(0); }
    """
    assert run(source) == 1  # (-1 + 1)*10 + 1 + 0


def test_braceless_if_emit_with_a_trailing_emit():
    # An `emit` as a braceless if-body (not wrapped in a { } block statement).
    source = """
    fn sign(x: int32) -> int32 {
        return {
            if (x > 0) emit 1;
            if (x < 0) emit -1;
            emit 0;
        };
    }
    fn main() -> int32 {
        return (sign(-9) + 2) * 100 + (sign(5) + 2) * 10 + (sign(0) + 2);
    }
    """
    assert run(source) == 132  # (1)(3)(2)


def test_braceless_if_else_emit_with_a_trailing_emit():
    source = """
    fn pick(c: bool) -> int32 {
        return { if (c) emit 1; else emit 2; emit 9; };
    }
    fn main() -> int32 { return pick(true) * 10 + pick(false); }
    """
    assert run(source) == 12


def test_if_else_both_emit_needs_no_trailing_emit():
    # An if/else where both arms emit is exhaustive, so no trailing emit is
    # needed -- the same guarantee a function's if/else both-return gives.
    assert run(
        "fn pick(c: bool) -> int32 { return { if (c) emit 1; else emit 2; }; }\n"
        "fn main() -> int32 { return pick(true) * 10 + pick(false); }"
    ) == 12


def test_case_all_arms_and_else_emit_needs_no_trailing_emit():
    assert run(
        "fn classify(n: int32) -> int32 {\n"
        "    return { case (n) { when 0: emit 5; else: emit 9; } };\n"
        "}\n"
        "fn main() -> int32 { return classify(0) * 10 + classify(7); }"
    ) == 59


def test_usable_in_expression_positions():
    # As a binary operand, a call argument, and a return value.
    assert run("fn main() -> int32 { return { emit 20; } + { emit 22; }; }") == 42
    assert run(
        "fn id(x: int32) -> int32 { return x; }\n"
        "fn main() -> int32 { return id({ emit 41; } + 1); }"
    ) == 42


def test_nested_block_expressions():
    assert run("fn main() -> int32 { return { emit { emit 9; } * 2; }; }") == 18


def test_defer_runs_at_the_emit(capfd):
    run(
        r"""
        import "libc/stdio";
        fn main() -> int32 {
            let v: int32 = { defer printf("D"); printf("B"); emit 5; };
            printf("=%d", v);
            return 0;
        }
        """
    )
    assert capfd.readouterr().out == "BD=5"


def test_return_punches_out_of_a_block_expression():
    # `return` targets the function, `emit` only the block.
    source = """
    fn pick(c: bool) -> int32 {
        let v: int32 = { if (c) return 99; emit 1; };
        return v + 100;
    }
    fn main() -> int32 { return pick(true); }   // 99, not 101
    """
    assert run(source) == 99


def test_emit_outside_a_block_expression_is_an_error():
    with pytest.raises(LangError, match="emit outside a block expression"):
        compile_ir("fn main() -> int32 { emit 5; }")


def test_block_that_may_fall_off_its_end_is_an_error():
    with pytest.raises(LangError, match="may end without an emit"):
        compile_ir(
            "fn main() -> int32 { let x: int32 = { let t: int32 = 1; }; return x; }"
        )


def test_block_that_only_returns_never_emits():
    with pytest.raises(LangError, match="never emits a value"):
        compile_ir(
            "fn main() -> int32 { let x: int32 = { return 1; }; return x; }"
        )


def test_trivial_emit_null_adapts():
    # The trivial { emit null; } is just `null`, so it adapts to the target.
    assert run(
        "fn main() -> int32 { let p: uint8* = { emit null; }; if (p == null) return 0; return 1; }"
    ) == 0


def test_emit_null_in_a_general_block_needs_a_type():
    # With statements present, the slot needs a concrete type and `null` has none.
    with pytest.raises(LangError, match="cannot infer the type of `emit null`"):
        compile_ir(
            "fn main() -> int32 {\n"
            "    let p: uint8* = { let n: int32 = 0; emit null; };\n"
            "    return 0;\n"
            "}"
        )


def test_mismatched_emit_types_are_rejected():
    with pytest.raises(LangError, match="emit: expected int32, got char\\*"):
        compile_ir(
            'fn main() -> int32 {\n'
            '    let x: int32 = { if (true) { emit 1; } emit "s"; };\n'
            '    return x;\n'
            '}'
        )


def test_result_slot_lives_in_the_entry_block():
    # The slot is hoisted to entry so it dominates branch arms that emit.
    ir_text = compile_ir(
        "fn main() -> int32 {\n"
        "    let x: int32 = { if (true) { emit 1; } emit 2; };\n"
        "    return x;\n"
        "}"
    )
    entry = ir_text.split("entry:")[1].split("blockexpr.end")[0]
    assert "alloca i32" in entry  # the result slot is allocated up front
