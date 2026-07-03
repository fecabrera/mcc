"""Error directives: @static_assert and @error, folded during codegen."""

import pytest

from mcc.driver import compile_to_ir
from mcc.errors import LangError
from helpers import compile_ir, run


def compile_file_ir(tmp_path, name, source, search_paths=None):
    """Write `source` to a file and compile it, so `import`s resolve."""
    path = tmp_path / name
    path.write_text(source)
    return str(compile_to_ir(path, search_paths or ()))


# --- @static_assert: passing conditions compile silently ---

def test_static_assert_nonzero_int_passes():
    src = """
    @static_assert(1, "unreachable");
    fn main() -> int32 { return 0; }
    """
    assert run(src) == 0


def test_static_assert_true_passes():
    src = """
    @static_assert(true, "unreachable");
    fn main() -> int32 { return 0; }
    """
    assert run(src) == 0


def test_static_assert_negative_is_nonzero_and_passes():
    src = """
    @static_assert(-1, "unreachable");
    fn main() -> int32 { return 0; }
    """
    assert run(src) == 0


def test_static_assert_char_literal_folds_to_integer_and_passes():
    src = """
    @static_assert('a', "unreachable");
    fn main() -> int32 { return 0; }
    """
    assert run(src) == 0


# --- @static_assert: false conditions fail with the message ---

def test_static_assert_zero_fails_with_message():
    src = """
    @static_assert(0, "sizes must match");
    fn main() -> int32 { return 0; }
    """
    with pytest.raises(LangError, match="static assertion failed: sizes must match"):
        compile_ir(src)


def test_static_assert_false_fails():
    src = """
    @static_assert(false, "never here");
    fn main() -> int32 { return 0; }
    """
    with pytest.raises(LangError, match="static assertion failed: never here"):
        compile_ir(src)


def test_static_assert_reports_its_own_line():
    src = "\n\n@static_assert(0, \"boom\");\nfn main() -> int32 { return 0; }"
    with pytest.raises(LangError, match="line 3: static assertion failed: boom"):
        compile_ir(src)


# --- @static_assert folds the type-system constructs the docs promise ---

def test_static_assert_sizeof_layout_passes():
    src = """
    struct Pair { a: int32; b: int32; }
    @static_assert(sizeof(struct Pair) == 8, "Pair must be two words");
    fn main() -> int32 { return 0; }
    """
    assert run(src) == 0


def test_static_assert_sizeof_layout_fails():
    src = """
    struct Pair { a: int32; b: int32; }
    @static_assert(sizeof(struct Pair) == 99, "wrong Pair size");
    fn main() -> int32 { return 0; }
    """
    with pytest.raises(LangError, match="static assertion failed: wrong Pair size"):
        compile_ir(src)


def test_static_assert_alignof_folds():
    src = """
    @static_assert(alignof(int64) == 8, "int64 aligns to 8");
    fn main() -> int32 { return 0; }
    """
    assert run(src) == 0


def test_static_assert_offsetof_folds():
    src = """
    struct Row { a: int32; b: int32; }
    @static_assert(offsetof(struct Row, b) == 4, "b follows a");
    fn main() -> int32 { return 0; }
    """
    assert run(src) == 0


def test_static_assert_const_reference_folds():
    src = """
    const WIDTH = 4;
    @static_assert(WIDTH == 4, "width drifted");
    fn main() -> int32 { return 0; }
    """
    assert run(src) == 0


def test_static_assert_enum_member_folds():
    src = """
    enum Color: int32 { Red = 0, Green = 1 }
    @static_assert(Color::Green == 1, "green moved");
    fn main() -> int32 { return 0; }
    """
    assert run(src) == 0


# --- @static_assert: a non-bool/non-int constant is rejected as ill-typed ---

def test_static_assert_float_constant_is_rejected():
    src = """
    @static_assert(1.5, "float");
    fn main() -> int32 { return 0; }
    """
    with pytest.raises(
        LangError, match="condition must fold to a bool or integer constant"
    ):
        compile_ir(src)


def test_static_assert_string_constant_is_rejected():
    src = """
    @static_assert("hi", "string");
    fn main() -> int32 { return 0; }
    """
    with pytest.raises(
        LangError, match="condition must fold to a bool or integer constant"
    ):
        compile_ir(src)


def test_static_assert_pointer_constant_is_rejected():
    src = """
    @static_assert(null, "pointer");
    fn main() -> int32 { return 0; }
    """
    with pytest.raises(
        LangError, match="condition must fold to a bool or integer constant"
    ):
        compile_ir(src)


def test_static_assert_non_constant_condition_reports_the_fold_error():
    # A condition that does not fold at all is caught by eval_const itself,
    # before the type check, with its own diagnostic.
    src = """
    @static_assert(x, "runtime");
    fn main() -> int32 { return 0; }
    """
    with pytest.raises(LangError, match="'x' is not a constant"):
        compile_ir(src)


# --- @error: fails unconditionally at its position ---

def test_error_fails_unconditionally():
    src = """
    @error("this build is unsupported");
    fn main() -> int32 { return 0; }
    """
    with pytest.raises(LangError, match="this build is unsupported"):
        compile_ir(src)


def test_error_reports_its_own_line():
    src = "\n@error(\"boom\");\nfn main() -> int32 { return 0; }"
    with pytest.raises(LangError, match="line 2: boom"):
        compile_ir(src)


# --- directive messages are decoded with the usual string escapes ---

def test_directive_message_processes_escapes():
    src = r"""
    @error("line1\nline2");
    fn main() -> int32 { return 0; }
    """
    with pytest.raises(LangError) as excinfo:
        compile_ir(src)
    assert "line1\nline2" in str(excinfo.value)


# --- ordering: the first failing directive in source order wins ---

def test_first_failing_directive_wins():
    src = """
    @error("first");
    @error("second");
    fn main() -> int32 { return 0; }
    """
    with pytest.raises(LangError, match="first"):
        compile_ir(src)


# --- interaction with compile-time @if ---

def test_error_in_dead_if_branch_never_fires():
    src = """
    @if (0) {
        @error("dropped with the dead branch");
    }
    fn main() -> int32 { return 0; }
    """
    assert run(src) == 0


def test_error_in_live_if_branch_fires():
    src = """
    @if (1) {
        @error("unsupported OS");
    }
    fn main() -> int32 { return 0; }
    """
    with pytest.raises(LangError, match="unsupported OS"):
        compile_ir(src)


def test_static_assert_survives_a_live_if_branch():
    src = """
    @if (1) {
        @static_assert(0, "guarded assertion");
    }
    fn main() -> int32 { return 0; }
    """
    with pytest.raises(LangError, match="static assertion failed: guarded assertion"):
        compile_ir(src)


# --- an imported directive fires, and names the defining file ---

def test_imported_directive_fires_and_names_its_file(tmp_path):
    (tmp_path / "lib.mc").write_text(
        "\n@error(\"lib refuses to build\");\n"
    )
    with pytest.raises(LangError) as excinfo:
        compile_file_ir(
            tmp_path,
            "main.mc",
            'import "lib";\nfn main() -> int32 { return 0; }\n',
            search_paths=(tmp_path,),
        )
    err = excinfo.value
    assert "lib refuses to build" in str(err)
    assert err.source is not None and err.source.endswith("lib.mc")
