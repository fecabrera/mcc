import "std/io";
import "std/stack";

// stack<T> -- a growable LIFO: push and pop at the top (lib/std/stack.mc). Like
// the other lib containers it owns a heap buffer that doubles when it fills.

fn main() -> int32 {
    // The stack methods take const/reference receivers, so a local stack passes
    // directly: no & needed. (A stack<T>* still works via pointer decay; see
    // examples/functions/pointer_decay.mc.) The ctor-sugar `let` auto-defers
    // stack<T>::destructor at scope end.
    let chars = stack<char>(2);

    chars.push('a');
    chars.push('b');
    chars.push('c');

    // Popping returns the most recently pushed element first.
    print("stack (LIFO): ");
    until (chars.is_empty()) {
        print(f"{chars.pop()} ");    // c b a
    }
    println("");

    return 0;
}

// See also: queues.mc for the FIFO counterpart, lists.mc for random access.
