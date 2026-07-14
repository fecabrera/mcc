"""Subsumption ordering of rank-tied generic overloads + adaptable-literal viability.

Two related refinements to overload resolution:

1. **Adaptable-literal viability**: an untyped integer literal at a bare
   type-parameter slot keeps a candidate viable only when the deduced binding
   is an *integer* type -- the generic mirror of the concrete ``is_integer``
   shape rule (mcc has no int-to-float literal adaptation). Previously a
   diagonal ``f(x: T, y: T)`` whose ``T`` deduced ``float64`` from another
   argument would "match" an int literal it could never emit, manufacturing
   phantom ties.

2. **Subsumption tie-break**: among a rank-tied top cohort (same tier, same
   pattern specificity), the candidate whose parameter pattern is strictly an
   *instance* of every other member's -- and whose type-parameter constraints
   *imply* theirs -- is the more specialized declaration and wins. The
   canonical case: the diagonal ``f(x: T, y: T)`` beats the open
   ``f(x: T, y: U)`` for agreeing arguments (the open pattern's wildcards
   bind consistently to the diagonal's ``T``; the reverse mapping cannot).
   Constraints participate: type groups imply by subset, ``extends`` bounds
   by the declared nominal chain; group vs bound is incomparable, and an
   unconstrained parameter never implies a constrained wildcard. Cohorts
   with no unique maximum -- mutually non-subsuming or incomparable members
   -- stay the standard ambiguity error, and the tie-break never crosses
   tiers (a bounded template still beats an unbounded one outright).

Method-receiver flavors of both live in test_methods.py; this file pins the
free-function shapes.
"""

import pytest

from mcc.errors import LangError
from helpers import compile_ir, run


# --- Part 1: adaptable-literal viability --------------------------------------

def test_int_literals_into_float_slots_pick_the_converting_overload(capfd):
    # The acceptance shape: a diagonal constructor and a converting sibling.
    # For a point<float64> receiver and int literals, the diagonal deduces
    # T = float64 at the literal slots -- non-integer, so it is NOT viable
    # (there is no int-to-float literal adaptation to emit). The converting
    # candidate binds U = int32 and wins outright; no tie is manufactured.
    assert (
        run(
            """
            import "std/io";
            struct point<T> { x: T; y: T; }
            fn point<T>::constructor(mut self: struct point<T>, x: T, y: T) {
                self.x = x;
                self.y = y;
            }
            fn point<T>::constructor<U>(mut self: struct point<T>, x: U, y: U) {
                self.x = x as T;
                self.y = y as T;
            }
            fn main() -> int32 {
                let p: point<float64>;
                point::constructor(p, 1, 2);
                println(f"{p.x = }, {p.y = }");
                return 0;
            }
            """
        )
        == 0
    )
    assert capfd.readouterr().out == "p.x = 1.000000, p.y = 2.000000\n"


def test_agreeing_receiver_picks_the_diagonal_constructor(capfd):
    # The same pair with a point<int32> receiver: both candidates are viable
    # and rank-tied, and the subsumption tie-break picks the DIAGONAL -- its
    # pattern is strictly an instance of the converting sibling's. The bodies
    # differ observably (the diagonal adds a marker), proving which ran.
    assert (
        run(
            """
            import "std/io";
            struct point<T> { x: T; y: T; }
            fn point<T>::constructor(mut self: struct point<T>, x: T, y: T) {
                self.x = x + 100;
                self.y = y + 100;
            }
            fn point<T>::constructor<U>(mut self: struct point<T>, x: U, y: U) {
                self.x = x as T;
                self.y = y as T;
            }
            fn main() -> int32 {
                let p: point<int32>;
                let a: int32 = 1;
                let b: int32 = 2;
                point::constructor(p, a, b);
                println(f"{p.x = }, {p.y = }");
                return 0;
            }
            """
        )
        == 0
    )
    assert capfd.readouterr().out == "p.x = 101, p.y = 102\n"


def test_lone_diagonal_with_float_receiver_is_a_coercion_error():
    # With NO converting sibling, the diagonal is the single candidate: the
    # call takes the direct path and reports a clean coercion error at the
    # literal argument -- not a wrong pick, not a shape mismatch.
    with pytest.raises(
        LangError,
        match=r"argument 2 of 'point::constructor': expected float64, "
        r"got int32",
    ):
        compile_ir(
            """
            struct point<T> { x: T; y: T; }
            fn point<T>::constructor(mut self: struct point<T>, x: T, y: T) {
                self.x = x;
                self.y = y;
            }
            fn main() -> int32 {
                let p: point<float64>;
                point::constructor(p, 1, 2);
                return 0;
            }
            """
        )


def test_adaptable_literal_filters_the_noninteger_diagonal_free_function(capfd):
    # Free-function flavor of the viability fix: `f(fv, 1)` deduces the
    # diagonal's T = float64 from the variable, so the int literal makes it
    # non-viable and the open sibling wins alone -- previously a phantom tie.
    assert (
        run(
            """
            import "std/io";
            fn f<T>(x: T, y: T) -> int32 { return 1; }
            fn f<T, U>(x: T, y: U) -> int32 { return 2; }
            fn main() -> int32 {
                let fv: float64 = 1.5;
                println(f"{f(fv, 1)}");
                return 0;
            }
            """
        )
        == 0
    )
    assert capfd.readouterr().out == "2\n"


# --- Part 2: the subsumption tie-break ----------------------------------------

def test_diagonal_beats_open_free_function(capfd):
    # The canonical win: f(x: T, y: T) is strictly an instance of
    # f(x: T, y: U) (T := T, U := T binds consistently; the reverse cannot),
    # so agreeing arguments pick the diagonal instead of the former tie.
    assert (
        run(
            """
            import "std/io";
            fn f<T>(x: T, y: T) -> int32 { return 1; }
            fn f<T, U>(x: T, y: U) -> int32 { return 2; }
            fn main() -> int32 {
                let a: int32 = 1;
                let b: int32 = 2;
                println(f"{f(a, b)} {f(a, 1.5)}");
                return 0;
            }
            """
        )
        == 0
    )
    # Disagreeing arguments still fall to the open pattern.
    assert capfd.readouterr().out == "1 2\n"


def test_three_way_chain_resolves_to_the_most_specialized(capfd):
    # (T,T,T) ⊑ (T,T,U) ⊑ (T,U,V): the full diagonal strictly subsumes into
    # BOTH others, so the three-way rank tie resolves to it.
    assert (
        run(
            """
            import "std/io";
            fn f<T>(x: T, y: T, z: T) -> int32 { return 1; }
            fn f<T, U>(x: T, y: T, z: U) -> int32 { return 2; }
            fn f<T, U, V>(x: T, y: U, z: V) -> int32 { return 3; }
            fn main() -> int32 {
                let a: int32 = 1;
                println(f"{f(a, a, a)} {f(a, a, 1.5)} {f(a, 1.5, a)}");
                return 0;
            }
            """
        )
        == 0
    )
    # The chain degrades gracefully: two agreeing positions pick the middle
    # pattern, none agreeing picks the fully open one.
    assert capfd.readouterr().out == "1 2 3\n"


def test_fork_stays_ambiguous_citing_all_contenders():
    # (T,T,U), (T,U,U), (T,U,V): the two partial diagonals are mutually
    # non-subsuming, so no member strictly subsumes into EVERY other -- no
    # maximum exists and the ambiguity stands, citing all three rank-tied
    # declaration sites.
    with pytest.raises(
        LangError, match=r"call to 'f' is ambiguous between overloads"
    ) as excinfo:
        compile_ir(
            """
            fn f<T, U>(x: T, y: T, z: U) -> int32 { return 1; }
            fn f<T, U>(x: T, y: U, z: U) -> int32 { return 2; }
            fn f<T, U, V>(x: T, y: U, z: V) -> int32 { return 3; }
            fn main() -> int32 {
                let a: int32 = 1;
                return f(a, a, a);
            }
            """
        )
    assert len(excinfo.value.notes) == 3


def test_mutual_subsumption_via_defaulted_extra_stays_ambiguous():
    # Same value patterns, one template carrying an extra defaulted parameter:
    # the two are distinct declarations (the default is in the template base)
    # that subsume each other -- neither is STRICTLY more specialized, so the
    # tie stands.
    with pytest.raises(
        LangError, match=r"call to 'f' is ambiguous between overloads"
    ):
        compile_ir(
            """
            fn f<T>(x: T) -> int32 { return 1; }
            fn f<T, U = int64>(x: T) -> int32 { return 2; }
            fn main() -> int32 {
                let a: int32 = 1;
                return f(a);
            }
            """
        )


def test_wildcard_absorbs_surplus_stars_through_an_alias(capfd):
    # A generic alias hiding a pointer (`sp<V>` = `pair<V*, V>`) keeps the
    # written pattern's specificity while the dealiased match binds the open
    # sibling's wildcard to the STARRED sub-pattern (U := T*, W := T) -- the
    # wildcard absorbs the surplus star and the alias-spelled diagonal wins.
    assert (
        run(
            """
            import "std/io";
            struct pair<A, B> { a: A; b: B; }
            type sp<V> = pair<V*, V>;
            fn f<T>(x: sp<T>) -> int32 { return 1; }
            fn f<U, W>(x: pair<U, W>) -> int32 { return 2; }
            fn main() -> int32 {
                let p: pair<int32*, int32>;
                let q: pair<int32, int32>;
                println(f"{f(p)} {f(q)}");
                return 0;
            }
            """
        )
        == 0
    )
    # The starless receiver cannot match sp<T> and falls to the open pattern.
    assert capfd.readouterr().out == "1 2\n"


def test_mut_position_mismatch_blocks_subsumption():
    # mut markers are template identity: candidates whose mut positions
    # differ are never ordered by subsumption, even when the value patterns
    # would map -- the rank tie stands.
    with pytest.raises(
        LangError, match=r"call to 'f' is ambiguous between overloads"
    ):
        compile_ir(
            """
            fn f<T>(mut x: T, y: T) -> int32 { return 1; }
            fn f<A, B>(x: A, mut y: B) -> int32 { return 2; }
            fn main() -> int32 {
                let a: int32 = 1;
                let b: int32 = 2;
                return f(a, b);
            }
            """
        )


def test_fn_type_behind_a_plain_alias_participates_by_name(capfd):
    # A written `fn(...)` pattern never passes the overload viability filter
    # today (pre-existing; such candidates are "no overload" in any set), so
    # the subsumes() fn-type arm -- exact-spelling comparison, v1 -- is
    # defensive only. The REACHABLE flavor is an fn type behind a plain
    # alias: the alias name is a concrete pattern like any other, matches by
    # name, and the diagonal tail orders the tie as usual.
    assert (
        run(
            """
            import "std/io";
            type cbi = fn(int32) -> int32;
            fn g(x: int32) -> int32 { return x; }
            fn f<T>(cb: cbi, x: T, y: T) -> int32 { return 1; }
            fn f<A, B>(cb: cbi, x: A, y: B) -> int32 { return 2; }
            fn main() -> int32 {
                let a: int32 = 1;
                println(f"{f(g, a, a)}");
                return 0;
            }
            """
        )
        == 0
    )
    assert capfd.readouterr().out == "1\n"


def test_tiers_stay_supreme_over_subsumption(capfd):
    # The tie-break runs WITHIN a rank-tied cohort only: a bounded open
    # pattern outranks an unbounded diagonal by tier before subsumption is
    # ever consulted (tier-over-specificity, the established rule).
    assert (
        run(
            """
            import "std/io";
            fn f<T>(x: T, y: T) -> int32 { return 1; }
            fn f<A: int32 | int64, B: int32 | int64>(x: A, y: B) -> int32 {
                return 2;
            }
            fn main() -> int32 {
                let a: int32 = 1;
                println(f"{f(a, a)}");
                return 0;
            }
            """
        )
        == 0
    )
    assert capfd.readouterr().out == "2\n"


# --- Part 3: constraints participate ------------------------------------------

def test_group_subset_lets_the_diagonal_win(capfd):
    # Equal groups on every mapped parameter: the diagonal's group trivially
    # subsets each wildcard's, so the pattern win carries through tier 1.
    assert (
        run(
            """
            import "std/io";
            fn f<T: int8 | int16>(x: T, y: T) -> int32 { return 1; }
            fn f<A: int8 | int16, B: int8 | int16>(x: A, y: B) -> int32 {
                return 2;
            }
            fn main() -> int32 {
                let a: int8 = 1;
                let b: int8 = 2;
                println(f"{f(a, b)}");
                return 0;
            }
            """
        )
        == 0
    )
    assert capfd.readouterr().out == "1\n"


def test_looser_diagonal_vs_tighter_open_is_incomparable():
    # Pattern direction says diagonal-wins, constraint direction says the
    # opposite: the diagonal's WIDER group does not subset the open pattern's
    # tighter ones, so no implication holds, the candidates are incomparable,
    # and the ambiguity stands.
    with pytest.raises(
        LangError, match=r"call to 'f' is ambiguous between overloads"
    ):
        compile_ir(
            """
            fn f<T: int8 | int16 | int32>(x: T, y: T) -> int32 { return 1; }
            fn f<A: int8 | int16, B: int8 | int16>(x: A, y: B) -> int32 {
                return 2;
            }
            fn main() -> int32 {
                let a: int8 = 1;
                let b: int8 = 2;
                return f(a, b);
            }
            """
        )


def test_extends_chain_implication_lets_the_diagonal_win(capfd):
    # `T extends circle` implies `A extends shape` because circle declares
    # the lineage: every satisfier of the tighter bound satisfies the looser
    # one, so the bounded diagonal subsumes into the bounded open pattern.
    assert (
        run(
            """
            import "std/io";
            struct shape { area: int32; }
            struct circle extends shape { r: int32; }
            fn f<T extends circle>(x: T, y: T) -> int32 { return 1; }
            fn f<A extends shape, B extends shape>(x: A, y: B) -> int32 {
                return 2;
            }
            fn main() -> int32 {
                let c: circle;
                let d: circle;
                println(f"{f(c, d)}");
                return 0;
            }
            """
        )
        == 0
    )
    assert capfd.readouterr().out == "1\n"


def test_group_vs_extends_bound_is_incomparable():
    # A closed group and an open `extends` bound never imply each other
    # (membership in a list proves nothing about an open lineage, and vice
    # versa), so the constraint check conservatively fails and the rank tie
    # stands even though the patterns would order.
    with pytest.raises(
        LangError, match=r"call to 'f' is ambiguous between overloads"
    ):
        compile_ir(
            """
            struct shape { area: int32; }
            struct circle extends shape { r: int32; }
            fn f<T: shape | circle>(x: T, y: T) -> int32 { return 1; }
            fn f<A extends shape, B extends shape>(x: A, y: B) -> int32 {
                return 2;
            }
            fn main() -> int32 {
                let c: circle;
                let d: circle;
                return f(c, d);
            }
            """
        )


def test_unconstrained_parameter_does_not_imply_a_constrained_wildcard():
    # Both templates sit in the bounded tier (each groups its trailing
    # parameter), but the diagonal's REPEATED parameter is itself
    # unconstrained -- an unconstrained parameter guarantees nothing, so it
    # cannot stand in for the open pattern's grouped wildcards and the tie
    # stands.
    with pytest.raises(
        LangError, match=r"call to 'f' is ambiguous between overloads"
    ):
        compile_ir(
            """
            fn f<T, W: int8 | int16>(x: T, y: T, z: W) -> int32 { return 1; }
            fn f<U: int8 | int16, V: int8 | int16, W: int8 | int16>(
                x: U, y: V, z: W
            ) -> int32 { return 2; }
            fn main() -> int32 {
                let a: int8 = 1;
                return f(a, a, a);
            }
            """
        )


def test_concrete_position_through_an_alias_satisfies_the_group(capfd):
    # A generic alias hiding a concrete type (`half<V>` = `pair<int32, V>`)
    # maps the open pattern's grouped wildcard to CONCRETE int32, which
    # satisfies the group directly (the same membership test instantiation
    # runs); the remaining wildcard orders by group subset as usual.
    assert (
        run(
            """
            import "std/io";
            struct pair<A, B> { a: A; b: B; }
            type half<V> = pair<int32, V>;
            fn f<V: int8 | int16>(x: half<V>) -> int32 { return 1; }
            fn f<T: int8 | int16 | int32, U: int8 | int16>(
                x: pair<T, U>
            ) -> int32 { return 2; }
            fn main() -> int32 {
                let p: pair<int32, int8>;
                let q: pair<int8, int8>;
                println(f"{f(p)} {f(q)}");
                return 0;
            }
            """
        )
        == 0
    )
    # A non-int32 head cannot match half<V> and falls to the open pattern.
    assert capfd.readouterr().out == "1 2\n"
