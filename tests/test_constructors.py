"""Constructor call sugar: ``S(args)`` desugars to ``S::constructor``.

For any type with a declared ``constructor`` family, ``let s = S(args);`` is
``let s: S; S::constructor(s, args);`` -- the slot is allocated (and
default-initialized exactly as a bare ``let s: S;``), passed as the family
call's first argument, and the expression evaluates to the constructed value.
``let`` binds the constructed slot directly (no temporary, no copy).

The head follows type-use spelling: explicit type arguments
(``point<float64>(1, 1)``), a non-generic or fully-defaulted type bare, a
type alias of a complete type (``pointf(1, 2)``) -- and, uniquely to calls, a
*bare generic* head (``point(1.0, 2.0)``) infers the instantiation from the
constructor's arguments through the family's ordinary overload resolution.
Name resolution is unchanged: a same-named function, variable, constant, or
``@static`` wins unconditionally, so the sugar sits at the very last resort
(where the call would otherwise be ``undefined function``). A type without a
declared constructor is a bespoke error -- ``S { ... }`` literals remain the
no-constructor spelling, and no builtin gains an implicit one (``int32(5)``
does not become a cast).
"""

import pytest

from mcc.errors import LangError
from helpers import compile_ir, run, run_path


# --- the driving use case ------------------------------------------------------


def test_explicit_type_args_pick_the_converting_ctor(capfd):
    # `point<float64>(1, 1)`: the receiver slot is typed up front, so T =
    # float64 binds through it; the diagonal ctor (x: T, y: T) is then
    # non-viable for int literals (no int-to-float literal adaptation), and
    # the converting ctor <U> wins -- the acceptance nuance.
    assert run(
        """
        import "std/io";
        struct point<T> { x: T; y: T; }
        fn point<T>::constructor(self: &struct point<T>, x: T, y: T) {
            self.x = x; self.y = y;
        }
        fn point<T>::constructor<U>(self: &struct point<T>, x: U, y: U) {
            self.x = x as T; self.y = y as T;
        }
        fn main() -> int32 {
            let p = point<float64>(1, 1);
            println(f"{p.x = } {p.y = }");
            return 0;
        }
        """
    ) == 0
    assert capfd.readouterr().out == "p.x = 1.000000 p.y = 1.000000\n"


def test_non_generic_struct_constructs(capfd):
    assert run(
        """
        import "std/io";
        struct counter { n: int32; }
        fn counter::constructor(self: &counter, n: int32) { self.n = n; }
        fn main() -> int32 {
            let c = counter(41);
            c.n += 1;
            println(f"{c.n}");
            return 0;
        }
        """
    ) == 0
    assert capfd.readouterr().out == "42\n"


# --- bare generic heads: inference from the constructor arguments --------------


def test_bare_generic_infers_from_typed_args(capfd):
    # `point(1.5, 2.5)`: no spelled instantiation, so the receiver enters
    # resolution as a placeholder and the float64 arguments bind T.
    assert run(
        """
        import "std/io";
        struct point<T> { x: T; y: T; }
        fn point<T>::constructor(self: &struct point<T>, x: T, y: T) {
            self.x = x; self.y = y;
        }
        fn main() -> int32 {
            let p = point(1.5, 2.5);
            println(f"{p.x = }");
            return 0;
        }
        """
    ) == 0
    assert capfd.readouterr().out == "p.x = 1.500000\n"


def test_bare_generic_int_literals_lean_int32():
    # Adaptable int arguments bind T = int32, exactly as the desugared
    # inference call would.
    ir = compile_ir(
        """
        struct point<T> { x: T; y: T; }
        fn point<T>::constructor(self: &struct point<T>, x: T, y: T) {
            self.x = x; self.y = y;
        }
        fn main() -> int32 {
            let p = point(1, 2);
            return p.x;
        }
        """
    )
    assert 'alloca %"point<int32>"' in ir


def test_bare_generic_uninferable_names_the_sugar_spelling():
    # The converting-only family binds U but never T from the arguments; the
    # fix is spelled at the sugar head, not at the (unwritable) family call.
    with pytest.raises(
        LangError,
        match=r"cannot infer type parameter\(s\) T for 'box::constructor'; "
        r"spell the instantiation, e\.g\. box<int32>\(\.\.\.\)",
    ):
        compile_ir(
            """
            struct box<T> { v: T; }
            fn box<T>::constructor<U>(self: &struct box<T>, v: U) {
                self.v = v as T;
            }
            fn main() -> int32 { let b = box(1); return 0; }
            """
        )


def test_bare_generic_ambiguity_is_the_family_error():
    with pytest.raises(
        LangError, match=r"call to 'pair::constructor' is ambiguous"
    ):
        compile_ir(
            """
            struct pair<K, V> { k: K; v: V; }
            fn pair<K, V>::constructor(self: &struct pair<K, V>, k: K, v: V) {
                self.k = k; self.v = v;
            }
            fn pair<K, V>::constructor(self: &struct pair<K, V>, v: V, k: K) {
                self.k = k; self.v = v;
            }
            fn main() -> int32 { let p = pair(1, 2.0); return 0; }
            """
        )


def test_bare_generic_no_overload_renders_the_placeholder():
    # A genuinely non-viable argument list reports the desugared signature;
    # the untyped receiver renders as <self>.
    with pytest.raises(
        LangError,
        match=r"no overload of 'point::constructor' with signature "
        r"point::constructor\(<self>, char\*, float64\)",
    ):
        compile_ir(
            """
            struct point<T> { x: T; y: T; }
            fn point<T>::constructor(self: &struct point<T>, x: T, y: T) {
                self.x = x; self.y = y;
            }
            fn point<T>::constructor<U>(self: &struct point<T>, x: U, y: U) {
                self.x = x as T; self.y = y as T;
            }
            fn main() -> int32 { let p = point("a", 1.5); return 0; }
            """
        )


def test_bare_generic_lone_concrete_specialization_fixes_the_slot(capfd):
    # A family that is one plain concrete function (a lone specialization)
    # never enters set resolution; its declared receiver types the slot.
    assert run(
        """
        import "std/io";
        struct point<T> { x: T; y: T; }
        fn point<float64>::constructor(self: &struct point<float64>,
                                       x: float64, y: float64) {
            self.x = x; self.y = y;
        }
        fn main() -> int32 {
            let p = point(1.5, 2.5);
            println(f"{p.y = }");
            return 0;
        }
        """
    ) == 0
    assert capfd.readouterr().out == "p.y = 2.500000\n"


def test_resolved_ctor_must_take_the_constructed_type_first():
    # `Type::` enforces no self convention, so a family whose winner does not
    # take the constructed type first can exist -- but the bare sugar cannot
    # type its slot from it.
    with pytest.raises(
        LangError,
        match=r"cannot construct 'point': the resolved 'fn point::constructor' "
        r"does not take the constructed point as its first parameter",
    ):
        compile_ir(
            """
            struct point<T> { x: T; }
            fn point<T>::constructor(a: T, b: T, c: T) { }
            fn main() -> int32 { let p = point(1, 2); return 0; }
            """
        )


def test_fully_defaulted_generic_constructs_at_its_defaults():
    # A fully-defaulted generic written bare is a complete type (as in
    # `let b: box;`), so the defaults fill -- the argument adapts to int64
    # rather than leaning int32.
    ir = compile_ir(
        """
        struct box<T = int64> { v: T; }
        fn box<T>::constructor(self: &struct box<T>, v: T) { self.v = v; }
        fn main() -> int32 { let b = box(1); return 0; }
        """
    )
    assert 'alloca %"box<int64>"' in ir


# --- alias heads ---------------------------------------------------------------


def test_alias_of_an_instantiation_constructs_it(capfd):
    # `pointf(3, 4)` over `type pointf = point<float64>` constructs the
    # aliased instantiation: T = float64 binds through the typed receiver, so
    # the converting ctor takes the int literals.
    assert run(
        """
        import "std/io";
        struct point<T> { x: T; y: T; }
        fn point<T>::constructor(self: &struct point<T>, x: T, y: T) {
            self.x = x; self.y = y;
        }
        fn point<T>::constructor<U>(self: &struct point<T>, x: U, y: U) {
            self.x = x as T; self.y = y as T;
        }
        type pointf = point<float64>;
        fn main() -> int32 {
            let p = pointf(3, 4);
            println(f"{p.y = }");
            return 0;
        }
        """
    ) == 0
    assert capfd.readouterr().out == "p.y = 4.000000\n"


def test_plain_alias_chain_to_bare_generic_infers():
    # `type pts = point;` pins nothing, so the chain stays a bare head and
    # the arguments infer the instantiation.
    ir = compile_ir(
        """
        struct point<T> { x: T; y: T; }
        fn point<T>::constructor(self: &struct point<T>, x: T, y: T) {
            self.x = x; self.y = y;
        }
        type pts = point;
        fn main() -> int32 {
            let p = pts(1.5, 2.5);
            return 0;
        }
        """
    )
    assert 'alloca %"point<float64>"' in ir


def test_generic_alias_bare_keeps_the_type_use_arity_error():
    # A generic alias used bare is incomplete as a type use, and the sugar
    # follows type-use spelling: annotate the head (`diag<int32>(...)`).
    with pytest.raises(
        LangError,
        match=r"type alias 'diag' expects 1 type argument\(s\), got 0",
    ):
        compile_ir(
            """
            struct point<T> { x: T; y: T; }
            fn point<T>::constructor(self: &struct point<T>, x: T, y: T) {
                self.x = x; self.y = y;
            }
            type diag<T> = point<T>;
            fn main() -> int32 { let d = diag(1, 2); return 0; }
            """
        )


def test_generic_alias_with_args_constructs_through_the_target():
    ir = compile_ir(
        """
        struct point<T> { x: T; y: T; }
        fn point<T>::constructor(self: &struct point<T>, x: T, y: T) {
            self.x = x; self.y = y;
        }
        type diag<T> = point<T>;
        fn main() -> int32 {
            let d = diag<float64>(1.5, 2.5);
            return 0;
        }
        """
    )
    assert 'alloca %"point<float64>"' in ir


# --- builtin heads (ruling: any type with a declared constructor family) -------


def test_builtin_with_declared_ctor_constructs(capfd):
    assert run(
        """
        import "std/io";
        fn char::constructor(self: &char, code: int32) {
            self = code as char;
        }
        fn main() -> int32 {
            let c = char(65);
            println(f"{c}");
            return 0;
        }
        """
    ) == 0
    assert capfd.readouterr().out == "A\n"


def test_alias_to_builtin_constructs(capfd):
    assert run(
        """
        import "std/io";
        fn char::constructor(self: &char, code: int32) {
            self = code as char;
        }
        type letter = char;
        fn main() -> int32 {
            let c = letter(66);
            println(f"{c}");
            return 0;
        }
        """
    ) == 0
    assert capfd.readouterr().out == "B\n"


def test_builtin_without_ctor_is_not_a_cast():
    with pytest.raises(
        LangError,
        match=r"type 'int32' has no constructor; "
        r"declare 'fn int32::constructor\(\.\.\.\)'",
    ):
        compile_ir("fn main() -> int32 { let x = int32(5); return 0; }")


# --- missing constructors --------------------------------------------------------


def test_struct_without_ctor_errors():
    with pytest.raises(
        LangError,
        match=r"struct 'point' has no constructor; declare "
        r"'fn point::constructor\(\.\.\.\)' or build the value with a "
        r"struct literal",
    ):
        compile_ir(
            """
            struct point { x: int32; }
            fn main() -> int32 { let p = point(1); return 0; }
            """
        )


def test_union_without_ctor_errors():
    with pytest.raises(LangError, match=r"union 'u' has no constructor"):
        compile_ir(
            """
            union u { n: int32; c: char; }
            fn main() -> int32 { let v = u(1); return 0; }
            """
        )


def test_alias_head_names_the_written_spelling():
    # The error names what the user wrote; the hint names the canonical
    # family to declare.
    with pytest.raises(
        LangError,
        match=r"struct 'pointf' has no constructor; declare "
        r"'fn point::constructor\(\.\.\.\)'",
    ):
        compile_ir(
            """
            struct point<T> { x: T; }
            type pointf = point<float64>;
            fn main() -> int32 { let p = pointf(1); return 0; }
            """
        )


def test_undefined_name_keeps_the_undefined_function_error():
    with pytest.raises(
        LangError, match=r"undefined function 'ghost' \(missing import\?\)"
    ):
        compile_ir("fn main() -> int32 { ghost(1); return 0; }")


# --- name resolution: the function wins unconditionally -------------------------


def test_same_named_function_wins_over_the_ctor():
    assert run(
        """
        struct point { x: int32; }
        fn point::constructor(self: &point, x: int32) { self.x = x; }
        fn point(v: int32) -> int32 { return v * 2; }
        fn main() -> int32 { return point(21) - 42; }
        """
    ) == 0


def test_variable_shadow_keeps_the_not_callable_error():
    with pytest.raises(
        LangError, match=r"'point' is not callable; it is a int32"
    ):
        compile_ir(
            """
            struct point { x: int32; }
            fn point::constructor(self: &point, x: int32) { self.x = x; }
            fn main() -> int32 {
                let point: int32 = 3;
                let p = point(1);
                return 0;
            }
            """
        )


# --- desugaring semantics --------------------------------------------------------


def test_let_elides_the_temporary():
    # `let p = point<int32>(1, 2)` constructs straight into p's slot: exactly
    # one alloca of the struct type in the whole module (the constructor's
    # own mut self is a pointer parameter, never a struct slot).
    ir = compile_ir(
        """
        struct point<T> { x: T; y: T; }
        fn point<T>::constructor(self: &struct point<T>, x: T, y: T) {
            self.x = x; self.y = y;
        }
        fn main() -> int32 {
            let p = point<int32>(1, 2);
            return p.x;
        }
        """
    )
    assert ir.count('alloca %"point<int32>"') == 1


def test_arguments_evaluate_once(capfd):
    assert run(
        """
        import "std/io";
        struct point { x: int32; }
        fn point::constructor(self: &point, x: int32) { self.x = x; }
        fn tick() -> int32 { println("tick"); return 7; }
        fn main() -> int32 {
            let p = point(tick());
            point(tick());
            return p.x - 7;
        }
        """
    ) == 0
    assert capfd.readouterr().out == "tick\ntick\n"


def test_expression_and_return_positions(capfd):
    assert run(
        """
        import "std/io";
        struct point<T> { x: T; y: T; }
        fn point<T>::constructor(self: &struct point<T>, x: T, y: T) {
            self.x = x; self.y = y;
        }
        fn norm1(p: struct point<float64>) -> float64 { return p.x + p.y; }
        fn mk(v: float64) -> struct point<float64> {
            return point<float64>(v, v);
        }
        fn main() -> int32 {
            println(f"{norm1(point<float64>(1.5, 2.5)) = }");
            println(f"{mk(2.0).x = }");
            return 0;
        }
        """
    ) == 0
    assert capfd.readouterr().out == (
        "norm1(point<float64>(1.5, 2.5)) = 4.000000\nmk(2.0).x = 2.000000\n"
    )


def test_nested_ctor_argument():
    assert run(
        """
        struct inner { n: int32; }
        fn inner::constructor(self: &inner, n: int32) { self.n = n; }
        struct outer { m: int32; }
        fn outer::constructor(self: &outer, i: inner) { self.m = i.n; }
        fn main() -> int32 {
            let o = outer(inner(5));
            return o.m - 5;
        }
        """
    ) == 0


def test_annotated_let_mismatch_is_the_plain_coerce_error():
    with pytest.raises(
        LangError, match=r"let w: expected point<int64>, got point<int32>"
    ):
        compile_ir(
            """
            struct point<T> { x: T; y: T; }
            fn point<T>::constructor(self: &struct point<T>, x: T, y: T) {
                self.x = x; self.y = y;
            }
            fn main() -> int32 {
                let w: point<int64> = point<int32>(1, 2);
                return 0;
            }
            """
        )


def test_annotated_let_keeps_a_const_view():
    with pytest.raises(
        LangError, match=r"cannot assign to read-only variable 'c'"
    ):
        compile_ir(
            """
            struct point { x: int32; }
            fn point::constructor(self: &point, x: int32) { self.x = x; }
            fn main() -> int32 {
                let c: const point = point(1);
                c = point(2);
                return 0;
            }
            """
        )


def test_default_fields_initialize_before_the_ctor_runs():
    # The slot default-initializes exactly as `let s: S;` does, so a
    # constructor that fills only some fields leaves the others at their
    # declared defaults.
    assert run(
        """
        struct config { verbose: int32 = 7; level: int32; }
        fn config::constructor(self: &config, level: int32) {
            self.level = level;
        }
        fn main() -> int32 {
            let c = config(2);
            return c.verbose + c.level - 9;
        }
        """
    ) == 0


def test_const_self_ctor_is_the_dumb_desugar():
    # `Type::` enforces no self convention: a const-self "constructor"
    # compiles, receives a spilled borrow, and initializes nothing -- the
    # object keeps its declared defaults.
    assert run(
        """
        struct config { verbose: int32 = 7; }
        fn config::constructor(const self: config, v: int32) { }
        fn main() -> int32 {
            let c = config(3);
            return c.verbose - 7;
        }
        """
    ) == 0


def test_non_void_ctor_return_is_discarded():
    assert run(
        """
        struct point { x: int32; }
        fn point::constructor(self: &point, x: int32) -> int32 {
            self.x = x;
            return 99;
        }
        fn main() -> int32 {
            let p = point(4);
            return p.x - 4;
        }
        """
    ) == 0


def test_explicit_family_call_stays_valid():
    # The desugared spelling remains a first-class call alongside the sugar.
    assert run(
        """
        struct point<T> { x: T; y: T; }
        fn point<T>::constructor(self: &struct point<T>, x: T, y: T) {
            self.x = x; self.y = y;
        }
        fn main() -> int32 {
            let p: struct point<int32>;
            point::constructor(p, 1, 2);
            let q = point<int32>(1, 2);
            return p.x - q.x;
        }
        """
    ) == 0


def test_sugar_inside_a_generic_function_context():
    # `point<T>(v, v)` inside `fn mk<T>` resolves T through the enclosing
    # bindings, per instantiation.
    assert run(
        """
        struct point<T> { x: T; y: T; }
        fn point<T>::constructor(self: &struct point<T>, x: T, y: T) {
            self.x = x; self.y = y;
        }
        fn mk<T>(v: T) -> struct point<T> {
            return point<T>(v, v);
        }
        fn main() -> int32 {
            let a = mk(1.5);
            let b = mk(2);
            return b.x - 2;
        }
        """
    ) == 0


def test_ctor_arity_error_counts_the_receiver():
    # Diagnostics are the desugared call's own: positions and arities count
    # the hidden receiver (argument 1).
    with pytest.raises(
        LangError,
        match=r"'point::constructor' expects 3 argument\(s\), got 2",
    ):
        compile_ir(
            """
            struct point<T> { x: T; y: T; }
            fn point<T>::constructor(self: &struct point<T>, x: T, y: T) {
                self.x = x; self.y = y;
            }
            fn main() -> int32 { let p = point(1); return 0; }
            """
        )


def test_const_initializer_position_rejects():
    with pytest.raises(
        LangError, match=r"a const initializer must be a compile-time constant"
    ):
        compile_ir(
            """
            struct point { x: int32; }
            fn point::constructor(self: &point, x: int32) { self.x = x; }
            const g = point(1);
            fn main() -> int32 { return 0; }
            """
        )


def test_private_ctor_is_access_checked(tmp_path):
    # Privacy is per overload, exactly as in a direct family call.
    (tmp_path / "pt.mc").write_text(
        "struct point { x: int32; }\n"
        "@private\n"
        "fn point::constructor(self: &point, x: int32) { self.x = x; }\n"
    )
    main = tmp_path / "main.mc"
    main.write_text(
        'import "pt";\nfn main() -> int32 { let p = point(1); return 0; }\n'
    )
    with pytest.raises(
        LangError,
        match=r"function 'point::constructor' is private to pt.mc",
    ):
        run_path(main)


# --- the implicit empty constructor ---------------------------------------------


def test_implicit_empty_ctor_is_the_plain_declaration():
    # `let c = char();` is `let c: char;` -- one uninitialized alloca, no
    # store, no constructor call (there is no family to call).
    ir_text = compile_ir("fn main() -> int32 { let c = char(); return 0; }")
    body = ir_text.split('define i32 @"main"()')[1]
    assert body.count("alloca i8") == 1
    assert "store" not in body and "call" not in body


def test_implicit_empty_ctor_despite_a_declared_family(capfd):
    # Declaring constructors does NOT suppress the implicit empty one (unlike
    # C++): the 2-arg family has no member that takes just the receiver, so
    # `point<float64>()` is `let p: point<float64>;` -- assignable and usable
    # exactly as the plain declaration.
    assert run(
        """
        import "std/io";
        struct point<T> { x: T; y: T; }
        fn point<T>::constructor(self: &point<T>, x: T, y: T) {
            self.x = x; self.y = y;
        }
        fn main() -> int32 {
            let p = point<float64>();
            p.x = 1.0; p.y = 2.0;
            println(f"{p.x = }, {p.y = }");
            return 0;
        }
        """
    ) == 0
    assert capfd.readouterr().out == "p.x = 1.000000, p.y = 2.000000\n"


def test_implicit_empty_ctor_never_calls_the_family():
    # The defaultless flavor: the slot stays uninitialized and no
    # `point::constructor` instance is ever emitted or called.
    ir_text = compile_ir(
        """
        struct point<T> { x: T; y: T; }
        fn point<T>::constructor(self: &point<T>, x: T, y: T) {
            self.x = x; self.y = y;
        }
        fn main() -> int32 {
            let p = point<float64>();
            p.x = 3.0;
            return p.x as int32;
        }
        """
    )
    assert "point::constructor" not in ir_text


def test_implicit_empty_ctor_on_a_derived_struct(capfd):
    # `pointf()` on the method-inheritance surface: the merged family (the
    # derived <U> plus the inherited diagonal) has no zero-arg member, so
    # the implicit form applies -- with the derived-added default observable.
    assert run(
        """
        import "std/io";
        struct point<T> { x: T; y: T; }
        struct pointf extends point<float64> { tag: int32 = 5; }
        fn point<T>::constructor(self: &point<T>, x: T, y: T) {
            self.x = x; self.y = y;
        }
        fn pointf::constructor<U>(self: &pointf, x: U, y: U) {
            self.x = x as float64; self.y = y as float64;
        }
        fn main() -> int32 {
            let p = pointf();
            println(f"{p.tag}");
            return 0;
        }
        """
    ) == 0
    assert capfd.readouterr().out == "5\n"


def test_declared_zero_arg_ctor_wins_over_the_implicit(capfd):
    # A genuine `(mut self)`-only member claims `T()` -- pinned by its side
    # effect overriding the field default.
    assert run(
        """
        import "std/io";
        struct cfg { mode: int32 = 7; }
        fn cfg::constructor(self: &cfg) { self.mode = 99; }
        fn main() -> int32 {
            let k = cfg();
            println(f"{k.mode}");
            return 0;
        }
        """
    ) == 0
    assert capfd.readouterr().out == "99\n"


def test_declared_collecting_ctor_claims_the_zero_arg_call(capfd):
    # A collecting member whose fixed prefix is just the receiver accepts
    # `T()` (with an empty collection), so it wins over the implicit form.
    assert run(
        """
        import "std/io";
        struct bag { n: int32 = 0; }
        fn bag::constructor(self: &bag, items...) {
            self.n = items.length as int32 + 40;
        }
        fn main() -> int32 {
            let b = bag();
            println(f"{b.n}");
            return 0;
        }
        """
    ) == 0
    assert capfd.readouterr().out == "40\n"


def test_implicit_empty_ctor_in_expression_position(capfd):
    # The default-initialized value flows as an ordinary argument -- the
    # declared field defaults are what the callee observes.
    assert run(
        """
        import "std/io";
        struct pt { x: int32 = 20; y: int32 = 22; }
        fn sum(p: pt) -> int32 { return p.x + p.y; }
        fn main() -> int32 {
            println(f"{sum(pt())}");
            return 0;
        }
        """
    ) == 0
    assert capfd.readouterr().out == "42\n"


def test_implicit_empty_ctor_on_any_builtin():
    # `int32()` allocates an int slot exactly as `let n: int32;` -- usable
    # once assigned, and no constructor family is required.
    assert run(
        """
        fn main() -> int32 {
            let n = int32();
            n = 42;
            return n;
        }
        """
    ) == 42


def test_implicit_empty_ctor_through_an_alias(capfd):
    # The head follows the ctor sugar's alias chase: `pf()` builds the
    # aliased instantiation.
    assert run(
        """
        import "std/io";
        struct point<T> { x: T; y: T; }
        type pf = point<float64>;
        fn main() -> int32 {
            let p = pf();
            p.x = 9.0;
            println(f"{p.x as int32}");
            return 0;
        }
        """
    ) == 0
    assert capfd.readouterr().out == "9\n"


def test_implicit_empty_ctor_on_a_union():
    # A union `u()` is `let v: u;` -- no family needed, storage uninitialized.
    assert run(
        """
        union u { n: int32; c: char; }
        fn main() -> int32 {
            let v = u();
            v.n = 0;
            return v.n;
        }
        """
    ) == 0


def test_implicit_empty_ctor_fully_defaulted_generic_bare():
    # A fully-defaulted generic written bare is a complete type, as
    # everywhere: `box()` is `let b: box;` at its defaults.
    assert run(
        """
        struct box<T = int64> { v: T; }
        fn main() -> int32 {
            let b = box();
            b.v = 42;
            return b.v as int32;
        }
        """
    ) == 42


def test_self_less_zero_param_ctor_never_claims_the_call():
    # `Type::` enforces no self convention, so a literal zero-PARAMETER
    # member is declarable -- but the desugared `T()` always passes the
    # receiver, which such a member cannot accept, so it never claims the
    # zero-argument call: the implicit empty constructor applies instead
    # (and the family stays reachable as an explicit qualified call).
    assert run(
        """
        struct s { n: int32 = 3; }
        fn s::constructor() -> int32 { return 1; }
        fn main() -> int32 {
            let v = s();
            return v.n + s::constructor() - 4;
        }
        """
    ) == 0


def test_implicit_empty_ctor_bare_generic_head_cannot_infer():
    # No arguments means nothing to infer from: a bare generic head keeps
    # the constructor sugar's cannot-infer error -- family or no family.
    for extra in (
        "",
        "fn point<T>::constructor(self: &point<T>, x: T, y: T) {"
        " self.x = x; self.y = y; }\n",
    ):
        with pytest.raises(
            LangError,
            match=r"cannot infer type parameter\(s\) T for "
            r"'point::constructor'; spell the instantiation, e\.g\. "
            r"point<int32>\(\.\.\.\)",
        ):
            compile_ir(
                "struct point<T> { x: T; y: T; }\n" + extra +
                "fn main() -> int32 { let p = point(); return 0; }"
            )


def test_implicit_empty_ctor_is_zero_arg_only():
    # Calls WITH arguments are untouched: no declared family stays the
    # bespoke no-constructor error, and a declared family keeps its own
    # arity diagnostics (counting the hidden receiver).
    with pytest.raises(
        LangError,
        match=r"type 'int32' has no constructor; "
        r"declare 'fn int32::constructor\(\.\.\.\)'",
    ):
        compile_ir("fn main() -> int32 { let x = int32(5); return 0; }")
    with pytest.raises(
        LangError, match=r"'point::constructor' expects 3 argument\(s\), got 2"
    ):
        compile_ir(
            """
            struct point<T> { x: T; y: T; }
            fn point<T>::constructor(self: &point<T>, x: T, y: T) {
                self.x = x; self.y = y;
            }
            fn main() -> int32 { let p = point<int32>(1); return 0; }
            """
        )


def test_implicit_empty_ctor_rejects_void():
    with pytest.raises(LangError, match=r"cannot construct a void value"):
        compile_ir("fn main() -> int32 { let v = void(); return 0; }")


def test_inherited_zero_arg_ctor_claims_the_derived_call(capfd):
    # The two features compose: `d()` finds the base's (mut self)-only
    # constructor in the merged family, so the DECLARED (inherited) member
    # claims the call -- its write lands in the base prefix, the derived
    # default stays -- and the implicit form stands down.
    assert run(
        """
        import "std/io";
        struct b { n: int32 = 7; }
        struct d extends b { extra: int32 = 1; }
        fn b::constructor(self: &b) { self.n = 99; }
        fn main() -> int32 {
            let v = d();
            println(f"{v.n} {v.extra}");
            return 0;
        }
        """
    ) == 0
    assert capfd.readouterr().out == "99 1\n"
