import "std";

// `char` is a distinct one-byte text type. A string literal is a NUL-terminated
// char array: "hi" has type char[3] (the 'h', the 'i', and the trailing '\0').
// It decays to a char* wherever a pointer is expected -- and char* coerces to
// uint8* like any pointer -- so a string still passes straight to a function
// that takes a uint8* (every libc string function does).

// `msg` arrives as uint8*; a string literal (char*) coerces to it.
fn shout(msg: uint8*) {
    println("%s!", msg);
}

fn main() -> int32 {
    // Bound to an array variable, the literal is an owned, mutable copy.
    // len() counts the bytes including the NUL, so "message 1" (9 chars) is 10.
    let owned = "message 1";              // inferred char[10]
    println("owned: \"%s\", len %llu, sizeof %llu",
            &owned[0], len(owned), sizeof(owned));

    owned[0] = 'M';                       // owned storage, so writable
    println("mutated: \"%s\"", &owned[0]);

    // `char[]` infers the size; an explicit, larger buffer is zero-filled past
    // the string; `char*` keeps the pointer-into-the-constant behavior (no copy).
    let inferred: char[] = "hi";          // char[3]
    let padded: char[8] = "hi";           // char[8], zeroed past "hi\0"
    let ptr: char* = "no copy";           // a pointer into the shared constant
    println("inferred len %llu, padded len %llu, ptr \"%s\"",
            len(inferred), len(padded), ptr);

    // A literal in any expression position decays to char*. A char literal is an
    // untyped constant: it defaults to char, but adapts to an integer slot too.
    shout("hello");
    let first = "abc"[0];                 // indexes the literal: 'a', a char
    let code: int32 = 'a';                // the literal adapts to int32 here
    println("first byte %d (= %d)", first as int32, code);

    // A char[N] is NUL-terminated text, so its slice<char> borrow drops the
    // terminator: view.length is the text length (8, not 9).
    let text = "slice me";                // char[9]
    let view = text as slice<char>;
    println("view.length %llu", view.length);   // 8
    print("text: ");
    for c in view {
        print("%c", c);
    }
    println("");

    // A uint8[N], by contrast, is a raw byte buffer: its slice keeps every byte.
    // (Char literals adapt to uint8 here, since the values fit.)
    let raw: uint8[3] = ['a', 'b', 'c'];
    let bytes = raw as slice<uint8>;
    println("raw bytes kept: %llu", bytes.length);   // 3, nothing dropped

    return 0;
}

// See also: string_tables.mc (string literals adapting to slice elements in
// array literals and @static initializers), memory/slices.mc.
