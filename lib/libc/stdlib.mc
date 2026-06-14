/***************************************
 * Memory management
 ***************************************/

/**
 * Allocates size bytes of uninitialized storage.
 *
 * @param size: number of bytes to allocate
 *
 * @return pointer to the storage, or null on failure
 */
@extern fn malloc(size: uint64) -> uint8*;

/**
 * Allocates storage for count objects of size bytes each, zero-initialized.
 *
 * @param count: number of objects
 * @param size:  size of each object in bytes
 *
 * @return pointer to the storage, or null on failure
 */
@extern fn calloc(count: uint64, size: uint64) -> uint8*;

/**
 * Resizes a previous allocation to size bytes, preserving its contents up to
 * the smaller of the old and new sizes.
 *
 * @param ptr:  pointer returned by malloc/calloc/realloc, or null
 * @param size: new size in bytes
 *
 * @return pointer to the resized storage (may differ from ptr), or null on failure
 */
@extern fn realloc(ptr: uint8*, size: uint64) -> uint8*;

/**
 * Releases storage previously returned by malloc/calloc/realloc. Passing null
 * does nothing.
 *
 * @param ptr: pointer to free
 */
@extern fn free(ptr: uint8*);

/***************************************
 * Program termination
 ***************************************/

/**
 * Terminates the program normally, running cleanup handlers and flushing
 * streams.
 *
 * @param status: exit status reported to the environment
 */
@extern fn exit(status: int32);

/**
 * Terminates the program immediately, without running cleanup handlers.
 *
 * @param status: exit status reported to the environment
 */
@extern fn abort();

/***************************************
 * Integer arithmetic
 ***************************************/

/**
 * Returns the absolute value of n.
 *
 * @param n: value whose magnitude is taken
 *
 * @return |n|; the result is undefined when n is INT_MIN
 */
@extern fn abs(n: int32) -> int32;

/***************************************
 * String conversion
 ***************************************/

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