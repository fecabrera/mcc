"""struct ... extends Base: prefix-layout specialization.

A struct may `extends` a single base; the base's fields are spliced in front
of its own, so the base sits at offset 0 and a pointer to the derived struct
is layout-compatible with a pointer to the base (an explicit `as` cast). The
base's @packed/@align/@volatile are inherited. With no body of its own
(`struct B extends A;`) the struct is a distinct same-layout specialization.
The base may also be a bare type parameter (`struct entry<T> extends T`, the
intrusive-container shape), resolved per instantiation.
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


def test_value_upcast_copies_base_prefix():
    # `derived as Base` (a value cast) yields a copy of the base prefix.
    assert run(
        POINT3 +
        "fn main() -> int32 {\n"
        "    let p: struct point3;\n"
        "    p.x = 2; p.y = 3; p.z = 99;\n"
        "    let base = p as struct point;\n"
        "    return base.x + base.y;\n"
        "}\n"
    ) == 5


def test_value_downcast_rejected():
    # The base lacks the derived's trailing fields, so widening by value would
    # read past it -- only the upcast direction is allowed.
    with pytest.raises(LangError, match="cannot cast"):
        run(POINT3 + "fn main() -> int32 {\n"
                     "    let p: struct point;\n"
                     "    let q = p as struct point3;\n"
                     "    return 0;\n"
                     "}\n")


def test_unrelated_struct_value_cast_rejected():
    with pytest.raises(LangError, match="cannot cast"):
        run("struct a { x: int32; }\n"
            "struct b { y: int64; }\n"
            "fn main() -> int32 { let v: struct a; let w = v as struct b; return 0; }\n")


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


# ------------------------------------------------ bare parameter as the base
#
# `struct entry<T> extends T` -- the intrusive-container shape. The bare
# parameter resolves through the instantiation's bindings, so each instance
# embeds its payload's fields as the layout prefix and appends its own;
# struct-ness is checked per instantiation, with the failure traced to the
# triggering request by an `in instantiation of ...` note.

MY = "struct my { a: int32; b: int64; }\n"
LLE = MY + "struct linked_list_entry<T> extends T { next: linked_list_entry<T>*; }\n"


def notes_of(source: str) -> tuple[LangError, list]:
    """Compile a failing source and return (error, [(note message, line)])."""
    with pytest.raises(LangError) as excinfo:
        compile_ir(source)
    return excinfo.value, [(n.message, n.line) for n in excinfo.value.notes]


def test_bare_param_payload_is_the_layout_prefix():
    ir_text = compile_ir(
        LLE +
        "fn main() -> int32 {\n"
        "    let e: struct linked_list_entry<struct my>;\n"
        "    e.a = 1; e.next = null;\n"
        "    return e.a;\n"
        "}\n"
    )
    # Payload fields first, the link appended last.
    assert (
        '%"linked_list_entry<my>" = type {i32, i64, %"linked_list_entry<my>"*}'
        in ir_text
    )


def test_bare_param_size_align_offset():
    # my: a@0, b@8 -> 16 with align 8; the entry appends next@16 -> 24.
    assert run(
        LLE +
        "fn main() -> int32 {\n"
        "    if (sizeof(struct linked_list_entry<struct my>) != 24) { return 1; }\n"
        "    if (alignof(struct linked_list_entry<struct my>) != 8) { return 2; }\n"
        "    if (offsetof(struct linked_list_entry<struct my>, next) != 16) { return 3; }\n"
        "    return 0;\n"
        "}\n"
    ) == 0


def test_bare_param_pointer_upcast_reaches_payload():
    assert run(
        LLE +
        "fn main() -> int32 {\n"
        "    let e: struct linked_list_entry<struct my>;\n"
        "    e.a = 7; e.b = 100; e.next = null;\n"
        "    let payload = &e as struct my*;\n"
        "    return payload->a + (payload->b as int32);\n"
        "}\n"
    ) == 107


def test_bare_param_value_upcast_copies_payload():
    assert run(
        LLE +
        "fn main() -> int32 {\n"
        "    let e: struct linked_list_entry<struct my>;\n"
        "    e.a = 2; e.b = 3; e.next = null;\n"
        "    let v = e as struct my;\n"
        "    return v.a + (v.b as int32);\n"
        "}\n"
    ) == 5


def test_bare_param_upcast_inside_generic_function():
    # `e as T*` upcasts to the bound parameter inside a generic body.
    assert run(
        "struct item { value: int32; }\n"
        "struct entry<T> extends T { next: entry<T>*; }\n"
        "fn payload<T>(e: entry<T>*) -> T* { return e as T*; }\n"
        "fn main() -> int32 {\n"
        "    let e: struct entry<struct item>;\n"
        "    e.value = 8; e.next = null;\n"
        "    return payload(&e)->value;\n"
        "}\n"
    ) == 8


def test_intrusive_list_end_to_end():
    assert run(
        'import "memory";\n'
        "struct item { value: int32; }\n"
        "struct entry<T> extends T { next: entry<T>*; }\n"
        "struct list<T> { head: entry<T>*; }\n"
        "fn push(l: struct list<struct item>*, v: int32) {\n"
        "    let e = alloc<struct entry<struct item>>(1);\n"
        "    e->value = v;\n"
        "    e->next = l->head;\n"
        "    l->head = e;\n"
        "}\n"
        "fn main() -> int32 {\n"
        "    let l: struct list<struct item>;\n"
        "    l.head = null;\n"
        "    push(&l, 1); push(&l, 2); push(&l, 3);\n"
        "    let sum: int32 = 0;\n"
        "    let cur = l.head;\n"
        "    while (cur != null) {\n"
        "        sum += cur->value;\n"
        "        cur = cur->next;\n"
        "    }\n"
        "    return sum;\n"
        "}\n"
    ) == 6


def test_bare_param_non_struct_argument_rejected():
    err, notes = notes_of(
        "struct entry<T> extends T { next: entry<T>*; }\n"
        "fn main() -> int32 { return sizeof(struct entry<int32>) as int32; }\n"
    )
    assert str(err) == "line 1: int32 is not a struct; cannot extend it"
    assert notes == [("in instantiation of entry<int32>", 2)]


def test_bare_param_pointer_argument_rejected():
    err, notes = notes_of(
        "struct my { a: int32; }\n"
        "struct entry<T> extends T { next: entry<T>*; }\n"
        "fn main() -> int32 { return sizeof(struct entry<struct my*>) as int32; }\n"
    )
    assert str(err) == "line 2: my* is not a struct; cannot extend it"
    assert notes == [("in instantiation of entry<my*>", 3)]


def test_bare_param_union_argument_rejected():
    err, notes = notes_of(
        "union u { i: int32; f: float64; }\n"
        "struct entry<T> extends T { next: entry<T>*; }\n"
        "fn main() -> int32 { return sizeof(struct entry<union u>) as int32; }\n"
    )
    assert str(err) == "line 2: a union cannot be extended, but 'entry' extends union u"
    assert notes == [("in instantiation of entry<u>", 3)]


def test_bare_param_fam_argument_rejected_with_note():
    err, notes = notes_of(
        "struct pkt { length: uint64; data: int32[]; }\n"
        "struct entry<T> extends T { next: entry<T>*; }\n"
        "fn main() -> int32 { return sizeof(struct entry<struct pkt>) as int32; }\n"
    )
    assert str(err) == (
        "line 2: cannot extend struct 'pkt': it ends in a flexible array "
        "member, which must stay the last field"
    )
    assert notes == [("in instantiation of entry<pkt>", 3)]


def test_bare_param_field_collision_rejected_with_note():
    err, notes = notes_of(
        "struct payload { next: int32; }\n"
        "struct entry<T> extends T { next: entry<T>*; }\n"
        "fn main() -> int32 { return sizeof(struct entry<struct payload>) as int32; }\n"
    )
    assert str(err) == "line 2: field 'next' is already defined in base struct 'payload'"
    assert notes == [("in instantiation of entry<payload>", 3)]


def test_self_feeding_instance_collides_on_link():
    # entry<entry<item>>'s base already carries `next`, so the outer collides.
    err, notes = notes_of(
        "struct item { value: int32; }\n"
        "struct entry<T> extends T { next: entry<T>*; }\n"
        "fn main() -> int32 { return sizeof(struct entry<struct entry<struct item>>) as int32; }\n"
    )
    assert str(err) == (
        "line 2: field 'next' is already defined in base struct 'entry<item>'"
    )
    assert notes == [("in instantiation of entry<entry<item>>", 3)]


def test_self_feeding_instance_fails_on_inner_non_struct():
    # The inner argument fails first: it is resolved at the request site,
    # so the note names the inner instance.
    err, notes = notes_of(
        "struct entry<T> extends T { next: entry<T>*; }\n"
        "fn main() -> int32 { return sizeof(struct entry<struct entry<int32>>) as int32; }\n"
    )
    assert str(err) == "line 1: int32 is not a struct; cannot extend it"
    assert notes == [("in instantiation of entry<int32>", 2)]


def test_flattened_literal_with_explicit_type_args():
    assert run(
        "struct item { value: int32; tag: int32; }\n"
        "struct entry<T> extends T { next: entry<T>*; }\n"
        "fn main() -> int32 {\n"
        "    let e = entry<struct item> { value = 5, tag = 2, next = null };\n"
        "    return e.value + e.tag;\n"
        "}\n"
    ) == 7


def test_literal_inference_cannot_see_base_fields():
    # Inference walks only the extender's own fields; naming a base field
    # without explicit type arguments is an error, not a deduction.
    with pytest.raises(LangError, match="struct 'entry' has no field 'value'"):
        compile_ir(
            "struct item { value: int32; }\n"
            "struct entry<T> extends T { next: entry<T>*; }\n"
            "fn main() -> int32 {\n"
            "    let e = entry { value = 5, next = null };\n"
            "    return e.value;\n"
            "}\n"
        )


ITEM_DEFAULTS = "struct item { value: int32 = 40; tag: int32; }\n"


def test_base_defaults_flow_through_bare_param_literal():
    # The payload's field defaults apply to the instance's literal, exactly
    # as a named base's would.
    assert run(
        ITEM_DEFAULTS +
        "struct entry<T> extends T { next: entry<T>*; }\n"
        "fn main() -> int32 {\n"
        "    let e = entry<struct item> { tag = 2 };\n"
        "    return e.value + e.tag;\n"
        "}\n"
    ) == 42


def test_base_defaults_flow_through_bare_param_bare_decl():
    assert run(
        ITEM_DEFAULTS +
        "struct entry<T> extends T { next: entry<T>*; }\n"
        "fn main() -> int32 {\n"
        "    let e: struct entry<struct item>;\n"
        "    return e.value + e.tag;\n"
        "}\n"
    ) == 40


def test_bare_param_base_composes_with_type_param_default():
    assert run(
        "struct item { value: int32; }\n"
        "struct entry<T = struct item> extends T { next: entry<T>*; }\n"
        "fn main() -> int32 {\n"
        "    let e: struct entry;\n"
        "    e.value = 42; e.next = null;\n"
        "    return e.value;\n"
        "}\n"
    ) == 42


def test_bodyless_bare_param_brands_per_instantiation():
    assert run(
        "struct item { value: int32; }\n"
        "struct branded<T> extends T;\n"
        "fn main() -> int32 {\n"
        "    let b: struct branded<struct item>;\n"
        "    b.value = 33;\n"
        "    let p = &b as struct item*;\n"
        "    return p->value;\n"
        "}\n"
    ) == 33


def test_packed_payload_packs_its_instance_only():
    # entry<pp> inherits @packed (9 + 8 = 17); the sibling instance over a
    # natural payload stays naturally laid out (8 + 8 = 16).
    assert run(
        "@packed struct pp { a: uint8; b: int64; }\n"
        "struct norm { a: int64; }\n"
        "struct entry<T> extends T { next: entry<T>*; }\n"
        "fn main() -> int32 {\n"
        "    if (sizeof(struct entry<struct pp>) != 17) { return 1; }\n"
        "    if (sizeof(struct entry<struct norm>) != 16) { return 2; }\n"
        "    return 0;\n"
        "}\n"
    ) == 0


# ------------------------------------------- nominal subtyping: declared lineage
#
# The struct subtype relation follows the declared `extends` lineage, not a
# matching layout prefix. A struct upcasts only to a base it names -- transitively
# -- in an `extends` clause; a coincidental layout twin with no `extends` is
# rejected, and sibling brands over one base never interconvert. (The generic
# and bare-parameter upcasts above are the positive lineage cases.)

def test_layout_match_without_extends_upcast_rejected():
    # `b` has `a`'s exact fields but does not `extends a`, so the value upcast is
    # rejected: declared lineage, not a matching field list, decides it.
    with pytest.raises(LangError, match="cannot cast"):
        run(
            "struct a { x: int32; y: int32; }\n"
            "struct b { x: int32; y: int32; }\n"
            "fn main() -> int32 {\n"
            "    let v: struct a; v.x = 1; v.y = 2;\n"
            "    let w = v as struct b;\n"
            "    return 0;\n"
            "}\n"
        )


def test_layout_prefix_without_extends_upcast_rejected():
    # `wide` opens with `narrow`'s exact field as a true layout prefix, but with
    # no `extends narrow` the structural twin no longer upcasts.
    with pytest.raises(LangError, match="cannot cast"):
        run(
            "struct narrow { x: int32; }\n"
            "struct wide { x: int32; y: int32; }\n"
            "fn main() -> int32 {\n"
            "    let v: struct wide; v.x = 1; v.y = 2;\n"
            "    let w = v as struct narrow;\n"
            "    return 0;\n"
            "}\n"
        )


def test_sibling_specializations_do_not_interconvert():
    # Two specializations of one base share its layout but are distinct brands:
    # neither converts to its sibling (only up to the shared base, below).
    with pytest.raises(LangError, match="cannot cast"):
        run(
            "struct base { id: int64; }\n"
            "struct user_id extends base;\n"
            "struct order_id extends base;\n"
            "fn main() -> int32 {\n"
            "    let u: struct user_id; u.id = 1;\n"
            "    let o = u as struct order_id;\n"
            "    return 0;\n"
            "}\n"
        )


def test_sibling_specialization_still_upcasts_to_shared_base():
    # The rejection above is nominal, not blanket: the declared base upcast works.
    assert run(
        "struct base { id: int64; }\n"
        "struct user_id extends base;\n"
        "struct order_id extends base;\n"
        "fn main() -> int32 {\n"
        "    let u: struct user_id; u.id = 42;\n"
        "    let b = u as struct base;\n"
        "    return b.id as int32;\n"
        "}\n"
    ) == 42
