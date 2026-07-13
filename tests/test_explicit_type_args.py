"""Explicit type arguments at ``::`` call sites: ``point<float64>::magnitude(p)``.

The qualifier of a qualified method call may spell the receiver instantiation.
The written reference resolves as an ordinary type use (arity checks,
trailing-default fill, an enclosing method's live type bindings, and
generic-alias substitution -- permutation included -- all apply for free), and
dispatch matches the resolved instantiation against each candidate's declared
qualifier annotation: a fresh type-parameter position seeds its binding from
the pin, a concrete (specialized) position must agree or the member does not
apply. The one type-argument list belongs to the STRUCT; a method's own type
parameters stay inference-only, exactly as at a dot call.

Bare aliases of a COMPLETE type inject the instantiation they name
(``pointf::sum(q)`` over ``type pointf = point<float64>`` pins
``point<float64>``), deliberately closing the old name-only-chase soundness
gap: a cross-instantiation receiver is now an error, not a silent re-dispatch.
Bare struct qualifiers (``point::m(p)``) are unchanged -- pure namespace,
inference from the arguments.

The primary motivation is chaining constructors and destructors, whose only
callable spelling is qualified: inside a generic method body,
``point<T>::constructor(self, x as T, y as T)`` resolves ``T`` through the
enclosing instantiation.
"""

import pytest

from mcc.driver import emit_interface
from mcc.errors import LangError
from helpers import compile_ir, parse, run, run_path


# --- the basic spellings -------------------------------------------------------

def test_written_qualifier_pins_and_calls():
    # The headline form: the qualifier spells the receiver instantiation and
    # the call dispatches into the generic member with T fixed by the pin.
    assert run(
        """
        struct point<T> { x: T; y: T; }
        fn point<T>::sum(const self: point<T>) -> T { return self.x + self.y; }
        fn main() -> int32 {
            let p: point<int32> = { x = 3, y = 4 };
            return point<int32>::sum(p);
        }
        """
    ) == 7


def test_written_qualifier_dispatches_to_a_full_specialization(capfd):
    # A matching pin MUST reach a declared specialization -- the written
    # instantiation selects among concrete members too (the positional
    # explicit-type-argument gate would silently drop them: a specialization
    # has no type parameters). The bodies return different values, so the
    # output proves which one ran.
    assert run(
        """
        import "std/io";
        struct box<T> { v: T; }
        fn box<T>::tag(self: box<T>) -> int32 { return 1; }
        fn box<float64>::tag(self: box<float64>) -> int32 { return 2; }
        fn main() -> int32 {
            let bi: box<int32> = { v = 7 };
            let bf: box<float64> = { v = 1.0 };
            println("{} {}", box<int32>::tag(bi), box<float64>::tag(bf));
            return 0;
        }
        """
    ) == 0
    assert capfd.readouterr().out == "1 2\n"


def test_lone_specialization_family_accepts_a_matching_pin():
    # A family of ONE concrete member (a lone specialization) resolves through
    # the set path when pinned: the direct-call path's "not a generic
    # function" gate never fires on the consumed qualifier arguments.
    assert run(
        """
        struct box<T> { v: T; }
        fn box<int32>::get(const self: box<int32>) -> int32 { return self.v; }
        fn main() -> int32 {
            let b: box<int32> = { v = 5 };
            return box<int32>::get(b);
        }
        """
    ) == 5


def test_lone_specialization_family_rejects_a_mismatched_pin():
    # The pin must match the lone member's declared instantiation even though
    # nothing else could win: `box<float64>::get` names a member the family
    # does not have.
    with pytest.raises(
        LangError,
        match=r"line 6: 'box::get' has no member for box<float64>: the "
        r"qualifier's type arguments pin the receiver instantiation",
    ):
        compile_ir(
            """
            struct box<T> { v: T; }
            fn box<int32>::get(const self: box<int32>) -> int32 { return self.v; }
            fn main() -> int32 {
                let b: box<int32> = { v = 5 };
                return box<float64>::get(b);
            }
            """
        )


def test_no_receiver_method_takes_the_pin():
    # With no receiver argument to infer from, the written qualifier is the
    # only source of T -- the case a bare call cannot express at all.
    assert run(
        """
        struct point<T> { x: T; }
        fn point<T>::zero() -> point<T> { return struct point<T> { x = 0 as T }; }
        fn main() -> int32 {
            let p = point<float64>::zero();
            return p.x as int32;
        }
        """
    ) == 0


def test_builtin_generic_qualifier_pins():
    # Builtin generic families take a written instantiation exactly like user
    # structs: `slice<int32>::first(s)`.
    assert run(
        """
        fn slice<T>::first(const self: slice<T>) -> T { return self[0]; }
        fn main() -> int32 {
            let s: slice<int32> = [7, 8, 9];
            return slice<int32>::first(s);
        }
        """
    ) == 7


def test_defaulted_tail_fills_from_the_struct_defaults():
    # The qualifier resolves as a type use, so a fully-defaulted tail may be
    # omitted and fills from the struct's declared defaults.
    assert run(
        """
        struct box<T, U = int32> { t: T; u: U; }
        fn box<T, U>::second(const self: box<T, U>) -> U { return self.u; }
        fn main() -> int32 {
            let b: box<float64> = { t = 1.0, u = 9 };
            return box<float64>::second(b);
        }
        """
    ) == 9


# --- constructor/destructor chaining (the driving use case) --------------------

def test_constructor_chains_with_enclosing_type_parameter(capfd):
    # Inside a converting constructor, `point<T>::constructor(self, ...)`
    # resolves T through the enclosing method's instantiation (the same
    # binding channel as `x as T`) and the diagonal direct constructor wins
    # over re-entering the converting one (subsumption: the diagonal is the
    # more specialized pattern).
    assert run(
        """
        import "std/io";
        struct point<T> { x: T; y: T; }
        fn point<T>::constructor(mut self: point<T>, x: T, y: T) {
            self.x = x;
            self.y = y;
        }
        fn point<T>::constructor<U>(mut self: point<T>, x: U, y: U) {
            point<T>::constructor(self, x as T, y as T);
        }
        fn main() -> int32 {
            let p = point<float64>(1, 2);
            println("{} {}", p.x, p.y);
            return 0;
        }
        """
    ) == 0
    assert capfd.readouterr().out == "1.000000 2.000000\n"


def test_destructor_chains_with_enclosing_type_parameter(capfd):
    # A member's destructor chains from the owner's with the enclosing T --
    # the qualified form is the destructor's only callable spelling, so the
    # pin is what makes the chain expressible inside a generic body.
    assert run(
        """
        import "std/io";
        struct inner<T> { v: T; }
        fn inner<T>::constructor(mut self: inner<T>, v: T) { self.v = v; }
        fn inner<T>::destructor(mut self: inner<T>) {
            println("inner down {}", self.v);
        }
        struct outer<T> { i: inner<T>; }
        fn outer<T>::constructor(mut self: outer<T>, v: T) {
            inner<T>::constructor(self.i, v);
        }
        fn outer<T>::destructor(mut self: outer<T>) {
            println("outer down");
            inner<T>::destructor(self.i);
        }
        fn main() -> int32 {
            let o = outer<int32>(9);
            return 0;
        }
        """
    ) == 0
    assert capfd.readouterr().out == "outer down\ninner down 9\n"


# --- specialization tiers under a pin ------------------------------------------

def test_partial_specialization_family_ranks_under_a_pin(capfd):
    # A pinned call still ranks the family: a pin agreeing with the partial's
    # concrete position reaches the partial, a disagreeing one falls to the
    # fully generic member.
    assert run(
        """
        import "std/io";
        struct pair<A, B> { a: A; b: B; }
        fn pair<A, B>::pick(const self: pair<A, B>) -> int32 { return 1; }
        fn pair<int32, U>::pick(const self: pair<int32, U>) -> int32 { return 2; }
        fn main() -> int32 {
            let p: pair<int32, float64> = { a = 1, b = 2.0 };
            let q: pair<int8, float64> = { a = 1, b = 2.0 };
            println("{} {}", pair<int32, float64>::pick(p), pair<int8, float64>::pick(q));
            return 0;
        }
        """
    ) == 0
    assert capfd.readouterr().out == "2 1\n"


def test_diagonal_beats_converting_under_a_pin():
    # Ranking and subsumption are unperturbed by the pin: the diagonal
    # `(self, T, T)` member is a pattern instance of the open `(self, U, U)`
    # one, so a pinned call with agreeing arguments stays unambiguous.
    assert run(
        """
        struct point<T> { x: T; y: T; }
        fn point<T>::mk(mut self: point<T>, x: T, y: T) -> int32 { return 1; }
        fn point<T>::mk<U>(mut self: point<T>, x: U, y: U) -> int32 { return 2; }
        fn main() -> int32 {
            let p: point<float64>;
            return point<float64>::mk(p, 1.0, 2.0);
        }
        """
    ) == 1


def test_untyped_literals_adapt_to_the_pinned_parameter():
    # A pinned parameter behaves like one fixed by explicit type arguments:
    # an untyped integer literal adapts to the pinned int8 instead of leaning
    # int32 (mcc has no int-to-float literal adaptation, so an integer pin is
    # the observable case).
    assert run(
        """
        struct point<T> { x: T; y: T; }
        fn point<T>::fill(mut self: point<T>, x: T, y: T) { self.x = x; self.y = y; }
        fn main() -> int32 {
            let p: point<int8>;
            point<int8>::fill(p, 1, 2);
            return (p.x + p.y) as int32;
        }
        """
    ) == 3


# --- diagnostics ---------------------------------------------------------------

def test_receiver_mismatching_the_pin_is_a_coercion_error():
    # The pin fixes the parameter type; a receiver of another instantiation
    # gets the ordinary coercion diagnostic naming both types.
    with pytest.raises(
        LangError,
        match=r"line 6: argument 1 of 'point::get': expected point<int32>, "
        r"got point<float64>",
    ):
        compile_ir(
            """
            struct point<T> { x: T; }
            fn point<T>::get(const self: point<T>) -> T { return self.x; }
            fn main() -> int32 {
                let p: point<float64> = { x = 1.0 };
                return point<int32>::get(p) as int32;
            }
            """
        )


def test_overload_set_miss_names_the_pin():
    # When no member of a SET survives the pin and the arguments, the
    # no-overload error names the pinned instantiation.
    with pytest.raises(
        LangError,
        match=r"no overload of 'box::get' with signature "
        r"box::get\(box<int32>, int32\); the qualifier pins box<float64>",
    ):
        compile_ir(
            """
            struct box<T> { v: T; }
            fn box<T>::get(const self: box<T>) -> T { return self.v; }
            fn box<T>::get(const self: box<T>, d: T) -> T { return self.v; }
            fn main() -> int32 {
                let b: box<int32> = { v = 5 };
                return box<float64>::get(b, 1);
            }
            """
        )


def test_qualifier_arity_error_is_the_type_use_error():
    # The written reference resolves as a type use, so a wrong-arity pin gets
    # the ordinary instantiation arity error.
    with pytest.raises(
        LangError, match=r"line 6: struct 'point' expects 1 type argument\(s\), got 2"
    ):
        compile_ir(
            """
            struct point<T> { x: T; }
            fn point<T>::get(const self: point<T>) -> T { return self.x; }
            fn main() -> int32 {
                let p: point<int32> = { x = 1 };
                return point<int32, int8>::get(p);
            }
            """
        )


def test_second_type_argument_list_is_a_parse_error():
    # The one written list belongs to the struct; a method's own type
    # parameters stay inference-only, so a list after the member name is
    # rejected at parse time (mirroring the dot-call rule).
    with pytest.raises(
        LangError,
        match=r"line 6: type arguments after 'conv' are not supported; the "
        r"qualifier's list names the struct instantiation and a method's "
        r"own type parameters are inferred",
    ):
        compile_ir(
            """
            struct point<T> { x: T; }
            fn point<T>::conv<U>(const self: point<T>, v: U) -> T { return v as T; }
            fn main() -> int32 {
                let p: point<int32> = { x = 1 };
                return point<int32>::conv<int8>(p, 5);
            }
            """
        )


def test_annotated_qualifier_without_a_call_is_a_parse_error():
    # `Type<args>::member` not followed by `(` can never mean anything (enum
    # members fold to constants and enums are not generic), so the parser
    # demands the call.
    with pytest.raises(
        LangError,
        match=r"line 4: expected '\(' after 'point<\.\.\.>::what': a "
        r"qualifier with type arguments forms a method call",
    ):
        compile_ir(
            """
            struct point<T> { x: T; }
            fn main() -> int32 {
                let e = point<int32>::what;
                return 0;
            }
            """
        )


# --- aliases -------------------------------------------------------------------

def test_written_args_generic_alias_qualifier_substitutes():
    # A generic alias with written arguments resolves through the alias's
    # target before pinning: `pd<float64>` is `pair<float64, int32>`.
    assert run(
        """
        struct pair<A, B> { a: A; b: B; }
        fn pair<A, B>::second(const self: pair<A, B>) -> B { return self.b; }
        type pd<X> = pair<X, int32>;
        fn main() -> int32 {
            let p: pair<float64, int32> = { a = 1.5, b = 40 };
            return pd<float64>::second(p);
        }
        """
    ) == 40


def test_permuting_alias_qualifier_honors_the_permutation():
    # `swap<int32, float64>` is `pair<float64, int32>` -- full type resolution
    # composes the permutation (name-only chasing would pin the wrong
    # positions). The receiver's A is float64, proving the swap applied.
    assert run(
        """
        struct pair<A, B> { a: A; b: B; }
        fn pair<A, B>::first(const self: pair<A, B>) -> A { return self.a; }
        type swap<X, Y> = pair<Y, X>;
        fn main() -> int32 {
            let p: pair<float64, int32> = { a = 1.5, b = 40 };
            return swap<int32, float64>::first(p) as int32;
        }
        """
    ) == 1


def test_bare_complete_alias_injects_its_instantiation():
    # `pointf::get(p)` over `type pointf = point<float64>` pins
    # point<float64>: the alias is a complete type, so the call means exactly
    # what `point<float64>::get(p)` does.
    assert run(
        """
        struct point<T> { x: T; }
        fn point<T>::get(const self: point<T>) -> T { return self.x; }
        type pointf = point<float64>;
        fn main() -> int32 {
            let p: point<float64> = { x = 41.5 };
            return (pointf::get(p) + 0.5) as int32;
        }
        """
    ) == 42


def test_bare_complete_alias_rejects_a_cross_instantiation_receiver():
    # THE FLIP: this program compiled before this feature -- the alias
    # qualifier chased by NAME only, so a point<int32> receiver silently
    # re-dispatched under the bare family (a soundness gap). The alias now
    # injects the instantiation it names, and the mismatch is an error.
    with pytest.raises(
        LangError,
        match=r"line 7: argument 1 of 'point::get': expected point<float64>, "
        r"got point<int32>",
    ):
        compile_ir(
            """
            struct point<T> { x: T; }
            fn point<T>::get(const self: point<T>) -> T { return self.x; }
            type pointf = point<float64>;
            fn main() -> int32 {
                let q: point<int32> = { x = 7 };
                return pointf::get(q);
            }
            """
        )


def test_bare_generic_alias_still_chases_by_name():
    # An alias that is NOT a complete type (`type pf = point` with point
    # generic) keeps the name-only chase: the call canonicalizes to the
    # family and infers from the arguments, exactly as before.
    assert run(
        """
        struct point<T> { x: T; }
        fn point<T>::get(const self: point<T>) -> T { return self.x; }
        type pf = point;
        fn main() -> int32 {
            let p: point<int32> = { x = 6 };
            return pf::get(p);
        }
        """
    ) == 6


def test_fully_defaulted_alias_is_complete_and_injects():
    # A fully-defaulted generic alias used bare is a complete type (the tail
    # fills from the defaults, as in any type use), so it injects too: a
    # receiver at another instantiation is the mismatch error.
    with pytest.raises(
        LangError,
        match=r"argument 1 of 'point::get': expected point<float64>, "
        r"got point<int32>",
    ):
        compile_ir(
            """
            struct point<T> { x: T; }
            fn point<T>::get(const self: point<T>) -> T { return self.x; }
            type pf<T = float64> = point<T>;
            fn main() -> int32 {
                let q: point<int32> = { x = 7 };
                return pf::get(q);
            }
            """
        )


def test_alias_of_an_unnameable_type_stays_undefined():
    # A bare alias of a pointer type has no bare-name family and nothing to
    # inject; the call keeps resolving (and reporting) under its written
    # name -- the pre-feature behavior.
    with pytest.raises(LangError, match=r"undefined function 'ip::m'"):
        compile_ir(
            """
            type ip = int32*;
            fn main() -> int32 { return ip::m(1); }
            """
        )


# --- inheritance ---------------------------------------------------------------

def test_inherited_member_matches_the_derived_pin():
    # An inherited member's frame is the DERIVED struct's parameter list, so
    # a written derived instantiation reaches it: `der<float64>::get(d)` runs
    # the base's member with T pinned through the derivation.
    assert run(
        """
        struct base<T> { v: T; }
        fn base<T>::get(const self: base<T>) -> T { return self.v; }
        struct der<T> extends base<T> { extra: int32; }
        fn main() -> int32 {
            let d: der<float64> = { v = 6.0, extra = 1 };
            return der<float64>::get(d) as int32;
        }
        """
    ) == 6


def test_base_pin_upcasts_a_derived_receiver():
    # Pinning the BASE family still upcasts the derived receiver: the pin
    # fixes base<float64> and the der<float64> receiver lends its base
    # prefix, exactly as the bare call does.
    assert run(
        """
        struct base<T> { v: T; }
        fn base<T>::get(const self: base<T>) -> T { return self.v; }
        struct der<T> extends base<T> { extra: int32; }
        fn main() -> int32 {
            let d: der<float64> = { v = 6.0, extra = 1 };
            return base<float64>::get(d) as int32;
        }
        """
    ) == 6


# --- file-scoped members -------------------------------------------------------

def test_static_specialization_takes_a_matching_pin():
    # A file-scoped concrete member (a @static specialization) verifies the
    # pin on the direct path -- there is no overload set to filter it.
    assert run(
        """
        struct point<T> { x: T; }
        @static
        fn point<int32>::get(const self: point<int32>) -> int32 { return self.x; }
        fn main() -> int32 {
            let p: point<int32> = { x = 3 };
            return point<int32>::get(p);
        }
        """
    ) == 3


def test_static_specialization_rejects_a_mismatched_pin():
    with pytest.raises(
        LangError,
        match=r"'point::get' has no member for point<float64>: the "
        r"qualifier's type arguments pin the receiver instantiation",
    ):
        compile_ir(
            """
            struct point<T> { x: T; }
            @static
            fn point<int32>::get(const self: point<int32>) -> int32 { return self.x; }
            fn main() -> int32 {
                let p: point<int32> = { x = 3 };
                return point<float64>::get(p);
            }
            """
        )


def test_static_generic_member_takes_the_pin():
    # A file-scoped generic member seeds its struct parameter from the pin
    # like any template.
    assert run(
        """
        struct point<T> { x: T; }
        @static
        fn point<T>::get(const self: point<T>) -> T { return self.x; }
        fn main() -> int32 {
            let p: point<int32> = { x = 8 };
            return point<int32>::get(p);
        }
        """
    ) == 8


# --- unchanged neighbors -------------------------------------------------------

def test_bare_qualified_call_still_infers():
    # `point::sum(p)` stays pure namespace + inference; the feature adds a
    # spelling, it does not change the bare one.
    assert run(
        """
        struct point<T> { x: T; y: T; }
        fn point<T>::sum(const self: point<T>) -> T { return self.x + self.y; }
        fn main() -> int32 {
            let p: point<int32> = { x = 3, y = 4 };
            return point::sum(p);
        }
        """
    ) == 7


def test_ctor_sugar_still_parses_before_the_qualified_form():
    # `point<float64>(1, 1)` commits on the `(` -- constructor sugar is
    # untouched by the `::` commit path (and RAII still schedules the
    # destructor for the sugar let).
    assert run(
        """
        struct point<T> { x: T; y: T; }
        fn point<T>::constructor(mut self: point<T>, x: T, y: T) {
            self.x = x;
            self.y = y;
        }
        fn main() -> int32 {
            let p = point<float64>(1.0, 1.0);
            return (p.x + p.y) as int32;
        }
        """
    ) == 2


def test_backtracked_type_args_restore_split_angles():
    # `a < b<c<d>> - 1` speculatively parses `b<c<d>>` as type arguments,
    # splitting the `>>` token in place; the speculation fails (no call
    # follows) and must UNDO the split, or the re-parse reads two lone `>`
    # tokens and dies on the second. The comparison/shift reading must
    # survive: `a < ((b < c) < (d >> 1))`.
    program = parse(
        """
        fn f(a: int32, b: int32, c: int32, d: int32) -> bool {
            return a < b<c<d>> - 1;
        }
        """
    )
    assert program.functions[0].name == "f"


def test_fstring_hole_takes_the_qualified_spelling(capfd):
    # The new spelling parses inside an f-string hole (the same expression
    # parser runs there).
    assert run(
        """
        import "std/io";
        struct point<T> { x: T; }
        fn point<T>::get(const self: point<T>) -> T { return self.x; }
        fn main() -> int32 {
            let p: point<int32> = { x = 5 };
            println(f"got {point<int32>::get(p)}");
            return 0;
        }
        """
    ) == 0
    assert capfd.readouterr().out == "got 5\n"


# --- interface stubs -----------------------------------------------------------

def test_generic_body_with_the_spelling_round_trips_through_mci(tmp_path):
    # A generic method travels verbatim in the stub, so a body chaining with
    # the new spelling must re-parse from the .mci -- and its Call type
    # arguments feed the dependency scan (the struct travels).
    lib = tmp_path / "lib.mc"
    lib.write_text(
        "struct point<T> { x: T; y: T; }\n"
        "fn point<T>::constructor(mut self: point<T>, x: T, y: T) {\n"
        "    self.x = x; self.y = y;\n"
        "}\n"
        "fn point<T>::constructor<U>(mut self: point<T>, x: U, y: U) {\n"
        "    point<T>::constructor(self, x as T, y as T);\n"
        "}\n"
    )
    out = tmp_path / "lib.mci"
    assert emit_interface(lib, (tmp_path,), None, {}, out) == 0
    stub = out.read_text()
    assert "point<T>::constructor(self, x as T, y as T);" in stub
    lib.unlink()  # force the import to resolve through the stub
    main = tmp_path / "main.mc"
    main.write_text(
        'import "lib";\n'
        "fn main() -> int32 {\n"
        "    let p = point<float64>(1, 2);\n"
        "    return (p.x + p.y) as int32;\n"
        "}\n"
    )
    assert run_path(main) == 3
