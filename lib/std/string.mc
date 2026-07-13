import "std/list";
import "std/equality";

/**
 * A growable, heap-backed byte string.
 *
 * `string` is `list<char>` (same fields and layout), so it inherits list's
 * constructors, destructor, and element accessors (`.push`, `.at`, `.get`,
 * `.set`, `.append`, `.reset`, `.has`) directly -- no wrappers. string adds
 * only what is text-specific: a NUL-terminated `char*` constructor and
 * `append`, and the `equals` members of the equality protocol below.
 */
type string = list<char>;

/**
 * Builds a string by copying a NUL-terminated C string: initializes self and
 * appends every byte up to (not including) the terminator, so the string owns
 * a private copy and shares no storage with str. self must be uninitialized
 * (or already destroyed) -- building into a live string leaks its buffer.
 *
 * @param self: uninitialized string to build into
 * @param str:  NUL-terminated bytes to copy from
 */
fn string::constructor(mut self: string, @nonnull str: char*) {
    string::constructor(self);
    self.append(str);
}

/**
 * Appends a NUL-terminated C string byte by byte, up to (not including) the
 * terminator: the overload for C strings whose length is not known up front.
 *
 * @param self: string to append to
 * @param str:  NUL-terminated bytes to append
 **/
@inline
fn string::append(mut self: string, @nonnull str: char*) {
    let i: uint64 = 0;
    until (str[i] == '\0') {
        self.push(str[i]);
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
 *              `s.equals("hi")` works directly
 *
 * @return true if both sides have the same length and bytes, false otherwise
 */
@inline
fn string::equals(const self: string, const str: slice<const char>) -> bool {
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
fn string::equals<T extends slice<char>>(const self: string, const str: T) -> bool {
    return self.equals(str as slice<const char>);
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
