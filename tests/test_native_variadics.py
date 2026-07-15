"""Native variadic arguments: trailing collection and the `args...` sugar.

A trailing `slice<const any>` parameter marks a *collecting* function: the
call site boxes each extra argument into a caller-stack `any` (entry allocas,
function lifetime) and passes a read-only slice over the run --
allocation-free. `fn f(args...)` is pure sugar for
`fn f(const args: slice<const any>)`. The pass-through rule keeps the change
purely additive: at exact arity a final argument that already is a
`slice<const any>` (or `slice<any>`, which widens) hands over uncollected.
Collection runs on the direct-call path and through overload resolution:
a collecting function may be overloaded or share a generic name, ranked
below any candidate that matches without collecting (more fixed parameters
break ties between collectors). Function-pointer calls stay explicit-slice.
"""

import pytest

from mcc.codegen import CodeGen
from mcc.driver import emit_interface
from mcc.errors import LangError
from mcc.interface import render_interface
from mcc.lexer import tokenize
from mcc.parser import Parser
from helpers import compile_ir, parse, run, run_path


# ---------------------------------------------------------------- parsing

def test_sugar_desugars_to_a_const_slice_of_const_any():
    # `args...` and `const args: slice<const any>` parse to the same Func.
    (sugar,) = parse("fn f(args...) {}").functions
    (explicit,) = parse("fn f(const args: slice<const any>) {}").functions
    assert sugar.params == explicit.params
    assert sugar.const_params == explicit.const_params == {"args"}
    assert not sugar.variadic  # native collection is not a C-variadic `...`


def test_sugar_must_be_the_last_parameter():
    with pytest.raises(LangError, match="'args...' must be the last parameter"):
        parse("fn f(args..., x: int32) {}")


def test_sugar_rejects_parameter_modifiers():
    with pytest.raises(
        LangError,
        match=r"'args\.\.\.' cannot take const, own, a reference, @noalias, @nonnull, "
        r"or @format \(it is already a const slice<const any>\)",
    ):
        parse("fn f(const args...) {}")


def test_bare_ellipsis_c_variadics_are_unaffected():
    (func,) = parse("fn f(fmt: uint8*, ...) {}").functions
    assert func.variadic and len(func.params) == 1
    # And an @extern C variadic still compiles and calls with extras.
    ir_text = compile_ir(
        '@extern fn printf(fmt: char*, ...) -> int32;\n'
        'fn main() -> int32 { printf("%d %d", 1, 2); return 0; }'
    )
    assert "(i8*, ...)" in ir_text


# ------------------------------------------------------------- collection

def test_mixed_extras_dispatch_with_case_type():
    # One extra per boxable flavor, walked with `for` + `case type`.
    assert run(
        """
        fn score(tag: slice<const char>, args...) -> int32 {
            let n: int32 = tag.length as int32;
            for a in args {
                case type (a) {
                    when int32 v:   n = n + v;
                    when char c:    n = n + (c == 'c' ? 10 : -10);
                    when bool b:    n = n + (b ? 100 : -100);
                    when float64 f: n = n + (f == 3.5 ? 1000 : -1000);
                    else:           n = n - 10000;
                }
            }
            return n;
        }
        fn main() -> int32 {
            return score("x", 1, 'c', true, 3.5 as float64) == 1112 ? 0 : 1;
        }
        """
    ) == 0


def test_zero_extras_synthesize_an_empty_slice():
    assert run(
        """
        fn f(args...) -> int32 {
            let hits: int32 = 0;
            for a in args { hits = hits + 1; }
            return args.length == 0 and hits == 0 ? 0 : 1;
        }
        fn main() -> int32 { return f(); }
        """
    ) == 0


def test_explicit_slice_const_any_passes_through_uncollected():
    # The callee must see the original elements -- never a double-boxed
    # one-element slice holding the slice itself.
    assert run(
        """
        fn total(args...) -> int32 {
            let n: int32 = 0;
            for a in args {
                case type (a) { when int32 v: n = n + v; else: return -1; }
            }
            return n;
        }
        fn main() -> int32 {
            let xs: any[2];
            xs[0] = 5;
            xs[1] = 6;
            let s = xs as slice<const any>;
            return total(s) == 11 ? 0 : 1;
        }
        """
    ) == 0


def test_mutable_slice_any_widens_and_passes_through():
    assert run(
        """
        fn total(args...) -> int32 {
            let n: int32 = 0;
            for a in args {
                case type (a) { when int32 v: n = n + v; else: return -1; }
            }
            return n;
        }
        fn main() -> int32 {
            let xs: any[2];
            xs[0] = 3;
            xs[1] = 4;
            let s = xs as slice<any>;    // mutable view; widens to const
            return total(s) == 7 ? 0 : 1;
        }
        """
    ) == 0


def test_a_single_any_collects_to_a_one_element_slice():
    # An any extra copies in as-is (never nests) -- the original type
    # is recovered, and the length is 1.
    assert run(
        """
        fn probe(args...) -> int32 {
            if (args.length != 1) { return -1; }
            case type (args[0]) { when int32 v: return v; else: return -2; }
        }
        fn main() -> int32 {
            let a: any = 42;
            return probe(a) == 42 ? 0 : 1;
        }
        """
    ) == 0


def test_a_slice_of_int32_boxes_as_one_element():
    # Only the exact slice-of-any shape passes through; a slice<int32> is
    # one boxed element like any other value.
    assert run(
        """
        fn probe(args...) -> int32 {
            if (args.length != 1) { return -1; }
            case type (args[0]) {
                when slice<int32> s: return s.length as int32;
                else:                return -2;
            }
        }
        fn main() -> int32 {
            let xs: int32[3] = [1, 2, 3];
            return probe(xs as slice<int32>) == 3 ? 0 : 1;
        }
        """
    ) == 0


def test_the_explicit_form_collects_exactly_like_the_sugar():
    assert run(
        """
        fn a(args...) -> int32 { return args.length as int32; }
        fn b(args: slice<const any>) -> int32 { return args.length as int32; }
        fn main() -> int32 {
            if (a(1, 2, 3) != 3) { return 1; }
            if (b(1, 2, 3) != 3) { return 2; }
            return 0;
        }
        """
    ) == 0


def test_calls_inside_a_loop_are_safe():
    # Boxes are entry allocas reused across iterations; each iteration's
    # stores must land before its call, so the values are always current.
    assert run(
        """
        fn add(args...) -> int32 {
            let n: int32 = 0;
            for a in args {
                case type (a) { when int32 v: n = n + v; else: return -1000; }
            }
            return n;
        }
        fn main() -> int32 {
            let total: int32 = 0;
            let i: int32 = 0;
            while (i < 4) {
                total = total + add(i, i * 10);
                i = i + 1;
            }
            return total == 66 ? 0 : 1;   // (0+0)+(1+10)+(2+20)+(3+30)
        }
        """
    ) == 0


def test_a_deferred_call_collects_safely():
    # Function-lifetime boxes: a defer body running at scope exit still
    # reads valid storage.
    assert run(
        """
        @static let acc: int32;
        fn add(args...) {
            for a in args {
                case type (a) { when int32 v: acc = acc + v; else: acc = -1000; }
            }
        }
        fn main() -> int32 {
            {
                let x: int32 = 5;
                defer add(x, 6);
            }
            return acc == 11 ? 0 : 1;
        }
        """
    ) == 0


# ------------------------------------------------------------ rejections

def test_struct_extra_boxes_by_reference():
    # A struct extra is a by-reference position (the trailing slice<const
    # any> is call-scoped), so it boxes -- the payload holds a pointer to the
    # caller's storage, recovered with no copy by `case type`.
    assert run(
        """
        struct p { x: int32; }
        fn first(args...) -> int32 {
            case type (args[0]) { when p v: return v.x; else: return -1; }
        }
        fn main() -> int32 {
            let s = struct p { x = 42 };
            return first(s) == 42 ? 0 : 1;
        }
        """
    ) == 0


def test_union_extra_still_hits_the_escape_hatch_error():
    # A union does not box (its tag would not name the live member), so it
    # keeps the pointer escape-hatch error even in a variadic position.
    with pytest.raises(
        LangError,
        match=r"cannot box a u in an any; box a pointer to it \(&value\) instead",
    ):
        compile_ir(
            "union u { i: int32; f: float64; }\n"
            "fn f(args...) {}\n"
            "fn main() -> int32 { let s = union u { i = 1 }; f(s); return 0; }"
        )


def test_array_extra_is_rejected_by_its_array_type():
    with pytest.raises(
        LangError,
        match=r"cannot box a int32\[3\] in an any; box a pointer to its "
        r"first element \(&value\[0\]\) instead",
    ):
        compile_ir(
            "fn f(args...) {}\n"
            "fn main() -> int32 { let xs: int32[3] = [1, 2, 3]; f(xs); return 0; }"
        )


def test_too_few_fixed_arguments():
    with pytest.raises(
        LangError, match=r"'f' expects at least 1 argument\(s\), got 0"
    ):
        compile_ir(
            "fn f(x: int32, args...) {}\nfn main() -> int32 { f(); return 0; }"
        )


def test_c_variadics_stay_banned_from_overload_sets():
    # Only the collecting ban lifted; the C-ABI `...` lift belongs to the
    # separate C-variadics roadmap item.
    with pytest.raises(
        LangError, match="variadic function 'f' cannot be overloaded"
    ):
        compile_ir("fn f(fmt: char*, ...) {}\nfn f(x: int32) {}")


# ------------------------------------------- overload and generic parity

def test_a_collecting_function_can_be_overloaded():
    # The stage-1 ban is lifted: an exact-arity concrete match beats the
    # collector (no-collect is the outermost rank component), everything
    # else collects.
    assert run(
        """
        fn f(x: int32) -> int32 { return 100 + x; }
        fn f(args...) -> int32 { return args.length as int32; }
        fn main() -> int32 {
            if (f(5) != 105) { return 1; }      // exact arity: never collects
            if (f() != 0) { return 2; }         // zero extras
            if (f(1, 2) != 2) { return 3; }     // extras collect
            if (f('c') != 1) { return 4; }      // wrong type for f(int32)
            return 0;
        }
        """
    ) == 0


def test_an_exact_arity_generic_beats_a_concrete_collector():
    # No-collect outranks the concrete-beats-generic tier: the unbounded
    # template wins at exact arity even against a concrete collecting
    # fallback (the C++ ellipsis-ranks-worst analogue).
    assert run(
        """
        fn g<T>(a: T, b: T) -> int32 { return 1; }
        fn g(args...) -> int32 { return 2; }
        fn main() -> int32 {
            if (g(1, 2) != 1) { return 1; }        // exact-arity generic
            if (g(7) != 2) { return 2; }           // one arg: collector
            if (g(1, 2, 3) != 2) { return 3; }     // three: collector
            return 0;
        }
        """
    ) == 0


def test_between_collectors_more_fixed_parameters_wins():
    assert run(
        """
        fn log(args...) -> int32 { return args.length as int32; }
        fn log(level: int32, args...) -> int32 {
            return level * 100 + args.length as int32;
        }
        fn main() -> int32 {
            if (log() != 0) { return 1; }           // only zero-fixed viable
            if (log(3) != 300) { return 2; }        // both viable: more fixed
            if (log(3, 4) != 301) { return 3; }
            if (log('a', 'b') != 2) { return 4; }   // char misses int32 level
            return 0;
        }
        """
    ) == 0


def test_equal_fixed_counts_with_tying_specificity_are_ambiguous():
    with pytest.raises(
        LangError, match="call to 'f' is ambiguous between overloads"
    ):
        compile_ir(
            "fn f<T>(x: T, y: int32, args...) -> int32 { return 1; }\n"
            "fn f<T>(x: int32, y: T, args...) -> int32 { return 2; }\n"
            "fn main() -> int32 {\n"
            "    return f(1 as int32, 2 as int32, 'c');\n"
            "}"
        )


def test_pass_through_survives_in_an_overload_set():
    # At exact arity a slice<const any> final argument still hands over
    # uncollected -- the callee sees the original elements, never a
    # re-boxed one-element slice.
    assert run(
        """
        fn t(x: int32) -> int32 { return -1; }
        fn t(args...) -> int32 {
            let n: int32 = 0;
            for a in args {
                case type (a) { when int32 v: n = n + v; else: return -2; }
            }
            return n;
        }
        fn main() -> int32 {
            let xs: any[2];
            xs[0] = 5;
            xs[1] = 6;
            return t(xs as slice<const any>) == 11 ? 0 : 1;
        }
        """
    ) == 0


def test_a_generic_function_can_collect():
    # T binds from the fixed arguments only; the extras recover it through
    # the generic case-type arm.
    assert run(
        """
        fn acc<T>(seed: T, args...) -> T {
            let n: T = seed;
            for a in args {
                case type (a) { when T v: n = n + v; else: return seed; }
            }
            return n;
        }
        fn main() -> int32 {
            if (acc(10, 1, 2) != 13) { return 1; }
            if (acc(7) != 7) { return 2; }             // zero extras
            let w: int64 = 1000000000000;
            if (acc(w, w) != 2000000000000) { return 3; }
            return 0;
        }
        """
    ) == 0


def test_a_static_generic_function_can_collect():
    assert run(
        """
        @static fn count<T>(x: T, args: slice<const any>) -> int32 {
            return args.length as int32;
        }
        fn main() -> int32 { return count(1, 'a', 'b') == 2 ? 0 : 1; }
        """
    ) == 0


def test_an_uninferrable_type_parameter_keeps_its_diagnostic():
    # The extras never bind T (collection is type-erased), so a T that only
    # the extras could name stays the existing cannot-infer error, and
    # explicit type arguments fix it.
    with pytest.raises(
        LangError,
        match=r"cannot infer type parameter\(s\) T for 'u'; "
        r"specify them explicitly, e.g. u<int32>\(\.\.\.\)",
    ):
        compile_ir(
            "fn u<T>(args...) -> int32 { return sizeof(T) as int32; }\n"
            "fn main() -> int32 { u(1); return 0; }"
        )
    assert run(
        """
        fn u<T>(args...) -> int32 { return sizeof(T) as int32; }
        fn main() -> int32 { return u<int64>(1, 2) == 8 ? 0 : 1; }
        """
    ) == 0


def test_a_mut_fixed_parameter_collects_through_the_set_path():
    # The accumulator-collector shape: a mut fixed parameter needs its address formed
    # before the winner is known, with the collecting-aware arity match.
    assert run(
        """
        fn bump(name: char*) -> int32 { return -1; }
        fn bump(acc: &int32, args...) -> int32 {
            for a in args {
                case type (a) { when int32 v: acc = acc + v; else: acc = -1000; }
            }
            return 0;
        }
        fn main() -> int32 {
            let total: int32 = 5;
            bump(total, 1, 2);
            return total == 8 ? 0 : 1;
        }
        """
    ) == 0


def test_deferred_literal_extras_reproduce_the_direct_path_errors():
    # An array or bare-struct literal extra pre-evaluates to a placeholder
    # on the resolution path; emission re-generates it, so the receiver-less
    # literal errors match the direct path's exactly.
    array_msg = (
        r"an array literal is only allowed where an array or slice type "
        r"receives it: an array or slice<T> variable initializer, an "
        r"array/slice element, or an `as slice<T>` borrow"
    )
    struct_msg = (
        r"a bare struct literal `\{ \.\.\. \}` has no type here; write the "
        r"type \(point \{ \.\.\. \}\) or use it where one is expected -- a "
        r"typed let, assignment, return, argument, element, or field"
    )
    # Direct path (single collecting function)...
    with pytest.raises(LangError, match=array_msg):
        compile_ir(
            "fn f(args...) {}\n"
            "fn main() -> int32 { f(1, [1, 2]); return 0; }"
        )
    # ...and the overload-set path raise the same strings.
    with pytest.raises(LangError, match=array_msg):
        compile_ir(
            "fn f(x: char) {}\nfn f(args...) {}\n"
            "fn main() -> int32 { f(1, [1, 2]); return 0; }"
        )
    with pytest.raises(LangError, match=struct_msg):
        compile_ir(
            "fn f(x: char) {}\nfn f(args...) {}\n"
            "fn main() -> int32 { f(1, { x = 1 }); return 0; }"
        )


def test_sugar_and_explicit_spelling_collide_as_duplicates():
    # One signature, two spellings: the params-key mangling sees the
    # desugared type, so the pair is a duplicate, not a second member.
    with pytest.raises(
        LangError,
        match=r"function 'f\(int32, slice<const any>\)' already defined; "
        r"overloads must differ in parameter types",
    ):
        compile_ir(
            "fn f(x: int32, args...) {}\n"
            "fn f(x: int32, const args: slice<const any>) {}"
        )


def test_main_cannot_collect():
    with pytest.raises(
        LangError, match="function 'main' cannot be a collecting function"
    ):
        compile_ir("fn main(args...) -> int32 { return 0; }")
    # The explicit spelling is the same marker.
    with pytest.raises(
        LangError, match="function 'main' cannot be a collecting function"
    ):
        compile_ir("fn main(args: slice<const any>) -> int32 { return 0; }")


def test_an_extern_function_cannot_collect():
    with pytest.raises(
        LangError,
        match=r"an @extern function cannot be a collecting function "
        r"\(C sees no slice<const any>; declare C varargs with '\.\.\.'\)",
    ):
        compile_ir("@extern fn f(args: slice<const any>);")


def test_collecting_plus_c_varargs_is_rejected():
    with pytest.raises(
        LangError,
        match=r"a collecting function cannot also take C varargs; "
        r"drop the '\.\.\.'",
    ):
        compile_ir("fn f(args: slice<const any>, ...) {}")


# --------------------------------------------- the marker is type-and-place

def test_a_non_trailing_slice_of_any_is_not_a_marker():
    # Only the *last* parameter's type marks collection; elsewhere the
    # arity stays exact.
    with pytest.raises(LangError, match=r"'f' expects 2 argument\(s\), got 3"):
        compile_ir(
            "fn f(args: slice<const any>, n: int32) {}\n"
            "fn main() -> int32 { f(1, 2, 3); return 0; }"
        )


def test_a_mut_trailing_slice_does_not_collect():
    # mut lends the caller's own storage, which collection can never
    # synthesize, so such a function stays explicit-slice.
    with pytest.raises(LangError, match=r"'f' expects 1 argument\(s\), got 2"):
        compile_ir(
            "fn f(args: &slice<const any>) {}\n"
            "fn main() -> int32 { f(1, 2); return 0; }"
        )


def test_function_pointer_calls_stay_explicit_slice():
    # A fn(...) type carries no marker: the call passes the slice explicitly,
    # and extras are a plain arity error.
    assert run(
        """
        fn total(args: slice<const any>) -> int32 {
            return args.length as int32;
        }
        fn main() -> int32 {
            let g: fn(slice<const any>) -> int32 = total;
            let xs: any[2];
            xs[0] = 1;
            xs[1] = 2;
            return g(xs as slice<const any>) == 2 ? 0 : 1;
        }
        """
    ) == 0
    with pytest.raises(LangError, match=r"'g' expects 1 argument\(s\), got 2"):
        compile_ir(
            "fn total(args: slice<const any>) -> int32 {"
            " return args.length as int32; }\n"
            "fn main() -> int32 {\n"
            "    let g: fn(slice<const any>) -> int32 = total;\n"
            "    return g(1, 2);\n"
            "}"
        )


# -------------------------------------------------------------- interface

def iface(source: str) -> str:
    """Render the interface stub for an import-free source string."""
    program = Parser(tokenize(source)).parse_program()
    imports = list(program.imports)
    cg = CodeGen(program, "test")
    cg.generate()
    return render_interface(cg, source, imports)


def test_the_stub_emits_the_desugared_parameter():
    out = iface("fn total(base: int32, args...) -> int32 { return base; }")
    assert "fn total(base: int32, const args: slice<const any>) -> int32;" in out


def test_collecting_round_trips_through_mci(tmp_path):
    # An @inline body travels in full, so the consumer compiles, collects,
    # and runs entirely from the stub -- the type is the marker on re-import.
    lib = tmp_path / "lib.mc"
    lib.write_text(
        "@inline fn total(base: int32, args...) -> int32 {\n"
        "    let n: int32 = base;\n"
        "    for a in args {\n"
        "        case type (a) { when int32 v: n = n + v; else: n = -1000; }\n"
        "    }\n"
        "    return n;\n"
        "}\n"
    )
    out = tmp_path / "lib.mci"
    assert emit_interface(lib, (tmp_path,), None, {}, out) == 0
    lib.unlink()  # force the import to resolve through the stub
    main = tmp_path / "main.mc"
    main.write_text(
        'import "lib";\n'
        "fn main() -> int32 {\n"
        "    if (total(1, 2, 3) != 6) { return 1; }\n"
        "    if (total(7) != 7) { return 2; }\n"
        "    return 0;\n"
        "}\n"
    )
    assert run_path(main) == 0


def test_a_collecting_set_member_round_trips_through_mci(tmp_path):
    # .mci needs zero changes for collecting overloads: the desugared type
    # is the marker and the params-key mangling distinguishes the members,
    # so the consumer resolves the set entirely from the stub.
    lib = tmp_path / "vlog.mc"
    lib.write_text(
        "@inline fn vlog(args...) -> int32 {\n"
        "    return args.length as int32;\n"
        "}\n"
        "@inline fn vlog(level: int32, args...) -> int32 {\n"
        "    return level * 100 + args.length as int32;\n"
        "}\n"
    )
    out = tmp_path / "vlog.mci"
    assert emit_interface(lib, (tmp_path,), None, {}, out) == 0
    # An @inline member's body travels verbatim, sugar included; it
    # re-desugars to the same marker on re-import.
    assert "args..." in out.read_text()
    lib.unlink()  # force the import to resolve through the stub
    main = tmp_path / "main.mc"
    main.write_text(
        'import "vlog";\n'
        "fn main() -> int32 {\n"
        "    if (vlog('a', 'b') != 2) { return 1; }\n"
        "    if (vlog(3, 4) != 301) { return 2; }\n"
        "    return 0;\n"
        "}\n"
    )
    assert run_path(main) == 0
