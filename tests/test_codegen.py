import pytest

from mcc.errors import LangError
from helpers import compile_ir, run

MAIN = "fn main() -> int32 {{ {body} return 0; }}"


def main_ir(body):
    return compile_ir("import \"libc/stdio\";\n" + MAIN.format(body=body))


def test_include_declares_printf():
    ir_text = main_ir("")
    assert 'declare i32 @"printf"(i8* %".1", ...)' in ir_text


def test_string_becomes_global_constant():
    ir_text = main_ir('printf("hi\\n");')
    assert 'c"hi\\0a\\00"' in ir_text


def test_signed_division_uses_sdiv():
    ir_text = main_ir("let a: int32 = 10; let b = a / 3;")
    assert "sdiv" in ir_text and "udiv" not in ir_text


def test_unsigned_division_uses_udiv():
    ir_text = main_ir("let a: uint32 = 10; let b = a / 3; let c = a % 3;")
    assert "udiv" in ir_text and "urem" in ir_text
    assert "sdiv" not in ir_text and "srem" not in ir_text


def test_comparison_signedness():
    assert "icmp sgt" in main_ir("let a: int32 = 1; let b = a > 0;")
    assert "icmp ugt" in main_ir("let a: uint32 = 1; let b = a > 0;")


def test_varargs_promotion_signedness():
    assert "sext" in main_ir('let a: int8 = -1; printf("%d", a);')
    assert "zext" in main_ir('let a: uint8 = 1; printf("%u", a);')


def test_bitwise_instructions():
    ir_text = main_ir(
        "let a: uint64 = 5; "
        "let b = a ^ (a >> 3); let c = a & 7; let d = a | 8; let e = a << 2;"
    )
    for instruction in ("xor", "lshr", "and", "or", "shl"):
        assert instruction in ir_text
    assert "ashr" in main_ir("let a: int64 = -5; let b = a >> 1;")


def test_bitwise_binds_tighter_than_comparison():
    # a & b == c parses as (a & b) == c, so this compiles (bool == would not).
    ir_text = main_ir("let a: int32 = 6; let ok = a & 4 == 4;")
    assert "and" in ir_text


def test_bitwise_constants_fold_and_stay_untyped():
    ir_text = main_ir("let x: uint8 = 1 << 7;")  # 128 fits in uint8
    assert "shl" not in ir_text  # folded away
    with pytest.raises(LangError, match="out of range for uint8"):
        main_ir("let x: uint8 = 1 << 8;")


def test_bitwise_not_emits_xor():
    # '~' lowers to xor with all-ones, on signed and unsigned alike.
    assert "xor" in main_ir("let a: uint32 = 5; let b = ~a;")
    assert "xor" in main_ir("let a: int64 = 5; let b = ~a;")


def test_bitwise_not_literal_folds_and_stays_untyped():
    # ~5 == -6 folds to a constant and still coerces to a wider type.
    ir_text = main_ir("let x: int64 = ~5;")
    assert "xor" not in ir_text  # folded away
    # ~0 == 255 in uint8's range after wrapping.
    assert "xor" not in main_ir("let x: uint8 = ~0 as uint8;")


def test_bitwise_not_rejects_non_integer():
    with pytest.raises(LangError, match="cannot apply '~' to a float64"):
        main_ir("let a: float64 = 1.0; let b = ~a;")


def test_bitwise_not_runtime_values():
    assert run(
        "fn main() -> int32 {\n"
        "    let a: uint8 = 0x0F;\n"
        "    if ((~a as uint8) != 240) { return 1; }\n"
        "    let b: int32 = 5;\n"
        "    if (~b != -6) { return 2; }\n"
        "    let c: uint64 = 0;\n"
        "    if (~c != 18446744073709551615) { return 3; }\n"
        "    return 0;\n"
        "}"
    ) == 0


def test_missing_return_in_main_is_implicit_zero():
    ir_text = compile_ir("fn main() -> int32 {}")
    assert "ret i32 0" in ir_text


def test_generic_monomorphization_and_caching():
    ir_text = compile_ir(
        """
        fn sum<T>(a: T, b: T) -> T { return a + b; }
        fn main() -> int32 {
            let a: uint8 = sum<uint8>(1, 2);
            let b: uint8 = sum<uint8>(3, 4);
            let c: int64 = sum<int64>(5, 6);
            return 0;
        }
        """
    )
    assert ir_text.count('define i8 @"sum<uint8>"') == 1
    assert ir_text.count('define i64 @"sum<int64>"') == 1


def test_uninstantiated_template_emits_nothing():
    ir_text = compile_ir(
        "fn unused<T>(a: T) -> T { return a; } fn main() -> int32 { return 0; }"
    )
    assert "unused" not in ir_text


@pytest.mark.parametrize(
    "source, message",
    [
        ("fn main() -> int32 { let x: uint8 = 300; return 0; }",
         "out of range for uint8"),
        ("fn main() -> int32 { let a: uint32 = 1; let b: int32 = 1; let c = a + b; return 0; }",
         "expected int32, got uint32"),
        ("fn main() -> int32 { let a: uint32 = 1; let b = -a; return 0; }",
         "cannot negate a uint32"),
        ("fn main() -> int32 { return x; }", "undefined variable 'x'"),
        ("fn main() -> int32 { printf(\"hi\"); return 0; }", "missing import"),
        ("fn main() -> int32 { let x: int32 = 1; let x: int32 = 2; return 0; }",
         "already declared"),
        ("fn f() {} fn f() {} fn main() -> int32 { return 0; }", "already defined"),
        ("fn f() -> int32 { let x: int32 = 1; } fn main() -> int32 { return f(); }",
         "may end without a return"),
        ("fn main() -> int32 { let v: void = 1; return 0; }", "void variable"),
        ("fn main() -> int32 { let v: void; return 0; }", "void variable"),
        ("fn main() -> int32 { let x: int32; let x: int32; return 0; }",
         "already declared"),
        ("fn main() -> int32 { break; return 0; }", "'break' outside a loop"),
        ("fn main() -> int32 { if (true) { continue; } return 0; }",
         "'continue' outside a loop"),
        # A generic instantiated from inside a loop does not inherit it.
        ("fn f<T>(x: T) -> T { break; return x; } fn main() -> int32 "
         "{ while (true) { return f(0 as int32); } return 0; }",
         "'break' outside a loop"),
        ("fn main() -> int32 { let x: nope = 1; return 0; }", "unknown type 'nope'"),
        ("fn f() {} fn main() -> int32 { f<int32>(); return 0; }", "not a generic function"),
        ("fn f<T>(x: int32) -> T { return x; } fn main() -> int32 { return f(1); }",
         "cannot infer type parameter"),
        ("fn f<T>(a: T, b: T) {} fn main() -> int32 "
         "{ let x: int32 = 1; let y: int64 = 2; f(x, y); return 0; }",
         "conflicting types for type parameter T in call to 'f': int32 vs int64"),
        ("fn f<T>(a: T, b: T) {} fn main() -> int32 "
         "{ let x: int32 = 1; let y: int64 = 2; f<int64>(x, y); return 0; }",
         "argument 1 of 'f': expected int64, got int32"),
        ("fn main() -> int32 { return 1.5; }", "expected int32, got float64"),
        ("fn main() -> int32 { let x = 1.5 % 2.0; return 0; }", "not supported for float64"),
        ("fn main() -> int32 { let x = true + 1; return 0; }", "expected bool, got int32"),
        ("fn main() -> int32 { let x = true + false; return 0; }",
         "'\\+' not supported for bool"),
        ("fn main() -> int32 { let x = 1; return 0; }", "type of 'x' is ambiguous"),
        ("fn main() -> int32 { let x = -1; return 0; }", "type of 'x' is ambiguous"),
        ("fn main() -> int32 { let x = (1 + 2) * 3; return 0; }", "type of 'x' is ambiguous"),
    ],
)
def test_compile_errors(source, message):
    with pytest.raises(LangError, match=message):
        compile_ir(source)


def test_untyped_let_accepts_typed_values():
    # Annotations, casts, typed operands, sizeof, calls, and non-integer
    # literals all give a definite type; bare integer constants do not.
    compile_ir(
        """
        fn f() -> int32 { return 1; }
        fn main() -> int32 {
            let a: int32 = 1;
            let b = 2 as uint8;
            let c = a + 1;
            let d = sizeof(int32);
            let e = f();
            let g = 1.5;
            let h = true;
            return 0;
        }
        """
    )
