import "std/io";

// A struct boxes into an `any` too, but only into a `const any` target and
// only BY HIDDEN REFERENCE: the payload holds a pointer to the value's
// existing storage (the same convention a `const`/`mut` struct parameter
// travels through, see functions/const_params.mc), tagged as the struct type
// itself (`point`, NOT `point*`). `case type` / `with` recover it as a
// read-only alias with no copy, so the arm reads the caller's live fields and
// can hand the binding on to a `const value: point` function that shares that
// same storage again. The archetypal `const any` position is the
// `slice<const any>` a native variadic collects into, which is where this
// example boxes from.
// An OWNING `any` of a struct (`let a: any = s;`, a return, a global) stays
// rejected: the payload would be a borrow that outlives the storage. Unions
// and fixed arrays stay rejected too, keeping their `&value` escape hatch.
// Prerequisites: any.mc (the box, single-type `case type`, and the `&s`
// escape hatch this extends), with_unwrap.mc (the one-pattern `with` sugar),
// functions/native_variadics.mc (the `slice<const any>` a variadic collects),
// and functions/const_params.mc (the hidden reference a `const` struct param
// already uses).

struct point {
    x: int32;
    y: int32;
}

// A `const value: point` consumer. A `const` struct parameter is itself a
// hidden reference, so handing it a recovered `point` binding copies nothing:
// the same caller storage flows all the way through.
fn manhattan(const p: point) -> int32 {
    let ax = p.x < 0 ? -p.x : p.x;
    let ay = p.y < 0 ? -p.y : p.y;
    return ax + ay;
}

// A collecting function: `args...` is sugar for `const args: slice<const any>`,
// the archetypal `const any` position. Each extra argument is boxed caller-side
// into that slice; a struct argument boxes by hidden reference.
fn describe(args...) {
    for a in args {
        case type (a) {
            // `when point p:` recovers the by-reference box as a read-only
            // (`const`) alias of the caller's storage. Reading p.x / p.y reads
            // the caller's live fields, and passing `p` to manhattan shares
            // that storage a second time -- still no copy.
            when point p:  println("point   ({}, {})  |m| = {}".format(p.x, p.y, manhattan(p)));

            // `point` and `point*` are DISTINCT tags. A pointer argument boxes
            // by value under its own pointer tag (as in any.mc) and lands here,
            // never in the struct arm above.
            when point* p: println(f"point*  ->({p!->x}, {p!->y})");

            when int32 n:  println(f"int32   {n}");
            else:          println("some other type");
        }
    }
}

// The single-pattern `with` sugar recovers the same by-reference box. `xs[0]`
// is a `const any` element; `with (p = xs[0] as point)` tests its tag and, on
// a match, binds `p` to the caller's storage for the true branch only.
fn first_point(xs: slice<const any>) {
    with (p = xs[0] as point) println(f"first:  point ({p.x}, {p.y})");
    else println("first:  not a point");
}

fn main() -> int32 {
    // Bare variables: each struct's own storage is shared directly. An rvalue
    // struct (a literal, a call return) would spill to a call-scoped temporary
    // first, but a named local like these boxes its live slot.
    let origin = point { x = 3, y = 4 };
    let neg = point { x = -5, y = 2 };

    // Two struct args box by reference (they hit `when point p:`), a pointer
    // arg boxes by value under the distinct point* tag, and an int32 boxes as
    // usual. All four recover from the one `slice<const any>`.
    describe(origin, neg, &origin, 42);

    // The `with` form over a struct-first and a non-struct-first collection.
    first_point(origin, neg);
    first_point(99, origin);

    // Rejected, shown as comments so the file still compiles in CI:
    //   let owned: any = origin;   // owning box of a struct: the borrow would
    //                              // escape -- error points at the const any
    //                              // allowance. Box &origin (a point*) instead.
    // Unions and fixed arrays are rejected the same way; box a pointer to them.

    return 0;
}

// See also: any.mc for the box, the boxable set, and the `&s` escape hatch a
// struct now complements by boxing by reference into a const any;
// with_unwrap.mc for the `with` sugar used above; case_type_groups.mc and
// generic_case_arms.mc for multi-type and generic arms (a generic `when T v:`
// recovers a struct tag by reference too); functions/const_params.mc for the
// hidden reference a `const` struct parameter shares with this box;
// functions/native_variadics.mc for the `slice<const any>` collection this
// boxes from; the docs section "The any type" for the owning/union/array
// rejection rules.
