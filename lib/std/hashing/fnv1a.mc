/**
 * FNV-1a content hash. Reads the pointee as a zero-terminated buffer of T
 * elements (e.g. a NUL-terminated uint8* string), folding each element into
 * the hash; the terminator itself is not hashed.
 *
 * @param key: zero-terminated buffer to hash
 *
 * @return hash of the buffer's contents
 */
fn fnv1a<T>(key: T*) -> uint64 {
    let hash: uint64 = 14695981039346656037;
    let i: uint64 = 0;
    while (key![i]) {
        fnv1a_k(hash, key![i] as uint64);
        i += 1;
    }
    return hash;
}

/**
 * FNV-1a content hash of a slice: folds exactly `length` elements into the
 * hash. The length-bounded sibling of the zero-terminated overload above —
 * elements may be anything, zero included, so this is the right member for
 * binary data; an empty slice hashes to the FNV offset basis.
 *
 * @param key: slice whose elements to hash
 *
 * @return hash of the slice's contents
 */
fn fnv1a<T>(key: slice<T>) -> uint64 {
    let hash: uint64 = 14695981039346656037;
    for k in key {
        fnv1a_k(hash, k as uint64);
    }
    return hash;
}

/**
 * Folds one element into a running FNV-1a hash: XORs the element in, then
 * multiplies by the FNV prime. Internal; called by fnv1a once per element.
 *
 * @param hash: running hash, updated in the caller's storage
 * @param k:    element to fold in
 */
@private
fn fnv1a_k(hash: &uint64, k: uint64) {
    hash ^= k;
    hash *= 1099511628211;
}
