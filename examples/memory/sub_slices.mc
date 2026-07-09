import "std/io";

// Sub-slicing: s[start:end] on a slice yields a NEW slice viewing the same
// storage -- { &s.data[start], end - start }. No allocation, no element copy:
// it is the same borrow, narrowed.
//
// Prerequisites: the slice<T> view itself (memory/slices.mc) and array
// literals borrowing into slices (memory/slice_literals.mc).
// See also: memory/slices.mc, memory/slice_literals.mc.

// The usual read-only consumer. A sub-slice is a plain slice value, so it
// passes here like any other. The result type is the receiver's verbatim:
// a sub-slice of slice<const T> is slice<const T> -- narrowing a read-only
// view never opens a new write path.
fn sum(xs: slice<const int32>) -> int32 {
    let total: int32 = 0;
    for x in xs {
        total += x;
    }
    return total;
}

fn main() -> int32 {
    let nums: slice<int32> = [10, 20, 30, 40, 50];

    // All four forms. Either bound may be omitted: start defaults to 0 and
    // end to nums.length, so nums[:] is a plain copy of the view.
    let mid  = nums[1:4];   // { &nums.data[1], 3 } -- 20 30 40
    let tail = nums[2:];    // 30 40 50
    let head = nums[:2];    // 10 20
    let all  = nums[:];     // 10 20 30 40 50
    println("mid:  length %llu, sum %d", mid.length, sum(mid));     // 3, 90
    println("tail: length %llu, sum %d", tail.length, sum(tail));   // 3, 120
    println("head: length %llu, sum %d", head.length, sum(head));   // 2, 30
    println("all:  length %llu, sum %d", all.length, sum(all));     // 5, 150

    // Same storage: writing an element through the sub-slice lands where the
    // parent view sees it. (The sub-slice expression itself is an rvalue:
    // mid[0] = ... writes fine, but nums[1:4] = ..., the compound forms, and
    // &nums[1:4] are all compile errors.)
    mid[0] = 99;
    println("after mid[0] = 99: nums[1] is %d", nums[1]);           // 99

    // Sub-slices chain -- each step narrows the previous view...
    println("nums[1:][1:] starts at %d", nums[1:][1:][0]);          // 30

    // ...and iterate directly, no binding needed.
    print("nums[3:]:");
    for x in nums[3:] {
        print(" %d", x);                                            // 40 50
    }
    println("");

    // Bounds have index parity: any integer type is accepted, each widened
    // by its own signedness, so an int32 start mixes freely with the
    // defaulted uint64 end. And like plain indexing, bounds are UNCHECKED:
    // nothing validates start <= end <= length, so an out-of-range pair is
    // undefined behavior, exactly like an out-of-range nums[i].
    let start: int32 = 1;
    println("int32 start: sum %d", sum(nums[start:]));              // 219

    // nums[n:n] is a defined empty slice, { &nums.data[n], 0 }: the
    // one-past pointer is formed but never dereferenced, and deliberately
    // NOT normalized to the empty literal's { null, 0 }.
    let none = nums[2:2];
    println("empty: length %llu, sum %d", none.length, sum(none));  // 0, 0

    // Receivers are slice-typed expressions ONLY. An array, list, or string
    // literal reaches sub-slicing through its existing borrow spelling; a
    // bare arr[1:] is a compile error suggesting exactly that.
    let arr: int32[4];
    arr[0] = 1; arr[1] = 2; arr[2] = 3; arr[3] = 4;
    println("borrowed array tail: sum %d", sum((arr as slice<int32>)[1:])); // 9

    print("string slice: ");
    for c in ("hello" as slice<char>)[1:3] {
        print("%c", c);                                             // el
    }
    println("");

    // A full expression parses before the slice ':' is considered, so a
    // ternary start binds its own ':' greedily: this is
    // start = (flag ? 1 : 2), end = 4. (No negative indices and no step:
    // nums[::2] is a parse error, '::' lexes as one token.)
    let flag = true;
    println("ternary start: sum %d", sum(nums[flag ? 1 : 2 : 4]));  // 169

    return 0;
}
