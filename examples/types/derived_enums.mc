import "std/io";

// An enum may derive from another by naming the base enum directly in the
// `: <base>` slot: the new enum copies every member of the base and adopts
// the base's underlying type, then adds members of its own. Builds on
// enums.mc. The reuse is compile-time only -- members still fold to plain
// constants of the underlying type, so nothing changes at runtime and no new
// type checking appears: derived values, base values, and plain integers mix
// freely.
enum io_error: int32 {
    SUCCESS   = 0,
    NOT_FOUND = 4,
}

// io_status inherits SUCCESS and NOT_FOUND, adopts int32, and adds RETRY.
// Redefining an inherited member here is rejected, even with an identical
// value: error: line N: enum 'io_status' redefines member 'SUCCESS'
// inherited from 'io_error'.
enum io_status: io_error {
    RETRY = 100,
}

// The base's members merge in before the derived enum's own values fold, so
// a new member may reference an inherited one through the derived scope.
enum limits: int32 {
    SMALL = 4,
}

enum wide_limits: limits {
    LARGE = wide_limits::SMALL * 8,
}

// Chains are transitive: each link sees every member above it.
enum tiny: uint8 { A = 1 }
enum small: tiny { B = 2 }      // has A and B
enum full:  small { C = 4 }     // has A, B, and C

// The adopted underlying type may be anything the base's is, pointers
// included: deriving from a string enum keeps char*.
enum greeting: char* {
    Hi = "hello",
}

enum long_greeting: greeting {
    Bye = "goodbye",
}

fn main() -> int32 {
    // An inherited member resolves through the derived enum and folds equal
    // to the base's spelling of it.
    println("io_status::NOT_FOUND = %d", io_status::NOT_FOUND);
    println("equal to io_error::NOT_FOUND: %d",
            (io_status::NOT_FOUND == io_error::NOT_FOUND) as int32);

    // Because it folds to a constant, compile-time contexts see it too:
    // here an inherited member sizes an array.
    let buf: int32[io_status::NOT_FOUND];
    buf[io_status::SUCCESS] = io_status::RETRY;
    println("buf[SUCCESS] = %d", buf[0]);

    // A derived member built from an inherited one.
    println("wide_limits::LARGE = %d", wide_limits::LARGE);

    // The full chain: one member from each link.
    println("full::A | full::B | full::C = %d",
            (full::A | full::B | full::C) as int32);

    // Inherited string members keep the pointer underlying type.
    println("%s, then %s", long_greeting::Hi, long_greeting::Bye);

    return 0;
}

// Only a bare, direct enum name in the slot derives. A pointer to an enum
// (`enum e: greeting*`) or a `type` alias to an enum keeps its old meaning,
// a plain underlying type with no member merge. A @private enum cannot be
// used as a base from another file, and the base must be declared before
// the enum that derives from it.
// See also: enums.mc for the enum basics this builds on.
