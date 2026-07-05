import "std";
import "stack";

// stack<T> -- a growable LIFO: push and pop at the top (libmc/stack.mc). Like
// the other lib containers it owns a heap buffer that doubles when it fills.

fn main() -> int32 {
    // The stack functions take const/mut receivers, so a local stack passes
    // directly: no & needed. (A stack<T>* still works via pointer decay; see
    // examples/functions/pointer_decay.mc.)
    let chars: struct stack<char>;
    stack_init(chars, 2);
    defer stack_destroy(chars);

    stack_push(chars, 'a');
    stack_push(chars, 'b');
    stack_push(chars, 'c');

    // Popping returns the most recently pushed element first.
    print("stack (LIFO): ");
    until (stack_is_empty(chars)) {
        print("%c ", stack_pop(chars));    // c b a
    }
    println("");

    return 0;
}

// See also: queues.mc for the FIFO counterpart, lists.mc for random access.
