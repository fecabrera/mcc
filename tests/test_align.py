"""@align(N): raise a struct's alignment beyond its natural one."""

import pytest

from mcc.errors import LangError
from helpers import compile_ir, parse, run


def test_sizeof_rounds_up_to_alignment():
    source = """
    @align(16)
    struct vec { x: float64; }
    fn main() -> int32 { return sizeof(struct vec) as int32; }
    """
    assert run(source) == 16


def test_array_stride_matches_sizeof():
    # p[1] must land sizeof(cell) bytes in, or the writes would overlap.
    source = """
    import "libc/stdlib";
    @align(8)
    struct cell { x: int32; }
    fn main() -> int32 {
        if (sizeof(struct cell) != 8) return 1;
        let p = malloc(2 * sizeof(struct cell)) as struct cell*;
        p[0].x = 1;
        p[1].x = 2;
        let total = p[0].x * 10 + p[1].x;
        free(p);
        return total;
    }
    """
    assert run(source) == 12


def test_over_aligned_field_offsets_in_outer_struct():
    # inner is 16-aligned, so it sits at offset 16 of outer (checked through
    # raw bytes) and pushes outer's size to 48.
    source = """
    import "libc/stdlib";
    @align(16)
    struct inner { x: int32; }
    struct outer {
        tag: uint8;
        mid: struct inner;
        y: int32;
    }
    fn main() -> int32 {
        if (sizeof(struct outer) != 48) return 1;
        let o = malloc(sizeof(struct outer)) as struct outer*;
        o->tag = 7;
        o->mid.x = 1000;
        o->y = 42;
        let raw = o as uint8*;
        let x_at_16 = *(&raw[16] as int32*);
        let result: int32 = 2;
        if (x_at_16 == 1000)
            result = o->y + o->tag as int32;
        free(o);
        return result;
    }
    """
    assert run(source) == 49


def test_generic_struct_takes_the_alignment():
    source = """
    @align(16)
    struct box<T> { value: T; }
    fn main() -> int32 {
        return (sizeof(struct box<uint8>) * 100 + sizeof(struct box<int64>)) as int32;
    }
    """
    assert run(source) == 1616


def test_local_of_aligned_struct_gets_an_aligned_slot():
    ir_text = compile_ir(
        "@align(32)\nstruct v { x: int32; }\n"
        "fn f(a: struct v) -> int32 { let b = a; return b.x; }"
    )
    assert ir_text.count("align 32") == 2  # the parameter slot and b


def test_align_below_natural_is_an_error():
    source = """
    @align(4)
    struct wide { x: int64; }
    fn main() -> int32 { return sizeof(struct wide) as int32; }
    """
    with pytest.raises(LangError, match="below struct 'wide'"):
        run(source)


def test_align_must_be_a_power_of_two():
    with pytest.raises(LangError, match="power of two"):
        parse("@align(12)\nstruct s { x: int32; }")


def test_align_on_a_function_is_an_error():
    with pytest.raises(LangError, match="only applies to structs"):
        parse("@align(8)\nfn f() {}")
