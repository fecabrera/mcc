/**
 * splitmix64 finalizer -- good avalanche for integer keys. The key is
 * converted to uint64 first, so any integer (or pointer) type works.
 *
 * @param key: key to hash
 *
 * @return hashed key
 */
fn splitmix64<T>(key: T) -> uint64 {
    let hash = key as uint64;
    hash = hash ^ (hash >> 30);
    hash = hash * 13787848793156543929;  // 0xbf58476d1ce4e5b9
    hash = hash ^ (hash >> 27);
    hash = hash * 10723151780598845931;  // 0x94d049bb133111eb
    hash = hash ^ (hash >> 31);
    return hash;
}
