import "std/io";

// Array literals adapting to slice<T>: a literal can be borrowed into a slice
// directly, with no named array in sight. The literal materializes a hidden
// backing array in the enclosing function's frame (alive for the whole call)
// and the slice views it: [1, 2, 3] as slice<int32> is { &backing[0], 3 }.
//
// Prerequisites: array literals (types/arrays.mc) and the slice<T> view
// itself (memory/slices.mc). String literals adapt to slices the same way;
// see types/string_tables.mc for that side.
// See also: memory/sub_slices.mc -- s[start:end] narrows any slice,
// including one borrowed from a literal, into a view over the same storage.
// See also: memory/slice_assignment.mc -- a string literal reborrows into a
// char-slice lvalue by assignment; an array-literal assignment is rejected
// (its frame-local backing would dangle past a longer-lived target).

// One consumer for the whole tour. It takes slice<const int32>, so mutable
// views pass too (they widen implicitly, as slices.mc shows).
fn sum(xs: slice<const int32>) -> int32 {
    let total: int32 = 0;
    for x in xs {
        total += x;
    }
    return total;
}

// An overloaded name: the moment `pick` gains a second overload it resolves
// through the overload-set path, yet a bare literal argument still adapts to
// the slice<int32> candidate (and never to the int32* one -- a literal is not
// a pointer). Both call paths adapt, so adding this overload cannot silently
// break pick([...]).
fn pick(xs: slice<int32>) -> int32 { return xs[xs.length - 1]; }
fn pick(p: int32*) -> int32 { return *p; }

// A @static literal lands in read-only data: the elements must be constant
// expressions, and the target must be slice<const T>. A mutable
// @static slice<int32> would open a write path into that read-only storage,
// so it is rejected with a message pointing at slice<const T>.
@static let table: slice<const int32> = [10, 20, 30];

fn main() -> int32 {
    // Spelling 1: the explicit borrow, legal in any expression slot.
    let view = [1, 2, 3] as slice<int32>;
    println("view: length %llu, sum %d", view.length, sum(view));   // 3, 6

    // Argument positions included -- no named array, no temporary binding.
    println("inline: %d", sum([10, 20, 30] as slice<const int32>)); // 60

    // ...and the `as` is now optional at an argument: a bare literal adapts to
    // the parameter's slice type directly, borrowing into this frame for the
    // call. A plain (non-mut) slice parameter is fine -- the backing array is
    // fresh writable storage -- so uniform-allow lets the literal in.
    println("bare arg: %d", sum([1, 2, 3, 4]));                     // 10

    // Through an overload set the literal still adapts, picking the
    // slice<int32> overload of pick over the int32* one.
    println("overloaded: %d", pick([7, 8, 9]));                     // 9

    // A ternary whose arms are both literals adapts arm by arm, each arm's
    // exact length surviving into its own branch.
    let flag = true;
    println("ternary arg: %d", sum(flag ? [100] : [1, 2, 3]));      // 100

    // Spelling 2: implicit adaptation at an annotated let. The annotation
    // supplies the element type, so no `as` is needed.
    let nums: slice<int32> = [0x10, 0x1F, 0xFF];
    println("adapted: nums[2] %d, sum %d", nums[2], sum(nums));     // 255, 302

    // Element position: a literal also adapts wherever the expected element
    // type is a slice, and nested literals recurse -- one annotation or `as`
    // covers the whole thing. Rows keep their own lengths, so the nested
    // form is a jagged matrix, not a rectangular T[2][2].
    let m: slice<int32>[2] = [[1, 2], [3, 4]];
    println("m rows: sum %d, sum %d", sum(m[0]), sum(m[1]));        // 3, 7

    let jagged = [[5, 6], [7]] as slice<slice<int32>>;
    for row in jagged {
        println("row of %llu: sum %d", row.length, sum(row));       // 2: 11, 1: 7
    }

    // The char seam: a literal's length is its EXACT element count, with no
    // NUL logic at all.
    let hi = ['h', 'i'] as slice<char>;
    println("literal chars: length %llu", hi.length);               // 2

    // The two-step form differs: a named char[N] is presumed NUL-terminated
    // text (see types/strings.mc), so its borrow drops the trailing byte.
    let cs: char[2] = ['h', 'i'];
    let named = cs as slice<char>;
    println("named char[2]: length %llu", named.length);            // 1

    // The empty literal builds no backing array at all: it is the { null, 0 }
    // empty view. sum's loop runs zero times.
    let empty: slice<int32> = [];
    println("empty: length %llu, sum %d", empty.length, sum(empty)); // 0, 0

    // A mutable target is fine. The backing array is fresh and nothing else
    // names it, so writes through the slice simply hit that hidden storage.
    let s: slice<int32> = [1, 2];
    s[0] = 9;
    println("after s[0] = 9: sum %d", sum(s));                      // 11

    // The @static view declared at the top of the file.
    println("static table: length %llu, sum %d", table.length, sum(table)); // 3, 60

    // One position never takes the shortcut: `return [1, 2] as slice<int32>;`
    // is a compile error, because the hidden backing array dies with the
    // returning call and the view would dangle.
    return 0;
}
