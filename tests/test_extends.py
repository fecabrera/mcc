"""struct ... extends Base: prefix-layout specialization.

A struct may `extends` a single base; the base's fields are spliced in front
of its own, so the base sits at offset 0 and a pointer to the derived struct
is layout-compatible with a pointer to the base (an explicit `as` cast). The
base's @packed/@align/@volatile are inherited. With no body of its own
(`struct B extends A;`) the struct is a distinct same-layout specialization.
"""

import pytest

from mcc.errors import LangError
from helpers import compile_ir, parse, run


# --------------------------------------------------------------------- parser

def test_extends_with_body_parses():
    (decl,) = parse(
        "struct point { x: int32; y: int32; }\n"
        "struct point3 extends point { z: int32; }\n"
    ).structs[1:]
    assert decl.name == "point3"
    assert str(decl.base) == "point"
    assert [n for n, _ in decl.fields] == ["z"]  # only its own; base spliced later


def test_specialization_form_parses():
    (decl,) = parse(
        "struct handle { id: int64; }\n"
        "struct user_id extends handle;\n"
    ).structs[1:]
    assert decl.name == "user_id"
    assert str(decl.base) == "handle"
    assert decl.fields == []


def test_extends_pointer_base_rejected():
    with pytest.raises(LangError, match="can only extend a struct name"):
        parse("struct a { x: int32; }\nstruct b extends a* { y: int32; }\n")


def test_no_body_without_base_rejected():
    with pytest.raises(LangError):
        parse("struct a;\n")


# -------------------------------------------------------------------- layout

POINT = "struct point { x: int32; y: int32; }\n"
POINT3 = POINT + "struct point3 extends point { z: int32; }\n"


def test_inherited_fields_are_accessible():
    assert run(
        POINT3 +
        "fn main() -> int32 {\n"
        "    let p: struct point3;\n"
        "    p.x = 1; p.y = 2; p.z = 3;\n"
        "    return p.x + p.y + p.z;\n"
        "}\n"
    ) == 6


def test_size_includes_base_fields():
    assert run(POINT3 + "fn main() -> int32 { return sizeof(struct point3) as int32; }") == 12


def test_specialization_has_base_size():
    assert run(
        "struct handle { id: int64; }\n"
        "struct user_id extends handle;\n"
        "fn main() -> int32 { return sizeof(struct user_id) as int32; }\n"
    ) == 8


def test_upcast_reads_base_fields_through_base_pointer():
    # The base sits at offset 0, so a derived pointer cast to the base reads
    # the same storage.
    assert run(
        POINT3 +
        "fn main() -> int32 {\n"
        "    let p: struct point3;\n"
        "    p.x = 4; p.y = 5; p.z = 6;\n"
        "    let base = &p as struct point*;\n"
        "    return base->x + base->y;\n"
        "}\n"
    ) == 9


def test_function_over_base_accepts_upcast_derived():
    assert run(
        POINT3 +
        "fn sum2(p: struct point*) -> int32 { return p->x + p->y; }\n"
        "fn main() -> int32 {\n"
        "    let p: struct point3;\n"
        "    p.x = 10; p.y = 20; p.z = 30;\n"
        "    return sum2(&p as struct point*);\n"
        "}\n"
    ) == 30


def test_derived_is_distinct_type_without_cast():
    # No implicit upcast: passing a derived pointer where the base is expected
    # is a type error until cast.
    with pytest.raises(LangError):
        run(
            POINT3 +
            "fn sum2(p: struct point*) -> int32 { return p->x + p->y; }\n"
            "fn main() -> int32 { let p: struct point3; return sum2(&p); }\n"
        )


# -------------------------------------------------------- inherited attributes

def test_volatile_is_inherited():
    ir_text = compile_ir(
        "@volatile struct reg { v: uint32; }\n"
        "struct reg2 extends reg { w: uint32; }\n"
        "fn write(r: struct reg2*) { r->v = 1; r->w = 2; }\n"
    )
    # Both the inherited field and the struct's own field store volatile.
    assert ir_text.count("store volatile") == 2


def test_align_is_inherited():
    assert run(
        "@align(16) struct a { a: int32; }\n"
        "struct b extends a { b: int32; }\n"
        "fn main() -> int32 { return sizeof(struct b) as int32; }\n"
    ) == 16


def test_packed_is_inherited():
    # @packed base (a: uint8, b: int64 -> 9 bytes); derived adds a uint8 -> 10.
    assert run(
        "@packed struct p { a: uint8; b: int64; }\n"
        "struct q extends p { c: uint8; }\n"
        "fn main() -> int32 { return sizeof(struct q) as int32; }\n"
    ) == 10


# ------------------------------------------------------------------- errors

def use_b(src):
    return src + "fn main() -> int32 { return sizeof(struct b) as int32; }\n"


def test_packed_on_unpacked_base_rejected():
    with pytest.raises(LangError, match="cannot be @packed unless its base is"):
        run(use_b("struct a { x: int32; }\n@packed struct b extends a { y: uint8; }\n"))


def test_field_collision_with_base_rejected():
    with pytest.raises(LangError, match="already defined in base"):
        run(use_b("struct a { x: int32; }\nstruct b extends a { x: int32; }\n"))


def test_cyclic_extends_rejected():
    with pytest.raises(LangError, match="cannot extend itself"):
        run(use_b("struct a extends b { p: int32; }\nstruct b extends a { q: int32; }\n"))


def test_extends_non_struct_rejected():
    with pytest.raises(LangError, match="not a struct"):
        run(use_b("struct b extends int32 { y: int32; }\n"))


# --------------------------------------------------------------------- generic

PAIR = "struct pair<K, V> { key: K; value: V; }\n"
ENTRY = PAIR + "struct entry<K, V> extends pair<K, V> { state: uint8; }\n"


def test_generic_extends_inherited_fields():
    assert run(
        ENTRY +
        "fn main() -> int32 {\n"
        "    let e: struct entry<int32, int64>;\n"
        "    e.key = 7; e.value = 100; e.state = 1;\n"
        "    return e.key + (e.value as int32) + (e.state as int32);\n"
        "}\n"
    ) == 108


def test_generic_extends_size():
    # pair<int32, int64>: key@0, value@8 -> 16; entry adds state@16 -> 24 (align 8).
    assert run(
        ENTRY + "fn main() -> int32 { return sizeof(struct entry<int32, int64>) as int32; }"
    ) == 24


def test_generic_upcast_to_generic_base():
    assert run(
        ENTRY +
        "fn main() -> int32 {\n"
        "    let e: struct entry<int32, int32>;\n"
        "    e.key = 11; e.value = 22; e.state = 0;\n"
        "    let base = &e as struct pair<int32, int32>*;\n"
        "    return base->key + base->value;\n"
        "}\n"
    ) == 33


def test_generic_base_field_collision_rejected():
    with pytest.raises(LangError, match="already defined in base"):
        run(
            "struct pair<K, V> { key: K; value: V; }\n"
            "struct bad<K, V> extends pair<K, V> { key: K; }\n"
            "fn main() -> int32 { return sizeof(struct bad<int32, int32>) as int32; }\n"
        )
