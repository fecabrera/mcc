"""Enums: a named set of compile-time constants, accessed as Enum::Member.

``enum Name[: type] { Member = value, ... }`` introduces ``Name`` as a type
aliasing its underlying type (``int32`` when omitted), plus a constant
``Name::Member`` of that type per member. A member's value is any constant
expression of the underlying type.
"""

import pytest

from mcc.errors import LangError
from mcc.nodes import EnumAccess, EnumDecl
from helpers import compile_ir, parse, run, run_path


# --------------------------------------------------------------------- parser

def test_enum_parses():
    (enum,) = parse("enum Color: int32 { Red = 0, Green = 1, Blue = 2 }").enums
    assert isinstance(enum, EnumDecl) and enum.name == "Color"
    assert str(enum.underlying) == "int32"
    assert [name for name, _ in enum.members] == ["Red", "Green", "Blue"]


def test_underlying_type_is_optional():
    (enum,) = parse("enum Dir { N = 0, E = 1 }").enums
    assert enum.underlying is None


def test_trailing_comma_is_allowed():
    (enum,) = parse("enum Dir { N = 0, E = 1, }").enums
    assert [name for name, _ in enum.members] == ["N", "E"]


def test_member_access_parses():
    (fn,) = parse("fn f() -> int32 { return Color::Blue; }").functions
    expr = fn.body[0].value
    assert isinstance(expr, EnumAccess)
    assert expr.enum == "Color" and expr.member == "Blue"


def test_empty_enum_is_rejected():
    with pytest.raises(LangError, match="enum 'Dir' has no members"):
        parse("enum Dir { }")


# -------------------------------------------------------------------- codegen

def test_default_underlying_is_int32():
    assert run("enum Dir { N = 0, W = 3 } fn main() -> int32 { return Dir::W; }") == 3


def test_members_fold_to_their_values():
    assert run(
        "enum Color: int32 { Red = 10, Green = 20 }\n"
        "fn main() -> int32 { return Color::Red + Color::Green; }"
    ) == 30


def test_member_value_is_any_constant_expression():
    assert run(
        "const BASE = 100;\n"
        "enum E: int32 { A = BASE * 2, B = 1 << 4 }\n"
        "fn main() -> int32 { return E::A + E::B; }"
    ) == 216


def test_member_can_reference_an_earlier_member():
    assert run(
        "enum E: int32 { A = 5, B = E::A + 1, C = E::B + 1 }\n"
        "fn main() -> int32 { return E::C; }"
    ) == 7


def test_enum_name_is_usable_as_a_type():
    assert run(
        "enum Color: int32 { Red = 0, Blue = 7 }\n"
        "fn main() -> int32 { let c: Color = Color::Blue; return c; }"
    ) == 7


def test_enum_as_parameter_and_return_type():
    assert run(
        "enum Color: int32 { Red = 1, Blue = 3 }\n"
        "fn pick() -> Color { return Color::Blue; }\n"
        "fn twice(c: Color) -> int32 { return c * 2; }\n"
        "fn main() -> int32 { return twice(pick()); }"
    ) == 6


def test_enum_as_struct_field_type():
    assert run(
        "enum Color: int32 { Red = 1, Green = 2 }\n"
        "struct Pixel { c: Color; }\n"
        "fn main() -> int32 { let p: Pixel; p.c = Color::Green; return p.c; }"
    ) == 2


def test_enum_member_matches_in_a_case():
    assert run(
        "enum Dir { N = 0, E = 1, S = 2, W = 3 }\n"
        "fn main() -> int32 {\n"
        "    let d: Dir = Dir::S;\n"
        "    case (d) {\n"
        "        when Dir::N, Dir::S: return 10;\n"
        "        else: return 20;\n"
        "    }\n"
        "}"
    ) == 10


def test_wide_member_keeps_its_underlying_type():
    # 1 << 40 needs uint64; the member carries the enum's underlying width.
    assert run(
        "enum Flags: uint64 { High = 1 << 40 }\n"
        "fn main() -> int32 {\n"
        "    let f: uint64 = Flags::High;\n"
        "    return (f >> 40) as int32;\n"
        "}"
    ) == 1


def test_pointer_underlying_with_string_members():
    assert run(
        "@extern fn strcmp(a: uint8*, b: uint8*) -> int32;\n"
        "enum Msg: uint8* { Hi = \"hello\" }\n"
        "fn main() -> int32 { return strcmp(Msg::Hi, \"hello\"); }"
    ) == 0


def test_members_have_no_storage():
    # Enum members are folded in, like consts -- no global symbol is emitted.
    ir = compile_ir(
        "enum Color: int32 { Red = 0, Blue = 7 }\n"
        "fn main() -> int32 { return Color::Blue; }"
    )
    assert "Color" not in ir and "i32 7" in ir


# --------------------------------------------------------------------- errors

def test_unknown_enum_is_rejected():
    with pytest.raises(LangError, match="unknown enum 'Nope'"):
        compile_ir("fn main() -> int32 { return Nope::X; }")


def test_unknown_member_is_rejected():
    with pytest.raises(LangError, match="enum 'Dir' has no member 'Z'"):
        compile_ir("enum Dir { N = 0 } fn main() -> int32 { return Dir::Z; }")


def test_member_out_of_range_is_rejected():
    with pytest.raises(LangError, match="constant 300 is out of range for uint8"):
        compile_ir("enum Small: uint8 { Big = 300 } fn main() -> int32 { return 0; }")


def test_non_constant_member_is_rejected():
    with pytest.raises(LangError, match="must be a compile-time constant"):
        compile_ir(
            "fn side() -> int32 { return 1; }\n"
            "enum E: int32 { A = side() }\n"
            "fn main() -> int32 { return 0; }"
        )


def test_duplicate_member_is_rejected():
    with pytest.raises(LangError, match="duplicate member 'N'"):
        compile_ir("enum Dir { N = 0, N = 1 } fn main() -> int32 { return 0; }")


def test_name_clash_with_builtin_type_is_rejected():
    with pytest.raises(LangError, match="type 'int32' already defined"):
        compile_ir("enum int32 { A = 0 } fn main() -> int32 { return 0; }")


def test_name_clash_with_struct_is_rejected():
    with pytest.raises(LangError, match="type 'Foo' already defined"):
        compile_ir(
            "struct Foo { x: int32; }\n"
            "enum Foo { A = 0 }\n"
            "fn main() -> int32 { return 0; }"
        )


def test_bad_annotation_on_enum_is_rejected():
    with pytest.raises(LangError, match="@volatile only applies"):
        parse("@volatile enum Dir { N = 0 }")


# ---------------------------------------------------------- privacy / scoping

PRIV_LIB = """
@private
enum Secret: int32 { A = 1, B = 2 }

enum Public: int32 { X = 9 }

fn blessed() -> int32 { return Secret::B; }  // same file: allowed
"""


def test_private_enum_usable_within_its_file(tmp_path):
    (tmp_path / "lib.mc").write_text(PRIV_LIB)
    main = tmp_path / "main.mc"
    main.write_text('import "lib";\nfn main() -> int32 { return blessed(); }')
    assert run_path(main) == 2


def test_private_enum_blocked_across_files(tmp_path):
    (tmp_path / "lib.mc").write_text(PRIV_LIB)
    main = tmp_path / "main.mc"
    main.write_text('import "lib";\nfn main() -> int32 { return Secret::A; }')
    with pytest.raises(LangError, match="enum 'Secret' is private to lib.mc"):
        run_path(main)


def test_public_enum_usable_across_files(tmp_path):
    (tmp_path / "lib.mc").write_text(PRIV_LIB)
    main = tmp_path / "main.mc"
    main.write_text('import "lib";\nfn main() -> int32 { return Public::X; }')
    assert run_path(main) == 9


def test_static_enum_is_file_scoped(tmp_path):
    # Each file defines its own @static Mode; they don't collide, and each
    # file sees its own.
    (tmp_path / "lib.mc").write_text(
        "@static enum Mode: int32 { On = 1 }\n"
        "fn lib_mode() -> int32 { return Mode::On; }\n"
    )
    main = tmp_path / "main.mc"
    main.write_text(
        'import "lib";\n'
        "@static enum Mode: int32 { On = 5 }\n"
        "fn main() -> int32 { return Mode::On + lib_mode(); }"
    )
    assert run_path(main) == 6
