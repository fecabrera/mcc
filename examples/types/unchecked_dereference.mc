import "libc/stdio";

// `-Wunchecked-dereference`, the first opt-in warning class: it warns on
// `*p`, `p->field`, and `p[i]` (reads, writes, and compound assignments
// alike) wherever the pointer is not proven non-null by the same proof
// relation `@nonnull` parameters use (functions/nonnull.mc). Unlike the
// author-placed `@warning` of warnings.mc, an opt-in class is an analysis
// over legal code, so it stays silent until a `-W<name>` flag enables it.
// That is why this file is CI-safe with live warning sites: the plain build
// (and CI's bare `-Werror` build) compiles it clean.
//
// Prerequisites: warnings.mc (the warning channel and -Werror),
// functions/nonnull.mc and nonnull_narrowing.mc (the proof relation and the
// null-check guards), functions/nonnull_assert.mc (the postfix `!` hatch).
//
// Fire the class by hand from the repo root:
//
//   pipenv run python -m mcc examples/types/unchecked_dereference.mc --run
//     (silent: the class is off by default, and this code is legal)
//
//   pipenv run python -m mcc examples/types/unchecked_dereference.mc --run -Wunchecked-dereference
//     examples/types/unchecked_dereference.mc: warning: line N: dereference of a possibly-null pointer (narrow it with a null check or assert with postfix '!') [-Wunchecked-dereference]
//     (one line per unproven site, then the program runs normally; `-Wall`
//      enables every opt-in class at once and reports the same. A typo in
//      the class name is a hard error: mcc: error: unknown warning class 'x')
//
//   pipenv run python -m mcc examples/types/unchecked_dereference.mc -Wunchecked-dereference -Werror -o /tmp/ud
//     examples/types/unchecked_dereference.mc: error: line N: dereference of a possibly-null pointer (narrow it with a null check or assert with postfix '!') [-Werror=unchecked-dereference]
//     (-Werror promotes exactly what an enabled class printed, naming the
//      class in the tail; exit status 1, no executable written)
//
// This file imports only libc on purpose: until the stdlib's own sweep
// lands, enabling the class on a program that imports the container modules
// surfaces libmc-internal warnings alongside yours.

struct node {
    value: int32;
    next: struct node*;
}

// THE WARNING SITES. A plain `T*` parameter is nullable and carries no
// proof, so with the class enabled every dereference form below reports,
// one line per site. The code still compiles and runs either way: the class
// never changes what is generated, it only reports.
fn describe(n: struct node*) -> int32 {
    return n->value;    // warns: `->` field access on an unproven pointer
}

fn bump(p: int32*) -> int32 {
    let first = *p;     // warns: `*` read
    p[1] = 7;           // warns: indexed write
    p[1] += 1;          // warns: compound assignment is a site like any other
    return first;
}

// Fix 1: a `@nonnull` parameter. The proof obligation moves to the call
// sites (which must pass something proven), and the body is warning-free.
fn sum2(@nonnull p: int32*) -> int32 {
    return p[0] + p[1];
}

// Fix 2: a null-check guard. The `if (p != null)` branch narrows `p`
// exactly as it does for @nonnull argument passing, so the dereference
// inside is silent. Passing null is still perfectly legal; the guard is
// what makes it safe.
fn value_or(p: int32*, fallback: int32) -> int32 {
    if (p != null) {
        return *p;      // silent: narrowed by the guard
    }
    return fallback;
}

// Fix 3: guards work on field projections too. The `and` chain proves `n`
// for the `n->next` read and then `n->next` for the final dereference.
fn next_value(n: struct node*) -> int32 {
    if (n != null and n->next != null) {
        return n->next->value;  // silent: both hops proven by the chain
    }
    return -1;
}

// Fix 4: the postfix `!` assertion, for pointers whose non-nullness only
// the programmer can see. A `let` seeded from `p!` starts proven, so one
// hatch at the binding silences every later use (null here would be UB).
fn trusted(p: int32*) -> int32 {
    let q = p!;
    return *q;          // silent: q was born proven
}

fn main() -> int32 {
    let a: int32[4] = [1, 2, 3, 4];
    a[2] = 9;           // arrays index directly: never a warning site
    let s = a as slice<int32>;
    let mid = s[1];     // slice indexing never warns either

    // A `let` seeded from a proven source (here `&x`) starts proven, so
    // local dereferences of it are silent with no guard at all.
    let x: int32 = 5;
    let q = &x;
    let v = *q;

    let n2 = struct node { value = 20, next = null };
    let n1 = struct node { value = 10, next = &n2 };

    let d = describe(&n1);        // the warnings live in the callees...
    let b = bump(a);              // ...calling them is not itself a site
    let two = sum2(a);            // an array decays proven into @nonnull
    let got = value_or(&x, 0);
    let missed = value_or(null, 42);
    let nv = next_value(&n1);
    let t = trusted(&x);

    printf("describe=%d bump=%d sum2=%d\n", d, b, two);
    printf("value_or=%d/%d next_value=%d trusted=%d\n", got, missed, nv, t);
    printf("mid=%d v=%d\n", mid, v);
    return 0;
}

// See also: warnings.mc for the warning channel, `-Werror`, and the
// unconditional `@warning` this class contrasts with;
// functions/nonnull.mc for the proof relation and its always-non-null
// sources; functions/nonnull_narrowing.mc for the guard shapes that
// silence a site; functions/nonnull_assert.mc for the postfix `!` hatch.
