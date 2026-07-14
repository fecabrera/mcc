import "std/io";
import "std/memory";   // alloc, dealloc
import "libc/stdio";   // printf: the C-variadic function the pointer demos use

// `fn(A, B) -> R` is the type of a pointer to a function. A bare function
// name -- written without the call parentheses -- is a value of that type.
fn add(a: int32, b: int32) -> int32 { return a + b; }
fn sub(a: int32, b: int32) -> int32 { return a - b; }

// A function pointer passed as a parameter and called through.
fn apply(op: fn(int32, int32) -> int32, x: int32, y: int32) -> int32 {
    return op(x, y);
}

// A variadic function-pointer type, `fn(A, ...) -> R` (a trailing `...` after
// at least one fixed parameter), types a pointer to a C-variadic function --
// so a printf-style function can be passed and called through with varargs.
// (A function type can also spell the per-parameter `@nonnull` contract, so
// a @nonnull function is a legal value too -- see nonnull_callbacks.mc --
// and the `mut`/`const` hidden-reference conventions -- see
// mut_callbacks.mc. Overload sets and generic functions stay direct-call
// names: mcc's own `println`, an overload set with generic members since
// print/println became verbatim single-string writers, is not a value.)
fn log_with(printer: fn(char*, ...) -> int32, label: char*) {
    printer("logging from %s\n", label);
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
// to spell out -- so even a C variadic like `printf` aliases cleanly. Handy
// for renaming a library function locally.
const plus = add;
const log = printf;

fn main() -> int32 {
    // In a variable, reassignable.
    let op: fn(int32, int32) -> int32 = add;
    println(f"add(10, 3) = {op(10, 3)}");
    op = sub;
    println(f"sub(10, 3) = {op(10, 3)}");

    // Passed as an argument.
    println(f"apply(add) = {apply(add, 4, 5)}");

    // A const alias of a function, called by its new name (`log` is `printf`).
    log("plus(8, 8) = %d\n", plus(8, 8));

    // Passing a variadic function through a variadic function-pointer param.
    log_with(printf, "fn_ptr demo");

    // Calling the result of a call directly.
    println("op_for('-')(9, 2) = {}".format(op_for('-')(9, 2)));

    // Calling a callback stored in a struct field, in place.
    let b = struct button { label = "press me", on_press = loud};
    println(f"{b.label} -> {b.on_press(7)}");

    // The @static dispatch table, indexed and called in place.
    println("binops[0](2, 3) = {}, binops[1](2, 3) = {}".format(
            binops[0](2, 3), binops[1](2, 3)));

    // A fixed-size table on the stack: (fn(...) -> ...)[N] is an array of N
    // function pointers, assigned element by element.
    let local: (fn(int32, int32) -> int32)[2];
    local[0] = add;
    local[1] = sub;
    println("local[0](7, 4) = {}, local[1](7, 4) = {}".format(local[0](7, 4), local[1](7, 4)));

    // A heap table: alloc<T> returns a typed T*, here a pointer to function
    // pointers -- no sizeof or cast needed. The element type is the grouped
    // (fn(...) -> ...) so the * from T* binds outside it.
    let table = alloc<fn(int32, int32) -> int32>(2)!;
    table[0] = add;
    table[1] = sub;
    println("table[0](2, 3) = {}, table[1](2, 3) = {}".format(table[0](2, 3), table[1](2, 3)));
    dealloc(table);

    // null is a valid function pointer; optional callbacks compare with ==/!=.
    let maybe: fn(int32) -> int32 = null;
    if (maybe == null) {
        println("no callback set");
    }

    return 0;
}
