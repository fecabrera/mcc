"""Forward declarations: a bodyless prototype plus its matching definition in
one program, same-file or cross-file. The prototype is checked against the
definition and discarded (the body generates into the prototype's declared
ir.Function); identical prototypes collapse; a signature mismatch is a
declaration-time error. Genuine duplicates, @extern, @removed tombstones, and
generic templates keep their duplicate-definition errors."""

import re
from pathlib import Path

import pytest

from mcc.codegen import CodeGen
from mcc.driver import compile_to_ir
from mcc.errors import LangError
from helpers import compile_ir, parse, run, run_path


def generate(source: str) -> CodeGen:
    """Compile a source string and return the CodeGen, warnings and all."""
    cg = CodeGen(parse(source), "test")
    cg.generate()
    return cg


# ------------------------------------------------------- accepted pairings

def test_proto_then_definition_same_file():
    src = """
    fn add(a: int32, b: int32) -> int32;
    fn add(a: int32, b: int32) -> int32 { return a + b; }
    fn main() -> int32 { return add(2, 3); }
    """
    assert run(src) == 5


def test_proto_after_definition_is_discarded():
    src = """
    fn add(a: int32, b: int32) -> int32 { return a + b; }
    fn add(x: int32, y: int32) -> int32;
    fn main() -> int32 { return add(20, 3); }
    """
    assert run(src) == 23


def test_identical_protos_collapse():
    src = """
    fn add(a: int32, b: int32) -> int32;
    fn add(a: int32, b: int32) -> int32;
    fn add(a: int32, b: int32) -> int32 { return a + b; }
    fn main() -> int32 { return add(4, 3); }
    """
    assert run(src) == 7


def test_parameter_names_may_differ():
    src = """
    fn scale(value: int32, by: int32) -> int32;
    fn scale(v: int32, factor: int32) -> int32 { return v * factor; }
    fn main() -> int32 { return scale(6, 7); }
    """
    assert run(src) == 42


def test_hidden_reference_conventions_pair():
    # mut/const-struct markers match positionally, so the pair keeps the mcc
    # convention: the definition's write reaches the caller's variable.
    src = """
    struct pair { x: int32; y: int32; }
    fn bump(n: &int32);
    fn total(const p: struct pair) -> int32;
    fn bump(n: &int32) { n = n + 1; }
    fn total(const p: struct pair) -> int32 { return p.x + p.y; }
    fn main() -> int32 {
        let v: int32 = 40;
        bump(v);
        let pr = pair { x = v, y = 1 };
        return total(pr);
    }
    """
    assert run(src) == 42


def test_prototype_only_program_still_declares():
    # No definition in the program: the prototype stays an LLVM declaration
    # for a body supplied by another object, exactly as before.
    out = compile_ir("fn ext(a: int32) -> int32;\nfn main() -> int32 { return ext(1); }")
    assert 'declare i32 @"ext"' in out


# ------------------------------------------------------------- mismatches

def test_mismatched_signature_is_an_error():
    with pytest.raises(LangError) as err:
        compile_ir(
            "fn f(a: int32) -> int32;\n"
            "fn f(a: int32) -> int64 { return 0; }"
        )
    assert err.value.message == "definition of 'f' does not match its prototype"
    assert [(n.message, n.line) for n in err.value.notes] == [
        ("previous declaration of 'f' is here", 1),
    ]


def test_mut_convention_mismatch_is_an_error():
    # (ret, params, variadic) agree; only the derived mut/hidden-reference
    # convention differs -- still a mismatch, the call ABIs are different.
    with pytest.raises(LangError) as err:
        compile_ir("fn f(a: int32);\nfn f(a: &int32) { a = 1; }")
    assert err.value.message == "definition of 'f' does not match its prototype"


def test_noalias_mismatch_is_an_error():
    with pytest.raises(LangError) as err:
        compile_ir(
            "fn f(@noalias p: int32*);\n"
            "fn f(p: int32*) { }"
        )
    assert err.value.message == "definition of 'f' does not match its prototype"


def test_private_mismatch_is_an_error():
    with pytest.raises(LangError) as err:
        compile_ir("@private fn f() -> int32;\nfn f() -> int32 { return 1; }")
    assert err.value.message == "definition of 'f' does not match its prototype"


def test_inline_definition_cannot_pair_with_a_proto():
    # A prototype is never @inline (the body must travel to be inlined), so
    # an @inline definition is a mismatch, not a completion.
    with pytest.raises(LangError) as err:
        compile_ir("fn f() -> int32;\n@inline fn f() -> int32 { return 1; }")
    assert err.value.message == "definition of 'f' does not match its prototype"


def test_proto_arriving_after_definition_must_match_too():
    # Same parameter list, drifting return type: pairing is per signature,
    # so same-params drift is what the mismatch error still means. (A proto
    # with a *different* parameter list joins the overload set instead --
    # see the overload-set prototype tests below.)
    with pytest.raises(LangError) as err:
        compile_ir(
            "fn f(a: int32) -> int32 { return a; }\n"
            "fn f(a: int32) -> int64;"
        )
    assert err.value.message == "definition of 'f' does not match its prototype"
    assert err.value.line == 2  # reported at the prototype, note at the definition
    assert [n.line for n in err.value.notes] == [1]


def test_conflicting_protos_are_an_error():
    # Two prototypes of one signature must agree beyond the parameter list.
    with pytest.raises(LangError) as err:
        compile_ir("fn f(a: int32) -> int32;\nfn f(a: int32) -> int64;")
    assert err.value.message == "conflicting prototypes for 'f'"


# ----------------------------------------- genuine duplicates stay errors

def test_two_definitions_still_collide():
    # Same parameter list twice is a duplicate definition -- with concrete
    # overloading the message now names the shared signature.
    with pytest.raises(
        LangError,
        match=re.escape(
            "function 'f()' already defined; overloads must differ in "
            "parameter types"
        ),
    ):
        compile_ir("fn f() -> int32 { return 1; }\nfn f() -> int32 { return 2; }")


def test_proto_never_pairs_with_extern():
    # Different ABI: @extern is the C convention, a proto the mcc convention.
    with pytest.raises(LangError, match="function 'f' already defined"):
        compile_ir("@extern fn f(a: int32) -> int32;\nfn f(a: int32) -> int32;")
    with pytest.raises(LangError, match="function 'f' already defined"):
        compile_ir("fn f(a: int32) -> int32;\n@extern fn f(a: int32) -> int32;")


def test_proto_never_pairs_with_a_tombstone():
    # @removed's one-tombstone-claims-the-name rule stays intact.
    with pytest.raises(LangError, match="function 'f' already defined"):
        compile_ir('@removed("gone") fn f() -> int32;\nfn f() -> int32;')
    with pytest.raises(LangError, match="function 'f' already defined"):
        compile_ir('fn f() -> int32;\n@removed("gone") fn f() -> int32;')


def test_proto_never_pairs_with_a_generic_template():
    # A same-module proto beside a generic template forms a MIXED overload
    # set (it does not pair -- there is no signature to check against a
    # template), so the proto keeps its own declaration. Cross-kind pairing
    # stays impossible: a cross-module template still collides.
    out = compile_ir(
        "fn f<T>(x: T) -> T { return x; }\nfn f(x: int32) -> int32;"
    )
    assert 'declare i32 @"f"' in out
    out = compile_ir(
        "fn f(x: int32) -> int32;\nfn f<T>(x: T) -> T { return x; }"
    )
    assert 'declare i32 @"f"' in out


# ------------------------------------- prototypes inside an overload set

def test_different_signature_proto_joins_the_set():
    # Pairing is per signature: a prototype with a new parameter list is
    # simply another member, declared under its mangled symbol. Both orders.
    out = compile_ir(
        "fn f(x: int32) -> int32;\n"
        "fn f(p: char*) -> int32 { return 2; }\n"
        "fn main() -> int32 { return 0; }"
    )
    assert 'declare i32 @"f(int32)"' in out
    assert 'define i32 @"f(char*)"' in out
    out = compile_ir(
        "fn f(p: char*) -> int32 { return 2; }\n"
        "fn f(x: int32) -> int32;\n"
        "fn main() -> int32 { return 0; }"
    )
    assert 'declare i32 @"f(int32)"' in out


def test_proto_and_definition_pair_inside_a_set():
    # A same-signature proto+definition inside a set is one member -- in
    # either order -- and the call dispatches to the body.
    for src in (
        "fn f(x: int32) -> int32;\n"
        "fn f(x: int32) -> int32 { return 1; }\n"
        "fn f(p: char*) -> int32 { return 2; }\n"
        "fn main() -> int32 { return f(7) * 10 + f(\"x\"); }",
        "fn f(x: int32) -> int32 { return 1; }\n"
        "fn f(x: int32) -> int32;\n"
        "fn f(p: char*) -> int32 { return 2; }\n"
        "fn main() -> int32 { return f(7) * 10 + f(\"x\"); }",
    ):
        assert run(src) == 12
        out = compile_ir(src)
        # One ir.Function per member: the pair collapsed onto the mangled
        # symbol, and the definition's body filled it in.
        assert out.count('i32 @"f(int32)"(') >= 1
        assert 'define i32 @"f(int32)"' in out


def test_set_member_pair_still_checks_return_drift():
    # Same parameter list, drifting return type: the per-signature pair
    # keeps every shipped rule, return-type-only drift included.
    with pytest.raises(LangError) as err:
        compile_ir(
            "fn f(x: int32) -> int32;\n"
            "fn f(x: int32) -> int64 { return 0; }\n"
            "fn f(p: char*) -> int32 { return 2; }\n"
            "fn main() -> int32 { return 0; }"
        )
    assert err.value.message == "definition of 'f' does not match its prototype"


def test_prototype_only_set_compiles_clean():
    # An unmatched prototype stays what it already is -- a link-time error
    # -- for set members exactly as for a single prototype.
    out = compile_ir(
        "fn f(x: int32) -> int32;\n"
        "fn f(p: char*) -> int32;\n"
        "fn main() -> int32 { return 0; }"
    )
    assert 'declare i32 @"f(int32)"' in out
    assert 'declare i32 @"f(char*)"' in out


# ------------------------------------------------------------- @deprecated

def test_definition_wins_deprecated_state():
    # The .mci stub's @deprecated warns importers; once the definition joins
    # the same program, its own (un)deprecated state is authoritative.
    src = """
    @deprecated("use new_f instead")
    fn f() -> int32;
    fn f() -> int32 { return 1; }
    fn main() -> int32 { return f(); }
    """
    assert generate(src).warnings == []


def test_definition_keeps_its_own_deprecated_message():
    src = """
    fn f() -> int32;
    @deprecated("use new_f instead")
    fn f() -> int32 { return 1; }
    fn main() -> int32 { return f(); }
    """
    assert [w.message for w in generate(src).warnings] == [
        "'f' is deprecated: use new_f instead",
    ]


# ------------------------------------------- cross-file: .mci plus source

API = "fn add(a: int32, b: int32) -> int32;\n"
IMPL = "fn add(a: int32, b: int32) -> int32 { return a + b; }\n"


def test_mci_proto_before_definition(tmp_path):
    (tmp_path / "api.mci").write_text(API)
    (tmp_path / "impl.mc").write_text(IMPL)
    main = tmp_path / "main.mc"
    main.write_text('import "api";\nimport "impl";\nfn main() -> int32 { return add(2, 3); }\n')
    assert run_path(main) == 5


def test_mci_proto_after_definition(tmp_path):
    (tmp_path / "api.mci").write_text(API)
    (tmp_path / "impl.mc").write_text(IMPL)
    main = tmp_path / "main.mc"
    main.write_text('import "impl";\nimport "api";\nfn main() -> int32 { return add(2, 3); }\n')
    assert run_path(main) == 5


def test_imported_definition_keeps_mergeable_linkage(tmp_path):
    # The proto skipped link_shared (linkonce_odr is only legal on
    # definitions); the definition that completes it is imported, so it must
    # still get linkonce_odr keyed on its own source.
    (tmp_path / "api.mci").write_text(API)
    (tmp_path / "impl.mc").write_text(IMPL)
    main = tmp_path / "main.mc"
    main.write_text('import "api";\nimport "impl";\nfn main() -> int32 { return add(2, 3); }\n')
    out = str(compile_to_ir(main))
    assert 'define linkonce_odr i32 @"add"' in out


def test_root_definition_keeps_external_linkage(tmp_path):
    # The root file's own definition completing an imported proto stays
    # external: a genuine duplicate should still be a link error.
    (tmp_path / "api.mci").write_text(API)
    main = tmp_path / "main.mc"
    main.write_text(
        'import "api";\n' + IMPL + "fn main() -> int32 { return add(2, 3); }\n"
    )
    out = str(compile_to_ir(main))
    assert 'define i32 @"add"' in out
    assert "linkonce_odr" not in out


def test_private_helper_survives_mci_proto_first(tmp_path):
    # The definition re-keys func_privacy to its own file: the module's
    # @private helper stays callable from its .mc even when the .mci stub
    # (which keeps the @private marker) registered the name first.
    (tmp_path / "api.mci").write_text(
        "@private fn helper(x: int32) -> int32;\n"
        "fn double_it(x: int32) -> int32;\n"
    )
    (tmp_path / "mod.mc").write_text(
        "@private fn helper(x: int32) -> int32 { return x + x; }\n"
        "fn double_it(x: int32) -> int32 { return helper(x); }\n"
    )
    main = tmp_path / "main.mc"
    main.write_text('import "api";\nimport "mod";\nfn main() -> int32 { return double_it(21); }\n')
    assert run_path(main) == 42


def test_paired_private_helper_stays_private(tmp_path):
    (tmp_path / "api.mci").write_text("@private fn helper(x: int32) -> int32;\n")
    (tmp_path / "mod.mc").write_text(
        "@private fn helper(x: int32) -> int32 { return x + x; }\n"
    )
    main = tmp_path / "main.mc"
    main.write_text('import "api";\nimport "mod";\nfn main() -> int32 { return helper(21); }\n')
    with pytest.raises(LangError, match="function 'helper' is private to mod.mc"):
        compile_to_ir(main)
