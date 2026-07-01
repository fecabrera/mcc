import "std";
import "list";
import "stack";
import "queue";

// A tour of the growable container types in libmc/. Each owns a heap buffer that
// doubles when it fills, so they all start small and grow as needed.

fn main() -> int32 {
    // list<T> -- a growable random-access sequence (libmc/list.mc).
    let nums: struct list<int32>;
    list_init(&nums, 2);

    let i: int32 = 0;
    while (i < 10) {
        list_push(&nums, i * i);      // one element; grows past the capacity of 2
        i += 1;
    }
    let value: int32 = 0;
    if (list_get(&nums, 6, &value))
        println("list: length %llu, nums[6] = %d", nums.length, value);

    // list_from_array builds an owned list by copying a raw array; list_append
    // concatenates a whole list; list_duplicate makes an independent deep copy.
    let seed: int32[3];
    seed[0] = 100; seed[1] = 200; seed[2] = 300;
    
    let more: struct list<int32>;
    list_from_array(&more, &seed[0], 3);   // more = [100, 200, 300]
    list_append(&nums, &more);             // append the whole list onto nums
    
    let copy: struct list<int32>;
    list_duplicate(&copy, &nums);          // independent deep copy
    println("after append: nums.length %llu, copy.length %llu",
            nums.length, copy.length);
    
    list_destroy(&more);
    list_destroy(&copy);
    list_destroy(&nums);

    // stack<T> -- LIFO: push and pop at the top (libmc/stack.mc).
    let chars: struct stack<char>;
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
    // (libmc/queue.mc).
    let q: struct queue<int32>;
    queue_init(&q, 2);
    i = 1;
    while (i <= 5) {
        queue_push(&q, i);               // grows past the initial capacity of 2
        i += 1;
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
