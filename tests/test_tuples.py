"""The builtin tuple<A, B, ...> product type (stages 1-3, any arity).

The core type (an interned struct with positional fields "0", "1", ...), the
paren literal `(a, b)` with struct-literal-style context coercion, constant
indexing `t[n]` with compile-time bounds checks, constant slicing `t[n:m]`
narrowing to the smaller tuple by value, and destructuring `let a, b = t;`
with the trailing-`...` rest binder -- pure sugar over the indexing and
slicing. Arity is fully open: `(x,)` and `tuple<T>` spell the 1-tuple, `()`
and `tuple<>` the zero-sized empty tuple (the empty struct's twin). The `as`
struct cast lands in stage 4.
"""

import pytest

from mcc.driver import emit_interface
from mcc.errors import LangError
from mcc.nodes import TupleLit, TypeRef
from helpers import compile_ir, parse, run, run_path


# ------------------------------------------------------------------ parsing

def test_tuple_type_parses():
    (func,) = parse("fn f(t: tuple<int32, char>) {}").functions
    t = func.params[0][1]
    assert isinstance(t, TypeRef) and t.name == "tuple"
    assert t.args == [TypeRef("int32"), TypeRef("char")]
    assert str(t) == "tuple<int32, char>"  # the canonical interning spelling


def test_paren_comma_parses_into_a_tuple_literal():
    (func,) = parse("fn f() { let t = (1, 2, 3); }").functions
    (stmt,) = func.body
    assert isinstance(stmt.value, TupleLit)
    assert len(stmt.value.elements) == 3


def test_parenthesized_expression_stays_grouping():
    (func,) = parse("fn f() -> int32 { return (1); }").functions
    (stmt,) = func.body
    assert not isinstance(stmt.value, TupleLit)


def test_trailing_comma_is_allowed():
    source = "fn main() -> int32 { let t = (40, 2,); return t[0] + t[1]; }"
    assert run(source) == 42


# ------------------------------------------------------------- construction

def test_typed_let_coerces_elements():
    # Untyped constants adapt to the declared positions, like struct fields.
    source = """
    fn main() -> int32 {
        let t: tuple<int64, int64> = (40, 2);
        return (t[0] + t[1]) as int32;
    }
    """
    assert run(source) == 42


def test_inferred_let_anchors_defaults():
    # With no context an untyped integer element anchors to int32, so the
    # inferred tuple adds against an int32 with no cast.
    source = """
    fn main() -> int32 {
        let t = (40, 'x');
        return t[0] + 2;
    }
    """
    assert run(source) == 42


def test_uninitialized_let_declares_like_a_struct():
    source = """
    fn main() -> int32 {
        let t: tuple<int32, int32>;
        t[0] = 40; t[1] = 2;
        return t[0] + t[1];
    }
    """
    assert run(source) == 42


def test_nested_tuple_literal_builds_the_nested_tuple():
    source = """
    fn main() -> int32 {
        let t = (1, (2, 3));
        return t[0] * 100 + t[1][0] * 10 + t[1][1];
    }
    """
    assert run(source) == 123


def test_string_literal_element_borrows_into_a_slice_position():
    # A string literal in a slice-typed position adapts inside the tuple
    # literal, exactly as it does in a struct-literal field.
    source = """
    fn main() -> int32 {
        let t: tuple<slice<const char>, int32> = ("hello", 37);
        return (t[0].length as int32) + t[1];
    }
    """
    assert run(source) == 42


def test_bare_struct_literal_element_builds_the_field_struct():
    source = """
    struct point { x: int32; y: int32; }
    fn main() -> int32 {
        let t: tuple<point, int32> = ({ x = 40, y = 2 }, 0);
        return t[0].x + t[0].y + t[1];
    }
    """
    assert run(source) == 42


def test_literal_arity_must_match_the_receiving_type():
    with pytest.raises(
        LangError,
        match=r"tuple literal has 3 elements, but tuple<int32, int32> has 2",
    ):
        compile_ir("fn main() -> int32 { let t: tuple<int32, int32> = (1, 2, 3); return 0; }")


def test_null_element_needs_a_pointer_type():
    with pytest.raises(LangError, match="a null tuple element needs a pointer type"):
        compile_ir("fn main() -> int32 { let t = (null, 1); return 0; }")


def test_null_element_adapts_under_a_declared_type():
    source = """
    fn main() -> int32 {
        let t: tuple<uint8*, int32> = (null, 42);
        return t[0] == null ? t[1] : 0;
    }
    """
    assert run(source) == 42


def test_literal_adapts_at_every_assignment_sink():
    # Assignment, store-through-pointer, element, and field sinks all route
    # the literal through the same adaptation as a typed let.
    source = """
    struct s { p: tuple<int32, int32>; }
    fn main() -> int32 {
        let t: tuple<int64, int64> = (0, 0);
        t = (1, 2);                              // plain assignment
        let u = (0, 0);
        let ptr = &u;
        *ptr = (3, 4);                           // store through a pointer
        let a: tuple<int32, int32>[1] = [(0, 0)];
        a[0] = (5, 6);                           // element assignment
        let x = s { p = (0, 0) };
        x.p = (7, 8);                            // field assignment
        return (t[0] + t[1]) as int32 + u[0] + u[1]
            + a[0][0] + a[0][1] + x.p[0] + x.p[1];
    }
    """
    assert run(source) == 36


def test_return_position_coerces_elements():
    source = """
    fn f() -> tuple<int64, int64> { return (40, 2); }
    fn main() -> int32 { let t = f(); return (t[0] + t[1]) as int32; }
    """
    assert run(source) == 42


# ------------------------------------------------ small arities: 1-tuples

def test_one_tuple_spells_with_a_trailing_comma():
    # `(x)` stays plain grouping; the trailing comma makes the 1-tuple.
    (func,) = parse("fn f() { let t = (1,); }").functions
    (stmt,) = func.body
    assert isinstance(stmt.value, TupleLit)
    assert len(stmt.value.elements) == 1


def test_one_tuple_type_parses():
    (func,) = parse("fn f(t: tuple<int32>) {}").functions
    t = func.params[0][1]
    assert isinstance(t, TypeRef) and t.args == [TypeRef("int32")]
    assert str(t) == "tuple<int32>"


def test_one_tuple_constructs_and_indexes():
    source = """
    fn main() -> int32 {
        let t: tuple<int64> = (40,);
        let u = (2,);
        return (t[0] + u[0]) as int32;
    }
    """
    assert run(source) == 42


def test_one_tuple_literal_indexes_directly():
    assert run("fn main() -> int32 { return (42,)[0]; }") == 42


def test_one_tuple_index_stays_bounds_checked():
    with pytest.raises(
        LangError,
        match=r"tuple index 1 is out of bounds for tuple<int32> "
        r"\(positions 0 to 0\)",
    ):
        compile_ir("fn main() -> int32 { let t = (1,); return t[1]; }")


def test_one_tuple_binds_a_generic_element():
    source = """
    fn first<T>(t: tuple<T>) -> T { return t[0]; }
    fn main() -> int32 { return first((42,)); }
    """
    assert run(source) == 42


# --------------------------------- small arities: the empty tuple (the unit)

def test_empty_parens_spell_the_empty_tuple_literal():
    (func,) = parse("fn f() { let u = (); }").functions
    (stmt,) = func.body
    assert isinstance(stmt.value, TupleLit)
    assert stmt.value.elements == []


def test_empty_tuple_type_parses():
    (func,) = parse("fn f(u: tuple<>) {}").functions
    t = func.params[0][1]
    assert isinstance(t, TypeRef) and t.name == "tuple" and t.args == []
    assert str(t) == "tuple<>"  # the canonical interning spelling


def test_empty_tuple_type_splits_a_nested_double_close():
    # The empty argument list sees through a `>>` nested close.
    (func,) = parse("fn f(t: tuple<int32, tuple<>>) {}").functions
    assert str(func.params[0][1]) == "tuple<int32, tuple<>>"


def test_empty_type_args_stay_rejected_elsewhere():
    # The empty argument list is tuple's alone: every other generic still
    # requires at least one type between the angles.
    with pytest.raises(LangError, match="expected 'IDENT'"):
        compile_ir("fn main() -> int32 { let s: slice<>; return 0; }")


def test_unit_declares_assigns_passes_and_returns():
    # The empty tuple mirrors the empty struct: an ordinary zero-sized value.
    source = """
    fn bounce(u: tuple<>) -> tuple<> { return u; }
    fn main() -> int32 {
        let u: tuple<>;
        u = ();
        let v = bounce(u);
        v = ();
        return sizeof(tuple<>) as int32;
    }
    """
    assert run(source) == 0


def test_unit_arrays_and_fields_follow_the_empty_struct():
    # `struct e {}` allows arrays and zero-sized fields; the unit matches.
    source = """
    struct holder { u: tuple<>; n: int32; }
    fn main() -> int32 {
        let a: tuple<>[4];
        a[0] = ();
        let h = holder{ u = (), n = 38 };
        return h.n + (sizeof(a) + sizeof(holder)) as int32;
    }
    """
    assert run(source) == 42


def test_unit_as_a_generic_argument():
    source = """
    struct box<T> { v: T; }
    fn main() -> int32 {
        let b = box<tuple<>>{ v = () };
        b.v = ();
        return sizeof(b) as int32;
    }
    """
    assert run(source) == 0


def test_unit_binds_a_generic_type_parameter():
    source = """
    fn bounce<T>(v: T) -> T { return v; }
    fn main() -> int32 { let u = bounce(()); u = (); return 0; }
    """
    assert run(source) == 0


def test_nested_unit_makes_a_one_tuple_of_unit():
    source = """
    fn main() -> int32 {
        let n = ((),);
        let inner: tuple<> = n[0];
        return (sizeof(n) + sizeof(inner)) as int32;
    }
    """
    assert run(source) == 0


def test_unit_boxes_and_case_type_recovers(capfd):
    # The struct rule: by hidden reference into a const any; the println
    # fallback renders the canonical spelling.
    source = """
    import "std/io";
    fn probe(v: const any) -> int32 {
        case type (v) {
            when tuple<> u: { return 42; }
            else: { return -1; }
        }
        return -2;
    }
    fn main() -> int32 {
        let u = ();
        println("{}", u);
        return probe(u) - 42;
    }
    """
    assert run(source) == 0
    assert capfd.readouterr().out == "<tuple<>>\n"


def test_unit_index_is_out_of_bounds():
    match = (
        r"tuple index 0 is out of bounds for tuple<> \(it has no positions\)"
    )
    with pytest.raises(LangError, match=match):
        compile_ir("fn main() -> int32 { let u = (); let x = u[0]; return 0; }")
    with pytest.raises(LangError, match=match):
        compile_ir("fn main() -> int32 { let u = (); u[0] = 1; return 0; }")


def test_literal_arity_must_match_an_empty_receiver():
    with pytest.raises(
        LangError, match="tuple literal has 1 elements, but tuple<> has 0"
    ):
        compile_ir("fn main() -> int32 { let u: tuple<> = (1,); return 0; }")


def test_small_arity_mci_round_trip(tmp_path):
    lib = tmp_path / "unit.mc"
    lib.write_text(
        "@inline fn unit() -> tuple<> { return (); }\n"
        "@inline fn one() -> tuple<int32> { return (42,); }\n"
    )
    out = tmp_path / "unit.mci"
    assert emit_interface(lib, (tmp_path,), None, {}, out) == 0
    text = out.read_text()
    assert "tuple<>" in text and "tuple<int32>" in text
    lib.unlink()  # force the import to resolve through the stub
    main = tmp_path / "main.mc"
    main.write_text(
        'import "unit";\n'
        "fn main() -> int32 { let u = unit(); u = (); return one()[0]; }\n"
    )
    assert run_path(main) == 42


def test_void_element_is_rejected():
    with pytest.raises(LangError, match="cannot make a tuple of void"):
        compile_ir("fn main() -> int32 { let t: tuple<void, int32>; return 0; }")


# ----------------------------------------------------------------- indexing

def test_index_reads_writes_and_compound_assigns():
    source = """
    fn main() -> int32 {
        let t = (1, 2);
        t[0] = 30;
        t[1] += 10;
        return t[0] + t[1];
    }
    """
    assert run(source) == 42


def test_nested_index_composes():
    source = """
    fn main() -> int32 {
        let t = (1, (2, 3));
        t[1][0] = 39;
        return t[1][0] + t[1][1];
    }
    """
    assert run(source) == 42


def test_index_folds_constant_expressions():
    source = """
    const LAST = 1;
    fn main() -> int32 {
        let t = (40, 2);
        return t[LAST - 1] + t[0 + 1];
    }
    """
    assert run(source) == 42


def test_runtime_index_is_rejected():
    with pytest.raises(
        LangError,
        match="a tuple index must be a compile-time constant: each position "
        "of a tuple<int32, int32> has its own type",
    ):
        compile_ir(
            "fn main() -> int32 { let t = (1, 2); let i: int32 = 0; return t[i]; }"
        )


def test_out_of_bounds_index_is_rejected():
    with pytest.raises(
        LangError,
        match=r"tuple index 2 is out of bounds for tuple<int32, int32> "
        r"\(positions 0 to 1\)",
    ):
        compile_ir("fn main() -> int32 { let t = (1, 2); return t[2]; }")


def test_negative_index_is_rejected():
    with pytest.raises(
        LangError, match=r"tuple index -1 is out of bounds for tuple<int32, int32>"
    ):
        compile_ir("fn main() -> int32 { let t = (1, 2); return t[-1]; }")


def test_indexing_a_call_result_reads_by_position():
    source = """
    fn divmod(a: int32, b: int32) -> tuple<int32, int32> {
        return (a / b, a % b);
    }
    fn main() -> int32 { return divmod(45, 2)[0] * 2 + divmod(45, 2)[1]; }
    """
    assert run(source) == 45


def test_assigning_into_a_call_result_is_rejected():
    # Parity with `f().field = v` on structs: the rvalue has no
    # caller-visible storage for the store to land in.
    with pytest.raises(
        LangError,
        match="cannot assign into a tuple<int32, int32> value; bind it to a "
        "variable first",
    ):
        compile_ir(
            "fn make() -> tuple<int32, int32> { return (1, 2); }\n"
            "fn main() -> int32 { make()[0] = 5; return 0; }"
        )


def test_mut_return_projects_a_tuple_element():
    # A mut-returning call is an lvalue: indexing projects through the
    # returned storage address, writes included.
    source = """
    import "std/list";
    fn main() -> int32 {
        let l: list<tuple<int32, int32>>;
        list_init(l, 2);
        list_push(l, (1, 2));
        list_at(l, 0)[0] = 40;
        let r = list_at(l, 0)[0] + list_at(l, 0)[1];
        list_destroy(l);
        return r;
    }
    """
    assert run(source) == 42


# ------------------------------------------------- constant slicing (stage 2)

def test_slice_narrows_to_the_smaller_tuple():
    # `t[n:m]` keeps positions n to m-1 (half-open, like a sub-slice) as a
    # new value of the interned tuple of those positions.
    out = compile_ir(
        "fn main() -> int32 { let t = (1, 'x', 2.5, 4); let u = t[1:3]; "
        "return 0; }"
    )
    assert '%"tuple<char, float64>"' in out


def test_slice_values_are_copied():
    # A tuple is a value type and the narrowed layout could not alias the
    # source anyway: the slice is a copy, not a view.
    source = """
    fn main() -> int32 {
        let t = (10, 20, 30);
        let u = t[0:2];
        u[0] = 999;                       // a copy: t is untouched
        return t[0] + u[1];
    }
    """
    assert run(source) == 30


def test_open_ended_bounds_fold_against_the_arity():
    # The grammar's open ends work on tuples too: an omitted start is 0, an
    # omitted end the arity, and `t[:]` a plain whole-value copy.
    source = """
    fn main() -> int32 {
        let t = (1, 2, 3);
        let head = t[:2];
        let tail = t[1:];
        let all = t[:];
        return head[0] * 100 + tail[1] * 10 + all[2];
    }
    """
    assert run(source) == 133


def test_slice_result_feeds_typed_sinks_and_aliases():
    # The result is an ordinary interned tuple: a typed let and a
    # tuple-naming alias both accept it.
    source = """
    type duo = tuple<int32, int32>;
    fn main() -> int32 {
        let t = (1, 2, 3);
        let u: tuple<int32, int32> = t[1:3];
        let p: duo = t[0:2];
        return u[0] + u[1] + p[0] + p[1];
    }
    """
    assert run(source) == 8


def test_slice_then_index_composes():
    assert run(
        "fn main() -> int32 { let t = (1, 20, 3); return t[0:2][1]; }"
    ) == 20


def test_slice_of_a_slice_chains():
    source = """
    fn main() -> int32 {
        let t = (1, 2, 3, 4);
        let u = t[0:3][1:3];              // positions 1 and 2
        return u[0] * 10 + u[1];
    }
    """
    assert run(source) == 23


def test_rvalue_base_slices():
    # A call result is a plain rvalue: the slice copies out of the returned
    # value, no binding needed.
    source = """
    fn three() -> tuple<int32, int32, int32> { return (10, 20, 30); }
    fn main() -> int32 { return three()[1:][0] + three()[0:2][1]; }
    """
    assert run(source) == 40


def test_const_parameter_base_slices():
    # A hidden-reference const tuple slices like any value; the copy sheds
    # the const, so the result's elements are writable.
    source = """
    fn f(const t: tuple<int32, int32, int32>) -> int32 {
        let u = t[1:];
        u[0] += 1;
        return u[0] + u[1];
    }
    fn main() -> int32 { return f((1, 2, 3)); }
    """
    assert run(source) == 6


def test_literal_base_slices():
    assert run(
        "fn main() -> int32 { let u = (1, 2, 3)[1:3]; return u[0] + u[1]; }"
    ) == 5


def test_generic_tuple_slices():
    # Inside a generic the bounds still fold; the narrowed tuple is built
    # from the instantiated element types and matches the declared return.
    source = """
    fn tail<A, B, C>(t: tuple<A, B, C>) -> tuple<B, C> { return t[1:3]; }
    fn main() -> int32 {
        let u = tail((1, 'x', 3));
        if (u[0] != 'x') { return 1; }
        return u[1] == 3 ? 42 : 0;
    }
    """
    assert run(source) == 42


def test_named_constant_bound_folds():
    source = """
    const K = 1;
    fn main() -> int32 {
        let t = (1, 2, 3);
        let u = t[K:3];
        return u[0] + u[1];
    }
    """
    assert run(source) == 5


def test_slice_relayouts_the_kept_positions():
    # The source's interior padding differs from the narrowed type's; the
    # copy re-lays the kept positions: tuple<int32, int64, int32> is 24
    # bytes, the sliced tuple<int32, int64> is 16.
    source = """
    fn main() -> int32 {
        let t = (1, 2 as int64, 3);
        let u = t[0:2];
        return sizeof(u) as int32 + u[1] as int32;
    }
    """
    assert run(source) == 18


def test_runtime_slice_bound_is_rejected():
    with pytest.raises(
        LangError,
        match="a tuple slice bound must be a compile-time constant: the "
        "bounds pick which positions of a tuple<int32, int32, int32> the "
        "result keeps, so a runtime bound has no single result type",
    ):
        compile_ir(
            "fn main() -> int32 { let t = (1, 2, 3); let n: int32 = 1; "
            "let u = t[n:3]; return 0; }"
        )


def test_out_of_bounds_slice_bound_is_rejected():
    # Each bound folds against the arity; the end bound is exclusive, so it
    # may equal the arity but not exceed it.
    with pytest.raises(
        LangError,
        match=r"tuple slice bound 4 is out of bounds for "
        r"tuple<int32, int32, int32> \(bounds run 0 to 3\)",
    ):
        compile_ir(
            "fn main() -> int32 { let t = (1, 2, 3); let u = t[0:4]; return 0; }"
        )
    with pytest.raises(
        LangError, match="tuple slice bound -1 is out of bounds"
    ):
        compile_ir(
            "fn main() -> int32 { let t = (1, 2, 3); let u = t[-1:2]; return 0; }"
        )


def test_inverted_slice_bounds_are_rejected():
    with pytest.raises(
        LangError, match="tuple slice bounds are inverted: 2 > 1"
    ):
        compile_ir(
            "fn main() -> int32 { let t = (1, 2, 3); let u = t[2:1]; return 0; }"
        )


def test_slice_narrows_to_any_arity():
    # A slice may keep 1 or 0 positions: `t[1:]` on a pair is the 1-tuple
    # tail (the rest binder's desugar) and `t[n:n]` the empty tuple.
    source = """
    fn main() -> int32 {
        let t = (40, 2);
        let tail: tuple<int32> = t[1:];
        let head = t[:1];
        let none: tuple<> = t[1:1];
        return head[0] + tail[0] + sizeof(none) as int32;
    }
    """
    assert run(source) == 42


def test_one_tuple_base_slices_to_both_arities():
    source = """
    fn main() -> int32 {
        let t = (42,);
        let whole = t[:];
        let same = t[0:1];
        let empty: tuple<> = t[1:];
        return whole[0] + same[0] - t[0] + sizeof(empty) as int32;
    }
    """
    assert run(source) == 42


def test_non_integer_slice_bound_is_rejected():
    with pytest.raises(
        LangError, match="slice bound must be an integer, not float64"
    ):
        compile_ir(
            "fn main() -> int32 { let t = (1, 2, 3); let u = t[0:2.5]; return 0; }"
        )


def test_tuple_slice_is_not_an_lvalue():
    # A slice is a value copy, not a place: not an assignment target, not
    # compound-assignable, and not addressable -- like a sub-slice view.
    with pytest.raises(LangError, match="invalid assignment target"):
        compile_ir(
            "fn main() -> int32 { let t = (1, 2, 3); t[0:2] = (9, 9); return 0; }"
        )
    with pytest.raises(LangError, match="invalid assignment target"):
        compile_ir(
            "fn main() -> int32 { let t = (1, 2, 3); t[0:2] += (9, 9); return 0; }"
        )
    with pytest.raises(LangError, match="expression is not addressable"):
        compile_ir(
            "fn main() -> int32 { let t = (1, 2, 3); let p = &t[0:2]; return 0; }"
        )


# ------------------------------------------------------------------- len()

def test_len_reports_the_arity():
    source = """
    fn main() -> int32 {
        let e: tuple<>;
        let s = (7,);
        let t = (1, 'x', 2.5);
        return (len(e) * 100 + len(s) * 10 + len(t)) as int32;   // 13
    }
    """
    assert run(source) == 13


def test_len_of_a_tuple_literal():
    source = """
    fn main() -> int32 {
        return (len(()) * 10 + len((1, 2))) as int32;   // 2
    }
    """
    assert run(source) == 2


def test_len_is_a_compile_time_constant():
    ir_text = compile_ir(
        "fn main() -> int32 { let t = (1, 2, 3); return len(t) as int32; }"
    )
    assert "i64 3" in ir_text  # the arity is a constant, not a runtime computation


def test_len_adapts_to_an_int32_counter():
    # len is an adaptable constant, so it compares against int32 without a cast.
    source = """
    fn main() -> int32 {
        let t = (4, 8, 15, 16);
        let i: int32 = 0;
        while (i < len(t)) { i = i + 1; }
        return i;
    }
    """
    assert run(source) == 4


def test_len_folds_as_an_index_bound():
    # len() folds through eval_const, so it composes with the constant-index
    # requirement of tuple positions.
    source = """
    fn main() -> int32 {
        let t = (10, 20, 30);
        return t[len(t) - 1];
    }
    """
    assert run(source) == 30


def test_len_folds_as_a_slice_bound():
    source = """
    fn main() -> int32 {
        let t = (1, 20, 22);
        let rest = t[1:len(t)];
        return rest[0] + rest[1];   // 42
    }
    """
    assert run(source) == 42


def test_len_as_an_index_is_bounds_checked():
    with pytest.raises(
        LangError,
        match=r"tuple index 2 is out of bounds for tuple<int32, int32> "
        r"\(positions 0 to 1\)",
    ):
        compile_ir("fn main() -> int32 { let t = (1, 2); return t[len(t)]; }")


def test_len_of_a_call_result():
    # Arity is purely a type property, so an rvalue operand needs no address.
    source = """
    fn divmod(a: int32, b: int32) -> tuple<int32, int32> {
        return (a / b, a % b);
    }
    fn main() -> int32 { return len(divmod(7, 2)) as int32; }
    """
    assert run(source) == 2


def test_len_evaluates_a_call_operand_once():
    source = """
    @static let calls: int32 = 0;
    fn make() -> tuple<int32, int32> { calls = calls + 1; return (1, 2); }
    fn main() -> int32 {
        let n: int32 = len(make());
        return n * 10 + calls;   // 21
    }
    """
    assert run(source) == 21


def test_len_composes_with_slicing():
    source = """
    fn main() -> int32 {
        let t = (1, 2, 3, 4);
        return len(t[1:3]) as int32;
    }
    """
    assert run(source) == 2


def test_len_of_a_const_tuple_parameter():
    source = """
    fn arity(const t: tuple<int32, char>) -> int32 { return len(t) as int32; }
    fn main() -> int32 { let t = (1, 'x'); return arity(t); }
    """
    assert run(source) == 2


def test_len_requires_an_array_or_tuple():
    with pytest.raises(
        LangError, match=r"len\(\) requires an array or tuple, got slice<int32>"
    ):
        compile_ir(
            "fn f(s: slice<int32>) -> int32 { return len(s) as int32; }\n"
            "fn main() -> int32 { return 0; }"
        )


# ------------------------------------------------------ const/mut discipline

def test_const_parameter_element_write_is_rejected():
    with pytest.raises(
        LangError, match="cannot assign to an element of a const parameter"
    ):
        compile_ir(
            "fn f(const t: tuple<int32, int32>) { t[0] = 1; }\n"
            "fn main() -> int32 { return 0; }"
        )


def test_const_parameter_nested_element_write_is_rejected():
    # The in-storage traversal follows nested tuple positions, like nested
    # struct fields.
    with pytest.raises(
        LangError, match="cannot assign to an element of a const parameter"
    ):
        compile_ir(
            "fn f(const t: tuple<int32, tuple<int32, int32>>) { t[1][0] = 1; }\n"
            "fn main() -> int32 { return 0; }"
        )


def test_const_parameter_elements_read_by_hidden_reference():
    # A const tuple parameter travels by hidden reference like any aggregate.
    source = """
    fn sum(const t: tuple<int32, int32>) -> int32 { return t[0] + t[1]; }
    fn main() -> int32 { let t = (40, 2); return sum(t); }
    """
    assert run(source) == 42


def test_mut_parameter_element_writes_reach_the_caller():
    source = """
    fn fill(mut t: tuple<int32, int32>) { t[0] = 40; t[1] = 2; }
    fn main() -> int32 { let t = (0, 0); fill(t); return t[0] + t[1]; }
    """
    assert run(source) == 42


def test_address_of_a_mut_parameter_element_is_rejected():
    # An element lives in the parameter's own storage, so & would leak the
    # non-escaping mut reference -- exactly as for an array element.
    with pytest.raises(
        LangError, match="cannot take the address of a mut parameter"
    ):
        compile_ir(
            "fn f(mut t: tuple<int32, int32>) { let p = &t[0]; }\n"
            "fn main() -> int32 { return 0; }"
        )


# ---------------------------------------------------------------- free riders

def test_multiple_return_values():
    # THE motivating example: divmod with no one-off struct.
    source = """
    fn divmod(a: int32, b: int32) -> tuple<int32, int32> {
        return (a / b, a % b);
    }
    fn main() -> int32 {
        let t = divmod(7, 2);
        return t[0] * 10 + t[1];
    }
    """
    assert run(source) == 31


def test_whole_value_assignment():
    source = """
    fn main() -> int32 {
        let t = (40, 2);
        let u = (0, 0);
        u = t;
        return u[0] + u[1];
    }
    """
    assert run(source) == 42


def test_generic_inference_binds_through_a_tuple():
    # unify recurses template and arguments, variable arity included.
    source = """
    fn fst<A, B>(t: tuple<A, B>) -> A { return t[0]; }
    fn main() -> int32 {
        let t = (42, 'x');
        return fst(t);
    }
    """
    assert run(source) == 42


def test_literal_argument_anchors_before_unify_binds():
    # The documented adaptable-literal limitation: (40, 2) lowers eagerly as
    # tuple<int32, int32>, then unify binds T = int32 from it.
    source = """
    fn fst<T>(t: tuple<T, T>) -> T { return t[0]; }
    fn main() -> int32 { return fst((40, 2)) + 2; }
    """
    assert run(source) == 42


def test_literal_argument_against_a_concrete_param_in_a_generic_is_pinned():
    # The flip side of eager anchoring: inside a generic call the literal is
    # not re-adapted against a concrete tuple parameter (re-building it would
    # run element side effects twice), so the anchored int32 shape must match.
    with pytest.raises(
        LangError,
        match=r"expected tuple<int64, int64>, got tuple<int32, int32>",
    ):
        compile_ir(
            "fn g<T>(x: T, t: tuple<int64, int64>) -> int64 "
            "{ return t[0] + (x as int64); }\n"
            "fn main() -> int32 { return g(1, (2, 3)) as int32; }"
        )


def test_literal_argument_adapts_on_the_direct_call_path():
    # A concrete (non-generic) callee coerces the literal's elements against
    # the declared positions, like a typed let.
    source = """
    fn sum(t: tuple<int64, int64>) -> int64 { return t[0] + t[1]; }
    fn main() -> int32 { return sum((40, 2)) as int32; }
    """
    assert run(source) == 42


def test_arrays_of_tuples_with_literal_elements():
    source = """
    fn main() -> int32 {
        let a: tuple<int32, int32>[2] = [(1, 2), (3, 4)];
        a[1][0] = 35;
        return a[0][0] + a[0][1] + a[1][0] + a[1][1];
    }
    """
    assert run(source) == 42


def test_tuple_as_a_struct_field():
    source = """
    struct span { bounds: tuple<int32, int32>; tag: char; }
    fn main() -> int32 {
        let s = span { bounds = (1, 2), tag = 'x' };
        s.bounds[0] = 40;
        return s.bounds[0] + s.bounds[1];
    }
    """
    assert run(source) == 42


def test_tuple_behind_a_pointer():
    source = """
    fn bump(p: tuple<int32, int32>*) { p[0][0] += 1; }
    fn main() -> int32 { let t = (41, 0); bump(&t); return t[0] + t[1]; }
    """
    assert run(source) == 42


def test_arrow_reaches_a_tuple_field_through_a_pointer():
    source = """
    struct holder { pair: tuple<int32, int32>; }
    fn get(h: holder*) -> int32 { h->pair[0] = 40; return h->pair[0] + h->pair[1]; }
    fn main() -> int32 { let h = holder { pair = (0, 2) }; return get(&h); }
    """
    assert run(source) == 42


def test_sizeof_includes_padding():
    # tuple<char, int64>: 1 byte, 7 bytes of padding, 8 bytes -- the same
    # layout the struct with those fields would have.
    source = "fn main() -> int32 { return sizeof(tuple<char, int64>) as int32; }"
    assert run(source) == 16


def test_over_aligned_element_layout_agrees():
    # The dual-site layout invariant: an @align(16) member forces the
    # spelled-out IR body, which must agree with types.py's sizeof.
    source = """
    @align(16) struct big { v: int32; }
    fn main() -> int32 {
        let t: tuple<char, big> = ('x', big { v = 10 });
        return sizeof(tuple<char, big>) as int32 + t[1].v;   // 32 + 10
    }
    """
    assert run(source) == 42


def test_tuple_field_in_a_packed_struct():
    # The element access inherits the packed owner's alignment-1 loads and
    # stores through the member machinery.
    source = """
    @packed struct rec { tag: char; pair: tuple<char, int32>; }
    fn main() -> int32 {
        let r = rec { tag = 't', pair = ('y', 29) };
        r.pair[1] += 4;
        return sizeof(rec) as int32 + r.pair[1];   // 9 + 33
    }
    """
    assert run(source) == 42


def test_same_shape_tuples_are_one_type_across_functions():
    # Interned by shape: a tuple built in one function assigns and passes
    # into another with no conversion.
    source = """
    fn make() -> tuple<int32, int32> { return (40, 2); }
    fn total(t: tuple<int32, int32>) -> int32 { return t[0] + t[1]; }
    fn main() -> int32 { return total(make()); }
    """
    assert run(source) == 42


def test_same_shape_tuples_are_one_type_across_modules(tmp_path):
    lib = tmp_path / "geo.mc"
    lib.write_text(
        "fn divmod(a: int32, b: int32) -> tuple<int32, int32> "
        "{ return (a / b, a % b); }\n"
    )
    main = tmp_path / "main.mc"
    main.write_text(
        'import "geo";\n'
        "fn main() -> int32 {\n"
        "    let t: tuple<int32, int32> = divmod(7, 2);\n"
        "    return t[0] * 10 + t[1];\n"
        "}\n"
    )
    assert run_path(main) == 31


def test_type_alias_names_a_tuple():
    # Naming a tuple is the type alias's job (`extends` stays rejected).
    source = """
    type polar = tuple<int64, float64>;
    fn main() -> int32 {
        let p: polar = (40, 1.5);
        return (p[0] + (p[1] * 2.0) as int64) as int32 - 1;
    }
    """
    assert run(source) == 42


def test_typename_reports_the_canonical_spelling(capfd):
    source = """
    import "std/io";
    fn main() -> int32 { println("{}", typename(tuple<int32, char>)); return 0; }
    """
    assert run(source) == 0
    assert capfd.readouterr().out == "tuple<int32, char>\n"


def test_mci_round_trip(tmp_path):
    # An @inline function ships in full through the stub, so the consumer
    # re-parses `tuple<...>` in a signature and a body from the .mci alone.
    lib = tmp_path / "geo.mc"
    lib.write_text(
        "@inline fn divmod(a: int32, b: int32) -> tuple<int32, int32> "
        "{ return (a / b, a % b); }\n"
    )
    out = tmp_path / "geo.mci"
    assert emit_interface(lib, (tmp_path,), None, {}, out) == 0
    assert "tuple<int32, int32>" in out.read_text()
    lib.unlink()  # force the import to resolve through the stub
    main = tmp_path / "main.mc"
    main.write_text(
        'import "geo";\n'
        "fn main() -> int32 {\n"
        "    let t = divmod(7, 2);\n"
        "    return t[0] * 10 + t[1];\n"
        "}\n"
    )
    assert run_path(main) == 31


# ----------------------------------------------------------- any and case type

def test_println_formats_a_tuple_through_the_fallback(capfd):
    # The struct rule: a tuple borrows into the variadic's const any and hits
    # the runtime formatter's unknown-aggregate fallback.
    source = """
    import "std/io";
    fn main() -> int32 { let t = (1, 2); println("{}", t); return 0; }
    """
    assert run(source) == 0
    assert capfd.readouterr().out == "<tuple<int32, int32>>\n"


def test_owning_any_box_is_rejected():
    with pytest.raises(
        LangError,
        match="cannot box a tuple<int32, int32> into an owning any; a struct "
        "only boxes by reference into a const any",
    ):
        compile_ir(
            "fn main() -> int32 { let t = (1, 2); let a: any = t; return 0; }"
        )


def test_case_type_matches_a_tuple_arm():
    source = """
    fn probe(v: const any) -> int32 {
        case type (v) {
            when tuple<int32, int32> t: { return t[0] + t[1]; }
            else: { return -1; }
        }
        return -2;
    }
    fn main() -> int32 { let t = (40, 2); return probe(t); }
    """
    assert run(source) == 42


# ------------------------------------------------- destructuring (stage 3)

def test_destructure_binds_each_position():
    source = """
    fn main() -> int32 {
        let t = (40, 'x', 2);
        let a, c, b = t;
        return c == 'x' ? a + b : -1;   // each binder takes its position's type
    }
    """
    assert run(source) == 42


def test_destructure_a_call_result():
    # The headline: multiple return values bind by name at the call site.
    source = """
    fn divmod(a: int32, b: int32) -> tuple<int32, int32> {
        return (a / b, a % b);
    }
    fn main() -> int32 { let q, r = divmod(9, 4); return q * 10 + r; }
    """
    assert run(source) == 21


def test_destructure_call_source_evaluates_once():
    source = """
    @static let calls: int32 = 0;
    fn make() -> tuple<int32, int32> { calls = calls + 1; return (1, 2); }
    fn main() -> int32 { let a, b = make(); return calls * 10 + a + b; }
    """
    assert run(source) == 13


def test_rest_binder_takes_the_tail():
    # `let a, rst... = t;` is `a = t[0]`, `rst = t[1:]` -- the stage-2 copy.
    source = """
    fn main() -> int32 {
        let t = (1, 2, 3);
        let a, rst... = t;                          // rst: tuple<int32, int32>
        return a + rst[0] + rst[1] + len(rst) as int32;
    }
    """
    assert run(source) == 8


def test_rest_tail_narrows_all_the_way_down():
    # On a pair the tail is the 1-tuple; binding every position leaves the
    # empty tuple -- the arities the surface takes everywhere.
    source = """
    fn main() -> int32 {
        let pair = (7, 8);
        let a, one... = pair;                       // one: tuple<int32>
        let x, y, none... = pair;                   // none: tuple<>
        return a + one[0] + x + y + len(none) as int32;
    }
    """
    assert run(source) == 30


def test_lone_rest_binder_copies_the_whole_tuple():
    # `let r... = t;` is `r = t[0:]`: legal, a plain whole copy.
    source = """
    fn main() -> int32 {
        let t = (40, 2);
        let r... = t;
        return r[0] + r[1];
    }
    """
    assert run(source) == 42


def test_lone_rest_binder_on_the_empty_tuple():
    source = """
    fn main() -> int32 {
        let u = ();
        let r... = u;
        return len(r) as int32;
    }
    """
    assert run(source) == 0


def test_destructure_nested_tuple_binds_whole_positions():
    # No nested binder grammar: an inner tuple binds as one value.
    source = """
    fn main() -> int32 {
        let t = ((1, 2), 3);
        let inner, c = t;
        return inner[0] + inner[1] + c;
    }
    """
    assert run(source) == 6


def test_rest_binder_is_a_copy_not_a_view():
    source = """
    fn main() -> int32 {
        let t = (1, 2, 3);
        let a, rst... = t;
        rst[0] = 50;                    // writes the copy, not t
        return t[1] + rst[0] + a;
    }
    """
    assert run(source) == 53


def test_destructure_a_const_parameter_source():
    # Binders are fresh locals: reassignable, and writes never reach the
    # const source.
    source = """
    fn f(const t: tuple<int32, int32>) -> int32 {
        let a, b = t;
        a = a + 1;
        return a + b + t[0];
    }
    fn main() -> int32 { return f((1, 2)); }
    """
    assert run(source) == 5


def test_destructure_too_few_binders_rejected():
    with pytest.raises(
        LangError,
        match=r"cannot destructure tuple<int32, int32, int32> into 2 binders "
        r"\(it has 3 positions\)",
    ):
        compile_ir(
            "fn main() -> int32 { let t = (1, 2, 3); let a, b = t; return a; }"
        )


def test_destructure_too_many_binders_rejected():
    with pytest.raises(
        LangError,
        match=r"cannot destructure tuple<int32, int32> into 3 binders "
        r"\(it has 2 positions\)",
    ):
        compile_ir(
            "fn main() -> int32 { let t = (1, 2); let a, b, c = t; return a; }"
        )


def test_rest_binder_overflow_rejected():
    # With a rest binder the fixed binders may still not exceed the arity.
    with pytest.raises(
        LangError,
        match=r"cannot destructure tuple<int32> into 2 binders and a rest "
        r"\(it has 1 position\)",
    ):
        compile_ir(
            "fn main() -> int32 { let t = (1,); let a, b, r... = t; return a; }"
        )


def test_destructure_the_empty_tuple_rejected():
    with pytest.raises(
        LangError,
        match=r"cannot destructure tuple<> into 2 binders \(it has no positions\)",
    ):
        compile_ir("fn main() -> int32 { let a, b = (); return 0; }")


def test_rest_binder_not_last_is_a_parse_error():
    with pytest.raises(LangError, match="'a...' must be the last binder"):
        compile_ir("fn main() -> int32 { let a..., b = (1, 2); return 0; }")


def test_annotated_binders_rejected():
    with pytest.raises(
        LangError,
        match="destructuring binders take their types from the source; "
        "drop the annotation",
    ):
        compile_ir("fn main() -> int32 { let a, b: int32 = (1, 2); return 0; }")


def test_trailing_comma_binder_is_a_parse_error():
    with pytest.raises(LangError, match="expected 'IDENT', got '='"):
        compile_ir("fn main() -> int32 { let a, = (1, 2); return 0; }")


def test_uninitialized_destructure_is_a_parse_error():
    with pytest.raises(LangError, match="expected '=', got ';'"):
        compile_ir("fn main() -> int32 { let a, b; return 0; }")


def test_duplicate_binder_rejected():
    with pytest.raises(
        LangError, match="variable 'a' already declared in this scope"
    ):
        compile_ir("fn main() -> int32 { let t = (1, 2); let a, a = t; return a; }")


def test_destructure_a_scalar_rejected():
    with pytest.raises(
        LangError,
        match="cannot destructure int32; only a tuple or slice can be destructured",
    ):
        compile_ir(
            "fn main() -> int32 { let x: int32 = 5; let a, b = x; return a; }"
        )


# --------------------------------------------------- rejected in this stage

def test_equality_operator_is_rejected_as_on_structs():
    with pytest.raises(
        LangError, match=r"operator '==' not supported for tuple<int32, int32>"
    ):
        compile_ir(
            "fn main() -> int32 { let a = (1, 2); let b = (1, 2); "
            "return a == b ? 1 : 0; }"
        )


def test_extends_a_tuple_is_rejected():
    with pytest.raises(
        LangError,
        match="a tuple cannot be extended, but 'p' extends "
        "tuple<int32, int32>; declare the fields as a struct instead",
    ):
        compile_ir(
            "struct p extends tuple<int32, int32> { z: int32; }\n"
            "fn main() -> int32 { let x: p; return 0; }"
        )


def test_user_struct_named_tuple_is_rejected():
    # `tuple` is a reserved type name, like `slice` and `any`.
    with pytest.raises(LangError, match="type 'tuple' already defined"):
        compile_ir("struct tuple<T> { v: T; } fn main() -> int32 { return 0; }")


def test_extern_crossing_uses_the_struct_abi_classification():
    # Pinned behavior ahead of stage 4: a tuple in an @extern signature
    # already classifies as the layout-equivalent C struct (here one
    # SysV/AAPCS64 eightbyte -> i64), because the ABI classifier keys on the
    # field list a tuple shares with structs.
    out = compile_ir(
        "@extern fn f(t: tuple<int32, int32>);\n"
        "fn main() -> int32 { f((1, 2)); return 0; }"
    )
    assert 'declare void @"f"(i64' in out
