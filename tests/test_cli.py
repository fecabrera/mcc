"""CLI tests: drive `python -m mcc` as a subprocess."""

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# The libc variant: a minimal valid program with no std/va_list dependency, so
# it compiles far enough to exercise target and codegen flags on any arch.
HELLO = ROOT / "examples" / "basics" / "helloworld-libc.mc"


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
        "    let buf: char[64];\n"
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
        "    let buf: char[16];\n"
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
        "    let buf: char[32];\n"
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


def test_instantiation_note_names_the_requesting_file(tmp_path):
    # Mirror of test_error_names_the_file_it_came_from, with the failing line
    # inside a generic body: the primary line still blames the template's
    # file, and a note now traces the instantiation back to the requesting
    # file's call line.
    (tmp_path / "lib.mc").write_text(
        "fn wrap<T>(n: T) -> int32* {\n"
        "    return oops;\n"
        "}\n"
    )
    main = tmp_path / "main.mc"
    main.write_text('import "lib";\nfn main() -> int32 { let p = wrap(4); return 0; }')
    result = mcc(main)
    assert result.returncode == 1
    lines = result.stderr.splitlines()
    assert lines[0] == (
        f"{tmp_path / 'lib.mc'}: error: line 2: undefined variable 'oops'"
    )
    assert lines[1] == f"{main}: note: line 2: in instantiation of wrap<int32>"


def test_instantiation_notes_trace_a_stdlib_chain(tmp_path):
    # An error deep inside a stdlib generic renders one note per
    # instantiation frame, innermost first: hashing a by-value struct key
    # fails inside splitmix64<box>, requested by hash<box>, requested by the
    # user's call. Stdlib lines are asserted loosely so libmc edits don't
    # break the test; the user-file note is exact.
    src = tmp_path / "chain.mc"
    src.write_text(
        'import "hash";\n'
        "struct box { x: int32; }\n"
        "fn main() -> int32 {\n"
        "    let b: struct box;\n"
        "    let h = hash(b);\n"
        "    return 0;\n"
        "}\n"
    )
    result = mcc(src)
    assert result.returncode == 1
    lines = result.stderr.splitlines()
    assert lines[0].startswith("libmc/hashing/splitmix64.mc: error: line ")
    assert lines[0].endswith("cannot cast box to uint64")
    assert lines[1].startswith("libmc/hash.mc: note: line ")
    assert lines[1].endswith("in instantiation of splitmix64<box>")
    assert lines[2] == f"{src}: note: line 5: in instantiation of hash<box>"


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


def test_strict_align_cross_compiles(tmp_path):
    obj = tmp_path / "hello.o"
    result = mcc(HELLO, "--target", "aarch64-unknown-none-elf",
                 "--strict-align", "-o", obj)
    assert result.returncode == 0, result.stderr
    assert obj.read_bytes()[:4] == b"\x7fELF"


def test_strict_align_with_general_regs_only(tmp_path):
    # The two feature flags combine on one build.
    obj = tmp_path / "hello.o"
    result = mcc(HELLO, "--target", "aarch64-unknown-none-elf",
                 "--general-regs-only", "--strict-align", "-o", obj)
    assert result.returncode == 0, result.stderr
    assert obj.read_bytes()[:4] == b"\x7fELF"


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


# ----------------------------------------------------------- -c / interface

def test_compile_only_emits_object(tmp_path):
    src = tmp_path / "lib.mc"
    src.write_text("fn twice(n: int32) -> int32 { return n * 2; }")
    obj = tmp_path / "lib.o"
    result = mcc(src, "-c", "-o", obj)
    assert result.returncode == 0, result.stderr
    assert obj.exists()
    assert not (tmp_path / "lib").exists()  # no executable, no link step


def test_compile_only_default_object_name(tmp_path):
    src = tmp_path / "lib.mc"
    src.write_text("fn one() -> int32 { return 1; }")
    assert mcc(src, "-c").returncode == 0
    assert (tmp_path / "lib.o").exists()


def test_compile_only_rejects_run(tmp_path):
    src = tmp_path / "lib.mc"
    src.write_text("fn main() -> int32 { return 0; }")
    result = mcc(src, "-c", "--run")
    assert result.returncode == 1 and "compile only" in result.stderr


# ------------------------------------------------------- linker passthrough

TWICE_LIB = "fn twice(n: int32) -> int32 { return n * 2; }"
TWICE_APP = (
    'import "libc/stdio";\n'
    "@extern fn twice(n: int32) -> int32;\n"
    'fn main() -> int32 { printf("%d\\n", twice(21)); return 0; }'
)


def build_twice_object(tmp_path):
    (tmp_path / "twice.mc").write_text(TWICE_LIB)
    assert mcc(tmp_path / "twice.mc", "-c").returncode == 0
    (tmp_path / "app.mc").write_text(TWICE_APP)
    return tmp_path / "twice.o"


def test_link_extra_object(tmp_path):
    # An extra non-.mc positional is forwarded to the link.
    obj = build_twice_object(tmp_path)
    exe = tmp_path / "app"
    result = mcc(tmp_path / "app.mc", obj, "-o", exe)
    assert result.returncode == 0, result.stderr
    assert subprocess.run([exe], capture_output=True, text=True).stdout == "42\n"


def test_link_archive_via_l_and_L(tmp_path):
    # -L adds a search path and -l names a library, both forwarded to cc.
    obj = build_twice_object(tmp_path)
    subprocess.run(["ar", "rcs", tmp_path / "libtwice.a", obj], check=True)
    exe = tmp_path / "app"
    result = mcc(tmp_path / "app.mc", "-L", tmp_path, "-ltwice", "-o", exe)
    assert result.returncode == 0, result.stderr
    assert subprocess.run([exe], capture_output=True, text=True).stdout == "42\n"


def test_explicit_lm_does_not_duplicate(tmp_path):
    # The driver always links libm; an explicit -lm must not break that.
    src = tmp_path / "trig.mc"
    src.write_text(
        'import "libc/stdio";\nimport "libc/math";\n'
        'fn main() -> int32 { printf("%d\\n", sqrt(16.0) as int32); return 0; }'
    )
    exe = tmp_path / "trig"
    assert mcc(src, "-lm", "-o", exe).returncode == 0
    assert subprocess.run([exe], capture_output=True, text=True).stdout == "4\n"


def test_link_extras_rejected_when_not_linking(tmp_path):
    obj = build_twice_object(tmp_path)
    for flag in ("--run", "-c", "--emit-llvm"):
        result = mcc(tmp_path / "app.mc", obj, flag)
        assert result.returncode == 1
        assert "apply only when linking" in result.stderr and flag in result.stderr
    result = mcc(tmp_path / "app.mc", "-ltwice", "--target", "aarch64-unknown-none-elf")
    assert result.returncode == 1 and "--target" in result.stderr


def test_missing_link_input_is_clean_error(tmp_path):
    (tmp_path / "app.mc").write_text(TWICE_APP)
    result = mcc(tmp_path / "app.mc", tmp_path / "nope.o")
    assert result.returncode == 1
    assert "cannot read" in result.stderr and "nope.o" in result.stderr
    assert "Traceback" not in result.stderr


def test_multiple_mc_sources_rejected(tmp_path):
    (tmp_path / "a.mc").write_text(TWICE_LIB)
    (tmp_path / "b.mc").write_text(TWICE_APP)
    result = mcc(tmp_path / "a.mc", tmp_path / "b.mc")
    assert result.returncode == 1
    assert "exactly one .mc source" in result.stderr


def test_link_failure_is_clean_error(tmp_path):
    (tmp_path / "app.mc").write_text(TWICE_APP)  # `twice` never defined
    exe = tmp_path / "app"
    result = mcc(tmp_path / "app.mc", "-o", exe)
    assert result.returncode == 1
    assert "linking failed" in result.stderr
    assert "Traceback" not in result.stderr
    assert not exe.with_suffix(".o").exists()  # intermediate still cleaned up


def test_link_input_may_not_collide_with_intermediate_object(tmp_path):
    # `mcc app.mc app.o` would overwrite and then delete the given app.o.
    obj = build_twice_object(tmp_path)
    (tmp_path / "app.o").write_bytes(obj.read_bytes())
    result = mcc(tmp_path / "app.mc", tmp_path / "app.o", "-o", tmp_path / "app")
    assert result.returncode == 1
    assert "collides" in result.stderr
    assert (tmp_path / "app.o").exists()  # the input was left alone


def test_interface_package_roundtrip(tmp_path):
    # Build a library to an object + interface, drop the source, then compile a
    # consumer that imports it by bare name (resolving to the .mci) and links
    # the object.
    lib = tmp_path / "mathlib.mc"
    lib.write_text(
        "const SCALE = 10;\n"
        "fn scaled(n: int32) -> int32 { return n * SCALE; }\n"
    )
    assert mcc(lib, "-c", "-o", tmp_path / "mathlib.o").returncode == 0
    assert mcc(lib, "--emit-interface").returncode == 0
    assert (tmp_path / "mathlib.mci").exists()
    lib.unlink()  # ship only the .o + .mci

    consumer = tmp_path / "app.mc"
    consumer.write_text(
        'import "mathlib";\n'
        "@extern fn printf(fmt: uint8*, ...) -> int32;\n"
        'fn main() -> int32 { printf("%d\\n", scaled(5)); return 0; }'
    )
    assert mcc(consumer, "-c", "-I", tmp_path, "-o", tmp_path / "app.o").returncode == 0
    exe = tmp_path / "app"
    link = subprocess.run(
        ["cc", str(tmp_path / "app.o"), str(tmp_path / "mathlib.o"), "-o", str(exe)],
        capture_output=True, text=True,
    )
    assert link.returncode == 0, link.stderr
    out = subprocess.run([exe], capture_output=True, text=True)
    assert out.stdout == "50\n"
