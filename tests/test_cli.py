"""CLI tests: drive `python -m mcc` as a subprocess."""

import os
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


def test_native_build_links_libm(tmp_path):
    # math.h functions require libm; the driver links -lm.
    src = tmp_path / "trig.mc"
    src.write_text(
        "#include <stdio.h>\n#include <math.h>\n"
        'fn main() -> int32 { printf("%d\\n", (sqrt(16.0) + sin(0.0)) as int32); return 0; }'
    )
    exe = tmp_path / "trig"
    assert mcc(src, "-o", exe).returncode == 0
    out = subprocess.run([exe], capture_output=True, text=True)
    assert out.stdout == "4\n"


def test_compile_error_exit_code(tmp_path):
    bad = tmp_path / "bad.mc"
    bad.write_text("fn main() -> int32 { return x; }")
    result = mcc(bad)
    assert result.returncode == 1
    assert "error: line 1: undefined variable 'x'" in result.stderr


def test_error_names_the_file_it_came_from(tmp_path):
    # The failing line is in the imported file; the message must say so
    # rather than blaming the entry file.
    (tmp_path / "lib.mc").write_text(
        "@extern fn ext(n: uint64);\n"
        "fn wrap(n: uint64) -> int32* {\n"
        "    return ext(n) as int32*;\n"
        "}\n"
    )
    main = tmp_path / "main.mc"
    main.write_text('import "lib";\nfn main() -> int32 { let p = wrap(4); return 0; }')
    result = mcc(main)
    assert result.returncode == 1
    assert f"{tmp_path / 'lib.mc'}: error: line 3:" in result.stderr
    assert "main.mc" not in result.stderr


def test_parse_error_names_the_imported_file(tmp_path):
    (tmp_path / "bad.mc").write_text("fn broken( {}")
    main = tmp_path / "main.mc"
    main.write_text('import "bad";\nfn main() {}')
    result = mcc(main)
    assert result.returncode == 1
    assert f"{tmp_path / 'bad.mc'}: error: line 1:" in result.stderr


def test_error_paths_under_cwd_print_relative(tmp_path):
    (tmp_path / "lib.mc").write_text("fn broken( {}")
    (tmp_path / "main.mc").write_text('import "lib";\nfn main() {}')
    result = subprocess.run(
        [sys.executable, "-m", "mcc", "main.mc"],
        cwd=tmp_path, capture_output=True, text=True,
        env={**os.environ, "PYTHONPATH": str(ROOT)},
    )
    assert result.returncode == 1
    assert result.stderr.startswith("lib.mc: error: line 1:")


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


def test_target_cross_compiles_an_elf_object(tmp_path):
    obj = tmp_path / "hello.o"
    result = mcc(HELLO, "--target", "aarch64-unknown-none-elf", "-o", obj)
    assert result.returncode == 0, result.stderr
    assert obj.read_bytes()[:4] == b"\x7fELF"


def test_target_defaults_to_object_beside_source(tmp_path):
    src = tmp_path / "answer.mc"
    src.write_text("fn answer() -> int32 { return 42; }")
    result = mcc(src, "--target", "x86_64-unknown-linux-gnu")
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "answer.o").read_bytes()[:4] == b"\x7fELF"


def test_general_regs_only_cross_compiles(tmp_path):
    obj = tmp_path / "hello.o"
    result = mcc(HELLO, "--target", "aarch64-unknown-none-elf",
                 "--general-regs-only", "-o", obj)
    assert result.returncode == 0, result.stderr
    assert obj.read_bytes()[:4] == b"\x7fELF"


def test_general_regs_only_rejects_unknown_arch(tmp_path):
    result = mcc(HELLO, "--target", "sparc-unknown-none", "--general-regs-only")
    assert result.returncode == 1
    assert "mcc: error:" in result.stderr
    assert "general-regs-only" in result.stderr
    assert "Traceback" not in result.stderr


def test_target_rejects_run():
    result = mcc(HELLO, "--target", "aarch64-unknown-none-elf", "--run")
    assert result.returncode == 1
    assert "--run" in result.stderr


def test_target_bad_triple_is_clean_error():
    result = mcc(HELLO, "--target", "not-a-triple")
    assert result.returncode == 1
    assert "mcc: error:" in result.stderr
    assert "Traceback" not in result.stderr
