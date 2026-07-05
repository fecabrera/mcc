import "memory";

// Singly-linked node holding one queued value.
struct queue_node<T> {
    value: T;
    next: queue_node<T>* = null;
}

// Linked-list FIFO queue<T>. Values enqueue at the tail and dequeue at the
// head, both in O(1). Nodes are heap-allocated per push.
struct queue<T> {
    head: queue_node<T>*;   // front: next to dequeue (oldest)
    tail: queue_node<T>*;   // back: last enqueued (newest)
}

// Cursor over a queue, from the front (oldest) to the back (newest).
struct queue_iterator<T> {
    current: queue_node<T>*; // next node to yield
}

/**
 * Initialises an empty queue.
 *
 * @param self: queue to initialise
 */
fn queue_init<T>(mut self: queue<T>) {
    self.head = null;
    self.tail = null;
}

/**
 * Pops and frees every remaining node, leaving the queue empty.
 * Queued values themselves are not released.
 *
 * @param self: queue to destroy
 */
fn queue_destroy<T>(mut self: queue<T>) {
    until (queue_is_empty<T>(self))
        queue_pop<T>(self);
    
    self.head = null;
    self.tail = null;
}

/**
 * Enqueues value in O(1) by appending a new node at the tail.
 *
 * @param self:  queue to push onto
 * @param value: value to enqueue
 */
fn queue_push<T>(mut self: queue<T>, value: T) {
    let node = {
        let tmp = new<queue_node<T>>();
        *tmp = queue_node<T> { value = value };
        emit tmp;
    };
    
    if (self.tail == null) {
        self.head = node;
    } else {
        self.tail->next = node;
    }
    
    self.tail = node;
}

/**
 * Dequeues the front (oldest) value in O(1), unlinking and freeing its node.
 * The caller must ensure the queue is non-empty (!queue_is_empty(q));
 * behaviour is undefined on an empty queue.
 *
 * @param self: queue to pop from
 * @return the dequeued value
 */
fn queue_pop<T>(mut self: queue<T>) -> T {
    let node = self.head;
    self.head = node->next;
    
    if (self.head == null)
        self.tail = null;
    
    let value = node->value;
    dealloc(node);

    return value;
}

/**
 * Returns the front (oldest) value without removing it. The caller must
 * ensure the queue is non-empty (!queue_is_empty(q)); behaviour is undefined
 * on an empty queue.
 *
 * @param self: queue to peek at
 * @return the front value
 */
fn queue_peek<T>(const self: queue<T>) -> T {
    return self.head->value;
}

/**
 * Reports whether the queue holds no values.
 *
 * @param self: queue to inspect
 * @return true if the queue holds no values
 */
fn queue_is_empty<T>(const self: queue<T>) -> bool {
    return self.head == null;
}

/**
 * Creates an iterator positioned at the front (oldest value).
 *
 * @param self: queue to iterate
 * @return iterator yielding values from oldest to newest (FIFO order)
 */
fn queue_it<T>(self: queue<T>*) -> queue_iterator<T> {
    return queue_iterator<T> { current = self->head };
}

/**
 * Advances the iterator, writing the current value to out.
 *
 * @param it:  iterator to advance
 * @param out: receives the next value when available
 * @return true if a value was produced, false when iteration is done
 */
fn queue_next<T>(it: queue_iterator<T>*, out: T*) -> bool {
    if (it->current == null) return false;
    *out = it->current->value;
    it->current = it->current->next;
    return true;
}
