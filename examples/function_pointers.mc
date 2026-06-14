import "std";
import "memory";   // alloc, dealloc

// `fn(A, B) -> R` is the type of a pointer to a function. A bare function
// name -- written without the call parentheses -- is a value of that type.
fn add(a: int32, b: int32) -> int32 { return a + b; }
fn sub(a: int32, b: int32) -> int32 { return a - b; }

// A function pointer passed as a parameter and called through.
fn apply(op: fn(int32, int32) -> int32, x: int32, y: int32) -> int32 {
    return op(x, y);
}

// A function pointer can be returned, too. (A case where every arm returns
// is not treated as a guaranteed return -- as with if/else -- so pick into a
// variable and return that.)
fn op_for(symbol: uint8) -> fn(int32, int32) -> int32 {
    let op: fn(int32, int32) -> int32 = add;
    case (symbol) {
        when '-': op = sub;
    }
    return op;
}

// A struct holding a callback -- the basis of vtables and event handlers.
struct button {
    label: uint8*;
    on_press: fn(int32) -> int32;
}

fn loud(x: int32) -> int32 { return x * 100; }

fn main() -> int32 {
    // In a variable, reassignable.
    let op: fn(int32, int32) -> int32 = add;
    println("add(10, 3) = %d", op(10, 3));
    op = sub;
    println("sub(10, 3) = %d", op(10, 3));

    // Passed as an argument.
    println("apply(add) = %d", apply(add, 4, 5));

    // Calling the result of a call directly.
    println("op_for('-')(9, 2) = %d", op_for('-')(9, 2));

    // Calling a callback stored in a struct field, in place.
    let b: struct button;
    b.label = "press me";
    b.on_press = loud;
    println("%s -> %d", b.label, b.on_press(7));

    // A dispatch table: alloc<T> returns a typed T*, here a pointer to
    // function pointers -- no sizeof or cast needed. The element type is the
    // grouped (fn(...) -> ...) so the * from T* binds outside it.
    let table = alloc<fn(int32, int32) -> int32>(2);
    table[0] = add;
    table[1] = sub;
    println("table[0](2, 3) = %d, table[1](2, 3) = %d", table[0](2, 3), table[1](2, 3));
    dealloc(table);

    // null is a valid function pointer; optional callbacks compare with ==/!=.
    let maybe: fn(int32) -> int32 = null;
    if (maybe == null) {
        println("no callback set");
    }

    return 0;
}
