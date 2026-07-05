import "std";
import "list";

// list<T> -- a growable random-access sequence (libmc/list.mc). It owns a heap
// buffer that doubles when it fills, so it starts small and grows as needed.

fn main() -> int32 {
    // The list functions take const/mut receivers, so a local list passes
    // directly: no & needed. (A list<T>* still works via pointer decay; see
    // examples/functions/pointer_decay.mc.)
    let nums: struct list<int32>;
    list_init(nums, 2);                    // initial capacity of 2
    defer list_destroy(nums);

    let i: int32 = 0;
    while (i < 10) {
        list_push(nums, i * i);            // grows past the capacity of 2
        i += 1;
    }

    // list_get writes the element through a `mut` parameter -- no & at the call.
    let value: int32 = 0;
    if (list_get(nums, 6, value))
        println("length %llu, nums[6] = %d", nums.length, value);

    // list_from_array builds an owned list by copying a raw array; list_append
    // concatenates a whole list (its source is a const receiver, so it passes
    // the same way); list_duplicate makes an independent deep copy.
    let seed: int32[3];
    seed[0] = 100; seed[1] = 200; seed[2] = 300;

    let more: struct list<int32>;
    list_from_array(more, &seed[0], 3);    // more = [100, 200, 300]
    defer list_destroy(more);
    list_append(nums, more);               // append the whole list onto nums

    let copy: struct list<int32>;
    list_duplicate(copy, nums);            // independent deep copy
    defer list_destroy(copy);
    println("after append: nums.length %llu, copy.length %llu",
            nums.length, copy.length);

    return 0;
}

// See also: stacks.mc and queues.mc for the other growable containers;
// control-flow/iteration.mc to walk a list with `for x in`;
// nonnull_heap_buffers.mc for the proof idioms a heap pointer needs when it
// crosses the stdlib's @nonnull contracts (list_from_array's raw-array
// source is one of them).
