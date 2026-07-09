import "std/io";

// Overloads of one generic name may freely mix `mut` and non-`mut`
// positions. The motivating shape is one name serving both kinds of
// destination: a `mut` overload for the caller's own variable, and a
// pointer overload for storage reached indirectly.
// Builds on mut_params.mc (what `mut` means) and types/generics.mc
// (generic functions); pointers are covered in memory/pointers.mc.
fn set<T>(mut a: T) { a = 7 as T; }    // for the caller's own variable
fn set<T>(p: T*)    { *p = 9 as T; }   // for storage reached by pointer

// Resolution first drops candidates the argument cannot match: an rvalue
// (a literal, a call result, an `&x`, a bare function name) denotes no
// writable storage, so it drops every candidate that is `mut` at that
// position. Then the most specific pattern wins (`T*` beats `T`, concrete
// types beat both). Lvalue-ness never breaks a tie: this same-shape pair is
// fine for an rvalue (only the by-value overload is viable) but ambiguous
// for an lvalue --
//     let x: int32 = 0; pick(x);
//     error: call to 'pick' is ambiguous between overloads
fn pick<T>(mut a: T) -> int32 { a = a; return 1; }
fn pick<T>(a: T)     -> int32 { return 2; }

// Writability is judged against the overload that WINS, not against every
// candidate. `label` has a mut overload and a concrete-char one; concrete
// beats generic, so a char argument goes to the read-only overload and a
// read-only `const` parameter is a fine argument. Had the mut overload won
// (say, for an int32 argument), the same lend would be rejected:
//     error: cannot pass a const parameter as a mut argument; it is read-only
fn label<T>(mut a: T, b: T) -> int32 { a = b; return 1; }
fn label<T>(a: char, b: T)  -> int32 { return 2; }

fn describe(const c: char) -> int32 {
    return label(c, 'x');   // const lvalue, non-mut overload wins: allowed
}

// Used below to show single evaluation of a mut-candidate argument.
fn advance(mut n: int32) -> int32 {
    n += 1;
    return n - 1;
}

fn main() -> int32 {
    let x: int32 = 0;
    let y: int32 = 0;

    set(x);     // lvalue, and int32 cannot match T*: the mut overload wins
    set(&y);    // &y is an rvalue: the mut overload drops, the T* one wins
    println("set(x)   -> x=%d", x);
    println("set(&y)  -> y=%d", y);

    // A literal is an rvalue, so only the by-value `pick` is viable.
    println("pick(3)  -> overload %d", pick(3));

    println("label(c) -> overload %d", describe('q'));

    // The argument is evaluated exactly once, before the winner is known:
    // because SOME candidate marks the position `mut`, the lvalue's address
    // (base and index included) is formed up front and its value read once
    // through it. `advance(i)` runs a single time, so i ends at 1 and only
    // a[0] is written.
    let a: int32[2] = [0, 0];
    let i: int32 = 0;
    set(a[advance(i)]);
    println("once     -> a=[%d, %d] i=%d", a[0], a[1], i);

    return 0;
}

// See also: mut_params.mc for mut itself; const_params.mc for the read-only
// dual; types/generics.mc for generic functions and overload basics;
// overloading.mc for concrete (non-generic) overload sets;
// mixed_overloads.mc for a generic template sharing a name with concrete
// members.
