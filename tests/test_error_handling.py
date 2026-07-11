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
    # The handler clause follows the try operand immediately: `try g() + 1
    # except ...` is not "try over the sum" -- the clause is missing where
    # this stage requires it.
    with pytest.raises(
        LangError,
        match=re.escape(
            "try without a handler is not yet supported; add "
            "except (err) { ... }"
        ),
    ):
        compile_ir(
            DECL + FIND
            + "fn f() -> int64 {\n"
            "    let v = try find(1) + 1 except (err) { emit 0; };\n"
            "    return v;\n"
            "}\n"
        )


def test_bare_try_is_staged():
    # Propagation (`let v = try g();`) is the next stage of the epic; until
    # then a try without its handler names the fix.
    with pytest.raises(
        LangError,
        match=re.escape(
            "try without a handler is not yet supported; add "
            "except (err) { ... }"
        ),
    ):
        compile_ir(
            DECL + FIND
            + "fn f() -> int64 { let v = try find(1); return v; }"
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
