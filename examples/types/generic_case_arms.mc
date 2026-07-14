import "std/io";

// A `case type` arm may be generic. No new syntax marks it: a bare arm-type
// name that resolves (a builtin, struct, alias, enum, or an enclosing
// generic's binding) is a concrete arm, and an unresolved bare name with at
// most one `*` introduces an arm-scoped type parameter. `when T* ptr:`
// matches every boxed pointer tag no earlier arm claimed, with `T` bound to
// the pointee; `when T v:` matches every remaining tag, with `T` bound to
// the boxed type itself. Each generic arm is a real generic context: its
// body compiles once per matching tag drawn from the whole program's boxed
// set, each copy fully type-checked.
// Prerequisites: any.mc for the box and concrete arms; case_type_groups.mc
// for the copy-per-type compilation model these arms generalize; generics.mc
// (later in this folder) for monomorphization itself.

struct point {
    x: int32;
    y: int32;
}

// A generic callee for the value arm to dispatch into. The arm instantiates
// it once per matching tag; an overload set works the same way, but then
// every boxed type in the program needs a viable member, and one without is
// a compile error at the `case type` naming the offending type.
fn payload_width<T>(v: T) -> int32 {
    return sizeof(T) as int32;
}

fn show(a: any) {
    case type (a) {
        // Dispatch is first-match-wins in textual order, so a concrete arm
        // above a generic one carves its tag out of the fallback: char*
        // stops here and never reaches the pointer arm below. The reverse
        // order is rejected outright, not left as a silent dead arm:
        // `case type arm for char* is unreachable: the generic pointer arm
        // 'T*' above it matches every pointer type`.
        when char* s:
            println(f"string:  {s}");

        // The pointer fallback. T binds per tag to the pointee (point* runs
        // a copy with T = point, int32* a copy with T = int32), and the
        // binding is the pointer itself, so sizeof(T) is the pointee's size.
        when T* ptr:
            println(f"pointer: {sizeof(T) as int32}-byte pointee");

        // The value fallback: every tag still unclaimed. T binds to the
        // boxed type itself. Without the pointer arm above, pointer tags
        // would land here too, with T = point* and so on.
        when T v:
            println(f"value:   {payload_width(v)}-byte payload");

        // `else` stays mandatory even under a catch-all `when T v:` arm.
        // The generic arms cover every tag the program boxes, but not the
        // zero-filled `any` (tag 0), which matches no arm and lands here.
        else:
            println("empty box");
    }
}

// A file-scoped `any` is zero-initialized (an `any` initializer is not
// supported at file scope yet), so it holds tag 0 and matches only `else`.
// A plain local `let empty: any;` would hold garbage instead, like a C local.
@static let empty: any;

fn main() -> int32 {
    // The concrete arm claims the char* tag.
    show("hello");

    // Two pointer tags, one arm, one copy of the body per pointee type.
    let origin = point { x = 3, y = 4 };
    let n: int32 = 7;
    show(&origin);
    show(&n);

    // Three value tags for the `when T v:` arm.
    show(42);
    show(2.5);
    show(false);

    // The zero-filled box reaches the mandatory else.
    show(empty);

    return 0;
}

// See also: any.mc for the box itself and single-type concrete arms;
// case_type_groups.mc for multi-type arms, the halfway point between
// concrete and generic; generics.mc for monomorphization in general;
// typename.mc, whose typename(T) inside these arms names a box's dynamic
// type statically; with_unwrap.mc for the `with` statement, a one-arm sugar
// whose pattern follows this exact detection rule (and shares the
// assumed-to-reach-the-end caveat for generic patterns);
// functions/native_variadics.mc, whose for + case type walk these arms make
// generic.
