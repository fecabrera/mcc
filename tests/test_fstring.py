"""F-string interpolation: `println(f"x = {x}")` as parse-time sugar.

An `f`-prefixed string literal holds `{expr}` holes; the prefix is what
separates the two brace grammars (in a plain literal `{x}` is a runtime
*modifier*, in an f-string it is the *expression* `x`). The literal desugars
at parse time into the sequential runtime form -- the hole expressions
become the collected arguments, `{expr:modifier}` carries the runtime
modifier through (`f"{x:08x}"` renders like `println("{08x}", x)`), and the
Python-style inspector `f"{n=}"` splices the hole's verbatim source text as
a label ahead of the value. An f-string is legal only as the format string
of an `@format` call (both marshal paths); every other sink -- a let, a
plain parameter, a const -- is a compile error, so a misplaced f-string can
never silently drop its holes. `{{`/`}}` escape literal braces, and a
hole-free f-string degrades to a plain string literal at parse time.
"""

import pytest

from mcc.codegen import CodeGen
from mcc.driver import STDLIB_DIR, merge_imports
from mcc.errors import LangError
from helpers import compile_ir, parse, run

IO = 'import "std/io";\n'


# ------------------------------------------------------------ basic holes

def test_holes_render_like_the_sequential_call(capfd):
    # Each hole becomes a `{}` placeholder plus a collected argument; the
    # hand-desugared spelling renders identically.
    run(
        IO
        + """
        fn main() -> int32 {
            let n = 7 as int32;
            println(f"n is {n}");
            println("n is {}", n);
            println(f"{n} + {n} = {n + n}");
            println("{} + {} = {}", n, n, n + n);
            return 0;
        }
        """
    )
    out = capfd.readouterr().out
    assert out == "n is 7\nn is 7\n7 + 7 = 14\n7 + 7 = 14\n"


def test_a_modifier_rides_after_a_colon(capfd):
    # `{expr:mods}` splits at the first colon left over once the expression
    # is parsed -- so a ternary's own colon stays inside the expression --
    # and the modifier text passes to the runtime verbatim.
    run(
        IO
        + """
        fn main() -> int32 {
            let x = 255 as int32;
            println(f"{x:x} {x:08x} {x:}");
            println(f"{x > 9 ? x : 0} {x > 9 ? x : 0:04}");
            return 0;
        }
        """
    )
    assert capfd.readouterr().out == "ff 000000ff 255\n255 0255\n"


def test_a_literal_zero_hole_is_the_expression_zero(capfd):
    # `{0}` is the integer expression 0, not a positional index: an
    # f-string is its own placeholder style.
    run(IO + 'fn main() -> int32 { println(f"{0} {1 + 2}"); return 0; }')
    assert capfd.readouterr().out == "0 3\n"


def test_escaped_braces_render_literally(capfd):
    run(
        IO
        + """
        fn main() -> int32 {
            let n = 7 as int32;
            println(f"{{n}} is {n}, {{{n}}}");
            println(f"{{}}");
            return 0;
        }
        """
    )
    assert capfd.readouterr().out == "{n} is 7, {7}\n{}\n"


def test_each_hole_evaluates_exactly_once(capfd):
    run(
        IO
        + """
        fn side() -> int32 {
            println("side!");
            return 7;
        }
        fn main() -> int32 {
            println(f"{side()} {side()}");
            return 0;
        }
        """
    )
    assert capfd.readouterr().out == "side!\nside!\n7 7\n"


def test_a_hole_free_fstring_degrades_to_a_plain_literal(capfd):
    # No holes, no FStrLit: the text (in runtime format syntax, escapes
    # kept) is an ordinary string literal, legal at any sink.
    run(
        IO
        + """
        fn main() -> int32 {
            let s: slice<const char> = f"plain";
            println("{}", s);
            println(f"no holes");
            return 0;
        }
        """
    )
    assert capfd.readouterr().out == "plain\nno holes\n"


# -------------------------------------------------------------- inspector

def test_the_inspector_prints_the_expression_source(capfd):
    # Python's `{n=}`: the label is the hole's verbatim text up to and
    # including the `=`, whitespace preserved, no synthesized spaces.
    run(
        IO
        + """
        fn main() -> int32 {
            let n = 7 as int32;
            println(f"{n=}");
            println(f"{n = }");
            println(f"{n + 1=}");
            return 0;
        }
        """
    )
    assert capfd.readouterr().out == "n=7\nn = 7\nn + 1=8\n"


def test_the_inspector_composes_with_a_modifier(capfd):
    # Python's order: the `=` before the colon -- the label stops at the
    # colon, the modifier steers the value's rendering.
    run(
        IO
        + """
        fn main() -> int32 {
            let x = 255 as int32;
            println(f"{x=:08x}");
            println(f"{x = :x}");
            return 0;
        }
        """
    )
    assert capfd.readouterr().out == "x=000000ff\nx = ff\n"


def test_equality_in_a_hole_is_not_an_inspector(capfd):
    # `==` is consumed by the expression parse; only a leftover `=` marks
    # the inspector, and `{n == 7=}` labels the comparison itself.
    run(
        IO
        + """
        fn main() -> int32 {
            let n = 7 as int32;
            println(f"{n == 7} {n != 7}");
            println(f"{n == 7=}");
            return 0;
        }
        """
    )
    assert capfd.readouterr().out == "true false\nn == 7=true\n"


def test_braces_in_an_inspector_label_re_escape(capfd):
    # The label splices into format text, so a struct literal's braces in
    # the spelling must come out literal, not as placeholders.
    run(
        IO
        + """
        struct point { x: int32; y: int32; }
        fn main() -> int32 {
            println(f"{point { x = 3, y = 4 }.x=}");
            return 0;
        }
        """
    )
    assert capfd.readouterr().out == "point { x = 3, y = 4 }.x=3\n"


# --------------------------------------------------- holes are expressions

def test_string_and_char_literals_nest_in_holes(capfd):
    # The hole scan is quote-aware: a `}` inside a nested string or char
    # literal never closes the hole (the outer quotes are `\"` escapes).
    run(
        IO
        + r"""
        fn main() -> int32 {
            let s = "hi";
            println(f"{s == \"}\"} {'}'}");
            return 0;
        }
        """
    )
    assert capfd.readouterr().out == "false }\n"


def test_a_struct_literal_expression_in_a_hole(capfd):
    # Depth counting keeps the literal's braces inside the hole.
    run(
        IO
        + """
        struct point { x: int32; y: int32; }
        fn format(mut str: string, value: struct point*, const modifier: slice<char>) {
            format(str, value!->x, modifier);
            string_push(str, ',');
            format(str, value!->y, modifier);
        }
        fn main() -> int32 {
            let p = point { x = 1, y = 2 };
            println(f"{ point { x = 5, y = 6 }.y } {&p}");
            return 0;
        }
        """
    )
    assert capfd.readouterr().out == "6 1,2\n"


# --------------------------------------------- the overload/generic path

def test_fstrings_desugar_through_an_overload_set(capfd):
    # Set members route through winner emission, not marshal_args -- the
    # parity site: holes, inspector, modifier, and single evaluation must
    # match the direct path.
    run(
        IO
        + """
        fn logf(@format const fmt: slice<const char>, args...) {
            print("p: ");
            print(fmt, args);
            println("");
        }
        fn logf(level: int32, @format const fmt: slice<const char>, args...) {
            print("l{}: ", level);
            print(fmt, args);
            println("");
        }
        fn side() -> int32 {
            println("side!");
            return 9;
        }
        fn main() -> int32 {
            let x = 255 as int32;
            logf(f"{x=} {x:x}");
            logf(2, f"{x = }");
            logf(f"{side()}");
            return 0;
        }
        """
    )
    out = capfd.readouterr().out
    assert out == "p: x=255 ff\nl2: x = 255\nside!\np: 9\n"


def test_fstrings_desugar_through_a_generic_collector(capfd):
    # A collecting template substitutes per instantiation from the original
    # FStrLit -- never an AST rewrite.
    run(
        IO
        + """
        fn tag<T>(prefix: T, @format const fmt: slice<const char>, args...) {
            print("{}: ", prefix);
            print(fmt, args);
            println("");
        }
        fn main() -> int32 {
            let n = 1 as int32;
            tag('a', f"{n=}");
            tag(2 as int32, f"{n + 1}");
            return 0;
        }
        """
    )
    assert capfd.readouterr().out == "a: n=1\n2: 2\n"


def test_set_path_extra_arguments_match_the_direct_path():
    with pytest.raises(
        LangError,
        match=r"'logf' takes no arguments after an f-string: the "
        r"placeholders already supply them",
    ):
        compile_ir(
            "fn logf(@format const fmt: slice<const char>, args...) {}\n"
            "fn logf(l: int32, @format const fmt: slice<const char>, "
            "args...) {}\n"
            'fn main() -> int32 { logf(f"{1 as int32}", 2); return 0; }'
        )


# ------------------------------------------------------------ diagnostics

def test_extra_arguments_after_an_fstring_are_an_error():
    # The holes are the arguments; there is no next placeholder for a
    # trailing extra to feed.
    with pytest.raises(
        LangError,
        match=r"'println' takes no arguments after an f-string: the "
        r"placeholders already supply them",
    ):
        compile_ir(
            IO + 'fn main() -> int32 { println(f"{1 as int32}", 2); return 0; }'
        )


def test_an_empty_hole_is_an_error():
    with pytest.raises(
        LangError, match=r"empty expression in f-string placeholder \{\}"
    ):
        compile_ir(IO + 'fn main() -> int32 { println(f"{}"); return 0; }')


def test_a_bare_modifier_hole_is_an_error():
    # `{:mods}` has no expression to render; an f-string is its own style,
    # so the positional escape spelling does not apply.
    with pytest.raises(
        LangError, match=r"empty expression in f-string placeholder \{:08x\}"
    ):
        compile_ir(IO + 'fn main() -> int32 { println(f"{:08x}"); return 0; }')


def test_a_bare_inspector_hole_is_an_error():
    with pytest.raises(
        LangError, match=r"empty expression in f-string placeholder \{=\}"
    ):
        compile_ir(IO + 'fn main() -> int32 { println(f"{=}"); return 0; }')


def test_a_stray_close_brace_is_an_error():
    with pytest.raises(
        LangError, match=r"single '\}' in f-string \(write \}\} for a literal '\}'\)"
    ):
        compile_ir(IO + 'fn main() -> int32 { println(f"a } b"); return 0; }')


def test_an_unclosed_hole_is_an_error():
    with pytest.raises(
        LangError,
        match=r"unclosed '\{' in f-string \(write \{\{ for a literal '\{'\)",
    ):
        compile_ir(IO + 'fn main() -> int32 { println(f"a {n"); return 0; }')


def test_trailing_junk_in_a_hole_is_an_error():
    with pytest.raises(
        LangError, match=r"unexpected '2' in f-string placeholder \{1 2\}"
    ):
        compile_ir(IO + 'fn main() -> int32 { println(f"{1 2}"); return 0; }')


def test_a_newline_escape_in_a_hole_is_an_error():
    # The literal's own \n unescapes to a real newline the hole's sub-lexer
    # cannot take; the escape must be doubled to reach a nested literal.
    with pytest.raises(
        LangError,
        match=r"a \\n escape inside an f-string placeholder becomes a real "
        r"newline; write \\\\n to put the escape in a nested string literal",
    ):
        compile_ir(
            IO + r'fn main() -> int32 { println(f"{\"a\nb\"}"); return 0; }'
        )


def test_hole_diagnostics_carry_the_literal_line():
    # Sub-parse tokens are re-stamped with the literal's line, so a hole
    # error points at the f-string, not at line 1 of the hole text.
    with pytest.raises(LangError, match=r"line 3: empty expression") as err:
        compile_ir(
            IO + 'fn main() -> int32 {\n    println(f"{}");\n    return 0;\n}'
        )
    assert err.value.line == 3


# ------------------------------------------------------ the sink rule

MISPLACED = (
    r"an f-string is only allowed as the format string of an @format call "
    r"like 'println' or 'format_args'"
)


def test_an_fstring_cannot_initialize_a_variable():
    # String-valued f-strings are a deliberate non-goal for now: the holes
    # would need a runtime buffer to render into.
    with pytest.raises(LangError, match=MISPLACED):
        compile_ir(
            IO + 'fn main() -> int32 { let s = f"{1 as int32}"; return 0; }'
        )


def test_an_fstring_cannot_bind_a_typed_slice_let():
    with pytest.raises(LangError, match=MISPLACED):
        compile_ir(
            IO
            + "fn main() -> int32 { "
            'let s: slice<const char> = f"{1 as int32}"; return 0; }'
        )


def test_an_fstring_cannot_pass_to_a_plain_parameter():
    # str_literal_adapts matches it (an FStrLit is a StrLit), but only an
    # @format position may receive one.
    with pytest.raises(LangError, match=MISPLACED):
        compile_ir(
            "fn f(s: slice<const char>) {}\n"
            'fn main() -> int32 { f(f"{1 as int32}"); return 0; }'
        )


def test_an_fstring_cannot_be_a_collected_extra():
    # Past the format string, an f-string is an ordinary argument -- and
    # ordinary sinks reject it.
    with pytest.raises(LangError, match=MISPLACED):
        compile_ir(
            IO
            + 'fn main() -> int32 { println("{}", f"{1 as int32}"); return 0; }'
        )


def test_an_fstring_cannot_be_returned():
    with pytest.raises(LangError, match=MISPLACED):
        compile_ir(
            "fn f() -> slice<const char> { return f\"{1 as int32}\"; }\n"
            "fn main() -> int32 { return 0; }"
        )


def test_an_fstring_cannot_initialize_a_const():
    with pytest.raises(LangError, match=MISPLACED):
        compile_ir(
            'const S = f"{1 as int32}";\nfn main() -> int32 { return 0; }'
        )


def test_an_fstring_is_not_an_ordinary_expression():
    with pytest.raises(LangError, match=MISPLACED):
        compile_ir(
            IO
            + "fn main() -> int32 { "
            'return len(f"{1 as int32}") as int32; }'
        )


def test_the_legacy_printf_println_rejects_fstrings():
    # Under -D PRINTF_PRINTLN=1 println is C-variadic with a char* format
    # -- not @format -- so an f-string has no legal position there.
    program = merge_imports(
        parse(IO + 'fn main() -> int32 { println(f"{1 as int32}"); return 0; }'),
        STDLIB_DIR,
        (STDLIB_DIR,),
    )
    with pytest.raises(LangError, match=MISPLACED):
        CodeGen(program, "test", defines={"PRINTF_PRINTLN": 1}).generate()
