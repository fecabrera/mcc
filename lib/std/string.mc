import "std/list";
import "std/equality";

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
 * Prepares a string for use, reserving room for capacity bytes.
 *
 * @param self:     string to initialize
 * @param capacity: initial number of bytes to reserve space for
 */
@inline
fn string_init(mut self: string, capacity: uint64) {
    list_init(self, capacity);
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
fn string_init(mut dst: string, const src: slice<char>) {
    string_init(dst, src.length);
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
fn string_init(mut self: string, @nonnull str: char*) {
    string_init(self);
    string_append(self, str);
}

/**
 * Builds a string by copying the first n bytes of a raw char array:
 * initializes self with capacity n and appends each byte, so the string owns
 * a private copy and shares no storage with str. self must be uninitialized
 * (or already destroyed) -- building into a live string leaks its buffer.
 *
 * @param self: uninitialized string to build into
 * @param str:  source bytes to copy from
 * @param n:    number of bytes to copy from str
 */
fn string_init(mut self: string, @nonnull str: char*, n: uint64) {
    string_init(self, n);
    string_append(self, str, n);
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
 * Reports whether index is in bounds — whether string_at is defined for it.
 *
 * @param self:  string to test against
 * @param index: zero-based byte position
 *
 * @return true if index < the string's length
 */
@inline
fn string_has(const self: string, index: uint64) -> bool {
    return list_has(self, index);
}

/**
 * Unchecked mutable access: returns the byte at index as an lvalue, so
 * `string_at(s, 0) = '/'` writes in place. Undefined if index is out of
 * bounds — guard with string_has, or use string_get for the checked read.
 * The lvalue points into the string's storage: consume it before any call
 * that can grow the string.
 *
 * @param self:  string to access
 * @param index: zero-based byte position; must be in bounds
 *
 * @return the byte at index, as an assignable lvalue
 */
@inline
fn string_at(mut self: string, index: uint64) -> mut char {
    return list_at(self, index);
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
fn string_append(mut self: string, const str: slice<const char>) {
    list_append(self, str);
}

/**
 * Appends the first n bytes of a raw char array to the end of the string,
 * growing it if needed.
 *
 * @param self: string to append to
 * @param str:  source bytes to append from
 * @param n:    number of bytes to append from str
 **/
@inline
fn string_append(mut self: string, @nonnull str: char*, n: uint64) {
    list_append(self, str, n);
}

/**
 * Appends a NUL-terminated C string byte by byte, up to (not including) the
 * terminator: the overload for C strings whose length is not known up front.
 *
 * @param self: string to append to
 * @param str:  NUL-terminated bytes to append
 **/
@inline
fn string_append(mut self: string, @nonnull str: char*) {
    let i: uint64 = 0;
    until (str[i] == '\0') {
        string_push(self, str[i]);
        i += 1;
    }
}

/**
 * Compares a string against a run of bytes for byte-for-byte equality, a
 * string member of the equality protocol. Re-lends `self` into the generic
 * slice `equals` in `equality`. Different lengths are never equal; empty
 * runs compare equal.
 *
 * @param self: string to compare
 * @param str:  bytes to compare against -- another string borrows in
 *              (`b as slice<const char>`); a literal adapts, so
 *              `equals(s, "hi")` works directly
 *
 * @return true if both sides have the same length and bytes, false otherwise
 */
@inline
fn equals(const self: string, const str: slice<const char>) -> bool {
    return equals(self as slice<const char>, str);
}

/**
 * Compares a string against any char-slice for byte-for-byte equality, the
 * string-vs-char-slice member of the equality protocol. `T` is bounded to
 * `slice<char>`, so `str` may be another `string`, a `list<char>`, or a
 * `slice<char>` -- anything that `extends` the char slice binds `T` and needs
 * no explicit borrow at the call site. Both sides then borrow into the generic
 * slice `equals`, so neither is copied.
 *
 * @param self: string to compare
 * @param str:  any value whose type extends `slice<char>` (a `string`,
 *              `list<char>`, or `slice<char>`) to compare against
 *
 * @return true if both sides have the same length and bytes, false
 *         otherwise
 */
@inline
fn equals<T extends slice<char>>(const self: string, const str: T) -> bool {
    return equals(self as slice<const char>, str as slice<const char>);
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
