"""Pointers, `as` casts, sizeof, and `import` directives."""

import pytest

from mcc.errors import LangError
from mcc.nodes import Binary, Cast, SizeOf, StoreDeref, StoreIndex
from helpers import compile_ir, parse, run, run_path


# --------------------------------------------------------------------- parser

def main_body(body_source):
    return parse("fn main() { " + body_source + " }").functions[0].body


def test_pointer_types_parse():
    (func,) = parse("fn f(p: int32*, q: uint8**) -> T* {}").functions
    assert [(n, str(ty)) for n, ty in func.params] == [("p", "int32*"), ("q", "uint8**")]
    assert str(func.ret_type) == "T*"


def test_as_binds_tighter_than_binary_ops():
    (stmt,) = main_body("let x = a + b as int64;")
    assert isinstance(stmt.value, Binary) and stmt.value.op == "+"
    assert isinstance(stmt.value.rhs, Cast)
    assert str(stmt.value.rhs.type_name) == "int64"


def test_chained_and_pointer_casts():
    (stmt,) = main_body("let x = p as uint8* as uint64;")
    outer = stmt.value
    assert isinstance(outer, Cast) and str(outer.type_name) == "uint64"
    assert isinstance(outer.value, Cast) and str(outer.value.type_name) == "uint8*"


def test_sizeof_parses():
    (stmt,) = main_body("let s = sizeof(int32*);")
    assert isinstance(stmt.value, SizeOf)
    assert str(stmt.value.type_name) == "int32*"


def test_store_targets():
    deref, index = main_body("*p = 1; p[2] = 3;")
    assert isinstance(deref, StoreDeref)
    assert isinstance(index, StoreIndex)


def test_invalid_assignment_target():
    # A call target parses now (mut returns make `f(s) = v;` a statement;
    # whether the callee returns mut is checked at codegen), so a binary
    # expression is the still-invalid parse-level target.
    with pytest.raises(LangError, match="invalid assignment target"):
        parse("fn main() { 1 + 2 = 3; }")


def test_import_directive():
    program = parse('import "lib/memory";\nimport "libc/stdio";\nfn main() {}')
    assert program.imports == [("lib/memory", 1), ("libc/stdio", 2)]


# -------------------------------------------------------------------- codegen

def test_stdlib_declares_malloc_and_free():
    ir_text = compile_ir('import "libc/stdlib";\nfn main() -> int32 { return 0; }')
    assert 'declare i8* @"malloc"(i64 %".1")' in ir_text
    assert 'declare void @"free"(i8* %".1")' in ir_text


def test_index_emits_gep():
    ir_text = compile_ir(
        "import \"libc/stdlib\";\n"
        "fn main() -> int32 { let p = malloc(4) as int32*; p[1] = 7; return p[1]; }"
    )
    assert "getelementptr" in ir_text
    assert "bitcast" in ir_text


def test_cast_instructions():
    src = "fn main() -> int32 {{ {0} return 0; }}"
    assert "trunc" in compile_ir(src.format("let a: int32 = 300; let b = a as uint8;"))
    assert "sext" in compile_ir(src.format("let a: int8 = -1; let b = a as int64;"))
    assert "zext" in compile_ir(src.format("let a: uint8 = 1; let b = a as uint64;"))
    assert "sitofp" in compile_ir(src.format("let a: int32 = 2; let b = a as float64;"))
    assert "uitofp" in compile_ir(src.format("let a: uint32 = 2; let b = a as float64;"))
    assert "fptosi" in compile_ir(src.format("let a = 2.5; let b = a as int32;"))
    assert "ptrtoint" in compile_ir(src.format("let a: int32 = 0; let p = &a; let n = p as uint64;"))


@pytest.mark.parametrize(
    "body, message",
    [
        ("let x: int32 = 1; let y = *x;", "cannot dereference a int32"),
        ("let x: int32 = 1; let y = x[0];", "cannot index a int32"),
        ("let x: int32 = 1; let p = &x; let y = p[1.5];", "index must be an integer"),
        ("let x = 1.5; let b = x as bool;", "cannot cast float64 to bool"),
        ("let p: void* = 0 as void*;", "no void pointers"),
        ("let x = &1;", "not addressable"),
        ("let x: int32 = 1; let p = &x; *p = 2.5;", "expected int32, got float64"),
    ],
)
def test_pointer_errors(body, message):
    with pytest.raises(LangError, match=message):
        compile_ir("fn main() -> int32 { " + body + " return 0; }")


def test_codegen_rejects_unresolved_imports():
    # CodeGen must be handed an already-merged program; driving it with a raw
    # parse that still has imports is a programming error.
    from mcc.codegen import CodeGen
    with pytest.raises(LangError, match="imports must be resolved"):
        CodeGen(parse('import "other";\nfn main() -> int32 { return 0; }'),
                "test").generate()


# ------------------------------------------------------------------ execution

def test_heap_roundtrip(capfd):
    run(
        """
        import "libc/stdio";
        import "libc/stdlib";
        fn main() -> int32 {
            let nums = malloc(10 * sizeof(int64)) as int64*;
            let i: int32 = 0;
            while (i < 10) {
                nums[i] = i as int64 * 1000000000;
                i = i + 1;
            }
            printf("%lld %lld\\n", nums[3], nums[9]);
            free(nums);
            return 0;
        }
        """
    )
    assert capfd.readouterr().out == "3000000000 9000000000\n"


def test_address_of_and_deref(capfd):
    run(
        """
        import "libc/stdio";
        fn bump(p: int32*) {
            *p = *p + 1;
        }
        fn main() -> int32 {
            let x: int32 = 41;
            bump(&x);
            printf("%d\\n", x);
            return 0;
        }
        """
    )
    assert capfd.readouterr().out == "42\n"


def test_cast_values():
    assert run("fn main() -> int32 { return 300 as uint8 as int32; }") == 44
    assert run("fn main() -> int32 { let x: uint8 = 200; return (x as int8 as int32); }") == -56
    assert run("fn main() -> int32 { return 3.99 as int32; }") == 3
    assert run("fn main() -> int32 { return (true as int32) + (false as int32); }") == 1


def test_sizeof_values():
    assert run("fn main() -> int32 { return sizeof(uint8) as int32; }") == 1
    assert run("fn main() -> int32 { return sizeof(int16) as int32; }") == 2
    assert run("fn main() -> int32 { return sizeof(float64) as int32; }") == 8
    assert run("fn main() -> int32 { return sizeof(int32*) as int32; }") == 8


def test_sizeof_of_a_variable():
    # sizeof(v) is the size of the variable's type, as in C.
    assert run("fn main() -> int32 { let n: int16 = 0; return sizeof(n) as int32; }") == 2
    assert run(
        "fn main() -> int32 { let buf: uint8[16]; return sizeof(buf) as int32; }"
    ) == 16
    src = """
    struct point { x: int32; y: int32; }
    fn main() -> int32 { let p: struct point; return sizeof(p) as int32; }
    """
    assert run(src) == 8


def test_sizeof_of_a_variable_is_not_evaluated():
    # The operand of sizeof is unevaluated -- a call in it must not run.
    src = """
    @static let calls: int32[1];
    fn bump() -> int32 { calls[0] = calls[0] + 1; return 0; }
    fn main() -> int32 {
        let n: int32 = 0;
        let s = sizeof(n);      // does not touch n or call anything
        return calls[0];        // 0: nothing ran
    }
    """
    assert run(src) == 0


def test_sizeof_variable_does_not_load_in_ir():
    # No load of the variable is emitted -- sizeof folds to a constant.
    ir_text = compile_ir(
        "fn main() -> int32 { let buf: int32[4]; return sizeof(buf) as int32; }"
    )
    assert "i64 16" in ir_text


def test_string_literals_are_uint8_pointers():
    # 'h' is byte 104; indexing a string yields uint8.
    assert run('fn main() -> int32 { let s = "hi"; return s[0] as int32; }') == 104


# --------------------------------------------------------- pointer arithmetic

def test_pointer_add_advances_by_elements(capfd):
    # `p + n` is exactly `&p[n]`: an int32* steps by sizeof(int32) == 4 bytes.
    run(
        """
        import "libc/stdio";
        fn main() -> int32 {
            let buf: int32[8];
            let i: int32 = 0;
            while (i < 8) { buf[i] = i * 10; i = i + 1; }
            let p = &buf[0];
            let q = p + 3;
            printf("%d %d\\n", *q, buf[3]);   // both 30
            return 0;
        }
        """
    )
    assert capfd.readouterr().out == "30 30\n"


def test_pointer_sub_integer_walks_back(capfd):
    # `p - n` widens n to i64, negates, then GEPs: it steps back n elements.
    run(
        """
        import "libc/stdio";
        fn main() -> int32 {
            let buf: int32[8];
            let i: int32 = 0;
            while (i < 8) { buf[i] = i * 10; i = i + 1; }
            let p = &buf[5];
            let back = p - 2;
            printf("%d\\n", *back);   // buf[3] == 30
            return 0;
        }
        """
    )
    assert capfd.readouterr().out == "30\n"


def test_pointer_difference_is_element_distance():
    # `p - q` yields the signed element distance as int64 (scaled down by
    # sizeof(pointee)), regardless of the byte gap between them.
    assert run(
        """
        fn main() -> int32 {
            let buf: int32[8];
            let p = &buf[6];
            let q = &buf[1];
            return (p - q) as int32;   // 5 elements, not 20 bytes
        }
        """
    ) == 5


def test_uint8_pointer_arithmetic_is_byte_scaled():
    # uint8* is the raw-memory pointer: element size 1, so element and byte
    # distance coincide.
    assert run(
        """
        fn main() -> int32 {
            let buf: uint8[16];
            let p = &buf[0];
            let q = p + 7;
            return (q - p) as int32;   // 7 bytes == 7 elements
        }
        """
    ) == 7


def test_pointer_difference_strips_const():
    # `int32*` minus `const int32*` compares pointees with const stripped, so
    # no explicit cast is needed.
    assert run(
        """
        fn main() -> int32 {
            let buf: int32[8];
            let p = &buf[5];
            let cp = &buf[1] as const int32*;
            return (p - cp) as int32;   // 4
        }
        """
    ) == 4


def test_pointer_scan_loop_with_ordering(capfd):
    # The `while (p < end)` scan loop: pointer ordering over identical types.
    run(
        """
        import "libc/stdio";
        fn main() -> int32 {
            let buf: int32[5];
            let i: int32 = 0;
            while (i < 5) { buf[i] = i + 1; i = i + 1; }
            let p = &buf[0];
            let end = p + 5;
            let sum: int32 = 0;
            while (p < end) { sum = sum + *p; p = p + 1; }
            printf("%d\\n", sum);   // 1+2+3+4+5 == 15
            return 0;
        }
        """
    )
    assert capfd.readouterr().out == "15\n"


def test_pointer_ordering_strips_const():
    # Ordering relationals also strip const on both sides before the
    # identical-type check.
    assert run(
        """
        fn main() -> int32 {
            let buf: int32[4];
            let p = &buf[1];
            let cp = &buf[2] as const int32*;
            if (p < cp) { return 1; }
            return 0;
        }
        """
    ) == 1


def test_pointer_compound_add_and_sub(capfd):
    # `p += n` / `p -= n` fall out of compound assignment's apply_binary reuse.
    run(
        """
        import "libc/stdio";
        fn main() -> int32 {
            let buf: int32[8];
            let i: int32 = 0;
            while (i < 8) { buf[i] = i * 100; i = i + 1; }
            let p = &buf[0];
            p += 5;
            printf("%d ", *p);   // buf[5] == 500
            p -= 2;
            printf("%d\\n", *p);   // buf[3] == 300
            return 0;
        }
        """
    )
    assert capfd.readouterr().out == "500 300\n"


def test_pointer_equality_against_null_still_works():
    # The `==`/`!=` path (incl. null comparisons) is untouched by the new arms.
    assert run(
        """
        fn main() -> int32 {
            let x: int32 = 7;
            let p = &x;
            let n: int32* = null;
            if (p != null) { if (n == null) { return 1; } }
            return 0;
        }
        """
    ) == 1


def test_pointer_add_emits_bare_gep():
    # `p + n` lowers to a typed getelementptr, exactly like `&p[n]`.
    ir_text = compile_ir(
        "fn main() -> int32 { let b: int32[4]; let p = &b[0]; "
        "let q = p + 2; return 0; }"
    )
    assert "getelementptr" in ir_text


def test_pointer_difference_emits_ptrtoint_and_sdiv():
    # `p - q` lowers to ptrtoint on both sides, a sub, then an exact sdiv.
    ir_text = compile_ir(
        "fn main() -> int32 { let b: int32[4]; let p = &b[3]; let q = &b[0]; "
        "return (p - q) as int32; }"
    )
    assert "ptrtoint" in ir_text
    assert "sdiv" in ir_text


@pytest.mark.parametrize(
    "body, message",
    [
        # Addition is pointer-left only: the commuted form gets a spelling hint.
        ("let b: int32[4]; let p = &b[0]; let x = 1 + p;",
         r"pointer on the left"),
        # Kept rejections: two pointers, the multiplicative and bitwise/shift
        # operators, and any null operand.
        ("let b: int32[4]; let p = &b[0]; let q = &b[1]; let x = p + q;",
         r"operator '\+' not supported for int32\*"),
        ("let b: int32[4]; let p = &b[0]; let q = &b[1]; let x = p * q;",
         r"operator '\*' not supported for int32\*"),
        ("let b: int32[4]; let p = &b[0]; let x = p / 2;",
         r"operand of '/'"),
        ("let b: int32[4]; let p = &b[0]; let x = p % 2;",
         r"operand of '%'"),
        ("let b: int32[4]; let p = &b[0]; let q = &b[1]; let x = p | q;",
         r"operator '\|' not supported for int32\*"),
        ("let b: int32[4]; let p = &b[0]; let x = p << 2;",
         r"operand of '<<'"),
        ("let x = null + 1;", r"operand of '\+'"),
        ("let b: int32[4]; let p = &b[0]; let x = p - null;",
         r"operator '-' not supported for int32\*"),
        ("let b: int32[4]; let p = &b[0]; let x = p < null;",
         r"operator '<' not supported for null"),
        # Pointer difference and ordering require identical types (const aside).
        ("let a: int32[4]; let b: uint8[4]; let p = &a[0]; let q = &b[0]; "
         "let x = p - q;", r"pointer difference requires identical pointer types"),
        ("let a: int32[4]; let b: uint8[4]; let p = &a[0]; let q = &b[0]; "
         "let x = p < q;", r"pointer ordering requires identical pointer types"),
    ],
)
def test_pointer_arithmetic_rejections(body, message):
    with pytest.raises(LangError, match=message):
        compile_ir("fn main() -> int32 { " + body + " return 0; }")


def test_function_pointers_reject_arithmetic():
    # A function value carries a signature, not a pointee, so it matches none
    # of the arithmetic arms: it keeps `==`/`!=` only.
    with pytest.raises(LangError, match=r"operand of '\+'"):
        compile_ir(
            "fn g(a: int32) -> int32 { return a; }\n"
            "fn main() -> int32 { let fp = g; let x = fp + 1; return 0; }"
        )
    with pytest.raises(LangError, match=r"operator '<' not supported"):
        compile_ir(
            "fn g(a: int32) -> int32 { return a; }\n"
            "fn main() -> int32 { let fp = g; let b = fp < fp; return 0; }"
        )


def test_pointer_arithmetic_proves_nonnull():
    # `f(p + n)` at a @nonnull slot proves, exactly as `&p[n]` does -- no
    # possibly-null rejection.
    assert run(
        """
        fn need(@nonnull p: int32*) -> int32 { return *p; }
        fn main() -> int32 {
            let buf: int32[4];
            buf[2] = 9;
            return need(&buf[0] + 2);   // buf[2] == 9
        }
        """
    ) == 9


def test_deref_of_pointer_arithmetic_is_silent():
    # `*(p + n)` never warns under -Wunchecked-dereference: the derived address
    # is proven like `*&p[n]` (v1 does not look through to the base pointer).
    from mcc.codegen import CodeGen

    src = (
        "fn deref(p: int32*) -> int32 { return *(p + 1); }\n"
        "fn main() -> int32 { let b: int32[4]; return deref(&b[0]); }"
    )
    cg = CodeGen(parse(src), "test")
    cg.generate()
    assert not [w for w in cg.warnings if w.wclass == "unchecked-dereference"]


def test_compound_pointer_move_rejects_nonnull_param():
    # `p += n` is a reassignment; a @nonnull parameter cannot be reassigned.
    with pytest.raises(
        LangError, match=r"cannot assign to @nonnull parameter 'p'"
    ):
        compile_ir(
            "fn f(@nonnull p: int32*) -> int32 { p += 1; return *p; }\n"
            "fn main() -> int32 { let b: int32[4]; return f(&b[0]); }"
        )


def test_compound_pointer_move_kills_narrowed_fact():
    # `p += n` kills a flow-narrowed local's non-null fact, so a later use at a
    # @nonnull slot needs re-proof.
    with pytest.raises(
        LangError, match=r"cannot pass a possibly-null pointer"
    ):
        compile_ir(
            """
            fn need(@nonnull p: int32*) -> int32 { return *p; }
            fn main() -> int32 {
                let b: int32[4];
                let p: int32* = &b[0];
                if (p != null) {
                    p += 1;
                    return need(p);
                }
                return 0;
            }
            """
        )


# -------------------------------------------------------------------- imports

LIB = """
import "libc/stdlib";
fn alloc<T>(n: uint64) -> T* {
    return malloc(n * sizeof(T)) as T*;
}
fn dealloc<T>(p: T*) {
    free(p);
}
"""


def test_import_lib(tmp_path, capfd):
    (tmp_path / "alloc.mc").write_text(LIB)
    main = tmp_path / "main.mc"
    main.write_text(
        """
        import "alloc";
        import "libc/stdio";
        fn main() -> int32 {
            let p = alloc<int32>(3);
            p[0] = 7;
            printf("%d\\n", p[0]);
            dealloc(p);
            return 0;
        }
        """
    )
    assert run_path(main) == 0
    assert capfd.readouterr().out == "7\n"


def test_diamond_import_loads_once(tmp_path):
    (tmp_path / "common.mc").write_text("fn one() -> int32 { return 1; }")
    (tmp_path / "a.mc").write_text('import "common";\nfn two() -> int32 { return one() + 1; }')
    (tmp_path / "b.mc").write_text('import "common";\nfn three() -> int32 { return one() + 2; }')
    main = tmp_path / "main.mc"
    main.write_text(
        'import "a";\nimport "b";\n'
        "fn main() -> int32 { return two() + three(); }"
    )
    assert run_path(main) == 5  # would fail with a duplicate-function error otherwise


def test_missing_import_is_clean_error(tmp_path):
    main = tmp_path / "main.mc"
    main.write_text('import "nowhere";\nfn main() -> int32 { return 0; }')
    with pytest.raises(LangError, match="cannot import 'nowhere'"):
        run_path(main)


def test_import_resolves_through_stdlib_path(tmp_path, capfd):
    # `import "std/memory";` works from anywhere: lib/ is on the search path.
    main = tmp_path / "main.mc"
    main.write_text(
        """
        import "std/memory";
        import "libc/stdio";
        fn main() -> int32 {
            let p = alloc<int32>(1);
            *p = 7;
            printf("%d\\n", *p);
            dealloc(p);
            return 0;
        }
        """
    )
    assert run_path(main) == 0
    assert capfd.readouterr().out == "7\n"


def test_relative_import_wins_over_search_path(tmp_path):
    # A file next to the importer shadows a same-named one on the search path
    # (here the bundled std/memory), so a local std/memory.mc wins.
    (tmp_path / "std").mkdir()
    (tmp_path / "std" / "memory.mc").write_text(
        "fn marker() -> int32 { return 42; }"
    )
    main = tmp_path / "main.mc"
    main.write_text('import "std/memory";\nfn main() -> int32 { return marker(); }')
    assert run_path(main) == 42
