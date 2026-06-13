"""@extern: declarations for functions and globals defined elsewhere."""

import pytest

from mcc.errors import LangError
from helpers import compile_ir, parse, run, run_path


def test_extern_function_call():
    # atoi comes from libc; the JIT resolves it in-process.
    source = """
    @extern
    fn atoi(s: uint8*) -> int32;
    fn main() -> int32 { return atoi("42"); }
    """
    assert run(source) == 42


def test_extern_function_emits_a_declaration():
    ir_text = compile_ir(
        '@extern\nfn atoi(s: uint8*) -> int32;\n'
        'fn main() -> int32 { return atoi("7"); }'
    )
    assert 'declare i32 @"atoi"' in ir_text


def test_extern_variable_emits_an_external_global():
    ir_text = compile_ir(
        "@extern\nlet counter: int64;\n"
        "fn main() -> int32 { counter = 5; return counter as int32; }"
    )
    assert '@"counter" = external global i64' in ir_text


def test_extern_variable_runtime():
    # optind is a real libc global; POSIX says assigning it is allowed.
    source = """
    @extern
    let optind: int32;
    fn main() -> int32 {
        optind = 7;
        return optind * 10 + 2;
    }
    """
    assert run(source) == 72


def test_identical_declarations_collapse(tmp_path):
    (tmp_path / "a.mc").write_text("@extern\nfn atoi(s: uint8*) -> int32;")
    (tmp_path / "b.mc").write_text("@extern\nfn atoi(s: uint8*) -> int32;")
    main = tmp_path / "main.mc"
    main.write_text(
        'import "a";\nimport "b";\n'
        '@extern\nfn atoi(s: uint8*) -> int32;\n'
        'fn main() -> int32 { return atoi("3"); }'
    )
    assert run_path(main) == 3


def test_conflicting_declarations_are_an_error(tmp_path):
    (tmp_path / "a.mc").write_text("@extern\nfn thing() -> int32;")
    (tmp_path / "b.mc").write_text("@extern\nfn thing() -> int64;")
    main = tmp_path / "main.mc"
    main.write_text('import "a";\nimport "b";\nfn main() -> int32 { return 0; }')
    with pytest.raises(LangError, match="conflicting extern declarations for 'thing'"):
        run_path(main)


def test_variadic_extern_function():
    # snprintf with a null buffer returns the formatted length: 5 for "4-2-7".
    source = """
    @extern
    fn snprintf(buf: uint8*, n: uint64, fmt: uint8*, ...) -> int32;
    fn main() -> int32 {
        return snprintf(null, 0, "%d-%d-%d", 4 as int32, 2 as int32, 7 as int32);
    }
    """
    assert run(source) == 5


def test_variadic_extern_emits_a_varargs_declaration():
    ir_text = compile_ir(
        "@extern\nfn printf(fmt: uint8*, ...) -> int32;\n"
        'fn main() -> int32 { return printf("hi"); }'
    )
    assert 'declare i32 @"printf"(i8* %".1", ...)' in ir_text


def test_variadic_redeclaration_of_a_header_function_collapses():
    source = """
    #include <stdio.h>
    @extern
    fn printf(fmt: uint8*, ...) -> int32;
    fn main() -> int32 { return printf("four") - 4; }
    """
    assert run(source) == 0


def test_variadic_mismatch_is_a_conflict(tmp_path):
    (tmp_path / "a.mc").write_text("@extern\nfn thing(x: int32, ...) -> int32;")
    (tmp_path / "b.mc").write_text("@extern\nfn thing(x: int32) -> int32;")
    main = tmp_path / "main.mc"
    main.write_text('import "a";\nimport "b";\nfn main() -> int32 { return 0; }')
    with pytest.raises(LangError, match="conflicting extern declarations for 'thing'"):
        run_path(main)


def test_ellipsis_outside_extern_is_an_error():
    with pytest.raises(LangError, match="only allowed in extern declarations"):
        parse("fn f(x: int32, ...) {}")


def test_ellipsis_must_be_last():
    with pytest.raises(LangError, match="must be the last parameter"):
        parse("@extern\nfn f(x: int32, ..., y: int32);")


def test_ellipsis_needs_a_named_parameter():
    with pytest.raises(LangError, match="at least one named parameter"):
        parse("@extern\nfn f(...);")


def test_redeclaring_a_header_function_collapses():
    source = """
    #include <stdlib.h>
    @extern
    fn abs(x: int32) -> int32;
    fn main() -> int32 { return abs(-9); }
    """
    assert run(source) == 9


def test_private_extern_function(tmp_path):
    (tmp_path / "lib.mc").write_text("@private\n@extern\nfn atoi(s: uint8*) -> int32;")
    main = tmp_path / "main.mc"
    main.write_text('import "lib";\nfn main() -> int32 { return atoi("1"); }')
    with pytest.raises(LangError, match="function 'atoi' is private to lib.mc"):
        run_path(main)


def test_local_shadows_extern_variable():
    source = """
    @extern
    let optind: int32;
    fn main() -> int32 {
        let optind = 5 as int32;
        return optind;
    }
    """
    assert run(source) == 5


def test_extern_function_with_a_body_is_an_error():
    with pytest.raises(LangError, match="expected ';'"):
        parse("@extern\nfn f() -> int32 { return 1; }")


def test_extern_function_cannot_be_generic():
    with pytest.raises(LangError, match="cannot be generic"):
        parse("@extern\nfn f<T>(x: T) -> T;")


def test_extern_and_static_cannot_be_combined():
    with pytest.raises(LangError, match="cannot be combined"):
        parse("@extern\n@static\nfn f() -> int32;")


def test_top_level_let_requires_extern():
    with pytest.raises(LangError, match="must be @extern"):
        parse("let x: int32;")


def test_extern_does_not_apply_to_structs():
    with pytest.raises(LangError, match="does not apply to structs"):
        parse("@extern\nstruct s { x: int32; }")
