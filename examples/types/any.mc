import "std";

// `any` is the builtin tagged box: a fixed 24-byte value (a type tag plus a
// 16-byte payload, aligned to 8) that holds one value of any boxable type at
// a time. Boxing is implicit: assigning, passing, returning, or storing a
// value where an `any` is expected wraps it. The v1 boxable set is the
// primitives, pointers (each pointer type gets its own tag), and slices.
// Structs, unions, and arrays do not box; the escape hatch is boxing a
// pointer to them. The only way back out is the `case type` switch below --
// there is no `as` unwrap and no tag/payload field access.
// Prerequisites: structs.mc and unions.mc; slice<char> is covered in
// memory/slices.mc, the value form of case/when in control-flow/case_when.mc.

struct point {
    x: int32;
    y: int32;
}

// `case type` is the recovery path: each arm names one type and binds the
// unboxed payload to a fresh name scoped to that arm. Arms do not fall
// through, and the `else` arm is mandatory -- the set of types a program
// boxes elsewhere is open, so no arm list is ever exhaustive.
fn describe(a: any) {
    case type (a) {
        when int32 n:       println("int32:       %d", n);
        when float64 f:     println("float64:     %f", f);
        when bool b:        println("bool:        %s", b ? "true" : "false");
        when char* s:       println("char*:       %s", s);
        when slice<char> t: println("slice<char>: %llu chars", t.length);
        when point* p:      println("point*:      (%d, %d)", p->x, p->y);
        else:               println("some other type");
    }
}

// Boxing at return: a `-> any` function can hand back differently-typed
// values from different paths.
fn pick(text: bool) -> any {
    if (text) {
        return "chosen" as slice<char>;   // borrows the literal as a slice
    }
    return 0;
}

fn main() -> int32 {
    // The box is one fixed size no matter what it holds.
    println("sizeof(any)  = %d", sizeof(any) as int32);
    println("alignof(any) = %d", alignof(any) as int32);

    // Boxing at assignment. An untyped literal anchors at its default
    // placeholder, so 5 boxes as int32 (the same rule call-site inference
    // uses).
    let a: any = 5;
    describe(a);

    // Boxing at argument passing: each call wraps its argument.
    describe(2.5);
    describe(false);

    // Each pointer type is its own tag: this char* only matches the char*
    // arm, never uint8* or int32*.
    describe("hello");

    // A struct does not box (compile error); box a pointer to it instead.
    let origin = point { x = 3, y = 4 };
    describe(&origin);

    // Both of pick's return paths flow through the same `-> any`.
    describe(pick(true));
    describe(pick(false));

    // A type with no arm of its own lands in the mandatory `else`.
    let wide: int64 = 7;
    describe(wide);

    return 0;
}

// See also: unions.mc for the raw untagged union `any` is the safe, tagged
// counterpart to; control-flow/case_when.mc for the value form of case/when;
// case_type_groups.mc for multi-type arms, several types sharing one body
// over one binding; functions/native_variadics.mc for the box's headline
// consumer, native variadic collection.
