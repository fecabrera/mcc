"""`own` by-value parameters: the consuming receiver `own self: T` (SIE-178).

Receiver-kind Phase 2 adds a fourth receiver kind, the consuming ``own self: T``
(a by-value *move*, not a copy): the method takes ownership of the receiver and
drops it (runs its ``destructor``) at the end of the body. More generally ``own``
marks any by-value owning parameter (``fn take(own b: box)``).

The call-site discipline mirrors the ``-> own`` return rule. A named owned local
is relinquished with an explicit ``move(x)`` -- its scheduled destructor is
cancelled and a later use is a use-after-move error; a fresh own value (a
constructor expression, an ``-> own`` call, or a dot-call's spilled rvalue
receiver) is adopted with no ``move``. ``own`` combined with a ``&`` reference
(the owned-*reference* receiver ``own self: &T``) is a later phase and rejected,
as are ``own`` on ``@extern``/``@asm`` and (for now) on generic functions.
"""

import pytest

from mcc.errors import LangError
from helpers import compile_ir, run


# A struct with a side-effecting destructor, so drops are observable via capfd.
BOX = """
import "std/io";
struct box { tag: int32; }
fn box::constructor(self: &box, tag: int32) { self.tag = tag; }
fn box::destructor(self: &box) { println(f"drop {self.tag}"); }
"""


# --- the consuming receiver, callee side -------------------------------------

def test_own_self_drops_at_end_of_body(capfd):
    # `own self: T` takes ownership and drops the receiver when the body ends
    # (before the return value flows out), not at the caller's scope exit.
    assert run(
        BOX
        + """
        fn box::consume(own self: box) -> int32 {
            println(f"consume {self.tag}");
            return self.tag;
        }
        fn main() -> int32 {
            let b = box(7);
            let v = box::consume(move(b));
            println(f"got {v}");
            return 0;
        }
        """
    ) == 0
    # The destructor runs inside consume (between "consume" and "got"),
    # exactly once -- no second drop at main's scope exit.
    assert capfd.readouterr().out == "consume 7\ndrop 7\ngot 7\n"


def test_move_transfers_ownership_no_double_drop(capfd):
    # The moved-from local's scheduled destructor is cancelled: only the
    # callee drops the value. A double-drop would print "drop 7" twice.
    assert run(
        BOX
        + """
        fn box::sink(own self: box) { }
        fn main() -> int32 {
            let b = box(7);
            box::sink(move(b));
            return 0;
        }
        """
    ) == 0
    assert capfd.readouterr().out == "drop 7\n"


def test_own_free_function_parameter_consumes(capfd):
    # `own` is a general by-value owning marker, not receiver-only.
    assert run(
        BOX
        + """
        fn drain(own b: box) -> int32 { return b.tag; }
        fn main() -> int32 {
            let b = box(5);
            return drain(move(b));
        }
        """
    ) == 5
    assert capfd.readouterr().out == "drop 5\n"


def test_return_self_transfers_through_own_return(capfd):
    # A consuming step that hands the value back (`-> own`, `return self`)
    # cancels its own drop on the return path; a builder chain of such steps
    # over fresh temporaries drops the value once, at the terminal step.
    assert run(
        BOX
        + """
        fn box::plus(own self: box, n: int32) -> own box {
            self.tag = self.tag + n;
            return self;
        }
        fn box::total(own self: box) -> int32 { return self.tag; }
        fn main() -> int32 {
            return box(1).plus(3).plus(4).total();
        }
        """
    ) == 8
    assert capfd.readouterr().out == "drop 8\n"


# --- the call-site relinquish discipline -------------------------------------

def test_fresh_constructor_argument_needs_no_move(capfd):
    # A constructor expression is a fresh owned value the callee adopts.
    assert run(
        BOX
        + """
        fn box::consume(own self: box) -> int32 { return self.tag; }
        fn main() -> int32 { return box::consume(box(9)); }
        """
    ) == 9
    assert capfd.readouterr().out == "drop 9\n"


def test_dot_call_on_temporary_receiver_adopts(capfd):
    # The dot-call sugar spills an rvalue receiver to a hidden local; it is a
    # fresh owned temporary adopted by the consuming method.
    assert run(
        BOX
        + """
        fn box::consume(own self: box) -> int32 { return self.tag; }
        fn main() -> int32 { return box(4).consume(); }
        """
    ) == 4
    assert capfd.readouterr().out == "drop 4\n"


def test_bare_owned_local_argument_requires_move():
    # Relinquishing a named owned local must be visible: a bare argument is
    # refused, directing to move(x).
    with pytest.raises(
        LangError,
        match=r"'b' is an owned value; passing it to an own parameter "
        r"relinquishes it -- spell the transfer move\(b\)",
    ):
        compile_ir(
            BOX
            + """
            fn box::consume(own self: box) -> int32 { return self.tag; }
            fn main() -> int32 {
                let b = box(7);
                return box::consume(b);
            }
            """
        )


def test_bare_owned_local_dot_call_requires_move():
    # The dot-call spelling on a named owned local is refused the same way
    # (there is no place to write move on a dot receiver, so the qualified
    # move form is the spelling).
    with pytest.raises(
        LangError,
        match=r"'b' is an owned value; passing it to an own parameter",
    ):
        compile_ir(
            BOX
            + """
            fn box::consume(own self: box) -> int32 { return self.tag; }
            fn main() -> int32 {
                let b = box(7);
                return b.consume();
            }
            """
        )


def test_use_after_move_is_diagnosed():
    # After a local is moved into an own parameter, naming it again is an error.
    with pytest.raises(
        LangError,
        match=r"'b' was moved into an own parameter and cannot be used again",
    ):
        compile_ir(
            BOX
            + """
            fn box::consume(own self: box) -> int32 { return self.tag; }
            fn main() -> int32 {
                let b = box(7);
                let v = box::consume(move(b));
                return b.tag;
            }
            """
        )


def test_a_rebound_name_in_a_sibling_scope_is_not_moved(capfd):
    # Move tracking is per-binding: a moved local leaves scope with its block,
    # so the same name rebound in a sibling block is a fresh, un-moved value.
    assert run(
        BOX
        + """
        fn box::consume(own self: box) -> int32 { return self.tag; }
        fn main() -> int32 {
            { let a = box(1); box::consume(move(a)); }
            { let a = box(2); box::consume(move(a)); }
            return 0;
        }
        """
    ) == 0
    assert capfd.readouterr().out == "drop 1\ndrop 2\n"


def test_a_moved_outer_local_stays_moved_past_a_nested_block():
    # A moved *outer* local is not un-moved by the inner block ending.
    with pytest.raises(
        LangError,
        match=r"'a' was moved into an own parameter and cannot be used again",
    ):
        compile_ir(
            BOX
            + """
            fn box::consume(own self: box) -> int32 { return self.tag; }
            fn main() -> int32 {
                let a = box(1);
                { box::consume(move(a)); }
                return a.tag;
            }
            """
        )


def test_double_move_of_the_same_local_is_diagnosed():
    with pytest.raises(
        LangError,
        match=r"'b' was moved into an own parameter and cannot be used again",
    ):
        compile_ir(
            BOX
            + """
            fn box::consume(own self: box) -> int32 { return self.tag; }
            fn main() -> int32 {
                let b = box(7);
                box::consume(move(b));
                box::consume(move(b));
                return 0;
            }
            """
        )


def test_non_owned_value_cannot_be_consumed():
    # Moving out of a field is not a value this frame owns to give away.
    with pytest.raises(
        LangError,
        match=r"an own parameter takes ownership of its argument, but this "
        r"value is not one this frame owns to give away",
    ):
        compile_ir(
            BOX
            + """
            struct pair { a: box; b: box; }
            fn box::consume(own self: box) -> int32 { return self.tag; }
            fn main() -> int32 {
                let p: pair = { a = box(1), b = box(2) };
                return box::consume(move(p.a));
            }
            """
        )


# --- a destructor-less own parameter is a no-op ------------------------------

def test_own_over_a_destructorless_type_is_a_noop():
    # No destructor family: `own` schedules nothing and requires no move (there
    # is no ownership to relinquish); it passes by value like a plain parameter.
    assert run(
        """
        struct pt { x: int32; }
        fn pt::get(own const self: pt) -> int32 { return self.x; }
        fn main() -> int32 {
            let p: pt = { x = 6 };
            return pt::get(p);
        }
        """
    ) == 6


def test_own_scalar_parameter_is_a_noop():
    assert run(
        """
        fn dbl(own n: int32) -> int32 { return n + n; }
        fn main() -> int32 { return dbl(21); }
        """
    ) == 42


# --- rejections --------------------------------------------------------------

def test_own_self_reference_is_rejected_phase_three():
    # `own self: &T` (the owned-reference receiver) is a later phase.
    with pytest.raises(
        LangError,
        match=r"a parameter cannot be both own and a reference.*owned-"
        r"reference receiver `own self: &T` arrives in a later phase",
    ):
        compile_ir(
            "struct box { tag: int32; }\n"
            "fn box::peek(own self: &box) -> int32 { return self.tag; }"
        )


def test_own_const_reference_is_rejected_phase_three():
    with pytest.raises(
        LangError, match=r"a parameter cannot be both own and a reference"
    ):
        compile_ir(
            "struct box { tag: int32; }\n"
            "fn box::peek(own const self: &box) -> int32 { return self.tag; }"
        )


def test_own_on_extern_is_rejected():
    with pytest.raises(
        LangError,
        match=r"own parameters are not allowed on @extern functions",
    ):
        compile_ir(
            "struct box { tag: int32; }\n"
            "@extern fn c_take(own b: box);"
        )


def test_own_on_asm_is_rejected():
    with pytest.raises(
        LangError,
        match=r"own parameters are not allowed on @asm functions",
    ):
        compile_ir('@asm fn t(own n: int32) -> int32 { "mov {}, {}" }')


def test_own_on_generic_function_is_rejected():
    with pytest.raises(
        LangError,
        match=r"own parameters are not yet supported on generic functions",
    ):
        compile_ir(
            "struct box<T> { v: T; }\n"
            "fn box<T>::consume(own self: box<T>) -> int32 { return 0; }"
        )


def test_own_on_overloaded_function_is_rejected():
    # An overloaded own family is dispatched on the generic path, which
    # marshals arguments before the winner is known; sound own-transfer there
    # is a follow-up, so it is rejected at the call site (uniformly over the
    # set) rather than risk a double free.
    with pytest.raises(
        LangError,
        match=r"own parameters are not yet supported on overloaded functions",
    ):
        compile_ir(
            "struct box { tag: int32; }\n"
            "fn box::constructor(self: &box, t: int32) { self.tag = t; }\n"
            "fn box::destructor(self: &box) { }\n"
            "fn box::eat(own self: box) -> int32 { return self.tag; }\n"
            "fn box::eat(own self: box, n: int32) -> int32 { return self.tag + n; }\n"
            "fn main() -> int32 { return box::eat(box(3)); }"
        )


def test_own_on_collecting_parameter_is_rejected():
    with pytest.raises(
        LangError,
        match=r"'args\.\.\.' cannot take const, own, a reference",
    ):
        compile_ir("fn f(own args...) { }")
