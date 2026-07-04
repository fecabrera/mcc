import "std";
import "memory";
import "hashing/crc32";

// A heap buffer crossing the stdlib's @nonnull contracts. The stdlib
// annotates its data, source, key, and destination pointers @nonnull (see
// functions/nonnull.mc): the memory copy/fill family (bytecopy, copy,
// bytezero, zero, bytefill, fill), the hashing digests (md5, crc32,
// murmur3), dict's string keys, and the raw-array sources of
// list_from_array/string_from_array. Every such call must prove the
// pointer non-null. A stack buffer (&x, an array) or a string literal is
// already a proof; a heap buffer comes back from alloc<T> as a plain,
// possibly-null pointer, and the migration is two idioms: one diverging
// null guard after the allocation covers the straight-line calls, and the
// postfix `!` assertion covers calls inside loops, where narrowed facts
// drop.
// Prerequisites: pointers.mc (alloc/dealloc) and the functions/nonnull.mc
// trio.

fn main() -> int32 {
    // alloc<T> returns a plain uint8*: no proof, so no @nonnull call
    // accepts it yet.
    let buf = alloc<uint8>(16);

    // The one-line diverging guard (shape 2 in functions/nonnull_narrowing.mc)
    // narrows buf for the rest of the scope. This is the whole migration
    // cost for straight-line code:
    if (buf == null) return 1;

    bytezero(buf, 16);               // ok: the narrowed buf satisfies @nonnull
    bytefill(buf, 0xAB as byte, 8);  // first half 0xAB, second half stays zero

    // Narrowed facts drop at loop entry, so inside the body buf is unproven
    // again, even though the guard above dominates it. Here the idiom is the
    // postfix `!` assertion (functions/nonnull_assert.mc): still zero-cost,
    // and sound because the guard already handled null.
    let n: uint64 = 4;
    while (n <= 16) {
        // crc32(buf, n);            // error: possibly-null pointer
        println("crc32 of the first %llu bytes: %u", n, crc32(buf!, n));
        n += 4;
    }

    // The fact does not come back after the loop either; code past the loop
    // entry stays on the dropped side. Assert again (or re-guard).
    fill(buf!, 0x11 as uint8, 16);
    println("crc32 after the refill: %u", crc32(buf!, 16));

    // dealloc keeps the plain T* on purpose: null is meaningful there (a
    // no-op), so it needs no proof.
    dealloc(buf);
    return 0;
}

// See also: functions/nonnull_narrowing.mc for the guard shapes and the
// exact rules on when facts drop; functions/nonnull_assert.mc for the `!`
// assertion; pointers.mc for alloc/dealloc; lists.mc for the container
// APIs, whose self parameters stay plain T* until they become mut/const
// receivers.
