import "std/io";
import "std/string";

// Move-out returns: a function declared `-> own T` hands its caller an
// OWNED value. The signature says, visibly, that the return transfers a
// resource -- returning an auto-destructed local cancels the local's
// scheduled destructor on that path (the whole-value hard error from
// destructors.mc, lifted exactly here), and the caller's let ADOPTS the
// cleanup obligation, scheduling `T::destructor` like a constructor-sugar
// let. Like `-> mut` (mut_returns.mc), `own` is a flag on the declaration,
// not part of the type, and the two never combine: mut lends a view, own
// hands over a value. No ABI changes anywhere -- own is compile-time
// policy.
//
// Prerequisites: destructors.mc (the schedule being cancelled and adopted),
// constructors.mc (the sugar), error_handling.mc (the result composition).

// A resource whose lifecycle is observable: constructing and destroying
// print, so the output shows exactly one destruction per value, at the
// adopting caller.
struct conn {
    id: int32;
}

fn conn::constructor(mut self: conn, id: int32) {
    self.id = id;
    println("  open {}", id);
}

fn conn::destructor(mut self: conn) {
    println("  close {}", self.id);
    self.id = -1;
}

// The headline: construct locally, hand the value out. The unmarked
// return forms an `-> own` function accepts are the ones that visibly
// hold the obligation -- this constructed local (THE transfer), a fresh
// constructor expression (`return conn(0);`), or a chained own call.
fn dial(id: int32) -> own conn {
    let c = conn(id);        // schedules conn::destructor(c)
    return c;                // transfer: the schedule is cancelled here
}

// Cancellation is per return path. The early return hands out a fresh
// temporary instead, so ON THAT PATH the local still closes -- watch for
// "close 7" before "fallback" in the output.
fn dial_checked(id: int32) -> own conn {
    let c = conn(id);
    if (id < 0) {
        println("  fallback");
        return conn(0);      // c's destructor still runs on this path
    }
    return c;                // and is cancelled on this one
}

// Anything else is a PLAIN COPY -- the original stays behind, still owned
// -- so minting a caller obligation from it needs the explicit assertion:
// move(v), which behaves like a builtin `fn move<T>(v: T) -> T` (the
// value passes through; the call is the statement "I relinquish this").
// Claimed by call shape like ok()/error(), legal only in the return value
// of an `-> own` function -- around the whole value or on an ok payload,
// `return ok(move(v));`. This is the pop-owned idiom: only the programmer
// knows the container relinquishes the element. (A wrong move() -- the
// source stays reachable and owned -- is the same undefined double-free
// as any aliasing copy; the marker makes that case visible, never
// silent.)
struct slot {
    c: conn;
}

fn slot::take(mut self: slot) -> own conn {
    return move(self.c);
}

// Ownership composes with results through the OK PAYLOAD: `return ok(c)`
// transfers, `return error(...)` is the error path (its locals are
// destroyed normally -- watch for "close 13"). An error-only result<E>
// cannot be own: there is no payload to hand over.
error dial_error {
    UNLUCKY,
}

fn dial_carefully(id: int32) -> own result<conn, dial_error> {
    let c = conn(id);
    if (id == 13)
        return error(dial_error::UNLUCKY);
    return ok(c);
}

fn main() -> int32 {
    // The adopting let: msg cleans up at scope end, exactly once.
    println("dial:");
    {
        let c = dial(1);
        println("  using {}", c.id);
    }                                    // close 1 -- the adopted schedule

    // Both paths of the checked dial. On the fallback path the abandoned
    // local still closes inside the callee ("close -1" right after the
    // fresh temporary opens), then the adopted fallback closes at scope
    // end ("close 0") -- one destruction per value, every path.
    println("checked, ok path:");
    { let c = dial_checked(2); }         // close 2
    println("checked, fallback path:");
    { let c = dial_checked(-1); }        // open -1, fallback, open 0,
                                         // close -1, then close 0

    // The explicit move.
    println("take:");
    {
        let s = slot { c = conn { id = 9 } };   // literal: nothing scheduled
        let c = s.take();                // adopts the moved-out value
        println("  took {}", c.id);
    }                                    // close 9 -- once, via c

    // Result composition: the adopting unwraps are the plain
    // `let c = try f();` (in a result-typed function) and this except
    // form -- the unwrapped payload carries its obligation into the bound
    // slot. The handler's emitted fallback fills the SAME slot, so it
    // rides the same scheduled cleanup ("close 0" at scope end on the
    // unlucky path, after the callee already closed its abandoned local).
    println("careful, ok:");
    {
        let c = try dial_carefully(3) except (e) { emit conn { id = 0 }; };
        println("  got {}", c.id);
    }                                    // close 3
    println("careful, unlucky:");
    {
        let c = try dial_carefully(13) except (e) { emit conn { id = 0 }; };
        println("  got {}", c.id);       // 0: the fallback literal
    }                                    // close 0

    // The marker rides function-pointer TYPES too: `fn(int32) -> own conn`
    // spells the contract the way `fn(...) -> mut T` spells a mut return,
    // so a call through a value -- here a factory local; a field-held
    // callback works the same -- still vouches for adoption. (Assigning an
    // own function to a PLAIN fn type, or the reverse, is a compile error
    // in both directions: `own` is a contract, and only an explicit `as`
    // retypes across it.)
    println("factory:");
    {
        let factory: fn(int32) -> own conn = dial;
        let c = factory(4);
        println("  made {}", c.id);
    }                                    // close 4 -- adopted through the type

    // Every other consumption of an own call is INERT -- the obligation
    // is dropped (a leak, documented and yours), consistent with
    // expression-position temporaries owning no cleanup: discarding the
    // call, passing it as an argument, chaining off it, assigning to an
    // existing variable, and the mixed-ownership `try f() ?? fallback`.
    // `own` on a destructor-less type is likewise a no-op, which keeps
    // generic `-> own T` signatures writable for every T.

    println("done");
    return 0;
}

// The signature travels: an .mci interface stub renders `-> own conn`, so
// importers adopt identically, and an own/plain mismatch between a
// prototype and its definition is rejected like a mut mismatch. This lift
// also closed a general escape: in ANY function, `return ok(local)` of an
// auto-destructed local is now the same hard error as the bare
// `return local` (the result wrap no longer smuggles a destroyed copy
// out). @extern functions cannot be own (C hands over no obligation);
// neither can @property/@accessor methods (field and index reads never
// transfer). `emit` keeps the whole-value error -- a block expression has
// no signature to carry the marker.
//
// See also: destructors.mc (the error this lifts and every opt-out
// spelling), mut_returns.mc (the sibling return flag), error_handling.mc
// (results, try, except). Full rules: docs/language.md, "Move-out
// returns".
