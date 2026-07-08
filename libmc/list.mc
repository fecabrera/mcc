import "memory";

/**
 * A growable, heap-backed list of T.
 */
struct list<T> extends slice<T> {
    capacity: uint64;
}

/**
 * Prepares an list for use, allocating room for capacity elements.
 *
 * @param self:     list to initialize
 * @param capacity: initial number of elements to reserve space for
 */
fn list_init<T>(mut self: list<T>, capacity: uint64) {
    self.data = alloc<T>(capacity);
    self.length = 0;
    self.capacity = capacity;
}

/**
 * Deep-copies src into a fresh list: initializes dst to src's length and
 * appends every element, so the two share no storage afterward. dst must be
 * uninitialized (or already destroyed) -- duplicating into a live list leaks
 * its buffer.
 *
 * @param dst: uninitialized list to copy src into
 * @param src: elements to copy from -- any borrowed run, so a source list
 *             (`a as slice<T>`) or an array's borrow both work
 */
fn list_init<T>(mut dst: list<T>, const src: slice<T>) {
    list_init(dst, src.length);
    list_append(dst, src);
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
fn list_init<T>(mut self: list<T>, @nonnull arr: T*, n: uint64) {
    list_init(self, n);
    list_append(self, arr, n);
}

/**
 * Releases the list's storage. The list must be re-initialized with
 * list_init before being used again.
 *
 * @param self: list to destroy
 */
fn list_destroy<T>(mut self: list<T>) {
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
fn list_reset<T>(mut self: list<T>) {
    self.length = 0;
}

/**
 * Reports whether index is in bounds — whether list_at is defined for it.
 *
 * @param self:  list to test against
 * @param index: zero-based index
 *
 * @return true if index < self.length
 */
fn list_has<T>(const self: list<T>, index: uint64) -> bool {
    return index < self.length;
}

/**
 * Unchecked mutable access: returns the element at index as an lvalue, so
 * `list_at(xs, i) = v` writes in place and `let x = list_at(xs, i)` copies
 * out. Undefined if index is out of bounds — guard with list_has, or use
 * list_get for the checked read. The lvalue points into the list's storage:
 * consume it before any call that can grow the list.
 *
 * @param self:  list to access
 * @param index: zero-based index; must be < self.length
 *
 * @return the element at index, as an assignable lvalue
 */
fn list_at<T>(mut self: list<T>, index: uint64) -> mut T {
    return self.data[index];
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
fn list_get<T>(const self: list<T>, index: uint64, mut out: T) -> bool {
    if (!list_has(self, index))
        return false;

    out = self.data[index];
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
fn list_set<T>(mut self: list<T>, index: uint64, value: T) -> bool {
    if (!list_has(self, index))
        return false;

    self.data[index] = value;
    return true;
}

/**
 * Inserts value to the end of the list, growing its storage if needed.
 *
 * @param self:  list to append to
 * @param value: value to append
 */
fn list_push<T>(mut self: list<T>, value: T) {
    if (self.length == self.capacity)
        list_grow<T>(self);

    self.data[self.length] = value;
    self.length += 1;
}

/**
 * Appends a run of elements to the end of the list, growing it if needed.
 *
 * @param self:  list to append to
 * @param items: elements to append -- any borrowed run, so a source list
 *               (`b as slice<T>`) or an array's borrow both work
 **/
fn list_append<T>(mut self: list<T>, const items: slice<T>) {
    for value in items {
        list_push(self, value);
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
fn list_append<T>(mut self: list<T>, @nonnull items: T*, n: uint64) {
    for i in range(n) {
        list_push(self, items[i]);
    }
}

/**
 * Doubles the list's capacity, moving the existing elements.
 * Internal; called by list_push when storage runs out.
 *
 * @param self: list to grow
 */
@private
fn list_grow<T>(mut self: list<T>) {
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
fn list_it<T>(self: list<T>*) -> struct iterator<list<T>> {
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
    if (it->idx >= it->obj->length)
        return false;

    *out = it->obj->data[it->idx];
    it->idx += 1;
    return true;
}
