import "std";
import "list";

// A tour of slice<T>: a builtin, non-owning view { ptr: T*; length: uint64 }
// over a contiguous run of T. A slice borrows storage it does not own -- it
// never allocates -- so the thing it views must outlive it.

// A slice is a normal value, so it passes to functions by value (two words: a
// pointer and a length). One function works over any borrowed run, whether the
// elements came from a fixed array or a growable list.
fn sum(xs: slice<int32>) -> int32 {
    let total: int32 = 0;
    for x in xs {                 // slices iterate natively -- no _it/_next
        total = total + x;
    }
    return total;
}

// Indexing a slice writes straight through to the borrowed storage.
fn double_all(xs: slice<int32>) {
    let i: uint64 = 0;
    while (i < xs.length) {        // .length is the runtime element count
        xs[i] = xs[i] * 2;
        i = i + 1;
    }
}

fn main() -> int32 {
    // Borrow a fixed array T[N]: `as slice<T>` reads {&arr[0], N}.
    let arr: int32[4];
    arr[0] = 1; arr[1] = 2; arr[2] = 3; arr[3] = 4;
    
    let view = arr as slice<int32>;
    println("array view: length %llu, first %d, last %d",
            view.length, view[0], view[view.length - 1]);
    println("sum %d", sum(view));        // 10

    // The slice borrows the array, so writes through it are visible in arr.
    double_all(view);
    println("after double_all: arr[0] %d, arr[3] %d", arr[0], arr[3]);  // 2, 8

    // Borrow an owned list<T>: `as slice<T>` reads {data, length} and drops
    // the list's capacity. The slice tracks the elements, not the list object.
    let nums: struct list<int32>;
    list_init(&nums, 2);
    let n: int32 = 1;
    while (n <= 5) { list_push(&nums, n * n); n = n + 1; }   // 1 4 9 16 25

    let s = nums as slice<int32>;
    print("list view: ");
    for v in s {                          // 1 4 9 16 25
        print("%d ", v);
    }
    println("");
    println("sum %d (length %llu)", sum(s), s.length);       // 55

    list_destroy(&nums);
    return 0;
}
