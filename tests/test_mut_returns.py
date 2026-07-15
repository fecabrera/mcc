"""``mut`` returns: functions returning lvalues (``-> mut T``).

A ``-> mut T`` call is an lvalue expression -- assignable, projectable, and
re-lendable as a ``mut`` argument -- and loads eagerly in value context. The
returned reference may only be formed from a ``mut``/pointer parameter or a
global (never this call's frame), traced through members, elements,
dereferences, and other ``mut``-returning calls.
"""

import pytest

from mcc.codegen import CodeGen
from mcc.driver import emit_interface
from mcc.errors import LangError
from mcc.interface import render_interface
from mcc.lexer import tokenize
from mcc.parser import Parser
from helpers import compile_ir, parse, run, run_path


# A tiny container: a mut-returning element accessor over a pointer field,
# the accessor shape the formation rule was designed for.
BUF = (
    "struct buf { data: char*; length: uint64; }\n"
    "fn buf_at(mut self: struct buf, i: uint64) -> mut char {\n"
    "    return self.data[i];\n"
    "}\n"
)

# main() building a 'abc' buffer named b over a local byte array.
SETUP = (
    "    let bytes: char[4];\n"
    "    bytes[0] = 'a'; bytes[1] = 'b'; bytes[2] = 'c'; bytes[3] = '\\0';\n"
    "    let b = struct buf { data = &bytes[0], length = 3 };\n"
)

FORMATION = "a reference return must be formed from a reference or pointer parameter"


def iface(source: str) -> str:
    """Render the interface stub for an import-free source string."""
    program = Parser(tokenize(source)).parse_program()
    imports = list(program.imports)
    cg = CodeGen(program, "test")
    cg.generate()
    return render_interface(cg, source, imports)


# --------------------------------------------------------------------- parser


def test_mut_return_parses():
    (func,) = parse("fn f(mut x: int32) -> mut int32 { return x; }").functions
    assert func.mut_return
    assert func.ret_type.name == "int32"


def test_plain_return_is_not_mut():
    (func,) = parse("fn f(x: int32) -> int32 { return x; }").functions
    assert not func.mut_return


def test_bodyless_prototype_carries_mut_return():
    # Interface stubs re-emit `-> mut`; the proto parses and keeps the flag.
    (func,) = parse("fn f(mut x: int32) -> mut int32;").functions
    assert func.proto and func.mut_return


def test_mut_return_rejected_on_extern():
    with pytest.raises(
        LangError, match="a reference return is not allowed on @extern functions"
    ):
        parse("@extern fn f(n: int32) -> mut int32;")


def test_mut_return_rejected_on_asm():
    with pytest.raises(
        LangError, match="a reference return is not allowed on @asm functions"
    ):
        parse('@asm fn f(n: int32) -> mut int32 { "nop" }')


def test_fn_pointer_type_spells_mut_return():
    # The fn(...) -> mut T *type* spells a mut return (see
    # test_mut_return_fn_types.py for the full behavior).
    program = parse("fn main() { let g: fn(int32) -> mut int32; }")
    (let,) = program.functions[0].body
    assert let.type_name.ret.mut
    assert str(let.type_name) == "fn(int32) -> &int32"


# ------------------------------------------------------------ declaration bans


def test_mut_void_return_rejected():
    with pytest.raises(LangError, match="cannot return a reference to void"):
        compile_ir("fn f() -> mut void { }")


def test_main_cannot_return_mut():
    with pytest.raises(LangError, match="'main' cannot return a reference"):
        compile_ir("fn main() -> mut int32 { return 0; }")


def test_overloads_differing_only_in_mut_return_collide():
    # The return type never distinguishes overloads; the mut marker rides
    # on the return, so a `-> T` / `-> mut T` pair spells one signature.
    with pytest.raises(
        LangError, match="overloads must differ in parameter types"
    ):
        compile_ir(
            "@static let g: int32 = 0;\n"
            "fn f(x: int32) -> int32 { return x; }\n"
            "fn f(x: int32) -> mut int32 { return g; }\n"
            "fn main() -> int32 { return 0; }"
        )


def test_generic_overloads_differing_only_in_mut_return_collide():
    with pytest.raises(
        LangError, match="overloads must differ in parameter patterns"
    ):
        compile_ir(
            "fn f<T>(mut x: T) -> T { return x; }\n"
            "fn f<T>(mut x: T) -> mut T { return x; }\n"
            "fn main() -> int32 { return 0; }"
        )


def test_mut_return_lowers_to_pointer_return():
    ir_text = compile_ir(
        "fn pick(mut x: int32) -> mut int32 { return x; }\n"
        "fn main() -> int32 { return 0; }"
    )
    assert 'define i32* @"pick"(i32* ' in ir_text


# ------------------------------------------------------------- formation rule


def test_mut_param_root_direct():
    # A mut parameter is the caller's own storage, legal as the returned
    # lvalue itself (zero hops).
    assert run(
        "fn self_ref(mut x: int32) -> mut int32 { return x; }\n"
        "fn main() -> int32 {\n"
        "    let n: int32 = 3;\n"
        "    self_ref(n) = 42;\n"
        "    return n;\n"
        "}"
    ) == 42


def test_mut_param_root_through_member_and_index():
    # The accessor shape: a value member hop, then an element through the
    # pointer it holds.
    assert run(
        BUF + "fn main() -> int32 {\n" + SETUP +
        "    buf_at(b, 1) = 'z';\n"
        "    return bytes[1] == 'z' ? 0 : 1;\n"
        "}"
    ) == 0


def test_pointer_param_root_through_deref():
    assert run(
        "fn deref(p: int32*) -> mut int32 { return *p; }\n"
        "fn main() -> int32 {\n"
        "    let n: int32 = 1;\n"
        "    deref(&n) = 7;\n"
        "    return n;\n"
        "}"
    ) == 7


def test_pointer_param_root_through_arrow():
    assert run(
        "struct cell { value: int32; }\n"
        "fn value_of(c: struct cell*) -> mut int32 { return c->value; }\n"
        "fn main() -> int32 {\n"
        "    let c: struct cell;\n"
        "    c.value = 0;\n"
        "    value_of(&c) = 11;\n"
        "    return c.value;\n"
        "}"
    ) == 11


def test_nonnull_assert_in_mut_return_chain():
    # A postfix `!` asserts non-null and passes its operand through unchanged
    # (no IR), so it is transparent to the formation walk: `p![i]` forms the
    # same mut lvalue as `p[i]`. This is what lets an invariant-backed element
    # (e.g. a container's backing buffer) be a mut return while asserting the
    # dereference under -Wunchecked-dereference.
    assert run(
        "fn at(p: int32*, i: uint64) -> mut int32 { return p![i]; }\n"
        "fn main() -> int32 {\n"
        "    let xs: int32[3];\n"
        "    at(xs, 1) = 8;\n"
        "    at(xs, 1) += 4;\n"
        "    return at(xs, 1);\n"
        "}"
    ) == 12


def test_nonnull_assert_in_mut_return_is_ir_neutral():
    # The `!` emits nothing, so a mut return through `p![i]` lowers to exactly
    # the IR of the same return through `p[i]`.
    asserted = compile_ir(
        "fn at(p: int32*, i: uint64) -> mut int32 { return p![i]; }\n"
        "fn main() -> int32 { let xs: int32[2]; at(xs, 0) = 1; return 0; }"
    )
    plain = compile_ir(
        "fn at(p: int32*, i: uint64) -> mut int32 { return p[i]; }\n"
        "fn main() -> int32 { let xs: int32[2]; at(xs, 0) = 1; return 0; }"
    )
    assert asserted == plain


def test_global_root():
    assert run(
        "@static let counter: int32 = 5;\n"
        "fn counter_ref() -> mut int32 { return counter; }\n"
        "fn main() -> int32 {\n"
        "    counter_ref() += 1;\n"
        "    return counter;\n"
        "}"
    ) == 6


def test_chained_mut_return_call():
    # The whole returned expression is itself a mut-returning call: the
    # callee's formation vouches, compositionally.
    assert run(
        BUF +
        "fn first(mut self: struct buf) -> mut char {\n"
        "    return buf_at(self, 0);\n"
        "}\n"
        "fn main() -> int32 {\n" + SETUP +
        "    first(b) = 'q';\n"
        "    return bytes[0] == 'q' ? 0 : 1;\n"
        "}"
    ) == 0


def test_chain_through_mut_return_call():
    # A projection continues past a mut-returning call in chain position.
    assert run(
        "struct pt { x: int32; y: int32; }\n"
        "fn pt_ref(mut p: struct pt) -> mut struct pt { return p; }\n"
        "fn x_of(mut p: struct pt) -> mut int32 { return pt_ref(p).x; }\n"
        "fn main() -> int32 {\n"
        "    let p = struct pt { x = 0, y = 0 };\n"
        "    x_of(p) = 9;\n"
        "    return p.x;\n"
        "}"
    ) == 9


def test_local_root_rejected():
    # Even the provably-safe alias is rejected: inline the chain instead.
    with pytest.raises(LangError, match=f"{FORMATION}.*'d' is a local"):
        compile_ir(
            BUF.replace(
                "    return self.data[i];\n",
                "    let d = self.data;\n    return d[i];\n",
            )
            + "fn main() -> int32 { return 0; }"
        )


def test_pointer_local_root_rejected_with_inlining_hint():
    with pytest.raises(LangError, match="inline its chain"):
        compile_ir(
            "fn f(p: int32*) -> mut int32 {\n"
            "    let q = p;\n"
            "    return *q;\n"
            "}\n"
            "fn main() -> int32 { return 0; }"
        )


def test_by_value_param_root_rejected():
    with pytest.raises(LangError, match="'x' is a by-value parameter"):
        compile_ir(
            "fn f(x: int32) -> mut int32 { return x; }\n"
            "fn main() -> int32 { return 0; }"
        )


def test_pointer_param_returned_directly_rejected():
    # `return p` would reference the parameter's own frame slot; `return *p`
    # (above) reaches the caller's storage.
    with pytest.raises(
        LangError, match="returning the pointer parameter 'p' itself"
    ):
        compile_ir(
            "fn f(p: int32*) -> mut int32* { return p; }\n"
            "fn main() -> int32 { return 0; }"
        )


def test_const_param_root_rejected():
    with pytest.raises(LangError, match="'p' is a const parameter"):
        compile_ir(
            "fn f(const p: int32*) -> mut int32 { return *p; }\n"
            "fn main() -> int32 { return 0; }"
        )


def test_cast_root_rejected():
    with pytest.raises(LangError, match="must be an lvalue chain"):
        compile_ir(
            "fn f(mut x: int32) -> mut int32 { return x as int32; }\n"
            "fn main() -> int32 { return 0; }"
        )


def test_null_root_rejected():
    with pytest.raises(LangError, match="must be an lvalue chain"):
        compile_ir(
            "fn f() -> mut int32 { return null; }\n"
            "fn main() -> int32 { return 0; }"
        )


def test_plain_call_in_chain_rejected():
    # A plain T*-returning call vouches for nothing.
    with pytest.raises(
        LangError, match="a call to 'raw' that does not return a reference"
    ):
        compile_ir(
            "@static let g: int32 = 0;\n"
            "fn raw() -> int32* { return &g; }\n"
            "fn f() -> mut int32 { return raw()[0]; }\n"
            "fn main() -> int32 { return 0; }"
        )


def test_global_array_element_root():
    # Indexing a global fixed-size array stays in the global's own storage.
    assert run(
        "@static let table: int32[4];\n"
        "fn slot(i: uint64) -> mut int32 { return table[i]; }\n"
        "fn main() -> int32 {\n"
        "    slot(2) = 30;\n"
        "    slot(2) += 3;\n"
        "    return table[2];\n"
        "}"
    ) == 33


def test_by_value_struct_member_rejected():
    # A value member hop stays in the by-value parameter's frame copy.
    with pytest.raises(LangError, match="'p' is a by-value parameter"):
        compile_ir(
            "struct pt { x: int32; }\n"
            "fn f(p: struct pt) -> mut int32 { return p.x; }\n"
            "fn main() -> int32 { return 0; }"
        )


def test_chain_through_static_mut_return_call():
    # A file-scoped @static accessor in chain position resolves by symbol.
    assert run(
        "struct pt { x: int32; y: int32; }\n"
        "@static fn pt_ref(mut p: struct pt) -> mut struct pt { return p; }\n"
        "fn x_of(mut p: struct pt) -> mut int32 { return pt_ref(p).x; }\n"
        "fn main() -> int32 {\n"
        "    let p = struct pt { x = 0, y = 0 };\n"
        "    x_of(p) = 8;\n"
        "    return p.x;\n"
        "}"
    ) == 8


def test_chain_through_shadowed_name_rejected():
    # A local function pointer shadows the name: an indirect call, which a
    # plain fn(...) type can never mark as returning mut.
    with pytest.raises(LangError, match="does not return a reference"):
        compile_ir(
            "struct pt { x: int32; }\n"
            "fn make() -> int32 { return 0; }\n"
            "fn f(mut p: struct pt) -> mut int32 {\n"
            "    let pt_ref = make;\n"
            "    return pt_ref().x;\n"
            "}\n"
            "fn main() -> int32 { return 0; }"
        )


def test_shadowing_let_drops_param_root():
    # A shadowing let over a pointer parameter is a local, not a root.
    with pytest.raises(LangError, match="'p' is a local"):
        compile_ir(
            "fn f(p: int32*, q: int32*) -> mut int32 {\n"
            "    let p = q;\n"
            "    return *p;\n"
            "}\n"
            "fn main() -> int32 { return 0; }"
        )


def test_exact_type_return_required():
    # The caller writes through the reference: nothing adapts or widens.
    with pytest.raises(
        LangError, match="reference return: expected a int32 lvalue, got int64"
    ):
        compile_ir(
            "fn f(mut x: int64) -> mut int32 { return x; }\n"
            "fn main() -> int32 { return 0; }"
        )


def test_volatile_storage_rejected_as_mut_return():
    with pytest.raises(
        LangError, match="cannot pass @volatile storage as a reference return"
    ):
        compile_ir(
            "@volatile struct reg { bits: int32; }\n"
            "fn bits_of(r: struct reg*) -> mut int32 { return r->bits; }\n"
            "fn main() -> int32 { return 0; }"
        )


def test_packed_field_rejected_as_mut_return():
    with pytest.raises(
        LangError, match="cannot pass a @packed field as a reference return"
    ):
        compile_ir(
            "@packed struct wire { tag: char; value: int32; }\n"
            "fn value_of(w: struct wire*) -> mut int32 { return w->value; }\n"
            "fn main() -> int32 { return 0; }"
        )


# ------------------------------------------------------------ lvalue surfaces


def test_assignment_through_call():
    assert run(
        BUF + "fn main() -> int32 {\n" + SETUP +
        "    buf_at(b, 0) = 'x';\n"
        "    return bytes[0] == 'x' ? 0 : 1;\n"
        "}"
    ) == 0


def test_compound_assignment_evaluates_target_once():
    # The target address is computed once: one accessor call, one index
    # computation, however complex the lvalue.
    assert run(
        BUF +
        "@static let calls: int32 = 0;\n"
        "fn pick() -> uint64 { calls = calls + 1; return 1; }\n"
        "fn main() -> int32 {\n" + SETUP +
        "    buf_at(b, pick()) += 1;\n"
        "    if (bytes[1] != 'c') { return 1; }\n"
        "    return calls;\n"
        "}"
    ) == 1


def test_field_projection_through_call():
    assert run(
        "struct pt { x: int32; y: int32; }\n"
        "fn pt_ref(mut p: struct pt) -> mut struct pt { return p; }\n"
        "fn main() -> int32 {\n"
        "    let p = struct pt { x = 0, y = 0 };\n"
        "    pt_ref(p).y = 4;\n"
        "    return p.y;\n"
        "}"
    ) == 4


def test_index_projection_through_call():
    # A mut return of pointer type indexes through the loaded pointer.
    assert run(
        BUF +
        "fn data_of(mut self: struct buf) -> mut char* {\n"
        "    return self.data;\n"
        "}\n"
        "fn main() -> int32 {\n" + SETUP +
        "    data_of(b)[2] = 'k';\n"
        "    return bytes[2] == 'k' ? 0 : 1;\n"
        "}"
    ) == 0


def test_relend_direct_path():
    assert run(
        BUF +
        "fn bump(mut c: char) { c += 1; }\n"
        "fn main() -> int32 {\n" + SETUP +
        "    bump(buf_at(b, 0));\n"
        "    return bytes[0] == 'b' ? 0 : 1;\n"
        "}"
    ) == 0


def test_relend_generic_path():
    # An overload set forces the pre-evaluate generic path; the carried
    # lvalue keeps the mut candidate viable and re-lends.
    assert run(
        BUF +
        "fn put<T>(mut a: T, v: T) { a = v; }\n"
        "fn put<T>(p: T*, v: T) { *p = v; }\n"
        "fn main() -> int32 {\n" + SETUP +
        "    put(buf_at(b, 2), 'w');\n"
        "    return bytes[2] == 'w' ? 0 : 1;\n"
        "}"
    ) == 0


def test_relend_generic_path_exact_type_required():
    # Explicit type arguments pin T; the re-lent lvalue must match exactly.
    with pytest.raises(LangError, match="expected a int64 lvalue, got char"):
        compile_ir(
            BUF +
            "fn put<T>(mut a: T) { }\n"
            "fn main() -> int32 {\n" + SETUP +
            "    put<int64>(buf_at(b, 0));\n"
            "    return 0;\n"
            "}"
        )


def test_relend_exact_type_required():
    with pytest.raises(LangError, match="expected a int64 lvalue, got char"):
        compile_ir(
            BUF +
            "fn wide(mut n: int64) { n = 0; }\n"
            "fn main() -> int32 {\n" + SETUP +
            "    wide(buf_at(b, 0));\n"
            "    return 0;\n"
            "}"
        )


def test_value_context_auto_loads():
    assert run(
        BUF + "fn main() -> int32 {\n" + SETUP +
        "    let c = buf_at(b, 0);\n"
        "    return c == 'a' ? 0 : 1;\n"
        "}"
    ) == 0


def test_loaded_value_coerces_in_expressions():
    # The eager load yields an ordinary value: an untyped constant adapts
    # to it in a binary expression like against any other char.
    assert run(
        BUF + "fn main() -> int32 {\n" + SETUP +
        "    let next = buf_at(b, 0) + 1;\n"
        "    return next == 'b' ? 0 : 1;\n"
        "}"
    ) == 0


def test_shared_storage_as_const_hidden_reference():
    # A mut-returning call's storage feeds a `const &` view parameter without
    # a spill: the hidden reference is the returned address itself. (Since
    # Phase B the view is spelled `const &T`; a plain `const T` copies.)
    ir_text = compile_ir(
        "struct pt { x: int32; y: int32; }\n"
        "fn pt_ref(mut p: struct pt) -> mut struct pt { return p; }\n"
        "fn x_of(const p: &struct pt) -> int32 { return p.x; }\n"
        "fn main() -> int32 {\n"
        "    let p: struct pt;\n"
        "    p.x = 3; p.y = 0;\n"
        "    return x_of(pt_ref(p));\n"
        "}"
    )
    assert 'call %"pt"* @"pt_ref"' in ir_text  # the pointer-typed return
    # The pt_ref result is handed to x_of directly: no spill temporary, so
    # the only pt alloca is main's own `p`.
    assert ir_text.count('alloca %"pt"') == 1


# ----------------------------------------------------------------- the & ban


def test_address_of_call_result_rejected():
    with pytest.raises(
        LangError, match="cannot take the address of a call result"
    ):
        compile_ir(
            BUF + "fn main() -> int32 {\n" + SETUP +
            "    let p = &buf_at(b, 0);\n"
            "    return 0;\n"
            "}"
        )


def test_address_of_plain_call_rejected_too():
    with pytest.raises(
        LangError, match="cannot take the address of a call result"
    ):
        compile_ir(
            "fn f() -> int32 { return 1; }\n"
            "fn main() -> int32 { let p = &f(); return 0; }"
        )


# --------------------------------------------------- non-mut calls as targets


def test_plain_call_not_assignable():
    with pytest.raises(
        LangError,
        match="the call to 'f' does not return a reference, so its result is not "
        "assignable",
    ):
        compile_ir(
            "fn f() -> int32 { return 1; }\n"
            "fn main() -> int32 { f() = 2; return 0; }"
        )


def test_plain_call_not_compound_assignable():
    with pytest.raises(LangError, match="does not return a reference"):
        compile_ir(
            "fn f() -> int32 { return 1; }\n"
            "fn main() -> int32 { f() += 2; return 0; }"
        )


def test_plain_call_field_not_assignable():
    with pytest.raises(LangError, match="does not return a reference"):
        compile_ir(
            "struct pt { x: int32; }\n"
            "fn f() -> struct pt { let p: struct pt; p.x = 0; return p; }\n"
            "fn main() -> int32 { f().x = 2; return 0; }"
        )


def test_void_call_not_assignable():
    with pytest.raises(LangError, match="does not return a reference"):
        compile_ir(
            "fn f() { }\n"
            "fn main() -> int32 { f() = 2; return 0; }"
        )


# ------------------------------------------------------------ function values


def test_mut_returning_function_value_infers_the_carrying_type():
    # The last function-value ban is gone: the inferred type spells the mut
    # return, and a call through the value is an lvalue expression (see
    # test_mut_return_fn_types.py for the full behavior).
    assert run(
        "@static let counter: int32 = 0;\n"
        "fn counter_ref() -> mut int32 { return counter; }\n"
        "fn main() -> int32 { let f = counter_ref; f() = 9; return counter; }"
    ) == 9


# ------------------------------------------------------------------- generics


def test_generic_mut_return_instantiates():
    assert run(
        "fn pick<T>(mut a: T, mut b: T, first: bool) -> mut T {\n"
        "    if (first) { return a; }\n"
        "    return b;\n"
        "}\n"
        "fn main() -> int32 {\n"
        "    let x: int32 = 1;\n"
        "    let y: int32 = 2;\n"
        "    pick(x, y, false) = 20;\n"
        "    pick(x, y, true) += 9;\n"
        "    return x + y;\n"  # 10 + 20
        "}"
    ) == 30


def test_generic_mut_return_formation_checked_per_instance():
    with pytest.raises(LangError, match="'x' is a by-value parameter"):
        compile_ir(
            "fn leak<T>(x: T) -> mut T { return x; }\n"
            "fn main() -> int32 {\n"
            "    let n: int32 = 0;\n"
            "    leak(n) = 1;\n"
            "    return 0;\n"
            "}"
        )


# ----------------------------------------------------- write-effect tracking


NONNULL = (
    "struct box { data: int32*; }\n"
    "fn first(@nonnull p: int32*) -> int32 { return *p; }\n"
    "@static let slot: int32 = 0;\n"
    "fn slot_ref() -> mut int32 { return slot; }\n"
)


def test_pure_mut_return_accessor_preserves_projection_fact():
    # Returning a reference is not a write: a pure accessor's call keeps
    # projection facts alive (the value read stores nothing).
    assert run(
        NONNULL +
        "fn main() -> int32 {\n"
        "    let x: int32 = 40;\n"
        "    let bx = struct box { data = &x };\n"
        "    if (bx.data == null) { return 0; }\n"
        "    let n = slot_ref();\n"
        "    return first(bx.data) + n + 2;\n"
        "}"
    ) == 42


def test_store_through_mut_return_kills_projection_fact():
    # `f(...) = v` is a through-memory store: the reference may alias any
    # guarded field, so the fact dies at the assignment.
    with pytest.raises(LangError, match="cannot pass a possibly-null"):
        compile_ir(
            NONNULL +
            "fn main() -> int32 {\n"
            "    let x: int32 = 0;\n"
            "    let bx = struct box { data = &x };\n"
            "    if (bx.data == null) { return 0; }\n"
            "    slot_ref() = 1;\n"
            "    return first(bx.data);\n"
            "}"
        )


def test_function_storing_through_mut_return_is_not_write_free():
    # The soundness-critical scan arm: a callee whose body assigns through a
    # mut-returning call writes caller-visible memory, so calling it must
    # kill projection facts (it must never count as write-free).
    with pytest.raises(LangError, match="cannot pass a possibly-null"):
        compile_ir(
            NONNULL +
            "fn poke() { slot_ref() = 1; }\n"
            "fn main() -> int32 {\n"
            "    let x: int32 = 0;\n"
            "    let bx = struct box { data = &x };\n"
            "    if (bx.data == null) { return 0; }\n"
            "    poke();\n"
            "    return first(bx.data);\n"
            "}"
        )


# ------------------------------------------------------------ interface files


def test_interface_renders_mut_return():
    out = iface(
        "fn at(mut self: char*, i: uint64) -> mut char { return self[i]; }"
    )
    assert "fn at(self: &char*, i: uint64) -> &char;" in out


def test_mut_return_round_trips_through_mci(tmp_path):
    lib = tmp_path / "lib.mc"
    lib.write_text(
        "@static let counter: int32 = 7;\n"
        "fn counter_ref() -> mut int32 { return counter; }\n"
        "fn counter_value() -> int32 { return counter; }\n"
    )
    out = tmp_path / "lib.mci"
    assert emit_interface(lib, (tmp_path,), None, {}, out) == 0
    assert "fn counter_ref() -> &int32;" in out.read_text()
    # The consumer compiles against the stub: the call is an lvalue there
    # too. (JIT-running would need the missing object; compiling is the
    # round-trip under test, via the definition living beside the stub.)
    main = tmp_path / "main.mc"
    main.write_text(
        'import "lib";\n'
        "fn main() -> int32 {\n"
        "    counter_ref() += 3;\n"
        "    return counter_value();\n"
        "}\n"
    )
    assert run_path(main) == 10


def test_prototype_mut_return_mismatch_rejected():
    # A `-> T` definition must not pair with a `-> mut T` stub: the two
    # disagree on the call's lvalue-ness and ABI.
    with pytest.raises(
        LangError, match="definition of 'f' does not match its prototype"
    ):
        compile_ir(
            "@static let g: int32 = 0;\n"
            "fn f() -> mut int32;\n"
            "fn f() -> int32 { return g; }\n"
            "fn main() -> int32 { return 0; }"
        )
