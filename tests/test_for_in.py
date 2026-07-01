"""`for x in obj` over the it/next protocol: a struct value is auto-borrowed
(iterated by snapshot), a `&obj` reference and a pointer pass straight through,
and an rvalue temporary is materialized so its iterator never dangles."""

import pytest

from mcc.errors import LangError
from helpers import compile_ir, run

# A minimal iterable -- a counter yielding 0..limit-1 -- so these tests do not
# depend on any library container. `count_it`/`count_next` are the protocol.
PREAMBLE = """
struct count { limit: int32; }
struct count_iter { obj: struct count*; i: int32; }
fn count_it(self: struct count*) -> struct count_iter {
    let it: struct count_iter;
    it.obj = self;
    it.i = 0;
    return it;
}
fn count_next(it: struct count_iter*, out: int32*) -> bool {
    if (it->i < it->obj->limit) {
        *out = it->i;
        it->i += 1;
        return true;
    }
    return false;
}
fn make(limit: int32) -> struct count { return struct count { limit = limit }; }
"""


def _prog(body):
    return 'import "libc/stdio";\n' + PREAMBLE + (
        "fn main() -> int32 {\n" + body + "\n    return 0;\n}\n"
    )


def test_iterates_a_struct_value(capfd):
    # The headline: a stack struct iterates directly, no `&`.
    run(_prog(
        "    let c = struct count { limit = 5 };\n"
        "    let sum: int32 = 0;\n"
        "    for i in c { sum += i; }\n"          # 0+1+2+3+4
        '    printf("%d\\n", sum);'
    ))
    assert capfd.readouterr().out == "10\n"


def test_reference_form_still_works(capfd):
    run(_prog(
        "    let c = struct count { limit = 5 };\n"
        "    let sum: int32 = 0;\n"
        "    for i in &c { sum += i; }\n"
        '    printf("%d\\n", sum);'
    ))
    assert capfd.readouterr().out == "10\n"


def test_pointer_variable_iterable(capfd):
    run(_prog(
        "    let c = struct count { limit = 5 };\n"
        "    let cp = &c;\n"
        "    let sum: int32 = 0;\n"
        "    for i in cp { sum += i; }\n"         # pointer passed straight through
        '    printf("%d\\n", sum);'
    ))
    assert capfd.readouterr().out == "10\n"


def test_iterates_an_rvalue_temporary(capfd):
    # A returned struct has no address to take -- it is materialized to a slot,
    # so its iterator's back-pointer stays valid. This was impossible with `&`.
    run(_prog(
        "    let sum: int32 = 0;\n"
        "    for i in make(5) { sum += i; }\n"
        '    printf("%d\\n", sum);'
    ))
    assert capfd.readouterr().out == "10\n"


def test_container_by_value(capfd):
    # A heap-backed library container iterates by value too; the snapshot shares
    # the buffer, so it reads the same elements.
    run(
        """
        import "list";
        import "libc/stdio";
        fn main() -> int32 {
            let xs: struct list<int32>;
            list_init(&xs, 4);
            defer list_destroy(&xs);
            list_push(&xs, 1);
            list_push(&xs, 2);
            list_push(&xs, 3);
            let sum: int32 = 0;
            for x in xs { sum += x; }
            printf("%d\\n", sum);
            return 0;
        }
        """
    )
    assert capfd.readouterr().out == "6\n"


def test_value_form_copies_once_reference_does_not():
    # `for i in c` materializes a snapshot (a `for.src` slot); `for i in &c`
    # borrows the original and copies nothing.
    assert '"for.src"' in compile_ir(_prog(
        "    let c = struct count { limit = 3 };\n    for i in c { }"))
    assert '"for.src"' not in compile_ir(_prog(
        "    let c = struct count { limit = 3 };\n    for i in &c { }"))


def test_non_struct_iterable_still_rejected():
    with pytest.raises(LangError, match="needs a struct iterable"):
        compile_ir(
            "fn main() -> int32 {\n"
            "    let n: int32 = 3;\n"
            "    for i in n { }\n"
            "    return 0;\n"
            "}\n"
        )
