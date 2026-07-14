"""stdlib panic/assert: report to stderr and abort.

`panic(msg)` writes `msg` verbatim to standard error and aborts;
`assert(cond, msg)` panics with `assertion failed: msg` when cond is false
and does nothing otherwise -- always enabled. Each is ONE member whose
message parameter is the const-covariant char-run family
(`T extends slice<const char>`), so a literal, a slice of either
constness, a `string`, and with it an f-string or `.format` rendering all
bind -- there is no `@format` variadic pair anymore, so braces are never
re-scanned. A rendering built for the panic path is never destroyed (the
process is dying); the blessed style is a plain verbatim message. panic is
`@noreturn`, so a call diverges like a `return`: it satisfies
missing-return analysis and an `if (p == null) { panic(...); }` guard
narrows p.

Anything that actually reaches abort() at runtime must NOT run through the
in-process JIT helpers (SIGABRT would take pytest down with it, see the
precedent in test_cli.py); the failure paths run `python -m mcc --run` as a
subprocess instead. Passing asserts and compile-shape checks stay
in-process.
"""

import signal
import subprocess
import sys
from pathlib import Path

import pytest

from mcc.codegen import CodeGen
from mcc.driver import STDLIB_DIR, merge_imports
from mcc.errors import LangError
from helpers import compile_ir, parse, run

ROOT = Path(__file__).resolve().parents[1]
IO = 'import "std/io";\n'


def run_aborting(tmp_path, source: str) -> subprocess.CompletedProcess:
    """JIT-run a source expected to abort, as a subprocess of the repo CLI."""
    src = tmp_path / "prog.mc"
    src.write_text(source)
    return subprocess.run(
        [sys.executable, "-m", "mcc", str(src), "--run"],
        cwd=ROOT, capture_output=True, text=True,
    )


# ------------------------------------------------------------ compile shape

def test_panic_members_carry_the_noreturn_attribute():
    ir = compile_ir(
        IO
        + 'fn main() -> int32 { assert(true, "ok"); panic("boom"); }'
    )
    # One generic member each; a literal binds T = slice<const char>.
    panics = [
        line
        for line in ir.splitlines()
        if line.startswith('define') and '@"panic<' in line
    ]
    assert panics and all("noreturn" in line for line in panics)
    # assert() returns on the passing path, so it must NOT be noreturn.
    for line in ir.splitlines():
        if line.startswith("define") and '@"assert<' in line:
            assert "noreturn" not in line


def test_a_trailing_panic_satisfies_missing_return():
    # The @noreturn call terminates the block like a `return`, through the
    # overload-set path: no dummy return is needed after it.
    compile_ir(
        IO
        + """
        fn last(n: int32) -> int32 {
            if (n > 0) { return n; }
            panic(f"no positive value, got {n}");
        }
        fn main() -> int32 { return last(1); }
        """
    )


def test_a_panic_guard_narrows_the_pointer():
    # `if (p == null) { panic(...); }` diverges, so the deref below it is
    # proven and -Wunchecked-dereference has nothing to report.
    src = (
        IO
        + """
        fn first(p: int32*) -> int32 {
            if (p == null) { panic("null input"); }
            return *p;
        }
        fn main() -> int32 { let x = 7 as int32; return first(&x); }
        """
    )
    cg = CodeGen(merge_imports(parse(src), STDLIB_DIR, (STDLIB_DIR,)), "test")
    cg.generate()
    assert not [w for w in cg.warnings if w.wclass == "unchecked-dereference"]


def test_the_msg_arm_keeps_braces_literal():
    # There is no collecting member left: a message is verbatim, so braces
    # are not placeholders -- the interned literal survives whole and the
    # literal binds the slice<const char> instance of the one generic.
    ir = compile_ir(IO + 'fn main() -> int32 { panic("hello {} braces"); }')
    assert 'c"hello {} braces\\00"' in ir
    assert (
        'call void @"panic<$0 extends slice<const char>>'
        '($0)<slice<const char>>"' in ir
    )


def test_an_fstring_panic_renders_to_a_string_value():
    # With no @format collector on panic, an f-string message is an
    # ordinary string value: it renders through slice::format and binds
    # the message family's string instance. The rendering is never
    # destroyed -- panic diverges, the process is dying -- which the
    # variadic member always did invisibly; the value spelling just makes
    # the allocation explicit at the call site.
    ir = compile_ir(
        IO
        + "fn main() -> int32 { let x = 1 as int32; "
        'panic(f"x = {x}"); }'
    )
    assert (
        'call void @"panic<$0 extends slice<const char>>'
        '($0)<list<char>>"' in ir
    )


def test_an_fstring_without_a_format_candidate_is_a_string_value():
    # With no @format collector in the set, the f-string renders to an
    # owned string and resolution re-runs over that value -- here neither
    # member takes a string (and there is no implicit string-to-slice
    # coercion), so the miss is the honest no-overload error.
    with pytest.raises(
        LangError,
        match=r"no overload of 'take' with signature take\(list<char>\)",
    ):
        compile_ir(
            IO
            + "fn take(s: slice<const char>) {}\n"
            "fn take(n: int32) {}\n"
            'fn main() -> int32 { take(f"{1 as int32}"); return 0; }'
        )


# ------------------------------------------------------------ passing path

def test_passing_asserts_return_normally(capfd):
    status = run(
        IO
        + """
        fn main() -> int32 {
            let x = 41 as int32;
            assert(true, "never fires");
            assert(x > 0, f"x must be positive, got {x}");
            println("alive");
            return 0;
        }
        """
    )
    assert status == 0
    assert capfd.readouterr().out == "alive\n"


# ------------------------------------------------------------ failure paths

def test_panic_msg_writes_stderr_and_aborts(tmp_path):
    out = run_aborting(
        tmp_path,
        IO + 'fn main() -> int32 { panic("hello {} braces stay literal"); }',
    )
    assert out.returncode == -signal.SIGABRT
    assert out.stderr == "hello {} braces stay literal\n"


def test_panic_renders_format_and_fstring_arguments(tmp_path):
    out = run_aborting(
        tmp_path,
        IO
        + """
        fn main() -> int32 {
            let x = 41 as int32;
            panic(f"x = {x}, giving up");
        }
        """,
    )
    assert out.returncode == -signal.SIGABRT
    assert out.stderr == "x = 41, giving up\n"


def test_assert_failure_message_is_not_double_prefixed(tmp_path):
    out = run_aborting(
        tmp_path,
        IO + 'fn main() -> int32 { assert(1 > 2, "one exceeds two"); return 0; }',
    )
    assert out.returncode == -signal.SIGABRT
    assert out.stderr == "assertion failed: one exceeds two\n"


def test_assert_failure_renders_format_arguments(tmp_path):
    out = run_aborting(
        tmp_path,
        IO
        + """
        fn main() -> int32 {
            let want = 3 as int32;
            let got = 5 as int32;
            assert(want == got, f"want {want}, got {got}");
            return 0;
        }
        """,
    )
    assert out.returncode == -signal.SIGABRT
    assert out.stderr == "assertion failed: want 3, got 5\n"


def test_stdout_is_flushed_before_the_panic(tmp_path):
    # glibc does not flush stdio buffers on abort(), so panic flushes stdout
    # itself: pending output must survive, in order, on every platform.
    out = run_aborting(
        tmp_path,
        IO
        + """
        fn main() -> int32 {
            print("pending output");   // no newline: stays buffered
            panic("down we go");
        }
        """,
    )
    assert out.returncode == -signal.SIGABRT
    assert out.stdout == "pending output"
    assert out.stderr == "down we go\n"


def test_defers_do_not_run_on_the_panic_path(tmp_path):
    # abort() never unwinds: a @noreturn call is not a block exit, so
    # enclosing defers stay unrun, exactly like exit() (see test_cli.py).
    out = run_aborting(
        tmp_path,
        IO
        + """
        fn main() -> int32 {
            defer println("cleanup");
            println("before");
            panic("no unwinding");
        }
        """,
    )
    assert out.returncode == -signal.SIGABRT
    assert out.stdout == "before\n"  # the deferred cleanup never printed
    assert out.stderr == "no unwinding\n"
