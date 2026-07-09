import "std";
import "memory";

// A struct literal `Name { field = value, ... }` builds a whole struct value
// in one expression, instead of declaring it and assigning fields one at a
// time. The `struct` keyword is optional (`struct Name { ... }` means the same
// thing). Any field left out is zero-initialized, and field order doesn't
// matter.

struct point { x: int32; y: int32; }
struct pair<A, B> { a: A; b: B; }
struct line { from: struct point; to: struct point; }

// A field whose type is a char slice / slice<T> accepts a string or array
// literal directly: it borrows into the field with no explicit `as`, the same
// adaptation a `let` or a function argument allows.
struct command { name: slice<const char>; args: slice<const int32>; }

// A field may declare a default with `= value`; a literal that omits the field
// uses the default instead of zero.
struct config { capacity: int32 = 16; verbose: int32 = 0; name: char*; }

// A literal is an ordinary value, so it works as a function argument...
fn length2(p: struct point) -> int32 { return p.x * p.x + p.y * p.y; }

// ...and as a return value.
fn origin() -> struct point { return point { }; }   // all zero

fn main() -> int32 {
    // The basic form: an initializer.
    let p = point { x = 3, y = 4 };
    println("p = (%d, %d)", p.x, p.y);

    // The keyword form is the same literal, spelled out.
    let k = struct point { x = 3, y = 4 };
    println("k = (%d, %d)", k.x, k.y);

    // Omitted fields are zero, and order is free.
    let q = point { y = 9 };     // x defaults to 0
    println("q = (%d, %d)", q.x, q.y);

    // An empty literal zero-initializes everything.
    let o = origin();
    println("origin = (%d, %d)", o.x, o.y);

    // Passed straight to a function, no named temporary needed.
    println("length2(6, 8) = %d", length2(point { x = 6, y = 8 }));

    // Generic structs work too. The type arguments can be given explicitly...
    let pr = pair<int32, char*> { a = 42, b = "answer" };
    println("pair = (%d, %s)", pr.a, pr.b);

    // ...or inferred from the field values, like a generic function call. The
    // types come from the *typed* values: A = int32 from `n`, B = char* from
    // the string. A bare untyped constant like `7` can't anchor a type
    // parameter -- that is the same ambiguity `let a = 0` is -- but it still
    // adapts to a parameter another field has already fixed.
    let n: int32 = 7;
    let pr2 = pair { a = n, b = "inferred" };
    println("pair2 = (%d, %s)", pr2.a, pr2.b);

    // A field whose type is itself a struct takes a nested literal.
    let seg = line {
        from = point { x = 1, y = 2 },
        to   = point { x = 4, y = 6 },
    };
    println("segment (%d,%d) -> (%d,%d)", seg.from.x, seg.from.y, seg.to.x, seg.to.y);

    // Default field values: the omitted fields take their declared defaults
    // (capacity = 16, verbose = 0), while a given field overrides its default.
    let cfg = config { name = "db" };
    println("config: capacity=%d verbose=%d name=%s", cfg.capacity, cfg.verbose, cfg.name);

    // Declaring a default also makes a bare `let` default-initialized (the same
    // as `config { }`), rather than leaving the value uninitialized.
    let dfl: struct config;
    println("default config: capacity=%d verbose=%d", dfl.capacity, dfl.verbose);

    // A literal is a value, so it can be written through a pointer -- the
    // pattern an eventual `new point { ... }` sugar would build on.
    let h = alloc<struct point>(1);
    defer dealloc(h);
    *h = point { x = 10, y = 20 };
    println("heap point = (%d, %d)", h->x, h->y);

    // A string or array literal in a slice-typed field borrows into it with no
    // explicit `as`. The string borrow drops the trailing NUL, so `.length` is
    // the text length (2 for "ls"); the array literal views a hidden backing
    // array, so its `.length` is the exact element count.
    let cmd = command { name = "ls", args = [1, 2, 3] };
    writestr(cmd.name);
    println(": %llu args", cmd.args.length);   // "ls: 3 args"

    // In a generic struct a literal field never drives type inference: it sits
    // out, like a bare untyped constant, and borrows once the type is fixed by
    // the *typed* fields. Here A = int32 comes from `n`; the string adapts to
    // the concrete slice<const char> field.
    let row = pair<int32, slice<const char>> { a = n, b = "row" };
    writestr(row.b);
    println(" #%d", row.a);                    // "row #7"

    // The one place a bare literal is not allowed is a `for x in ... {` header,
    // where the `{` always starts the loop body; parenthesize to iterate a
    // literal there: `for x in (A { ... }) { ... }`.

    return 0;
}

// See also: static_initializers.mc, where these same literals initialize
// `@static` globals -- folded to data constants at compile time; and
// string_tables.mc / memory/slices.mc for the slice borrow the string- and
// array-literal fields above rely on. The field assignment `cmd.name = "hi"`
// mirrors the `command { name = "ls" }` literal above; see
// memory/slice_assignment.mc for that and the other assignment lvalue forms.
