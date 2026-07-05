import "std";
import "queue";

// queue<T> -- a linked-list FIFO: push links a node at the back, pop unlinks
// the front, both O(1), one heap node per queued value (libmc/queue.mc).
// The array-backed FIFO this replaced lives on as ring<T> (libmc/ring.mc).

fn main() -> int32 {
    // The queue functions take const/mut receivers, so a local queue passes
    // directly: no & needed. (A queue<T>* still works via pointer decay; see
    // examples/functions/pointer_decay.mc.)
    let q: struct queue<int32>;
    queue_init(q);
    defer queue_destroy(q);

    let i: int32 = 1;
    while (i <= 5) {
        queue_push(q, i);
        i += 1;
    }

    println("front %d", queue_peek(q));

    // for-in walks front to back (oldest to newest) without consuming.
    print("queue (walk):  ");
    for v in &q {
        print("%d ", v);                   // 1 2 3 4 5
    }
    println("");

    // Popping returns the oldest element first.
    print("queue (FIFO):  ");
    until (queue_is_empty(q)) {
        print("%d ", queue_pop(q));        // 1 2 3 4 5
    }
    println("");

    return 0;
}

// See also: stacks.mc for the LIFO counterpart, lists.mc for random access.
