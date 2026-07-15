"""Generic overload sets: same name, dispatch by parameter pattern."""

import re

import pytest

from mcc.driver import compile_to_ir
from mcc.errors import LangError
from helpers import compile_ir, run, run_path

VARIANTS = """
fn describe<T>(x: T) -> int32 { return 1; }
fn describe<T>(x: T*) -> int32 { return 2; }
"""


def test_dispatch_by_pointerness():
    assert run(
        VARIANTS
        + """
        fn main() -> int32 {
            let v: int64 = 5;
            let by_value = describe(v);        // T
            let by_pointer = describe(&v);     // T*
            return by_value * 10 + by_pointer;
        }
        """
    ) == 12


def test_more_specific_pattern_wins_for_pointers():
    # A pointer argument matches both T and T*; T* is more specific.
    assert run(
        VARIANTS
        + """
        fn main() -> int32 {
            let s = "hi";
            return describe(s);
        }
        """
    ) == 2


def test_struct_pattern_beats_bare_parameter():
    assert run(
        """
        struct box<T> { value: T; }
        fn pick<T>(x: T) -> int32 { return 1; }
        fn pick<T>(x: box<T>*) -> int32 { return 2; }
        fn main() -> int32 {
            let b: struct box<int32>* = null;
            return pick(b);
        }
        """
    ) == 2


def test_no_matching_overload():
    with pytest.raises(
        LangError, match=r"no overload of 'describe' with signature describe\(int32\)"
    ):
        compile_ir(
            "fn describe<T>(x: T*) -> int32 { return 1; }\n"
            "fn describe<T>(x: T**) -> int32 { return 2; }\n"
            "fn main() -> int32 { return describe(5); }"
        )


def test_ambiguous_overloads():
    # Two pattern-distinct variants with equal specificity tie at the call.
    # (Same-pattern pairs no longer get this far: they are declare-time
    # duplicates, tested below.)
    with pytest.raises(LangError, match="ambiguous"):
        compile_ir(
            "fn f<T>(x: T, y: int32) -> int32 { return 1; }\n"
            "fn f<T>(x: int32, y: T) -> int32 { return 2; }\n"
            "fn main() -> int32 { return f(5 as int32, 6 as int32); }"
        )


def test_overloads_merge_across_files(tmp_path):
    # A second file can extend an imported overload set.
    (tmp_path / "base.mc").write_text("fn measure<T>(x: T) -> int32 { return 1; }")
    main = tmp_path / "main.mc"
    main.write_text(
        'import "base";\n'
        "fn measure<T>(x: T*) -> int32 { return 2; }\n"
        "fn main() -> int32 {\n"
        "    let v: int32 = 0;\n"
        "    return measure(v) * 10 + measure(&v);\n"
        "}"
    )
    assert run_path(main) == 12


def test_hash_lib_dispatches(tmp_path, capfd):
    main = tmp_path / "main.mc"
    main.write_text(
        """
        import "std/hash";
        import "libc/stdio";
        fn main() -> int32 {
            let by_value = hash(99 as uint64) == splitmix64(99);
            let by_content = hash("abc") == fnv1a("abc");
            printf("%d %d\\n", by_value, by_content);
            return 0;
        }
        """
    )
    assert run_path(main) == 0
    assert capfd.readouterr().out == "1 1\n"


# ------------------- template symbol bases (order-independent, per-pattern)


def test_template_base_and_instance_spellings():
    # Every template takes a signature-derived base -- name, type parameters
    # alpha-renamed to positional $i placeholders, parameter patterns -- and
    # an instance appends its bindings. Lone templates included: an imported
    # file may extend the set, so the spelling never depends on set size.
    out = compile_ir(
        VARIANTS
        + """
        fn main() -> int32 {
            let v: int64 = 5;
            return describe(v) * 10 + describe(&v);
        }
        """
    )
    assert 'define i32 @"describe<$0>($0)<int64>"(' in out
    assert 'define i32 @"describe<$0>($0*)<int64>"(' in out


def test_lone_template_takes_a_pattern_base():
    out = compile_ir(
        "fn id<T>(x: T) -> T { return x; }\n"
        "fn main() -> int32 { return id<int32>(7); }"
    )
    assert 'define i32 @"id<$0>($0)<int32>"(' in out
    assert 'define i32 @"id<int32>"(' not in out


def test_fn_type_pattern_substitutes_in_the_template_base():
    # Substitution reaches inside fn(...) -> ... parameter types.
    out = compile_ir(
        "fn apply<T>(f: fn(T) -> T, x: T) -> T { return f(x); }\n"
        "fn inc(x: int32) -> int32 { return x + 1; }\n"
        "fn main() -> int32 { return apply(inc, 41) - 42; }"
    )
    assert 'define i32 @"apply<$0>(fn($0) -> $0, $0)<int32>"(' in out


def test_mut_marker_is_part_of_the_template_base():
    # A same-shape mut/by-value template pair is a genuine overload (an
    # rvalue filters out the mut candidate), so the marker must keep their
    # symbols apart -- unlike concrete sets, where marker-only variants are
    # uncallable duplicates.
    out = compile_ir(
        "fn bump<T>(mut a: T) { a = a + (1 as T); }\n"
        "fn main() -> int32 { let x: int32 = 1; bump(x); return x - 2; }"
    )
    assert 'define void @"bump<$0>(&$0)<int32>"(' in out


def test_defaulted_type_param_is_part_of_the_template_base():
    out = compile_ir(
        "fn size<T = int64>(x: T) -> int64 { return sizeof(T) as int64; }\n"
        "fn main() -> int32 { return (size(0) - 8) as int32; }"
    )
    assert 'define i64 @"size<$0 = int64>($0)<int64>"(' in out


def test_alpha_variant_template_is_a_duplicate():
    # Type parameters alpha-rename in the base, so a renamed copy spells the
    # same pattern -- every call would be ambiguous, so it collides at
    # declaration instead.
    with pytest.raises(LangError) as err:
        compile_ir(
            "fn f<T>(x: T) -> int32 { return 1; }\n"
            "fn f<U>(x: U) -> int32 { return 2; }\n"
            "fn main() -> int32 { return 0; }"
        )
    assert err.value.message == (
        "function 'f<$0>($0)' already defined; overloads must differ in "
        "parameter patterns"
    )


def test_return_type_only_template_variant_is_a_duplicate():
    with pytest.raises(LangError) as err:
        compile_ir(
            "fn f<T>(x: T) -> int32 { return 1; }\n"
            "fn f<T>(x: T) -> int64 { return 2; }\n"
            "fn main() -> int32 { return 0; }"
        )
    assert err.value.message == (
        "function 'f<$0>($0)' already defined; overloads must differ in "
        "parameter patterns"
    )


def test_same_pattern_templates_collide_across_modules(tmp_path):
    # Extending an imported set is legal, but a same-pattern extension is a
    # duplicate in either import order: the pair could only ever produce
    # ambiguous calls (and, separately compiled, one merged symbol).
    (tmp_path / "one.mc").write_text("fn f<T>(x: T) -> int32 { return 1; }")
    (tmp_path / "two.mc").write_text("fn f<U>(x: U) -> int32 { return 2; }")
    for imports in (
        'import "one";\nimport "two";\n',
        'import "two";\nimport "one";\n',
    ):
        main = tmp_path / "main.mc"
        main.write_text(imports + "fn main() -> int32 { return 0; }\n")
        with pytest.raises(
            LangError,
            match=re.escape(
                "function 'f<$0>($0)' already defined; overloads must "
                "differ in parameter patterns"
            ),
        ):
            run_path(main)


def test_type_param_arity_distinguishes_templates():
    # f<$0>($0) and f<$0, $1>($0) are distinct patterns; explicit type
    # arguments dispatch to each.
    assert run(
        "fn f<T>(x: T) -> int32 { return 1; }\n"
        "fn f<T, U>(x: T) -> int32 { return sizeof(U) as int32; }\n"
        "fn main() -> int32 { return f<int32>(0) * 10 + f<int32, int64>(0); }"
    ) == 18


def test_default_spelling_distinguishes_templates():
    # A defaulted parameter spells `$i = <default>` in the base, so the
    # defaulted variant coexists with the undefaulted one -- and is the only
    # viable candidate when the second type argument is omitted.
    assert run(
        "fn f<T, U>(x: T) -> int32 { return 1; }\n"
        "fn f<T, U = int32>(x: T) -> int32 { return 2; }\n"
        "fn main() -> int32 { return f(0 as int64) - 2; }"
    ) == 0


def test_instance_symbols_are_import_order_independent(tmp_path):
    # The recorded hazard behind the pattern bases: with declaration-order
    # bases (name, name#1, ...) two objects that merged one set in different
    # import orders emitted *different templates'* instances under one
    # linkonce_odr symbol. The base is now a fact of the declaration alone.
    (tmp_path / "m1.mc").write_text("fn g<T>(x: T) -> int32 { return 1; }")
    (tmp_path / "m2.mc").write_text("fn g<T>(x: T*) -> int32 { return 2; }")
    symbol = 'define linkonce_odr i32 @"g<$0>($0*)<int32>"('
    for name, imports in (
        ("a.mc", 'import "m1";\nimport "m2";\n'),
        ("b.mc", 'import "m2";\nimport "m1";\n'),
    ):
        main = tmp_path / name
        main.write_text(
            imports
            + "fn main() -> int32 {\n"
            "    let b: int32 = 2;\n"
            "    return g(&b) - 2;\n"
            "}\n"
        )
        assert symbol in str(compile_to_ir(main))
        assert run_path(main) == 0


# ---------------------------------------------------- concrete overload sets

COUNTER = """
struct counter { value: int32; step: int32; }

fn counter_init(mut self: counter) {
    self.value = 0;
    self.step = 1;
}

fn counter_init(mut self: counter, start: int32) {
    self.value = start;
    self.step = 1;
}

fn counter_init(mut self: counter, start: int32, step: int32) {
    self.value = start;
    self.step = step;
}
"""


def test_concrete_family_dispatches_by_arity():
    # The constructor-flavored family from the roadmap: same name, picked by
    # the argument list, writing through the mut receiver.
    assert run(
        COUNTER
        + """
        fn main() -> int32 {
            let a: struct counter;
            let b: struct counter;
            let c: struct counter;
            counter_init(a);
            counter_init(b, 40);
            counter_init(c, 1, 5);
            return a.value + a.step + b.value + c.value + c.step;
        }
        """
    ) == 47


def test_concrete_dispatch_by_argument_type():
    assert run(
        """
        fn tag(x: int32) -> int32 { return 1; }
        fn tag(p: char*) -> int32 { return 2; }
        fn main() -> int32 {
            let n: int32 = 7;
            let s = "hi";
            return tag(n) * 10 + tag(s);
        }
        """
    ) == 12


def test_single_definition_keeps_plain_symbol():
    # A name with one definition never mangles: plain, C-linkable symbol and
    # the direct-call fast path.
    out = compile_ir(
        "fn add(a: int32, b: int32) -> int32 { return a + b; }\n"
        "fn main() -> int32 { return add(1, 2); }"
    )
    assert 'define i32 @"add"(' in out
    assert 'call i32 @"add"(' in out
    assert "add(int32, int32)" not in out


def test_set_members_take_mangled_symbols():
    out = compile_ir(
        "fn tag(x: int32) -> int32 { return 1; }\n"
        "fn tag(p: char*) -> int32 { return 2; }\n"
        "fn main() -> int32 {\n"
        "    let s = \"x\";\n"
        "    return tag(1) + tag(s);\n"
        "}"
    )
    assert 'define i32 @"tag(int32)"(' in out
    assert 'define i32 @"tag(char*)"(' in out
    assert 'define i32 @"tag"(' not in out


def test_width_only_overloads_ambiguous_for_untyped_literal():
    # f(0) between int32 and int64 stays declared-not-guessed: the literal
    # adapts to either width, so neither candidate is more specific.
    with pytest.raises(LangError) as err:
        compile_ir(
            "fn f(x: int32) -> int32 { return 1; }\n"
            "fn f(x: int64) -> int32 { return 2; }\n"
            "fn main() -> int32 { return f(0); }"
        )
    assert err.value.message == "call to 'f' is ambiguous between overloads"


def test_width_ambiguity_resolved_by_cast_or_typed_variable():
    assert run(
        """
        fn f(x: int32) -> int32 { return 1; }
        fn f(x: int64) -> int32 { return 2; }
        fn main() -> int32 {
            let n: int64 = 0;
            return f(0 as int64) * 10 + f(n) * 100 + f(0 as int32);
        }
        """
    ) == 221


def test_return_type_only_variant_is_a_duplicate():
    with pytest.raises(LangError) as err:
        compile_ir(
            "fn f(x: int32) -> int32 { return 1; }\n"
            "fn f(x: int32) -> int64 { return 2; }\n"
            "fn main() -> int32 { return 0; }"
        )
    assert err.value.message == (
        "function 'f(int32)' already defined; overloads must differ in "
        "parameter types"
    )


def test_marker_only_variant_is_a_duplicate():
    # const/mut are callee contracts, not part of the call shape: a same-type
    # mut/non-mut pair is uncallable under the resolution rules, so it stays
    # a duplicate definition (and stays out of the mangle).
    with pytest.raises(LangError) as err:
        compile_ir(
            "fn f(x: int32) -> int32 { return x; }\n"
            "fn f(mut x: int32) -> int32 { x = 0; return x; }\n"
            "fn main() -> int32 { return 0; }"
        )
    assert err.value.message == (
        "function 'f(int32)' already defined; overloads must differ in "
        "parameter types"
    )


def test_annotation_only_variant_is_a_duplicate():
    # @nonnull/@noalias are caller promises about the supplied value; they do
    # not participate in resolution or the mangle.
    for variant in ("@nonnull p: int32*", "@noalias p: int32*"):
        with pytest.raises(LangError) as err:
            compile_ir(
                "fn f(p: int32*) -> int32 { return 0; }\n"
                f"fn f({variant}) -> int32 {{ return 1; }}\n"
                "fn main() -> int32 { return 0; }"
            )
        assert err.value.message == (
            "function 'f(int32*)' already defined; overloads must differ in "
            "parameter types"
        )


def test_main_cannot_be_overloaded():
    with pytest.raises(LangError) as err:
        compile_ir(
            "fn main() -> int32 { return 0; }\n"
            "fn main(code: int32) -> int32 { return code; }"
        )
    assert err.value.message == "function 'main' cannot be overloaded"


def test_variadic_function_cannot_be_overloaded():
    with pytest.raises(LangError) as err:
        compile_ir(
            "fn log(fmt: char*, ...) { }\n"
            "fn log(fmt: char*, n: int32) { }\n"
            "fn main() -> int32 { return 0; }"
        )
    assert err.value.message == "variadic function 'log' cannot be overloaded"


def test_va_list_parameter_cannot_be_overloaded():
    with pytest.raises(LangError) as err:
        compile_ir(
            "fn vlog(ap: va_list) { }\n"
            "fn vlog(n: int32) { }\n"
            "fn main() -> int32 { return 0; }"
        )
    assert err.value.message == (
        "function 'vlog' cannot be overloaded: it takes a va_list parameter"
    )


def test_static_functions_cannot_be_overloaded():
    with pytest.raises(LangError, match="function 'f' already defined"):
        compile_ir(
            "@static fn f(x: int32) -> int32 { return 1; }\n"
            "@static fn f(p: char*) -> int32 { return 2; }\n"
            "fn main() -> int32 { return 0; }"
        )


def test_extern_cannot_join_an_overload_set():
    # Either order: an @extern's C symbol is fixed, so it collides with a
    # set rather than joining it.
    with pytest.raises(LangError, match="function 'f' already defined"):
        compile_ir(
            "@extern fn f(x: int32) -> int32;\n"
            "fn f(p: char*) -> int32 { return 1; }\n"
            "fn f(p: char*, n: int32) -> int32 { return 2; }\n"
            "fn main() -> int32 { return 0; }"
        )
    with pytest.raises(LangError, match="function 'f' already defined"):
        compile_ir(
            "fn f(p: char*) -> int32 { return 1; }\n"
            "fn f(p: char*, n: int32) -> int32 { return 2; }\n"
            "@extern fn f(x: int32) -> int32;\n"
            "fn main() -> int32 { return 0; }"
        )


def test_tombstone_cannot_join_an_overload_set():
    with pytest.raises(LangError, match="function 'f' already defined"):
        compile_ir(
            '@removed("gone") fn f() -> int32;\n'
            "fn f(x: int32) -> int32 { return 1; }\n"
            "fn f(p: char*) -> int32 { return 2; }\n"
            "fn main() -> int32 { return 0; }"
        )
    with pytest.raises(LangError, match="function 'f' already defined"):
        compile_ir(
            "fn f(x: int32) -> int32 { return 1; }\n"
            "fn f(p: char*) -> int32 { return 2; }\n"
            '@removed("gone") fn f() -> int32;\n'
            "fn main() -> int32 { return 0; }"
        )


def test_mixed_set_concrete_beats_generic_on_exact_match():
    # A generic template and concrete definitions share one name in one
    # module: on an exact match the concrete tier wins (rank leads with
    # is-concrete), in both declaration orders.
    for src in (
        "fn f<T>(x: T) -> int32 { return 0; }\n"
        "fn f(x: int32) -> int32 { return 1; }\n"
        "fn f(p: char*) -> int32 { return 2; }\n"
        "fn main() -> int32 { return f(9); }",
        "fn f(x: int32) -> int32 { return 1; }\n"
        "fn f(p: char*) -> int32 { return 2; }\n"
        "fn f<T>(x: T) -> int32 { return 0; }\n"
        "fn main() -> int32 { return f(9); }",
    ):
        assert run(src) == 1


def test_mixed_set_generic_wins_on_non_exact_match():
    # No concrete member fits a bool argument, so the template instantiates.
    assert run(
        "fn f<T>(x: T) -> int32 { return 100; }\n"
        "fn f(x: int32) -> int32 { return 1; }\n"
        "fn f(p: char*) -> int32 { return 2; }\n"
        "fn main() -> int32 { return f(true); }"
    ) == 100


def test_mixed_set_with_single_concrete_keeps_plain_symbol():
    # The symbol choice counts concrete signatures alone: one concrete
    # member beside a template keeps its plain, C-linkable symbol, and the
    # call still dispatches through the set.
    src = (
        "fn f<T>(x: T) -> int32 { return 100; }\n"
        "fn f(x: int32) -> int32 { return 1; }\n"
        "fn main() -> int32 { return f(9) + f(true); }"
    )
    assert run(src) == 101
    out = compile_ir(src)
    assert 'define i32 @"f"(i32' in out


def test_mixed_set_explicit_type_args_select_the_generic():
    # f<...>(...) resolves among the generic candidates; the concrete member
    # is not viable under explicit type arguments.
    assert run(
        "fn f<T>(x: T) -> int32 { return 100; }\n"
        "fn f(x: int32) -> int32 { return 1; }\n"
        "fn main() -> int32 { return f<int32>(9); }"
    ) == 100


def test_mixed_set_same_shape_tie_is_ambiguous():
    # A generic whose effective parameter list ties a concrete one loses to
    # the concrete tier on an exact match -- but two candidates in the SAME
    # tier with equal specificity stay the ambiguity error.
    with pytest.raises(
        LangError, match="call to 'f' is ambiguous between overloads"
    ):
        compile_ir(
            "fn f(x: int32) -> int32 { return 1; }\n"
            "fn f(x: int64) -> int32 { return 2; }\n"
            "fn f<T>(x: T) -> int32 { return 0; }\n"
            "fn main() -> int32 { return f(0); }"
        )


def test_mixed_set_spans_modules(tmp_path):
    # Open sets: a generic template and a concrete function may share a name
    # across modules, in either import order. The concrete keeps its plain
    # symbol and beats the generic on an exact match.
    (tmp_path / "gen.mc").write_text("fn pick<T>(x: T) -> int32 { return 0; }")
    (tmp_path / "conc.mc").write_text(
        "fn pick(x: int32) -> int32 { return 1; }"
    )
    for imports in (
        'import "gen";\nimport "conc";\n',
        'import "conc";\nimport "gen";\n',
    ):
        main = tmp_path / "main.mc"
        main.write_text(
            imports
            + "fn main() -> int32 {\n"
            "    let b = false;\n"
            "    return pick(7) * 10 + pick(b);\n"
            "}\n"
        )
        assert run_path(main) == 10


def test_cross_module_group_template_joins_a_concrete_set(tmp_path):
    # The generic escape hatch: a closed-group template from another module
    # joins a foreign concrete set (mixed sets used to close both sides into
    # one module, so even the template side died at declaration).
    (tmp_path / "conc.mc").write_text(
        "fn pick(p: char*) -> int32 { return 1; }\n"
        "fn pick(b: bool) -> int32 { return 2; }\n"
    )
    (tmp_path / "gen.mc").write_text(
        "fn pick<T: int32 | int64>(x: T) -> int32 { return 3; }"
    )
    main = tmp_path / "main.mc"
    main.write_text(
        'import "conc";\nimport "gen";\n'
        "fn main() -> int32 { return pick(9) * 10 + pick(true); }\n"
    )
    assert run_path(main) == 32


def test_main_cannot_join_a_mixed_set():
    for src in (
        "fn main() -> int32 { return 0; }\nfn main<T>(x: T) -> int32 { return 1; }",
        "fn main<T>(x: T) -> int32 { return 1; }\nfn main() -> int32 { return 0; }",
    ):
        with pytest.raises(
            LangError, match="function 'main' cannot be overloaded"
        ):
            compile_ir(src)


def test_va_list_concrete_cannot_join_a_mixed_set():
    msg = "function 'vlog' cannot be overloaded: it takes a va_list parameter"
    for src in (
        "fn vlog(ap: va_list) { }\n"
        "fn vlog<T>(x: T) { }\n"
        "fn main() -> int32 { return 0; }",
        "fn vlog<T>(x: T) { }\n"
        "fn vlog(ap: va_list) { }\n"
        "fn main() -> int32 { return 0; }",
    ):
        with pytest.raises(LangError, match=re.escape(msg)):
            compile_ir(src)


def test_duplicate_signature_inside_a_set_is_still_a_duplicate():
    # Two definitions of one signature inside a genuine set (a third,
    # distinct signature makes it one) still collide on the shared mangle.
    with pytest.raises(
        LangError,
        match=re.escape(
            "function 'f(int32)' already defined; overloads must differ in "
            "parameter types"
        ),
    ):
        compile_ir(
            "fn f(x: int32) -> int32 { return 1; }\n"
            "fn f(x: int32) -> int64 { return 2; }\n"
            "fn f(p: char*) -> int32 { return 3; }\n"
            "fn main() -> int32 { return 0; }"
        )


def test_generic_still_collides_with_an_extern():
    # An @extern's C symbol is fixed; it never joins a set, mixed included.
    with pytest.raises(LangError, match="function 'f' already defined"):
        compile_ir(
            "@extern fn f(x: int32) -> int32;\n"
            "fn f<T>(x: T) -> int32 { return 0; }\n"
            "fn main() -> int32 { return 0; }"
        )


def test_variadic_concrete_cannot_join_a_mixed_set():
    # The non-overloadables hold whichever side declares first.
    with pytest.raises(
        LangError, match="variadic function 'f' cannot be overloaded"
    ):
        compile_ir(
            "fn f(fmt: char*, ...) -> int32 { return 0; }\n"
            "fn f<T>(x: T) -> int32 { return 1; }\n"
            "fn main() -> int32 { return 0; }"
        )
    with pytest.raises(
        LangError, match="variadic function 'f' cannot be overloaded"
    ):
        compile_ir(
            "fn f<T>(x: T) -> int32 { return 1; }\n"
            "fn f(fmt: char*, ...) -> int32 { return 0; }\n"
            "fn main() -> int32 { return 0; }"
        )


def test_cross_module_singles_join_one_set(tmp_path):
    # Open sets: two modules that each declare one `pick` merge into a
    # whole-program set at import; both members mangle (each file sees two
    # signatures) and dispatch by argument type.
    (tmp_path / "a.mc").write_text("fn pick(x: int32) -> int32 { return 1; }")
    (tmp_path / "b.mc").write_text("fn pick(p: char*) -> int32 { return 2; }")
    for imports in (
        'import "a";\nimport "b";\n',
        'import "b";\nimport "a";\n',
    ):
        main = tmp_path / "main.mc"
        main.write_text(
            imports
            + 'fn main() -> int32 { return pick(7) * 10 + pick("s"); }\n'
        )
        assert run_path(main) == 12


def test_cross_module_single_extends_a_set_either_order(tmp_path):
    (tmp_path / "family.mc").write_text(
        "fn pick(x: int32) -> int32 { return 1; }\n"
        "fn pick(p: char*) -> int32 { return 2; }\n"
    )
    (tmp_path / "lone.mc").write_text(
        "fn pick(n: int64) -> int32 { return 3; }"
    )
    for imports in (
        'import "family";\nimport "lone";\n',
        'import "lone";\nimport "family";\n',
    ):
        main = tmp_path / "main.mc"
        main.write_text(
            imports
            + "fn main() -> int32 {\n"
            "    let wide: int64 = 0;\n"
            '    return pick(7 as int32) * 100 + pick("s") * 10 + pick(wide);\n'
            "}\n"
        )
        assert run_path(main) == 123


def test_cross_module_sets_union(tmp_path):
    (tmp_path / "one.mc").write_text(
        "fn pick(x: int32) -> int32 { return 1; }\n"
        "fn pick(p: char*) -> int32 { return 2; }\n"
    )
    (tmp_path / "two.mc").write_text(
        "fn pick(n: int64) -> int32 { return 3; }\n"
        "fn pick(b: bool) -> int32 { return 4; }\n"
    )
    main = tmp_path / "main.mc"
    main.write_text(
        'import "one";\nimport "two";\n'
        "fn main() -> int32 {\n"
        "    let wide: int64 = 0;\n"
        '    return pick(7 as int32) * 1000 + pick("s") * 100\n'
        "        + pick(wide) * 10 + pick(true);\n"
        "}\n"
    )
    assert run_path(main) == 1234


def test_cross_module_same_pattern_still_collides(tmp_path):
    # The declare-time gate that stays: two modules may not both spell one
    # parameter list. As plain singles the name itself collides; inside a
    # larger set the mangled symbol collides, and the error cites the prior
    # member's site.
    (tmp_path / "a.mc").write_text("fn pick(x: int32) -> int32 { return 1; }")
    (tmp_path / "b.mc").write_text("fn pick(x: int32) -> int32 { return 2; }")
    main = tmp_path / "main.mc"
    main.write_text(
        'import "a";\nimport "b";\nfn main() -> int32 { return 0; }\n'
    )
    with pytest.raises(LangError, match="function 'pick' already defined"):
        run_path(main)
    (tmp_path / "b.mc").write_text(
        "fn pick(x: int32) -> int32 { return 2; }\n"
        "fn pick(p: char*) -> int32 { return 3; }\n"
    )
    with pytest.raises(
        LangError,
        match=re.escape(
            "function 'pick(int32)' already defined; overloads must differ "
            "in parameter types"
        ),
    ) as excinfo:
        run_path(main)
    assert any(
        n.source and n.source.endswith("a.mc") for n in excinfo.value.notes
    )


def test_cross_module_ambiguity_cites_both_sites(tmp_path):
    # An import supplying an equal-rank candidate makes the call ambiguous
    # -- loudly, citing both declaration sites.
    (tmp_path / "a.mc").write_text(
        "fn pick<T>(x: T, y: int32) -> int32 { return 1; }"
    )
    (tmp_path / "b.mc").write_text(
        "fn pick<T>(x: int32, y: T) -> int32 { return 2; }"
    )
    main = tmp_path / "main.mc"
    main.write_text(
        'import "a";\nimport "b";\n'
        "fn main() -> int32 { return pick(1, 2); }\n"
    )
    with pytest.raises(
        LangError, match="call to 'pick' is ambiguous between overloads"
    ) as excinfo:
        run_path(main)
    sources = {n.source for n in excinfo.value.notes}
    assert any(s and s.endswith("a.mc") for s in sources)
    assert any(s and s.endswith("b.mc") for s in sources)


def test_cross_module_concrete_outranks_a_group_template(tmp_path):
    # The protocol behavior @override is scoped around: replacing
    # group-covered behavior needs no annotation, because a concrete beats a
    # bounded generic on an exact match through the ordinary rank tiers.
    (tmp_path / "lib.mc").write_text(
        "fn pick<T: int32 | int64>(x: T) -> int32 { return 1; }"
    )
    main = tmp_path / "main.mc"
    main.write_text(
        'import "lib";\n'
        "fn pick(x: int32) -> int32 { return 2; }\n"
        "fn main() -> int32 {\n"
        "    let wide: int64 = 0;\n"
        "    return pick(7) * 10 + pick(wide);\n"
        "}\n"
    )
    assert run_path(main) == 21


def test_overloaded_name_is_not_a_function_value():
    with pytest.raises(LangError) as err:
        compile_ir(
            "fn f(x: int32) -> int32 { return 1; }\n"
            "fn f(p: char*) -> int32 { return 2; }\n"
            "fn main() -> int32 { let g = f; return 0; }"
        )
    assert err.value.message == (
        "'f' is overloaded; a function value needs a single function"
    )


def test_type_args_on_an_overloaded_concrete_name():
    with pytest.raises(LangError, match="'f' is not a generic function"):
        compile_ir(
            "fn f(x: int32) -> int32 { return 1; }\n"
            "fn f(p: char*) -> int32 { return 2; }\n"
            "fn main() -> int32 { return f<int32>(1); }"
        )


def test_no_overload_matches_concrete_set():
    with pytest.raises(
        LangError, match=r"no overload of 'f' with signature f\(float64\)"
    ):
        compile_ir(
            "fn f(x: int32) -> int32 { return 1; }\n"
            "fn f(p: char*) -> int32 { return 2; }\n"
            "fn main() -> int32 { return f(1.5); }"
        )


def test_no_overload_on_arity_shows_the_call_signature():
    # Every candidate takes two arguments; the error renders the one-argument
    # call as a signature, so the arity mismatch is visible at a glance.
    with pytest.raises(
        LangError, match=r"no overload of 'f' with signature f\(int32\)"
    ):
        compile_ir(
            "fn f(x: int32, y: int32) -> int32 { return x; }\n"
            "fn f(p: char*, q: char*) -> int32 { return 0; }\n"
            "fn main() -> int32 { return f(1); }"
        )


def test_string_literal_adapts_through_overloaded_call():
    # marshal_args parity: making a function overloaded must not break its
    # literal call sites -- the literal still borrows to the slice parameter.
    assert run(
        """
        fn describe(s: slice<const char>) -> int32 { return s.length as int32; }
        fn describe(n: int32) -> int32 { return 0 - n; }
        fn main() -> int32 {
            return describe("hello") * 10 + describe(3);
        }
        """
    ) == 47


def test_literal_spills_for_const_slice_parameter_of_overload():
    # A const slice parameter travels by hidden reference: on the overloaded
    # path the borrowed literal view spills to a temporary first.
    assert run(
        """
        fn describe(const s: slice<const char>) -> int32 {
            return s.length as int32;
        }
        fn describe(n: int32) -> int32 { return 0 - n; }
        fn main() -> int32 { return describe("hello") + describe(2); }
        """
    ) == 3


def test_ternary_of_literals_adapts_through_overloaded_call():
    # Both arms borrow in their own branch, so the merged value is already
    # the expected slice carrying the chosen literal's own length.
    assert run(
        """
        fn describe(s: slice<const char>) -> int32 { return s.length as int32; }
        fn describe(n: int32) -> int32 { return 0 - n; }
        fn main() -> int32 {
            let flag = true;
            return describe(flag ? "y" : "yes");
        }
        """
    ) == 1


def test_const_slice_value_matches_const_slice_overload():
    # Regression: a slice<const T> *value* (not a literal) must match a
    # slice<const T> parameter on the overload-set path. shape_matches rebuilt
    # the parameter's element as TypeRef(name) -- dropping the `const` -- so an
    # exact slice<const char> argument failed to match a slice<const char>
    # candidate while a mutable slice<char> (widened) matched. Filtered the
    # right candidate out and raised "no overload".
    assert run(
        """
        fn describe(const s: slice<const char>) -> int32 {
            return s.length as int32;
        }
        fn describe(n: int32) -> int32 { return 0 - n; }
        fn main() -> int32 {
            let v: slice<const char> = "hello";
            return describe(v);
        }
        """
    ) == 5


def test_mutable_slice_value_widens_into_const_slice_overload():
    # The other direction still holds: a mutable slice<char> value adds const
    # (a safe widening) to reach a slice<const char> candidate in the set. The
    # sentinel returns prove the slice overload -- not the int32 one -- is
    # chosen, independent of the borrowed view's length.
    assert run(
        """
        fn describe(const s: slice<const char>) -> int32 { return 42; }
        fn describe(n: int32) -> int32 { return 0 - n; }
        fn main() -> int32 {
            let buf: char[3] = ['a', 'b', 'c'];
            let v: slice<char> = buf as slice<char>;
            return describe(v);
        }
        """
    ) == 42


def test_const_slice_value_rejected_by_mutable_slice_overload():
    # Dropping const stays rejected: a slice<const char> argument must not
    # match a slice<char> candidate (the filter allows adding const, never
    # removing it), so the set offers no viable overload.
    with pytest.raises(
        LangError,
        match=re.escape(
            "no overload of 'describe' with signature "
            "describe(slice<const char>)"
        ),
    ):
        compile_ir(
            """
            fn describe(s: slice<char>) -> int32 { return s.length as int32; }
            fn describe(@nonnull p: char*) -> int32 { return 0; }
            fn use(v: slice<const char>) -> int32 { return describe(v); }
            fn main() -> int32 { return 0; }
            """
        )


def test_hidden_ref_sharing_unchanged_when_not_overloaded():
    # A non-overloaded const-struct call still passes the caller's storage
    # directly (the direct-call fast path, no spill).
    out = compile_ir(
        "struct point { x: int32; y: int32; }\n"
        "fn total(const p: point) -> int32 { return p.x + p.y; }\n"
        "fn main() -> int32 {\n"
        "    let p = struct point { x = 3, y = 4 };\n"
        "    return total(p);\n"
        "}"
    )
    assert 'call i32 @"total"(%"point"* %"p")' in out


def test_overloaded_const_struct_call_spills_to_a_temporary():
    # The accepted stage-1 cost: an overloaded call routes through the
    # pre-evaluate path, so a const-struct argument spills instead of
    # sharing the caller's storage. Behavior stays correct.
    src = (
        "struct point { x: int32; y: int32; }\n"
        "fn total(const p: point) -> int32 { return p.x + p.y; }\n"
        "fn total(const p: point, scale: int32) -> int32 {\n"
        "    return (p.x + p.y) * scale;\n"
        "}\n"
        "fn main() -> int32 {\n"
        "    let p = struct point { x = 3, y = 4 };\n"
        "    return total(p) + total(p, 2);\n"
        "}"
    )
    assert run(src) == 21
    out = compile_ir(src)
    assert 'call i32 @"total(point)"(' in out
    assert 'call i32 @"total(point)"(%"point"* %"p")' not in out


def test_prototype_pairs_with_its_set_member():
    # Stage 2: a prototype names its member by signature and pairs with the
    # matching definition; the set dispatches as usual.
    assert run(
        "fn f(x: int32) -> int32;\n"
        "fn f(x: int32) -> int32 { return 1; }\n"
        "fn f(p: char*) -> int32 { return 2; }\n"
        "fn main() -> int32 { return f(0) * 10 + f(\"x\"); }"
    ) == 12


# ------------------- open sets: per-overload privacy


def test_foreign_private_overload_is_not_a_candidate(tmp_path):
    # An @private overload is a candidate only inside its own module: a
    # foreign call falls through to the members it can see (here the
    # unbounded generic), instead of erroring on the private one.
    (tmp_path / "lib.mc").write_text(
        "fn describe<T>(x: T) -> int32 { return 0; }\n"
        "@private\nfn describe(x: int32) -> int32 { return 1; }\n"
        "fn via_lib(x: int32) -> int32 { return describe(x); }\n"
    )
    main = tmp_path / "main.mc"
    main.write_text(
        'import "lib";\n'
        "fn main() -> int32 { return describe(5) * 10 + via_lib(5); }\n"
    )
    # main's call skips the private member (generic: 0); lib's own call
    # sees it (concrete beats generic on the exact match: 1).
    assert run_path(main) == 1


def test_all_private_set_is_a_privacy_error(tmp_path):
    (tmp_path / "pri.mc").write_text(
        "@private\nfn hidden(x: int32) -> int32 { return 1; }\n"
        "@private\nfn hidden(p: char*) -> int32 { return 2; }\n"
    )
    main = tmp_path / "main.mc"
    main.write_text(
        'import "pri";\nfn main() -> int32 { return hidden(5); }\n'
    )
    with pytest.raises(
        LangError, match="function 'hidden' is private to pri.mc"
    ):
        run_path(main)


def test_private_members_salt_their_symbols(tmp_path):
    # Two modules may each contribute an @private member with the same
    # parameter pattern: the mangled symbols carry the defining file's stem,
    # so they never collide, and each module resolves to its own.
    (tmp_path / "base.mc").write_text(
        "fn fmt(x: int32) -> int32 { return 0; }\n"
        "fn fmt(p: char*) -> int32 { return 9; }\n"
    )
    (tmp_path / "m1.mc").write_text(
        'import "base";\n'
        "@private\nfn fmt(b: bool) -> int32 { return 1; }\n"
        "fn via_m1() -> int32 { return fmt(true); }\n"
    )
    (tmp_path / "m2.mc").write_text(
        'import "base";\n'
        "@private\nfn fmt(b: bool) -> int32 { return 2; }\n"
        "fn via_m2() -> int32 { return fmt(true); }\n"
    )
    main = tmp_path / "main.mc"
    main.write_text(
        'import "m1";\nimport "m2";\n'
        "fn main() -> int32 { return via_m1() * 10 + via_m2(); }\n"
    )
    assert run_path(main) == 12
    out = str(compile_to_ir(main))
    assert 'i32 @"fmt(bool).m1"(' in out
    assert 'i32 @"fmt(bool).m2"(' in out


def test_privacy_only_variant_is_still_a_duplicate():
    # One file may not spell one parameter list twice, even when the two
    # differ only in @private (they would tie at every call).
    with pytest.raises(
        LangError,
        match=re.escape(
            "function 'f(int32)' already defined; overloads must differ in "
            "parameter types"
        ),
    ):
        compile_ir(
            "fn f(x: int32) -> int32 { return 1; }\n"
            "@private\nfn f(x: int32) -> int32 { return 2; }\n"
            "fn f(p: char*) -> int32 { return 3; }\n"
            "fn main() -> int32 { return 0; }"
        )


def test_deferred_dispatch_skips_foreign_private_overloads(tmp_path):
    # The formatting-protocol consequence, pinned deliberately: a dispatch
    # site that lives in another module (println's format_arg pattern -- a
    # generic `with` arm resolved at end of codegen) cannot see the caller's
    # @private overload; the value falls through to the fallback. Direct
    # calls in the owning module still see it.
    (tmp_path / "lib.mc").write_text(
        "fn render<T>(x: T) -> int32 { return 0; }\n"
        "fn dispatch(args...) -> int32 {\n"
        "    with (t = args[0] as T) { return render(t); }\n"
        "    return -1;\n"
        "}\n"
    )
    main = tmp_path / "main.mc"
    main.write_text(
        'import "lib";\n'
        "@private\nfn render(x: int32) -> int32 { return 1; }\n"
        "fn main() -> int32 { return dispatch(5) * 10 + render(5); }\n"
    )
    # dispatch's arm resolves in lib.mc, where the private member is
    # invisible (generic fallback: 0); the direct call sees it (1).
    assert run_path(main) == 1


def test_deprecated_overload_warns_through_a_cross_module_set(tmp_path):
    # Deprecation is per overload on the set path: only resolution picking
    # the deprecated member warns, imports included.
    (tmp_path / "lib.mc").write_text(
        '@deprecated("use pick(int64) instead")\n'
        "fn pick(x: int32) -> int32 { return 1; }\n"
    )
    main = tmp_path / "main.mc"
    main.write_text(
        'import "lib";\n'
        "fn pick(p: char*) -> int32 { return 2; }\n"
        "fn main() -> int32 {\n"
        '    return pick(7) * 10 + pick("s");\n'
        "}\n"
    )
    warnings = []
    compile_to_ir(main, warnings=warnings)
    assert [
        (w.message, w.line) for w in warnings
    ] == [("'pick' is deprecated: use pick(int64) instead", 4)]


def test_plain_single_duplicating_a_set_member_collides(tmp_path):
    # a.mc's set holds a mangled pick(int32); b.mc's pick(int32) stays a
    # plain single (a's @private sibling is invisible to b, so b sees one
    # signature) -- but it spells a pattern the set already has.
    (tmp_path / "a.mc").write_text(
        "fn pick(x: int32) -> int32 { return 1; }\n"
        "@private\nfn pick(b: bool) -> int32 { return 2; }\n"
    )
    (tmp_path / "b.mc").write_text("fn pick(x: int32) -> int32 { return 3; }")
    main = tmp_path / "main.mc"
    main.write_text(
        'import "a";\nimport "b";\nfn main() -> int32 { return 0; }\n'
    )
    with pytest.raises(
        LangError,
        match=re.escape(
            "function 'pick(int32)' already defined; overloads must differ "
            "in parameter types"
        ),
    ) as excinfo:
        run_path(main)
    assert any(
        n.source and n.source.endswith("a.mc") for n in excinfo.value.notes
    )


def test_set_member_duplicating_a_plain_single_collides(tmp_path):
    # The mirror image: the stub pins a plain pick(int32), and a module
    # whose own set mangles spells the same pattern.
    (tmp_path / "api.mci").write_text("fn pick(x: int32) -> int32;\n")
    (tmp_path / "b.mc").write_text(
        "fn pick(x: int32) -> int32 { return 1; }\n"
        "fn pick(b: bool) -> int32 { return 2; }\n"
    )
    main = tmp_path / "main.mc"
    main.write_text(
        'import "api";\nimport "b";\nfn main() -> int32 { return 0; }\n'
    )
    with pytest.raises(
        LangError,
        match=re.escape(
            "function 'pick(int32)' already defined; overloads must differ "
            "in parameter types"
        ),
    ) as excinfo:
        run_path(main)
    assert any(
        n.source and n.source.endswith("api.mci")
        for n in excinfo.value.notes
    )


def test_all_private_sets_from_two_modules_name_both_owners(tmp_path):
    (tmp_path / "pri1.mc").write_text(
        "@private\nfn hidden(x: int32) -> int32 { return 1; }\n"
        "@private\nfn hidden(b: bool) -> int32 { return 2; }\n"
    )
    (tmp_path / "pri2.mc").write_text(
        "@private\nfn hidden(p: char*) -> int32 { return 3; }\n"
        "@private\nfn hidden(n: int64) -> int32 { return 4; }\n"
    )
    main = tmp_path / "main.mc"
    main.write_text(
        'import "pri1";\nimport "pri2";\n'
        "fn main() -> int32 { return hidden(5); }\n"
    )
    with pytest.raises(
        LangError,
        match="function 'hidden' is private to pri1.mc, pri2.mc",
    ):
        run_path(main)
