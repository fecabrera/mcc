/***************************************
 * Constants
 ***************************************/

const EXIT_SUCCESS = 0;   // successful termination status for exit()
const EXIT_FAILURE = 1;   // unsuccessful termination status for exit()
const RAND_MAX = 2147483647;   // the largest value rand() can return

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
@extern fn malloc(size: uint64) -> byte*;

/**
 * Allocates storage for count objects of size bytes each, zero-initialized.
 *
 * @param count: number of objects
 * @param size:  size of each object in bytes
 *
 * @return pointer to the storage, or null on failure
 */
@extern fn calloc(count: uint64, size: uint64) -> byte*;

/**
 * Resizes a previous allocation to size bytes, preserving its contents up to
 * the smaller of the old and new sizes.
 *
 * @param ptr:  pointer returned by malloc/calloc/realloc, or null
 * @param size: new size in bytes
 *
 * @return pointer to the resized storage (may differ from ptr), or null on failure
 */
@extern fn realloc(ptr: byte*, size: uint64) -> byte*;

/**
 * Allocates size bytes aligned to alignment, which must be a power of two and a
 * multiple of which size is. The result must be released with free.
 *
 * @param alignment: required alignment in bytes (a power of two)
 * @param size:      number of bytes to allocate (a multiple of alignment)
 *
 * @return pointer to the aligned storage, or null on failure
 */
@extern fn aligned_alloc(alignment: uint64, size: uint64) -> byte*;

/**
 * Releases storage previously returned by malloc/calloc/realloc. Passing null
 * does nothing.
 *
 * @param ptr: pointer to free
 */
@extern fn free(ptr: byte*);

/***************************************
 * Program termination
 ***************************************/

/**
 * Terminates the program normally, running cleanup handlers and flushing
 * streams.
 *
 * @param status: exit status reported to the environment
 */
@noreturn @extern fn exit(status: int32);

/**
 * Terminates the program immediately, without running cleanup handlers.
 *
 * @param status: exit status reported to the environment
 */
@noreturn @extern fn abort();

/**
 * Terminates the program immediately with the given status, without running
 * atexit handlers; streams are not flushed.
 *
 * @param status: exit status reported to the environment
 */
@noreturn @extern fn _Exit(status: int32);

/**
 * Registers a function to be called, in reverse order of registration, when the
 * program terminates normally (via exit or returning from main).
 *
 * @param func: handler taking no arguments and returning nothing
 *
 * @return 0 on success, non-zero if the handler could not be registered
 */
@extern fn atexit(func: fn()) -> int32;

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

/**
 * Returns the absolute value of a long.
 *
 * @param n: value whose magnitude is taken
 *
 * @return |n|; the result is undefined when n is LONG_MIN
 */
@extern fn labs(n: int64) -> int64;

/**
 * Returns the absolute value of a long long.
 *
 * @param n: value whose magnitude is taken
 *
 * @return |n|; the result is undefined when n is LLONG_MIN
 */
@extern fn llabs(n: int64) -> int64;

/**
 * The quotient and remainder of an `int` division, as returned by div. Crosses
 * the C boundary by value (two `int` fields, eight bytes).
 */
struct div_t {
    quot: int32;   // the quotient numer / denom
    rem: int32;    // the remainder numer % denom
}

/**
 * The quotient and remainder of a `long` division, as returned by ldiv (two
 * 64-bit fields to match the LP64 `long` the other bindings assume).
 */
struct ldiv_t {
    quot: int64;   // the quotient numer / denom
    rem: int64;    // the remainder numer % denom
}

/**
 * The quotient and remainder of a `long long` division, as returned by lldiv.
 */
struct lldiv_t {
    quot: int64;   // the quotient numer / denom
    rem: int64;    // the remainder numer % denom
}

/**
 * Divides numer by denom, returning the quotient and remainder together. The
 * quotient is rounded toward zero; denom must be non-zero.
 *
 * @param numer: dividend
 * @param denom: divisor (must be non-zero)
 *
 * @return a div_t whose quot is numer / denom and rem is numer % denom
 */
@extern fn div(numer: int32, denom: int32) -> struct div_t;

/**
 * The long form of div.
 *
 * @param numer: dividend
 * @param denom: divisor (must be non-zero)
 *
 * @return an ldiv_t whose quot is numer / denom and rem is numer % denom
 */
@extern fn ldiv(numer: int64, denom: int64) -> struct ldiv_t;

/**
 * The long long form of div.
 *
 * @param numer: dividend
 * @param denom: divisor (must be non-zero)
 *
 * @return an lldiv_t whose quot is numer / denom and rem is numer % denom
 */
@extern fn lldiv(numer: int64, denom: int64) -> struct lldiv_t;

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
@extern fn atoi(@nonnull str: char*) -> int32;

/**
 * Same as atoi but returns long.
 *
 * @param str: null-terminated string to parse
 *
 * @return parsed value; 0 if no digits are found
 */
@extern fn atol(@nonnull str: char*) -> int64;

/**
 * Same as atoi but returns long long.
 *
 * @param str: null-terminated string to parse
 *
 * @return parsed value; 0 if no digits are found
 */
@extern fn atoll(@nonnull str: char*) -> int64;

/**
 * Converts the initial portion of str to a double, skipping leading whitespace.
 *
 * @param str: null-terminated string to parse
 *
 * @return parsed value; 0.0 if no conversion can be performed
 */
@extern fn atof(@nonnull str: char*) -> float64;

/**
 * Converts the initial portion of str to a long, in the given base (2..36, or 0
 * to auto-detect a 0x/0 prefix). Unlike atoi, it reports where parsing stopped
 * and can detect overflow (via errno).
 *
 * @param str:    null-terminated string to parse
 * @param endptr: if non-null, receives a pointer to the first unparsed character
 * @param base:   numeric base 2..36, or 0 to auto-detect
 *
 * @return parsed value; 0 if no conversion can be performed
 */
@extern fn strtol(@nonnull str: char*, endptr: char**, base: int32) -> int64;

/**
 * Like strtol, but returns long long.
 *
 * @param str:    null-terminated string to parse
 * @param endptr: if non-null, receives a pointer to the first unparsed character
 * @param base:   numeric base 2..36, or 0 to auto-detect
 *
 * @return parsed value; 0 if no conversion can be performed
 */
@extern fn strtoll(@nonnull str: char*, endptr: char**, base: int32) -> int64;

/**
 * Like strtol, but returns unsigned long.
 *
 * @param str:    null-terminated string to parse
 * @param endptr: if non-null, receives a pointer to the first unparsed character
 * @param base:   numeric base 2..36, or 0 to auto-detect
 *
 * @return parsed value; 0 if no conversion can be performed
 */
@extern fn strtoul(@nonnull str: char*, endptr: char**, base: int32) -> uint64;

/**
 * Like strtol, but returns unsigned long long.
 *
 * @param str:    null-terminated string to parse
 * @param endptr: if non-null, receives a pointer to the first unparsed character
 * @param base:   numeric base 2..36, or 0 to auto-detect
 *
 * @return parsed value; 0 if no conversion can be performed
 */
@extern fn strtoull(@nonnull str: char*, endptr: char**, base: int32) -> uint64;

/**
 * Converts the initial portion of str to a double, reporting where parsing
 * stopped.
 *
 * @param str:    null-terminated string to parse
 * @param endptr: if non-null, receives a pointer to the first unparsed character
 *
 * @return parsed value; 0.0 if no conversion can be performed
 */
@extern fn strtod(@nonnull str: char*, endptr: char**) -> float64;

/***************************************
 * Pseudo-random numbers
 ***************************************/

/**
 * Returns the next pseudo-random integer in the range 0..RAND_MAX.
 *
 * @return a pseudo-random value in [0, RAND_MAX]
 */
@extern fn rand() -> int32;

/**
 * Seeds the pseudo-random number generator used by rand. The same seed yields
 * the same sequence.
 *
 * @param seed: seed value
 */
@extern fn srand(seed: uint32);

/***************************************
 * Searching and sorting
 ***************************************/

/**
 * Sorts an array in place using a comparison function. The array has count
 * elements of size bytes each, starting at base.
 *
 * @param base:  pointer to the first element
 * @param count: number of elements
 * @param size:  size of each element in bytes
 * @param cmp:   compares two elements; returns <0, 0, or >0 (a < b, a == b, a > b)
 */
@extern fn qsort(base: byte*, count: uint64, size: uint64,
                 cmp: fn(byte*, byte*) -> int32);

/**
 * Binary-searches a sorted array for key. The array has count elements of size
 * bytes each, starting at base, ordered consistently with cmp.
 *
 * @param key:   pointer to the value to find
 * @param base:  pointer to the first element
 * @param count: number of elements
 * @param size:  size of each element in bytes
 * @param cmp:   compares key against an element; returns <0, 0, or >0
 *
 * @return pointer to a matching element, or null if none is found
 */
@extern fn bsearch(key: byte*, base: byte*, count: uint64, size: uint64,
                   cmp: fn(byte*, byte*) -> int32) -> byte*;

/***************************************
 * Environment
 ***************************************/

/**
 * Looks up an environment variable by name.
 *
 * @param name: null-terminated variable name
 *
 * @return pointer to its value, or null if the variable is not set
 */
@extern fn getenv(@nonnull name: char*) -> char*;

/**
 * Passes command to the host command processor (a shell). A null command tests
 * whether a command processor is available.
 *
 * @param command: null-terminated command line, or null to probe availability
 *
 * @return the command's termination status, or (for a null command) non-zero
 *         if a processor is available
 */
@extern fn system(command: char*) -> int32;