"""lib/std/list.mc: the growable random-access sequence, over mut/const
receivers (stage 4 of the libmc receiver migration)."""

from helpers import run


def test_direct_receiver_with_growth():
    # The post-migration idiom: a local list passes directly, no `&`.
    # Capacity 1 forces list_grow (mut-to-mut re-lending inside list_push).
    assert run(
        """
        import "std/list";
        fn main() -> int32 {
            let xs: struct list<int32>;
            list_init(xs, 1);
            list_push(xs, 7);
            list_push(xs, 8);
            list_push(xs, 9);
            if (xs.length != 3) return 100;
            let v: int32 = 0;
            if (!list_get(xs, 2, v)) return 101;   // const self, mut out
            if (!list_set(xs, 0, 70)) return 102;
            let first: int32 = 0;
            list_get(xs, 0, first);
            list_reset(xs);
            if (xs.length != 0) return 103;        // reset keeps the storage
            list_destroy(xs);
            return first + v;                      // 70 + 9
        }
        """
    ) == 79


def test_has_is_true_strictly_below_length():
    # list_has is the triad's domain predicate: true for the last element
    # (length - 1), false at length itself.
    assert run(
        """
        import "std/list";
        fn main() -> int32 {
            let xs: struct list<int32>;
            list_init(xs, 2);
            list_push(xs, 7);
            list_push(xs, 8);
            list_push(xs, 9);
            if (!list_has(xs, 2)) return 1;   // last index is in bounds
            if (list_has(xs, 3)) return 2;    // length itself is not
            list_destroy(xs);
            return 0;
        }
        """
    ) == 0


def test_at_is_an_assignable_lvalue():
    # list_at is the triad's unchecked mutable half (list_get is the checked
    # read): plain assignment writes into the list's storage, compound
    # assignment addresses the element once, and value context copies out.
    assert run(
        """
        import "std/list";
        fn main() -> int32 {
            let xs: struct list<int32>;
            list_init(xs, 4);
            list_push(xs, 10);
            list_push(xs, 20);
            list_at(xs, 0) = 11;              // write-through
            list_at(xs, 1) += 1;              // compound: 21
            let a = list_at(xs, 0);           // value context copies out
            let b: int32 = 0;
            list_get(xs, 1, b);               // the checked read agrees
            list_destroy(xs);
            return a + b;                     // 11 + 21
        }
        """
    ) == 32


def test_has_guards_at():
    # The guard idiom: list_at is undefined out of bounds, so a list_has
    # test brackets the access; the out-of-range write never runs.
    assert run(
        """
        import "std/list";
        fn main() -> int32 {
            let xs: struct list<int32>;
            list_init(xs, 2);
            list_push(xs, 1);
            list_push(xs, 2);
            if (list_has(xs, 1)) list_at(xs, 1) = 5;   // in bounds: writes
            if (list_has(xs, 9)) list_at(xs, 9) = 5;   // out: never accessed
            let v: int32 = 0;
            list_get(xs, 1, v);
            list_destroy(xs);
            return v;
        }
        """
    ) == 5


def test_amp_call_sites_still_compile():
    # Pre-migration `&x` call shapes keep working via pointer decay.
    assert run(
        """
        import "std/list";
        fn main() -> int32 {
            let xs: struct list<int32>;
            list_init(&xs, 2);
            list_push(&xs, 5);
            list_push(&xs, 6);
            let v: int32 = 0;
            list_get(&xs, 1, v);
            list_destroy(&xs);
            return v;
        }
        """
    ) == 6


def test_heap_pointer_decays_after_guard():
    # A heap list<T>* reaches the receiver slots through the usual @nonnull
    # proof: one null guard after the allocation; a mut receiver inside a
    # loop drops the narrowed fact, so the in-loop push takes the `!` hatch.
    assert run(
        """
        import "std/list";
        import "std/memory";
        fn main() -> int32 {
            let p = alloc<struct list<uint64>>(1);
            if (p == null) return 1;
            list_init(p, 2);
            for i in range(5 as uint64) {
                list_push(p!, i * i);
            }
            let v: uint64 = 0;
            list_get(p, 4, v);              // 16
            let n = p->length;              // field reads need no proof shape
            list_destroy(p);
            dealloc(p);
            return (v + n) as int32;        // 16 + 5
        }
        """
    ) == 21


def test_init_overloads_build_owned_copies():
    # list_init's copying overloads take a raw (T*, n) run or a slice<T>, so
    # they copy from any borrowed run -- here an array and its borrow -- not
    # just from another list.
    assert run(
        """
        import "std/list";
        fn main() -> int32 {
            let seed: int32[3];
            seed[0] = 1; seed[1] = 2; seed[2] = 3;

            let a: struct list<int32>;
            list_init(a, &seed[0], 3);

            let b: struct list<int32>;
            list_init(b, seed as slice<int32>);

            seed[0] = 100;                  // neither list shares seed's storage
            let x: int32 = 0;
            let y: int32 = 0;
            list_get(a, 0, x);
            list_get(b, 0, y);
            let total = (a.length + b.length) as int32 + x + y;   // 3+3+1+1
            list_destroy(a);
            list_destroy(b);
            return total;
        }
        """
    ) == 8


def test_append_and_init_copy_through_const_source():
    # list_append's items and the slice overload of list_init's src are const
    # slice<T> views: a source list borrows in with `as` (its slice prefix),
    # and the for-in inside list_append walks the borrowed run.
    assert run(
        """
        import "std/list";
        fn main() -> int32 {
            let a: struct list<int32>;
            list_init(a, 2);
            list_push(a, 1);
            list_push(a, 2);

            let b: struct list<int32>;
            list_init(b, a as slice<int32>);        // deep copy: [1, 2]
            list_push(b, 3);
            if (a.length != 2) return 100;  // a untouched by b's push

            list_append(a, b as slice<int32>);      // a becomes [1, 2, 1, 2, 3]
            let last: int32 = 0;
            list_get(a, 4, last);
            let total = (a.length as int32) * 10 + last;   // 53
            list_destroy(a);
            list_destroy(b);
            return total;
        }
        """
    ) == 53
