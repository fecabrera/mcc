"""Qualified free-function methods: ``fn Type::method(...)`` + ``Type::method(...)``.

The foundational slice of the Methods/OOP roadmap item. A ``fn Type::method``
definition namespaces an ordinary function to a struct; it is called by its
explicit qualified name ``Type::method(args)``. The qualified name is a single
string (``"point::magnitude"``) everywhere in the compiler -- as the function
name, the call name, the registration key, and the LLVM symbol -- so
overloading, ``@private``, and direct-call resolution all work unchanged.

``Type::`` is purely a namespace in this slice: no ``self`` convention is
enforced. The only validation is that the qualifier names a declared struct.
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


# --- validation: the qualifier must be a declared struct ----------------------

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


def test_partial_specialization_is_an_error():
    # Some concrete, some parameter -- a partial specialization -- is rejected:
    # every struct argument must be all-concrete or all-parameter.
    with pytest.raises(
        LangError,
        match=r"partial specialization is not supported",
    ):
        compile_ir(
            """
            struct pair<A, B> { a: A; b: B; }
            fn pair<int32, U>::m(self: pair<int32, U>) -> int32 { return 0; }
            fn main() -> int32 { return 0; }
            """
        )


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
    # A specialization exports as the desugared concrete prototype
    # `fn box::tag(self: box<float64>)`. Re-parsing and re-compiling that stub
    # yields the same concrete overload (its body lives in the compiled object,
    # so the stub carries only the prototype -- not JIT-runnable on its own).
    lib = tmp_path / "lib.mc"
    lib.write_text(
        "struct box<T> { v: T; }\n"
        "fn box<T>::tag(self: box<T>) -> int32 { return 1; }\n"
        "fn box<float64>::tag(self: box<float64>) -> int32 { return 2; }\n"
    )
    out = tmp_path / "lib.mci"
    assert emit_interface(lib, (tmp_path,), None, {}, out) == 0
    stub = out.read_text()
    assert "fn box::tag(self: box<float64>) -> int32;" in stub
    # Re-parsing and re-compiling the stub round-trips: the desugared prototype
    # is the concrete box<float64> overload (a plain-symbol declaration whose
    # body lives in the object), so it re-compiles without reintroducing the
    # generic-vs-specialization collision the pre-`::` `<float64>` once caused.
    ir = compile_ir(stub)
    assert 'declare i32 @"box::tag"(%"box<float64>"' in ir


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
