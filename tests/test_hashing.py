"""lib/hashing/: murmur3, crc32, md5, and fnv1a against authoritative references."""

import hashlib
import zlib

from helpers import run_path


def murmur3_32(data: bytes, seed: int = 0) -> int:
    m = 0xFFFFFFFF
    h = seed
    nblocks = len(data) // 4
    for i in range(nblocks):
        k = int.from_bytes(data[i * 4:i * 4 + 4], "little")
        k = (k * 0xCC9E2D51) & m
        k = ((k << 15) | (k >> 17)) & m
        k = (k * 0x1B873593) & m
        h ^= k
        h = ((h << 13) | (h >> 19)) & m
        h = (h * 5 + 0xE6546B64) & m
    tail = data[nblocks * 4:]
    k = 0
    if len(tail) >= 3:
        k ^= tail[2] << 16
    if len(tail) >= 2:
        k ^= tail[1] << 8
    if len(tail) >= 1:
        k ^= tail[0]
        k = (k * 0xCC9E2D51) & m
        k = ((k << 15) | (k >> 17)) & m
        k = (k * 0x1B873593) & m
        h ^= k
    h ^= len(data)
    h ^= h >> 16
    h = (h * 0x85EBCA6B) & m
    h ^= h >> 13
    h = (h * 0xC2B2AE35) & m
    h ^= h >> 16
    return h


def fnv1a64(elems) -> int:
    h = 0xCBF29CE484222325
    for k in elems:
        h = ((h ^ k) * 0x100000001B3) & 0xFFFFFFFFFFFFFFFF
    return h


def run_program(tmp_path, body: str) -> None:
    main = tmp_path / "main.mc"
    main.write_text(
        'import "libc/stdio";\nimport "libc/string";\nimport "memory";\n' + body
    )
    assert run_path(main) == 0


def test_murmur3(tmp_path, capfd):
    run_program(
        tmp_path,
        'import "hashing/murmur3";\n'
        "fn main() -> int32 {\n"
        '    printf("%u %u %u %u\\n",\n'
        '        murmur3("hello", 5, 0), murmur3("hello", 5, 42),\n'
        '        murmur3("The quick brown fox", 19, 0), murmur3("", 0, 0));\n'
        "    return 0;\n"
        "}\n",
    )
    expected = " ".join(str(x) for x in (
        murmur3_32(b"hello"), murmur3_32(b"hello", 42),
        murmur3_32(b"The quick brown fox"), murmur3_32(b""),
    ))
    assert capfd.readouterr().out == expected + "\n"


def test_crc32(tmp_path, capfd):
    run_program(
        tmp_path,
        'import "hashing/crc32";\n'
        "fn main() -> int32 {\n"
        '    let embedded_nul = alloc<uint8>(3);\n'
        "    if (embedded_nul == null) return 1;\n"  # narrows for crc32's @nonnull
        "    embedded_nul[0] = 97; embedded_nul[1] = 0; embedded_nul[2] = 98;\n"
        '    printf("%u %u %u\\n",\n'
        '        crc32("hello", 5), crc32("", 0), crc32(embedded_nul, 3));\n'
        "    return 0;\n"
        "}\n",
    )
    expected = " ".join(str(zlib.crc32(b) & 0xFFFFFFFF)
                        for b in (b"hello", b"", b"a\x00b"))
    assert capfd.readouterr().out == expected + "\n"


def test_md5(tmp_path, capfd):
    # Covers single-block, the 55/56-byte padding boundary, and multi-block.
    run_program(
        tmp_path,
        'import "hashing/md5";\n'
        "fn show(@nonnull data: uint8*, n: uint64) {\n"  # forwards into md5's @nonnull
        "    let digest = alloc<uint8>(16);\n"
        "    if (digest == null) return;\n"
        "    md5(data, n, digest);\n"
        "    let i: int32 = 0;\n"
        '    while (i < 16) { printf("%02x", digest[i]); i = i + 1; }\n'
        "    putchar(10);\n"
        "    dealloc(digest);\n"
        "}\n"
        "fn main() -> int32 {\n"
        '    show("", 0);\n'
        '    show("abc", 3);\n'
        '    show("aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", 55);\n'
        '    show("aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", 56);\n'
        '    show("The quick brown fox jumps over the lazy dog", 43);\n'
        "    return 0;\n"
        "}\n",
    )
    cases = [b"", b"abc", b"a" * 55, b"a" * 56,
             b"The quick brown fox jumps over the lazy dog"]
    expected = "".join(hashlib.md5(b).hexdigest() + "\n" for b in cases)
    assert capfd.readouterr().out == expected


def test_fnv1a_slice(tmp_path, capfd):
    # The slice member folds exactly .length elements, so zeros in the data
    # are hashed; the zero-terminated pointer member stops at the first zero.
    run_program(
        tmp_path,
        'import "hashing/fnv1a";\n'
        "fn main() -> int32 {\n"
        "    let buf = alloc<uint8>(3);\n"
        "    if (buf == null) return 1;\n"
        "    buf[0] = 97; buf[1] = 0; buf[2] = 98;\n"
        "    let bytes: slice<uint8> = [97, 0, 98];\n"
        "    let empty: slice<uint8> = [];\n"
        "    let ints: slice<int32> = [3, 65536, 2026];\n"
        '    printf("%llu %llu %llu %llu %llu %llu\\n",\n'
        '        fnv1a("hi"), fnv1a("hi" as slice<const char>),\n'
        "        fnv1a(bytes), fnv1a(buf), fnv1a(empty), fnv1a(ints));\n"
        "    dealloc(buf);\n"
        "    return 0;\n"
        "}\n",
    )
    expected = " ".join(str(x) for x in (
        fnv1a64(b"hi"),           # pointer member over "hi"...
        fnv1a64(b"hi"),           # ...agrees with the slice member, same bytes
        fnv1a64([97, 0, 98]),     # the slice hashes a zero mid-data...
        fnv1a64([97]),            # ...where the pointer member truncates
        0xCBF29CE484222325,       # empty slice: the FNV offset basis
        fnv1a64([3, 65536, 2026]),  # non-char elements instantiate too
    ))
    assert capfd.readouterr().out == expected + "\n"
