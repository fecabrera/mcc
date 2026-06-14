"""defer: run an action when the enclosing block exits.

Deferred actions run in LIFO order on every exit path -- falling off the end
of the block, return, break, and continue -- and a returned value is computed
before they run.
"""

from helpers import run


def out(capfd, source):
    run(source)
    return capfd.readouterr().out


def test_runs_at_block_end(capfd):
    source = r"""
    #include <stdio.h>
    fn main() -> int32 { defer printf("A"); printf("B"); return 0; }
    """
    assert out(capfd, source) == "BA"


def test_lifo_order(capfd):
    source = r"""
    #include <stdio.h>
    fn main() -> int32 {
        defer printf("1"); defer printf("2"); defer printf("3");
        return 0;
    }
    """
    assert out(capfd, source) == "321"


def test_block_form(capfd):
    source = r"""
    #include <stdio.h>
    fn main() -> int32 {
        defer { printf("a"); printf("b"); }
        printf("X");
        return 0;
    }
    """
    assert out(capfd, source) == "Xab"


def test_runs_on_return_from_nested_block(capfd):
    source = r"""
    #include <stdio.h>
    fn f() { defer printf("X"); if (true) { return; } printf("unreached"); }
    fn main() -> int32 { f(); return 0; }
    """
    assert out(capfd, source) == "X"


def test_inner_block_scope(capfd):
    # The defer fires at the end of its own block, before code after it.
    source = r"""
    #include <stdio.h>
    fn main() -> int32 {
        if (true) { defer printf("in"); printf("body"); }
        printf("after");
        return 0;
    }
    """
    assert out(capfd, source) == "bodyinafter"


def test_runs_on_break(capfd):
    source = r"""
    #include <stdio.h>
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
    #include <stdio.h>
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
    #include <stdio.h>
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
    #include <stdio.h>
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
