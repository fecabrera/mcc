import "std/io";
import "std/ring";

// ring<T> -- an array-backed FIFO ring buffer: one contiguous heap buffer
// where push writes at the back, pop advances the head, and freed slots are
// reused as the indices wrap around (lib/std/ring.mc). It doubles when full.
// For the linked-list FIFO with one node per value, see queues.mc.

fn main() -> int32 {
    // The ring methods take const/reference receivers, so a local ring passes
    // directly: no & needed. (A ring<T>* still works via pointer decay; see
    // examples/functions/pointer_decay.mc.) The ctor-sugar `let` auto-defers
    // ring<T>::destructor at scope end.
    let r = ring<int32>(4);                // 4 slots: [_ _ _ _]

    // Fill every slot.
    let v: int32 = 1;
    while (v <= 4) {
        r.push(v);                         // [1 2 3 4], head at slot 0
        v += 1;
    }

    println(f"pop {r.pop()}");            // pop 1
    println(f"pop {r.pop()}");            // pop 2 -- head is now at slot 2

    // Pushing again reuses the two freed slots: the buffer physically holds
    // [5 6 3 4], but logically the ring still reads 3 4 5 6 front to back.
    r.push(5);
    r.push(6);

    println(f"front {r.peek()}");         // front 3

    // .at indexes logically from the front (index 0), wrap and all; .length
    // is the live element count.
    print("ring (wrapped): ");
    for i in range(r.length) {
        print(f"{r.at(i)} ");             // 3 4 5 6
    }
    println("");

    // The ring is full again, so this push doubles the buffer to 8 slots,
    // re-laying the wrapped elements in logical order from slot 0.
    r.push(7);

    print("ring (grown):   ");
    for i in range(r.length) {
        print(f"{r.at(i)} ");             // 3 4 5 6 7
    }
    println("");

    // Draining pops in arrival order, like any FIFO.
    print("ring (FIFO):    ");
    until (r.is_empty()) {
        print(f"{r.pop()} ");             // 3 4 5 6 7
    }
    println("");

    return 0;
}

// See also: queues.mc for the linked-list FIFO, lists.mc for random access.
