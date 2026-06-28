import "range";

/**
 * Rotates x left by r bits.
 *
 * @param x: value to rotate
 * @param r: rotation distance in bits; must be in 1..31
 *
 * @return x rotated left by r
 */
@static
fn rotl32(x: uint32, r: uint32) -> uint32 {
    return (x << r) | (x >> (32 - r));
}

/**
 * Loads four bytes as a little-endian uint32.
 *
 * @param p: buffer to read from; must hold at least 4 bytes
 *
 * @return p[0..3] assembled least-significant byte first
 */
@static
fn load_le(p: uint8*) -> uint32 {
    return p[0] as uint32
         | (p[1] as uint32 << 8)
         | (p[2] as uint32 << 16)
         | (p[3] as uint32 << 24);
}

/**
 * MurmurHash3 (x86, 32-bit variant) over a byte buffer. Binary-safe: the
 * buffer may contain zero bytes.
 *
 * @param key:    buffer to hash
 * @param length: number of bytes to hash
 * @param seed:   starting hash state; different seeds give independent hashes
 *
 * @return 32-bit hash of the buffer's contents
 */
fn murmur3(key: uint8*, length: uint64, seed: uint32) -> uint32 {
    let h: uint32 = seed;
    let nblocks = length / 4;

    let r = struct range { end = nblocks };
    for i in &r {
        let k = load_le(&key[i * 4]);
        k = k * 3432918353;  // 0xcc9e2d51
        k = rotl32(k, 15);
        k = k * 461845907;   // 0x1b873593
        h = h ^ k;
        h = rotl32(h, 13);
        h = h * 5 + 3864292196;  // 0xe6546b64
    }

    let tail = nblocks * 4;
    let rem = length & 3;
    let kt: uint32 = 0;
    if (rem == 3)
        kt = kt ^ (key[tail + 2] as uint32 << 16);
    if (rem >= 2)
        kt = kt ^ (key[tail + 1] as uint32 << 8);
    if (rem >= 1) {
        kt = kt ^ key[tail] as uint32;
        kt = kt * 3432918353;
        kt = rotl32(kt, 15);
        kt = kt * 461845907;
        h = h ^ kt;
    }

    h = h ^ length as uint32;
    h = h ^ (h >> 16);
    h = h * 2246822507;  // 0x85ebca6b
    h = h ^ (h >> 13);
    h = h * 3266489909;  // 0xc2b2ae35
    h = h ^ (h >> 16);
    return h;
}
