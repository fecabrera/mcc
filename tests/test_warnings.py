"""The warning channel: @warning directives and @deprecated call-site warnings
collected on CodeGen.warnings."""

from pathlib import Path

import pytest

from mcc.codegen import CodeGen
from mcc.driver import (
    _report_warnings,
    compile_to_ir,
    parse_wflags,
    split_werror_classes,
)
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


def test_call_from_another_deprecated_function_is_exempt():
    # A deprecated function may delegate to other deprecated functions (a
    # deprecation shim among the deprecated cluster) without re-warning: the
    # call to 'f' from inside deprecated 'wrapper' is silent. The live call
    # to 'wrapper' from main still warns -- the exemption is only for the
    # enclosing-function-is-deprecated case.
    src = """
    @deprecated("use g instead")
    fn f() -> int32 { return 1; }
    @deprecated("use h instead")
    fn wrapper() -> int32 { return f(); }
    fn main() -> int32 { return wrapper(); }
    """
    assert [(w.message, w.line) for w in generate(src).warnings] == [
        ("'wrapper' is deprecated: use h instead", 6),
    ]


def test_generic_deprecated_function_calling_deprecated_is_exempt():
    # The exemption holds for a monomorphized deprecated generic body too:
    # instantiating deprecated 'shim<T>' emits its body with the flag set, so
    # its inner call to deprecated 'base' does not warn. The live call to
    # 'shim' from main still warns.
    src = """
    @deprecated("use base directly")
    fn base(x: int32) -> int32 { return x; }
    @deprecated("use base directly")
    fn shim<T>(x: T) -> int32 { return base(x); }
    fn main() -> int32 { return shim(7 as int32); }
    """
    assert [(w.message, w.line) for w in generate(src).warnings] == [
        ("'shim' is deprecated: use base directly", 6),
    ]


def test_deprecated_function_value_of_deprecated_is_exempt():
    # Forming a function value of a deprecated function is a warn site too,
    # but taken from inside a deprecated body it is exempt like a direct call.
    # The live call to 'holder' from main still warns.
    src = """
    @deprecated("use g instead")
    fn f() -> int32 { return 1; }
    @deprecated("use h instead")
    fn holder() -> int32 {
        let p: fn() -> int32 = f;
        return p();
    }
    fn main() -> int32 { return holder(); }
    """
    assert [(w.message, w.line) for w in generate(src).warnings] == [
        ("'holder' is deprecated: use h instead", 9),
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
    lib_root = Path(__file__).resolve().parents[1] / "lib"
    main = tmp_path / "main.mc"
    main.write_text(
        'import "std/memory";\n'
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
    compile_to_ir(main, (lib_root,), warnings=warnings)
    # Attributed to the caller's file and lines, not lib/std/memory.mc.
    assert [(w.message, w.line, w.source) for w in warnings] == [
        ("'copy_bytes' is deprecated: use bytecopy instead", 7, str(main)),
        ("'copy_items' is deprecated: use copy instead", 8, str(main)),
        ("'set_bytes' is deprecated: use bytefill instead", 9, str(main)),
        ("'set_items' is deprecated: use fill instead", 10, str(main)),
    ]


def test_stdlib_compiles_clean_of_deprecation_warnings(tmp_path):
    # The internal callers were repointed to the new names, so merely
    # importing the library (and its dependents) warns nothing.
    lib_dir = Path(__file__).resolve().parents[1] / "lib"
    main = tmp_path / "main.mc"
    main.write_text(
        'import "std/dict";\n'
        'import "std/hashing/md5";\n'
        "fn main() -> int32 {\n"
        "    let d = dict<int32>(4);\n"
        '    d.set("k", 1);\n'
        "    let digest: uint8[16];\n"
        '    md5("abc", 3, &digest[0]);\n'  # a cast would strip the literal's non-null proof
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


def test_ternary_of_proven_arms_deref_does_not_warn():
    # `*(flag ? p : q)` with both arms narrowed: whichever arm executes is a
    # proven source, so the site is silent.
    src = """
    fn pick(flag: bool, p: int32*, q: int32*) -> int32 {
        if (p == null) { return 0; }
        if (q == null) { return 0; }
        return *(flag ? p : q);
    }
    fn main() -> int32 {
        let x: int32 = 1;
        let y: int32 = 2;
        return pick(true, &x, &y) - 1;
    }
    """
    assert class_warnings(src) == []


def test_ternary_with_unproven_arm_deref_warns():
    # One unproven arm keeps the site warning.
    src = """
    fn pick(flag: bool, p: int32*, q: int32*) -> int32 {
        if (p == null) { return 0; }
        return *(flag ? p : q);
    }
    fn main() -> int32 {
        let x: int32 = 1;
        let y: int32 = 2;
        return pick(true, &x, &y) - 1;
    }
    """
    assert class_warnings(src) == [(DEREF_MSG, 4)]


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


# --- derived-address chains: an array step is address arithmetic, not a load ---

def test_nested_array_indexing_does_not_warn():
    # grid[0] decays to a derived address into grid's own storage -- the same
    # always-non-null source the variable's own decay is.
    src = """
    fn main() -> int32 {
        let grid: int32[2][2] = [[1, 2], [3, 4]];
        return grid[1][1] - 4;
    }
    """
    assert class_warnings(src) == []


def test_array_field_indexing_does_not_warn():
    # unit.sizes decays to &unit.sizes[0]: named storage, never null.
    src = """
    struct box { corner: int32; sizes: int32[3]; }
    fn main() -> int32 {
        let unit: struct box;
        unit.sizes[2] = 5;
        return unit.sizes[2] - 5;
    }
    """
    assert class_warnings(src) == []


def test_flexible_array_member_indexing_does_not_warn():
    # p->data is p + offset, the derived address `p + n` is; only the arrow
    # itself needs a proof, and the assertion supplies it.
    src = """
    struct packet { length: uint64; data: int32[]; }
    fn fill(p: struct packet*) {
        p!->data[0] = 1;
    }
    fn main() -> int32 { return 0; }
    """
    assert class_warnings(src) == []


def test_flexible_array_member_charges_only_the_arrow():
    # An unproven arrow warns for p; the tail index adds no second site.
    src = """
    struct packet { length: uint64; data: int32[]; }
    fn peek(p: struct packet*) -> int32 { return p->data[0]; }
    fn main() -> int32 { return 0; }
    """
    assert [line for _, line in class_warnings(src)] == [3]


def test_union_array_member_indexing_does_not_warn():
    src = """
    union value { i: int64; b: uint8[8]; }
    fn low(v: union value*) -> uint8 { return v!->b[0]; }
    fn main() -> int32 { return 0; }
    """
    assert class_warnings(src) == []


def test_pointer_hop_in_a_chain_still_warns():
    # p![0] loads a pointer from memory; indexing that load is not address
    # arithmetic, so the outer index is an unproven site.
    src = """
    fn peek(p: int32**) -> int32 { return p![0][1]; }
    fn main() -> int32 { return 0; }
    """
    assert [line for _, line in class_warnings(src)] == [2]


# --- fact lifetimes around stores: the RHS evaluates before the fact dies ---

def test_reassignment_rhs_reads_through_the_dying_fact():
    # `cur = cur->next` dereferences cur before the store overwrites it, so
    # the header-narrowed fact still covers the read.
    src = """
    struct node { value: int32; next: struct node*; }
    fn walk(head: struct node*) -> int32 {
        let cur = head;
        until (cur == null) { cur = cur->next; }
        return 0;
    }
    fn main() -> int32 { return 0; }
    """
    assert class_warnings(src) == []


def test_reassigned_local_fact_still_dies_with_the_store():
    src = """
    fn get(p: int32*) -> int32 {
        if (p != null) {
            p = null;
            return *p;
        }
        return 0;
    }
    fn main() -> int32 { return 0; }
    """
    assert [line for _, line in class_warnings(src)] == [5]


def test_pointer_compound_assign_keeps_the_fact():
    # `p += 1` is address arithmetic: a seeded fact survives it, straight-line
    # and across the loop back edge.
    src = """
    fn scan(start: uint8*) -> uint8 {
        let p = start!;
        let i: int32 = 0;
        while (i < 4) { p += 1; i = i + 1; }
        return *p;
    }
    fn main() -> int32 { return 0; }
    """
    assert class_warnings(src) == []


# --- -Wdead-code: statements silently dropped after a diverging construct ---

DEAD = "dead-code"


def dead_code_warnings(source: str) -> list[tuple[str, int]]:
    """The dead-code emissions of a source, as (message, line)."""
    return [(w.message, w.line)
            for w in generate(source).warnings if w.wclass == DEAD]


def test_code_after_return_warns():
    src = """
    fn main() -> int32 {
        return 0;
        let x: int32 = 1;
    }
    """
    assert dead_code_warnings(src) == [
        ("unreachable code: nothing runs after the 'return' above", 4),
    ]


def test_code_after_break_warns():
    src = """
    fn main() -> int32 {
        while (true) {
            break;
            let x: int32 = 1;
        }
        return 0;
    }
    """
    assert dead_code_warnings(src) == [
        ("unreachable code: nothing runs after 'break'", 5),
    ]


def test_code_after_continue_warns():
    src = """
    fn main() -> int32 {
        let i: int32 = 0;
        while (i < 3) {
            i = i + 1;
            continue;
            i = 100;
        }
        return i - 3;
    }
    """
    assert dead_code_warnings(src) == [
        ("unreachable code: nothing runs after 'continue'", 7),
    ]


def test_code_after_unreachable_warns():
    src = """
    fn main() -> int32 {
        if (true) { return 0; }
        unreachable;
        let x: int32 = 1;
    }
    """
    assert dead_code_warnings(src) == [
        ("unreachable code: nothing runs after 'unreachable'", 5),
    ]


def test_code_after_a_noreturn_call_warns():
    # An expression statement that terminated the block is necessarily a
    # direct call to a @noreturn function; the message never names the
    # callee (type-free, so generic re-emissions stay byte-identical).
    src = """
    @noreturn @extern fn abort();
    fn main() -> int32 {
        abort();
        return 0;
    }
    """
    assert dead_code_warnings(src) == [
        ("unreachable code: nothing runs after a call to a @noreturn function", 5),
    ]


def test_code_after_emit_warns():
    src = """
    fn main() -> int32 {
        let v = {
            emit 3;
            let d: int32 = 1;
        };
        return v - 3;
    }
    """
    assert dead_code_warnings(src) == [
        ("unreachable code: nothing runs after 'emit'", 5),
    ]


def test_code_after_a_diverging_if_else_warns():
    src = """
    fn main() -> int32 {
        let x: int32 = 1;
        if (x > 0) { return 1; } else { return 2; }
        let y: int32 = 0;
    }
    """
    assert dead_code_warnings(src) == [
        ("unreachable code: every path through the statement above diverges", 5),
    ]


def test_code_after_an_all_arms_diverging_case_warns():
    src = """
    fn main() -> int32 {
        let x: int32 = 1;
        case (x) {
            when 1: return 0;
            else:   return 2;
        }
        let y: int32 = 0;
    }
    """
    assert dead_code_warnings(src) == [
        ("unreachable code: every path through the statement above diverges", 8),
    ]


def test_code_after_a_diverging_bare_block_warns():
    src = """
    fn main() -> int32 {
        { return 0; }
        let x: int32 = 1;
    }
    """
    assert dead_code_warnings(src) == [
        ("unreachable code: every path through the statement above diverges", 4),
    ]


def test_one_warning_per_dead_region_at_its_first_statement():
    # Per-region granularity: the walk reports the first dead statement and
    # drops the rest of the region exactly as it always has.
    src = """
    fn main() -> int32 {
        return 0;
        let x: int32 = 1;
        let y: int32 = 2;
        let z: int32 = 3;
    }
    """
    assert dead_code_warnings(src) == [
        ("unreachable code: nothing runs after the 'return' above", 4),
    ]


def test_a_dead_defer_warns_and_never_runs():
    # A defer in a dead region is dead code like any other statement: it is
    # never registered, so its body cannot run at scope exit.
    src = """
    fn main() -> int32 {
        let x: int32 = 40;
        return x + 2;
        defer { x = 0; }
    }
    """
    assert dead_code_warnings(src) == [
        ("unreachable code: nothing runs after the 'return' above", 5),
    ]
    assert run(src) == 42


def test_code_after_a_breaking_forever_loop_stays_live():
    # The fold's gate: a `break` anywhere in the body keeps the loop's end
    # block reachable, so the code after it is live and never warns.
    src = """
    fn main() -> int32 {
        while (true) { break; }
        return 0;
    }
    """
    assert dead_code_warnings(src) == []


def test_code_after_a_forever_loop_warns():
    # Constant-condition folding removes the never-taken exit edge, so with
    # no `break` in the body nothing after the loop can run.
    src = """
    fn main() -> int32 {
        while (true) {
            if (false) { return 1; }
        }
        return 0;
    }
    """
    assert dead_code_warnings(src) == [
        ("unreachable code: nothing runs after a loop that never exits", 6),
    ]


def test_code_after_a_forever_until_loop_warns():
    # `until (false)` is the dual spelling; same fold, same warning.
    src = """
    fn main() -> int32 {
        until (false) {
            if (false) { return 1; }
        }
        return 0;
    }
    """
    assert dead_code_warnings(src) == [
        ("unreachable code: nothing runs after a loop that never exits", 6),
    ]


def test_forever_loop_warning_is_type_free_across_instantiations():
    # Byte-identical per instantiation, so the driver's print-time dedup
    # collapses the generic re-emissions to one diagnostic.
    src = """
    fn spin<T>(v: T) -> int32 {
        while (true) {
            if (false) { return 1; }
        }
        return 0;
    }
    fn main() -> int32 { return spin(1) + spin(true) - 2; }
    """
    assert dead_code_warnings(src) == [
        ("unreachable code: nothing runs after a loop that never exits", 6),
        ("unreachable code: nothing runs after a loop that never exits", 6),
    ]


def test_dead_tail_inside_a_live_static_if_arm_warns():
    # The statement-level @if walks its taken arm with its own skip, so the
    # dead tail reports there, not in the enclosing block's walk.
    src = """
    fn main() -> int32 {
        @if (1) {
            return 0;
            let x: int32 = 1;
        }
    }
    """
    assert dead_code_warnings(src) == [
        ("unreachable code: nothing runs after the 'return' above", 5),
    ]


def test_dead_static_if_branch_never_warns():
    # The not-taken @if branch is structurally unseen -- never walked, never
    # type-checked -- so nothing inside it can be reported as dead code.
    src = """
    fn main() -> int32 {
        @if (0) {
            return 0;
            let x: int32 = 1;
        }
        return 0;
    }
    """
    assert dead_code_warnings(src) == []


def test_generic_body_collects_identical_messages_per_instantiation():
    # The message is type-free, so both instantiations emit byte-identical
    # (source, line, message) entries and the driver's print-time dedup
    # collapses them to one diagnostic (see test_cli.py).
    src = """
    fn pick<T>(v: T) -> int32 {
        return 1;
        let dead: int32 = 0;
    }
    fn main() -> int32 { return pick(1) + pick(true) - 2; }
    """
    assert dead_code_warnings(src) == [
        ("unreachable code: nothing runs after the 'return' above", 4),
        ("unreachable code: nothing runs after the 'return' above", 4),
    ]


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


# --- selective -Werror=<class>: split_werror_classes and the error-level gate ---

def test_split_werror_classes_peels_the_attached_form():
    classes, rest = split_werror_classes(
        ["a.mc", "-Werror=extern-nonnull", "-Wall"])
    assert classes == ["extern-nonnull"]
    assert rest == ["a.mc", "-Wall"]


def test_split_werror_classes_leaves_bare_werror_for_argparse():
    # A bare -Werror is the whole-build boolean; it must pass through so
    # argparse still sees it.
    classes, rest = split_werror_classes(["-Werror", "a.mc"])
    assert classes == []
    assert rest == ["-Werror", "a.mc"]


def test_split_werror_classes_is_repeatable():
    classes, rest = split_werror_classes(
        ["-Werror=dead-code", "-Werror=extern-nonnull"])
    assert classes == ["dead-code", "extern-nonnull"]
    assert rest == []


def test_split_werror_class_names_validate_through_parse_wflags():
    # The peeled names are validated by parse_wflags, so an unknown one fails
    # exactly like an unknown -W<name>.
    classes, _ = split_werror_classes(["-Werror=bogus"])
    with pytest.raises(ValueError, match="unknown warning class 'bogus'"):
        parse_wflags(classes)


def test_report_error_class_promotes_without_global_werror(capsys):
    # A class in error_classes fails the build and renders [-Werror=<name>]
    # even though global -Werror is off (the -Werror=<class> posture).
    notes = [Note("m", 1, "w.mc", UNCHECKED)]
    enabled = frozenset({UNCHECKED})
    error_classes = frozenset({UNCHECKED})
    assert _report_warnings(
        notes, Path("w.mc"), False, enabled, error_classes) is True
    assert capsys.readouterr().err == (
        "w.mc: error: line 1: m [-Werror=unchecked-dereference]\n")


def test_report_error_class_leaves_other_classes_as_warnings(capsys):
    # Only the promoted class fails; another enabled class still prints as a
    # plain warning and does not fail the build.
    notes = [Note("m", 1, "w.mc", UNCHECKED), Note("d", 2, "w.mc", "dead-code")]
    enabled = frozenset({UNCHECKED, "dead-code"})
    error_classes = frozenset({UNCHECKED})
    assert _report_warnings(
        notes, Path("w.mc"), False, enabled, error_classes) is True
    assert capsys.readouterr().err == (
        "w.mc: error: line 1: m [-Werror=unchecked-dereference]\n"
        "w.mc: warning: line 2: d [-Wdead-code]\n")


# --- -Wunused-result: a result produced and silently dropped ---------------
#
# A result carries an error; dropping one in statement position drops the
# error on the floor -- the accidental-ignore hole the error-handling design
# exists to close. Every consuming form (a `let` binding, the destructure,
# `try`/`except`, an argument, a return) counts as handled; only a truly-
# dropped result warns. The deliberate suppressor is a `_` binding.

UNUSED = "unused-result"
UNUSED_MSG = ("discarded result may carry an error (bind it, destructure it "
              "with 'let v, err =', handle it with 'try', or explicitly "
              "discard it with 'let _ = ...')")

# error decl + a result<T,E> function, a result<E> function, and a taker.
RESULT_PRELUDE = (
    "error e { OOPS }\n"
    "fn f(k: int32) -> result<int32, e> {\n"
    "    if (k == 0) { return error(e::OOPS); }\n"
    "    return ok(k);\n"
    "}\n"
    "fn g(bad: int32) -> result<e> {\n"
    "    if (bad) { return error(e::OOPS); }\n"
    "    return ok();\n"
    "}\n"
    "fn take(r: result<int32, e>) -> int32 { let a, b = r; return a; }\n"
)


def unused_result_warnings(source: str) -> list[tuple[str, int]]:
    """The unused-result emissions of a source, as (message, line)."""
    return [(w.message, w.line)
            for w in generate(source).warnings if w.wclass == UNUSED]


def caller(body: str) -> str:
    """Wrap a statement body in a caller after the shared result prelude.

    The body's first line is line 12 (the prelude is ten lines, then the
    caller signature).
    """
    return RESULT_PRELUDE + "fn caller() {\n" + body + "\n}\n"


def test_dropped_result_warns():
    # A bare call whose value is a result and is discarded.
    assert unused_result_warnings(caller("    f(1);")) == [(UNUSED_MSG, 12)]


def test_dropped_result_e_warns():
    # The error-only result<E> discarded in statement position warns too --
    # it is nothing but an error. (Its handled forms are `try`/`except`.)
    assert unused_result_warnings(caller("    g(1);")) == [(UNUSED_MSG, 12)]


def test_dropping_a_stored_result_variable_warns():
    # Not only calls: a result-typed variable evaluated and dropped as a
    # statement also loses its error.
    assert unused_result_warnings(
        caller("    let r = f(1);\n    r;")
    ) == [(UNUSED_MSG, 13)]


def test_binding_a_result_does_not_warn():
    assert unused_result_warnings(caller("    let r = f(1);")) == []


def test_destructuring_a_result_does_not_warn():
    assert unused_result_warnings(caller("    let v, err = f(1);")) == []


def test_underscore_binding_suppresses_the_warning():
    # The documented deliberate-discard escape hatch: bind to `_`.
    assert unused_result_warnings(caller("    let _ = f(1);")) == []


def test_try_propagate_statement_does_not_warn():
    # `try f();` consumes the result (propagate-or-continue). The caller must
    # be able to absorb the error, so it returns a matching result.
    src = (
        RESULT_PRELUDE
        + "fn caller() -> result<e> {\n"
        "    try f(1);\n"
        "    return ok();\n"
        "}\n"
    )
    assert unused_result_warnings(src) == []


def test_try_except_statement_does_not_warn():
    assert unused_result_warnings(
        caller("    try f(0) except (err) { };")
    ) == []


def test_try_fallback_binding_does_not_warn():
    assert unused_result_warnings(caller("    let d = try f(0) ?? 0;")) == []


def test_passing_a_result_as_an_argument_does_not_warn():
    # The result flows into `take` -- consumed, not dropped.
    assert unused_result_warnings(caller("    let n = take(f(1));")) == []


def test_returning_a_result_does_not_warn():
    src = (
        RESULT_PRELUDE
        + "fn passthru(k: int32) -> result<int32, e> { return f(k); }\n"
    )
    assert unused_result_warnings(src) == []


def test_a_dropped_non_result_never_warns():
    # An ordinary discarded expression statement is not the class's business.
    assert unused_result_warnings(caller("    let n = take(f(1));\n    n;")) == []


def test_unused_result_is_off_without_the_flag():
    # Collection is unconditional but the driver gates it: a bare -Werror
    # build (this repo's CI) never fails on a class it did not enable.
    from mcc.driver import _report_warnings
    note = Note(UNUSED_MSG, 11, "w.mc", UNUSED)
    assert _report_warnings([note], Path("w.mc"), True) is False


def test_unused_result_promotes_under_its_selective_werror(capsys):
    from mcc.driver import _report_warnings
    note = Note(UNUSED_MSG, 11, "w.mc", UNUSED)
    enabled = frozenset({UNUSED})
    error_classes = frozenset({UNUSED})
    assert _report_warnings(
        [note], Path("w.mc"), False, enabled, error_classes) is True
    assert capsys.readouterr().err == (
        f"w.mc: error: line 11: {UNUSED_MSG} [-Werror=unused-result]\n")


# --- -Wnoreturn-own: an own argument to a @noreturn callee ------------------
#
# A @noreturn callee's statement never ends, so the statement-end drop an
# own argument queued is discarded unemitted (flush_own_drops on the
# terminated path): the value's destructor provably never runs, a
# guaranteed leak. panic(f"...") is the archetype -- harmless by
# construction (the process is dying), so the class is a visibility
# diagnostic. The detection is the drop machinery's own judgment: whatever
# never queues a drop (a plain value, a destructor-less own, a callee that
# returns) stays silent.

NR_OWN = "noreturn-own"
NR_OWN_MSG = ("own value passed to a @noreturn function is never destroyed: "
              "the call never returns, so the value's statement-end cleanup "
              "never runs and it leaks (pass a plain value, or bind it to a "
              "let first to make the leak explicit)")

# A destructor-carrying own producer, a destructor-less one, and a
# @noreturn sink over a hidden-ref const struct -- all import-free (the
# constant-true loop diverges, satisfying @noreturn).
NR_PRELUDE = (
    "struct res { id: int32; }\n"
    "fn res::destructor(self: &res) { }\n"
    "fn mk() -> own res { return move(res { id = 1 }); }\n"
    "struct bare { id: int32; }\n"
    "fn mk_bare() -> own bare { return move(bare { id = 1 }); }\n"
    "@noreturn fn die(const r: res) { while (true) {} }\n"
)


def noreturn_own_warnings(source: str) -> list[tuple[str, int]]:
    """The noreturn-own emissions of a source, as (message, line)."""
    return [(w.message, w.line)
            for w in generate(source).warnings if w.wclass == NR_OWN]


def noreturn_own_warnings_io(source: str) -> list[tuple[str, int]]:
    """Like noreturn_own_warnings, but resolved like a real stdlib build --
    the import graph and the implicit runtime prelude merged in, so
    f-string/panic formatting resolves through the runtime modules."""
    from helpers import _resolve
    cg = CodeGen(_resolve(source), "t")
    cg.generate()
    return [(w.message, w.line)
            for w in cg.warnings if w.wclass == NR_OWN]


def test_an_own_argument_to_a_noreturn_callee_warns():
    # The direct marshal path: a concrete @noreturn taking the own call's
    # result by hidden const reference.
    src = NR_PRELUDE + "fn main() -> int32 { die(mk()); }\n"
    assert noreturn_own_warnings(src) == [(NR_OWN_MSG, 7)]


def test_an_own_argument_through_an_overload_set_warns():
    # The set path: the winner's @noreturn is known only after resolution.
    src = (
        NR_PRELUDE
        + "@noreturn fn die(n: int32) { while (true) {} }\n"
        "fn main() -> int32 { die(mk()); }\n"
    )
    assert noreturn_own_warnings(src) == [(NR_OWN_MSG, 8)]


def test_a_rendered_fstring_to_panic_warns():
    # The user's archetype: panic(f"...") renders an own string (the
    # synthesized slice::format call) that the dying path never destroys.
    src = (
        'import "std/io";\n'
        "fn main() -> int32 {\n"
        "    let x = 1 as int32;\n"
        '    panic(f"x = {x}");\n'
        "}\n"
    )
    assert noreturn_own_warnings_io(src) == [(NR_OWN_MSG, 4)]


def test_an_own_hole_temporary_at_a_noreturn_collector_warns():
    # A @noreturn @format collector: the f-string splices, and the hole's
    # own temporary is queued at winner emission -- also a guaranteed leak.
    src = (
        'import "std/io";\n'
        + NR_PRELUDE
        + "fn format(str: &string, const value: res, const modifier: slice<char>) {\n"
        "    format(str, value.id, modifier);\n"
        "}\n"
        "@noreturn\n"
        "fn logdie(@format const fmt: slice<const char>, args...) {\n"
        "    while (true) {}\n"
        "}\n"
        'fn main() -> int32 { logdie(f"lost {mk()}"); }\n'
    )
    assert noreturn_own_warnings_io(src) == [(NR_OWN_MSG, 15)]


def test_an_assert_message_does_not_warn():
    # The contrast case: assert returns on the passing path, so the
    # rendered message's statement-end drop runs normally.
    src = (
        'import "std/io";\n'
        "fn main() -> int32 {\n"
        "    let x = 1 as int32;\n"
        '    assert(x > 0, f"x = {x}");\n'
        "    return 0;\n"
        "}\n"
    )
    assert noreturn_own_warnings_io(src) == []


def test_a_plain_message_to_panic_does_not_warn():
    src = 'import "std/io";\nfn main() -> int32 { panic("plain"); }\n'
    assert noreturn_own_warnings_io(src) == []


def test_a_destructorless_own_value_does_not_warn():
    # Nothing to destroy, nothing queued -- silent, consistent with the
    # drop machinery everywhere else.
    src = (
        NR_PRELUDE
        + "@noreturn fn drop_bare(const b: bare) { while (true) {} }\n"
        "fn main() -> int32 { drop_bare(mk_bare()); }\n"
    )
    assert noreturn_own_warnings(src) == []


def test_a_returning_callee_does_not_warn():
    # The class is @noreturn-specific: an ordinary callee's own argument
    # drops at statement end as designed.
    src = (
        NR_PRELUDE
        + "fn use(const r: res) -> int32 { return r.id; }\n"
        "fn main() -> int32 { return use(mk()); }\n"
    )
    assert noreturn_own_warnings(src) == []


def test_an_indirect_call_never_warns():
    # @noreturn is not part of a function type, so a call through a
    # function-pointer value is never known to diverge -- out of the
    # class's reach, deliberately.
    src = (
        NR_PRELUDE
        + "fn main() -> int32 {\n"
        "    let g = die;\n"
        "    g(mk());\n"
        "    return 0;\n"
        "}\n"
    )
    assert noreturn_own_warnings(src) == []


def test_noreturn_own_is_off_without_the_flag():
    # Collection is unconditional but the driver gates it: a bare -Werror
    # build never fails on a class it did not enable.
    note = Note(NR_OWN_MSG, 7, "w.mc", NR_OWN)
    assert _report_warnings([note], Path("w.mc"), True) is False


def test_noreturn_own_renders_its_flag_when_enabled(capsys):
    note = Note(NR_OWN_MSG, 7, "w.mc", NR_OWN)
    enabled = frozenset({NR_OWN})
    assert _report_warnings([note], Path("w.mc"), False, enabled) is False
    assert capsys.readouterr().err == (
        f"w.mc: warning: line 7: {NR_OWN_MSG} [-Wnoreturn-own]\n")


def test_noreturn_own_promotes_under_its_selective_werror(capsys):
    note = Note(NR_OWN_MSG, 7, "w.mc", NR_OWN)
    enabled = frozenset({NR_OWN})
    error_classes = frozenset({NR_OWN})
    assert _report_warnings(
        [note], Path("w.mc"), False, enabled, error_classes) is True
    assert capsys.readouterr().err == (
        f"w.mc: error: line 7: {NR_OWN_MSG} [-Werror=noreturn-own]\n")


def test_wall_enables_noreturn_own():
    assert NR_OWN in parse_wflags(["all"])


# --- -Wdestructor-copy: a bitwise copy of an owning value ------------------
#
# mcc has no copy constructor, so a bitwise copy of a value whose type declares
# a destructor makes two names alias one live resource (both would free it at
# cleanup). The class fires at the copy site -- an explicit `let b = a;` and,
# since Phase B, the by-value parameter copies a plain `const x: T` on an
# owning aggregate creates. move(...) is the sanctioned relinquishing spelling
# that exempts the site, as does taking the value by `const &` view.

DTOR_COPY = "destructor-copy"
DTOR_COPY_MSG = ("a value with a destructor is copied here, aliasing a live "
                 "resource (both copies would free it); hand it over with "
                 "'move(...)' or take it by 'const &' reference")

# An owning type, a destructor-less one, and both by-value and const-&
# consumers of each -- all import-free.
DC_PRELUDE = (
    "struct res { id: int32; }\n"
    "fn res::destructor(self: &res) { }\n"
    "struct bare { id: int32; }\n"
    "fn by_value(const r: res) -> int32 { return r.id; }\n"
    "fn by_ref(const r: &res) -> int32 { return r.id; }\n"
    "fn bare_by_value(const b: bare) -> int32 { return b.id; }\n"
)


def dtor_copy_warnings(source: str) -> list[tuple[str, int]]:
    """The destructor-copy emissions of a source, as (message, line)."""
    return [(w.message, w.line)
            for w in generate(source).warnings if w.wclass == DTOR_COPY]


def test_let_copy_of_an_owning_value_warns():
    # The explicit copy: `let b = a;` where a's type declares a destructor.
    src = (
        DC_PRELUDE
        + "fn main() -> int32 {\n"
        "    let a = res { id = 1 };\n"
        "    let b = a;\n"
        "    return b.id;\n"
        "}\n"
    )
    assert dtor_copy_warnings(src) == [(DTOR_COPY_MSG, 9)]


def test_let_copy_of_a_destructorless_value_is_silent():
    # No destructor family -> nothing to double-free -> no warning.
    src = (
        DC_PRELUDE
        + "fn main() -> int32 {\n"
        "    let a = bare { id = 1 };\n"
        "    let b = a;\n"
        "    return b.id;\n"
        "}\n"
    )
    assert dtor_copy_warnings(src) == []


def test_let_move_of_an_owning_value_is_exempt():
    # move(...) blesses the copy: the author relinquishes a deliberately.
    src = (
        DC_PRELUDE
        + "fn main() -> int32 {\n"
        "    let a = res { id = 1 };\n"
        "    let b = move(a);\n"
        "    return b.id;\n"
        "}\n"
    )
    assert dtor_copy_warnings(src) == []


def test_by_value_param_copy_of_an_owning_value_warns():
    # The Phase B site: a plain `const r: res` takes the argument by value, so
    # passing a live lvalue bit-copies an owning value.
    src = (
        DC_PRELUDE
        + "fn main() -> int32 {\n"
        "    let a = res { id = 1 };\n"
        "    return by_value(a);\n"
        "}\n"
    )
    assert dtor_copy_warnings(src) == [(DTOR_COPY_MSG, 9)]


def test_by_value_param_move_is_exempt():
    src = (
        DC_PRELUDE
        + "fn main() -> int32 {\n"
        "    let a = res { id = 1 };\n"
        "    return by_value(move(a));\n"
        "}\n"
    )
    assert dtor_copy_warnings(src) == []


def test_const_ref_param_does_not_copy():
    # A `const &` view shares the caller's storage: no copy, no warning.
    src = (
        DC_PRELUDE
        + "fn main() -> int32 {\n"
        "    let a = res { id = 1 };\n"
        "    return by_ref(a);\n"
        "}\n"
    )
    assert dtor_copy_warnings(src) == []


def test_by_value_param_of_a_destructorless_value_is_silent():
    src = (
        DC_PRELUDE
        + "fn main() -> int32 {\n"
        "    let a = bare { id = 1 };\n"
        "    return bare_by_value(a);\n"
        "}\n"
    )
    assert dtor_copy_warnings(src) == []


def test_own_call_initializer_is_not_a_copy():
    # `let s = make();` adopts the callee's handed-over value -- a transfer,
    # not an alias, so no destructor-copy warning.
    src = (
        DC_PRELUDE
        + "fn make() -> own res { return move(res { id = 1 }); }\n"
        "fn main() -> int32 {\n"
        "    let s = make();\n"
        "    return s.id;\n"
        "}\n"
    )
    assert dtor_copy_warnings(src) == []


def test_chained_own_receiver_is_not_a_copy():
    # `make().poke()` where poke takes a by-value owning `self` spills the
    # own-call temporary to a hidden `0recv...` local; that ephemeral,
    # consumed receiver is not a persistent alias, so no warning fires.
    src = (
        DC_PRELUDE
        + "fn make() -> own res { return move(res { id = 5 }); }\n"
        "fn res::poke(const self: &res) -> int32 { return self.id; }\n"
        "fn main() -> int32 {\n"
        "    return make().poke();\n"
        "}\n"
    )
    assert dtor_copy_warnings(src) == []


def test_destructor_copy_is_off_without_the_flag():
    note = Note(DTOR_COPY_MSG, 9, "w.mc", DTOR_COPY)
    assert _report_warnings([note], Path("w.mc"), True) is False


def test_destructor_copy_renders_its_flag_when_enabled(capsys):
    note = Note(DTOR_COPY_MSG, 9, "w.mc", DTOR_COPY)
    enabled = frozenset({DTOR_COPY})
    assert _report_warnings([note], Path("w.mc"), False, enabled) is False
    assert capsys.readouterr().err == (
        f"w.mc: warning: line 9: {DTOR_COPY_MSG} [-Wdestructor-copy]\n")


def test_wall_enables_destructor_copy():
    assert DTOR_COPY in parse_wflags(["all"])
