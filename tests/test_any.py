"""`any`: the builtin tagged box, and its `case type` type-switch.

An `any` is `{ tag: uint64; payload: 16 bytes, align 8 }` -- 24 bytes. The tag
is the FNV-1a hash of the boxed type's canonical name, computed at compile
time; values box implicitly at the coerce choke point (assignment, argument
passing, return, stores), and the only way to recover one is
`case type (a) { when int32 n: ... else: ... }`. The v1 boxable set is
primitives, pointers, and slices.
"""

import pytest

import mcc.codegen.generator as generator
from mcc.codegen import CodeGen
from mcc.codegen.types import fnv1a64
from mcc.driver import emit_interface
from mcc.errors import LangError
from mcc.interface import render_interface
from mcc.lexer import tokenize
from mcc.nodes import CaseType
from mcc.parser import Parser
from helpers import compile_ir, parse, run, run_path


# ---------------------------------------------------------------- parsing

def test_parses_into_a_case_type_node():
    (func,) = parse(
        "fn f(a: any) { case type (a) { when int32 n: g(); else: h(); } }"
    ).functions
    (node,) = func.body
    assert isinstance(node, CaseType)
    ((type_refs, name, body, _line),) = node.arms
    (type_ref,) = type_refs
    assert type_ref.name == "int32" and name == "n" and len(body) == 1
    assert len(node.otherwise) == 1


def test_else_arm_is_required():
    with pytest.raises(LangError, match="case type needs an else arm"):
        parse("fn f(a: any) { case type (a) { when int32 n: g(); } }")


def test_arm_binding_is_required():
    with pytest.raises(LangError, match="a case type arm needs a binding name"):
        parse("fn f(a: any) { case type (a) { when int32: g(); else: h(); } }")


def test_parses_a_multi_type_arm():
    # `when int32, int16 n:` is one arm: a type list over a single binding.
    (func,) = parse(
        "fn f(a: any) { case type (a) { when int32, int16 n: g(); else: h(); } }"
    ).functions
    (node,) = func.body
    ((type_refs, name, _body, _line),) = node.arms
    assert [t.name for t in type_refs] == ["int32", "int16"] and name == "n"


def test_generic_arm_needs_no_new_syntax():
    # `when T* ptr:` parses like any other arm: one TypeRef, one binding.
    # Whether `T` is a concrete type or an arm-scoped type parameter is
    # decided at codegen by name resolution (see tests/test_generic_arms.py).
    (func,) = parse(
        "fn f(a: any) { case type (a) { when T* ptr: g(); else: h(); } }"
    ).functions
    (node,) = func.body
    ((type_refs, name, _body, _line),) = node.arms
    (type_ref,) = type_refs
    assert type_ref.name == "T" and type_ref.stars == 1 and name == "ptr"


def test_multi_type_arm_still_needs_a_binding():
    with pytest.raises(LangError, match="a case type arm needs a binding name"):
        parse(
            "fn f(a: any) { case type (a) { when int32, float64: g(); else: h(); } }"
        )


def test_else_still_required_beside_a_multi_type_arm():
    # An explicit type list does not close the universe of boxable types.
    with pytest.raises(LangError, match="case type needs an else arm"):
        parse("fn f(a: any) { case type (a) { when int32, int16 n: g(); } }")


def test_type_stays_a_contextual_keyword():
    # `type` still declares an alias and still names a variable; only
    # `case type (` means type-switch.
    assert run(
        "type myint = int32;\n"
        "fn main() -> int32 { let type: myint = 0; return type; }"
    ) == 0


# ------------------------------------------------------- boxing + recovery

def test_each_boxable_category_round_trips():
    # One value per v1 category -- integer, bool, char, float64, pointer,
    # slice -- boxed implicitly and recovered by its own tag.
    assert run(
        """
        fn kind(a: any) -> int32 {
            case type (a) {
                when uint8 u:        return 1;
                when bool b:         return b ? 2 : -2;
                when char c:         return c == 'x' ? 3 : -3;
                when float64 f:      return f == 2.5 ? 4 : -4;
                when int32* p:       return *p == 44 ? 5 : -5;
                when slice<char> s:  return s.length == 5 ? 6 : -6;
                else:                return 0;
            }
        }
        fn main() -> int32 {
            let u: uint8 = 9;
            let c: char = 'x';
            let n: int32 = 44;
            let s: slice<char> = "hello" as slice<char>;
            if (kind(u) != 1)      { return 1; }
            if (kind(true) != 2)   { return 2; }
            if (kind(c) != 3)      { return 3; }
            if (kind(2.5) != 4)    { return 4; }
            if (kind(&n) != 5)     { return 5; }
            if (kind(s) != 6)      { return 6; }
            return 0;
        }
        """
    ) == 0


def test_untyped_literal_anchors_as_int32():
    # `5` boxes at its adaptable default placeholder -- int32, the same rule
    # call-site inference uses -- so the int64 arm must not match.
    assert run(
        """
        fn which(a: any) -> int32 {
            case type (a) {
                when int64 wide: return 1;
                when int32 n:    return 2;
                else:            return 3;
            }
        }
        fn main() -> int32 { return which(5) == 2 ? 0 : 1; }
        """
    ) == 0


def test_each_pointer_type_gets_its_own_tag():
    # char* and uint8* are distinct tags: a char* never matches a uint8* arm.
    assert run(
        """
        fn main() -> int32 {
            let a: any = "text";
            case type (a) {
                when uint8* p: return 1;
                when char* s:  return 0;
                else:          return 2;
            }
        }
        """
    ) == 0


def test_boxing_at_assignment_return_and_store():
    # The coerce choke point covers reassignment, `return`, a struct field,
    # an array element, and a store through an any* -- all box the same way.
    assert run(
        """
        struct holder { v: any; }
        fn wrap() -> any { return 2.5; }
        fn unwrap_int(a: any) -> int32 {
            case type (a) { when int32 n: return n; else: return -1; }
        }
        fn main() -> int32 {
            let a: any = 1;
            a = 7;                       // reassignment re-boxes
            if (unwrap_int(a) != 7) { return 1; }
            let h: struct holder;
            h.v = 8;                     // field store boxes
            if (unwrap_int(h.v) != 8) { return 2; }
            let xs: any[2];
            xs[0] = 9;                   // element store boxes
            if (unwrap_int(xs[0]) != 9) { return 3; }
            *(&a) = 10;                  // store through an any* boxes
            if (unwrap_int(a) != 10) { return 4; }
            case type (wrap()) {         // an rvalue subject works too
                when float64 f: return f == 2.5 ? 0 : 5;
                else: return 6;
            }
        }
        """
    ) == 0


def test_any_pointer_subject_auto_dereferences():
    assert run(
        """
        fn through(p: any*) -> int32 {
            case type (p) { when int32 n: return n; else: return -1; }
        }
        fn main() -> int32 {
            let a: any = 21;
            return through(&a) == 21 ? 0 : 1;
        }
        """
    ) == 0


def test_const_any_param_recovers_its_value():
    # A const any parameter rides the const-struct hidden-reference
    # convention; the type-switch still reads it.
    assert run(
        """
        fn get(const a: any) -> int32 {
            case type (a) { when int32 n: return n; else: return -1; }
        }
        fn main() -> int32 { return get(33) == 33 ? 0 : 1; }
        """
    ) == 0


def test_any_to_any_is_a_copy_not_a_nesting():
    assert run(
        """
        fn main() -> int32 {
            let a: any = 5;
            let b: any = a;   // copies the box; no any-in-any
            case type (b) { when int32 n: return n == 5 ? 0 : 1; else: return 2; }
        }
        """
    ) == 0


def test_transparent_enum_boxes_under_its_underlying_tag():
    assert run(
        """
        enum color: int32 { red = 7 }
        fn main() -> int32 {
            let a: any = color::red;
            case type (a) { when int32 n: return n == 7 ? 0 : 1; else: return 2; }
        }
        """
    ) == 0


def test_zero_filled_global_any_matches_only_else():
    # An uninitialized global is zero-filled: tag 0, which no type name
    # hashes to (FNV-1a never yields 0 for these names), so only else runs.
    assert run(
        """
        @static let g: any;
        fn main() -> int32 {
            case type (g) {
                when int32 n:   return 1;
                when uint64 u:  return 2;
                else:           return 0;
            }
        }
        """
    ) == 0


def test_arms_fall_through_to_the_code_after_the_switch():
    # A non-diverging arm (and else) branches to the end block; execution
    # continues after the type-switch.
    assert run(
        """
        fn score(a: any) -> int32 {
            let out: int32 = 0;
            case type (a) {
                when int32 n:   out = n;
                when float64 f: out = 100;
                else:           out = -1;
            }
            return out + 1;
        }
        fn main() -> int32 {
            if (score(41) != 42)  { return 1; }
            if (score(1.5) != 101) { return 2; }
            if (score(true) != 0)  { return 3; }
            return 0;
        }
        """
    ) == 0


def test_sizeof_and_alignof():
    assert run(
        "fn main() -> int32 {"
        " return (sizeof(any) == 24 and alignof(any) == 8) ? 0 : 1; }"
    ) == 0


# --------------------------------------------------------- multi-type arms

def test_multi_type_arm_groups_dispatch():
    # One arm, several tags: each listed type matches its own tag and the
    # binding holds the recovered value, typed as that copy's type.
    assert run(
        """
        fn kind(a: any) -> int32 {
            case type (a) {
                when int32, int16, int8 n:    return n as int32;
                when uint32, uint16, uint8 u: return (u as int32) + 100;
                else:                         return -1;
            }
        }
        fn main() -> int32 {
            let w: int16 = -3;
            let b: uint8 = 7;
            if (kind(5) != 5)     { return 1; }
            if (kind(w) != -3)    { return 2; }
            if (kind(b) != 107)   { return 3; }
            if (kind(2.5) != -1)  { return 4; }
            return 0;
        }
        """
    ) == 0


def test_binding_is_typed_per_listed_type():
    # The shared body compiles once per listed type: an overload set resolves
    # against each copy's concrete binding type, not a union.
    assert run(
        """
        fn width(n: int32) -> int32 { return 4; }
        fn width(n: int16) -> int32 { return 2; }
        fn probe(a: any) -> int32 {
            case type (a) {
                when int32, int16 n: return width(n);
                else:                return -1;
            }
        }
        fn main() -> int32 {
            let w: int16 = 1;
            if (probe(1) != 4) { return 1; }
            if (probe(w) != 2) { return 2; }
            return 0;
        }
        """
    ) == 0


def test_multi_type_arm_falls_through_per_copy():
    # A non-diverging copy branches to the shared end block, like any arm.
    assert run(
        """
        fn score(a: any) -> int32 {
            let out: int32 = -1;
            case type (a) {
                when int32, int16 n: out = n as int32;
                else:                out = 0;
            }
            return out + 1;
        }
        fn main() -> int32 {
            let w: int16 = 9;
            if (score(41) != 42)  { return 1; }
            if (score(w) != 10)   { return 2; }
            if (score(2.5) != 1)  { return 3; }
            return 0;
        }
        """
    ) == 0


def test_duplicate_type_within_one_list_is_rejected():
    with pytest.raises(LangError, match="duplicate case type arm for int32"):
        compile_ir(
            "fn main() -> int32 { let a: any = 5;"
            " case type (a) { when int32, int32 n: return 1; else: return 0; } }"
        )


def test_duplicate_between_a_list_and_a_later_arm_is_rejected():
    with pytest.raises(LangError, match="duplicate case type arm for int16"):
        compile_ir(
            "fn main() -> int32 { let a: any = 5;"
            " case type (a) { when int32, int16 n: return 1;"
            " when int16 m: return 2; else: return 0; } }"
        )


def test_per_type_body_failure_names_the_offending_type():
    # Every copy is fully type-checked: a listed type for which the shared
    # body does not compile errors out, the note naming that type (the
    # primary `file: error: line N: message` head stays intact).
    with pytest.raises(LangError, match="cannot dereference a int32") as exc:
        compile_ir(
            "fn main() -> int32 { let a: any = 5;"
            " case type (a) { when int32*, int32 p: return *p; else: return 0; } }"
        )
    assert any(
        "in case type arm for int32" == note.message for note in exc.value.notes
    )


def test_unknown_type_in_a_list_is_rejected():
    # Stage 1 lists take concrete types only; an unresolved name is still the
    # plain unknown-type error (generic T*/T arms are a later stage).
    with pytest.raises(LangError, match="unknown type 'in32'"):
        compile_ir(
            "fn main() -> int32 { let a: any = 5;"
            " case type (a) { when int32, in32 n: return 1; else: return 0; } }"
        )


# ------------------------------------------------ struct boxing by reference

def test_struct_boxes_by_reference_into_a_variadic():
    # A struct extra collects into the trailing slice<const any> by hidden
    # reference; `case type` recovers it and reads the caller's field values.
    assert run(
        """
        struct point { x: int32; y: int32; }
        fn sum_first(args...) -> int32 {
            case type (args[0]) {
                when point p:  return p.x + p.y;
                else:          return -1;
            }
        }
        fn main() -> int32 {
            let p: point = { x = 10, y = 20 };
            return sum_first(p) == 30 ? 0 : 1;
        }
        """
    ) == 0


def test_struct_box_payload_holds_a_pointer_not_the_value():
    # The by-reference discipline: the box stores the struct's *address* into
    # the payload (a `point**` bitcast of the payload slot), never the 8-byte
    # struct value -- so the recovery aliases the caller's storage.
    ir = compile_ir(
        """
        struct point { x: int32; y: int32; }
        fn take(args...) -> int32 {
            case type (args[0]) { when point p: return p.x; else: return -1; }
        }
        fn main() -> int32 {
            let p: point = { x = 1, y = 2 };
            return take(p);
        }
        """
    )
    # The payload is reinterpreted as a pointer-to-struct on both the box and
    # the recovery side.
    assert '%"point"**' in ir


def test_struct_rvalue_extra_spills_and_boxes():
    # An rvalue struct (a function return) has no storage of its own, so it
    # spills to a call-scoped temporary whose address is boxed.
    assert run(
        """
        struct point { x: int32; y: int32; }
        fn make(a: int32, b: int32) -> point { return point { x = a, y = b }; }
        fn probe(args...) -> int32 {
            case type (args[0]) {
                when point p:  return p.x * 10 + p.y;
                else:          return -1;
            }
        }
        fn main() -> int32 {
            return probe(make(3, 4)) == 34 ? 0 : 1;
        }
        """
    ) == 0


def test_struct_and_struct_pointer_get_distinct_tags():
    # `point` (by-reference struct) and `point*` (a plain pointer box) tag
    # differently, so their arms never cross.
    assert run(
        """
        struct point { x: int32; y: int32; }
        fn probe(args...) -> int32 {
            let total: int32 = 0;
            case type (args[0]) {
                when point p:   total += 1;    // struct tag
                when point* pp: total += 100;  // pointer tag
                else:           total += -100;
            }
            case type (args[1]) {
                when point p:   total += 1;
                when point* pp: total += 100;
                else:           total += -100;
            }
            return total;
        }
        fn main() -> int32 {
            let q: point = { x = 0, y = 0 };
            return probe(q, &q) == 101 ? 0 : 1;   // struct arm + pointer arm
        }
        """
    ) == 0


def test_const_any_local_borrows_a_struct():
    # A `const any`-typed target is a by-reference position too, so a struct
    # boxes into it; a bare `any` local would be owning and stay rejected.
    assert run(
        """
        struct point { x: int32; y: int32; }
        fn main() -> int32 {
            let p: point = { x = 5, y = 6 };
            let a: const any = p;
            case type (a) {
                when point q:  return q.x + q.y == 11 ? 0 : 1;
                else:          return 2;
            }
        }
        """
    ) == 0


def test_generic_arm_recovers_a_struct_by_reference():
    # A generic `when T v:` arm matches the struct tag from the whole-program
    # boxed set and recovers it as a reference, dispatching into a per-tag
    # overload that reads the caller's fields.
    assert run(
        """
        struct point { x: int32; y: int32; }
        fn area(const p: point) -> int32 { return p.x * p.y; }
        fn area(n: int32) -> int32 { return n; }
        fn probe(args...) -> int32 {
            case type (args[0]) {
                when T v:  return area(v);
                else:      return -1;
            }
            return -2;   // a generic arm defers, so the case is assumed to
                         // reach its end (the documented conservatism)
        }
        fn main() -> int32 {
            let p: point = { x = 3, y = 4 };
            return probe(p) == 12 ? 0 : 1;
        }
        """
    ) == 0


# ------------------------------------------------------------ rejections

def test_owning_struct_boxing_is_rejected():
    # A struct only boxes by reference into a call-scoped const any (a
    # variadic argument); an owning `any` local would outlive the borrow.
    with pytest.raises(
        LangError,
        match=r"cannot box a p into an owning any; a struct only boxes by "
        r"reference into a const any \(e\.g\. a variadic argument\), or box a "
        r"pointer to it \(&value\) instead",
    ):
        compile_ir(
            "struct p { x: int32; }\n"
            "fn main() -> int32 {"
            " let s = struct p { x = 1 }; let a: any = s; return 0; }"
        )


def test_union_boxing_is_rejected():
    with pytest.raises(
        LangError,
        match=r"cannot box a u in an any; box a pointer to it \(&value\) instead",
    ):
        compile_ir(
            "union u { i: int64; f: float64; }\n"
            "fn main() -> int32 {"
            " let v = union u { i = 3 }; let a: any = v; return 0; }"
        )


def test_array_boxing_is_rejected_by_its_array_type():
    # An array decays to a pointer in value contexts; the boxing site still
    # rejects it by the array type instead of silently boxing the pointer.
    with pytest.raises(
        LangError,
        match=r"cannot box a int32\[3\] in an any; box a pointer to its "
        r"first element \(&value\[0\]\) instead",
    ):
        compile_ir(
            "fn main() -> int32 {"
            " let xs: int32[3] = [1, 2, 3]; let a: any = xs; return 0; }"
        )


def test_struct_pointer_is_the_escape_hatch():
    assert run(
        """
        struct p { x: int32; }
        fn main() -> int32 {
            let s = struct p { x = 12 };
            let a: any = &s;
            case type (a) { when p* q: return q->x == 12 ? 0 : 1; else: return 2; }
        }
        """
    ) == 0


def test_bare_null_boxing_is_rejected():
    with pytest.raises(
        LangError,
        match=r"cannot box a bare null in an any; give it a pointer type "
        r"first \(e\.g\. null as uint8\*\)",
    ):
        compile_ir("fn main() -> int32 { let a: any = null; return 0; }")


def test_any_never_nests():
    with pytest.raises(
        LangError, match="cannot box an any in an any; an any never nests"
    ):
        compile_ir(
            "fn main() -> int32 { let a: any = 5;"
            " case type (a) { when any x: return 1; else: return 0; } }"
        )


def test_unboxable_arm_type_is_rejected():
    # An arm whose type can never be boxed is dead by construction: error.
    # A union never boxes (a struct now does, by reference, so it is a live
    # arm even when nothing local boxes one).
    with pytest.raises(LangError, match="cannot box a u in an any"):
        compile_ir(
            "union u { i: int32; f: float64; }\n"
            "fn main() -> int32 { let a: any = 5;"
            " case type (a) { when u v: return 1; else: return 0; } }"
        )


def test_a_struct_arm_is_live_even_without_a_local_box():
    # A struct is a boxable category now (by reference), so a `when p v:` arm
    # is accepted rather than rejected as unboxable -- it is simply a dead
    # tag when nothing in the program boxes a `p`.
    assert run(
        "struct p { x: int32; }\n"
        "fn main() -> int32 { let a: any = 5;"
        " case type (a) { when p v: return 1; else: return 0; } }"
    ) == 0


def test_duplicate_arm_is_rejected():
    with pytest.raises(LangError, match="duplicate case type arm for int32"):
        compile_ir(
            "fn main() -> int32 { let a: any = 5;"
            " case type (a) { when int32 x: return 1;"
            " when int32 y: return 2; else: return 0; } }"
        )


def test_non_any_subject_is_rejected():
    with pytest.raises(
        LangError, match=r"case type needs an any \(or any\*\), got int32"
    ):
        compile_ir(
            "fn main() -> int32 { let n: int32 = 3;"
            " case type (n) { when int32 x: return x; else: return 0; } }"
        )


def test_any_subject_in_a_value_case_is_rejected():
    with pytest.raises(LangError, match="cannot match a any in a case"):
        compile_ir(
            "fn main() -> int32 { let a: any = 5;"
            " case (a) { when 1: return 1; else: return 0; } }"
        )


def test_as_unwrap_is_rejected():
    with pytest.raises(
        LangError,
        match="cannot cast an any to int32; recover its value with case type",
    ):
        compile_ir(
            "fn main() -> int32 { let a: any = 5; let n = a as int32; return n; }"
        )


def test_as_boxing_is_rejected():
    with pytest.raises(
        LangError,
        match="cannot cast int32 to any; boxing is implicit",
    ):
        compile_ir("fn main() -> int32 { let n: int32 = 5; let a = n as any; return 0; }")


def test_any_has_no_fields():
    with pytest.raises(
        LangError, match="an any has no fields; recover its value with case type"
    ):
        compile_ir(
            "fn main() -> int32 { let a: any = 5; return a.tag as int32; }"
        )


def test_global_any_initializer_is_rejected():
    with pytest.raises(
        LangError,
        match="a global any initializer is not supported yet; "
        "assign the value at runtime instead",
    ):
        compile_ir("@static let g: any = 5;\nfn main() -> int32 { return 0; }")


def test_function_pointer_boxing_is_rejected():
    # Function pointers are outside the v1 boxable set (primitives, data
    # pointers, slices).
    with pytest.raises(LangError, match=r"cannot box a fn\(\) -> int32 in an any"):
        compile_ir(
            "fn f() -> int32 { return 1; }\n"
            "fn main() -> int32 {"
            " let g: fn() -> int32 = f; let a: any = g; return 0; }"
        )


def test_any_is_not_generic():
    with pytest.raises(LangError, match="type 'any' is not generic"):
        compile_ir("fn main() -> int32 { let a: any<int32>; return 0; }")


def test_a_user_struct_cannot_shadow_any():
    with pytest.raises(LangError, match="type 'any' already defined"):
        compile_ir("struct any { x: int32; }")


# ------------------------------------------------------------------- tags

def test_fnv1a64_reference_vectors():
    # The classic FNV-1a 64 test vectors; the tag scheme is deterministic
    # across compilations by design (no registry).
    assert fnv1a64("") == 0xCBF29CE484222325
    assert fnv1a64("a") == 0xAF63DC4C8601EC8C
    assert fnv1a64("int32") == fnv1a64("int32")
    assert fnv1a64("int32") != fnv1a64("uint32")


def test_tag_hash_collision_is_a_compile_error(monkeypatch):
    # Force every name onto one tag: the second boxed type must be caught at
    # its own site instead of corrupting the type-switch.
    monkeypatch.setattr(generator, "fnv1a64", lambda name: 7)
    with pytest.raises(
        LangError,
        match="any type tags collide: 'int32' and 'float64' hash to the "
        "same 64-bit id",
    ):
        compile_ir(
            "fn main() -> int32 {"
            " let a: any = 5; let b: any = 2.5; return 0; }"
        )


# -------------------------------------------------------------- interface

def iface(source: str) -> str:
    """Render the interface stub for an import-free source string."""
    program = Parser(tokenize(source)).parse_program()
    imports = list(program.imports)
    cg = CodeGen(program, "test")
    cg.generate()
    return render_interface(cg, source, imports)


def test_any_signature_survives_the_interface_stub():
    out = iface(
        "fn pick(a: any, fallback: int32) -> any {\n"
        "    case type (a) { when int32 n: return a; else: return fallback; }\n"
        "}"
    )
    assert "fn pick(a: any, fallback: int32) -> any;" in out


def test_any_round_trips_through_mci(tmp_path):
    # An @inline body travels in full, so the consumer compiles and runs the
    # boxing and the type-switch entirely from the stub.
    lib = tmp_path / "lib.mc"
    lib.write_text(
        "@inline fn as_int(a: any, fallback: int32) -> int32 {\n"
        "    case type (a) { when int32 n: return n; else: return fallback; }\n"
        "}\n"
    )
    out = tmp_path / "lib.mci"
    assert emit_interface(lib, (tmp_path,), None, {}, out) == 0
    lib.unlink()  # force the import to resolve through the stub
    main = tmp_path / "main.mc"
    main.write_text(
        'import "lib";\n'
        "fn main() -> int32 {\n"
        "    if (as_int(40, -1) != 40) { return 1; }\n"
        "    if (as_int(2.5, -1) != -1) { return 2; }\n"
        "    return 0;\n"
        "}\n"
    )
    assert run_path(main) == 0
