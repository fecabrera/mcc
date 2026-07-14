import "std/io";

// A function type spells the return convention too: `fn(...) -> mut T` is
// the type of a mut-returning function (mut_returns.mc), so such a
// function is a legal function value -- the last function-value ban is
// gone. A call through the value is the same lvalue expression a direct
// call is: assignable, compound-assignable, projectable, and re-lendable,
// with the same guarantees, since the callee's own body already passed the
// formation rule when it compiled.
// Prerequisites: function_pointers.mc, mut_returns.mc, mut_callbacks.mc.

// The accessor shape from mut_returns.mc: hands back a writable reference
// to one byte of the caller's buffer.
struct buf { data: char*; length: uint64; }

fn buf_at(mut self: struct buf, i: uint64) -> mut char {
    return self.data![i];
}

// A whole-struct view, chained through an indirect callee below.
fn buf_ref(mut self: struct buf) -> mut struct buf {
    return self;
}

// A callback table: the accessor rides in a struct field, its return
// convention spelled in the field's type.
struct ops { at: fn(mut struct buf, uint64) -> mut char; }

// Used below to show re-lending the indirect call's reference.
fn bump(mut c: char) { c += 1; }

// Inside another `-> mut` function the value composes into a formation
// chain: the call through `view` vouches for the storage it hands back
// exactly as a named mut-returning candidate would (the direct form of
// this composition is buf_first in mut_returns.mc).
fn length_slot(view: fn(mut struct buf) -> mut struct buf,
        mut b: struct buf) -> mut uint64 {
    return view(b).length;
}

fn main() -> int32 {
    let bytes: char[4];
    bytes[0] = 'a'; bytes[1] = 'b'; bytes[2] = 'c'; bytes[3] = '\0';
    let b = struct buf { data = &bytes[0], length = 3 };

    // The bare name of a mut-returning function is a value, and `let`
    // infers the carrying type: f is fn(mut struct buf, uint64) -> mut char.
    let f = buf_at;

    // ...or spell the type out: the declared slot accepts the same function.
    let g: fn(mut struct buf, uint64) -> mut char = buf_at;

    // The call through the value is assignable, exactly like a direct
    // buf_at(b, 0) -- and compound-assignable: the target is addressed
    // once (one accessor call), then read and written through that address.
    f(b, 0) = 'z';
    g(b, 1) += 1;                       // 'b' + 1 = 'c'

    // Re-lendable as a mut argument, forwarding straight into bump's
    // parameter. `&f(b, 2)` stays rejected ("cannot take the address of a
    // call result"): the reference must not outlive the full expression.
    bump(f(b, 2));                      // 'c' + 1 = 'd'
    println(f"bytes  -> {bytes[0]}{bytes[1]}{bytes[2]}");

    // A field-held callee is written through the same way: whichever
    // expression names the function, the call is the lvalue.
    let t = struct ops { at = buf_at };
    t.at(b, 0) = 'A';
    t.at(b, 0) += 1;                    // 'A' + 1 = 'B'
    println(f"t.at   -> {bytes[0]}");

    // The formation chain through the indirect callee: length_slot returns
    // a reference into b, and the assignment lands in the caller's struct.
    length_slot(buf_ref, b) = 2;
    println(f"length -> {b.length as int32}");

    // Like the parameter conventions (mut_callbacks.mc), the return
    // convention is NOT convertible -- in either direction, with no `as`
    // hatch: a `-> mut char` call returns a pointer to the vouched storage
    // where a `-> char` call returns the value itself, so no call sequence
    // through the wrong type could be correct:
    //     let h: fn(mut struct buf, uint64) -> char = buf_at;
    // error: let h: expected fn(mut buf, uint64) -> char, got
    // fn(mut buf, uint64) -> mut char (a mut return is passed as a pointer
    // to the returned storage, a different calling convention; the types
    // are not convertible)
    return 0;
}

// See also: mut_returns.mc for direct `-> mut T` calls and the formation
// rule whose guarantees the indirect call inherits; mut_callbacks.mc for
// the parameter-side conventions in function types and the
// non-convertibility story; function_pointers.mc for plain fn values,
// tables, and callbacks; docs/language.md "mut/const-carrying function
// types" for the full rules.
