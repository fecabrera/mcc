"""`for x in obj` over the it/next protocol: a struct value is auto-borrowed
(iterated by snapshot), a `&obj` reference and a pointer pass straight through,
and an rvalue temporary is materialized so its iterator never dangles."""

import pytest

from mcc.errors import LangError
from helpers import compile_ir, run


def test_iterates_a_struct_value(capfd):
    # The headline: a stack struct iterates directly, no `&`.
    run(
        """
        import "range";
        import "libc/stdio";
        fn main() -> int32 {
            let r = struct range<int32> { end = 5 };
            let sum: int32 = 0;
            for i in r { sum += i; }
            printf("%d\\n", sum);        // 0+1+2+3+4
            return 0;
        }
        """
    )
    assert capfd.readouterr().out == "10\n"


def test_reference_form_still_works(capfd):
    run(
        """
        import "range";
        import "libc/stdio";
        fn main() -> int32 {
            let r = struct range<int32> { end = 5 };
            let sum: int32 = 0;
            for i in &r { sum += i; }
            printf("%d\\n", sum);
            return 0;
        }
        """
    )
    assert capfd.readouterr().out == "10\n"


def test_pointer_variable_iterable(capfd):
    # `for i in rp` where rp is already a pointer -- passed straight through.
    run(
        """
        import "range";
        import "libc/stdio";
        fn total(rp: struct range<int32>*) -> int32 {
            let s: int32 = 0;
            for i in rp { s += i; }
            return s;
        }
        fn main() -> int32 {
            let r = struct range<int32> { end = 5 };
            printf("%d\\n", total(&r));
            return 0;
        }
        """
    )
    assert capfd.readouterr().out == "10\n"


def test_iterates_an_rvalue_temporary(capfd):
    # A returned struct has no address to take -- it is materialized to a slot,
    # so its iterator's back-pointer stays valid. This was impossible with `&`.
    run(
        """
        import "range";
        import "libc/stdio";
        fn make() -> struct range<int32> {
            return struct range<int32> { start = 10, end = 13 };
        }
        fn main() -> int32 {
            let s: int32 = 0;
            for i in make() { s += i; }   // 10+11+12
            printf("%d\\n", s);
            return 0;
        }
        """
    )
    assert capfd.readouterr().out == "33\n"


def test_container_by_value(capfd):
    # A heap-backed container iterates by value too; the snapshot shares the
    # buffer, so it reads the same elements.
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


def _range_loop(iterable):
    return (
        'import "range";\n'
        "fn main() -> int32 {\n"
        "    let r = struct range<int32> { end = 5 };\n"
        f"    let s: int32 = 0;\n    for i in {iterable} {{ s += i; }}\n"
        "    return s;\n}\n"
    )


def test_value_form_copies_once_reference_does_not():
    # `for i in r` materializes a snapshot (a `for.src` slot); `for i in &r`
    # borrows the original and copies nothing.
    assert '"for.src"' in compile_ir(_range_loop("r"))
    assert '"for.src"' not in compile_ir(_range_loop("&r"))


def test_non_struct_iterable_still_rejected():
    with pytest.raises(LangError, match="needs a struct iterable"):
        compile_ir(
            "fn main() -> int32 {\n"
            "    let n: int32 = 3;\n"
            "    for i in n { }\n"
            "    return 0;\n"
            "}\n"
        )
