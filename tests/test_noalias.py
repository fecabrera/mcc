"""``@noalias`` parameters: LLVM's noalias attribute (C's restrict)."""

import pytest

from mcc.codegen import CodeGen
from mcc.errors import LangError
from mcc.interface import render_interface
from mcc.lexer import tokenize
from mcc.parser import Parser
from helpers import compile_ir, parse, run


COPY = (
    "fn copy(@noalias dst: uint8*, @noalias src: uint8*, n: uint64) {\n"
    "    let i: uint64 = 0;\n"
    "    while (i < n) { dst[i] = src[i]; i += 1; }\n"
    "}\n"
)


# --------------------------------------------------------------------- parser


def test_noalias_param_parses():
    (func,) = parse("fn f(@noalias p: uint8*) {}").functions
    assert func.noalias_params == {"p"}


def test_noalias_with_const_parses():
    # @noalias combines with a const pointer (a const pointer is not a hidden
    # reference, so there is no aliasing guarantee to contradict).
    (func,) = parse("fn f(@noalias const p: uint8*) -> uint8 { return *p; }").functions
    assert func.noalias_params == {"p"} and func.const_params == {"p"}


def test_noalias_and_mut_rejected():
    message = "a parameter cannot be both @noalias and a reference"
    with pytest.raises(LangError, match=message):
        parse("fn f(@noalias mut p: int32) {}")


def test_noalias_on_extern_parses():
    # Unlike const/mut, @noalias is attribute-only, so it is allowed on @extern.
    (func,) = parse("@extern fn memcpy(@noalias d: byte*, @noalias s: byte*);").functions
    assert func.noalias_params == {"d", "s"}


def test_noalias_on_asm_rejected():
    message = "@noalias parameters are not allowed on @asm functions"
    with pytest.raises(LangError, match=message):
        parse('@asm fn f(@noalias p: uint8*) -> uint8 { "nop" }')


def test_noalias_at_top_level_is_unknown_annotation():
    # @noalias is only a parameter annotation; at the top level it is unknown.
    with pytest.raises(LangError, match="unknown annotation '@noalias'"):
        parse("@noalias fn f() {}")


# --------------------------------------------------------------------- codegen


def test_noalias_emits_argument_attribute():
    ir_text = compile_ir(COPY + "fn main() -> int32 { return 0; }")
    assert ir_text.count("noalias") == 2
    assert "noalias" in ir_text.split("@\"copy\"")[1].split("\n")[0]


def test_noalias_on_extern_declaration():
    ir_text = compile_ir(
        "@extern fn memcpy(@noalias d: byte*, @noalias s: byte*, n: uint64) -> byte*;\n"
        "fn main() -> int32 { return 0; }"
    )
    assert "declare" in ir_text and ir_text.count("noalias") == 2


def test_noalias_on_static_function():
    ir_text = compile_ir(
        "@static fn cp(@noalias d: uint8*, @noalias s: uint8*) { *d = *s; }\n"
        "fn main() -> int32 { return 0; }"
    )
    assert ir_text.count("noalias") == 2


def test_noalias_survives_monomorphization():
    ir_text = compile_ir(
        "fn cp<T>(@noalias a: T*, @noalias b: T*) { *a = *b; }\n"
        "fn main() -> int32 {\n"
        "    let x: int32 = 1;\n"
        "    let y: int32 = 2;\n"
        "    cp(&x, &y);\n"
        "    return 0;\n"
        "}"
    )
    assert ir_text.count("noalias") == 2


def test_noalias_non_pointer_rejected():
    with pytest.raises(LangError, match="@noalias only applies to pointer parameters"):
        compile_ir("fn f(@noalias n: int32) {}\nfn main() -> int32 { return 0; }")


def test_noalias_generic_non_pointer_instantiation_rejected():
    # The pointer check runs per instantiation, so a T* pattern is fine but a
    # bare T bound to a scalar is rejected when stamped out.
    with pytest.raises(LangError, match="@noalias only applies to pointer parameters"):
        compile_ir(
            "fn f<T>(@noalias x: T) {}\n"
            "fn main() -> int32 { let n: int32 = 1; f(n); return 0; }"
        )


# ---------------------------------------------------------------- runtime


def test_noalias_copy_runs():
    assert run(
        COPY + "fn main() -> int32 {\n"
        "    let a: uint8[3] = [1 as uint8, 2 as uint8, 3 as uint8];\n"
        "    let b: uint8[3] = [0 as uint8, 0 as uint8, 0 as uint8];\n"
        "    copy(&b[0], &a[0], 3);\n"
        "    return (b[0] + b[1] + b[2]) as int32;\n"
        "}"
    ) == 6


# --------------------------------------------------------------- interface


def _iface(source: str) -> str:
    program = Parser(tokenize(source)).parse_program()
    imports = list(program.imports)
    cg = CodeGen(program, "test")
    cg.generate()
    return render_interface(cg, source, imports)


def test_noalias_round_trips_through_interface():
    out = _iface("fn copy(@noalias dst: uint8*, @noalias src: uint8*, n: uint64) {}")
    assert "fn copy(@noalias dst: uint8*, @noalias src: uint8*, n: uint64);" in out
