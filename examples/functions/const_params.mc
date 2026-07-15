import "std/io";

// A `const` parameter is read-only: the body may not assign to it, to one of
// its fields or array elements, or take its address with `&`. Since Phase B of
// the `&`-reference redesign, `const` on a binder chooses between two ways of
// receiving that read-only value:
//
//   * `const x: T`  -- a by-value read-only COPY, of every type (the ordinary
//                      calling convention; read-only only to the callee).
//   * `const x: &T` -- a read-only hidden REFERENCE: a pointer to the caller's
//                      storage, available uniformly on all types, no copy.
//
// (A third, unrelated meaning is the type-level `const` in `slice<const char>`
// -- a read-only element, not a parameter convention.)

struct vec3 { x: float64; y: float64; z: float64; }

// By-value `const`: `v` is a private read-only copy of the caller's vec3.
// For a small plain-data struct the copy is cheap and the value semantics are
// exactly what you want. A `v.x = ...` here would be a compile error.
fn magnitude_squared(const v: struct vec3) -> float64 {
    return v.x * v.x + v.y * v.y + v.z * v.z;
}

// The `const &` view: `a` and `b` are hidden references to the caller's
// storage -- no copy of the three doubles -- yet still read as plain values
// (`a.x`, not `a->x`). Prefer this for larger structs, and required for a
// type that owns a resource (a by-value copy of one would alias it; see
// destructors.mc and -Wdestructor-copy).
fn dot(const a: &struct vec3, const b: &struct vec3) -> float64 {
    return a.x * b.x + a.y * b.y + a.z * b.z;
}

// A `const &` view forwards straight to another view -- still no copy.
fn length_squared(const v: &struct vec3) -> float64 {
    return dot(v, v);
}

// `const &` is available on scalars too (new in Phase B): a read-only
// reference to the caller's int64, read like a plain value.
fn triple(const n: &int64) -> int64 {
    return n * 3;
}

// `const` on a *pointer* parameter freezes the pointer, not what it points at:
// `c` cannot be reassigned, but `c->...` may still be written -- the same
// distinction as C's `counter* const` versus `const counter*`.
struct counter { hits: int64; }
fn record(const c: struct counter*) {
    c!->hits += 1;   // allowed: writes the pointee, not the pointer
}

// On a scalar, plain `const` simply makes the by-value parameter read-only.
fn scaled(const n: int64, const factor: int64) -> int64 {
    return n * factor;
}

fn main() -> int32 {
    let a = struct vec3 { x = 1.0, y = 2.0, z = 2.0 };
    let b = struct vec3 { x = 0.0, y = 3.0, z = 4.0 };

    println(f"magnitude_squared = {magnitude_squared(a) as int32}");  // 1+4+4 = 9
    println(f"length_squared = {length_squared(a) as int32}");        // 9
    println(f"dot = {dot(a, b) as int32}");                           // 0+6+8 = 14

    let k = 7 as int64;
    println(f"triple = {triple(k) as int32}");                        // 21

    let c = struct counter { hits = 0 };
    record(&c);
    record(&c);
    record(&c);
    println(f"hits = {c.hits as int32}");                             // 3

    println(f"scaled = {scaled(6, 7) as int32}");                     // 42
    return 0;
}

// See also: reference_params.mc (write-through `&T`, the dual of `const &`),
// the pointer promises in noalias.mc (@noalias) and nonnull.mc (@nonnull),
// pointer_decay.mc for a proven-non-null T* decaying into a `const &` slot,
// reference_callbacks.mc for `const`/`&` conventions carried into function
// types, and destructors.mc for why an owning type must be taken by `const &`.
