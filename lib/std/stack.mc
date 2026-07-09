import "std/memory";

struct stack<T> {
    data: T*;         // heap-allocated element buffer
    top: uint64;      // index of the next free slot (== live element count)
    capacity: uint64; // total allocated slots
}

/**
 * Allocates the backing buffer and initializes an empty stack.
 *
 * @param self:     stack to initialize
 * @param capacity: initial slot count
 */
fn stack_init<T>(mut self: struct stack<T>, capacity: uint64) {
    self.data = alloc<T>(capacity);
    self.top = 0;
    self.capacity = capacity;
}

/**
 * Frees the backing buffer and zeroes the stack fields.
 *
 * @param self: stack to destroy
 */
fn stack_destroy<T>(mut self: struct stack<T>) {
    dealloc(self.data);

    self.data = null;
    self.top = 0;
    self.capacity = 0;
}

/**
 * Pushes value onto the top of the stack. Grows the backing buffer if full.
 *
 * @param self:  stack to push onto
 * @param value: value to push
 */
fn stack_push<T>(mut self: struct stack<T>, value: T) {
    if (self.top == self.capacity)
        stack_grow<T>(self);

    self.data![self.top] = value;
    self.top += 1;
}

/**
 * Removes and returns the top element. The caller must ensure the stack is
 * non-empty (self.top > 0); behavior is undefined on an empty stack.
 *
 * @param self: stack to pop from
 *
 * @return the popped value
 */
fn stack_pop<T>(mut self: struct stack<T>) -> T {
    self.top -= 1;
    return self.data![self.top];
}

/**
 * Returns the top element without removing it. The caller must ensure the
 * stack is non-empty (self.top > 0); behavior is undefined on an empty stack.
 *
 * @param self: stack to peek at
 *
 * @return the top value
 */
fn stack_peek<T>(const self: struct stack<T>) -> T {
    return self.data![self.top - 1];
}

/**
 * Returns the number of elements currently on the stack.
 *
 * @param self: stack to measure
 *
 * @return the live element count
 */
fn stack_len<T>(const self: struct stack<T>) -> uint64 {
    return self.top;
}

/**
 * Reports whether the stack holds no elements.
 *
 * @param self: stack to test
 *
 * @return true if the stack is empty, false otherwise
 */
fn stack_is_empty<T>(const self: struct stack<T>) -> bool {
    return self.top == 0;
}

/**
 * Doubles the backing buffer, preserving the elements. Internal; called by
 * stack_push when the stack is full.
 *
 * @param self: stack whose buffer to grow
 */
@private
fn stack_grow<T>(mut self: struct stack<T>) {
    let new_capacity: uint64 = self.capacity * 2;
    if (new_capacity == 0)
        new_capacity = 1;
    self.data = resize(self.data, new_capacity);
    self.capacity = new_capacity;
}
