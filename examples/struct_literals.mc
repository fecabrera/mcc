import "std";
import "memory";

// A struct literal `struct Name { field = value, ... }` builds a whole struct
// value in one expression, instead of declaring it and assigning fields one at
// a time. Any field left out is zero-initialized, and field order doesn't
// matter.

struct point { x: int32; y: int32; }
struct pair<A, B> { a: A; b: B; }
struct line { from: struct point; to: struct point; }

// A literal is an ordinary value, so it works as a function argument...
fn length2(p: struct point) -> int32 { return p.x * p.x + p.y * p.y; }

// ...and as a return value.
fn origin() -> struct point { return struct point { }; }   // all zero

fn main() -> int32 {
    // The basic form: an initializer.
    let p = struct point { x = 3, y = 4 };
    println("p = (%d, %d)", p.x, p.y);

    // Omitted fields are zero, and order is free.
    let q = struct point { y = 9 };     // x defaults to 0
    println("q = (%d, %d)", q.x, q.y);

    // An empty literal zero-initializes everything.
    let o = origin();
    println("origin = (%d, %d)", o.x, o.y);

    // Passed straight to a function, no named temporary needed.
    println("length2(6, 8) = %d", length2(struct point { x = 6, y = 8 }));

    // Generic structs work too. The type arguments can be given explicitly...
    let pr = struct pair<int32, uint8*> { a = 42, b = "answer" };
    println("pair = (%d, %s)", pr.a, pr.b);

    // ...or inferred from the field values, like a generic function call. Here
    // A = int32 (from 7) and B = uint8* (from the string). An untyped constant
    // adapts to the field type, just as in an assignment.
    let pr2 = struct pair { a = 7, b = "inferred" };
    println("pair2 = (%d, %s)", pr2.a, pr2.b);

    // A field whose type is itself a struct takes a nested literal.
    let seg = struct line {
        from = struct point { x = 1, y = 2 },
        to   = struct point { x = 4, y = 6 },
    };
    println("segment (%d,%d) -> (%d,%d)", seg.from.x, seg.from.y, seg.to.x, seg.to.y);

    // A literal is a value, so it can be written through a pointer -- the
    // pattern an eventual `new point { ... }` sugar would build on.
    let h = alloc<struct point>(1);
    defer dealloc(h);
    *h = struct point { x = 10, y = 20 };
    println("heap point = (%d, %d)", h->x, h->y);

    return 0;
}
