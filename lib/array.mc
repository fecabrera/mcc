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
        _array_grow<T>(self);

    self->data[self->length] = value;
    self->length = self->length + 1;
}

/**
 * Doubles the array's capacity, moving the existing elements.
 * Internal; called by array_append when storage runs out.
 *
 * @param self: array to grow
 */
fn _array_grow<T>(self: struct array<T>*) {
    let new_capacity: uint64 = self->capacity * 2;
    if (new_capacity == 0)
        new_capacity = 1;
    let new_data = alloc<T>(new_capacity);

    copy_bytes(new_data, self->data, self->length);
    dealloc(self->data);

    self->data = new_data;
    self->capacity = new_capacity;
}
