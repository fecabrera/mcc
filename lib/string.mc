import "array";

/**
 * Default slot count reserved by string_init before the first growth.
 */
@private
const DEFAULT_STRING_CAPACITY = 10;

/**
 * A growable, heap-backed byte string.
 *
 * `string` is a specialization of `array<uint8>` (same fields and layout), so a
 * `struct string*` upcasts to a `struct array<uint8>*` and every operation
 * forwards to the matching `array_*` function. Each `string_*` wrapper is
 * `@inline`, so the indirection costs nothing once optimized.
 */
struct string extends array<uint8>;

/**
 * Prepares a string for use, reserving DEFAULT_STRING_CAPACITY bytes.
 *
 * @param self: string to initialize
 */
@inline
fn string_init(self: struct string*) {
    array_init(self as struct array<uint8>*, DEFAULT_STRING_CAPACITY);
}

/**
 * Releases the string's storage. The string must be re-initialized with
 * string_init before being used again.
 *
 * @param self: string to destroy
 */
@inline
fn string_destroy(self: struct string*) {
    array_destroy(self as struct array<uint8>*);
}

/**
 * Empties the string to length 0 while keeping its allocated capacity.
 *
 * @param self: string to reset
 */
@inline
fn string_reset(self: struct string*) {
    array_reset(self as struct array<uint8>*);
}

/**
 * Reads the byte at index into out.
 *
 * @param self:  string to read from
 * @param index: position to read
 * @param out:   written with the byte at index when in bounds
 *
 * @return true if index is in bounds, false otherwise
 */
@inline
fn string_get(self: struct string*, index: uint64, out: uint8*) -> bool {
    return array_get(self as struct array<uint8>*, index, out);
}

/**
 * Overwrites the byte at index.
 *
 * @param self:  string to modify
 * @param index: position to write
 * @param value: byte to store at index
 *
 * @return true if index is in bounds, false otherwise
 */
@inline
fn string_set(self: struct string*, index: uint64, value: uint8) -> bool {
    return array_set(self as struct array<uint8>*, index, value);
}

/**
 * Appends a byte to the end of the string, growing it if needed.
 *
 * @param self:  string to append to
 * @param value: byte to append
 */
@inline
fn string_append(self: struct string*, value: uint8) {
    array_append(self as struct array<uint8>*, value);
}

/***************************************
 * Iteration
 ***************************************/

/**
 * A forward cursor over a string's bytes, produced by `string_it`. A
 * specialization of `array_iter<uint8>`, so it forwards to the array iterator.
 */
struct string_iter extends array_iter<uint8>;

/**
 * Begins an iteration over a string's bytes, front to back. Part of the
 * `string_it`/`string_next` protocol (used by `for ... in`); pair it with
 * `string_next`.
 *
 * @param self: string to iterate
 *
 * @return an iterator positioned at the first byte
 */
fn string_it(self: struct string*) -> struct string_iter {
    return array_it(self as struct array<uint8>*) as struct string_iter;
}

/**
 * Advances the iterator and writes the next byte into out.
 *
 * @param it:  iterator to advance
 * @param out: location the next byte is written to; untouched when the string
 *             is exhausted
 *
 * @return true if a byte was produced, false once iteration is complete
 */
fn string_next(it: struct string_iter*, out: uint8*) -> bool {
    return array_next(it as struct array_iter<uint8>*, out);
}
