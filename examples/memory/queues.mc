import "std";
import "queue";

// queue<T> -- a growable FIFO ring buffer: push at the back, pop from the front
// (libmc/queue.mc). The ring reuses freed slots, doubling only when it fills.

fn main() -> int32 {
    // The queue functions take const/mut receivers, so a local queue passes
    // directly: no & needed. (A queue<T>* still works via pointer decay; see
    // examples/functions/pointer_decay.mc.)
    let q: struct queue<int32>;
    queue_init(q, 2);
    defer queue_destroy(q);

    let i: int32 = 1;
    while (i <= 5) {
        queue_push(q, i);                  // grows past the initial capacity of 2
        i += 1;
    }

    println("len %llu, front %d", queue_len(q), queue_peek(q));

    // Popping returns the oldest element first.
    print("queue (FIFO): ");
    until (queue_is_empty(q)) {
        print("%d ", queue_pop(q));        // 1 2 3 4 5
    }
    println("");

    return 0;
}

// See also: stacks.mc for the LIFO counterpart, lists.mc for random access.
