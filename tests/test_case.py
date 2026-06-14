"""case / when: a switch without fall-through."""

import pytest

from mcc.errors import LangError
from mcc.nodes import Case
from helpers import compile_ir, parse, run


def case_in_main(body):
    return run(
        "import \"libc/stdio\";\n"
        "fn main() -> int32 {\n" + body + "\nreturn out; }"
    )


def test_parses_into_a_case_node():
    (func,) = parse(
        "fn main() { case (x) { when 1: f(); when 2: g(); h(); else: z(); } }"
    ).functions
    (node,) = func.body
    assert isinstance(node, Case)
    assert len(node.arms) == 2
    assert len(node.arms[1][1]) == 2  # the `when 2` arm has two statements
    assert len(node.otherwise) == 1


def test_else_is_optional():
    (func,) = parse("fn main() { case (x) { when 0: f(); } }").functions
    (node,) = func.body
    assert node.arms and node.otherwise == []


def test_matching_arm_runs_only_its_own_body():
    # No fall-through: matching `when 1` must not run the `when 2` body.
    status = case_in_main(
        """
        let out: int32 = 0;
        case (1) {
            when 1: out = out + 1;
            when 2: out = out + 100;
            else:   out = out + 1000;
        }
        """
    )
    assert status == 1


def test_else_runs_when_nothing_matches():
    assert case_in_main(
        "let out: int32 = 0; case (7) { when 1: out = 1; else: out = 42; }"
    ) == 42


def test_no_arm_and_no_else_is_a_no_op():
    assert case_in_main(
        "let out: int32 = 5; case (7) { when 1: out = 1; }"
    ) == 5


def test_char_subject(capfd):
    run(
        r"""
        import "libc/stdio";
        fn main() -> int32 {
            let s: uint8* = "a1b";
            let i: uint64 = 0;
            while (s[i] != '\0') {
                case (s[i]) {
                    when 'a': printf("alpha ");
                    when 'b': printf("bravo ");
                    else:     printf("other ");
                }
                i = i + 1;
            }
            return 0;
        }
        """
    )
    assert capfd.readouterr().out == "alpha other bravo "


def test_break_in_an_arm_exits_the_enclosing_loop(capfd):
    run(
        """
        import "libc/stdio";
        fn main() -> int32 {
            let i: int32 = 0;
            while (i < 5) {
                case (i) {
                    when 3: break;       // leaves the while, not just the case
                    else:   printf("%d ", i);
                }
                i = i + 1;
            }
            printf("done");
            return 0;
        }
        """
    )
    assert capfd.readouterr().out == "0 1 2 done"


def test_dangling_else_belongs_to_the_case_not_the_inner_if():
    # The unbraced `if` must not swallow the case's `else:`.
    assert case_in_main(
        """
        let out: int32 = 0;
        case (1) {
            when 1:
                if (out == 0) out = 10;
            else:
                out = 99;
        }
        """
    ) == 10


def test_when_value_must_match_the_subject_type():
    with pytest.raises(LangError, match="when value: expected int32, got uint8\\*"):
        compile_ir('fn main() { let n: int32 = 1; case (n) { when "x": f(); } }')


def test_struct_subject_is_rejected():
    with pytest.raises(LangError, match="cannot match a .* in a case"):
        compile_ir(
            "struct p { x: int32; }\n"
            "fn main() { let s: struct p; case (s) { when 0: f(); } }"
        )


def test_arm_value_adapts_to_a_typed_subject():
    # `when 200` (an untyped constant) adapts to the uint8 subject.
    assert run(
        "fn main() -> int32 { let b: uint8 = 200; "
        "let out: int32 = 0; case (b) { when 200: out = 1; else: out = 2; } "
        "return out; }"
    ) == 1
