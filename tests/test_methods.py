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
