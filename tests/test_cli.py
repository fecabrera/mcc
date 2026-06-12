"""CLI tests: drive `python -m mcc` as a subprocess."""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HELLO = ROOT / "examples" / "helloworld.mc"


def mcc(*args, **kwargs):
    return subprocess.run(
        [sys.executable, "-m", "mcc", *map(str, args)],
        cwd=ROOT, capture_output=True, text=True, **kwargs,
    )


def test_emit_llvm():
    result = mcc(HELLO, "--emit-llvm")
    assert result.returncode == 0
    assert 'define i32 @"main"()' in result.stdout


def test_native_build_and_run(tmp_path):
    exe = tmp_path / "hello"
    result = mcc(HELLO, "-o", exe)
    assert result.returncode == 0, result.stderr
    assert not exe.with_suffix(".o").exists()  # intermediate object cleaned up
    out = subprocess.run([exe], capture_output=True, text=True)
    assert out.stdout == "hello, world\n"
    assert out.returncode == 0


def test_compile_error_exit_code(tmp_path):
    bad = tmp_path / "bad.mc"
    bad.write_text("fn main() -> int32 { return x; }")
    result = mcc(bad)
    assert result.returncode == 1
    assert "error: line 1: undefined variable 'x'" in result.stderr


def test_missing_file_is_clean_error():
    result = mcc("does-not-exist.mc")
    assert result.returncode == 1
    assert "cannot read" in result.stderr
    assert "Traceback" not in result.stderr


STDLIB_IMPORT = """
import "memory";
#include <stdio.h>
fn main() -> int32 {
    let p = alloc<int32>(1);
    dealloc(p);
    puts("ok");
    return 0;
}
"""


def test_stdlib_import_by_default(tmp_path):
    main = tmp_path / "main.mc"
    main.write_text(STDLIB_IMPORT)
    result = mcc(main, "--run")
    assert result.returncode == 0
    assert result.stdout == "ok\n"


def test_naked_drops_stdlib_path(tmp_path):
    main = tmp_path / "main.mc"
    main.write_text(STDLIB_IMPORT)
    result = mcc(main, "--naked")
    assert result.returncode == 1
    assert "cannot import 'memory'" in result.stderr


def test_import_path_flag(tmp_path):
    libs = tmp_path / "mylibs"
    libs.mkdir()
    (libs / "helper.mc").write_text("fn seven() -> int32 { return 7; }")
    main = tmp_path / "main.mc"
    main.write_text('import "helper";\nfn main() -> int32 { return seven(); }')
    assert mcc(main, "--naked").returncode == 1  # not found without -I
    result = mcc(main, "--naked", "-I", libs, "--run")
    assert result.returncode == 7
