import "std/io";

// A function declared `-> mut T` returns an lvalue: a reference to
// caller-reachable storage instead of a copy of the value. The call
// expression then works on both sides of `=`. It is the accessor shape,
// `_at`-style element access without handing out a raw pointer, and the
// return-side counterpart of a `mut` parameter (mut_params.mc); pointers
// are covered in memory/pointers.mc.
struct buf { data: char*; length: uint64; }

// The formation rule, checked at the callee's `return`: the returned
// lvalue must be formed from a mut/pointer parameter or a global, traced
// through members, elements, and dereferences. `self` is a mut parameter,
// so `self.data[i]` reaches the caller's bytes: legal. Anything rooted in
// this call's own frame is rejected, even a provably-safe alias --
//     let d = self.data; return d[i];
//     error: a mut return must be formed from a mut or pointer parameter
//     or a global; 'd' is a local (its storage dies with this call;
//     inline its chain into the return expression)
// A by-value or const parameter is rejected the same way, and a pointer
// parameter must be dereferenced: `return *p` reaches the caller's
// storage, but `return p` would reference the parameter's own frame slot.
fn buf_at(mut self: struct buf, i: uint64) -> mut char {
    return self.data![i];
}

// A mut parameter is itself legal as the returned lvalue (zero hops): it
// already names the caller's storage.
fn buf_ref(mut self: struct buf) -> mut struct buf {
    return self;
}

// Composition: a call that itself returns mut may continue the chain; its
// own formation rule vouches for the storage it hands back.
fn buf_first(mut self: struct buf) -> mut char {
    return buf_at(self, 0);
}

// A `-> mut T*` return: the caller indexes through the loaded pointer.
fn buf_data(mut self: struct buf) -> mut char* {
    return self.data;
}

// A global is the third legal root; the call is a zero-argument lvalue.
@static let high_score: int32 = 10;

fn high_score_ref() -> mut int32 {
    return high_score;
}

// Used below to show re-lending the returned reference.
fn bump(mut c: char) {
    c += 1;
}

// Works on generics too (formation checked per instantiation): which
// caller variable the lvalue names can be decided at runtime.
fn pick<T>(mut a: T, mut b: T, first: bool) -> mut T {
    if (first) { return a; }
    return b;
}

fn main() -> int32 {
    let bytes: char[4];
    bytes[0] = 'a'; bytes[1] = 'b'; bytes[2] = 'c'; bytes[3] = '\0';
    let b = struct buf { data = &bytes[0], length = 3 };

    // The call is assignable, exactly like a variable.
    buf_at(b, 0) = '/';

    // Compound-assignable: the target is addressed once (one accessor
    // call), then read and written through that one address.
    buf_at(b, 1) += 1;                  // 'b' + 1 = 'c'

    // Re-lendable as a mut argument: the reference forwards straight into
    // bump's mut parameter, never escaping either call. `&buf_at(b, 0)`
    // is rejected ("cannot take the address of a call result"): the
    // reference must not outlive the full expression.
    bump(buf_at(b, 2));                 // 'c' + 1 = 'd'

    // In value context the call auto-loads the current value.
    let c = buf_at(b, 0);
    println("buf_at(b, 0) -> %c", c);

    // Projections: a struct-typed mut return takes `.field`, and a
    // pointer-typed one indexes through the loaded pointer.
    buf_ref(b).length = 2;
    buf_data(b)![1] = 'z';
    buf_first(b) = 'q';                 // through the composed accessor
    println("bytes        -> %c%c%c (length %d)",
            bytes[0], bytes[1], bytes[2], b.length as int32);

    high_score_ref() += 32;
    println("high_score   -> %d", high_score);

    let x: int32 = 1;
    let y: int32 = 2;
    pick(x, y, false) = 20;             // the returned lvalue is y
    pick(x, y, true) += 9;              // ...and here x
    println("pick         -> x=%d y=%d", x, y);

    return 0;
}

// See also: mut_params.mc for `mut` parameters, the argument-side half of
// the same no-escape reference; mut_overloads.mc for how an lvalue call
// argument keeps mut overload candidates viable; docs/language.md "mut
// returns" for the full formation and storage rules.
