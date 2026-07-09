import "memory";

// Dynamic ring-buffer FIFO ring<T>. Grows automatically when full; never
// shrinks.
struct ring<T> {
    data: T*;         // heap-allocated element buffer
    head: uint64;     // index of the front element
    length: uint64;   // number of live elements
    capacity: uint64; // total allocated slots
}

/**
 * Allocates the backing buffer and initialises an empty ring.
 *
 * @param self:     ring to initialise
 * @param capacity: initial slot count
 */
fn ring_init<T>(mut self: ring<T>, capacity: uint64) {
    self.data = alloc<T>(capacity);
    self.head = 0;
    self.length = 0;
    self.capacity = capacity;
}

/**
 * Frees the backing buffer and zeroes the ring fields.
 *
 * @param self: ring to destroy
 */
fn ring_destroy<T>(mut self: ring<T>) {
    dealloc(self.data);

    self.data = null;
    self.head = 0;
    self.length = 0;
    self.capacity = 0;
}

/**
 * Appends value to the back of the ring. Grows the backing buffer if full.
 *
 * @param self:  ring to push onto
 * @param value: value to append
 */
fn ring_push<T>(mut self: ring<T>, value: T) {
    if (self.length == self.capacity)
        ring_grow(self);

    let pos = (self.head + self.length) % (self.capacity);

    self.length += 1;
    self.data![pos] = value;
}

/**
 * Removes and returns the front element. The caller must ensure the ring is
 * non-empty (self.length > 0); behaviour is undefined on an empty ring.
 *
 * @param self: ring to pop from
 *
 * @return the popped value
 */
fn ring_pop<T>(mut self: ring<T>) -> T {
    let pos = self.head;

    self.head = (self.head + 1) % self.capacity;
    self.length -= 1;

    return self.data![pos];
}

/**
 * Reports whether a logical index is in bounds — whether ring_at is defined
 * for it; index 0 is the front.
 *
 * @param self:  ring to test against
 * @param index: logical position from the front
 *
 * @return true if index < self.length
 */
fn ring_has<T>(const self: ring<T>, index: uint64) -> bool {
    return index < self.length;
}

/**
 * Unchecked mutable access at a logical index; index 0 is the front. Returns
 * the element as an lvalue, so `ring_at(r, 0) = v` writes in place and
 * `let x = ring_at(r, 0)` copies out. Undefined if index is out of bounds —
 * guard with ring_has. The lvalue points into the ring's storage: consume it
 * before any call that can grow the ring.
 *
 * @param self:  ring to access
 * @param index: logical position from the front; must be < self.length
 *
 * @return the element at that position, as an assignable lvalue
 */
fn ring_at<T>(mut self: ring<T>, index: uint64) -> mut T {
    let pos = (self.head + index) % (self.capacity);
    return self.data![pos];
}

/**
 * Returns the front element without removing it. The caller must ensure the
 * ring is non-empty (self.length > 0); behaviour is undefined on an empty
 * ring.
 *
 * @param self: ring to peek at
 *
 * @return the front value
 */
fn ring_peek<T>(const self: ring<T>) -> T {
    return self.data![self.head];
}

/**
 * Returns the number of elements currently on the ring.
 *
 * @param self: ring to measure
 *
 * @return the live element count
 */
fn ring_len<T>(const self: ring<T>) -> uint64 {
    return self.length;
}

/**
 * Reports whether the ring holds no elements.
 *
 * @param self: ring to test
 *
 * @return true if the ring is empty, false otherwise
 */
fn ring_is_empty<T>(const self: ring<T>) -> bool {
    return self.length == 0;
}

/**
 * Doubles the backing buffer and re-lays the elements in logical order from
 * index 0, resetting head. Internal; called by ring_push when the ring is
 * full.
 *
 * @param self: ring whose buffer to grow
 */
@private
fn ring_grow<T>(mut self: ring<T>) {
    let new_capacity: uint64 = self.capacity * 2;
    if (new_capacity == 0)
        new_capacity = 1;

    let new_data: T* = alloc<T>(new_capacity);

    for i in range(self.length) {
        new_data![i] = self.data![(self.head + i) % self.capacity];
    }

    dealloc(self.data);

    self.data = new_data;
    self.head = 0;
    self.capacity = new_capacity;
}
