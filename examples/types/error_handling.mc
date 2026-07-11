import "std/io";

// Error handling, stage 1: the `error` declaration naming a set of failure
// causes, the builtin `result<T, E>` / `result<E>` type carrying either an
// ok value or an error, and the explicit ok()/error() constructors.
// Recoverable errors are ordinary values: no exceptions, no unwinding.
//
// Prerequisites: enums.mc (case/when, `Name::Member` reading) and structs.mc
// (a result stored in a struct field).
//
// This stage ships declarations, the type, and the constructors. The
// consumption forms (the `let ret, err = f();` destructure, `except`
// handler blocks, `try`) are the next stages of the roadmap epic, so this
// example constructs, returns, passes, and stores results, and tests error
// values directly.

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
// `e + 1`, `e < f`, and `return 1;` into a file_error all reject.
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

// error() takes any expression of the declared error type, not just a
// member: the shape the future propagation idiom builds on.
fn relabel(e: file_error) -> result<int32, file_error> {
    return error(e);
}

// A result is an ordinary value: it passes and returns by value like any
// other. It exposes NO fields, though, and cannot be destructured; until
// the binding forms land, a callee can only carry it onward.
fn keep(r: result<int32, file_error>) -> result<int32, file_error> {
    return r;
}

// A result works as a struct field like any value type.
struct lookup {
    key: int32;
    outcome: result<int32, file_error>;
}

fn main() -> int32 {
    // Reading the numeric value out is an explicit escape, `as int32`; no
    // cast goes the other way. (An error value does not box into `{}`
    // either, by design: print the read-out or a describe()-style string.)
    println("NOT_FOUND = {}, PERMISSION = {}",
            file_error::NOT_FOUND as int32, file_error::PERMISSION as int32);
    println("EXHAUSTED = {}, TIMEOUT = {}",
            file_error::EXHAUSTED as int32, file_error::TIMEOUT as int32);

    // Truthiness tests against the reserved zero state. Every declared
    // variant is non-zero by construction, so a held cause is always true;
    // the zero state itself becomes reachable once the binding forms land,
    // making `if (err)` the total did-it-fail check.
    let e = file_error::PERMISSION;
    if (e) { println("e holds a cause: {}", describe(e)); }

    // Equality against members of the same declaration.
    if (e == file_error::PERMISSION) { println("e == PERMISSION"); }
    if (e != file_error::NOT_FOUND)  { println("e != NOT_FOUND"); }

    // Constructing and moving results around. ok()/error() are
    // context-typed like a bare struct literal: legal in a return, a typed
    // let, an assignment, an argument, or a struct field, and an error
    // elsewhere (`let r = ok(5);` alone has no type to adapt to).
    let good = find(2);          // ok path
    let bad = find(0);           // error path: same type, opaque either way
    let solo = flush(false);     // the one-argument result<file_error>

    // A typed let and a reassignment are both result contexts.
    let pending: result<int32, file_error> = ok(5);
    pending = error(file_error::TIMEOUT);

    // Passed and returned by value, stored in a struct field, reassigned.
    let copied = keep(good);
    let entry = struct lookup { key = 2, outcome = copied };
    entry.outcome = relabel(file_error::EXHAUSTED);
    entry.outcome = bad;

    println("results constructed, passed, and stored");
    return 0;
}

// See also: enums.mc for the transparent constant-set counterpart (an enum
// member folds to its underlying value; an error variant stays nominal).
// Full rules and the staged roadmap: docs/language.md "Error handling".
