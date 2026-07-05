/**
 * splitmix64 finalizer -- good avalanche for integer keys. This is the
 * uint64 core mixer; the generic overload below converts and forwards here.
 *
 * @param key: key to hash
 *
 * @return hashed key
 */
fn splitmix64(key: uint64) -> uint64 {
    let hash = key;
    hash ^= hash >> 30;
    hash *= 13787848793156543929;  // 0xbf58476d1ce4e5b9
    hash ^= hash >> 27;
    hash *= 10723151780598845931;  // 0x94d049bb133111eb
    hash ^= hash >> 31;
    return hash;
}

/**
 * splitmix64 finalizer for any integer-convertible key: converts the key to
 * uint64 and forwards to the core overload, so any integer (or pointer)
 * type works.
 *
 * @param key: key to hash
 *
 * @return hashed key
 */
@inline
fn splitmix64<T>(key: T) -> uint64 {
    return splitmix64(key as uint64);
}
