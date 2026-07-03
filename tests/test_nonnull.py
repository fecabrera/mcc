"""``@nonnull`` parameters: a checked non-null refinement over ``T*``."""

import pytest

from mcc.codegen import CodeGen
from mcc.errors import LangError
from mcc.interface import render_interface
from mcc.lexer import tokenize
from mcc.parser import Parser
from helpers import compile_ir, parse, run


FIRST = (
    "fn first(@nonnull p: int32*) -> int32 {\n"
    "    return *p;\n"
    "}\n"
)


# --------------------------------------------------------------------- parser


def test_nonnull_param_parses():
    (func,) = parse("fn f(@nonnull p: uint8*) {}").functions
    assert func.nonnull_params == {"p"}


def test_nonnull_with_const_parses():
    (func,) = parse("fn f(@nonnull const p: uint8*) -> uint8 { return *p; }").functions
    assert func.nonnull_params == {"p"} and func.const_params == {"p"}


def test_nonnull_combines_with_noalias_in_either_order():
    (func,) = parse("fn f(@noalias @nonnull d: uint8*, @nonnull @noalias s: uint8*) {}").functions
    assert func.nonnull_params == {"d", "s"}
    assert func.noalias_params == {"d", "s"}


def test_nonnull_and_mut_rejected():
    message = "a parameter cannot be both @nonnull and mut"
    with pytest.raises(LangError, match=message):
        parse("fn f(@nonnull mut p: int32) {}")


def test_nonnull_on_extern_parses():
    # Like @noalias, @nonnull is attribute-only, so it is allowed on @extern.
    (func,) = parse("@extern fn strlen(@nonnull s: uint8*) -> uint64;").functions
    assert func.nonnull_params == {"s"}


def test_nonnull_on_asm_rejected():
    message = "@nonnull parameters are not allowed on @asm functions"
    with pytest.raises(LangError, match=message):
        parse('@asm fn f(@nonnull p: uint8*) -> uint8 { "nop" }')


def test_nonnull_at_top_level_is_unknown_annotation():
    # @nonnull is only a parameter annotation; at the top level it is unknown.
    with pytest.raises(LangError, match="unknown annotation '@nonnull'"):
        parse("@nonnull fn f() {}")


# --------------------------------------------------------------------- codegen


def test_nonnull_emits_argument_attributes():
    ir_text = compile_ir(
        FIRST + "fn main() -> int32 { let x: int32 = 7; return first(&x); }"
    )
    head = ir_text.split('@"first"')[1].split("\n")[0]
    assert "nonnull" in head and "dereferenceable(4)" in head


def test_nonnull_on_extern_declaration():
    ir_text = compile_ir(
        "@extern fn strlen(@nonnull s: uint8*) -> uint64;\n"
        'fn main() -> int32 { return strlen("hi") as int32; }'
    )
    assert "declare" in ir_text and "nonnull" in ir_text


def test_nonnull_on_static_function():
    ir_text = compile_ir(
        "@static fn get(@nonnull p: int32*) -> int32 { return *p; }\n"
        "fn main() -> int32 { let x: int32 = 3; return get(&x); }"
    )
    assert "nonnull dereferenceable(4)" in ir_text


def test_nonnull_survives_monomorphization():
    ir_text = compile_ir(
        "fn get<T>(@nonnull p: T*) -> T { return *p; }\n"
        "fn main() -> int32 {\n"
        "    let x: int32 = 1;\n"
        "    return get(&x);\n"
        "}"
    )
    assert "nonnull dereferenceable(4)" in ir_text


def test_nonnull_non_pointer_rejected():
    with pytest.raises(LangError, match="@nonnull only applies to pointer parameters"):
        compile_ir("fn f(@nonnull n: int32) {}\nfn main() -> int32 { return 0; }")


def test_nonnull_generic_non_pointer_instantiation_rejected():
    # The pointer check runs per instantiation, like @noalias.
    with pytest.raises(LangError, match="@nonnull only applies to pointer parameters"):
        compile_ir(
            "fn f<T>(@nonnull x: T) {}\n"
            "fn main() -> int32 { let n: int32 = 1; f(n); return 0; }"
        )


# --------------------------------------------------------- call-site checking


def test_null_literal_rejected():
    with pytest.raises(
        LangError, match=r"cannot pass null as argument 1 of 'first'"
    ):
        compile_ir(FIRST + "fn main() -> int32 { return first(null); }")


def test_plain_pointer_rejected():
    with pytest.raises(LangError, match="cannot pass a possibly-null pointer"):
        compile_ir(
            FIRST + "fn main() -> int32 {\n"
            "    let x: int32 = 1;\n"
            "    let p: int32* = &x;\n"
            "    return first(p);\n"
            "}"
        )


def test_address_of_proves_nonnull():
    assert run(
        FIRST + "fn main() -> int32 { let x: int32 = 42; return first(&x); }"
    ) == 42


def test_string_literal_proves_nonnull():
    assert run(
        "fn head(@nonnull s: uint8*) -> int32 { return *s as int32; }\n"
        'fn main() -> int32 { return head("A"); }'
    ) == 65


def test_array_decay_proves_nonnull():
    assert run(
        FIRST + "fn main() -> int32 {\n"
        "    let a: int32[2] = [7, 9];\n"
        "    return first(a);\n"
        "}"
    ) == 7


def test_nonnull_param_forwards_transitively():
    # A @nonnull callee passing its own parameter onward needs no check.
    assert run(
        FIRST + "fn outer(@nonnull p: int32*) -> int32 { return first(p); }\n"
        "fn main() -> int32 { let x: int32 = 5; return outer(&x); }"
    ) == 5


def test_plain_param_does_not_forward():
    with pytest.raises(LangError, match="cannot pass a possibly-null pointer"):
        compile_ir(
            FIRST + "fn outer(p: int32*) -> int32 { return first(p); }\n"
            "fn main() -> int32 { let x: int32 = 5; return outer(&x); }"
        )


def test_generic_call_checks_proof():
    with pytest.raises(LangError, match="cannot pass null as argument 1"):
        compile_ir(
            "fn get<T>(@nonnull p: T*) -> T { return *p; }\n"
            "fn main() -> int32 { return get<int32>(null); }"
        )


def test_null_to_plain_parameter_still_allowed():
    # The check applies only to @nonnull slots.
    assert run(
        "fn f(p: int32*) -> int32 { return (p == null) ? 1 : 0; }\n"
        "fn main() -> int32 { return f(null); }"
    ) == 1


# --------------------------------------------------------- binding soundness


def test_assignment_to_nonnull_param_rejected():
    with pytest.raises(
        LangError, match="cannot assign to @nonnull parameter 'p'"
    ):
        compile_ir(
            "fn f(@nonnull p: int32*) { p = null; }\n"
            "fn main() -> int32 { return 0; }"
        )


def test_compound_assignment_to_nonnull_param_rejected():
    with pytest.raises(
        LangError, match="cannot assign to @nonnull parameter 'p'"
    ):
        compile_ir(
            "fn f(@nonnull p: int32*) { p += 1; }\n"
            "fn main() -> int32 { return 0; }"
        )


def test_address_of_nonnull_param_rejected():
    with pytest.raises(
        LangError, match="cannot take the address of a @nonnull parameter"
    ):
        compile_ir(
            "fn f(@nonnull p: int32*) { let q: int32** = &p; }\n"
            "fn main() -> int32 { return 0; }"
        )


def test_nonnull_param_as_mut_argument_rejected():
    # A mut callee writes through a hidden reference into the caller's slot;
    # it could store null while the parameter stays "known non-null".
    with pytest.raises(
        LangError, match="cannot pass a @nonnull parameter as a mut argument"
    ):
        compile_ir(
            "fn clobber(mut q: int32*) { q = null; }\n"
            "fn f(@nonnull p: int32*) { clobber(p); }\n"
            "fn main() -> int32 { return 0; }"
        )


def test_nonnull_param_as_generic_mut_argument_rejected():
    # The mut legality checks are deferred in a generic call until after
    # overload resolution; the ban must still fire on that path.
    with pytest.raises(
        LangError, match="cannot pass a @nonnull parameter as a mut argument"
    ):
        compile_ir(
            "fn clobber<T>(mut q: T) { }\n"
            "fn f(@nonnull p: int32*) { clobber(p); }\n"
            "fn main() -> int32 { return 0; }"
        )


def test_plain_pointer_still_passes_to_mut():
    # The ban is specific to @nonnull parameters; an ordinary pointer
    # variable is still a fine mut argument.
    assert run(
        "fn clobber(mut q: int32*) { q = null; }\n"
        "fn main() -> int32 {\n"
        "    let x: int32 = 1;\n"
        "    let p: int32* = &x;\n"
        "    clobber(p);\n"
        "    return (p == null) ? 0 : 1;\n"
        "}"
    ) == 0


def test_nonnull_param_still_passes_by_value():
    # Passing the parameter's value (a non-mut slot) is untouched; only
    # lending its storage is banned.
    assert run(
        FIRST + "fn outer(@nonnull p: int32*) -> int32 { return first(p); }\n"
        "fn peek(q: int32*) -> int32 { return (q == null) ? -1 : *q; }\n"
        "fn wrap(@nonnull p: int32*) -> int32 { return peek(p); }\n"
        "fn main() -> int32 { let x: int32 = 5; return outer(&x) + wrap(&x); }"
    ) == 10


def test_shadowing_let_drops_the_fact():
    # A shadowing binding is a fresh, possibly-null variable; it must not
    # inherit the parameter's non-null proof.
    with pytest.raises(LangError, match="cannot pass a possibly-null pointer"):
        compile_ir(
            FIRST + "fn outer(@nonnull p: int32*) -> int32 {\n"
            "    let p: int32* = null;\n"
            "    return first(p);\n"
            "}\n"
            "fn main() -> int32 { return 0; }"
        )


def test_function_value_of_nonnull_function_rejected():
    with pytest.raises(
        LangError, match="cannot take a function value of 'first'"
    ):
        compile_ir(
            FIRST + "fn main() -> int32 {\n"
            "    let f: fn(int32*) -> int32 = first;\n"
            "    return 0;\n"
            "}"
        )


# ------------------------------------------------------ escape hatch (p!)


def test_postfix_assert_parses():
    from mcc.nodes import NonnullAssert, Var

    (func,) = parse("fn f(p: int32*) -> int32* { return p!; }").functions
    node = func.body[0].value
    assert isinstance(node, NonnullAssert) and isinstance(node.operand, Var)


def test_hatch_crosses_concrete_call():
    # A heap pointer carries no syntactic proof; `p!` is the programmer's
    # explicit assertion, and it is the whole proof.
    assert run(
        'import "std";\n' + FIRST + "fn main() -> int32 {\n"
        "    let p: int32* = malloc(4) as int32*;\n"
        "    *p = 42;\n"
        "    let r = first(p!);\n"
        "    free(p as uint8*);\n"
        "    return r;\n"
        "}"
    ) == 42


def test_hatch_crosses_generic_call():
    # The generic path re-runs the syntactic proof after inference; the
    # hatch must satisfy that prover too.
    assert run(
        "fn get<T>(@nonnull p: T*) -> T { return *p; }\n"
        "fn main() -> int32 {\n"
        "    let x: int32 = 7;\n"
        "    let p: int32* = &x;\n"
        "    return get(p!);\n"
        "}"
    ) == 7


def test_hatch_on_member_operand():
    assert run(
        "struct Buf { data: int32*; }\n" + FIRST + "fn peek(b: Buf*) -> int32 {\n"
        "    return first(b->data!);\n"
        "}\n"
        "fn main() -> int32 {\n"
        "    let x: int32 = 5;\n"
        "    let b = struct Buf { data = &x };\n"
        "    return peek(&b);\n"
        "}"
    ) == 5


def test_null_bang_rejected():
    with pytest.raises(LangError, match="cannot assert null as non-null"):
        compile_ir(FIRST + "fn main() -> int32 { return first(null!); }")


def test_hatch_non_pointer_rejected():
    with pytest.raises(
        LangError,
        match="postfix '!' asserts a pointer non-null, but the operand is a int32",
    ):
        compile_ir("fn main() -> int32 { let n: int32 = 1; let m = n!; return 0; }")


def test_hatch_result_is_not_an_lvalue():
    with pytest.raises(LangError, match="invalid assignment target"):
        compile_ir(
            "fn main() -> int32 {\n"
            "    let x: int32 = 1;\n"
            "    let p: int32* = &x;\n"
            "    p! = null;\n"
            "    return 0;\n"
            "}"
        )


def test_hatch_does_not_seed_facts_through_let():
    # The assertion covers the expression it wraps, not the binding it lands
    # in: `let q = p!` leaves q a plain, unproven pointer (flow-narrowing,
    # not the hatch, will change this).
    with pytest.raises(LangError, match="cannot pass a possibly-null pointer"):
        compile_ir(
            FIRST + "fn main() -> int32 {\n"
            "    let x: int32 = 1;\n"
            "    let p: int32* = &x;\n"
            "    let q = p!;\n"
            "    first(q);\n"
            "    return 0;\n"
            "}"
        )


def test_hatch_emits_no_instructions():
    # The assertion is purely static: identical IR with and without it.
    with_hatch = compile_ir(
        FIRST + "fn main() -> int32 {\n"
        "    let x: int32 = 1;\n"
        "    let p: int32* = &x;\n"
        "    return first(p!);\n"
        "}"
    )
    without = compile_ir(
        FIRST + "fn outer(@nonnull p: int32*) -> int32 { return first(p); }\n"
        "fn main() -> int32 { let x: int32 = 1; return outer(&x); }"
    )
    body = with_hatch.split('@"main"')[1]
    assert "icmp" not in body and "freeze" not in body and "select" not in body
    assert without  # both programs compile; the hatch adds no runtime check


def test_hatch_outside_nonnull_position_is_identity():
    assert run(
        "fn main() -> int32 {\n"
        "    let x: int32 = 9;\n"
        "    let p: int32* = &x;\n"
        "    return *(p!);\n"
        "}"
    ) == 9


def test_bang_equals_still_lexes_as_comparison():
    # `p != q` is one `!=` token (greedy lexing), never `p!` then `= q`.
    assert run(
        "fn main() -> int32 {\n"
        "    let x: int32 = 1;\n"
        "    let p: int32* = &x;\n"
        "    let q: int32* = null;\n"
        "    return (p != q) ? 3 : 4;\n"
        "}"
    ) == 3


def test_parenthesized_hatch_compares():
    assert run(
        "fn main() -> int32 {\n"
        "    let x: int32 = 1;\n"
        "    let p: int32* = &x;\n"
        "    let q: int32* = &x;\n"
        "    return ((p!) == q) ? 9 : 8;\n"
        "}"
    ) == 9


def test_hatch_round_trips_through_interface():
    # A generic/@inline body is emitted verbatim into the .mci; the postfix
    # assertion inside it must survive and re-parse.
    out = _iface(
        "fn grab<T>(@nonnull p: T*) -> T { return *p; }\n"
        "@inline fn head(p: int32*) -> int32 { return grab(p!); }\n"
    )
    assert "grab(p!)" in out
    Parser(tokenize(out)).parse_program()  # re-parses cleanly


# --------------------------------------------------------------- interface


def _iface(source: str) -> str:
    program = Parser(tokenize(source)).parse_program()
    imports = list(program.imports)
    cg = CodeGen(program, "test")
    cg.generate()
    return render_interface(cg, source, imports)


def test_nonnull_round_trips_through_interface():
    out = _iface("fn first(@nonnull p: int32*) -> int32 { return *p; }")
    assert "fn first(@nonnull p: int32*) -> int32;" in out


def test_nonnull_and_noalias_round_trip_together():
    out = _iface(
        "fn blit(@noalias @nonnull dst: uint8*, @noalias @nonnull src: uint8*, "
        "n: uint64) {}"
    )
    assert (
        "fn blit(@noalias @nonnull dst: uint8*, "
        "@noalias @nonnull src: uint8*, n: uint64);" in out
    )
