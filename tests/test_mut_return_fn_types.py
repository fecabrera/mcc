"""``mut`` returns in function types: ``fn(...) -> &T``.

The function type spells the return convention too: a ``-> mut``-returning
function is a legal function value, its type says so (``fn() -> &int32``),
and a call through the value is an lvalue expression -- assignable,
projectable, and re-lendable as a ``mut`` argument -- exactly like a direct
call. Like the parameter conventions, the mut return is a calling-convention
fact (the call returns a pointer to the vouched storage), so two fn types
that differ in it are not convertible, in either direction, with no ``as``
hatch.
"""

import re

import pytest

from mcc.codegen import CodeGen
from mcc.errors import LangError
from mcc.interface import render_interface
from mcc.lexer import tokenize
from mcc.parser import Parser
from helpers import compile_ir, parse, run


COUNTER = (
    "@static let counter: int32 = 0;\n"
    "fn counter_ref() -> &int32 { return counter; }\n"
)
ZERO = "fn zero() -> int32 { return 0; }\n"
BOX = (
    "struct box { v: int32; w: int32; }\n"
    "fn pick(b: &struct box) -> &struct box { return b; }\n"
)

MUTRET_MISMATCH = (
    "(a reference return is passed as a pointer to the returned storage, a "
    "different calling convention; the types are not convertible)"
)
MUT_CONST_RETURN = (
    "a return cannot be both a reference and const (a reference return must be writable)"
)


def _iface(source: str) -> str:
    program = Parser(tokenize(source)).parse_program()
    imports = list(program.imports)
    cg = CodeGen(program, "test")
    cg.generate()
    return render_interface(cg, source, imports)


# --------------------------------------------------------------------- parser


def test_fn_type_mut_return_parses():
    (func,) = parse("fn f(get: fn(uint64) -> &char) {}").functions
    get_type = func.params[0][1]
    assert get_type.ret.mut
    assert str(get_type) == "fn(uint64) -> &char"


def test_fn_type_plain_return_is_not_mut():
    (func,) = parse("fn f(get: fn(uint64) -> char) {}").functions
    assert not func.params[0][1].ret.mut


def test_fn_type_mut_const_return_rejected():
    # The compose ban: a mut return must be writable, so `mut const` is
    # uninhabitable and banned at parse time -- in the fn-type slot...
    with pytest.raises(LangError, match=re.escape(MUT_CONST_RETURN)):
        parse("fn f(get: fn() -> &const int32) {}")


def test_decl_mut_const_return_rejected():
    # ...and in the declaration slot, symmetrically.
    with pytest.raises(LangError, match=re.escape(MUT_CONST_RETURN)):
        parse("fn f() -> &const int32 { }")


def test_fn_type_mut_void_return_rejected():
    with pytest.raises(
        LangError,
        match=re.escape(
            "a function type cannot return a reference to void (there is no storage "
            "to reference)"
        ),
    ):
        compile_ir("fn main() { let f: fn() -> &void; }")


# ------------------------------------------------------- values and inference


def test_mut_returning_fn_value_writes_through():
    # The semantic point: a call through the value is an lvalue expression,
    # so assignment and compound assignment land in the vouched storage.
    assert run(
        COUNTER + "fn main() -> int32 {\n"
        "    let f = counter_ref;\n"
        "    f() = 41;\n"
        "    f() += 1;\n"
        "    return f();\n"
        "}"
    ) == 42


def test_declared_type_accepts_mut_returning_function():
    assert run(
        COUNTER + "fn main() -> int32 {\n"
        "    let f: fn() -> &int32 = counter_ref;\n"
        "    f() = 5;\n"
        "    return counter;\n"
        "}"
    ) == 5


def test_mut_fn_value_ir_type_is_pointer_returning():
    # The H1 regression canary: the value's LLVM type must return a pointer,
    # or every use of the value would fail IR verification.
    out = compile_ir(
        COUNTER + "fn main() -> int32 { let f = counter_ref; return 0; }"
    )
    assert "i32* ()*" in out


def test_fn_type_parameter_takes_mut_returning_callback():
    assert run(
        COUNTER + "fn poke(get: fn() -> &int32) { get() = 7; }\n"
        "fn main() -> int32 { poke(counter_ref); return counter; }"
    ) == 7


def test_static_table_of_mut_returning_fn_values():
    # The constant-initializer path (const_coerce) admits the exact type,
    # and the indexed callee (a CallExpr) is a statement target too.
    assert run(
        COUNTER + "@static let other: int32 = 0;\n"
        "fn other_ref() -> &int32 { return other; }\n"
        "@static let ops: (fn() -> &int32)[] = [counter_ref, other_ref];\n"
        "fn main() -> int32 {\n"
        "    ops[0]() = 2;\n"
        "    ops[1]() = 3;\n"
        "    ops[1]() += 1;\n"
        "    return counter + other;\n"
        "}"
    ) == 6


def test_generic_alias_spells_mut_return_per_binding():
    # `type getter<T> = fn() -> mut T` resolves transparently per binding.
    assert run(
        COUNTER + "type getter<T> = fn() -> &T;\n"
        "fn main() -> int32 {\n"
        "    let f: getter<int32> = counter_ref;\n"
        "    f() = 6;\n"
        "    return counter;\n"
        "}"
    ) == 6


def test_generic_alias_at_void_rejected_per_binding():
    # The void rule is checked per use, so the alias itself is fine and the
    # void binding is what errors (mirroring the decl side's per-instance
    # check).
    with pytest.raises(
        LangError, match="a function type cannot return a reference to void"
    ):
        compile_ir(
            "type getter<T> = fn() -> &T;\n"
            "fn main() { let f: getter<void>; }"
        )


# ------------------------------------------------------------ lvalue surfaces


def test_struct_field_callee_assigned_through():
    # A field-held callee is a CallExpr: `t.get() = v` and `t.get() += v`
    # store through the returned reference.
    assert run(
        COUNTER + "struct tbl { get: fn() -> &int32; }\n"
        "fn main() -> int32 {\n"
        "    let t = struct tbl { get = counter_ref };\n"
        "    t.get() = 5;\n"
        "    t.get() += 2;\n"
        "    return t.get();\n"
        "}"
    ) == 7


def test_member_projection_through_fn_value():
    assert run(
        BOX + "fn main() -> int32 {\n"
        "    let g = pick;\n"
        "    let b = struct box { v = 1, w = 2 };\n"
        "    g(b).v = 40;\n"
        "    g(b).w += 1;\n"
        "    return b.v + b.w;\n"
        "}"
    ) == 43


def test_tuple_projection_through_fn_value():
    assert run(
        "fn tref(t: &tuple<int32, int32>) -> &tuple<int32, int32> {\n"
        "    return t;\n"
        "}\n"
        "fn main() -> int32 {\n"
        "    let g = tref;\n"
        "    let t: tuple<int32, int32> = (1, 2);\n"
        "    g(t)[0] = 10;\n"
        "    return t[0] + t[1];\n"
        "}"
    ) == 12


def test_relend_through_fn_value():
    # The indirect result's lvalue re-lends as a mut argument, like a direct
    # mut-returning call's.
    assert run(
        COUNTER + "fn bump(a: &int32) { a = a + 1; }\n"
        "fn main() -> int32 { let f = counter_ref; bump(f()); return counter; }"
    ) == 1


def test_address_of_indirect_call_result_banned():
    # `&` on a call result stays banned -- the reference must not escape its
    # full expression -- for a named fn-value callee and a field-held one.
    with pytest.raises(
        LangError, match="cannot take the address of a call result"
    ):
        compile_ir(
            COUNTER + "fn main() { let f = counter_ref; let p = &f(); }"
        )
    with pytest.raises(
        LangError, match="cannot take the address of a call result"
    ):
        compile_ir(
            COUNTER + "struct tbl { get: fn() -> &int32; }\n"
            "fn main() {\n"
            "    let t = struct tbl { get = counter_ref };\n"
            "    let p = &t.get();\n"
            "}"
        )


def test_plain_fn_value_call_not_assignable():
    # A variable-held plain callee reports the named-call rejection.
    with pytest.raises(
        LangError,
        match="the call to 'f' does not return a reference, so its result is not "
        "assignable",
    ):
        compile_ir(ZERO + "fn main() { let f = zero; f() = 2; }")


def test_plain_field_callee_not_assignable():
    # A field-held plain callee (a CallExpr target) reports by type.
    with pytest.raises(
        LangError,
        match=re.escape(
            "the call to a fn() -> int32 value does not return a reference, so its "
            "result is not assignable"
        ),
    ):
        compile_ir(
            ZERO + "struct tbl { get: fn() -> int32; }\n"
            "fn main() { let t = struct tbl { get = zero }; t.get() = 5; }"
        )


# --------------------------------- non-convertibility (both directions, D4)


def test_mut_returning_fn_value_does_not_drop_to_plain():
    with pytest.raises(
        LangError,
        match=re.escape(
            "let g: expected fn() -> int32, got fn() -> &int32 "
            + MUTRET_MISMATCH
        ),
    ):
        compile_ir(
            COUNTER + "fn main() { let g: fn() -> int32 = counter_ref; }"
        )


def test_plain_fn_value_does_not_lift_to_mut_return():
    with pytest.raises(
        LangError,
        match=re.escape(
            "let g: expected fn() -> &int32, got fn() -> int32 "
            + MUTRET_MISMATCH
        ),
    ):
        compile_ir(ZERO + "fn main() { let g: fn() -> &int32 = zero; }")


def test_argument_position_reports_the_same_mismatch():
    with pytest.raises(
        LangError,
        match=re.escape(
            "argument 1 of 'take': expected fn() -> int32, "
            "got fn() -> &int32 " + MUTRET_MISMATCH
        ),
    ):
        compile_ir(
            COUNTER + "fn take(cb: fn() -> int32) {}\n"
            "fn main() { take(counter_ref); }"
        )


def test_static_initializer_reports_the_same_mismatch():
    with pytest.raises(
        LangError,
        match=re.escape(
            "@static initializer: expected fn() -> int32, "
            "got fn() -> &int32 " + MUTRET_MISMATCH
        ),
    ):
        compile_ir(
            COUNTER + "@static let ops: (fn() -> int32)[] = [counter_ref];\n"
            "fn main() { }"
        )


def test_ternary_mix_of_return_conventions_rejected():
    with pytest.raises(
        LangError,
        match=re.escape(
            "ternary branch: expected fn() -> &int32, "
            "got fn() -> int32 " + MUTRET_MISMATCH
        ),
    ):
        compile_ir(
            COUNTER + ZERO + "fn main() -> int32 {\n"
            "    let flag: bool = true;\n"
            "    let h = flag ? counter_ref : zero;\n"
            "    return 0;\n"
            "}"
        )


def test_nonnull_still_lifts_at_equal_mut_return():
    # The @nonnull contravariant rule stays orthogonal: a plain-parameter
    # function lifts into the annotated slot when the mut return matches.
    assert run(
        "fn get(p: int32*) -> &int32 { return *p; }\n"
        "fn main() -> int32 {\n"
        "    let g: fn(@nonnull int32*) -> &int32 = get;\n"
        "    let x: int32 = 1;\n"
        "    g(&x) = 8;\n"
        "    return x;\n"
        "}"
    ) == 8


def test_nonnull_drop_at_equal_mut_return_keeps_its_hint():
    # A pure @nonnull drop (mut return equal on both sides) still gets the
    # contract error naming the `as` hatch -- not the convention error.
    with pytest.raises(
        LangError, match="a @nonnull contract cannot be dropped"
    ):
        compile_ir(
            "fn get(@nonnull p: int32*) -> &int32 { return *p; }\n"
            "fn main() { let g: fn(int32*) -> &int32 = get; }"
        )


# ------------------------------------------------------------ the `as` rule


def test_as_dropping_mut_return_rejected():
    with pytest.raises(
        LangError,
        match=re.escape(
            "cannot cast fn() -> &int32 to fn() -> int32: a reference return "
            "is passed as a pointer to the returned storage, a different "
            "calling convention; the types are not convertible"
        ),
    ):
        compile_ir(
            COUNTER + "fn main() { let g = counter_ref as fn() -> int32; }"
        )


def test_as_adding_mut_return_rejected():
    with pytest.raises(
        LangError,
        match=re.escape(
            "cannot cast fn() -> int32 to fn() -> &int32: a reference return "
            "is passed as a pointer to the returned storage, a different "
            "calling convention; the types are not convertible"
        ),
    ):
        compile_ir(ZERO + "fn main() { let g = zero as fn() -> &int32; }")


def test_as_same_convention_reinterpret_still_works():
    # A same-convention signature reinterpret stays open: both types return
    # a pointer to the storage, so the cast changes only the spelled type.
    assert run(
        COUNTER + "fn main() -> int32 {\n"
        "    let g = counter_ref as fn() -> &uint32;\n"
        "    g() = 7 as uint32;\n"
        "    return counter;\n"
        "}"
    ) == 7


def test_pointer_laundering_is_the_remaining_ub_door():
    # fn -> uint8* -> fn crosses the convention unchecked, like inttoptr:
    # deliberately open, documented as undefined behavior.
    out = compile_ir(
        COUNTER + "fn main() -> int32 {\n"
        "    let raw = counter_ref as uint8*;\n"
        "    let g = raw as fn() -> int32;\n"
        "    return 0;\n"
        "}"
    )
    assert "bitcast" in out


# ------------------------------------------------------------- formation rule


def test_top_of_return_through_indirect_callee():
    # The whole returned expression being one indirect call defers to the
    # post-resolution lvalue check, like a named call.
    assert run(
        COUNTER + "fn wrap(get: fn() -> &int32) -> &int32 {\n"
        "    return get();\n"
        "}\n"
        "fn main() -> int32 { wrap(counter_ref) = 4; return counter; }"
    ) == 4


def test_chain_through_indirect_callee():
    # A chain-position call through a fn-value parameter vouches via its
    # spelled type, composing like a named mut-returning candidate.
    assert run(
        BOX + "fn via(g: fn(&struct box) -> &struct box,\n"
        "        b: &struct box) -> &int32 {\n"
        "    return g(b).v;\n"
        "}\n"
        "fn main() -> int32 {\n"
        "    let b = struct box { v = 1, w = 0 };\n"
        "    via(pick, b) = 7;\n"
        "    return b.v;\n"
        "}"
    ) == 7


def test_chain_through_const_held_fn_value():
    assert run(
        BOX + "const getv = pick;\n"
        "fn via(b: &struct box) -> &int32 { return getv(b).v; }\n"
        "fn main() -> int32 {\n"
        "    let b = struct box { v = 1, w = 0 };\n"
        "    via(b) = 3;\n"
        "    return b.v;\n"
        "}"
    ) == 3


def test_chain_through_plain_fn_value_rejected():
    # A variable-held callee without the mut return does not vouch.
    with pytest.raises(
        LangError,
        match="the chain passes through a call to 'g' that does not "
        "return a reference",
    ):
        compile_ir(
            "struct box { v: int32; }\n"
            "fn via(g: fn(&struct box) -> struct box,\n"
            "        b: &struct box) -> &int32 {\n"
            "    return g(b).v;\n"
            "}\n"
            "fn main() { }"
        )


def test_chain_through_field_held_callee():
    # A CallExpr in chain position vouches through the field's spelled type.
    assert run(
        BOX + "struct api { pick: fn(&struct box) -> &struct box; }\n"
        "fn via(a: struct api, b: &struct box) -> &int32 {\n"
        "    return a.pick(b).v;\n"
        "}\n"
        "fn main() -> int32 {\n"
        "    let a = struct api { pick = pick };\n"
        "    let b = struct box { v = 1, w = 0 };\n"
        "    via(a, b) = 5;\n"
        "    return b.v;\n"
        "}"
    ) == 5


def test_chain_through_plain_field_held_callee_rejected():
    with pytest.raises(
        LangError,
        match="the chain passes through an indirect call that does not "
        "return a reference",
    ):
        compile_ir(
            "struct box { v: int32; }\n"
            "struct api { pick: fn(&struct box) -> struct box; }\n"
            "fn via(a: struct api, b: &struct box) -> &int32 {\n"
            "    return a.pick(b).v;\n"
            "}\n"
            "fn main() { }"
        )


# ------------------------------------------------------------ .mci round-trip


def test_mut_return_fn_type_round_trips_through_interface():
    # The stub renders the fn-type parameter through TypeRef.__str__, so the
    # return convention ships in the .mci and re-parses into the same type.
    src = (
        "struct buf { data: char*; length: uint64; }\n"
        "fn buf_at(self: &struct buf, i: uint64) -> &char {\n"
        "    return self.data[i];\n"
        "}\n"
        "fn install(get: fn(&struct buf, uint64) -> &char,\n"
        "        b: &struct buf) {\n"
        "    get(b, 0) = 'Z';\n"
        "}\n"
    )
    out = _iface(src)
    # (The prototype spells the struct name bare -- `struct` is an optional
    # C-habit keyword to the parser -- unlike str(TypeRef) surfaces, which
    # never had it to drop.)
    assert "fn install(get: fn(&buf, uint64) -> &char, b: &buf);" in out
    CodeGen(Parser(tokenize(out)).parse_program(), "test").generate()


def test_fn_type_in_return_position_round_trips():
    src = (
        COUNTER + "fn pick() -> fn() -> &int32 { return counter_ref; }\n"
    )
    out = _iface(src)
    assert "fn pick() -> fn() -> &int32;" in out
    CodeGen(Parser(tokenize(out)).parse_program(), "test").generate()


# ----------------------------------------------------------- monomorphization


def test_mut_return_fn_type_monomorphizes_separately():
    # The convention is spelled into the type's name, so fn() -> mut int32
    # and fn() -> int32 instantiate a template separately.
    ir_text = compile_ir(
        "fn id<T>(f: T) -> T { return f; }\n"
        + COUNTER + ZERO
        + "fn main() -> int32 {\n"
        "    let a = id(counter_ref);\n"
        "    let b = id(zero);\n"
        "    return 0;\n"
        "}"
    )
    assert "id<$0>($0)<fn() -> &int32>" in ir_text
    assert "id<$0>($0)<fn() -> int32>" in ir_text
