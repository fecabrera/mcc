import "std/io";
import "std/string";
import "std/dict";

// @accessor: the method behind a type's `[]` operator. `xs[i]` calls
// `list<int32>::at(xs, i)` -- the annotation says "index me through this
// method", so `[]` on a struct desugars to an ordinary method call. Nothing
// else changes: the call spellings `xs.at(i)` / `list<int32>::at(xs, i)`
// stay valid beside `[]`, and generics, inheritance, and overload dispatch
// all carry through. The indices are ordinary arguments: any number, of any
// type. Natively indexable types (a pointer, array, slice, or tuple) keep
// native `[]` -- an accessor never competes with it.
//
// Prerequisites: properties.mc for the same bare-vs-get/set split on field
// syntax, method_calls.mc for the dot-call desugar, and
// functions/mut_returns.mc for the `-> mut` lvalue the bare form rides on.

// A 2D grid: TWO indices, mapped onto one backing array. `g[r, c]` is
// `grid::at(g, r, c)` -- each index becomes one argument.
struct grid {
    cells: int32[16];
}

// The bare form: returning `-> mut int32` re-lends the element's storage
// (functions/mut_returns.mc), so `g[r, c]` is an assignable lvalue --
// `g[r, c] = v` writes through the mut return, `g[r, c] += v` compounds.
@accessor
fn grid::at(mut self: grid, r: uint64, c: uint64) -> mut int32 {
    return self.cells[r * 4 + c];
}

// For elements that need LOGIC on the write path, @accessor("get") /
// @accessor("set") declare an explicit pair (the same split as
// @property("get")/("set")): `b[i]` calls the getter, `b[i] = v` the setter
// -- indices first, the assigned value last -- and `b[i] op= v` is
// read-modify-write through both. Here the setter clamps writes to one byte.
struct bytes {
    raw: int32[4];
}

@accessor("get")
fn bytes::at(const self: bytes, i: uint64) -> int32 {
    return self.raw[i];
}

@accessor("set")
fn bytes::at(mut self: bytes, i: uint64, value: int32) {
    self.raw[i] = value < 0 ? 0 : (value > 255 ? 255 : value);
}

// All @accessor methods of one type share one name (`[]` carries no method
// name to pick by), but within the family the indices dispatch as ordinary
// overloads -- and a generic accessor resolves per instantiation.

fn main() -> int32 {
    // The bare form: read, write, and compound-assign through `[]`.
    let g: grid;
    g[1, 2] = 40;
    g[1, 2] += 2;
    println("g[1, 2] = {}", g[1, 2]);                   // 42
    // Both spellings reach the same method.
    println("call spelling: {}", g.at(1, 2));           // 42

    // The get/set pair: writes run the setter's logic, so the clamp is
    // unbypassable through `[]`.
    let b: bytes;
    b[0] = 300;                                         // clamps to 255
    b[1] = -5;                                          // clamps to 0
    b[1] += 7;                                          // RMW: set(get() + 7)
    println("b[0] = {}, b[1] = {}", b[0], b[1]);        // 255, 7

    // The stdlib's bare accessor: std/list marks list<T>::at @accessor, so
    // elements read and write like array slots -- and string = list<char>
    // inherits it.
    let xs = list<int32>();
    xs.push(1);
    xs.push(2);
    xs[0] = 10;
    xs[1] *= 4;
    println("xs = [{}, {}]", xs[0], xs[1]);             // [10, 8]

    let s = string("hallo");
    s[1] = 'e';
    println("s[1] = {}, fixed: {}", s[1], s.equals("hello"));  // e, true

    // The stdlib's get/set pair: std/dict marks dict<V>::at with the pair,
    // so a dict indexes BY KEY -- the index is a char*, not an integer.
    // `d[k] = v` inserts or updates through dict's own set (growth and key
    // copying included); `d[k]` reads unchecked, so guard with `.has`.
    let d = dict<int32>();
    d["answer"] = 40;                                   // insert
    d["answer"] += 2;                                   // RMW through get+set
    if (d.has("answer"))
        println("d[\"answer\"] = {}", d["answer"]);     // 42

    return 0;
}

// The read of a missing dict key is undefined (like list's out-of-bounds
// `.at`): `[]` keeps the house posture, unchecked -- `.has` guards, `.get`
// is the checked read. A write-only accessor (a setter with no getter)
// cannot be read or compound-assigned; a getter-only one cannot be written.
// See properties.mc for the same rules on field syntax.
