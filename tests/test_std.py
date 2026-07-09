"""The std module's mut-powered helpers: swap and replace."""

from helpers import run


def test_swap_scalars():
    assert run(
        'import "std/io";\n'
        "fn main() -> int32 {\n"
        "    let a: int32 = 3;\n"
        "    let b: int32 = 9;\n"
        "    swap(a, b);\n"
        "    return a * 10 + b;\n"
        "}"
    ) == 93


def test_swap_monomorphizes_per_type():
    assert run(
        'import "std/io";\n'
        "fn main() -> int32 {\n"
        "    let a: int32 = 1;\n"
        "    let b: int32 = 2;\n"
        "    swap(a, b);\n"
        "    let f = 1.5;\n"
        "    let g = 2.5;\n"
        "    swap(f, g);\n"
        "    return a * 10 + (f > g ? 1 : 0);\n"
        "}"
    ) == 21


def test_swap_structs():
    assert run(
        'import "std/io";\n'
        "struct point { x: int32; y: int32; }\n"
        "fn main() -> int32 {\n"
        "    let p = point { x = 1, y = 2 };\n"
        "    let q = point { x = 3, y = 4 };\n"
        "    swap(p, q);\n"
        "    return p.x * 10 + q.y;\n"
        "}"
    ) == 32


def test_swap_array_elements():
    assert run(
        'import "std/io";\n'
        "fn main() -> int32 {\n"
        "    let a: int32[2] = [7, 9];\n"
        "    swap(a[0], a[1]);\n"
        "    return a[0] * 10 + a[1];\n"
        "}"
    ) == 97


def test_replace_returns_old_value():
    assert run(
        'import "std/io";\n'
        "fn main() -> int32 {\n"
        "    let a: int32 = 9;\n"
        "    let old = replace(a, 100);\n"
        "    return (a == 100 and old == 9) ? 0 : 1;\n"
        "}"
    ) == 0


def test_replace_structs():
    assert run(
        'import "std/io";\n'
        "struct point { x: int32; y: int32; }\n"
        "fn main() -> int32 {\n"
        "    let p = point { x = 1, y = 2 };\n"
        "    let old = replace(p, point { x = 8, y = 9 });\n"
        "    return p.x * 10 + old.y;\n"
        "}"
    ) == 82
