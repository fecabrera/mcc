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
 * Compares a string against a NUL-terminated C string byte by byte: equal
 * when every byte matches and the C string's terminator sits exactly at the
 * string's length. The overload for C strings whose length is not known up
 * front; the slice members inherited from list<char> cover everything else
 * (a literal, another string, a slice).
 *
 * @param self: string to compare
 * @param arr:  NUL-terminated bytes to compare against
 *
 * @return true if the string's bytes are exactly the C string's, false
 *         otherwise
 */
@inline
fn string::equals(const self: string, @nonnull arr: char*) -> bool {
    for el in enumerate(self) {
        if (el.value != arr[el.index])
            return false;
    }

    if (arr[self.length] != '\0')
        return false;

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
