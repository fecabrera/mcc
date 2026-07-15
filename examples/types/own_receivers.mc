import "std/io";

// The consuming receiver, `own self: T`: a method that takes ownership of
// its receiver BY MOVE (not a copy) and drops it -- runs its destructor --
// at the END of the body. `own` is a by-value owning parameter marker,
// written before the name like `const`, and it works on any parameter
// (`fn drain(own b: box)`), not just `self`. It is the receiver-side mirror
// of the `-> own` return (own_returns.mc): a move-out hands a value to the
// caller, a move-in takes one from the caller, and both obey the SAME
// relinquish discipline. Where the reference receivers (methods.mc) borrow
// a view, this one consumes the value. A consuming method is monomorphic
// and not dispatch-eligible by design: it is a deliberate ownership
// transfer, never something resolved dynamically.
//
// The call-site relinquish rule (verbatim from own_returns.mc's move-out):
//   * A FRESH owned value is ADOPTED by the callee, no `move` needed -- a
//     constructor expression (`adder()`), an `-> own` call, or the spilled
//     rvalue receiver of a dot-call (`adder().plus(3)`, or a chain).
//   * A NAMED owned local must be relinquished explicitly with `move(x)`:
//     `adder::total(move(a))`. Its scheduled destructor is cancelled (no
//     double-drop), and using `a` afterward is a use-after-move error.
//
// Prerequisites: destructors.mc (the drop this receiver runs, and the
// scheduled-destructor machinery it cancels), own_returns.mc (the move/
// adopt discipline this mirrors, and `move(...)`), methods.mc /
// method_calls.mc (the receiver kinds and the `.method()` desugaring).

// A resource whose lifecycle is observable: constructing prints, and the
// destructor prints the value's latest `sum`, so the output shows exactly
// one construction and one drop per value.
struct adder {
    sum: int32;
}

fn adder::constructor(self: &adder) {
    self.sum = 0;
    println("  new");
}

fn adder::destructor(self: &adder) {
    println(f"  drop sum={self.sum}");
}

// A consuming BUILDER step: it takes ownership of self, mutates it, and
// hands it back with `return self`. The `-> own` return transfers the
// value out and cancels the drop on that path (own_returns.mc), so a value
// threaded through a chain of these is never dropped at an intermediate
// step -- only the terminal consuming step drops it.
fn adder::plus(own self: adder, n: int32) -> own adder {
    self.sum = self.sum + n;
    return self;                 // transfer out: no drop here
}

// A TERMINAL consuming step: it consumes self, drops it at the end of the
// body, and yields a plain value. The drop runs before the caller gets the
// result -- watch for "drop sum=7" BEFORE "total=7" in the output.
fn adder::total(own self: adder) -> int32 {
    return self.sum;             // self drops as the body ends
}

// `own` on a free function parameter works the same: drain consumes its
// argument and drops it at the end of its body.
fn drain(own b: adder) {
    println(f"  drain sum={b.sum}");
}

fn main() -> int32 {
    // ---- Adoption: a chain over FRESH temporaries needs no `move`.
    // adder() is a fresh owned value the first `plus` adopts; each `plus`
    // hands its owned result to the next step; `total` consumes the last.
    // One construction, one drop (at the accumulated sum), at the terminal
    // step.
    println("chain:");
    let t = adder().plus(3).plus(4).total();   // new, drop sum=7
    println(f"  total={t}");

    // ---- Relinquishing a NAMED local: `move(a)`. The local's scheduled
    // destructor (destructors.mc) is cancelled and ownership moves into the
    // callee, which drops it. `a` is unusable afterward.
    println("move(local):");
    {
        let a = adder();                       // schedules adder::destructor(a)
        let s = adder::total(move(a));         // schedule cancelled; drop in callee
        println(f"  s={s}");
        // A bare `adder::total(a)` (or `a.total()`) is refused, directing
        // to move:
        //   error: line N: 'a' is an owned value; passing it to an own
        //   parameter relinquishes it -- spell the transfer move(a)
        // And using `a` after the move is a use-after-move error:
        //   error: line N: 'a' was moved into an own parameter and cannot
        //   be used again (its ownership was transferred)
    }

    // ---- The same discipline on a free function's `own` parameter: a
    // fresh temporary is adopted, a named local needs `move`.
    println("drain:");
    drain(adder());                            // new, drain sum=0, drop sum=0
    {
        let b = adder();
        drain(move(b));                        // new, drain sum=0, drop sum=0
    }

    return 0;
}

// `own` works beyond the direct call too -- on generic functions, methods of
// generic structs (a container's consuming method), overloaded sets, and as a
// function value (`fn(own box)`). See own_generic.mc for those forms.
//
// Not in this phase, each a compile error today:
//   * `own self: &T`, the owned-reference receiver (own moves a value in,
//     a reference lends a view): "a parameter cannot be both own and a
//     reference ... the owned-reference receiver `own self: &T` arrives in
//     a later phase".
//   * `own` on an @extern parameter: "own parameters are not allowed on
//     @extern functions (C has no ownership obligation to take)".
//
// See also: own_generic.mc (`own` on generic/overloaded/function-value
// paths), own_returns.mc (the move-OUT return this mirrors, and
// `move(...)`), destructors.mc (the scheduled drop this runs and cancels),
// own_drops.mc (where an UNadopted `-> own` temporary drops instead -- a
// consuming `own` parameter adopts its temporary, it does not drop it at
// statement end), methods.mc / method_calls.mc (the reference receiver
// kinds this consuming one joins). Full rules: docs/language.md,
// "Move-out returns" (the `own` parameter and consuming receiver share its
// section).
