"""F-string interpolation: `println(f"x = {x}")` and `let s = f"x = {x}";`.

An `f`-prefixed string literal holds `{expr}` holes; the prefix is what
separates the two brace grammars (in a plain literal `{x}` is a runtime
*modifier*, in an f-string it is the *expression* `x`). The literal desugars
at parse time into the sequential runtime form -- the hole expressions
become the collected arguments, `{expr:modifier}` carries the runtime
modifier through (`f"{x:08x}"` renders like `"{08x}".format(x)`), and the
Python-style inspector `f"{n=}"` splices the hole's verbatim source text as
a label ahead of the value. At an `@format` callee's format string the
literal splices at compile time (zero-cost, injection-free); everywhere
else it is a runtime *value* -- the literal renders through a synthesized
`slice::format` call into an `-> own string`, so a let adopts it, an
argument's temporary drops at statement end, a method chains off it, and an
`-> own string` return transfers it. Only the positions a runtime value can
never fill stay compile errors: a compile-time constant and in-place
addressing. `{{`/`}}` escape literal braces, and a hole-free f-string keeps
its f-string identity (it renders, never binding a verbatim overload as a
plain literal would).
"""

import pytest

from mcc.errors import LangError
from helpers import compile_ir, run

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
            println("n is {}".format(n));
            println(f"{n} + {n} = {n + n}");
            println("{} + {} = {}".format(n, n, n + n));
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


def test_a_hole_free_fstring_keeps_format_only_semantics(capfd):
    # A hole-free f-string (only plain text or escaped braces) keeps its
    # f-string identity: bound to an @format call it renders through the
    # sequential runtime -- escapes collapse, and a verbatim overload never
    # steals it. (The misplaced case is covered below.)
    run(
        IO
        + """
        fn main() -> int32 {
            println(f"no holes");
            println(f"{{}}");
            return 0;
        }
        """
    )
    assert capfd.readouterr().out == "no holes\n{}\n"


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
            str.push(',');
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
            print(fmt.format(args));
            println("");
        }
        fn logf(level: int32, @format const fmt: slice<const char>, args...) {
            print(f"l{level}: ");
            print(fmt.format(args));
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
            print(f"{prefix}: ");
            print(fmt.format(args));
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
    # trailing extra to feed. Fires against the resolved @format collector
    # on the direct path (the set-path twin is below).
    with pytest.raises(
        LangError,
        match=r"'logf' takes no arguments after an f-string: the "
        r"placeholders already supply them",
    ):
        compile_ir(
            "fn logf(@format const fmt: slice<const char>, args...) {}\n"
            'fn main() -> int32 { logf(f"{1 as int32}", 2); return 0; }'
        )


def test_extra_arguments_after_an_fstring_at_a_verbatim_callee():
    # println has no @format collector, so the f-string is a string value
    # -- and no println takes (string, int32): the ordinary no-overload
    # error reports the honest signature.
    with pytest.raises(
        LangError,
        match=r"no overload of 'println' with signature "
        r"println\(list<char>, int32\)",
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


# --------------------------------------------- f-strings as string values

# A destructor-carrying probe with its own format member: renders as its id,
# announces its destruction -- the observable for adoption and drop timing.
PROBE = (
    IO
    + """
    struct probe { id: int32; }
    fn probe::constructor(mut self: probe, id: int32) { self.id = id; }
    fn probe::destructor(mut self: probe) { println(f"drop {self.id}"); }
    fn mk(id: int32) -> own probe { return probe(id); }
    fn format(mut str: string, const value: probe, const modifier: slice<char>) {
        format(str, value.id, modifier);
    }
    """
)


def test_a_let_adopts_an_fstring(capfd):
    # Outside an @format slot the literal renders through a synthesized
    # `slice::format` call (-> own string), and the let adopts the
    # obligation exactly as `let s = "...".format(x);` would.
    run(
        IO
        + """
        fn main() -> int32 {
            let x = 255 as int32;
            let s = f"x is {x}, hex {x:08x}";
            println(s);
            let t: string = f"{x + 1=}";
            println(t);
            return 0;
        }
        """
    )
    assert capfd.readouterr().out == "x is 255, hex 000000ff\nx + 1=256\n"


def test_a_zero_hole_fstring_is_a_string_value(capfd):
    # A hole-free f-string still renders (a heap string, the terse
    # constructor); its escapes collapse like any other rendering.
    run(
        IO
        + """
        fn main() -> int32 {
            let h = f"no holes";
            println(h);
            let b = f"{{}}";
            println(b);
            return 0;
        }
        """
    )
    assert capfd.readouterr().out == "no holes\n{}\n"


def test_an_fstring_chains_methods(capfd):
    # An f-string receiver is an rvalue: the evaluate-once spill renders it
    # and the temporary drops when the full chain ends.
    run(
        IO
        + """
        fn main() -> int32 {
            let x = 41 as int32;
            if (f"{x}".equals("41")) println("chain ok");
            return 0;
        }
        """
    )
    assert capfd.readouterr().out == "chain ok\n"


def test_a_return_transfers_in_an_own_function(capfd):
    # `return f"{x}"` in an `-> own string` function is a visible transfer
    # source (the synthesized call chains through), so the caller adopts.
    run(
        IO
        + """
        fn greet(name: slice<const char>) -> own string {
            return f"hello {name}!";
        }
        fn main() -> int32 {
            let g = greet("world");
            println(g);
            return 0;
        }
        """
    )
    assert capfd.readouterr().out == "hello world!\n"


def test_an_fstring_argument_drops_after_the_chain(capfd):
    # Argument position never adopts: the rendered string (and an own hole
    # temporary inside it) is destroyed at statement end, after the callee
    # returns.
    run(
        PROBE
        + """
        fn takes(s: const string) { println(f"takes: {s as slice<char>}"); }
        fn main() -> int32 {
            takes(f"value {mk(3)}");
            println("after");
            return 0;
        }
        """
    )
    out = capfd.readouterr().out
    assert out == "takes: value 3\ndrop 3\nafter\n"


def test_a_discarded_fstring_drops_at_statement_end(capfd):
    run(
        PROBE
        + """
        fn main() -> int32 {
            f"{mk(2)}";
            println("after");
            return 0;
        }
        """
    )
    assert capfd.readouterr().out == "drop 2\nafter\n"


def test_hole_own_temporaries_drop_at_statement_end(capfd):
    # The recorded follow-up, closed: an own call inside a hole is a
    # consumed expression temporary on both marshal paths -- the set path
    # (println's overloads) and the direct path (a single collector).
    run(
        PROBE
        + """
        fn logf(@format const fmt: slice<const char>, args...) {
            print(fmt.format(args));
            println("");
        }
        fn main() -> int32 {
            println(f"set: {mk(1)}");
            println("between");
            logf(f"direct: {mk(2)}");
            println("after");
            return 0;
        }
        """
    )
    out = capfd.readouterr().out
    assert out == "set: 1\ndrop 1\nbetween\ndirect: 2\ndrop 2\nafter\n"


def test_the_format_slot_still_wins_over_value_rendering():
    # With an @format collector in the set, the compile-time splice wins
    # (zero-cost, injection-free) -- the f-string binds the format slot,
    # never the verbatim member a rendered string would prefer.
    ir = compile_ir(
        IO
        + "fn logf(const msg: slice<const char>) {}\n"
        "fn logf(@format const fmt: slice<const char>, args...) {}\n"
        'fn main() -> int32 { logf(f"{1 as int32}"); return 0; }'
    )
    assert 'call void @"logf(slice<const char>, slice<const any>)"' in ir


def test_an_fstring_extra_renders_as_a_value(capfd):
    # Past a plain format string, a collected f-string is an ordinary value
    # argument: it renders to a string and formats as its text, on both
    # marshal paths (and its own temporaries drop at statement end).
    run(
        PROBE
        + """
        fn logf(@format const fmt: slice<const char>, args...) {
            print(fmt.format(args));
            println("");
        }
        fn main() -> int32 {
            let x = 7 as int32;
            println("set: {}".format(f"x={x}"));
            logf("direct: {}", f"p={mk(4)}");
            println("after");
            return 0;
        }
        """
    )
    out = capfd.readouterr().out
    assert out == "set: x=7\ndirect: p=4\ndrop 4\nafter\n"


def test_value_mode_resolves_among_ordinary_overloads(capfd):
    # With no @format candidate, the f-string is a string value and
    # resolution re-runs over the set -- the string-taking member wins,
    # exactly as the spelled-out `take("...".format(x))` would.
    run(
        IO
        + """
        fn take(n: int32) { println("int"); }
        fn take(const s: const string) {
            println(f"string: {s as slice<const char>}");
        }
        fn main() -> int32 {
            take(f"{1 + 1}");
            return 0;
        }
        """
    )
    assert capfd.readouterr().out == "string: 2\n"


def test_an_fstring_borrows_with_an_explicit_as(capfd):
    # The escape hatch into a concrete char-slice position: `as slice<...>`
    # renders and borrows the owned string's leading slice prefix.
    run(
        IO
        + """
        fn f(s: slice<const char>) { println(s); }
        fn main() -> int32 {
            f(f"{6 * 7} borrowed" as slice<const char>);
            let v: slice<const char> = f"{1 + 2} bound" as slice<const char>;
            println(v);
            return 0;
        }
        """
    )
    assert capfd.readouterr().out == "42 borrowed\n3 bound\n"


# ------------------------------------------- positions that stay errors

MISPLACED = (
    r"an f-string renders at runtime into an owned string, so it cannot "
    r"form a compile-time constant or be addressed in place; bind it to a "
    r"let first"
)


def test_an_fstring_cannot_initialize_a_const():
    with pytest.raises(LangError, match=MISPLACED):
        compile_ir(
            IO + 'const S = f"{1 as int32}";\nfn main() -> int32 { return 0; }'
        )


def test_an_fstring_cannot_initialize_a_static_global():
    with pytest.raises(LangError, match=MISPLACED):
        compile_ir(
            IO
            + '@static let g: string = f"{1 as int32}";\n'
            "fn main() -> int32 { return 0; }"
        )


def test_an_fstring_cannot_be_addressed_in_place():
    # len() addresses its operand's storage; a runtime rendering has none
    # to offer -- bind it to a let and use .length.
    with pytest.raises(LangError, match=MISPLACED):
        compile_ir(
            IO
            + "fn main() -> int32 { "
            'return len(f"{1 as int32}") as int32; }'
        )


def test_a_concrete_slice_position_reports_the_honest_mismatch():
    # A rendered f-string is a string value, so a concrete char-slice
    # parameter reports the ordinary type mismatch (the fix is the explicit
    # `as` borrow) -- there is no implicit string-to-slice coercion to hide
    # a dangling view behind.
    with pytest.raises(
        LangError,
        match=r"f-string value: expected slice<const char>, got list<char>",
    ):
        compile_ir(
            IO
            + "fn f(s: slice<const char>) {}\n"
            'fn main() -> int32 { f(f"{1 as int32}"); return 0; }'
        )


def test_a_typed_slice_let_reports_the_honest_mismatch():
    with pytest.raises(
        LangError,
        match=r"let s: expected slice<const char>, got list<char>",
    ):
        compile_ir(
            IO
            + "fn main() -> int32 { "
            'let s: slice<const char> = f"{1 as int32}"; return 0; }'
        )


def test_a_non_string_return_reports_the_honest_mismatch():
    with pytest.raises(
        LangError,
        match=r"f-string value: expected slice<const char>, got list<char>",
    ):
        compile_ir(
            IO
            + "fn f() -> slice<const char> { return f\"{1 as int32}\"; }\n"
            "fn main() -> int32 { return 0; }"
        )


def test_a_value_fstring_needs_the_renderer_imported():
    # Without std/slice in the import graph there is no `slice::format` to
    # render through; the miss names the import instead of a bare
    # undefined-function error.
    with pytest.raises(
        LangError,
        match=r"an f-string used as a value renders through 'slice::format'; "
        r'import "std/slice" \(or "std/io", which pulls it in\) to build '
        r"the string",
    ):
        compile_ir(
            'fn main() -> int32 { let s = f"{1 as int32}"; return 0; }'
        )
