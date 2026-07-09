import "std/io";
import "std/memory";   // alloc, dealloc

// `fn(A, B) -> R` is the type of a pointer to a function. A bare function
// name -- written without the call parentheses -- is a value of that type.
fn add(a: int32, b: int32) -> int32 { return a + b; }
fn sub(a: int32, b: int32) -> int32 { return a - b; }

// A function pointer passed as a parameter and called through.
fn apply(op: fn(int32, int32) -> int32, x: int32, y: int32) -> int32 {
    return op(x, y);
}

// A variadic function-pointer type, `fn(A, ...) -> R` (a trailing `...` after
// at least one fixed parameter), types a pointer to a variadic function -- so
// even a printf-style function like `println` can be passed and called through
// with varargs.
fn log_with(printer: fn(char*, ...), label: char*) {
    printer("logging from %s", label);
}

// A function pointer can be returned, too. Here a default is picked and a
// case overrides it for specific inputs.
fn op_for(symbol: char) -> fn(int32, int32) -> int32 {
    let op: fn(int32, int32) -> int32 = add;
    case (symbol) {
        when '-': op = sub;
    }
    return op;
}

// A struct holding a callback -- the basis of vtables and event handlers.
struct button {
    label: char*;
    on_press: fn(int32) -> int32;
}

fn loud(x: int32) -> int32 { return x * 100; }

// A fixed-size dispatch table as a @static global. A function name folds to
// a constant address, so the table can be initialized at compile time. The
// grouped (fn(...) -> ...)[] is an array of function pointers, with the row
// count inferred from the initializer.
@static let binops: (fn(int32, int32) -> int32)[] = [add, sub];

// A const (or @static let) can name a function: a compile-time alias you then
// call by its new name. The type is inferred from the function -- no signature
// to spell out -- so even a variadic like `println` aliases cleanly. Handy for
// renaming a library function locally.
const plus = add;
const log = println;

fn main() -> int32 {
    // In a variable, reassignable.
    let op: fn(int32, int32) -> int32 = add;
    println("add(10, 3) = %d", op(10, 3));
    op = sub;
    println("sub(10, 3) = %d", op(10, 3));

    // Passed as an argument.
    println("apply(add) = %d", apply(add, 4, 5));

    // A const alias of a function, called by its new name (`log` is `println`).
    log("plus(8, 8) = %d", plus(8, 8));

    // Passing a variadic function through a variadic function-pointer param.
    log_with(println, "fn_ptr demo");

    // Calling the result of a call directly.
    println("op_for('-')(9, 2) = %d", op_for('-')(9, 2));

    // Calling a callback stored in a struct field, in place.
    let b = struct button { label = "press me", on_press = loud};
    println("%s -> %d", b.label, b.on_press(7));

    // The @static dispatch table, indexed and called in place.
    println("binops[0](2, 3) = %d, binops[1](2, 3) = %d",
            binops[0](2, 3), binops[1](2, 3));

    // A fixed-size table on the stack: (fn(...) -> ...)[N] is an array of N
    // function pointers, assigned element by element.
    let local: (fn(int32, int32) -> int32)[2];
    local[0] = add;
    local[1] = sub;
    println("local[0](7, 4) = %d, local[1](7, 4) = %d", local[0](7, 4), local[1](7, 4));

    // A heap table: alloc<T> returns a typed T*, here a pointer to function
    // pointers -- no sizeof or cast needed. The element type is the grouped
    // (fn(...) -> ...) so the * from T* binds outside it.
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
