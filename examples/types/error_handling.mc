import "std/io";

// Error handling: the `error` declaration naming a set of failure causes,
// the builtin `result<T, E>` / `result<E>` type carrying either an ok
// value or an error, the explicit ok()/error() constructors, and the two
// binding forms that consume a result: the `let ret, err = f();`
// destructure and `try ... except`. Recoverable errors are ordinary
// values: no exceptions, no unwinding, no hidden control flow.
//
// Prerequisites: enums.mc (case/when, `Name::Member` reading) and
// block_expressions.mc (`emit`, which the except handler reuses).
//
// This covers stages 1 and 2 of the roadmap epic (declarations, the type,
// the constructors, the binding forms). Still staged: the bare `try g()`
// propagation shorthand (a compile error until it lands), the
// `try (ret = f()) { }` statement, and the `??` fallback.

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

// THE PROPAGATION IDIOM. `try` attempts the call and hands its error to
// the `except` handler. On the error arm the handler runs with `err`
// bound (a plain copy of the error, scoped to the handler) and here hands
// it onward, explicitly reconstructed with error(err): with the same E the
// construction type-checks directly, a different error type would need a
// mapping. There is no implicit coercion. On the ok arm the handler is
// skipped and v is the payload.
fn wrap(key: int32) -> result<int32, file_error> {
    let v = try find(key) except (err) { return error(err); };
    return ok(v + 1000);
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
    // its consumer is the statement-position try ... except below.)
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

    // The propagation idiom observed from outside: wrap() forwards find's
    // error unchanged, and adds 1000 on the ok path.
    let big, werr = wrap(7);
    println("wrap(7) = {} (werr reads out as {})", big, werr as int32);
    let none, werr2 = wrap(0);
    if (werr2 == file_error::NOT_FOUND) {
        println("wrap(0) propagated NOT_FOUND; none = {}", none);
    }

    return 0;
}

// See also: enums.mc for the transparent constant-set counterpart (an enum
// member folds to its underlying value; an error variant stays nominal),
// and block_expressions.mc for `emit` targeting a block, the shape the
// except handler reuses. Full rules and the staged roadmap:
// docs/language.md "Error handling".
