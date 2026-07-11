"""Constant-condition loop folding: a `while (true)`-style loop whose
condition folds to always-run emits no exit edge, and -- when no `break` can
target it -- no end block at all, so the loop diverges (lifting the
missing-return and missing-emit checks) and code after it is dead."""

import pytest

from mcc.driver import compile_to_ir
from mcc.errors import LangError
from helpers import _execute, compile_ir, run


# --- the fold: IR shape ---

def test_noreturn_spin_emits_no_end_block_and_no_cbranch():
    ir = compile_ir("@noreturn fn spin() { while (true) {} }")
    assert "while.end" not in ir
    assert "br i1" not in ir
    # No fall-off unreachable is planted either: the loop itself diverges.
    assert "unreachable" not in ir


def test_until_false_is_the_dual():
    ir = compile_ir("@noreturn fn spin() { until (false) {} }")
    assert "until.end" not in ir
    assert "br i1" not in ir


def test_integer_condition_folds():
    # `while (1)` generates an icmp today, not an ir.Constant -- the fold
    # judges the AST via eval_const, so it catches it all the same.
    ir = compile_ir("@noreturn fn spin() { while (1) {} }")
    assert "while.end" not in ir
    assert "br i1" not in ir


def test_const_reference_condition_folds():
    ir = compile_ir("const SPIN = true; @noreturn fn spin() { while (SPIN) {} }")
    assert "while.end" not in ir


def test_constant_arithmetic_condition_folds():
    ir = compile_ir("@noreturn fn spin() { while (2 - 1) {} }")
    assert "while.end" not in ir


def test_runtime_condition_is_not_folded():
    ir = compile_ir("fn wait(go: bool) { while (go) {} }")
    assert "while.end" in ir
    assert "br i1" in ir


def test_while_false_keeps_its_blocks_and_type_checks_the_body():
    # The never-runs dual is deliberately out of scope: the body stays
    # emitted (LLVM deletes it) and fully type-checked, like `if (false)`.
    ir = compile_ir("fn f() { while (false) {} }")
    assert "while.end" in ir
    with pytest.raises(LangError, match="expected int32, got bool"):
        compile_ir("fn f() { while (false) { let n: int32 = true; } }")


def test_forever_loop_with_a_break_keeps_the_end_block_only():
    # The gate: a `break` targets the end block, so it must exist -- but the
    # constant condition still drops the conditional exit edge (the only
    # way in is the break).
    src = """
    fn main() -> int32 {
        while (true) { break; }
        return 0;
    }
    """
    ir = compile_ir(src)
    assert "while.end" in ir
    assert "br i1" not in ir
    assert run(src) == 0


# --- the missing-return lift ---

def test_bare_forever_loop_satisfies_missing_return():
    # Pre-fold this errored: the exit edge made the fall-off structurally
    # reachable, forcing a dummy trailing return.
    ir = compile_ir("fn f() -> int32 { while (true) {} } "
                    "fn main() -> int32 { return 0; }")
    assert 'define i32 @"f"()' in ir


def test_forever_loop_returning_from_inside_runs():
    src = """
    fn wait_for(limit: int32) -> int32 {
        let n: int32 = 0;
        while (true) {
            n += 1;
            if (n == limit) { return n; }
        }
    }
    fn main() -> int32 { return wait_for(3); }
    """
    assert run(src) == 3


def test_continue_re_enters_a_folded_loop():
    src = """
    fn f() -> int32 {
        let n: int32 = 0;
        while (true) {
            n += 1;
            if (n < 3) { continue; }
            return n;
        }
    }
    fn main() -> int32 { return f(); }
    """
    assert run(src) == 3


def test_forever_loop_with_a_break_still_needs_a_return():
    # The gate's negative: a break keeps the exit edge reachable, so the
    # function can fall off its end again.
    with pytest.raises(LangError, match="function 'f' may end without a return"):
        compile_ir("fn f() -> int32 { while (true) { break; } }")


def test_break_in_a_case_arm_blocks_the_lift():
    # `case` does not push a loop scope, so its break targets the loop; the
    # gate's scan descends case arms and keeps the exit.
    src = """
    fn f(n: int32) -> int32 {
        while (true) {
            case (n) {
                when 1:
                    break;
                else:
                    return n;
            }
        }
    }
    """
    with pytest.raises(LangError, match="function 'f' may end without a return"):
        compile_ir(src)


def test_break_in_a_nested_block_expression_blocks_the_lift():
    src = """
    fn f(n: int32) -> int32 {
        while (true) {
            let x: int32 = {
                if (n == 1) { break; }
                emit n;
            };
        }
    }
    """
    with pytest.raises(LangError, match="function 'f' may end without a return"):
        compile_ir(src)


# --- the block-expression unlock ---

def test_forever_loop_emitting_from_inside_compiles_and_runs():
    src = """
    fn main() -> int32 {
        let x: int32 = { while (true) { emit 5; } };
        return x;
    }
    """
    assert run(src) == 5


def test_forever_loop_that_never_emits_still_errors():
    src = """
    fn main() -> int32 {
        let x: int32 = { while (true) {} };
        return x;
    }
    """
    with pytest.raises(LangError, match="block expression never emits a value"):
        compile_ir(src)


# --- error-path parity: the fold never swallows a diagnostic ---

def test_non_scalar_condition_error_is_unchanged():
    with pytest.raises(LangError, match="condition must be a bool or integer"):
        compile_ir('fn f() { while ("hi") {} }')


def test_private_const_condition_errors_identically(tmp_path):
    # eval_const raises the privacy error inside the try-fold; the swallow
    # falls through to the runtime path, which reports the same error.
    (tmp_path / "config.mc").write_text("@private const SECRET = 1;\n")
    (tmp_path / "main.mc").write_text(
        'import "config";\nfn main() -> int32 { while (SECRET) {} return 0; }\n'
    )
    with pytest.raises(LangError, match="constant 'SECRET' is private"):
        compile_to_ir(tmp_path / "main.mc", ())


# --- semantics regressions around the loop's other exits ---

def test_return_from_a_folded_loop_unwinds_defers(capfd):
    # The folded loop keeps the defer bookkeeping intact: a return from
    # inside unwinds the loop body's defers, then the function's.
    src = r"""
    import "libc/stdio";
    fn f() {
        defer printf("outer");
        while (true) {
            defer printf("loop");
            return;
        }
    }
    fn main() -> int32 { f(); return 0; }
    """
    assert run(src) == 0
    assert capfd.readouterr().out == "loopouter"
