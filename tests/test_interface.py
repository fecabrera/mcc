"""The .mci interface generator (mcc/interface.py).

An interface stub turns a compiled file's public surface into importable mcc:
concrete functions become @extern prototypes, while types, constants, and
generic/@inline functions are emitted in full. @private/@static declarations
are dropped, and imports are preserved.
"""

import pytest

from mcc.codegen import CodeGen
from mcc.driver import _import_candidates, emit_interface, load_program
from mcc.errors import LangError
from mcc.interface import render_interface
from mcc.lexer import tokenize
from mcc.parser import Parser


def iface(source: str) -> str:
    """Render the interface stub for an import-free source string."""
    program = Parser(tokenize(source)).parse_program()
    imports = list(program.imports)
    cg = CodeGen(program, "test")
    cg.generate()
    return render_interface(cg, source, imports)


# ------------------------------------------------------------- concrete fns

def test_concrete_fn_becomes_extern_prototype():
    out = iface("fn add(a: int32, b: int32) -> int32 { return a + b; }")
    assert "@extern fn add(a: int32, b: int32) -> int32;" in out
    assert "return a + b" not in out  # the body does not travel


def test_void_return_omits_arrow():
    assert "@extern fn nothing();" in iface("fn nothing() { return; }")


def test_variadic_is_preserved():
    out = iface("fn log(n: int32, ...) -> int32 { return n; }")
    assert "@extern fn log(n: int32, ...) -> int32;" in out


def test_struct_param_keeps_pointer_type():
    out = iface(
        "struct point { x: int32; }\n"
        "fn px(p: struct point*) -> int32 { return p->x; }"
    )
    assert "@extern fn px(p: point*) -> int32;" in out


# ----------------------------------------------------- full-source content

def test_struct_is_emitted_in_full():
    out = iface("struct point { x: int32; y: int32; }")
    assert "struct point { x: int32; y: int32; }" in out


def test_mut_param_cannot_be_exported():
    with pytest.raises(LangError, match="a mut parameter \\('out'\\) is passed"):
        iface("fn set(mut out: int32) { out = 7; }")


def test_union_is_emitted_in_full():
    out = iface(
        "union value { i: int64; f: float64; }\n"
        "fn value_int(v: union value*) -> int64 { return v->i; }"
    )
    assert "union value { i: int64; f: float64; }" in out
    assert "@extern fn value_int(v: value*) -> int64;" in out


def test_enum_is_emitted_in_full():
    out = iface("enum Color: int32 { Red = 0, Blue = 7 }")
    assert "enum Color: int32 { Red = 0, Blue = 7 }" in out


def test_const_is_emitted_in_full():
    assert "const VERSION = 3;" in iface("const VERSION = 3;")


def test_generic_fn_ships_full_source():
    src = "fn id<T>(x: T) -> T { return x; }"
    out = iface(src)
    assert "fn id<T>(x: T) -> T { return x; }" in out
    assert "@extern" not in out  # a generic is not externable


def test_inline_fn_ships_full_source():
    src = "@inline\nfn dbl(n: int32) -> int32 { return n * 2; }"
    out = iface(src)
    assert "@inline" in out and "return n * 2" in out


def test_extern_decl_is_redeclared_verbatim():
    out = iface("@extern fn puts(s: uint8*) -> int32;")
    assert "@extern fn puts(s: uint8*) -> int32;" in out


# --------------------------------------------------------- dropped surface

def test_private_fn_is_dropped():
    out = iface(
        "@private\nfn secret() -> int32 { return 1; }\n"
        "fn pub() -> int32 { return 2; }"
    )
    assert "secret" not in out and "@extern fn pub() -> int32;" in out


def test_static_fn_is_dropped():
    out = iface(
        "@static\nfn local() -> int32 { return 1; }\n"
        "fn pub() -> int32 { return 2; }"
    )
    assert "local" not in out and "pub" in out


def test_private_struct_is_dropped():
    out = iface("@private struct hidden { x: int32; }\nstruct shown { y: int32; }")
    assert "hidden" not in out and "struct shown { y: int32; }" in out


# ----------------------------------------------------------------- errors

def test_const_struct_param_is_rejected():
    src = (
        "struct big { a: int64; b: int64; }\n"
        "fn use(const s: struct big) -> int64 { return s.a; }"
    )
    with pytest.raises(LangError, match="const struct parameter"):
        iface(src)


def test_static_concrete_fn_reachable_is_rejected():
    # A public @inline body (which travels) calling a @static concrete helper:
    # the helper's symbol is file-local, so it cannot be externed.
    src = (
        "@static\nfn local() -> int32 { return 1; }\n"
        "@inline\nfn pub() -> int32 { return local(); }"
    )
    with pytest.raises(LangError, match="cannot export @static function 'local'"):
        iface(src)


# --------------------------------------------------------- reachability closure

def test_reachable_private_generic_is_included_in_full():
    # The public generic's body calls a @private generic helper, so the helper
    # must travel as source (kept @private).
    src = (
        "@private\nfn grow<T>(x: T) -> T { return x; }\n"
        "fn use<T>(x: T) -> T { return grow<T>(x); }"
    )
    out = iface(src)
    assert "@private\nfn grow<T>(x: T) -> T { return x; }" in out
    assert "fn use<T>" in out


def test_reachable_private_concrete_is_an_extern_prototype():
    src = (
        "@private\nfn helper(n: int32) -> int32 { return n; }\n"
        "@inline\nfn dbl(n: int32) -> int32 { return helper(n) * 2; }"
    )
    out = iface(src)
    assert "@private @extern fn helper(n: int32) -> int32;" in out


def test_unreachable_private_is_dropped():
    src = (
        "@private\nfn internal() -> int32 { return 1; }\n"
        "fn pub() -> int32 { return 2; }"
    )
    assert "internal" not in iface(src)


def test_private_type_in_public_signature_is_included():
    # Shipping the @private type is fine -- it stays private to the .mci, so a
    # consumer can use the public function but cannot name the type directly.
    src = (
        "@private struct secret { x: int32; }\n"
        "fn make() -> struct secret* { return null; }"
    )
    out = iface(src)
    assert "@private struct secret { x: int32; }" in out
    assert "@extern fn make() -> secret*;" in out


# --------------------------------------------------------- ordering / misc

def test_declarations_keep_source_order():
    out = iface(
        "const A = 1;\n"
        "struct S { x: int32; }\n"
        "fn f() -> int32 { return A; }"
    )
    assert out.index("const A") < out.index("struct S") < out.index("@extern fn f")


# ---------------------------------------------------------- driver / files

def test_emit_interface_preserves_imports(tmp_path):
    (tmp_path / "dep.mc").write_text("fn helper() -> int32 { return 1; }")
    lib = tmp_path / "lib.mc"
    lib.write_text('import "dep";\nfn use() -> int32 { return helper(); }')
    out = tmp_path / "lib.mci"
    assert emit_interface(lib, (tmp_path,), None, {}, out) == 0
    text = out.read_text()
    assert 'import "dep";' in text
    assert "@extern fn use() -> int32;" in text
    # The imported helper belongs to dep, not lib -- it is not re-emitted here.
    assert "helper" not in text.split("@extern fn use")[0].replace('import "dep";', "")


def test_emit_interface_resolves_at_if_for_target(tmp_path):
    lib = tmp_path / "lib.mc"
    lib.write_text(
        "@if (TARGET_OS == OS_DARWIN) {\n"
        "    fn host() -> int32 { return 1; }\n"
        "} @else {\n"
        "    fn host() -> int32 { return 2; }\n"
        "}"
    )
    out = tmp_path / "lib.mci"
    assert emit_interface(lib, (tmp_path,), "x86_64-unknown-linux-gnu", {}, out) == 0
    text = out.read_text()
    # Only the live (@else, non-Darwin) branch survives -- one prototype, no @if.
    assert "@extern fn host() -> int32;" in text
    assert "@if" not in text and text.count("host") == 1


# ------------------------------------------------------- .mci import resolution

def test_bare_import_resolves_to_mci_when_no_source(tmp_path):
    (tmp_path / "dep.mci").write_text("@extern fn dep() -> int32;")
    main = tmp_path / "main.mc"
    main.write_text('import "dep";\nfn main() -> int32 { return dep(); }')
    program = load_program(main, (tmp_path,))
    assert any(f.name == "dep" and f.extern for f in program.functions)


def test_source_mc_wins_over_mci(tmp_path):
    # When both exist, the real source resolves; the stub is the fallback.
    (tmp_path / "dep.mc").write_text("fn dep() -> int32 { return 1; }")
    (tmp_path / "dep.mci").write_text("@extern fn dep() -> int32;")
    main = tmp_path / "main.mc"
    main.write_text('import "dep";\nfn main() -> int32 { return dep(); }')
    program = load_program(main, (tmp_path,))
    (dep,) = [f for f in program.functions if f.name == "dep"]
    assert not dep.extern  # the .mc definition, not the .mci prototype


def test_import_candidate_order(tmp_path):
    cands = _import_candidates(tmp_path, "dep")
    assert [c.name for c in cands] == ["dep.mc", "dep.mci"]


def test_explicit_mci_suffix_is_kept(tmp_path):
    cands = _import_candidates(tmp_path, "dep.mci")
    assert [c.name for c in cands] == ["dep.mci"]


# ----------------------------------------------------------- type aliases

def test_type_alias_is_emitted_in_full():
    out = iface(
        "type cb = fn(int32) -> int32;\n"
        "fn run(f: cb, x: int32) -> int32 { return f(x); }"
    )
    assert "type cb = fn(int32) -> int32;" in out
    assert "@extern fn run(f: cb, x: int32) -> int32;" in out


def test_reachable_private_alias_is_included():
    out = iface(
        "@private type id = uint64;\n"
        "fn tag(x: id) -> int32 { return x as int32; }"
    )
    assert "@private type id = uint64;" in out
    assert "@extern fn tag(x: id) -> int32;" in out


def test_unreachable_alias_is_dropped():
    out = iface(
        "@private type unused = uint8;\n"
        "fn pub() -> int32 { return 0; }"
    )
    assert "unused" not in out
