import "std/memory";
import "std/slice";

@private
const DEFAULT_LIST_CAPACITY = 2;

/**
 * A growable, heap-backed list of T.
 */
struct list<T> extends slice<T> {
    capacity: uint64;
}

fn list<T>::constructor(self: &list<T>) {
    list<T>::constructor(self, DEFAULT_LIST_CAPACITY);
}

/**
 * Prepares an list for use, allocating room for capacity elements.
 *
 * @param self:     list to initialize
 * @param capacity: initial number of elements to reserve space for
 */
fn list<T>::constructor(self: &list<T>, capacity: uint64) {
    self.data = alloc<T>(capacity);
    self.length = 0;
    self.capacity = capacity;
}

/**
 * Deep-copies src into a fresh list: initializes self to src's length and
 * appends every element, so the two share no storage afterward. self must be
 * uninitialized (or already destroyed) -- duplicating into a live list leaks
 * its buffer.
 *
 * @param self: uninitialized list to copy src into
 * @param src:  elements to copy from -- any borrowed run, so a source list
 *              (`a as slice<T>`) or an array's borrow both work
 */
fn list<T>::constructor(self: &list<T>, const src: slice<T>) {
    list<T>::constructor(self, src.length);
    self.append(src);
}

/**
 * Builds a list from the first n elements of a raw array: initializes self with
 * capacity n and appends each element, so the list owns a private copy and
 * shares no storage with arr. self must be uninitialized (or already destroyed)
 * -- building into a live list leaks its buffer.
 *
 * @param self: uninitialized list to build into
 * @param arr:  source array to copy from
 * @param n:    number of elements to copy from arr
 */
fn list<T>::constructor<S: int64 | uint64 | int32 | uint32>(self: &list<T>, @nonnull arr: T*, n: S) {
    let count = n as uint64;
    list<T>::constructor(self, count);
    self.append(arr, count);
}

/**
 * Releases the list's storage. The list must be re-initialized with the
 * `list<T>` constructor before being used again.
 *
 * @param self: list to destroy
 */
fn list<T>::destructor(self: &list<T>) {
    dealloc(self.data);

    self.data = null;
    self.length = 0;
    self.capacity = 0;
}

/**
 * Empties the list without releasing its storage.
 *
 * @param self: list to reset
 */
fn list<T>::reset(self: &list<T>) {
    self.length = 0;
}

/**
 * Reports whether index is in bounds — whether `.at` is defined for it.
 *
 * @param self:  list to test against
 * @param index: zero-based index
 *
 * @return true if index < self.length
 */
fn list<T>::has(const self: &list<T>, index: uint64) -> bool {
    return index < self.length;
}

/**
 * Unchecked mutable access: returns the element at index as an lvalue, so
 * `xs.at(i) = v` writes in place and `let x = xs.at(i)` copies
 * out. Undefined if index is out of bounds — guard with `.has`, or use
 * `.get` for the checked read. The lvalue points into the list's storage:
 * consume it before any call that can grow the list.
 *
 * The list's `[]` operator: `xs[i]` reads through this accessor and
 * `xs[i] = v` (or `xs[i] += v`) writes through its lvalue.
 *
 * @param self:  list to access
 * @param index: zero-based index; must be < self.length
 *
 * @return the element at index, as an assignable lvalue
 */
@accessor
fn list<T>::at(self: &list<T>, index: uint64) -> &T {
    return self.data![index];
}

/**
 * Reads the element at index into out.
 *
 * @param self:  list to read from
 * @param index: zero-based index; must be < self->length
 * @param out:   written with the element; unchanged if index is out of bounds
 *
 * @return true on success, false if index is out of bounds
 */
fn list<T>::get(const self: &list<T>, index: uint64, out: &T) -> bool {
    if (!self.has(index))
        return false;

    out = self.data![index];
    return true;
}

/**
 * Overwrites the element at index with value.
 *
 * @param self:  list to write into
 * @param index: zero-based index; must be < self->length
 * @param value: value to store
 *
 * @return true on success, false if index is out of bounds
 */
fn list<T>::set(self: &list<T>, index: uint64, value: T) -> bool {
    if (!self.has(index))
        return false;

    self.data![index] = value;
    return true;
}

/**
 * Inserts value to the end of the list, growing its storage if needed.
 *
 * @param self:  list to append to
 * @param value: value to append
 */
fn list<T>::push(self: &list<T>, value: T) {
    if (self.length == self.capacity)
        self.grow();

    self.data![self.length] = value;
    self.length += 1;
}

/**
 * Appends a run of elements to the end of the list, growing it if needed.
 *
 * @param self:  list to append to
 * @param items: elements to append -- any borrowed run, so a source list
 *               (`b as slice<T>`) or an array's borrow both work
 **/
fn list<T>::append(self: &list<T>, const items: slice<const T>) {
    for value in items {
        self.push(value);
    }
}

/**
 * Appends the first n elements of a raw array to the end of the list,
 * growing it if needed.
 *
 * @param self:  list to append to
 * @param items: source array to copy from
 * @param n:     number of elements to append from items
 **/
fn list<T>::append(self: &list<T>, @nonnull items: T*, n: uint64) {
    for i in range(n) {
        self.push(items[i]);
    }
}

/**
 * Compares a list against a run of elements for element-by-element equality,
 * the list member of the equality protocol. Re-lends `self` into the generic
 * slice `equals` in `equality`. Different lengths are never equal; empty
 * runs compare equal.
 *
 * @param self: list to compare
 * @param lst:  elements to compare against -- another list borrows in
 *              (`b as slice<const T>`); for a list<char> a string literal
 *              adapts, so `s.equals("hi")` works directly
 *
 * @return true if both sides have the same length and elements, false
 *         otherwise
 */
@inline
fn list<T>::equals(const self: &list<T>, const lst: &slice<const T>) -> bool {
    return (self as slice<const T>).equals(lst);
}

/**
 * Compares a list against any T-slice-shaped value for element-by-element
 * equality. `U` is bounded by the DEPENDENT bound `slice<T>` -- the
 * container's own element type -- so `lst` may be another `list<T>`, a
 * `string` (against a `list<char>`), or a `slice<T>`: anything that
 * `extends slice<T>` binds `U` and needs no explicit borrow at the call
 * site. Both sides then borrow into the generic slice `equals`, so neither
 * is copied.
 *
 * @param self: list to compare
 * @param lst:  any value whose type extends `slice<T>` to compare against
 *
 * @return true if both sides have the same length and elements, false
 *         otherwise
 */
@inline
fn list<T>::equals<U extends slice<T>>(const self: &list<T>, const lst: &U) -> bool {
    return self.equals(lst as slice<const T>);
}

/**
 * Doubles the list's capacity, moving the existing elements.
 * Internal; called by `.push` when storage runs out.
 *
 * @param self: list to grow
 */
@private
fn list<T>::grow(self: &list<T>) {
    let new_capacity: uint64 = self.capacity * 2;
    if (new_capacity == 0)
        new_capacity = 1;
    self.data = resize(self.data, new_capacity);
    self.capacity = new_capacity;
}

/***************************************
 * Iteration
 ***************************************/

/**
 * Begins an iteration over an list, from the first element to the last. Part
 * of the `list_it`/`list_next` protocol (used by `for ... in`); pair it with
 * `list_next`.
 *
 * @param self: list to iterate
 *
 * @return an iterator positioned at the first element
 */
fn list_it<T>(@nonnull self: list<T>*) -> struct iterator<list<T>> {
    return struct iterator { obj = self, idx = 0 };
}

/**
 * Advances the iterator and writes the next element into out.
 *
 * @param it:  iterator to advance
 * @param out: location the next element is written to; untouched when the
 *             list is exhausted
 *
 * @return true if an element was produced, false once iteration is complete
 */
fn list_next<T>(it: struct iterator<list<T>>*, out: T*) -> bool {
    if (it!->idx >= it!->obj!->length)
        return false;

    *out! = it!->obj!->data![it!->idx];
    it!->idx += 1;
    return true;
}
