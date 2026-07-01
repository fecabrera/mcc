import "memory";

// Dynamic ring-buffer FIFO queue<T>. Grows automatically when full; never
// shrinks.
struct queue<T> {
    data: T*;         // heap-allocated element buffer
    head: uint64;     // index of the front element
    length: uint64;   // number of live elements
    capacity: uint64; // total allocated slots
}

/**
 * Allocates the backing buffer and initialises an empty queue.
 *
 * @param self:     queue to initialise
 * @param capacity: initial slot count
 */
fn queue_init<T>(self: struct queue<T>*, capacity: uint64) {
    self->data = alloc<T>(capacity);
    self->head = 0;
    self->length = 0;
    self->capacity = capacity;
}

/**
 * Frees the backing buffer and zeroes the queue fields.
 *
 * @param self: queue to destroy
 */
fn queue_destroy<T>(self: struct queue<T>*) {
    dealloc(self->data);

    self->data = null;
    self->head = 0;
    self->length = 0;
    self->capacity = 0;
}

/**
 * Appends value to the back of the queue. Grows the backing buffer if full.
 *
 * @param self:  queue to push onto
 * @param value: value to enqueue
 */
fn queue_push<T>(self: struct queue<T>*, value: T) {
    if (self->length == self->capacity)
        queue_grow(self);

    let pos = (self->head + self->length) % (self->capacity);

    self->length = self->length + 1;
    self->data[pos] = value;
}

/**
 * Removes and returns the front element. The caller must ensure the queue is
 * non-empty (self->length > 0); behaviour is undefined on an empty queue.
 *
 * @param self: queue to pop from
 *
 * @return the dequeued value
 */
fn queue_pop<T>(self: struct queue<T>*) -> T {
    let pos = self->head;

    self->head = (self->head + 1) % self->capacity;
    self->length = self->length - 1;

    return self->data[pos];
}

/**
 * Returns the element at a logical index without removing it; index 0 is the
 * front. The caller must ensure index < self->length; behaviour is undefined
 * otherwise.
 *
 * @param self:  queue to index into
 * @param index: logical position from the front
 *
 * @return the value at that position
 */
fn queue_at<T>(self: struct queue<T>*, index: uint64) -> T {
    let pos = (self->head + index) % (self->capacity);
    return self->data[pos];
}

/**
 * Returns the front element without removing it. The caller must ensure the
 * queue is non-empty (self->length > 0); behaviour is undefined on an empty
 * queue.
 *
 * @param self: queue to peek at
 *
 * @return the front value
 */
fn queue_peek<T>(self: struct queue<T>*) -> T {
    return self->data[self->head];
}

/**
 * Returns the number of elements currently on the queue.
 *
 * @param self: queue to measure
 *
 * @return the live element count
 */
fn queue_len<T>(self: struct queue<T>*) -> uint64 {
    return self->length;
}

/**
 * Reports whether the queue holds no elements.
 *
 * @param self: queue to test
 *
 * @return true if the queue is empty, false otherwise
 */
fn queue_is_empty<T>(self: struct queue<T>*) -> bool {
    return self->length == 0;
}

/**
 * Doubles the backing buffer and re-lays the elements in logical order from
 * index 0, resetting head. Internal; called by queue_push when the queue is
 * full.
 *
 * @param self: queue whose buffer to grow
 */
@private
fn queue_grow<T>(self: struct queue<T>*) {
    let new_capacity: uint64 = self->capacity * 2;
    if (new_capacity == 0)
        new_capacity = 1;

    let new_data: T* = alloc<T>(new_capacity);

    for i in range(self->length) {
        new_data[i] = self->data[(self->head + i) % self->capacity];
    }

    dealloc(self->data);

    self->data = new_data;
    self->head = 0;
    self->capacity = new_capacity;
}