// Bodyless `fn` prototypes and `.mci` interface files: shipping a compiled
// mcc library that other programs import and link against.
// Prerequisites: extern.mc, functions/reference_params.mc, functions/const_params.mc.
//
// A plain `fn` may end with `;` instead of a body. Where `@extern` means "a
// symbol with the C calling convention" (see extern.mc), a bodyless prototype
// means "a concrete mcc function defined in another linked object, called
// with the mcc convention". The difference matters for the hidden-reference
// forms `&T` and `const &T`: their by-reference passing is part of the mcc
// convention, so a prototype carries it while `@extern` deliberately rejects
// it (a by-value `const T` is fine on `@extern` since Phase B; the reference
// forms are what it cannot describe). Every signature marker (`const`, `&`,
// `@noalias`, `@nonnull`) means exactly what it does on a definition.
//
// You rarely write a prototype by hand: `--emit-interface` writes them for
// you. This file is a complete little library (plus a `main` that demos it).
// To ship it compiled, emit the object and the interface stub:
//
//     mcc interfaces.mc -c -o interfaces.o    # the machine code
//     mcc interfaces.mc --emit-interface      # the interface: interfaces.mci
//
// The stub is valid mcc source, written next to this file. For this file it
// reads (generated output, quoted verbatim):
//
//     // Interface generated from interfaces.mc by mcc -- do not edit.
//     // Import this alongside the matching object file.
//
//     @extern fn printf(fmt: char*, ...) -> int32;
//
//     struct pair { a: int64; b: int64; }
//
//     const SCALE: int64 = 100;
//
//     fn total(const p: &pair) -> int64;
//
//     fn bump(n: &int32);
//
//     fn larger<T>(a: T, b: T) -> T {
//         if (a > b) { return a; }
//         return b;
//     }
//
//     fn main() -> int32;
//
// Note the split. Concrete functions became bodyless prototypes with every
// parameter marker re-emitted; before this form existed, `total` and `bump`
// could not be exported at all (an ABI `@extern` cannot describe a hidden
// reference). The struct, the constant, and the `@extern` declaration are
// shipped in full, and so is the generic function: a generic cannot be a
// prototype because its body must travel to be re-instantiated (`@inline`,
// `@asm`, and `@static` functions are likewise excluded).
//
// A consumer compiles against the stub, then links the object. Drop `main`
// from a real library first; two `main`s cannot link.
//
//     import "interfaces";   // resolves to interfaces.mci
//
//     mcc app.mc -c -I <dir with interfaces.mci> -o app.o
//     cc app.o interfaces.o -o app
//
// The consumer's `bump(n)` writes through to its own variable across the
// object boundary: both sides derive the convention from the same signature.
// A prototype is also a forward declaration: it may coexist with its
// matching definition, checked against it and then discarded (see
// functions/forward_declarations.mc). So the *functions* of a build that
// imports interfaces.mci while also compiling interfaces.mc no longer
// collide -- though the types the stub re-emits in full still do, so that
// build isn't deliverable quite yet.
// Docs: docs/language.md, "Bodyless fn prototypes" and "Interface files".

@extern fn printf(fmt: char*, ...) -> int32;

// The library surface: everything below lands in interfaces.mci in the shape
// quoted above.
struct pair { a: int64; b: int64; }

const SCALE: int64 = 100;

// A `const &` struct parameter is passed by hidden reference (no copy); as a
// prototype the `const &` marker rides along, so consumers pass it the same way.
fn total(const p: &struct pair) -> int64 {
    return (p.a + p.b) * SCALE;
}

// A reference parameter writes the caller's variable through a hidden reference;
// also inexpressible as @extern, exported cleanly as `fn bump(n: &int32);`.
fn bump(n: &int32) {
    n = n + 1;
}

// A generic has no single symbol to link against, so the stub carries its
// body in full and each consumer re-instantiates it.
fn larger<T>(a: T, b: T) -> T {
    if (a > b) { return a; }
    return b;
}

// The demo driver: in the shipped-library flow, this side moves to app.mc.
fn main() -> int32 {
    let p = struct pair { a = 30, b = 11 };
    printf("total  = %lld\n", total(p));    // (30 + 11) * 100 = 4100

    let n: int32 = 6;
    bump(n);                                // writes through: n is now 7
    printf("bumped = %d\n", n);

    printf("larger = %d\n", larger(n, 3 as int32));
    return 0;
}

// See also: extern.mc, functions/reference_params.mc, functions/const_params.mc.
