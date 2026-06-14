"""Block scoping: every { } is its own scope, with C/Rust-style shadowing.

A name declared in a block is visible only until the block ends; sibling and
sequential blocks may reuse names, an inner block may shadow an outer
variable (the outer binding returns on exit), and redeclaring a name in the
same block is an error.
"""

import pytest

from mcc.errors import LangError
from helpers import compile_ir, run


def test_if_and_else_reuse_a_name():
    source = """
    fn main() -> int32 {
        if (true) { let x: int32 = 1; } else { let x: int32 = 2; }
        return 0;
    }
    """
    assert run(source) == 0


def test_sequential_blocks_reuse_a_name():
    source = """
    fn main() -> int32 {
        if (true) { let x: int32 = 1; }
        if (true) { let x: int32 = 2; }
        return 0;
    }
    """
    assert run(source) == 0


def test_inner_block_shadows_outer():
    source = """
    fn main() -> int32 {
        let x: int32 = 1;
        let seen: int32 = 0;
        if (true) {
            let x: int32 = 99;   // shadows the outer x
            seen = x;            // 99
        }
        return seen * 100 + x;   // 99*100 + 1 (outer x restored) = 9901
    }
    """
    assert run(source) == 9901


def test_while_body_is_a_scope():
    source = """
    fn main() -> int32 {
        let i: int32 = 0;
        let sum: int32 = 0;
        while (i < 3) {
            let doubled: int32 = i * 2;   // block-local, fresh each iteration
            sum = sum + doubled;
            i = i + 1;
        }
        return sum;                        // 0 + 2 + 4 = 6
    }
    """
    assert run(source) == 6


def test_shadow_in_a_while_body():
    source = """
    fn main() -> int32 {
        let i: int32 = 0;
        let last: int32 = 0;
        while (i < 3) {
            i = i + 1;          // advance the outer counter first
            let i: uint8 = 7;   // then shadow it within the body
            last = i as int32;
        }
        return last;            // the inner i, 7
    }
    """
    assert run(source) == 7


def test_block_local_does_not_leak():
    with pytest.raises(LangError, match="undefined variable 'y'"):
        compile_ir("fn main() -> int32 { if (true) { let y: int32 = 5; } return y; }")


def test_redeclaration_in_the_same_block_is_an_error():
    with pytest.raises(LangError, match="variable 'x' already declared in this scope"):
        compile_ir("fn main() -> int32 { let x: int32 = 1; let x: int32 = 2; return x; }")


def test_bare_block_is_its_own_scope():
    source = """
    fn main() -> int32 {
        let x: int32 = 1;
        { let x: int32 = 2; }     // a bare block; inner x is independent
        { let x: int32 = 3; }     // sibling block reuses the name
        return x;                 // outer x, still 1
    }
    """
    assert run(source) == 1


def test_bare_block_local_does_not_leak():
    with pytest.raises(LangError, match="undefined variable 'y'"):
        compile_ir("fn main() -> int32 { { let y: int32 = 5; } return y; }")


def test_case_arms_are_separate_scopes():
    # Each when/else arm is its own block, so they may reuse a name.
    source = """
    fn main() -> int32 {
        let total: int32 = 0;
        case (1) {
            when 1: let v: int32 = 10; total = v;
            when 2: let v: int32 = 20; total = v;
            else:   let v: int32 = 30; total = v;
        }
        return total;
    }
    """
    assert run(source) == 10
