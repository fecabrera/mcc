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


def test_a_result_does_not_destructure():
    # The binding forms are a later stage; today the reject names no
    # misleading borrow.
    with pytest.raises(
        LangError,
        match=re.escape(
            "cannot destructure result<int32, my_error>; only a tuple or "
            "slice can be destructured"
        ),
    ):
        compile_ir(
            DECL
            + "fn g() -> result<int32, my_error> { return ok(1); }\n"
            + "fn f() { let a, b = g(); }"
        )


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
