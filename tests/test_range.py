"""The builtin `range` in a `for` loop: `for i in range(end)` and
`for i in range(start, end)` lower to a direct counting loop over the half-open
interval [start, end), with no struct built and no protocol calls. No import."""

import pytest

from mcc.errors import LangError
from helpers import compile_ir, run

MAIN = 'import "libc/stdio";\nfn main() -> int32 {{\n{body}\n    return 0;\n}}\n'


def _run(body):
    run(MAIN.format(body=body))


def test_range_end(capfd):
    _run('    let s: int32 = 0;\n    for i in range(5) { s += i; }\n    printf("%d\\n", s);')
    assert capfd.readouterr().out == "10\n"          # 0+1+2+3+4


def test_range_start_end(capfd):
    _run('    let s: int32 = 0;\n    for i in range(2, 6) { s += i; }\n    printf("%d\\n", s);')
    assert capfd.readouterr().out == "14\n"          # 2+3+4+5


def test_range_needs_no_import(capfd):
    # No `import` at all -- range is a builtin.
    run(
        "fn main() -> int32 {\n"
        "    let s: int32 = 0;\n"
        "    for i in range(4) { s += i; }\n"
        "    return s;\n"
        "}\n"
    )  # returns 6; just assert it compiles and runs
    # (exit status is checked by run(); no output expected)


def test_empty_range_runs_zero_times(capfd):
    _run(
        '    let n: int32 = 0;\n'
        '    for i in range(5, 5) { n += 1; }\n'      # start == end -> empty
        '    for i in range(9, 2) { n += 1; }\n'      # start > end  -> empty
        '    printf("%d\\n", n);'
    )
    assert capfd.readouterr().out == "0\n"


def test_variable_bounds(capfd):
    _run(
        '    let a: int32 = 3;\n    let b: int32 = 7;\n    let s: int32 = 0;\n'
        '    for i in range(a, b) { s += i; }\n    printf("%d\\n", s);'   # 3+4+5+6
    )
    assert capfd.readouterr().out == "18\n"


def test_explicit_type_argument(capfd):
    _run(
        '    let s: int64 = 0;\n'
        '    for i in range<int64>(3000000000, 3000000003) { s += i; }\n'
        '    printf("%lld\\n", s);'
    )
    assert capfd.readouterr().out == str(3000000000 + 3000000001 + 3000000002) + "\n"


def test_unsigned_bounds_use_unsigned_compare():
    ir_text = compile_ir(
        "fn main() -> int32 {\n"
        "    let n: uint64 = 4;\n"
        "    let s: uint64 = 0;\n"
        "    for i in range(n) { s += i; }\n"
        "    return s as int32;\n"
        "}\n"
    )
    assert "icmp ult" in ir_text and "icmp slt" not in ir_text


def test_break_and_continue(capfd):
    _run(
        '    let s: int32 = 0;\n'
        '    for i in range(100) {\n'
        '        if (i == 3) { continue; }\n'
        '        if (i >= 6) { break; }\n'
        '        s += i;\n'                            # 0+1+2+4+5
        '    }\n    printf("%d\\n", s);'
    )
    assert capfd.readouterr().out == "12\n"


def test_loop_variable_is_a_fresh_copy(capfd):
    # Reassigning the loop variable inside the body must not perturb the counter.
    _run(
        '    let n: int32 = 0;\n'
        '    for i in range(5) { i = 99; n += 1; }\n'  # still exactly 5 iterations
        '    printf("%d\\n", n);'
    )
    assert capfd.readouterr().out == "5\n"


def test_lowers_without_a_struct_or_protocol_call():
    ir_text = compile_ir(
        "fn main() -> int32 {\n"
        "    let s: int32 = 0;\n"
        "    for i in range(5) { s += i; }\n"
        "    return s;\n"
        "}\n"
    )
    assert "range.cond" in ir_text          # the direct counting loop
    assert "range_it" not in ir_text        # no protocol calls
    assert "range_next" not in ir_text


def test_user_defined_range_function_takes_precedence(capfd):
    # A user `fn range` wins over the builtin; here it makes a one-element view.
    run(
        """
        import "libc/stdio";
        struct one { hit: bool; }
        struct one_iter { obj: struct one*; done: bool; }
        fn range(x: int32) -> struct one { return struct one { hit = false }; }
        fn one_it(self: struct one*) -> struct one_iter {
            let it: struct one_iter; it.obj = self; it.done = false; return it;
        }
        fn one_next(it: struct one_iter*, out: int32*) -> bool {
            if (it->done) { return false; }
            *out = 42; it->done = true; return true;
        }
        fn main() -> int32 {
            let seen: int32 = 0;
            for v in range(7) { seen += v; }   // uses the user range -> one 42
            printf("%d\\n", seen);
            return 0;
        }
        """
    )
    assert capfd.readouterr().out == "42\n"


def test_wrong_argument_count_rejected():
    with pytest.raises(LangError, match="range\\(\\) takes 1 or 2 arguments"):
        compile_ir(
            "fn main() -> int32 {\n    for i in range(1, 2, 3) { }\n    return 0;\n}\n"
        )
