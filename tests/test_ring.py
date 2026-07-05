"""libmc/ring.mc: the growable FIFO ring buffer, over mut/const receivers.

Formerly libmc/queue.mc's implementation; the queue moved to a linked list
(see test_queue.py) and the ring buffer kept living here under ring_* names.
"""

from helpers import run


def test_fifo_with_direct_receiver():
    # A local ring passes directly, no `&`. Capacity 1 forces ring_grow
    # (mut-to-mut re-lending inside ring_push).
    assert run(
        """
        import "ring";
        fn main() -> int32 {
            let r: struct ring<int32>;
            ring_init(r, 1);
            ring_push(r, 1);
            ring_push(r, 2);
            ring_push(r, 3);
            if (ring_len(r) != 3) return 100;
            if (ring_peek(r) != 1) return 101;
            if (ring_at(r, 2) != 3) return 102;
            let first = ring_pop(r);          // FIFO: oldest first
            let second = ring_pop(r);
            if (ring_is_empty(r)) return 103; // one element left
            ring_destroy(r);
            return first * 10 + second;        // 12
        }
        """
    ) == 12


def test_wraps_around_head():
    # Pops move head forward, so later pushes reuse freed slots; ring_at
    # (const self) reads through the wrap without disturbing the ring.
    assert run(
        """
        import "ring";
        fn main() -> int32 {
            let r: struct ring<int32>;
            ring_init(r, 4);
            ring_push(r, 1);
            ring_push(r, 2);
            ring_push(r, 3);
            ring_pop(r);
            ring_pop(r);          // head = 2, length = 1
            ring_push(r, 4);
            ring_push(r, 5);      // physically wraps to slots 0 and 1
            if (ring_len(r) != 3) return 100;
            let sum = ring_at(r, 0) + ring_at(r, 1) + ring_at(r, 2);
            ring_destroy(r);
            return sum;            // 3 + 4 + 5
        }
        """
    ) == 12


def test_grow_relays_wrapped_elements():
    # Growing a wrapped ring re-lays the elements in logical order from
    # index 0 and resets head, so FIFO order survives the reallocation.
    assert run(
        """
        import "ring";
        fn main() -> int32 {
            let r: struct ring<int32>;
            ring_init(r, 2);
            ring_push(r, 1);
            ring_push(r, 2);
            ring_pop(r);           // head = 1
            ring_push(r, 3);       // wraps into slot 0; ring is full
            ring_push(r, 4);       // forces ring_grow on a wrapped layout
            if (ring_len(r) != 3) return 100;
            let sum = ring_at(r, 0) + ring_at(r, 1) + ring_at(r, 2);
            if (ring_peek(r) != 2) return 101;   // front survived the move
            ring_destroy(r);
            return sum;             // 2 + 3 + 4
        }
        """
    ) == 9


def test_amp_call_sites_still_compile():
    # Pre-migration `&x` call shapes keep working via pointer decay.
    assert run(
        """
        import "ring";
        fn main() -> int32 {
            let r: struct ring<char>;
            ring_init(&r, 2);
            ring_push(&r, 'a');
            ring_push(&r, 'b');
            let front = ring_peek(&r);
            let n = ring_len(&r) as int32;
            ring_destroy(&r);
            return (front == 'a' and n == 2) ? 0 : 1;
        }
        """
    ) == 0
