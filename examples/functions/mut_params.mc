import "std";

// A `mut` parameter is passed by hidden reference to the caller's storage:
// the callee's writes land in the caller's variable, but `&` on it is
// rejected, so the reference can never outlive the call. It is the
// memory-safe replacement for an out-pointer parameter (`out: int32*`) --
// no address is ever handed out.
fn set(mut out: int32) {
    out = 7;               // writes the caller's variable
}

// Reading a mut parameter loads the current value (copy on read); writing
// stores through. Both sides of `n = n + 1` work on the caller's storage.
fn bump(mut n: int32) {
    n += 1;
}

// A mut parameter can be re-lent to another mut parameter (and through
// recursion): the reference is forwarded, never escaping either call.
fn twice(mut n: int32) {
    bump(n);
    bump(n);
}

// The classic out-parameter shape: return success, deliver the value
// through `mut` -- no pointer in the signature.
fn find(haystack: slice<const int32>, needle: int32, mut index: uint64) -> bool {
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

fn mirror(mut p: struct point) {
    let t = p.x;
    p.x = p.y;
    p.y = t;
}

fn main() -> int32 {
    let x: int32 = 0;
    set(x);
    println("set(x)      -> %d", x);

    twice(x);
    println("twice(x)    -> %d", x);

    let data: int32[5] = [4, 8, 15, 16, 23];
    let at: uint64 = 0;
    if (find(data as slice<const int32>, 15, at)) {
        println("find(15)    -> index %d", at as int32);
    }

    let p = point { x = 1, y = 2 };
    mirror(p);
    println("mirror(p)   -> (%d, %d)", p.x, p.y);

    // The standard library builds on mut: `swap` exchanges two values in
    // place and `replace` stores a new value while returning the old one,
    // both generic and pointer-free at the call site (import "std").
    let a: int32 = 3;
    let b: int32 = 9;
    swap(a, b);
    println("swap(a, b)  -> a=%d b=%d", a, b);

    let old = replace(a, 100);
    println("replace(a)  -> a=%d old=%d", a, old);

    return 0;
}

// See also: const_params.mc (read-only, the dual of mut).
