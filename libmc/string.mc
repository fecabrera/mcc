import "list";

/**
 * Default slot count reserved by string_init before the first growth.
 */
@private
const DEFAULT_STRING_CAPACITY = 10;

/**
 * A growable, heap-backed byte string.
 *
 * `string` is a specialization of `list<char>` (same fields and layout), so
 * every operation re-lends its `mut`/`const` self receiver straight into the
 * matching `list_*` function through the transparent alias. Each `string_*`
 * wrapper is `@inline`, so the indirection costs nothing once optimized.
 */
type string = list<char>;

/**
 * Prepares a string for use, reserving DEFAULT_STRING_CAPACITY bytes.
 *
 * @param self: string to initialize
 */
@inline
fn string_init(mut self: string) {
    list_init(self, DEFAULT_STRING_CAPACITY);
}

/**
 * Deep-copies src into a fresh string: initializes dst to src's length and
 * appends every byte, so the two share no storage afterward. dst must be
 * uninitialized (or already destroyed) -- duplicating into a live string leaks
 * its buffer.
 *
 * @param dst: uninitialized string to copy src into
 * @param src: bytes to copy from -- another string borrows in
 *             (`a as slice<char>`); a string literal adapts directly
 */
fn string_duplicate(mut dst: string, const src: slice<char>) {
    list_init(dst, src.length);
    string_append(dst, src);
}

/**
 * Builds a string by copying a NUL-terminated C string: initializes self and
 * appends every byte up to (not including) the terminator, so the string owns
 * a private copy and shares no storage with str. self must be uninitialized
 * (or already destroyed) -- building into a live string leaks its buffer.
 *
 * @param self: uninitialized string to build into
 * @param str:  NUL-terminated bytes to copy from
 */
fn string_from_array(mut self: string, @nonnull str: char*) {
    string_init(self);
    string_append_array(self, str);
}

/**
 * Releases the string's storage. The string must be re-initialized with
 * string_init before being used again.
 *
 * @param self: string to destroy
 */
@inline
fn string_destroy(mut self: string) {
    list_destroy(self);
}

/**
 * Empties the string to length 0 while keeping its allocated capacity.
 *
 * @param self: string to reset
 */
@inline
fn string_reset(mut self: string) {
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
fn string_get(const self: string, index: uint64, mut out: char) -> bool {
    return list_get(self, index, out);    // re-lends the mut reference
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
fn string_set(mut self: string, index: uint64, value: char) -> bool {
    return list_set(self, index, value);
}

/**
 * Inserts a byte at the end of the string, growing it if needed.
 *
 * @param self:  string to append to
 * @param value: byte to push
 */
@inline
fn string_push(mut self: string, value: char) {
    list_push(self, value);
}

/**
 * Appends a run of bytes to the end of the string, growing it if needed.
 *
 * @param self: string to append to
 * @param str:  bytes to append -- another string borrows in
 *              (`b as slice<char>`); a string literal adapts directly
 **/
@inline
fn string_append(mut self: string, const str: slice<char>) {
    list_append(self, str);
}

/**
 * Appends a NUL-terminated C string byte by byte, up to (not including) the
 * terminator: the char* counterpart of string_append, for C strings whose
 * length is not known up front.
 *
 * @param self: string to append to
 * @param str:  NUL-terminated bytes to append
 **/
@inline
fn string_append_array(mut self: string, @nonnull str: char*) {
    let i: uint64 = 0;
    until (str[i] == '\0') {
        string_push(self, str[i]);
        i += 1;
    }
}

/**
 * Compares the string against a run of bytes for byte-for-byte equality.
 * Different lengths are never equal; empty runs compare equal.
 *
 * @param self: string to compare
 * @param str:  bytes to compare against -- another string borrows in
 *              (`b as slice<char>`); a literal adapts, so
 *              `string_eq(s, "hi")` works directly
 *
 * @return true if both sides have the same length and bytes, false otherwise
 */
fn string_eq(const self: string, const str: slice<char>) -> bool {
    if (self.length != str.length)
        return false;

    for i in range(self.length) {
        if (self.data[i] != str.data[i])
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
@inline
fn string_it(self: string*) -> struct iterator<string> {
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
@inline
fn string_next(it: struct iterator<string>*, out: char*) -> bool {
    return list_next(it, out);
}
