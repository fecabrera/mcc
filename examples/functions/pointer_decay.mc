import "std/io";
import "std/memory";

// Pointer decay into `const`/`mut` parameters: a proven-non-null `T*`
// argument at a hidden-reference slot (a `const T` struct parameter, or a
// `mut T` parameter of any type) implicitly dereferences. The pointer value
// is forwarded as the hidden reference, so a stack value and a heap pointer
// call the same function with the same spelling, no `*p` at the call site.
// Prerequisites: mut_params.mc and const_params.mc for the two slots,
// nonnull_narrowing.mc for the null-check proof, memory/pointers.mc for the
// heap.

struct point { x: int32; y: int32; }

// Both parameters travel as hidden references: `p` is written through,
// `by` is read-only. Nothing here is decay-specific; the call sites decide.
fn shift(mut p: struct point, const by: struct point) {
    p.x += by.x;
    p.y += by.y;
}

fn main() -> int32 {
    let delta = point { x = 10, y = 20 };

    // A stack value: the ordinary mut/const call from mut_params.mc.
    let s = point { x = 1, y = 2 };
    shift(s, delta);
    println(f"stack -> ({s.x}, {s.y})");

    // A heap value: new<point>() returns a nullable point*. A reference is
    // never null, so decay demands proof; this one diverging guard narrows
    // `h` for the whole rest of the scope (an unproven `shift(h, ...)` here
    // would be a compile error naming this exact fix).
    let h = new<point>();
    if (h == null) { return 1; }
    h->x = 1;
    h->y = 2;

    // The same call shape as the stack value: `h` decays into the mut slot.
    shift(h, delta);

    // The pointer passed by value, so the narrowed fact survives the call:
    // `h` is still proven non-null, no re-guard before these reads.
    println(f"heap  -> ({h->x}, {h->y})");

    // Rvalue pointers decay too, here `&s` into the const slot -- and `&s`
    // is its own proof. (The explicit `shift(*h, ...)` spelling stays legal
    // and needs no proof; the dereference is just visible at the call site.)
    shift(h, &s);
    println(f"heap  -> ({h->x}, {h->y})");

    dealloc(h);
    return 0;
}

// Decay is exactly one level deep and only into hidden-reference slots: a
// `const` scalar or a plain by-value `T` parameter still needs an explicit
// `*p`. See also: mut_params.mc and const_params.mc for the receiving slots;
// nonnull_narrowing.mc and nonnull_assert.mc for the ways a pointer becomes
// proven; memory/pointers.mc for new/dealloc.
