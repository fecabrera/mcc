"""Nominal type-parameter bounds: `fn f<T extends shape>(x: T)`.

An `extends` bound after a type parameter constrains it to a struct and the
structs in that struct's declared `extends` lineage. The relation is
**nominal** (the same `nominal_subtype` model the upcast and slice-borrow
use): a layout twin that does not declare the lineage is rejected. Deduction
is unchanged; the bound is an open-set post-deduction viability filter, checked
lazily at each call/instantiation site. A bounded template ranks in the middle
overload tier (concrete beats bounded beats unbounded), so it may coexist with
an unbounded fallback but not with a second bounded same-pattern overload.
"""

import pytest

from mcc.driver import compile_to_ir, emit_interface
from mcc.errors import LangError
from helpers import compile_ir, run, run_path


# a small hierarchy reused across the acceptance tests
HIER = """
struct shape  { area: int32; }
struct circle extends shape  { r: int32; }
struct disc   extends circle { fill: int32; }
"""


# ------------------------------------------------------------ acceptance

def test_bound_accepts_the_bound_struct_itself():
    assert run(
        HIER
        + """
        fn area<T extends shape>(x: T*) -> int32 { return x->area; }
        fn main() -> int32 {
            let s = shape { area = 7 };
            return area(&s);
        }
        """
    ) == 7


def test_bound_accepts_a_direct_subtype():
    assert run(
        HIER
        + """
        fn area<T extends shape>(x: T*) -> int32 { return x->area; }
        fn main() -> int32 {
            let c = circle { area = 9, r = 2 };
            return area(&c);
        }
        """
    ) == 9


def test_bound_accepts_a_transitive_subtype():
    # disc extends circle extends shape -- two hops up the lineage.
    assert run(
        HIER
        + """
        fn area<T extends shape>(x: T*) -> int32 { return x->area; }
        fn main() -> int32 {
            let d = disc { area = 4, r = 1, fill = 3 };
            return area(&d);
        }
        """
    ) == 4


def test_bound_over_a_generic_base_instance():
    # The lineage may pass through a generic base: named<V> extends
    # pair<int32, V>, and the bound is the fully-applied pair<int32, char>.
    assert run(
        """
        struct pair<K, V> { a: K; b: V; }
        struct named<V> extends pair<int32, V> { tag: int32; }
        fn first<T extends pair<int32, char>>(x: T*) -> int32 { return x->a; }
        fn main() -> int32 {
            let n = named<char> { a = 5, b = 'z', tag = 1 };
            return first(&n);
        }
        """
    ) == 5


def test_bound_where_lineage_uses_a_bare_parameter_base():
    # entry<T> extends T: the base is whatever T binds to. An entry<shape>
    # therefore reaches shape and satisfies `extends shape`.
    assert run(
        HIER
        + """
        struct entry<T> extends T { next: int32; }
        fn area<T extends shape>(x: T*) -> int32 { return x->area; }
        fn main() -> int32 {
            let e = entry<shape> { area = 6, next = 0 };
            return area(&e);
        }
        """
    ) == 6


# ------------------------------------------------------------ rejection

def test_rejects_an_unrelated_struct_at_the_call_site():
    with pytest.raises(
        LangError,
        match=r"blob does not satisfy the bound shape of 'area'",
    ) as exc:
        compile_ir(
            HIER
            + """
            struct blob { name: int32; }
            fn area<T extends shape>(x: T*) -> int32 { return 1; }
            fn main() -> int32 {
                let b = blob { name = 1 };
                return area(&b);
            }
            """
        )
    # the offending call site, not the declaration
    assert exc.value.line == HIER.count("\n") + 6


def test_rejects_a_layout_twin_that_does_not_declare_extends():
    # `twin` has shape's exact field prefix but no `extends shape`, so the
    # nominal rule rejects it where a structural rule would have accepted it.
    with pytest.raises(
        LangError,
        match=r"twin does not satisfy the bound shape of 'area'",
    ):
        compile_ir(
            HIER
            + """
            struct twin { area: int32; }
            fn area<T extends shape>(x: T*) -> int32 { return x->area; }
            fn main() -> int32 {
                let t = twin { area = 3 };
                return area(&t);
            }
            """
        )


def test_rejects_a_non_struct_deduced_type():
    with pytest.raises(
        LangError,
        match=r"int32 does not satisfy the bound shape of 'f'",
    ):
        compile_ir(
            HIER
            + """
            fn f<T extends shape>(x: T) -> int32 { return 1; }
            fn main() -> int32 { return f(5); }
            """
        )


def test_explicit_type_argument_hits_the_instantiate_backstop():
    with pytest.raises(
        LangError,
        match=r"blob does not satisfy the bound shape of 'f'",
    ):
        compile_ir(
            HIER
            + """
            struct blob { area: int32; }
            fn f<T extends shape>() -> int32 { return 1; }
            fn main() -> int32 { return f<blob>(); }
            """
        )


# ------------------------------------------------------------ declaration

def test_bound_target_that_is_not_a_struct_is_a_declaration_error():
    with pytest.raises(
        LangError,
        match=r"int32 is not a struct; cannot extend it",
    ) as exc:
        compile_ir(
            """
            fn f<T extends int32>(x: T) -> int32 { return 1; }
            fn main() -> int32 { return f(5); }
            """
        )
    assert exc.value.line == 2  # the declaration, not a call


def test_bound_target_that_is_a_union_is_a_declaration_error():
    with pytest.raises(
        LangError,
        match=r"a union cannot be extended",
    ):
        compile_ir(
            """
            union pick { a: int32; b: int32; }
            fn f<T extends pick>(x: T) -> int32 { return 1; }
            fn main() -> int32 { return 0; }
            """
        )


def test_bound_referencing_a_type_parameter_resolves_per_call():
    # `<S, T extends S>` -- a DEPENDENT bound over an earlier parameter --
    # resolves at the call site once S is deduced: satisfied when T's binding
    # extends S's, rejected otherwise (the resolved bound in the message).
    assert run(
        HIER
        + """
        fn f<S, T extends S>(a: S, b: T) -> int32 {
            return (b as shape).area;
        }
        fn main() -> int32 {
            let s = shape { area = 7 };
            let c = circle { area = 9, r = 1 };
            return f(s, c);       // S = shape, T = circle extends shape
        }
        """
    ) == 9

    with pytest.raises(
        LangError,
        match=r"shape does not satisfy the bound circle of 'f'",
    ):
        compile_ir(
            HIER
            + """
            fn f<S, T extends S>(a: S, b: T) -> int32 { return 0; }
            fn main() -> int32 {
                let s = shape { area = 7 };
                let c = circle { area = 9, r = 1 };
                return f(c, s);   // S = circle; shape does not extend it
            }
            """
        )


def test_bound_on_a_struct_type_parameter_is_rejected():
    with pytest.raises(
        LangError,
        match=r"type-parameter bounds are only supported on function type",
    ):
        compile_ir(
            """
            struct shape { area: int32; }
            struct holder<T extends shape> { it: T; }
            fn main() -> int32 { return 0; }
            """
        )


# ------------------------------------------------------------ default

def test_bound_with_a_satisfying_default_is_accepted():
    # T defaults to circle, which extends shape; sizeof(circle) == 8.
    assert run(
        HIER
        + """
        fn make<T extends shape = circle>() -> int32 { return sizeof(T) as int32; }
        fn main() -> int32 { return make(); }
        """
    ) == 8


def test_bound_with_a_violating_default_is_a_declaration_error():
    with pytest.raises(
        LangError,
        match=r"default blob for type parameter T of 'make' does not satisfy "
        r"its bound shape",
    ) as exc:
        compile_ir(
            HIER
            + """
            struct blob { area: int32; }
            fn make<T extends shape = blob>() -> int32 { return 1; }
            fn main() -> int32 { return make(); }
            """
        )
    # the declaration line, not a call
    assert exc.value.line == HIER.count("\n") + 3


# ------------------------------------------------------------ bound + group

def test_bound_and_group_on_one_parameter_is_rejected():
    with pytest.raises(
        LangError,
        match=r"type parameter 'T' cannot have both a closed type group and "
        r"an 'extends' bound",
    ):
        compile_ir(
            """
            struct shape { area: int32; }
            fn f<T: int32 | int16 extends shape>(x: T) -> int32 { return 1; }
            fn main() -> int32 { return 0; }
            """
        )


# ------------------------------------------------------------ overload ranking

def test_bounded_overload_beside_an_unbounded_fallback():
    # The bounded template claims subtypes of shape; the unbounded catch-all
    # (a tier below) takes everything else.
    assert run(
        HIER
        + """
        fn kind<T extends shape>(x: T) -> int32 { return 1; }
        fn kind<T>(x: T) -> int32 { return 0; }
        fn main() -> int32 {
            let c = circle { area = 1, r = 2 };
            return kind(c) * 10 + kind(42);
        }
        """
    ) == 10


def test_two_bounded_same_pattern_overloads_collide():
    with pytest.raises(
        LangError,
        match=r"two same-pattern bounded overloads are not yet supported",
    ):
        compile_ir(
            """
            struct shape { area: int32; }
            struct other { x: int32; }
            fn kind<T extends shape>(x: T) -> int32 { return 1; }
            fn kind<T extends other>(x: T) -> int32 { return 2; }
            fn main() -> int32 { return 0; }
            """
        )


# ------------------------------------------------------------ symbol base

def test_bound_joins_the_template_symbol_base():
    # The bound is part of the collision key: two same-pattern templates whose
    # bounds differ still collide (open sets cannot be shown disjoint), and the
    # error spells the bound into the symbol base.
    with pytest.raises(
        LangError,
        match=r"'kind<\$0 extends other>\(\$0\)' overlaps "
        r"'kind<\$0 extends shape>\(\$0\)'",
    ):
        compile_ir(
            """
            struct shape { area: int32; }
            struct other { x: int32; }
            fn kind<T extends shape>(x: T) -> int32 { return 1; }
            fn kind<T extends other>(x: T) -> int32 { return 2; }
            fn main() -> int32 { return 0; }
            """
        )


# ------------------------------------------------------------ generic alias bound

def test_bound_as_a_generic_alias_instance_resolves_transparently():
    # A transparent generic alias in the bound resolves to the underlying
    # struct, so a value whose lineage reaches that struct satisfies it.
    assert run(
        """
        struct pair<K, V> { a: K; b: V; }
        type ipair<V> = pair<int32, V>;
        struct named extends pair<int32, char> { tag: int32; }
        fn first<T extends ipair<char>>(x: T*) -> int32 { return x->a; }
        fn main() -> int32 {
            let n = named { a = 8, b = 'q', tag = 0 };
            return first(&n);
        }
        """
    ) == 8


# ------------------------------------------------------------ .mci round-trip

def test_bound_renders_in_the_interface_and_round_trips(tmp_path):
    lib = tmp_path / "lib.mc"
    lib.write_text(
        "struct shape { area: int32; }\n"
        "struct circle extends shape { r: int32; }\n"
        "fn area<T extends shape>(x: T*) -> int32 { return x->area; }\n"
    )
    out = tmp_path / "lib.mci"
    assert emit_interface(lib, (tmp_path,), None, {}, out) == 0
    stub = out.read_text()
    # The template travels verbatim, bound included, and its target struct is
    # pulled into the stub.
    assert "fn area<T extends shape>(x: T*)" in stub
    assert "struct shape" in stub
    lib.unlink()  # force the import to resolve through the stub
    main = tmp_path / "main.mc"
    main.write_text(
        'import "lib";\n'
        "fn main() -> int32 {\n"
        "    let c = circle { area = 11, r = 3 };\n"
        "    return area(&c);\n"
        "}\n"
    )
    assert run_path(main) == 11  # the re-imported bound still accepts


def test_reimported_bound_still_rejects_a_non_subtype(tmp_path):
    lib = tmp_path / "lib.mc"
    lib.write_text(
        "struct shape { area: int32; }\n"
        "struct blob { area: int32; }\n"
        "fn area<T extends shape>(x: T*) -> int32 { return x->area; }\n"
    )
    out = tmp_path / "lib.mci"
    assert emit_interface(lib, (tmp_path,), None, {}, out) == 0
    lib.unlink()
    main = tmp_path / "main.mc"
    main.write_text(
        'import "lib";\n'
        "fn main() -> int32 {\n"
        "    let b = blob { area = 1 };\n"
        "    return area(&b);\n"
        "}\n"
    )
    with pytest.raises(
        LangError,
        match=r"blob does not satisfy the bound shape of 'area'",
    ):
        compile_to_ir(main)


# ------------------------------------------------- dependent bounds

# The driving stdlib shape: a generic container method accepting "anything
# that extends slice<T>" with no explicit borrow at the call site.
DEP = """
struct box<T> extends slice<T> { cap: uint64; }
fn box<T>::eq<U extends slice<T>>(const self: box<T>, const o: U) -> bool {
    return self.length == (o as slice<const T>).length;
}
"""


def test_qualifier_dependent_bound_accepts_without_a_borrow():
    # `U extends slice<T>` references the METHOD QUALIFIER's parameter: the
    # bound resolves per instantiation (T = int32 makes it slice<int32>), so
    # a whole box<int32> argument binds U with no `as` at the call.
    assert run(
        DEP
        + """
        fn main() -> int32 {
            let a: box<int32>;     // uninitialized (C-style): set the fields read
            a.length = 2;
            let b: box<int32>;
            b.length = 2;
            return a.eq(b) ? 0 : 1;
        }
        """
    ) == 0


def test_qualifier_dependent_bound_partitions_per_instantiation():
    # The same family under T = int32 rejects a box<char>: the RESOLVED
    # bound (slice<int32>) appears in the message.
    with pytest.raises(
        LangError,
        match=r"box<char> does not satisfy the bound slice<int32> of 'box::eq'",
    ):
        compile_ir(
            DEP
            + """
            fn main() -> int32 {
                let a: box<int32>;
                let c: box<char>;
                return box<int32>::eq(a, c) ? 0 : 1;
            }
            """
        )


def test_unrelated_struct_fails_the_dependent_bound():
    with pytest.raises(
        LangError,
        match=r"other does not satisfy the bound slice<int32> of 'box::eq'",
    ):
        compile_ir(
            DEP
            + """
            struct other { n: int32; }
            fn main() -> int32 {
                let a: box<int32>;
                let o = other { n = 1 };
                return a.eq(o) ? 0 : 1;
            }
            """
        )


def test_unsatisfiable_dependent_bound_rejects():
    # `U extends T` with T deduced to a scalar: the resolved bound is not a
    # struct, so nothing satisfies it -- the standard rejection, naming it.
    with pytest.raises(
        LangError,
        match=r"does not satisfy the bound int32 of 'f'",
    ):
        compile_ir(
            """
            fn f<T, U extends T>(a: T, b: U) -> int32 { return 0; }
            fn main() -> int32 { return f(1 as int32, 2 as int32); }
            """
        )


def test_dependent_bound_inherited_by_concrete_derivation():
    # A derived struct inherits the dependently-bounded method with the
    # qualifier parameter seeded (T = int32): the bound resolves through the
    # seed and both accepts and (below) rejects correctly.
    assert run(
        DEP
        + """
        struct ibox extends box<int32> { extra: int32; }
        fn main() -> int32 {
            let a: ibox;
            a.length = 3;
            let b: box<int32>;
            b.length = 3;
            return a.eq(b) ? 0 : 1;
        }
        """
    ) == 0

    with pytest.raises(
        LangError,
        match=r"other does not satisfy the bound slice<int32> of 'box::eq'",
    ):
        compile_ir(
            DEP
            + """
            struct ibox extends box<int32> { extra: int32; }
            struct other { n: int32; }
            fn main() -> int32 {
                let a: ibox;
                let o = other { n = 1 };
                return a.eq(o) ? 0 : 1;
            }
            """
        )


def test_dependent_bound_inherited_by_generic_derivation():
    assert run(
        DEP
        + """
        struct gbox<T> extends box<T> { extra: int32; }
        fn main() -> int32 {
            let a: gbox<int32>;
            a.length = 3;
            let b: box<int32>;
            b.length = 3;
            return a.eq(b) ? 0 : 1;
        }
        """
    ) == 0


def test_unbounded_fallback_catches_what_the_dependent_bound_excludes():
    # Tier ranking is unchanged: the dependently-bounded overload wins where
    # its (resolved) bound holds; anything it excludes falls to the unbounded
    # same-name fallback one tier below.
    assert run(
        """
        struct box<T> extends slice<T> { cap: uint64; }
        fn box<T>::tag<U extends slice<T>>(const self: box<T>, const o: U) -> int32 {
            return 1;                    // the bounded member
        }
        fn box<T>::tag<U>(const self: box<T>, const o: U) -> int32 {
            return 2;                    // the unbounded fallback
        }
        fn main() -> int32 {
            let a: box<int32>;
            let b: box<int32>;
            let n: int32 = 7;
            return a.tag(b) * 10 + a.tag(n);   // bounded, then fallback: 12
        }
        """
    ) == 12


def test_dependent_bound_round_trips_through_an_interface(tmp_path):
    lib = tmp_path / "lib.mc"
    lib.write_text(
        "struct box<T> extends slice<T> { cap: uint64; }\n"
        "fn box<T>::eq<U extends slice<T>>"
        "(const self: box<T>, const o: U) -> bool {\n"
        "    return self.length == (o as slice<const T>).length;\n"
        "}\n"
    )
    out = tmp_path / "lib.mci"
    assert emit_interface(lib, (tmp_path,), None, {}, out) == 0
    stub = out.read_text()
    # The template travels verbatim, dependent bound included.
    assert "<U extends slice<T>>" in stub
    lib.unlink()  # force the import to resolve through the stub
    main = tmp_path / "main.mc"
    main.write_text(
        'import "lib";\n'
        "fn main() -> int32 {\n"
        "    let a: box<int32>;\n"
        "    a.length = 1;\n"
        "    let b: box<int32>;\n"
        "    b.length = 1;\n"
        "    return a.eq(b) ? 0 : 1;\n"
        "}\n"
    )
    assert run_path(main) == 0  # the re-imported dependent bound still works


# ------------------------------------------------- slice const-covariance

def test_a_const_slice_bound_admits_the_mutable_family(capfd):
    # `extends slice<const char>` is const-covariant: anything whose
    # lineage reaches slice<char> satisfies it too (adding element const
    # is always safe, the coerce widening rule) -- so ONE signature takes
    # a slice of either constness and an owned string/list<char>. This is
    # the signature behind std/io's print/println and panic/assert.
    run(
        'import "std/io";\n'
        + """
        fn show<T extends slice<const char>>(const str: T) {
            print(str as slice<const char>);
            print("|");
        }
        fn main() -> int32 {
            let sc: slice<const char> = "sc";
            show(sc);
            let owned = "ow";
            show(owned as slice<char>);
            let s = string("str");
            show(s);
            let l = list<char>();
            l.push('l');
            show(l);
            println("");
            return 0;
        }
        """
    )
    assert capfd.readouterr().out == "sc|ow|str|l|\n"


def test_the_covariance_is_strictly_one_way():
    # A mutable slice<char> bound still rejects slice<const char>:
    # satisfying it through the mutable spelling would launder const away.
    with pytest.raises(
        LangError,
        match=r"slice<const char> does not satisfy the bound slice<char> "
        r"of 'take'",
    ):
        compile_ir(
            'import "std/io";\n'
            "fn take<T extends slice<char>>(const str: T) {}\n"
            "fn main() -> int32 {\n"
            '    let sc: slice<const char> = "sc";\n'
            "    take(sc);\n"
            "    return 0;\n"
            "}"
        )


def test_a_string_literal_binds_a_bounded_char_slice_parameter(capfd):
    # A literal pre-evaluates to a char* that could never satisfy a slice
    # bound; instead the declared bound itself becomes the binding
    # (T = slice<const char>) and the literal borrows against it at
    # emission, exactly as a concrete slice parameter takes one. A
    # ternary of literals rides along.
    run(
        'import "std/io";\n'
        + """
        fn show<T extends slice<const char>>(const str: T) {
            print(str as slice<const char>);
            print("|");
        }
        fn main() -> int32 {
            show("literal");
            show(true ? "yes" : "no");
            println("");
            return 0;
        }
        """
    )
    assert capfd.readouterr().out == "literal|yes|\n"


def test_a_literal_at_a_mutable_slice_bound_matches_the_concrete_rule():
    # The literal binds the bound exactly as declared (T = slice<char>
    # here), in parity with the concrete-parameter rule: a string literal
    # adapts to a concrete `slice<char>` parameter today, so the bounded
    # spelling accepts it identically -- the covariance launders nothing
    # the concrete form would reject.
    ir = compile_ir(
        "fn take<T extends slice<char>>(const str: T) {}\n"
        'fn main() -> int32 { take("hi"); return 0; }'
    )
    assert 'call void @"take<$0 extends slice<char>>($0)<slice<char>>"' in ir
