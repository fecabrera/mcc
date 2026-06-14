
/**
 * Converts a signed integer to a null-terminated string in the given base. Negative values are only
 * supported for base 10.
 *
 * @param value: integer to convert
 * @param str: output buffer (must be large enough to hold the result)
 * @param base: numeric base (e.g. 2, 8, 10, 16)
 *
 * @return str
 */
@extern fn itoa(value: int64, str: uint8*, base: int32) -> uint8*;

/**
 * Converts the initial decimal digits of str to an integer, skipping leading whitespace and
 * handling an optional leading sign. Stops at the first non-digit character.
 *
 * @param str: null-terminated string to parse
 *
 * @return parsed value; 0 if no digits are found
 */
@extern fn atoi(str: uint8*) -> int32;

/**
 * Same as atoi but returns long.
 *
 * @param str: null-terminated string to parse
 *
 * @return parsed value; 0 if no digits are found
 */
@extern fn atol(str: uint8*) -> int64;

/**
 * Same as atoi but returns long long.
 *
 * @param str: null-terminated string to parse
 *
 * @return parsed value; 0 if no digits are found
 */
@extern fn atoll(str: uint8*) -> int64;