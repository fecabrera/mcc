"""lib/std/queue.mc: the linked-list FIFO queue, over mut/const receivers.

Enqueue links a node at the tail, dequeue unlinks the head, both O(1); the
ring-buffer implementation this queue replaced lives on as lib/std/ring.mc
(see test_ring.py).
"""

from helpers import run


def test_fifo_with_direct_receiver():
    # The post-migration idiom: a local queue passes directly, no `&`.
    assert run(
        """
        import "std/queue";
        fn main() -> int32 {
            let q = queue<int32>();
            q.push(1);
            q.push(2);
            q.push(3);
            if (q.peek() != 1) return 101;
            let first = q.pop();               // FIFO: oldest first
            let second = q.pop();
            if (q.is_empty()) return 103;      // one element left
            return first * 10 + second;        // 12
        }
        """
    ) == 12


def test_first_push_links_head():
    # Regression: the first push into an empty queue must link head, or the
    # queue reports empty forever. Also drains back to empty and reuses the
    # queue, so tail relinks after a full drain.
    assert run(
        """
        import "std/queue";
        fn main() -> int32 {
            let q = queue<int32>();
            q.push(7);
            if (q.is_empty()) return 100;      // head was linked
            if (q.pop() != 7) return 101;
            if (!q.is_empty()) return 102;     // drained: head and tail reset
            q.push(8);                         // relinks after the drain
            if (q.peek() != 8) return 103;
            return 0;
        }
        """
    ) == 0


def test_for_in_yields_fifo_order():
    # queue_it/queue_next walk from the front (oldest) to the back (newest).
    assert run(
        """
        import "std/queue";
        fn main() -> int32 {
            let q = queue<int32>();
            q.push(1);
            q.push(2);
            q.push(3);
            let sum: int32 = 0;
            for v in &q { sum = sum * 10 + v; }
            return sum;             // 123 in FIFO order
        }
        """
    ) == 123


def test_amp_call_sites_still_compile():
    # Pre-migration `&x` call shapes keep working via pointer decay.
    assert run(
        """
        import "std/queue";
        fn main() -> int32 {
            let q = queue<char>();
            q.push('a');
            q.push('b');
            let front = q.peek();
            return (front == 'a' and !q.is_empty()) ? 0 : 1;
        }
        """
    ) == 0
