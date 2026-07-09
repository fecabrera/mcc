
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
@extern fn strcpy(@noalias @nonnull dest: char*, @noalias @nonnull src: char*) -> char*;

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
@extern fn strncpy(@noalias @nonnull dest: char*, @noalias @nonnull src: char*, count: uint64) -> char*;

/**
 * Appends the null-terminated string src to the end of dest, overwriting dest's
 * null terminator and adding a new one. The objects must not overlap and dest
 * must be large enough.
 *
 * @param dest: null-terminated destination string to append to
 * @param src:  null-terminated string to append
 *
 * @return dest
 */
@extern fn strcat(@noalias @nonnull dest: char*, @noalias @nonnull src: char*) -> char*;

/**
 * Appends at most count characters from src to the end of dest, then adds a
 * null terminator. The objects must not overlap and dest must be large enough.
 *
 * @param dest:  null-terminated destination string to append to
 * @param src:   string to append
 * @param count: maximum number of characters to copy from src
 *
 * @return dest
 */
@extern fn strncat(@noalias @nonnull dest: char*, @noalias @nonnull src: char*, count: uint64) -> char*;

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
@extern fn strlen(@nonnull str: char*) -> uint64;

/**
 * Returns the length of str, but reads at most count bytes. Unlike strlen, will not read past count
 * bytes even if no null terminator is found — safe for buffers that may not be null-terminated.
 *
 * @param str:   pointer to the string to measure
 * @param count: maximum number of bytes to inspect
 *
 * @return number of characters before the null terminator, or count if none found
 */
@extern fn strnlen(@nonnull str: char*, count: uint64) -> uint64;

/**
 * Compares two null-terminated strings lexicographically.
 *
 * @param lhs: pointer to the first null-terminated string
 * @param rhs: pointer to the second null-terminated string
 *
 * @return Negative if lhs < rhs, zero if equal, positive if lhs > rhs.
 */
@extern fn strcmp(@nonnull lhs: char*, @nonnull rhs: char*) -> int32;

/**
 * Compares at most count characters of two null-terminated strings lexicographically.
 *
 * @param lhs: pointer to the first null-terminated string
 * @param rhs: pointer to the second null-terminated string
 * @param count: maximum number of characters to compare
 *
 * @return Negative if lhs < rhs, zero if equal or count is zero, positive if lhs > rhs.
 */
@extern fn strncmp(@nonnull lhs: char*, @nonnull rhs: char*, count: uint64) -> int32;

/**
 * Compares two null-terminated strings according to the current locale's
 * collation order.
 *
 * @param lhs: pointer to the first null-terminated string
 * @param rhs: pointer to the second null-terminated string
 *
 * @return Negative if lhs < rhs, zero if equal, positive if lhs > rhs.
 */
@extern fn strcoll(@nonnull lhs: char*, @nonnull rhs: char*) -> int32;

/**
 * Transforms src into a form such that comparing two transformed strings with
 * strcmp matches comparing the originals with strcoll. Writes up to count bytes
 * (including the null terminator) into dest.
 *
 * @param dest:  destination buffer (may be null if count is 0)
 * @param src:   null-terminated source string
 * @param count: capacity of dest in bytes
 *
 * @return the length of the transformed string, excluding the null terminator;
 *         if it is >= count, the contents of dest are indeterminate
 */
@extern fn strxfrm(@noalias dest: char*, @noalias @nonnull src: char*, count: uint64) -> uint64;

/***************************************
 * String searching
 ***************************************/

/**
 * Finds the first occurrence of ch (as an unsigned char) in the null-terminated
 * string str. The null terminator is considered part of the string.
 *
 * @param str: null-terminated string to search
 * @param ch:  character to find (converted to unsigned char)
 *
 * @return pointer to the first match, or null if ch does not occur
 */
@extern fn strchr(@nonnull str: char*, ch: int32) -> char*;

/**
 * Finds the last occurrence of ch (as an unsigned char) in the null-terminated
 * string str. The null terminator is considered part of the string.
 *
 * @param str: null-terminated string to search
 * @param ch:  character to find (converted to unsigned char)
 *
 * @return pointer to the last match, or null if ch does not occur
 */
@extern fn strrchr(@nonnull str: char*, ch: int32) -> char*;

/**
 * Finds the first occurrence of the substring needle in haystack. An empty
 * needle matches at the start.
 *
 * @param haystack: null-terminated string to search
 * @param needle:   null-terminated substring to find
 *
 * @return pointer to the start of the first match, or null if not found
 */
@extern fn strstr(@nonnull haystack: char*, @nonnull needle: char*) -> char*;

/**
 * Returns the length of the initial run of str made up entirely of characters
 * in accept.
 *
 * @param str:    null-terminated string to scan
 * @param accept: null-terminated set of accepted characters
 *
 * @return number of leading characters that are all in accept
 */
@extern fn strspn(@nonnull str: char*, @nonnull accept: char*) -> uint64;

/**
 * Returns the length of the initial run of str made up entirely of characters
 * NOT in reject.
 *
 * @param str:    null-terminated string to scan
 * @param reject: null-terminated set of rejected characters
 *
 * @return number of leading characters before the first one in reject
 */
@extern fn strcspn(@nonnull str: char*, @nonnull reject: char*) -> uint64;

/**
 * Finds the first character in str that is also in accept.
 *
 * @param str:    null-terminated string to search
 * @param accept: null-terminated set of characters to look for
 *
 * @return pointer to the first matching character, or null if none match
 */
@extern fn strpbrk(@nonnull str: char*, @nonnull accept: char*) -> char*;

/**
 * Splits a string into tokens separated by any character in delim. The first
 * call passes the string; subsequent calls pass null to continue. Modifies the
 * string in place and keeps static state, so it is not reentrant or thread-safe.
 *
 * @param str:   string to tokenize on the first call, null to continue
 * @param delim: null-terminated set of delimiter characters
 *
 * @return pointer to the next token, or null when there are no more
 */
@extern fn strtok(str: char*, @nonnull delim: char*) -> char*;

/***************************************
 * Character array manipulation
 ***************************************/

/**
 * Finds the first occurrence of byte ch in the first count bytes of the object
 * pointed to by ptr.
 *
 * @param ptr:   pointer to the object to search
 * @param ch:    byte value to find (converted to unsigned char)
 * @param count: number of bytes to search
 *
 * @return pointer to the matching byte, or null if it does not occur
 */
@extern fn memchr(@nonnull ptr: byte*, ch: int32, count: uint64) -> byte*;

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
@extern fn memcmp(@nonnull lhs: byte*, @nonnull rhs: byte*, count: uint64) -> int32;

/**
 * Fills the first count bytes of the object pointed to by dest with the value ch.
 *
 * @param dest: pointer to the object to fill
 * @param ch: fill byte value (converted to unsigned char)
 * @param count: number of bytes to fill
 *
 * @return dest
 */
@extern fn memset(@nonnull dest: byte*, value: int32, count: uint64) -> byte*;

/**
 * Copies count bytes from src to dest. The objects must not overlap.
 *
 * @param dest: pointer to the object to copy to
 * @param src: pointer to the object to copy from
 * @param count: number of bytes to copy
 *
 * @return dest
 */
@extern fn memcpy(@noalias @nonnull dest: byte*, @noalias @nonnull src: byte*, count: uint64) -> byte*;

/**
 * Copies count bytes from src to dest. The objects may overlap.
 *
 * @param dest: pointer to the object to copy to
 * @param src: pointer to the object to copy from
 * @param count: number of bytes to copy
 *
 * @return dest
 */
@extern fn memmove(@nonnull dest: byte*, @nonnull src: byte*, count: uint64) -> byte*;

/***************************************
 * Error messages
 ***************************************/

/**
 * Returns a textual description of an error code such as one found in errno.
 * The returned string must not be modified, and may be overwritten by a later
 * call.
 *
 * @param errnum: an error code (e.g. an errno value)
 *
 * @return pointer to a static, null-terminated description string
 */
@extern fn strerror(errnum: int32) -> char*;