"""The .mci interface generator (mcc/interface.py).

An interface stub turns a compiled file's public surface into importable mcc:
concrete functions become bodyless `fn` prototypes (called with the mcc
convention, so const/mut markers are re-emitted), while types, constants, and
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
from helpers import compile_ir, run_path


def iface(source: str) -> str:
    """Render the interface stub for an import-free source string."""
    program = Parser(tokenize(source)).parse_program()
    imports = list(program.imports)
    cg = CodeGen(program, "test")
    cg.generate()
    return render_interface(cg, source, imports)


# ------------------------------------------------------------- concrete fns

def test_concrete_fn_becomes_prototype():
    out = iface("fn add(a: int32, b: int32) -> int32 { return a + b; }")
    assert "fn add(a: int32, b: int32) -> int32;" in out
    assert "@extern" not in out  # a plain proto keeps the mcc convention
    assert "return a + b" not in out  # the body does not travel


def test_void_return_omits_arrow():
    assert "fn nothing();" in iface("fn nothing() { return; }")


def test_variadic_is_preserved():
    out = iface("fn log(n: int32, ...) -> int32 { return n; }")
    assert "fn log(n: int32, ...) -> int32;" in out


def test_struct_param_keeps_pointer_type():
    out = iface(
        "struct point { x: int32; }\n"
        "fn px(p: struct point*) -> int32 { return p->x; }"
    )
    assert "fn px(p: point*) -> int32;" in out


# ----------------------------------------------------- full-source content

def test_struct_is_emitted_in_full():
    out = iface("struct point { x: int32; y: int32; }")
    assert "struct point { x: int32; y: int32; }" in out


def test_mut_param_is_re_emitted():
    # A proto keeps the mcc convention, so the hidden-reference marker
    # travels: the consumer's call site passes a pointer, like the definition.
    out = iface("fn set(mut out: int32) -> bool { out = 7; return true; }")
    assert "fn set(mut out: int32) -> bool;" in out


def test_const_struct_param_is_re_emitted():
    out = iface(
        "struct big { a: int64; b: int64; }\n"
        "fn use(const s: struct big) -> int64 { return s.a; }"
    )
    assert "fn use(const s: big) -> int64;" in out


def test_scalar_const_param_is_re_emitted():
    # No ABI change on a scalar, but the marker is part of the signature and
    # is re-emitted for fidelity (it used to be silently dropped).
    out = iface("fn scale(const k: int32) -> int32 { return k * 2; }")
    assert "fn scale(const k: int32) -> int32;" in out


def test_union_is_emitted_in_full():
    out = iface(
        "union value { i: int64; f: float64; }\n"
        "fn value_int(v: union value*) -> int64 { return v->i; }"
    )
    assert "union value { i: int64; f: float64; }" in out
    assert "fn value_int(v: value*) -> int64;" in out


def test_enum_is_emitted_in_full():
    out = iface("enum Color: int32 { Red = 0, Blue = 7 }")
    assert "enum Color: int32 { Red = 0, Blue = 7 }" in out


def test_bare_param_extends_round_trips_through_mci(tmp_path):
    # `extends T` survives the stub verbatim; re-importing re-registers the
    # template, so a consumer instantiates it against its own payload struct.
    lib = tmp_path / "lib.mc"
    lib.write_text("struct entry<T> extends T { next: entry<T>*; }\n")
    out = tmp_path / "lib.mci"
    assert emit_interface(lib, (tmp_path,), None, {}, out) == 0
    assert "struct entry<T> extends T { next: entry<T>*; }" in out.read_text()
    lib.unlink()  # force the import to resolve through the stub
    main = tmp_path / "main.mc"
    main.write_text(
        'import "lib";\n'
        "struct item { value: int32; }\n"
        "fn main() -> int32 {\n"
        "    let e: struct entry<struct item>;\n"
        "    e.value = 21; e.next = null;\n"
        "    return e.value;\n"
        "}\n"
    )
    assert run_path(main) == 21


def test_derived_enum_round_trips_through_mci(tmp_path):
    # The stub keeps the `: base` reference; re-importing it re-registers both
    # enums and recomputes the member merge, so an inherited member resolves
    # through the derived scope on the other side.
    lib = tmp_path / "lib.mc"
    lib.write_text(
        "enum x_error: int32 { OK = 0, NOT_FOUND = 4 }\n"
        "enum x_status: x_error { RETRY = 100 }\n"
    )
    out = tmp_path / "lib.mci"
    assert emit_interface(lib, (tmp_path,), None, {}, out) == 0
    assert "enum x_status: x_error { RETRY = 100 }" in out.read_text()
    lib.unlink()  # force the import to resolve through the stub
    main = tmp_path / "main.mc"
    main.write_text('import "lib";\nfn main() -> int32 { return x_status::NOT_FOUND; }')
    assert run_path(main) == 4


def test_const_is_emitted_in_full():
    assert "const VERSION = 3;" in iface("const VERSION = 3;")


def test_generic_fn_ships_full_source():
    src = "fn id<T>(x: T) -> T { return x; }"
    out = iface(src)
    assert "fn id<T>(x: T) -> T { return x; }" in out
    assert "fn id<T>(x: T) -> T;" not in out  # a generic is never a proto


def test_inline_fn_ships_full_source():
    src = "@inline\nfn dbl(n: int32) -> int32 { return n * 2; }"
    out = iface(src)
    assert "@inline" in out and "return n * 2" in out


def test_extern_decl_is_redeclared_verbatim():
    out = iface("@extern fn puts(s: uint8*) -> int32;")
    assert "@extern fn puts(s: uint8*) -> int32;" in out


def test_overloaded_fn_rejects_interface_emission():
    # A concrete overload set would render as same-name prototypes, which
    # the importer rejects; stage 2 of the overloading work lifts this.
    with pytest.raises(LangError) as err:
        iface(
            "fn f(x: int32) -> int32 { return 1; }\n"
            "fn f(p: char*) -> int32 { return 2; }"
        )
    assert err.value.message == (
        "cannot emit an interface for overloaded function 'f' "
        "(overload sets do not support interfaces yet)"
    )


# --------------------------------------------------------- dropped surface

def test_private_fn_is_dropped():
    out = iface(
        "@private\nfn secret() -> int32 { return 1; }\n"
        "fn pub() -> int32 { return 2; }"
    )
    assert "secret" not in out and "fn pub() -> int32;" in out


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


def test_reachable_private_concrete_is_a_prototype():
    src = (
        "@private\nfn helper(n: int32) -> int32 { return n; }\n"
        "@inline\nfn dbl(n: int32) -> int32 { return helper(n) * 2; }"
    )
    out = iface(src)
    assert "@private fn helper(n: int32) -> int32;" in out


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
    assert "fn make() -> secret*;" in out


def test_type_param_default_round_trips_verbatim():
    src = "fn size<T = int64>(x: T) -> T { return x; }"
    assert "fn size<T = int64>(x: T) -> T { return x; }" in iface(src)


def test_type_param_default_pulls_its_type_into_the_closure(tmp_path):
    # The default names a @private struct nothing else references: the closure
    # must ship it, and the defaulted instantiation must work on the consumer's
    # side of the .mci -- resolved against the defining file, where the
    # private type is visible.
    lib = tmp_path / "lib.mc"
    lib.write_text(
        "@private struct hidden { v: int64; }\n"
        "fn boxed<T = struct hidden>() -> int64 { return sizeof(T) as int64; }\n"
    )
    out = tmp_path / "lib.mci"
    assert emit_interface(lib, (tmp_path,), None, {}, out) == 0
    text = out.read_text()
    assert "@private struct hidden { v: int64; }" in text
    assert "fn boxed<T = struct hidden>() -> int64" in text
    lib.unlink()  # force the import to resolve through the stub
    main = tmp_path / "main.mc"
    main.write_text(
        'import "lib";\nfn main() -> int32 { return boxed() as int32; }'
    )
    assert run_path(main) == 8


# --------------------------------------------------------- ordering / misc

def test_declarations_keep_source_order():
    out = iface(
        "const A = 1;\n"
        "struct S { x: int32; }\n"
        "fn f() -> int32 { return A; }"
    )
    assert out.index("const A") < out.index("struct S") < out.index("fn f()")


# ---------------------------------------------------------- driver / files

def test_emit_interface_preserves_imports(tmp_path):
    (tmp_path / "dep.mc").write_text("fn helper() -> int32 { return 1; }")
    lib = tmp_path / "lib.mc"
    lib.write_text('import "dep";\nfn use() -> int32 { return helper(); }')
    out = tmp_path / "lib.mci"
    assert emit_interface(lib, (tmp_path,), None, {}, out) == 0
    text = out.read_text()
    assert 'import "dep";' in text
    assert "fn use() -> int32;" in text
    # The imported helper belongs to dep, not lib -- it is not re-emitted here.
    assert "helper" not in text.split("fn use")[0].replace('import "dep";', "")


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
    assert "fn host() -> int32;" in text
    assert "@if" not in text and text.count("host") == 1


# ------------------------------------------------------- .mci import resolution

def test_bare_import_resolves_to_mci_when_no_source(tmp_path):
    (tmp_path / "dep.mci").write_text("fn dep() -> int32;")
    main = tmp_path / "main.mc"
    main.write_text('import "dep";\nfn main() -> int32 { return dep(); }')
    program = load_program(main, (tmp_path,))
    assert any(f.name == "dep" and f.proto for f in program.functions)


def test_source_mc_wins_over_mci(tmp_path):
    # When both exist, the real source resolves; the stub is the fallback.
    (tmp_path / "dep.mc").write_text("fn dep() -> int32 { return 1; }")
    (tmp_path / "dep.mci").write_text("fn dep() -> int32;")
    main = tmp_path / "main.mc"
    main.write_text('import "dep";\nfn main() -> int32 { return dep(); }')
    program = load_program(main, (tmp_path,))
    (dep,) = [f for f in program.functions if f.name == "dep"]
    assert not dep.proto  # the .mc definition, not the .mci prototype


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
    assert "fn run(f: cb, x: int32) -> int32;" in out


def test_reachable_private_alias_is_included():
    out = iface(
        "@private type id = uint64;\n"
        "fn tag(x: id) -> int32 { return x as int32; }"
    )
    assert "@private type id = uint64;" in out
    assert "fn tag(x: id) -> int32;" in out


def test_unreachable_alias_is_dropped():
    out = iface(
        "@private type unused = uint8;\n"
        "fn pub() -> int32 { return 0; }"
    )
    assert "unused" not in out


# ------------------------------------------- bodyless prototypes (the form)

def test_handwritten_proto_parses():
    src = "fn set(@nonnull const p: int32*, mut out: int32) -> bool;"
    program = Parser(tokenize(src)).parse_program()
    (fn,) = program.functions
    assert fn.proto and not fn.extern and fn.body == []
    assert fn.mut_params == {"out"} and fn.const_params == {"p"}
    assert fn.nonnull_params == {"p"}


def test_proto_call_uses_hidden_references():
    # A proto is called with the mcc convention: a pointer to the caller's
    # storage at every mut and const-struct position, exactly as if the
    # definition were in this module.
    ir_text = compile_ir(
        "struct big { a: int64; b: int64; }\n"
        "fn set(mut out: int32) -> bool;\n"
        "fn peek(const s: struct big) -> int64;\n"
        "fn main() -> int32 {\n"
        "    let x: int32 = 0;\n"
        "    set(x);\n"
        "    let b = struct big { a = 1, b = 2 };\n"
        "    peek(b);\n"
        "    return x;\n"
        "}\n"
    )
    assert 'declare i1 @"set"(i32* %".1")' in ir_text
    assert 'declare i64 @"peek"(%"big"* %".1")' in ir_text
    assert 'call i1 @"set"(i32* %"x")' in ir_text
    assert 'call i64 @"peek"(%"big"* %"b")' in ir_text


def test_mut_proto_round_trips_through_mci(tmp_path):
    # Emit a stub for a library with a mut function, drop the source, and
    # compile a consumer against the stub: the imported proto must stay an
    # external declaration (no linkonce_odr -- illegal on a declaration) and
    # the call site must pass the hidden reference.
    lib = tmp_path / "lib.mc"
    lib.write_text("fn bump(mut n: int32) { n = n + 1; }")
    out = tmp_path / "lib.mci"
    assert emit_interface(lib, (tmp_path,), None, {}, out) == 0
    assert "fn bump(mut n: int32);" in out.read_text()
    lib.unlink()  # force the import to resolve through the stub
    main = tmp_path / "main.mc"
    main.write_text(
        'import "lib";\nfn main() -> int32 { let x: int32 = 41; bump(x); return x; }'
    )
    program = load_program(main, (tmp_path,))
    cg = CodeGen(program, main.name, root_source=str(main.resolve()))
    ir_text = str(cg.generate())
    assert 'declare void @"bump"(i32* %".1")' in ir_text
    assert "linkonce" not in ir_text.split("define")[0]  # the declare is external
    assert 'call void @"bump"(i32* %"x")' in ir_text


def test_generic_proto_is_rejected():
    with pytest.raises(
        LangError,
        match="a generic function cannot be a bodyless prototype "
        r"\(its body must travel to be instantiated\)",
    ):
        Parser(tokenize("fn id<T>(x: T) -> T;")).parse_program()


def test_inline_proto_is_rejected():
    with pytest.raises(
        LangError,
        match="an @inline function cannot be a bodyless prototype "
        r"\(its body must travel to be inlined\)",
    ):
        Parser(tokenize("@inline\nfn dbl(n: int32) -> int32;")).parse_program()


def test_asm_proto_is_rejected():
    with pytest.raises(
        LangError,
        match="an @asm function cannot be a bodyless prototype "
        r"\(its body is the asm template\)",
    ):
        Parser(tokenize("@asm\nfn pause();")).parse_program()


def test_static_proto_is_rejected():
    with pytest.raises(
        LangError,
        match="a @static function cannot be a bodyless prototype",
    ):
        Parser(tokenize("@static\nfn local() -> int32;")).parse_program()


def test_proto_plus_definition_pairs_as_a_forward_declaration():
    # A proto plus a matching definition is a forward declaration: the proto
    # is checked and discarded, the definition supplies the body (the full
    # matrix -- mismatches, cross-file, @extern/@removed/generic collisions --
    # lives in test_forward_decls.py).
    out = compile_ir("fn f() -> int32;\nfn f() -> int32 { return 1; }")
    assert 'define i32 @"f"' in out


def test_function_value_of_mut_proto_is_rejected():
    # The hidden-reference registration comes from the signature alone, so the
    # function-value gate applies to a proto exactly as to a local definition.
    with pytest.raises(
        LangError, match="cannot take a function value of 'set'"
    ):
        compile_ir(
            "fn set(mut out: int32);\n"
            "fn main() -> int32 { let p = set; return 0; }"
        )


# -------------------------------------------------------------- @deprecated

def test_deprecated_concrete_fn_is_re_emitted_on_the_prototype():
    out = iface(
        '@deprecated("use renamed instead")\n'
        "fn old(x: int32) -> int32 { return x + 1; }\n"
    )
    assert (
        '@deprecated("use renamed instead") fn old(x: int32) -> int32;' in out
    )


def test_deprecated_generic_fn_travels_verbatim():
    out = iface(
        '@deprecated("use bytecopy instead")\n'
        "@inline\n"
        "fn copy_bytes<T>(dst: T*, src: T*, n: uint64) { }\n"
    )
    # The verbatim source span already carries the attribute.
    assert '@deprecated("use bytecopy instead")' in out
    assert "fn copy_bytes<T>" in out


def test_deprecated_message_escapes_survive_re_emission():
    # The parser decodes escapes at parse time, so the writer must re-encode:
    # quotes and backslashes in the message round-trip unharmed.
    src = (
        '@deprecated("gone \\"soon\\": path is C:\\\\tmp")\n'
        "fn old() -> int32 { return 0; }\n"
    )
    out = iface(src)
    assert '@deprecated("gone \\"soon\\": path is C:\\\\tmp") fn old() -> int32;' in out
    # Re-parsing the stub restores the decoded message exactly.
    (fn,) = Parser(tokenize(out)).parse_program().functions
    assert fn.deprecated_msg == 'gone "soon": path is C:\\tmp'


def test_deprecated_proto_round_trips_through_mci(tmp_path):
    # Emit a stub for a library with a deprecated concrete function, drop the
    # source, and compile a consumer against the stub: the importer's call
    # site warns, so the deprecation follows the shipped interface.
    lib = tmp_path / "lib.mc"
    lib.write_text(
        '@deprecated("use renamed instead")\n'
        "fn old(x: int32) -> int32 { return x + 1; }\n"
    )
    out = tmp_path / "lib.mci"
    assert emit_interface(lib, (tmp_path,), None, {}, out) == 0
    lib.unlink()  # force the import to resolve through the stub
    main = tmp_path / "main.mc"
    main.write_text('import "lib";\nfn main() -> int32 { return old(1); }')
    program = load_program(main, (tmp_path,))
    cg = CodeGen(program, main.name, root_source=str(main.resolve()))
    cg.generate()
    assert [(w.message, w.line, w.source) for w in cg.warnings] == [
        ("'old' is deprecated: use renamed instead", 2, str(main.resolve())),
    ]


def test_deprecated_generic_round_trips_through_mci(tmp_path):
    # A generic forwarder ships as verbatim source in the stub, so the
    # attribute rides for free and the importer's call site warns.
    lib = tmp_path / "lib.mc"
    lib.write_text(
        "fn fresh<T>(v: T) -> T { return v; }\n"
        '@deprecated("use fresh instead")\n'
        "@inline\n"
        "fn stale<T>(v: T) -> T { return fresh(v); }\n"
    )
    out = tmp_path / "lib.mci"
    assert emit_interface(lib, (tmp_path,), None, {}, out) == 0
    lib.unlink()
    main = tmp_path / "main.mc"
    main.write_text(
        'import "lib";\nfn main() -> int32 { return stale(0 as int32); }'
    )
    program = load_program(main, (tmp_path,))
    cg = CodeGen(program, main.name, root_source=str(main.resolve()))
    cg.generate()
    assert [(w.message, w.line, w.source) for w in cg.warnings] == [
        ("'stale' is deprecated: use fresh instead", 2, str(main.resolve())),
    ]


# ----------------------------------------------------------------- @removed

def test_removed_concrete_tombstone_is_re_emitted_on_the_prototype():
    out = iface(
        '@removed("use renamed instead")\n'
        "fn old(x: int32) -> int32;\n"
    )
    assert '@removed("use renamed instead") fn old(x: int32) -> int32;' in out


def test_removed_generic_tombstone_travels_verbatim():
    out = iface(
        '@removed("use bytecopy instead")\n'
        "fn copy_bytes<T>(dst: T*, src: T*, n: uint64);\n"
    )
    # The verbatim source span already carries the attribute.
    assert (
        '@removed("use bytecopy instead")\n'
        "fn copy_bytes<T>(dst: T*, src: T*, n: uint64);" in out
    )


def test_removed_message_escapes_survive_re_emission():
    # Same round-trip contract as @deprecated: the writer re-encodes what the
    # parser decoded, so quotes and backslashes survive.
    src = (
        '@removed("gone \\"now\\": path is C:\\\\tmp")\n'
        "fn old() -> int32;\n"
    )
    out = iface(src)
    assert '@removed("gone \\"now\\": path is C:\\\\tmp") fn old() -> int32;' in out
    (fn,) = Parser(tokenize(out)).parse_program().functions
    assert fn.removed_msg == 'gone "now": path is C:\\tmp'


def test_removed_proto_round_trips_through_mci(tmp_path):
    # Emit a stub for a library with a concrete tombstone, drop the source,
    # and compile a consumer against the stub: the importer's call site gets
    # the removal error, not a bare unknown-function one.
    lib = tmp_path / "lib.mc"
    lib.write_text(
        '@removed("use renamed instead")\n'
        "fn old(x: int32) -> int32;\n"
        "fn renamed(x: int32) -> int32 { return x + 1; }\n"
    )
    out = tmp_path / "lib.mci"
    assert emit_interface(lib, (tmp_path,), None, {}, out) == 0
    lib.unlink()  # force the import to resolve through the stub
    main = tmp_path / "main.mc"
    main.write_text('import "lib";\nfn main() -> int32 { return old(1); }')
    program = load_program(main, (tmp_path,))
    cg = CodeGen(program, main.name, root_source=str(main.resolve()))
    with pytest.raises(LangError) as excinfo:
        cg.generate()
    err = excinfo.value
    assert str(err) == "line 2: 'old' was removed: use renamed instead"
    assert err.source == str(main.resolve())


def test_removed_generic_round_trips_through_mci(tmp_path):
    # A generic tombstone ships as verbatim source in the stub, so the
    # attribute (and the lifted bodiless-generic form) rides for free.
    lib = tmp_path / "lib.mc"
    lib.write_text(
        "fn fresh<T>(v: T) -> T { return v; }\n"
        '@removed("use fresh instead")\n'
        "fn stale<T>(v: T) -> T;\n"
    )
    out = tmp_path / "lib.mci"
    assert emit_interface(lib, (tmp_path,), None, {}, out) == 0
    lib.unlink()
    main = tmp_path / "main.mc"
    main.write_text(
        'import "lib";\nfn main() -> int32 { return stale(0 as int32); }'
    )
    program = load_program(main, (tmp_path,))
    cg = CodeGen(program, main.name, root_source=str(main.resolve()))
    with pytest.raises(LangError) as excinfo:
        cg.generate()
    err = excinfo.value
    assert str(err) == "line 2: 'stale' was removed: use fresh instead"
    assert err.source == str(main.resolve())
