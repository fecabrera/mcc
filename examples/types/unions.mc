import "std/io";

// A `union` is an aggregate whose members all share one storage: its size is
// the largest member's (rounded to the alignment), and every member sits at
// offset 0. Reading a member other than the one last written reinterprets the
// same bytes -- deliberate type punning, the C-interop workhorse (epoll_data,
// sigval, and most syscall structs embed one).
union value {
    i: int64;
    f: float64;
    b: uint8[8];
}

// A union literal sets at most one member -- the live one -- and zero-fills
// the storage first, so `value { }` is all zeroes.
fn float_bits(x: float64) -> int64 {
    let v = value { f = x };
    return v.i;    // the float's raw bit pattern
}

// Members read and write through `.` and `->` exactly like struct fields.
fn low_byte(v: value*) -> uint8 {
    return v->b[0];
}

// Unions are generic like structs: one instantiation per type argument, each
// sized by its own members.
union boxed<T> {
    typed: T;
    raw:   uint64;
}

fn main() -> int32 {
    // sizeof is the largest member; every member is at offset 0.
    println("sizeof(value) = %d", sizeof(union value) as int32);
    println("offsetof(f)   = %d", offsetof(union value, f) as int32);

    // 1.0 is 0x3FF0000000000000: exponent bits land in the high byte.
    let bits = float_bits(1.0);
    println("bits(1.0)     = %lx", bits);

    // Poking one member is visible through the others (little-endian here).
    let v = value { i = 0x41 };
    println("low byte      = %c", low_byte(&v) as int32);

    // A generic union adapts its storage to the instantiation.
    let box: union boxed<float64>;
    box.typed = 2.5;
    println("raw(2.5)      = %lx", box.raw);

    return 0;
}

// See also: any.mc for the builtin `any` box, the tagged counterpart to this
// raw storage sharing: it remembers which type it holds and recovers it
// safely through `case type`; and static_initializers.mc for union literals
// that initialize `@static` globals, folded to data constants at compile time.
