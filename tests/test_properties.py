"""`@property`: a method reachable through field syntax.

`s.length` calls `T::length(s)`; both `s.length` and `s.length()` reach the
same method. A `-> mut` property is an assignable lvalue, so `s.length = v`
is `T::length(s) = v`. A real field of the name shadows a property, and the
usual method machinery (inheritance, pointer auto-deref) carries through.
"""

import pytest

from mcc.errors import LangError
from helpers import compile_ir, run


# --- reads --------------------------------------------------------------------

def test_property_read_without_parens():
    # The headline: a zero-arg method reads like a field.
    assert run(
        """
        struct counter { n: int32; }
        @property fn counter::doubled(const self: counter) -> int32 {
            return self.n * 2;
        }
        fn main() -> int32 {
            let c = counter { n = 21 };
            return c.doubled;              // -> 42
        }
        """
    ) == 42


def test_both_spellings_reach_the_method():
    # @property ADDS the field spelling; the call spelling still works.
    assert run(
        """
        struct counter { n: int32; }
        @property fn counter::value(const self: counter) -> int32 {
            return self.n;
        }
        fn main() -> int32 {
            let c = counter { n = 7 };
            return c.value * 10 + c.value();   // 7*10 + 7 -> 77
        }
        """
    ) == 77


def test_property_reads_in_a_fstring(capfd):
    # The motivating case: f"{s.length}" interpolates the property.
    assert run(
        """
        import "std/io";
        import "std/stack";
        fn main() -> int32 {
            let s = stack<int32>(2);
            s.push(1);
            s.push(2);
            s.push(3);
            println(f"{s.length}");        // 3
            return 0;
        }
        """
    ) == 0
    assert capfd.readouterr().out == "3\n"


# --- mut properties are assignable lvalues ------------------------------------

def test_mut_property_is_an_assignable_lvalue():
    # A `-> mut` property returns the field's lvalue, so plain and compound
    # assignment write straight through it -- `b.value = v` is
    # `box::value(b) = v`.
    assert run(
        """
        struct box { v: int32; }
        @property fn box::value(mut self: box) -> mut int32 { return self.v; }
        fn main() -> int32 {
            let b = box { v = 5 };
            b.value = 40;                  // property write
            b.value += 2;                  // compound: 42
            return b.value;                // -> 42
        }
        """
    ) == 42


def test_write_to_read_only_property_is_rejected():
    # A non-mut property is read-only: assigning to it rejects exactly as a
    # non-mut-returning call target would.
    with pytest.raises(
        LangError,
        match=r"the call to 'box::value' does not return mut",
    ):
        compile_ir(
            """
            struct box { v: int32; }
            @property fn box::value(const self: box) -> int32 { return self.v; }
            fn main() -> int32 {
                let b = box { v = 1 };
                b.value = 9;
                return 0;
            }
            """
        )


# --- shadowing, inheritance, pointers -----------------------------------------

def test_a_real_field_shadows_a_property():
    # Field-first: a struct field of the name wins for `x.v` (and `x.v()`
    # too -- shadowing is the existing dot-call rule). The property stays
    # reachable only through its qualified form, `b::v(x)`.
    assert run(
        """
        struct b { v: int32; }
        @property fn b::v(const self: b) -> int32 { return 999; }
        fn main() -> int32 {
            let x = b { v = 7 };
            return x.v * 100 + b::v(x);    // field 7 -> 700, method 999 -> 1699
        }
        """
    ) == 1699


def test_property_inherited_through_extends():
    # A derived type reaches a base's @property through its extends chain.
    assert run(
        """
        struct base { n: int32; }
        @property fn base::doubled(const self: base) -> int32 {
            return self.n * 2;
        }
        struct derived extends base { extra: int32; }
        fn main() -> int32 {
            let d = derived { n = 5, extra = 0 };
            return d.doubled;              // inherited -> 10
        }
        """
    ) == 10


def test_property_through_pointer_and_deref():
    # A pointer receiver auto-derefs like the dot-call `p.doubled()`, and an
    # explicit `(*p)` receiver works too.
    assert run(
        """
        struct b { v: int32; }
        @property fn b::val(const self: b) -> int32 { return self.v; }
        fn main() -> int32 {
            let x = b { v = 7 };
            let p = &x;
            return p.val + (*p).val;       // 7 + 7 -> 14
        }
        """
    ) == 14


def test_generic_property_monomorphizes():
    # A @property on a generic method resolves per instantiation.
    assert run(
        """
        struct pair<T> { a: T; b: T; }
        @property fn pair<T>::first(const self: pair<T>) -> T { return self.a; }
        fn main() -> int32 {
            let p = pair<int32> { a = 9, b = 3 };
            return p.first;                // -> 9
        }
        """
    ) == 9


def test_unknown_field_still_errors_normally():
    # A non-field, non-property access keeps today's diagnostic; the property
    # fallback does not mask it.
    with pytest.raises(LangError, match=r"struct b has no field 'nope'"):
        compile_ir(
            """
            struct b { v: int32; }
            fn main() -> int32 {
                let x = b { v = 1 };
                return x.nope;
            }
            """
        )


# --- declaration-shape errors -------------------------------------------------

def test_property_on_a_free_function_is_rejected():
    with pytest.raises(
        LangError, match=r"@property only applies to a method"
    ):
        compile_ir(
            """
            struct b { v: int32; }
            @property fn free(x: b) -> int32 { return x.v; }
            fn main() -> int32 { return 0; }
            """
        )


def test_property_with_extra_parameters_is_rejected():
    with pytest.raises(
        LangError, match=r"@property method takes only its receiver"
    ):
        compile_ir(
            """
            struct b { v: int32; }
            @property fn b::at(const self: b, i: int32) -> int32 {
                return self.v;
            }
            fn main() -> int32 { return 0; }
            """
        )


def test_property_returning_void_is_rejected():
    with pytest.raises(
        LangError, match=r"@property method must return a value"
    ):
        compile_ir(
            """
            struct b { v: int32; }
            @property fn b::go(const self: b) { }
            fn main() -> int32 { return 0; }
            """
        )


def test_property_on_a_prototype_is_rejected():
    with pytest.raises(LangError, match=r"@property method needs a body"):
        compile_ir(
            """
            struct b { v: int32; }
            @property fn b::val(const self: b) -> int32;
            fn main() -> int32 { return 0; }
            """
        )


def test_property_on_a_non_function_is_rejected():
    with pytest.raises(LangError, match=r"@property only applies to methods"):
        compile_ir(
            """
            @property struct b { v: int32; }
            fn main() -> int32 { return 0; }
            """
        )
