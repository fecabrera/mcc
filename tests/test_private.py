"""@private: functions and structs usable only within their defining file."""

from pathlib import Path

import pytest

from mcc.errors import LangError
from helpers import parse, run_path

LIB = """
@private
fn secret() -> int32 { return 7; }

fn blessed() -> int32 { return secret(); }  // same file: allowed

@private
struct hidden { x: int32; }

@private
fn generic_secret<T>(v: T) -> T { return v; }
"""


def write_lib(tmp_path):
    (tmp_path / "lib.mc").write_text(LIB)


def test_private_fn_callable_within_its_file(tmp_path):
    write_lib(tmp_path)
    main = tmp_path / "main.mc"
    main.write_text('import "lib";\nfn main() -> int32 { return blessed(); }')
    assert run_path(main) == 7


def test_private_fn_blocked_across_files(tmp_path):
    write_lib(tmp_path)
    main = tmp_path / "main.mc"
    main.write_text('import "lib";\nfn main() -> int32 { return secret(); }')
    with pytest.raises(LangError, match="function 'secret' is private to lib.mc"):
        run_path(main)


def test_private_generic_fn_blocked_across_files(tmp_path):
    write_lib(tmp_path)
    main = tmp_path / "main.mc"
    main.write_text(
        'import "lib";\nfn main() -> int32 { return generic_secret(1 as int32); }'
    )
    with pytest.raises(LangError, match="function 'generic_secret' is private"):
        run_path(main)


def test_private_struct_blocked_across_files(tmp_path):
    write_lib(tmp_path)
    main = tmp_path / "main.mc"
    main.write_text(
        'import "lib";\nfn main() -> int32 { let p: struct hidden* = null; return 0; }'
    )
    with pytest.raises(LangError, match="struct 'hidden' is private to lib.mc"):
        run_path(main)


def test_privacy_holds_through_transitive_imports(tmp_path):
    write_lib(tmp_path)
    (tmp_path / "middle.mc").write_text('import "lib";\n')
    main = tmp_path / "main.mc"
    main.write_text('import "middle";\nfn main() -> int32 { return secret(); }')
    with pytest.raises(LangError, match="is private to lib.mc"):
        run_path(main)


def test_stdlib_internals_are_private(tmp_path):
    main = tmp_path / "main.mc"
    main.write_text(
        'import "std/list";\n'
        "fn main() -> int32 {\n"
        "    let a = alloc<struct list<int32>>(1);\n"
        "    if (a == null) return 1;    // proves a for the receiver slots below\n"
        "    list_init(a, 1);\n"
        "    list_grow(a);\n"
        "    return 0;\n"
        "}"
    )
    with pytest.raises(LangError, match="function 'list_grow' is private to list.mc"):
        run_path(main)


def test_unknown_annotation_is_an_error():
    with pytest.raises(LangError, match="unknown annotation '@nonsense'"):
        parse("@nonsense\nfn f() {}")
