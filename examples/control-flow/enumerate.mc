import "std/io";
import "std/list";

// `enumerate(obj)` is a builtin adapter, like `range`: it runs obj's ordinary
// iteration while counting, yielding a builtin `enumerated<T> { index; value }`
// per element -- the position as a uint64 and the element itself. No import is
// needed to name it.

fn main() -> int32 {
    let nums = list<int32>(4);           // ctor-sugar `let`: auto-defers cleanup

    let i: int32 = 1;
    while (i <= 5) {
        nums.push(i * i);                // 1 4 9 16 25
        i += 1;
    }

    // index and value come paired; break/continue work as in any loop.
    for e in enumerate(&nums) {
        if (e.index == 3) { break; }     // just the first three
        println("nums[{}] = {}", e.index, e.value);
    }

    // As with a bare `for x in`, the & is optional: a struct value is borrowed
    // automatically, so enumerate(nums) iterates a snapshot with no dereference.
    for e in enumerate(nums) {
        if (e.index > 0) { break; }
        println("first square (by value): {}", e.value);
    }

    return 0;
}

// See also: iteration.mc for the bare `for x in` protocol enumerate builds on,
// ranges.mc for counting without a container.
