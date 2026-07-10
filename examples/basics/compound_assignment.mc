import "std/io";

// A counter with a side effect: it returns the current index and advances the
// caller's counter. Used below to show that a compound assignment evaluates its
// target exactly once.
fn bump(counter: int32*) -> int32 {
    let i = *counter!;      // `!` asserts the pointer is non-null (callers pass &i)
    *counter! = i + 1;
    return i;
}

fn main() -> int32 {
    // `x op= y` means `x = x op y`. Every arithmetic, bitwise, and shift
    // operator has a compound form: += -= *= /= %= &= |= ^= <<= >>=.
    let x: int32 = 10;
    x += 5;    // 15
    x -= 2;    // 13
    x *= 3;    // 39
    x /= 2;    // 19  (integer division truncates)
    x %= 7;    // 5
    x <<= 3;   // 40
    x >>= 1;   // 20
    x &= 0xC;  // 4
    x |= 0x1;  // 5
    x ^= 0x6;  // 3
    println("x = {}", x);   // 3

    // The right-hand side is a full expression, and the result keeps the
    // target's type -- exactly like the plain assignment it stands in for.
    let total: int32 = 0;
    total += 2 * 3 + 1;     // total = total + (2 * 3 + 1)
    println("total = {}", total);   // 7

    // Compound assignment works through every assignable target: a variable,
    // a pointer dereference, an array element, and a struct field.
    let n: int32 = 100;
    let p = &n;
    *p -= 58;               // through a pointer
    println("n = {}", n);   // 42

    // The target is evaluated a single time. `bump(&i)` runs once, so the
    // counter advances by one and only arr[0] is incremented -- not the
    // twice-evaluated behavior a naive `arr[bump(&i)] = arr[bump(&i)] + 1`
    // would have.
    let arr: int32[3] = [0, 0, 0];
    let i: int32 = 0;
    arr[bump(&i)] += 10;
    println("arr = [{}, {}, {}], i = {}", arr[0], arr[1], arr[2], i);

    // float64 supports the arithmetic forms (no %= for floats).
    let f: float64 = 2.0;
    f += 0.5;
    f *= 4.0;
    println("f = {.1f}", f);   // 10.0 (the {.1f} modifier rounds to one decimal)

    return 0;
}
