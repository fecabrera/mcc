"""End-to-end tests: JIT-compile and run programs, asserting on real output.

capfd captures at the file-descriptor level, so it sees libc's printf output.
"""

from helpers import run


def test_hello_world(capfd):
    status = run(
        """
        #include <stdio.h>
        fn main() -> int32 {
            printf("hello, world\\n");
            return 0;
        }
        """
    )
    assert status == 0
    assert capfd.readouterr().out == "hello, world\n"


def test_exit_status_is_main_return_value():
    assert run("fn main() -> int32 { return 41 + 1; }") == 42


def test_loops_recursion_and_varargs(capfd):
    run(
        """
        #include <stdio.h>
        fn fib(n: int32) -> int32 {
            if (n < 2) { return n; }
            return fib(n - 1) + fib(n - 2);
        }
        fn main() -> int32 {
            let i: int32 = 0;
            while (i < 8) {
                printf("%d ", fib(i));
                i = i + 1;
            }
            return 0;
        }
        """
    )
    assert capfd.readouterr().out == "0 1 1 2 3 5 8 13 "


def test_if_else_chain(capfd):
    run(
        """
        #include <stdio.h>
        fn label(n: int32) {
            if (n > 10) { puts("big"); }
            else if (n > 5) { puts("medium"); }
            else { puts("small"); }
        }
        fn main() -> int32 {
            label(20);
            label(7);
            label(1);
            return 0;
        }
        """
    )
    assert capfd.readouterr().out == "big\nmedium\nsmall\n"


def test_until_loop(capfd):
    run(
        """
        #include <stdio.h>
        fn main() -> int32 {
            let n: int32 = 27;
            let steps: int32 = 0;
            until (n == 1) {
                if (n % 2 == 0) { n = n / 2; }
                else { n = 3 * n + 1; }
                steps = steps + 1;
            }
            printf("collatz(27) took %d steps\\n", steps);
            until (true) { puts("never runs"); }
            return 0;
        }
        """
    )
    assert capfd.readouterr().out == "collatz(27) took 111 steps\n"


def test_unsigned_semantics(capfd):
    run(
        """
        #include <stdio.h>
        fn main() -> int32 {
            let big: uint32 = 4000000000;
            printf("%u\\n", big / 2);
            if (big > 100) { puts("gt"); }
            let small: uint8 = 200;
            printf("%u\\n", small);
            let s: int8 = -100;
            printf("%d\\n", s);
            let huge: uint64 = 18000000000000000000;
            printf("%llu\\n", huge % 7);
            return 0;
        }
        """
    )
    assert capfd.readouterr().out == "2000000000\ngt\n200\n-100\n4\n"


def test_generics_explicit_inferred_and_recursive(capfd):
    run(
        """
        #include <stdio.h>
        fn max<T>(a: T, b: T) -> T {
            if (a > b) { return a; }
            return b;
        }
        fn fact<T>(n: T) -> T {
            if (n < 2) { return 1; }
            return n * fact(n - 1);
        }
        fn main() -> int32 {
            let x: int64 = 9000000000;
            printf("%lld\\n", max(x, 7));
            printf("%u\\n", max<uint8>(3, 200));
            printf("%llu\\n", fact<uint64>(20));
            printf("%f\\n", max(1.5, 2.5));
            return 0;
        }
        """
    )
    out = capfd.readouterr().out
    assert out == "9000000000\n200\n2432902008176640000\n2.500000\n"


def test_float_arithmetic(capfd):
    run(
        """
        #include <stdio.h>
        fn main() -> int32 {
            let x = 3.5;
            printf("%f\\n", (x + 0.5) * 2.0 / 4.0 - 1.0);
            return 0;
        }
        """
    )
    assert capfd.readouterr().out == "1.000000\n"


def test_bool_logic(capfd):
    run(
        """
        #include <stdio.h>
        fn main() -> int32 {
            let t = true;
            if (!t) { puts("wrong"); } else { puts("right"); }
            let cmp = 3 < 4;
            if (cmp == true) { puts("cmp"); }
            return 0;
        }
        """
    )
    assert capfd.readouterr().out == "right\ncmp\n"


def test_uninitialized_let():
    status = run(
        """
        struct point { x: int32; y: int32; }
        fn main() -> int32 {
            let n: int32;
            if (true) { n = 40; } else { n = 7; }
            let p: struct point;
            p.x = n;
            p.y = 2;
            let q: struct point*;
            q = &p;
            return q->x + q->y;
        }
        """
    )
    assert status == 42


def test_break_and_continue(capfd):
    status = run(
        """
        #include <stdio.h>
        fn main() -> int32 {
            let i: int32 = 0;
            let sum: int32 = 0;
            while (true) {
                i = i + 1;
                if (i > 10) { break; }
                if (i % 2 == 0) { continue; }
                sum = sum + i;            // odd numbers 1..9
            }
            printf("%d\\n", sum);
            return 0;
        }
        """
    )
    assert status == 0
    assert capfd.readouterr().out == "25\n"


def test_break_only_exits_the_inner_loop(capfd):
    run(
        """
        #include <stdio.h>
        fn main() -> int32 {
            let i: int32 = 0;
            until (i == 3) {
                i = i + 1;
                let j: int32 = 0;
                while (j < 10) {
                    j = j + 1;
                    if (j == 2) { break; }
                }
                printf("%d:%d ", i, j);
            }
            return 0;
        }
        """
    )
    assert capfd.readouterr().out == "1:2 2:2 3:2 "


def test_continue_in_an_until_loop(capfd):
    run(
        """
        #include <stdio.h>
        fn main() -> int32 {
            let i: int32 = 0;
            until (i >= 6) {
                i = i + 1;
                if (i % 3 != 0) { continue; }
                printf("%d ", i);
            }
            return 0;
        }
        """
    )
    assert capfd.readouterr().out == "3 6 "
