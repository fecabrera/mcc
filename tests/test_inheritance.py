"""Method inheritance through ``extends``: derived types expose base families.

A derived struct's method-family call resolves over the MERGED set of its own
members and its base chain's, the latter rebased at the declared base
instantiation -- ``pointf extends point<float64>`` inherits the diagonal
``fn point<T>::constructor`` as a CONCRETE ``(float64, float64)`` member
(tier 2), so ``pointf(1.0, 1.0)`` prefers it over a derived generic while
``pointf(1, 1)`` still picks the converting ``<U>`` overload. Rank order is
``(no-collect, tier, -hop, specificity, fixed)``: the tier beats the hop (an
inherited exact match beats a derived generic), the hop beats specificity (a
derived same-shape member shadows an inherited one). Emission always
instantiates the ORIGIN template -- one shared instance and symbol -- and the
receiver coerces at the boundary: a ``&``/``const &`` receiver lends its base
prefix in place. A by-value copy receiver is rejected by construction (it
would slice the derived value), so every receiver is reference-shaped and the
prefix is always lent, never copied. The derived->base reference upcast (a
view, never a copy) applies to the receiver of a method-family call (explicit
qualified calls included, so ``point::constructor(self, ...)`` chains from a
derived constructor) and -- since SIE-101 Stage 2 -- to any **fat reference
parameter** at any position (a ``&<extended base>`` argument forms a base
view). A by-value argument still keeps the explicit ``as`` (that copy slices,
so it is made explicit).
"""

import pytest

from mcc.driver import emit_interface
from mcc.errors import LangError
from helpers import compile_ir, run, run_path


POINT = """
    struct point<T> { x: T; y: T; }
    struct pointf extends point<float64> {}
    fn point<T>::constructor(self: &point<T>, x: T, y: T) {
        self.x = x; self.y = y;
    }
    fn pointf::constructor<U>(self: &pointf, x: U, y: U) {
        self.x = x as float64; self.y = y as float64;
    }
    fn point<T>::magnitude(const self: &struct point<T>) -> float64 {
        return sqrt(pow(self.x as float64, 2.0) + pow(self.y as float64, 2.0));
    }
"""


# --- the driving use case ------------------------------------------------------


def test_acceptance_int_literals_pick_the_derived_converting_ctor(capfd):
    # `pointf(1, 1)`: the inherited diagonal is concrete (float64, float64),
    # and an int literal never adapts to a float slot, so it is non-viable;
    # the derived generic <U> converts. The dot-call consumes the inherited
    # magnitude with T = float64.
    assert run(
        'import "std/io";\nimport "libc/math";\n' + POINT + """
        fn main() -> int32 {
            let p = pointf(1, 1);
            println(f"{p.x = }, {p.y = }");
            println(f"{p.magnitude() = }");
            return 0;
        }
        """
    ) == 0
    out = capfd.readouterr().out
    assert out == "p.x = 1.000000, p.y = 1.000000\np.magnitude() = 1.414214\n"


def test_acceptance_float_literals_pick_the_inherited_diagonal(capfd):
    # `pointf(1.0, 1.0)`: both the inherited diagonal (concrete, tier 2) and
    # the derived <U> (generic, tier 0) are viable; exactness beats
    # genericity wherever declared, so the INHERITED member wins. The bodies
    # differ observably (the derived one offsets by 100).
    assert run(
        'import "std/io";\nimport "libc/math";\n'
        + POINT.replace("self.x = x as float64;", "self.x = x as float64 + 100.0;")
        + """
        fn main() -> int32 {
            let p = pointf(1.0, 1.0);
            println(f"{p.x = }");
            let q = pointf(1, 1);
            println(f"{q.x = }");
            return 0;
        }
        """
    ) == 0
    assert capfd.readouterr().out == "p.x = 1.000000\nq.x = 101.000000\n"


# --- plain (non-generic) inheritance -------------------------------------------


def test_concrete_base_method_callable_on_derived(capfd):
    # Dot sugar and the explicit qualified spelling both reach the base
    # family; the derived spelling `d::describe` resolves the inherited
    # member too (dot sugar rewrites to the derived spelling).
    assert run(
        """
        import "std/io";
        struct b { tag: int32; }
        struct d extends b { extra: int32; }
        fn b::describe(const self: &b) -> int32 { return self.tag * 10; }
        fn main() -> int32 {
            let v: d = { tag = 3, extra = 9 };
            println(f"{v.describe()} {d::describe(v)} {b::describe(v)}");
            return 0;
        }
        """
    ) == 0
    assert capfd.readouterr().out == "30 30 30\n"


def test_mut_self_write_through_lands_in_the_base_prefix(capfd):
    # A mut-self inherited method writes through a bitcast of the DERIVED
    # value's address: the write lands in its base prefix, and the derived
    # extra field is untouched.
    assert run(
        """
        import "std/io";
        struct b { n: int32; }
        struct d extends b { extra: int32; }
        fn b::bump(self: &b) { self.n = self.n + 1; }
        fn main() -> int32 {
            let v: d = { n = 5, extra = 7 };
            v.bump();
            b::bump(v);
            println(f"{v.n} {v.extra}");
            return 0;
        }
        """
    ) == 0
    assert capfd.readouterr().out == "7 7\n"


def test_by_value_copy_receiver_is_rejected():
    # A by-value (plain `self`) receiver would slice a derived value into the
    # base prefix -- the footgun the receiver-kind ruling forbids by
    # construction. It is a hard error, not a silent copy: use `const self: &b`
    # to read or `self: &b` to mutate.
    with pytest.raises(
        LangError,
        match=r"a by-value copy receiver 'self' is not allowed",
    ):
        compile_ir(
            """
            struct b { n: int32; }
            struct d extends b { extra: int32; }
            fn b::probe(self: b) -> int32 { return self.n; }
            fn main() -> int32 { return 0; }
            """
        )


def test_pointer_receiver_auto_derefs_into_the_inherited_method(capfd):
    # `q.m()` on a d* receiver auto-derefs one hop, then the inherited
    # family resolves on the pointee -- including a mut-self write-through.
    assert run(
        """
        import "std/io";
        struct b { n: int32; }
        struct d extends b { extra: int32; }
        fn b::bump(self: &b) { self.n = self.n + 1; }
        fn main() -> int32 {
            let v: d = { n = 1, extra = 0 };
            let q: d* = &v;
            q.bump();
            println(f"{v.n}");
            return 0;
        }
        """
    ) == 0
    assert capfd.readouterr().out == "2\n"


def test_inherited_constructor_defaults_derived_added_fields(capfd):
    # An inherited constructor never sees the derived-added fields; they
    # keep `let s: S;` semantics -- declared field defaults apply.
    assert run(
        """
        import "std/io";
        struct b { n: int32; }
        struct d extends b { extra: int32 = 42; }
        fn b::constructor(self: &b, n: int32) { self.n = n; }
        fn main() -> int32 {
            let v = d(7);
            println(f"{v.n} {v.extra}");
            return 0;
        }
        """
    ) == 0
    assert capfd.readouterr().out == "7 42\n"


# --- ranking: hop and tier ------------------------------------------------------


def test_derived_same_shape_override_shadows_the_inherited_member(capfd):
    # Both members are concrete with the same receiver shape; the rank ties
    # on (no-collect, tier) and the HOP breaks it toward the derived one.
    assert run(
        """
        import "std/io";
        struct b { n: int32; }
        struct d extends b {}
        fn b::describe(const self: &b) -> int32 { return 1; }
        @override fn d::describe(const self: &d) -> int32 { return 2; }
        fn main() -> int32 {
            let v: d = { n = 0 };
            let w: b = { n = 0 };
            println(f"{v.describe()} {w.describe()}");
            return 0;
        }
        """
    ) == 0
    assert capfd.readouterr().out == "2 1\n"


def test_derived_generic_does_not_shadow_an_inherited_concrete_exact_match(capfd):
    # The tier outranks the hop: an inherited member that matches exactly
    # (concrete, tier 2) beats a derived generic (tier 0) -- exactness beats
    # genericity wherever declared.
    assert run(
        """
        import "std/io";
        struct b { n: int32; }
        struct d extends b {}
        fn b::take(const self: &b, x: int32) -> int32 { return 1; }
        fn d::take<U>(const self: &d, x: U) -> int32 { return 2; }
        fn main() -> int32 {
            let v: d = { n = 0 };
            let x: int32 = 5;
            let y: int64 = 5;
            println(f"{v.take(x)} {v.take(y)}");
            return 0;
        }
        """
    ) == 0
    # int32 hits the inherited exact match; int64 only fits the derived <U>.
    assert capfd.readouterr().out == "1 2\n"


def test_nearer_hop_shadows_the_farther_one_transitively(capfd):
    # a extends b extends c: the same-shape family exists on both bases; the
    # b member (hop 1) shadows the c member (hop 2), and c's OTHER family
    # still reaches a through two hops.
    assert run(
        """
        import "std/io";
        struct c { n: int32; }
        struct b extends c { m: int32; }
        struct a extends b { k: int32; }
        fn c::which(const self: &c) -> int32 { return 3; }
        @override fn b::which(const self: &b) -> int32 { return 2; }
        fn c::deep(const self: &c) -> int32 { return self.n + 30; }
        fn main() -> int32 {
            let v: a = { n = 1, m = 2, k = 3 };
            println(f"{v.which()} {v.deep()}");
            return 0;
        }
        """
    ) == 0
    assert capfd.readouterr().out == "2 31\n"


# --- the @override marker on method overrides (SIE-101 stage 1) -----------------


def test_unmarked_derived_shadow_requires_the_override_marker():
    # A derived member whose signature pattern matches an inherited base
    # member shadows it, so it must be marked @override; leaving it bare is
    # the accidental-shadow error.
    with pytest.raises(
        LangError,
        match=(
            r"method 'd::describe' shadows the inherited base member of the "
            r"same signature and must be marked @override"
        ),
    ):
        compile_ir(
            """
            struct b { n: int32; }
            struct d extends b {}
            fn b::describe(const self: &b) -> int32 { return 1; }
            fn d::describe(const self: &d) -> int32 { return 2; }
            fn main() -> int32 { return 0; }
            """
        )


def test_unmarked_transitive_shadow_requires_the_override_marker():
    # The shadow need not be of the immediate base: b::which shadows c::which
    # two structs up the chain, and the marker is still required.
    with pytest.raises(
        LangError,
        match=r"method 'b::which' shadows the inherited base member",
    ):
        compile_ir(
            """
            struct c { n: int32; }
            struct b extends c { m: int32; }
            fn c::which(const self: &c) -> int32 { return 3; }
            fn b::which(const self: &b) -> int32 { return 2; }
            fn main() -> int32 { return 0; }
            """
        )


def test_override_on_a_method_that_shadows_nothing_is_an_error():
    # A derived member with @override whose pattern matches no inherited base
    # member overrides nothing -- a different shape merely OVERLOADS, so the
    # marker is wrong.
    with pytest.raises(
        LangError,
        match=(
            r"@override method 'd::take' overrides no inherited base member"
        ),
    ):
        compile_ir(
            """
            struct b { n: int32; }
            struct d extends b {}
            fn b::take(const self: &b, x: int32) -> int32 { return 1; }
            @override fn d::take(const self: &d, x: char*) -> int32 {
                return 2;
            }
            fn main() -> int32 { return 0; }
            """
        )


def test_override_with_no_base_family_at_all_is_an_error():
    # The base chain exposes no `note` family, so the @override targets
    # nothing -- the overrides-nothing error, not a silent accept.
    with pytest.raises(
        LangError,
        match=r"@override method 'd::note' overrides no inherited base member",
    ):
        compile_ir(
            """
            struct b { n: int32; }
            struct d extends b {}
            @override fn d::note(const self: &d) -> int32 { return 2; }
            fn main() -> int32 { return 0; }
            """
        )


def test_marked_method_override_compiles_and_dispatches(capfd):
    # The positive case: a properly marked override shadows the inherited
    # member (the hop breaks the rank tie), and the base body is still
    # reachable through the explicit qualified call.
    assert run(
        """
        import "std/io";
        struct b { n: int32; }
        struct d extends b {}
        fn b::describe(const self: &b) -> int32 { return 1; }
        @override fn d::describe(const self: &d) -> int32 { return 2; }
        fn main() -> int32 {
            let v: d = { n = 0 };
            println(f"{v.describe()} {b::describe(v)}");
            return 0;
        }
        """
    ) == 0
    assert capfd.readouterr().out == "2 1\n"


def test_override_marker_not_required_on_a_differently_shaped_overload(capfd):
    # A derived member with a DIFFERENT signature merely overloads the merged
    # family (no name hiding), so it needs no marker and coexists with the
    # inherited member.
    assert run(
        """
        import "std/io";
        struct b { n: int32; }
        struct d extends b {}
        fn b::describe(const self: &b) -> int32 { return 1; }
        fn d::describe(const self: &d, x: int32) -> int32 { return x; }
        fn main() -> int32 {
            let v: d = { n = 0 };
            println(f"{v.describe()} {v.describe(7)}");
            return 0;
        }
        """
    ) == 0
    # The inherited no-arg member and the derived one-arg overload both reach v.
    assert capfd.readouterr().out == "1 7\n"


def test_derived_destructor_needs_no_override_marker(capfd):
    # Destructors are exempt from the marker: a derived T::destructor shadows
    # the base's same-shape destructor by nature, so leaving it unmarked is
    # NOT the accidental-shadow error (base cleanup chains manually).
    assert run(
        """
        import "std/io";
        struct base { n: int32; }
        struct derived extends base {}
        fn base::constructor(self: &base, n: int32) { self.n = n; }
        fn derived::constructor(self: &derived, n: int32) {
            base::constructor(self, n);
        }
        fn base::destructor(self: &base) { println("~base"); }
        fn derived::destructor(self: &derived) {
            println("~derived");
            base::destructor(self);
        }
        fn main() -> int32 {
            let d = derived(0);
            return 0;
        }
        """
    ) == 0
    assert capfd.readouterr().out == "~derived\n~base\n"


def test_same_signature_derived_constructor_needs_no_override_marker(capfd):
    # Constructors are exempt too: a derived constructor with the SAME
    # signature as the base's shadows it, but construction is never dispatched
    # (you always build a concrete type), so no marker is required.
    assert run(
        """
        import "std/io";
        struct base { n: int32; }
        struct derived extends base {}
        fn base::constructor(self: &base, n: int32) { self.n = n; }
        fn derived::constructor(self: &derived, n: int32) {
            base::constructor(self, n);
        }
        fn main() -> int32 {
            let d = derived(5);
            println(f"{d.n}");
            return 0;
        }
        """
    ) == 0
    assert capfd.readouterr().out == "5\n"


def test_redundant_override_on_a_destructor_is_tolerated(capfd):
    # The marker is neither required nor rejected on a destructor: writing it
    # is inert, not the overrides-nothing error.
    assert run(
        """
        import "std/io";
        struct base { n: int32; }
        struct derived extends base {}
        fn base::constructor(self: &base, n: int32) { self.n = n; }
        fn derived::constructor(self: &derived, n: int32) {
            base::constructor(self, n);
        }
        fn base::destructor(self: &base) { println("~base"); }
        @override fn derived::destructor(self: &derived) {
            println("~derived");
            base::destructor(self);
        }
        fn main() -> int32 {
            let d = derived(0);
            return 0;
        }
        """
    ) == 0
    assert capfd.readouterr().out == "~derived\n~base\n"


# --- constructor chaining -------------------------------------------------------


def test_constructor_chains_through_the_explicit_base_qualified_call(capfd):
    # Inside a derived constructor, `point::constructor(self, ...)` upcasts
    # the mut receiver to the base prefix -- constructor chaining.
    assert run(
        """
        import "std/io";
        struct point<T> { x: T; y: T; }
        struct pointf extends point<float64> { label: int32; }
        fn point<T>::constructor(self: &point<T>, x: T, y: T) {
            self.x = x; self.y = y;
        }
        fn pointf::constructor(self: &pointf, x: int32, y: int32) {
            point::constructor(self, x as float64, y as float64);
            self.label = 7;
        }
        fn main() -> int32 {
            let p = pointf(2, 3);
            println(f"{p.x = }, {p.y = }, {p.label = }");
            return 0;
        }
        """
    ) == 0
    assert capfd.readouterr().out == "p.x = 2.000000, p.y = 3.000000, p.label = 7\n"


# --- generic derivation ---------------------------------------------------------


def test_generic_derived_over_generic_base_infers_through_the_receiver(capfd):
    # `pd<T> extends point<T>`: the inherited members stay generic; the
    # receiver binds the derived parameter and the origin instantiates
    # through the seed (T -> T).
    assert run(
        """
        import "std/io";
        struct point<T> { x: T; y: T; }
        struct pd<T> extends point<T> { tag: int32; }
        fn point<T>::sum(const self: &point<T>) -> T { return self.x + self.y; }
        fn main() -> int32 {
            let p: pd<int32> = { x = 20, y = 22, tag = 1 };
            let q: pd<int64> = { x = 100, y = 1, tag = 2 };
            println(f"{p.sum()} {q.sum()}");
            return 0;
        }
        """
    ) == 0
    assert capfd.readouterr().out == "42 101\n"


def test_generic_derived_bare_ctor_head_infers_the_instantiation(capfd):
    # `pd(1, 2)` with only the inherited constructor: the bare head defers
    # the receiver to resolution (the _CtorSelf path), the arguments bind
    # the derived parameter, and the slot materializes as pd<int32> -- not
    # as the origin's point<int32>.
    assert run(
        """
        import "std/io";
        struct point<T> { x: T; y: T; }
        struct pd<T> extends point<T> { tag: int32 = 9; }
        fn point<T>::constructor(self: &point<T>, x: T, y: T) {
            self.x = x; self.y = y;
        }
        fn main() -> int32 {
            let p = pd(40, 2);
            let q = pd<int64>(1, 2);
            println(f"{p.x + p.y} {p.tag} {q.x + q.y}");
            return 0;
        }
        """
    ) == 0
    assert capfd.readouterr().out == "42 9 3\n"


def test_method_own_type_param_survives_and_renames_off_a_collision(capfd):
    # `fn point<T>::map<U>` keeps its own U on the clone; deriving with a
    # struct parameter named U forces the rename -- the collision must not
    # cross-bind the two axes.
    assert run(
        """
        import "std/io";
        struct point<T> { x: T; y: T; }
        struct pd<U> extends point<U> {}
        fn point<T>::first_as<U>(const self: &point<T>, w: U) -> U {
            return (self.x as U) + w;
        }
        fn main() -> int32 {
            let p: pd<int32> = { x = 40, y = 0 };
            let wide: int64 = 2;
            println(f"{p.first_as(wide)}");
            return 0;
        }
        """
    ) == 0
    assert capfd.readouterr().out == "42\n"


# --- specialization filtering ---------------------------------------------------


def test_base_specializations_filter_by_the_declared_instantiation(capfd):
    # `fn point<int32>::only_i` applies to derived types whose declared base
    # instantiation IS point<int32>, and never to others.
    assert run(
        """
        import "std/io";
        struct point<T> { x: T; y: T; }
        struct pi extends point<int32> {}
        fn point<int32>::only_i(const self: &point<int32>) -> int32 {
            return self.x + 1;
        }
        fn main() -> int32 {
            let p: pi = { x = 41, y = 0 };
            println(f"{p.only_i()}");
            return 0;
        }
        """
    ) == 0
    assert capfd.readouterr().out == "42\n"


def test_base_specialization_for_another_instantiation_is_not_inherited():
    with pytest.raises(
        LangError, match=r"struct 'pointf' has no field or method 'only_i'"
    ):
        compile_ir(
            """
            struct point<T> { x: T; y: T; }
            struct pointf extends point<float64> {}
            fn point<int32>::only_i(const self: &point<int32>) -> int32 {
                return self.x;
            }
            fn main() -> int32 {
                let p: pointf = { x = 1.0, y = 2.0 };
                return p.only_i() as int32;
            }
            """
        )


def test_diagonal_qualifier_filters_on_a_disagreeing_base(capfd):
    # `fn pair2<A, A>::same` is inherited where the declared base repeats
    # one type and filtered where it does not.
    assert run(
        """
        import "std/io";
        struct pair2<K, V> { k: K; v: V; }
        struct twin extends pair2<int32, int32> {}
        fn pair2<A, A>::same(const self: &pair2<A, A>) -> A {
            return self.k + self.v;
        }
        fn main() -> int32 {
            let t: twin = { k = 40, v = 2 };
            println(f"{t.same()}");
            return 0;
        }
        """
    ) == 0
    assert capfd.readouterr().out == "42\n"


def test_diagonal_qualifier_is_not_inherited_where_the_base_disagrees():
    with pytest.raises(
        LangError, match=r"struct 'mixed' has no field or method 'same'"
    ):
        compile_ir(
            """
            struct pair2<K, V> { k: K; v: V; }
            struct mixed extends pair2<int32, float64> {}
            fn pair2<A, A>::same(const self: &pair2<A, A>) -> A { return self.k; }
            fn main() -> int32 {
                let t: mixed = { k = 1, v = 2.0 };
                return t.same();
            }
            """
        )


# --- constraints ride along (the id(func)-keyed tables) --------------------------


def test_bounded_inherited_method_keeps_its_bound_on_a_generic_derivation():
    # `fn holder<E extends b>::get` seeds E from the derived parameter; the
    # bound transfers onto the clone's parameter, so a violating receiver
    # still fails -- the constraint tables must follow the clone.
    source = """
        struct b {{ n: int32; }}
        struct d extends b {{ m: int32; }}
        struct holder<E> {{ item: E; }}
        struct hd<E> extends holder<E> {{}}
        fn holder<E extends b>::get(const self: &holder<E>) -> int32 {{
            return self.item.n;
        }}
        fn main() -> int32 {{
            let v: hd<{elem}> = {{ item = {init} }};
            return v.get();
        }}
    """
    assert run(source.format(elem="d", init="{ n = 0, m = 1 }")) == 0
    # A lone candidate skips the set-path viability filter, so the violation
    # surfaces as the instantiation backstop's precise bound error -- named
    # after the ORIGIN, where the bound is declared.
    with pytest.raises(
        LangError, match=r"int32 does not satisfy the bound b of 'holder::get'"
    ):
        compile_ir(source.format(elem="int32", init="7"))


def test_grouped_inherited_method_is_filtered_where_the_base_violates_it():
    # A closed group on a seeded parameter: the concrete derivation is
    # checked at the rebase, so a violating base instantiation simply does
    # not inherit the member.
    source = """
        struct box<T> {{ v: T; }}
        struct {name} extends box<{arg}> {{}}
        fn box<T: int32 | int64>::val(const self: &box<T>) -> int32 {{
            return self.v as int32;
        }}
        fn main() -> int32 {{
            let b: {name} = {{ v = {init} }};
            return b.val();
        }}
    """
    assert run(source.format(name="ibox", arg="int32", init="42")) == 42
    with pytest.raises(
        LangError, match=r"struct 'fbox' has no field or method 'val'"
    ):
        compile_ir(source.format(name="fbox", arg="float64", init="1.0"))


# --- explicit base-qualified calls (the upcast surface) --------------------------


def test_explicit_base_qualified_call_upcasts_a_derived_receiver(capfd):
    assert run(
        'import "std/io";\nimport "libc/math";\n' + POINT + """
        fn main() -> int32 {
            let p = pointf(3.0, 4.0);
            println(f"{point::magnitude(p) = }");
            return 0;
        }
        """
    ) == 0
    assert capfd.readouterr().out == "point::magnitude(p) = 5.000000\n"


def test_non_receiver_arguments_do_not_upcast():
    # The upcast surface is the receiver position only: a derived value in
    # any OTHER slot still needs the explicit `as`.
    with pytest.raises(
        LangError, match=r"argument 2 of 'b::plus': expected b, got d"
    ):
        compile_ir(
            """
            struct b { n: int32; }
            struct d extends b {}
            fn b::plus(const self: &b, other: b) -> int32 {
                return self.n + other.n;
            }
            fn main() -> int32 {
                let v: d = { n = 1 };
                let w: d = { n = 2 };
                return b::plus(v, w);
            }
            """
        )


def test_non_receiver_argument_upcasts_with_an_explicit_as(capfd):
    assert run(
        """
        import "std/io";
        struct b { n: int32; }
        struct d extends b {}
        fn b::plus(const self: &b, other: b) -> int32 {
            return self.n + other.n;
        }
        fn main() -> int32 {
            let v: d = { n = 40 };
            let w: d = { n = 2 };
            println(f"{b::plus(v, w as b)}");
            return 0;
        }
        """
    ) == 0
    assert capfd.readouterr().out == "42\n"


def test_fat_reference_param_upcasts_a_derived_argument(capfd):
    # SIE-101 Stage 2 broadens the derived->base reference conversion beyond
    # the receiver: a fat reference parameter (`&b`, b extended) accepts a
    # derived `d` argument at ANY position, forming a base VIEW that writes the
    # shared prefix in place. The reference upcast never slices, so -- unlike a
    # by-value argument, which still needs an explicit `as` -- it is implicit.
    # (Before Stage 2 this was `expected a b lvalue, got d`.)
    assert run(
        """
        import "std/io";
        struct b { n: int32; }
        struct d extends b {}
        fn poke(self: &b) { self.n = 1; }
        fn main() -> int32 {
            let v: d = { n = 0 };
            poke(v);                 // d upcasts to a &b view; writes v's prefix
            println(f"{v.n}");
            return 0;
        }
        """
    ) == 0
    assert capfd.readouterr().out == "1\n"


# --- mut returns ----------------------------------------------------------------


def test_inherited_mut_return_re_lends_the_base_prefix(capfd):
    # An inherited `-> mut` method is assignable and chains: the formation
    # walk vouches through the merged family.
    assert run(
        """
        import "std/io";
        struct b { n: int32; }
        struct d extends b { extra: int32; }
        fn b::ref_n(self: &b) -> &int32 { return self.n; }
        fn main() -> int32 {
            let v: d = { n = 1, extra = 0 };
            v.ref_n() = 41;
            d::ref_n(v) = d::ref_n(v) + 1;
            println(f"{v.n}");
            return 0;
        }
        """
    ) == 0
    assert capfd.readouterr().out == "42\n"


# --- builtin bases --------------------------------------------------------------


def test_builtin_base_family_is_inherited(capfd):
    # `named<T> extends slice<T>`: a user family on the builtin slice<T>
    # becomes callable on the derived view (the receiver borrows as the
    # slice prefix).
    assert run(
        """
        import "std/io";
        struct named<T> extends slice<T> { tag: int32; }
        fn slice<T>::head(const self: &slice<T>) -> T { return self[0]; }
        fn main() -> int32 {
            let backing: int32[3] = [7, 8, 9];
            let v: named<int32> = { data = &backing[0], length = 3, tag = 1 };
            println(f"{v.head()} {slice::head(v)}");
            return 0;
        }
        """
    ) == 0
    assert capfd.readouterr().out == "7 7\n"


# --- non-goals ------------------------------------------------------------------


def test_bare_type_parameter_base_inherits_nothing():
    # `struct entry<T> extends T` (the intrusive shape) resolves per
    # instantiation; there is no declared base family to inherit.
    with pytest.raises(
        LangError, match=r"struct 'entry' has no field or method 'describe'"
    ):
        compile_ir(
            """
            struct b { n: int32; }
            struct entry<T> extends T { next: int32; }
            fn b::describe(const self: &b) -> int32 { return self.n; }
            fn main() -> int32 {
                let e: entry<b> = { n = 1, next = 0 };
                return e.describe();
            }
            """
        )


def test_unknown_method_on_a_derived_struct_keeps_the_bespoke_error():
    with pytest.raises(
        LangError, match=r"struct 'd' has no field or method 'ghost'"
    ):
        compile_ir(
            """
            struct b { n: int32; }
            struct d extends b {}
            fn b::real(const self: &b) -> int32 { return 0; }
            fn main() -> int32 {
                let v: d = { n = 1 };
                return v.ghost();
            }
            """
        )


# --- diagnostics ----------------------------------------------------------------


def test_ambiguity_note_names_the_base_the_member_was_inherited_from():
    # Two rank-tied viable members, both inherited (a derived member could
    # never tie -- the hop shadows): each contender note points at its
    # ORIGIN declaration and names the base it was inherited from.
    try:
        compile_ir(
            """
            struct b { n: int32; }
            struct d extends b {}
            fn b::pick<T>(const self: &b, x: T, y: int32) -> int32 { return 1; }
            fn b::pick<T>(const self: &b, x: int32, y: T) -> int32 { return 2; }
            fn main() -> int32 {
                let v: d = { n = 0 };
                let a: int32 = 1;
                return v.pick(a, a);
            }
            """
        )
    except LangError as err:
        assert "ambiguous between overloads" in str(err)
        notes = [n.message for n in err.notes]
        assert notes.count("candidate is here (inherited from b)") == 2
    else:  # pragma: no cover - the call must be ambiguous
        pytest.fail("expected an ambiguity error")


def test_derived_call_shadows_the_would_be_base_ambiguity(capfd):
    # The same incomparable pair called on the BASE stays ambiguous, but a
    # derived same-shape member outranks both inherited ones via the hop.
    assert run(
        """
        import "std/io";
        struct b { n: int32; }
        struct d extends b {}
        fn b::pick<T>(const self: &b, x: T, y: int32) -> int32 { return 1; }
        fn b::pick<T>(const self: &b, x: int32, y: T) -> int32 { return 2; }
        @override fn d::pick<T>(const self: &d, x: T, y: int32) -> int32 { return 3; }
        fn main() -> int32 {
            let v: d = { n = 0 };
            let a: int32 = 1;
            println(f"{v.pick(a, a)}");
            return 0;
        }
        """
    ) == 0
    assert capfd.readouterr().out == "3\n"


def test_lone_inherited_member_keeps_the_direct_coercion_error():
    # A family that merges to a single candidate takes the direct path, so
    # a bad argument gets the precise coercion error under the DERIVED
    # spelling the call used.
    with pytest.raises(
        LangError,
        match=r"argument 2 of 'd::describe': expected int32, got float64",
    ):
        compile_ir(
            """
            struct b { n: int32; }
            struct d extends b {}
            fn b::describe(const self: &b, x: int32) -> int32 { return 1; }
            fn main() -> int32 {
                let v: d = { n = 0 };
                return v.describe(1.5);
            }
            """
        )


# --- cross-module (.mci) --------------------------------------------------------


def test_inherited_methods_resolve_through_an_interface_stub(capfd, tmp_path):
    # The base struct and its family live in an imported module, resolved
    # through its .mci stub; a LOCAL derived struct inherits the family.
    lib = tmp_path / "geo.mc"
    lib.write_text(
        "struct point<T> { x: T; y: T; }\n"
        "fn point<T>::constructor(self: &point<T>, x: T, y: T) {\n"
        "    self.x = x; self.y = y;\n"
        "}\n"
        "fn point<T>::sum(const self: &point<T>) -> T {\n"
        "    return self.x + self.y;\n"
        "}\n"
    )
    out = tmp_path / "geo.mci"
    assert emit_interface(lib, (tmp_path,), None, {}, out) == 0
    stub = out.read_text()
    assert "fn point<T>::sum" in stub
    main = tmp_path / "main.mc"
    main.write_text(
        'import "std/io";\n'
        'import "geo";\n'
        "struct pointi extends point<int32> { tag: int32 = 5; }\n"
        "fn main() -> int32 {\n"
        "    let p = pointi(40, 2);\n"
        '    println(f"{p.sum()} {p.tag}");\n'
        "    return 0;\n"
        "}\n"
    )
    assert run_path(main) == 0
    assert capfd.readouterr().out == "42 5\n"


def test_derived_struct_in_a_stub_inherits_a_local_base_family(capfd, tmp_path):
    # The reverse direction: the DERIVED struct travels through a stub; the
    # base family declared beside it still merges at the use site.
    lib = tmp_path / "shapes.mc"
    lib.write_text(
        "struct b { n: int32; }\n"
        "struct d extends b { m: int32; }\n"
        "fn b::total(const self: &b) -> int32 { return self.n + 1; }\n"
    )
    out = tmp_path / "shapes.mci"
    assert emit_interface(lib, (tmp_path,), None, {}, out) == 0
    assert "struct d extends b" in out.read_text()
    main = tmp_path / "main.mc"
    main.write_text(
        'import "std/io";\n'
        'import "shapes";\n'
        "fn main() -> int32 {\n"
        "    let v: d = { n = 41, m = 0 };\n"
        '    println(f"{v.total()}");\n'
        "    return 0;\n"
        "}\n"
    )
    assert run_path(main) == 0
    assert capfd.readouterr().out == "42\n"


# --- alias-spelled bases --------------------------------------------------------


def test_extends_through_a_plain_alias_inherits(capfd):
    # The `extends` target chases plain aliases at the declaration level, so
    # the alias spelling inherits exactly as the canonical one.
    assert run(
        """
        import "std/io";
        struct point<T> { x: T; y: T; }
        type pf = point<float64>;
        struct pointf extends pf {}
        fn point<T>::sum(const self: &point<T>) -> T { return self.x + self.y; }
        fn main() -> int32 {
            let p: pointf = { x = 1.5, y = 2.5 };
            println(f"{p.sum() as int32}");
            return 0;
        }
        """
    ) == 0
    assert capfd.readouterr().out == "4\n"


def test_extends_through_a_generic_alias_inherits(capfd):
    # A generic-alias application (`extends boxed<int32>` over
    # `type boxed<T> = point<T>`) expands before the rebase.
    assert run(
        """
        import "std/io";
        struct point<T> { x: T; y: T; }
        type boxed<T> = point<T>;
        struct pi extends boxed<int32> {}
        fn point<T>::sum(const self: &point<T>) -> T { return self.x + self.y; }
        fn main() -> int32 {
            let p: pi = { x = 40, y = 2 };
            println(f"{p.sum()}");
            return 0;
        }
        """
    ) == 0
    assert capfd.readouterr().out == "42\n"


# --- more inheritance shapes ----------------------------------------------------


def test_concrete_base_overload_set_is_inherited_whole(capfd):
    # A base family that is a concrete OVERLOAD set (two signatures) merges
    # both members onto the derived type.
    assert run(
        """
        import "std/io";
        struct b { n: int32; }
        struct d extends b {}
        fn b::get(const self: &b) -> int32 { return self.n; }
        fn b::get(const self: &b, bias: int32) -> int32 { return self.n + bias; }
        fn main() -> int32 {
            let v: d = { n = 40 };
            println(f"{v.get()} {v.get(2)}");
            return 0;
        }
        """
    ) == 0
    assert capfd.readouterr().out == "40 42\n"


def test_grouped_seed_transfers_onto_a_generic_derivation():
    # `pd<T> extends box<T>` seeds the grouped parameter with the derived T:
    # the group rides the clone, and a violating instantiation still fails
    # (via the origin's backstop at the instantiation).
    source = """
        struct box<T> {{ v: T; }}
        struct pd<T> extends box<T> {{}}
        fn box<T: int32 | int64>::val(const self: &box<T>) -> int32 {{
            return self.v as int32;
        }}
        fn main() -> int32 {{
            let b: pd<{arg}> = {{ v = {init} }};
            return b.val();
        }}
    """
    assert run(source.format(arg="int32", init="42")) == 42
    with pytest.raises(
        LangError, match=r"float64 is not in the type group of 'box::val'"
    ):
        compile_ir(source.format(arg="float64", init="1.0"))


def test_bound_violating_concrete_derivation_is_not_inherited():
    # The `extends`-bound sibling of the group filter: a concrete derivation
    # whose seed fails the bound simply does not inherit the member.
    with pytest.raises(
        LangError, match=r"struct 'hb' has no field or method 'get'"
    ):
        compile_ir(
            """
            struct s { n: int32; }
            struct holder<E> { item: E; }
            struct hb extends holder<int32> {}
            fn holder<E extends s>::get(const self: &holder<E>) -> int32 {
                return self.item.n;
            }
            fn main() -> int32 {
                let v: hb = { item = 7 };
                return v.get();
            }
            """
        )


def test_method_own_constraint_rides_the_clone():
    # A method-own (leftover) parameter's group is forwarded to the clone's
    # id-keyed table: the inherited member still enforces it.
    source = """
        struct point<T> {{ x: T; y: T; }}
        struct pi extends point<int32> {{}}
        fn point<T>::sum_as<U: int64 | float64>(const self: &point<T>, w: U) -> U {{
            return (self.x as U) + (self.y as U) + w;
        }}
        fn main() -> int32 {{
            let p: pi = {{ x = 20, y = 20 }};
            let w: {wtype} = 2;
            return p.sum_as(w) as int32;
        }}
    """
    assert run(source.format(wtype="int64")) == 42
    # A lone candidate skips the set-path filter; the backstop's precise
    # group error names the ORIGIN, where the group is declared.
    with pytest.raises(
        LangError,
        match=r"int16 is not in the type group of 'point::sum_as' "
        r"\(int64 \| float64\)",
    ):
        compile_ir(source.format(wtype="int16"))


def test_explicit_qualified_call_two_hops_up_the_chain(capfd):
    # The receiver-position upcast walks the whole lineage: a grandparent's
    # qualified spelling accepts the grandchild.
    assert run(
        """
        import "std/io";
        struct c { n: int32; }
        struct b extends c { m: int32; }
        struct a extends b { k: int32; }
        fn c::deep(const self: &c) -> int32 { return self.n + 30; }
        fn main() -> int32 {
            let v: a = { n = 1, m = 2, k = 3 };
            println(f"{c::deep(v)}");
            return 0;
        }
        """
    ) == 0
    assert capfd.readouterr().out == "31\n"


def test_base_specialization_is_not_inherited_through_a_generic_derivation():
    # `pd<T> extends point<T>` cannot prove per-declaration that any
    # instantiation matches `fn point<int32>::only_i`, so the specialization
    # is conservatively not inherited -- even on pd<int32> (v1; spell the
    # receiver as the base to reach it: `point::only_i(p as point<int32>)`).
    with pytest.raises(
        LangError, match=r"struct 'pd' has no field or method 'only_i'"
    ):
        compile_ir(
            """
            struct point<T> { x: T; y: T; }
            struct pd<T> extends point<T> {}
            fn point<int32>::only_i(const self: &point<int32>) -> int32 {
                return self.x;
            }
            fn main() -> int32 {
                let p: pd<int32> = { x = 1, y = 2 };
                return p.only_i();
            }
            """
        )


# --- receiver forms on the direct path -------------------------------------------


def test_rvalue_derived_receiver_spills_and_upcasts(capfd):
    # A derived RVALUE receiver (a call result) at a lone inherited const
    # method: the value spills to its own temporary, lent as the base prefix.
    assert run(
        """
        import "std/io";
        struct b { n: int32; }
        struct d extends b { extra: int32; }
        fn b::get(const self: &b) -> int32 { return self.n; }
        fn mk(n: int32) -> d {
            let v: d = { n = n, extra = 0 };
            return v;
        }
        fn main() -> int32 {
            println(f"{b::get(mk(42))}");
            return 0;
        }
        """
    ) == 0
    assert capfd.readouterr().out == "42\n"


def test_mut_returning_receiver_re_lends_upcast_on_the_direct_path(capfd):
    # A mut-returning call in receiver position re-lends its carried lvalue
    # viewed as the base prefix -- const and mut receivers both.
    assert run(
        """
        import "std/io";
        struct b { n: int32; }
        struct d extends b { extra: int32; }
        fn b::get(const self: &b) -> int32 { return self.n; }
        fn b::bump(self: &b) { self.n = self.n + 1; }
        fn d::ref(self: &d) -> &d { return self; }
        fn main() -> int32 {
            let v: d = { n = 41, extra = 0 };
            b::bump(d::ref(v));
            println(f"{b::get(d::ref(v))}");
            return 0;
        }
        """
    ) == 0
    assert capfd.readouterr().out == "42\n"


def test_mut_returning_receiver_re_lends_upcast_on_the_set_path(capfd):
    # The same re-lend through the overload-set path: the base family has
    # two members, so resolution defers the mut checks to the winner.
    assert run(
        """
        import "std/io";
        struct b { n: int32; }
        struct d extends b { extra: int32; }
        fn b::bump(self: &b) { self.n = self.n + 1; }
        fn b::bump(self: &b, by: int32) { self.n = self.n + by; }
        fn d::ref(self: &d) -> &d { return self; }
        fn main() -> int32 {
            let v: d = { n = 1, extra = 0 };
            b::bump(d::ref(v));
            b::bump(d::ref(v), 40);
            println(f"{v.n}");
            return 0;
        }
        """
    ) == 0
    assert capfd.readouterr().out == "42\n"


# --- interactions ---------------------------------------------------------------


def test_fields_still_shadow_inherited_methods():
    # Field-first shadowing is unchanged: a derived FIELD named like a base
    # method keeps the field-call diagnostics (not callable), while the
    # qualified spelling still reaches the method.
    with pytest.raises(LangError, match=r"cannot call a value of type int32"):
        compile_ir(
            """
            struct b { n: int32; }
            struct d extends b { probe: int32; }
            fn b::probe(const self: &b) -> int32 { return self.n; }
            fn main() -> int32 {
                let v: d = { n = 1, probe = 2 };
                return v.probe();
            }
            """
        )


def test_return_types_stay_spelled_at_the_base(capfd):
    # An inherited method returning the base type is NOT respelled: the
    # origin body builds a base value, and the call's static type says so.
    assert run(
        """
        import "std/io";
        struct point<T> { x: T; y: T; }
        struct pi extends point<int32> {}
        fn point<T>::flipped(const self: &point<T>) -> point<T> {
            let r: point<T> = { x = self.y, y = self.x };
            return r;
        }
        fn main() -> int32 {
            let p: pi = { x = 1, y = 2 };
            let f: point<int32> = p.flipped();
            println(f"{f.x} {f.y}");
            return 0;
        }
        """
    ) == 0
    assert capfd.readouterr().out == "2 1\n"


def test_inherited_and_derived_different_shapes_overload(capfd):
    # Java-style: a derived member with a DIFFERENT signature joins the
    # merged set as an overload, never C++ name-hiding.
    assert run(
        """
        import "std/io";
        struct b { n: int32; }
        struct d extends b {}
        fn b::get(const self: &b) -> int32 { return self.n; }
        fn d::get(const self: &d, bias: int32) -> int32 { return self.n + bias; }
        fn main() -> int32 {
            let v: d = { n = 40 };
            println(f"{v.get()} {v.get(2)}");
            return 0;
        }
        """
    ) == 0
    assert capfd.readouterr().out == "40 42\n"


def test_one_origin_instance_is_shared_across_derived_types():
    # Emission instantiates the ORIGIN template: two derived types calling
    # the same inherited member produce ONE point::sum instance -- no
    # per-derived-type code.
    ir_text = compile_ir(
        """
        struct point<T> { x: T; y: T; }
        struct pa extends point<int32> {}
        struct pb extends point<int32> {}
        fn point<T>::sum(const self: &point<T>) -> T { return self.x + self.y; }
        fn main() -> int32 {
            let a: pa = { x = 1, y = 2 };
            let b: pb = { x = 3, y = 4 };
            return a.sum() + b.sum();
        }
        """
    )
    # One define, called twice through receiver bitcasts; no derived-spelled
    # symbol ever exists.
    symbol = '@"point::sum<$0>(point<$0>)<int32>"'
    assert ir_text.count(f"define i32 {symbol}") == 1
    assert ir_text.count(f"call i32 {symbol}") == 2
    assert "pa::sum" not in ir_text and "pb::sum" not in ir_text
