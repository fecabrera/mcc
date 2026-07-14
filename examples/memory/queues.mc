import "std/io";
import "std/queue";

// queue<T> -- a linked-list FIFO: push links a node at the back, pop unlinks
// the front, both O(1), one heap node per queued value (lib/std/queue.mc).
// The array-backed FIFO this replaced lives on as ring<T> (lib/std/ring.mc).

fn main() -> int32 {
    // The queue methods take const/mut receivers, so a local queue passes
    // directly: no & needed. (A queue<T>* still works via pointer decay; see
    // examples/functions/pointer_decay.mc.) The ctor-sugar `let` auto-defers
    // queue<T>::destructor at scope end.
    let q = queue<int32>();

    let i: int32 = 1;
    while (i <= 5) {
        q.push(i);
        i += 1;
    }

    println(f"front {q.peek()}");

    // for-in walks front to back (oldest to newest) without consuming.
    print("queue (walk):  ");
    for v in &q {
        print(f"{v} ");                   // 1 2 3 4 5
    }
    println("");

    // Popping returns the oldest element first.
    print("queue (FIFO):  ");
    until (q.is_empty()) {
        print(f"{q.pop()} ");             // 1 2 3 4 5
    }
    println("");

    return 0;
}

// See also: stacks.mc for the LIFO counterpart, lists.mc for random access.
