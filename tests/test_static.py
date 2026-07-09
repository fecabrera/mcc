"""@static: file-scoped names that other files can freely reuse."""

import pytest

from mcc.errors import LangError
from helpers import compile_ir, run, run_path


def test_same_static_name_in_two_files(tmp_path):
    # The collision @private couldn't solve: both files own a `scale`.
    (tmp_path / "a.mc").write_text(
        "@static\nfn scale(x: int32) -> int32 { return x * 2; }\n"
        "fn double_it(x: int32) -> int32 { return scale(x); }"
    )
    (tmp_path / "b.mc").write_text(
        "@static\nfn scale(x: int32) -> int32 { return x * 10; }\n"
        "fn tenfold(x: int32) -> int32 { return scale(x); }"
    )
    main = tmp_path / "main.mc"
    main.write_text(
        'import "a";\nimport "b";\n'
        "fn main() -> int32 { return double_it(3) + tenfold(3); }"
    )
    assert run_path(main) == 36  # 6 + 30


def test_static_shadows_imported_public(tmp_path):
    (tmp_path / "lib.mc").write_text("fn answer() -> int32 { return 1; }")
    main = tmp_path / "main.mc"
    main.write_text(
        'import "lib";\n'
        "@static\nfn answer() -> int32 { return 2; }\n"
        "fn main() -> int32 { return answer(); }"
    )
    assert run_path(main) == 2


def test_static_fn_invisible_to_other_files(tmp_path):
    (tmp_path / "lib.mc").write_text("@static\nfn helper() -> int32 { return 1; }")
    main = tmp_path / "main.mc"
    main.write_text('import "lib";\nfn main() -> int32 { return helper(); }')
    with pytest.raises(LangError, match="undefined function 'helper'"):
        run_path(main)


def test_same_static_generic_in_two_files(tmp_path):
    (tmp_path / "a.mc").write_text(
        "@static\nfn pick<T>(x: T, y: T) -> T { return x; }\n"
        "fn first64(x: int64, y: int64) -> int64 { return pick(x, y); }"
    )
    (tmp_path / "b.mc").write_text(
        "@static\nfn pick<T>(x: T, y: T) -> T { return y; }\n"
        "fn second64(x: int64, y: int64) -> int64 { return pick(x, y); }"
    )
    main = tmp_path / "main.mc"
    main.write_text(
        'import "a";\nimport "b";\n'
        "fn main() -> int32 { return (first64(1, 2) + second64(10, 20)) as int32; }"
    )
    assert run_path(main) == 21  # 1 + 20


def test_same_static_struct_in_two_files(tmp_path):
    (tmp_path / "a.mc").write_text(
        "@static\nstruct blob { x: uint8; }\n"
        "fn a_size() -> uint64 { return sizeof(struct blob); }"
    )
    (tmp_path / "b.mc").write_text(
        "@static\nstruct blob { x: int64; y: int64; }\n"
        "fn b_size() -> uint64 { return sizeof(struct blob); }"
    )
    main = tmp_path / "main.mc"
    main.write_text(
        'import "a";\nimport "b";\n'
        "fn main() -> int32 { return (a_size() * 100 + b_size()) as int32; }"
    )
    assert run_path(main) == 116  # 1 byte and 16 bytes


def test_static_and_public_same_name_same_file(tmp_path):
    main = tmp_path / "main.mc"
    main.write_text(
        "@static\nfn f() -> int32 { return 1; }\n"
        "fn f() -> int32 { return 2; }\n"
        "fn main() -> int32 { return f(); }"
    )
    with pytest.raises(LangError, match="function 'f' already defined"):
        run_path(main)


def test_static_global_has_zero_initialized_storage():
    # A @static let is a file-scoped variable with its own storage, unlike an
    # @extern let which is only a declaration.
    ir_text = compile_ir(
        "@static let counter: int32;\n"
        "fn main() -> int32 { counter = counter + 1; return counter; }"
    )
    assert "internal global i32 0" in ir_text


def test_static_global_persists_across_calls():
    source = """
    @static let calls: int32;
    fn tick() -> int32 { calls = calls + 1; return calls; }
    fn main() -> int32 { tick(); tick(); return tick(); }
    """
    assert run(source) == 3


def test_static_list_global():
    source = """
    @static let cache: int32[4];
    fn put(i: int32, v: int32) { cache[i] = v; }
    fn get(i: int32) -> int32 { return cache[i]; }
    fn main() -> int32 { put(0, 40); put(3, 2); return get(0) + get(3); }
    """
    assert run(source) == 42


def test_same_static_global_in_two_files(tmp_path):
    (tmp_path / "a.mc").write_text(
        "@static let n: int32;\nfn bump_a() -> int32 { n = n + 10; return n; }"
    )
    main = tmp_path / "main.mc"
    main.write_text(
        'import "a";\n'
        "@static let n: int32;\n"
        "fn bump() -> int32 { n = n + 1; return n; }\n"
        "fn main() -> int32 { return bump() + bump_a() + bump(); }"  # 1 + 10 + 2
    )
    assert run_path(main) == 13


def test_static_global_invisible_to_other_files(tmp_path):
    (tmp_path / "a.mc").write_text("@static let secret: int32;")
    main = tmp_path / "main.mc"
    main.write_text('import "a";\nfn main() -> int32 { return secret; }')
    with pytest.raises(LangError, match="undefined variable 'secret'"):
        run_path(main)


def test_top_level_let_requires_extern_or_static():
    with pytest.raises(LangError, match="must be @extern or @static"):
        compile_ir("let loose: int32;\nfn main() -> int32 { return 0; }")


def test_static_initializer_takes_a_const_reference():
    # A @static initializer is a full constant expression, like a const's.
    assert (
        run(
            "const N: int32 = 42;\n"
            "@static let n: int32 = N;\n"
            "fn main() -> int32 { return n; }"
        )
        == 42
    )


def test_static_pointer_initialized_from_const_cast():
    source = (
        "struct reg {}\n"
        "const BASE: uint64 = 0x40001000;\n"
        "@static let r: struct reg* = BASE as struct reg*;\n"
    )
    # The integer->pointer cast folds to an inttoptr constant expression.
    assert "inttoptr" in compile_ir(source + "fn main() -> int32 { return 0; }")
    # And the address round-trips at runtime.
    assert (
        run(
            source
            + "fn main() -> int32 { if ((r as uint64) == BASE) return 1; return 0; }"
        )
        == 1
    )


def test_static_initializer_type_is_inferred():
    # An @static let with an initializer needs no type annotation; it infers
    # from the initializer, like a local `let`.
    assert (
        run(
            "@static let answer = 6 as int64 * 7 as int64;\n"
            "fn main() -> int32 { return answer as int32; }"
        )
        == 42
    )


def test_inferred_static_pointer_from_a_const_cast():
    # The motivating case: a memory-mapped register base, no annotation.
    source = (
        "struct reg { ctlr: uint32; }\n"
        "const BASE: uint64 = 0x09000000;\n"
        "@static let dev = BASE as struct reg*;\n"
    )
    assert "inttoptr" in compile_ir(source + "fn main() -> int32 { return 0; }")
    assert (
        run(
            source
            + "fn main() -> int32 { if ((dev as uint64) == BASE) return 1; return 0; }"
        )
        == 1
    )


def test_inferred_static_of_an_untyped_constant_is_ambiguous():
    # Like `let x = 5;`, an untyped constant has no inferable type.
    with pytest.raises(LangError, match="ambiguous"):
        compile_ir("@static let x = 5;\nfn main() -> int32 { return 0; }")


def test_inferred_static_list_literal_needs_an_annotation():
    with pytest.raises(LangError, match="array literal needs a type annotation"):
        compile_ir("@static let xs = [1, 2, 3];\nfn main() -> int32 { return 0; }")


def test_uninitialized_static_still_needs_a_type():
    with pytest.raises(LangError, match="needs a type"):
        compile_ir("@static let x;\nfn main() -> int32 { return 0; }")


# ----------------------------------------------- struct-literal globals


def test_static_struct_literal_global():
    ir_text = compile_ir(
        "struct point { x: int32; y: int32; }\n"
        "@static let p: struct point = point { x = 3, y = 4 };\n"
        "fn main() -> int32 { return p.x + p.y; }"
    )
    assert 'global %"point" {i32 3, i32 4}' in ir_text


def test_static_struct_literal_global_runs():
    assert (
        run(
            "struct point { x: int32; y: int32; }\n"
            "@static let p: struct point = point { x = 3, y = 4 };\n"
            "fn main() -> int32 { return p.x + p.y; }"
        )
        == 7
    )


def test_static_struct_literal_omitted_field_is_zero():
    # An omitted field with no default reads as zero, like the runtime literal.
    assert (
        run(
            "struct point { x: int32; y: int32; }\n"
            "@static let p: struct point = point { x = 5 };\n"
            "fn main() -> int32 { return p.x + p.y; }"
        )
        == 5
    )


def test_static_struct_literal_applies_field_default():
    # An omitted field that declares a default folds that default.
    assert (
        run(
            "struct config { limit: int32 = 100; used: int32; }\n"
            "@static let c: struct config = config { used = 5 };\n"
            "fn main() -> int32 { return c.limit + c.used; }"
        )
        == 105
    )


def test_static_struct_literal_nested_struct_and_array_fields():
    assert (
        run(
            "struct pt { x: int32; y: int32; }\n"
            "struct box { corner: struct pt; sizes: int32[3]; }\n"
            "@static let b: struct box = "
            "box { corner = pt { x = 1, y = 2 }, sizes = [10, 20, 30] };\n"
            "fn main() -> int32 { return b.corner.x + b.corner.y + b.sizes[2]; }"
        )
        == 33
    )


def test_static_generic_struct_literal_global():
    assert (
        run(
            "struct pair<A, B> { a: A; b: B; }\n"
            "@static let p: struct pair<int32, int32> = "
            "pair<int32, int32> { a = 6, b = 7 };\n"
            "fn main() -> int32 { return p.a + p.b; }"
        )
        == 13
    )


def test_static_struct_literal_wrong_type_is_rejected():
    with pytest.raises(LangError, match="expected a, got b"):
        compile_ir(
            "struct a { x: int32; }\n"
            "struct b { x: int32; }\n"
            "@static let g: struct a = b { x = 1 };\n"
            "fn main() -> int32 { return 0; }"
        )


def test_static_struct_literal_unknown_field_is_rejected():
    with pytest.raises(LangError, match="has no field 'z'"):
        compile_ir(
            "struct point { x: int32; y: int32; }\n"
            "@static let p: struct point = point { z = 1 };\n"
            "fn main() -> int32 { return 0; }"
        )


def test_static_struct_literal_duplicate_field_is_rejected():
    with pytest.raises(LangError, match="'x' is set twice"):
        compile_ir(
            "struct point { x: int32; y: int32; }\n"
            "@static let p: struct point = point { x = 1, x = 2 };\n"
            "fn main() -> int32 { return 0; }"
        )


def test_static_struct_literal_into_non_struct_is_rejected():
    with pytest.raises(LangError, match="a struct literal cannot initialize a"):
        compile_ir(
            "struct point { x: int32; y: int32; }\n"
            "@static let n: int32 = point { x = 1 };\n"
            "fn main() -> int32 { return 0; }"
        )


def test_static_generic_struct_literal_infers_args_from_annotation():
    # The literal gives no type arguments; the annotation supplies them, so the
    # concrete-type guard is skipped rather than resolving the bare literal.
    assert (
        run(
            "struct pair<A, B> { a: A; b: B; }\n"
            "@static let p: struct pair<int32, int32> = pair { a = 6, b = 7 };\n"
            "fn main() -> int32 { return p.a + p.b; }"
        )
        == 13
    )


def test_static_struct_literal_non_constant_field_is_rejected():
    with pytest.raises(LangError, match="must be a compile-time constant"):
        compile_ir(
            "struct point { x: int32; y: int32; }\n"
            "fn side() -> int32 { return 1; }\n"
            "@static let p: struct point = point { x = side() };\n"
            "fn main() -> int32 { return 0; }"
        )


def test_static_struct_and_union_global_survives_interface(tmp_path):
    # @static globals are file-local, so an emitted interface drops them while
    # keeping the public types they use; the round trip stays well-formed.
    (tmp_path / "a.mc").write_text(
        "struct point { x: int32; y: int32; }\n"
        "union num { i: int64; b: uint8; }\n"
        "@static let p: struct point = point { x = 1, y = 2 };\n"
        "@static let g: union num = num { b = 3 };\n"
        "fn identity(v: int32) -> int32 { return v; }\n"
    )
    import subprocess
    import sys

    out = subprocess.run(
        [sys.executable, "-m", "mcc", str(tmp_path / "a.mc"), "--emit-interface"],
        capture_output=True,
        text=True,
    )
    assert out.returncode == 0, out.stderr
    stub = (tmp_path / "a.mci").read_text()
    # The types export; the file-local globals do not.
    assert "struct point" in stub
    assert "union num" in stub
    assert "@static" not in stub
