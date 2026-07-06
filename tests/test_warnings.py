"""The warning channel: @warning directives and @deprecated call-site warnings
collected on CodeGen.warnings."""

from pathlib import Path

import pytest

from mcc.codegen import CodeGen
from mcc.driver import _report_warnings, compile_to_ir, parse_wflags
from mcc.errors import WARNING_CLASSES, LangError, Note
from helpers import parse, run


def generate(source: str) -> CodeGen:
    """Compile a source string and return the CodeGen, warnings and all."""
    cg = CodeGen(parse(source), "test")
    cg.generate()
    return cg


# --- warn(): the channel itself ---

def test_warn_collects_a_note_with_the_current_source():
    cg = CodeGen(parse("fn main() -> int32 { return 0; }"), "test")
    cg.current_source = "somewhere.mc"
    cg.warn("stamped at emission", 7)
    assert cg.warnings == [Note("stamped at emission", 7, "somewhere.mc")]


def test_warn_never_raises_and_generation_succeeds():
    src = """
    @warning("still compiles");
    fn main() -> int32 { return 0; }
    """
    cg = generate(src)
    assert [(w.message, w.line) for w in cg.warnings] == [("still compiles", 2)]
    assert 'define i32 @"main"()' in str(cg.module)


def test_warned_program_still_runs():
    src = """
    @warning("non-fatal");
    fn main() -> int32 { return 42; }
    """
    assert run(src) == 42


# --- ordering: emission order is source order, no dedup ---

def test_warnings_preserve_emission_order():
    src = """
    @warning("first");
    @warning("second");
    @warning("first");
    fn main() -> int32 { return 0; }
    """
    assert [w.message for w in generate(src).warnings] == [
        "first", "second", "first",
    ]


# --- messages decode the usual string escapes ---

def test_warning_message_processes_escapes():
    src = r"""
    @warning("line1\nline2");
    fn main() -> int32 { return 0; }
    """
    assert generate(src).warnings[0].message == "line1\nline2"


# --- interaction with compile-time @if ---

def test_warning_in_dead_if_branch_never_fires():
    src = """
    @if (0) {
        @warning("dropped with the dead branch");
    }
    fn main() -> int32 { return 0; }
    """
    assert generate(src).warnings == []


def test_warning_in_live_if_branch_fires():
    src = """
    @if (1) {
        @warning("live branch warns");
    }
    fn main() -> int32 { return 0; }
    """
    assert [w.message for w in generate(src).warnings] == ["live branch warns"]


# --- compile_to_ir: the out-list keyword ---

def test_compile_to_ir_extends_the_out_list(tmp_path):
    path = tmp_path / "w.mc"
    path.write_text('@warning("from a file");\nfn main() -> int32 { return 0; }\n')
    warnings = []
    compile_to_ir(path, (), warnings=warnings)
    assert [(w.message, w.line, w.source) for w in warnings] == [
        ("from a file", 1, str(path)),
    ]


def test_compile_to_ir_without_the_keyword_discards_warnings(tmp_path):
    # The pre-warning call shape (~15 test call sites) keeps working untouched.
    path = tmp_path / "w.mc"
    path.write_text('@warning("discarded");\nfn main() -> int32 { return 0; }\n')
    module = compile_to_ir(path, ())
    assert 'define i32 @"main"()' in str(module)


def test_warnings_before_a_hard_error_are_dropped(tmp_path):
    # "After success" is literal: the except path never sees the list filled.
    path = tmp_path / "w.mc"
    path.write_text(
        '@warning("collected then dropped");\n'
        '@error("boom");\n'
        "fn main() -> int32 { return 0; }\n"
    )
    warnings = []
    with pytest.raises(LangError, match="line 2: boom"):
        compile_to_ir(path, (), warnings=warnings)
    assert warnings == []


# --- an imported @warning is attributed to the file that declares it ---

def test_imported_warning_names_its_file(tmp_path):
    (tmp_path / "lib.mc").write_text('@warning("lib is grumpy");\n')
    main = tmp_path / "main.mc"
    main.write_text('import "lib";\nfn main() -> int32 { return 0; }\n')
    warnings = []
    compile_to_ir(main, (tmp_path,), warnings=warnings)
    assert len(warnings) == 1
    assert warnings[0].message == "lib is grumpy"
    assert warnings[0].line == 1
    assert warnings[0].source is not None and warnings[0].source.endswith("lib.mc")


# --- @deprecated: every call site warns with the migration message ---

def test_deprecated_call_warns_at_the_call_site():
    src = """
    @deprecated("use renamed instead")
    fn old(x: int32) -> int32 { return x + 1; }
    fn main() -> int32 {
        return old(1);
    }
    """
    assert [(w.message, w.line) for w in generate(src).warnings] == [
        ("'old' is deprecated: use renamed instead", 5),
    ]


def test_deprecated_function_stays_callable_and_correct():
    src = """
    @deprecated("use renamed instead")
    fn old(x: int32) -> int32 { return x * 2; }
    fn main() -> int32 { return old(21); }
    """
    assert run(src) == 42


def test_uncalled_deprecated_function_does_not_warn():
    src = """
    @deprecated("use renamed instead")
    fn old(x: int32) -> int32 { return x; }
    fn main() -> int32 { return 0; }
    """
    assert generate(src).warnings == []


def test_call_from_another_deprecated_function_still_warns():
    # No suppression: a deprecated function calling a deprecated function
    # warns too, so a migration cannot hide behind another alias.
    src = """
    @deprecated("use g instead")
    fn f() -> int32 { return 1; }
    @deprecated("use h instead")
    fn wrapper() -> int32 { return f(); }
    fn main() -> int32 { return wrapper(); }
    """
    assert [(w.message, w.line) for w in generate(src).warnings] == [
        ("'f' is deprecated: use g instead", 5),
        ("'wrapper' is deprecated: use h instead", 6),
    ]


def test_generic_deprecated_function_warns_at_the_callers_line():
    src = """
    @deprecated("use bytecopy instead")
    @inline
    fn copy_bytes<T>(dst: T*, src: T*, n: uint64) { }
    fn main() -> int32 {
        let a: int32[2]; let b: int32[2];
        copy_bytes(&a[0], &b[0], 2);
        return 0;
    }
    """
    assert [(w.message, w.line) for w in generate(src).warnings] == [
        ("'copy_bytes' is deprecated: use bytecopy instead", 7),
    ]


def test_static_deprecated_function_warns():
    src = """
    @static @deprecated("use fresh instead")
    fn helper() -> int32 { return 3; }
    fn main() -> int32 { return helper(); }
    """
    assert [(w.message, w.line) for w in generate(src).warnings] == [
        ("'helper' is deprecated: use fresh instead", 4),
    ]


def test_extern_deprecated_function_warns():
    src = """
    @extern @deprecated("use newapi instead")
    fn oldapi(x: int32) -> int32;
    fn main() -> int32 { return oldapi(0); }
    """
    assert [(w.message, w.line) for w in generate(src).warnings] == [
        ("'oldapi' is deprecated: use newapi instead", 4),
    ]


def test_function_value_of_deprecated_function_warns():
    # A function value is a call site in waiting: its formation warns, since
    # calls through the pointer can no longer be attributed.
    src = """
    @deprecated("use g instead")
    fn f(x: int32) -> int32 { return x; }
    fn main() -> int32 {
        let p: fn(int32) -> int32 = f;
        return p(0);
    }
    """
    assert [(w.message, w.line) for w in generate(src).warnings] == [
        ("'f' is deprecated: use g instead", 5),
    ]


def test_mixed_overload_set_warns_only_when_the_deprecated_overload_wins():
    src = """
    @deprecated("pass a pointer instead")
    fn pick<T>(v: T) -> int32 { return 1; }
    fn pick<T>(v: T*) -> int32 { return 2; }
    fn main() -> int32 {
        let x: int32 = 5;
        let a = pick(x);
        let b = pick(&x);
        return a + b;
    }
    """
    assert [(w.message, w.line) for w in generate(src).warnings] == [
        ("'pick' is deprecated: pass a pointer instead", 7),
    ]


def test_for_in_over_a_deprecated_protocol_warns_for_it_and_next():
    src = """
    struct counter { n: int32; }
    struct counter_iter { cur: int32; stop: int32; }
    @deprecated("iterate the replacement type")
    fn counter_it(c: struct counter*) -> struct counter_iter {
        let it: struct counter_iter;
        it.cur = 0; it.stop = c->n;
        return it;
    }
    @deprecated("iterate the replacement type")
    fn counter_next(it: struct counter_iter*, out: int32*) -> bool {
        if (it->cur >= it->stop) { return false; }
        *out = it->cur;
        it->cur = it->cur + 1;
        return true;
    }
    fn main() -> int32 {
        let c: struct counter;
        c.n = 3;
        let total: int32 = 0;
        for v in c { total = total + v; }
        return total;
    }
    """
    cg = generate(src)
    # The iterator bodies' unproven derefs also collect (tagged with the
    # opt-in unchecked-dereference class); this test is about deprecation.
    assert [(w.message, w.line) for w in cg.warnings if w.wclass is None] == [
        ("'counter_it' is deprecated: iterate the replacement type", 21),
        ("'counter_next' is deprecated: iterate the replacement type", 21),
    ]


def test_deprecated_message_processes_escapes():
    src = r"""
    @deprecated("gone \"soon\": use g")
    fn f() -> int32 { return 0; }
    fn main() -> int32 { return f(); }
    """
    assert generate(src).warnings[0].message == (
        "'f' is deprecated: gone \"soon\": use g"
    )


def test_each_instantiation_re_emits_the_raw_warning():
    # One offending line inside a generic body emits once per instantiation
    # on the raw channel; the driver deduplicates at print time (see
    # test_cli.py), keeping this list faithful.
    src = """
    @deprecated("use g instead")
    fn f() -> int32 { return 1; }
    fn wrap<T>(v: T) -> int32 { return f(); }
    fn main() -> int32 {
        return wrap(1) + wrap(true);
    }
    """
    assert [(w.message, w.line) for w in generate(src).warnings] == [
        ("'f' is deprecated: use g instead", 4),
        ("'f' is deprecated: use g instead", 4),
    ]


# --- @deprecated: parse-time validation ---

def test_deprecated_requires_a_message():
    with pytest.raises(LangError, match="needs a non-empty message"):
        parse('@deprecated("") fn f() { }')


def test_deprecated_only_applies_to_functions():
    with pytest.raises(LangError, match="only applies to functions"):
        parse('@deprecated("no") struct s { x: int32; }')
    with pytest.raises(LangError, match="only applies to functions"):
        parse('@deprecated("no") @static let g: int32;')


# --- the stdlib forwarders: the motivating use case ---

def test_stdlib_memory_forwarders_warn_with_their_replacements(tmp_path):
    lib_dir = Path(__file__).resolve().parents[1] / "libmc"
    main = tmp_path / "main.mc"
    main.write_text(
        f'import "{lib_dir / "memory"}";\n'
        "fn main() -> int32 {\n"
        "    let a = alloc<int32>(2);\n"
        "    if (a == null) return 1;\n"
        "    let b = alloc<int32>(2);\n"
        "    if (b == null) return 1;\n"
        "    copy_bytes(a, b, 2);\n"
        "    copy_items(a, b, 2);\n"
        "    set_bytes(a, 0, 2);\n"
        "    set_items(a, 0, 2);\n"
        "    dealloc(a); dealloc(b);\n"
        "    return 0;\n"
        "}\n"
    )
    warnings = []
    compile_to_ir(main, (), warnings=warnings)
    # Attributed to the caller's file and lines, not libmc/memory.mc.
    assert [(w.message, w.line, w.source) for w in warnings] == [
        ("'copy_bytes' is deprecated: use bytecopy instead", 7, str(main)),
        ("'copy_items' is deprecated: use copy instead", 8, str(main)),
        ("'set_bytes' is deprecated: use bytefill instead", 9, str(main)),
        ("'set_items' is deprecated: use fill instead", 10, str(main)),
    ]


def test_stdlib_compiles_clean_of_deprecation_warnings(tmp_path):
    # The internal callers were repointed to the new names, so merely
    # importing the library (and its dependents) warns nothing.
    lib_dir = Path(__file__).resolve().parents[1] / "libmc"
    main = tmp_path / "main.mc"
    main.write_text(
        'import "dict";\n'
        'import "hashing/md5";\n'
        "fn main() -> int32 {\n"
        "    let d: dict<int32>;\n"
        "    dict_init(&d, 4);\n"
        '    dict_set(&d, "k", 1);\n'
        "    let digest: uint8[16];\n"
        '    md5("abc", 3, &digest[0]);\n'  # a cast would strip the literal's non-null proof
        "    dict_destroy(&d);\n"
        "    return 0;\n"
        "}\n"
    )
    warnings = []
    compile_to_ir(main, (lib_dir,), warnings=warnings)
    # Filter to the unconditional channel: libmc's containers still collect
    # opt-in unchecked-dereference emissions (the libmc sweep is a recorded
    # follow-up; the class is default-off, so nothing prints without -W).
    assert [w for w in warnings if w.wclass is None] == []


# ------------------------------------------- opt-in warning classes (wclass)

DEREF_MSG = ("dereference of a possibly-null pointer (narrow it with a null "
             "check or assert with postfix '!')")
UNCHECKED = "unchecked-dereference"


def class_warnings(source: str) -> list[tuple[str, int]]:
    """The unchecked-dereference emissions of a source, as (message, line)."""
    return [(w.message, w.line)
            for w in generate(source).warnings if w.wclass == UNCHECKED]


def test_warn_tags_the_note_with_its_class():
    cg = CodeGen(parse("fn main() -> int32 { return 0; }"), "test")
    cg.current_source = "somewhere.mc"
    cg.warn("tagged", 3, wclass=UNCHECKED)
    assert cg.warnings == [Note("tagged", 3, "somewhere.mc", UNCHECKED)]


def test_warn_defaults_to_the_unconditional_channel():
    cg = CodeGen(parse("fn main() -> int32 { return 0; }"), "test")
    cg.warn("plain", 3)
    assert cg.warnings == [Note("plain", 3, None, None)]


def test_warn_rejects_an_unregistered_class():
    # A producer typo mints no silently-unenableable class; it fails here.
    cg = CodeGen(parse("fn main() -> int32 { return 0; }"), "test")
    with pytest.raises(AssertionError, match="unregistered warning class 'tpyo'"):
        cg.warn("oops", 1, wclass="tpyo")


def test_reserved_names_are_never_registered_classes():
    # "error" (-Werror) and "all" (-Wall) are claimed by the driver; "no-"
    # keeps the -Wno-<name> spelling available for per-class disabling later.
    assert "error" not in WARNING_CLASSES
    assert "all" not in WARNING_CLASSES
    assert not any(name.startswith("no-") for name in WARNING_CLASSES)


# --- -Wunchecked-dereference: unproven dereference sites collect ---

def test_unproven_load_deref_warns():
    src = """
    fn first(p: int32*) -> int32 { return *p; }
    fn main() -> int32 { let x: int32 = 1; return first(&x); }
    """
    assert class_warnings(src) == [(DEREF_MSG, 2)]


def test_unproven_store_target_deref_warns():
    src = """
    fn set(p: int32*) { *p = 1; }
    fn main() -> int32 { let x: int32 = 0; set(&x); return x - 1; }
    """
    assert class_warnings(src) == [(DEREF_MSG, 2)]


def test_unproven_compound_store_target_deref_warns():
    src = """
    fn bump(p: int32*) { *p += 1; }
    fn main() -> int32 { let x: int32 = 0; bump(&x); return x - 1; }
    """
    assert class_warnings(src) == [(DEREF_MSG, 2)]


def test_unproven_arrow_warns():
    src = """
    struct point { x: int32; }
    fn read(p: struct point*) -> int32 { return p->x; }
    fn main() -> int32 { let pt: struct point; pt.x = 3; return read(&pt); }
    """
    assert class_warnings(src) == [(DEREF_MSG, 3)]


def test_unproven_index_warns():
    src = """
    fn second(p: int32*) -> int32 { return p[1]; }
    fn main() -> int32 { let a: int32[2]; a[1] = 9; return second(&a[0]); }
    """
    # One report for the parameter indexing; a[...] on the array never warns.
    assert class_warnings(src) == [(DEREF_MSG, 2)]


def test_class_collects_even_though_it_is_off_by_default(tmp_path):
    # Collection is unconditional -- codegen never sees -W flags -- so the
    # embedder-facing out-list keeps the tagged emission; only printing is
    # gated (see the _report_warnings tests below).
    path = tmp_path / "w.mc"
    path.write_text("fn first(p: int32*) -> int32 { return *p; }\n"
                    "fn main() -> int32 { let x: int32 = 1; return first(&x); }\n")
    warnings = []
    compile_to_ir(path, (), warnings=warnings)
    assert [(w.message, w.line, w.wclass) for w in warnings] == [
        (DEREF_MSG, 1, UNCHECKED),
    ]


def test_generic_body_collects_per_instantiation():
    # The raw list keeps every emission; collapsing repeats of one site is
    # the driver's print-time dedup, not the channel's.
    src = """
    fn first<T>(p: T*) -> T { return *p; }
    fn main() -> int32 {
        let x: int32 = 1;
        let y: int64 = 2;
        return first(&x) + first(&y) as int32 - 3;
    }
    """
    assert class_warnings(src) == [(DEREF_MSG, 2), (DEREF_MSG, 2)]


# --- ...and every proven site stays silent ---

def test_nonnull_param_deref_does_not_warn():
    src = """
    fn first(@nonnull p: int32*) -> int32 { return *p; }
    fn main() -> int32 { let x: int32 = 1; return first(&x); }
    """
    assert class_warnings(src) == []


def test_narrowed_local_deref_does_not_warn():
    src = """
    fn first(p: int32*) -> int32 {
        if (p != null) { return *p; }
        return 0;
    }
    fn main() -> int32 { let x: int32 = 1; return first(&x); }
    """
    assert class_warnings(src) == []


def test_narrowed_projection_deref_does_not_warn():
    src = """
    struct buf { data: int32*; }
    fn head(@nonnull b: struct buf*) -> int32 {
        if (b->data != null) { return *b->data; }
        return 0;
    }
    fn main() -> int32 {
        let x: int32 = 5;
        let b: struct buf;
        b.data = &x;
        return head(&b) - 5;
    }
    """
    assert class_warnings(src) == []


def test_postfix_assert_silences_the_site():
    src = """
    fn first(p: int32*) -> int32 { return *p!; }
    fn main() -> int32 { let x: int32 = 1; return first(&x); }
    """
    assert class_warnings(src) == []


def test_let_seeded_local_deref_does_not_warn():
    src = """
    fn main() -> int32 {
        let x: int32 = 2;
        let p = &x;
        return *p - 2;
    }
    """
    assert class_warnings(src) == []


def test_array_indexing_never_warns():
    src = """
    fn main() -> int32 {
        let a: int32[4];
        a[0] = 1;
        return a[0] - 1;
    }
    """
    assert class_warnings(src) == []


def test_slice_indexing_never_warns():
    # A slice indexes through its data field -- the borrow's invariant, not a
    # user-visible pointer dereference.
    src = """
    fn main() -> int32 {
        let a: int32[4];
        a[2] = 6;
        let s = a as slice<int32>;
        return s[2] - 6;
    }
    """
    assert class_warnings(src) == []


# --- the driver's print-time gate: parse_wflags and _report_warnings ---

def test_parse_wflags_accepts_registered_names():
    assert parse_wflags(["unchecked-dereference"]) == frozenset({UNCHECKED})


def test_parse_wflags_all_expands_to_the_registry():
    assert parse_wflags(["all"]) == WARNING_CLASSES


def test_parse_wflags_rejects_unknown_names():
    with pytest.raises(ValueError, match="unknown warning class 'bogus'"):
        parse_wflags(["bogus"])


def test_parse_wflags_defaults_to_nothing_enabled():
    assert parse_wflags([]) == frozenset()


def test_report_skips_a_disabled_class(capsys):
    notes = [Note("m", 1, "w.mc", UNCHECKED)]
    assert _report_warnings(notes, Path("w.mc"), False) is False
    assert capsys.readouterr().err == ""


def test_report_promotion_is_post_filter(capsys):
    # -Werror promotes exactly what printed: a disabled class never fails
    # the build (this is what keeps CI's bare -Werror safe).
    notes = [Note("m", 1, "w.mc", UNCHECKED)]
    assert _report_warnings(notes, Path("w.mc"), True) is False
    assert capsys.readouterr().err == ""


def test_report_names_the_flag_of_an_enabled_class(capsys):
    notes = [Note("m", 1, "w.mc", UNCHECKED)]
    enabled = frozenset({UNCHECKED})
    assert _report_warnings(notes, Path("w.mc"), False, enabled) is False
    assert capsys.readouterr().err == (
        "w.mc: warning: line 1: m [-Wunchecked-dereference]\n")


def test_report_werror_names_the_class_it_promotes(capsys):
    notes = [Note("m", 1, "w.mc", UNCHECKED)]
    enabled = frozenset({UNCHECKED})
    assert _report_warnings(notes, Path("w.mc"), True, enabled) is True
    assert capsys.readouterr().err == (
        "w.mc: error: line 1: m [-Werror=unchecked-dereference]\n")


def test_report_unconditional_werror_tail_is_unchanged(capsys):
    # Untagged producers keep the plain [-Werror] marker byte-identical.
    notes = [Note("m", 1, "w.mc")]
    assert _report_warnings(notes, Path("w.mc"), True) is True
    assert capsys.readouterr().err == "w.mc: error: line 1: m [-Werror]\n"


def test_report_filters_before_deduplicating(capsys):
    # A skipped (disabled-class) warning must not consume the dedup key of
    # an identical unconditional one.
    notes = [Note("m", 1, "w.mc", UNCHECKED), Note("m", 1, "w.mc")]
    assert _report_warnings(notes, Path("w.mc"), False) is False
    assert capsys.readouterr().err == "w.mc: warning: line 1: m\n"


def test_report_dedups_within_an_enabled_class(capsys):
    # Per-instantiation re-emissions of one site still print once.
    notes = [Note("m", 2, "w.mc", UNCHECKED), Note("m", 2, "w.mc", UNCHECKED)]
    enabled = frozenset({UNCHECKED})
    assert _report_warnings(notes, Path("w.mc"), False, enabled) is False
    assert capsys.readouterr().err == (
        "w.mc: warning: line 2: m [-Wunchecked-dereference]\n")
