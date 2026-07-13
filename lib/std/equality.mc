// The equality protocol's baseline overload set: `equals(a, b)` reports
// whether two values are equal. Overload sets are open, so a type joins by
// adding an `equals` overload for itself in its own module.

/**
 * Compares two slices element by element. Different lengths are never
 * equal; empty slices compare equal. Both sides must view the same element
 * type, and elements compare with `!=`, so T must be a scalar or pointer
 * type (element-wise struct comparison needs its own overload).
 *
 * A `char` slice compares against a string literal directly, so
 * `equals(modifier, "y")` works as-is. A `string` is a `list<char>`, so it
 * borrows in with an explicit `as` (`equals(s as slice<char>, "hi")`) or
 * compares through its own `s.equals("hi")` member.
 *
 * @param self: slice to compare
 * @param str:  slice to compare against
 *
 * @return true if both sides have the same length and elements, false
 *         otherwise
 */
// should turn to slice::equals() once OOP lands
fn equals<T>(const self: slice<T>, const str: slice<T>) -> bool {
    if (self.length != str.length)
        return false;

    for i in range(self.length) {
        if (self.data![i] != str.data![i])
            return false;
    }

    return true;
}
