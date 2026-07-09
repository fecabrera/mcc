import "std";

// A `type` alias may carry a type-parameter list, naming a *family* of existing
// types. The alias stays transparent: `entry<int32>` is not a new type, it *is*
// `pair<char*, int32>`, expanded where it is used. The two spellings share one
// struct instantiation -- no monomorphized artifact is minted for the alias.

struct pair<A, B> { first: A; second: B; }

// A generic alias: a wider generic partially applied. `entry<T>` fixes the key
// to `char*` and leaves the value open.
type entry<T> = pair<char*, T>;

// Because the alias is transparent, an `entry<int32>` and a bare
// `pair<char*, int32>` are interchangeable without a cast.
fn value_of(e: entry<int32>) -> int32 { return e.second; }
fn make_pair(v: int32) -> pair<char*, int32> {
    return struct pair<char*, int32> { first = "k", second = v };
}

// A generic alias also names a *shape*: `cmp<T>` is the comparator type over any
// element `T`. Spelling it once beats writing the function-pointer type at each
// use site.
type cmp<T> = fn(T, T) -> bool;

fn less(a: int32, b: int32) -> bool { return a < b; }
fn greater(a: int32, b: int32) -> bool { return a > b; }

// `pick` takes a comparator and returns whichever of x/y it prefers.
fn pick(better: cmp<int32>, x: int32, y: int32) -> int32 {
    if (better(x, y)) { return x; }
    return y;
}

// An unused alias parameter is inert: transparency makes `boxed<bool>` and
// `boxed<char>` the *same* type (unlike a struct, whose unused-parameter
// instantiations stay nominally distinct).
type boxed<T> = int32;

// A parameter may declare a default, exactly as on functions and structs, so a
// bare `record` means `record<int64>` -- a complete written type.
type record<T = int64> = pair<char*, T>;

fn main() -> int32 {
    // The alias-typed value flows into the underlying-typed function, and vice
    // versa -- one type, two names.
    let e: entry<int32> = make_pair(41);
    println("entry.second = %d", value_of(e));
    println("value_of(pair) = %d", value_of(make_pair(7)));

    // The comparator alias, reassignable like any function value.
    let choose: cmp<int32> = less;
    println("pick(less, 3, 9) = %d", pick(choose, 3, 9));
    choose = greater;
    println("pick(greater, 3, 9) = %d", pick(choose, 3, 9));

    // Inert parameter: both spellings are the one `int32` type, so they add.
    let a: boxed<bool> = 20;
    let b: boxed<char> = 22;
    println("boxed<bool> + boxed<char> = %d", a + b);

    // Defaulted alias: `record` alone is `record<int64>`.
    let r: record;
    r.second = 100;
    println("record.second = %ld", r.second);

    return 0;
}
