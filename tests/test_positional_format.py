"""Positional `{n}` format placeholders: compile-time sugar over `{}`.

A parameter marked `@format` (the `slice<const char>` just before a
collecting `args...`; `std/io` marks `print`/`println`/`format_args`) opts a
function's format string into the desugar: when the argument bound to it is
a string *literal*, `{n}` placeholders select the collected arguments
manually and the call rewrites to the sequential runtime form --
`println("{0}, {0}", x)` becomes `println("{}, {}", x, x)`, duplicating or
reordering the once-evaluated arguments, so the runtime parser stays
sequential-only. In the positional form a `:` separates the index from the
modifiers (`{0:x}` -> `{x}`), the index-less `{:mods}` escape spells a bare
all-digit runtime modifier (`{:2}` -> the `{2}` field width the positional
grammar would otherwise claim), and one string commits to one placeholder
style. The desugar runs on both marshal paths -- the direct call and the
overload/generic winner emission -- and a non-literal format string is
untouched (runtime behavior unchanged).
"""

import pytest

from mcc.codegen import CodeGen
from mcc.driver import emit_interface
from mcc.errors import LangError
from mcc.interface import render_interface
from mcc.lexer import tokenize
from mcc.parser import Parser
from helpers import compile_ir, run, run_path

IO = 'import "std/io";\n'


# ----------------------------------------------------------------- desugar

def test_positional_matches_the_equivalent_sequential_call(capfd):
    # `{n}` duplicates and reorders at compile time; the sequential spelling
    # of the same selection renders identically.
    run(
        IO
        + """
        fn main() -> int32 {
            println("{0}, {0}", 42);
            println("{}, {}", 42, 42);
            println("{1} beats {0}", "rock", "paper");
            println("{} beats {}", "paper", "rock");
            return 0;
        }
        """
    )
    out = capfd.readouterr().out
    assert out == "42, 42\n42, 42\npaper beats rock\npaper beats rock\n"


def test_a_duplicated_argument_evaluates_once(capfd):
    # The slot map re-boxes the once-evaluated value; the expression's side
    # effect must not run twice.
    run(
        IO
        + """
        fn side() -> int32 {
            println("side!");
            return 7;
        }
        fn main() -> int32 {
            println("{0} {0} {0}", side());
            return 0;
        }
        """
    )
    assert capfd.readouterr().out == "side!\n7 7 7\n"


def test_extras_evaluate_in_source_order_then_map(capfd):
    # Reordering happens on the evaluated values: f() still runs before g().
    run(
        IO
        + """
        fn mark(c: char, v: int32) -> int32 {
            print("{}", c);
            return v;
        }
        fn main() -> int32 {
            println(" -> {1} {0}", mark('f', 1), mark('g', 2));
            return 0;
        }
        """
    )
    assert capfd.readouterr().out == "fg -> 2 1\n"


def test_a_colon_separates_the_index_from_the_modifiers(capfd):
    # `{0:x}` desugars to `{x}`; an empty modifier (`{0:}`) is just `{0}`.
    run(
        IO
        + """
        fn main() -> int32 {
            println("{0} {0:x} {0:08x} {0:}", 255);
            return 0;
        }
        """
    )
    assert capfd.readouterr().out == "255 ff 000000ff 255\n"


def test_the_index_less_escape_spells_a_bare_width(capfd):
    # `{:2}` desugars to the runtime `{2}` field width the positional
    # grammar now claims -- the while.mc table spelling.
    run(
        IO
        + """
        fn main() -> int32 {
            print("{:2}|", 7);
            print("{:08}|", 5);
            println("");
            return 0;
        }
        """
    )
    assert capfd.readouterr().out == " 7|00000005|\n"


def test_digit_leading_runtime_modifiers_pass_through(capfd):
    # Only all-digit content (and a `:`) is claimed; `{06x}`/`{0x}` stay the
    # runtime integer modifiers they always were.
    run(
        IO
        + """
        fn main() -> int32 {
            println("{06x} {0x}", 255, 255);
            return 0;
        }
        """
    )
    assert capfd.readouterr().out == "0000ff ff\n"


def test_escaped_braces_render_literally(capfd):
    # `{{0}}` is a literal `{0}`, consumes no argument, and does not make
    # the string positional-style.
    run(
        IO
        + """
        fn main() -> int32 {
            println("{{0}} = {0}", 7);
            return 0;
        }
        """
    )
    assert capfd.readouterr().out == "{0} = 7\n"


def test_an_unclosed_brace_passes_through_verbatim(capfd):
    # The runtime parser silently discards a trailing unclosed `{mod`; the
    # scanner leaves the text verbatim so that behavior is unchanged.
    run(
        IO
        + """
        fn main() -> int32 {
            println("{0} {d", 1);
            return 0;
        }
        """
    )
    assert capfd.readouterr().out == "1 \n"


def test_an_escape_interleaved_span_passes_through_verbatim(capfd):
    # `"}{0}}"` is another unspecified runtime edge: the leading stray `}`
    # arms the `}}` escape, so the first `}` after `{0` emits a literal `}`
    # mid-placeholder and the second closes it. The scanner leaves such a
    # span verbatim and unclassified -- runtime behavior (modifier "0", a
    # zero-pad flag with no width) is unchanged, pinned, not promised.
    run(
        IO
        + """
        fn main() -> int32 {
            println("}{0}}", 1);
            return 0;
        }
        """
    )
    assert capfd.readouterr().out == "}1\n"


def test_a_pass_through_slice_counts_as_one_argument(capfd):
    # Inside a collecting body `args` is already a slice<const any>: it is
    # ONE source argument, `{0}` selects it, and the pass-through rule still
    # hands it over uncollected. The desugared string has one `{}`, so it
    # renders the slice's first element and the rest fall to the runtime's
    # silent excess-argument edge -- `{0} {1}` would be out of range, not a
    # per-element selection.
    run(
        IO
        + """
        fn relay(@format const fmt: slice<const char>, args...) {
            println("{0}", args);
        }
        fn main() -> int32 {
            relay("{} {}", 3, 4);
            return 0;
        }
        """
    )
    assert capfd.readouterr().out == "3\n"


def test_a_variable_format_string_is_untouched(capfd):
    # No literal, no desugar: the runtime parser sees `{0}` as the integer
    # modifier it always was (a zero-pad flag with no width) -- pinned as
    # today's behavior, not a promise.
    run(
        IO
        + """
        fn main() -> int32 {
            let fmt: slice<const char> = "{0}!";
            println(fmt, 42);
            return 0;
        }
        """
    )
    assert capfd.readouterr().out == "42!\n"


# --------------------------------------------- the overload/generic path

def test_positional_desugars_through_an_overload_set(capfd):
    # Set members route through winner emission, not marshal_args -- the
    # parity site: duplication, reordering, and single evaluation must
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
            logf("{1}-{0}", "x", "y");
            logf(2, "{0} {0:x}", 255);
            logf("{0} {0}", side());
            return 0;
        }
        """
    )
    out = capfd.readouterr().out
    assert out == "p: y-x\nl2: 255 ff\nside!\np: 9 9\n"


def test_positional_desugars_through_a_generic_collector(capfd):
    # A collecting template's @format is validated and desugared per
    # instantiation; the fresh literal never rewrites the template's AST,
    # so a second instantiation desugars again from the original.
    run(
        IO
        + """
        fn tag<T>(prefix: T, @format const fmt: slice<const char>, args...) {
            print("{}: ", prefix);
            print(fmt, args);
            println("");
        }
        fn main() -> int32 {
            tag('a', "{0}/{0}", 1);
            tag(2 as int32, "{1} {0}", 'x', 'y');
            return 0;
        }
        """
    )
    assert capfd.readouterr().out == "a: 1/1\n2: y x\n"


def test_set_path_out_of_range_matches_the_direct_path():
    with pytest.raises(
        LangError,
        match=r"positional placeholder \{3\} is out of range: 'logf' has "
        r"2 argument\(s\) after the format string",
    ):
        compile_ir(
            "fn logf(@format const fmt: slice<const char>, args...) {}\n"
            "fn logf(l: int32, @format const fmt: slice<const char>, args...) {}\n"
            'fn main() -> int32 { logf("{3}", 1, 2); return 0; }'
        )


# ------------------------------------------------------------ diagnostics

def test_out_of_range_index_with_the_width_hint():
    # A bare `{2}` used to be a field width, so the error points at the
    # `{:2}` spelling.
    with pytest.raises(
        LangError,
        match=r"positional placeholder \{2\} is out of range: 'println' has "
        r"1 argument\(s\) after the format string "
        r"\(for a field width, write \{:2\}\)",
    ):
        compile_ir(IO + 'fn main() -> int32 { println("{2}", 1); return 0; }')


def test_out_of_range_index_with_modifiers_gets_no_width_hint():
    # `{2:x}` is unambiguously positional; the hint would mislead.
    with pytest.raises(
        LangError,
        match=r"positional placeholder \{2\} is out of range: 'println' has "
        r"1 argument\(s\) after the format string$",
    ):
        compile_ir(IO + 'fn main() -> int32 { println("{2:x}", 1); return 0; }')


def test_mixing_automatic_and_positional_is_an_error():
    with pytest.raises(
        LangError,
        match=r"format string mixes automatic '\{\}' and positional "
        r"'\{0\}' placeholders",
    ):
        compile_ir(
            IO + 'fn main() -> int32 { println("{0} {}", 1); return 0; }'
        )


def test_the_escape_counts_as_automatic_style():
    with pytest.raises(
        LangError,
        match=r"format string mixes automatic '\{:2\}' and positional "
        r"'\{0\}' placeholders",
    ):
        compile_ir(
            IO + 'fn main() -> int32 { println("{:2} {0}", 1); return 0; }'
        )


def test_an_unreferenced_argument_is_an_error():
    # Manual mode must reference every collected argument (argument 3 is
    # the second one after the format string).
    with pytest.raises(
        LangError,
        match=r"argument 3 of 'println' is never referenced by the format "
        r"string",
    ):
        compile_ir(
            IO + 'fn main() -> int32 { println("{0}", 1, 2); return 0; }'
        )


def test_a_pass_through_slice_makes_a_second_index_out_of_range():
    # The explicit slice is one source argument, so `{1}` selects nothing.
    with pytest.raises(
        LangError,
        match=r"positional placeholder \{1\} is out of range: 'println' has "
        r"1 argument\(s\) after the format string",
    ):
        compile_ir(
            IO
            + "fn relay(@format const fmt: slice<const char>, args...) {\n"
            '    println("{1}", args);\n'
            "}\n"
            'fn main() -> int32 { relay("{}", 1); return 0; }'
        )


def test_a_colon_without_an_index_before_it_is_an_error():
    # The colon is reserved in @format literals: digits (or nothing, the
    # escape) may precede it, a runtime modifier may not.
    with pytest.raises(
        LangError,
        match=r"expected an argument index before ':' in format "
        r"placeholder \{x:2\}",
    ):
        compile_ir(
            IO + 'fn main() -> int32 { println("{x:2}", 1); return 0; }'
        )


# -------------------------------------------------- the @format attribute

def test_format_requires_a_collecting_function():
    with pytest.raises(
        LangError,
        match=r"@format only applies to a collecting function's format "
        r"string \(declare a trailing 'args\.\.\.'\)",
    ):
        compile_ir("fn f(@format const fmt: slice<const char>) {}")


def test_format_must_mark_the_last_fixed_parameter():
    with pytest.raises(
        LangError,
        match=r"@format only applies to the parameter just before the "
        r"collecting 'args\.\.\.'",
    ):
        compile_ir(
            "fn f(@format const fmt: slice<const char>, x: int32, args...) {}"
        )


def test_format_requires_a_slice_of_const_char():
    with pytest.raises(
        LangError,
        match=r"an @format parameter must be a slice<const char>, not char\*",
    ):
        compile_ir("fn f(@format fmt: char*, args...) {}")


def test_format_is_rejected_on_extern():
    # An @extern never collects, so the collecting requirement rejects it.
    with pytest.raises(
        LangError,
        match=r"@format only applies to a collecting function's format string",
    ):
        compile_ir("@extern fn f(@format fmt: char*, ...);")


def test_a_generic_format_is_validated_per_instantiation():
    # The marked parameter's type may name a type parameter; the shape check
    # runs once the instance's types resolve.
    with pytest.raises(
        LangError,
        match=r"an @format parameter must be a slice<const char>, not int32",
    ):
        compile_ir(
            "fn f<T>(@format fmt: T, args...) {}\n"
            "fn main() -> int32 { f(1 as int32, 2); return 0; }"
        )


def test_format_cannot_be_mut():
    with pytest.raises(
        LangError,
        match=r"a parameter cannot be both @format and mut "
        r"\(a format string is read, never written\)",
    ):
        compile_ir("fn f(@format mut fmt: slice<const char>, args...) {}")


def test_the_collecting_sugar_rejects_format():
    with pytest.raises(
        LangError,
        match=r"'args\.\.\.' cannot take const, mut, @noalias, @nonnull, "
        r"or @format",
    ):
        compile_ir("fn f(@format args...) {}")


def test_a_prototype_must_match_its_definition_on_format():
    with pytest.raises(
        LangError, match=r"definition of 'f' does not match its prototype"
    ):
        compile_ir(
            "fn f(@format const fmt: slice<const char>, args...);\n"
            "fn f(const fmt: slice<const char>, args...) {}"
        )


# -------------------------------------------------------------- interface

def iface(source: str) -> str:
    """Render the interface stub for an import-free source string."""
    program = Parser(tokenize(source)).parse_program()
    imports = list(program.imports)
    cg = CodeGen(program, "test")
    cg.generate()
    return render_interface(cg, source, imports)


def test_format_round_trips_through_the_prototype():
    out = iface(
        "fn logf(@format const fmt: slice<const char>, args...) {}"
    )
    assert (
        "fn logf(@format const fmt: slice<const char>, "
        "const args: slice<const any>);" in out
    )
    Parser(tokenize(out)).parse_program()  # re-parses cleanly


def test_the_desugar_fires_through_an_mci_stub(tmp_path):
    # The marker travels in the stub, so an importer's call sites desugar
    # (and diagnose) without the definition: the out-of-range error fires
    # at compile time, before any linking.
    lib = tmp_path / "logf.mc"
    lib.write_text(
        "fn logf(@format const fmt: slice<const char>, args...) {}\n"
    )
    out = tmp_path / "logf.mci"
    assert emit_interface(lib, (tmp_path,), None, {}, out) == 0
    assert "@format const fmt" in out.read_text()
    lib.unlink()  # force the import to resolve through the stub
    main = tmp_path / "main.mc"
    main.write_text(
        'import "logf";\n'
        'fn main() -> int32 { logf("{9}", 1); return 0; }\n'
    )
    with pytest.raises(
        LangError, match=r"positional placeholder \{9\} is out of range"
    ):
        run_path(main)
