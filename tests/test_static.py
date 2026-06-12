"""@static: file-scoped names that other files can freely reuse."""

import pytest

from mcc.errors import LangError
from helpers import run_path


def test_same_static_name_in_two_files(tmp_path):
    # The collision @private couldn't solve: both files own a `scale`.
    (tmp_path / "a.mc").write_text(
        "@static\nfn scale(x: int32) -> int32 { return x * 2; }\n"
        "fn double_it(x: int32) -> int32 { return scale(x); }"
    )
    (tmp_path / "b.mc").write_text(
        "@static\nfn scale(x: int32) -> int32 { return x * 10; }\n"
        "fn tenfold(x: int32) -> int32 { return scale(x); }"
    )
    main = tmp_path / "main.mc"
    main.write_text(
        'import "a";\nimport "b";\n'
        "fn main() -> int32 { return double_it(3) + tenfold(3); }"
    )
    assert run_path(main) == 36  # 6 + 30


def test_static_shadows_imported_public(tmp_path):
    (tmp_path / "lib.mc").write_text("fn answer() -> int32 { return 1; }")
    main = tmp_path / "main.mc"
    main.write_text(
        'import "lib";\n'
        "@static\nfn answer() -> int32 { return 2; }\n"
        "fn main() -> int32 { return answer(); }"
    )
    assert run_path(main) == 2


def test_static_fn_invisible_to_other_files(tmp_path):
    (tmp_path / "lib.mc").write_text("@static\nfn helper() -> int32 { return 1; }")
    main = tmp_path / "main.mc"
    main.write_text('import "lib";\nfn main() -> int32 { return helper(); }')
    with pytest.raises(LangError, match="undefined function 'helper'"):
        run_path(main)


def test_same_static_generic_in_two_files(tmp_path):
    (tmp_path / "a.mc").write_text(
        "@static\nfn pick<T>(x: T, y: T) -> T { return x; }\n"
        "fn first64(x: int64, y: int64) -> int64 { return pick(x, y); }"
    )
    (tmp_path / "b.mc").write_text(
        "@static\nfn pick<T>(x: T, y: T) -> T { return y; }\n"
        "fn second64(x: int64, y: int64) -> int64 { return pick(x, y); }"
    )
    main = tmp_path / "main.mc"
    main.write_text(
        'import "a";\nimport "b";\n'
        "fn main() -> int32 { return (first64(1, 2) + second64(10, 20)) as int32; }"
    )
    assert run_path(main) == 21  # 1 + 20


def test_same_static_struct_in_two_files(tmp_path):
    (tmp_path / "a.mc").write_text(
        "@static\nstruct blob { x: uint8; }\n"
        "fn a_size() -> uint64 { return sizeof(struct blob); }"
    )
    (tmp_path / "b.mc").write_text(
        "@static\nstruct blob { x: int64; y: int64; }\n"
        "fn b_size() -> uint64 { return sizeof(struct blob); }"
    )
    main = tmp_path / "main.mc"
    main.write_text(
        'import "a";\nimport "b";\n'
        "fn main() -> int32 { return (a_size() * 100 + b_size()) as int32; }"
    )
    assert run_path(main) == 116  # 1 byte and 16 bytes


def test_static_and_public_same_name_same_file(tmp_path):
    main = tmp_path / "main.mc"
    main.write_text(
        "@static\nfn f() -> int32 { return 1; }\n"
        "fn f() -> int32 { return 2; }\n"
        "fn main() -> int32 { return f(); }"
    )
    with pytest.raises(LangError, match="function 'f' already defined"):
        run_path(main)
