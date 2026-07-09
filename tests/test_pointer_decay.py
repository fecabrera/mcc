"""Pointer decay into ``const``/``mut`` parameters.

A proven-non-null ``T*`` argument at a hidden-reference slot (a ``const``
struct parameter or a ``mut`` parameter of any type) forwards the pointer
value itself instead of forming ``&lvalue``: the callee sees the pointee,
read-only or writable, without the caller writing ``*var``.
"""

import re

import pytest

from mcc.errors import LangError
from helpers import compile_ir, run

POINT = "struct point { x: int32; y: int32; }\n"

BOX = "struct box<T> { value: T; }\n"


# ------------------------------------------------------------- concrete path


def test_pointer_decays_into_concrete_mut_struct():
    assert run(
        POINT + "fn bump(mut p: struct point) { p.x += 1; }\n"
        "fn main() -> int32 {\n"
        "    let v = point { x = 1, y = 2 };\n"
        "    let ptr = &v;\n"  # let-seeded proof: &v is never null
        "    bump(ptr);\n"
        "    return v.x;\n"
        "}"
    ) == 2


def test_pointer_decays_into_concrete_mut_scalar():
    assert run(
        "fn set(mut n: int32) { n = 7; }\n"
        "fn main() -> int32 {\n"
        "    let x: int32 = 0;\n"
        "    let p = &x;\n"
        "    set(p);\n"
        "    return x;\n"
        "}"
    ) == 7


def test_pointer_decays_into_concrete_const_struct():
    assert run(
        POINT + "fn sum(const p: struct point) -> int32 { return p.x + p.y; }\n"
        "fn main() -> int32 {\n"
        "    let v = point { x = 1, y = 2 };\n"
        "    let q = &v;\n"
        "    return sum(q) + sum(&v);\n"  # lvalue pointer and rvalue &v alike
        "}"
    ) == 6


def test_rvalue_pointer_decays_into_mut():
    # An rvalue T* may decay into mut T -- the pointee is real storage even
    # when the pointer expression is a temporary -- deliberately unlike the
    # plain rule that a mut argument must be an lvalue.
    assert run(
        "fn set(mut n: int32) { n = 9; }\n"
        "fn main() -> int32 {\n"
        "    let x: int32 = 0;\n"
        "    set(&x);\n"
        "    return x;\n"
        "}"
    ) == 9


def test_call_result_pointer_decays_with_assert():
    assert run(
        POINT + "fn pick(a: struct point*) -> struct point* { return a; }\n"
        "fn set(mut p: struct point) { p.x = 5; }\n"
        "fn main() -> int32 {\n"
        "    let v = point { x = 1, y = 2 };\n"
        "    set(pick(&v)!);\n"
        "    return v.x;\n"
        "}"
    ) == 5


def test_double_pointer_decays_one_level_into_mut_pointer():
    # T** decays into mut T* -- exactly one level; the callee repoints the
    # caller's pointer through the decayed reference.
    assert run(
        "fn repoint(mut q: int32*, target: int32*) { q = target; }\n"
        "fn main() -> int32 {\n"
        "    let a: int32[2] = [7, 9];\n"
        "    let p = &a[0];\n"
        "    let pp = &p;\n"
        "    repoint(pp, &a[1]);\n"
        "    return *p;\n"
        "}"
    ) == 9


def test_explicit_deref_still_works_without_proof():
    # `*p` at a mut slot is the visible-dereference spelling; it needs no
    # non-null proof (the deref itself is the programmer's claim) and stays
    # legal alongside decay.
    compile_ir(
        "fn set(mut n: int32) { n = 3; }\n"
        "fn g(p: int32*) { set(*p); }\n"
        "fn main() -> int32 { return 0; }"
    )


def test_const_pointer_storage_still_decays():
    # The const-ness (and volatility, packedness) of the *pointer's own
    # storage* does not block decay: those facts describe the pointer, not
    # the pointee the callee receives.
    assert run(
        "fn set(mut n: int32) { n = 5; }\n"
        "fn g(const p: int32*) { set(p!); }\n"
        "fn main() -> int32 {\n"
        "    let x: int32 = 0;\n"
        "    g(&x);\n"
        "    return x;\n"
        "}"
    ) == 5


def test_volatile_pointer_storage_gets_a_volatile_load():
    ir_text = compile_ir(
        "@extern @volatile let r: int32*;\n"
        "fn set(mut n: int32) { n = 1; }\n"
        "fn main() -> int32 { set(r!); return 0; }"
    )
    assert "load volatile" in ir_text


# -------------------------------------------------------------------- proofs


def test_nonnull_param_proof_decays():
    assert run(
        "fn set(mut n: int32) { n = 3; }\n"
        "fn g(@nonnull p: int32*) { set(p); }\n"
        "fn main() -> int32 {\n"
        "    let x: int32 = 0;\n"
        "    g(&x);\n"
        "    return x;\n"
        "}"
    ) == 3


def test_flow_narrowed_pointer_decays():
    assert run(
        "fn set(mut n: int32) { n = 4; }\n"
        "fn g(p: int32*) -> int32 {\n"
        "    if (p == null) { return -1; }\n"
        "    set(p);\n"
        "    return 1;\n"
        "}\n"
        "fn main() -> int32 {\n"
        "    let x: int32 = 0;\n"
        "    return g(&x) * x;\n"
        "}"
    ) == 4


def test_assert_hatch_decays():
    compile_ir(
        "fn set(mut n: int32) {}\n"
        "fn g(p: int32*) { set(p!); }\n"
        "fn main() -> int32 { return 0; }"
    )


def test_narrowing_survives_a_decayed_call():
    # The pointer is passed by value, so the callee cannot store null into
    # it: the flow-narrowed fact survives, and a second decayed call needs
    # no fresh guard (contrast lending the pointer variable itself as mut,
    # which kills the fact).
    assert run(
        "fn bump(mut n: int32) { n += 1; }\n"
        "fn g(p: int32*) -> int32 {\n"
        "    if (p == null) { return -1; }\n"
        "    bump(p);\n"
        "    bump(p);\n"
        "    return 1;\n"
        "}\n"
        "fn main() -> int32 {\n"
        "    let x: int32 = 0;\n"
        "    return g(&x) * x;\n"
        "}"
    ) == 2


def test_direct_mut_lend_of_the_pointer_still_kills_narrowing():
    # Lending the pointer variable itself (mut int32* slot: an exact type
    # match, no decay) lets the callee store null through the reference, so
    # the narrowed fact dies and a later decaying call needs new proof.
    message = re.escape(
        "cannot pass a possibly-null int32* as argument 1 of 'bump'"
    )
    with pytest.raises(LangError, match=message):
        compile_ir(
            "fn bump(mut n: int32) { n += 1; }\n"
            "fn clear(mut q: int32*) { q = null; }\n"
            "fn g(p: int32*) {\n"
            "    if (p == null) { return; }\n"
            "    clear(p);\n"
            "    bump(p);\n"
            "}\n"
            "fn main() -> int32 { return 0; }"
        )


# ----------------------------------------------------------------- rejections


def test_unproven_pointer_at_mut_slot_error_is_pinned():
    source = (
        "struct box { value: int32; }\n"
        "fn f(mut b: struct box) {}\n"
        "fn g(p: struct box*) { f(p); }\n"
        "fn main() -> int32 { return 0; }"
    )
    with pytest.raises(LangError) as err:
        compile_ir(source)
    assert str(err.value) == (
        "line 3: cannot pass a possibly-null box* as argument 1 of 'f': "
        "decaying into a mut box parameter forms a reference, which is "
        "never null (narrow with a null check or assert with postfix '!')"
    )


def test_unproven_pointer_at_const_slot_error_is_pinned():
    source = (
        "struct box { value: int32; }\n"
        "fn f(const b: struct box) {}\n"
        "fn g(p: struct box*) { f(p); }\n"
        "fn main() -> int32 { return 0; }"
    )
    with pytest.raises(LangError) as err:
        compile_ir(source)
    assert str(err.value) == (
        "line 3: cannot pass a possibly-null box* as argument 1 of 'f': "
        "decaying into a const box parameter forms a reference, which is "
        "never null (narrow with a null check or assert with postfix '!')"
    )


def test_double_pointer_does_not_decay_twice():
    # Exactly one level: the pointee of int32** is int32*, never int32.
    with pytest.raises(LangError, match="expected a int32 lvalue, got int32\\*\\*"):
        compile_ir(
            "fn set(mut n: int32) {}\n"
            "fn main() -> int32 {\n"
            "    let x: int32 = 0;\n"
            "    let p = &x;\n"
            "    let pp = &p;\n"
            "    set(pp);\n"
            "    return 0;\n"
            "}"
        )


def test_const_scalar_slot_does_not_decay():
    # A const scalar parameter is a by-value copy with no hidden reference
    # behind it, so there is no slot for the pointer to decay into.
    with pytest.raises(LangError, match="expected int32, got int32\\*"):
        compile_ir(
            "fn f(const n: int32) -> int32 { return n; }\n"
            "fn g(@nonnull p: int32*) -> int32 { return f(p); }\n"
            "fn main() -> int32 { return 0; }"
        )


def test_plain_by_value_slot_does_not_decay():
    # A plain T parameter still needs an explicit *var: the copy stays
    # visible at the call site.
    with pytest.raises(LangError, match="expected point, got point\\*"):
        compile_ir(
            POINT + "fn take(v: struct point) {}\n"
            "fn g(@nonnull p: struct point*) { take(p); }\n"
            "fn main() -> int32 { return 0; }"
        )


def test_null_literal_does_not_decay():
    # `null` is exactly what a decayed reference can never be.
    with pytest.raises(LangError, match="argument 1 of 'f' is not assignable"):
        compile_ir(
            "fn f(mut n: int32) {}\n"
            "fn main() -> int32 { f(null); return 0; }"
        )


def test_string_literal_does_not_decay_into_mut():
    # A string literal's bytes live in a constant global; a mut callee
    # could write through the decayed reference, so it never decays.
    with pytest.raises(LangError, match="argument 1 of 'f' is not assignable"):
        compile_ir(
            "fn f(mut c: char) {}\n"
            'fn main() -> int32 { f("hi"); return 0; }'
        )


# ------------------------------------------------------------------- generics


def test_generic_inference_through_decay_at_const_slot():
    # `box<int32>*` at `const b: box<T>` binds T = int32 through the
    # pointee -- previously "cannot infer type parameter(s) T".
    assert run(
        BOX + "fn get<T>(const b: struct box<T>) -> T { return b.value; }\n"
        "fn main() -> int32 {\n"
        "    let b = box { value = 41 as int32 };\n"
        "    let p = &b;\n"
        "    return get(p) + 1;\n"
        "}"
    ) == 42


def test_generic_inference_through_decay_at_mut_slot():
    assert run(
        BOX + "fn clear<T>(mut b: struct box<T>) { b.value = 0 as T; }\n"
        "fn main() -> int32 {\n"
        "    let b = box { value = 7 as int32 };\n"
        "    let p = &b;\n"
        "    clear(p);\n"
        "    return b.value;\n"
        "}"
    ) == 0


def test_generic_rvalue_pointer_decays_into_mut():
    # Single candidate: the direct reading (T = int32*) is a dead end at an
    # unaddressed mut position, so the decay reading (T = int32) wins.
    assert run(
        "fn set<T>(mut a: T) { a = 7 as T; }\n"
        "fn main() -> int32 {\n"
        "    let x: int32 = 0;\n"
        "    set(&x);\n"
        "    return x;\n"
        "}"
    ) == 7


def test_decay_binding_beats_untyped_literal_at_struct_receiver():
    # A struct-shaped pattern (box<T>, no stars) can never instantiate to a
    # pointer, so a pointer lvalue there binds T through the decay reading
    # even when another argument -- an untyped literal leaning int32 --
    # already gave the direct pass a "successful" (but unemittable) binding.
    # The libmc stage-3 shape: dict_set(d, "k", 10) on a heap dict<uint64>*.
    assert run(
        BOX + "fn put<T>(mut b: struct box<T>, v: T) { b.value = v; }\n"
        "fn main() -> int32 {\n"
        "    let b = box { value = 0 as uint64 };\n"
        "    let p = &b;\n"
        "    put(p, 9);\n"  # 9 leans int32; T must come from box<uint64>
        "    return b.value as int32;\n"
        "}"
    ) == 9


def test_bare_mut_type_param_still_binds_the_pointer_itself():
    # The counterweight: a bare `mut a: T` pattern with a pointer LVALUE
    # keeps its direct reading (T = int32*), mutating the caller's pointer,
    # not the pointee.
    assert run(
        "fn redirect<T>(mut a: T, b: T) { a = b; }\n"
        "fn main() -> int32 {\n"
        "    let x: int32 = 1;\n"
        "    let y: int32 = 5;\n"
        "    let p = &x;\n"
        "    redirect(p, &y);\n"
        "    return *p;\n"
        "}"
    ) == 5


def test_generic_mixed_direct_lend_and_decay():
    # One mut position takes the caller's own lvalue, the other decays a
    # proven pointer, in the same call.
    assert run(
        "fn assign<T>(mut a: T, mut b: T) { a = b; }\n"
        "fn main() -> int32 {\n"
        "    let x: int32 = 0;\n"
        "    let y: int32 = 9;\n"
        "    let p = &y;\n"
        "    assign(x, p);\n"
        "    return x;\n"
        "}"
    ) == 9


def test_generic_decay_infers_through_list_pointer():
    # The libmc migration shape: a heap-style list<int32>* argument infers
    # T at a const list<T> slot while the mut slot takes a plain lvalue.
    assert run(
        'import "std/list";\n'
        "fn steal_len<T>(mut dst: struct list<T>, const src: struct list<T>) {\n"
        "    dst.length = src.length;\n"
        "}\n"
        "fn main() -> int32 {\n"
        "    let a: struct list<int32>;\n"
        "    list_init(&a, 4);\n"
        "    defer list_destroy(&a);\n"
        "    list_push(&a, 1);\n"
        "    list_push(&a, 2);\n"
        "    let b: struct list<int32>;\n"
        "    list_init(&b, 4);\n"
        "    defer list_destroy(&b);\n"
        "    let p = &a;\n"
        "    steal_len(b, p);\n"
        "    return b.length as int32;\n"
        "}"
    ) == 2


def test_generic_decay_with_both_arguments_ampersand_shaped():
    # The &x-shaped call sites the libmc migration leans on: both hidden
    # slots receive rvalue pointers, both prove non-null for free.
    assert run(
        'import "std/list";\n'
        "fn steal_len<T>(mut dst: struct list<T>, const src: struct list<T>) {\n"
        "    dst.length = src.length;\n"
        "}\n"
        "fn main() -> int32 {\n"
        "    let a: struct list<int32>;\n"
        "    list_init(&a, 4);\n"
        "    defer list_destroy(&a);\n"
        "    list_push(&a, 5);\n"
        "    let b: struct list<int32>;\n"
        "    list_init(&b, 4);\n"
        "    defer list_destroy(&b);\n"
        "    steal_len(&b, &a);\n"
        "    return b.length as int32;\n"
        "}"
    ) == 1


def test_generic_unproven_pointer_error_is_pinned():
    source = (
        BOX + "fn get<T>(const b: struct box<T>) -> T { return b.value; }\n"
        "fn g(p: struct box<int32>*) -> int32 { return get(p); }\n"
        "fn main() -> int32 { return 0; }"
    )
    with pytest.raises(LangError) as err:
        compile_ir(source)
    assert str(err.value) == (
        "line 3: cannot pass a possibly-null box<int32>* as argument 1 of "
        "'get': decaying into a const box<int32> parameter forms a "
        "reference, which is never null (narrow with a null check or "
        "assert with postfix '!')"
    )


def test_generic_arity_error_survives_the_decay_retry():
    # A failed direct resolution retries with the decay reading; when that
    # cannot help, the original error is re-raised untouched.
    with pytest.raises(LangError, match=r"'f' expects 1 argument\(s\), got 2"):
        compile_ir(
            "fn f<T>(mut a: T) {}\n"
            "fn main() -> int32 { let x: int32 = 0; f(x, 1); return 0; }"
        )


def test_cannot_infer_is_preserved_without_a_pointer_to_decay():
    with pytest.raises(LangError, match=r"cannot infer type parameter\(s\) T"):
        compile_ir(
            BOX + "fn get<T>(const b: struct box<T>) -> T { return b.value; }\n"
            "fn main() -> int32 { get(5); return 0; }"
        )


# ------------------------------------------------- two-tier overload viability


def test_exact_pointer_overload_beats_decay():
    # f(x: T*) beside f(mut x: T): a pointer argument goes to the pointer
    # overload -- decayed candidates enter resolution only when no candidate
    # matches the pointer type directly.
    assert run(
        "fn f<T>(x: T*) -> int32 { return 1; }\n"
        "fn f<T>(mut x: T) -> int32 { return 2; }\n"
        "fn main() -> int32 {\n"
        "    let x: int32 = 0;\n"
        "    let p = &x;\n"
        "    return f(p) * 10 + f(&x);\n"
        "}"
    ) == 11


def test_decay_tier_mixes_direct_lend_and_decayed_positions():
    # Once no candidate matches directly, the decay tier re-resolves; the
    # winner may take one position as a direct lend and another decayed.
    assert run(
        "fn f<T>(mut a: T, mut b: T) -> int32 { a = b; return 1; }\n"
        "fn f<T>(mut a: T, b: slice<T>) -> int32 { return 2; }\n"
        "fn main() -> int32 {\n"
        "    let x: int32 = 0;\n"
        "    let y: int32 = 9;\n"
        "    let p = &y;\n"
        "    let got = f(x, p);\n"
        "    return got * 10 + x;\n"
        "}"
    ) == 19
