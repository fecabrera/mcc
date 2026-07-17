"""lib/std/list.mc: the growable random-access sequence, over mut/const
receivers (stage 4 of the libmc receiver migration)."""

from helpers import compile_ir, run


def test_direct_receiver_with_growth():
    # The post-migration idiom: a local list passes directly, no `&`.
    # Capacity 1 forces list_grow (mut-to-mut re-lending inside list_push).
    assert run(
        """
        import "std/list";
        fn main() -> int32 {
            let xs = list<int32>(1);
            xs.push(7);
            xs.push(8);
            xs.push(9);
            if (xs.length != 3) return 100;
            let v: int32 = 0;
            if (!xs.get(2, v)) return 101;   // const self, mut out
            if (!xs.set( 0, 70)) return 102;
            let first: int32 = 0;
            xs.get(0, first);
            xs.reset();
            if (xs.length != 0) return 103;        // reset keeps the storage
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
            let xs = list<int32>(2);
            xs.push(7);
            xs.push(8);
            xs.push(9);
            if (!xs.has(2)) return 1;   // last index is in bounds
            if (xs.has(3)) return 2;    // length itself is not
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
            let xs = list<int32>(4);
            xs.push(10);
            xs.push(20);
            xs.at(0) = 11;              // write-through
            xs.at(1) += 1;              // compound: 21
            let a = xs.at(0);           // value context copies out
            let b: int32 = 0;
            xs.get(1, b);               // the checked read agrees
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
            let xs = list<int32>(2);
            xs.push(1);
            xs.push(2);
            if (xs.has(1)) xs.at(1) = 5;   // in bounds: writes
            if (xs.has(9)) xs.at(9) = 5;   // out: never accessed
            let v: int32 = 0;
            xs.get(1, v);
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
            let xs = list<int32>(2);
            xs.push(5);
            xs.push(6);
            let v: int32 = 0;
            xs.get(1, v);
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
            list::constructor(p, 2);
            for i in range(5 as uint64) {
                list::push(p!, i * i);
            }
            let v: uint64 = 0;
            list::get(p, 4, v);              // 16
            let n = p->length;              // field reads need no proof shape
            list::destructor(p);
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

            let a = list<int32>(&seed[0], 3);

            let b = list<int32>(seed as slice<int32>);

            seed[0] = 100;                  // neither list shares seed's storage
            let x: int32 = 0;
            let y: int32 = 0;
            a.get(0, x);
            b.get(0, y);
            let total = (a.length + b.length) as int32 + x + y;   // 3+3+1+1
            return total;
        }
        """
    ) == 8


def test_pointer_length_ctor_wins_over_inherited_slice_constructor():
    # list<T> extends slice<T>, so slice::constructor is inherited into the
    # merged family. (p, n) sugar must reach list's copying ctor (same tier,
    # nearer hop), not the inherited slice view -- which would leave capacity
    # unset and dealloc a foreign pointer at scope end.
    ir_text = compile_ir(
        """
        import "std/list";
        fn main() -> int32 {
            let seed: int32[3];
            let a = list<int32>(&seed[0], 3);
            return a.length as int32;
        }
        """
    )
    assert (
        'call void @"list::constructor<$0, $1: int64|uint64|int32|uint32>'
        "(&list<$0>, $0*, $1)<int32, int32>"
    ) in ir_text
    assert "slice::constructor" not in ir_text.split('define i32 @"main"')[1]


def test_append_and_init_copy_through_const_source():
    # list_append's items and the slice overload of list_init's src are const
    # slice<T> views: a source list borrows in with `as` (its slice prefix),
    # and the for-in inside list_append walks the borrowed run.
    assert run(
        """
        import "std/list";
        fn main() -> int32 {
            let a = list<int32>(2);
            a.push(1);
            a.push(2);

            let b = list<int32>(a as slice<int32>);        // deep copy: [1, 2]
            b.push(3);
            if (a.length != 2) return 100;  // a untouched by b's push

            a.append(b as slice<int32>);      // a becomes [1, 2, 1, 2, 3]
            let last: int32 = 0;
            a.get(4, last);
            let total = (a.length as int32) * 10 + last;   // 53
            
            return total;
        }
        """
    ) == 53
