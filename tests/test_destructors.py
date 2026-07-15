"""Destructors: automatic cleanup of stack-constructed values.

When a type declares (or inherits) a ``T::destructor`` family, the
constructor-sugar let schedules cleanup on the enclosing block's defer
scope::

    let p = point<float64>();
    // == let p: point<float64>;
    //    point<float64>::constructor(p);   (when a member claims the call)
    //    defer point<float64>::destructor(p);

ONLY the constructor-sugar let triggers -- ``let t = T(args);`` and
``let t = T();`` (explicit or implicit empty constructor alike). Manual
construction (``let t: T;`` plus a constructor call), struct-literal lets,
copies, and assignments never schedule cleanup: they are the documented
opt-out spellings. The scheduled call shares the ordinary defer machinery
verbatim -- LIFO with explicit defers, reverse construction order, per
iteration in loops, early return/break/continue/try-propagation unwinding --
and destruction ignores a ``const`` view (scope teardown is not user
mutation; a user-written call on a const value still errors). Returning or
emitting the whole auto-destructed local is a hard error (the copy would
escape its own cleanup); returning the constructor expression directly, a
field escape, or manual construction are the escape hatches. The dot
spelling of the two semantic method names is refused outright
(``t.destructor()`` -- use the qualified form; see test_dot_calls.py), and
manually calling ``T::destructor(t)`` on an auto-destructed value compiles
and double-destroys -- undefined behavior, exactly like a C double-free.
"""

import re

import pytest

from mcc.driver import STDLIB_DIR, emit_interface
from mcc.errors import LangError
from helpers import compile_ir, run, run_path


# A non-generic resource type with a deterministic destructor trace.
RES = """
struct res { id: int32; }
fn res::constructor(self: &res, id: int32) { self.id = id; }
fn res::destructor(self: &res) { println(f"drop {self.id}"); }
"""

# The same shape with a silent destructor, for compile-only error tests
# (no ``import "std/io"`` needed).
RES_QUIET = """
struct res { id: int32; }
fn res::constructor(self: &res, id: int32) { self.id = id; }
fn res::destructor(self: &res) { }
"""

# The acceptance shape: a generic struct, both methods, an f-string body.
POINT = """
struct point<T> { x: T; y: T; }
fn point<T>::constructor(self: &point<T>, x: T, y: T) {
    self.x = x; self.y = y;
}
fn point<T>::destructor(self: &point<T>) {
    println(f"destroying point<T>({self.x}, {self.y})");
}
"""


# --- the driving use case ------------------------------------------------------


def test_explicit_ctor_let_destroys_at_scope_exit(capfd):
    assert run(
        'import "std/io";'
        + RES
        + """
        fn main() -> int32 {
            let r = res(7);
            println("body");
            return 0;
        }
        """
    ) == 0
    assert capfd.readouterr().out == "body\ndrop 7\n"


def test_acceptance_shape_generic_ctor_fstring_dtor(capfd):
    # The acceptance program's shape, with deterministic fields (explicit
    # constructor arguments) instead of relying on a zeroed fresh stack.
    assert run(
        'import "std/io";'
        + POINT
        + """
        fn main() -> int32 {
            let p = point<float64>(1.5, 2.5);
            return 0;
        }
        """
    ) == 0
    assert capfd.readouterr().out == "destroying point<T>(1.500000, 2.500000)\n"


def test_implicit_empty_ctor_schedules_the_destructor(capfd):
    # `let p = T();` where no member claims the zero-argument call: the slot
    # is `let p: T;` (uninitialized), and the destructor is still scheduled.
    # Fields are set before scope exit, so the trace is deterministic.
    assert run(
        'import "std/io";'
        + POINT
        + """
        fn main() -> int32 {
            let p = point<float64>();
            p.x = 4.0; p.y = 8.0;
            return 0;
        }
        """
    ) == 0
    assert capfd.readouterr().out == "destroying point<T>(4.000000, 8.000000)\n"


def test_mutations_after_construction_are_visible_to_the_destructor(capfd):
    assert run(
        'import "std/io";'
        + RES
        + """
        fn main() -> int32 {
            let r = res(1);
            r.id = 99;
            return 0;
        }
        """
    ) == 0
    assert capfd.readouterr().out == "drop 99\n"


def test_generic_instantiations_destroy_separately(capfd):
    # One destructor instance per instantiation, each over its own fields.
    assert run(
        'import "std/io";'
        + POINT
        + """
        fn main() -> int32 {
            let a = point<int32>(1, 2);
            let b = point<float64>(0.5, 0.5);
            return 0;
        }
        """
    ) == 0
    assert capfd.readouterr().out == (
        "destroying point<T>(0.500000, 0.500000)\n"
        "destroying point<T>(1, 2)\n"
    )


# --- ordering: the shared defer machinery ---------------------------------------


def test_lifo_with_explicit_defers(capfd):
    # Reverse construction order, interleaved with explicit defers -- one
    # defer stack, strictly LIFO.
    assert run(
        'import "std/io";'
        + RES
        + """
        fn main() -> int32 {
            let a = res(1);
            defer println("explicit");
            let b = res(2);
            return 0;
        }
        """
    ) == 0
    assert capfd.readouterr().out == "drop 2\nexplicit\ndrop 1\n"


def test_nested_block_destroys_at_its_own_exit(capfd):
    assert run(
        'import "std/io";'
        + RES
        + """
        fn main() -> int32 {
            let outer = res(1);
            {
                let inner = res(2);
            }
            println("after block");
            return 0;
        }
        """
    ) == 0
    assert capfd.readouterr().out == "drop 2\nafter block\ndrop 1\n"


def test_loop_body_destroys_per_iteration(capfd):
    assert run(
        'import "std/io";'
        + RES
        + """
        fn main() -> int32 {
            let i: int32 = 0;
            while (i < 3) {
                let r = res(i);
                i += 1;
            }
            return 0;
        }
        """
    ) == 0
    assert capfd.readouterr().out == "drop 0\ndrop 1\ndrop 2\n"


def test_early_return_unwinds(capfd):
    assert run(
        'import "std/io";'
        + RES
        + """
        fn work(flag: int32) -> int32 {
            let r = res(5);
            if (flag == 1) {
                println("early");
                return 1;
            }
            return 0;
        }
        fn main() -> int32 { return work(1) - 1; }
        """
    ) == 0
    assert capfd.readouterr().out == "early\ndrop 5\n"


def test_break_unwinds_the_iteration(capfd):
    assert run(
        'import "std/io";'
        + RES
        + """
        fn main() -> int32 {
            let i: int32 = 0;
            while (i < 10) {
                let r = res(i);
                if (i == 1) { break; }
                i += 1;
            }
            println("after");
            return 0;
        }
        """
    ) == 0
    assert capfd.readouterr().out == "drop 0\ndrop 1\nafter\n"


def test_continue_unwinds_the_iteration(capfd):
    assert run(
        'import "std/io";'
        + RES
        + """
        fn main() -> int32 {
            let i: int32 = 0;
            while (i < 2) {
                let r = res(i);
                i += 1;
                continue;
            }
            return 0;
        }
        """
    ) == 0
    assert capfd.readouterr().out == "drop 0\ndrop 1\n"


def test_try_propagation_unwinds(capfd):
    # `try f();` propagating an error is an early return: the enclosing
    # scope's automatic destructor runs before the error leaves.
    assert run(
        'import "std/io";'
        + RES
        + """
        error my_error { BOOM }
        fn fail() -> result<my_error> { return error(my_error::BOOM); }
        fn work() -> result<my_error> {
            let r = res(3);
            try fail();
            println("unreached");
            return ok();
        }
        fn main() -> int32 {
            try work() except (err) { println("handled"); };
            return 0;
        }
        """
    ) == 0
    assert capfd.readouterr().out == "drop 3\nhandled\n"


# --- inheritance -----------------------------------------------------------------


def test_inherited_destructor_auto_defers_on_the_derived_value(capfd):
    # `tagged` declares no destructor of its own: the merged family resolves
    # the base's, receiver-only upcast included.
    assert run(
        'import "std/io";'
        + RES
        + """
        struct tagged extends res { tag: int32; }
        fn tagged::constructor(self: &tagged, id: int32, tag: int32) {
            res::constructor(self, id);
            self.tag = tag;
        }
        fn main() -> int32 {
            let t = tagged(7, 1);
            return 0;
        }
        """
    ) == 0
    assert capfd.readouterr().out == "drop 7\n"


def test_derived_destructor_chains_the_base_manually(capfd):
    # Base cleanup is MANUAL, mirroring constructor chaining: the derived
    # destructor ends its body with the qualified base call. Only the
    # derived member is scheduled automatically.
    assert run(
        'import "std/io";'
        + RES
        + """
        struct tagged extends res { tag: int32; }
        fn tagged::constructor(self: &tagged, id: int32, tag: int32) {
            res::constructor(self, id);
            self.tag = tag;
        }
        fn tagged::destructor(self: &tagged) {
            println(f"drop tag {self.tag}");
            res::destructor(self);
        }
        fn main() -> int32 {
            let t = tagged(7, 2);
            return 0;
        }
        """
    ) == 0
    assert capfd.readouterr().out == "drop tag 2\ndrop 7\n"


# --- the trigger surface is the constructor-sugar let only ----------------------


def test_manual_construction_does_not_schedule(capfd):
    # `let r: res; res::constructor(r, ...)` -- the documented opt-out: the
    # user owns cleanup (or its absence).
    assert run(
        'import "std/io";'
        + RES
        + """
        fn main() -> int32 {
            let r: res;
            res::constructor(r, 9);
            println("end");
            return 0;
        }
        """
    ) == 0
    assert capfd.readouterr().out == "end\n"


def test_struct_literal_let_does_not_schedule(capfd):
    assert run(
        'import "std/io";'
        + RES
        + """
        fn main() -> int32 {
            let r = res{id = 9};
            println("end");
            return 0;
        }
        """
    ) == 0
    assert capfd.readouterr().out == "end\n"


def test_copy_does_not_schedule(capfd):
    # `let b = a;` is a bitwise copy: only the constructed original is
    # destroyed, once.
    assert run(
        'import "std/io";'
        + RES
        + """
        fn main() -> int32 {
            let a = res(4);
            let b = a;
            return 0;
        }
        """
    ) == 0
    assert capfd.readouterr().out == "drop 4\n"


def test_any_annotated_ctor_let_does_not_schedule(capfd):
    # An annotation that coerces the constructed value away binds a copy,
    # not the constructed slot: `let x: any = handle(5);` boxes the scalar
    # and schedules nothing. (A struct into an owning `any` is already
    # rejected by the boxing rules before the question arises.)
    assert run(
        """
        import "std/io";
        type handle = int32;
        fn int32::constructor(self: &int32, v: int32) { self = v; }
        fn int32::destructor(self: &int32) { println(f"drop {self}"); }
        fn main() -> int32 {
            let x: any = handle(5);
            println("end");
            return 0;
        }
        """
    ) == 0
    assert capfd.readouterr().out == "end\n"


def test_no_destructor_family_means_zero_ir():
    # Cost when no destructor exists: dictionary lookups only. The function
    # body carries no call at all beyond the constructor's.
    ir_text = compile_ir(
        """
        struct plain { n: int32; }
        fn plain::constructor(self: &plain, n: int32) { self.n = n; }
        fn main() -> int32 {
            let p = plain(3);
            return p.n - 3;
        }
        """
    )
    body = ir_text.split('define i32 @"main"()')[1]
    assert "destructor" not in ir_text
    assert body.count("call") == 1  # the constructor, nothing else


# --- const views -----------------------------------------------------------------


def test_const_viewed_let_is_still_destroyed(capfd):
    # Destruction is scope teardown, not user mutation: the const view does
    # not suppress (or const-reject) the synthesized mut-receiver call.
    assert run(
        'import "std/io";'
        + RES
        + """
        fn main() -> int32 {
            let r: const res = res(6);
            println("body");
            return 0;
        }
        """
    ) == 0
    assert capfd.readouterr().out == "body\ndrop 6\n"


def test_user_destructor_call_on_const_still_errors():
    # The bypass is exactly the synthesized call: a user-written qualified
    # `res::destructor(r)` on a const view keeps the ordinary mut-receiver
    # error. (The dot spelling is refused before any mut check -- see
    # test_dot_calls.py.)
    with pytest.raises(
        LangError, match=r"cannot pass a read-only const res as a reference argument"
    ):
        compile_ir(
            RES_QUIET
            + """
            fn main() -> int32 {
                let r: const res = res(1);
                res::destructor(r);
                return 0;
            }
            """
        )


# --- escapes ---------------------------------------------------------------------


def test_returning_the_auto_destructed_local_is_an_error():
    with pytest.raises(
        LangError,
        match=re.escape(
            "cannot return 'r': its automatic destructor runs as the return "
            "unwinds this scope, so the returned copy would escape its own "
            "cleanup; declare the function `-> own` to transfer ownership, "
            "return the constructor expression directly, or construct "
            "manually (an uninitialized let plus a constructor call) and "
            "manage cleanup yourself"
        ),
    ):
        compile_ir(
            RES_QUIET
            + """
            fn make() -> res {
                let r = res(1);
                return r;
            }
            fn main() -> int32 { return 0; }
            """
        )


def test_emitting_the_auto_destructed_local_is_an_error():
    with pytest.raises(
        LangError,
        match=re.escape(
            "cannot emit 'r': its automatic destructor runs as the emit "
            "unwinds the block, so the emitted copy would escape its own "
            "cleanup; emit the constructor expression directly, or construct "
            "manually (an uninitialized let plus a constructor call) and "
            "manage cleanup yourself"
        ),
    ):
        compile_ir(
            RES_QUIET
            + """
            fn main() -> int32 {
                let x = { let r = res(1); emit r; };
                return 0;
            }
            """
        )


def test_emitting_an_outer_local_stays_legal(capfd):
    # A local from OUTSIDE the block expression survives the emit: emitting
    # it is an ordinary copy (exactly `let x = r;`), not an escape.
    assert run(
        'import "std/io";'
        + RES
        + """
        fn main() -> int32 {
            let r = res(2);
            let x = { emit r; };
            println(f"got {x.id}");
            return 0;
        }
        """
    ) == 0
    assert capfd.readouterr().out == "got 2\ndrop 2\n"


def test_returning_the_constructor_expression_is_the_hatch(capfd):
    # `return res(1);` -- an expression-position temporary owns no automatic
    # cleanup (v1: only the let form does), so the value moves out freely.
    assert run(
        'import "std/io";'
        + RES
        + """
        fn make() -> res { return res(8); }
        fn main() -> int32 {
            let r = make();
            println(f"made {r.id}");
            return 0;
        }
        """
    ) == 0
    # `let r = make();` is a copy-binding let, not constructor sugar: no
    # cleanup is scheduled for it either.
    assert capfd.readouterr().out == "made 8\n"


def test_field_escape_is_not_caught(capfd):
    # `return r.data` is allowed: the language does not track interior
    # ownership (documented).
    assert run(
        'import "std/io";'
        + RES
        + """
        fn pick() -> int32 {
            let r = res(3);
            return r.id;
        }
        fn main() -> int32 { return pick() - 3; }
        """
    ) == 0
    assert capfd.readouterr().out == "drop 3\n"


def test_return_of_a_shadowing_non_destructed_local_is_legal(capfd):
    # The guard is keyed by slot, not name: a shadowing let of the same name
    # returns freely, and the outer value is still destroyed by the unwind.
    assert run(
        'import "std/io";'
        + RES
        + """
        fn work() -> int32 {
            let r = res(1);
            {
                let r: int32 = 42;
                return r;
            }
        }
        fn main() -> int32 { return work() - 42; }
        """
    ) == 0
    assert capfd.readouterr().out == "drop 1\n"


# --- double destruction is the user's problem (UB, like C) ------------------------


def test_manual_call_alongside_the_automatic_one_compiles_and_runs_twice(capfd):
    # No suppression magic, no warning (v1): the manual qualified call (the
    # only reachable manual spelling -- the dot form is refused) runs, then
    # the scheduled one runs again. Undefined behavior by contract -- this
    # test only pins that it compiles and that both calls execute.
    assert run(
        'import "std/io";'
        + RES
        + """
        fn main() -> int32 {
            let r = res(5);
            res::destructor(r);
            return 0;
        }
        """
    ) == 0
    assert capfd.readouterr().out == "drop 5\ndrop 5\n"


# --- resolution is ordinary ---------------------------------------------------------


def test_destructor_needing_extra_arguments_errors_at_the_let():
    # Dumb desugar: the family exists, so the receiver-only call is
    # scheduled and ordinary resolution reports the arity (positions count
    # the hidden receiver, as at any method call). Overloads with extra
    # parameters stay manually callable; they just cannot be automatic.
    with pytest.raises(
        LangError,
        match=re.escape("line 5: 'big::destructor' expects 2 argument(s), got 1"),
    ):
        compile_ir(
            """
struct big { id: int32; }
fn big::constructor(self: &big, id: int32) { self.id = id; }
fn big::destructor(self: &big, flag: int32) { }
fn main() -> int32 { let b = big(1); return 0; }
"""
        )


def test_cross_module_destructor_runs(capfd, tmp_path):
    (tmp_path / "r.mc").write_text(
        'import "std/io";\n'
        "struct res { id: int32; }\n"
        "fn res::constructor(self: &res, id: int32) { self.id = id; }\n"
        'fn res::destructor(self: &res) { println(f"drop {self.id}"); }\n'
    )
    main = tmp_path / "main.mc"
    main.write_text(
        'import "r";\nfn main() -> int32 { let r = res(11); return 0; }\n'
    )
    assert run_path(main) == 0
    assert capfd.readouterr().out == "drop 11\n"


def test_cross_module_private_destructor_is_access_checked(tmp_path):
    # Natural behavior: the scheduled family call is access-checked like any
    # other, so a foreign ctor-let over a @private destructor errors at the
    # let's line with the existing visibility diagnostic.
    (tmp_path / "r.mc").write_text(
        "struct res { id: int32; }\n"
        "fn res::constructor(self: &res, id: int32) { self.id = id; }\n"
        "@private\n"
        "fn res::destructor(self: &res) { }\n"
    )
    main = tmp_path / "main.mc"
    main.write_text(
        'import "r";\nfn main() -> int32 { let r = res(1); return 0; }\n'
    )
    with pytest.raises(
        LangError, match=r"function 'res::destructor' is private to r.mc"
    ):
        run_path(main)


def test_destructor_round_trips_through_mci(capfd, tmp_path):
    # The destructor is an ordinary qualified function to the interface
    # writer; an @inline pair travels verbatim and the importer's ctor-let
    # schedules cleanup through the stub.
    lib = tmp_path / "lib.mc"
    lib.write_text(
        'import "std/io";\n'
        "struct res { id: int32; }\n"
        "@inline\n"
        "fn res::constructor(self: &res, id: int32) { self.id = id; }\n"
        "@inline\n"
        'fn res::destructor(self: &res) { println(f"drop {self.id}"); }\n'
    )
    out = tmp_path / "lib.mci"
    assert emit_interface(lib, (tmp_path, STDLIB_DIR), None, {}, out) == 0
    assert "res::destructor" in out.read_text()
    lib.unlink()  # force the import to resolve through the stub
    main = tmp_path / "main.mc"
    main.write_text(
        'import "lib";\nfn main() -> int32 { let r = res(12); return 0; }\n'
    )
    assert run_path(main) == 0
    assert capfd.readouterr().out == "drop 12\n"
