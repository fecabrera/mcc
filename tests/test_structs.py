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

    lib_dir = Path(__file__).resolve().parents[1] / "lib" / "std"
    main = tmp_path / "main.mc"
    main.write_text(
        f'import "{lib_dir / "memory"}";\n'
        """
        import \"libc/stdio\";
        struct point { x: int32; y: int32; }
        fn main() -> int32 {
            let src = alloc<int64>(3);
            if (src == null) return 1;   // each guard narrows one heap pointer
            src[0] = 10; src[1] = 20; src[2] = 30;
            let a = alloc<int64>(3);
            if (a == null) return 1;
            let b = alloc<int64>(3);
            if (b == null) return 1;
            bytecopy(a, src, 3);
            copy(b, src, 3);
            printf("%lld %lld\\n", a[2], b[2]);

            // item-by-item copy of struct elements
            let pts = alloc<struct point>(2);
            if (pts == null) return 1;
            pts[0].x = 1; pts[0].y = 2;
            pts[1].x = 3; pts[1].y = 4;
            let out = alloc<struct point>(2);
            if (out == null) return 1;
            copy(out, pts, 2);
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

    lib_dir = Path(__file__).resolve().parents[1] / "lib" / "std"
    main = tmp_path / "main.mc"
    main.write_text(
        f'import "{lib_dir / "list"}";\n'
        """
        import \"libc/stdio\";
        fn main() -> int32 {
            let floats = alloc<struct list<float64>>(1);
            if (floats == null) { return 1; }  // proves floats for the receivers
            list_init(floats, 1);
            let i: int32 = 0;
            while (i < 5) {
                list_push(floats!, i as float64 / 2.0);  // mut receiver in a loop
                i = i + 1;                               // drops the fact, so !
            }
            let v: float64 = 0.0;
            list_get(floats, 3, v);
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

    lib_dir = Path(__file__).resolve().parents[1] / "lib" / "std"
    main = tmp_path / "main.mc"
    main.write_text(
        f'import "{lib_dir / "list"}";\n'
        """
        import \"libc/stdio\";
        fn main() -> int32 {
            let xs: struct list<int32>;
            list_init(&xs, 2);
            list_push(&xs, 10);
            list_push(&xs, 20);
            list_push(&xs, 30);          // grows past the initial capacity
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

    lib_dir = Path(__file__).resolve().parents[1] / "lib" / "std"
    main = tmp_path / "main.mc"
    main.write_text(
        f'import "{lib_dir / "list"}";\n'
        """
        import \"libc/stdio\";
        fn main() -> int32 {
            let xs: struct list<int32>;
            list_init(&xs, 2);
            list_push(&xs, 10);
            list_push(&xs, 20);
            list_push(&xs, 30);
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


def test_list_append_concatenates(capfd):
    run(
        """
        import "std/list";
        import "libc/stdio";
        fn main() -> int32 {
            let a: struct list<int32>;
            list_init(&a, 2);
            list_push(&a, 1);
            list_push(&a, 2);
            let b: struct list<int32>;
            list_init(&b, 2);
            list_push(&b, 3);
            list_push(&b, 4);
            list_append(&a, b as slice<int32>);     // a becomes [1, 2, 3, 4]
            let sum: int32 = 0;
            for v in &a { sum = sum + v; }
            printf("%d %llu\\n", sum, a.length);    // 10 4
            list_destroy(&a);
            list_destroy(&b);
            return 0;
        }
        """
    )
    assert capfd.readouterr().out == "10 4\n"


def test_list_init_from_slice_deep_copies(capfd):
    run(
        """
        import "std/list";
        import "libc/stdio";
        fn main() -> int32 {
            let a: struct list<int32>;
            list_init(&a, 4);
            list_push(&a, 7);
            list_push(&a, 8);
            let b: struct list<int32>;
            list_init(&b, a as slice<int32>);       // independent copy
            list_set(&a, 0, 99);                    // mutate the original
            let first: int32 = 0;
            list_get(&b, 0, first);                // copy is unaffected
            printf("%d %llu\\n", first, b.length);  // 7 2
            list_destroy(&a);
            list_destroy(&b);
            return 0;
        }
        """
    )
    assert capfd.readouterr().out == "7 2\n"


def test_list_init_from_array_builds_a_private_copy(capfd):
    run(
        """
        import "std/list";
        import "libc/stdio";
        fn main() -> int32 {
            let raw: int32[3];
            raw[0] = 5; raw[1] = 6; raw[2] = 7;
            let xs: struct list<int32>;
            list_init(&xs, &raw[0], 3);
            raw[0] = 0;                             // the list owns its own copy
            let sum: int32 = 0;
            for v in &xs { sum = sum + v; }
            printf("%d %llu\\n", sum, xs.length);   // 18 3
            list_destroy(&xs);
            return 0;
        }
        """
    )
    assert capfd.readouterr().out == "18 3\n"


# ------------------------------------------------ flexible array members (FAM)

# A trailing `field: T[]` lays out as a zero-length array, last in the struct.
PACKET = "struct packet { length: int32; data: int32[]; }\n"


def test_flexible_array_member_parses():
    (decl,) = parse(PACKET).structs
    assert [(n, str(t)) for n, t in decl.fields] == [
        ("length", "int32"),
        ("data", "int32[]"),
    ]


def test_flexible_array_member_adds_nothing_to_sizeof():
    # The FAM contributes 0; sizeof is just the int32 length.
    assert (
        run(PACKET + "fn main() -> int32 { return sizeof(struct packet) as int32; }")
        == 4
    )


def test_flexible_array_member_emits_zero_length_array():
    ir_text = compile_ir(PACKET + "fn f(p: struct packet*) -> int32 { return p->length; }")
    assert '%"packet" = type {i32, [0 x i32]}' in ir_text


@pytest.mark.parametrize(
    "source, message",
    [
        (
            "struct bad { data: int32[]; length: int32; }\n"
            "fn f(b: struct bad*) {}",
            "must be the struct's last field",
        ),
        (
            "struct bad { n: int32; data: int32[][3]; }\n"
            "fn f(b: struct bad*) {}",
            "only array dimension",
        ),
        (
            "struct base { n: int32; data: int32[]; }\n"
            "struct derived extends base { extra: int32; }\n"
            "fn f(d: struct derived*) {}",
            "ends in a flexible array member",
        ),
        (
            PACKET + "fn f() { let p = struct packet { length = 1, data = 0 }; }",
            "flexible array member with no storage",
        ),
        (
            'import "std/list";\n' + PACKET
            + "fn f(p: struct packet*) { let s = p->data as slice<int32>; }",
            "cannot borrow a flexible array member",
        ),
    ],
)
def test_flexible_array_member_errors(source, message):
    with pytest.raises(LangError, match=message):
        compile_ir(source)


def test_flexible_array_member_roundtrips(capfd):
    run(
        """
        import "libc/stdio";
        import "libc/stdlib";
        struct packet { length: int32; data: int32[]; }
        fn main() -> int32 {
            let n: int32 = 4;
            let p = malloc(sizeof(struct packet)
                           + (n as uint64) * sizeof(int32)) as struct packet*;
            p->length = n;
            let i: int32 = 0;
            while (i < n) { p->data[i] = i * i; i = i + 1; }
            let sum: int32 = 0;
            i = 0;
            while (i < p->length) { sum = sum + p->data[i]; i = i + 1; }
            printf("%d\\n", sum);                    // 0+1+4+9 = 14
            free(p as byte*);
            return 0;
        }
        """
    )
    assert capfd.readouterr().out == "14\n"


def test_generic_flexible_array_member(capfd):
    run(
        """
        import "libc/stdio";
        import "std/memory";
        struct vec<T> { length: uint64; items: T[]; }
        fn main() -> int32 {
            let v = alloc<byte>(sizeof(struct vec<int32>)
                                + 3 * sizeof(int32)) as struct vec<int32>*;
            v->length = 3;
            v->items[0] = 10; v->items[1] = 20; v->items[2] = 30;
            let sum: int32 = 0;
            let i: uint64 = 0;
            while (i < v->length) { sum = sum + v->items[i]; i = i + 1; }
            printf("%llu %d\\n", sizeof(struct vec<int32>), sum);  // 8 60
            dealloc(v as byte*);
            return 0;
        }
        """
    )
    assert capfd.readouterr().out == "8 60\n"


# ------------------------------------------------------- alignof and offsetof

# uint8 at 0, int64 padded to 8, uint16 at 16; sizeof 24, alignment 8.
MIXED = "struct mixed { a: uint8; b: int64; c: uint16; }\n"


def test_alignof_parses():
    from mcc.nodes import AlignOf

    (stmt,) = parse("fn f() { let n = alignof(int64); }").functions[0].body
    assert isinstance(stmt.value, AlignOf)
    assert str(stmt.value.type_name) == "int64"


def test_offsetof_parses():
    from mcc.nodes import OffsetOf

    (stmt,) = parse("fn f() { let n = offsetof(struct s, field); }").functions[0].body
    assert isinstance(stmt.value, OffsetOf)
    assert str(stmt.value.type_name) == "s" and stmt.value.field == "field"


def test_alignof_scalars():
    assert run(
        "fn main() -> int32 { return (alignof(int64) + alignof(uint8)) as int32; }"
    ) == 9


def test_alignof_struct():
    assert run(MIXED + "fn main() -> int32 { return alignof(struct mixed) as int32; }") == 8


def test_alignof_of_a_variable():
    # Like sizeof, a bare name in scope is the variable's type.
    assert run(
        MIXED + "fn main() -> int32 { let m: struct mixed; return alignof(m) as int32; }"
    ) == 8


def test_offsetof_honors_padding():
    assert run(MIXED + "fn main() -> int32 { return offsetof(struct mixed, c) as int32; }") == 16


def test_offsetof_packed_has_no_padding():
    assert run(
        "@packed struct wire { tag: uint8; length: uint64; }\n"
        "fn main() -> int32 { return offsetof(struct wire, length) as int32; }"
    ) == 1


def test_offsetof_follows_extends():
    assert run(
        "struct base { x: int32; y: int32; }\n"
        "struct derived extends base { z: int32; }\n"
        "fn main() -> int32 { return offsetof(struct derived, z) as int32; }"
    ) == 8


def test_offsetof_in_generic_struct():
    assert run(
        "struct box<T> { tag: uint8; value: T; }\n"
        "fn main() -> int32 { return offsetof(struct box<int64>, value) as int32; }"
    ) == 8


def test_alignof_and_offsetof_in_const():
    assert run(
        MIXED + "const OFF = offsetof(struct mixed, b);\n"
        "const AL = alignof(int64);\n"
        "fn main() -> int32 { return (OFF + AL) as int32; }"  # 8 + 8
    ) == 16


def test_offsetof_of_a_flexible_array_member(capfd):
    # The FAM's offset is where its elements physically begin -- after the
    # header, padded to the element's alignment. sizeof rounds that up to the
    # struct alignment (here it includes a byte of trailing padding), so offsetof
    # is the tight base for a FAM allocation. alignof counts the FAM element.
    run(
        """
        import "libc/stdio";
        struct s { a: int64; b: uint8; data: uint8[]; }
        fn main() -> int32 {
            printf("%llu %llu %llu\\n",
                sizeof(struct s),            // 16 (rounded to align 8)
                offsetof(struct s, data),    // 9  (right after a, b)
                alignof(struct s));          // 8
            return 0;
        }
        """
    )
    assert capfd.readouterr().out == "16 9 8\n"


def test_alignof_struct_counts_a_flexible_array_member():
    # A 1-byte header but an int64 FAM element raises the alignment to 8.
    assert run(
        "struct buf { n: uint8; data: int64[]; }\n"
        "fn main() -> int32 { return alignof(struct buf) as int32; }"
    ) == 8


@pytest.mark.parametrize(
    "source, message",
    [
        (
            "fn main() -> int32 { return offsetof(int32, x) as int32; }",
            "offsetof needs a struct",
        ),
        (
            "struct point { x: int32; y: int32; }\n"
            "fn main() -> int32 { return offsetof(struct point, z) as int32; }",
            "no field 'z'",
        ),
    ],
)
def test_offsetof_errors(source, message):
    with pytest.raises(LangError, match=message):
        compile_ir(source)
