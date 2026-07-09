"""Struct literals: `struct Name { field = expr, ... }` and the keyword-free
`Name { field = expr, ... }` shorthand."""

import pytest

from mcc.errors import LangError
from mcc.nodes import StructLit, Var
from helpers import compile_ir, parse, run

POINT = "struct point { x: int32; y: int32; }\n"


# --------------------------------------------------------------------- parser


def test_struct_literal_parses():
    (stmt,) = parse(
        "fn f() { let p = struct point { x = 6, y = 4 }; }"
    ).functions[0].body
    lit = stmt.value
    assert isinstance(lit, StructLit)
    assert lit.type_ref.name == "point"
    assert [name for name, _ in lit.fields] == ["x", "y"]


def test_struct_literal_trailing_comma_and_empty():
    (with_comma,) = parse(
        "fn f() { let p = struct point { x = 1, }; }"
    ).functions[0].body
    assert [name for name, _ in with_comma.value.fields] == ["x"]

    (empty,) = parse("fn f() { let p = struct point { }; }").functions[0].body
    assert empty.value.fields == []


def test_generic_struct_literal_parses():
    (stmt,) = parse(
        "fn f() { let p = struct pair<int32, uint8*> { a = 1, b = null }; }"
    ).functions[0].body
    assert [str(t) for t in stmt.value.type_ref.args] == ["int32", "uint8*"]


# -------------------------------------------------------------------- codegen


def test_struct_literal_zero_initializes_omitted_fields():
    ir_text = compile_ir(
        POINT + "fn f() -> int32 { let p = struct point { x = 9 }; return p.y; }"
    )
    # the temporary is zero-filled before the named fields are stored
    assert "zeroinitializer" in ir_text


def test_struct_literal_sets_fields():
    source = POINT + """
        fn main() -> int32 {
            let p = struct point { x = 6, y = 4 };
            return p.x * 10 + p.y;
        }
    """
    assert run(source) == 64


def test_struct_literal_omitted_field_is_zero():
    source = POINT + """
        fn main() -> int32 {
            let p = struct point { x = 7 };
            return p.x + p.y;     // y defaulted to 0
        }
    """
    assert run(source) == 7


def test_empty_struct_literal_is_all_zero():
    source = POINT + """
        fn main() -> int32 {
            let p = struct point { };
            return p.x + p.y;
        }
    """
    assert run(source) == 0


def test_generic_struct_literal_runs():
    source = """
        struct pair<A, B> { a: A; b: B; }
        fn main() -> int32 {
            let p = struct pair<int32, int32> { a = 30, b = 12 };
            return p.a + p.b;
        }
    """
    assert run(source) == 42


def test_generic_struct_literal_infers_type_args():
    source = """
        struct pair<A, B> { a: A; b: B; }
        fn main() -> int32 {
            let n: int64 = 30;
            let m: int32 = 12;
            // A=int64, B=int32 -- both anchored by typed values, not guessed.
            let p = struct pair { a = n, b = m };
            return (p.a as int32) + p.b;
        }
    """
    assert run(source) == 42


def test_inference_typed_field_pins_parameter():
    source = """
        struct box<T> { lo: T; hi: T; }
        fn main() -> int32 {
            let n: int64 = 40;
            let b = struct box { lo = 2, hi = n };   // lo adapts to int64 from hi
            return (b.lo + b.hi) as int32;
        }
    """
    assert run(source) == 42


def test_inference_conflict_is_rejected():
    source = """
        struct box<T> { lo: T; hi: T; }
        fn f(a: int32, b: int64) { let x = struct box { lo = a, hi = b }; }
    """
    with pytest.raises(LangError, match="conflicting types for type parameter T"):
        compile_ir(source)


def test_uninferable_parameter_is_rejected():
    source = """
        struct holder<T> { tag: int32; item: T; }
        fn f() { let h = struct holder { tag = 1 }; }
    """
    with pytest.raises(LangError, match="cannot infer type parameter.*T"):
        compile_ir(source)


def test_untyped_only_field_does_not_anchor_parameter():
    # Regression: an untyped constant must not silently pick `int32` for T, the
    # same ambiguity `let a = 0` raises -- only a typed value (or explicit type
    # args) anchors a parameter. The constant still adapts once T is fixed.
    source = """
        struct box<T> { lo: T; hi: T; }
        fn f() { let b = struct box { lo = 0, hi = 10 }; }
    """
    with pytest.raises(LangError, match="cannot infer type parameter.*T"):
        compile_ir(source)


def test_unknown_field_on_generic_literal_is_rejected():
    source = """
        struct box<T> { lo: T; hi: T; }
        fn f() { let h = struct box { nope = 1 }; }
    """
    with pytest.raises(LangError, match="no field 'nope'"):
        compile_ir(source)


# ----------------------------------------------------------- keyword-free form


def test_keyword_free_literal_parses():
    (stmt,) = parse(
        "fn f() { let p = point { x = 6, y = 4 }; }"
    ).functions[0].body
    lit = stmt.value
    assert isinstance(lit, StructLit)
    assert lit.type_ref.name == "point"
    assert [name for name, _ in lit.fields] == ["x", "y"]


def test_keyword_free_generic_literal_parses():
    (stmt,) = parse(
        "fn f() { let p = pair<int32, uint8*> { a = 1, b = null }; }"
    ).functions[0].body
    assert isinstance(stmt.value, StructLit)
    assert [str(t) for t in stmt.value.type_ref.args] == ["int32", "uint8*"]


def test_keyword_free_literal_runs():
    source = POINT + """
        fn flip(p: struct point) -> struct point {
            return point { x = p.y, y = p.x };      // as a return value
        }
        fn main() -> int32 {
            let p = flip(point { x = 4, y = 6 });   // as an argument
            return p.x * 10 + p.y;
        }
    """
    assert run(source) == 64


def test_keyword_free_generic_inference_runs():
    source = """
        struct pair<A, B> { a: A; b: B; }
        fn main() -> int32 {
            let n: int64 = 30;
            let m: int32 = 12;
            let p = pair { a = n, b = m };
            return (p.a as int32) + p.b;
        }
    """
    assert run(source) == 42


def test_keyword_free_nested_literal_runs():
    source = """
        struct point { x: int32; y: int32; }
        struct line { from: struct point; to: struct point; }
        fn main() -> int32 {
            let l = line {
                from = point { x = 1, y = 2 },
                to = point { x = 3, y = 4 },
            };
            return l.from.x + l.from.y + l.to.x + l.to.y;
        }
    """
    assert run(source) == 10


def test_keyword_free_literal_through_pointer():
    source = POINT + """
        fn main() -> int32 {
            let p = point { };
            let q = &p;
            *q = point { x = 40, y = 2 };
            return q->x + q->y;
        }
    """
    assert run(source) == 42


def test_for_header_name_brace_is_loop_body():
    # In a `for x in <expr> { ... }` header a bare `Name {` must read `{` as
    # the loop body, not a struct literal -- the one barred position.
    fn = parse("fn f(xs: struct list<int32>*) { for x in xs { } }").functions[0]
    (loop,) = fn.body
    assert isinstance(loop.iterable, Var)
    assert loop.iterable.name == "xs"
    assert loop.body == []


def test_for_header_parenthesized_literal_is_iterable():
    # Parenthesizing forces the literal reading back on.
    fn = parse(
        "fn f() { for x in (count { limit = 3 }) { } }"
    ).functions[0]
    (loop,) = fn.body
    assert isinstance(loop.iterable, StructLit)
    assert loop.iterable.type_ref.name == "count"


def test_for_header_call_argument_literal_is_allowed():
    # Bracket/paren-delimited sub-expressions of the header are unambiguous,
    # so a literal is allowed again inside them.
    fn = parse("fn f() { for x in make(point { x = 1 }) { } }").functions[0]
    (loop,) = fn.body
    (arg,) = loop.iterable.args
    assert isinstance(arg, StructLit)


# --------------------------------------------------------- default member values


def test_field_default_parses():
    (decl,) = parse("struct cfg { cap: int32 = 16; name: uint8*; }").structs
    assert decl.defaults.keys() == {"cap"}
    assert "name" not in decl.defaults


def test_omitted_field_uses_its_default():
    source = """
        struct cfg { cap: int32 = 16; flag: int32 = 1; }
        fn main() -> int32 {
            let c = struct cfg { };       // both defaulted
            return c.cap + c.flag;        // 16 + 1
        }
    """
    assert run(source) == 17


def test_provided_field_overrides_default():
    source = """
        struct cfg { cap: int32 = 16; }
        fn main() -> int32 {
            let c = struct cfg { cap = 5 };
            return c.cap;
        }
    """
    assert run(source) == 5


def test_default_with_generic_and_inference():
    source = """
        struct range<T> { start: T = 0; end: T; }
        fn main() -> int32 {
            let e: int32 = 5;
            let r = struct range { end = e };   // start defaults to 0; T=int32 from end
            return r.end - r.start;
        }
    """
    assert run(source) == 5


def test_inherited_field_default():
    source = """
        struct base { tag: int32 = 7; }
        struct derived extends base { extra: int32 = 9; }
        fn main() -> int32 {
            let d = struct derived { };
            return d.tag + d.extra;     // 7 + 9
        }
    """
    assert run(source) == 16


def test_plain_declaration_applies_defaults():
    source = """
        struct cfg { cap: int32 = 16; flag: int32 = 1; other: int32; }
        fn main() -> int32 {
            let c: struct cfg;          // default-initialized, no literal
            return c.cap + c.flag + c.other;   // 16 + 1 + 0
        }
    """
    assert run(source) == 17


def test_plain_declaration_applies_inherited_defaults():
    source = """
        struct base { tag: int32 = 7; }
        struct derived extends base { extra: int32 = 9; }
        fn main() -> int32 {
            let d: struct derived;
            return d.tag + d.extra;
        }
    """
    assert run(source) == 16


def test_nested_struct_literal():
    source = """
        struct point { x: int32; y: int32; }
        struct line { from: struct point; to: struct point; }
        fn main() -> int32 {
            let l = struct line {
                from = struct point { x = 1, y = 2 },
                to = struct point { x = 3, y = 4 },
            };
            return l.from.x + l.from.y + l.to.x + l.to.y;
        }
    """
    assert run(source) == 10


def test_struct_literal_as_value_through_pointer():
    source = POINT + """
        fn main() -> int32 {
            let p = struct point { x = 1, y = 2 };
            let q = &p;
            *q = struct point { x = 40, y = 2 };
            return q->x + q->y;
        }
    """
    assert run(source) == 42


def test_struct_literal_adapts_untyped_constant():
    source = """
        struct box { v: uint64; }
        fn main() -> int32 {
            let b = struct box { v = 5 };   // 5 adapts to uint64
            return b.v as int32;
        }
    """
    assert run(source) == 5


# ------------------------------------------- string/array literal field adaptation


def test_string_literal_field_borrows_into_char_slice():
    # A string literal in a `slice<const char>` field borrows with no `as`, the
    # struct-literal position of the same adaptation a let/argument allows: the
    # length is NUL-free (2 for "ls").
    source = """
        struct cmd { name: slice<const char>; argc: int32; }
        fn main() -> int32 {
            let c = struct cmd { name = "ls", argc = 3 };
            return c.argc + (c.name.length as int32);
        }
    """
    assert run(source) == 5


def test_array_literal_field_borrows_into_slice():
    source = """
        struct nums { xs: slice<int32>; n: int32; }
        fn main() -> int32 {
            let m = struct nums { xs = [10, 20, 30], n = 3 };
            return m.xs[0] + m.xs[2] + m.n;
        }
    """
    assert run(source) == 43


def test_string_literal_field_is_source_order_preserving():
    # A string literal is side-effect-free, so a non-generic literal evaluates
    # its fields strictly left to right -- the field after the string still runs
    # after it. (Two ordinary fields; the string sits between them.)
    source = """
        struct rec { a: int32; name: slice<const char>; b: int32; }
        fn main() -> int32 {
            let r = struct rec { a = 1, name = "hey", b = 2 };
            return r.a + (r.name.length as int32) + r.b;
        }
    """
    assert run(source) == 6


def test_literal_field_never_infers_a_slice_type():
    # The invariant: a literal field never drives generic inference. `box { v =
    # "hello" }` has field `v: T` (a bare parameter, not a slice), so the string
    # binds T = char* -- it monomorphizes to box<char*>, exactly as before, and
    # does NOT become box<slice<...>>.
    source = """
        struct box<T> { v: T; }
        fn main() -> int32 {
            let b = struct box { v = "hello" };
            return 0;
        }
    """
    ir = compile_ir(source)
    assert "box<char*>" in ir
    assert "box<slice" not in ir


def test_array_literal_against_bare_type_param_stays_rejected():
    # An array literal against a bare type parameter cannot lower (nothing fixes
    # the element type or storage), so it stays the same error as anywhere else
    # an array literal has no receiving array/slice type.
    source = """
        struct box<T> { v: T; }
        fn f() { let b = struct box { v = [1, 2, 3] }; }
    """
    with pytest.raises(LangError, match="an array literal is only allowed"):
        compile_ir(source)


def test_generic_struct_infers_from_typed_field_while_literal_adapts():
    # T is inferred from the typed non-literal field `val`; the string field,
    # whose declared type is the concrete `slice<const char>`, sits out
    # inference and borrows once the struct type is fixed.
    source = """
        struct row<T> { name: slice<const char>; val: T; }
        fn main() -> int32 {
            let seven: int32 = 7;
            let r = struct row { name = "x", val = seven };
            return r.val + (r.name.length as int32);
        }
    """
    assert run(source) == 8


def test_explicit_generic_args_array_literal_field():
    source = """
        struct nums<T> { xs: slice<T>; n: int32; }
        fn main() -> int32 {
            let m = struct nums<int32> { xs = [1, 2, 3], n = 3 };
            return m.xs[1] + m.n;
        }
    """
    assert run(source) == 5


def test_nested_struct_literal_with_string_field():
    source = """
        struct inner { name: slice<const char>; }
        struct outer { i: struct inner; k: int32; }
        fn main() -> int32 {
            let o = struct outer { i = struct inner { name = "hi" }, k = 4 };
            return o.k + (o.i.name.length as int32);
        }
    """
    assert run(source) == 6


def test_union_literal_string_member_borrows():
    source = """
        union u { s: slice<const char>; n: int32; }
        fn main() -> int32 {
            let x = union u { s = "abc" };
            return x.s.length as int32;
        }
    """
    assert run(source) == 3


def test_default_field_string_literal_borrows():
    # An omitted field whose declared default is a string literal borrows the
    # same way as a provided one.
    source = """
        struct cfg { name: slice<const char> = "def"; k: int32; }
        fn main() -> int32 {
            let c = struct cfg { k = 9 };
            return c.k + (c.name.length as int32);
        }
    """
    assert run(source) == 12


def test_static_struct_string_literal_field():
    # The @static/const path already folds a string-literal field to a constant
    # {pointer, length} view; pin it.
    source = """
        struct cmd { name: slice<const char>; argc: int32; }
        @static let g: struct cmd = struct cmd { name = "ls", argc = 1 };
        fn main() -> int32 { return g.argc + (g.name.length as int32); }
    """
    assert run(source) == 3


def test_static_struct_array_literal_field():
    # The array-literal sibling also folds through the const path: an anonymous
    # constant global backs the view.
    source = """
        struct nums { xs: slice<const int32>; n: int32; }
        @static let g: struct nums = struct nums { xs = [5, 6], n = 2 };
        fn main() -> int32 { return g.xs[0] + g.xs[1] + g.n; }
    """
    assert run(source) == 13


def test_string_literal_field_ternary_borrows():
    # The adaptation reaches through a ternary of string literals, borrowing arm
    # by arm, so the merged view carries the chosen literal's own length.
    source = """
        struct cmd { name: slice<const char>; flag: int32; }
        fn main() -> int32 {
            let c = struct cmd { name = 1 > 0 ? "yes" : "no", flag = 4 };
            return c.flag + (c.name.length as int32);
        }
    """
    assert run(source) == 7


def test_generic_struct_ternary_string_field_sits_out_inference():
    # A ternary-of-string-literals field against a concrete slice field in a
    # generic struct: it borrows arm by arm and still sits out inference, so T
    # comes from the typed `val` field.
    source = """
        struct row<T> { name: slice<const char>; val: T; }
        fn main() -> int32 {
            let seven: int32 = 7;
            let r = struct row { name = 1 > 0 ? "yes" : "no", val = seven };
            return r.val + (r.name.length as int32);
        }
    """
    assert run(source) == 10


# --------------------------------------------------------------------- errors


def test_unknown_field_is_rejected():
    with pytest.raises(LangError, match="no field 'z'"):
        compile_ir(POINT + "fn f() { let p = struct point { z = 1 }; }")


def test_duplicate_field_is_rejected():
    with pytest.raises(LangError, match="set twice"):
        compile_ir(POINT + "fn f() { let p = struct point { x = 1, x = 2 }; }")


def test_non_struct_type_is_rejected():
    with pytest.raises(LangError, match="needs a struct type"):
        compile_ir("fn f() { let p = struct int32 { x = 1 }; }")


# --------------------------------------------- bare, type-inferred `{ ... }`


def test_bare_struct_literal_parses_without_a_type():
    (stmt,) = parse(
        "fn f() { let p: point = { x = 6, y = 4 }; }"
    ).functions[0].body
    lit = stmt.value
    assert isinstance(lit, StructLit)
    assert lit.type_ref is None  # the type comes from the annotation
    assert [name for name, _ in lit.fields] == ["x", "y"]


def test_bare_struct_literal_single_field_no_comma_parses():
    # `{ x = 9 }` closes with no top-level separator, so it is a struct
    # literal, not a block whose lone statement is an assignment.
    (stmt,) = parse("fn f() { let p: point = { x = 9 }; }").functions[0].body
    assert isinstance(stmt.value, StructLit) and stmt.value.type_ref is None


def test_bare_struct_literal_in_let():
    source = POINT + """
        fn main() -> int32 {
            let p: point = { x = 6, y = 4 };
            return p.x * 10 + p.y;
        }
    """
    assert run(source) == 64


def test_bare_struct_literal_omitted_field_is_zero():
    source = POINT + """
        fn main() -> int32 {
            let p: point = { x = 5 };
            return p.x * 10 + p.y;
        }
    """
    assert run(source) == 50


def test_bare_struct_literal_in_assignment():
    source = POINT + """
        fn main() -> int32 {
            let p: point = { x = 1, y = 2 };
            p = { x = 3, y = 4 };
            return p.x * 10 + p.y;
        }
    """
    assert run(source) == 34


def test_bare_struct_literal_in_return():
    # A struct is a value copy, so it adapts in `return` (unlike an array
    # literal, whose borrowed view would dangle).
    source = POINT + """
        fn make() -> point { return { x = 7, y = 8 }; }
        fn main() -> int32 { let p = make(); return p.x * 10 + p.y; }
    """
    assert run(source) == 78


def test_bare_struct_literal_as_argument():
    source = POINT + """
        fn take(const p: point) -> int32 { return p.x * 10 + p.y; }
        fn main() -> int32 { return take({ x = 2, y = 3 }); }
    """
    assert run(source) == 23


def test_bare_struct_literal_nested_field():
    source = """
        struct point { x: int32; y: int32; }
        struct seg { a: point; b: point; }
        fn main() -> int32 {
            let s: seg = { a = { x = 1, y = 2 }, b = { x = 3, y = 4 } };
            return s.a.x * 1000 + s.a.y * 100 + s.b.x * 10 + s.b.y;
        }
    """
    assert run(source) == 1234


def test_bare_struct_literal_as_array_element():
    source = POINT + """
        fn main() -> int32 {
            let ps: point[2] = [{ x = 1, y = 2 }, { x = 3, y = 4 }];
            return ps[0].x * 1000 + ps[0].y * 100 + ps[1].x * 10 + ps[1].y;
        }
    """
    assert run(source) == 1234


def test_bare_struct_literal_stores():
    source = """
        struct point { x: int32; y: int32; }
        struct holder { p: point; }
        fn main() -> int32 {
            let h: holder = { p = { x = 1, y = 1 } };
            h.p = { x = 2, y = 3 };            // StoreMember
            let arr: point[1] = [{ x = 0, y = 0 }];
            arr[0] = { x = 4, y = 5 };         // StoreIndex
            let pp: point* = &h.p;
            *pp = { x = 6, y = 7 };            // StoreDeref
            return h.p.x * 1000 + h.p.y * 100 + arr[0].x * 10 + arr[0].y;
        }
    """
    assert run(source) == 6745


def test_bare_union_literal():
    source = """
        union u { i: int32; b: bool; }
        fn main() -> int32 { let v: u = { i = 41 }; return v.i; }
    """
    assert run(source) == 41


def test_bare_struct_literal_static_initializer():
    source = """
        struct point { x: int32; y: int32; }
        @static let origin: struct point = { x = 3, y = 9 };
        fn main() -> int32 { return origin.x * 10 + origin.y; }
    """
    assert run(source) == 39


def test_bare_struct_literal_picks_overload_by_fields():
    # `{ x, y }` fits point but not box, so the call resolves unambiguously.
    source = """
        struct point { x: int32; y: int32; }
        struct box { w: int32; h: int32; }
        fn area(const p: point) -> int32 { return p.x * p.y; }
        fn area(const b: box) -> int32 { return b.w + b.h; }
        fn main() -> int32 {
            return area({ x = 3, y = 4 }) * 100 + area({ w = 5, h = 6 });
        }
    """
    assert run(source) == 1211


def test_bare_struct_literal_argument_to_generic_concrete_param():
    source = """
        struct point { x: int32; y: int32; }
        fn tagged<T>(t: T, const p: point) -> int32 { return (t as int32) + p.x; }
        fn main() -> int32 { return tagged(100, { x = 7, y = 0 }); }
    """
    assert run(source) == 107


def test_bare_struct_literal_field_values_with_calls_and_indexing():
    # A field value bearing its own parens/brackets keeps the disambiguation
    # scan at the right depth (the `,` inside `f(...)` is not a field separator).
    source = POINT + """
        fn f(a: int32) -> int32 { return a; }
        fn main() -> int32 {
            let arr: int32[2] = [10, 20];
            let p: point = { x = f(1), y = arr[1] };
            return p.x + p.y;
        }
    """
    assert run(source) == 21


def test_unterminated_bare_struct_literal_is_a_syntax_error():
    # The disambiguation scan runs off the end (no top-level `,`/`;`/`}`), so the
    # `{` falls back to a block-expression, which then fails to close.
    with pytest.raises(LangError):
        compile_ir(POINT + "fn f() { let p: point = { x = 1")


def test_block_expression_still_parses():
    # A `{` opening a statement (not `IDENT =`) stays a block-expression.
    source = "fn main() -> int32 { let n: int32 = { emit 5; }; return n; }"
    assert run(source) == 5


def test_block_expression_with_leading_assignment_stays_a_block():
    # First statement is an assignment `w = 8;` -- a `;` at top level, so this
    # is a block, not a bare struct literal.
    source = """
        fn main() -> int32 {
            let w: int32 = 0;
            let r: int32 = { w = 8; emit w; };
            return r;
        }
    """
    assert run(source) == 8


def test_bare_struct_literal_with_no_context_is_rejected():
    with pytest.raises(LangError, match="bare struct literal .* has no type here"):
        compile_ir(POINT + "fn f() -> int32 { return ({ x = 1 }).x; }")


def test_bare_struct_literal_unknown_field_reports_precisely():
    # In a fixed-type position a bad field reaches gen_struct_lit for a precise
    # error, not the generic "no type here".
    with pytest.raises(LangError, match="no field 'z'"):
        compile_ir(POINT + "fn f() { let p: point = { x = 1, z = 2 }; }")


def test_bare_struct_literal_ternary_arms_do_not_adapt():
    # A bare literal in a ternary arm is a documented non-goal: name the arms.
    with pytest.raises(LangError, match="bare struct literal .* has no type here"):
        compile_ir(
            POINT
            + "fn f() -> int32 { let p: point = 1 > 0 ? { x = 1 } : { x = 2 };"
            + " return p.x; }"
        )
