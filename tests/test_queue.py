"""libmc/queue.mc: the linked-list FIFO queue, over mut/const receivers.

Enqueue links a node at the tail, dequeue unlinks the head, both O(1); the
ring-buffer implementation this queue replaced lives on as libmc/ring.mc
(see test_ring.py).
"""

from helpers import run


def test_fifo_with_direct_receiver():
    # The post-migration idiom: a local queue passes directly, no `&`.
    assert run(
        """
        import "queue";
        fn main() -> int32 {
            let q: struct queue<int32>;
            queue_init(q);
            queue_push(q, 1);
            queue_push(q, 2);
            queue_push(q, 3);
            if (queue_peek(q) != 1) return 101;
            let first = queue_pop(q);          // FIFO: oldest first
            let second = queue_pop(q);
            if (queue_is_empty(q)) return 103; // one element left
            queue_destroy(q);
            return first * 10 + second;         // 12
        }
        """
    ) == 12


def test_first_push_links_head():
    # Regression: the first push into an empty queue must link head, or the
    # queue reports empty forever. Also drains back to empty and reuses the
    # queue, so tail relinks after a full drain.
    assert run(
        """
        import "queue";
        fn main() -> int32 {
            let q: struct queue<int32>;
            queue_init(q);
            queue_push(q, 7);
            if (queue_is_empty(q)) return 100;   // head was linked
            if (queue_pop(q) != 7) return 101;
            if (!queue_is_empty(q)) return 102;  // drained: head and tail reset
            queue_push(q, 8);                    // relinks after the drain
            if (queue_peek(q) != 8) return 103;
            queue_destroy(q);
            return 0;
        }
        """
    ) == 0


def test_for_in_yields_fifo_order():
    # queue_it/queue_next walk from the front (oldest) to the back (newest).
    assert run(
        """
        import "queue";
        fn main() -> int32 {
            let q: struct queue<int32>;
            queue_init(q);
            queue_push(q, 1);
            queue_push(q, 2);
            queue_push(q, 3);
            let sum: int32 = 0;
            for v in &q { sum = sum * 10 + v; }
            queue_destroy(q);
            return sum;             // 123 in FIFO order
        }
        """
    ) == 123


def test_amp_call_sites_still_compile():
    # Pre-migration `&x` call shapes keep working via pointer decay.
    assert run(
        """
        import "queue";
        fn main() -> int32 {
            let q: struct queue<char>;
            queue_init(&q);
            queue_push(&q, 'a');
            queue_push(&q, 'b');
            let front = queue_peek(&q);
            queue_destroy(&q);
            return (front == 'a' and queue_is_empty(&q)) ? 0 : 1;
        }
        """
    ) == 0
