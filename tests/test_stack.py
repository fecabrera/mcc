"""lib/std/stack.mc: the growable LIFO, over mut/const receivers (stage 2 of the
libmc receiver migration)."""

from helpers import run


def test_lifo_with_direct_receiver():
    # The post-migration idiom: a local stack passes directly, no `&`.
    # Capacity 1 forces the grow path (mut-to-mut re-lending inside push).
    assert run(
        """
        import "std/stack";
        fn main() -> int32 {
            let s = stack<int32>(1);
            s.push(1);
            s.push(2);
            s.push(3);
            if (s.length != 3) return 100;        // @property: field syntax
            if (s.peek() != 3) return 101;
            let last = s.pop();               // LIFO: most recent first
            let mid = s.pop();
            if (s.is_empty()) return 102;     // one element left
            return last * 10 + mid;           // 32
        }
        """
    ) == 32


def test_amp_call_sites_still_compile():
    # Pre-migration `&x` call shapes keep working via pointer decay.
    assert run(
        """
        import "std/stack";
        fn main() -> int32 {
            let s = stack<char>(2);
            s.push('a');
            s.push('b');
            let top = s.peek();
            let n = s.length() as int32;
            return (top == 'b' and n == 2) ? 0 : 1;
        }
        """
    ) == 0


def test_heap_pointer_decays_after_guard():
    # A heap stack<T>* reaches the receiver slots through the usual @nonnull
    # proof: one null guard covers every later call. Explicit construction, so
    # the matching destructor is called by hand (no ctor-sugar auto-defer).
    assert run(
        """
        import "std/stack";
        import "std/memory";
        fn main() -> int32 {
            let p = alloc<struct stack<int32>>(1);
            if (p == null) return 1;
            stack::constructor(p, 2);
            stack::push(p, 7);
            let v = stack::pop(p);
            let empty = stack::is_empty(p);
            stack::destructor(p);
            dealloc(p);
            return (v == 7 and empty) ? 0 : 2;
        }
        """
    ) == 0
