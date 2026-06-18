"""Default typing of untyped integer literals: int32, widening to int64/uint64."""

import pytest

from helpers import compile_ir, run


def test_small_literal_defaults_to_int32():
    # A value that fits int32 stays int32, so `printf("%d", n)` matches C's int.
    ir = compile_ir(
        'import "libc/stdio";\n'
        'fn main() -> int32 { printf("%d", 7); return 0; }'
    )
    assert "i32 7" in ir


def test_large_literal_widens_to_int64():
    # A value past int32 widens instead of truncating, so a variadic gets all
    # 64 bits (the bug: it used to be passed as a truncated i32).
    ir = compile_ir(
        'import "libc/stdio";\n'
        'fn main() -> int32 { printf("%lld", 5000000000); return 0; }'
    )
    assert "i64 5000000000" in ir


def test_constant_arithmetic_does_not_wrap_at_32_bits():
    # 2**32 + 7 must not fold in int32 (which would give 7).
    assert run(
        "fn main() -> int32 { let x: uint64 = 4294967296 + 7; "
        "return (x - 4294967296) as int32; }"
    ) == 7


def test_mixed_width_constants_widen():
    # A small (int32) and a large (int64) untyped operand widen to int64.
    assert run(
        "fn main() -> int32 { let x: uint64 = 1 + 5000000000; "
        "return (x - 5000000000) as int32; }"
    ) == 1


def test_top_bit_set_64bit_literal():
    # A value past int64's signed max defaults to uint64 and round-trips.
    assert run(
        "fn main() -> int32 { let m: uint64 = 18446744073709551615; "
        "return (m == 0xFFFFFFFFFFFFFFFF) as int32; }"
    ) == 1


def test_small_literal_still_adapts_down():
    # The default is only a placeholder; the value still adapts to any type it
    # fits, so this needs no cast.
    assert run("fn main() -> int32 { let b: uint8 = 200; return b as int32; }") == 200


def test_literal_too_large_for_target_still_rejected():
    # Adaptation is value-based: a value that does not fit the target is an error.
    with pytest.raises(Exception, match="out of range"):
        compile_ir("fn main() -> int32 { let b: uint8 = 5000000000; return 0; }")


def test_negative_small_literal_stays_int32():
    assert run("fn main() -> int32 { let n: int32 = -5; return n; }") == -5


def test_untyped_left_shift_widens_to_fit():
    # An untyped constant has no width to overflow, so `1 << 40` is the integer
    # 2**40 (int64), not a 32-bit shift -- no cast needed for a uint64.
    assert run(
        "const C: uint64 = (1 << 40);\n"
        "fn main() -> int32 { return (C >> 40) as int32; }"
    ) == 1
    assert run("fn main() -> int32 { let x: uint64 = 1 << 63; return (x >> 63) as int32; }") == 1


def test_small_shift_stays_int32():
    ir = compile_ir("fn main() -> int32 { let x: int32 = 1 << 4; return x; }")
    assert "i32 16" in ir  # folded, still int32


def test_typed_left_shift_is_not_widened():
    # The widening is only for untyped constants; a typed operand shifts in its
    # own width (here uint32 wraps).
    assert run(
        "fn main() -> int32 { let h: uint32 = 0xFF; h = h << 28; return (h >> 28) as int32; }"
    ) == 15
