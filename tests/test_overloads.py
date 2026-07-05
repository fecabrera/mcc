"""Generic overload sets: same name, dispatch by parameter pattern."""

import re

import pytest

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
    with pytest.raises(LangError, match="no overload of 'describe' matches"):
        compile_ir(
            "fn describe<T>(x: T*) -> int32 { return 1; }\n"
            "fn describe<T>(x: T**) -> int32 { return 2; }\n"
            "fn main() -> int32 { return describe(5); }"
        )


def test_ambiguous_overloads():
    with pytest.raises(LangError, match="ambiguous"):
        compile_ir(
            "fn f<T>(x: T) -> int32 { return 1; }\n"
            "fn f<T>(x: T) -> int32 { return 2; }\n"
            "fn main() -> int32 { return f(5 as int32); }"
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
        import "hash";
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


def test_mixed_set_cannot_span_modules(tmp_path):
    # All overloads of a name -- generic members included -- live in one
    # defining module, in either import order.
    (tmp_path / "gen.mc").write_text("fn pick<T>(x: T) -> int32 { return 0; }")
    (tmp_path / "conc.mc").write_text(
        "fn pick(x: int32) -> int32 { return 1; }"
    )
    for imports in (
        'import "gen";\nimport "conc";\n',
        'import "conc";\nimport "gen";\n',
    ):
        main = tmp_path / "main.mc"
        main.write_text(imports + "fn main() -> int32 { return 0; }\n")
        with pytest.raises(LangError, match="function 'pick' already defined"):
            run_path(main)


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


def test_cross_module_same_name_still_collides(tmp_path):
    # All overloads of a name must live in one defining module: the symbol
    # choice (plain vs mangled) is a per-file fact.
    (tmp_path / "a.mc").write_text("fn pick(x: int32) -> int32 { return 1; }")
    (tmp_path / "b.mc").write_text("fn pick(p: char*) -> int32 { return 2; }")
    main = tmp_path / "main.mc"
    main.write_text(
        'import "a";\nimport "b";\nfn main() -> int32 { return 0; }\n'
    )
    with pytest.raises(LangError, match="function 'pick' already defined"):
        run_path(main)


def test_cross_module_set_and_single_collide_either_order(tmp_path):
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
        main.write_text(imports + "fn main() -> int32 { return 0; }\n")
        with pytest.raises(LangError, match="function 'pick' already defined"):
            run_path(main)


def test_cross_module_sets_collide_too(tmp_path):
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
        'import "one";\nimport "two";\nfn main() -> int32 { return 0; }\n'
    )
    with pytest.raises(LangError, match="function 'pick' already defined"):
        run_path(main)


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
        LangError, match="no overload of 'f' matches argument types"
    ):
        compile_ir(
            "fn f(x: int32) -> int32 { return 1; }\n"
            "fn f(p: char*) -> int32 { return 2; }\n"
            "fn main() -> int32 { return f(1.5); }"
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
