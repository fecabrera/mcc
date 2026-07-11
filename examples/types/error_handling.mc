import "std/io";

// Error handling: the `error` declaration naming a set of failure causes,
// the builtin `result<T, E>` / `result<E>` type carrying either an ok
// value or an error, the explicit ok()/error() constructors, and the ways
// a result is consumed: the `let ret, err = f();` destructure, and `try`
// with its three endings, an `except` handler, no clause (propagation),
// or a `??` fallback, plus the block-scoped `try (r = f()) { }` statement.
// Recoverable errors are ordinary values: no exceptions, no unwinding, no
// hidden control flow.
//
// Prerequisites: enums.mc (case/when, `Name::Member` reading) and
// block_expressions.mc (`emit`, which the except handler reuses).
//
// This covers stages 1 through 3 of the roadmap epic (declarations, the
// type, the constructors, every consuming form). Still staged: stage 4,
// diagnostics and rendering (an error value boxing into `{}`, a warning
// for a silently dropped result).

// An `error` declaration is enum-like but NOMINAL: a distinct int32-backed
// type with no arithmetic, no ordering, and no implicit integer conversion
// in either direction (`error(5)` and casting an int32 into `file_error`
// both reject). Two same-shaped declarations do not mix.
//
// Variants auto-number from 1; zero is the reserved, unnameable no-error
// state (`= 0` is a compile error, as is a duplicate value). An explicit
// `= n` continues numbering from n + 1, the C convention. A variant may
// carry a display string instead of a value: stored data for a future
// rendering stage, it does not affect the numbering.
error file_error {
    NOT_FOUND = "Not Found",   // 1, and carries display data
    PERMISSION,                // 2
    EXHAUSTED = 100,           // explicit value
    TIMEOUT,                   // 101, numbering resumes from 100 + 1
}

// An error value supports exactly what a failure cause needs: truthiness,
// ==/!= against members of its own declaration, and `case`. Nothing else:
// `e + 1`, `e < f`, `!e`, and `return 1;` into a file_error all reject;
// `e as int32` is the explicit numeric read-out.
fn describe(e: file_error) -> char* {
    case (e) {
        when file_error::NOT_FOUND:  return "not found";
        when file_error::PERMISSION: return "permission denied";
        when file_error::EXHAUSTED:  return "quota exhausted";
        else:                        return "some other cause";
    }
}

// A function that can fail returns result<T, E>: either the ok value or the
// error, never both. E must be a declared error type. Construction is
// explicit at every return site: `return 40 + key;` here would reject,
// there is no implicit wrap (and no implicit unwrap on the way out).
fn find(key: int32) -> result<int32, file_error> {
    if (key == 0) { return error(file_error::NOT_FOUND); }
    if (key > 9)  { return error(file_error::EXHAUSTED); }
    return ok(40 + key);
}

// A function that can only fail returns the one-argument result<E> and
// reports success with the bare ok(). (The language has no void type
// argument, here or anywhere.)
fn flush(fail: bool) -> result<file_error> {
    if (fail) { return error(file_error::TIMEOUT); }
    return ok();
}

// PROPAGATION, THE BARE TRY. `try find(key)` with no clause: on error the
// enclosing function returns error(err) itself, so its return type must
// carry the SAME declared error type (result<T2, E> for any T2, or
// result<E>); anything else, including main, is a compile error naming
// both types. On ok it yields the payload, but it is never implicitly
// wrapped on the way back out: `return try find(key);` rejects, the ok
// construction stays explicit (`return ok(try find(key) + 1000);` would
// also do, since a bare try composes as an ordinary operand). Mapping to
// a DIFFERENT error type stays a handler's job, the long spelling:
// `try find(key) except (err) { return error(other::CAUSE); }`.
fn wrap(key: int32) -> result<int32, file_error> {
    let v = try find(key);
    return ok(v + 1000);
}

// Over the error-only result<E> there is no payload to yield, so bare try
// is statement position only: `try flush(fail);` propagates on error and
// simply continues on ok, the propagate-or-continue consumer. (Over a
// result<T, E> the statement form propagates and discards the ok value.)
// And since propagation returns, a bare try inside a defer body is banned
// like the `return` it desugars to.
fn flush_all(fail: bool) -> result<file_error> {
    try flush(fail);
    try flush(false);
    return ok();
}

// A side-effecting default, to make the `??` fallback's laziness visible
// from main: the counter ticks only when the fallback actually runs.
@static let defaults_used: int32;
fn slow_default() -> int32 {
    defaults_used += 1;
    return -7;
}

fn main() -> int32 {
    // Reading the numeric value out is an explicit escape, `as int32`; no
    // cast goes the other way. (An error value does not box into `{}`
    // either, until the rendering stage: print the read-out or a
    // describe()-style string.)
    println("NOT_FOUND = {}, PERMISSION = {}",
            file_error::NOT_FOUND as int32, file_error::PERMISSION as int32);
    println("EXHAUSTED = {}, TIMEOUT = {}",
            file_error::EXHAUSTED as int32, file_error::TIMEOUT as int32);

    // Truthiness tests against the reserved zero state; equality works
    // against members of the same declaration.
    let e = file_error::PERMISSION;
    if (e) { println("e holds a cause: {}", describe(e)); }
    if (e == file_error::PERMISSION) { println("e == PERMISSION"); }
    if (e != file_error::NOT_FOUND)  { println("e != NOT_FOUND"); }

    // ok()/error() are context-typed like a bare struct literal: legal in
    // a return, a typed let, an assignment, an argument, or a struct
    // field, and an error elsewhere (`let r = ok(5);` alone has no type to
    // adapt to). A result exposes no fields; the two forms below are how
    // one is opened.
    let pending: result<int32, file_error> = ok(5);
    pending = error(file_error::TIMEOUT);

    // FORM 1, THE DESTRUCTURE: `let ret, err = f();` splits a
    // result<T, E> into exactly two binders (no rest binder, no type
    // annotations; each takes its arm's type). On ok, err is the reserved
    // zero no-error state: falsy by construction, since every declared
    // variant is non-zero, which makes `if (err)` a total did-it-fail
    // check for any error type.
    let found, err = find(2);
    if (err) { println("never printed: find(2) is ok"); }
    println("find(2): found = {}, err reads out as {}", found, err as int32);

    // On error, err is the cause and ret is the ZERO VALUE of T: zero
    // filled, never the union's other-arm bytes reinterpreted.
    let missing, cause = find(0);
    if (cause) {
        println("find(0) failed: {}; missing = {} (the zero value of int32)",
                describe(cause), missing);
    }

    // The destructure also opens a stored result, not just a fresh call.
    // (The error-only result<E> rejects here: it has no ok value to bind;
    // its consumers are statement position, the try ... except below or
    // the bare `try flush(fail);` inside flush_all() above.)
    let stale, why = pending;
    if (why == file_error::TIMEOUT) {
        println("pending held TIMEOUT; stale = {}", stale);
    }

    // FORM A, TRY ... EXCEPT: `try f() except (err) { H } [else { S }];`.
    // `try` attempts the call chain immediately after it (a unary-level
    // prefix) and hands its error to the `except` handler; `except` never
    // appears without its `try` (the un-prefixed spelling rejects with
    // "except needs try: try f() except (err) { ... }"). Where a value
    // escapes (a let initializer or a return value), H must diverge
    // (return, break, continue, panic) or `emit` a fallback that coerces
    // to T and stands in for the ok value. The optional `else` is the OK
    // ARM ONLY, as in Python's try/except/else. The one subtle path: on an
    // emit-fallback, else is SKIPPED (a fallback is not an ok), but the
    // code after the statement still runs, with the binding set to the
    // fallback.
    let v = try find(0) except (err) {
        println("handler: {}, emitting a fallback", describe(err));
        emit -1;
    } else {
        println("never printed: the fallback path skips else");
    };
    println("after: v = {} (the fallback; else did not run)", v);

    // On the ok arm the handler is skipped and `else` runs with the bound
    // value already in scope; it stays in scope after the statement.
    let w = try find(3) except (err) {
        emit -1;
    } else {
        println("else runs on ok: w = {}", w);
    };
    println("after: w = {}", w);

    // Because `try` sits at unary level, the whole form composes as an
    // ordinary operand inside a larger expression, same diverge-or-emit
    // obligation.
    let n = 1 + try find(2) except (err) { emit 0; };
    println("1 + try find(2) ... = {}", n);

    // STATEMENT POSITION: as a whole expression statement nothing escapes,
    // so the handler is obligation-free and may simply fall through ("log
    // and move on"). This is also the consumer for the error-only
    // result<E>; with no ok value there is no T, so `emit` rejects inside
    // this handler and the let/return forms reject the call outright.
    try flush(true) except (err) {
        println("flush failed: {}, logged and moving on", err as int32);
    };
    try flush(false) except (err) {
        println("never printed: flush(false) is ok");
    };

    // Bare-try propagation observed from outside: wrap() forwards find's
    // error unchanged, and adds 1000 on the ok path. (main itself returns
    // int32, so a bare try HERE is a compile error naming both types;
    // main opens results with the binding forms instead.)
    let big, werr = wrap(7);
    println("wrap(7) = {} (werr reads out as {})", big, werr as int32);
    let none, werr2 = wrap(0);
    if (werr2 == file_error::NOT_FOUND) {
        println("wrap(0) propagated NOT_FOUND; none = {}", none);
    }

    // And the statement form observed the same way: flush_all(true) stops
    // at its first `try flush(fail);` and forwards TIMEOUT; flush_all(false)
    // continues past both and reaches ok().
    try flush_all(true) except (err) {
        println("flush_all(true) propagated: {}", err as int32);
    };
    try flush_all(false) except (err) {
        println("never printed: flush_all(false) runs to ok()");
    };

    // DEFAULTING, THE ?? FALLBACK: `try f() ?? fallback` is the third try
    // ending (a try takes exactly one: `?? ... except` does not combine).
    // It discards the error and supplies a default coercing to T; nothing
    // escapes the expression, so the enclosing return type is never
    // consulted, which is why it is legal right here in main. (result<E>
    // rejects the form: no ok value to default.)
    let fell = try find(0) ?? -1;
    println("try find(0) ?? -1 = {}", fell);

    // The fallback is LAZY: it evaluates only on the error path, so its
    // side effects never run on ok. The counter stays at 0 through the
    // first line and ticks on the second.
    let hit = try find(2) ?? slow_default();
    println("ok path: hit = {}, defaults_used = {}", hit, defaults_used);
    let dflt = try find(0) ?? slow_default();
    println("error path: dflt = {}, defaults_used = {}", dflt, defaults_used);

    // The right-hand side is atomic: a unary expression (identifier,
    // literal, call, member/index chain, prefix -/!/~), a parenthesized
    // (expr), or an emit-block that may do work before emitting the
    // default (or diverge instead of emitting).
    let logged = try find(0) ?? {
        println("emit-block fallback: logging, then defaulting");
        emit -2;
    };
    println("logged = {}", logged);

    // Precedence: ?? binds tighter than the ternary and every binary
    // operator, so the fallback reduces to a single operand before the
    // subtraction applies: this is (try find(0) ?? 2) - 1, never ?? (2 - 1).
    let tight = try find(0) ?? 2 - 1;
    println("try find(0) ?? 2 - 1 = {}", tight);

    // FORM B, THE TRY STATEMENT: `try (r = f()) { B } except (err) { H }`
    // keeps the binding inside a block. The head binds a fresh r with no
    // `let` (the same `r = expr` head spelling as the `with` statement,
    // see with_unwrap.mc), scoped to B ONLY; B runs on ok, H runs on
    // error with err bound (scoped to H) and is obligation-free. There is
    // no else arm: the block already is the no-error arm.
    try (r = find(5)) {
        println("try statement: r = {} inside the ok block", r);
    } except (err) {
        println("never printed: find(5) is ok");
    }

    // r did not escape the statement above, so the name is free to be
    // bound again by the next head.
    try (r = find(0)) {
        println("never printed: find(0) fails");
    } except (err) {
        println("try statement error arm: {}", describe(err));
    }

    return 0;
}

// See also: enums.mc for the transparent constant-set counterpart (an enum
// member folds to its underlying value; an error variant stays nominal),
// and block_expressions.mc for `emit` targeting a block, the shape the
// except handler reuses. Full rules and the staged roadmap:
// docs/language.md "Error handling".
