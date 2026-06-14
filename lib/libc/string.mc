
/***************************************
 * String manipulation
 ***************************************/

/**
 * Copies the null-terminated string pointed to by src into dest, including the null terminator.
 * The objects must not overlap.
 *
 * @param dest: pointer to the character array to copy to
 * @param src: pointer to the null-terminated string to copy from
 *
 * @return dest
 */
@extern fn strcpy(dest: uint8*, src: uint8*) -> uint8*;

/**
 * Copies at most count characters from src into dest. If src is shorter than count,
 * the remaining characters in dest are padded with null bytes.
 *
 * @param dest: pointer to the character array to copy to
 * @param src: pointer to the null-terminated string to copy from
 * @param count: maximum number of characters to copy
 *
 * @return dest
 */
@extern fn strncpy(dest: uint8*, src: uint8*, count: uint64) -> uint8*;

/***************************************
 * String examination
 ***************************************/

/**
 * Returns the length of the null-terminated string str, not including the null terminator.
 *
 * @param str: pointer to the null-terminated string to measure
 *
 * @return Number of characters in str.
 */
@extern fn strlen(str: uint8*) -> uint64;

/**
 * Returns the length of str, but reads at most count bytes. Unlike strlen, will not read past count
 * bytes even if no null terminator is found — safe for buffers that may not be null-terminated.
 *
 * @param str:   pointer to the string to measure
 * @param count: maximum number of bytes to inspect
 *
 * @return number of characters before the null terminator, or count if none found
 */
@extern fn strnlen(str: uint8*, count: uint64) -> uint64;

/**
 * Compares two null-terminated strings lexicographically.
 *
 * @param lhs: pointer to the first null-terminated string
 * @param rhs: pointer to the second null-terminated string
 *
 * @return Negative if lhs < rhs, zero if equal, positive if lhs > rhs.
 */
@extern fn strcmp(lhs: uint8*, rhs: uint8*) -> int32;

/**
 * Compares at most count characters of two null-terminated strings lexicographically.
 *
 * @param lhs: pointer to the first null-terminated string
 * @param rhs: pointer to the second null-terminated string
 * @param count: maximum number of characters to compare
 *
 * @return Negative if lhs < rhs, zero if equal or count is zero, positive if lhs > rhs.
 */
@extern fn strncmp(lhs: uint8*, rhs: uint8*, count: uint64) -> int32;

/***************************************
 * Character array manipulation
 ***************************************/

/**
 * Compares the first count bytes of the objects pointed to by lhs and rhs lexicographically.
 * The sign of the result is the sign of the difference between the first pair of differing bytes.
 * Behavior is undefined if either pointer is null or access goes beyond the end of either object.
 *
 * @param lhs: pointer to the first object
 * @param rhs: pointer to the second object
 * @param count: number of bytes to compare
 *
 * @return Negative if lhs < rhs, zero if equal or count is zero, positive if lhs > rhs.
 */
@extern fn memcmp(lhs: uint8*, rhs: uint8*, count: uint64) -> int32;

/**
 * Fills the first count bytes of the object pointed to by dest with the value ch.
 *
 * @param dest: pointer to the object to fill
 * @param ch: fill byte value (converted to unsigned char)
 * @param count: number of bytes to fill
 *
 * @return dest
 */
@extern fn memset(dest: uint8*, value: int32, count: uint64) -> uint8*;

/**
 * Copies count bytes from src to dest. The objects must not overlap.
 *
 * @param dest: pointer to the object to copy to
 * @param src: pointer to the object to copy from
 * @param count: number of bytes to copy
 *
 * @return dest
 */
@extern fn memcpy(dest: uint8*, src: uint8*, count: uint64) -> uint8*;

/**
 * Copies count bytes from src to dest. The objects may overlap.
 *
 * @param dest: pointer to the object to copy to
 * @param src: pointer to the object to copy from
 * @param count: number of bytes to copy
 *
 * @return dest
 */
@extern fn memmove(dest: uint8*, src: uint8*, count: uint64) -> uint8*;