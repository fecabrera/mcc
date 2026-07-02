"""The builtin `enumerate` in a `for` loop: `for e in enumerate(obj)` runs
`obj`'s ordinary iteration (the `_it`/`_next` protocol or a slice's native
walk) with a position counter, yielding an `enumerated<T>` (`{ index: uint64;
value: T }`) per element. No import; a user `enumerate` takes precedence."""

import pytest

from mcc.errors import LangError
from helpers import compile_ir, run

# A tiny self-contained iterable: yields limit, limit-1, ..., 1.
PREAMBLE = """
import "libc/stdio";
struct count { limit: int32; }
fn count_it(c: struct count*) -> int32 { return c->limit; }
fn count_next(it: int32*, x: int32*) -> bool {
    if (*it <= 0) { return false; }
    *x = *it;
    *it -= 1;
    return true;
}
"""

def _run(body):
    run(PREAMBLE + "fn main() -> int32 {\n" + body + "\n    return 0;\n}\n")


def test_enumerate_protocol_struct(capfd):
    _run(
        "    let c = count { limit = 3 };\n"
        "    for e in enumerate(&c) {\n"
        '        printf("%llu:%d ", e.index, e.value);\n'
        "    }\n"
        '    printf("\\n");'
    )
    assert capfd.readouterr().out == "0:3 1:2 2:1 \n"


def test_enumerate_borrows_a_struct_value(capfd):
    # A value iterable is auto-borrowed, exactly like a bare `for x in c`.
    _run(
        "    let c = count { limit = 2 };\n"
        "    for e in enumerate(c) {\n"
        '        printf("%llu:%d ", e.index, e.value);\n'
        "    }\n"
        '    printf("\\n");'
    )
    assert capfd.readouterr().out == "0:2 1:1 \n"


def test_enumerate_rvalue_iterable(capfd):
    _run(
        "    for e in enumerate(count { limit = 2 }) {\n"
        '        printf("%llu:%d ", e.index, e.value);\n'
        "    }\n"
        '    printf("\\n");'
    )
    assert capfd.readouterr().out == "0:2 1:1 \n"


def test_enumerate_slice(capfd):
    _run(
        "    let xs: int32[3];\n"
        "    xs[0] = 5; xs[1] = 6; xs[2] = 7;\n"
        "    for e in enumerate(xs as slice<int32>) {\n"
        '        printf("%llu:%d ", e.index, e.value);\n'
        "    }\n"
        '    printf("\\n");'
    )
    assert capfd.readouterr().out == "0:5 1:6 2:7 \n"


def test_continue_does_not_skip_an_index(capfd):
    # The position is claimed when the element is yielded, so a `continue`d
    # element still consumes its index; `break` stops cleanly.
    _run(
        "    let c = count { limit = 4 };\n"
        "    for e in enumerate(&c) {\n"
        "        if (e.value == 3) { continue; }\n"
        "        if (e.index == 3) { break; }\n"
        '        printf("%llu:%d ", e.index, e.value);\n'
        "    }\n"
        '    printf("\\n");'
    )
    # yields 4,3,2,1 -> prints 0:4, skips 1:3, prints 2:2, breaks at 3:1
    assert capfd.readouterr().out == "0:4 2:2 \n"


def test_loop_variable_is_a_fresh_copy(capfd):
    # Writing to e inside the body does not perturb the iteration.
    _run(
        "    let c = count { limit = 3 };\n"
        "    for e in enumerate(&c) {\n"
        "        e.index = 99; e.value = -1;\n"
        '        printf("%llu:%d ", e.index, e.value);\n'
        "    }\n"
        '    printf("\\n");'
    )
    assert capfd.readouterr().out == "99:-1 99:-1 99:-1 \n"


def test_enumerated_is_an_ordinary_struct(capfd):
    # The element can leave the loop -- copied out, passed, returned.
    run(
        PREAMBLE
        + "fn describe(e: struct enumerated<int32>) -> int32 {"
        "     return (e.index as int32) * 10 + e.value; }\n"
        "fn main() -> int32 {\n"
        "    let last: struct enumerated<int32>;\n"
        "    let c = count { limit = 3 };\n"
        "    for e in enumerate(&c) { last = e; }\n"
        '    printf("%d\\n", describe(last));\n'
        "    return 0;\n"
        "}\n"
    )
    assert capfd.readouterr().out == "21\n"  # index 2, value 1


def test_enumerate_needs_no_import():
    run(
        "struct one { dummy: int32; }\n"
        "fn one_it(o: struct one*) -> int32 { return 1; }\n"
        "fn one_next(it: int32*, x: int32*) -> bool {\n"
        "    if (*it <= 0) { return false; }\n"
        "    *x = 7; *it -= 1; return true;\n"
        "}\n"
        "fn main() -> int32 {\n"
        "    let s: int32 = 0;\n"
        "    let o = one { };\n"
        "    for e in enumerate(o) { s += (e.index as int32) + e.value; }\n"
        "    return s;\n"  # 0 + 7
        "}\n"
    )


def test_user_enumerate_function_takes_precedence(capfd):
    run(
        PREAMBLE
        + "fn enumerate(c: struct count) -> struct count {\n"
        "    return count { limit = c.limit + 1 };\n"
        "}\n"
        "fn main() -> int32 {\n"
        "    for x in enumerate(count { limit = 2 }) {"
        ' printf("%d ", x); }\n'
        '    printf("\\n");\n'
        "    return 0;\n"
        "}\n"
    )
    assert capfd.readouterr().out == "3 2 1 \n"


# --------------------------------------------------------------------- errors


def test_enumerate_of_builtin_range_is_rejected():
    with pytest.raises(LangError, match="iterate the range directly"):
        compile_ir("fn f() { for e in enumerate(range(3)) { } }")


def test_wrong_argument_count_is_rejected():
    with pytest.raises(LangError, match="exactly one iterable"):
        compile_ir(PREAMBLE + "fn f() { for e in enumerate() { } }")
    with pytest.raises(LangError, match="exactly one iterable"):
        compile_ir(
            PREAMBLE + "fn f(a: struct count, b: struct count) "
            "{ for e in enumerate(a, b) { } }"
        )


def test_type_arguments_are_rejected():
    with pytest.raises(LangError, match="no type arguments"):
        compile_ir(
            PREAMBLE + "fn f(c: struct count) { for e in enumerate<int32>(c) { } }"
        )


def test_non_iterable_argument_is_rejected():
    with pytest.raises(LangError, match="needs a struct iterable"):
        compile_ir("fn f() { for e in enumerate(3) { } }")


def test_shadowed_enumerated_struct_is_a_clear_error():
    source = PREAMBLE + (
        "struct enumerated { tag: int32; }\n"
        "fn f(c: struct count) { for e in enumerate(c) { } }"
    )
    with pytest.raises(LangError, match="shadows it"):
        compile_ir(source)


def test_enumerate_outside_a_for_header_is_undefined():
    with pytest.raises(LangError, match="undefined function 'enumerate'"):
        compile_ir("fn f() { let e = enumerate(1); }")
