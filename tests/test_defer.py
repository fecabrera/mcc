"""defer: run an action when the enclosing block exits.

Deferred actions run in LIFO order on every exit path -- falling off the end
of the block, return, break, and continue -- and a returned value is computed
before they run.
"""

import pytest

from mcc.errors import LangError
from helpers import compile_ir, run


def out(capfd, source):
    run(source)
    return capfd.readouterr().out


def test_runs_at_block_end(capfd):
    source = r"""
    import "libc/stdio";
    fn main() -> int32 { defer printf("A"); printf("B"); return 0; }
    """
    assert out(capfd, source) == "BA"


def test_lifo_order(capfd):
    source = r"""
    import "libc/stdio";
    fn main() -> int32 {
        defer printf("1"); defer printf("2"); defer printf("3");
        return 0;
    }
    """
    assert out(capfd, source) == "321"


def test_block_form(capfd):
    source = r"""
    import "libc/stdio";
    fn main() -> int32 {
        defer { printf("a"); printf("b"); }
        printf("X");
        return 0;
    }
    """
    assert out(capfd, source) == "Xab"


def test_runs_on_return_from_nested_block(capfd):
    source = r"""
    import "libc/stdio";
    fn f() { defer printf("X"); if (true) { return; } printf("unreached"); }
    fn main() -> int32 { f(); return 0; }
    """
    assert out(capfd, source) == "X"


def test_inner_block_scope(capfd):
    # The defer fires at the end of its own block, before code after it.
    source = r"""
    import "libc/stdio";
    fn main() -> int32 {
        if (true) { defer printf("in"); printf("body"); }
        printf("after");
        return 0;
    }
    """
    assert out(capfd, source) == "bodyinafter"


def test_runs_on_break(capfd):
    source = r"""
    import "libc/stdio";
    fn main() -> int32 {
        let i: int32 = 0;
        while (i < 3) {
            defer printf("d");
            printf("%d", i);
            i = i + 1;
            if (i == 2) { break; }
        }
        printf("|");
        return 0;
    }
    """
    # iter0: "0" then defer "d"; iter1: "1", break -> defer "d", then "|"
    assert out(capfd, source) == "0d1d|"


def test_runs_on_continue(capfd):
    source = r"""
    import "libc/stdio";
    fn main() -> int32 {
        let i: int32 = 0;
        while (i < 3) {
            defer printf("d");
            i = i + 1;
            if (i == 2) { continue; }
            printf("%d", i);
        }
        return 0;
    }
    """
    assert out(capfd, source) == "1dd3d"


def test_return_value_is_taken_before_defers_run():
    # The result is snapshotted before the defer mutates x.
    source = """
    fn f() -> int32 {
        let x: int32 = 5;
        defer x = 99;
        return x;
    }
    fn main() -> int32 { return f(); }
    """
    assert run(source) == 5


def test_nested_returns_unwind_all_scopes(capfd):
    source = r"""
    import "libc/stdio";
    fn f() {
        defer printf("outer");
        while (true) {
            defer printf("loop");
            if (true) { defer printf("inner"); return; }
        }
    }
    fn main() -> int32 { f(); return 0; }
    """
    # innermost first: inner, loop, outer
    assert out(capfd, source) == "innerloopouter"


def test_realistic_alloc_free(capfd):
    source = r"""
    import "libc/stdio";
    @extern fn malloc(n: uint64) -> uint8*;
    @extern fn free(p: uint8*);
    fn main() -> int32 {
        let buf: uint8* = malloc(8);
        defer free(buf);        // released at scope exit, no matter how we leave
        buf[0] = 65;            // 'A'
        buf[1] = 0;
        printf("%s", buf);
        return 0;
    }
    """
    assert out(capfd, source) == "A"


# --- control flow cannot jump out of a defer body ---
#
# A defer body runs while its scope is already unwinding: a break/continue/
# emit targeting a construct outside the body (or any return) would re-unwind
# the very scope whose defers are running -- pre-fix, the compiler recursed
# to death on these. Each is a compile-time error at the offending statement;
# a loop or block expression opened *inside* the body remains fair game.

def test_break_inside_a_defer_body_is_rejected():
    source = """
    fn main() -> int32 {
        while (true) { defer break; }
        return 0;
    }
    """
    with pytest.raises(
        LangError, match="'break' inside a defer body cannot exit the enclosing loop"
    ):
        compile_ir(source)


def test_continue_inside_a_defer_body_is_rejected():
    source = """
    fn main() -> int32 {
        while (true) { defer continue; }
        return 0;
    }
    """
    with pytest.raises(
        LangError,
        match="'continue' inside a defer body cannot continue the enclosing loop",
    ):
        compile_ir(source)


def test_return_inside_a_defer_body_is_rejected():
    source = """
    fn main() -> int32 {
        {
            defer return 1;
        }
        return 0;
    }
    """
    with pytest.raises(
        LangError,
        match="'return' inside a defer body cannot exit the enclosing function",
    ):
        compile_ir(source)


def test_emit_inside_a_defer_body_is_rejected():
    source = """
    fn main() -> int32 {
        let x: int32 = {
            defer emit 5;
            emit 3;
        };
        return x;
    }
    """
    with pytest.raises(
        LangError,
        match="'emit' inside a defer body cannot exit the enclosing "
        "block expression",
    ):
        compile_ir(source)


def test_loop_opened_inside_a_defer_body_may_break(capfd):
    # The judgment resets at constructs opened inside the body: this break
    # targets the defer's own loop, never the enclosing one.
    source = r"""
    import "libc/stdio";
    fn main() -> int32 {
        let n: int32 = 0;
        while (n < 2) {
            defer {
                while (true) { break; }
                printf("D");
            }
            n += 1;
        }
        return 0;
    }
    """
    assert out(capfd, source) == "DD"


def test_block_expr_opened_inside_a_defer_body_may_emit(capfd):
    source = r"""
    import "libc/stdio";
    fn main() -> int32 {
        let x: int32 = {
            defer {
                let y: int32 = { printf("D"); emit 1; };
                printf("%d", y);
            }
            emit 3;
        };
        return x - 3;
    }
    """
    assert out(capfd, source) == "D1"


def test_nested_defer_cannot_break_the_outer_defers_loop():
    # One level down, same rule: the inner defer's break targets a loop
    # opened outside the *inner* body.
    source = """
    fn main() -> int32 {
        defer {
            while (true) { defer break; }
        }
        return 0;
    }
    """
    with pytest.raises(
        LangError, match="'break' inside a defer body cannot exit the enclosing loop"
    ):
        compile_ir(source)
