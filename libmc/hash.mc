import "hashing/splitmix64";
import "hashing/fnv1a";

/**
 * Hashes an integer (or pointer identity) key by value via splitmix64.
 *
 * @param key: key to hash
 *
 * @return hashed key
 */
fn hash<T>(key: T) -> uint64 {
    return splitmix64(key);
}

/**
 * Hashes a pointer key by content via FNV-1a: the pointee is read as a
 * NUL-terminated buffer (e.g. a string). Selected over the by-value
 * overload whenever the key is a pointer.
 *
 * @param key: NUL-terminated buffer to hash
 *
 * @return hashed key
 */
fn hash<T>(key: T*) -> uint64 {
    return fnv1a(key);
}
