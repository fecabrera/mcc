"""@inline: emit functions with LLVM's alwaysinline so they fold into callers.

The attribute is honored by the always-inliner, which runs only when
optimizing, so the unoptimized IR still carries a standalone body -- the
checks here look for the attribute, then confirm that at -O2 the call site
actually disappears.
"""

import re

import pytest

from mcc.codegen import CodeGen
from mcc.driver import build_native_module, compile_to_ir
from mcc.errors import LangError
from tests.helpers import compile_ir, parse, run


def define_line(ir_text, symbol):
    (line,) = [ln for ln in ir_text.splitlines()
               if ln.startswith("define") and f'@"{symbol}"' in ln]
    return line


def test_inline_function_gets_alwaysinline():
    ir_text = compile_ir(
        "@inline fn add(a: int32, b: int32) -> int32 { return a + b; }\n"
        "fn main() -> int32 { return add(2, 3); }\n"
    )
    assert "alwaysinline" in define_line(ir_text, "add")


def test_plain_function_has_no_alwaysinline():
    ir_text = compile_ir(
        "fn add(a: int32, b: int32) -> int32 { return a + b; }\n"
        "fn main() -> int32 { return add(2, 3); }\n"
    )
    assert "alwaysinline" not in define_line(ir_text, "add")


def test_inline_generic_instance_gets_alwaysinline():
    ir_text = compile_ir(
        "@inline fn id<T>(x: T) -> T { return x; }\n"
        "fn main() -> int32 { return id<int32>(7); }\n"
    )
    assert "alwaysinline" in define_line(ir_text, "id<$0>($0)<int32>")


def test_inline_call_is_folded_when_optimizing():
    # At -O2 the always-inliner runs: main's body must no longer call add.
    module = CodeGen(
        parse("@inline fn add(a: int32, b: int32) -> int32 { return a + b; }\n"
              "fn main() -> int32 { return add(2, 3); }\n"),
        "test",
    ).generate()
    native, _ = build_native_module(module, opt_level=2)
    # The optimized binding module prints unquoted names (@main, not @"main").
    body = re.search(r'define[^\n]*@"?main"?\(.*?\n\}', str(native), re.S).group(0)
    assert "call" not in body


def test_inline_function_runs(capfd):
    status = run(
        "@inline fn triple(x: int32) -> int32 { return x * 3; }\n"
        "fn main() -> int32 { return triple(14); }\n"
    )
    assert status == 42


def test_imported_inline_is_inlinable_in_the_importer(tmp_path):
    # Cross-file separate compilation: an @inline function defined in an
    # imported module is copied into the importing object (like a generic or a
    # @static), so its body is present to inline at the call site. It must carry
    # both alwaysinline (to be folded) and linkonce_odr (so the copies merge
    # instead of colliding at link time).
    (tmp_path / "lib.mc").write_text(
        "@inline fn square(x: int32) -> int32 { return x * x; }\n"
    )
    (tmp_path / "main.mc").write_text(
        'import "lib";\n'
        "fn main() -> int32 { return square(7); }\n"
    )
    ir_text = str(compile_to_ir(tmp_path / "main.mc", (tmp_path,)))
    line = define_line(ir_text, "square")
    assert "alwaysinline" in line and "linkonce_odr" in line


def test_inline_extern_rejected():
    with pytest.raises(LangError, match="@inline only applies"):
        parse("@extern @inline fn f() -> int32;\n")


def test_inline_global_rejected():
    with pytest.raises(LangError, match="@inline only applies"):
        parse("@inline @static let x: int32;\n")
