"""The ``&T`` reference spelling: the by-hidden-reference convention for
parameters (``&T`` in the type slot) and returns (``-> &T``).

``&T`` rides the same name-set registries (``mut_params`` / ``mut_return``)
the internals still key off, and is the only surface spelling: the legacy
``mut`` / ``-> mut`` keyword forms were removed once their deprecation window
closed (Phase C of the ``&``-reference redesign), so ``mut`` is now an
ordinary identifier. See ``test_mut_params``, ``test_mut_returns`` and the
``fn``-type suites for the full behavior."""

import pytest

from mcc.codegen import CodeGen
from mcc.errors import LangError
from helpers import compile_ir, parse, run


def generate(source: str) -> CodeGen:
    cg = CodeGen(parse(source), "test")
    cg.generate()
    return cg


# --------------------------------------------------------------- parser: params


def test_amp_param_sets_mut_params():
    (func,) = parse("fn set(out: &int32) { out = 7; }").functions
    assert func.mut_params == {"out"}
    assert func.const_params == set()


def test_amp_marker_is_lifted_off_the_stored_type():
    # The reference marker rides the name-set registry, not the TypeRef, so
    # the stored parameter type is pristine (zero representation change from
    # the legacy `mut out: int32`).
    (func,) = parse("fn set(out: &int32) {}").functions
    (_, ptype) = func.params[0]
    assert ptype.mut is False


def test_amp_and_const_params_coexist():
    (func,) = parse(
        "fn f(a: int32, const b: struct s, c: &int32) {}"
    ).functions
    assert func.const_params == {"b"} and func.mut_params == {"c"}


def test_const_amp_is_the_read_only_reference_view():
    # Phase B: `const x: &T` is the read-only reference view -- read-only (in
    # const_params) yet passed by hidden reference (in constref_params), never
    # writable, so it does NOT join mut_params.
    (func,) = parse("fn f(const n: &int32) {}").functions
    assert func.const_params == {"n"}
    assert func.constref_params == {"n"}
    assert func.mut_params == set()


def test_amp_never_written_on_the_binder():
    # `&` is part of the type, never a binder annotation: `fn f(&x: T)` is a
    # syntax error (an `&` where a parameter name is expected).
    with pytest.raises(LangError):
        parse("fn f(&x: int32) {}")


# --------------------------------------------------------------- parser: returns


def test_amp_return_sets_mut_return():
    (func,) = parse("fn ref() -> &int32 { return g; }").functions
    assert func.mut_return is True


def test_amp_return_and_own_do_not_combine():
    with pytest.raises(
        LangError, match="a return cannot be both own and a reference"
    ):
        parse("fn f() -> own &int32 { return g; }")


def test_amp_return_and_const_do_not_combine():
    with pytest.raises(
        LangError, match="a return cannot be both a reference and const"
    ):
        parse("fn f() -> &const int32 { return g; }")


# ------------------------------------------------------------- code equivalence


def test_amp_param_write_reaches_caller():
    assert run(
        "fn set(out: &int32) { out = 7; }\n"
        "fn main() -> int32 { let x: int32 = 0; set(x); return x; }"
    ) == 7


def test_amp_return_is_an_lvalue():
    assert run(
        "@static let counter: int32 = 7;\n"
        "fn counter_ref() -> &int32 { return counter; }\n"
        "fn main() -> int32 { counter_ref() += 3; return counter; }"
    ) == 10


def test_amp_fn_type_spells_the_convention():
    program = parse("fn main() { let g: fn(&char) -> &int32; }")
    (let,) = program.functions[0].body
    assert let.type_name.params[0].mut and let.type_name.ret.mut
    assert str(let.type_name) == "fn(&char) -> &int32"


# ------------------------------------------------------- mut is now an identifier


def test_amp_spelling_never_warns():
    cg = generate(
        "@static let c: int32 = 0;\n"
        "fn set(out: &int32) { out = 7; }\n"
        "fn ref() -> &int32 { return c; }"
    )
    assert cg.warnings == []


def test_mut_is_no_longer_a_keyword():
    # Phase C retired the deprecated `mut` spelling and de-keyworded `mut`,
    # so it is now an ordinary identifier: usable as a local, a parameter
    # name, and a struct field.
    assert run(
        "struct box { mut: int32; }\n"
        "fn twice(mut: int32) -> int32 { return mut + mut; }\n"
        "fn main() -> int32 {\n"
        "    let mut: int32 = 21;\n"
        "    let b = box { mut = mut };\n"
        "    return twice(b.mut);\n"
        "}"
    ) == 42


def test_mut_binder_spelling_no_longer_parses():
    # The deprecated `mut out: int32` binder form now reads `mut` as the
    # parameter name, so the following `out` (with no separator) is a parse
    # error -- the spelling is gone, not silently accepted.
    with pytest.raises(LangError):
        parse("fn set(mut out: int32) { out = 7; }")


# ------------------------------------------- `&` is not a general type (ruling)

@pytest.mark.parametrize("source", [
    "fn main() { let r: &int32; }",             # a reference local
    "fn f(x: list<&int32>) {}",                 # a reference as a generic arg
    "fn f(x: int32) { let y = x as &int32; }",  # a reference cast target
    "struct s { r: &int32; }",                  # a reference struct field
])
def test_amp_outside_param_or_return_is_rejected(source):
    with pytest.raises(
        LangError,
        match="a '&' reference type is only allowed in a parameter or "
              "return type",
    ):
        compile_ir(source + "\nfn other() {}")


def test_nested_amp_is_rejected():
    # The inner parse runs without allow_ref, so `&&T` is caught as a
    # misplaced nested `&`.
    with pytest.raises(
        LangError,
        match="a '&' reference type is only allowed in a parameter or "
              "return type",
    ):
        parse("fn f(x: &&int32) {}")


# -------------------------------------------------- the blessed .mci spelling

def test_interface_emits_the_amp_spelling():
    from mcc.interface import render_interface
    source = "fn set(out: &int32) -> bool { return true; }"
    cg = CodeGen(parse(source), "test")
    cg.generate()
    out = render_interface(cg, source, [])
    assert "fn set(out: &int32) -> bool;" in out


def test_amp_interface_round_trips(tmp_path):
    from mcc.driver import emit_interface
    from helpers import run_path
    lib = tmp_path / "lib.mc"
    lib.write_text(
        "@static let counter: int32 = 7;\n"
        "fn counter_ref() -> &int32 { return counter; }\n"
        "fn counter_value() -> int32 { return counter; }\n"
    )
    out = tmp_path / "lib.mci"
    assert emit_interface(lib, (tmp_path,), None, {}, out) == 0
    assert "fn counter_ref() -> &int32;" in out.read_text()
    main = tmp_path / "main.mc"
    main.write_text(
        'import "lib";\n'
        "fn main() -> int32 { counter_ref() += 3; return counter_value(); }\n"
    )
    assert run_path(main) == 10
