"""@removed(msg) tombstones: each use of a removed function is a hard compile
error carrying the migration message; the tombstone itself compiles clean."""

import pytest

from mcc.codegen import CodeGen
from mcc.errors import LangError
from helpers import compile_ir, parse, run


def generate(source: str) -> CodeGen:
    """Compile a source string and return the CodeGen, warnings and all."""
    cg = CodeGen(parse(source), "test")
    cg.generate()
    return cg


def removed_error(source: str) -> LangError:
    """Compile a failing source string and return the LangError."""
    with pytest.raises(LangError) as excinfo:
        compile_ir(source)
    return excinfo.value


# --- call sites error with the exact message, at the caller's line ---

def test_call_to_a_removed_concrete_function_errors():
    src = """
    @removed("use bytecopy instead")
    fn copy_bytes(dst: byte*, src: byte*, n: uint64);
    fn main() -> int32 {
        copy_bytes(0 as byte*, 0 as byte*, 0 as uint64);
        return 0;
    }
    """
    err = removed_error(src)
    assert str(err) == "line 5: 'copy_bytes' was removed: use bytecopy instead"


def test_call_to_a_removed_generic_function_errors():
    # The bodiless generic tombstone: the roadmap's motivating form.
    src = """
    @removed("use bytecopy instead")
    fn copy_bytes<T>(dst: T*, src: T*, n: uint64);
    fn main() -> int32 {
        copy_bytes(0 as int32*, 0 as int32*, 0 as uint64);
        return 0;
    }
    """
    err = removed_error(src)
    assert str(err) == "line 5: 'copy_bytes' was removed: use bytecopy instead"


def test_explicit_type_args_error_before_instantiation():
    src = """
    @removed("use bytecopy instead")
    fn copy_bytes<T>(dst: T*, src: T*, n: uint64);
    fn main() -> int32 {
        copy_bytes<int32>(0 as int32*, 0 as int32*, 0 as uint64);
        return 0;
    }
    """
    err = removed_error(src)
    assert str(err) == "line 5: 'copy_bytes' was removed: use bytecopy instead"
    assert err.notes == []  # no instantiation was attempted


def test_call_to_a_removed_extern_function_errors():
    # Retiring a libc binding is meaningful, so @removed on @extern is allowed.
    src = """
    @removed("use newapi instead")
    @extern fn oldapi(x: int32) -> int32;
    fn main() -> int32 { return oldapi(1); }
    """
    err = removed_error(src)
    assert str(err) == "line 4: 'oldapi' was removed: use newapi instead"


# --- function values and the for-in protocol error too ---

def test_function_value_of_a_removed_function_errors():
    src = """
    @removed("use g instead")
    fn f(x: int32) -> int32;
    fn main() -> int32 {
        let p: fn(int32) -> int32 = f;
        return 0;
    }
    """
    err = removed_error(src)
    assert str(err) == "line 5: 'f' was removed: use g instead"


def test_const_function_pointer_of_a_removed_function_errors():
    src = """
    @removed("use g instead")
    fn f(x: int32) -> int32;
    const p = f;
    fn main() -> int32 { return 0; }
    """
    err = removed_error(src)
    assert str(err) == "line 4: 'f' was removed: use g instead"


def test_for_in_over_a_removed_next_errors():
    # `_it` resolves through gen_call; `_next` is resolved separately by the
    # for-in protocol, so the tombstone check must ride along there too.
    src = """
    struct counter { n: int32; }
    struct counter_iter { cur: int32; stop: int32; }
    fn counter_it(c: struct counter*) -> struct counter_iter {
        let it: struct counter_iter;
        it.cur = 0; it.stop = c->n;
        return it;
    }
    @removed("iterate the replacement type")
    fn counter_next(it: struct counter_iter*, out: int32*) -> bool;
    fn main() -> int32 {
        let c: struct counter;
        c.n = 3;
        for v in c { }
        return 0;
    }
    """
    err = removed_error(src)
    assert str(err) == (
        "line 14: 'counter_next' was removed: iterate the replacement type"
    )


# --- shadowing: the check runs after variable/const resolution ---

def test_variable_shadowing_a_removed_name_still_works():
    src = """
    @removed("use g instead")
    fn f(x: int32) -> int32;
    fn main() -> int32 {
        let f: int32 = 42;
        return f;
    }
    """
    assert run(src) == 42


# --- the tombstone itself compiles clean (and warns nothing) ---

def test_uncalled_tombstone_compiles_clean():
    src = """
    @removed("use bytecopy instead")
    fn copy_bytes<T>(dst: T*, src: T*, n: uint64);
    fn main() -> int32 { return 0; }
    """
    cg = generate(src)
    assert cg.warnings == []  # errors abort; nothing rides the warning channel
    assert 'define i32 @"main"()' in str(cg.module)


def test_tombstone_signature_is_never_resolved():
    # The parse-don't-resolve payoff: a tombstone stays valid even when its
    # parameter types were deleted along with the implementation.
    src = """
    @removed("use fresh instead")
    fn stale(v: vanished_t) -> also_gone;
    fn main() -> int32 { return 0; }
    """
    assert 'define i32 @"main"()' in compile_ir(src)
    err = removed_error(src + "fn call_it() -> int32 { return stale(0); }")
    assert str(err) == "line 5: 'stale' was removed: use fresh instead"


def test_body_bearing_tombstone_is_allowed_and_its_body_is_dead():
    # The author may keep the body around briefly; it is never generated, so
    # even a body that no longer compiles cannot break the build.
    src = """
    @removed("use g instead")
    fn f(x: int32) -> int32 { return no_such_fn(x); }
    fn main() -> int32 { return 0; }
    """
    assert 'define i32 @"main"()' in compile_ir(src)


def test_uninstantiated_generic_body_calling_a_removed_fn_is_unchecked():
    # Single-pass compiler: a generic body is only checked when instantiated,
    # consistent with every other error inside a never-used template.
    src = """
    @removed("use g instead")
    fn f(x: int32) -> int32;
    fn wrap<T>(v: T) -> T { f(0); return v; }
    fn main() -> int32 { return 0; }
    """
    assert 'define i32 @"main"()' in compile_ir(src)


# --- instantiation backtraces attach to a removed call in a generic body ---

def test_removed_call_inside_a_generic_body_carries_the_backtrace():
    src = """
    @removed("use g instead")
    fn f(x: int32) -> int32;
    fn wrap<T>(v: T) -> T { f(0); return v; }
    fn main() -> int32 { return wrap(1 as int32); }
    """
    err = removed_error(src)
    assert str(err) == "line 4: 'f' was removed: use g instead"
    assert [(n.message, n.line) for n in err.notes] == [
        ("in instantiation of wrap<int32>", 5),
    ]


# --- registration: a tombstone claims the whole name ---

def test_removed_generic_plus_live_generic_overload_is_rejected():
    src = """
    @removed("use fresh instead")
    fn stale<T>(v: T) -> T;
    fn stale<T>(v: T*) -> T { return *v; }
    fn main() -> int32 { return 0; }
    """
    err = removed_error(src)
    assert str(err) == (
        "line 4: function 'stale' cannot be both @removed and live: "
        "a tombstone replaces the whole overload set"
    )


def test_live_generic_before_the_tombstone_is_rejected_too():
    # The same conflict in the other registration order.
    src = """
    fn stale<T>(v: T*) -> T { return *v; }
    @removed("use fresh instead")
    fn stale<T>(v: T) -> T;
    fn main() -> int32 { return 0; }
    """
    err = removed_error(src)
    assert str(err) == (
        "line 4: function 'stale' cannot be both @removed and live: "
        "a tombstone replaces the whole overload set"
    )


def test_tombstone_plus_live_concrete_definition_is_already_defined():
    src = """
    @removed("use fresh instead")
    fn stale(v: int32) -> int32;
    fn stale(v: int32) -> int32 { return v; }
    fn main() -> int32 { return 0; }
    """
    assert str(removed_error(src)) == "line 4: function 'stale' already defined"


def test_live_concrete_before_the_tombstone_is_already_defined_too():
    src = """
    fn stale(v: int32) -> int32 { return v; }
    @removed("use fresh instead")
    fn stale(v: int32) -> int32;
    fn main() -> int32 { return 0; }
    """
    assert str(removed_error(src)) == "line 4: function 'stale' already defined"


def test_duplicate_tombstones_for_one_name_are_already_defined():
    # The tombstone is name-keyed (its signature never resolves), so one
    # tombstone speaks for the whole former overload set.
    src = """
    @removed("use fresh instead")
    fn stale<T>(v: T) -> T;
    @removed("use fresh instead")
    fn stale<T>(v: T*) -> T;
    fn main() -> int32 { return 0; }
    """
    assert str(removed_error(src)) == "line 5: function 'stale' already defined"


# --- parsing: the tombstone form and the rejected annotation combos ---

def test_bodiless_generic_tombstone_parses():
    (fn,) = parse(
        '@removed("use bytecopy instead")\n'
        "fn copy_bytes<T>(dst: T*, src: T*, n: uint64);\n"
    ).functions
    assert fn.removed_msg == "use bytecopy instead"
    assert fn.type_params == ["T"]
    assert fn.proto and fn.body == []


def test_plain_generic_proto_is_still_rejected():
    # The carve-out is @removed-only; the original rejection (and its exact
    # wording) stands for every other generic prototype.
    with pytest.raises(
        LangError,
        match="a generic function cannot be a bodyless prototype "
        r"\(its body must travel to be instantiated\)",
    ):
        parse("fn copy_bytes<T>(dst: T*, src: T*, n: uint64);")


def test_removed_message_processes_escapes():
    src = r"""
    @removed("gone \"now\": use g")
    fn f() -> int32;
    fn main() -> int32 { return f(); }
    """
    assert removed_error(src).message == "'f' was removed: gone \"now\": use g"


def test_removed_needs_a_non_empty_message():
    with pytest.raises(LangError, match="@removed needs a non-empty message"):
        parse('@removed("") fn f() -> int32;')


def test_removed_only_applies_to_functions():
    with pytest.raises(LangError, match="@removed only applies to functions"):
        parse('@removed("gone") struct point { x: int32; }')


def test_removed_and_deprecated_cannot_be_combined():
    with pytest.raises(
        LangError,
        match=r"@deprecated and @removed cannot be combined \(a removed "
        r"function already errors at every call site\)",
    ):
        parse('@deprecated("soon") @removed("now") fn f() -> int32;')


def test_removed_and_inline_cannot_be_combined():
    with pytest.raises(
        LangError,
        match=r"@removed and @inline cannot be combined \(a removed function "
        r"is uncallable, so there is nothing to inline\)",
    ):
        parse('@removed("gone") @inline fn f() -> int32 { return 0; }')


def test_removed_and_asm_cannot_be_combined():
    with pytest.raises(
        LangError,
        match=r"@removed and @asm cannot be combined \(a removed function "
        r"is uncallable, so an asm body is meaningless\)",
    ):
        parse('@removed("gone") @asm fn f() -> int32 { "nop" }')


def test_removed_and_static_cannot_be_combined():
    with pytest.raises(
        LangError,
        match=r"@removed and @static cannot be combined \(a file-local "
        r"tombstone serves no caller in another file\)",
    ):
        parse('@removed("gone") @static fn f() -> int32;')
