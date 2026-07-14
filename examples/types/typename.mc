import "std/io";

// `typename(...)` recovers the canonical name of a type as a string. It
// mirrors `sizeof` in every surface respect: the operand is a type or, as a
// bare name in scope, a variable (naming its static type; the operand is
// never evaluated), and it folds at compile time to an ordinary rodata
// string literal, a `char*`, sharing bytes with every other literal spelling
// the same characters. Value-level by design: the name flows anywhere a
// string literal can, `const` and `@static` initializers included.
// Prerequisites: arrays.mc for sizeof, strings.mc for char*; generics.mc
// (later in this folder) for monomorphization; any.mc and
// generic_case_arms.mc for the `case type` composition at the end.

// A generic struct instantiation names with its type arguments spelled out.
struct box<T> {
    value: T;
}

// The fold happens wherever sizeof's does: a file-scoped `const` holds the
// name as a plain compile-time string constant.
const BYTE_NAME = typename(uint8);

// In a generic, typename(T) resolves per instantiation: monomorphization
// gives each copy its own literal.
fn describe<T>(v: T) {
    println(f"{typename(T)}: {sizeof(T) as int32} bytes");
}

// The headline composition. A generic `case type` arm is a real generic
// context, so typename(T) names the dynamic type of the boxed `any` per
// tag, statically, with zero runtime machinery: no descriptors, no
// registry. (Contrast with typename on the box itself in main below.)
fn show(a: any) {
    case type (a) {
        // In a pointer arm T binds to the pointee, and the binding is the
        // pointer: typename(T) names the pointee, typename(ptr) the pointer.
        when T* ptr:
            println(f"a boxed {typename(ptr)} (pointee {typename(T)})");
        when T v:
            println(f"a boxed {typename(T)}");
        else:
            println("an empty box");
    }
}

fn main() -> int32 {
    // A type operand folds to the compiler's canonical spelling: exactly
    // the string the `any` tags hash and the diagnostics print, so it is
    // deterministic across compilations.
    println(f"{typename(int64)}");         // int64
    println(f"{typename(int32**)}");       // int32**
    println(f"{typename(slice<int32>)}");  // slice<int32>
    println(f"{typename(box<int64>)}");    // box<int64>

    // A bare name in scope names the variable's static type. A top-level
    // `const` strips, matching what boxing does with tags, so both of these
    // print plain "float64" / "int64".
    let x: const float64 = 1.5;
    println(f"{typename(x)}");             // float64
    println(f"{typename(const int64)}");   // int64

    // The result is an ordinary string value: here one that folded into a
    // file-scoped const initializer above.
    println(f"{BYTE_NAME} is {sizeof(uint8) as int32} byte");   // uint8 is 1 byte

    // typename never looks through a box at runtime: an `any` variable
    // names as its STATIC type, "any", no matter what it holds.
    let a: any = 5;
    println(f"{typename(a)}");             // any, not int32

    // Per-instantiation resolution: each call stamps its own name.
    describe(7);        // int32: 4 bytes (an untyped literal anchors at int32)
    describe(2.5);      // float64: 8 bytes

    // The generic arms recover the DYNAMIC name of the same values, plus a
    // string literal landing in the pointer arm under its char* tag.
    show(7);            // a boxed int32
    show(2.5);          // a boxed float64
    show("hi");         // a boxed char* (pointee char)

    return 0;
}

// See also: generic_case_arms.mc for the generic arms themselves (dispatch
// order, the pointer/value fallbacks, the mandatory else); any.mc for the
// box and its tags, which typename's canonical spellings are the preimage
// of; generics.mc for monomorphization in general.
