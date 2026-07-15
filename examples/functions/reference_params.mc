import "std/io";
import "std/utils";

// An `&` parameter (`out: &int32`) is passed by hidden reference to the
// caller's storage: the callee's writes land in the caller's variable, but
// `&` on it is rejected, so the reference can never outlive the call. It is
// the memory-safe replacement for an out-pointer parameter (`out: int32*`)
// -- no address is ever handed out.
//
// `&T` is the blessed spelling: the reference marker goes in the type slot,
// as in `-> &T` returns (reference_returns.mc). (`mut` was once an
// accepted-but-deprecated alias for it; it is no longer a keyword at all, so
// it is now an ordinary identifier -- see main.)
fn set(out: &int32) {
    out = 7;               // writes the caller's variable
}

// Reading a reference parameter loads the current value (copy on read); writing
// stores through. Both sides of `n = n + 1` work on the caller's storage.
fn bump(n: &int32) {
    n += 1;
}

// A reference parameter can be re-lent to another reference parameter (and through
// recursion): the reference is forwarded, never escaping either call.
fn twice(n: &int32) {
    bump(n);
    bump(n);
}

// The classic out-parameter shape: return success, deliver the value
// through `&` -- no pointer in the signature. (When a failure deserves a
// named cause, `result<T, E>` carries the value and the error in one
// return instead: see types/error_handling.mc.)
fn find(haystack: slice<const int32>, needle: int32, index: &uint64) -> bool {
    for pair in enumerate(haystack) {
        if (pair.value == needle) {
            index = pair.index;
            return true;
        }
    }
    return false;
}

// Structs work too, with field projection writing through.
struct point { x: int32; y: int32; }

fn mirror(p: &struct point) {
    let t = p.x;
    p.x = p.y;
    p.y = t;
}

fn main() -> int32 {
    let x: int32 = 0;
    set(x);
    println(f"set(x)      -> {x}");

    twice(x);
    println(f"twice(x)    -> {x}");

    let data: int32[5] = [4, 8, 15, 16, 23];
    let at: uint64 = 0;
    if (find(data as slice<const int32>, 15, at)) {
        println(f"find(15)    -> index {at as int32}");
    }

    let p = point { x = 1, y = 2 };
    mirror(p);
    println(f"mirror(p)   -> ({p.x}, {p.y})");

    // The standard library builds on references: `swap` exchanges two values in
    // place and `replace` stores a new value while returning the old one,
    // both generic and pointer-free at the call site (import "std/utils").
    let a: int32 = 3;
    let b: int32 = 9;
    swap(a, b);
    println(f"swap(a, b)  -> a={a} b={b}");

    let old = replace(a, 100);
    println(f"replace(a)  -> a={a} old={old}");

    // `mut` is no longer a keyword, so it is usable as an ordinary name.
    let mut: int32 = 42;
    println(f"mut (ident) -> {mut}");

    return 0;
}

// See also: const_params.mc (read-only, the dual of `&`); reference_returns.mc
// for the return-side counterpart, `-> &T` functions returning lvalues;
// reference_overloads.mc for overloads of one generic name mixing `&` and
// non-`&` positions; pointer_decay.mc for a proven-non-null T* decaying
// into a reference slot; reference_callbacks.mc for `fn(&T)` function types, which
// make a reference-taking function a legal callback and dispatch-table entry.
