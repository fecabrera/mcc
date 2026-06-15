import "std";
import "array";
import "stack";
import "queue";

// A tour of the growable container types in lib/. Each owns a heap buffer that
// doubles when it fills, so they all start small and grow as needed.

fn main() -> int32 {
    // array<T> -- a growable random-access sequence (lib/array.mc).
    let nums: struct array<int32>;
    array_init(&nums, 2);
    let i: int32 = 0;
    while (i < 10) {
        array_append(&nums, i * i);      // grows past the initial capacity of 2
        i = i + 1;
    }
    let value: int32 = 0;
    if (array_get(&nums, 6, &value))
        println("array: length %llu, nums[6] = %d", nums.length, value);
    array_destroy(&nums);

    // stack<T> -- LIFO: push and pop at the top (lib/stack.mc).
    let chars: struct stack<uint8>;
    stack_init(&chars, 2);
    stack_push(&chars, 'a');
    stack_push(&chars, 'b');
    stack_push(&chars, 'c');
    print("stack (LIFO): ");
    until (stack_is_empty(&chars)) {
        print("%c ", stack_pop(&chars));  // c b a
    }
    println("");
    stack_destroy(&chars);

    // queue<T> -- FIFO ring buffer: push at the back, pop from the front
    // (lib/queue.mc).
    let q: struct queue<int32>;
    queue_init(&q, 2);
    i = 1;
    while (i <= 5) {
        queue_push(&q, i);               // grows past the initial capacity of 2
        i = i + 1;
    }
    println("queue: len %llu, front %d", queue_len(&q), queue_peek(&q));
    print("queue (FIFO): ");
    until (queue_is_empty(&q)) {
        print("%d ", queue_pop(&q));      // 1 2 3 4 5
    }
    println("");
    queue_destroy(&q);

    return 0;
}
