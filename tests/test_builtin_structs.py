"""The builtin struct templates `iterator<T>` and `pair<K, V>`: available in
every program with no import, and shadowed by a same-named user struct (the
same precedence rule as the builtin `range` counting loop)."""

from helpers import run


def test_pair_needs_no_import(capfd):
    run(
        """
        import "libc/stdio";
        fn main() -> int32 {
            let p = struct pair<int32, char*> { key = 7, value = "seven" };
            printf("%d %s\\n", p.key, p.value);
            return 0;
        }
        """
    )
    assert capfd.readouterr().out == "7 seven\n"


def test_iterator_needs_no_import(capfd):
    # A custom container built on the shared cursor: `_it` returns a builtin
    # iterator<count>, `_next` advances it -- no import anywhere.
    run(
        """
        import "libc/stdio";
        struct count { limit: uint64; }
        fn count_it(@nonnull self: struct count*) -> struct iterator<count> {
            return struct iterator { obj = self, idx = 0 };
        }
        fn count_next(it: struct iterator<count>*, out: uint64*) -> bool {
            if (it->idx < it->obj->limit) {
                *out = it->idx;
                it->idx += 1;
                return true;
            }
            return false;
        }
        fn main() -> int32 {
            let c = struct count { limit = 4 };
            let sum: uint64 = 0;
            for i in c { sum += i; }
            printf("%llu\\n", sum);
            return 0;
        }
        """
    )
    assert capfd.readouterr().out == "6\n"


def test_containers_yield_builtin_pair(capfd):
    # `set_next` fills a builtin pair<K, V>; usable by name with no import
    # beyond the container's own.
    run(
        """
        import "std/set";
        import "libc/stdio";
        fn main() -> int32 {
            let s = set<uint64, uint64>(4);
            s.set(2, 20);
            let it = set_it<uint64, uint64>(&s);
            let p: struct pair<uint64, uint64>;
            let total: uint64 = 0;
            while (set_next<uint64, uint64>(&it, &p)) {
                total += p.key + p.value;
            }
            printf("%llu\\n", total);
            return 0;
        }
        """
    )
    assert capfd.readouterr().out == "22\n"


def test_user_pair_takes_precedence(capfd):
    # A user struct named `pair` shadows the builtin -- its own fields, its own
    # shape, exactly as before pair was builtin.
    run(
        """
        import "libc/stdio";
        struct pair<A, B> { a: A; b: B; }
        fn main() -> int32 {
            let p = struct pair<int32, int32> { a = 1, b = 2 };
            printf("%d\\n", p.a + p.b);
            return 0;
        }
        """
    )
    assert capfd.readouterr().out == "3\n"


def test_user_iterator_takes_precedence(capfd):
    run(
        """
        import "libc/stdio";
        struct iterator { pos: int32; }
        fn main() -> int32 {
            let it = struct iterator { pos = 9 };
            printf("%d\\n", it.pos);
            return 0;
        }
        """
    )
    assert capfd.readouterr().out == "9\n"
