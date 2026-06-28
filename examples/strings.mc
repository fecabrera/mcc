import "std";

// A string literal is a NUL-terminated byte array: "hi" has type uint8[3]
// (the 'h', the 'i', and the trailing '\0'). It decays to a uint8* wherever a
// pointer is expected -- exactly like any other array -- so it still passes
// straight to functions that take a uint8*.

// `msg` decays to uint8* here; the callee never sees the array length.
fn shout(msg: uint8*) {
    println("%s!", msg);
}

fn main() -> int32 {
    // Bound to an array variable, the literal is an owned, mutable copy.
    // len() counts the bytes including the NUL, so "message 1" (9 chars) is 10.
    let owned = "message 1";              // inferred uint8[10]
    println("owned: \"%s\", len %llu, sizeof %llu",
            &owned[0], len(owned), sizeof(owned));

    owned[0] = 'M';                       // owned storage, so writable
    println("mutated: \"%s\"", &owned[0]);

    // `uint8[]` infers the size; an explicit, larger buffer is zero-filled past
    // the string; `uint8*` keeps the old pointer-into-the-constant behavior
    // (no copy).
    let inferred: uint8[] = "hi";         // uint8[3]
    let padded: uint8[8] = "hi";          // uint8[8], zeroed past "hi\0"
    let ptr: uint8* = "no copy";          // a pointer into the shared constant
    println("inferred len %llu, padded len %llu, ptr \"%s\"",
            len(inferred), len(padded), ptr);

    // A literal in any expression position decays to uint8*, as always.
    shout("hello");
    let first = "abc"[0];                 // indexes the literal: 'a' (97)
    println("first byte %d", first as int32);

    // An owned byte array borrows as a slice<uint8> (a { ptr, length } view),
    // so it iterates natively. A uint8[N] is a NUL-terminated string, so the
    // borrow drops the terminator: view.length is the text length (8, not 9).
    let bytes = "slice me";               // uint8[9]
    let view = bytes as slice<uint8>;
    println("view.length %llu", view.length);   // 8
    print("bytes: ");
    for c in view {
        print("%c", c);
    }
    println("");

    return 0;
}
