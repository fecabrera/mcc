import "std/io";

// `own` (own_receivers.mc) is not a direct-call-only convention: the same
// move-in discipline holds on EVERY call path. This example shows the three
// that own_receivers.mc deferred -- a consuming method on a generic container,
// an overloaded consuming set, and an `own` function VALUE.
//
// The relinquish rule is unchanged everywhere (own_receivers.mc):
//   * a FRESH owned value (a constructor, an `-> own` call, a dot-call's
//     rvalue receiver) is ADOPTED, no `move`;
//   * a NAMED owned local is relinquished with `move(x)`, its scheduled
//     destructor cancelled and later use a use-after-move error.
//
// Prerequisites: own_receivers.mc (the `own self`/`own` parameter and its
// discipline), generics.mc (generic structs and functions), overloading.mc
// (overload sets), function_pointers.mc (function values).

// A generic container with an observable lifecycle: constructing stamps, and
// the destructor stamps the pair, so the output shows exactly one build and
// one drop per value.
struct pair<T> {
    a: T;
    b: T;
}

fn pair<T>::constructor(self: &pair<T>, a: T, b: T) {
    self.a = a;
    self.b = b;
    println("  build");
}

fn pair<T>::destructor(self: &pair<T>) {
    println("  drop");
}

// (1) A CONSUMING METHOD on a generic struct -- what `own` on the generic
// path unlocks. `into_sum` takes ownership of the container and drops it at
// the end of the body (watch "drop" print BEFORE the sum flows out). This is
// the container idiom `list<T>::into_iter` / `string::into_bytes` follow.
fn pair<T>::into_sum(own self: pair<T>) -> T {
    return self.a + self.b;
}

// (2) An OVERLOADED consuming set. Both members mark the same position (slot
// 0, the receiver) `own`, so the caller contract is unambiguous -- a set
// mixing a consuming and a copying member at one name is rejected.
fn pair<T>::into_scaled(own self: pair<T>) -> T { return self.a + self.b; }
fn pair<T>::into_scaled(own self: pair<T>, k: T) -> T {
    return (self.a + self.b) * k;
}

// (3) A plain `own` free function, to be used as a VALUE below.
fn drain(own p: pair<int32>) -> int32 {
    return p.a + p.b;
}

fn main() -> int32 {
    // ---- Consuming method, generic struct. A fresh temporary receiver is
    // adopted by the dot-call; the container drops inside into_sum.
    println("into_sum (fresh):");
    let s1 = pair<int32>(3, 4).into_sum();          // build, drop, then s1=7
    println(f"  s1={s1}");

    // A named local uses the qualified move form (a dot-call `p.into_sum()`
    // on a named local is refused, directing to move -- there is no place to
    // write move on a dot receiver).
    println("into_sum (move):");
    {
        let p = pair<int32>(10, 20);                // build
        let s2 = pair<int32>::into_sum(move(p));    // drop in callee; p unusable
        println(f"  s2={s2}");
    }

    // ---- Overloaded consuming set: the arity picks the winner, each member
    // consumes its receiver.
    println("into_scaled:");
    let s3 = pair<int32>(1, 2).into_scaled();       // build, drop, s3=3
    let s4 = pair<int32>(1, 2).into_scaled(10);     // build, drop, s4=30
    println(f"  s3={s3} s4={s4}");

    // ---- An `own` function VALUE. Its type carries the move-in contract,
    // `fn(own pair<int32>) -> int32`, distinct from `fn(pair<int32>) -> int32`.
    // A call through the value enforces the move like a direct call: a fresh
    // value adopted, a named local moved.
    println("function value:");
    let f = drain;                                  // fn(own pair<int32>) -> int32
    let s5 = f(pair<int32>(5, 6));                  // build, drop, s5=11
    {
        let q = pair<int32>(7, 8);                  // build
        let s6 = f(move(q));                        // drop in callee, s6=15
        println(f"  s5={s5} s6={s6}");
    }

    return 0;
}

// Not in this phase, each a compile error today:
//   * `own self: &T`, the owned-reference receiver: "a parameter cannot be
//     both own and a reference ... arrives in a later phase" (own_receivers.mc).
//   * an overload set that DISAGREES on own positions (one consuming, one
//     copying member at the same name): "own parameters must agree across an
//     overload set ...".
//   * assigning an own function value to a plain `fn(...)` slot (or vice
//     versa): "... an own parameter moves ownership in and the callee drops
//     it, a different calling convention from a by-value copy ... not
//     convertible".
//
// See also: own_receivers.mc (the direct-call receiver and the shared
// discipline), own_returns.mc (the move-OUT `-> own` return). Full rules:
// docs/language.md, "Consuming receivers: `own self`".
