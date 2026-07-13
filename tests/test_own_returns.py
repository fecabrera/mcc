"""`-> own T`: move-out returns and caller-adopted destruction.

A function declared `-> own T` hands its caller an owned value: returning
an auto-destructed local cancels the local's scheduled destructor on that
path and transfers the cleanup obligation, and the caller's let adopts it
(scheduling `T::destructor` exactly like a constructor-sugar let). The
formation rule is strict: an unmarked return must visibly hold the
obligation it hands over -- the constructed local, a fresh constructor
expression, or a chained own call -- and any other value (a plain copy)
needs the explicit `move(v)` assertion -- a builtin-shaped
`fn move<T>(v: T) -> T` claimed by call shape like ok()/error(), legal
only in the return value of an own function (around the whole value,
`return move(v);`, or on the ok payload, `return ok(move(v));`).

Over a `result<T, E>` return the ownership rides the ok payload:
`return ok(local)` transfers, `return error(...)` is the error path (locals
destroyed normally), `let s = try f();` and the except form adopt the
unwrapped payload. `own` on a destructor-less type is a no-op, so generic
`-> own T` stays writable. No ABI change anywhere: `own` is compile-time
policy, a flag beside `mut_return` (the two are mutually exclusive).
"""

import re

import pytest

from mcc.codegen import CodeGen
from mcc.errors import LangError
from mcc.interface import render_interface
from mcc.lexer import tokenize
from mcc.parser import Parser
from helpers import compile_ir, run


# A resource type whose destructor visibly stamps the value, so a test can
# tell a destroyed copy (h == -1) from a transferred one.
RES = """
struct res { h: int32; }
fn res::constructor(mut self: res, v: int32) { self.h = v; }
fn res::destructor(mut self: res) { self.h = -1; }
"""

FAIL = 'error fail { OOPS, }\n'


def dtor_calls_by_function(ir: str) -> dict:
    """Count destructor call instructions per defining function."""
    counts: dict = {}
    fn = None
    for line in ir.splitlines():
        m = re.match(r'define .*@"?([^"(]+)"?\(', line)
        if m:
            fn = m.group(1)
        if fn and re.search(r"call .*destructor", line):
            counts[fn] = counts.get(fn, 0) + 1
    return counts


# --- the transfer ---------------------------------------------------------------

def test_returning_the_local_transfers_and_caller_adopts():
    # The headline: the callee's scheduled destructor is cancelled on the
    # moving return, and the caller's let adopts the obligation -- so the
    # value arrives alive and is destroyed exactly once, at the caller.
    assert run(
        RES
        + """
        fn make(v: int32) -> own res {
            let r = res(v);
            return r;              // transfer: r's destructor cancelled here
        }
        fn main() -> int32 {
            let r = make(42);
            return r.h;            // alive: 42, not the destructor's -1
        }
        """
    ) == 42


def test_destructor_runs_once_at_the_caller():
    ir = compile_ir(
        RES
        + """
        fn make(v: int32) -> own res {
            let r = res(v);
            return r;
        }
        fn main() -> int32 {
            let r = make(1);
            return 0;
        }
        """
    )
    counts = dtor_calls_by_function(ir)
    assert counts.get("main") == 1  # adoption
    assert "make" not in counts     # cancelled on the moving path


def test_non_moving_paths_keep_the_destructor():
    # Cancellation is per return path: an exit that does not return the
    # local still destroys it.
    ir = compile_ir(
        RES
        + """
        fn make(v: int32) -> own res {
            let r = res(v);
            if (v < 0) {
                return res(0);     // fresh temporary; r destroyed HERE
            }
            return r;              // r's destructor cancelled HERE only
        }
        fn main() -> int32 {
            let r = make(1);
            return 0;
        }
        """
    )
    counts = dtor_calls_by_function(ir)
    assert counts.get("make") == 1  # exactly the early-return path
    assert counts.get("main") == 1


def test_two_path_values_at_runtime():
    assert run(
        RES
        + """
        fn make(v: int32) -> own res {
            let r = res(v);
            if (v < 0) {
                return res(0);
            }
            return r;
        }
        fn main() -> int32 {
            let a = make(41);
            let b = make(-5);
            return a.h + b.h + 1;   // 41 + 0 + 1
        }
        """
    ) == 42


def test_constructor_expression_and_chained_own_call_return_unmarked():
    # The other blessed forms: a fresh temporary mints the obligation, a
    # chained own call passes it through.
    assert run(
        RES
        + """
        fn fresh(v: int32) -> own res {
            return res(v);          // expression temporary: nothing to cancel
        }
        fn chain(v: int32) -> own res {
            return fresh(v);        // the obligation flows through
        }
        fn main() -> int32 {
            let r = chain(42);
            return r.h;
        }
        """
    ) == 42


def test_own_on_a_destructorless_type_is_a_noop():
    # Keeps generic `-> own T` writable: for a T with no destructor family
    # there is nothing to cancel or adopt.
    assert run(
        """
        fn pick<T>(a: T, b: T, first: bool) -> own T {
            return move(first ? a : b);
        }
        fn main() -> int32 {
            return pick(42, 7, true);
        }
        """
    ) == 42


# --- return move: the explicit escape --------------------------------------------

def test_plain_copy_needs_move():
    with pytest.raises(
        LangError,
        match=r"an own return transfers ownership, but this value is a "
        r"plain copy",
    ):
        compile_ir(
            RES
            + """
            fn steal(mut r: res) -> own res {
                return r;           // a parameter copy: the original stays
            }
            fn main() -> int32 { return 0; }
            """
        )


def test_move_asserts_the_copy_transfer():
    # The pop_owned idiom: the container relinquishes an element it owns,
    # which only the programmer can know -- `move` is that assertion.
    assert run(
        RES
        + """
        struct box { r: res; }
        fn box::pop(mut self: box) -> own res {
            return move(self.r);
        }
        fn main() -> int32 {
            let bx = box { r = res { h = 42 } };
            let r = bx.pop();
            return r.h;
        }
        """
    ) == 42


def test_move_outside_an_own_function_is_rejected():
    with pytest.raises(
        LangError,
        match=r"move\(\.\.\.\) has no transfer target here",
    ):
        compile_ir(
            RES
            + """
            fn f() -> res {
                let r: res;
                return move(r);
            }
            fn main() -> int32 { return 0; }
            """
        )


def test_move_outside_a_return_is_rejected():
    # move() behaves like a builtin fn move<T>(v: T) -> T, but only a
    # return in an own function gives the assertion a transfer target --
    # a let (which would imply an adoption that does not happen) rejects.
    with pytest.raises(
        LangError,
        match=r"move\(\.\.\.\) has no transfer target here",
    ):
        compile_ir(
            RES
            + """
            fn main() -> int32 {
                let r: res;
                let s = move(r);
                return 0;
            }
            """
        )


def test_move_composes_inside_the_ok_payload():
    # `return ok(move(v))` -- the assertion sits on the payload, like
    # ok()/error() themselves claim their value.
    assert run(
        RES
        + FAIL
        + """
        struct box { r: res; }
        fn box::pop(mut self: box) -> own result<res, fail> {
            return ok(move(self.r));
        }
        fn main() -> int32 {
            let bx = box { r = res { h = 42 } };
            let r = try bx.pop() except (e) { return -1; };
            return r.h;
        }
        """
    ) == 42


def test_move_on_an_error_payload_is_rejected():
    with pytest.raises(
        LangError,
        match=r"an error return transfers nothing to move",
    ):
        compile_ir(
            RES
            + FAIL
            + """
            fn f() -> own result<res, fail> {
                return error(move(fail::OOPS));
            }
            fn main() -> int32 { return 0; }
            """
        )


# --- result composition -----------------------------------------------------------

def test_ok_local_transfers_and_error_path_destroys():
    assert run(
        RES
        + FAIL
        + """
        fn make(v: int32) -> own result<res, fail> {
            let r = res(v);
            if (v < 0)
                return error(fail::OOPS);   // r destroyed normally here
            return ok(r);                   // transfer through the payload
        }
        fn main() -> int32 {
            let r = try make(41) ?? res { h = 0 };
            let bad = try make(-1) ?? res { h = 1 };
            return r.h + bad.h;             // 41 + 1
        }
        """
    ) == 42


def test_try_let_adopts_the_unwrapped_payload():
    ir = compile_ir(
        RES
        + FAIL
        + """
        fn make(v: int32) -> own result<res, fail> {
            let r = res(v);
            return ok(r);
        }
        fn use() -> result<int32, fail> {
            let r = try make(41);   // adopts: destroyed at use()'s scope end
            return ok(r.h);
        }
        fn main() -> int32 {
            let n = try use() ?? -1;
            return n + 1;
        }
        """
    )
    counts = dtor_calls_by_function(ir)
    assert counts.get("use") == 1
    assert "make" not in counts


def test_except_let_adopts_and_own_chain_returns():
    assert run(
        RES
        + FAIL
        + """
        fn make(v: int32) -> own result<res, fail> {
            let r = res(v);
            return ok(r);
        }
        fn keep() -> int32 {
            let r = try make(41) except (e) { return -1; };  // adopts
            return r.h;
        }
        fn ownwrap() -> own res {
            // The unwrapped payload of an own chain transfers through the
            // return-position except desugar unmarked.
            return try make(1) except (e) { return move(res { h = 0 }); };
        }
        fn main() -> int32 {
            let w = ownwrap();
            return keep() + w.h;    // 41 + 1
        }
        """
    ) == 42


def test_unwrapped_own_return_in_a_plain_function_stays_legal():
    # A non-own function returning the unwrapped payload of an own call is
    # today's plain copy (the obligation is dropped -- a documented leak),
    # not an error: the except desugar's hidden let never adopts.
    assert run(
        RES
        + FAIL
        + """
        fn make(v: int32) -> own result<res, fail> {
            let r = res(v);
            return ok(r);
        }
        fn plainwrap() -> res {
            return try make(42) except (e) { return res { h = 0 }; };
        }
        fn main() -> int32 {
            return plainwrap().h;
        }
        """
    ) == 42


def test_own_over_an_error_only_result_is_rejected():
    with pytest.raises(
        LangError,
        match=r"an own return hands over the ok payload, and an error-only "
        r"result has none",
    ):
        compile_ir(
            FAIL
            + """
            fn f() -> own result<fail> { return ok(); }
            fn main() -> int32 { return 0; }
            """
        )


# --- the closed escape hole --------------------------------------------------------

def test_ok_wrapped_local_escape_is_now_rejected():
    # Pre-existing hole, closed with this feature: in a NON-own function,
    # `return ok(local)` of an auto-destructed local smuggled the same
    # destroyed-copy escape past the bare-Var check.
    with pytest.raises(
        LangError,
        match=r"cannot return 'r': its automatic destructor runs",
    ):
        compile_ir(
            RES
            + FAIL
            + """
            fn f() -> result<res, fail> {
                let r = res(1);
                return ok(r);
            }
            fn main() -> int32 { return 0; }
            """
        )


def test_bare_local_escape_names_the_own_hatch():
    with pytest.raises(
        LangError,
        match=r"declare the function `-> own` to transfer ownership",
    ):
        compile_ir(
            RES
            + """
            fn f() -> res {
                let r = res(1);
                return r;
            }
            fn main() -> int32 { return 0; }
            """
        )


# --- declaration-shape rejections ---------------------------------------------------

def test_own_void_is_rejected():
    with pytest.raises(
        LangError, match=r"an own return needs a value to hand over"
    ):
        compile_ir("fn f() -> own void { return; } fn main() -> int32 { return 0; }")


def test_own_and_mut_do_not_combine():
    with pytest.raises(
        LangError, match=r"a return cannot be both mut and own"
    ):
        compile_ir(
            RES
            + """
            fn f(mut r: res) -> mut own res { return r; }
            fn main() -> int32 { return 0; }
            """
        )


def test_own_on_extern_is_rejected():
    with pytest.raises(
        LangError, match=r"an own return is not allowed on @extern"
    ):
        compile_ir("@extern fn f() -> own int32;\nfn main() -> int32 { return 0; }")


def test_own_on_a_property_is_rejected():
    with pytest.raises(
        LangError,
        match=r"a @property or @accessor method cannot return own",
    ):
        compile_ir(
            RES
            + """
            @property fn res::v(const self: res) -> own int32 {
                return self.h;
            }
            fn main() -> int32 { return 0; }
            """
        )


# --- fn-pointer-type parity --------------------------------------------------------

def test_indirect_call_through_an_own_typed_value_adopts():
    # `fn(int32) -> own res` carries the bit, so a call through the value
    # vouches for adoption exactly like a direct call.
    ir = compile_ir(
        RES
        + """
        fn make(v: int32) -> own res {
            let r = res(v);
            return r;
        }
        fn main() -> int32 {
            let factory: fn(int32) -> own res = make;
            let r = factory(1);
            return 0;
        }
        """
    )
    assert dtor_calls_by_function(ir).get("main") == 1


def test_inferred_function_value_carries_the_own_bit():
    # `let factory = make;` infers the fn type WITH ownret (the function
    # value derives it from the declaration), so adoption survives
    # inference too.
    ir = compile_ir(
        RES
        + """
        fn make(v: int32) -> own res {
            let r = res(v);
            return r;
        }
        fn main() -> int32 {
            let factory = make;
            let r = factory(1);
            return 0;
        }
        """
    )
    assert dtor_calls_by_function(ir).get("main") == 1


def test_field_held_own_callback_adopts():
    assert run(
        RES
        + """
        fn make(v: int32) -> own res {
            let r = res(v);
            return r;
        }
        struct maker { build: fn(int32) -> own res; }
        fn main() -> int32 {
            let m = maker { build = make };
            let r = m.build(42);
            return r.h;         // alive: adopted, destroyed at scope end
        }
        """
    ) == 42


def test_dropping_the_own_marker_implicitly_is_rejected():
    with pytest.raises(
        LangError,
        match=r"an own return is a contract, not a convention",
    ):
        compile_ir(
            RES
            + """
            fn make(v: int32) -> own res {
                let r = res(v);
                return r;
            }
            fn main() -> int32 {
                let f: fn(int32) -> res = make;
                return 0;
            }
            """
        )


def test_fabricating_the_own_marker_implicitly_is_rejected():
    with pytest.raises(
        LangError,
        match=r"an own return is a contract, not a convention",
    ):
        compile_ir(
            RES
            + """
            fn plain(v: int32) -> res { return res(v); }
            fn main() -> int32 {
                let f: fn(int32) -> own res = plain;
                return 0;
            }
            """
        )


def test_cast_is_the_explicit_marker_hatch():
    # `as` retypes across the own contract explicitly (dropping adoption
    # is then a documented leak, the C stance).
    assert run(
        RES
        + """
        fn make(v: int32) -> own res {
            let r = res(v);
            return r;
        }
        fn main() -> int32 {
            let f = make as fn(int32) -> res;
            let r = f(42);      // no adoption: the plain type says so
            return r.h;
        }
        """
    ) == 42


def test_own_void_fn_type_is_rejected():
    with pytest.raises(
        LangError, match=r"an own return needs a value to hand over"
    ):
        compile_ir(
            "fn main() -> int32 { let f: fn() -> own void; return 0; }"
        )


def test_own_fn_type_renders_in_interface_stubs():
    source = (
        RES
        + """
        fn take(cb: fn(int32) -> own res) -> int32 {
            let r = cb(1);
            return r.h;
        }
        """
    )
    program = Parser(tokenize(source)).parse_program()
    cg = CodeGen(program, "test")
    cg.generate()
    out = render_interface(cg, source, list(program.imports))
    assert "fn take(cb: fn(int32) -> own res) -> int32;" in out


# --- the signature travels ------------------------------------------------------------

def test_own_renders_in_interface_stubs():
    source = (
        RES
        + """
        fn make(v: int32) -> own res {
            let r = res(v);
            return r;
        }
        """
    )
    program = Parser(tokenize(source)).parse_program()
    cg = CodeGen(program, "test")
    cg.generate()
    out = render_interface(cg, source, list(program.imports))
    assert "fn make(v: int32) -> own res;" in out


def test_own_mismatch_with_prototype_is_rejected():
    with pytest.raises(
        LangError, match=r"definition of 'make' does not match its prototype"
    ):
        compile_ir(
            RES
            + """
            fn make(v: int32) -> res;
            fn make(v: int32) -> own res {
                return res(v);
            }
            fn main() -> int32 { return 0; }
            """
        )
