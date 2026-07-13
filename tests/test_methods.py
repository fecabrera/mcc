"""Qualified free-function methods: ``fn Type::method(...)`` + ``Type::method(...)``.

The foundational slice of the Methods/OOP roadmap item. A ``fn Type::method``
definition namespaces an ordinary function to a struct; it is called by its
explicit qualified name ``Type::method(args)``. The qualified name is a single
string (``"point::magnitude"``) everywhere in the compiler -- as the function
name, the call name, the registration key, and the LLVM symbol -- so
overloading, ``@private``, and direct-call resolution all work unchanged.

``Type::`` is purely a namespace in this slice: no ``self`` convention is
enforced. The only validation is that the qualifier names a declared TYPE --
a struct, a builtin type, or a type alias of either (an alias qualifier
canonicalizes to the type it names: registering a method for ``pointf`` IS
registering it for ``point<float64>``, and both spellings call one family) --
and that a GENERIC qualifier annotates its type parameters: a declaration's
bare ``fn point::m`` / ``fn pf::m`` over a generic struct or generic alias is
an error (only a complete type -- non-generic, or fully defaulted so the bare
name is a complete type use -- may be named bare). Calls are unchanged: a bare
``point::m(p)`` looks the family up and infers from the arguments; the
qualifier may also spell the receiver instantiation
(``point<float64>::m(p)``, see test_explicit_type_args.py).
Call sugar (``.method()``), constructors, and dynamic dispatch are future work.
"""

import pytest

from mcc.driver import emit_interface
from mcc.errors import LangError
from helpers import compile_ir, run, run_path


# --- the driving use case -----------------------------------------------------

def test_qualified_def_and_call_returns_a_value(capfd):
    # A method defined under `point::` is called by its explicit qualified name
    # and returns a computed value -- the acceptance shape for this slice.
    assert run(
        """
        import "std/io";
        struct point { x: int32; y: int32; }
        fn point::sum(self: point) -> int32 {
            return self.x + self.y;
        }
        fn main() -> int32 {
            let p: point = { x = 3, y = 4 };
            println("{}", point::sum(p));
            return 0;
        }
        """
    ) == 0
    assert capfd.readouterr().out == "7\n"


def test_mut_self_mutation_visible_to_caller(capfd):
    # `mut self` is an ordinary mut param -- no receiver machinery -- so a
    # mutation through it is visible to the caller after the call.
    assert run(
        """
        import "std/io";
        struct counter { n: int32; }
        fn counter::bump(mut self: counter) {
            self.n = self.n + 1;
        }
        fn main() -> int32 {
            let c: counter = { n = 41 };
            counter::bump(c);
            println("{}", c.n);
            return 0;
        }
        """
    ) == 0
    assert capfd.readouterr().out == "42\n"


# --- the symbol is the qualified string ---------------------------------------

def test_llvm_symbol_is_the_qualified_name():
    # The qualified name threads through to the LLVM symbol verbatim; llvmlite
    # accepts `::` in a function name.
    ir = compile_ir(
        """
        struct point { x: int32; y: int32; }
        fn point::mag2(self: point) -> int32 {
            return self.x * self.x + self.y * self.y;
        }
        fn main() -> int32 {
            let p: point = { x = 1, y = 2 };
            return point::mag2(p);
        }
        """
    )
    assert '@"point::mag2"' in ir


# --- namespacing: same method name on two structs does not collide ------------

def test_same_method_name_on_two_structs(capfd):
    assert run(
        """
        import "std/io";
        struct square { side: int32; }
        struct rect { w: int32; h: int32; }
        fn square::area(self: square) -> int32 { return self.side * self.side; }
        fn rect::area(self: rect) -> int32 { return self.w * self.h; }
        fn main() -> int32 {
            let s: square = { side = 5 };
            let r: rect = { w = 2, h = 3 };
            println("{} {}", square::area(s), rect::area(r));
            return 0;
        }
        """
    ) == 0
    assert capfd.readouterr().out == "25 6\n"


# --- overloading works on the qualified string --------------------------------

def test_overloaded_qualified_method_dispatches_by_arg(capfd):
    # Two `point::shift` signatures form an overload set keyed on the qualified
    # name; dispatch picks by argument type, exactly as for a plain function.
    assert run(
        """
        import "std/io";
        struct point { x: int32; y: int32; }
        fn point::shift(self: point, dx: int32) -> int32 { return self.x + dx; }
        fn point::shift(self: point, dx: int32, dy: int32) -> int32 {
            return self.x + dx + self.y + dy;
        }
        fn main() -> int32 {
            let p: point = { x = 10, y = 20 };
            println("{} {}", point::shift(p, 1), point::shift(p, 1, 2));
            return 0;
        }
        """
    ) == 0
    assert capfd.readouterr().out == "11 33\n"


# --- @private on a qualified method -------------------------------------------

def test_private_qualified_method(capfd, tmp_path):
    # `@private` salts the qualified symbol like any other private function: it
    # is callable within its own module but invisible across modules.
    (tmp_path / "geo.mc").write_text(
        "import \"std/io\";\n"
        "struct point { x: int32; y: int32; }\n"
        "@private fn point::secret(self: point) -> int32 { return self.x; }\n"
        "fn point::show(self: point) -> int32 { return point::secret(self); }\n"
    )
    main = tmp_path / "main.mc"
    main.write_text(
        "import \"std/io\";\n"
        "import \"geo\";\n"
        "fn main() -> int32 {\n"
        "    let p: point = { x = 9, y = 0 };\n"
        "    println(\"{}\", point::show(p));\n"
        "    return 0;\n"
        "}\n"
    )
    assert run_path(main) == 0
    assert capfd.readouterr().out == "9\n"


# --- validation: the qualifier must be a declared type ------------------------

def test_undeclared_qualifier_is_an_error():
    with pytest.raises(
        LangError, match=r"no struct type 'ghost' for method 'ghost::m'"
    ):
        compile_ir(
            """
            fn ghost::m(x: int32) -> int32 { return x; }
            fn main() -> int32 { return 0; }
            """
        )


def test_enum_qualifier_is_an_error():
    # An enum is not a struct, so `Color::` is not a legal method namespace,
    # even though `Color::Red` remains a valid value expression (see below).
    with pytest.raises(
        LangError, match=r"no struct type 'Color' for method 'Color::m'"
    ):
        compile_ir(
            """
            enum Color { Red = 0, Green = 1 }
            fn Color::m(x: int32) -> int32 { return x; }
            fn main() -> int32 { return 0; }
            """
        )


# --- regression: Enum::Member as a plain value is unaffected -------------------

def test_enum_member_value_still_works(capfd):
    # `Enum::Member` NOT followed by `(` still parses to an enum-access value;
    # the qualified-call parsing only claims a `::` member that a `(` follows.
    assert run(
        """
        import "std/io";
        enum Color { Red = 0, Green = 1, Blue = 2 }
        fn main() -> int32 {
            let c: int32 = Color::Blue;
            println("{}", c);
            return 0;
        }
        """
    ) == 0
    assert capfd.readouterr().out == "2\n"


# --- generic-struct methods: `fn Type<T>::method` -----------------------------

def test_generic_method_def_and_inference_call_returns_a_value(capfd):
    # `fn point<T>::sum` namespaces a generic method to `point<T>`; the explicit
    # receiver names its type args, and the call infers `T` from the argument.
    assert run(
        """
        import "std/io";
        struct point<T> { x: T; y: T; }
        fn point<T>::sum(self: point<T>) -> T {
            return self.x + self.y;
        }
        fn main() -> int32 {
            let p: point<int32> = { x = 3, y = 4 };
            println("{}", point::sum(p));
            return 0;
        }
        """
    ) == 0
    assert capfd.readouterr().out == "7\n"


def test_generic_mut_self_mutation_visible_to_caller(capfd):
    # `mut self: point<T>` is an ordinary mut parameter of the instantiated
    # struct, so a mutation through it is visible to the caller.
    assert run(
        """
        import "std/io";
        struct point<T> { x: T; y: T; }
        fn point<T>::scale(mut self: point<T>, k: T) {
            self.x = self.x * k;
            self.y = self.y * k;
        }
        fn main() -> int32 {
            let p: point<int32> = { x = 2, y = 3 };
            point::scale(p, 10);
            println("{} {}", p.x, p.y);
            return 0;
        }
        """
    ) == 0
    assert capfd.readouterr().out == "20 30\n"


def test_generic_method_monomorphizes_across_two_instantiations(capfd):
    # One generic method template instantiates once per element type; the two
    # instances are distinct functions over distinct instances.
    assert run(
        """
        import "std/io";
        struct point<T> { x: T; y: T; }
        fn point<T>::sum(self: point<T>) -> T {
            return self.x + self.y;
        }
        fn main() -> int32 {
            let pi: point<int32> = { x = 3, y = 4 };
            let pf: point<float64> = { x = 1.5, y = 2.0 };
            println("{} {}", point::sum(pi), point::sum(pf));
            return 0;
        }
        """
    ) == 0
    assert capfd.readouterr().out == "7 3.500000\n"


def test_generic_method_with_own_type_param(capfd):
    # A generic method may declare its OWN type parameter after `::method`; the
    # struct's `<T>` and the method's `<U>` merge into one template, and both
    # are inferred from the call arguments (`T` from the receiver, `U` from the
    # extra argument).
    assert run(
        """
        import "std/io";
        struct box<T> { v: T; }
        fn box<T>::combine<U>(const self: box<T>, extra: U) -> U {
            return self.v as U + extra;
        }
        fn main() -> int32 {
            let b: box<int32> = { v = 10 };
            println("{}", box::combine(b, 5));
            return 0;
        }
        """
    ) == 0
    assert capfd.readouterr().out == "15\n"


def test_method_type_param_shadowing_struct_param_is_an_error():
    # A method type parameter may not reuse the name of one of the struct's
    # type parameters -- the two lists merge, so a shared name is ambiguous.
    with pytest.raises(
        LangError,
        match=r"method type parameter 'T' shadows a type parameter of struct 'point'",
    ):
        compile_ir(
            """
            struct point<T> { x: T; y: T; }
            fn point<T>::m<T>(self: point<T>) -> int32 { return 0; }
            fn main() -> int32 { return 0; }
            """
        )


def test_generic_const_self_and_private_method(capfd, tmp_path):
    # `const self` and `@private` compose with a generic method exactly as with
    # a plain one: the private generic method is callable within its module.
    (tmp_path / "geo.mc").write_text(
        'import "std/io";\n'
        "struct point<T> { x: T; y: T; }\n"
        "@private fn point<T>::first(const self: point<T>) -> T { return self.x; }\n"
        "fn point<T>::show(const self: point<T>) -> T { return point::first(self); }\n"
    )
    main = tmp_path / "main.mc"
    main.write_text(
        'import "std/io";\n'
        'import "geo";\n'
        "fn main() -> int32 {\n"
        "    let p: point<int32> = { x = 9, y = 0 };\n"
        '    println("{}", point::show(p));\n'
        "    return 0;\n"
        "}\n"
    )
    assert run_path(main) == 0
    assert capfd.readouterr().out == "9\n"


def test_bare_generic_receiver_still_requires_type_args():
    # RULING: no `point`-means-`point<T>` sugar. A receiver that spells the bare
    # `point` keeps the ordinary generic arity error. `T` is inferred here from
    # the plain `x: T` parameter, so instantiation proceeds and then fails
    # resolving the bare `point` receiver type -- the same error any un-argued
    # generic struct use raises.
    with pytest.raises(
        LangError,
        match=r"struct 'point' expects 1 type argument\(s\), got 0",
    ):
        run(
            """
            struct point<T> { x: T; y: T; }
            fn point<T>::sum(self: point, x: T) -> int32 { return x; }
            fn main() -> int32 {
                let p: point<int32> = { x = 1, y = 2 };
                return point::sum(p, 5);
            }
            """
        )


def test_generic_method_not_naming_struct_round_trips_through_mci(tmp_path):
    # `_decl_refs` force-adds the qualifier struct to a method's dependencies,
    # so a generic method whose signature never names its struct
    # (`fn point<T>::from_scalar(x: T) -> int32`) still pulls `point` into the
    # stub -- without it the re-imported stub would reference an undeclared
    # struct and fail codegen's `::`-qualifier validation.
    lib = tmp_path / "lib.mc"
    lib.write_text(
        "struct point<T> { x: T; y: T; }\n"
        "fn point<T>::from_scalar(x: T) -> int32 { return x as int32 + 1; }\n"
    )
    out = tmp_path / "lib.mci"
    assert emit_interface(lib, (tmp_path,), None, {}, out) == 0
    stub = out.read_text()
    assert "struct point<T>" in stub  # the struct travels despite no reference
    assert "fn point<T>::from_scalar(x: T) -> int32" in stub
    lib.unlink()  # force the import to resolve through the stub
    main = tmp_path / "main.mc"
    main.write_text(
        'import "lib";\n'
        "fn main() -> int32 { return point::from_scalar(40); }\n"
    )
    assert run_path(main) == 41


# --- method specialization: `fn Type<Concrete>::method` -----------------------


def test_specialization_dispatches_a_distinct_body(capfd):
    # `fn box<float64>::tag` specializes the generic `fn box<T>::tag` for one
    # instantiation. A box<float64> receiver runs the specialization; any other
    # receiver falls to the generic. The two bodies return DIFFERENT values, so
    # the output proves which one ran (not merely that both compiled).
    assert run(
        """
        import "std/io";
        struct box<T> { v: T; }
        fn box<T>::tag(self: box<T>) -> int32 { return 1; }
        fn box<float64>::tag(self: box<float64>) -> int32 { return 2; }
        fn main() -> int32 {
            let bi: box<int32> = { v = 7 };
            let bf: box<float64> = { v = 1.0 };
            println("{} {}", box::tag(bi), box::tag(bf));
            return 0;
        }
        """
    ) == 0
    assert capfd.readouterr().out == "1 2\n"  # generic, then specialization


def test_specialization_on_a_user_struct_argument(capfd):
    # Classification happens at codegen against the type environment, so ANY
    # concrete type -- including a user struct -- may specialize a method.
    assert run(
        """
        import "std/io";
        struct widget { n: int32; }
        struct holder<T> { item: T; }
        fn holder<T>::code(self: holder<T>) -> int32 { return 0; }
        fn holder<widget>::code(self: holder<widget>) -> int32 { return 42; }
        fn main() -> int32 {
            let w: holder<widget> = { item = { n = 5 } };
            let i: holder<int32> = { item = 9 };
            println("{} {}", holder::code(w), holder::code(i));
            return 0;
        }
        """
    ) == 0
    assert capfd.readouterr().out == "42 0\n"  # specialization, then generic


def test_lone_specialization_without_a_generic_base(capfd):
    # A specialization needs no generic base -- it is just a concrete
    # namespaced overload for one instantiation.
    assert run(
        """
        import "std/io";
        struct box<T> { v: T; }
        fn box<int32>::only(self: box<int32>) -> int32 { return self.v + 5; }
        fn main() -> int32 {
            let b: box<int32> = { v = 3 };
            println("{}", box::only(b));
            return 0;
        }
        """
    ) == 0
    assert capfd.readouterr().out == "8\n"


def test_partial_specialization_three_tier_dispatch(capfd):
    # A MIX of concrete and fresh arguments is a PARTIAL specialization: a
    # template matching only receivers whose concrete positions agree. The
    # existing rank machinery orders the whole family -- full specialization
    # (concrete, tier 2) beats a partial, and the partial's concrete positions
    # score higher pattern specificity than the fully generic template's bare
    # names within tier 0. The three bodies return DIFFERENT values, so the
    # output proves the ordering.
    assert run(
        """
        import "std/io";
        struct pair<A, B> { a: A; b: B; }
        fn pair<T, U>::which(self: pair<T, U>) -> int32 { return 0; }
        fn pair<int32, U>::which(self: pair<int32, U>) -> int32 { return 1; }
        fn pair<int32, int8>::which(self: pair<int32, int8>) -> int32 { return 2; }
        fn main() -> int32 {
            let g: pair<int64, int64> = { a = 1, b = 2 };
            let p: pair<int32, int64> = { a = 1, b = 2 };
            let f: pair<int32, int8> = { a = 1, b = 2 };
            println("{} {} {}", pair::which(g), pair::which(p), pair::which(f));
            return 0;
        }
        """
    ) == 0
    assert capfd.readouterr().out == "0 1 2\n"  # generic, partial, full


def test_duplicate_specialization_collides():
    # Two bodies for one instantiation spell the same concrete parameter list,
    # so the existing duplicate check catches them.
    with pytest.raises(
        LangError,
        match=r"function 'box::tag\(box<float64>\)' already defined",
    ):
        compile_ir(
            """
            struct box<T> { v: T; }
            fn box<float64>::tag(self: box<float64>) -> int32 { return 1; }
            fn box<float64>::tag(self: box<float64>) -> int32 { return 2; }
            fn main() -> int32 { return 0; }
            """
        )


def test_specialization_wrong_arity_is_an_error():
    # A specialization's argument count must match the struct's arity.
    with pytest.raises(
        LangError,
        match=r"specialization of struct 'box' expects 1 type argument",
    ):
        compile_ir(
            """
            struct box<T> { v: T; }
            fn box<int32, int32>::m(self: box<int32>) -> int32 { return 0; }
            fn main() -> int32 { return 0; }
            """
        )


def test_specialization_round_trips_through_mci(tmp_path):
    # A specialization exports as a concrete prototype that re-spells the
    # qualifier's annotation, `fn box<float64>::tag(self: box<float64>)` -- a
    # bare `fn box::tag` would not re-parse (a generic qualifier must be
    # annotated). Re-parsing and re-compiling that stub classifies it straight
    # back to the same concrete overload (its body lives in the compiled
    # object, so the stub carries only the prototype -- not JIT-runnable on
    # its own).
    lib = tmp_path / "lib.mc"
    lib.write_text(
        "struct box<T> { v: T; }\n"
        "fn box<T>::tag(self: box<T>) -> int32 { return 1; }\n"
        "fn box<float64>::tag(self: box<float64>) -> int32 { return 2; }\n"
    )
    out = tmp_path / "lib.mci"
    assert emit_interface(lib, (tmp_path,), None, {}, out) == 0
    stub = out.read_text()
    assert "fn box<float64>::tag(self: box<float64>) -> int32;" in stub
    # Re-parsing and re-compiling the stub round-trips: the annotated
    # prototype classifies as the same concrete box<float64> overload (a
    # plain-symbol declaration whose body lives in the object) -- the binding
    # substitution is a no-op on the already-concrete signature.
    ir = compile_ir(stub)
    assert 'declare i32 @"box::tag"(%"box<float64>"' in ir


def test_lone_partial_specialization(capfd):
    # A partial needs no generic base or full specialization around it: it is
    # simply a generic template whose concrete positions constrain the match.
    assert run(
        """
        import "std/io";
        struct pair<A, B> { a: A; b: B; }
        fn pair<int32, U>::second(self: pair<int32, U>) -> U { return self.b; }
        fn main() -> int32 {
            let p: pair<int32, int64> = { a = 1, b = 40 };
            println("{}", pair::second(p) + 2);
            return 0;
        }
        """
    ) == 0
    assert capfd.readouterr().out == "42\n"


def test_partial_mismatched_receiver_falls_to_generic(capfd):
    # A receiver disagreeing on a concrete position never matches the partial:
    # binding resolution filters it out, and the call falls to the generic.
    assert run(
        """
        import "std/io";
        struct pair<A, B> { a: A; b: B; }
        fn pair<T, U>::which(self: pair<T, U>) -> int32 { return 0; }
        fn pair<int32, U>::which(self: pair<int32, U>) -> int32 { return 1; }
        fn main() -> int32 {
            let p: pair<int64, int8> = { a = 1, b = 2 };
            println("{}", pair::which(p));
            return 0;
        }
        """
    ) == 0
    assert capfd.readouterr().out == "0\n"


def test_lone_partial_wrong_receiver_is_a_coercion_error():
    # With ONLY the partial declared, a receiver disagreeing on the concrete
    # position has nowhere to fall: the standard coercion diagnostic reports
    # the pattern the partial demands against the receiver supplied.
    with pytest.raises(
        LangError,
        match=r"argument 1 of 'pair::m': expected pair<int32, int8>, "
        r"got pair<int64, int8>",
    ):
        compile_ir(
            """
            struct pair<A, B> { a: A; b: B; }
            fn pair<int32, U>::m(self: pair<int32, U>) -> int32 { return 1; }
            fn main() -> int32 {
                let p: pair<int64, int8> = { a = 1, b = 2 };
                return pair::m(p);
            }
            """
        )


def test_bounded_partial_specialization(capfd):
    # A fresh position may carry a closed type group (the parser rides the
    # decoration along; codegen merges it into the template): the partial then
    # matches only receivers inside the group, others fall to the generic.
    assert run(
        """
        import "std/io";
        struct pair<A, B> { a: A; b: B; }
        fn pair<T, U>::which(self: pair<T, U>) -> int32 { return 0; }
        fn pair<int32, U: int8 | int16>::which(self: pair<int32, U>) -> int32 {
            return 1;
        }
        fn main() -> int32 {
            let inside: pair<int32, int8> = { a = 1, b = 2 };
            let outside: pair<int32, int64> = { a = 1, b = 2 };
            println("{} {}", pair::which(inside), pair::which(outside));
            return 0;
        }
        """
    ) == 0
    assert capfd.readouterr().out == "1 0\n"


def test_bounded_generic_beats_unbounded_partial(capfd):
    # The pre-existing tier rule is tier-over-specificity: a BOUNDED generic
    # (tier 1 -- a written commitment to a type set) beats an UNBOUNDED
    # partial (tier 0 -- the open pattern), even though the partial's concrete
    # position is the more specific pattern.
    assert run(
        """
        import "std/io";
        struct pair<A, B> { a: A; b: B; }
        fn pair<K: int8 | int32, V>::which(self: pair<K, V>) -> int32 { return 1; }
        fn pair<int32, U>::which(self: pair<int32, U>) -> int32 { return 2; }
        fn main() -> int32 {
            let p: pair<int32, int64> = { a = 1, b = 2 };
            println("{}", pair::which(p));
            return 0;
        }
        """
    ) == 0
    assert capfd.readouterr().out == "1\n"  # the bounded generic wins


def test_bounded_partial_beats_bounded_generic(capfd):
    # Bounding the partial levels the tiers (both 1), and within a tier the
    # partial's concrete position scores higher pattern specificity.
    assert run(
        """
        import "std/io";
        struct pair<A, B> { a: A; b: B; }
        fn pair<K: int8 | int32, V>::which(self: pair<K, V>) -> int32 { return 1; }
        fn pair<int32, U: int8 | int64>::which(self: pair<int32, U>) -> int32 {
            return 2;
        }
        fn main() -> int32 {
            let p: pair<int32, int8> = { a = 1, b = 2 };
            println("{}", pair::which(p));
            return 0;
        }
        """
    ) == 0
    assert capfd.readouterr().out == "2\n"  # the bounded partial wins


def test_default_on_a_partial_fresh_param(capfd):
    # A fresh position may declare a default, filled when the parameter is
    # neither given nor inferred -- exactly the ordinary generic-default rule.
    # Defaults stay trailing among the FRESH positions (a concrete argument is
    # not a parameter), so an undefaulted fresh name may precede a defaulted
    # one across a concrete position.
    assert run(
        """
        import "std/io";
        struct pair<A, B> { a: A; b: B; }
        struct triple<A, B, C> { a: A; b: B; c: C; }
        fn pair<int32, U = int16>::width(x: int32) -> int64 {
            return sizeof(U) as int64 + x as int64;
        }
        fn triple<K, int32, V = int8>::tag(k: K) -> int64 {
            return sizeof(K) as int64 * 10 + sizeof(V) as int64;
        }
        fn main() -> int32 {
            println("{} {}", pair::width(0), triple::tag(7));
            return 0;
        }
        """
    ) == 0
    assert capfd.readouterr().out == "2 41\n"  # sizeof(int16); int32*10+int8


def test_two_rank_tied_partials_are_ambiguous():
    # Two partials that each match the receiver and tie on (tier, specificity)
    # hit the standard ambiguity error: `pair<int32, U>` and `pair<T, int8>`
    # are INCOMPARABLE under the subsumption tie-break -- neither pattern is
    # an instance of the other (each holds a concrete type where the other
    # holds a wildcard), so no maximum exists and the tie stands.
    with pytest.raises(
        LangError,
        match=r"call to 'pair::m' is ambiguous between overloads",
    ):
        compile_ir(
            """
            struct pair<A, B> { a: A; b: B; }
            fn pair<int32, U>::m(self: pair<int32, U>) -> int32 { return 1; }
            fn pair<T, int8>::m(self: pair<T, int8>) -> int32 { return 2; }
            fn main() -> int32 {
                let p: pair<int32, int8> = { a = 1, b = 2 };
                return pair::m(p);
            }
            """
        )


def test_partial_fresh_name_capturing_bound_struct_param_is_an_error():
    # `struct pair<A, B>` + `fn pair<int32, A>::m`: the fresh name `A` reuses
    # the struct parameter name the concrete position binds -- the signature's
    # `A` would have to substitute to int32 AND stand for the free parameter.
    with pytest.raises(
        LangError,
        match=r"type parameter 'A' shadows a type parameter of struct 'pair' "
        r"bound to a concrete type by the partial specialization",
    ):
        compile_ir(
            """
            struct pair<A, B> { a: A; b: B; }
            fn pair<int32, A>::m(self: pair<int32, A>) -> int32 { return 0; }
            fn main() -> int32 { return 0; }
            """
        )


def test_partial_fresh_name_may_reuse_its_own_position(capfd):
    # Reusing the struct's declared name for the SAME position it occupies
    # (`B` is pair's second parameter) binds nothing away and is fine.
    assert run(
        """
        import "std/io";
        struct pair<A, B> { a: A; b: B; }
        fn pair<int32, B>::second(self: pair<int32, B>) -> B { return self.b; }
        fn main() -> int32 {
            let p: pair<int32, int64> = { a = 1, b = 7 };
            println("{}", pair::second(p));
            return 0;
        }
        """
    ) == 0
    assert capfd.readouterr().out == "7\n"


def test_decorated_concrete_struct_argument_is_an_error():
    # A type group (or bound, or default) declares a parameter; writing one on
    # a concrete type is nonsensical and no longer falls into the silent
    # parameter-named-'int32' trap.
    with pytest.raises(
        LangError,
        match=r"struct type argument 'int32' names a concrete type; a type "
        r"group, 'extends' bound, or default may only decorate a fresh "
        r"type parameter",
    ):
        compile_ir(
            """
            struct pair<A, B> { a: A; b: B; }
            fn pair<int32: int8 | int16, U>::m(self: pair<int32, U>) -> int32 {
                return 0;
            }
            fn main() -> int32 { return 0; }
            """
        )


def test_partial_wrong_arity_is_an_error():
    # A partial's argument count must match the struct's arity, with the same
    # message a full specialization gives.
    with pytest.raises(
        LangError,
        match=r"specialization of struct 'triple' expects 3 type argument",
    ):
        compile_ir(
            """
            struct triple<A, B, C> { a: A; b: B; c: C; }
            fn triple<int32, U>::m(self: triple<int32, U, U>) -> int32 {
                return 0;
            }
            fn main() -> int32 { return 0; }
            """
        )


def test_partial_fresh_name_shadowing_own_type_param_is_an_error():
    # A fresh struct position and the method's own list may not spell one
    # name -- the same shadow rule as a fully generic method.
    with pytest.raises(
        LangError,
        match=r"method type parameter 'U' shadows a type parameter of "
        r"struct 'pair'",
    ):
        compile_ir(
            """
            struct pair<A, B> { a: A; b: B; }
            fn pair<int32, U>::m<U>(self: pair<int32, U>) -> int32 { return 0; }
            fn main() -> int32 { return 0; }
            """
        )


def test_partial_with_method_own_type_param(capfd):
    # The fresh struct names prepend the method's own type parameters, exactly
    # as the fully generic arm does; both are inferred at the call.
    assert run(
        """
        import "std/io";
        struct pair<A, B> { a: A; b: B; }
        fn pair<int32, U>::pick<W>(self: pair<int32, U>, w: W) -> W { return w; }
        fn main() -> int32 {
            let p: pair<int32, int8> = { a = 1, b = 2 };
            println("{}", pair::pick(p, 42));
            return 0;
        }
        """
    ) == 0
    assert capfd.readouterr().out == "42\n"


def test_partial_on_user_struct_concrete_argument(capfd):
    # Classification resolves against the type environment, so a user struct
    # works as a partial's concrete position too.
    assert run(
        """
        import "std/io";
        struct vec2 { x: int32; y: int32; }
        struct table<K, V> { k: K; v: V; }
        fn table<T, U>::kind(self: table<T, U>) -> int32 { return 0; }
        fn table<vec2, U>::kind(self: table<vec2, U>) -> int32 { return 33; }
        fn main() -> int32 {
            let t: table<vec2, int8> = { k = { x = 1, y = 2 }, v = 3 };
            let u: table<int32, int8> = { k = 1, v = 3 };
            println("{} {}", table::kind(t), table::kind(u));
            return 0;
        }
        """
    ) == 0
    assert capfd.readouterr().out == "33 0\n"


def test_partial_uninferable_fresh_param_is_an_error():
    # A fresh position no argument mentions is the standard inference failure.
    with pytest.raises(
        LangError,
        match=r"cannot infer type parameter\(s\) U for 'pair::zero'",
    ):
        compile_ir(
            """
            struct pair<A, B> { a: A; b: B; }
            fn pair<int32, U>::zero(x: int32) -> int32 { return x; }
            fn main() -> int32 { return pair::zero(5); }
            """
        )


def test_duplicate_partial_specialization_collides():
    # Two partials binding the same positions spell one template base (fresh
    # names alpha-rename to positional placeholders), so the existing
    # duplicate-template check catches them, alpha-variants included.
    with pytest.raises(
        LangError,
        match=r"function 'pair::m<\$0>\(pair<int32, \$0>\)' already defined",
    ):
        compile_ir(
            """
            struct pair<A, B> { a: A; b: B; }
            fn pair<int32, U>::m(self: pair<int32, U>) -> int32 { return 1; }
            fn pair<int32, W>::m(self: pair<int32, W>) -> int32 { return 2; }
            fn main() -> int32 { return 0; }
            """
        )


def test_partial_specialization_round_trips_through_mci(tmp_path):
    # A partial is a generic template after classification, so its body
    # travels VERBATIM into the stub -- the pre-`::` mix, a bounded fresh
    # position included -- and the re-parsed stub re-classifies identically.
    lib = tmp_path / "lib.mc"
    lib.write_text(
        "struct pair<A, B> { a: A; b: B; }\n"
        "fn pair<T, U>::which(self: pair<T, U>) -> int32 { return 0; }\n"
        "fn pair<int32, U>::which(self: pair<int32, U>) -> int32 { return 1; }\n"
        "fn pair<int64, U: int8 | int16>::which(self: pair<int64, U>) -> int32"
        " { return 2; }\n"
    )
    out = tmp_path / "lib.mci"
    assert emit_interface(lib, (tmp_path,), None, {}, out) == 0
    stub = out.read_text()
    assert (
        "fn pair<int32, U>::which(self: pair<int32, U>) -> int32 { return 1; }"
        in stub
    )
    assert (
        "fn pair<int64, U: int8 | int16>::which(self: pair<int64, U>) -> int32"
        " { return 2; }" in stub
    )
    lib.unlink()  # force the import to resolve through the stub
    main = tmp_path / "main.mc"
    main.write_text(
        'import "lib";\n'
        "fn main() -> int32 {\n"
        "    let g: pair<int8, int8> = { a = 1, b = 2 };\n"
        "    let p: pair<int32, int8> = { a = 1, b = 2 };\n"
        "    let b: pair<int64, int16> = { a = 1, b = 2 };\n"
        "    return pair::which(g) * 100 + pair::which(p) * 10"
        " + pair::which(b);\n"
        "}\n"
    )
    assert run_path(main) == 12  # 0*100 + 1*10 + 2


def test_decorated_all_fresh_list_still_works(capfd):
    # REGRESSION: an all-fresh DECORATED pre-`::` list (`fn pair<K: ..., V>`)
    # now routes through the captured struct_type_args path instead of the
    # parse-time merge -- behavior must be identical.
    assert run(
        """
        import "std/io";
        struct pair<A, B> { a: A; b: B; }
        fn pair<K: int32 | int64, V>::sum(self: pair<K, V>) -> K {
            return self.a + self.b as K;
        }
        fn main() -> int32 {
            let p: pair<int32, int8> = { a = 40, b = 2 };
            println("{}", pair::sum(p));
            return 0;
        }
        """
    ) == 0
    assert capfd.readouterr().out == "42\n"


def test_decorated_all_fresh_validation_still_applies():
    # REGRESSION: the declaration-shape checks parse_type_params ran on a
    # decorated pre-`::` list still fire (from classification now), with the
    # same messages: a non-trailing default...
    with pytest.raises(
        LangError,
        match=r"type parameter 'V' without a default cannot follow a "
        r"defaulted one",
    ):
        compile_ir(
            """
            struct pair<A, B> { a: A; b: B; }
            fn pair<K = int32, V>::m(self: pair<K, V>) -> int32 { return 0; }
            fn main() -> int32 { return 0; }
            """
        )
    # ...a parameter carrying both a group and a bound (the speculation
    # backtracks, so parse_type_params still owns this one, verbatim)...
    with pytest.raises(
        LangError,
        match=r"type parameter 'K' cannot have both a closed type group and "
        r"an 'extends' bound",
    ):
        compile_ir(
            """
            struct shape { kind: int32; }
            struct pair<A, B> { a: A; b: B; }
            fn pair<K: int8 | int16 extends shape, V>::m(self: pair<K, V>)
                -> int32 { return 0; }
            fn main() -> int32 { return 0; }
            """
        )
    # ...a default referencing the parameter itself (or a later one)...
    with pytest.raises(
        LangError,
        match=r"default for type parameter 'K' references 'K', which is "
        r"not declared before it",
    ):
        compile_ir(
            """
            struct pair<A, B> { a: A; b: B; }
            fn pair<K = K, V = int32>::m(self: pair<K, V>) -> int32 {
                return 0;
            }
            fn main() -> int32 { return 0; }
            """
        )
    # ...a group member referencing a parameter...
    with pytest.raises(
        LangError,
        match=r"type group member V for parameter 'K' references type "
        r"parameter 'V'; group members must be concrete types",
    ):
        compile_ir(
            """
            struct pair<A, B> { a: A; b: B; }
            fn pair<K: V | int8, V>::m(self: pair<K, V>) -> int32 { return 0; }
            fn main() -> int32 { return 0; }
            """
        )
    # ...a grouped parameter's default referencing an (earlier) parameter...
    with pytest.raises(
        LangError,
        match=r"default for type parameter 'K' references 'V'; a grouped "
        r"parameter's default must name a group member",
    ):
        compile_ir(
            """
            struct pair<A, B> { a: A; b: B; }
            fn pair<V, K: int8 | int16 = V>::m(self: pair<V, K>) -> int32 {
                return 0;
            }
            fn main() -> int32 { return 0; }
            """
        )
    # ...and a bound referencing a parameter.
    with pytest.raises(
        LangError,
        match=r"bound V for type parameter 'K' references type parameter "
        r"'V'; a bound must be a concrete struct",
    ):
        compile_ir(
            """
            struct pair<A, B> { a: A; b: B; }
            fn pair<K extends V, V>::m(self: pair<K, V>) -> int32 { return 0; }
            fn main() -> int32 { return 0; }
            """
        )


def test_malformed_struct_arg_list_still_reports_parse_errors():
    # REGRESSION: the pre-`::` speculation backtracks on a malformed list, so
    # parse_type_params still reports the parse error -- an unexpected token
    # mid-list...
    with pytest.raises(LangError, match=r"expected '>', got '\|'"):
        compile_ir(
            """
            struct pair<A, B> { a: A; b: B; }
            fn pair<K | V>::m(self: pair<K, V>) -> int32 { return 0; }
            fn main() -> int32 { return 0; }
            """
        )
    # ...and an entry that is not a type at all.
    with pytest.raises(LangError, match=r"expected 'IDENT', got '3'"):
        compile_ir(
            """
            struct pair<A, B> { a: A; b: B; }
            fn pair<3, V>::m(self: pair<int32, V>) -> int32 { return 0; }
            fn main() -> int32 { return 0; }
            """
        )


def test_specialization_coexists_with_own_type_param_generic(capfd):
    # REGRESSION: a generic method with its OWN type parameter
    # (`fn box<T>::labeled<U>`) still parses and runs after the pre-`::` list is
    # routed through the new classification path.
    assert run(
        """
        import "std/io";
        struct box<T> { v: T; }
        fn box<T>::labeled<U>(self: box<T>, label: U) -> U { return label; }
        fn main() -> int32 {
            let b: box<int32> = { v = 7 };
            println("{}", box::labeled(b, 99));
            return 0;
        }
        """
    ) == 0
    assert capfd.readouterr().out == "99\n"


# --- methods on type aliases + any-type qualifiers -----------------------------
#
# Methods register to a TYPE, and a type alias is just an alias: declaring
# `fn pointf::m` with `type pointf = point<float64>` registers to the family
# `point::m` exactly as `fn point<float64>::m` would -- and vice versa. The
# same principle admits builtin-type qualifiers (`fn int32::m`): `Type::`
# stays a pure namespace, so the family is simply the canonical name string.


def test_alias_declared_specialization_outranks_generic(capfd):
    # The acceptance shape (main.mc): a specialization declared through a
    # plain alias (`fn pointf::tag`) coexists with the generic
    # `fn point<T>::tag` and wins for a point<float64> receiver. Distinct
    # return values prove which body ran.
    assert run(
        """
        import "std/io";
        struct point<T> { x: T; y: T; }
        type pointf = point<float64>;
        fn pointf::tag(self: pointf) -> int32 { return 2; }
        fn point<T>::tag(self: point<T>) -> int32 { return 1; }
        fn main() -> int32 {
            let pi: point<int64>;
            let pf: pointf;
            println("{} {}", point::tag(pi), point::tag(pf));
            return 0;
        }
        """
    ) == 0
    assert capfd.readouterr().out == "1 2\n"  # generic, then alias-declared


def test_plain_struct_alias_qualifier_and_both_call_spellings(capfd):
    # An alias of a NON-generic struct: `fn c::bump` registers to
    # `counter::bump`, and both the alias and the canonical spelling call
    # the one family.
    assert run(
        """
        import "std/io";
        struct counter { n: int32; }
        type c = counter;
        fn c::bump(mut self: counter) { self.n = self.n + 1; }
        fn main() -> int32 {
            let x: counter = { n = 0 };
            counter::bump(x);
            c::bump(x);
            println("{}", x.n);
            return 0;
        }
        """
    ) == 0
    assert capfd.readouterr().out == "2\n"


def test_alias_of_alias_chain_canonicalizes():
    # The chase follows a chain: pf2 -> pf1 -> point<float64>. The declared
    # method is a point<float64> specialization; the deepest and shallowest
    # spellings both reach it.
    assert (
        run(
            """
            struct point<T> { x: T; y: T; }
            type pf1 = point<float64>;
            type pf2 = pf1;
            fn pf2::code(self: pf2) -> int32 { return 42; }
            fn main() -> int32 {
                let p: pf1;
                return point::code(p) + pf2::code(p) - 42 * 2;
            }
            """
        )
        == 0
    )


def test_permuting_generic_alias_becomes_a_partial_specialization():
    # `type swap<X, Y> = pair2<Y, X>` with `fn swap<int32, U>::pick`
    # substitutes to the partial `fn pair2<U, int32>::pick`: it matches only
    # receivers whose SECOND position is int32, and U binds through the
    # permutation.
    assert (
        run(
            """
            struct pair2<A, B> { a: A; b: B; }
            type swap<X, Y> = pair2<Y, X>;
            fn swap<int32, U>::pick(self: swap<int32, U>) -> U {
                return self.a;
            }
            fn main() -> int32 {
                let p: pair2<int64, int32>;
                p.a = 42;
                p.b = 7;
                return pair2::pick(p) as int32 - 42;
            }
            """
        )
        == 0
    )


def test_bare_generic_alias_qualifier_is_an_error():
    # RULING: a method declaration must annotate a generic qualifier's type
    # parameters. A bare generic-alias qualifier (`fn pf::mk` with
    # `type pf<T> = point<T>`) is invalid exactly as bare `fn point::mk` is --
    # there is no namespace-passthrough form.
    with pytest.raises(
        LangError,
        match=r"type alias 'pf' is generic; the method qualifier must "
        r"annotate its type parameter\(s\), e.g. 'fn pf<T>::mk' or "
        r"'fn pf<float64>::mk'",
    ):
        compile_ir(
            """
            struct point<T> { x: T; y: T; }
            type pf<T> = point<T>;
            fn pf::mk() -> int32 { return 7; }
            fn main() -> int32 { return 0; }
            """
        )


def test_bare_generic_struct_qualifier_is_an_error():
    # The same ruling for the struct spelled directly: bare `fn point::mk`
    # over a generic struct must annotate -- `fn point<T>::mk` (generic) or
    # `fn point<float64>::mk` (a specialization).
    with pytest.raises(
        LangError,
        match=r"struct 'point' is generic; the method qualifier must "
        r"annotate its type parameter\(s\), e.g. 'fn point<T>::mk' or "
        r"'fn point<float64>::mk'",
    ):
        compile_ir(
            """
            struct point<T> { x: T; y: T; }
            fn point::mk() -> int32 { return 7; }
            fn main() -> int32 { return 0; }
            """
        )


def test_alias_chain_landing_on_a_bare_generic_is_an_error():
    # A plain alias of a bare generic NAME supplies no arguments along the
    # chase, so the chain lands on the generic struct unannotated -- the same
    # error, reported for the struct (the alias itself is not generic, so
    # annotating `pf` is not the fix; naming the struct's parameters is).
    with pytest.raises(
        LangError,
        match=r"struct 'point' is generic; the method qualifier must "
        r"annotate its type parameter\(s\)",
    ):
        compile_ir(
            """
            struct point<T> { x: T; y: T; }
            type pf = point;
            fn pf::mk() -> int32 { return 7; }
            fn main() -> int32 { return 0; }
            """
        )


def test_method_own_type_params_do_not_satisfy_the_qualifier():
    # The method's own `<...>` list sits AFTER the name; it never annotates
    # the qualifier. `fn point::mk<W>` is still a bare generic qualifier.
    with pytest.raises(
        LangError,
        match=r"struct 'point' is generic; the method qualifier must "
        r"annotate its type parameter\(s\)",
    ):
        compile_ir(
            """
            struct point<T> { x: T; y: T; }
            fn point::mk<W>(x: W) -> int32 { return 0; }
            fn main() -> int32 { return 0; }
            """
        )


def test_fully_defaulted_struct_qualifier_may_be_bare():
    # PINNED: a fully-defaulted generic struct's bare name is a complete type
    # use (`box` IS `box<int32>`, the tail fills), so a bare qualifier is
    # complete here too -- `fn box::tag` is the specialization at the
    # defaults, outranking the generic for a box<int32> receiver.
    assert (
        run(
            """
            struct box<T = int32> { v: T; }
            fn box<T>::tag(self: box<T>) -> int32 { return 1; }
            fn box::tag(self: box<int32>) -> int32 { return 2; }
            fn main() -> int32 {
                let a: box<int64>;
                let b: box<int32>;
                return box::tag(a) * 10 + box::tag(b) - 12;
            }
            """
        )
        == 0
    )


def test_fully_defaulted_alias_qualifier_may_be_bare():
    # ...and the same for a fully-defaulted generic ALIAS: `fn pf::m` with
    # `type pf<T = float64> = point<T>` is `fn point<float64>::m`, exactly as
    # the bare type use resolves. A partially-defaulted alias stays the error.
    assert (
        run(
            """
            struct point<T> { x: T; y: T; }
            type pf<T = float64> = point<T>;
            fn point<T>::m(self: point<T>) -> int32 { return 1; }
            fn pf::m(self: point<float64>) -> int32 { return 2; }
            fn main() -> int32 {
                let a: point<int32>;
                let b: point<float64>;
                return point::m(a) * 10 + point::m(b) - 12;
            }
            """
        )
        == 0
    )
    with pytest.raises(
        LangError,
        match=r"type alias 'two' is generic; the method qualifier must "
        r"annotate its type parameter\(s\)",
    ):
        compile_ir(
            """
            struct pair2<A, B> { a: A; b: B; }
            type two<A, B = int32> = pair2<A, B>;
            fn two::m() -> int32 { return 0; }
            fn main() -> int32 { return 0; }
            """
        )


def test_builtin_qualifier_direct_and_via_alias_share_one_family():
    # Ruling: methods register to a TYPE, struct or builtin alike. A direct
    # `fn int32::double` and an alias-declared `fn myint::triple` both live
    # in the int32 family, and every spelling calls either.
    assert (
        run(
            """
            type myint = int32;
            fn int32::double(x: int32) -> int32 { return x * 2; }
            fn myint::triple(x: int32) -> int32 { return x * 3; }
            fn main() -> int32 {
                return int32::triple(2) + myint::double(4) - 14;
            }
            """
        )
        == 0
    )


def test_builtin_generic_qualifier_with_fresh_names():
    # `fn slice<T>::first` rides the generic path: a builtin family has no
    # declared parameter names, but fresh names simply prepend the method's
    # own type parameters, exactly as they do for a struct.
    assert (
        run(
            """
            fn slice<T>::first(s: slice<T>) -> T { return s[0]; }
            fn main() -> int32 {
                let a: int32[3] = [5, 6, 7];
                return slice::first(a as slice<int32>) - 5;
            }
            """
        )
        == 0
    )


def test_builtin_specialization_is_an_error():
    # A builtin type has no declared parameter names for a concrete argument
    # to bind, so specializing one is rejected -- the signature alone already
    # drives dispatch.
    with pytest.raises(
        LangError,
        match=r"cannot specialize builtin type 'slice'; spell the receiver "
        r"type in the method's signature instead",
    ):
        compile_ir(
            """
            fn slice<int32>::first(s: slice<int32>) -> int32 { return s[0]; }
            fn main() -> int32 { return 0; }
            """
        )


def test_duplicate_position_alias_is_a_diagonal_constraint(capfd):
    # `type diag<T> = pair2<T, T>` expands `fn diag<U>::first` to the target
    # pair2<U, U> with ONE parameter U: a pair2<int32, int32> receiver binds
    # it (and the alias-spelled signature infers through the alias)...
    assert run(
        """
        import "std/io";
        struct pair2<A, B> { a: A; b: B; }
        type diag<T> = pair2<T, T>;
        fn diag<U>::first(self: diag<U>) -> U { return self.a; }
        fn main() -> int32 {
            let p: pair2<int32, int32>;
            p.a = 41;
            p.b = 1;
            println("{}", pair2::first(p) + 1);
            return 0;
        }
        """
    ) == 0
    assert capfd.readouterr().out == "42\n"
    # ...and a receiver whose positions DISAGREE is rejected: unification
    # binds U at the first position and reports the conflict at the second.
    with pytest.raises(
        LangError,
        match=r"conflicting types for type parameter U in call to "
        r"'pair2::first': int32 vs float64",
    ):
        compile_ir(
            """
            struct pair2<A, B> { a: A; b: B; }
            type diag<T> = pair2<T, T>;
            fn diag<U>::first(self: diag<U>) -> U { return self.a; }
            fn main() -> int32 {
                let p: pair2<int32, float64>;
                return pair2::first(p) as int32;
            }
            """
        )


def test_diagonal_alias_beside_a_generic_sibling():
    # Alongside a fully generic sibling, a MISMATCHED receiver is non-viable
    # for the diagonal (the repeated position conflicts) and falls through to
    # the generic...
    assert (
        run(
            """
            struct pair2<A, B> { a: A; b: B; }
            type diag<T> = pair2<T, T>;
            fn diag<U>::which(self: diag<U>) -> int32 { return 1; }
            fn pair2<A, B>::which(self: pair2<A, B>) -> int32 { return 0; }
            fn main() -> int32 {
                let mixed: pair2<int32, float64>;
                return pair2::which(mixed);
            }
            """
        )
        == 0
    )
    # ...and an AGREEING receiver picks the DIAGONAL: the two patterns tie
    # on rank (repeated names score no extra specificity), and the
    # subsumption tie-break resolves the tie -- pair2<U, U> is strictly an
    # instance of the open pair2<A, B> (A := U, B := U binds consistently;
    # the reverse mapping cannot, since U would have to bind both A and B),
    # so the diagonal is the more specialized declaration and wins. The
    # alias spelling participates through dealias_pattern, like inference.
    assert (
        run(
            """
            struct pair2<A, B> { a: A; b: B; }
            type diag<T> = pair2<T, T>;
            fn diag<U>::which(self: diag<U>) -> int32 { return 1; }
            fn pair2<A, B>::which(self: pair2<A, B>) -> int32 { return 0; }
            fn main() -> int32 {
                let same: pair2<int32, int32>;
                return pair2::which(same);
            }
            """
        )
        == 1
    )


def test_alias_and_canonical_spellings_collide_as_duplicates():
    # Registering for the alias IS registering for the type: the two
    # spellings of one signature are the existing duplicate error.
    with pytest.raises(
        LangError,
        match=r"function 'point::m\(point<float64>\)' already defined; "
        r"overloads must differ in parameter types",
    ):
        compile_ir(
            """
            struct point<T> { x: T; y: T; }
            type pointf = point<float64>;
            fn pointf::m(self: pointf) -> int32 { return 1; }
            fn point<float64>::m(self: point<float64>) -> int32 { return 2; }
            fn main() -> int32 { return 0; }
            """
        )


def test_generic_alias_qualifier_arity_error():
    # Written pre-`::` arguments bind the ALIAS's parameters, so their count
    # is checked against the alias, not the underlying struct.
    with pytest.raises(
        LangError, match=r"type alias 'pf' expects 1 type argument\(s\), got 2"
    ):
        compile_ir(
            """
            struct point<T> { x: T; y: T; }
            type pf<T> = point<T>;
            fn pf<A, B>::m(self: point<A>) -> int32 { return 0; }
            fn main() -> int32 { return 0; }
            """
        )


def test_plain_alias_qualifier_with_args_is_not_generic():
    with pytest.raises(
        LangError, match=r"type alias 'pointf' is not generic"
    ):
        compile_ir(
            """
            struct point<T> { x: T; y: T; }
            type pointf = point<float64>;
            fn pointf<float64>::m(self: pointf) -> int32 { return 0; }
            fn main() -> int32 { return 0; }
            """
        )


def test_alias_of_an_unnameable_type_is_an_error():
    # A pointer (or array, or function) type has no bare-name spelling to
    # namespace on, so an alias of one keeps the family error -- reported
    # against the qualifier as written.
    with pytest.raises(
        LangError, match=r"no struct type 'ip' for method 'ip::m'"
    ):
        compile_ir(
            """
            type ip = int32*;
            fn ip::m(x: int32*) -> int32 { return 0; }
            fn main() -> int32 { return 0; }
            """
        )
    with pytest.raises(
        LangError, match=r"no struct type 'cb' for method 'cb::m'"
    ):
        compile_ir(
            """
            type cb = fn(int32) -> int32;
            fn cb::m(f: cb) -> int32 { return 0; }
            fn main() -> int32 { return 0; }
            """
        )


def test_private_alias_qualifier_is_access_checked(tmp_path):
    # The chase access-checks each hop: a cross-file @private alias qualifier
    # errors at the declaration...
    (tmp_path / "geo.mc").write_text(
        "struct point<T> { x: T; y: T; }\n"
        "@private type pointf = point<float64>;\n"
    )
    decl = tmp_path / "decl.mc"
    decl.write_text(
        'import "geo";\n'
        "fn pointf::m(self: point<float64>) -> int32 { return 0; }\n"
        "fn main() -> int32 { return 0; }\n"
    )
    with pytest.raises(
        LangError, match=r"type alias 'pointf' is private to geo.mc"
    ):
        run_path(decl)
    # ...and at a call.
    (tmp_path / "geo2.mc").write_text(
        "struct point<T> { x: T; y: T; }\n"
        "@private type pointf = point<float64>;\n"
        "fn point<float64>::mk() -> int32 { return 7; }\n"
    )
    call = tmp_path / "call.mc"
    call.write_text(
        'import "geo2";\n'
        "fn main() -> int32 { return pointf::mk(); }\n"
    )
    with pytest.raises(
        LangError, match=r"type alias 'pointf' is private to geo2.mc"
    ):
        run_path(call)


def test_alias_declared_generic_method_round_trips_through_mci(tmp_path):
    # A generic method travels VERBATIM in the stub -- still spelling the
    # alias -- so the recorded original qualifier must pull the (@private)
    # alias declaration in even when no parameter or return type names it.
    lib = tmp_path / "lib.mc"
    lib.write_text(
        "struct point<T> { x: T; y: T; }\n"
        "@private type pf<T> = point<T>;\n"
        "fn pf<T>::same(x: T) -> T { return x; }\n"
    )
    out = tmp_path / "lib.mci"
    assert emit_interface(lib, (tmp_path,), None, {}, out) == 0
    stub = out.read_text()
    assert "@private type pf<T> = point<T>;" in stub  # the alias travels
    assert "fn pf<T>::same(x: T) -> T" in stub  # the method, verbatim
    lib.unlink()  # force the import to resolve through the stub
    main = tmp_path / "main.mc"
    main.write_text(
        'import "lib";\n'
        "fn main() -> int32 { return point::same(42) - 42; }\n"
    )
    assert run_path(main) == 0


def test_alias_specialization_prototype_round_trips_through_mci(tmp_path):
    # A CONCRETE alias-declared method emits as a prototype under its
    # canonical family name with the qualifier annotation re-spelled
    # (`fn point<float64>::mag` -- bare `fn point::mag` would not re-parse);
    # the alias-spelled signature pulls the alias along, so the stub compiles
    # on re-import. (compile_ir, not run: a bodyless prototype's body lives
    # in the unlinked object.)
    lib = tmp_path / "lib.mc"
    lib.write_text(
        "struct point<T> { x: T; y: T; }\n"
        "type pointf = point<float64>;\n"
        "fn pointf::mag(self: pointf) -> float64 { return self.x; }\n"
    )
    out = tmp_path / "lib.mci"
    assert emit_interface(lib, (tmp_path,), None, {}, out) == 0
    stub = out.read_text()
    assert "fn point<float64>::mag(self: pointf) -> float64;" in stub  # canonical, annotated
    assert "type pointf = point<float64>;" in stub
    compile_ir(stub)  # the stub is self-contained and compiles


def test_spec_qualifier_only_type_travels_through_mci(tmp_path):
    # The stub prototype re-spells the qualifier annotation, so a type named
    # ONLY there (`fn holder<widget>::code(x: int32)` -- no parameter or
    # return names widget) must still travel: left out, the bare `widget`
    # would re-classify as a FRESH type parameter on re-parse, silently
    # turning the specialization generic.
    lib = tmp_path / "lib.mc"
    lib.write_text(
        "struct widget { id: int32; }\n"
        "struct holder<T> { v: T; }\n"
        "fn holder<widget>::code(x: int32) -> int32 { return x + 1; }\n"
    )
    out = tmp_path / "lib.mci"
    assert emit_interface(lib, (tmp_path,), None, {}, out) == 0
    stub = out.read_text()
    assert "fn holder<widget>::code(x: int32) -> int32;" in stub
    assert "struct widget" in stub  # pulled in by the qualifier annotation
    ir = compile_ir(stub)  # ...so the stub re-classifies concrete and compiles
    assert 'declare i32 @"holder::code"(i32' in ir


def test_override_pairs_across_alias_and_canonical_spellings(tmp_path):
    # @override reconciles AFTER canonicalization over resolved parameter
    # types, so an alias-spelled override replaces the canonical definition.
    (tmp_path / "base.mc").write_text(
        "struct point<T> { x: T; y: T; }\n"
        "type pointf = point<float64>;\n"
        "fn point<float64>::m(self: point<float64>) -> int32 { return 1; }\n"
    )
    main = tmp_path / "main.mc"
    main.write_text(
        'import "base";\n'
        "@override fn pointf::m(self: pointf) -> int32 { return 2; }\n"
        "fn main() -> int32 {\n"
        "    let p: pointf;\n"
        "    return point::m(p) - 2;\n"
        "}\n"
    )
    assert run_path(main) == 0


def test_undeclared_call_qualifier_keeps_its_written_name():
    # A call qualifier that chases nowhere resolves (and reports) under its
    # written name, unchanged.
    with pytest.raises(LangError, match=r"undefined function 'ghost::m'"):
        compile_ir(
            """
            fn main() -> int32 { return ghost::m(1); }
            """
        )


def test_defaulted_generic_alias_qualifier_fills_the_tail():
    # A shorter pre-`::` list fills from the alias's trailing defaults,
    # exactly as a type use does: `fn box2<float64>::code` with
    # `type box2<T, U = int32> = pair2<U, T>` specializes
    # pair2<int32, float64>...
    assert (
        run(
            """
            struct pair2<A, B> { a: A; b: B; }
            type box2<T, U = int32> = pair2<U, T>;
            fn box2<float64>::code(self: pair2<int32, float64>) -> int32 {
                return 42;
            }
            fn main() -> int32 {
                let p: pair2<int32, float64>;
                return pair2::code(p) - 42;
            }
            """
        )
        == 0
    )
    # ...and a defaulted alias SPELLING in a generic signature dealiases the
    # same way for inference: `self: box2<V>` is pair2<int32, V>, binding V
    # through the expansion (the qualifier annotates as the partial the
    # pattern implies -- its first position is pinned to the default int32).
    assert (
        run(
            """
            struct pair2<A, B> { a: A; b: B; }
            type box2<T, U = int32> = pair2<U, T>;
            fn pair2<int32, V>::grab(self: box2<V>) -> V { return self.b; }
            fn main() -> int32 {
                let p: pair2<int32, int64>;
                p.b = 42;
                return pair2::grab(p) as int32 - 42;
            }
            """
        )
        == 0
    )


def test_call_through_an_unnameable_alias_stays_undefined():
    # A call qualifier chasing into an alias of a pointer type has no
    # canonical family; the call resolves (and reports) under its written
    # name.
    with pytest.raises(LangError, match=r"undefined function 'ip::m'"):
        compile_ir(
            """
            type ip = int32*;
            fn main() -> int32 { return ip::m(1); }
            """
        )
