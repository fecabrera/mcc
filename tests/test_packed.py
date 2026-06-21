"""@packed: structs with no padding between fields."""

import pytest

from mcc.errors import LangError
from helpers import compile_ir, parse, run


def test_sizeof_has_no_padding():
    source = """
    @packed
    struct mixed {
        a: uint8;
        b: int64;
        c: uint8;
    }
    fn main() -> int32 { return sizeof(struct mixed) as int32; }
    """
    assert run(source) == 10  # 24 if naturally padded


def test_fields_sit_at_unpadded_offsets():
    # b lives at byte 1; check its first byte through a raw pointer.
    source = """
    import "libc/stdlib";
    @packed
    struct header {
        tag: uint8;
        length: int64;
    }
    fn main() -> int32 {
        let h = malloc(sizeof(struct header)) as struct header*;
        h->tag = 9;
        h->length = 42;
        let raw = h as uint8*;
        let result: int32 = 1;
        if (raw[1] == 42)
            result = h->length as int32 + h->tag as int32;
        free(h);
        return result;
    }
    """
    assert run(source) == 51


def test_list_stride_matches_sizeof():
    source = """
    import "libc/stdlib";
    @packed
    struct cell { a: uint8; b: int32; }
    fn main() -> int32 {
        if (sizeof(struct cell) != 5) return 1;
        let p = malloc(2 * sizeof(struct cell)) as struct cell*;
        p[0].b = 10;
        p[1].b = 2;
        let total = p[0].b + p[1].b;
        free(p);
        return total;
    }
    """
    assert run(source) == 12


def test_nested_struct_fields_inherit_the_misalignment():
    # pair keeps its internal layout but sits at byte 1, so pair.y lands at
    # byte 5 -- and its accesses must not assume natural alignment.
    source = """
    import "libc/stdlib";
    struct pair { x: int32; y: int32; }
    @packed
    struct framed {
        tag: uint8;
        p: struct pair;
    }
    fn main() -> int32 {
        if (sizeof(struct framed) != 9) return 1;
        let f = malloc(sizeof(struct framed)) as struct framed*;
        f->tag = 9;
        f->p.x = 300;
        f->p.y = 42;
        let raw = f as uint8*;
        let result: int32 = 2;
        if (raw[1] == 44)               // 300 = 0x012C, little-endian
            if (raw[2] == 1)
                if (raw[5] == 42)
                    result = f->p.y + f->tag as int32;
        free(f);
        return result;
    }
    """
    assert run(source) == 51


def test_member_access_is_marked_unaligned():
    ir_text = compile_ir(
        "@packed\nstruct s { a: uint8; b: int64; }\n"
        "fn f(p: struct s*) -> int64 { p->b = 5; return p->b; }"
    )
    assert ir_text.count("align 1") == 2  # the field's store and load


def test_packed_combines_with_align():
    # @align pads the total size without reintroducing field padding.
    source = """
    @packed
    @align(4)
    struct cell { a: uint8; b: int32; }
    fn main() -> int32 { return sizeof(struct cell) as int32; }
    """
    assert run(source) == 8  # fields end at byte 5, rounded up to 4


def test_generic_packed_struct():
    source = """
    @packed
    struct box<T> { tag: uint8; value: T; }
    fn main() -> int32 {
        return (sizeof(struct box<int16>) * 10 + sizeof(struct box<int64>)) as int32;
    }
    """
    assert run(source) == 39


def test_packed_on_a_function_is_an_error():
    with pytest.raises(LangError, match="only applies to structs"):
        parse("@packed\nfn f() {}")
