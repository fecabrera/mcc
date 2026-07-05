"""libmc/list.mc: the growable random-access sequence, over mut/const
receivers (stage 4 of the libmc receiver migration)."""

from helpers import run


def test_direct_receiver_with_growth():
    # The post-migration idiom: a local list passes directly, no `&`.
    # Capacity 1 forces list_grow (mut-to-mut re-lending inside list_push).
    assert run(
        """
        import "list";
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


def test_amp_call_sites_still_compile():
    # Pre-migration `&x` call shapes keep working via pointer decay.
    assert run(
        """
        import "list";
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
        import "list";
        import "memory";
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


def test_from_array_and_from_slice_build_owned_copies():
    assert run(
        """
        import "list";
        fn main() -> int32 {
            let seed: int32[3];
            seed[0] = 1; seed[1] = 2; seed[2] = 3;

            let a: struct list<int32>;
            list_from_array(a, &seed[0], 3);

            let b: struct list<int32>;
            list_from_slice(b, seed as slice<int32>);

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


def test_append_and_duplicate_through_const_source():
    # list_append's source and list_duplicate's src are const receivers; the
    # for-in inside list_append walks the borrowed source.
    assert run(
        """
        import "list";
        fn main() -> int32 {
            let a: struct list<int32>;
            list_init(a, 2);
            list_push(a, 1);
            list_push(a, 2);

            let b: struct list<int32>;
            list_duplicate(b, a);           // deep copy: [1, 2]
            list_push(b, 3);
            if (a.length != 2) return 100;  // a untouched by b's push

            list_append(a, b);              // a becomes [1, 2, 1, 2, 3]
            let last: int32 = 0;
            list_get(a, 4, last);
            let total = (a.length as int32) * 10 + last;   // 53
            list_destroy(a);
            list_destroy(b);
            return total;
        }
        """
    ) == 53
