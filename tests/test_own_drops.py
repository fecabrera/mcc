"""Statement-end destruction of expression-position `-> own` temporaries.

An `-> own` call whose value no `let` adopts hands over a temporary the
consumer alone would have leaked. Every receiverless consumption -- the
discard `f();`, argument position `g(f())`, chaining `f().m()`, assignment
to an existing lvalue `v = f();`, and the `try f() ?? fallback` mix -- now
destroys that temporary when the full call chain / statement ends: the
value stays alive through every call that consumes it (in
`println("{}".format(test()))` the handed-over value is destroyed only
after `println` returns), the statement's computed result lands in its own
storage first, and the destructor runs on the temporary's dedicated copy,
so it can never clobber the result flowing onward. A `let` receiver keeps
today's adoption (scope-end destruction) unchanged -- and the `??` mix now
adopts under a let exactly like the bare call.

The detection predicate is adoption's own (`own_call_initializer`), so a
mixed own/plain overload set conservatively stays a plain copy, and `own`
over a destructor-less type remains the documented no-op
(`type_owns_cleanup`, shared with RAII scheduling).
"""

from helpers import run


# A resource whose lifecycle is observable: constructing and destroying
# print, so a test pins exactly when each value opens and closes.
RES = """
import "std/io";

struct res { fd: int32; }

fn res::constructor(mut self: res, fd: int32) {
    self.fd = fd;
    println(f"open {fd}");
}

fn res::destructor(mut self: res) {
    println(f"close {self.fd}");
    self.fd = -1;
}

fn make(fd: int32) -> own res { return res(fd); }
fn use(r: res) -> int32 { println(f"use {r.fd}"); return r.fd; }
"""

FAIL = "error fail { OOPS }\n"

LOAD = FAIL + """
fn load(k: int32) -> own result<res, fail> {
    if (k < 0) { return error(fail::OOPS); }
    return ok(res(k));
}
"""


# --- the five receiverless forms -------------------------------------------


def test_discarded_call_destroys_at_statement_end(capfd):
    src = RES + """
    fn main() -> int32 {
        make(1);
        println("done");
        return 0;
    }
    """
    assert run(src) == 0
    assert capfd.readouterr().out == "open 1\nclose 1\ndone\n"


def test_discard_through_an_own_typed_function_value(capfd):
    # The ownret bit on a function value's type vouches like a direct call.
    src = RES + """
    fn main() -> int32 {
        let fp = make;
        fp(2);
        println("done");
        return 0;
    }
    """
    assert run(src) == 0
    assert capfd.readouterr().out == "open 2\nclose 2\ndone\n"


def test_argument_temp_destroyed_after_the_callee_returns(capfd):
    # `use` runs on the still-live value; the close follows its return.
    src = RES + """
    fn main() -> int32 {
        let n = use(make(3));
        println(f"got {n}");
        return 0;
    }
    """
    assert run(src) == 0
    assert capfd.readouterr().out == "open 3\nuse 3\nclose 3\ngot 3\n"


def test_nested_chain_destroys_at_the_full_chain_end(capfd):
    # Statement end, not innermost-call end: the temp survives through the
    # OUTER call and is destroyed after it returns.
    src = RES + """
    fn outer(x: int32) -> int32 { println(f"outer {x}"); return x; }
    fn main() -> int32 {
        let n = outer(use(make(4)));
        return n - 4;
    }
    """
    assert run(src) == 0
    assert capfd.readouterr().out == "open 4\nuse 4\nouter 4\nclose 4\n"


def test_generic_callee_argument_temp_drops(capfd):
    # The overload-resolution marshal path (pre-evaluated arguments).
    src = RES + """
    fn gtake<T>(v: T) -> int32 { println("gtake"); return 1; }
    fn main() -> int32 {
        let n = gtake(make(5));
        return n - 1;
    }
    """
    assert run(src) == 0
    assert capfd.readouterr().out == "open 5\ngtake\nclose 5\n"


def test_collected_extra_temp_drops(capfd):
    # The trailing `args...` collection is argument position too.
    src = RES + """
    fn sink(args...) -> int32 { println("sink"); return 0; }
    fn main() -> int32 {
        return sink(make(6));
    }
    """
    assert run(src) == 0
    assert capfd.readouterr().out == "open 6\nsink\nclose 6\n"


def test_chained_receiver_drops_after_the_chain(capfd):
    src = RES + """
    fn res::poke(self: res) -> int32 { println(f"poke {self.fd}"); return self.fd; }
    fn main() -> int32 {
        let n = make(7).poke();
        println(f"got {n}");
        return 0;
    }
    """
    assert run(src) == 0
    assert capfd.readouterr().out == "open 7\npoke 7\nclose 7\ngot 7\n"


def test_assignment_drops_the_temp_and_the_copy_aliases(capfd):
    # The ruling stands with its documented aliasing consequence: the temp
    # closes after the statement (the destructor runs on the temp's own
    # copy, so r's bits stay intact), r's copy now names the destroyed
    # resource, and its adopted schedule destroys value 11 AGAIN at scope
    # end -- while the overwritten value 10 is never closed at all. The
    # `-Wdestructor-copy` roadmap item is the diagnostic direction here.
    src = RES + """
    fn main() -> int32 {
        let r = make(10);
        r = make(11);
        println(f"assigned {r.fd}");
        return 0;
    }
    """
    assert run(src) == 0
    assert capfd.readouterr().out == (
        "open 10\nopen 11\nclose 11\nassigned 11\nclose 11\n"
    )


def test_field_store_drops_the_temp(capfd):
    src = RES + """
    struct holder { r: res; }
    fn main() -> int32 {
        let h: holder = { r = res(0) };
        h.r = make(12);
        println("stored");
        return 0;
    }
    """
    assert run(src) == 0
    assert capfd.readouterr().out == "open 0\nopen 12\nclose 12\nstored\n"


def test_compound_assignment_drops_a_scalar_temp(capfd):
    # A scalar can carry a destructor family too; the RHS temp drops once
    # the right-hand side is computed, before the combined store.
    src = """
    import "std/io";
    fn int32::destructor(mut self: int32) { println(f"dint {self}"); }
    fn mkint() -> own int32 {
        let v: int32 = 7;
        return move(v);
    }
    fn main() -> int32 {
        let x: int32 = 1;
        x += mkint();
        println(f"x {x}");
        return 0;
    }
    """
    assert run(src) == 0
    assert capfd.readouterr().out == "dint 7\nx 8\n"


def test_fallback_mix_receiverless_drops_the_payload(capfd):
    # `try f() ?? fb;` in statement position mirrors the bare call: on the
    # ok arm the unwrapped payload drops after the statement (the fallback
    # is never built); on the error arm the built fallback stands in for
    # the payload and drops the same way.
    src = RES + LOAD + """
    fn main() -> int32 {
        try load(30) ?? res(31);
        println("ok arm");
        try load(-1) ?? res(32);
        println("err arm");
        return 0;
    }
    """
    assert run(src) == 0
    assert capfd.readouterr().out == (
        "open 30\nclose 30\nok arm\nopen 32\nclose 32\nerr arm\n"
    )


# --- try composition --------------------------------------------------------


def test_try_discard_destroys_the_ok_payload(capfd):
    # `try f();` propagates on error (nothing was constructed there) and
    # destroys the unwrapped payload on the continue path.
    src = RES + LOAD + """
    fn drive(k: int32) -> result<fail> {
        try load(k);
        println("after try");
        return ok();
    }
    fn main() -> int32 {
        let _ok = drive(20);
        let _err = drive(-1);
        println("done");
        return 0;
    }
    """
    assert run(src) == 0
    assert capfd.readouterr().out == "open 20\nclose 20\nafter try\ndone\n"


def test_raw_result_discard_is_tag_guarded(capfd):
    # Discarding the whole own result destroys the payload only on the ok
    # tag; an error-tagged value holds no payload to destroy. (The dropped
    # error is -Wunused-result's business, unchanged.)
    src = RES + LOAD + """
    fn main() -> int32 {
        load(21);
        println("ok dropped");
        load(-1);
        println("err dropped");
        return 0;
    }
    """
    assert run(src) == 0
    assert capfd.readouterr().out == (
        "open 21\nclose 21\nok dropped\nerr dropped\n"
    )


def test_fallback_let_adopts_on_both_arms(capfd):
    # The `??` mix under a let ADOPTS like the bare call: whichever value
    # fills the slot -- the unwrapped payload or the built fallback -- is
    # destroyed at scope end, not at the statement.
    src = RES + LOAD + """
    fn scope(k: int32) {
        let v = try load(k) ?? res(99);
        println(f"held {v.fd}");
    }
    fn main() -> int32 {
        scope(40);
        scope(-1);
        return 0;
    }
    """
    assert run(src) == 0
    assert capfd.readouterr().out == (
        "open 40\nheld 40\nclose 40\nopen 99\nheld 99\nclose 99\n"
    )


# --- adoption stays adoption ------------------------------------------------


def test_adopting_lets_destroy_at_scope_end_once(capfd):
    # A plain let, a `_` binding (a real local), the bare-try unwrap, and
    # the except form all adopt: one close each, at scope end, LIFO.
    src = RES + LOAD + """
    fn main() -> int32 {
        let a = make(50);
        let _ = make(51);
        let c = try load(52) except (err) { return -1; };
        println(f"held {a.fd} {c.fd}");
        return 0;
    }
    """
    assert run(src) == 0
    assert capfd.readouterr().out == (
        "open 50\nopen 51\nopen 52\nheld 50 52\n"
        "close 52\nclose 51\nclose 50\n"
    )


def test_transfer_chain_does_not_double_destroy(capfd):
    # `return inner();` chains the obligation through untouched: no drop in
    # the forwarding frame, one close at the adopter's scope end.
    src = RES + """
    fn wrap(k: int32) -> own res { return make(k); }
    fn main() -> int32 {
        let w = wrap(60);
        println(f"held {w.fd}");
        return 0;
    }
    """
    assert run(src) == 0
    assert capfd.readouterr().out == "open 60\nheld 60\nclose 60\n"


def test_destructorless_own_stays_a_noop(capfd):
    # No destructor family: nothing to adopt, so nothing to drop -- every
    # receiverless form compiles and runs without synthesized calls.
    src = """
    import "std/io";
    struct plain { x: int32; }
    fn mint(x: int32) -> own plain {
        let p: plain = { x = x };
        return move(p);
    }
    fn take(p: plain) -> int32 { return p.x; }
    fn main() -> int32 {
        mint(1);
        let n = take(mint(2));
        let p = mint(3);
        p = mint(4);
        println(f"ok {n + p.x}");
        return 0;
    }
    """
    assert run(src) == 0
    assert capfd.readouterr().out == "ok 6\n"


def test_mixed_overload_set_stays_a_plain_copy(capfd):
    # Adoption's conservative judgment: a name behind which only some
    # candidates are own never vouches, so nothing is scheduled and
    # nothing drops -- the documented leak remains for such sets.
    src = RES + """
    fn mixed(k: int32) -> own res { return res(k); }
    fn mixed(flag: bool) -> int32 { return 0; }
    fn main() -> int32 {
        mixed(70);
        println("done");
        return 0;
    }
    """
    assert run(src) == 0
    assert capfd.readouterr().out == "open 70\ndone\n"


# --- placement of the drop --------------------------------------------------


def test_return_value_computes_before_the_drop_and_the_return(capfd):
    # The ruling's shape: mov tmp, f(); mov out, g(tmp); drop tmp; ret out.
    # The close lands inside the callee, after its return value is
    # computed and before it returns -- the caller sees the value intact.
    src = RES + """
    fn ret_path(k: int32) -> int32 {
        return use(make(k));
    }
    fn main() -> int32 {
        let n = ret_path(80);
        println(f"ret {n}");
        return 0;
    }
    """
    assert run(src) == 0
    assert capfd.readouterr().out == "open 80\nuse 80\nclose 80\nret 80\n"


def test_statement_temps_drop_before_scope_defers(capfd):
    # A statement's own temporaries die at its end; the block's defers run
    # later, at scope exit -- on the fall-through and the return path both.
    src = RES + """
    fn walk() {
        defer println("defer");
        use(make(90));
        println("mid");
    }
    fn ret_walk() -> int32 {
        defer println("rdefer");
        return use(make(91));
    }
    fn main() -> int32 {
        walk();
        let n = ret_walk();
        return n - 91;
    }
    """
    assert run(src) == 0
    assert capfd.readouterr().out == (
        "open 90\nuse 90\nclose 90\nmid\ndefer\n"
        "open 91\nuse 91\nclose 91\nrdefer\n"
    )


def test_conditional_arms_drop_only_what_executed(capfd):
    # A ternary arm and a short-circuit right operand run their drops
    # inside their own arm; the unselected arm never constructs at all.
    src = RES + """
    fn pick(c: bool) -> int32 {
        return c ? use(make(100)) : use(make(101));
    }
    fn main() -> int32 {
        let a = pick(true);
        let ok = a > 0 and use(make(102)) > 0;
        let skipped = a < 0 and use(make(103)) > 0;
        println(f"{ok} {skipped}");
        return 0;
    }
    """
    assert run(src) == 0
    assert capfd.readouterr().out == (
        "open 100\nuse 100\nclose 100\n"
        "open 102\nuse 102\nclose 102\n"
        "true false\n"
    )


def test_break_destroys_temps_of_the_abandoned_expression(capfd):
    # A jump out of the loop abandons the in-flight argument list: the
    # already-constructed temp is destroyed on the way out (a temp is only
    # ever destroyed when its call actually executed).
    src = RES + LOAD + """
    fn use2(a: res, b: res) -> int32 { return a.fd + b.fd; }
    fn main() -> int32 {
        while (true) {
            let x = use2(make(110), try load(-1) ?? { break; });
            println(f"not reached {x}");
        }
        println("after");
        return 0;
    }
    """
    assert run(src) == 0
    assert capfd.readouterr().out == "open 110\nclose 110\nafter\n"


def test_generic_body_drops_per_instantiation(capfd):
    # The drop synthesizes inside each monomorphized body like any other
    # statement; two instantiations, two open/close pairs.
    src = RES + """
    fn spill<T>(v: T) -> int32 {
        make(120);
        return 0;
    }
    fn main() -> int32 {
        return spill(1) + spill(true);
    }
    """
    assert run(src) == 0
    assert capfd.readouterr().out == "open 120\nclose 120\nopen 120\nclose 120\n"
