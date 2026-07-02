"""Unions: declaration, layout, member access, punning, and the rejections."""

import pytest

from mcc.errors import LangError
from helpers import compile_ir, parse, run


# --------------------------------------------------------------------- parser

VALUE = "union value { i: int64; f: float64; b: uint8[4]; }\n"


def test_union_declaration():
    (decl,) = parse("union u { i: int64; f: float64; }").structs
    assert decl.union
    assert [(n, str(t)) for n, t in decl.fields] == [("i", "int64"), ("f", "float64")]


def test_struct_declaration_is_not_a_union():
    (decl,) = parse("struct point { x: int32; y: int32; }").structs
    assert not decl.union


def test_generic_union_declaration():
    (decl,) = parse("union opt<T> { value: T; raw: uint64; }").structs
    assert decl.union and decl.type_params == ["T"]


def test_union_rejects_extends():
    with pytest.raises(LangError, match="a union cannot extend another type"):
        parse("union u extends v { i: int64; }")


def test_union_rejects_member_defaults():
    with pytest.raises(LangError, match="a union member cannot declare a default"):
        parse("union u { i: int64 = 3; }")


def test_union_keyword_is_reserved():
    with pytest.raises(LangError):
        parse("fn main() -> int32 { let union = 1; return 0; }")


# --------------------------------------------------------------------- layout


def test_union_size_is_largest_member():
    assert run(
        VALUE + "fn main() -> int32 { return sizeof(union value) as int32; }"
    ) == 8


def test_union_alignment_is_most_aligned_member():
    assert run(
        VALUE + "fn main() -> int32 { return alignof(union value) as int32; }"
    ) == 8


def test_union_size_rounds_to_alignment():
    # Largest member is 7 bytes, but int32 aligns the union to 4 -> size 8.
    assert run(
        "union u { i: int32; b: uint8[7]; }\n"
        "fn main() -> int32 { return sizeof(union u) as int32; }"
    ) == 8


def test_union_members_all_at_offset_zero():
    assert run(
        VALUE + "fn main() -> int32 {\n"
        "    return (offsetof(union value, i) + offsetof(union value, f)\n"
        "        + offsetof(union value, b)) as int32;\n"
        "}"
    ) == 0


def test_offsetof_unknown_union_member():
    with pytest.raises(LangError, match="has no field 'nope'"):
        compile_ir(
            VALUE + "fn main() -> int32 {"
            " return offsetof(union value, nope) as int32; }"
        )


def test_union_body_is_representative_member_plus_pad():
    ir_text = compile_ir(
        "union u { i: int32; b: uint8[7]; }\n"
        "fn main() -> int32 { let v: union u; return 0; }"
    )
    assert '%"u" = type {i32, [4 x i8]}' in ir_text


def test_packed_union_alignment_is_one():
    assert run(
        "@packed union u { i: int64; }\n"
        "fn main() -> int32 { return alignof(union u) as int32; }"
    ) == 1


def test_align_override_raises_union_alignment():
    assert run(
        "@align(16) union u { i: int32; }\n"
        "fn main() -> int32 {\n"
        "    return (sizeof(union u) * 100 + alignof(union u)) as int32;\n"
        "}"
    ) == 1616


def test_union_inside_struct_layout():
    # value aligns the holder to 8: tag pads to offset 8, z lands at 16.
    assert run(
        VALUE + "struct holder { tag: uint8; v: union value; z: uint8; }\n"
        "fn main() -> int32 {\n"
        "    return (offsetof(struct holder, v) * 100\n"
        "        + offsetof(struct holder, z)) as int32;\n"
        "}"
    ) == 816


def test_struct_inside_union_layout():
    assert run(
        "struct inner { a: int32; b: int64; }\n"
        "union u { c: char; s: struct inner; }\n"
        "fn main() -> int32 { return sizeof(union u) as int32; }"
    ) == 16


# -------------------------------------------------------- access and punning


def test_union_member_write_and_read():
    assert run(
        VALUE + "fn main() -> int32 {\n"
        "    let v: union value;\n"
        "    v.i = 42;\n"
        "    return v.i as int32;\n"
        "}"
    ) == 42


def test_union_member_access_casts_instead_of_gep():
    ir_text = compile_ir(
        VALUE + "fn main() -> int32 { let v: union value; v.i = 1;"
        " return v.i as int32; }"
    )
    assert 'bitcast %"value"* ' in ir_text


def test_union_cross_member_read_is_byte_reinterpretation():
    # 1.0 is 0x3FF0000000000000; reading the int64 member sees those bytes.
    assert run(
        VALUE + "fn main() -> int32 {\n"
        "    let v = value { f = 1.0 };\n"
        "    return (v.i == 0x3FF0000000000000) ? 0 : 1;\n"
        "}"
    ) == 0


def test_union_byte_member_pokes_other_members():
    assert run(
        VALUE + "fn main() -> int32 {\n"
        "    let v = value { i = 0 };\n"
        "    v.b[0] = 7 as uint8;\n"
        "    return (v.i & 0xFF) as int32;\n"
        "}"
    ) == 7


def test_union_access_through_pointer():
    assert run(
        VALUE + "fn set(v: union value*, x: int64) { v->i = x; }\n"
        "fn main() -> int32 {\n"
        "    let v: union value;\n"
        "    set(&v, 9);\n"
        "    return v.i as int32;\n"
        "}"
    ) == 9


def test_union_member_of_call_result():
    # Field of a non-addressable union value spills and casts.
    assert run(
        VALUE + "fn make(x: int64) -> union value { \n"
        "    let v = value { i = x };\n"
        "    return v;\n"
        "}\n"
        "fn main() -> int32 { return make(5).i as int32; }"
    ) == 5


def test_union_whole_value_copy():
    assert run(
        VALUE + "fn main() -> int32 {\n"
        "    let a = value { i = 11 };\n"
        "    let b = a;\n"
        "    b.i = 22;\n"
        "    return (a.i * 100 + b.i) as int32;\n"
        "}"
    ) == 1122


# ------------------------------------------------------------------ literals


def test_union_literal_zero_fills():
    assert run(
        VALUE + "fn main() -> int32 {\n"
        "    let v = value { };\n"
        "    return v.i as int32;\n"
        "}"
    ) == 0


def test_union_literal_rejects_two_members():
    with pytest.raises(LangError, match="a union literal sets at most one member"):
        compile_ir(
            VALUE + "fn main() -> int32 {"
            " let v = value { i = 1, f = 2.0 }; return 0; }"
        )


def test_union_literal_bare_name():
    assert run(
        VALUE + "fn main() -> int32 {\n"
        "    let v = value { i = 3 };\n"
        "    return v.i as int32;\n"
        "}"
    ) == 3


# ------------------------------------------------------------------ generics


def test_generic_union_monomorphizes():
    assert run(
        "union opt<T> { value: T; raw: uint64; }\n"
        "fn main() -> int32 {\n"
        "    let a: union opt<int32>;\n"
        "    a.value = 5;\n"
        "    let b: union opt<float64>;\n"
        "    b.value = 2.5;\n"
        "    return (a.value as uint64 + (b.raw >> 60)) as int32;\n"
        "}"
    ) == 9  # 2.5 is 0x4004000000000000; its top nibble is 4


def test_generic_union_sizes_per_instantiation():
    assert run(
        "union opt<T> { value: T; tag: uint8; }\n"
        "fn main() -> int32 {\n"
        "    return (sizeof(union opt<int64>) * 10\n"
        "        + sizeof(union opt<uint8>)) as int32;\n"
        "}"
    ) == 81


# ---------------------------------------------------------------- rejections


def test_union_cannot_be_extended():
    with pytest.raises(LangError, match="a union cannot be extended"):
        compile_ir(
            "union u { i: int64; }\n"
            "struct s extends u { x: int32; }\n"
            "fn main() -> int32 { let v: struct s; return 0; }"
        )


def test_union_rejects_flexible_array_member():
    with pytest.raises(LangError, match="a union cannot contain a flexible array"):
        compile_ir(
            "union u { n: uint64; tail: uint8[]; }\n"
            "fn main() -> int32 { let v: union u; return 0; }"
        )


def test_union_is_not_a_prefix_upcast_source():
    # A union whose members mirror a struct's fields shares no layout with it.
    with pytest.raises(LangError, match="cannot cast"):
        compile_ir(
            "struct s { x: int64; }\n"
            "union u { x: int64; }\n"
            "fn main() -> int32 {\n"
            "    let v: union u;\n"
            "    let w = v as struct s;\n"
            "    return 0;\n"
            "}"
        )


def test_static_union_initializer_is_rejected():
    message = "a global union initializer is not supported yet"
    with pytest.raises(LangError, match=message):
        compile_ir(
            "union u { i: int64; }\n"
            "@static let g: union u = u { i = 1 };\n"
            "fn main() -> int32 { return 0; }"
        )


# ------------------------------------------------------------- interactions


def test_volatile_union_member_access():
    ir_text = compile_ir(
        "@volatile union reg { word: uint32; byte0: uint8; }\n"
        "fn main() -> int32 {\n"
        "    let r: union reg;\n"
        "    r.word = 5;\n"
        "    return r.byte0 as int32;\n"
        "}"
    )
    assert "store volatile" in ir_text and "load volatile" in ir_text


def test_const_union_parameter_passes_by_hidden_reference():
    ir_text = compile_ir(
        VALUE + "fn read(const v: union value) -> int64 { return v.i; }\n"
        "fn main() -> int32 {\n"
        "    let v = value { i = 1 };\n"
        "    return read(v) as int32;\n"
        "}"
    )
    assert 'define i64 @"read"(%"value"* ' in ir_text


def test_union_in_case_subject_is_rejected():
    with pytest.raises(LangError, match="cannot match a "):
        compile_ir(
            VALUE + "fn main() -> int32 {\n"
            "    let v: union value;\n"
            "    case (v) { when 1: return 1; }\n"
            "    return 0;\n"
            "}"
        )
