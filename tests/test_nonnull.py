"""``@nonnull`` parameters: a checked non-null refinement over ``T*``."""

import pytest

from mcc.codegen import CodeGen
from mcc.driver import STDLIB_DIR, merge_imports
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


def compile_ir_posture(source: str, *, strict: bool = False) -> str:
    """Compile a string to IR under a chosen extern-nonnull posture.

    ``strict`` promotes the ``extern-nonnull`` class to error level (the
    ``-Werror=extern-nonnull`` / global-``-Werror`` posture); the default is
    relaxed. Uses ``parse`` (no import merge) -- the extern-nonnull tests
    declare their own ``@extern`` prototypes, so no libc bindings are needed.
    """
    classes = frozenset({"extern-nonnull"}) if strict else frozenset()
    return str(CodeGen(parse(source), "test", error_classes=classes).generate())


def extern_nonnull_warnings(source: str) -> list:
    """The warnings a compile collects, for inspecting the warn posture."""
    cg = CodeGen(parse(source), "test")
    cg.generate()
    return cg.warnings


def test_nonnull_on_extern_declaration_relaxed_drops_hint():
    # Relaxed (default) posture: a possibly-null argument is accepted, so the
    # LLVM nonnull/dereferenceable hint would be unsound -- it is not emitted.
    ir_text = compile_ir(
        "@extern fn strlen(@nonnull s: uint8*) -> uint64;\n"
        'fn main() -> int32 { return strlen("hi") as int32; }'
    )
    assert "declare" in ir_text and "nonnull" not in ir_text


def test_nonnull_on_extern_declaration_strict_emits_hint():
    # Strict posture restores unconditional caller proof, so the hint is sound
    # again and rides the extern declare.
    ir_text = compile_ir_posture(
        "@extern fn strlen(@nonnull s: uint8*) -> uint64;\n"
        'fn main() -> int32 { return strlen("hi") as int32; }',
        strict=True,
    )
    head = ir_text.split('declare')[1].split("\n")[0]
    assert "nonnull" in head and "dereferenceable(1)" in head


# ------------------------------------------- extern-nonnull graded postures

# A possibly-null argument: the pointer comes from a call's return value, an
# unproven source (see test_plain_pointer_rejected).
EXTERN_NN = "@extern fn ext(@nonnull p: int32*) -> int32;\n"
MAYBE_NULL = (
    "fn make() -> int32* { return null; }\n"
    "fn main() -> int32 {\n"
    "    let p: int32* = make();\n"
    "    return ext(p);\n"
    "}"
)


def test_extern_nonnull_relaxed_accepts_possibly_null():
    # Relaxed: a possibly-null argument compiles without error (the C-port
    # default). The call is emitted; only the hard error is gone.
    ir_text = compile_ir(EXTERN_NN + MAYBE_NULL)
    assert 'call i32 @"ext"' in ir_text


def test_extern_nonnull_possibly_null_collects_class_warning():
    # The warning is always collected (tagged extern-nonnull); the driver's
    # class filter is what makes it silent (relaxed) or printed (warn).
    (warning,) = extern_nonnull_warnings(EXTERN_NN + MAYBE_NULL)
    assert warning.wclass == "extern-nonnull"
    assert "possibly-null" in warning.message and "@extern" in warning.message


def test_extern_nonnull_strict_rejects_possibly_null():
    # Strict: the possibly-null case is a hard error again.
    with pytest.raises(LangError, match="cannot pass a possibly-null pointer"):
        compile_ir_posture(EXTERN_NN + MAYBE_NULL, strict=True)


def test_extern_nonnull_literal_null_always_errors():
    # The literal null is never porting noise -- it is a hard error at every
    # posture, and it collects no warning.
    program = EXTERN_NN + "fn main() -> int32 { return ext(null); }"
    for strict in (False, True):
        with pytest.raises(LangError, match="cannot pass null as argument 1"):
            compile_ir_posture(program, strict=strict)


def test_native_nonnull_possibly_null_errors_at_every_posture():
    # A native @nonnull never joins the class: its caller proof is load-bearing
    # (the body holds the parameter as non-null), so possibly-null stays a hard
    # error whatever the extern-nonnull posture.
    program = (
        FIRST + "fn make() -> int32* { return null; }\n"
        "fn main() -> int32 {\n"
        "    let p: int32* = make();\n"
        "    return first(p);\n"
        "}"
    )
    for strict in (False, True):
        with pytest.raises(LangError, match="cannot pass a possibly-null pointer"):
            compile_ir_posture(program, strict=strict)


def test_native_nonnull_possibly_null_collects_no_warning():
    # The native rejection is not a class warning -- nothing is collected.
    program = (
        FIRST + "fn make() -> int32* { return null; }\n"
        "fn main() -> int32 { return 0; }"
    )
    assert extern_nonnull_warnings(program) == []


def test_extern_nonnull_proven_argument_needs_no_posture():
    # A proven-non-null argument passes at every posture with no warning.
    program = EXTERN_NN + (
        "fn main() -> int32 { let x: int32 = 3; return ext(&x); }"
    )
    assert extern_nonnull_warnings(program) == []
    assert 'call i32 @"ext"' in compile_ir_posture(program, strict=True)


def test_extern_nonnull_round_trips_through_mci():
    # The annotation ships unconditionally in the .mci stub (the declared
    # promise never varies per build), filling the interface coverage gap.
    source = "@extern fn strlen(@nonnull s: uint8*) -> uint64;\n"
    cg = CodeGen(parse(source), "test")
    cg.generate()
    stub = render_interface(cg, source, [])
    assert "@nonnull s: uint8*" in stub


# ------------------------------------------- annotated libc binding surface

# Wave 2 (ROADMAP 1800) annotates the @extern libc bindings with @nonnull.
# These tests reach the *real* lib/libc declarations through an import merge,
# so they cover the annotation as it actually ships (not an inline stand-in)
# and confirm the graded postures fire through the real binding.


def compile_libc_strict(source: str) -> str:
    """Resolve imports against the real lib/ tree and compile at the strict
    extern-nonnull posture, so an annotated libc binding is enforced through
    its shipped declaration rather than an inline stand-in prototype."""
    program = merge_imports(parse(source), STDLIB_DIR, (STDLIB_DIR,))
    return str(
        CodeGen(program, "test", error_classes=frozenset({"extern-nonnull"})).generate()
    )


def test_libc_strlen_proven_call_compiles_strict():
    # libc/string's strlen now carries @nonnull on `str`; a proven-non-null
    # argument (a string literal) crosses cleanly at strict, and the LLVM hint
    # rides the real extern declare.
    ir_text = compile_libc_strict(
        'import "libc/string";\n'
        'fn main() -> int32 { return strlen("hi") as int32; }'
    )
    (declare,) = [
        line for line in ir_text.splitlines()
        if "declare" in line and '@"strlen"' in line
    ]
    assert "nonnull" in declare and "dereferenceable(1)" in declare


def test_libc_strlen_possibly_null_rejected_strict():
    # The same annotated binding rejects a possibly-null argument at strict,
    # naming the real function.
    with pytest.raises(
        LangError,
        match="cannot pass a possibly-null pointer as argument 1 of 'strlen'",
    ):
        compile_libc_strict(
            'import "libc/string";\n'
            "fn make() -> char* { return null; }\n"
            "fn main() -> int32 {\n"
            "    let p: char* = make();\n"
            "    return strlen(p) as int32;\n"
            "}"
        )


def test_wave1_wrapper_proven_call_into_annotated_libc_compiles_strict():
    # memory.mc's bytecopy (a wave-1 @nonnull wrapper) forwards its own
    # @nonnull dst/src into libc memcpy's freshly-@nonnull slots. The wrapper's
    # proof satisfies the annotated binding, so the whole chain compiles clean
    # at strict -- the blast-radius guarantee, exercised end to end.
    ir_text = compile_libc_strict(
        'import "std/memory";\n'
        "fn main() -> int32 {\n"
        "    let a: int32[2] = [1, 2];\n"
        "    let b: int32[2] = [0, 0];\n"
        "    bytecopy(&b[0], &a[0], 8);\n"
        "    return b[1];\n"
        "}"
    )
    assert '@"memcpy"' in ir_text


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
    # A pointer from an unproven source (here a call's return value) carries
    # no proof. (`let p = &x;` would seed a fact -- see the let-seeding tests.)
    with pytest.raises(LangError, match="cannot pass a possibly-null pointer"):
        compile_ir(
            FIRST + "fn make() -> int32* { return null; }\n"
            "fn main() -> int32 {\n"
            "    let p: int32* = make();\n"
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


# --------------------------------------------------------- flow-narrowing


def test_then_branch_narrows():
    # `if (p != null)` proves p non-null inside the then branch.
    assert run(
        FIRST + "fn main() -> int32 {\n"
        "    let x: int32 = 8;\n"
        "    let p: int32* = &x;\n"
        "    if (p != null) { return first(p); }\n"
        "    return 0;\n"
        "}"
    ) == 8


def test_flipped_null_operand_narrows():
    # The guard matches either operand order.
    assert run(
        FIRST + "fn main() -> int32 {\n"
        "    let x: int32 = 3;\n"
        "    let p: int32* = &x;\n"
        "    if (null != p) { return first(p); }\n"
        "    return 0;\n"
        "}"
    ) == 3


def test_early_return_guard_narrows_remainder():
    # The C-idiomatic early guard: a diverging `if (p == null)` then-body
    # with no else proves p for the remainder of the enclosing scope.
    assert run(
        FIRST + "fn get(p: int32*) -> int32 {\n"
        "    if (p == null) { return 0; }\n"
        "    return first(p);\n"
        "}\n"
        "fn main() -> int32 { let x: int32 = 7; return get(&x) + get(null); }"
    ) == 7


def test_break_guard_narrows_loop_remainder():
    # Divergence is any terminator, so `break` counts inside a loop body.
    assert run(
        FIRST + "fn main() -> int32 {\n"
        "    let x: int32 = 5;\n"
        "    let p: int32* = &x;\n"
        "    let total: int32 = 0;\n"
        "    let i: int32 = 0;\n"
        "    while (i < 3) {\n"
        "        if (p == null) { break; }\n"
        "        total = total + first(p);\n"
        "        i = i + 1;\n"
        "    }\n"
        "    return total;\n"
        "}"
    ) == 15


def test_continue_guard_narrows_loop_remainder():
    assert run(
        FIRST + "fn main() -> int32 {\n"
        "    let x: int32 = 2;\n"
        "    let p: int32* = &x;\n"
        "    let total: int32 = 0;\n"
        "    for i in range(0, 3) {\n"
        "        if (p == null) { continue; }\n"
        "        total = total + first(p);\n"
        "    }\n"
        "    return total;\n"
        "}"
    ) == 6


def test_nested_all_diverging_guard_narrows_remainder():
    # Divergence is "the block ended terminated", not a return-statement
    # scan, so a then-body whose own if/else both return counts.
    assert run(
        FIRST + "fn get(p: int32*, flag: bool) -> int32 {\n"
        "    if (p == null) {\n"
        "        if (flag) { return -1; } else { return -2; }\n"
        "    }\n"
        "    return first(p);\n"
        "}\n"
        "fn main() -> int32 { let x: int32 = 4; return get(&x, true); }"
    ) == 4


def test_else_branch_narrows_on_equality_guard():
    # The flipped guard: `if (p == null) {A} else {B}` proves p in B.
    assert run(
        FIRST + "fn get(p: int32*) -> int32 {\n"
        "    let r: int32 = 0;\n"
        "    if (p == null) { r = -1; } else { r = first(p); }\n"
        "    return r;\n"
        "}\n"
        "fn main() -> int32 { let x: int32 = 6; return get(&x); }"
    ) == 6


def test_then_branch_narrows_with_else_present():
    assert run(
        FIRST + "fn get(p: int32*) -> int32 {\n"
        "    let r: int32 = 0;\n"
        "    if (p != null) { r = first(p); } else { r = -1; }\n"
        "    return r;\n"
        "}\n"
        "fn main() -> int32 { let x: int32 = 9; return get(&x); }"
    ) == 9


def test_narrowed_pointer_crosses_generic_call():
    # The generic path re-runs the syntactic proof after inference; the
    # narrowed fact must satisfy that prover too.
    assert run(
        "fn get<T>(@nonnull p: T*) -> T { return *p; }\n"
        "fn main() -> int32 {\n"
        "    let x: int32 = 9;\n"
        "    let p: int32* = &x;\n"
        "    if (p != null) { return get(p); }\n"
        "    return 0;\n"
        "}"
    ) == 9


def test_redundant_guard_on_nonnull_param_is_harmless():
    # Guarding an already-@nonnull parameter narrows nothing new; its
    # permanent fact must survive the guard's exit.
    assert run(
        FIRST + "fn outer(@nonnull p: int32*) -> int32 {\n"
        "    if (p == null) { return -1; }\n"
        "    return first(p);\n"
        "}\n"
        "fn main() -> int32 { let x: int32 = 3; return outer(&x); }"
    ) == 3


def test_non_diverging_equality_guard_does_not_narrow_remainder():
    # The remainder is only proven when the then-body cannot fall through.
    with pytest.raises(LangError, match="cannot pass a possibly-null pointer"):
        compile_ir(
            FIRST + "fn get(p: int32*) -> int32 {\n"
            "    if (p == null) { }\n"
            "    return first(p);\n"
            "}\n"
            "fn main() -> int32 { return 0; }"
        )


def test_equality_guard_does_not_narrow_then_branch():
    # Inside `if (p == null)` the pointer is known *null*, never non-null.
    with pytest.raises(LangError, match="cannot pass a possibly-null pointer"):
        compile_ir(
            FIRST + "fn get(p: int32*) -> int32 {\n"
            "    if (p == null) { return first(p); }\n"
            "    return 0;\n"
            "}\n"
            "fn main() -> int32 { return 0; }"
        )


def test_global_pointer_does_not_narrow():
    # Any call between the guard and the use could store null into a global.
    with pytest.raises(LangError, match="cannot pass a possibly-null pointer"):
        compile_ir(
            "@static let g: int32*;\n" + FIRST + "fn main() -> int32 {\n"
            "    if (g != null) { return first(g); }\n"
            "    return 0;\n"
            "}"
        )


def test_mut_param_does_not_narrow():
    # A callee taking two mut references can alias a mut parameter, so a
    # call could null it without naming it at this site.
    with pytest.raises(LangError, match="cannot pass a possibly-null pointer"):
        compile_ir(
            FIRST + "fn f(mut p: int32*) -> int32 {\n"
            "    if (p != null) { return first(p); }\n"
            "    return 0;\n"
            "}\n"
            "fn main() -> int32 { return 0; }"
        )


def test_address_taken_before_guard_blocks_narrowing():
    with pytest.raises(LangError, match="cannot pass a possibly-null pointer"):
        compile_ir(
            FIRST + "fn snoop(q: int32**) { }\n"
            "fn main() -> int32 {\n"
            "    let x: int32 = 1;\n"
            "    let p: int32* = &x;\n"
            "    snoop(&p);\n"
            "    if (p != null) { return first(p); }\n"
            "    return 0;\n"
            "}"
        )


def test_address_taken_after_guard_blocks_narrowing():
    # The &p ban is whole-function: a pointer stored later (or reached on a
    # loop back edge) could null p without ever naming it.
    with pytest.raises(LangError, match="cannot pass a possibly-null pointer"):
        compile_ir(
            FIRST + "fn main() -> int32 {\n"
            "    let x: int32 = 1;\n"
            "    let p: int32* = &x;\n"
            "    if (p != null) { return first(p); }\n"
            "    let q: int32** = &p;\n"
            "    return 0;\n"
            "}"
        )


def test_reassignment_invalidates_narrowing():
    with pytest.raises(LangError, match="cannot pass a possibly-null pointer"):
        compile_ir(
            FIRST + "fn main() -> int32 {\n"
            "    let x: int32 = 1;\n"
            "    let p: int32* = &x;\n"
            "    if (p != null) {\n"
            "        p = null;\n"
            "        return first(p);\n"
            "    }\n"
            "    return 0;\n"
            "}"
        )


def test_mut_argument_invalidates_narrowing():
    # Lending p's storage to a mut slot lets the callee store null through
    # the reference; the fact dies at the call.
    with pytest.raises(LangError, match="cannot pass a possibly-null pointer"):
        compile_ir(
            "fn clobber(mut q: int32*) { q = null; }\n"
            + FIRST + "fn main() -> int32 {\n"
            "    let x: int32 = 1;\n"
            "    let p: int32* = &x;\n"
            "    if (p != null) {\n"
            "        clobber(p);\n"
            "        return first(p);\n"
            "    }\n"
            "    return 0;\n"
            "}"
        )


def test_shadowing_let_invalidates_narrowing():
    # A shadowing binding is a fresh, possibly-null variable.
    with pytest.raises(LangError, match="cannot pass a possibly-null pointer"):
        compile_ir(
            FIRST + "fn main() -> int32 {\n"
            "    let x: int32 = 1;\n"
            "    let p: int32* = &x;\n"
            "    if (p != null) {\n"
            "        let p: int32* = null;\n"
            "        return first(p);\n"
            "    }\n"
            "    return 0;\n"
            "}"
        )


def test_invalidation_in_nested_block_persists_outward():
    # Block exit restores facts by intersection: names narrowed inside the
    # block vanish, but an invalidation from inside must stick.
    with pytest.raises(LangError, match="cannot pass a possibly-null pointer"):
        compile_ir(
            FIRST + "fn main() -> int32 {\n"
            "    let x: int32 = 1;\n"
            "    let p: int32* = &x;\n"
            "    if (p != null) {\n"
            "        { p = null; }\n"
            "        return first(p);\n"
            "    }\n"
            "    return 0;\n"
            "}"
        )


def test_guard_then_while_keeps_uninvalidated_fact():
    # The loop-entry pre-scan keeps facts the loop cannot invalidate: the
    # guard-then-loop idiom needs no in-body guard or hatch.
    assert run(
        FIRST + "fn get(p: int32*) -> int32 {\n"
        "    if (p == null) { return 0; }\n"
        "    let total: int32 = 0;\n"
        "    let i: int32 = 0;\n"
        "    while (i < 2) {\n"
        "        total = total + first(p);\n"
        "        i = i + 1;\n"
        "    }\n"
        "    return total;\n"
        "}\n"
        "fn main() -> int32 { let x: int32 = 3; return get(&x); }"
    ) == 6


def test_guard_then_until_keeps_uninvalidated_fact():
    assert run(
        FIRST + "fn get(p: int32*) -> int32 {\n"
        "    if (p == null) { return 0; }\n"
        "    let total: int32 = 0;\n"
        "    let i: int32 = 0;\n"
        "    until (i == 2) {\n"
        "        total = total + first(p);\n"
        "        i = i + 1;\n"
        "    }\n"
        "    return total;\n"
        "}\n"
        "fn main() -> int32 { let x: int32 = 4; return get(&x); }"
    ) == 8


def test_guard_then_for_range_keeps_uninvalidated_fact():
    assert run(
        FIRST + "fn get(p: int32*) -> int32 {\n"
        "    if (p == null) { return 0; }\n"
        "    let total: int32 = 0;\n"
        "    for i in range(0, 2) { total = total + first(p); }\n"
        "    return total;\n"
        "}\n"
        "fn main() -> int32 { let x: int32 = 5; return get(&x); }"
    ) == 10


def test_fact_survives_past_loop_exit():
    # A fact the loop cannot invalidate holds after the loop too.
    assert run(
        FIRST + "fn get(p: int32*) -> int32 {\n"
        "    if (p == null) { return 0; }\n"
        "    let i: int32 = 0;\n"
        "    while (i < 2) { i = i + 1; }\n"
        "    return first(p);\n"
        "}\n"
        "fn main() -> int32 { let x: int32 = 7; return get(&x); }"
    ) == 7


def test_loop_body_assignment_kills_fact_at_entry():
    # The body reassigns p, so the back edge can carry null into the *next*
    # iteration: the fact dies at loop entry, before even the first use.
    with pytest.raises(LangError, match="cannot pass a possibly-null pointer"):
        compile_ir(
            FIRST + "fn get(p: int32*) -> int32 {\n"
            "    if (p == null) { return 0; }\n"
            "    let i: int32 = 0;\n"
            "    while (i < 2) {\n"
            "        first(p);\n"
            "        p = null;\n"
            "        i = i + 1;\n"
            "    }\n"
            "    return 0;\n"
            "}\n"
            "fn main() -> int32 { return 0; }"
        )


def test_for_body_assignment_kills_fact_at_entry():
    with pytest.raises(LangError, match="cannot pass a possibly-null pointer"):
        compile_ir(
            FIRST + "fn get(p: int32*) -> int32 {\n"
            "    if (p == null) { return 0; }\n"
            "    for i in range(0, 2) { first(p); p = null; }\n"
            "    return 0;\n"
            "}\n"
            "fn main() -> int32 { return 0; }"
        )


def test_loop_body_mut_lend_kills_fact_at_entry():
    with pytest.raises(LangError, match="cannot pass a possibly-null pointer"):
        compile_ir(
            "fn clobber(mut q: int32*) { q = null; }\n"
            + FIRST + "fn get(p: int32*) -> int32 {\n"
            "    if (p == null) { return 0; }\n"
            "    let i: int32 = 0;\n"
            "    while (i < 2) {\n"
            "        first(p);\n"
            "        clobber(p);\n"
            "        i = i + 1;\n"
            "    }\n"
            "    return 0;\n"
            "}\n"
            "fn main() -> int32 { return 0; }"
        )


def test_loop_body_generic_mut_lend_kills_fact_at_entry():
    # The pre-scan resolves mut positions by name across every template
    # overload -- before overload resolution, over-approximating is safe.
    with pytest.raises(LangError, match="cannot pass a possibly-null pointer"):
        compile_ir(
            "fn clobber<T>(mut q: T) { }\n"
            + FIRST + "fn get(p: int32*) -> int32 {\n"
            "    if (p == null) { return 0; }\n"
            "    let i: int32 = 0;\n"
            "    while (i < 2) { first(p); clobber(p); i = i + 1; }\n"
            "    return 0;\n"
            "}\n"
            "fn main() -> int32 { return 0; }"
        )


def test_loop_body_static_mut_lend_kills_fact_at_entry():
    # @static functions resolve by (file, name); their mut positions must
    # feed the pre-scan too.
    with pytest.raises(LangError, match="cannot pass a possibly-null pointer"):
        compile_ir(
            "@static fn clobber(mut q: int32*) { q = null; }\n"
            + FIRST + "fn get(p: int32*) -> int32 {\n"
            "    if (p == null) { return 0; }\n"
            "    let i: int32 = 0;\n"
            "    while (i < 2) { first(p); clobber(p); i = i + 1; }\n"
            "    return 0;\n"
            "}\n"
            "fn main() -> int32 { return 0; }"
        )


def test_loop_condition_mut_lend_kills_fact_at_entry():
    # The condition re-runs on the back edge too; a mut lend there kills.
    with pytest.raises(LangError, match="cannot pass a possibly-null pointer"):
        compile_ir(
            "fn advance(mut q: int32*) -> bool { q = null; return false; }\n"
            + FIRST + "fn get(p: int32*) -> int32 {\n"
            "    if (p == null) { return 0; }\n"
            "    while (advance(p)) { first(p); }\n"
            "    return 0;\n"
            "}\n"
            "fn main() -> int32 { return 0; }"
        )


def test_loop_body_pointer_compound_assign_keeps_fact_at_entry():
    # `p += n` is pointer arithmetic: it cannot null a non-null p (the same
    # axiom that proves `p + n`), so the guard's fact survives the pre-scan
    # and the body's @nonnull call compiles.
    compile_ir(
        FIRST + "fn get(p: int32*) -> int32 {\n"
        "    if (p == null) { return 0; }\n"
        "    let i: int32 = 0;\n"
        "    while (i < 2) { first(p); p += 1; i = i + 1; }\n"
        "    return 0;\n"
        "}\n"
        "fn main() -> int32 { return 0; }"
    )


def test_loop_body_shadowing_let_kills_fact_at_entry():
    # A shadowing `let p` anywhere in the body kills conservatively, even
    # for uses lexically before it (the shadow's scope is a nested block).
    with pytest.raises(LangError, match="cannot pass a possibly-null pointer"):
        compile_ir(
            FIRST + "fn get(p: int32*) -> int32 {\n"
            "    if (p == null) { return 0; }\n"
            "    let i: int32 = 0;\n"
            "    while (i < 2) {\n"
            "        first(p);\n"
            "        { let p: int32* = null; }\n"
            "        i = i + 1;\n"
            "    }\n"
            "    return 0;\n"
            "}\n"
            "fn main() -> int32 { return 0; }"
        )


def test_nested_if_assignment_in_loop_kills_fact_at_entry():
    # The pre-scan is a whole-subtree walk: an assignment buried in a nested
    # branch still kills, whichever path runs.
    with pytest.raises(LangError, match="cannot pass a possibly-null pointer"):
        compile_ir(
            FIRST + "fn get(p: int32*, flag: bool) -> int32 {\n"
            "    if (p == null) { return 0; }\n"
            "    let i: int32 = 0;\n"
            "    while (i < 2) {\n"
            "        first(p);\n"
            "        if (flag) { p = null; }\n"
            "        i = i + 1;\n"
            "    }\n"
            "    return 0;\n"
            "}\n"
            "fn main() -> int32 { return 0; }"
        )


# ---------------------------------------------- and/or condition threading


def test_and_guard_narrows_both_operands():
    # `if (p != null and q != null)`: both conjuncts held in the then branch.
    assert run(
        FIRST + "fn both(p: int32*, q: int32*) -> int32 {\n"
        "    if (p != null and q != null) { return first(p) + first(q); }\n"
        "    return -1;\n"
        "}\n"
        "fn main() -> int32 {\n"
        "    let x: int32 = 2;\n"
        "    let y: int32 = 3;\n"
        "    return both(&x, &y) + both(&x, null);\n"
        "}"
    ) == 4


def test_or_guard_narrows_diverging_remainder():
    # `if (p == null or q == null) { return; }`: past the guard both
    # disjuncts failed, so both pointers are non-null.
    assert run(
        FIRST + "fn both(p: int32*, q: int32*) -> int32 {\n"
        "    if (p == null or q == null) { return -1; }\n"
        "    return first(p) + first(q);\n"
        "}\n"
        "fn main() -> int32 {\n"
        "    let x: int32 = 4;\n"
        "    let y: int32 = 5;\n"
        "    return both(&x, &y) + both(null, &y);\n"
        "}"
    ) == 8


def test_or_guard_does_not_narrow_then_branch():
    # A true `or` pins down neither operand: `p != null or q != null`
    # proves nothing inside the then branch.
    with pytest.raises(LangError, match="cannot pass a possibly-null pointer"):
        compile_ir(
            FIRST + "fn get(p: int32*, q: int32*) -> int32 {\n"
            "    if (p != null or q != null) { return first(p); }\n"
            "    return 0;\n"
            "}\n"
            "fn main() -> int32 { return 0; }"
        )


def test_and_guard_does_not_narrow_else_remainder():
    # A false `and` pins down neither operand either.
    with pytest.raises(LangError, match="cannot pass a possibly-null pointer"):
        compile_ir(
            FIRST + "fn get(p: int32*, q: int32*) -> int32 {\n"
            "    if (p == null and q == null) { return 0; }\n"
            "    return first(p);\n"
            "}\n"
            "fn main() -> int32 { return 0; }"
        )


def test_and_rhs_sees_lhs_fact():
    # Short-circuiting: the rhs only runs when the lhs held, so the lhs's
    # fact is live while the rhs evaluates.
    assert run(
        FIRST + "fn get(p: int32*) -> int32 {\n"
        "    if (p != null and first(p) == 6) { return 1; }\n"
        "    return 0;\n"
        "}\n"
        "fn main() -> int32 { let x: int32 = 6; return get(&x); }"
    ) == 1


def test_or_rhs_sees_lhs_fact():
    # For `or` the rhs runs when the lhs was false: `p == null or use(p)`.
    assert run(
        FIRST + "fn get(p: int32*) -> int32 {\n"
        "    if (p == null or first(p) == 0) { return -1; }\n"
        "    return first(p);\n"
        "}\n"
        "fn main() -> int32 { let x: int32 = 9; return get(&x); }"
    ) == 9


# ------------------------------------------------- loop header narrowing


def test_while_header_narrows_body():
    # `while (p != null)` proves p at the top of every iteration; a mid-body
    # reassignment is fine because the header re-proves on the back edge.
    assert run(
        FIRST + "fn main() -> int32 {\n"
        "    let x: int32 = 5;\n"
        "    let p: int32* = &x;\n"
        "    let total: int32 = 0;\n"
        "    while (p != null) {\n"
        "        total = total + first(p);\n"
        "        p = null;\n"
        "    }\n"
        "    return total;\n"
        "}"
    ) == 5


def test_until_header_narrows_body():
    # `until (p == null)` runs the body while p != null: same proof.
    assert run(
        FIRST + "fn main() -> int32 {\n"
        "    let x: int32 = 8;\n"
        "    let p: int32* = &x;\n"
        "    let total: int32 = 0;\n"
        "    until (p == null) {\n"
        "        total = total + first(p);\n"
        "        p = null;\n"
        "    }\n"
        "    return total;\n"
        "}"
    ) == 8


def test_while_header_fact_dies_after_reassignment():
    # Within one iteration the usual invalidation still applies: after
    # `p = ...` the header's fact is gone for the rest of the body.
    with pytest.raises(LangError, match="cannot pass a possibly-null pointer"):
        compile_ir(
            FIRST + "fn main() -> int32 {\n"
            "    let x: int32 = 1;\n"
            "    let p: int32* = &x;\n"
            "    while (p != null) {\n"
            "        p = null;\n"
            "        first(p);\n"
            "    }\n"
            "    return 0;\n"
            "}"
        )


def test_while_header_does_not_prove_after_loop():
    # The `while (p != null)` exit edge implies p == null -- never a proof.
    with pytest.raises(LangError, match="cannot pass a possibly-null pointer"):
        compile_ir(
            FIRST + "fn main() -> int32 {\n"
            "    let x: int32 = 1;\n"
            "    let p: int32* = &x;\n"
            "    while (p != null) { p = null; }\n"
            "    return first(p);\n"
            "}"
        )


def test_while_exit_condition_narrows_after_loop():
    # The flipped header: `while (p == null)` can only exit with p non-null,
    # regardless of what the body did (the exit edge leaves the condition).
    assert run(
        FIRST + "fn main() -> int32 {\n"
        "    let x: int32 = 6;\n"
        "    let p: int32* = null;\n"
        "    while (p == null) { p = &x; }\n"
        "    return first(p);\n"
        "}"
    ) == 6


def test_until_exit_condition_narrows_after_loop():
    assert run(
        FIRST + "fn main() -> int32 {\n"
        "    let x: int32 = 7;\n"
        "    let p: int32* = null;\n"
        "    until (p != null) { p = &x; }\n"
        "    return first(p);\n"
        "}"
    ) == 7


def test_break_blocks_post_exit_narrowing():
    # A `break` jumps to the loop's end without re-testing the condition,
    # so the exit-edge fact is unsound and must not be added.
    with pytest.raises(LangError, match="cannot pass a possibly-null pointer"):
        compile_ir(
            FIRST + "fn get(flag: bool) -> int32 {\n"
            "    let x: int32 = 1;\n"
            "    let p: int32* = null;\n"
            "    while (p == null) {\n"
            "        if (flag) { break; }\n"
            "        p = &x;\n"
            "    }\n"
            "    return first(p);\n"
            "}\n"
            "fn main() -> int32 { return 0; }"
        )


def test_nested_loop_break_does_not_block_post_exit():
    # A break inside a nested loop targets the inner loop; the outer loop
    # still always exits through its own condition.
    assert run(
        FIRST + "fn main() -> int32 {\n"
        "    let x: int32 = 4;\n"
        "    let p: int32* = null;\n"
        "    while (p == null) {\n"
        "        p = &x;\n"
        "        while (true) { break; }\n"
        "    }\n"
        "    return first(p);\n"
        "}"
    ) == 4


# ----------------------------------------------- fact-seeding through let


def test_let_seeds_fact_from_address_of():
    # `let p = &x` binds a provably non-null value: p starts narrowed.
    assert run(
        FIRST + "fn main() -> int32 {\n"
        "    let x: int32 = 42;\n"
        "    let p: int32* = &x;\n"
        "    return first(p);\n"
        "}"
    ) == 42


def test_let_carries_narrowed_fact():
    # `let q = p;` under a guard: p's fact seeds q.
    assert run(
        FIRST + "fn get(p: int32*) -> int32 {\n"
        "    if (p == null) { return 0; }\n"
        "    let q = p;\n"
        "    return first(q);\n"
        "}\n"
        "fn main() -> int32 { let x: int32 = 3; return get(&x); }"
    ) == 3


def test_let_from_unproven_pointer_does_not_seed():
    with pytest.raises(LangError, match="cannot pass a possibly-null pointer"):
        compile_ir(
            FIRST + "fn get(p: int32*) -> int32 {\n"
            "    let q = p;\n"
            "    return first(q);\n"
            "}\n"
            "fn main() -> int32 { return 0; }"
        )


def test_let_seeded_fact_dies_on_reassignment():
    # A seeded fact is an ordinary narrowed fact: the usual invalidations
    # (reassignment, mut lend, loops that kill) all apply.
    with pytest.raises(LangError, match="cannot pass a possibly-null pointer"):
        compile_ir(
            FIRST + "fn main() -> int32 {\n"
            "    let x: int32 = 1;\n"
            "    let q: int32* = &x;\n"
            "    q = null;\n"
            "    return first(q);\n"
            "}"
        )


def test_addr_taken_let_does_not_seed():
    # The whole-function &q ban applies to seeded facts too.
    with pytest.raises(LangError, match="cannot pass a possibly-null pointer"):
        compile_ir(
            FIRST + "fn snoop(r: int32**) { }\n"
            "fn main() -> int32 {\n"
            "    let x: int32 = 1;\n"
            "    let q: int32* = &x;\n"
            "    snoop(&q);\n"
            "    return first(q);\n"
            "}"
        )


# ------------------------------------------------- proof through as casts


def test_cast_to_pointer_preserves_proof():
    # `"A" as uint8*` stays in pointer land: the literal's proof threads
    # through (previously the cast stripped it).
    assert run(
        "fn head(@nonnull s: uint8*) -> int32 { return *s as int32; }\n"
        'fn main() -> int32 { return head("A" as uint8*); }'
    ) == 65


def test_cast_to_alias_pointer_preserves_proof():
    # The target is resolved, not read off the syntax: an alias whose
    # underlying type is a pointer counts.
    assert run(
        "type cstr = uint8*;\n"
        "fn head(@nonnull s: uint8*) -> int32 { return *s as int32; }\n"
        'fn main() -> int32 { return head("B" as cstr); }'
    ) == 66


def test_cast_of_narrowed_pointer_preserves_proof():
    assert run(
        "fn head(@nonnull s: uint8*) -> int32 { return *s as int32; }\n"
        "fn get(p: uint8*) -> int32 {\n"
        "    if (p == null) { return 0; }\n"
        "    return head(p as uint8*);\n"
        "}\n"
        'fn main() -> int32 { let b: uint8 = 9; return get(&b); }'
    ) == 9


def test_cast_through_integer_severs_proof():
    # A non-pointer intermediate severs the chain: an address that visited
    # integer land carries no proof, even if it started from one.
    with pytest.raises(LangError, match="cannot pass a possibly-null pointer"):
        compile_ir(
            FIRST + "fn main() -> int32 {\n"
            "    let x: int32 = 1;\n"
            "    return first((&x as uint64) as int32*);\n"
            "}"
        )


def test_cast_of_unproven_pointer_does_not_prove():
    with pytest.raises(LangError, match="cannot pass a possibly-null pointer"):
        compile_ir(
            FIRST + "fn get(p: int32*) -> int32 { return first(p as int32*); }\n"
            "fn main() -> int32 { return 0; }"
        )


def test_narrowing_emits_no_instructions():
    # Narrowing is purely static: the guarded call compiles to the exact
    # same IR as the same program asserting with the escape hatch.
    def program(arg: str) -> str:
        return (
            FIRST + "fn main() -> int32 {\n"
            "    let x: int32 = 1;\n"
            "    let p: int32* = &x;\n"
            "    if (p != null) { return first(" + arg + "); }\n"
            "    return 0;\n"
            "}"
        )

    assert compile_ir(program("p")) == compile_ir(program("p!"))


# ------------------------------------------- projection (path) narrowing

BUF = "struct Buf { data: int32*; }\n"


def test_projection_then_branch_narrows():
    # `if (b->data != null)` proves the projection inside the then branch.
    assert run(
        BUF + FIRST + "fn peek(b: Buf*) -> int32 {\n"
        "    if (b != null and b->data != null) { return first(b->data); }\n"
        "    return -1;\n"
        "}\n"
        "fn main() -> int32 {\n"
        "    let x: int32 = 9;\n"
        "    let b = struct Buf { data = &x };\n"
        "    return peek(&b) + peek(null) + 1;\n"
        "}"
    ) == 9


def test_projection_dot_base_narrows():
    # A struct-value base narrows through `.` exactly like `->`.
    assert run(
        BUF + FIRST + "fn main() -> int32 {\n"
        "    let x: int32 = 4;\n"
        "    let b = struct Buf { data = &x };\n"
        "    if (b.data != null) { return first(b.data); }\n"
        "    return 0;\n"
        "}"
    ) == 4


def test_projection_early_return_guard_narrows_remainder():
    assert run(
        BUF + FIRST + "fn peek(@nonnull b: Buf*) -> int32 {\n"
        "    if (b->data == null) { return 0; }\n"
        "    return first(b->data);\n"
        "}\n"
        "fn main() -> int32 {\n"
        "    let x: int32 = 6;\n"
        "    let b = struct Buf { data = &x };\n"
        "    return peek(&b);\n"
        "}"
    ) == 6


def test_projection_else_branch_narrows():
    assert run(
        BUF + FIRST + "fn main() -> int32 {\n"
        "    let x: int32 = 3;\n"
        "    let b = struct Buf { data = &x };\n"
        "    if (b.data == null) { return 0; } else { return first(b.data); }\n"
        "}"
    ) == 3


def test_projection_while_header_narrows_body():
    # The header re-proves per back edge, so the fact holds at the top of
    # every iteration even though the body's store blanket-kills it.
    assert run(
        BUF + FIRST + "fn main() -> int32 {\n"
        "    let x: int32 = 2;\n"
        "    let b = struct Buf { data = &x };\n"
        "    let total: int32 = 0;\n"
        "    while (b.data != null) {\n"
        "        total = total + first(b.data);\n"
        "        if (total >= 6) { b.data = null; }\n"
        "    }\n"
        "    return total;\n"
        "}"
    ) == 6


def test_projection_while_exit_condition_narrows_after_loop():
    # The normal exit leaves `b.data == null` false, so the projection is
    # proven after the loop, whatever the body did.
    assert run(
        BUF + FIRST + "fn main() -> int32 {\n"
        "    let x: int32 = 5;\n"
        "    let b = struct Buf { data = null };\n"
        "    while (b.data == null) { b.data = &x; }\n"
        "    return first(b.data);\n"
        "}"
    ) == 5


def test_projection_or_guard_threads_with_names():
    # A diverging or-guard proves a name and a projection together.
    assert run(
        BUF + FIRST + "fn peek(b: Buf*) -> int32 {\n"
        "    if (b == null or b->data == null) { return -1; }\n"
        "    return first(b->data);\n"
        "}\n"
        "fn main() -> int32 {\n"
        "    let x: int32 = 7;\n"
        "    let b = struct Buf { data = &x };\n"
        "    return peek(&b) + peek(null) + 1;\n"
        "}"
    ) == 7


def test_projection_short_circuit_rhs_sees_fact():
    # In `b.data != null and use(b.data)` the rhs runs only when the lhs
    # held, so the projection is proven while it evaluates.
    assert run(
        BUF + FIRST + "fn main() -> int32 {\n"
        "    let x: int32 = 8;\n"
        "    let b = struct Buf { data = &x };\n"
        "    if (b.data != null and first(b.data) == 8) { return 8; }\n"
        "    return 0;\n"
        "}"
    ) == 8


def test_projection_mut_param_base_narrows():
    # A mut parameter is an ineligible *name* fact, but a fine path base:
    # the blanket call/store kills cover the aliasing that bans the name.
    assert run(
        BUF + FIRST + "fn peek(mut b: Buf) -> int32 {\n"
        "    if (b.data == null) { return 0; }\n"
        "    return first(b.data);\n"
        "}\n"
        "fn main() -> int32 {\n"
        "    let x: int32 = 5;\n"
        "    let b = struct Buf { data = &x };\n"
        "    return peek(b);\n"
        "}"
    ) == 5


def test_multi_level_projection_narrows():
    assert run(
        BUF + FIRST + "struct Outer { inner: Buf*; }\n"
        "fn main() -> int32 {\n"
        "    let x: int32 = 3;\n"
        "    let b = struct Buf { data = &x };\n"
        "    let o = struct Outer { inner = &b };\n"
        "    let p: Outer* = &o;\n"
        "    if (p->inner->data != null) { return first(p->inner->data); }\n"
        "    return 0;\n"
        "}"
    ) == 3


def test_paren_deref_projection_canonicalizes():
    # `(*b).data` and `b->data` are the same lvalue and share one path key,
    # in both directions.
    assert run(
        BUF + FIRST + "fn peek(@nonnull b: Buf*) -> int32 {\n"
        "    if ((*b).data == null) { return 0; }\n"
        "    return first(b->data);\n"
        "}\n"
        "fn peek2(@nonnull b: Buf*) -> int32 {\n"
        "    if (b->data == null) { return 0; }\n"
        "    return first((*b).data);\n"
        "}\n"
        "fn main() -> int32 {\n"
        "    let x: int32 = 2;\n"
        "    let b = struct Buf { data = &x };\n"
        "    return peek(&b) + peek2(&b);\n"
        "}"
    ) == 4


def test_extends_inherited_projection_narrows():
    # The spliced base field projects off the extending struct.
    assert run(
        BUF + FIRST + "struct Ext extends Buf { n: int32; }\n"
        "fn main() -> int32 {\n"
        "    let x: int32 = 6;\n"
        "    let e = struct Ext { data = &x, n = 1 };\n"
        "    let p: Ext* = &e;\n"
        "    if (p->data != null) { return first(p->data); }\n"
        "    return 0;\n"
        "}"
    ) == 6


def test_volatile_owner_projection_never_forms():
    # A @volatile owner's field can change between the check and the use
    # (that is the point of @volatile), so no fact ever forms.
    with pytest.raises(LangError, match="cannot pass a possibly-null pointer"):
        compile_ir(
            "@volatile struct Reg { data: int32*; }\n" + FIRST
            + "fn peek(r: Reg*) -> int32 {\n"
            "    if (r != null and r->data != null) { return first(r->data); }\n"
            "    return 0;\n"
            "}\n"
            "fn main() -> int32 { return 0; }"
        )


def test_volatile_via_extends_projection_never_forms():
    # @volatile is inherited through extends, and so is the exclusion.
    with pytest.raises(LangError, match="cannot pass a possibly-null pointer"):
        compile_ir(
            "@volatile struct Reg { data: int32*; }\n"
            "struct Ext extends Reg { n: int32; }\n" + FIRST
            + "fn peek(@nonnull e: Ext*) -> int32 {\n"
            "    if (e->data != null) { return first(e->data); }\n"
            "    return 0;\n"
            "}\n"
            "fn main() -> int32 { return 0; }"
        )


def test_global_base_projection_does_not_narrow():
    # Paths root at locals only: any call could rewrite a global's field
    # without the blanket kills ever seeing a site here.
    with pytest.raises(LangError, match="cannot pass a possibly-null pointer"):
        compile_ir(
            BUF + "@static let g: Buf;\n" + FIRST + "fn main() -> int32 {\n"
            "    if (g.data != null) { return first(g.data); }\n"
            "    return 0;\n"
            "}"
        )


def test_index_projection_does_not_narrow():
    # Array elements carry no path fact (excluded from v1).
    with pytest.raises(LangError, match="cannot pass a possibly-null pointer"):
        compile_ir(
            BUF + FIRST + "fn main() -> int32 {\n"
            "    let bs: Buf[2];\n"
            "    if (bs[0].data != null) { return first(bs[0].data); }\n"
            "    return 0;\n"
            "}"
        )


# ---------------------------------------------- projection fact invalidation


def test_projection_fact_dies_on_base_reassignment():
    # Reassigning the base retargets every path rooted at it.
    with pytest.raises(LangError, match="cannot pass a possibly-null pointer"):
        compile_ir(
            BUF + FIRST + "fn peek(b: Buf*, c: Buf*) -> int32 {\n"
            "    if (b != null and b->data != null) {\n"
            "        b = c;\n"
            "        return first(b->data);\n"
            "    }\n"
            "    return 0;\n"
            "}\n"
            "fn main() -> int32 { return 0; }"
        )


def test_projection_fact_dies_on_shadowing_let():
    with pytest.raises(LangError, match="cannot pass a possibly-null pointer"):
        compile_ir(
            BUF + FIRST + "fn peek(b: Buf*) -> int32 {\n"
            "    if (b == null or b->data == null) { return 0; }\n"
            "    { let b: Buf* = null; }\n"
            "    return first(b->data);\n"
            "}\n"
            "fn main() -> int32 { return 0; }"
        )


def test_scalar_compound_assign_keeps_projection_fact():
    # A compound assignment to a bare scalar local writes its own slot
    # only: it prefix-kills paths rooted at *that* name (none here), and
    # the projection fact survives.
    assert run(
        BUF + FIRST + "fn main() -> int32 {\n"
        "    let x: int32 = 4;\n"
        "    let b = struct Buf { data = &x };\n"
        "    let n: int32 = 1;\n"
        "    if (b.data == null) { return 0; }\n"
        "    n += 2;\n"
        "    return first(b.data) + n;\n"
        "}"
    ) == 7


def test_projection_fact_dies_on_store_through_pointer():
    # Any through-memory store may alias the guarded field: blanket kill.
    with pytest.raises(LangError, match="cannot pass a possibly-null pointer"):
        compile_ir(
            BUF + FIRST + "fn poke(b: Buf*, q: int32**) -> int32 {\n"
            "    if (b == null or b->data == null) { return 0; }\n"
            "    *q = null;\n"
            "    return first(b->data);\n"
            "}\n"
            "fn main() -> int32 { return 0; }"
        )


def test_projection_fact_dies_on_element_store():
    with pytest.raises(LangError, match="cannot pass a possibly-null pointer"):
        compile_ir(
            BUF + FIRST + "fn poke(b: Buf*, qs: int32*[2]) -> int32 {\n"
            "    if (b == null or b->data == null) { return 0; }\n"
            "    qs[0] = null;\n"
            "    return first(b->data);\n"
            "}\n"
            "fn main() -> int32 { return 0; }"
        )


def test_projection_fact_dies_on_member_store():
    # A store to *any* field kills every path fact -- another base may
    # alias the guarded one.
    with pytest.raises(LangError, match="cannot pass a possibly-null pointer"):
        compile_ir(
            BUF + FIRST + "fn poke(b: Buf*, c: Buf*) -> int32 {\n"
            "    if (b == null or c == null or b->data == null) { return 0; }\n"
            "    c->data = null;\n"
            "    return first(b->data);\n"
            "}\n"
            "fn main() -> int32 { return 0; }"
        )


def test_projection_fact_dies_on_compound_member_store():
    with pytest.raises(LangError, match="cannot pass a possibly-null pointer"):
        compile_ir(
            BUF + FIRST + "struct Pair { data: int32*; n: int32; }\n"
            "fn poke(b: Buf*, p: Pair*) -> int32 {\n"
            "    if (b == null or p == null or b->data == null) { return 0; }\n"
            "    p->n += 1;\n"
            "    return first(b->data);\n"
            "}\n"
            "fn main() -> int32 { return 0; }"
        )


def test_projection_fact_dies_at_writing_call():
    # A writing callee can reach the field through any escaped or global
    # pointer, so its call still kills. (A callee the write-effect analysis
    # proves write-free preserves instead: see the write-effect battery
    # below.)
    with pytest.raises(LangError, match="cannot pass a possibly-null pointer"):
        compile_ir(
            BUF + FIRST + "@static let g: int32 = 0;\n"
            "fn bump() { g = 1; }\n"
            "fn peek(b: Buf*) -> int32 {\n"
            "    if (b == null or b->data == null) { return 0; }\n"
            "    bump();\n"
            "    return first(b->data);\n"
            "}\n"
            "fn main() -> int32 { return 0; }"
        )


def test_projection_before_call_argument_accepted():
    # Argument checks interleave with lowering left to right: the
    # projection's load happens under its guard, before g() runs.
    assert run(
        BUF + "fn two(@nonnull p: int32*, n: int32) -> int32 { return *p + n; }\n"
        "fn g() -> int32 { return 1; }\n"
        "fn main() -> int32 {\n"
        "    let x: int32 = 4;\n"
        "    let b = struct Buf { data = &x };\n"
        "    if (b.data == null) { return 0; }\n"
        "    return two(b.data, g());\n"
        "}"
    ) == 5


def test_projection_after_call_argument_rejected():
    # In the other order g() runs first and may null the field before the
    # projection is loaded. (g writes a global so its call kills; a
    # write-free g would preserve the fact in either order.)
    with pytest.raises(LangError, match="cannot pass a possibly-null pointer"):
        compile_ir(
            BUF + "fn two(n: int32, @nonnull p: int32*) -> int32 { return *p + n; }\n"
            "@static let counter: int32 = 0;\n"
            "fn g() -> int32 { counter = counter + 1; return 1; }\n"
            "fn main() -> int32 {\n"
            "    let x: int32 = 4;\n"
            "    let b = struct Buf { data = &x };\n"
            "    if (b.data == null) { return 0; }\n"
            "    return two(g(), b.data);\n"
            "}"
        )


def test_projection_before_call_argument_accepted_generic():
    # The generic path pre-evaluates arguments, so the proof is recorded
    # per argument at evaluation time, matching the direct path.
    assert run(
        BUF + "fn two<T>(@nonnull p: T*, n: int32) -> T { return *p + n; }\n"
        "fn g() -> int32 { return 1; }\n"
        "fn main() -> int32 {\n"
        "    let x: int32 = 4;\n"
        "    let b = struct Buf { data = &x };\n"
        "    if (b.data == null) { return 0; }\n"
        "    return two(b.data, g());\n"
        "}"
    ) == 5


def test_projection_after_call_argument_rejected_generic():
    with pytest.raises(LangError, match="cannot pass a possibly-null pointer"):
        compile_ir(
            BUF + "fn two<T>(n: int32, @nonnull p: T*) -> T { return *p + n; }\n"
            "@static let counter: int32 = 0;\n"
            "fn g() -> int32 { counter = counter + 1; return 1; }\n"
            "fn main() -> int32 {\n"
            "    let x: int32 = 4;\n"
            "    let b = struct Buf { data = &x };\n"
            "    if (b.data == null) { return 0; }\n"
            "    return two(g(), b.data);\n"
            "}"
        )


def test_address_of_projection_alone_is_harmless():
    # &b->data needs no formation ban: only an actual aliasing write (a
    # store or a call, both blanket kills) can null the field.
    assert run(
        BUF + FIRST + "fn main() -> int32 {\n"
        "    let x: int32 = 7;\n"
        "    let b = struct Buf { data = &x };\n"
        "    let alias: int32** = &b.data;\n"
        "    if (b.data != null) { return first(b.data); }\n"
        "    return 0;\n"
        "}"
    ) == 7


def test_aliasing_store_after_address_of_projection_kills():
    with pytest.raises(LangError, match="cannot pass a possibly-null pointer"):
        compile_ir(
            BUF + FIRST + "fn main() -> int32 {\n"
            "    let x: int32 = 7;\n"
            "    let b = struct Buf { data = &x };\n"
            "    let alias: int32** = &b.data;\n"
            "    if (b.data == null) { return 0; }\n"
            "    *alias = null;\n"
            "    return first(b.data);\n"
            "}"
        )


def test_union_cross_member_store_kills():
    # Union members share storage; the member-store blanket kill covers a
    # write through a sibling.
    with pytest.raises(LangError, match="cannot pass a possibly-null pointer"):
        compile_ir(
            "union U { data: int32*; raw: uint64; }\n" + FIRST
            + "fn peek(u: U*) -> int32 {\n"
            "    if (u == null or u->data == null) { return 0; }\n"
            "    u->raw = 0;\n"
            "    return first(u->data);\n"
            "}\n"
            "fn main() -> int32 { return 0; }"
        )


def test_projection_fact_drops_at_loop_entry():
    # No pre-scan for paths yet: every projection fact drops wholesale at
    # loop entry (guard inside the body, or hatch with `!`).
    with pytest.raises(LangError, match="cannot pass a possibly-null pointer"):
        compile_ir(
            BUF + FIRST + "fn peek(b: Buf*) -> int32 {\n"
            "    if (b == null or b->data == null) { return 0; }\n"
            "    let total: int32 = 0;\n"
            "    let i: int32 = 0;\n"
            "    while (i < 3) {\n"
            "        total = total + first(b->data);\n"
            "        i = i + 1;\n"
            "    }\n"
            "    return total;\n"
            "}\n"
            "fn main() -> int32 { return 0; }"
        )


def test_projection_fact_drops_at_for_entry():
    with pytest.raises(LangError, match="cannot pass a possibly-null pointer"):
        compile_ir(
            BUF + FIRST + "fn peek(b: Buf*) -> int32 {\n"
            "    if (b == null or b->data == null) { return 0; }\n"
            "    let total: int32 = 0;\n"
            "    for i in range(0, 3) { total = total + first(b->data); }\n"
            "    return total;\n"
            "}\n"
            "fn main() -> int32 { return 0; }"
        )


def test_call_in_guard_tail_drops_path_fact():
    # The rhs of the `and` runs *after* the projection's null test and may
    # null the field before the branch, so the path fact must not form.
    # (A name fact has no such window: no call can reach an eligible local.)
    with pytest.raises(LangError, match="cannot pass a possibly-null pointer"):
        compile_ir(
            BUF + FIRST + "fn check() -> bool { return true; }\n"
            "fn peek(@nonnull b: Buf*) -> int32 {\n"
            "    if (b->data != null and check()) { return first(b->data); }\n"
            "    return 0;\n"
            "}\n"
            "fn main() -> int32 { return 0; }"
        )


def test_call_in_guard_tail_keeps_name_fact():
    # The asymmetry pinned: the same shape still proves a bare local.
    assert run(
        FIRST + "fn check() -> bool { return true; }\n"
        "fn main() -> int32 {\n"
        "    let x: int32 = 3;\n"
        "    let p: int32* = &x;\n"
        "    if (p != null and check()) { return first(p); }\n"
        "    return 0;\n"
        "}"
    ) == 3


def test_assign_to_aliased_struct_kills_path_facts():
    # `s = ...` writes storage `p` points at (&s exists): the whole-struct
    # assignment rewrites the guarded field through the alias.
    with pytest.raises(LangError, match="cannot pass a possibly-null pointer"):
        compile_ir(
            BUF + FIRST + "fn main() -> int32 {\n"
            "    let x: int32 = 1;\n"
            "    let s = struct Buf { data = &x };\n"
            "    let p: Buf* = &s;\n"
            "    if (p->data != null) {\n"
            "        s = struct Buf { data = null };\n"
            "        return first(p->data);\n"
            "    }\n"
            "    return 0;\n"
            "}"
        )


# ------------------------------------------------- call write-effect analysis
#
# The blanket call kill is refined by a per-function, transitive write-effect
# bit: a call to a callee the compiler proves transitively write-free (no
# through-memory stores, no mut-parameter or global writes, nothing opaque,
# all callees likewise) preserves projection facts; everything else keeps the
# kill. Name facts always survived calls and are unaffected throughout.


def test_pure_leaf_call_preserves_projection_fact():
    # The motivating pair: the fact survives a call to a pure math leaf and
    # then feeds a second @nonnull call -- no rebinding, no hatch.
    assert run(
        BUF + FIRST + "fn leaf(n: int32) -> int32 { return n * 2; }\n"
        "fn main() -> int32 {\n"
        "    let x: int32 = 21;\n"
        "    let b = struct Buf { data = &x };\n"
        "    if (b.data == null) { return 0; }\n"
        "    let n = leaf(1);\n"
        "    return first(b.data) + n;\n"
        "}"
    ) == 23


def test_pure_generic_leaf_call_preserves_projection_fact():
    # A generic instance takes its template's (per-template) bit.
    assert run(
        BUF + FIRST + "fn same<T>(x: T) -> T { return x; }\n"
        "fn main() -> int32 {\n"
        "    let x: int32 = 5;\n"
        "    let b = struct Buf { data = &x };\n"
        "    if (b.data == null) { return 0; }\n"
        "    let n = same(2);\n"
        "    return first(b.data) + n;\n"
        "}"
    ) == 7


def test_println_call_kills_projection_fact():
    # println wraps @extern printf: opaque, transitively -- the roadmap's
    # canonical const-laundering case.
    with pytest.raises(LangError, match="cannot pass a possibly-null pointer"):
        run(
            'import "std/io";\n'
            + BUF + FIRST + "fn peek(b: Buf*) -> int32 {\n"
            "    if (b == null or b->data == null) { return 0; }\n"
            '    println("checking");\n'
            "    return first(b->data);\n"
            "}\n"
            "fn main() -> int32 { return 0; }"
        )


def test_transitive_taint_through_clean_intermediary():
    # middle() performs no write of its own, but calls a global-writing
    # leaf: the bit propagates bottom-up through the call graph.
    with pytest.raises(LangError, match="cannot pass a possibly-null pointer"):
        compile_ir(
            BUF + FIRST + "@static let g: int32 = 0;\n"
            "fn deep() { g = 1; }\n"
            "fn middle() -> int32 { deep(); return 0; }\n"
            "fn peek(b: Buf*) -> int32 {\n"
            "    if (b == null or b->data == null) { return 0; }\n"
            "    middle();\n"
            "    return first(b->data);\n"
            "}\n"
            "fn main() -> int32 { return 0; }"
        )


def test_write_free_recursion_cycle_preserves():
    # The fixpoint is optimistic-clear: a mutually recursive, write-free
    # cycle settles clear rather than defaulting to tainted.
    assert run(
        BUF + FIRST + "fn even(n: int32) -> int32 {\n"
        "    if (n == 0) { return 1; }\n"
        "    return odd(n - 1);\n"
        "}\n"
        "fn odd(n: int32) -> int32 {\n"
        "    if (n == 0) { return 0; }\n"
        "    return even(n - 1);\n"
        "}\n"
        "fn main() -> int32 {\n"
        "    let x: int32 = 4;\n"
        "    let b = struct Buf { data = &x };\n"
        "    if (b.data == null) { return 0; }\n"
        "    let e = even(6);\n"
        "    return first(b.data) + e;\n"
        "}"
    ) == 5


def test_tainted_recursion_cycle_kills():
    # A base condition anywhere in the cycle taints the whole cycle.
    with pytest.raises(LangError, match="cannot pass a possibly-null pointer"):
        compile_ir(
            BUF + FIRST + "@static let g: int32 = 0;\n"
            "fn ping(n: int32) { if (n > 0) { pong(n - 1); } }\n"
            "fn pong(n: int32) { g = 1; if (n > 0) { ping(n - 1); } }\n"
            "fn peek(b: Buf*) -> int32 {\n"
            "    if (b == null or b->data == null) { return 0; }\n"
            "    ping(2);\n"
            "    return first(b->data);\n"
            "}\n"
            "fn main() -> int32 { return 0; }"
        )


def test_mut_param_assignment_taints():
    # `n = 0` on a mut parameter stores through the hidden reference into
    # the caller's storage: a swap-shaped helper is never write-free.
    with pytest.raises(LangError, match="cannot pass a possibly-null pointer"):
        compile_ir(
            BUF + FIRST + "fn reset(mut n: int32) { n = 0; }\n"
            "fn peek(b: Buf*) -> int32 {\n"
            "    let k: int32 = 1;\n"
            "    if (b == null or b->data == null) { return 0; }\n"
            "    reset(k);\n"
            "    return first(b->data);\n"
            "}\n"
            "fn main() -> int32 { return 0; }"
        )


def test_store_inside_defer_taints():
    # A defer body is an ordinary child of the function's AST: the store
    # through q counts even though it runs at scope exit.
    with pytest.raises(LangError, match="cannot pass a possibly-null pointer"):
        compile_ir(
            BUF + FIRST + "fn sneaky(q: int32*) { defer { *q = 1; } }\n"
            "fn peek(b: Buf*, q: int32*) -> int32 {\n"
            "    if (b == null or b->data == null) { return 0; }\n"
            "    sneaky(q);\n"
            "    return first(b->data);\n"
            "}\n"
            "fn main() -> int32 { return 0; }"
        )


def test_candidate_union_taints_caller_of_mixed_set():
    # The analysis is syntactic, so a call edge unions every same-name
    # candidate: wrap() would resolve h(1) to the write-free template, but
    # the tainted concrete overload sharing the name taints wrap anyway.
    with pytest.raises(LangError, match="cannot pass a possibly-null pointer"):
        compile_ir(
            BUF + FIRST + "@static let g: int32 = 0;\n"
            "fn h<T>(x: T) -> T { return x; }\n"
            "fn h(x: int32, y: int32) -> int32 { g = 1; return x; }\n"
            "fn wrap() -> int32 { return h(1); }\n"
            "fn peek(b: Buf*) -> int32 {\n"
            "    if (b == null or b->data == null) { return 0; }\n"
            "    wrap();\n"
            "    return first(b->data);\n"
            "}\n"
            "fn main() -> int32 { return 0; }"
        )


def test_resolved_call_site_uses_winning_candidate_bit():
    # At an emission site the winner is in hand, so the same mixed set is
    # judged precisely: h(1) resolves to the write-free template and the
    # fact survives (contrast the union-tainted wrap() above).
    assert run(
        BUF + FIRST + "@static let g: int32 = 0;\n"
        "fn h<T>(x: T) -> T { return x; }\n"
        "fn h(x: int32, y: int32) -> int32 { g = 1; return x; }\n"
        "fn main() -> int32 {\n"
        "    let x: int32 = 9;\n"
        "    let b = struct Buf { data = &x };\n"
        "    if (b.data == null) { return 0; }\n"
        "    let n = h(1);\n"
        "    return first(b.data) + n;\n"
        "}"
    ) == 10


def test_bodyless_prototype_call_kills():
    # An unpaired prototype (an imported .mci stub's shape) has no body to
    # analyze: bodyless means opaque, exactly like @extern.
    with pytest.raises(LangError, match="cannot pass a possibly-null pointer"):
        compile_ir(
            BUF + FIRST + "fn helper(n: int32) -> int32;\n"
            "fn peek(b: Buf*) -> int32 {\n"
            "    if (b == null or b->data == null) { return 0; }\n"
            "    helper(1);\n"
            "    return first(b->data);\n"
            "}\n"
            "fn main() -> int32 { return 0; }"
        )


def test_paired_prototype_takes_definition_bit():
    # A forward declaration pairs with its same-signature definition; the
    # definition's (clear) bit stands in for the prototype.
    assert run(
        BUF + FIRST + "fn leaf(n: int32) -> int32;\n"
        "fn main() -> int32 {\n"
        "    let x: int32 = 3;\n"
        "    let b = struct Buf { data = &x };\n"
        "    if (b.data == null) { return 0; }\n"
        "    let n = leaf(1);\n"
        "    return first(b.data) + n;\n"
        "}\n"
        "fn leaf(n: int32) -> int32 { return n + 1; }"
    ) == 5


def test_function_pointer_call_still_kills():
    # An indirect call drops the callee's identity -- and with it the bit:
    # even a pointer to a proven write-free function kills.
    with pytest.raises(LangError, match="cannot pass a possibly-null pointer"):
        compile_ir(
            BUF + FIRST + "fn pure(n: int32) -> int32 { return n; }\n"
            "fn main() -> int32 {\n"
            "    let x: int32 = 1;\n"
            "    let b = struct Buf { data = &x };\n"
            "    let fp: fn(int32) -> int32 = pure;\n"
            "    if (b.data == null) { return 0; }\n"
            "    fp(1);\n"
            "    return first(b.data);\n"
            "}"
        )


def test_calling_struct_default_taints_literal():
    # A field default is evaluated at each application site, outside the
    # body's own AST: once a call-bearing default exists in the program,
    # a struct literal counts as opaque.
    with pytest.raises(LangError, match="cannot pass a possibly-null pointer"):
        compile_ir(
            BUF + FIRST + "@static let g: int32 = 0;\n"
            "fn next_id() -> int32 { g = g + 1; return g; }\n"
            "struct Tagged { id: int32 = next_id(); }\n"
            "fn peek(b: Buf*) -> int32 {\n"
            "    if (b == null or b->data == null) { return 0; }\n"
            "    let t = struct Tagged { };\n"
            "    return first(b->data) + t.id;\n"
            "}\n"
            "fn main() -> int32 { return 0; }"
        )


def test_protocol_for_loop_taints():
    # A protocol `for` lowers to <struct>_it/<struct>_next calls whose
    # names the syntactic pass cannot see: the looping caller is opaque,
    # and a call to it kills. (The builtin range loop is exempt: see
    # test_pure_leaf_call_preserves_projection_fact's leaf shape plus the
    # range-based loops throughout this file.)
    with pytest.raises(LangError, match="cannot pass a possibly-null pointer"):
        compile_ir(
            BUF + FIRST + "struct box { n: int32; }\n"
            "fn box_it(b: box*) -> int32 { return 0; }\n"
            "fn box_next(it: int32*, out: int32*) -> bool { return false; }\n"
            "fn walk(bx: box*) { for v in *bx { } }\n"
            "fn peek(b: Buf*, bx: box*) -> int32 {\n"
            "    if (b == null or b->data == null) { return 0; }\n"
            "    walk(bx);\n"
            "    return first(b->data);\n"
            "}\n"
            "fn main() -> int32 { return 0; }"
        )


def test_name_fact_survives_writing_call():
    # The refinement never touches name facts: an eligible local is
    # unreachable from any callee, writing or not.
    assert run(
        FIRST + "@static let g: int32 = 0;\n"
        "fn bump() { g = 1; }\n"
        "fn main() -> int32 {\n"
        "    let x: int32 = 6;\n"
        "    let p: int32* = &x;\n"
        "    if (p == null) { return 0; }\n"
        "    bump();\n"
        "    return first(p);\n"
        "}"
    ) == 6


# ------------------------------------------------ projection fact consumers


def test_projection_narrowing_compiles_generic_sizeof_repro():
    # The motivating repro: a diverging or-guard chains a size check, a
    # name, and a projection; the projection then crosses a generic
    # @nonnull slot. Compile-only: the local is deliberately uninitialized.
    assert "define" in compile_ir(
        "struct A<T> { ptr: T*; }\n"
        "fn f<T>(@nonnull buf: T*, n: uint64) { }\n"
        "fn main() -> int32 {\n"
        "    let a: A<int64>*;\n"
        "    if (sizeof(int64) != sizeof(uint64) or a == null or a->ptr == null)\n"
        "        return 1;\n"
        "    f(a->ptr, sizeof(a));\n"
        "    return 0;\n"
        "}"
    )


def test_guarded_projection_decays_into_mut():
    # A proven projection decays into a mut slot like a proven local does.
    assert run(
        BUF + "fn bump(mut n: int32) { n = n + 1; }\n"
        "fn main() -> int32 {\n"
        "    let x: int32 = 4;\n"
        "    let b = struct Buf { data = &x };\n"
        "    if (b.data == null) { return 0; }\n"
        "    bump(b.data);\n"
        "    return x;\n"
        "}"
    ) == 5


def test_guarded_projection_decays_into_generic_const():
    # A struct-only pattern (`const c: Cell<T>`) can only bind a pointer
    # argument through the decay reading, whose proof was recorded when
    # the projection argument was evaluated (the generic pre-eval path).
    assert run(
        "struct Cell<T> { n: T; }\n"
        "struct Holder { cell: Cell<int32>*; }\n"
        "fn read<T>(const c: Cell<T>) -> T { return c.n; }\n"
        "fn main() -> int32 {\n"
        "    let c = struct Cell<int32> { n = 6 };\n"
        "    let h = struct Holder { cell = &c };\n"
        "    if (h.cell == null) { return 0; }\n"
        "    return read(h.cell);\n"
        "}"
    ) == 6


def test_projection_hatch_still_works():
    # a->ptr! keeps working with no guard in sight (the shipped behavior).
    assert run(
        BUF + FIRST + "fn main() -> int32 {\n"
        "    let x: int32 = 2;\n"
        "    let b = struct Buf { data = &x };\n"
        "    return first(b.data!);\n"
        "}"
    ) == 2


def test_let_seeds_name_fact_from_guarded_projection():
    # `let q = b.data` under the guard seeds q's *name* fact, which then
    # survives calls and loops the path fact would not.
    assert run(
        BUF + FIRST + "fn noop() { }\n"
        "fn main() -> int32 {\n"
        "    let x: int32 = 3;\n"
        "    let b = struct Buf { data = &x };\n"
        "    if (b.data == null) { return 0; }\n"
        "    let q: int32* = b.data;\n"
        "    noop();\n"
        "    return first(q);\n"
        "}"
    ) == 3


def test_projection_cast_to_pointer_preserves_proof():
    # The as-cast proof threading applies to projections too.
    assert run(
        BUF + "fn firstb(@nonnull p: uint8*) -> int32 { return *p as int32; }\n"
        "fn main() -> int32 {\n"
        "    let x: int32 = 1;\n"
        "    let b = struct Buf { data = &x };\n"
        "    if (b.data == null) { return 0; }\n"
        "    return firstb(b.data as uint8*);\n"
        "}"
    ) == 1


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
        'import "std/io";\n' + FIRST + "fn main() -> int32 {\n"
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


def test_hatch_seeds_fact_through_let():
    # Fact-seeding through `let`: the binding's initializer proves (the
    # assertion is the proof), so `q` starts flow-narrowed -- one hatch at
    # the binding covers every later use.
    assert run(
        FIRST + "fn get(p: int32*) -> int32 {\n"
        "    let q = p!;\n"
        "    return first(q);\n"
        "}\n"
        "fn main() -> int32 { let x: int32 = 7; return get(&x); }"
    ) == 7


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


# --------------------------------------------------- stdlib adoption (wave 1)


def test_stdlib_rejects_unproven_heap_pointer():
    # The memory copy/fill family's pointers are @nonnull: a raw alloc result
    # carries no proof, so the call is a compile error, not a latent null
    # dereference.
    with pytest.raises(
        LangError, match="cannot pass a possibly-null pointer as argument 1 "
        "of 'bytecopy'"
    ):
        run(
            """
            import "std/memory";
            fn main() -> int32 {
                let src: int32[2];
                src[0] = 1; src[1] = 2;
                let dst = alloc<int32>(2);
                bytecopy(dst, &src[0], 2);
                return 0;
            }
            """
        )


def test_stdlib_accepts_guarded_heap_pointer():
    # The migration story: one diverging null guard after the allocation
    # narrows the pointer for every annotated stdlib call that follows.
    assert run(
        """
        import "std/memory";
        fn main() -> int32 {
            let src: int32[2];
            src[0] = 40; src[1] = 2;
            let dst = alloc<int32>(2);
            if (dst == null) return 1;
            bytecopy(dst, &src[0], 2);
            let got = dst[0] + dst[1];
            dealloc(dst);
            return got;
        }
        """
    ) == 42
