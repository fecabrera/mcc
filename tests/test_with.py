"""The `with` statement: `with (t = v as T) body; else other;`.

The checked-`as` test, pure sugar over a single-arm `case type`: it tests an
`any` subject's boxed tag against one pattern and, on a match, binds the
recovered value to a fresh name scoped to the true branch (the `else` branch
has no binding). The head is initializer-style and is itself the checked
context: inside it `t = v as T` is the tag test plus bind -- deliberately
the same spelling as the planned bare unwrap `let t = v as T;`, with
`with`/`else` supplying the mismatch handling -- while `as` everywhere else
keeps its cast semantics on non-`any` subjects. The pattern follows the
exact detection rule of generic `case type` arms: a resolvable name is a
concrete test (single tag compare), an unresolved bare name with zero or
one `*` a generic pattern monomorphized per boxed tag over the whole
program's boxed set, each copy fully type-checked. Unlike `case type`, the
`else` is optional: an unmatched tag (including a zeroed any's tag 0) takes
the `else` or falls through a lone `with` doing nothing -- defined
behavior, no trap. The binding is required (`with (v as T)` without `t =`
does not parse), the pattern names exactly one type, and the head does not
compose with `and`/`or`.
"""

import pytest

from mcc.errors import LangError
from helpers import compile_ir, parse, run


# ------------------------------------------------------------- parse errors

def test_missing_binding_is_a_parse_error():
    # The initializer-style `t =` is what makes the head a checked bind.
    with pytest.raises(
        LangError,
        match=r"a with head binds a name first, as in "
              r"'with \(n = v as int32\)'",
    ):
        parse(
            """
            fn f(a: any) {
                with (a as int32) return;
            }
            """
        )


def test_missing_parens_is_a_parse_error():
    with pytest.raises(LangError, match=r"expected '\(', got 'n'"):
        parse(
            """
            fn f(a: any) {
                with n = a as int32 return;
            }
            """
        )


def test_and_composition_is_a_parse_error():
    # The checked bind is the entire head: nothing composes after the
    # pattern.
    with pytest.raises(LangError, match=r"expected '\)', got 'and'"):
        parse(
            """
            fn f(a: any, ok: bool) {
                with (n = a as int32 and ok) return;
            }
            """
        )


def test_or_composition_around_the_subject_is_a_parse_error():
    # The subject sits below `as` in the precedence chain, so a composed
    # condition never reaches the pattern.
    with pytest.raises(LangError, match=r"expected 'as', got 'or'"):
        parse(
            """
            fn f(a: any, ok: bool) {
                with (n = ok or a as int32) return;
            }
            """
        )


def test_composition_before_the_binding_is_a_parse_error():
    # A condition in front of the head's `t =` shape is not a binding.
    with pytest.raises(
        LangError,
        match=r"a with head binds a name first, as in "
              r"'with \(n = v as int32\)'",
    ):
        parse(
            """
            fn f(a: any, ok: bool) {
                with (ok and n = a as int32) return;
            }
            """
        )


def test_negated_test_is_a_parse_error():
    # `!` applies to expressions; the head's checked bind is not one.
    with pytest.raises(LangError, match=r"expected 'as', got '\)'"):
        parse(
            """
            fn f(a: any) {
                with (n = !(a as int32)) return;
            }
            """
        )


def test_comma_multi_type_pattern_is_rejected():
    with pytest.raises(
        LangError,
        match=r"a with pattern tests exactly one type; dispatch over "
              r"several with case type",
    ):
        parse(
            """
            fn f(a: any) {
                with (n = a as int32, int16) return;
            }
            """
        )


# --------------------------------------------------------- subject checking

def test_non_any_subject_is_a_compile_error():
    # Outside a with head `as` keeps its cast meaning; the checked test
    # needs a box to test.
    with pytest.raises(
        LangError,
        match=r"with needs an any \(or any\*\) subject, got int32; "
              r"'as' on a non-any is a cast",
    ):
        compile_ir(
            """
            fn main() -> int32 {
                let x: int32 = 5;
                with (n = x as int32) return n;
                return 0;
            }
            """
        )


# --------------------------------------------------------- concrete pattern

def test_concrete_pattern_hit_binds_the_value():
    assert run(
        """
        fn main() -> int32 {
            let a: any = 42;
            with (n = a as int32) return n;
            return -1;
        }
        """
    ) == 42


def test_concrete_pattern_miss_takes_the_else():
    assert run(
        """
        fn main() -> int32 {
            let a: any = 2.5;
            with (n = a as int32) return 1;
            else return 0;
            return 2;
        }
        """
    ) == 0


def test_braced_bodies_and_else_block():
    assert run(
        """
        fn f(a: any) -> int32 {
            let out: int32 = 0;
            with (n = a as int32) { out = n; }
            else { out = -1; }
            return out;
        }
        fn main() -> int32 {
            if (f(7) != 7)    { return 1; }
            if (f(2.5) != -1) { return 2; }
            return 0;
        }
        """
    ) == 0


def test_lone_with_falls_through_doing_nothing():
    # No else: an unmatched tag is defined fall-through, not a trap.
    assert run(
        """
        fn main() -> int32 {
            let a: any = 2.5;
            let out: int32 = 0;
            with (n = a as int32) out = n;
            return out;
        }
        """
    ) == 0


def test_zero_filled_any_takes_the_else():
    # An uninitialized global any is zero-filled: tag 0, which no type name
    # hashes to, so the pattern never matches.
    assert run(
        """
        @static let g: any;
        fn main() -> int32 {
            with (n = g as int32) return 1;
            else return 0;
            return 2;
        }
        """
    ) == 0


# ---------------------------------------------------------- generic pattern

def test_generic_pattern_monomorphizes_per_boxed_tag():
    # An unresolved bare name is an arm-scoped type parameter, exactly as
    # in a generic case type arm: one body copy per boxed tag.
    assert run(
        """
        fn width(a: any) -> int32 {
            with (v = a as T) return sizeof(T) as int32;
            return 0;
        }
        fn main() -> int32 {
            let s: char* = "hi";
            if (width(5) != 4)   { return 1; }  // T = int32
            if (width(2.5) != 8) { return 2; }  // T = float64
            if (width(s) != 8)   { return 3; }  // T = char* (a pointer tag)
            return 0;
        }
        """
    ) == 0


def test_typename_resolves_per_instantiation_inside_the_body(capfd):
    assert run(
        """
        import "libc/stdio";
        fn name(a: any) {
            with (v = a as T) printf("%s\\n", typename(T));
            else printf("none\\n");
        }
        fn main() -> int32 {
            name(5);
            name(2.5);
            return 0;
        }
        """
    ) == 0
    assert capfd.readouterr().out == "int32\nfloat64\n"


def test_pointer_pattern_matches_only_pointer_tags():
    # `T*` takes every boxed pointer tag, T bound to the pointee; non-
    # pointer tags fall to the else.
    assert run(
        """
        struct point { x: int32; y: int32; }
        fn kind(a: any) -> int32 {
            with (ptr = a as T*) return 100 + sizeof(T) as int32;
            else return -1;
            return -2;  // a generic pattern is assumed to reach the end
        }
        fn main() -> int32 {
            let x: int32 = 7;
            let p = struct point { x = 1, y = 2 };
            if (kind(&x) != 104) { return 1; }
            if (kind(&p) != 108) { return 2; }
            if (kind(5) != -1)   { return 3; }
            return 0;
        }
        """
    ) == 0


def test_body_that_fails_for_a_boxed_type_names_it():
    # Every monomorphized copy is fully type-checked; a boxed tag the body
    # doesn't compile for fails the compile, the note naming the type and
    # the construct the user wrote.
    with pytest.raises(LangError) as exc:
        compile_ir(
            """
            fn describe(n: int32) -> int32 { return 1; }
            fn classify(a: any) -> int32 {
                with (v = a as T) return describe(v);
                return 0;
            }
            fn main() -> int32 {
                let a: any = 2.5;
                return classify(a);
            }
            """
        )
    assert any(
        "in with pattern for float64" == note.message
        for note in exc.value.notes
    )


# ----------------------------------------------------------------- scoping

def test_binding_is_not_visible_in_the_else_branch():
    with pytest.raises(LangError, match=r"undefined variable 'n'"):
        compile_ir(
            """
            fn main() -> int32 {
                let a: any = 1;
                with (n = a as int32) return n;
                else return n;
                return 0;
            }
            """
        )


def test_binding_is_not_visible_after_the_statement():
    with pytest.raises(LangError, match=r"undefined variable 'n'"):
        compile_ir(
            """
            fn main() -> int32 {
                let a: any = 1;
                with (n = a as int32) { }
                return n;
            }
            """
        )


def test_binding_is_a_fresh_copy_scoped_to_the_true_branch():
    # Writing the binding does not write the box, like a case type arm's.
    assert run(
        """
        fn main() -> int32 {
            let a: any = 10;
            with (n = a as int32) n = 99;
            with (n = a as int32) return n;
            return -1;
        }
        """
    ) == 10


# -------------------------------------------------------------- composition

def test_nested_with_unwraps_two_boxes():
    assert run(
        """
        fn add(a: any, b: any) -> int32 {
            with (x = a as int32) {
                with (y = b as int32) return x + y;
                else return -2;
            }
            else return -1;
            return -3;
        }
        fn main() -> int32 {
            if (add(2, 3) != 5)    { return 1; }
            if (add(2, 2.5) != -2) { return 2; }
            if (add(2.5, 3) != -1) { return 3; }
            return 0;
        }
        """
    ) == 0


def test_with_inside_a_generic_function_stays_concrete():
    # Inside a generic function the enclosing T resolves, so the pattern
    # is a concrete per-instantiation tag test -- the same rule as a
    # `when T v:` arm there.
    assert run(
        """
        fn is_a<T>(a: any) -> int32 {
            with (v = a as T) return 1;
            else return 0;
            return -1;
        }
        fn main() -> int32 {
            let a: any = 5;
            if (is_a<int32>(a) != 1)   { return 1; }
            if (is_a<float64>(a) != 0) { return 2; }
            return 0;
        }
        """
    ) == 0


def test_with_else_inside_a_case_arm_leaves_the_case_else_alone():
    # A with's `else` (no colon) is consumed by the with; the enclosing
    # case's `else:` arm still belongs to the case.
    assert run(
        """
        fn f(sel: int32, a: any) -> int32 {
            case (sel) {
                when 1:
                    with (n = a as int32) return n;
                    else return -1;
                else:
                    return -2;
            }
            return -3;
        }
        fn main() -> int32 {
            if (f(1, 42) != 42)  { return 1; }
            if (f(1, 2.5) != -1) { return 2; }
            if (f(9, 42) != -2)  { return 3; }
            return 0;
        }
        """
    ) == 0


def test_any_pointer_subject_auto_dereferences():
    # An any* subject reads the box through the pointer, per the case type
    # precedent.
    assert run(
        """
        fn get(p: any*) -> int32 {
            with (n = *p as int32) return n;
            return -1;
        }
        fn main() -> int32 {
            let a: any = 8;
            return get(&a) == 8 ? 0 : 1;
        }
        """
    ) == 0


def test_execution_continues_after_a_non_diverging_with():
    assert run(
        """
        fn score(a: any) -> int32 {
            let out: int32 = 0;
            with (n = a as int32) out = n;
            else out = -1;
            return out + 1;
        }
        fn main() -> int32 {
            if (score(5) != 6)   { return 1; }
            if (score(2.5) != 0) { return 2; }
            return 0;
        }
        """
    ) == 0
