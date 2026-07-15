"""Receiver-kind check: a method receiver (`self`) must be reference-shaped.

The receiver-kind ruling (SIE-100 Phase 1) formalizes the first parameter named
``self`` as a *receiver* and forbids the by-value copy receiver by construction:
a copy receiver would slice a derived value into a base prefix and can never be
a dispatch (vtable) entry. The allowed kinds are ``const self: &T`` (read),
``self: &T`` (mutate), and the pointer-class ``@nonnull self: T*``; the plain
by-value copy ``self: T`` -- and a nullable ``self: T*`` -- are rejected.

The check is *name-based*: it fires on any function whose first parameter is
named ``self``, leaving receiverless methods (``point::origin()``,
``point::of(x, y)``) untouched. Call sites are unaffected -- an explicit
qualified call passing a value to a ``const self: &T`` receiver forms the hidden
reference automatically.
"""

import pytest

from mcc.errors import LangError
from helpers import compile_ir, run


STRUCT = "struct point { x: int32; y: int32; }\n"


# --- the allowed receiver kinds -----------------------------------------------

def test_const_reference_receiver_reads():
    # `const self: &T` is the read-only receiver.
    compile_ir(STRUCT + "fn point::getx(const self: &point) -> int32 { return self.x; }")


def test_reference_receiver_mutates():
    # `self: &T` mutates through to the caller's object.
    compile_ir(STRUCT + "fn point::bump(self: &point) { self.x = self.x + 1; }")


def test_nonnull_pointer_receiver_is_allowed():
    # `@nonnull self: T*` is the pointer-class receiver -- the shape the stdlib
    # iterator producers use. (A pointer receiver is a free function, not a
    # `::` method, whose qualifier resolves the struct value.)
    compile_ir(
        STRUCT + "fn getx(@nonnull self: point*) -> int32 { return self->x; }"
    )


def test_value_receiver_reads_through_the_hidden_reference():
    # A value argument to a `const self: &T` receiver forms the reference at the
    # call site -- the qualified call, the method sugar, and the explicit `&`
    # all resolve to the same read. (Declaration-side migration only.)
    assert run(
        STRUCT
        + """
        fn point::sum(const self: &point) -> int32 { return self.x + self.y; }
        fn main() -> int32 {
            let p: point = { x = 3, y = 4 };
            return point::sum(p) + p.sum() + point::sum(&p);
        }
        """
    ) == 21


def test_receiverless_method_is_untouched():
    # A `::` method whose first parameter is not named `self` is receiverless
    # and never triggers the check.
    assert run(
        STRUCT
        + """
        fn point::of(x: int32, y: int32) -> point { return point { x = x, y = y }; }
        fn main() -> int32 { let p = point::of(2, 5); return p.x + p.y; }
        """
    ) == 7


# --- the forbidden by-value copy receiver -------------------------------------

def test_by_value_copy_receiver_is_rejected():
    with pytest.raises(
        LangError,
        match=r"a by-value copy receiver 'self' is not allowed \(it slices "
        r"derived values and can never dispatch\); use 'const self: &T' to "
        r"read or 'self: &T' to mutate",
    ):
        compile_ir(STRUCT + "fn point::getx(self: point) -> int32 { return self.x; }")


def test_const_by_value_copy_receiver_is_rejected():
    # `const` does not rescue a by-value receiver -- it is still a copy.
    with pytest.raises(LangError, match=r"a by-value copy receiver 'self'"):
        compile_ir(STRUCT + "fn point::getx(const self: point) -> int32 { return self.x; }")


def test_scalar_by_value_receiver_is_rejected_no_exemption():
    # There is no scalar exemption: `&` everywhere, even for a `char`.
    with pytest.raises(LangError, match=r"a by-value copy receiver 'self'"):
        compile_ir(
            "fn char::is_x(const self: char) -> bool { return self == 'x'; }"
        )


def test_free_function_receiver_is_name_based():
    # The check is name-based, not `::`-based: a plain free function whose first
    # parameter is named `self` is a receiver too.
    with pytest.raises(LangError, match=r"a by-value copy receiver 'self'"):
        compile_ir("fn helper(self: int32) -> int32 { return self + 1; }")


# --- the nullable pointer receiver --------------------------------------------

def test_bare_pointer_receiver_is_rejected():
    with pytest.raises(
        LangError,
        match=r"a pointer receiver 'self' must be spelled '@nonnull self: T\*' "
        r"\(a nullable receiver cannot dispatch\)",
    ):
        compile_ir(STRUCT + "fn point::getx(self: point*) -> int32 { return self.x; }")


# --- a non-receiver `self` (not first) is not checked -------------------------

def test_self_named_non_first_parameter_is_not_a_receiver():
    # `self` in a non-first position is an ordinary parameter, not a receiver,
    # so a by-value one is fine.
    assert run(
        STRUCT
        + """
        fn add(base: int32, self: int32) -> int32 { return base + self; }
        fn main() -> int32 { return add(4, 3); }
        """
    ) == 7
