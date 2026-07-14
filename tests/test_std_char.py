"""lib/std/char.mc: classification and case-conversion methods on `char`,
thin @inline wrappers over libc's ctype registered as builtin-qualifier
methods (`char::is_alpha(c)` etc.)."""

from helpers import run


def test_is_alpha_letters_only():
    assert run(
        """
        import "std/char";
        fn main() -> int32 {
            if (char::is_alpha('a') == false) return 1;
            if (char::is_alpha('Z') == false) return 2;
            if (char::is_alpha('4')) return 3;
            if (char::is_alpha(' ')) return 4;
            if (char::is_alpha('_')) return 5;
            return 0;
        }
        """
    ) == 0


def test_is_alnum_letters_and_digits():
    assert run(
        """
        import "std/char";
        fn main() -> int32 {
            if (char::is_alnum('m') == false) return 1;
            if (char::is_alnum('Q') == false) return 2;
            if (char::is_alnum('0') == false) return 3;
            if (char::is_alnum('-')) return 4;
            if (char::is_alnum(' ')) return 5;
            return 0;
        }
        """
    ) == 0


def test_is_digit_decimal_only():
    assert run(
        """
        import "std/char";
        fn main() -> int32 {
            if (char::is_digit('0') == false) return 1;
            if (char::is_digit('9') == false) return 2;
            if (char::is_digit('a')) return 3;    // hex digit, not decimal
            if (char::is_digit(' ')) return 4;
            return 0;
        }
        """
    ) == 0


def test_is_hex_covers_both_cases():
    assert run(
        """
        import "std/char";
        fn main() -> int32 {
            if (char::is_hex('7') == false) return 1;
            if (char::is_hex('a') == false) return 2;
            if (char::is_hex('F') == false) return 3;
            if (char::is_hex('g')) return 4;
            if (char::is_hex('G')) return 5;
            return 0;
        }
        """
    ) == 0


def test_is_space_whitespace_set():
    assert run(
        r"""
        import "std/char";
        fn main() -> int32 {
            if (char::is_space(' ') == false) return 1;
            if (char::is_space('\t') == false) return 2;
            if (char::is_space('\n') == false) return 3;
            if (char::is_space('\r') == false) return 4;
            if (char::is_space('x')) return 5;
            if (char::is_space('0')) return 6;
            return 0;
        }
        """
    ) == 0


def test_is_upper_is_lower_partition_letters():
    assert run(
        """
        import "std/char";
        fn main() -> int32 {
            if (char::is_upper('A') == false) return 1;
            if (char::is_upper('a')) return 2;
            if (char::is_lower('a') == false) return 3;
            if (char::is_lower('A')) return 4;
            // non-letters are neither
            if (char::is_upper('5')) return 5;
            if (char::is_lower('5')) return 6;
            return 0;
        }
        """
    ) == 0


def test_upper_and_lower_convert_letters():
    assert run(
        """
        import "std/char";
        fn main() -> int32 {
            if (char::upper('h') != 'H') return 1;
            if (char::lower('H') != 'h') return 2;
            // already in the target case: unchanged
            if (char::upper('H') != 'H') return 3;
            if (char::lower('h') != 'h') return 4;
            return 0;
        }
        """
    ) == 0


def test_upper_and_lower_leave_non_letters_unchanged():
    assert run(
        """
        import "std/char";
        fn main() -> int32 {
            if (char::upper('!') != '!') return 1;
            if (char::lower('!') != '!') return 2;
            if (char::upper('7') != '7') return 3;
            if (char::lower(' ') != ' ') return 4;
            return 0;
        }
        """
    ) == 0


def test_methods_compose_over_a_string():
    # The motivating shape: classify/convert characters while scanning text.
    assert run(
        """
        import "std/char";
        fn main() -> int32 {
            let letters: int32 = 0;
            let digits: int32 = 0;
            let spaces: int32 = 0;
            for c in "Ab3 x9" as slice<char> {
                if (char::is_alpha(c)) letters += 1;
                if (char::is_digit(c)) digits += 1;
                if (char::is_space(c)) spaces += 1;
            }
            return letters * 100 + digits * 10 + spaces;   // 3, 2, 1
        }
        """
    ) == 321


def test_upper_maps_over_a_string(capfd):
    assert run(
        """
        import "std/io";
        import "std/char";
        fn main() -> int32 {
            for c in "mcc rocks!" as slice<char> {
                print(f"{char::upper(c)}");
            }
            println("");
            return 0;
        }
        """
    ) == 0
    assert capfd.readouterr().out == "MCC ROCKS!\n"
