"""Generic `case type` arms: `when T* ptr:` and `when T v:`.

A generic arm is a real generic context matched against the whole program's
boxed-tag set: `when T* ptr:` takes every boxed pointer tag not claimed by an
earlier arm (`T` bound to the pointee, the binding typed as the pointer), and
`when T v:` takes every remaining boxed tag (`T` bound to the boxed type
itself, pointer tags included). The body monomorphizes once per matching tag
at end of codegen (a fixpoint worklist: copies can box new types and
instantiate new generics), each copy fully type-checked -- a tag the body
doesn't compile for is a compile error whose note names the offending type.
Dispatch is first-match-wins textual order; an arm subsumed by a generic arm
above it is a hard unreachable-arm error. `else` stays mandatory.
"""

import pytest

from mcc.errors import LangError
from helpers import compile_ir, parse, run


# ---------------------------------------------------------- dispatch: T* ptr

def test_pointer_arm_matches_every_boxed_pointer_tag():
    # T binds per tag: sizeof(T) differs per pointee, so the bodies differ.
    assert run(
        """
        struct point { x: int32; y: int32; }
        fn kind(a: any) -> int32 {
            case type (a) {
                when T* ptr: return 100 + sizeof(T) as int32;
                else:        return -1;
            }
            return -2;
        }
        fn main() -> int32 {
            let x: int32 = 7;
            let p = struct point { x = 1, y = 2 };
            if (kind(&x) != 104) { return 1; }
            if (kind(&p) != 108) { return 2; }
            if (kind(5) != -1)   { return 3; }
            return 0;
        }
        """
    ) == 0


def test_pointer_arm_binding_is_the_typed_pointer():
    # The binding is the payload pointer typed T*: dereferencing it reads
    # the pointee, per tag.
    assert run(
        """
        fn deref(a: any) -> int32 {
            case type (a) {
                when T* ptr: return *ptr as int32;
                else:        return -1;
            }
            return -2;
        }
        fn main() -> int32 {
            let x: int32 = 41;
            let y: int64 = 9;
            if (deref(&x) != 41) { return 1; }
            if (deref(&y) != 9)  { return 2; }
            return 0;
        }
        """
    ) == 0


def test_pointer_arm_dispatches_into_a_generic_callee():
    # The roadmap's motivating shape: handle(ptr) into handle<T>(p: T*)
    # compiles per tag like any generic call.
    assert run(
        """
        fn fingerprint<T>(p: T*) -> int32 { return sizeof(T) as int32; }
        fn f(a: any) -> int32 {
            case type (a) {
                when T* ptr: return fingerprint(ptr);
                else:        return 0;
            }
            return -1;
        }
        fn main() -> int32 {
            let c: char = 'x';
            let n: int64 = 1;
            if (f(&c) != 1) { return 1; }
            if (f(&n) != 8) { return 2; }
            return 0;
        }
        """
    ) == 0


# ----------------------------------------------------------- dispatch: T v

def test_value_arm_matches_non_pointer_and_pointer_tags():
    # A lone `T v` arm widens over everything boxed -- pointer tags too,
    # binding e.g. v: char* with T = char*.
    assert run(
        """
        fn width(a: any) -> int32 {
            case type (a) {
                when T v: return sizeof(T) as int32;
                else:     return 0;
            }
            return -1;
        }
        fn main() -> int32 {
            let s: char* = "hi";
            if (width(5) != 4)    { return 1; }  // T = int32
            if (width(2.5) != 8)  { return 2; }  // T = float64
            if (width(s) != 8)    { return 3; }  // T = char* (a pointer tag)
            return 0;
        }
        """
    ) == 0


def test_value_arm_dispatches_into_an_overload_set():
    assert run(
        """
        fn describe(n: int32) -> int32   { return 1; }
        fn describe(f: float64) -> int32 { return 2; }
        fn classify(a: any) -> int32 {
            case type (a) {
                when T v: return describe(v);
                else:     return 0;
            }
            return -1;
        }
        fn main() -> int32 {
            if (classify(7) != 1)   { return 1; }
            if (classify(1.5) != 2) { return 2; }
            return 0;
        }
        """
    ) == 0


def test_no_viable_overload_for_a_boxed_type_is_a_compile_error():
    # A boxed type the body doesn't compile for fails the compile at the
    # case type site; the note names the offending type.
    with pytest.raises(LangError) as exc:
        compile_ir(
            """
            fn describe(n: int32) -> int32 { return 1; }
            fn classify(a: any) -> int32 {
                case type (a) {
                    when T v: return describe(v);
                    else:     return 0;
                }
                return -1;
            }
            fn main() -> int32 {
                let a: any = 2.5;
                return classify(a);
            }
            """
        )
    assert any(
        "in case type arm for float64" == note.message for note in exc.value.notes
    )


# ------------------------------------------------------------ textual order

def test_concrete_arm_shields_its_tag_from_a_generic_arm():
    assert run(
        """
        fn f(a: any) -> int32 {
            case type (a) {
                when int32 n: return 100;
                when T v:     return sizeof(T) as int32;
                else:         return 0;
            }
            return -1;
        }
        fn main() -> int32 {
            if (f(5) != 100) { return 1; }  // the concrete arm keeps int32
            if (f(2.5) != 8) { return 2; }  // T v takes the rest
            return 0;
        }
        """
    ) == 0


def test_pointer_arm_shields_pointer_tags_from_a_value_arm():
    # `T* ptr` first consumes every pointer tag; `T v` takes the remainder.
    assert run(
        """
        fn f(a: any) -> int32 {
            case type (a) {
                when char* s: return 1;
                when T* ptr:  return 2;
                when T v:     return 3;
                else:         return 4;
            }
            return -1;
        }
        fn main() -> int32 {
            let s: char* = "hi";
            let x: int32 = 7;
            if (f(s) != 1)  { return 1; }  // concrete beats both generics
            if (f(&x) != 2) { return 2; }  // pointer tag: the T* arm
            if (f(x) != 3)  { return 3; }  // non-pointer: the T v arm
            return 0;
        }
        """
    ) == 0


def test_concrete_non_pointer_arm_after_a_pointer_arm_is_reachable():
    # A T* arm widens over pointers only, so a later non-pointer concrete
    # arm still fires (the switch's default continues the chain).
    assert run(
        """
        fn f(a: any) -> int32 {
            case type (a) {
                when T* ptr:  return 1;
                when int32 n: return 2;
                else:         return 3;
            }
            return -1;
        }
        fn main() -> int32 {
            let x: int32 = 7;
            if (f(&x) != 1) { return 1; }
            if (f(x) != 2)  { return 2; }
            if (f(2.5) != 3) { return 3; }
            return 0;
        }
        """
    ) == 0


# ------------------------------------------------- unreachable-arm hard errors

def test_concrete_arm_after_a_value_arm_is_unreachable():
    with pytest.raises(
        LangError,
        match="case type arm for int32 is unreachable: the generic arm 'T' "
        "above it matches every type",
    ):
        compile_ir(
            "fn main() -> int32 { let a: any = 5; case type (a) {"
            " when T v: return 1; when int32 n: return 2; else: return 0; } }"
        )


def test_pointer_arm_after_a_value_arm_is_unreachable():
    with pytest.raises(
        LangError,
        match=r"case type arm 'T\*' is unreachable: the generic arm 'T' "
        "above it matches every type",
    ):
        compile_ir(
            "fn main() -> int32 { let a: any = 5; case type (a) {"
            " when T v: return 1; when T* p: return 2; else: return 0; } }"
        )


def test_second_value_arm_is_unreachable():
    with pytest.raises(
        LangError,
        match="case type arm 'U' is unreachable: the generic arm 'T' "
        "above it matches every type",
    ):
        compile_ir(
            "fn main() -> int32 { let a: any = 5; case type (a) {"
            " when T v: return 1; when U w: return 2; else: return 0; } }"
        )


def test_concrete_pointer_arm_after_a_pointer_arm_is_unreachable():
    with pytest.raises(
        LangError,
        match=r"case type arm for int32\* is unreachable: the generic pointer "
        r"arm 'T\*' above it matches every pointer type",
    ):
        compile_ir(
            "fn main() -> int32 { let a: any = 5; case type (a) {"
            " when T* p: return 1; when int32* q: return 2; else: return 0; } }"
        )


def test_second_pointer_arm_is_unreachable():
    with pytest.raises(
        LangError,
        match=r"case type arm 'U\*' is unreachable: the generic pointer "
        r"arm 'T\*' above it matches every pointer type",
    ):
        compile_ir(
            "fn main() -> int32 { let a: any = 5; case type (a) {"
            " when T* p: return 1; when U* q: return 2; else: return 0; } }"
        )


# ------------------------------------------------------------ detection rule

def test_a_resolvable_name_stays_a_concrete_arm():
    # `myint` resolves (an alias), so the arm is concrete: an int64 subject
    # falls to else instead of matching generically.
    assert run(
        """
        type myint = int32;
        fn f(a: any) -> int32 {
            case type (a) {
                when myint n: return 1;
                else:         return 0;
            }
        }
        fn main() -> int32 {
            if (f(5) != 1)          { return 1; }
            if (f(9 as int64) != 0) { return 2; }
            return 0;
        }
        """
    ) == 0


def test_an_enclosing_generic_binding_stays_a_concrete_arm():
    # Inside fn g<T>, `when T v:` is a concrete arm per instantiation: the
    # enclosing binding resolves, so no generic-arm detection fires.
    assert run(
        """
        fn is_t<T>(a: any) -> int32 {
            case type (a) {
                when T v: return 1;
                else:     return 0;
            }
        }
        fn main() -> int32 {
            let a: any = 5;
            if (is_t<int32>(a) != 1) { return 1; }
            if (is_t<int64>(a) != 0) { return 2; }
            return 0;
        }
        """
    ) == 0


def test_two_stars_keep_the_unknown_type_error():
    # Only zero or one star introduces a type parameter; any other
    # unresolved shape keeps today's error.
    with pytest.raises(LangError, match="unknown type 'T'"):
        compile_ir(
            "fn main() -> int32 { let a: any = 5; case type (a) {"
            " when T** p: return 1; else: return 0; } }"
        )


def test_generic_pattern_in_a_multi_type_list_is_rejected():
    # A generic arm binds exactly one pattern; list members must be
    # concrete, so an unresolved name in a list keeps the unknown-type error.
    with pytest.raises(LangError, match="unknown type 'T'"):
        compile_ir(
            "fn main() -> int32 { let a: any = 5; case type (a) {"
            " when T, int32 n: return 1; else: return 0; } }"
        )


def test_else_is_still_required_beside_a_generic_arm():
    with pytest.raises(LangError, match="case type needs an else arm"):
        parse("fn f(a: any) { case type (a) { when T v: g(); } }")


# --------------------------------------------------- the whole-program tag set

def test_tags_boxed_only_inside_a_late_generic_instantiation_dispatch():
    # probe's arm is enqueued before send<T> ever instantiates; the boxing
    # inside the instances still feeds the arm's tag set.
    assert run(
        """
        fn probe(a: any) -> int32 {
            case type (a) {
                when T v: return sizeof(T) as int32;
                else:     return 0;
            }
            return -1;
        }
        fn send<T>(x: T) -> int32 { let a: any = x; return probe(a); }
        fn main() -> int32 {
            if (send(5 as int64) != 8) { return 1; }
            if (send(true) != 1)       { return 2; }
            return 0;
        }
        """
    ) == 0


def test_boxing_inside_an_arm_body_feeds_the_fixpoint():
    # Compiling a body copy boxes a new type (int64), which must feed back
    # into every generic arm's tag set -- the finalize worklist fixpoint.
    assert run(
        """
        fn inner(b: any) -> int32 {
            case type (b) {
                when T v: return sizeof(T) as int32;
                else:     return 0;
            }
            return -1;
        }
        fn outer(a: any) -> int32 {
            case type (a) {
                when T v: {
                    let b: any = 100 as int64;
                    return inner(b) + sizeof(T) as int32;
                }
                else: return 0;
            }
            return -1;
        }
        fn main() -> int32 {
            // int32 boxes here; int64 boxes only inside outer's arm copy.
            return outer(5) == 12 ? 0 : 1;  // inner(int64)=8 + sizeof(int32)=4
        }
        """
    ) == 0


def test_new_pending_arms_appearing_during_finalize_are_drained():
    # inspect<int32> -- a generic function containing its own generic arm --
    # is instantiated only while compiling outer's deferred copy, so its
    # pending arm (and the int16 boxed alongside) appear mid-finalize and
    # must be picked up by a later worklist pass.
    assert run(
        """
        fn inspect<W>(b: any, w: W) -> int32 {
            case type (b) {
                when U v: return sizeof(U) as int32 + sizeof(W) as int32;
                else:     return 0;
            }
            return -1;
        }
        fn outer(a: any) -> int32 {
            case type (a) {
                when T v: {
                    let b: any = 9 as int16;
                    return inspect(b, v) + 100;
                }
                else: return 0;
            }
            return -1;
        }
        fn main() -> int32 {
            // U=int16 (2) + W=int32 (4) + 100
            return outer(5) == 106 ? 0 : 1;
        }
        """
    ) == 0


def test_generic_arm_inside_a_generic_function():
    # The arm-scoped U composes with the enclosing instantiation's T.
    assert run(
        """
        fn peek<T>(a: any, w: T) -> int32 {
            case type (a) {
                when U v: return sizeof(U) as int32 + sizeof(T) as int32;
                else:     return 0;
            }
            return -1;
        }
        fn main() -> int32 {
            let a: any = 5;  // int32
            return peek(a, 1 as int64) == 12 ? 0 : 1;  // U=int32, T=int64
        }
        """
    ) == 0


def test_empty_boxed_set_compiles_the_arm_to_nothing():
    # Nothing ever boxes: the T v arm has zero instantiations (no
    # casetype.generic blocks) and a zero-filled any lands in else.
    source = """
        struct holder { pad: int32; a: any; }
        fn main() -> int32 {
            let h = struct holder { pad = 1 };  // the any field zero-fills
            case type (h.a) {
                when T v: return 1;
                else:     return 0;
            }
        }
    """
    assert "casetype.generic" not in compile_ir(source)
    assert run(source) == 0


def test_zero_filled_any_falls_to_else_past_generic_arms():
    assert run(
        """
        struct holder { pad: int32; a: any; }
        fn f(a: any) -> int32 {
            case type (a) {
                when T v: return 1;
                else:     return 0;
            }
            return -1;
        }
        fn main() -> int32 {
            let h = struct holder { pad = 1 };
            if (f(5) != 1)   { return 1; }  // the arm does instantiate
            if (f(h.a) != 0) { return 2; }  // tag 0 still falls to else
            return 0;
        }
        """
    ) == 0


# --------------------------------------------------------- semantics details

def test_defer_runs_on_a_return_from_a_deferred_arm_body():
    # The context snapshot deep-copies defer scopes, so a return out of a
    # monomorphized arm copy unwinds the defers registered before the case.
    assert run(
        """
        fn f(a: any, out: int32*) -> int32 {
            defer *out = 42;
            case type (a) {
                when T v: return 1;
                else:     return 0;
            }
            return -1;
        }
        fn main() -> int32 {
            let r: int32 = 0;
            if (f(5, &r) != 1) { return 1; }
            return r == 42 ? 0 : 2;
        }
        """
    ) == 0


def test_generic_arm_kills_narrowed_name_facts_it_could_invalidate():
    # The deferred body compiles out of band, so a name fact it can kill
    # (the assignment to p) is dropped at the chain position: the later
    # @nonnull argument no longer proves.
    with pytest.raises(LangError, match="cannot pass a possibly-null pointer"):
        compile_ir(
            """
            fn takes(@nonnull q: int32*) { }
            fn f(p: int32*, a: any) {
                if (p != null) {
                    case type (a) {
                        when T v: { p = null as int32*; }
                        else:     { }
                    }
                    takes(p);
                }
            }
            fn main() -> int32 { let a: any = 5; return 0; }
            """
        )


def test_generic_arm_blanket_kills_narrowed_path_facts():
    # Path facts die at the chain position even when the body is empty
    # (the call-site blanket-kill precedent).
    with pytest.raises(LangError, match="cannot pass a possibly-null pointer"):
        compile_ir(
            """
            struct holder { ptr: int32*; }
            fn takes(@nonnull q: int32*) { }
            fn f(h: holder*, a: any) {
                if (h->ptr != null) {
                    case type (a) {
                        when T v: { }
                        else:     { }
                    }
                    takes(h->ptr);
                }
            }
            fn main() -> int32 { let a: any = 5; return 0; }
            """
        )


def test_a_narrowed_fact_survives_a_body_that_cannot_kill_it():
    # The kill is the loop pre-scan walker, not a blanket: a body that
    # never writes p leaves the name fact standing.
    assert (
        compile_ir(
            """
            fn takes(@nonnull q: int32*) { }
            fn f(p: int32*, a: any) {
                if (p != null) {
                    case type (a) {
                        when T v: { }
                        else:     { }
                    }
                    takes(p);
                }
            }
            fn main() -> int32 { let a: any = 5; return 0; }
            """
        )
        != ""
    )


def test_all_arms_returning_still_needs_a_trailing_return():
    # The accepted conservatism: deferred arms are assumed to reach the end
    # block, so an all-arms-return case doesn't prove the function returns.
    with pytest.raises(LangError, match="may end without a return"):
        compile_ir(
            """
            fn f(a: any) -> int32 {
                case type (a) {
                    when T v: return 1;
                    else:     return 0;
                }
            }
            fn main() -> int32 { let a: any = 5; return f(a); }
            """
        )
