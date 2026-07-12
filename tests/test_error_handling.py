"""Error handling stage 1: `error` declarations, `result<T, E>`, constructors.

An `error` declaration is enum-like but **nominal** and int32-backed: variants
auto-number from 1 in declaration order (explicit values continue the
numbering; `= 0` and duplicate values reject), so every variant is non-zero by
construction and zero is the reserved, unnameable no-error state that makes
`if (err)` a total check. A variant may carry a display string instead of a
value (`NOT_FOUND = "Not Found"`), stored for the rendering stage. `error` is
a contextual introducer -- `error(` stays the constructor and `@error(msg)`
the directive.

`result<T, E>` / `result<E>` is a compiler-built interned template (the
slice/tuple pattern) laid out as `{ tag: uint8, payload }` -- a union of the
arms for arity 2, `E` directly for the error-only arity 1. `E` must be a
declared error type. `ok(v)` / `ok()` / `error(e)` are the only constructors,
context-typed like a bare struct literal; there is no implicit
value-to-result coercion in either direction, and a result exposes no fields.

Stage 2 adds the binding forms (their tests in the stage-2 section below):
form 1, the destructure `let ret, err = f();` (a tag select -- the unselected
binder is zero-filled, never a raw read of the other union arm), and form A,
`try expr except (err) { H } [else { S }]` on a `let`, a `return`, and in
expression-statement position (the `result<E>` consumer, obligation-free).
Where a value escapes, the handler must diverge or `emit` a fallback; `else`
is the ok-arm only (Python's try/except/else) and is skipped on the
emit-fallback path.

Stage 3 completes the `try` production (its tests in the stage-3 sections
below): a try takes exactly ONE of three endings. Bare `try g()` propagates
-- on error the enclosing function returns `error(err)`, so its return type
must be a result carrying the SAME declared error type; `try g() ?? fallback`
discards the error and lazily evaluates the fallback (a greedy expression, or
an emit-block that may diverge) with no requirement on the enclosing return
type; `except (err) { ... }` handles (stage 2). `??` binds LOOSER than the
ternary and every binary operator (the lowest-precedence expression form,
just above assignment) and chains RIGHT-associatively, so its fallback
extends greedily to the end of the expression -- `try g() ?? 2 + 1` is
`try g() ?? (2 + 1)`; parenthesize to operate on the unwrapped value. The try
owns its first `??` clause, whose RHS is that same greedy expression, so
`try g() ?? p ?? q` is `try g() ?? (p ?? q)`. The general coalesce production
exists with every arm rejected: a result unwraps through try, and the pointer
null-coalescing arm waits on the pointer-truthiness roadmap item. The
statement form
`try (ret = f()) { B } except (err) { H }` binds a fresh `ret` scoped to `B`
with an obligation-free handler and no `else` arm.
"""

import re

import pytest

from mcc.codegen import CodeGen
from mcc.errors import LangError
from mcc.interface import render_interface
from mcc.lexer import tokenize
from mcc.parser import Parser
from helpers import compile_ir, run


DECL = "error my_error { NOT_FOUND, PERMISSION, EXHAUSTED }\n"


def iface(source: str) -> str:
    """Render the interface stub for an import-free source string."""
    program = Parser(tokenize(source)).parse_program()
    imports = list(program.imports)
    cg = CodeGen(program, "test")
    cg.generate()
    return render_interface(cg, source, imports)


# ------------------------------------------------------- error declarations

def test_variants_auto_number_from_one():
    # The numeric value reads out through the explicit `as` escape; the
    # variants count 1, 2, 3 in declaration order.
    assert run(
        DECL
        + """
        fn main() -> int32 {
            let a = my_error::NOT_FOUND as int32;
            let b = my_error::PERMISSION as int32;
            let c = my_error::EXHAUSTED as int32;
            return a * 100 + b * 10 + c;
        }
        """
    ) == 123


def test_explicit_value_continues_the_numbering():
    # The C convention: after `= n`, auto-numbering resumes from n + 1.
    assert run(
        """
        error e { A, B = 7, C, D = 100, E }
        fn main() -> int32 {
            if (e::A as int32 != 1)   { return 1; }
            if (e::B as int32 != 7)   { return 2; }
            if (e::C as int32 != 8)   { return 3; }
            if (e::D as int32 != 100) { return 4; }
            if (e::E as int32 != 101) { return 5; }
            return 0;
        }
        """
    ) == 0


def test_display_string_does_not_affect_numbering():
    # A display string is data on the variant, not a value: the variant
    # still auto-numbers in sequence.
    assert run(
        """
        error e { A = "first cause", B, C = "third cause" }
        fn main() -> int32 {
            let a = e::A as int32;
            let b = e::B as int32;
            let c = e::C as int32;
            return a * 100 + b * 10 + c;
        }
        """
    ) == 123


def test_truthiness_against_the_zero_state():
    # `if (err)` and `while (err)` test against the reserved zero no-error
    # state; every declared variant is non-zero by construction.
    assert run(
        DECL
        + """
        fn code(e: my_error) -> int32 {
            if (e) { return 1; }
            return 0;
        }
        fn main() -> int32 {
            if (code(my_error::NOT_FOUND) != 1) { return 1; }
            if (code(my_error::EXHAUSTED) != 1) { return 2; }
            return 42;
        }
        """
    ) == 42


def test_equality_against_own_members():
    assert run(
        DECL
        + """
        fn main() -> int32 {
            let e = my_error::PERMISSION;
            if (e != my_error::PERMISSION) { return 1; }
            if (e == my_error::NOT_FOUND)  { return 2; }
            return 42;
        }
        """
    ) == 42


def test_case_over_an_error_value():
    assert run(
        DECL
        + """
        fn classify(e: my_error) -> int32 {
            case (e) {
                when my_error::NOT_FOUND:  return 1;
                when my_error::PERMISSION: return 2;
                else:                      return 3;
            }
        }
        fn main() -> int32 {
            if (classify(my_error::NOT_FOUND) != 1)  { return 1; }
            if (classify(my_error::PERMISSION) != 2) { return 2; }
            if (classify(my_error::EXHAUSTED) != 3)  { return 3; }
            return 42;
        }
        """
    ) == 42


def test_error_values_are_ordinary_data():
    # An error value passes, returns, and sits in a struct field like any
    # value of a nominal scalar type.
    assert run(
        DECL
        + """
        struct report { cause: my_error; count: int32; }
        fn worst(a: my_error, b: my_error) -> my_error {
            if (a == my_error::EXHAUSTED) { return a; }
            return b;
        }
        fn main() -> int32 {
            let r = struct report { cause = my_error::EXHAUSTED, count = 2 };
            let w = worst(r.cause, my_error::NOT_FOUND);
            if (w == my_error::EXHAUSTED) { return 42; }
            return 1;
        }
        """
    ) == 42


# ------------------------------------------- error declaration rejections

def test_zero_value_is_rejected():
    with pytest.raises(
        LangError,
        match=re.escape(
            "error 'e' member 'A' cannot be 0; zero is the reserved "
            "no-error state (values start at 1)"
        ),
    ):
        compile_ir("error e { A = 0 }")


def test_duplicate_values_are_rejected():
    with pytest.raises(
        LangError,
        match=re.escape(
            "error 'e' members 'A' and 'C' share the value 2; each variant "
            "is a distinct cause"
        ),
    ):
        # C auto-numbers to 2, colliding with the explicit B = ... A = 2? no:
        # A auto-numbers to 1, B takes 2 explicitly? A=2 explicit, B=1, C
        # auto-continues from B: 2 -- the continuation collides with A.
        compile_ir("error e { A = 2, B = 1, C }")


def test_duplicate_member_name_is_rejected():
    with pytest.raises(
        LangError, match=re.escape("error 'e' has a duplicate member 'A'")
    ):
        compile_ir("error e { A, A }")


def test_empty_declaration_is_rejected():
    with pytest.raises(LangError, match=re.escape("error 'e' has no members")):
        compile_ir("error e { }")


def test_arithmetic_on_error_values_is_rejected():
    with pytest.raises(
        LangError, match=re.escape("operand of '+': expected my_error, got int32")
    ):
        compile_ir(DECL + "fn f(e: my_error) -> my_error { return e + 1; }")


def test_ordering_on_error_values_is_rejected():
    # Identity comparison only: the variants are named causes, their values
    # an implementation detail of the numbering.
    with pytest.raises(
        LangError, match=re.escape("operator '<' not supported for my_error")
    ):
        compile_ir(
            DECL + "fn f(e: my_error) -> bool { return e < my_error::PERMISSION; }"
        )


def test_no_implicit_conversion_in_either_direction():
    with pytest.raises(
        LangError, match=re.escape("return value: expected int32, got my_error")
    ):
        compile_ir(DECL + "fn f(e: my_error) -> int32 { return e; }")
    with pytest.raises(
        LangError, match=re.escape("return value: expected my_error, got int32")
    ):
        compile_ir(DECL + "fn f() -> my_error { return 1; }")


def test_cast_into_an_error_type_is_rejected():
    # Minting an error from an integer would name a value no member declares
    # (0 included); error(member) is the only producer. Reading the numeric
    # value out stays an explicit escape (covered by the numbering tests).
    with pytest.raises(
        LangError,
        match=re.escape(
            "cannot cast int32 to my_error; an error value is one of "
            "my_error's declared members"
        ),
    ):
        compile_ir(DECL + "fn f() -> my_error { return 1 as my_error; }")


def test_error_value_does_not_box_into_any():
    # Rendering an error through `{}` is a later stage; today the box is
    # rejected rather than printing a meaningless integer.
    with pytest.raises(LangError, match=re.escape("cannot box a my_error in an any")):
        compile_ir(
            DECL
            + "fn f(e: my_error) { let a: any = e; }"
        )


def test_case_arm_of_the_wrong_type_is_rejected():
    with pytest.raises(
        LangError, match=re.escape("expected my_error, got int32")
    ):
        compile_ir(
            DECL
            + """
            fn f(e: my_error) -> int32 {
                case (e) { when 1: return 1; else: return 0; }
            }
            """
        )


def test_two_declarations_do_not_mix():
    # Nominal: same shape, different declarations -- a member of one is not
    # a value of the other.
    with pytest.raises(
        LangError, match=re.escape("return value: expected other, got my_error")
    ):
        compile_ir(
            DECL
            + "error other { X }\n"
            + "fn f() -> other { return my_error::NOT_FOUND; }"
        )


# ----------------------------------------------------------- the result type

def test_layout_tag_plus_union_and_sizeof_agreement():
    # The dual-site layout invariant: the IR identified type (asserted on the
    # module text) and the fields-driven sizeof (asserted at runtime) must
    # describe the same struct.
    src = DECL + """
        struct wide { a: int64; b: int64; c: int64; }
        fn two() -> result<int32, my_error> { return ok(1); }
        fn one() -> result<my_error> { return ok(); }
        fn big() -> result<wide, my_error> { return error(my_error::EXHAUSTED); }
        fn main() -> int32 {
            if (sizeof(result<int32, my_error>) != 8)  { return 1; }
            if (sizeof(result<my_error>) != 8)         { return 2; }
            if (sizeof(result<wide, my_error>) != 32)  { return 3; }
            return 0;
        }
        """
    ir_text = compile_ir(src)
    # Arity 2: a one-byte tag plus the union payload (its storage the
    # widest-aligned arm plus pad); arity 1: the tag plus E directly.
    assert '%"result<int32, my_error>.payload" = type {i32}' in ir_text
    assert (
        '%"result<int32, my_error>" = type {i8, %"result<int32, my_error>.payload"}'
        in ir_text
    )
    assert '%"result<my_error>" = type {i8, i32}' in ir_text
    assert '%"result<wide, my_error>.payload" = type {%"wide"}' in ir_text
    assert run(src) == 0


def test_results_construct_return_pass_and_store():
    # A result is an ordinary value: returned, passed by value, stored in a
    # local, reassigned, and placed in a struct field -- all without any
    # consumption form.
    assert run(
        DECL
        + """
        struct slot { r: result<int32, my_error>; }
        fn find(key: int32) -> result<int32, my_error> {
            if (key == 0) { return error(my_error::NOT_FOUND); }
            return ok(40 + key);
        }
        fn ping(fail: bool) -> result<my_error> {
            if (fail) { return error(my_error::EXHAUSTED); }
            return ok();
        }
        fn keep(r: result<int32, my_error>) -> result<int32, my_error> {
            return r;
        }
        fn main() -> int32 {
            let good = find(2);
            let bad = find(0);
            let solo = ping(false);
            let copy = keep(good);
            let s = struct slot { r = copy };
            s.r = find(1);
            let annotated: result<int32, my_error> = ok(5);
            annotated = error(my_error::PERMISSION);
            return 42;
        }
        """
    ) == 42


def test_ok_value_adapts_like_a_typed_position():
    # The ok arm is a typed position: a bare struct literal builds a struct
    # T, a string literal borrows into a slice<char> T, an untyped constant
    # adapts to an integer T.
    assert run(
        DECL
        + """
        struct point { x: int32; y: int32; }
        fn origin() -> result<point, my_error> { return ok({ x = 1, y = 2 }); }
        fn name() -> result<slice<char>, my_error> { return ok("hi"); }
        fn wide() -> result<uint64, my_error> { return ok(5); }
        fn main() -> int32 {
            let a = origin();
            let b = name();
            let c = wide();
            return 42;
        }
        """
    ) == 42


def test_error_takes_any_expression_of_the_declared_type():
    # Not just a literal member: a variable of the error type works, which is
    # what the future propagation idiom (`error(err)`) relies on.
    assert run(
        DECL
        + """
        fn fail(e: my_error) -> result<int32, my_error> { return error(e); }
        fn main() -> int32 {
            let r = fail(my_error::PERMISSION);
            return 42;
        }
        """
    ) == 42


def test_result_as_argument_adapts_in_call_position():
    # ok()/error() are context-typed by a parameter's declared result type,
    # on the concrete path and through an overload set.
    assert run(
        DECL
        + """
        fn takes(r: result<int32, my_error>) -> int32 { return 40; }
        fn over(r: result<my_error>) -> int32 { return 1; }
        fn over(x: int32) -> int32 { return 2; }
        fn main() -> int32 {
            return takes(ok(7)) + over(error(my_error::NOT_FOUND)) + over(0) - 1;
        }
        """
    ) == 42


def test_generic_inference_recurses_through_result():
    # The template+args unify path: result<T, E> against a concrete
    # result<int32, my_error> binds both parameters.
    assert run(
        DECL
        + """
        fn pick<T, E>(r: result<T, E>, fallback: T) -> T {
            return fallback;
        }
        fn make() -> result<int32, my_error> { return ok(1); }
        fn main() -> int32 {
            return pick(make(), 42);
        }
        """
    ) == 42


def test_nested_result_ok_arm():
    # result<result<...>, E>: the inner constructor adapts against the outer
    # ok arm's type, the same nesting a struct literal supports.
    assert run(
        DECL
        + """
        fn wrap() -> result<result<int32, my_error>, my_error> {
            return ok(ok(7));
        }
        fn main() -> int32 {
            let r = wrap();
            return 42;
        }
        """
    ) == 42


def test_result_t_t_style_same_types_both_arms_is_legal():
    # Arms are named at construction, so E appearing as T too is fine.
    assert run(
        DECL
        + """
        fn f(fail: bool) -> result<my_error, my_error> {
            if (fail) { return error(my_error::NOT_FOUND); }
            return ok(my_error::PERMISSION);
        }
        fn main() -> int32 {
            let r = f(true);
            let s = f(false);
            return 42;
        }
        """
    ) == 42


# ------------------------------------------------------- result rejections

def test_error_type_must_be_an_error_declaration():
    msg = "result's error type must be an error declaration, got int32"
    with pytest.raises(LangError, match=re.escape(msg)):
        compile_ir("fn f() -> result<int32, int32> { return ok(1); }")
    with pytest.raises(LangError, match=re.escape(msg)):
        compile_ir("fn f() -> result<int32> { return ok(1); }")
    with pytest.raises(
        LangError,
        match=re.escape("result's error type must be an error declaration, got point"),
    ):
        compile_ir(
            "struct point { x: int32; }\n"
            "fn f() -> result<int32, point> { return ok(1); }"
        )
    with pytest.raises(
        LangError,
        match=re.escape("result's error type must be an error declaration, got int32"),
    ):
        # A plain enum is transparent -- it *is* its underlying integer, so
        # it rejects as that integer (nominal enums are a separate roadmap
        # item; the error declaration is the nominal-from-birth kind).
        compile_ir(
            "enum e { A = 1 }\n"
            "fn f() -> result<int32, e> { return ok(1); }"
        )


def test_result_arity_is_one_or_two():
    with pytest.raises(
        LangError, match=re.escape("type 'result' takes 1 or 2 type arguments, got 0")
    ):
        compile_ir("fn f() -> result { return ok(1); }")
    with pytest.raises(
        LangError, match=re.escape("type 'result' takes 1 or 2 type arguments, got 3")
    ):
        compile_ir(
            DECL
            + "fn f() -> result<int32, int32, my_error> { return ok(1); }"
        )


def test_result_has_no_void_arm():
    with pytest.raises(
        LangError,
        match=re.escape(
            "result has no void arm; a function that can only fail returns "
            "result<my_error>"
        ),
    ):
        compile_ir(
            DECL + "fn f() -> result<void, my_error> { return error(my_error::NOT_FOUND); }"
        )


def test_raw_value_into_error_constructor_is_rejected():
    with pytest.raises(
        LangError, match=re.escape("error value: expected my_error, got int32")
    ):
        compile_ir(DECL + "fn f() -> result<int32, my_error> { return error(5); }")


def test_wrong_declaration_into_error_constructor_is_rejected():
    with pytest.raises(
        LangError, match=re.escape("error value: expected my_error, got other")
    ):
        compile_ir(
            DECL
            + "error other { X }\n"
            + "fn f() -> result<int32, my_error> { return error(other::X); }"
        )


def test_constructor_arity_against_the_result_arity():
    with pytest.raises(
        LangError,
        match=re.escape(
            "ok() takes the ok value here: a result<int32, my_error> carries "
            "one (ok() with no value is for the error-only result<E>)"
        ),
    ):
        compile_ir(DECL + "fn f() -> result<int32, my_error> { return ok(); }")
    with pytest.raises(
        LangError,
        match=re.escape("a result<my_error> has no ok value; write ok()"),
    ):
        compile_ir(DECL + "fn f() -> result<my_error> { return ok(5); }")
    with pytest.raises(
        LangError,
        match=re.escape(
            "error() takes the error value, e.g. error(my_error::NOT_FOUND)"
        ),
    ):
        compile_ir(DECL + "fn f() -> result<int32, my_error> { return error(); }")


def test_constructor_needs_a_result_context():
    msg = (
        "ok(...) has no result type here; use it where one is expected -- "
        "a typed let, assignment, return, argument, or field"
    )
    with pytest.raises(LangError, match=re.escape(msg)):
        compile_ir(DECL + "fn f() { let r = ok(5); }")
    # A non-result expected type is the same miss: nothing implicit builds
    # a result, and no result unwraps implicitly.
    with pytest.raises(LangError, match=re.escape(msg)):
        compile_ir(DECL + "fn f() { let r: int32 = ok(5); }")


def test_no_implicit_wrap_or_unwrap():
    with pytest.raises(
        LangError,
        match=re.escape("return value: expected result<int32, my_error>, got int32"),
    ):
        compile_ir(DECL + "fn f() -> result<int32, my_error> { return 1; }")
    with pytest.raises(
        LangError,
        match=re.escape("return value: expected int32, got result<int32, my_error>"),
    ):
        compile_ir(
            DECL
            + "fn g() -> result<int32, my_error> { return ok(1); }\n"
            + "fn f() -> int32 { return g(); }"
        )


def test_a_result_exposes_no_fields():
    with pytest.raises(
        LangError, match=re.escape("a result<int32, my_error> has no fields")
    ):
        compile_ir(
            DECL
            + "fn f(r: result<int32, my_error>) -> uint8 { return r.tag; }"
        )
    with pytest.raises(
        LangError, match=re.escape("a result<int32, my_error> has no fields")
    ):
        compile_ir(
            DECL
            + "fn f() -> uint64 { return offsetof(result<int32, my_error>, tag); }"
        )


def test_a_result_is_not_a_struct_literal():
    with pytest.raises(
        LangError,
        match=re.escape(
            "a result<int32, my_error> is not built from a struct literal; "
            "construct it with ok(...) or error(...)"
        ),
    ):
        compile_ir(
            DECL
            + "fn f() -> result<int32, my_error> {"
            "    return struct result<int32, my_error> { };"
            "}"
        )


def test_a_result_destructures_into_two_binders():
    # Stage 2's form 1 replaced the stage-1 blanket reject: `let a, b = g();`
    # over a result binds the ok value and the error. The full battery lives
    # in the stage-2 section below.
    assert run(
        DECL
        + """
        fn g() -> result<int32, my_error> { return ok(42); }
        fn main() -> int32 {
            let a, b = g();
            if (b) { return 1; }
            return a;
        }
        """
    ) == 42


def test_no_const_or_static_result_globals():
    msg = (
        "a result is a runtime value; ok(...) cannot initialize a const or "
        "@static global"
    )
    with pytest.raises(LangError, match=re.escape(msg)):
        compile_ir(DECL + "const K = ok(5);")
    with pytest.raises(LangError, match=re.escape(msg)):
        compile_ir(DECL + "@static let g: result<int32, my_error> = ok(1);")


def test_mut_result_return_needs_an_lvalue():
    # ok(...) is an rvalue, so the mut-return lvalue discipline rejects it
    # with its normal wording -- no result-specific ban needed.
    with pytest.raises(
        LangError, match="a mut return must be formed from a mut or pointer"
    ):
        compile_ir(
            DECL + "fn f() -> mut result<int32, my_error> { return ok(1); }"
        )


def test_constructor_takes_a_single_value():
    with pytest.raises(LangError, match=re.escape("ok() takes a single value")):
        compile_ir(DECL + "fn f() -> result<int32, my_error> { return ok(1, 2); }")


# --------------------------------------------------- contextual spellings

def test_ok_and_error_stay_ordinary_identifiers():
    # Only the call shape `ok(` / `error(` is the constructor; the bare names
    # are variables like any other, and `@error(msg)` is untouched (its
    # directive tests live in test_directives.py).
    assert run(
        """
        fn main() -> int32 {
            let ok: int32 = 40;
            let error: int32 = 2;
            return ok + error;
        }
        """
    ) == 42


def test_error_declaration_head_needs_a_name():
    # `error(` at the top level is not a declaration; the introducer is only
    # claimed before an identifier, like the `type` alias head.
    with pytest.raises(LangError):
        compile_ir("error { A }")


def test_private_error_declaration_registers():
    assert run(
        "@private error e { A }\n"
        + """
        fn main() -> int32 {
            if (e::A == e::A) { return 42; }
            return 1;
        }
        """
    ) == 42


# ------------------------------------------------------------- .mci stubs

def test_interface_round_trips_error_and_result():
    src = (
        'error my_error {\n'
        '    NOT_FOUND = "Not Found",\n'
        '    PERMISSION = 5,\n'
        '    EXHAUSTED,\n'
        '}\n'
        '\n'
        'fn find(key: int32) -> result<int32, my_error> {\n'
        '    if (key == 0) { return error(my_error::NOT_FOUND); }\n'
        '    return ok(key);\n'
        '}\n'
    )
    stub = iface(src)
    # The declaration travels verbatim (display strings and explicit values
    # included); the concrete function becomes a prototype spelling the
    # result type.
    assert 'NOT_FOUND = "Not Found",' in stub
    assert "PERMISSION = 5," in stub
    assert "fn find(key: int32) -> result<int32, my_error>;" in stub
    # And the stub is valid source a consumer compiles against.
    consumer = stub + (
        "\nfn main() -> int32 {\n"
        "    let r = find(3);\n"
        "    let e = my_error::EXHAUSTED;\n"
        "    if (e == my_error::EXHAUSTED) { return 0; }\n"
        "    return 1;\n"
        "}\n"
    )
    compile_ir(consumer)


def test_interface_pulls_the_error_declaration_into_the_closure():
    # A public signature reaching my_error forces the declaration into the
    # stub even when nothing else names it.
    src = (
        "@private error hidden { OOPS }\n"
        "fn f() -> result<hidden> { return error(hidden::OOPS); }\n"
    )
    stub = iface(src)
    assert "@private error hidden { OOPS }" in stub
    assert "fn f() -> result<hidden>;" in stub


# ------------------------------------------------------- drive-by: void args

def test_void_generic_type_argument_is_a_frontend_error():
    # Previously a raw LLVM verifier crash; now a compile error (and the same
    # guard is why result<void, E> can never sneak in through a generic).
    with pytest.raises(
        LangError,
        match=re.escape("struct 'box' cannot take void as a type argument"),
    ):
        compile_ir(
            "struct box<T> { v: T; }\n"
            "fn f() { let b: box<void>; }"
        )


# ======================================================= stage 2: form 1

FIND = (
    "fn find(key: int32) -> result<int64, my_error> {\n"
    "    if (key == 0) { return error(my_error::NOT_FOUND); }\n"
    "    return ok((key * 2) as int64);\n"
    "}\n"
)

FLUSH = (
    "fn flush(fail: int32) -> result<my_error> {\n"
    "    if (fail) { return error(my_error::PERMISSION); }\n"
    "    return ok();\n"
    "}\n"
)


def test_form1_ok_arm_binds_value_and_the_zero_error_state():
    # On success `err` is the reserved zero state: falsy under `if (err)`
    # and zero through the explicit read-out.
    assert run(
        DECL + FIND
        + """
        fn main() -> int32 {
            let ret, err = find(21);
            if (err) { return 1; }
            if (err as int32 != 0) { return 2; }
            return ret as int32;
        }
        """
    ) == 42


def test_form1_error_arm_binds_error_and_zero_fills_the_value():
    # On failure `ret` is the zero value of T -- a zero-FILL, never the
    # stored error arm's bytes reinterpreted (those are 1 here, not 0).
    assert run(
        DECL + FIND
        + """
        fn main() -> int32 {
            let ret, err = find(0);
            if (err != my_error::NOT_FOUND) { return 1; }
            if (ret != 0) { return 2; }
            return 42;
        }
        """
    ) == 42


def test_form1_lowers_as_a_tag_select_not_a_union_pun():
    # The IR shape is contract: both binders zero-fill, the tag is tested,
    # and each arm's payload load happens only under its own branch -- the
    # ok arm's bytes are never read when the tag says error.
    ir_text = compile_ir(
        DECL + FIND
        + "fn f() -> int64 {\n"
        "    let ret, err = find(1);\n"
        "    if (err) { return -1; }\n"
        "    return ret;\n"
        "}\n"
    )
    assert '"destructure.tag"' in ir_text
    assert "destructure.ok:" in ir_text
    assert "destructure.err:" in ir_text
    # Zero-fills for both binder slots (i64 T, i32-backed E).
    assert 'store i64 0, i64* %"ret"' in ir_text
    assert 'store i32 0, i32* %"err"' in ir_text
    # No unconditional select over both loaded arms.
    assert "select i1" not in ir_text


def test_form1_zero_fill_when_t_is_a_struct():
    # A struct T zero-fills on the error arm (every field reads zero).
    assert run(
        DECL
        + """
        struct point { x: int32; y: int32; }
        fn make(fail: int32) -> result<point, my_error> {
            if (fail) { return error(my_error::EXHAUSTED); }
            return ok(point { x = 3, y = 4 });
        }
        fn main() -> int32 {
            let p, err = make(1);
            if (err != my_error::EXHAUSTED) { return 1; }
            if (p.x != 0 or p.y != 0) { return 2; }
            let q, e2 = make(0);
            if (e2) { return 3; }
            return q.x * 10 + q.y;
        }
        """
    ) == 34


def test_form1_rejects_the_error_only_result():
    with pytest.raises(
        LangError,
        match=re.escape(
            "cannot destructure result<my_error>: it has no ok value; "
            "handle it with except: try f() except (err) { ... };"
        ),
    ):
        compile_ir(
            DECL + FLUSH + "fn f() { let ret, err = flush(1); }"
        )


def test_form1_takes_exactly_two_binders():
    with pytest.raises(
        LangError,
        match=re.escape(
            "cannot destructure result<int64, my_error> into 3 binders (it "
            "binds a value and an error: let ret, err = f();)"
        ),
    ):
        compile_ir(DECL + FIND + "fn f() { let a, b, c = find(1); }")


def test_form1_takes_no_rest_binder():
    with pytest.raises(
        LangError,
        match=re.escape(
            "cannot destructure result<int64, my_error> into 2 binders and "
            "a rest (it binds a value and an error: let ret, err = f();)"
        ),
    ):
        compile_ir(DECL + FIND + "fn f() { let a, b... = find(1); }")


def test_form1_duplicate_binder_rejects():
    with pytest.raises(
        LangError,
        match=re.escape("variable 'a' already declared in this scope"),
    ):
        compile_ir(DECL + FIND + "fn f() { let a, a = find(1); }")


def test_form1_leaves_tuple_destructuring_unchanged():
    # The result arm sits before the tuple arm; tuples keep every behavior,
    # rest binder included.
    assert run(
        DECL + FIND
        + """
        fn pair() -> tuple<int32, int32> { return (40, 2); }
        fn main() -> int32 {
            let a, b = pair();
            let first, rest... = (1, 2, 3);
            let ret, err = find(1);
            if (err) { return -1; }
            return a + b + first + len(rest) as int32
                + ret as int32 - 5;
        }
        """
    ) == 42


# ================================================ stage 2: form A (except)

def test_except_emit_fallback_skips_else():
    # The one corner ruled prominently: a fallback is not an ok, so `else`
    # does NOT run on the emit path -- but code after does, ret = fallback.
    assert run(
        DECL + FIND
        + """
        fn main() -> int32 {
            let mark: int32 = 0;
            let v = try find(0) except (err) { emit -7; } else { mark = 1; };
            if (mark != 0) { return 1; }
            if (v != -7) { return 2; }
            return 42;
        }
        """
    ) == 42


def test_except_else_runs_on_ok_with_ret_in_scope():
    # `else` is the ok arm; ret is live inside it and after the statement.
    assert run(
        DECL + FIND
        + """
        fn main() -> int32 {
            let mark: int32 = 0;
            let v = try find(5) except (err) { emit 0; } else { mark = v as int32; };
            if (mark != 10) { return 1; }
            return (v as int32) * 4 + 2;
        }
        """
    ) == 42


def test_except_diverging_handler_return():
    assert run(
        DECL + FIND
        + """
        fn get(key: int32) -> int32 {
            let v = try find(key) except (err) { return -1; };
            return v as int32;
        }
        fn main() -> int32 {
            if (get(0) != -1) { return 1; }
            return get(21);
        }
        """
    ) == 42


def test_except_diverging_handler_break_and_continue():
    # break/continue count as divergence; they target the enclosing loop.
    assert run(
        DECL + FIND
        + """
        fn main() -> int32 {
            let sum: int32 = 0;
            let i: int32 = 3;
            while (i >= 0) {
                i = i - 1;
                let v = try find(i + 1) except (err) { break; };
                sum = sum + v as int32;
            }
            return sum;  // 6 + 4 + 2, then find(0) breaks
        }
        """
    ) == 12


def test_except_handler_must_diverge_or_emit_in_value_position():
    with pytest.raises(
        LangError,
        match=re.escape(
            "the except handler may fall through without a value; emit a "
            "fallback or diverge (return, break, continue, panic)"
        ),
    ):
        compile_ir(
            DECL + FIND
            + "fn f() -> int64 {\n"
            "    let v = try find(1) except (err) { let x: int32 = 1; };\n"
            "    return v;\n"
            "}\n"
        )


def test_try_needs_a_result():
    with pytest.raises(
        LangError,
        match=re.escape("try needs a result value, got int32"),
    ):
        compile_ir(
            DECL
            + "fn g() -> int32 { return 1; }\n"
            "fn f() { let v = try g() except (err) { emit 0; }; }"
        )


def test_try_composes_as_an_operand():
    # `try` sits at unary level, so the whole try...except is an ordinary
    # operand: it composes into a larger expression and into a call
    # argument (the plain value form -- diverge-or-emit still holds).
    assert run(
        DECL + FIND
        + """
        fn use(v: int64) -> int64 { return v + 2; }
        fn main() -> int32 {
            let a = 1 as int64 + try find(0) except (err) { emit 39; };
            let b = use(try find(0) except (e) { emit 0; });
            return (a + b) as int32;
        }
        """
    ) == 42


def test_try_binds_the_call_chain():
    # With bare try legal, `try g() + 1` is `(try g()) + 1` -- try binds the
    # call chain, and a handler displaced past the `+ 1` has no try left to
    # attach to: the parse error at `except` names the fix.
    with pytest.raises(
        LangError,
        match=re.escape("except needs try: try f() except (err) { ... }"),
    ):
        compile_ir(
            DECL + FIND
            + "fn f() -> int64 {\n"
            "    let v = try find(1) + 1 except (err) { emit 0; };\n"
            "    return v;\n"
            "}\n"
        )


def test_except_without_try_names_the_fix():
    # The old un-prefixed attachment is gone; at the statement heads the
    # error names the try spelling instead of a bare "expected ';'".
    msg = "except needs try: try f() except (err) { ... }"
    with pytest.raises(LangError, match=re.escape(msg)):
        compile_ir(
            DECL + FIND
            + "fn f() -> int64 { let v = find(1) except (err) { emit 0; }; }"
        )
    with pytest.raises(LangError, match=re.escape(msg)):
        compile_ir(
            DECL + FIND
            + "fn f() -> int64 { return find(1) except (err) { emit 0; }; }"
        )
    with pytest.raises(LangError, match=re.escape(msg)):
        compile_ir(
            DECL + FIND
            + "fn f() { find(1) except (err) { }; }"
        )


def test_try_value_form_must_produce_a_value():
    # Nested in a larger expression, a diverging handler plus a diverging
    # else would leave no path delivering the operand's value.
    with pytest.raises(
        LangError,
        match=re.escape(
            "this try expression never produces a value: the handler and "
            "the else block both diverge"
        ),
    ):
        compile_ir(
            DECL + FIND
            + "fn f() -> int64 {\n"
            "    return 1 + try find(1) except (err) { return -1; }\n"
            "        else { return -2; };\n"
            "}\n"
        )


def test_except_let_rejects_the_error_only_result():
    with pytest.raises(
        LangError,
        match=re.escape(
            "a result<my_error> has no ok value to bind; handle it in "
            "statement position: try f() except (err) { ... };"
        ),
    ):
        compile_ir(
            DECL + FLUSH
            + "fn f() { let v = try flush(1) except (err) { emit 0; }; }"
        )


def test_except_emit_coerces_to_the_ok_type():
    with pytest.raises(
        LangError,
        match=re.escape("emit: expected int64, got bool"),
    ):
        compile_ir(
            DECL + FIND
            + "fn f() -> int64 {\n"
            "    let v = try find(1) except (err) { emit true; };\n"
            "    return v;\n"
            "}\n"
        )


def test_except_emit_takes_adapted_literals():
    # The handler's emit is a typed sink: a bare struct literal builds T.
    assert run(
        DECL
        + """
        struct point { x: int32; y: int32; }
        fn make(fail: int32) -> result<point, my_error> {
            if (fail) { return error(my_error::NOT_FOUND); }
            return ok(point { x = 1, y = 2 });
        }
        fn main() -> int32 {
            let p = try make(1) except (err) { emit { x = 40, y = 2 }; };
            return p.x + p.y;
        }
        """
    ) == 42


def test_except_binder_is_scoped_to_the_handler():
    with pytest.raises(
        LangError, match=re.escape("undefined variable 'err'")
    ):
        compile_ir(
            DECL + FIND
            + "fn f() -> int32 {\n"
            "    let v = try find(1) except (err) { emit 0; };\n"
            "    return err as int32;\n"
            "}\n"
        )


def test_except_binder_shadows_and_restores():
    # The binder may shadow an outer name inside the handler; the outer
    # binding is untouched after the statement.
    assert run(
        DECL + FIND
        + """
        fn main() -> int32 {
            let err: int32 = 40;
            let v = try find(0) except (err) { emit err as int32 as int64; };
            return err + v as int32 + 1;  // 40 + 1 (NOT_FOUND) + 1
        }
        """
    ) == 42


def test_except_handler_cannot_read_the_let_name():
    # The let's name binds after the initializer, handler included.
    with pytest.raises(
        LangError, match=re.escape("undefined variable 'v'")
    ):
        compile_ir(
            DECL + FIND
            + "fn f() {\n"
            "    let v = try find(1) except (err) { emit v; };\n"
            "}\n"
        )


def test_except_annotated_let_coerces_the_ok_value():
    with pytest.raises(
        LangError, match=re.escape("let v: expected int32, got int64")
    ):
        compile_ir(
            DECL + FIND
            + "fn f() { let v: int32 = try find(1) except (err) { emit 0; }; }"
        )


def test_except_nested_inside_a_handler():
    # A handler is an ordinary block: a nested let-except works, and each
    # emit targets its own clause.
    assert run(
        DECL + FIND
        + """
        fn main() -> int32 {
            let v = try find(0) except (err) {
                let w = try find(21) except (inner) { emit 0; };
                emit w;
            };
            return v as int32;
        }
        """
    ) == 42


def test_except_nested_block_expression_keeps_its_own_emit():
    # A block expression inside the handler takes the inner emit; the
    # handler's own emit still fills the fallback slot.
    assert run(
        DECL + FIND
        + """
        fn main() -> int32 {
            let v = try find(0) except (err) {
                let base: int32 = { emit 40; };
                emit (base + 2) as int64;
            };
            return v as int32;
        }
        """
    ) == 42


def test_except_in_a_generic_function():
    assert run(
        DECL + FIND
        + """
        fn get_or<T>(fallback: T) -> result<T, my_error> {
            return error(my_error::NOT_FOUND);
        }
        fn unwrap<T>(fallback: T) -> T {
            let v = try get_or(fallback) except (err) { emit fallback; };
            return v;
        }
        fn main() -> int32 {
            if (unwrap(true) != true) { return 1; }
            return unwrap(42);
        }
        """
    ) == 42


# --------------------------------------------- stage 2: return position

def test_return_except_both_arms(capfd):
    # On ok the payload returns (after the else block runs); on error the
    # handler's fallback does. The else is the ok arm only.
    assert run(
        'import "std/io";\n'
        + DECL + FIND
        + """
        fn get(key: int32) -> int64 {
            return try find(key) except (err) { emit -1; }
                   else { println("ok"); };
        }
        fn main() -> int32 {
            if (get(0) != -1) { return 1; }
            if (get(21) != 42) { return 2; }
            return 0;
        }
        """
    ) == 0
    out, _ = capfd.readouterr()
    assert out == "ok\n"  # once: the error path skipped the else


def test_return_except_diverging_handler():
    assert run(
        DECL + FIND
        + """
        fn get(key: int32) -> int64 {
            return try find(key) except (err) { return -9; };
        }
        fn main() -> int32 {
            if (get(0) != -9) { return 1; }
            return get(21) as int32;
        }
        """
    ) == 42


def test_return_except_rejects_the_error_only_result():
    with pytest.raises(
        LangError,
        match=re.escape(
            "a result<my_error> has no ok value to bind; handle it in "
            "statement position: try f() except (err) { ... };"
        ),
    ):
        compile_ir(
            DECL + FLUSH
            + "fn f() -> int32 { return try flush(1) except (err) { emit 0; }; }"
        )


def test_propagation_idiom():
    # `return error(err);` in a function returning result<T2, E> with the
    # same E -- explicit construction, no implicit coercion.
    assert run(
        DECL + FIND
        + """
        fn wrap(key: int32) -> result<int32, my_error> {
            let v = try find(key) except (err) { return error(err); };
            return ok(v as int32);
        }
        fn main() -> int32 {
            let a, e1 = wrap(21);
            if (e1) { return 1; }
            let b, e2 = wrap(0);
            if (e2 != my_error::NOT_FOUND) { return 2; }
            if (b != 0) { return 3; }
            return a;
        }
        """
    ) == 42


def test_propagation_to_a_different_error_type_needs_a_mapping():
    # A different E2 does not absorb E: error(err) rejects at the
    # constructor with the normal arm coercion error.
    with pytest.raises(
        LangError,
        match=re.escape("error value: expected other_error, got my_error"),
    ):
        compile_ir(
            DECL + FIND
            + "error other_error { BOOM }\n"
            "fn wrap(key: int32) -> result<int32, other_error> {\n"
            "    let v = try find(key) except (err) { return error(err); };\n"
            "    return ok(v as int32);\n"
            "}\n"
        )


# ------------------------------------------ stage 2: statement position

def test_statement_except_handles_the_error_only_result():
    # The result<E> consumer: obligation-free handler, else = ok arm.
    assert run(
        DECL + FLUSH
        + """
        fn main() -> int32 {
            let seen: int32 = 0;
            try flush(1) except (err) { seen = err as int32; };
            if (seen != 2) { return 1; }
            try flush(0) except (err) { seen = -1; } else { seen = 40; };
            return seen + 2;
        }
        """
    ) == 42


def test_statement_except_handler_may_fall_through():
    # Obligation-free: nothing escapes, so a fall-through handler is legal
    # and code after runs on both paths.
    assert run(
        DECL + FIND
        + """
        fn main() -> int32 {
            try find(0) except (err) { let note: int32 = 1; };
            return 42;
        }
        """
    ) == 42


def test_statement_except_arity_two_emit_is_discarded():
    # Over a two-arm result an emit still coerces to T (uniform semantics);
    # the value simply goes nowhere.
    assert run(
        DECL + FIND
        + """
        fn main() -> int32 {
            try find(0) except (err) { emit -1; };
            return 42;
        }
        """
    ) == 42


def test_statement_except_error_only_emit_rejects():
    with pytest.raises(
        LangError,
        match=re.escape(
            "a result<my_error> has no ok value; the except handler has "
            "nothing to emit"
        ),
    ):
        compile_ir(
            DECL + FLUSH
            + "fn f() { try flush(1) except (err) { emit 0; }; }"
        )


def test_statement_except_all_paths_diverging_diverges():
    # When the handler and a diverging else close both arms, the statement
    # diverges -- and the dead-code class names it correctly.
    src = (
        DECL + FIND
        + "fn f() -> int32 {\n"
        "    try find(1) except (err) { return -1; } else { return 1; };\n"
        "    let dead: int32 = 0;\n"
        "    return dead;\n"
        "}\n"
    )
    cg = CodeGen(Parser(tokenize(src)).parse_program(), "test")
    cg.generate()
    dead = [w for w in cg.warnings if w.wclass == "dead-code"]
    assert [w.message for w in dead] == [
        "unreachable code: every path through the statement above diverges"
    ]


def test_statement_except_else_is_reachable_no_dead_code_misfire():
    # `f() except (err) { return 1; } else { ... }`: the else is the ok arm
    # and perfectly reachable; -Wdead-code must not fire.
    src = (
        DECL + FIND
        + "fn f() -> int32 {\n"
        "    try find(1) except (err) { return -1; } else { let ok_note: int32 = 1; };\n"
        "    return 0;\n"
        "}\n"
    )
    cg = CodeGen(Parser(tokenize(src)).parse_program(), "test")
    cg.generate()
    assert [w for w in cg.warnings if w.wclass == "dead-code"] == []


# ------------------------------------------------ stage 2: defer interplay

def test_except_emit_inside_a_defer_is_legal():
    # The documented carve-out: the emit targets the except clause, a block
    # expression opened inside the defer body.
    assert run(
        DECL + FIND
        + """
        fn main() -> int32 {
            let seen: int32 = 0;
            {
                defer {
                    let v = try find(0) except (err) { emit 42; };
                    seen = v as int32;
                }
                seen = 1;
            }
            return seen;
        }
        """
    ) == 42


def test_except_return_inside_a_defer_stays_banned():
    # A handler whose path returns is, inside a defer body, the existing
    # defer-escape ban -- no new rule.
    with pytest.raises(
        LangError,
        match=re.escape(
            "'return' inside a defer body cannot exit the enclosing function"
        ),
    ):
        compile_ir(
            DECL + FIND
            + "fn f() -> int64 {\n"
            "    defer { let v = try find(1) except (err) { return -1; }; }\n"
            "    return 0;\n"
            "}\n"
        )


def test_except_break_inside_a_defer_stays_banned():
    with pytest.raises(
        LangError,
        match=re.escape(
            "'break' inside a defer body cannot exit the enclosing loop"
        ),
    ):
        compile_ir(
            DECL + FIND
            + "fn f() {\n"
            "    while (true) {\n"
            "        defer { try find(1) except (err) { break; }; }\n"
            "    }\n"
            "}\n"
        )


# ------------------------------------------------- stage 2: parse contract

def test_except_handler_requires_braces():
    with pytest.raises(
        LangError,
        match=re.escape(
            "an except handler is a braced block, as in "
            "'try f() except (err) { ... }'"
        ),
    ):
        compile_ir(
            DECL + FIND
            + "fn f() { let v = try find(1) except (err) emit 0; }"
        )


def test_except_else_requires_braces():
    with pytest.raises(
        LangError,
        match=re.escape(
            "an except else is a braced block, as in "
            "'try f() except (err) { ... } else { ... }'"
        ),
    ):
        compile_ir(
            DECL + FIND
            + "fn f() -> int32 {\n"
            "    try find(1) except (err) { return -1; } else return 0;\n"
            "}\n"
        )


def test_destructuring_let_takes_no_except():
    with pytest.raises(
        LangError,
        match=re.escape(
            "a destructuring let does not take an except handler; bind the "
            "value alone (let ret = try f() except (err) { ... };) or test the "
            "error (let ret, err = f();)"
        ),
    ):
        compile_ir(
            DECL + FIND
            + "fn f() { let a, b = try find(1) except (err) { emit 0; }; }"
        )


def test_except_is_a_reserved_word():
    with pytest.raises(LangError):
        compile_ir("fn f() { let except: int32 = 1; }")


def test_try_is_a_reserved_word():
    with pytest.raises(LangError):
        compile_ir("fn f() { let try: int32 = 1; }")


# --------------------------------------------- stage 3: bare-try propagation

def test_bare_try_propagates_both_arms():
    # `try g()` yields T on ok; on error the enclosing function returns
    # `error(err)` -- the caller observes the same error.
    assert run(
        DECL + FIND
        + """
        fn wrap(key: int32) -> result<int64, my_error> {
            let v = try find(key);
            return ok(v + 1);
        }
        fn main() -> int32 {
            let a, e1 = wrap(3);
            if (e1) { return 1; }
            let b, e2 = wrap(0);
            if (e2 as int32 != 1) { return 2; }
            return (a - 7 + 42) as int32;
        }
        """
    ) == 42


def test_bare_try_composes_as_an_operand():
    # Unary level, like the except form: `1 + try g()` and an argument.
    assert run(
        DECL + FIND
        + """
        fn use(v: int64) -> int64 { return v + 2; }
        fn wrap() -> result<int64, my_error> {
            let a = 1 as int64 + try find(3);   // 1 + 6
            let b = use(try find(5));           // 10 + 2
            return ok(a + b);
        }
        fn main() -> int32 {
            let v, err = wrap();
            return (v + 23) as int32;           // 19 + 23
        }
        """
    ) == 42


def test_bare_try_requires_an_absorbing_return_type():
    # The enclosing return type must be a result carrying the SAME declared
    # error type; main() -> int32 is not one. The error names both types.
    with pytest.raises(
        LangError,
        match=re.escape(
            "try propagates my_error, but this function returns int32"
        ),
    ):
        compile_ir(
            DECL + FIND
            + "fn main() -> int32 { let v = try find(1); return 0; }"
        )
    with pytest.raises(
        LangError,
        match=re.escape(
            "try propagates my_error, but this function returns void"
        ),
    ):
        compile_ir(DECL + FIND + "fn f() { let v = try find(1); }")


def test_bare_try_between_two_error_types_rejects():
    # No error conversions exist (mapping is a handler's job): a result
    # return type with a DIFFERENT declared error type does not absorb.
    with pytest.raises(
        LangError,
        match=re.escape(
            "try propagates my_error, but this function returns "
            "result<int64, other_error>"
        ),
    ):
        compile_ir(
            DECL + "error other_error { OOPS }\n" + FIND
            + "fn wrap(k: int32) -> result<int64, other_error> {\n"
            "    let v = try find(k);\n"
            "    return ok(v);\n"
            "}\n"
        )


def test_return_try_is_not_implicitly_wrapped():
    # `return try g();` yields a bare T where the result is expected; there
    # is no implicit value-to-result coercion, so the correct spelling is
    # `return ok(try g());`.
    with pytest.raises(
        LangError,
        match=re.escape(
            "return value: expected result<int64, my_error>, got int64"
        ),
    ):
        compile_ir(
            DECL + FIND
            + "fn wrap(k: int32) -> result<int64, my_error> {\n"
            "    return try find(k);\n"
            "}\n"
        )
    # The wrapped spelling works, both arms.
    assert run(
        DECL + FIND
        + """
        fn wrap(k: int32) -> result<int64, my_error> {
            return ok(try find(k));
        }
        fn main() -> int32 {
            let v, err = wrap(21);
            let v2, err2 = wrap(0);
            if (err or !(err2 as int32 == 1)) { return 1; }
            return v as int32;
        }
        """
    ) == 42


def test_bare_try_on_the_error_only_result_needs_statement_position():
    # A result<E> has no payload for `try` to yield in value position.
    with pytest.raises(
        LangError,
        match=re.escape(
            "a result<my_error> has no ok value; propagate it in statement "
            "position: try f();"
        ),
    ):
        compile_ir(
            DECL + FLUSH
            + "fn f() -> result<my_error> {\n"
            "    let x = try flush(1);\n"
            "    return ok();\n"
            "}\n"
        )


def test_bare_try_statement_propagates_the_error_only_result():
    # `try f();` in statement position: propagate-or-continue, the
    # error-only consumer.
    assert run(
        DECL + FLUSH
        + """
        fn run_it(fail: int32) -> result<my_error> {
            try flush(fail);
            return ok();
        }
        fn main() -> int32 {
            let e1 = run_it(0);
            let e2 = run_it(1);
            let bad, err = find_state(e1, e2);
            return bad;
        }
        fn find_state(a: result<my_error>, b: result<my_error>)
                -> result<int32, my_error> {
            try a;                       // ok: continues
            let out: int32 = 42;
            try (x = probe(b)) { out = -1; } except (err) {
                if (err as int32 != 2) { out = -2; }
            }
            return ok(out);
        }
        fn probe(r: result<my_error>) -> result<int32, my_error> {
            try r;                       // propagates b's PERMISSION
            return ok(1);
        }
        """
    ) == 42


def test_bare_try_statement_discards_the_ok_value():
    # Over an arity-2 result, statement-position `try g();` propagates on
    # error and discards T on ok, like any expression statement's value.
    assert run(
        DECL + FIND
        + """
        fn peek(k: int32) -> result<int64, my_error> {
            try find(k);
            return ok(42 as int64);
        }
        fn main() -> int32 {
            let v, err = peek(3);
            return v as int32;
        }
        """
    ) == 42


def test_bare_try_inside_a_defer_is_banned():
    # Propagation returns out of the enclosing function -- exactly what a
    # defer body cannot do; the ban names the construct the user wrote.
    with pytest.raises(
        LangError,
        match=re.escape(
            "try propagation inside a defer body cannot exit the enclosing "
            "function; handle the error with except"
        ),
    ):
        compile_ir(
            DECL + FLUSH
            + "fn f() -> result<my_error> {\n"
            "    defer { try flush(1); }\n"
            "    return ok();\n"
            "}\n"
        )


def test_bare_try_in_a_generic_function():
    # Propagation inside a monomorphized body: the absorb check runs per
    # instantiation against the instantiated return type.
    assert run(
        DECL + FIND
        + """
        fn pass<T>(r: result<T, my_error>) -> result<T, my_error> {
            return ok(try r);
        }
        fn main() -> int32 {
            let v, err = pass(find(21));
            if (err) { return -1; }
            return v as int32;
        }
        """
    ) == 42


# ------------------------------------------------- stage 3: the ?? fallback

def test_fallback_supplies_the_default_on_error():
    # On ok the payload; on error the fallback, coerced to T (the untyped
    # literal adapts to int64). Legal in main: no absorb requirement.
    assert run(
        DECL + FIND
        + """
        fn main() -> int32 {
            let a = try find(3) ?? 100;    // ok arm: 6
            let b = try find(0) ?? 36;     // error arm: the default
            return (a + b) as int32;
        }
        """
    ) == 42


def test_fallback_is_lazy():
    # The fallback evaluates only on the error path: side effects must not
    # run when the call succeeds.
    assert run(
        DECL + FIND
        + """
        @static let hits: int32;
        fn bump() -> int64 { hits = hits + 1; return 40; }
        fn main() -> int32 {
            let a = try find(3) ?? bump();   // ok: bump must NOT run
            let b = try find(0) ?? bump();   // error: bump runs once
            return hits + (a - 5 + b) as int32;   // 1 + 1 + 40
        }
        """
    ) == 42


def test_fallback_emit_block():
    # `?? { ...; emit v; }` runs statements on the error path and emits the
    # fallback.
    assert run(
        DECL + FIND
        + """
        fn main() -> int32 {
            let note: int32 = 0;
            let v = try find(0) ?? { note = 2; emit 40; };
            return (v as int32) + note;
        }
        """
    ) == 42


def test_fallback_emit_block_may_diverge():
    # All paths diverging is legal: the ok arm still delivers the value.
    assert run(
        DECL + FIND
        + """
        fn get(k: int32) -> int32 {
            let v = try find(k) ?? { return -1; };
            return v as int32;
        }
        fn main() -> int32 {
            if (get(0) != -1) { return 1; }
            return get(21);
        }
        """
    ) == 42


def test_fallback_block_must_emit_or_diverge():
    with pytest.raises(
        LangError,
        match=re.escape(
            "the '??' fallback block may fall through without a value; "
            "emit the fallback or diverge (return, break, continue, panic)"
        ),
    ):
        compile_ir(
            DECL + FIND
            + "fn f() -> int32 {\n"
            "    let v = try find(1) ?? { let note: int32 = 1; };\n"
            "    return v as int32;\n"
            "}\n"
        )


def test_fallback_coerces_to_the_ok_type():
    with pytest.raises(
        LangError,
        match=re.escape("'??' fallback: expected int64, got bool"),
    ):
        compile_ir(
            DECL + FIND
            + "fn f() -> int64 { return try find(1) ?? true; }"
        )


def test_fallback_takes_atomic_forms():
    # A sample of fallback right-hand sides: a call, prefix forms, a member
    # chain, a parenthesized expression. (Each is parenthesized here so the
    # trailing `as int32` casts the unwrapped value, not the fallback -- see
    # test_operating_on_the_unwrapped_value_requires_parens.)
    assert run(
        DECL + FIND
        + """
        struct pair { a: int64; b: int64; }
        fn fallback() -> int64 { return 30; }
        fn main() -> int32 {
            let p = struct pair { a = 3, b = 4 };
            let s: int32 = 0;
            s = s + (try find(0) ?? fallback()) as int32;   // 30
            s = s + (try find(0) ?? -1) as int32;           // 29
            s = s + (try find(0) ?? ~0) as int32;           // 28
            s = s + (try find(0) ?? p.a) as int32;          // 31
            s = s + (try find(0) ?? (5 + 6)) as int32;      // 42
            return s;
        }
        """
    ) == 42


def test_fallback_rejects_the_error_only_result():
    with pytest.raises(
        LangError,
        match=re.escape(
            "a result<my_error> has no ok value to default; handle it "
            "(try f() except (err) { ... };) or propagate it (try f();)"
        ),
    ):
        compile_ir(
            DECL + FLUSH
            + "fn f() { let x = try flush(1) ?? 0; }"
        )


def test_fallback_and_except_do_not_combine():
    # A try takes exactly one ending.
    with pytest.raises(
        LangError,
        match=re.escape(
            "a try takes one ending -- nothing (propagate), '?? fallback' "
            "(default), or 'except (err) { ... }' (handle) -- not two"
        ),
    ):
        compile_ir(
            DECL + FIND
            + "fn f() { let x = try find(1) ?? 1 except (e) { }; }"
        )


def test_fallback_inside_a_defer_is_legal():
    # Nothing escapes the expression, so the defer bans have nothing to say.
    assert run(
        DECL + FIND
        + """
        fn main() -> int32 {
            let seen: int32 = 0;
            { defer { seen = (try find(0) ?? 42) as int32; } }
            return seen;
        }
        """
    ) == 42


# ------------------------------------ stage 3: ?? precedence (pinned rulings)

def test_fallback_precedence_groupings():
    # `??` is the lowest-precedence expression form: looser than the ternary
    # and every binary operator, and right-associative, so its fallback
    # extends greedily to the end of the expression. Pinned structurally
    # (the exact groupings the parser builds -- runnable value proofs follow
    # in the tests below):
    from mcc.nodes import Binary, Coalesce, Ternary, TryFallback

    def init(expr):
        src = "fn f() { let x = " + expr + "; }"
        return Parser(tokenize(src)).parse_program().functions[0].body[0].value

    # `try f() ?? 2 + 1` is `try f() ?? (2 + 1)`.
    n = init("try find(1) ?? 2 + 1")
    assert isinstance(n, TryFallback) and isinstance(n.fallback, Binary)
    assert n.fallback.op == "+"

    # `try f() ?? c ? a : b` is `try f() ?? (c ? a : b)`.
    n = init("try find(1) ?? c ? a : b")
    assert isinstance(n, TryFallback) and isinstance(n.fallback, Ternary)

    # `try f() ?? p ?? q + 1` is `try f() ?? (p ?? (q + 1))` -- right-assoc,
    # and `q + 1` binds tighter than the inner `??`.
    n = init("try find(1) ?? p ?? q + 1")
    assert isinstance(n, TryFallback) and isinstance(n.fallback, Coalesce)
    inner = n.fallback
    assert isinstance(inner.rhs, Binary) and inner.rhs.op == "+"

    # `try f() ?? v > p ?? q` is `try f() ?? ((v > p) ?? q)` -- the
    # comparison binds tighter than `??`, then the `?? q` chains right over
    # it. (This is the grouping "?? looser than binary" forces; it is NOT
    # `v > (p ?? q)`.)
    n = init("try find(1) ?? v > p ?? q")
    assert isinstance(n, TryFallback) and isinstance(n.fallback, Coalesce)
    assert isinstance(n.fallback.lhs, Binary) and n.fallback.lhs.op == ">"

    # Bare (unparenthesized) general coalesce also chains right:
    # `p ?? q ?? r` is `p ?? (q ?? r)`.
    n = init("p ?? q ?? r")
    assert isinstance(n, Coalesce) and isinstance(n.rhs, Coalesce)


def test_fallback_binds_looser_than_the_ternary():
    # Runnable proof: `try find(1) ?? 0 ? 40 : 2` is
    # `try find(1) ?? (0 ? 40 : 2)`. find(1) is ok(2), so the ternary
    # fallback is skipped -> 2. (Under the old tighter rule this was
    # `(try find(1) ?? 0) ? 40 : 2` = 40, so the value distinguishes the
    # groupings.) On the error path the ternary IS the fallback.
    assert run(
        DECL + FIND
        + """
        fn main() -> int32 {
            let hi: int64 = 40;
            let lo: int64 = 2;
            let a = try find(1) ?? 0 ? hi : lo;   // ok(2), ternary skipped -> 2
            let b = try find(0) ?? 1 ? hi : lo;   // error -> (1 ? 40 : 2) = 40
            return (a + b) as int32;              // 2 + 40 = 42
        }
        """                                       # old rule made a = 40 too
    ) == 42


def test_fallback_binds_looser_than_binary_operators():
    # Runnable proof: `try find(...) ?? 2 - 1` is `try find(...) ?? (2 - 1)`.
    assert run(
        DECL + FIND
        + """
        fn main() -> int32 {
            let a = try find(0) ?? 2 - 1;      // error -> (2 - 1) = 1
            let b = try find(0) ?? (2 - 1);    // 1 (explicit)
            let c = try find(20) ?? 2 - 1;     // ok(40), fallback skipped -> 40
            return (a + b + c) as int32;       // 1 + 1 + 40 = 42
        }
        """                                    # old rule made c = (40) - 1 = 39
    ) == 42


def test_operating_on_the_unwrapped_value_requires_parens():
    # The fallback extends greedily, so `try f() ?? 0 + base` is
    # `try f() ?? (0 + base)`. To add to the UNWRAPPED value, parenthesize:
    # `(try f() ?? 0) + base`.
    assert run(
        DECL + FIND
        + """
        fn main() -> int32 {
            let base: int64 = 10;
            let bare = try find(20) ?? 0 + base;      // ok(40), skipped -> 40
            let wrapped = (try find(20) ?? 0) + base; // 40 + 10 = 50
            let err = try find(0) ?? 0 + base;        // error -> (0 + 10) = 10
            if (bare == 40 and wrapped == 50 and err == 10) { return 42; }
            return 0;
        }
        """
    ) == 42


def test_fallback_chains_right_into_the_general_coalesce():
    # Pinned: `try g() ?? p ?? q` is `try g() ?? (p ?? q)` (right-assoc).
    # The try owns its first `??`; the inner `p ?? q` is the general
    # coalesce, whose pointer arm rejects until the pointer item ships. The
    # pointer message (not a fallback-type error) proves the RHS grouped as
    # `(p ?? q)`: under the old left-assoc rule the try's `??` would have
    # tried a pointer as an int64 fallback instead.
    with pytest.raises(
        LangError,
        match=re.escape(
            "'??' on a pointer is null coalescing, which lands with the "
            "pointer-truthiness roadmap item; spell the test for now: "
            "p == null ? q : p"
        ),
    ):
        compile_ir(
            DECL + FIND
            + "fn f() {\n"
            "    let p: int64* = null;\n"
            "    let q: int64* = null;\n"
            "    let x = try find(1) ?? p ?? q;\n"
            "}\n"
        )


def test_coalesce_chains_group_as_comparison_operands():
    # Pinned: `try g() ?? v > w ?? q` is `try g() ?? ((v > w) ?? q)` -- the
    # comparison binds tighter than the looser `??`, then the trailing
    # `?? q` chains right over it. So the inner `??`'s LEFT operand is the
    # comparison result (a bool), and its reject arm reports `got bool` --
    # proving the fallback grouped as the whole `(v > w) ?? q`, not as an
    # operand of an outer `>`. (Under the old tighter rule the right-hand
    # `w ?? q` would have grouped first, a distinct grouping.)
    with pytest.raises(
        LangError,
        match=re.escape(
            "'??' coalesces pointers or supplies a try fallback; got bool"
        ),
    ):
        compile_ir(
            DECL + FIND
            + "fn f() {\n"
            "    let x = try find(1) ?? 5 > 3 ?? 9;\n"
            "}\n"
        )
    # And the runnable twin: parenthesized try chains as comparison operands.
    assert run(
        DECL + FIND
        + """
        fn main() -> int32 {
            let big = (try find(5) ?? 0) > (try find(1) ?? 100);   // 10 > 2
            if (big) { return 42; }
            return 0;
        }
        """
    ) == 42


def test_coalesce_on_a_result_needs_try():
    with pytest.raises(
        LangError,
        match=re.escape(
            "a result<int64, my_error> left of '??' unwraps through try: "
            "try f() ?? v"
        ),
    ):
        compile_ir(DECL + FIND + "fn f() { let x = find(1) ?? 0; }")


def test_coalesce_on_a_pointer_is_reserved():
    with pytest.raises(
        LangError,
        match=re.escape(
            "'??' on a pointer is null coalescing, which lands with the "
            "pointer-truthiness roadmap item; spell the test for now: "
            "p == null ? q : p"
        ),
    ):
        compile_ir(
            "fn f() { let p: int32* = null; let q = p ?? p; }"
        )


def test_coalesce_on_other_types_rejects():
    with pytest.raises(
        LangError,
        match=re.escape(
            "'??' coalesces pointers or supplies a try fallback; got int32"
        ),
    ):
        compile_ir("fn f() { let a = 1 ?? 2; }")


# --------------------------------------------- stage 3: the try statement

def test_try_statement_both_arms():
    # Ok: the fresh binding runs the block; error: the handler runs with
    # the binder bound, obligation-free.
    assert run(
        DECL + FIND
        + """
        fn main() -> int32 {
            let out: int32 = 0;
            try (v = find(21)) { out = v as int32; } except (err) { out = -1; }
            try (v = find(0)) { out = -2; } except (err) {
                out = out + (err as int32) - 1;
            }
            return out;   // 42 + 1 - 1
        }
        """
    ) == 42


def test_try_statement_handler_may_fall_through():
    # Obligation-free: fall through and continue after the statement.
    assert run(
        DECL + FIND
        + """
        fn main() -> int32 {
            try (v = find(0)) { return -1; } except (err) { }
            return 42;
        }
        """
    ) == 42


def test_try_statement_binding_is_scoped_to_the_block():
    # `ret is not available here` -- the binding dies with the block.
    with pytest.raises(LangError, match=re.escape("undefined variable 'v'")):
        compile_ir(
            DECL + FIND
            + "fn f() -> int32 {\n"
            "    try (v = find(1)) { } except (e) { }\n"
            "    return v as int32;\n"
            "}\n"
        )


def test_try_statement_binder_is_scoped_to_the_handler():
    with pytest.raises(LangError, match=re.escape("undefined variable 'e'")):
        compile_ir(
            DECL + FIND
            + "fn f() -> int32 {\n"
            "    try (v = find(1)) { } except (e) { }\n"
            "    return e as int32;\n"
            "}\n"
        )


def test_try_statement_bindings_shadow_and_restore():
    # Both names may shadow enclosing locals; the outer values are intact
    # after the statement.
    assert run(
        DECL + FIND
        + """
        fn main() -> int32 {
            let v: int32 = 40;
            let err: int32 = 2;
            try (v = find(0)) { v; } except (err) { let inner = err; }
            return v + err;
        }
        """
    ) == 42


def test_try_statement_rejects_the_error_only_result():
    with pytest.raises(
        LangError,
        match=re.escape(
            "a result<my_error> has no ok value to bind; handle it without "
            "the binding: try f(); or try f() except (err) { ... };"
        ),
    ):
        compile_ir(
            DECL + FLUSH
            + "fn f() { try (v = flush(1)) { } except (e) { } }"
        )


def test_try_statement_takes_no_else():
    # Ruled: the block already is the no-error arm.
    with pytest.raises(
        LangError,
        match=re.escape(
            "a try statement takes no else arm: the block already is the "
            "no-error arm"
        ),
    ):
        compile_ir(
            DECL + FIND
            + "fn f() { try (v = find(1)) { } except (e) { } else { } }"
        )


def test_try_statement_needs_its_handler():
    with pytest.raises(
        LangError,
        match=re.escape(
            "a try statement needs its except handler: "
            "try (ret = f()) { ... } except (err) { ... }"
        ),
    ):
        compile_ir(DECL + FIND + "fn f() { try (v = find(1)) { } }")


def test_try_statement_blocks_are_braced():
    with pytest.raises(
        LangError,
        match=re.escape(
            "a try statement's block is braced, as in "
            "'try (ret = f()) { ... } except (err) { ... }'"
        ),
    ):
        compile_ir(
            DECL + FIND
            + "fn f() { try (v = find(1)) return; except (e) { } }"
        )
    with pytest.raises(
        LangError,
        match=re.escape(
            "an except handler is a braced block, as in "
            "'try (ret = f()) { ... } except (err) { ... }'"
        ),
    ):
        compile_ir(
            DECL + FIND
            + "fn f() { try (v = find(1)) { } except (e) return; }"
        )


def test_try_statement_head_needs_a_result():
    with pytest.raises(
        LangError,
        match=re.escape("try needs a result value, got int32"),
    ):
        compile_ir(
            "fn g() -> int32 { return 1; }\n"
            "fn f() { try (v = g()) { } except (e) { } }"
        )


def test_statement_position_try_disambiguates_on_the_head():
    # `try ( IDENT =` opens the statement; anything else is an expression
    # statement -- `try (r);` and `try (g());` propagate a parenthesized
    # operand.
    assert run(
        DECL + FLUSH
        + """
        fn run_both(fail: int32) -> result<my_error> {
            let r = flush(fail);
            try (r);
            try (flush(fail));
            return ok();
        }
        fn main() -> int32 {
            let a = run_both(0);
            let got, err = check(run_both(1));
            return got + (err as int32);   // 42 + 0
        }
        fn check(r: result<my_error>) -> result<int32, my_error> {
            try (v = probe(r)) { return ok(-1); } except (err) {
                return ok(40 + err as int32);
            }
        }
        fn probe(r: result<my_error>) -> result<int32, my_error> {
            try r;
            return ok(40);
        }
        """
    ) == 42


def test_try_statement_all_arms_diverging_diverges():
    # Both arms diverge: the statement diverges, and the dead-code class
    # names it as a whole-statement divergence.
    src = (
        DECL + FIND
        + "fn f() -> int32 {\n"
        "    try (v = find(1)) { return 1; } except (err) { return -1; }\n"
        "    let dead: int32 = 0;\n"
        "    return dead;\n"
        "}\n"
    )
    cg = CodeGen(Parser(tokenize(src)).parse_program(), "test")
    cg.generate()
    dead = [w for w in cg.warnings if w.wclass == "dead-code"]
    assert [w.message for w in dead] == [
        "unreachable code: every path through the statement above diverges"
    ]


def test_try_statement_falling_arms_no_dead_code_misfire():
    src = (
        DECL + FIND
        + "fn f() -> int32 {\n"
        "    try (v = find(1)) { } except (err) { }\n"
        "    return 0;\n"
        "}\n"
    )
    cg = CodeGen(Parser(tokenize(src)).parse_program(), "test")
    cg.generate()
    assert [w for w in cg.warnings if w.wclass == "dead-code"] == []


def test_try_statement_handler_in_a_defer_obeys_the_escape_ban():
    # Form B inside a defer body: a handler that returns or breaks out of
    # the defer's scope hits the existing bans, unchanged.
    with pytest.raises(
        LangError,
        match=re.escape(
            "'return' inside a defer body cannot exit the enclosing function"
        ),
    ):
        compile_ir(
            DECL + FIND
            + "fn f() {\n"
            "    defer { try (v = find(1)) { } except (e) { return; } }\n"
            "}\n"
        )
    with pytest.raises(
        LangError,
        match=re.escape(
            "'break' inside a defer body cannot exit the enclosing loop"
        ),
    ):
        compile_ir(
            DECL + FIND
            + "fn f() {\n"
            "    while (true) {\n"
            "        defer { try (v = find(1)) { } except (e) { break; } }\n"
            "        break;\n"
            "    }\n"
            "}\n"
        )


def test_dangling_else_binds_the_inner_try():
    # `if (c) try ... except (e) { } else { }`: the else belongs to the
    # inner try's except clause (greedy-inner, C's dangling-else class),
    # not to the if -- so it runs on the try's ok arm even when `c` holds.
    assert run(
        DECL + FIND
        + """
        fn main() -> int32 {
            let seen: int32 = 0;
            if (true) try find(1) except (e) { seen = -1; } else { seen = 42; };
            return seen;
        }
        """
    ) == 42


# --- stage 4: error_name / error_message accessors -------------------------
#
# `error_name(err)` renders a declared error value to its variant identifier
# (`"NOT_FOUND"`); `error_message(err)` renders its declared display string,
# falling back to the identifier when the variant declared none. Both funnel
# through a compiler-synthesized per-declaration switch (one internal function
# each, cached), keyed on the error's int32 value; the reserved zero no-error
# state and any unreachable gap render as the empty string. The names are
# claimed only when directly followed by `(`, so they stay identifiers.

ACC = (
    "error acc_error {\n"
    '    NOT_FOUND = "Not Found",\n'   # 1, display
    "    PERMISSION,\n"                # 2, no display
    "    EXHAUSTED = 100,\n"           # explicit value (gap 3..99)
    "    TIMEOUT,\n"                   # 101, resumes from 100 + 1
    "}\n"
)
ACC_FIND = (
    "fn afind(key: int32) -> result<int32, acc_error> {\n"
    "    if (key == 0) { return error(acc_error::NOT_FOUND); }\n"
    "    return ok(key);\n"
    "}\n"
)


def test_error_name_returns_the_variant_identifier(capfd):
    # Every variant, including the explicit-value and post-gap ones, renders
    # as its own spelled identifier -- never the display string.
    assert run(
        'import "std/io";\n' + ACC
        + """
        fn main() -> int32 {
            println("{}", error_name(acc_error::NOT_FOUND));
            println("{}", error_name(acc_error::PERMISSION));
            println("{}", error_name(acc_error::EXHAUSTED));
            println("{}", error_name(acc_error::TIMEOUT));
            return 0;
        }
        """
    ) == 0
    out, _ = capfd.readouterr()
    assert out == "NOT_FOUND\nPERMISSION\nEXHAUSTED\nTIMEOUT\n"


def test_error_message_prefers_display_then_identifier(capfd):
    # A declared display string wins; a variant without one falls back to its
    # identifier, so a message is never empty for a real variant.
    assert run(
        'import "std/io";\n' + ACC
        + """
        fn main() -> int32 {
            println("{}", error_message(acc_error::NOT_FOUND));
            println("{}", error_message(acc_error::PERMISSION));
            println("{}", error_message(acc_error::EXHAUSTED));
            println("{}", error_message(acc_error::TIMEOUT));
            return 0;
        }
        """
    ) == 0
    out, _ = capfd.readouterr()
    assert out == "Not Found\nPERMISSION\nEXHAUSTED\nTIMEOUT\n"


def test_error_accessors_run_through_a_destructured_result(capfd):
    # The realistic path: name/message a value pulled out of a real result.
    assert run(
        'import "std/io";\n' + ACC + ACC_FIND
        + """
        fn main() -> int32 {
            let value, err = afind(0);
            if (err) {
                println("{}: {}", error_name(err), error_message(err));
            }
            return 0;
        }
        """
    ) == 0
    out, _ = capfd.readouterr()
    assert out == "NOT_FOUND: Not Found\n"


def test_error_name_of_the_zero_no_error_state_is_empty(capfd):
    # A successful destructure binds `err` to the reserved zero state, which
    # names no variant: the accessors render the empty string (the sentinel
    # brackets show it is genuinely empty, not a missing line).
    assert run(
        'import "std/io";\n' + ACC + ACC_FIND
        + """
        fn main() -> int32 {
            let value, err = afind(7);
            println("[{}][{}]", error_name(err), error_message(err));
            return 0;
        }
        """
    ) == 0
    out, _ = capfd.readouterr()
    assert out == "[][]\n"


def test_error_name_rejects_a_non_error():
    with pytest.raises(
        LangError,
        match=re.escape(
            "error_name() takes a declared error value, got int32"
        ),
    ):
        compile_ir(
            "fn f() -> int32 { let n = error_name(5); return 0; }"
        )


def test_error_message_rejects_a_non_error():
    with pytest.raises(
        LangError,
        match=re.escape(
            "error_message() takes a declared error value, got bool"
        ),
    ):
        compile_ir(
            "fn f() -> int32 { let n = error_message(true); return 0; }"
        )


def test_error_name_in_a_const_initializer_is_not_constant():
    # The accessors are runtime lookups; a const global initializer rejects
    # one through the ordinary not-a-constant path.
    with pytest.raises(
        LangError, match="const initializer must be a compile-time constant"
    ):
        compile_ir(
            ACC
            + 'const g: char* = error_name(acc_error::NOT_FOUND);\n'
            + "fn main() -> int32 { return 0; }\n"
        )


def test_error_name_stays_an_ordinary_identifier():
    # Only the call shape `error_name(` / `error_message(` is claimed; the
    # bare names remain usable as ordinary variables.
    assert run(
        """
        fn main() -> int32 {
            let error_name: int32 = 40;
            let error_message: int32 = 2;
            return error_name + error_message;
        }
        """
    ) == 42


def test_error_accessor_lowers_to_one_cached_function_each():
    # Repeated calls share a single synthesized internal function per accessor
    # (cached), and each call site is a plain call to it.
    ir = compile_ir(
        ACC
        + """
        fn f(e: acc_error) -> char* {
            let a = error_name(e);
            let b = error_name(e);
            let c = error_message(e);
            return a;
        }
        """
    )
    assert ir.count('define internal i8* @"error.name.acc_error"') == 1
    assert ir.count('define internal i8* @"error.message.acc_error"') == 1
    assert ir.count('call i8* @"error.name.acc_error"') == 2
    assert ir.count('call i8* @"error.message.acc_error"') == 1


def test_error_accessor_does_not_leak_into_the_interface():
    # `error_name` lives only in a function body; a public result-returning
    # prototype renders normally, with no accessor symbol in the stub.
    stub = iface(ACC + ACC_FIND)
    assert "afind" in stub
    assert "error_name" not in stub
    assert "error.name" not in stub
