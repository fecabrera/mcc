import "std/io";

// `any` is the builtin tagged box: a fixed 24-byte value (a type tag plus a
// 16-byte payload, aligned to 8) that holds one value of any boxable type at
// a time. Boxing is implicit: assigning, passing, returning, or storing a
// value where an `any` is expected wraps it. The v1 boxable set is the
// primitives, pointers (each pointer type gets its own tag), and slices.
// Structs, unions, and arrays do not box by value; the escape hatch is boxing
// a pointer to them. (A struct additionally boxes by hidden reference into a
// `const any` target -- see any_struct_boxing.mc.) The only way back out is a checked test: the `case type`
// switch below, or its single-pattern statement sugar `with` (see
// with_unwrap.mc) -- there is no unchecked `as` unwrap and no tag/payload
// field access.
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
        when int32 n:       println("int32:       {}", n);
        when float64 f:     println("float64:     {}", f);
        when bool b:        println("bool:        {}", b ? "true" : "false");
        when char* s:       println("char*:       {}", s);
        when slice<char> t: println("slice<char>: {} chars", t.length);
        when point* p:      println("point*:      ({}, {})", p!->x, p!->y);
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

// Boxing at global scope: a `@static` (or top-level) `any` takes a constant
// initializer, folded at compile time into a constant tagged box under the
// same tags runtime boxing produces. The same anchoring rule applies, so
// PORTS boxes as int32 (the `const` folds through first), and a string
// literal boxes as char* exactly as `describe("hello")` below does. The
// owning-box rules are unchanged: a struct, union, or array literal or a
// bare `null` is rejected with the same message as at runtime -- and a
// global is an owning slot even declared `const any`, so the hidden-
// reference struct carve-out (any_struct_boxing.mc) never applies here.
const PORTS = 40 + 2;

@static let g_count: any = PORTS;      // int32, via the folded const
@static let g_ratio: any = 1.5;        // float64
@static let g_name:  any = "static";   // char*, never a slice
@static let g_empty: any;              // no initializer: zero-filled,
                                       // tag 0 matches only `else`

fn main() -> int32 {
    // The box is one fixed size no matter what it holds.
    println("sizeof(any)  = {}", sizeof(any) as int32);
    println("alignof(any) = {}", alignof(any) as int32);

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

    // A struct does not box by value; box a pointer to it instead. (A struct
    // does box by hidden reference into a `const any`, e.g. a variadic's
    // slice<const any> -- see any_struct_boxing.mc.)
    let origin = point { x = 3, y = 4 };
    describe(&origin);

    // Both of pick's return paths flow through the same `-> any`.
    describe(pick(true));
    describe(pick(false));

    // A type with no arm of its own lands in the mandatory `else`.
    let wide: int64 = 7;
    describe(wide);

    // The globals boxed at compile time recover through the exact same
    // `case type` arms as the runtime boxes above.
    describe(g_count);
    describe(g_ratio);
    describe(g_name);
    describe(g_empty);   // zero-filled, so `else`

    return 0;
}

// See also: unions.mc for the raw untagged union `any` is the safe, tagged
// counterpart to; control-flow/case_when.mc for the value form of case/when;
// case_type_groups.mc for multi-type arms, several types sharing one body
// over one binding; generic_case_arms.mc for generic arms, `when T* ptr:`
// and `when T v:` fallbacks over every tag no concrete arm claims;
// with_unwrap.mc for `with (t = v as T)`, the one-arm statement sugar with
// an optional else;
// any_struct_boxing.mc for a struct boxing by hidden reference into a
// `const any`, recovered by a `when point p:` arm with no copy;
// functions/native_variadics.mc for the box's headline consumer, native
// variadic collection;
// static_initializers.mc for `@static` struct and union globals, the
// aggregate counterparts of the constant boxed globals above.
