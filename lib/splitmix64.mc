/**
 * splitmix64 finalizer -- good avalanche for integer keys.
 *
 * @param key: key to hash
 *
 * @return hashed key
 */
fn splitmix64(key: uint64) -> uint64 {
    key = key ^ (key >> 30);
    key = key * 13787848793156543929;  // 0xbf58476d1ce4e5b9
    key = key ^ (key >> 27);
    key = key * 10723151780598845931;  // 0x94d049bb133111eb
    key = key ^ (key >> 31);
    return key;
}
