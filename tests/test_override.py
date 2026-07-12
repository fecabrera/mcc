"""`@override`: replace a same-pattern member of another module's overload set.

An `@override` definition drops the overridden (unannotated) member before
registration and emits its own body under the member's shared mangled symbol,
so the replacement is order-independent and global. It needs exactly one
source-visible, body-bearing, cross-module target of the same pattern; a
missing target, a same-file target, a prototype-only target, or a second
`@override` of one pattern is a compile error.
"""

import re

import pytest

from mcc.errors import LangError
from helpers import compile_ir, run, run_path


# --- the driving use case: replacing a stdlib formatter -----------------------

def test_overrides_a_stdlib_concrete_formatter(capfd):
    # The stdlib's concrete bool formatter is a same-pattern member; the
    # override replaces it globally, so println's dispatch (which resolves in
    # the stdlib module) picks up the new body through the shared symbol.
    assert run(
        """
        import "std/io";
        @override
        fn format(mut str: string, value: bool, const modifier: slice<char>) {
            string_append(str, value ? "YEP" : "NOPE");
        }
        fn main() -> int32 {
            println("flag is {}", true);
            return 0;
        }
        """
    ) == 0
    assert capfd.readouterr().out == "flag is YEP\n"


def test_overrides_the_typename_fallback(capfd):
    # The unbounded `<typename>` template fallback is the other override
    # target: a struct with no concrete formatter falls to it, while an int
    # keeps its own concrete overload (which outranks the fallback).
    assert run(
        """
        import "std/io";
        struct point { x: int32; y: int32; }
        @override
        fn format<T>(mut str: string, value: T, const modifier: slice<char>) {
            string_append(str, "?custom?");
        }
        fn main() -> int32 {
            let p = struct point { x = 1, y = 2 };
            println("{} then {}", 42, p);
            return 0;
        }
        """
    ) == 0
    assert capfd.readouterr().out == "42 then ?custom?\n"


# --- cross-module replacement -------------------------------------------------

def test_overrides_a_plain_single_from_another_module(tmp_path):
    # A single plain member in another module is replaced wholesale: after the
    # drop, only the override remains, taking over the plain `kind` symbol.
    (tmp_path / "base.mc").write_text("fn kind() -> int32 { return 1; }\n")
    main = tmp_path / "main.mc"
    main.write_text(
        'import "base";\n'
        "@override fn kind() -> int32 { return 42; }\n"
        "fn main() -> int32 { return kind(); }\n"
    )
    assert run_path(main) == 42


def test_overrides_one_member_of_an_imported_set(tmp_path):
    # An open set's `pick(int32)` member is replaced; its sibling `pick(char*)`
    # is untouched, and both dispatch by pattern as before.
    (tmp_path / "base.mc").write_text(
        "fn pick(x: int32) -> int32 { return 1; }\n"
        "fn pick(x: char*) -> int32 { return 2; }\n"
    )
    main = tmp_path / "main.mc"
    main.write_text(
        'import "base";\n'
        "@override fn pick(x: int32) -> int32 { return 100; }\n"
        "fn main() -> int32 {\n"
        '    return pick(0) + pick("hi");\n'
        "}\n"
    )
    assert run_path(main) == 102


def test_replacement_is_import_order_independent(tmp_path):
    # The override wins whether it registers before or after its target.
    (tmp_path / "base.mc").write_text("fn kind() -> int32 { return 1; }\n")
    (tmp_path / "over.mc").write_text(
        "@override fn kind() -> int32 { return 42; }\n"
    )
    for imports in (
        'import "base";\nimport "over";\n',
        'import "over";\nimport "base";\n',
    ):
        main = tmp_path / "main.mc"
        main.write_text(imports + "fn main() -> int32 { return kind(); }\n")
        assert run_path(main) == 42


# --- no-match / same-file / prototype-only targets ----------------------------

def test_override_with_no_target_is_an_error():
    with pytest.raises(
        LangError,
        match=re.escape(
            "@override function 'nope' matches no existing overload to replace"
        ),
    ):
        compile_ir(
            "@override fn nope(x: int32) -> int32 { return x; }\n"
            "fn main() -> int32 { return 0; }\n"
        )


def test_override_of_a_nonexistent_pattern_is_an_error(tmp_path):
    # The name exists as a set, but no member has this parameter pattern: the
    # typo guard fires (there is nothing to replace).
    (tmp_path / "base.mc").write_text(
        "fn pick(x: int32) -> int32 { return 1; }\n"
        "fn pick(x: char*) -> int32 { return 2; }\n"
    )
    main = tmp_path / "main.mc"
    main.write_text(
        'import "base";\n'
        "@override fn pick(x: int64) -> int32 { return 3; }\n"
        "fn main() -> int32 { return 0; }\n"
    )
    with pytest.raises(
        LangError,
        match=re.escape(
            "@override function 'pick' matches no existing overload to replace"
        ),
    ):
        run_path(main)


def test_override_of_a_same_file_member_is_rejected():
    with pytest.raises(
        LangError,
        match=re.escape(
            "@override function 'g' matches a same-pattern definition in its "
            "own file; @override replaces a member declared in another module, "
            "not a local one"
        ),
    ):
        compile_ir(
            "fn g(x: int32) -> int32 { return x; }\n"
            "fn g(x: char*) -> int32 { return 1; }\n"
            "@override fn g(x: int32) -> int32 { return x + 1; }\n"
            "fn main() -> int32 { return 0; }\n"
        )


def test_cannot_override_a_prototype_only_member(tmp_path):
    # A same-pattern member visible only as a prototype (its body lives in
    # another object) cannot be overridden: replacement is by emitting the
    # winner's body under the shared symbol, which that object already defines.
    (tmp_path / "base.mc").write_text("fn kind() -> int32;\n")
    main = tmp_path / "main.mc"
    main.write_text(
        'import "base";\n'
        "@override fn kind() -> int32 { return 42; }\n"
        "fn main() -> int32 { return 0; }\n"
    )
    with pytest.raises(
        LangError,
        match=re.escape(
            "cannot @override 'kind': its definition is not source-visible"
        ),
    ):
        run_path(main)


def test_two_overrides_of_one_pattern_collide(tmp_path):
    (tmp_path / "base.mc").write_text("fn kind() -> int32 { return 1; }\n")
    main = tmp_path / "main.mc"
    main.write_text(
        'import "base";\n'
        "@override fn kind() -> int32 { return 2; }\n"
        "@override fn kind() -> int32 { return 3; }\n"
        "fn main() -> int32 { return 0; }\n"
    )
    with pytest.raises(
        LangError,
        match=re.escape(
            "function 'kind' has two @override definitions of one overload "
            "pattern; at most one may replace it"
        ),
    ):
        run_path(main)


# --- annotation-combination rules (parser) ------------------------------------

def test_override_only_applies_to_functions():
    with pytest.raises(
        LangError, match=re.escape("@override only applies to functions")
    ):
        compile_ir("@override struct s { x: int32; }\n")


def test_override_on_a_prototype_is_rejected():
    with pytest.raises(
        LangError,
        match=re.escape(
            "an @override function cannot be a bodyless prototype"
        ),
    ):
        compile_ir("@override fn f(x: int32) -> int32;\n")


def test_override_and_extern_cannot_combine():
    with pytest.raises(
        LangError,
        match=re.escape("@override and @extern cannot be combined"),
    ):
        compile_ir("@override @extern fn f(x: int32) -> int32;\n")


def test_override_and_static_cannot_combine():
    with pytest.raises(
        LangError,
        match=re.escape("@override and @static cannot be combined"),
    ):
        compile_ir("@override @static fn f() -> int32 { return 0; }\n")


def test_override_and_removed_cannot_combine():
    with pytest.raises(
        LangError,
        match=re.escape("@override and @removed cannot be combined"),
    ):
        compile_ir(
            '@override @removed("gone") fn f() -> int32 { return 0; }\n'
        )


def test_override_and_private_not_yet_combinable():
    with pytest.raises(
        LangError,
        match=re.escape("@override and @private cannot yet be combined"),
    ):
        compile_ir(
            '@override @private fn f() -> int32 { return 0; }\n'
        )
