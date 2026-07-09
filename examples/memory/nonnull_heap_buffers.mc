import "std/io";
import "std/memory";
import "std/hashing/crc32";

// A heap buffer crossing the stdlib's @nonnull contracts. The stdlib
// annotates its data, source, key, and destination pointers @nonnull (see
// functions/nonnull.mc): the memory copy/fill family (bytecopy, copy,
// bytezero, zero, bytefill, fill), the hashing digests (md5, crc32,
// murmur3), dict's string keys, and the raw-array source overloads of
// list_init/string_init. Every such call must prove the
// pointer non-null. A stack buffer (&x, an array) or a string literal is
// already a proof; a heap buffer comes back from alloc<T> as a plain,
// possibly-null pointer, and the whole migration is one idiom: a single
// diverging null guard after the allocation. The narrowed fact carries
// through every later call, including calls inside loops, because a loop
// only drops the facts it could invalidate and these loops never touch buf.
// The tail section puts the same buffer behind a struct field, where the
// call write-effect analysis lets the guarded field survive a write-free
// callee like crc32.
// Prerequisites: pointers.mc (alloc/dealloc) and the functions/nonnull.mc
// family.

struct view {
    data: uint8*;
    size: uint64;
}

fn main() -> int32 {
    // alloc<T> returns a plain uint8*: no proof, so no @nonnull call
    // accepts it yet.
    let buf = alloc<uint8>(16);

    // The one-line diverging guard (shape 2 in functions/nonnull_narrowing.mc)
    // narrows buf for the rest of the scope. This is the whole migration
    // cost:
    if (buf == null) return 1;

    bytezero(buf, 16);               // ok: the narrowed buf satisfies @nonnull
    bytefill(buf, 0xAB as byte, 8);  // first half 0xAB, second half stays zero

    // The guard covers loops too. Nothing in this loop assigns to buf,
    // shadows it, or lends it as a mut argument, so the fact survives loop
    // entry and every iteration crosses into crc32's @nonnull slot with no
    // in-body guard and no `buf!` hatch. (A loop that did touch buf would
    // drop the fact at entry: see functions/nonnull_loops.mc.)
    let n: uint64 = 4;
    while (n <= 16) {
        println("crc32 of the first {} bytes: {}", n, crc32(buf, n));
        n += 4;
    }

    // The surviving fact also holds past the loop's exit: still no hatch.
    fill(buf, 0x11 as uint8, 16);
    println("crc32 after the refill: {}", crc32(buf, 16));

    // The same heap buffer behind a struct field. A guarded field like
    // v.data is a choosier fact than the bare name buf: it dies at any call
    // that might write memory (see functions/nonnull_projections.mc). Calls
    // the compiler proves transitively write-free are the exception, and
    // crc32 is one (it stores only to its own scalar locals), so the guarded
    // field survives its own call and the second call reuses the proof:
    let v = struct view { data = buf, size = 16 };
    if (v.data == null) return 1;
    let head = crc32(v.data, 8);       // the write-free call keeps the fact
    let whole = crc32(v.data, v.size); // so this call needs no new proof
    println("field crc32: first half {}, whole {}", head, whole);

    // println is a writing call (it bottoms out in @extern printf), so past
    // this point v.data would need re-proving. When a checked field must
    // cross a writing call or a loop, bind it while the fact is alive:
    // `let q = v.data;` under the guard carries a name fact like buf's.

    // dealloc keeps the plain T* on purpose: null is meaningful there (a
    // no-op), so it needs no proof.
    dealloc(buf);
    return 0;
}

// See also: functions/nonnull_narrowing.mc for the guard shapes and the
// exact rules on when facts die; functions/nonnull_loops.mc for narrowed
// facts crossing loops; functions/nonnull_projections.mc for field facts
// and the write-effect rules on which calls kill them;
// functions/nonnull_assert.mc for the `!` assertion
// where no guard fits; pointers.mc for alloc/dealloc; lists.mc for the
// container APIs, whose mut/const self receivers accept a heap pointer
// through the same one-line guard (see functions/pointer_decay.mc).
