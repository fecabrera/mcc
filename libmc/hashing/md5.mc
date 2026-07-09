import "memory";
import "libc/math";   // sin/floor/fabs, for the K constant table

/**
 * Rotates x left by s bits.
 *
 * @param x: value to rotate
 * @param s: rotation distance in bits; must be in 1..31
 *
 * @return x rotated left by s
 */
@static
fn rotl32(x: uint32, s: uint32) -> uint32 {
    return (x << s) | (x >> (32 - s));
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
    return p![0] as uint32
         | (p![1] as uint32 << 8)
         | (p![2] as uint32 << 16)
         | (p![3] as uint32 << 24);
}

/**
 * Stores w into four bytes, little-endian.
 *
 * @param p: buffer to write to; must hold at least 4 bytes
 * @param w: value to store
 */
@static
fn store_le(p: uint8*, w: uint32) {
    p![0] = w as uint8;
    p![1] = (w >> 8) as uint8;
    p![2] = (w >> 16) as uint8;
    p![3] = (w >> 24) as uint8;
}

/**
 * Per-round rotate amount (RFC 1321's s table, computed by position).
 *
 * @param round: round index, 0..63
 *
 * @return how far that round's mix rotates left
 */
@static
fn md5_shift(round: uint32) -> uint32 {
    let pos = round % 4;
    if (round < 16) {
        if (pos == 0) return 7;
        if (pos == 1) return 12;
        if (pos == 2) return 17;
        return 22;
    }
    if (round < 32) {
        if (pos == 0) return 5;
        if (pos == 1) return 9;
        if (pos == 2) return 14;
        return 20;
    }
    if (round < 48) {
        if (pos == 0) return 4;
        if (pos == 1) return 11;
        if (pos == 2) return 16;
        return 23;
    }
    if (pos == 0) return 6;
    if (pos == 1) return 10;
    if (pos == 2) return 15;
    return 21;
}

/**
 * Per-round additive constant: floor(2^32 * |sin(round + 1)|), computed
 * instead of tabulated (RFC 1321 defines the table exactly this way).
 *
 * @param round: round index, 0..63
 *
 * @return that round's additive constant
 */
@static
fn md5_k(round: uint32) -> uint32 {
    return (fabs(sin((round + 1) as float64)) * 4294967296.0) as uint32;
}

/**
 * MD5 digest (RFC 1321) of a byte buffer. Binary-safe. MD5 is broken for
 * security purposes; use it for checksums and interop, not authentication.
 *
 * @param data:   buffer to digest
 * @param length: number of bytes to digest
 * @param digest: written with the 16-byte digest
 */
fn md5(@nonnull data: uint8*, length: uint64, @nonnull digest: uint8*) {
    // Pad to a multiple of 64 bytes: 0x80, zeros, then the bit length as a
    // little-endian uint64.
    let total = ((length + 8) / 64 + 1) * 64;
    let buf = alloc<uint8>(total)!;
    bytecopy(buf, data, length);   // allocation assumed to succeed
    buf[length] = 128;
    memset(&buf[length + 1], 0, total - length - 1);
    let bits = length * 8;
    let j: uint64 = 0;
    while (j < 8) {
        buf[total - 8 + j] = (bits >> (j * 8)) as uint8;
        j += 1;
    }

    let a0: uint32 = 1732584193;   // 0x67452301
    let b0: uint32 = 4023233417;   // 0xefcdab89
    let c0: uint32 = 2562383102;   // 0x98badcfe
    let d0: uint32 = 271733878;    // 0x10325476

    let chunk: uint64 = 0;
    while (chunk < total) {
        let a = a0;
        let b = b0;
        let c = c0;
        let d = d0;

        let round: uint32 = 0;
        while (round < 64) {
            let f: uint32 = 0;
            let g: uint32 = 0;
            if (round < 16) {
                f = (b & c) | ((b ^ 4294967295) & d);
                g = round;
            } else if (round < 32) {
                f = (d & b) | ((d ^ 4294967295) & c);
                g = (5 * round + 1) % 16;
            } else if (round < 48) {
                f = b ^ c ^ d;
                g = (3 * round + 5) % 16;
            } else {
                f = c ^ (b | (d ^ 4294967295));
                g = (7 * round) % 16;
            }
            let m = load_le(&buf[chunk + g as uint64 * 4]);
            f += a + md5_k(round) + m;
            a = d;
            d = c;
            c = b;
            b += rotl32(f, md5_shift(round));
            round += 1;
        }

        a0 += a;
        b0 += b;
        c0 += c;
        d0 += d;
        chunk += 64;
    }

    store_le(&digest[0], a0);
    store_le(&digest[4], b0);
    store_le(&digest[8], c0);
    store_le(&digest[12], d0);
    dealloc(buf);
}
