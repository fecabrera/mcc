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
    while (key[i]) {
        hash = (hash ^ key[i] as uint64) * 1099511628211;
        i = i + 1;
    }
    return hash;
}