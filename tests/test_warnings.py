"""The warning channel: @warning directives collected on CodeGen.warnings."""

import pytest

from mcc.codegen import CodeGen
from mcc.driver import compile_to_ir
from mcc.errors import LangError, Note
from helpers import parse, run


def generate(source: str) -> CodeGen:
    """Compile a source string and return the CodeGen, warnings and all."""
    cg = CodeGen(parse(source), "test")
    cg.generate()
    return cg


# --- warn(): the channel itself ---

def test_warn_collects_a_note_with_the_current_source():
    cg = CodeGen(parse("fn main() -> int32 { return 0; }"), "test")
    cg.current_source = "somewhere.mc"
    cg.warn("stamped at emission", 7)
    assert cg.warnings == [Note("stamped at emission", 7, "somewhere.mc")]


def test_warn_never_raises_and_generation_succeeds():
    src = """
    @warning("still compiles");
    fn main() -> int32 { return 0; }
    """
    cg = generate(src)
    assert [(w.message, w.line) for w in cg.warnings] == [("still compiles", 2)]
    assert 'define i32 @"main"()' in str(cg.module)


def test_warned_program_still_runs():
    src = """
    @warning("non-fatal");
    fn main() -> int32 { return 42; }
    """
    assert run(src) == 42


# --- ordering: emission order is source order, no dedup ---

def test_warnings_preserve_emission_order():
    src = """
    @warning("first");
    @warning("second");
    @warning("first");
    fn main() -> int32 { return 0; }
    """
    assert [w.message for w in generate(src).warnings] == [
        "first", "second", "first",
    ]


# --- messages decode the usual string escapes ---

def test_warning_message_processes_escapes():
    src = r"""
    @warning("line1\nline2");
    fn main() -> int32 { return 0; }
    """
    assert generate(src).warnings[0].message == "line1\nline2"


# --- interaction with compile-time @if ---

def test_warning_in_dead_if_branch_never_fires():
    src = """
    @if (0) {
        @warning("dropped with the dead branch");
    }
    fn main() -> int32 { return 0; }
    """
    assert generate(src).warnings == []


def test_warning_in_live_if_branch_fires():
    src = """
    @if (1) {
        @warning("live branch warns");
    }
    fn main() -> int32 { return 0; }
    """
    assert [w.message for w in generate(src).warnings] == ["live branch warns"]


# --- compile_to_ir: the out-list keyword ---

def test_compile_to_ir_extends_the_out_list(tmp_path):
    path = tmp_path / "w.mc"
    path.write_text('@warning("from a file");\nfn main() -> int32 { return 0; }\n')
    warnings = []
    compile_to_ir(path, (), warnings=warnings)
    assert [(w.message, w.line, w.source) for w in warnings] == [
        ("from a file", 1, str(path)),
    ]


def test_compile_to_ir_without_the_keyword_discards_warnings(tmp_path):
    # The pre-warning call shape (~15 test call sites) keeps working untouched.
    path = tmp_path / "w.mc"
    path.write_text('@warning("discarded");\nfn main() -> int32 { return 0; }\n')
    module = compile_to_ir(path, ())
    assert 'define i32 @"main"()' in str(module)


def test_warnings_before_a_hard_error_are_dropped(tmp_path):
    # "After success" is literal: the except path never sees the list filled.
    path = tmp_path / "w.mc"
    path.write_text(
        '@warning("collected then dropped");\n'
        '@error("boom");\n'
        "fn main() -> int32 { return 0; }\n"
    )
    warnings = []
    with pytest.raises(LangError, match="line 2: boom"):
        compile_to_ir(path, (), warnings=warnings)
    assert warnings == []


# --- an imported @warning is attributed to the file that declares it ---

def test_imported_warning_names_its_file(tmp_path):
    (tmp_path / "lib.mc").write_text('@warning("lib is grumpy");\n')
    main = tmp_path / "main.mc"
    main.write_text('import "lib";\nfn main() -> int32 { return 0; }\n')
    warnings = []
    compile_to_ir(main, (tmp_path,), warnings=warnings)
    assert len(warnings) == 1
    assert warnings[0].message == "lib is grumpy"
    assert warnings[0].line == 1
    assert warnings[0].source is not None and warnings[0].source.endswith("lib.mc")
