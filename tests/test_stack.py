"""libmc/stack.mc: the growable LIFO, over mut/const receivers (stage 2 of the
libmc receiver migration)."""

from helpers import run


def test_lifo_with_direct_receiver():
    # The post-migration idiom: a local stack passes directly, no `&`.
    # Capacity 1 forces stack_grow (mut-to-mut re-lending inside stack_push).
    assert run(
        """
        import "stack";
        fn main() -> int32 {
            let s: struct stack<int32>;
            stack_init(s, 1);
            stack_push(s, 1);
            stack_push(s, 2);
            stack_push(s, 3);
            if (stack_len(s) != 3) return 100;
            if (stack_peek(s) != 3) return 101;
            let last = stack_pop(s);          // LIFO: most recent first
            let mid = stack_pop(s);
            if (stack_is_empty(s)) return 102; // one element left
            stack_destroy(s);
            return last * 10 + mid;            // 32
        }
        """
    ) == 32


def test_amp_call_sites_still_compile():
    # Pre-migration `&x` call shapes keep working via pointer decay.
    assert run(
        """
        import "stack";
        fn main() -> int32 {
            let s: struct stack<char>;
            stack_init(&s, 2);
            stack_push(&s, 'a');
            stack_push(&s, 'b');
            let top = stack_peek(&s);
            let n = stack_len(&s) as int32;
            stack_destroy(&s);
            return (top == 'b' and n == 2) ? 0 : 1;
        }
        """
    ) == 0


def test_heap_pointer_decays_after_guard():
    # A heap stack<T>* reaches the receiver slots through the usual @nonnull
    # proof: one null guard covers every later call.
    assert run(
        """
        import "stack";
        import "memory";
        fn main() -> int32 {
            let p = alloc<struct stack<int32>>(1);
            if (p == null) return 1;
            stack_init(p, 2);
            stack_push(p, 7);
            let v = stack_pop(p);
            let empty = stack_is_empty(p);
            stack_destroy(p);
            dealloc(p);
            return (v == 7 and empty) ? 0 : 2;
        }
        """
    ) == 0
