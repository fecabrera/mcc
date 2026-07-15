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
as is ``own`` on ``@extern``/``@asm``.

SIE-180 extends the same discipline to every non-direct call path -- generic
functions, methods of generic structs, overloaded sets, and indirect
(function-pointer) calls. The generic/overloaded path runs the move-in at
winner emission, on the pre-evaluated arguments; an overload set must agree on
which positions are ``own``. The indirect path carries the ``own`` positions
on the function-pointer type (``fn(own box)``), distinct from ``fn(box)``.
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


# --- own on the generic / overloaded path (SIE-180) --------------------------

def test_own_on_generic_free_function_consumes(capfd):
    # A generic function may take an own parameter: the move-in discipline runs
    # at winner emission, on the pre-evaluated argument (a fresh value adopted,
    # a named local relinquished with move(x)). One drop per value, in the
    # callee.
    assert run(
        """
        import "std/io";
        struct box<T> { v: T; }
        fn box<T>::constructor(self: &box<T>, v: T) { self.v = v; }
        fn box<T>::destructor(self: &box<T>) { println("drop"); }
        fn drain<T>(own b: box<T>) -> T { return b.v; }
        fn main() -> int32 {
            let r1 = drain(box<int32>(7));   // fresh, inferred
            let b = box<int32>(9);
            let r2 = drain<int32>(move(b));  // named local via move
            println(f"{r1} {r2}");
            return 0;
        }
        """
    ) == 0
    assert capfd.readouterr().out == "drop\ndrop\n7 9\n"


def test_own_consuming_method_on_a_generic_struct(capfd):
    # The container-consuming case: a method of a generic struct takes
    # `own self`. A fresh temporary receiver (a dot-call rvalue) is adopted;
    # a named local uses the qualified move form. The receiver drops once, in
    # the method, before the sum flows out.
    assert run(
        """
        import "std/io";
        struct vec<T> { a: T; b: T; }
        fn vec<T>::constructor(self: &vec<T>, a: T, b: T) { self.a = a; self.b = b; }
        fn vec<T>::destructor(self: &vec<T>) { println("drop vec"); }
        fn vec<T>::into_sum(own self: vec<T>) -> T { return self.a + self.b; }
        fn main() -> int32 {
            let s1 = vec<int32>(3, 4).into_sum();       // fresh temp, adopted
            let v = vec<int32>(10, 20);
            let s2 = vec<int32>::into_sum(move(v));      // named local, moved
            println(f"{s1} {s2}");
            return 0;
        }
        """
    ) == 0
    assert capfd.readouterr().out == "drop vec\ndrop vec\n7 30\n"


def test_own_generic_bare_local_requires_move():
    # The relinquish must be visible on the generic path too: a bare owned
    # local at an own slot is refused, directing to move(x).
    with pytest.raises(
        LangError,
        match=r"'b' is an owned value; passing it to an own parameter "
        r"relinquishes it -- spell the transfer move\(b\)",
    ):
        compile_ir(
            """
            struct box<T> { v: T; }
            fn box<T>::constructor(self: &box<T>, v: T) { self.v = v; }
            fn box<T>::destructor(self: &box<T>) { }
            fn drain<T>(own b: box<T>) -> T { return b.v; }
            fn main() -> int32 {
                let b = box<int32>(7);
                return drain(b);
            }
            """
        )


def test_own_generic_use_after_move_is_diagnosed():
    # A move into a generic own parameter marks the source moved; a later read
    # is a use-after-move error, exactly as on the direct path.
    with pytest.raises(
        LangError,
        match=r"'b' was moved into an own parameter and cannot be used again",
    ):
        compile_ir(
            """
            struct box<T> { v: T; }
            fn box<T>::constructor(self: &box<T>, v: T) { self.v = v; }
            fn box<T>::destructor(self: &box<T>) { }
            fn drain<T>(own b: box<T>) -> int32 { return 0; }
            fn main() -> int32 {
                let b = box<int32>(7);
                drain<int32>(move(b));
                return b.v;
            }
            """
        )


def test_own_on_overloaded_function_consumes(capfd):
    # An overloaded own family dispatches on the generic path; each winner's
    # own positions consume their argument. The members agree on the own
    # positions (slot 0 in both), so the caller contract is unambiguous.
    assert run(
        BOX
        + """
        fn box::eat(own self: box) -> int32 { return self.tag; }
        fn box::eat(own self: box, n: int32) -> int32 { return self.tag + n; }
        fn main() -> int32 {
            println(f"{box::eat(box(3))} {box::eat(box(5), 100)}");
            return 0;
        }
        """
    ) == 0
    assert capfd.readouterr().out == "drop 3\ndrop 5\n3 105\n"


def test_own_overload_set_must_agree_on_positions():
    # A set mixing a consuming member and a copying one at the same name has no
    # single call contract (a call site cannot know whether to relinquish), so
    # it is rejected. (Reachable with free functions -- a by-value copy
    # *receiver* is already barred by the receiver-kind rule.)
    with pytest.raises(
        LangError,
        match=r"own parameters must agree across an overload set: every "
        r"member of 'eat' must mark the same parameter positions own",
    ):
        compile_ir(
            "struct box { tag: int32; }\n"
            "fn box::constructor(self: &box, t: int32) { self.tag = t; }\n"
            "fn box::destructor(self: &box) { }\n"
            "fn eat(own b: box) -> int32 { return b.tag; }\n"
            "fn eat(b: box, n: int32) -> int32 { return b.tag + n; }\n"
            "fn main() -> int32 { return eat(box(3)); }"
        )


def test_own_on_collecting_parameter_is_rejected():
    with pytest.raises(
        LangError,
        match=r"'args\.\.\.' cannot take const, own, a reference",
    ):
        compile_ir("fn f(own args...) { }")


# --- own on the indirect (function-value) path (SIE-180) ---------------------

def test_own_function_value_consumes_through_indirect_call(capfd):
    # A function with own parameters IS a first-class value: its type carries
    # the move-in contract (`fn(own box) -> int32`), and a call through the
    # value runs the same discipline as a direct call -- a fresh own value is
    # adopted, a named owned local relinquished with move(x). The callee drops
    # each once, so a double-drop would print "drop" twice per value.
    assert run(
        BOX
        + """
        fn drain(own b: box) -> int32 { return b.tag; }
        fn main() -> int32 {
            let f = drain;             // inferred fn(own box) -> int32
            let r1 = f(box(7));        // fresh own value, adopted
            let b = box(9);
            let r2 = f(move(b));       // named owned local via move(x)
            println(f"{r1} {r2}");
            return 0;
        }
        """
    ) == 0
    assert capfd.readouterr().out == "drop 7\ndrop 9\n7 9\n"


def test_own_function_value_bare_local_requires_move():
    # The relinquish must be visible through the pointer too: a bare owned
    # local is refused, directing to move(x), exactly as on the direct path.
    with pytest.raises(
        LangError,
        match=r"'b' is an owned value; passing it to an own parameter "
        r"relinquishes it -- spell the transfer move\(b\)",
    ):
        compile_ir(
            BOX
            + """
            fn drain(own b: box) -> int32 { return b.tag; }
            fn main() -> int32 {
                let f = drain;
                let b = box(7);
                return f(b);
            }
            """
        )


def test_own_function_value_use_after_move_is_diagnosed():
    # A move through a function value marks the source moved, so a later read
    # is a use-after-move error, exactly as on the direct path.
    with pytest.raises(
        LangError,
        match=r"'b' was moved into an own parameter and cannot be used again",
    ):
        compile_ir(
            BOX
            + """
            fn drain(own b: box) -> int32 { return b.tag; }
            fn main() -> int32 {
                let f = drain;
                let b = box(7);
                f(move(b));
                return b.tag;
            }
            """
        )


def test_own_is_part_of_a_function_type():
    # `fn(own box)` and `fn(box)` are distinct calling conventions: the first
    # transfers ownership (the callee drops), the second copies. Neither
    # coerces to the other -- dropping the marker would double-free, adding it
    # would leak -- so an explicit slot of the plain type refuses the value.
    src = (
        "struct box { tag: int32; }\n"
        "fn box::constructor(self: &box, t: int32) { self.tag = t; }\n"
        "fn box::destructor(self: &box) { }\n"
        "fn drain(own b: box) -> int32 { return b.tag; }\n"
    )
    with pytest.raises(
        LangError,
        match=r"expected fn\(box\) -> int32, got fn\(own box\) -> int32 \(an "
        r"own parameter moves ownership in and the callee drops it.*"
        r"not convertible\)",
    ):
        compile_ir(
            src
            + "fn main() -> int32 { let g: fn(box) -> int32 = drain; return 0; }"
        )
    # The reverse -- a plain function into an `own` slot -- is refused too.
    plain = (
        "struct box { tag: int32; }\n"
        "fn keep(b: box) -> int32 { return b.tag; }\n"
    )
    with pytest.raises(
        LangError, match=r"expected fn\(own box\) -> int32, got fn\(box\) -> int32"
    ):
        compile_ir(
            plain
            + "fn main() -> int32 { let g: fn(own box) -> int32 = keep; "
            "return 0; }"
        )


def test_own_callback_field_consumes(capfd):
    # A struct field of function-pointer type carries the own contract, so a
    # call through the field consumes its argument like any own call.
    assert run(
        BOX
        + """
        struct sink { handler: fn(own box) -> int32; }
        fn drain(own b: box) -> int32 { return b.tag; }
        fn main() -> int32 {
            let s: sink = { handler = drain };
            println(f"{s.handler(box(4))}");
            return 0;
        }
        """
    ) == 0
    assert capfd.readouterr().out == "drop 4\n4\n"


def test_own_reference_parameter_in_a_function_type_is_rejected():
    # `fn(own &T)` is the owned-*reference* parameter -- a later phase, exactly
    # as on the declaration side.
    with pytest.raises(
        LangError,
        match=r"a parameter cannot be both own and a reference.*owned-"
        r"reference receiver `own self: &T` arrives in a later phase",
    ):
        compile_ir(
            "struct box { tag: int32; }\n"
            "fn f(cb: fn(own &box) -> int32) -> int32 { return 0; }"
        )
