"""Type aliases: `type <name> = <type>;`, a transparent name for a type.

An alias is structural, not a new distinct type -- it is interchangeable with
the type it names. `type` is a contextual keyword, so it stays usable as an
ordinary identifier (a field, variable, or parameter name).
"""

import pytest

from mcc.errors import LangError
from mcc.nodes import TypeAlias
from helpers import compile_ir, parse, run, run_path


# --------------------------------------------------------------------- parser

def test_alias_parses():
    (alias,) = parse("type byte = uint8;").aliases
    assert isinstance(alias, TypeAlias) and alias.name == "byte"
    assert str(alias.target) == "uint8"


def test_function_pointer_alias_parses():
    (alias,) = parse("type cb = fn(int32, uint8**) -> int32;").aliases
    assert alias.name == "cb"
    assert str(alias.target) == "fn(int32, uint8**) -> int32"


def test_type_stays_an_identifier():
    # `type` is only a keyword as a top-level `type <name> =`; elsewhere it is a
    # plain identifier.
    prog = parse(
        "struct tagged { type: int32; }\n"
        "fn f(type: int32) -> int32 { let type = 1 as int32; return type; }"
    )
    assert not prog.aliases
    (fn,) = prog.functions
    assert fn.params[0][0] == "type"


# -------------------------------------------------------------------- codegen

def test_alias_to_builtin_is_transparent():
    assert run(
        "type word = int32;\n"
        "fn main() -> word { let n: word = 7; return n; }"
    ) == 7


def test_pointer_alias():
    assert run(
        '@extern fn strcmp(a: uint8*, b: uint8*) -> int32;\n'
        "type str = uint8*;\n"
        'fn main() -> int32 { let s: str = "hi"; return strcmp(s, "hi"); }'
    ) == 0


def test_function_pointer_alias_runs():
    assert run(
        "type binop = fn(int32, int32) -> int32;\n"
        "fn add(a: int32, b: int32) -> int32 { return a + b; }\n"
        "fn apply(f: binop, a: int32, b: int32) -> int32 { return f(a, b); }\n"
        "fn main() -> int32 { let f: binop = add; return apply(f, 30, 12); }"
    ) == 42


def test_struct_pointer_alias():
    assert run(
        "struct point { x: int32; y: int32; }\n"
        "type point_ref = struct point*;\n"
        "fn gx(p: point_ref) -> int32 { return p->x; }\n"
        "fn main() -> int32 { let p: struct point; p.x = 9; p.y = 0; return gx(&p); }"
    ) == 9


def test_alias_interchangeable_with_target():
    # A value typed by the alias and one typed by the target combine without a
    # cast -- they are the same type.
    assert run(
        "type word = int32;\n"
        "fn main() -> int32 { let a: word = 20; let b: int32 = 22; return a + b; }"
    ) == 42


def test_alias_of_alias():
    assert run(
        "type a = int32;\n"
        "type b = a;\n"
        "fn main() -> int32 { let x: b = 5; return x; }"
    ) == 5


def test_pointer_to_alias_applies_use_site_stars():
    # `bytes` is uint8*, so `bytes*` is uint8**.
    ir = compile_ir(
        "type bytes = uint8*;\n"
        "fn f(p: bytes*) -> bytes { return *p; }"
    )
    assert "i8**" in ir


def test_alias_used_as_a_struct_field():
    assert run(
        "type word = int32;\n"
        "struct box { v: word; }\n"
        "fn main() -> int32 { let b: struct box; b.v = 4; return b.v; }"
    ) == 4


# --------------------------------------------------------------------- errors

def test_cyclic_alias_is_rejected():
    with pytest.raises(LangError, match="cyclic alias"):
        compile_ir("type a = b;\ntype b = a;\nfn f() { let x: a; }")


def test_alias_clash_with_builtin_is_rejected():
    with pytest.raises(LangError, match="type 'int32' already defined"):
        compile_ir("type int32 = uint8;\nfn main() -> int32 { return 0; }")


def test_alias_clash_with_struct_is_rejected():
    with pytest.raises(LangError, match="type 'S' already defined"):
        compile_ir(
            "struct S { x: int32; }\ntype S = uint8;\nfn main() -> int32 { return 0; }"
        )


def test_alias_is_not_generic():
    with pytest.raises(LangError, match="type alias 'box' is not generic"):
        compile_ir("type box = uint8;\nfn f() { let x: box<int32>; }")


def test_unknown_target_is_rejected():
    with pytest.raises(LangError, match="unknown type 'nope'"):
        compile_ir("type a = nope;\nfn f() { let x: a; }")


# ---------------------------------------------------------- privacy / scoping

ALIAS_LIB = """
@private type Secret = uint64;
type Public = int32;

fn blessed() -> Secret { return 5; }  // same file: allowed
"""


def test_private_alias_usable_within_its_file(tmp_path):
    (tmp_path / "lib.mc").write_text(ALIAS_LIB)
    main = tmp_path / "main.mc"
    main.write_text('import "lib";\nfn main() -> int32 { return blessed() as int32; }')
    assert run_path(main) == 5


def test_private_alias_blocked_across_files(tmp_path):
    (tmp_path / "lib.mc").write_text(ALIAS_LIB)
    main = tmp_path / "main.mc"
    main.write_text('import "lib";\nfn main() -> int32 { let x: Secret = 1; return 0; }')
    with pytest.raises(LangError, match="type alias 'Secret' is private to lib.mc"):
        run_path(main)


def test_public_alias_usable_across_files(tmp_path):
    (tmp_path / "lib.mc").write_text(ALIAS_LIB)
    main = tmp_path / "main.mc"
    main.write_text('import "lib";\nfn main() -> Public { let x: Public = 7; return x; }')
    assert run_path(main) == 7


def test_static_alias_is_file_scoped(tmp_path):
    (tmp_path / "lib.mc").write_text(
        "@static type Mode = int32;\nfn lib_mode() -> Mode { return 1; }\n"
    )
    main = tmp_path / "main.mc"
    main.write_text(
        'import "lib";\n'
        "@static type Mode = int32;\n"
        "fn main() -> int32 { let m: Mode = 5; return m + lib_mode(); }"
    )
    assert run_path(main) == 6


# ------------------------------------------------------------- generic aliases
#
# A `type` declaration may carry a type-parameter list, naming a family of
# existing types (`type entry<T> = pair<char*, T>;`). The alias stays
# transparent: `entry<int32>` *is* `pair<char*, int32>`, expanded in the type
# resolver, minting no monomorphized artifact of its own.

PAIR = "struct pair<A, B> { first: A; second: B; }\n"


def test_generic_alias_parses():
    (alias,) = parse("type entry<T> = pair<char*, T>;").aliases
    assert alias.name == "entry"
    assert alias.type_params == ["T"]
    assert str(alias.target) == "pair<char*, T>"


def test_generic_struct_alias_runs():
    assert run(
        PAIR
        + "type entry<T> = pair<char*, T>;\n"
        "fn main() -> int32 { let e: entry<int32>; e.second = 42; return e.second; }"
    ) == 42


def test_generic_fn_pointer_alias_runs():
    assert run(
        "type cmp<T> = fn(T, T) -> bool;\n"
        "fn lt(a: int32, b: int32) -> bool { return a < b; }\n"
        "fn call(f: cmp<int32>, a: int32, b: int32) -> bool { return f(a, b); }\n"
        "fn main() -> int32 {\n"
        "    let f: cmp<int32> = lt;\n"
        "    if (call(f, 1, 2)) { return 42; }\n"
        "    return 0;\n"
        "}"
    ) == 42


def test_generic_alias_shares_one_instantiation():
    # `entry<int32>` and `pair<char*, int32>` are the same type: exactly one
    # LLVM struct is minted, and the two spellings interoperate without a cast.
    ir = compile_ir(
        PAIR
        + "type entry<T> = pair<char*, T>;\n"
        "fn viaentry(e: entry<int32>) -> int32 { return e.second; }\n"
        "fn viapair(p: pair<char*, int32>) -> int32 { return viaentry(p); }\n"
    )
    defs = [ln for ln in ir.splitlines() if "= type {" in ln and "pair" in ln]
    assert len(defs) == 1
    assert '%"pair<char*, int32>" = type {i8*, i32}' in ir


def test_generic_alias_as_a_field_of_another_generic():
    assert run(
        PAIR
        + "type entry<T> = pair<char*, T>;\n"
        "struct box<T> { e: entry<T>; }\n"
        "fn main() -> int32 { let b: box<int32>; b.e.second = 42; return b.e.second; }"
    ) == 42


def test_generic_alias_in_extends_slot():
    assert run(
        "struct base<T> { v: T; }\n"
        "type ent<T> = base<T>;\n"
        "struct derived<T> extends ent<T> { w: T; }\n"
        "fn main() -> int32 { let d: derived<int32>; d.v = 40; d.w = 2; return d.v + d.w; }"
    ) == 42


def test_generic_alias_composes_with_outer_parameter():
    # `entry<T>` inside a generic function's body, with `T` the outer parameter.
    assert run(
        PAIR
        + "type entry<T> = pair<char*, T>;\n"
        "fn wrap<T>(x: T) -> int32 { let e: entry<T>; e.second = x; return e.second; }\n"
        "fn main() -> int32 { return wrap<int32>(42); }"
    ) == 42


def test_alias_parameter_does_not_leak_outer_same_name():
    # The alias's own `T` binds the *argument* (`int32`), never the outer
    # generic's same-named `T` (`bool`) -- the target resolves with only the
    # alias's parameters in scope.
    assert run(
        "type ident<T> = T;\n"
        "fn outer<T>(flag: T) -> int32 { let x: ident<int32> = 41; return x + 1; }\n"
        "fn main() -> int32 { return outer<bool>(true); }"
    ) == 42


def test_unused_alias_parameter_is_inert():
    # Transparency makes a phantom parameter inert: `m<bool>` and `m<char>` are
    # the *same* type (asymmetric with structs, whose unused-parameter
    # instantiations stay nominally distinct).
    assert run(
        "type m<T> = int32;\n"
        "fn f(a: m<bool>, b: m<char>) -> int32 { return a + b; }\n"
        "fn main() -> int32 { let x: m<bool> = 20; let y: m<char> = 22; return f(x, y); }"
    ) == 42


def test_generic_alias_default_fills_bare_name():
    # A bare defaulted generic alias is a complete written type.
    assert run(
        PAIR
        + "type entry<T = int64> = pair<char*, T>;\n"
        "fn main() -> int32 { let e: entry; e.second = 42; return e.second as int32; }"
    ) == 42


def test_generic_alias_default_omitted_tail():
    assert run(
        PAIR
        + "type entry<K, V = int64> = pair<K, V>;\n"
        "fn main() -> int32 { let e: entry<char*>; e.second = 42; return e.second as int32; }"
    ) == 42


# ------------------------------------------------------- generic-alias errors

def test_bare_generic_alias_is_rejected():
    with pytest.raises(
        LangError, match="type alias 'entry' expects 1 type argument"
    ):
        compile_ir("type entry<T> = int32;\nfn f() { let x: entry; }")


def test_generic_alias_wrong_arity_is_rejected():
    with pytest.raises(
        LangError, match="type alias 'entry' expects 1 type argument.*got 2"
    ):
        compile_ir("type entry<T> = int32;\nfn f() { let x: entry<int32, bool>; }")


def test_cyclic_generic_alias_is_rejected():
    # A self-referential generic alias stays an error -- recursive types remain
    # structs' job, via the self-reference-through-a-pointer rule.
    with pytest.raises(LangError, match="cyclic alias"):
        compile_ir(
            PAIR
            + "type node<T> = pair<T, node<T>*>;\n"
            "fn f() { let x: node<int32>; }"
        )


def test_generic_alias_renders_args_in_backtrace():
    # An error inside a generic alias's target names the alias *with its
    # arguments* in the instantiation backtrace.
    with pytest.raises(LangError) as excinfo:
        compile_ir(
            "type inner<T> = nope<T>;\n"
            "type outer<T> = inner<T>;\n"
            "fn f(x: outer<int32>) { }"
        )
    notes = [n.message for n in excinfo.value.notes]
    assert "in instantiation of inner<int32>" in notes
    assert "in instantiation of outer<int32>" in notes


# ------------------------------------------------------ generic-alias imports

def test_generic_alias_round_trips_across_files(tmp_path):
    (tmp_path / "lib.mc").write_text(
        "struct pair<A, B> { first: A; second: B; }\n"
        "type entry<T> = pair<char*, T>;\n"
        "fn make(v: int32) -> entry<int32> {\n"
        '    let e: entry<int32>; e.first = "k"; e.second = v; return e;\n'
        "}"
    )
    main = tmp_path / "main.mc"
    main.write_text(
        'import "lib";\nfn main() -> int32 { let e = make(42); return e.second; }'
    )
    assert run_path(main) == 42
