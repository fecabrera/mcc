"""lib/std/ring.mc: the growable FIFO ring buffer, over mut/const receivers.

Formerly lib/std/queue.mc's implementation; the queue moved to a linked list
(see test_queue.py) and the ring buffer kept living here. Element count reads
through the public `.length` field (the `ring_len` accessor is gone).
"""

from helpers import run


def test_fifo_with_direct_receiver():
    # A local ring passes directly, no `&`. Capacity 1 forces the grow path
    # (mut-to-mut re-lending inside push).
    assert run(
        """
        import "std/ring";
        fn main() -> int32 {
            let r = ring<int32>(1);
            r.push(1);
            r.push(2);
            r.push(3);
            if (r.length != 3) return 100;
            if (r.peek() != 1) return 101;
            if (r.at(2) != 3) return 102;
            let first = r.pop();              // FIFO: oldest first
            let second = r.pop();
            if (r.is_empty()) return 103;     // one element left
            return first * 10 + second;       // 12
        }
        """
    ) == 12


def test_wraps_around_head():
    # Pops move head forward, so later pushes reuse freed slots; `.at` in
    # value context reads through the wrap without disturbing the ring.
    assert run(
        """
        import "std/ring";
        fn main() -> int32 {
            let r = ring<int32>(4);
            r.push(1);
            r.push(2);
            r.push(3);
            r.pop();
            r.pop();              // head = 2, length = 1
            r.push(4);
            r.push(5);            // physically wraps to slots 0 and 1
            if (r.length != 3) return 100;
            let sum = r.at(0) + r.at(1) + r.at(2);
            return sum;           // 3 + 4 + 5
        }
        """
    ) == 12


def test_grow_relays_wrapped_elements():
    # Growing a wrapped ring re-lays the elements in logical order from
    # index 0 and resets head, so FIFO order survives the reallocation.
    assert run(
        """
        import "std/ring";
        fn main() -> int32 {
            let r = ring<int32>(2);
            r.push(1);
            r.push(2);
            r.pop();              // head = 1
            r.push(3);            // wraps into slot 0; ring is full
            r.push(4);            // forces the grow path on a wrapped layout
            if (r.length != 3) return 100;
            let sum = r.at(0) + r.at(1) + r.at(2);
            if (r.peek() != 2) return 101;   // front survived the move
            return sum;           // 2 + 3 + 4
        }
        """
    ) == 9


def test_has_and_at_write_through_the_wrap():
    # `.has` is the triad's domain predicate (logical index < length); `.at`
    # is its unchecked mutable half: at a wrapped position the returned lvalue
    # lands on the physical slot behind the logical index.
    assert run(
        """
        import "std/ring";
        fn main() -> int32 {
            let r = ring<int32>(4);
            r.push(1);
            r.push(2);
            r.push(3);
            r.pop();
            r.pop();             // head = 2, length = 1
            r.push(4);           // slot 3
            r.push(5);           // physically wraps into slot 0
            if (!r.has(2)) return 100;   // last logical index
            if (r.has(3)) return 101;    // length itself is not
            r.at(2) = 50;        // logical back = wrapped slot 0
            r.at(0) += 1;        // logical front, slot 2: 3 -> 4
            let back = r.at(2);              // value context copies out
            r.pop();
            r.pop();
            let last = r.pop();             // drains the wrapped slot
            return (back == 50 and last == 50) ? 0 : 1;
        }
        """
    ) == 0


def test_has_and_at_track_a_grow():
    # The grow path re-lays wrapped elements in logical order and resets head,
    # so lvalues formed after the reallocation address the new buffer and
    # `.has` tracks the grown length.
    assert run(
        """
        import "std/ring";
        fn main() -> int32 {
            let r = ring<int32>(2);
            r.push(1);
            r.push(2);
            r.pop();             // head = 1
            r.push(3);           // wraps into slot 0; ring is full
            r.push(4);           // grow re-lays [2, 3], resets head
            if (!r.has(2) or r.has(3)) return 100;
            r.at(0) = 20;        // front, in the new buffer: 2 -> 20
            r.at(2) += 6;        // back: 4 -> 10
            let sum = r.at(0) + r.at(1) + r.at(2);
            return sum;          // 20 + 3 + 10
        }
        """
    ) == 33


def test_amp_call_sites_still_compile():
    # Pre-migration `&x` call shapes keep working via pointer decay.
    assert run(
        """
        import "std/ring";
        fn main() -> int32 {
            let r = ring<char>(2);
            r.push('a');
            r.push('b');
            let front = r.peek();
            let n = r.length as int32;
            return (front == 'a' and n == 2) ? 0 : 1;
        }
        """
    ) == 0
