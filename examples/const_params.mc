import "std";

// A `const` parameter is read-only: the body may not assign to it, to one of
// its fields or array elements, or take its address with `&`. For a *struct*,
// `const` also changes how it is passed -- by a hidden pointer to the caller's
// storage instead of a by-value copy. You get value semantics (the callee sees
// the struct, never mutates the caller's) without hand-writing a pointer or
// paying for the copy.

struct vec3 { x: float64; y: float64; z: float64; }

// `a` and `b` are passed by hidden reference: no copy of the three doubles, yet
// `dot` still treats them as plain values (`a.x`, not `a->x`). A `a.x = ...`
// here would be a compile error -- the parameter is read-only.
fn dot(const a: struct vec3, const b: struct vec3) -> float64 {
    return a.x * b.x + a.y * b.y + a.z * b.z;
}

// A const value forwards straight to another const parameter -- still no copy.
fn length_squared(const v: struct vec3) -> float64 {
    return dot(v, v);
}

// `const` on a *pointer* parameter freezes the pointer, not what it points at:
// `c` cannot be reassigned, but `c->...` may still be written -- the same
// distinction as C's `counter* const` versus `const counter*`.
struct counter { hits: int64; }
fn record(const c: struct counter*) {
    c->hits = c->hits + 1;   // allowed: writes the pointee, not the pointer
}

// On a scalar, `const` simply makes the parameter read-only.
fn scaled(const n: int64, const factor: int64) -> int64 {
    return n * factor;
}

fn main() -> int32 {
    let a: struct vec3;
    a.x = 1.0; a.y = 2.0; a.z = 2.0;
    let b: struct vec3;
    b.x = 0.0; b.y = 3.0; b.z = 4.0;

    println("length_squared = %d", length_squared(a) as int32);   // 1+4+4 = 9
    println("dot = %d", dot(a, b) as int32);                       // 0+6+8 = 14

    let c: struct counter;
    c.hits = 0;
    record(&c); record(&c); record(&c);
    println("hits = %d", c.hits as int32);                         // 3

    println("scaled = %d", scaled(6, 7) as int32);                 // 42
    return 0;
}
