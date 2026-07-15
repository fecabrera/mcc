"""``mut``/``const``-carrying function types: ``fn(mut T)`` / ``fn(const T)``.

The function type spells the per-parameter calling convention (`fn(mut char)`,
`fn(const big)`), so a function with ``mut`` or hidden-reference ``const``
parameters is a legal function value and calls through the value pass the
same by-reference arguments -- and run the same lvalue/storage checks -- as a
direct call. Unlike the ``@nonnull`` contract, the hidden-reference shape is
a calling convention: two fn types that differ in it are simply not
convertible, in either direction, with no ``as`` hatch.
"""

import re

import pytest

from mcc.codegen import CodeGen
from mcc.errors import LangError
from mcc.interface import render_interface
from mcc.lexer import tokenize
from mcc.parser import Parser
from helpers import compile_ir, parse, run


BUMP = "fn bump(mut a: char) { a = a + 1; }\n"
PLAIN = "fn plain(a: char) {}\n"
BIG = "struct big { a: int64; b: int64; c: int64; }\n"
SUM = "fn sum(const s: &struct big) -> int64 { return s.a + s.b + s.c; }\n"

MUT_MISMATCH = (
    "(a reference parameter is passed by hidden reference, a different calling "
    "convention; the types are not convertible)"
)
CONST_MISMATCH = (
    "(a const parameter is passed by hidden reference, a different calling "
    "convention; the types are not convertible)"
)


def _iface(source: str) -> str:
    program = Parser(tokenize(source)).parse_program()
    imports = list(program.imports)
    cg = CodeGen(program, "test")
    cg.generate()
    return render_interface(cg, source, imports)


# --------------------------------------------------------------------- parser


def test_fn_type_mut_param_parses():
    (func,) = parse("fn apply(cb: fn(mut char, int32)) {}").functions
    cb_type = func.params[0][1]
    assert str(cb_type) == "fn(&char, int32) -> void"
    assert cb_type.params[0].mut and not cb_type.params[1].mut


def test_fn_type_const_param_parses():
    (func,) = parse("fn apply(cb: fn(const struct big) -> int64) {}").functions
    cb_type = func.params[0][1]
    assert str(cb_type) == "fn(const big) -> int64"
    assert cb_type.params[0].const


def test_fn_type_nonnull_and_mut_rejected():
    # The declaration-side compose ban, mirrored at the fn-type slot.
    with pytest.raises(
        LangError,
        match=re.escape(
            "a parameter cannot be both @nonnull and a reference "
            "(a reference parameter is passed by hidden reference and is never null)"
        ),
    ):
        parse("fn f(cb: fn(@nonnull mut char*)) {}")


def test_fn_type_const_ref_is_the_read_only_view():
    # Phase B: `const &T` in a function type is the read-only reference view
    # (no longer rejected). Both flags ride the TypeRef; the resolver
    # reconciles them into a read-only hidden reference.
    (func,) = parse("fn f(cb: fn(const &char)) {}").functions
    ref = func.params[0][1].params[0]
    assert ref.const and ref.mut


# ------------------------------------------------------- values and inference


def test_mut_fn_value_writes_through_to_the_caller():
    # The semantic point: a call through the value passes the argument's
    # address, so the callee's write lands in the caller's local.
    assert run(
        BUMP + "fn main() -> int32 {\n"
        "    let f = bump;\n"
        "    let c: char = 'A';\n"
        "    f(c);\n"
        "    return (c == 'B') ? 1 : 0;\n"
        "}"
    ) == 1


def test_mut_fn_value_ir_type_is_pointer_taking():
    out = compile_ir(
        BUMP + "fn main() -> int32 { let f = bump; return 0; }"
    )
    assert "void (i8*)*" in out


def test_const_struct_fn_value_runs():
    assert run(
        BIG + SUM + "fn main() -> int32 {\n"
        "    let f = sum;\n"
        "    let v: struct big;\n"
        "    v.a = 1; v.b = 2; v.c = 3;\n"
        "    return f(v) as int32;\n"
        "}"
    ) == 6


def test_explicit_annotated_type_accepts_matching_function():
    assert run(
        BUMP + "fn main() -> int32 {\n"
        "    let f: fn(mut char) = bump;\n"
        "    let c: char = 'x';\n"
        "    f(c);\n"
        "    return (c == 'y') ? 3 : 0;\n"
        "}"
    ) == 3


def test_fn_type_parameter_passes_callback():
    # A fn(mut T) parameter takes the callback and calls through it.
    assert run(
        BUMP + "fn twice(cb: fn(mut char), mut c: char) { cb(c); cb(c); }\n"
        "fn main() -> int32 {\n"
        "    let c: char = 'a';\n"
        "    twice(bump, c);\n"
        "    return (c == 'c') ? 2 : 0;\n"
        "}"
    ) == 2


def test_struct_field_carries_convention():
    assert run(
        BUMP + "struct holder { cb: fn(mut char); }\n"
        "fn main() -> int32 {\n"
        "    let h: holder = holder{cb = bump};\n"
        "    let c: char = '0';\n"
        "    h.cb(c);\n"
        "    return (c == '1') ? 4 : 0;\n"
        "}"
    ) == 4


def test_static_table_of_mut_fn_values():
    # The constant-initializer path (const_coerce) admits the exact type.
    assert run(
        BUMP + "fn drop2(mut a: char) { a = a - 2; }\n"
        "@static let ops: (fn(mut char))[] = [bump, drop2];\n"
        "fn main() -> int32 {\n"
        "    let c: char = 'M';\n"
        "    ops[0](c);\n"
        "    ops[1](c);\n"
        "    return (c == 'L') ? 5 : 0;\n"
        "}"
    ) == 5


def test_mixed_convention_indices_are_per_parameter():
    # Only the marked indices change convention; the plain one stays by value.
    assert run(
        BIG + "fn mix(n: int32, mut acc: int64, const s: struct big) {\n"
        "    acc = acc + s.a + (n as int64);\n"
        "}\n"
        "fn main() -> int32 {\n"
        "    let f = mix;\n"
        "    let total: int64 = 0;\n"
        "    let v: struct big;\n"
        "    v.a = 10; v.b = 0; v.c = 0;\n"
        "    f(1, total, v);\n"
        "    return total as int32;\n"
        "}"
    ) == 11


# ------------------------------------------- call-site rules through the value


def test_indirect_mut_argument_must_be_an_lvalue():
    with pytest.raises(
        LangError,
        match="argument 1 of 'f' is not assignable; a reference parameter needs "
        "a variable, field, element, or dereference",
    ):
        compile_ir(
            BUMP + "fn main() -> int32 { let f = bump; f('x'); return 0; }"
        )


def test_indirect_mut_argument_rejects_const_parameter():
    with pytest.raises(
        LangError,
        match="cannot pass a const parameter as a reference argument; it is read-only",
    ):
        compile_ir(
            "fn setv(mut n: int32) { n = 7; }\n"
            "fn outer(const n: int32) { let f = setv; f(n); }\n"
            "fn main() -> int32 { return 0; }"
        )


def test_indirect_mut_accepts_proven_pointer_decay():
    # A proven-non-null pointer decays into the mut slot through the value,
    # exactly as at a direct call.
    assert run(
        BUMP + "fn main() -> int32 {\n"
        "    let c: char = 'p';\n"
        "    let p: char* = &c;\n"
        "    let f = bump;\n"
        "    f(p);\n"
        "    return (c == 'q') ? 6 : 0;\n"
        "}"
    ) == 6


def test_indirect_mut_rejects_unproven_pointer_decay():
    with pytest.raises(
        LangError, match="cannot pass a possibly-null char\\* as argument 1 of 'f'"
    ):
        compile_ir(
            BUMP + "fn make() -> char* { return null; }\n"
            "fn main() -> int32 {\n"
            "    let f = bump;\n"
            "    let p: char* = make();\n"
            "    f(p);\n"
            "    return 0;\n"
            "}"
        )


# --------------------------------- non-convertibility (both directions, D4)


def test_mut_fn_value_does_not_drop_to_plain():
    with pytest.raises(
        LangError,
        match=re.escape(
            "let g: expected fn(char) -> void, got fn(&char) -> void "
            + MUT_MISMATCH
        ),
    ):
        compile_ir(
            BUMP + "fn main() -> int32 { let g: fn(char) = bump; return 0; }"
        )


def test_plain_fn_value_does_not_lift_to_mut():
    # Unlike @nonnull there is no contravariant direction: the convention
    # must match exactly.
    with pytest.raises(
        LangError,
        match=re.escape(
            "let g: expected fn(&char) -> void, got fn(char) -> void "
            + MUT_MISMATCH
        ),
    ):
        compile_ir(
            PLAIN + "fn main() -> int32 { let g: fn(mut char) = plain; return 0; }"
        )


def test_const_ref_struct_fn_value_does_not_drop_to_by_value():
    # A `const &` reference fn value keeps its hidden-reference convention: it
    # does not silently match a by-value slot. (Since Phase B a by-value
    # `const T` erases, so `fn(big)` is the by-value shape.)
    with pytest.raises(
        LangError,
        match=re.escape(
            "let g: expected fn(big) -> int64, got fn(const &big) -> int64 "
            + CONST_MISMATCH
        ),
    ):
        compile_ir(
            BIG + SUM
            + "fn main() -> int32 { let g: fn(struct big) -> int64 = sum; return 0; }"
        )


def test_by_value_struct_fn_does_not_lift_to_const_ref():
    # ...and the reverse: a by-value function does not lift into a `const &`
    # reference slot (`fn(mut const T)` is the fn-type spelling of `const &T`).
    with pytest.raises(
        LangError,
        match=re.escape(
            "let g: expected fn(const &big) -> int64, got fn(big) -> int64 "
            + CONST_MISMATCH
        ),
    ):
        compile_ir(
            BIG + "fn vsum(s: struct big) -> int64 { return s.a; }\n"
            "fn main() -> int32 {\n"
            "    let g: fn(const &struct big) -> int64 = vsum;\n"
            "    return 0;\n"
            "}"
        )


def test_argument_position_reports_the_same_mismatch():
    with pytest.raises(
        LangError,
        match=re.escape(
            "argument 1 of 'take': expected fn(char) -> void, "
            "got fn(&char) -> void " + MUT_MISMATCH
        ),
    ):
        compile_ir(
            BUMP + "fn take(cb: fn(char)) {}\n"
            "fn main() -> int32 { take(bump); return 0; }"
        )


def test_static_initializer_reports_the_same_mismatch():
    with pytest.raises(
        LangError,
        match=re.escape(
            "@static initializer: expected fn(char) -> void, "
            "got fn(&char) -> void " + MUT_MISMATCH
        ),
    ):
        compile_ir(
            BUMP + "@static let ops: (fn(char))[] = [bump];\n"
            "fn main() -> int32 { return 0; }"
        )


def test_ternary_mix_of_conventions_rejected():
    with pytest.raises(LangError, match=re.escape("ternary branch: ")):
        compile_ir(
            BUMP + PLAIN + "fn main() -> int32 {\n"
            "    let flag: bool = true;\n"
            "    let h = flag ? bump : plain;\n"
            "    return 0;\n"
            "}"
        )


# ------------------------------------------------------------ the `as` rule


def test_as_between_convention_shapes_rejected():
    # There is no call sequence an `as` could make correct, so it is not
    # offered -- unlike the @nonnull strip, which stays open below.
    with pytest.raises(
        LangError,
        match=re.escape(
            "cannot cast fn(&char) -> void to fn(char) -> void: a reference "
            "parameter is passed by hidden reference, a different calling "
            "convention; the types are not convertible"
        ),
    ):
        compile_ir(
            BUMP + "fn main() -> int32 { let g = bump as fn(char); return 0; }"
        )


def test_as_between_const_shapes_rejected():
    # A `const &` reference shape cannot be cast to a by-value shape: no call
    # sequence bridges the calling-convention change.
    with pytest.raises(
        LangError,
        match=re.escape(
            "cannot cast fn(const &big) -> int64 to fn(big) -> int64: a const "
            "parameter is passed by hidden reference, a different calling "
            "convention; the types are not convertible"
        ),
    ):
        compile_ir(
            BIG + SUM
            + "fn main() -> int32 {\n"
            "    let g = sum as fn(struct big) -> int64;\n"
            "    return 0;\n"
            "}"
        )


def test_as_same_shape_reinterpret_still_works():
    # A same-shape signature reinterpret stays open: both types pass the
    # argument by reference, so the cast changes only the spelled type.
    assert run(
        BUMP + "fn main() -> int32 {\n"
        "    let g = bump as fn(mut uint8);\n"
        "    let u: uint8 = 65;\n"
        "    g(u);\n"
        "    return (u == 66) ? 7 : 0;\n"
        "}"
    ) == 7


def test_as_nonnull_strip_still_works_on_matching_shape():
    # The shipped @nonnull hatch survives: same hidden-reference shape (none),
    # so stripping the contract is still a free reinterpret.
    out = compile_ir(
        "fn first(@nonnull p: int32*) -> int32 { return *p; }\n"
        "fn main() -> int32 {\n"
        "    let g = first as fn(int32*) -> int32;\n"
        "    return 0;\n"
        "}"
    )
    assert "i32 (i32*)*" in out


def test_pointer_laundering_is_the_remaining_ub_door():
    # fn -> uint8* -> fn crosses the convention unchecked, like inttoptr:
    # deliberately open, documented as undefined behavior.
    out = compile_ir(
        BUMP + "fn main() -> int32 {\n"
        "    let raw = bump as uint8*;\n"
        "    let g = raw as fn(char);\n"
        "    return 0;\n"
        "}"
    )
    assert "bitcast" in out


# ------------------------------------------------- const-scalar erasure (D1a)


def test_const_scalar_fn_type_is_the_plain_type():
    # `const` on a by-value scalar parameter is not part of the convention:
    # `fn(const int32)` *is* `fn(int32)`, so both spellings inhabit freely.
    assert run(
        "fn take(n: int32) -> int32 { return n + 1; }\n"
        "fn ctake(const n: int32) -> int32 { return n + 2; }\n"
        "fn main() -> int32 {\n"
        "    let a: fn(const int32) -> int32 = take;\n"
        "    let b: fn(int32) -> int32 = ctake;\n"
        "    return a(1) + b(1);\n"
        "}"
    ) == 5


def test_const_scalar_spelling_normalizes_in_messages():
    # The canonical name drops the erased const, so diagnostics spell the
    # one true type.
    with pytest.raises(
        LangError, match=re.escape("expected fn(int32) -> void, got int32")
    ):
        compile_ir(
            "fn main() -> int32 {\n"
            "    let f: fn(const int32) = 0 as int32;\n"
            "    return 0;\n"
            "}"
        )


# ----------------------------------------------------- generic alias: cmp<T>


def test_cmp_alias_inhabited_at_scalar_t():
    # The roadmap's motivating alias: `const T` classifies per binding, so at
    # a scalar T the const erases and a plain-parameter function inhabits it.
    assert run(
        "type cmp<T> = fn(const T, const T) -> bool;\n"
        "fn less(a: int32, b: int32) -> bool { return a < b; }\n"
        "fn main() -> int32 {\n"
        "    let c: cmp<int32> = less;\n"
        "    return c(1, 2) ? 8 : 0;\n"
        "}"
    ) == 8


def test_cmp_alias_inhabited_at_struct_t():
    # ...and at a struct T the const still erases (Phase B generalized the
    # scalar rule to all types), so a const-parameter comparator -- itself a
    # by-value read-only copy -- inhabits the alias.
    assert run(
        "type cmp<T> = fn(const T, const T) -> bool;\n"
        + BIG
        + "fn big_less(const a: struct big, const b: struct big) -> bool {\n"
        "    return a.a < b.a;\n"
        "}\n"
        "fn main() -> int32 {\n"
        "    let c: cmp<struct big> = big_less;\n"
        "    let x: struct big; let y: struct big;\n"
        "    x.a = 1; x.b = 0; x.c = 0;\n"
        "    y.a = 2; y.b = 0; y.c = 0;\n"
        "    return c(x, y) ? 9 : 0;\n"
        "}"
    ) == 9


def test_cmp_alias_at_struct_t_accepts_by_value_comparator():
    # Phase B generalizes const-erases-from-fn-types to every type: `const T`
    # erases at a struct binding too, so `cmp<big>` is `fn(big, big)` and a
    # plain by-value comparator inhabits it (the old hidden-reference
    # requirement is gone).
    assert run(
        "type cmp<T> = fn(const T, const T) -> bool;\n"
        + BIG
        + "fn vless(a: struct big, b: struct big) -> bool { return a.a < b.a; }\n"
        "fn main() -> int32 {\n"
        "    let c: cmp<struct big> = vless;\n"
        "    let x: struct big; let y: struct big;\n"
        "    x.a = 1; x.b = 0; x.c = 0;\n"
        "    y.a = 2; y.b = 0; y.c = 0;\n"
        "    return c(x, y) ? 4 : 0;\n"
        "}"
    ) == 4


def test_cmp_alias_at_struct_t_rejects_const_ref_comparator():
    # ...but a `const &` reference comparator does NOT inhabit the by-value
    # alias: the reference is a distinct calling convention.
    with pytest.raises(LangError, match=re.escape(CONST_MISMATCH)):
        compile_ir(
            "type cmp<T> = fn(const T, const T) -> bool;\n"
            + BIG
            + "fn rless(const a: &struct big, const b: &struct big) -> bool {\n"
            "    return true;\n"
            "}\n"
            "fn main() -> int32 { let c: cmp<struct big> = rless; return 0; }"
        )


# --------------------------------------------------- collecting values (D5)


def test_collecting_fn_is_a_legal_value_with_an_explicit_slice():
    # A collecting function is an ordinary value; through the pointer there
    # is no collection, so the call passes the trailing slice explicitly.
    assert run(
        "fn total(args...) -> int32 {\n"
        "    let n: int32 = 0;\n"
        "    for a in args { case type (a) { when int32 v: n = n + v; else: } }\n"
        "    return n;\n"
        "}\n"
        "fn forward(args...) -> int32 {\n"
        "    let f = total;\n"
        "    return f(args);\n"
        "}\n"
        "fn main() -> int32 { return forward(1, 2, 3); }"
    ) == 6


def test_collecting_fn_value_spells_the_slice_parameter():
    # The `args...` sugar param is a plain `const slice<const any>`: a by-value
    # read-only copy since Phase B, so the value's type erases const to
    # `fn(slice<const any>)` -- the `const` and bare spellings name one type.
    for slot in ("fn(const slice<const any>)", "fn(slice<const any>)"):
        program = (
            "fn total(args...) -> int32 { return 0; }\n"
            f"fn use(cb: {slot} -> int32) -> int32 {{ return 1; }}\n"
            "fn main() -> int32 { return use(total); }"
        )
        assert run(program) == 1
    # A `const &` reference slot is a distinct convention the by-value
    # collector does not inhabit.
    with pytest.raises(LangError, match=re.escape(CONST_MISMATCH)):
        compile_ir(
            "fn total(args...) -> int32 { return 0; }\n"
            "fn use(cb: fn(const &slice<const any>) -> int32) -> int32 "
            "{ return 1; }\n"
            "fn main() -> int32 { return use(total); }"
        )


# ------------------------------------------------------------ .mci round-trip


def test_mut_fn_type_round_trips_through_interface():
    # The stub renders the fn-type parameter through TypeRef.__str__, so the
    # convention ships in the .mci and re-parses into the same type.
    src = "fn twice(cb: fn(mut char), mut c: char) { cb(c); cb(c); }\n"
    out = _iface(src)
    assert "fn twice(cb: fn(&char) -> void, c: &char);" in out
    caller = out + (
        BUMP + "fn main() -> int32 {\n"
        "    let c: char = 'a';\n"
        "    twice(bump, c);\n"
        "    return (c == 'c') ? 1 : 0;\n"
        "}\n"
    )
    CodeGen(Parser(tokenize(caller)).parse_program(), "test").generate()


def test_const_fn_type_round_trips_through_interface():
    src = (
        BIG
        + "struct table { cmp: fn(const struct big, const struct big) -> bool; }\n"
    )
    out = _iface(src)
    # (The stub spells struct types with their `struct` keyword.)
    assert "cmp: fn(const struct big, const struct big) -> bool;" in out
    Parser(tokenize(out)).parse_program()  # re-parses cleanly


# ----------------------------------------------------------- monomorphization


def test_mut_fn_type_monomorphizes_separately():
    # The convention is spelled into the type's name, so fn(mut char) and
    # fn(char) instantiate a template separately.
    ir_text = compile_ir(
        "fn id<T>(f: T) -> T { return f; }\n"
        + BUMP + PLAIN
        + "fn main() -> int32 {\n"
        "    let a = id(bump);\n"
        "    let b = id(plain);\n"
        "    let c: char = 'x';\n"
        "    a(c);\n"
        "    b('y');\n"
        "    return 0;\n"
        "}"
    )
    assert "id<$0>($0)<fn(&char) -> void>" in ir_text
    assert "id<$0>($0)<fn(char) -> void>" in ir_text
