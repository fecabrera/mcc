"""Closed type groups: `fn f<T: int64 | int32>(x: T)`.

A pipe-separated group after a type parameter is the closed set of types the
parameter may instantiate to. Deduction is unchanged; the group is a
post-deduction viability filter, checked eagerly at end of codegen for every
listed member. Same-pattern templates with disjoint groups partition into a
resolvable overload set; overlapping groups collide at declaration.
"""

import pytest

from mcc.driver import compile_to_ir, emit_interface
from mcc.errors import LangError
from helpers import compile_ir, run, run_path


# ------------------------------------------------------------ basic filter

def test_member_instantiation_compiles_and_runs():
    assert run(
        """
        fn f<T: int64 | int32>(x: T) -> int32 { return sizeof(T) as int32; }
        fn main() -> int32 {
            let a: int32 = 1;
            let b: int64 = 2;
            return f(a) * 10 + f(b);
        }
        """
    ) == 48


def test_non_member_deduction_is_a_call_site_error():
    with pytest.raises(
        LangError,
        match=r"int8 is not in the type group of 'f' \(int64 \| int32\)",
    ) as exc:
        compile_ir(
            """
            fn f<T: int64 | int32>(x: T) -> int32 { return 1; }
            fn main() -> int32 { let v: int8 = 3; return f(v); }
            """
        )
    assert exc.value.line == 3  # the call site, not the declaration


def test_explicit_type_argument_is_checked():
    with pytest.raises(
        LangError,
        match=r"char is not in the type group of 'f' \(int64 \| int32\)",
    ):
        compile_ir(
            """
            fn f<T: int64 | int32>(x: T) -> int32 { return 1; }
            fn main() -> int32 { return f<char>('c'); }
            """
        )


def test_explicit_pointer_type_argument_is_checked():
    with pytest.raises(
        LangError,
        match=r"char\* is not in the type group of 'f' \(int64 \| int32\)",
    ):
        compile_ir(
            """
            fn f<T: int64 | int32>(x: T) -> int32 { return 1; }
            fn main() -> int32 { return f<char*>("s"); }
            """
        )


def test_explicit_member_type_argument_is_fine():
    assert run(
        """
        fn f<T: int64 | int32>(x: T) -> int32 { return sizeof(T) as int32; }
        fn main() -> int32 { return f<int64>(1); }
        """
    ) == 8


def test_pointer_members_are_ordinary_concrete_types():
    assert run(
        """
        fn probe<T: char* | int32>(x: T) -> int32 { return 1; }
        fn main() -> int32 { return probe("hi"); }
        """
    ) == 1


def test_generic_struct_instances_are_valid_members():
    # A concrete instantiation of a generic struct is a concrete type; the
    # member's own `>` (and a `>>` needing the split) parse inside the list.
    assert run(
        """
        struct box<T> { value: T; }
        fn f<U: box<box<int32>> | box<int32> | int64>(x: U) -> int32 {
            return sizeof(U) as int32;
        }
        fn main() -> int32 {
            let b = struct box<int32> { value = 7 };
            return f(b);
        }
        """
    ) == 4


def test_group_on_static_template_filters_too():
    with pytest.raises(
        LangError,
        match=r"int8 is not in the type group of 'f' \(int32\)",
    ):
        compile_ir(
            """
            @static fn f<T: int32>(x: T) -> int32 { return 1; }
            fn main() -> int32 { let v: int8 = 3; return f(v); }
            """
        )


# ------------------------------------------------------------------ syntax

def test_group_member_may_not_reference_a_type_parameter():
    with pytest.raises(
        LangError,
        match=r"type group member T for parameter 'U' references type "
        r"parameter 'T'; group members must be concrete types",
    ):
        compile_ir(
            """
            fn f<T, U: T | int32>(x: T, y: U) -> int32 { return 1; }
            fn main() -> int32 { return 0; }
            """
        )


def test_unresolved_member_errors_at_declaration():
    with pytest.raises(LangError, match=r"unknown type 'nosuch'"):
        compile_ir(
            """
            fn f<T: nosuch | int32>(x: T) -> int32 { return 1; }
            fn main() -> int32 { return 0; }
            """
        )


def test_duplicate_member_errors():
    # Matching the duplicate case-type-arm precedent: a group lists each
    # member once.
    with pytest.raises(
        LangError,
        match=r"duplicate type group member int32 for type parameter T of 'f'",
    ):
        compile_ir(
            """
            fn f<T: int32 | int64 | int32>(x: T) -> int32 { return 1; }
            fn main() -> int32 { return 0; }
            """
        )


def test_alias_duplicating_a_member_is_caught_resolved():
    # Members compare resolved, so an alias spelling of a listed member is
    # the same duplicate.
    with pytest.raises(
        LangError,
        match=r"duplicate type group member int32 for type parameter T of 'f'",
    ):
        compile_ir(
            """
            type word = int32;
            fn f<T: int32 | word>(x: T) -> int32 { return 1; }
            fn main() -> int32 { return 0; }
            """
        )


def test_groups_are_rejected_on_struct_type_parameters():
    with pytest.raises(
        LangError,
        match="type groups are only supported on function type parameters",
    ):
        compile_ir(
            """
            struct box<T: int32 | int64> { value: T; }
            fn main() -> int32 { return 0; }
            """
        )


# ---------------------------------------------------------------- defaults

def test_default_must_name_a_group_member():
    with pytest.raises(
        LangError,
        match=r"default for type parameter T of 'f' must be a member of its "
        r"type group \(int64 \| int32\), got char",
    ):
        compile_ir(
            """
            fn f<T: int64 | int32 = char>(x: T) -> int32 { return 1; }
            fn main() -> int32 { return 0; }
            """
        )


def test_default_alias_of_a_member_qualifies():
    # Membership is checked resolved, so an alias naming a member is fine.
    assert run(
        """
        type word = int32;
        fn f<T: int64 | int32 = word>(x: T) -> int32 {
            return sizeof(T) as int32;
        }
        fn main() -> int32 { return f(0); }
        """
    ) == 4


def test_grouped_default_may_not_reference_a_parameter():
    with pytest.raises(
        LangError,
        match=r"default for type parameter 'U' references 'T'; a grouped "
        r"parameter's default must name a group member",
    ):
        compile_ir(
            """
            fn f<T, U: int32 | int64 = T>(x: T, y: U) -> int32 { return 1; }
            fn main() -> int32 { return 0; }
            """
        )


def test_group_default_anchors_untyped_literal():
    # The shipped priority order is unchanged: the declared default beats the
    # untyped literal's int32 leaning.
    assert run(
        """
        fn width<T: int64 | int32 = int64>(x: T) -> int32 {
            return sizeof(T) as int32;
        }
        fn main() -> int32 { return width(0); }
        """
    ) == 8


# ---------------------------------------------------------- eager checking

EAGER_BAD = """
fn only32(x: int32) -> int32 { return x; }
fn g<T: int32 | int64>(x: T) -> int32 { return only32(x); }
"""


def test_uncalled_member_is_checked_eagerly():
    # No call anywhere: the int64 member still instantiates at end of
    # codegen, and its body error lands at the declaration naming the member.
    with pytest.raises(
        LangError, match=r"argument 1 of 'only32': expected int32, got int64"
    ) as exc:
        compile_ir(EAGER_BAD + "fn main() -> int32 { return 0; }")
    note = exc.value.notes[-1]
    assert note.message == "in instantiation of g<int64>"
    assert note.line == 3  # the declaration, not a call site


def test_called_member_does_not_mask_the_eager_check():
    # A program only ever exercising the good member still fails to build.
    with pytest.raises(
        LangError, match=r"argument 1 of 'only32': expected int32, got int64"
    ):
        compile_ir(EAGER_BAD + "fn main() -> int32 { return g(7); }")


def test_eager_check_covers_static_templates():
    with pytest.raises(
        LangError, match=r"argument 1 of 'only32': expected int32, got int64"
    ):
        compile_ir(
            """
            fn only32(x: int32) -> int32 { return x; }
            @static fn g<T: int32 | int64>(x: T) -> int32 {
                return only32(x);
            }
            fn main() -> int32 { return 0; }
            """
        )


def test_eager_instances_are_emitted_once_and_shared():
    # The eager instance and a call's instance are the same monomorphized
    # function; the never-called member is an ordinary emitted definition.
    ir = compile_ir(
        """
        fn g<T: int32 | int64>(x: T) -> T { return x; }
        fn main() -> int32 { let v: int32 = 1; return g(v); }
        """
    )
    assert ir.count('define i32 @"g<$0: int32|int64>($0)<int32>"') == 1
    assert ir.count('define i64 @"g<$0: int32|int64>($0)<int64>"') == 1


def test_non_grouped_non_defaulted_parameter_skips_eager_enumeration():
    # `U` has no closed set of types, so the template cannot be enumerated;
    # it is checked at its call sites only, like an ordinary generic.
    assert run(
        """
        fn pairup<T: int32 | int64, U>(x: T, y: U) -> int32 {
            return sizeof(T) as int32 + sizeof(U) as int32;
        }
        fn main() -> int32 {
            let a: int64 = 1;
            let b: int8 = 2;
            return pairup(a, b);
        }
        """
    ) == 9


def test_defaulted_sibling_parameter_is_enumerated_through_its_default():
    # `U = int8` closes the enumeration: (T=int32, U=int8) and
    # (T=int64, U=int8) are both checked eagerly -- and the int64 one fails.
    with pytest.raises(
        LangError, match=r"argument 1 of 'only32': expected int32, got int64"
    ) as exc:
        compile_ir(
            """
            fn only32(x: int32) -> int32 { return x; }
            fn g<T: int32 | int64, U = int8>(x: T, y: U) -> int32 {
                return only32(x);
            }
            fn main() -> int32 { return 0; }
            """
        )
    assert exc.value.notes[-1].message == "in instantiation of g<int64, int8>"


# --------------------------------------------------- overload partitioning

SHOW_PAIR = """
fn show<T: int32 | int16 | int8>(x: T) -> int32 { return 1; }
fn show<T: uint32 | uint16 | uint8>(x: T) -> int32 { return 2; }
"""


def test_disjoint_groups_partition_a_same_pattern_set():
    assert run(
        SHOW_PAIR
        + """
        fn main() -> int32 {
            let a: int16 = -4;
            let b: uint32 = 4;
            return show(a) * 10 + show(b);
        }
        """
    ) == 12


def test_deduction_outside_every_group_is_no_overload():
    with pytest.raises(
        LangError, match=r"no overload of 'show' with signature show\(float64\)"
    ):
        compile_ir(
            SHOW_PAIR
            + """
            fn main() -> int32 { let x: float64 = 1.5; return show(x); }
            """
        )


def test_group_appears_in_the_instance_symbol():
    # The group joins the template's symbol base: two disjoint-group
    # same-pattern templates are distinct symbols.
    ir = compile_ir(
        SHOW_PAIR
        + """
        fn main() -> int32 { let a: int16 = -4; return show(a); }
        """
    )
    assert '@"show<$0: int32|int16|int8>($0)<int16>"' in ir
    assert '@"show<$0: uint32|uint16|uint8>($0)<uint16>"' in ir


def test_overlapping_groups_collide_at_declaration():
    with pytest.raises(
        LangError,
        match=r"function 'h<\$0: int64\|char>\(\$0\)' overlaps "
        r"'h<\$0: int32\|int64>\(\$0\)'; same-pattern overloads need "
        r"disjoint type groups",
    ):
        compile_ir(
            """
            fn h<T: int32 | int64>(x: T) -> int32 { return 1; }
            fn h<T: int64 | char>(x: T) -> int32 { return 2; }
            fn main() -> int32 { return 0; }
            """
        )


def test_groups_on_different_parameters_overlap():
    # Each template leaves the other's constrained parameter unbounded, so a
    # deduction can satisfy both: not a resolvable partition.
    with pytest.raises(LangError, match="need disjoint type groups"):
        compile_ir(
            """
            fn h<T: int32 | int64, U>(x: T, y: U) -> int32 { return 1; }
            fn h<T, U: char | bool>(x: T, y: U) -> int32 { return 2; }
            fn main() -> int32 { return 0; }
            """
        )


def test_identical_groups_are_still_a_duplicate_definition():
    with pytest.raises(
        LangError,
        match=r"function 'h<\$0: int32\|int64>\(\$0\)' already defined; "
        "overloads must differ in parameter patterns",
    ):
        compile_ir(
            """
            fn h<T: int32 | int64>(x: T) -> int32 { return 1; }
            fn h<U: int32 | int64>(x: U) -> int32 { return 2; }
            fn main() -> int32 { return 0; }
            """
        )


def test_overlapping_groups_collide_cross_module(tmp_path):
    (tmp_path / "lib.mc").write_text(
        "fn h<T: int32 | int64>(x: T) -> int32 { return 1; }\n"
    )
    main = tmp_path / "main.mc"
    main.write_text(
        'import "lib";\n'
        "fn h<T: int64 | char>(x: T) -> int32 { return 2; }\n"
        "fn main() -> int32 { return 0; }\n"
    )
    with pytest.raises(LangError, match="need disjoint type groups"):
        compile_to_ir(main)


# ----------------------------------------------------------------- ranking

def test_concrete_beats_bounded_beats_unbounded():
    # All three tiers in one set: the concrete member takes the exact match,
    # the bounded generic takes its other member, the unbounded generic
    # catches the rest.
    assert run(
        """
        fn t(x: int32) -> int32 { return 3; }
        fn t<T: int32 | int64>(x: T) -> int32 { return 2; }
        fn t<T>(x: T) -> int32 { return 1; }
        fn main() -> int32 {
            let a: int32 = 0;
            let b: int64 = 0;
            let c: int16 = 0;
            return t(a) * 100 + t(b) * 10 + t(c);
        }
        """
    ) == 321


def test_bounded_beats_a_more_specific_unbounded_pattern():
    # The tier leads the sort key: a bounded bare-T candidate outranks an
    # unbounded candidate even when the latter's pattern is more specific.
    assert run(
        """
        fn t<T: int32* | int64>(x: T) -> int32 { return 2; }
        fn t<T>(x: T*) -> int32 { return 1; }
        fn main() -> int32 {
            let v: int32 = 0;
            return t(&v);
        }
        """
    ) == 2


def test_unbounded_catches_what_the_groups_exclude():
    assert run(
        SHOW_PAIR
        + """
        fn show<T>(x: T) -> int32 { return 3; }
        fn main() -> int32 {
            let a: int8 = 1;
            let b: float64 = 1.5;
            return show(a) * 10 + show(b);
        }
        """
    ) == 13


# ------------------------------------------------------------- .mci stubs

def test_group_renders_in_the_interface_and_round_trips(tmp_path):
    lib = tmp_path / "lib.mc"
    lib.write_text(
        "fn show<T: int32 | int16 | int8>(x: T) -> int32 { return 1; }\n"
        "fn show<T: uint32 | uint16 | uint8>(x: T) -> int32 { return 2; }\n"
    )
    out = tmp_path / "lib.mci"
    assert emit_interface(lib, (tmp_path,), None, {}, out) == 0
    stub = out.read_text()
    # A generic template travels verbatim, group included.
    assert "fn show<T: int32 | int16 | int8>(x: T)" in stub
    assert "fn show<T: uint32 | uint16 | uint8>(x: T)" in stub
    lib.unlink()  # force the import to resolve through the stub
    main = tmp_path / "main.mc"
    main.write_text(
        'import "lib";\n'
        "fn main() -> int32 {\n"
        "    let a: int16 = -4;\n"
        "    let b: uint32 = 4;\n"
        "    return show(a) * 10 + show(b);\n"
        "}\n"
    )
    assert run_path(main) == 12  # the re-imported set still partitions


def test_reimported_group_enforces_identically(tmp_path):
    lib = tmp_path / "lib.mc"
    lib.write_text("fn f<T: int64 | int32>(x: T) -> int32 { return 1; }\n")
    out = tmp_path / "lib.mci"
    assert emit_interface(lib, (tmp_path,), None, {}, out) == 0
    lib.unlink()
    main = tmp_path / "main.mc"
    main.write_text(
        'import "lib";\n'
        "fn main() -> int32 { let v: int8 = 3; return f(v); }\n"
    )
    with pytest.raises(
        LangError,
        match=r"int8 is not in the type group of 'f' \(int64 \| int32\)",
    ):
        compile_to_ir(main)


def test_group_member_struct_is_pulled_into_the_stub(tmp_path):
    lib = tmp_path / "lib.mc"
    lib.write_text(
        "@private struct color { r: int32; }\n"
        "fn f<T: color | int32>(x: T) -> int32 { return 1; }\n"
    )
    out = tmp_path / "lib.mci"
    assert emit_interface(lib, (tmp_path,), None, {}, out) == 0
    assert "struct color" in out.read_text()


def test_mci_template_overlapping_root_template_collides(tmp_path):
    (tmp_path / "api.mci").write_text(
        "fn h<T: int32 | int64>(x: T) -> int32 { return 1; }\n"
    )
    main = tmp_path / "main.mc"
    main.write_text(
        'import "api";\n'
        "fn h<T: int64 | char>(x: T) -> int32 { return 2; }\n"
        "fn main() -> int32 { return 0; }\n"
    )
    with pytest.raises(LangError, match="need disjoint type groups"):
        compile_to_ir(main)


# ------------------------------------------------- generic case-type arms

def test_group_error_composes_under_a_generic_arm():
    # A generic `when T v:` body calling a bounded template compiles one copy
    # per boxed tag; the out-of-group tag's copy fails with the group error
    # under the existing arm note.
    with pytest.raises(
        LangError,
        match=r"char is not in the type group of 'f' \(int32 \| int64\)",
    ) as exc:
        compile_ir(
            """
            fn f<T: int32 | int64>(x: T) -> int32 { return 1; }
            fn probe(a: any) -> int32 {
                case type (a) {
                    when T v: return f(v);
                    else:     return 0;
                }
                return -1;
            }
            fn main() -> int32 { let a: any = 'c'; return probe(a); }
            """
        )
    assert any(
        note.message == "in case type arm for char" for note in exc.value.notes
    )


def test_in_group_tags_dispatch_through_a_generic_arm():
    assert run(
        SHOW_PAIR
        + """
        fn probe(a: any) -> int32 {
            case type (a) {
                when T v: return show(v);
                else:     return 0;
            }
            return -1;
        }
        fn main() -> int32 {
            let a: any = -4 as int16;
            let b: any = 4 as uint32;
            return probe(a) * 10 + probe(b);
        }
        """
    ) == 12


def test_eager_instances_feed_the_arms_fixpoint():
    # Nothing calls poke, and main only ever boxes int32 -- but the eager
    # float64 member instantiation boxes float64, which the pending generic
    # arm must then claim, and that copy fails: the two finalizers loop.
    with pytest.raises(
        LangError, match=r"argument 1 of 'frob': expected int32, got float64"
    ) as exc:
        compile_ir(
            """
            fn frob(x: int32) -> int32 { return x; }
            fn probe(a: any) -> int32 {
                case type (a) {
                    when T v: return frob(v);
                    else:     return 0;
                }
                return -1;
            }
            fn poke<T: float64 | int32>(y: T) -> int32 {
                let a: any = y;
                return probe(a);
            }
            fn main() -> int32 { return probe(7 as int32); }
            """
        )
    assert any(
        note.message == "in case type arm for float64"
        for note in exc.value.notes
    )
