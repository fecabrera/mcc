import "std/io";
import "std/memory";

// A flexible array member: a trailing `field: T[]` with no size. It adds 0 to
// the struct's `sizeof` and decays to a `T*` at the struct's tail, so a single
// allocation holds the header and a run of elements contiguously -- the C
// `linux_dirent64`-style layout, without the `T[1]` "struct hack".
struct packet {
    length: uint64;     // how many elements `data` actually holds
    data: int32[];      // the flexible array member -- must be the last field
}

// One malloc covers the header plus `n` elements; the elements live right after
// `length`, reached through `p->data` (which is just a pointer to the tail).
//
// `offsetof(struct packet, data)` is where the elements begin -- the tight base
// for the allocation. `sizeof(struct packet)` works too, but it is rounded up to
// the struct's alignment and so can include trailing padding the elements would
// overlap; offsetof is exact. (`alignof` counts the element type, so the tail
// is always aligned for a T.)
fn make_packet(n: uint64) -> struct packet* {
    let p = alloc<byte>(offsetof(struct packet, data) + n * sizeof(int32))
        as struct packet*;
    p->length = n;
    let i: uint64 = 0;
    while (i < n) {
        p->data[i] = (i * i) as int32;
        i += 1;
    }
    return p;
}

fn main() -> int32 {
    // The FAM contributes nothing to sizeof -- it is just the uint64 length.
    println("sizeof(struct packet) = %llu", sizeof(struct packet));

    let p = make_packet(5);
    print("data:");
    let i: uint64 = 0;
    while (i < p->length) {           // 0 1 4 9 16
        print(" %d", p->data[i]);
        i += 1;
    }
    println("");

    // Writing through the tail pointer is an ordinary indexed store.
    p->data[0] = 100;
    println("data[0] is now %d", p->data[0]);

    dealloc(p as byte*);
    return 0;
}
