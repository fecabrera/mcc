import "std/io";

// Statement-end destruction of expression-position `-> own` temporaries:
// a receiverless own call DROPS, in the Rust sense. An `-> own` function
// (own_returns.mc) hands its caller a cleanup obligation; a `let` adopts
// it (scope-end destruction, unchanged). Every OTHER consumption --
// discarding the call, argument position, chaining, assignment to an
// existing lvalue, the `try f() ?? fallback` mix -- receives a temporary
// that is destroyed automatically when the full call chain (the
// statement) ends: the value stays alive through every call that
// consumes it, the statement's computed result lands in its own storage
// first, and the destructor runs on the temporary's dedicated copy, so
// it can never clobber the result flowing onward. The open/close stamps
// below pin exactly WHEN each value dies.
//
// Prerequisites: own_returns.mc (the obligation, adoption, `move`),
// destructors.mc (the schedule; its stack-lets-only scope has exactly
// this one expression-position exception), error_handling.mc (results,
// `try`, `??`), control-flow/defer.mc (the defer stack).
//
//   pipenv run python -m mcc examples/types/own_drops.mc --run
//
// Every "open" below has a matching "close" except the two deliberate
// leaks flagged where they happen: assignment's overwritten value, and
// the mixed overload set at the end.

// The print-stamped resource: constructing and destroying print, so the
// output shows exactly where each drop lands.
struct res {
    id: int32;
}

fn res::constructor(self: &res, id: int32) {
    self.id = id;
    println(f"  open {id}");
}

fn res::destructor(self: &res) {
    println(f"  close {self.id}");
    self.id = -1;
}

// The own factory, the consumers the sections below feed it into, and
// the result composition.
fn make(id: int32) -> own res {
    return res(id);
}

fn use(r: res) -> int32 {
    println(f"  use {r.id}");
    return r.id;
}

fn use2(a: res, b: res) -> int32 {
    println(f"  use2 {a.id} {b.id}");
    return a.id + b.id;
}

fn outer(x: int32) -> int32 {
    println(f"  outer {x}");
    return x;
}

fn res::poke(self: res) -> int32 {
    println(f"  poke {self.id}");
    return self.id;
}

error load_error {
    BUSY,
}

fn load(id: int32) -> own result<res, load_error> {
    if (id < 0)
        return error(load_error::BUSY);
    return ok(res(id));
}

// The `try f();` statement: the try propagates the error (on that path
// nothing was constructed), and on the continue path the unwrapped ok
// payload drops at statement end like any bare discard.
fn refresh(k: int32) -> result<load_error> {
    try load(k);                    // open k, close k: the drop lands at
    println("  after try");         // this statement's end, before the print
    return ok();
}

// Return timing: mov tmp, make(k); mov out, use(tmp); drop tmp; ret out.
// The close lands inside this callee, after the return value is computed
// and before the function returns -- the caller sees the value intact.
fn ret_path(k: int32) -> int32 {
    return use(make(k));
}

// Transfer still transfers: `return make(k);` chains the obligation
// through untouched -- no drop in this forwarding frame, one close at
// the adopter's scope end.
fn wrap(k: int32) -> own res {
    return make(k);
}

// Statement temps die at their own statement's end, BEFORE the scope's
// defers run at block exit.
fn walk() {
    defer println("  defer");
    use(make(90));
    println("  mid");
}

// The `??` mix under a let ADOPTS like the bare call: whichever value
// fills the slot -- the unwrapped payload or the built fallback -- is
// destroyed at scope end, not at the statement.
fn hold_default(k: int32) {
    let v = try load(k) ?? res(99);
    println(f"  held {v.id}");
}

// The documented no-op: `own` over a destructor-less type has nothing to
// adopt, so nothing to drop -- which keeps generic `-> own T` signatures
// writable for every T.
struct plain {
    x: int32;
}

fn mint(x: int32) -> own plain {
    let p: plain = { x = x };
    return move(p);                 // a struct-literal let is a plain copy:
}                                   // handing it out takes the assertion

fn take_plain(p: plain) -> int32 {
    return p.x;
}

// A mixed overload set: an own member and a plain one behind one name.
// Detection is adoption's own conservative certainly-own judgment, so
// the set neither adopts nor drops -- a plain copy, and the one
// REMAINING documented leak among these shapes.
fn mixed(id: int32) -> own res {
    return res(id);
}

fn mixed(flag: bool) -> int32 {
    return 0;
}

fn main() -> int32 {
    // DISCARD: the value constructs, the statement ends, the drop runs.
    // A call through an own-typed function value (`let fp = make;
    // fp(1);`) drops identically -- the marker rides the type.
    println("discard:");
    make(1);                        // open 1, close 1

    // ARGUMENT POSITION: the temporary stays alive through the callee
    // (use reads it fine) and closes after the callee returns. Several
    // temps in one statement drop newest-first: 4 closes before 3.
    println("argument:");
    let n = use(make(2));           // open 2, use 2, close 2
    println(f"  got {n}");
    let s = use2(make(3), make(4)); // open 3, open 4, use2 3 4,
    println(f"  sum {s}");         //   close 4, close 3

    // FULL CHAIN END, not innermost-call end: the temp survives through
    // the OUTER call too and closes only after it returns.
    println("chain:");
    let m = outer(use(make(5)));    // open 5, use 5, outer 5, close 5
    println(f"  got {m}");

    // CHAINING: a method-call receiver drops the same way.
    println("chained:");
    let p = make(6).poke();         // open 6, poke 6, close 6
    println(f"  got {p}");

    // ASSIGNMENT, the sharp edge. The temp closes after the statement;
    // the destructor ran on the temp's dedicated copy, so r's bits stay
    // intact (it still prints 11). But r's bitwise copy now NAMES a
    // destroyed resource, and r's own adopted schedule destroys it AGAIN
    // at scope end -- while the overwritten value 10 is never closed at
    // all. This is the copies-are-bitwise stance doing what it says;
    // `-Wdestructor-copy` (roadmap) is the diagnostic direction.
    println("assigned:");
    {
        let r = make(10);           // adopts
        r = make(11);               // open 11, close 11 (the temp)
        println(f"  holding {r.id}");
    }                               // close 11 AGAIN; 10 never closes

    // THE TRY STATEMENT, in a result-returning helper: `try load(20);`
    // unwraps and drops the payload; the error path constructs nothing
    // and propagates.
    println("try:");
    let _ok = refresh(20);          // open 20, close 20, after try
    let _err = refresh(-1);         // silent: error propagated

    // THE `??` MIX, receiverless: mirrors the bare call. On the ok arm
    // the unwrapped payload drops after the statement (the fallback is
    // never built); on the error arm the built fallback stands in and
    // drops the same way.
    println("fallback:");
    try load(30) ?? res(31);        // open 30, close 30
    try load(-1) ?? res(32);        // open 32, close 32

    // ... and under a let it now ADOPTS (this closed the old
    // never-adopts gap): scope-end destruction on both arms.
    println("fallback let:");
    hold_default(40);               // open 40, held 40, close 40
    hold_default(-1);               // open 99, held 99, close 99

    // ADOPTION UNCHANGED: a plain let and a `let _` (a real local) each
    // schedule one close at scope end, LIFO. The try unwraps bound by a
    // let adopt the same way (own_returns.mc).
    println("adopted:");
    {
        let a = make(50);
        let _ = make(51);
        println(f"  held {a.id}");
    }                               // close 51, close 50

    // RETURN TIMING: the close lands inside ret_path, between computing
    // the return value and returning it.
    println("return:");
    let rv = ret_path(60);          // open 60, use 60, close 60
    println(f"  got {rv}");

    // TRANSFER: wrap forwards the obligation without dropping; the
    // adopting let here closes it once, at this block's end.
    println("transfer:");
    {
        let w = wrap(70);
        println(f"  held {w.id}");
    }                               // close 70

    // ONLY IF EXECUTED: a ternary arm or short-circuit right operand
    // drops inside its own arm; the unselected arm never constructs
    // (81 and 83 never open). A break/continue/return abandoning an
    // in-flight expression likewise destroys only the temps it had
    // already built on the way out.
    println("conditional:");
    let c = rv > 0 ? use(make(80)) : use(make(81));
    let both = c > 0 and use(make(82)) > 0;
    let skipped = c < 0 and use(make(83)) > 0;
    println(f"  {both} {skipped}");

    // BEFORE THE DEFERS: walk's temp closes at its statement's end;
    // the block's defer runs later, at scope exit.
    println("defers:");
    walk();                         // open 90, use 90, close 90, mid, defer

    // THE NO-OPS. plain has no destructor: nothing adopts, nothing
    // drops, every form is silent. And the mixed set never certainly
    // hands over, so value 100 opens and NEVER closes -- the deliberate
    // leak, adoption's conservative judgment applied to drops.
    println("no-ops:");
    mint(1);                        // silent: no obligation exists
    let k = take_plain(mint(2));
    mixed(100);                     // open 100 -- and no close anywhere
    println(f"  plain {k}");

    println("done");
    return 0;
}

// Two boundaries worth knowing. Discarding a raw own RESULT (`load(20);`
// with no try) is tag-guarded -- the payload is destroyed only on the ok
// tag -- but the dropped ERROR is still reportable under the opt-in
// -Wunused-result class: automatic destruction handles cleanup, never
// error handling, which is why this file consumes every result through
// its let/try/?? spellings and compiles clean under `-Wall -Werror`.
// And these forms remain PLAIN COPIES (no automatic destruction,
// follow-up work): an own call in a struct-literal field initializer,
// `emit f();`, `return f();` from a non-own function, and field
// projection (`make(9).id` reads the field out of an undestroyed
// copy). F-string hole temporaries, once on this list, now drop at
// statement end like any collected argument's: see fstring_values.mc.
//
// See also: own_returns.mc for the feature itself (the obligation, the
// formation rule, move, adoption, the function-pointer marker);
// destructors.mc for the schedule and the stack-lets-only scope this
// drop rule is the one exception to; error_handling.mc for results, the
// try endings, and `??`; control-flow/defer.mc for the defer stack
// statement temps die before; fstring_values.mc for the drop schedule
// carrying an f-string's rendered string and its hole temporaries.
// Full rules: docs/language.md, "Move-out returns: `-> own`".
