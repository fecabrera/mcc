"""Generic type-parameter defaults: ``fn f<T = uint8*>`` / ``struct r<T = int64>``.

A declared default fills a type parameter that is neither given explicitly nor
inferred from a *typed* value. The priority order is: explicit type argument >
typed-value inference > declared default > untyped-constant anchoring -- so a
default beats an untyped literal's ``int32`` leaning, and the literal adapts
to it. Defaults are trailing-only and may reference only earlier parameters.
"""

import pytest

from mcc.errors import LangError
from helpers import compile_ir, run


# --------------------------------------------------------------- parse rules


def test_non_trailing_default_is_rejected():
    with pytest.raises(
        LangError,
        match="type parameter 'U' without a default cannot follow a defaulted one",
    ):
        compile_ir("fn f<T = int32, U>(x: T, y: U) { }")


def test_non_trailing_default_on_struct_is_rejected():
    with pytest.raises(
        LangError,
        match="type parameter 'B' without a default cannot follow a defaulted one",
    ):
        compile_ir("struct p<A = int32, B> { a: A; b: B; }")


def test_default_referencing_itself_is_rejected():
    with pytest.raises(
        LangError,
        match="default for type parameter 'T' references 'T', "
        "which is not declared before it",
    ):
        compile_ir("fn f<T = T>(x: T) { }")


def test_default_referencing_later_parameter_is_rejected():
    # `U` may not fall through to a same-named global type either -- the
    # reference is rejected outright.
    with pytest.raises(
        LangError,
        match="default for type parameter 'T' references 'U', "
        "which is not declared before it",
    ):
        compile_ir("fn f<T = U, U = int32>(x: T, y: U) { }")


def test_default_referencing_earlier_parameter_parses():
    src = """
        fn f<T, U = T*>(x: T, y: U) -> int64 { return sizeof(U) as int64; }
        fn main() -> int32 { let v = 5 as int8; return f(v, &v) as int32; }
    """
    assert run(src) == 8  # U defaulted to int8* -- a pointer's width


# ------------------------------------------------------------ function calls


def test_default_fills_when_nothing_infers():
    # The untyped literal would otherwise anchor T = int32; the declared
    # default wins and the literal adapts to int64 (sizeof 8, not 4).
    src = """
        fn size<T = int64>(x: T) -> int64 { return sizeof(T) as int64; }
        fn main() -> int32 { return size(0) as int32; }
    """
    assert run(src) == 8


def test_typed_value_beats_default():
    src = """
        fn size2<T = int32>(a: T, b: T) -> int64 { return sizeof(T) as int64; }
        fn main() -> int32 { return size2(0, 2 as int64) as int32; }
    """
    assert run(src) == 8


def test_explicit_type_argument_beats_default():
    src = """
        fn size<T = int64>(x: T) -> int64 { return sizeof(T) as int64; }
        fn main() -> int32 { return size<int16>(0) as int32; }
    """
    assert run(src) == 2


def test_defaulted_tail_may_be_omitted_from_explicit_args():
    src = """
        fn g<T, U = int8>(x: T) -> int64 { return sizeof(U) as int64; }
        fn main() -> int32 { return g<int32>(1) as int32; }
    """
    assert run(src) == 1


def test_omitting_an_undefaulted_parameter_keeps_the_arity_error():
    with pytest.raises(
        LangError, match=r"'g' expects 2 type argument\(s\), got 1"
    ):
        compile_ir(
            "fn g<T, U>(x: int32) -> int32 { return 0; }\n"
            "fn main() -> int32 { return g<int32>(1); }"
        )


def test_defaulted_tail_makes_the_arity_error_a_range():
    with pytest.raises(
        LangError, match=r"'g' expects between 2 and 3 type argument\(s\), got 1"
    ):
        compile_ir(
            "fn g<T, U, V = int8>(x: int32) -> int32 { return 0; }\n"
            "fn main() -> int32 { return g<int32>(1); }"
        )


def test_undefaulted_parameter_keeps_cannot_infer_error():
    with pytest.raises(LangError, match="cannot infer type parameter"):
        compile_ir(
            "fn f<T>(x: int32) -> int32 { return 0; }\n"
            "fn main() -> int32 { return f(1); }"
        )


def test_default_of_void_is_rejected_at_the_call():
    # The default resolves, then the existing binding check rejects void.
    with pytest.raises(
        LangError, match="cannot bind type parameter T to void"
    ):
        compile_ir(
            "fn f<T = void>(x: int32) -> int32 { return 0; }\n"
            "fn main() -> int32 { return f(1); }"
        )


def test_null_never_binds_but_the_default_fills():
    with pytest.raises(LangError, match="cannot infer type parameter"):
        compile_ir(
            "fn f<T>(x: T*) -> int32 { return 0; }\n"
            "fn main() -> int32 { return f(null); }"
        )
    src = """
        fn f<T = int64>(x: T*) -> int64 { return sizeof(T) as int64; }
        fn main() -> int32 { return f(null) as int32; }
    """
    assert run(src) == 8


def test_default_and_explicit_spellings_share_one_instance():
    src = """
        fn size<T = int64>(x: T) -> int64 { return sizeof(T) as int64; }
        fn main() -> int32 {
            return (size(0) + size<int64>(1 as int64)) as int32;
        }
    """
    ir = compile_ir(src)
    assert ir.count('define i64 @"size<int64>"') == 1


def test_default_can_make_an_overload_ambiguous():
    # Adding a default turns a previously-nonviable candidate viable: both
    # overloads now resolve for one typed argument, and the tie is reported,
    # not silently broken. (Documented hazard of adding a default.)
    src = """
        fn h<T>(x: T) -> int32 { return 1; }
        fn h<T, U = int64>(x: T) -> int32 { return 2; }
        fn main() -> int32 { return h(5 as int16); }
    """
    with pytest.raises(
        LangError, match="call to 'h' is ambiguous between overloads"
    ):
        compile_ir(src)


def test_broken_default_gets_a_use_site_note():
    with pytest.raises(LangError) as excinfo:
        compile_ir(
            "fn f<T = nosuch>(x: int32) -> int32 { return 0; }\n"
            "fn main() -> int32 {\n"
            "    return f(1);\n"
            "}\n"
        )
    assert str(excinfo.value) == "line 1: unknown type 'nosuch'"
    assert [(n.message, n.line) for n in excinfo.value.notes] == [
        ("in default for type parameter T of f", 3),
    ]


# -------------------------------------------------------------------- structs


RANGE = "struct range<T = int64> { start: T; stop: T; }\n"


def test_struct_literal_uses_the_default():
    src = (
        RANGE
        + """
        fn width<T>(x: T) -> int64 { return sizeof(T) as int64; }
        fn main() -> int32 {
            let r = struct range { start = 0 };
            return width(r.start) as int32;
        }
    """
    )
    assert run(src) == 8


def test_typed_field_beats_the_struct_default():
    src = (
        RANGE
        + """
        fn width<T>(x: T) -> int64 { return sizeof(T) as int64; }
        fn main() -> int32 {
            let r = struct range { start = 1 as int16, stop = 10 };
            return width(r.stop) as int32;
        }
    """
    )
    assert run(src) == 2


def test_undefaulted_struct_parameter_keeps_cannot_infer_error():
    # `B` defaults but `A` does not; untyped-only fields still cannot anchor A.
    src = """
        struct pair<A, B = int8> { a: A; b: B; }
        fn f() { let p = struct pair { a = 1 }; }
    """
    with pytest.raises(LangError, match="cannot infer type parameter.*A"):
        compile_ir(src)


def test_bare_defaulted_struct_works_as_a_written_type():
    src = (
        RANGE
        + """
        fn main() -> int32 {
            let r: range;
            r.stop = 16 as int64;
            return sizeof(range) as int32;
        }
    """
    )
    assert run(src) == 16


def test_bare_defaulted_struct_works_in_extends():
    src = """
        struct base<T = int64> { v: T; }
        struct child extends base { extra: int32; }
        fn main() -> int32 { let c: child; return sizeof(child) as int32; }
    """
    assert run(src) == 16  # int64 + int32, padded to the int64 alignment


def test_bare_generic_without_default_keeps_the_arity_error():
    with pytest.raises(
        LangError, match=r"struct 'range' expects 1 type argument\(s\), got 0"
    ):
        compile_ir(
            "struct range<T> { start: T; }\n"
            "fn main() -> int32 { return sizeof(range) as int32; }"
        )


def test_struct_defaulted_tail_may_be_omitted():
    src = """
        struct pair<A, B = int8> { a: A; b: B; }
        fn main() -> int32 { let p: pair<int8>; return sizeof(pair<int8>) as int32; }
    """
    assert run(src) == 2


def test_struct_arity_error_becomes_a_range_with_defaults():
    with pytest.raises(
        LangError,
        match=r"struct 'range' expects between 0 and 1 type argument\(s\), got 2",
    ):
        compile_ir(
            RANGE + "fn main() -> int32 { return sizeof(range<int8, int8>) as int32; }"
        )
