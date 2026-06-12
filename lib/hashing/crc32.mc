/**
 * CRC-32 (IEEE 802.3, the zlib/PNG polynomial) over a byte buffer,
 * computed bitwise with the reflected polynomial 0xEDB88320. Binary-safe:
 * the buffer may contain zero bytes.
 *
 * @param data:   buffer to checksum
 * @param length: number of bytes to checksum
 *
 * @return CRC-32 of the buffer's contents
 */
fn crc32(data: uint8*, length: uint64) -> uint32 {
    let crc: uint32 = 4294967295;  // 0xffffffff

    let i: uint64 = 0;
    while (i < length) {
        crc = crc ^ data[i] as uint32;
        let bit: int32 = 0;
        while (bit < 8) {
            if (crc & 1)
                crc = (crc >> 1) ^ 3988292384;  // 0xedb88320
            else
                crc = crc >> 1;
            bit = bit + 1;
        }
        i = i + 1;
    }

    return crc ^ 4294967295;
}
