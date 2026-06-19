"""CLI tests: drive `python -m mcc` as a subprocess."""

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# The libc variant: a minimal valid program with no std/va_list dependency, so
# it compiles far enough to exercise target and codegen flags on any arch.
HELLO = ROOT / "examples" / "helloworld-libc.mc"


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
        "import \"libc/stdio\";\nimport \"libc/math\";\n"
        'fn main() -> int32 { printf("%d\\n", (sqrt(16.0) + sin(0.0)) as int32); return 0; }'
    )
    exe = tmp_path / "trig"
    assert mcc(src, "-o", exe).returncode == 0
    out = subprocess.run([exe], capture_output=True, text=True)
    assert out.stdout == "4\n"


def test_libc_math_binding(tmp_path):
    # The lib/libc/math.mc bindings, linked against libm by the driver.
    src = tmp_path / "m.mc"
    src.write_text(
        'import "libc/stdio";\n'
        'import "libc/math";\n'
        'fn main() -> int32 {\n'
        '    printf("%d\\n", (sqrt(16.0) + pow(2.0, 3.0)) as int32);  // 4 + 8\n'
        '    return 0;\n'
        '}'
    )
    exe = tmp_path / "m"
    result = mcc(src, "-o", exe)
    assert result.returncode == 0, result.stderr
    out = subprocess.run([exe], capture_output=True, text=True)
    assert out.stdout == "12\n"


def test_libc_stdio_streams(tmp_path):
    # FILE* streams: write a file then read it back, plus fprintf to stdout.
    # This links against the platform's real stdout symbol (__stdoutp / stdout),
    # so it exercises the @if/@symbol selection end to end.
    data = tmp_path / "data.txt"
    src = tmp_path / "s.mc"
    src.write_text(
        'import "libc/stdio";\n'
        "fn main() -> int32 {\n"
        f'    let path = "{data}";\n'
        '    let f = fopen(path, "w");\n'
        "    if (f == null) { return 1; }\n"
        '    fprintf(f, "value=%d\\n", 7 as int32);\n'
        "    fclose(f);\n"
        '    let g = fopen(path, "r");\n'
        "    if (g == null) { return 2; }\n"
        "    let buf: uint8[64];\n"
        "    fgets(&buf[0], 64 as int32, g);\n"
        "    fclose(g);\n"
        '    fprintf(stdout, "read %s", &buf[0]);\n'
        "    return 0;\n"
        "}"
    )
    exe = tmp_path / "s"
    result = mcc(src, "-o", exe)
    assert result.returncode == 0, result.stderr
    out = subprocess.run([exe], capture_output=True, text=True)
    assert out.stdout == "read value=7\n"


def test_libc_stdlib_string_bindings(tmp_path):
    # qsort (function-pointer arg), strtol with a base/endptr, and string
    # search/concat bindings, linked against the real libc.
    src = tmp_path / "fill.mc"
    src.write_text(
        'import "libc/stdio";\n'
        'import "libc/stdlib";\n'
        'import "libc/string";\n'
        "fn cmp(a: uint8*, b: uint8*) -> int32 {\n"
        "    return *(a as int32*) - *(b as int32*);\n"
        "}\n"
        "fn main() -> int32 {\n"
        "    let xs: int32[4];\n"
        "    xs[0] = 4; xs[1] = 2; xs[2] = 3; xs[3] = 1;\n"
        "    qsort(&xs[0] as uint8*, 4 as uint64, 4 as uint64, cmp);\n"
        '    let v = strtol("2a", null, 16 as int32);\n'
        "    let buf: uint8[16];\n"
        '    strcpy(&buf[0], "ab");\n'
        '    strcat(&buf[0], "cd");\n'
        '    printf("%d%d%d%d %lld %s %d\\n", xs[0], xs[1], xs[2], xs[3], v,\n'
        '           &buf[0], (strstr(&buf[0], "cd") != null) as int32);\n'
        "    return EXIT_SUCCESS;\n"
        "}"
    )
    exe = tmp_path / "fill"
    result = mcc(src, "-o", exe)
    assert result.returncode == 0, result.stderr
    out = subprocess.run([exe], capture_output=True, text=True)
    assert out.stdout == "1234 42 abcd 1\n"


def test_libc_errno_and_time(tmp_path):
    # errno (the __error / __errno_location split, via @if) read after a failed
    # open, plus time.h's gmtime/strftime on the epoch -- both deterministic.
    src = tmp_path / "et.mc"
    src.write_text(
        'import "libc/stdio";\n'
        'import "libc/errno";\n'
        'import "libc/time";\n'
        'import "libc/float";\n'
        "fn main() -> int32 {\n"
        "    set_errno(0 as int32);\n"
        '    fopen("/no/such/file", "r");\n'
        "    let t = 0 as int64;\n"
        "    let buf: uint8[32];\n"
        '    strftime(&buf[0], 32 as uint64, "%Y-%m-%d", gmtime(&t));\n'
        '    printf("%d %s %d\\n", (errno() != 0) as int32, &buf[0], DBL_DIG);\n'
        "    return 0;\n"
        "}"
    )
    exe = tmp_path / "et"
    result = mcc(src, "-o", exe)
    assert result.returncode == 0, result.stderr
    out = subprocess.run([exe], capture_output=True, text=True)
    assert out.stdout == "1 1970-01-01 15\n"


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
import "libc/stdio";
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


def test_nostdlib_drops_stdlib_path(tmp_path):
    main = tmp_path / "main.mc"
    main.write_text(STDLIB_IMPORT)
    result = mcc(main, "--nostdlib")
    assert result.returncode == 1
    assert "cannot import 'memory'" in result.stderr


def test_import_path_flag(tmp_path):
    libs = tmp_path / "mylibs"
    libs.mkdir()
    (libs / "helper.mc").write_text("fn seven() -> int32 { return 7; }")
    main = tmp_path / "main.mc"
    main.write_text('import "helper";\nfn main() -> int32 { return seven(); }')
    assert mcc(main, "--nostdlib").returncode == 1  # not found without -I
    result = mcc(main, "--nostdlib", "-I", libs, "--run")
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


DEFINE_SRC = """
import "libc/stdio";
fn main() -> int32 {
    @if (FEATURE) { puts("on"); } @else { puts("off"); }
    @if (LEVEL >= 2) { puts("hi"); }
    return 0;
}
"""


def test_define_selects_branches(tmp_path):
    src = tmp_path / "d.mc"
    src.write_text(DEFINE_SRC)
    assert mcc(src, "--run").stdout == "off\n"                       # no defines
    assert mcc(src, "-DFEATURE", "--run").stdout == "on\n"           # bare = 1
    assert mcc(src, "-DFEATURE", "-DLEVEL=2", "--run").stdout == "on\nhi\n"
    assert mcc(src, "-DLEVEL=0x3", "--run").stdout == "off\nhi\n"    # hex value


def test_define_bad_name_is_clean_error(tmp_path):
    src = tmp_path / "d.mc"
    src.write_text(DEFINE_SRC)
    result = mcc(src, "-D2bad", "--run")
    assert result.returncode == 1
    assert "mcc: error:" in result.stderr and "Traceback" not in result.stderr


def test_define_non_integer_value_is_clean_error(tmp_path):
    src = tmp_path / "d.mc"
    src.write_text(DEFINE_SRC)
    result = mcc(src, "-DLEVEL=high", "--run")
    assert result.returncode == 1
    assert "not an integer" in result.stderr


def test_define_cannot_redefine_a_target_fact(tmp_path):
    src = tmp_path / "d.mc"
    src.write_text(DEFINE_SRC)
    result = mcc(src, "-DTARGET_ARCH=9", "--run")
    assert result.returncode == 1
    assert "built-in target fact" in result.stderr
