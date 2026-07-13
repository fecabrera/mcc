import "std/io";
import "std/list";

// list<T> -- a growable random-access sequence (lib/std/list.mc). It owns a heap
// buffer that doubles when it fills, so it starts small and grows as needed.

fn main() -> int32 {
    // The list methods take const/mut receivers, so a local list passes
    // directly: no & needed. (A list<T>* still works via pointer decay; see
    // examples/functions/pointer_decay.mc.)
    let nums = list<int32>(2);             // initial capacity of 2; the ctor-sugar
                                           // `let` auto-defers list::destructor here

    let i: int32 = 0;
    while (i < 10) {
        nums.push(i * i);                  // grows past the capacity of 2
        i += 1;
    }

    // get writes the element through a `mut` parameter -- no & at the call.
    let value: int32 = 0;
    if (nums.get(6, value))
        println("length {}, nums[6] = {}", nums.length, value);

    // The list<T> constructor is overloaded on its source: a (T*, n) pair
    // copies a raw array, and a const slice<T> copies any borrowed run -- so a
    // source list borrows in with `as` (its slice prefix). append mirrors the
    // same two overloads.
    let seed: int32[3];
    seed[0] = 100; seed[1] = 200; seed[2] = 300;

    let more = list<int32>(&seed[0], 3);   // more = [100, 200, 300]
    nums.append(more as slice<int32>);     // append the whole list onto nums

    let copy = list<int32>(nums as slice<int32>);   // independent deep copy
    println("after append: nums.length {}, copy.length {}",
            nums.length, copy.length);

    return 0;
}

// See also: stacks.mc and queues.mc for the other growable containers;
// control-flow/iteration.mc to walk a list with `for x in`;
// nonnull_heap_buffers.mc for the proof idioms a heap pointer needs when it
// crosses the stdlib's @nonnull contracts (the list<T> constructor's
// raw-array source is one of them).
