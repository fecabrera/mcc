import "std/io";

// `type <name> = <type>;` gives an existing type a second name. An alias is
// transparent -- not a new distinct type -- so a value typed by the alias and
// one typed by the underlying type are interchangeable without a cast.

type word = int32;     // a plain rename
type bytes = uint8*;   // pointer suffixes are part of the target...
                       // ...so `bytes*` below means `uint8**`.

// A function-pointer alias -- the readability win the feature is really for.
type binop = fn(int32, int32) -> int32;

fn add(a: int32, b: int32) -> int32 { return a + b; }
fn sub(a: int32, b: int32) -> int32 { return a - b; }

// `binop` reads better than spelling the function-pointer type at each use.
fn apply(op: binop, x: int32, y: int32) -> int32 { return op(x, y); }

// Aliases work in any type position: parameters, returns, struct fields.
struct point { x: int32; y: int32; }
type point_ref = struct point*;

fn dist2(p: point_ref) -> word { return p->x * p->x + p->y * p->y; }

// An alias may name another alias.
type number = word;

// `type` is only a keyword as `type <name> = ...`; elsewhere it is an ordinary
// identifier, so a field (or variable, or parameter) may be named `type`.
struct tagged { type: int32; value: number; }

fn main() -> int32 {
    // Alias and underlying type combine freely -- they are the same type.
    let a: word = 20;
    let b: int32 = 22;
    println("word + int32 = %d", a + b);

    // A function-pointer alias, reassignable like any variable.
    let op: binop = add;
    println("apply(add) = %d", apply(op, 10, 3));
    op = sub;
    println("apply(sub) = %d", apply(op, 10, 3));

    // Pointer alias: `bytes` is uint8*, so a string literal fits.
    let s: bytes = "aliased";
    println("bytes = %s", s);

    // Struct-pointer alias passed through.
    let pt = struct point { x = 3, y = 4 };
    println("dist2 = %d", dist2(&pt));

    // `type` used as a field name, with an alias-typed field beside it.
    let t = struct tagged { type = 7, value = 42 };
    println("tagged { type = %d, value = %d }", t.type, t.value);

    return 0;
}
