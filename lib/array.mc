import "memory";

/**
 * A growable, heap-backed array of T.
 */
struct array<T> {
    data: T*;
    length: uint64;
    capacity: uint64;
}

/**
 * Prepares an array for use, allocating room for capacity elements.
 *
 * @param self:     array to initialize
 * @param capacity: initial number of elements to reserve space for
 */
fn array_init<T>(self: struct array<T>*, capacity: uint64) {
    self->data = alloc<T>(capacity);
    self->length = 0;
    self->capacity = capacity;
}

/**
 * Releases the array's storage. The array must be re-initialized with
 * array_init before being used again.
 *
 * @param self: array to destroy
 */
fn array_destroy<T>(self: struct array<T>*) {
    dealloc(self->data);

    self->data = null;
    self->length = 0;
    self->capacity = 0;
}

/**
 * Empties the array without releasing its storage.
 *
 * @param self: array to reset
 */
fn array_reset<T>(self: struct array<T>*) {
    self->length = 0;
}

/**
 * Reads the element at index into out.
 *
 * @param self:  array to read from
 * @param index: zero-based index; must be < self->length
 * @param out:   location the element is written to
 *
 * @return true on success, false if index is out of bounds
 */
fn array_get<T>(self: struct array<T>*, index: uint64, out: T*) -> bool {
    if (index >= self->length)
        return false;

    *out = self->data[index];
    return true;
}

/**
 * Overwrites the element at index with value.
 *
 * @param self:  array to write into
 * @param index: zero-based index; must be < self->length
 * @param value: value to store
 *
 * @return true on success, false if index is out of bounds
 */
fn array_set<T>(self: struct array<T>*, index: uint64, value: T) -> bool {
    if (index >= self->length)
        return false;

    self->data[index] = value;
    return true;
}

/**
 * Appends value to the end of the array, growing its storage if needed.
 *
 * @param self:  array to append to
 * @param value: value to append
 */
fn array_append<T>(self: struct array<T>*, value: T) {
    if (self->length == self->capacity)
        array_grow<T>(self);

    self->data[self->length] = value;
    self->length = self->length + 1;
}

/**
 * Doubles the array's capacity, moving the existing elements.
 * Internal; called by array_append when storage runs out.
 *
 * @param self: array to grow
 */
@private
fn array_grow<T>(self: struct array<T>*) {
    let new_capacity: uint64 = self->capacity * 2;
    if (new_capacity == 0)
        new_capacity = 1;
    self->data = resize(self->data, new_capacity);
    self->capacity = new_capacity;
}

/***************************************
 * Iteration
 ***************************************/

/**
 * A forward cursor over an array's elements, produced by `array_it`. It borrows
 * the array (does not copy it), so the array must outlive the iterator and
 * must not be resized while iterating.
 */
struct array_iter<T> {
    obj: struct array<T>*;   // the array being walked
    idx: uint64;             // index of the next element to yield
}

/**
 * Begins an iteration over an array, from the first element to the last. Part
 * of the `array_it`/`array_next` protocol (used by `for ... in`); pair it with
 * `array_next`.
 *
 * @param self: array to iterate
 *
 * @return an iterator positioned at the first element
 */
fn array_it<T>(self: struct array<T>*) -> struct array_iter<T> {
    let it: struct array_iter<T>;
    it.obj = self;
    it.idx = 0;
    return it;
}

/**
 * Advances the iterator and writes the next element into out.
 *
 * @param it:  iterator to advance
 * @param out: location the next element is written to; untouched when the
 *             array is exhausted
 *
 * @return true if an element was produced, false once iteration is complete
 */
fn array_next<T>(it: struct array_iter<T>*, out: T*) -> bool {
    if (it->idx >= it->obj->length)
        return false;

    *out = it->obj->data[it->idx];
    it->idx = it->idx + 1;
    return true;
}
