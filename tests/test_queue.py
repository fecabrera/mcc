"""libmc/queue.mc: the growable FIFO ring buffer, over mut/const receivers
(stage 2 of the libmc receiver migration)."""

from helpers import run


def test_fifo_with_direct_receiver():
    # The post-migration idiom: a local queue passes directly, no `&`.
    # Capacity 1 forces queue_grow (mut-to-mut re-lending inside queue_push).
    assert run(
        """
        import "queue";
        fn main() -> int32 {
            let q: struct queue<int32>;
            queue_init(q, 1);
            queue_push(q, 1);
            queue_push(q, 2);
            queue_push(q, 3);
            if (queue_len(q) != 3) return 100;
            if (queue_peek(q) != 1) return 101;
            if (queue_at(q, 2) != 3) return 102;
            let first = queue_pop(q);          // FIFO: oldest first
            let second = queue_pop(q);
            if (queue_is_empty(q)) return 103; // one element left
            queue_destroy(q);
            return first * 10 + second;         // 12
        }
        """
    ) == 12


def test_ring_wraps_around_head():
    # Pops move head forward, so later pushes reuse freed slots; queue_at
    # (const self) reads through the wrap without disturbing the ring.
    assert run(
        """
        import "queue";
        fn main() -> int32 {
            let q: struct queue<int32>;
            queue_init(q, 4);
            queue_push(q, 1);
            queue_push(q, 2);
            queue_push(q, 3);
            queue_pop(q);
            queue_pop(q);          // head = 2, length = 1
            queue_push(q, 4);
            queue_push(q, 5);      // physically wraps to slots 0 and 1
            if (queue_len(q) != 3) return 100;
            let sum = queue_at(q, 0) + queue_at(q, 1) + queue_at(q, 2);
            queue_destroy(q);
            return sum;             // 3 + 4 + 5
        }
        """
    ) == 12


def test_amp_call_sites_still_compile():
    # Pre-migration `&x` call shapes keep working via pointer decay.
    assert run(
        """
        import "queue";
        fn main() -> int32 {
            let q: struct queue<char>;
            queue_init(&q, 2);
            queue_push(&q, 'a');
            queue_push(&q, 'b');
            let front = queue_peek(&q);
            let n = queue_len(&q) as int32;
            queue_destroy(&q);
            return (front == 'a' and n == 2) ? 0 : 1;
        }
        """
    ) == 0
