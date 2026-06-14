"""Generic overload sets: same name, dispatch by parameter pattern."""

import pytest

from mcc.errors import LangError
from helpers import compile_ir, run, run_path

VARIANTS = """
fn describe<T>(x: T) -> int32 { return 1; }
fn describe<T>(x: T*) -> int32 { return 2; }
"""


def test_dispatch_by_pointerness():
    assert run(
        VARIANTS
        + """
        fn main() -> int32 {
            let v: int64 = 5;
            let by_value = describe(v);        // T
            let by_pointer = describe(&v);     // T*
            return by_value * 10 + by_pointer;
        }
        """
    ) == 12


def test_more_specific_pattern_wins_for_pointers():
    # A pointer argument matches both T and T*; T* is more specific.
    assert run(
        VARIANTS
        + """
        fn main() -> int32 {
            let s = "hi";
            return describe(s);
        }
        """
    ) == 2


def test_struct_pattern_beats_bare_parameter():
    assert run(
        """
        struct box<T> { value: T; }
        fn pick<T>(x: T) -> int32 { return 1; }
        fn pick<T>(x: box<T>*) -> int32 { return 2; }
        fn main() -> int32 {
            let b: struct box<int32>* = null;
            return pick(b);
        }
        """
    ) == 2


def test_no_matching_overload():
    with pytest.raises(LangError, match="no overload of 'describe' matches"):
        compile_ir(
            "fn describe<T>(x: T*) -> int32 { return 1; }\n"
            "fn describe<T>(x: T**) -> int32 { return 2; }\n"
            "fn main() -> int32 { return describe(5); }"
        )


def test_ambiguous_overloads():
    with pytest.raises(LangError, match="ambiguous"):
        compile_ir(
            "fn f<T>(x: T) -> int32 { return 1; }\n"
            "fn f<T>(x: T) -> int32 { return 2; }\n"
            "fn main() -> int32 { return f(5 as int32); }"
        )


def test_overloads_merge_across_files(tmp_path):
    # A second file can extend an imported overload set.
    (tmp_path / "base.mc").write_text("fn measure<T>(x: T) -> int32 { return 1; }")
    main = tmp_path / "main.mc"
    main.write_text(
        'import "base";\n'
        "fn measure<T>(x: T*) -> int32 { return 2; }\n"
        "fn main() -> int32 {\n"
        "    let v: int32 = 0;\n"
        "    return measure(v) * 10 + measure(&v);\n"
        "}"
    )
    assert run_path(main) == 12


def test_hash_lib_dispatches(tmp_path, capfd):
    main = tmp_path / "main.mc"
    main.write_text(
        """
        import "hash";
        import "libc/stdio";
        fn main() -> int32 {
            let by_value = hash(99 as uint64) == splitmix64(99);
            let by_content = hash("abc") == fnv1a("abc");
            printf("%d %d\\n", by_value, by_content);
            return 0;
        }
        """
    )
    assert run_path(main) == 0
    assert capfd.readouterr().out == "1 1\n"
