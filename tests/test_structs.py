"""Structs, member access, null, and braceless control-flow bodies."""

import pytest

from mcc.errors import LangError
from mcc.nodes import If, Member, NullLit, StoreMember
from helpers import compile_ir, parse, run, run_path


# --------------------------------------------------------------------- parser


def test_struct_declaration():
    (decl,) = parse("struct point { x: int32; y: int32; }").structs
    assert decl.name == "point"
    assert decl.type_params == []
    assert [(n, str(t)) for n, t in decl.fields] == [("x", "int32"), ("y", "int32")]


def test_generic_struct_declaration():
    (decl,) = parse("struct node<T> { value: T; next: struct node<T>*; }").structs
    assert decl.type_params == ["T"]
    assert str(decl.fields[1][1]) == "node<T>*"


def test_member_access_parses():
    (stmt,) = parse("fn f() { let a = p->data[0].x; }").functions[0].body
    outer = stmt.value
    assert isinstance(outer, Member) and outer.field == "x" and not outer.arrow
    inner = outer.base.base  # Index's base
    assert isinstance(inner, Member) and inner.field == "data" and inner.arrow


def test_member_store_targets():
    body = parse("fn f() { p->x = 1; v.y = 2; }").functions[0].body
    assert isinstance(body[0], StoreMember) and body[0].arrow
    assert isinstance(body[1], StoreMember) and not body[1].arrow


def test_braceless_bodies():
    (stmt,) = parse("fn f() { if (a) return; else g(); }").functions[0].body
    assert isinstance(stmt, If)
    assert len(stmt.then) == 1 and len(stmt.otherwise) == 1


def test_null_parses():
    (stmt,) = parse("fn f() { let p: int32* = null; }").functions[0].body
    assert isinstance(stmt.value, NullLit)


# -------------------------------------------------------------------- codegen

POINT = "struct point { x: int32; y: int32; }\n"


def test_struct_emits_identified_type():
    ir_text = compile_ir(POINT + "fn get(p: struct point*) -> int32 { return p->x; }")
    assert '%"point" = type {i32, i32}' in ir_text
    assert "getelementptr inbounds" in ir_text


def test_generic_struct_monomorphizes():
    ir_text = compile_ir(
        "struct pair<A, B> { first: A; second: B; }\n"
        "fn f(p: pair<int32, float64>*) -> float64 { return p->second; }"
    )
    assert '%"pair<int32, float64>" = type {i32, double}' in ir_text


def test_struct_keyword_is_optional_in_types():
    compile_ir(POINT + "fn get(p: point*) -> int32 { return p->x; }")


@pytest.mark.parametrize(
    "source, message",
    [
        (POINT + "fn f(p: struct point*) -> int32 { return p->z; }", "no field 'z'"),
        (
            POINT + "fn f(p: struct point) -> int32 { return p->x; }",
            "'->' requires a struct pointer",
        ),
        (POINT + "fn f(x: int32) -> int32 { return x.y; }", "int32 is not a struct"),
        (POINT + "struct point { x: int32; } fn main() {}", "already defined"),
        ("fn f(p: point*) {}", "unknown type 'point'"),
        (
            "struct pair<A, B> { a: A; b: B; }\nfn f(p: pair<int32>*) {}",
            "expects 2 type argument",
        ),
        (
            'import "libc/stdio";\n'
            + POINT
            + 'fn f(p: struct point) { printf("%d", p); }',
            "cannot pass a struct to a variadic",
        ),
        (
            POINT + "fn f(p: struct point) -> int32 { return p as int32; }",
            "cannot cast point to int32",
        ),
    ],
)
def test_struct_errors(source, message):
    with pytest.raises(LangError, match=message):
        compile_ir(source)


def test_conflict_detected_inside_generic_struct_pattern():
    with pytest.raises(LangError, match="conflicting types for type parameter T"):
        compile_ir(
            "struct box<T> { value: T; }\n"
            "fn f<T>(a: box<T>*, b: box<T>*) {}\n"
            "fn main() -> int32 {\n"
            "    let a: box<int32>* = null;\n"
            "    let b: box<int64>* = null;\n"
            "    f(a, b);\n"
            "    return 0;\n"
            "}"
        )


def test_sizeof_struct_includes_padding():
    # uint8 followed by int64 pads to 16 bytes.
    assert (
        run(
            "struct padded { a: uint8; b: int64; }\n"
            "fn main() -> int32 { return sizeof(struct padded) as int32; }"
        )
        == 16
    )


# ------------------------------------------------------------------ execution


def test_struct_fields_roundtrip(capfd):
    run(
        """
        import "libc/stdio";
        import "libc/stdlib";
        struct point { x: int32; y: int32; }
        fn main() -> int32 {
            let p = malloc(sizeof(struct point)) as struct point*;
            p->x = 3;
            p->y = 4;
            let copy = *p;
            copy.x = 30;
            printf("%d %d %d\\n", copy.x, copy.y, p->x);
            free(p);
            return 0;
        }
        """
    )
    assert capfd.readouterr().out == "30 4 3\n"


def test_null_and_linked_list(capfd):
    run(
        """
        import "libc/stdio";
        import "libc/stdlib";
        struct node<T> { value: T; next: struct node<T>*; }
        fn push(head: struct node<int64>*, value: int64) -> struct node<int64>* {
            let n = malloc(sizeof(struct node<int64>)) as struct node<int64>*;
            n->value = value;
            n->next = head;
            return n;
        }
        fn main() -> int32 {
            let head: struct node<int64>* = null;
            let i: int64 = 1;
            while (i <= 4) {
                head = push(head, i * 1000000000);
                i = i + 1;
            }
            let total: int64 = 0;
            until (head == null) {
                total = total + head->value;
                let next = head->next;
                free(head);
                head = next;
            }
            printf("%lld\\n", total);
            return 0;
        }
        """
    )
    assert capfd.readouterr().out == "10000000000\n"


def test_address_of_field(capfd):
    run(
        """
        import "libc/stdio";
        import "libc/stdlib";
        struct point { x: int32; y: int32; }
        fn bump(v: int32*) { *v = *v + 1; }
        fn main() -> int32 {
            let p = malloc(sizeof(struct point)) as struct point*;
            p->x = 9;
            bump(&p->x);
            printf("%d\\n", p->x);
            free(p);
            return 0;
        }
        """
    )
    assert capfd.readouterr().out == "10\n"


def test_memory_lib_copies(tmp_path, capfd):
    from pathlib import Path

    lib_dir = Path(__file__).resolve().parents[1] / "libmc"
    main = tmp_path / "main.mc"
    main.write_text(
        f'import "{lib_dir / "memory"}";\n'
        """
        import \"libc/stdio\";
        struct point { x: int32; y: int32; }
        fn main() -> int32 {
            let src = alloc<int64>(3);
            src[0] = 10; src[1] = 20; src[2] = 30;
            let a = alloc<int64>(3);
            let b = alloc<int64>(3);
            copy_bytes(a, src, 3);
            copy_items(b, src, 3);
            printf("%lld %lld\\n", a[2], b[2]);

            // item-by-item copy of struct elements
            let pts = alloc<struct point>(2);
            pts[0].x = 1; pts[0].y = 2;
            pts[1].x = 3; pts[1].y = 4;
            let out = alloc<struct point>(2);
            copy_items(out, pts, 2);
            printf("%d %d\\n", out[1].x, out[1].y);

            dealloc(src); dealloc(a); dealloc(b);
            dealloc(pts); dealloc(out);
            return 0;
        }
        """
    )
    assert run_path(main) == 0
    assert capfd.readouterr().out == "30 30\n3 4\n"


def test_list_lib(tmp_path, capfd):
    from pathlib import Path

    lib_dir = Path(__file__).resolve().parents[1] / "libmc"
    main = tmp_path / "main.mc"
    main.write_text(
        f'import "{lib_dir / "list"}";\n'
        """
        import \"libc/stdio\";
        fn main() -> int32 {
            let floats = alloc<struct list<float64>>(1);
            list_init(floats, 1);
            let i: int32 = 0;
            while (i < 5) {
                list_append(floats, i as float64 / 2.0);
                i = i + 1;
            }
            let v: float64 = 0.0;
            list_get(floats, 3, &v);
            printf("%f %llu\\n", v, floats->length);
            list_destroy(floats);
            dealloc(floats);
            return 0;
        }
        """
    )
    assert run_path(main) == 0
    assert capfd.readouterr().out == "1.500000 5\n"


def test_list_iterator(tmp_path, capfd):
    from pathlib import Path

    lib_dir = Path(__file__).resolve().parents[1] / "libmc"
    main = tmp_path / "main.mc"
    main.write_text(
        f'import "{lib_dir / "list"}";\n'
        """
        import \"libc/stdio\";
        fn main() -> int32 {
            let xs: struct list<int32>;
            list_init(&xs, 2);
            list_append(&xs, 10);
            list_append(&xs, 20);
            list_append(&xs, 30);          // grows past the initial capacity
            defer list_destroy(&xs);

            let sum: int32 = 0;
            {
                let it = list_it(&xs);        // list_it/list_next protocol
                let x: int32;
                while (list_next(&it, &x)) {
                    sum = sum + x;
                }
            }
            printf("%d\\n", sum);          // 60
            return 0;
        }
        """
    )
    assert run_path(main) == 0
    assert capfd.readouterr().out == "60\n"


def test_for_in_loop(tmp_path, capfd):
    from pathlib import Path

    lib_dir = Path(__file__).resolve().parents[1] / "libmc"
    main = tmp_path / "main.mc"
    main.write_text(
        f'import "{lib_dir / "list"}";\n'
        """
        import \"libc/stdio\";
        fn main() -> int32 {
            let xs: struct list<int32>;
            list_init(&xs, 2);
            list_append(&xs, 10);
            list_append(&xs, 20);
            list_append(&xs, 30);
            defer list_destroy(&xs);

            let sum: int32 = 0;
            for v in &xs {               // element type inferred from next
                if (v == 30) { break; }  // break/continue work in a for
                sum = sum + v;
            }
            printf("%d\\n", sum);       // 10 + 20 = 30
            return 0;
        }
        """
    )
    assert run_path(main) == 0
    assert capfd.readouterr().out == "30\n"
