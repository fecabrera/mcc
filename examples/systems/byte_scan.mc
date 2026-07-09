import "libc/stdio";

// Pointer arithmetic, C's element-scaled semantics under the shipped operators:
// `p + n` / `p - n` advance by elements, `p - q` is the signed element
// distance, and `< <= > >=` order pointers of identical type. `uint8*` is the
// raw-memory pointer, so its element size is 1 and its arithmetic is byte
// arithmetic -- the natural fit for a byte scanner.

// A memchr-style scan: return a pointer to the first `target` byte in the
// half-open range `[start, end)`, or null if it holds none. The `while (p <
// end)` loop is the pointer-ordering idiom, and `p += 1` walks one byte at a
// time. `p < end` bounds the scan but is no null proof (ordering does not
// narrow), so the binding seeds one with `start!`; arithmetic on p keeps it.
fn find_byte(start: uint8*, end: uint8*, target: uint8) -> uint8* {
    let p = start!;
    while (p < end) {
        if (*p == target) {
            return p;
        }
        p += 1;
    }
    return null;
}

fn main() -> int32 {
    let data: uint8[8];
    let i: int32 = 0;
    while (i < 8) {
        data[i] = (i * 11) as uint8;   // 0, 11, 22, 33, 44, 55, 66, 77
        i = i + 1;
    }

    let start = &data[0];
    let end = start + 8;               // one-past-end: 8 elements past start

    // Element-scaled advance: `start + 3` is exactly `&data[3]`.
    printf("data[3] via start + 3 = %u\n", *(start + 3));

    // Pointer difference is the element distance (bytes here, since uint8* has
    // element size 1), not a raw address gap.
    printf("range length = %lld\n", end - start);

    // Scan for a present byte, then report its offset with pointer difference.
    let hit = find_byte(start, end, 44);
    if (hit != null) {
        printf("found 44 at offset %lld\n", hit - start);
    }

    // Scan for an absent byte: the loop runs off the end and returns null.
    let miss = find_byte(start, end, 200);
    if (miss == null) {
        printf("200 not found\n");
    }

    // Walk backward with `p - n`: `end - 1` addresses the last element.
    let last = end - 1;
    printf("last byte = %u\n", *last);

    return 0;
}

// See also: memory/pointers.mc for address-of, dereference, indexing, `as`
// casts, and sizeof; functions/nonnull.mc for the @nonnull promise a pointer
// parameter can carry (pointer arithmetic proves it, just as &p[n] does).
