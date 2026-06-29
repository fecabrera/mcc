"""The `byte` type: a transparent builtin alias for uint8.

Unlike `char` (a distinct one-byte text type), `byte` is the very same type as
uint8 -- it reads as intent at a raw-memory boundary (the allocators, memcpy,
fread) without introducing a new type, so byte and uint8 values and pointers are
interchangeable with no cast.
"""

import pytest

from mcc.errors import LangError
from mcc.nodes import TypeRef
from helpers import compile_ir, parse, run


def test_byte_type_parses():
    (func,) = parse("fn f(b: byte) {}").functions
    assert func.params[0][1] == TypeRef("byte")


def test_byte_is_uint8_lowering():
    # byte lowers to an unsigned i8, exactly like uint8.
    ir_text = compile_ir("fn f(b: byte) -> byte { return b; }")
    assert 'define i8 @"f"(i8 %"b")' in ir_text


def test_byte_value_interchangeable_with_uint8():
    # No cast either way: byte *is* uint8, so a byte initializes a uint8 slot
    # and a uint8 initializes a byte slot directly.
    src = """
    fn main() -> int32 {
        let b: byte = 65;
        let u: uint8 = b;     // byte -> uint8, no `as`
        let b2: byte = u;     // uint8 -> byte, no `as`
        return b2 as int32;
    }
    """
    assert run(src) == 65


def test_byte_pointer_interchangeable_with_uint8_pointer():
    # A byte* is accepted where a uint8* is expected and vice versa, with no
    # cast -- they are one type.
    src = """
    fn take_u8(p: uint8*) -> uint8 { return p[0]; }
    fn take_byte(p: byte*) -> byte { return p[0]; }
    fn main() -> int32 {
        let buf: byte[2];
        buf[0] = 7;
        let a = take_u8(&buf[0]);     // byte* -> uint8*
        let raw: uint8[2];
        raw[0] = 5;
        let b = take_byte(&raw[0]);   // uint8* -> byte*
        return (a as int32) + (b as int32);
    }
    """
    assert run(src) == 12


def test_byte_literal_adapts_like_uint8():
    # As an integer alias, a bare constant adapts into a byte slot (range-checked).
    assert run("fn main() -> int32 { let b: byte = 200; return b as int32; }") == 200
    with pytest.raises(LangError):
        run("fn main() -> int32 { let b: byte = 300; return 0; }")


def test_byte_name_is_reserved():
    # A builtin type name cannot be redefined by a struct.
    with pytest.raises(LangError):
        run("struct byte {} fn main() -> int32 { return 0; }")


def test_alloc_returns_interchangeable_raw_memory():
    # The memory library's allocators yield byte* (raw memory); it round-trips
    # through a typed pointer and back without friction.
    src = """
    import "memory";
    fn main() -> int32 {
        let p = alloc<byte>(4);
        p[0] = 9;
        let n: uint8 = p[0];          // byte element -> uint8, no cast
        dealloc(p);
        return n as int32;
    }
    """
    assert run(src) == 9
