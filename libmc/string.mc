import "list";
import "range";
import "iteration/iterator";

/**
 * Default slot count reserved by string_init before the first growth.
 */
@private
const DEFAULT_STRING_CAPACITY = 10;

/**
 * A growable, heap-backed byte string.
 *
 * `string` is a specialization of `list<char>` (same fields and layout), so a
 * `struct string*` upcasts to a `struct list<char>*` and every operation
 * forwards to the matching `list_*` function. Each `string_*` wrapper is
 * `@inline`, so the indirection costs nothing once optimized.
 */
type string = list<char>;

/**
 * Prepares a string for use, reserving DEFAULT_STRING_CAPACITY bytes.
 *
 * @param self: string to initialize
 */
@inline
fn string_init(self: struct string*) {
    list_init(self, DEFAULT_STRING_CAPACITY);
}

/**
 * Deep-copies src into a fresh string: initializes dst with src's capacity and
 * appends every byte of src, so the two share no storage afterward. dst must be
 * uninitialized (or already destroyed) -- duplicating into a live string leaks
 * its buffer.
 *
 * @param dst: uninitialized string to copy src into
 * @param src: string to copy from
 */
fn string_duplicate(dst: struct string*, src: struct string*) {
    list_init(dst, src->capacity);
    string_append(dst, src);
}

/**
 * Builds a string from the first n bytes of a raw byte array: initializes self
 * with capacity n and appends each byte, so the string owns a private copy and
 * shares no storage with str. self must be uninitialized (or already destroyed)
 * -- building into a live string leaks its buffer.
 *
 * @param self: uninitialized string to build into
 * @param str:  source byte array to copy from
 * @param n:    number of bytes to copy from str
 */
fn string_from_array(self: struct string*, str: char*) {
    string_init(self);

    let i: uint64 = 0;
    until (str[i] == '\0') {
        string_push(self, str[i]);
        i = i + 1;
    }
}

/**
 * Builds a string by copying a borrowed run of bytes: initializes self and
 * appends every byte of the slice, so the string owns a private copy. self must
 * be uninitialized (or already destroyed). A string literal borrows in directly
 * (`str as slice<char>`), which drops the literal's trailing NUL.
 *
 * @param self: uninitialized string to build into
 * @param str:  byte slice to copy from
 */
@inline
fn string_from_slice(self: struct string*, const str: slice<char>) {
    list_from_slice(self, str);
}

/**
 * Releases the string's storage. The string must be re-initialized with
 * string_init before being used again.
 *
 * @param self: string to destroy
 */
@inline
fn string_destroy(self: struct string*) {
    list_destroy(self);
}

/**
 * Empties the string to length 0 while keeping its allocated capacity.
 *
 * @param self: string to reset
 */
@inline
fn string_reset(self: struct string*) {
    list_reset(self);
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
fn string_get(self: struct string*, index: uint64, out: char*) -> bool {
    return list_get(self, index, out);
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
fn string_set(self: struct string*, index: uint64, value: char) -> bool {
    return list_set(self, index, value);
}

/**
 * Inserts a byte at the end of the string, growing it if needed.
 *
 * @param self:  string to append to
 * @param value: byte to push
 */
@inline
fn string_push(self: struct string*, value: char) {
    list_push(self, value);
}

/**
 * Appends another string to the end of the string, growing it if needed.
 *
 * @param self:  string to append to
 * @param value: string to append
 **/
@inline
fn string_append(self: struct string*, str: struct string*) {
    list_append(self, str);
}

/**
 * Compares two strings for byte-for-byte equality. Strings of different lengths
 * are never equal; empty strings compare equal.
 *
 * @param self: first string
 * @param str:  second string
 *
 * @return true if both strings have the same length and bytes, false otherwise
 */
fn string_eq(self: struct string*, str: struct string*) -> bool {
    if (self->length != str->length)
        return false;
    
    let r = struct range { end = self->length };
    for i in &r {
        if (self->data[i] != str->data[i])
            return false;
    }
    
    return true;
}

/***************************************
 * Iteration
 ***************************************/

/**
 * Begins an iteration over a string's bytes, front to back. Part of the
 * `string_it`/`string_next` protocol (used by `for ... in`); pair it with
 * `string_next`.
 *
 * @param self: string to iterate
 *
 * @return an iterator positioned at the first byte
 */
fn string_it(self: struct string*) -> struct iterator<struct string> {
    return list_it(self);
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
fn string_next(it: struct iterator<struct string>*, out: char*) -> bool {
    return list_next(it, out);
}
