"""Method call sugar: ``recv.method(args)`` desugars to ``Type::method``.

A dot-shaped call whose receiver type registers a ``Type::method`` family
rewrites to ``Type::method(recv, args)``, passing the receiver expression
verbatim -- so overload resolution, ``mut``-receiver legality, evaluate-once
addressing, and every diagnostic are the desugared call's own. Fields shadow
methods (a fn-typed field keeps today's field-call behavior; the method stays
reachable by its qualified name), ``->`` stays fields-only, and a pointer
receiver auto-derefs one hop (``q.m()`` is ``Type::m(*q, ...)`` -- ``.`` on a
pointer was an error, so the space is free). An unprobeable receiver (a call
result) evaluates once into a hidden local: a plain rvalue spills to a
*const* slot, so a mut-self method on a temporary stays an error, while a
``mut``-returning receiver re-lends its carried lvalue. Bare ``p.method``
without a call is not a bound-method value, and explicit type arguments at a
dot-call (``p.m<int32>(...)``) do not parse -- both as at ``::`` calls.
"""

import re

import pytest

from mcc.driver import emit_interface
from mcc.errors import LangError
from helpers import compile_ir, run, run_path


# --- the driving use case ------------------------------------------------------


def test_acceptance_ctor_and_dot_call_through_fstring(capfd):
    # The headline shape: constructor sugar builds the receiver, the dot-call
    # rides an f-string hole, and the converting ctor was selected (the
    # diagonal is non-viable for int literals into float64 slots).
    assert run(
        """
        import "std/io";
        import "libc/math";
        struct point<T> { x: T; y: T; }
        fn point<T>::constructor(mut self: struct point<T>, x: T, y: T) {
            self.x = x; self.y = y;
        }
        fn point<T>::constructor<U>(mut self: struct point<T>, x: U, y: U) {
            self.x = x as T; self.y = y as T;
        }
        fn point<T>::magnitude(const self: struct point<T>) -> float64 {
            return sqrt(pow(self.x as float64, 2.0)
                        + pow(self.y as float64, 2.0));
        }
        fn main() -> int32 {
            let p = point<float64>(1, 1);
            println(f"{p.magnitude() = }");
            return 0;
        }
        """
    ) == 0
    assert capfd.readouterr().out == "p.magnitude() = 1.414214\n"


def test_mut_self_dot_call_mutates_the_receiver(capfd):
    assert run(
        """
        import "std/io";
        struct counter { n: int32; }
        fn counter::bump(mut self: counter) { self.n += 1; }
        fn counter::get(const self: counter) -> int32 { return self.n; }
        fn main() -> int32 {
            let c: counter; c.n = 0;
            c.bump();
            c.bump();
            println("{}", c.get());
            return 0;
        }
        """
    ) == 0
    assert capfd.readouterr().out == "2\n"


def test_explicit_qualified_call_stays_valid():
    assert run(
        """
        struct counter { n: int32; }
        fn counter::bump(mut self: counter) { self.n += 1; }
        fn main() -> int32 {
            let c: counter; c.n = 0;
            c.bump();
            counter::bump(c);
            return c.n - 2;
        }
        """
    ) == 0


# --- fields shadow methods -------------------------------------------------------


def test_fn_typed_field_shadows_the_method():
    # `h.cb(5)` calls the field's function pointer (10), never the method
    # (500); the method stays reachable by its qualified name.
    assert run(
        """
        struct holder { cb: fn(int32) -> int32; }
        fn double(v: int32) -> int32 { return v * 2; }
        fn holder::cb(const self: holder, v: int32) -> int32 {
            return v * 100;
        }
        fn main() -> int32 {
            let h: holder; h.cb = double;
            return h.cb(5) - 10 + holder::cb(h, 5) - 500;
        }
        """
    ) == 0


def test_non_fn_field_keeps_the_not_callable_diagnostics():
    # A same-named data field shadows the method in call shape too; calling
    # it is the existing not-a-function-pointer error, not silent dispatch.
    with pytest.raises(LangError, match=r"cannot call a value of type int32"):
        compile_ir(
            """
            struct s { v: int32; }
            fn s::v(const self: s) -> int32 { return 1; }
            fn main() -> int32 {
                let x: s; x.v = 3;
                return x.v(1);
            }
            """
        )


# --- error strings ---------------------------------------------------------------


def test_call_shape_with_neither_is_the_new_error():
    with pytest.raises(
        LangError, match=r"struct 'point' has no field or method 'nope'"
    ):
        compile_ir(
            """
            struct point { x: int32; }
            fn main() -> int32 {
                let p: point; p.x = 1;
                return p.nope(2);
            }
            """
        )


def test_union_call_shape_names_the_union():
    with pytest.raises(
        LangError, match=r"union 'u' has no field or method 'nope'"
    ):
        compile_ir(
            """
            union u { n: int32; c: char; }
            fn main() -> int32 {
                let v: u; v.n = 1;
                v.nope();
                return 0;
            }
            """
        )


def test_bare_member_access_keeps_the_field_error():
    # Only the call shape gets the new message; `p.get` without a call is
    # not a bound-method value and keeps the exact field diagnostics.
    with pytest.raises(LangError, match=r"struct point has no field 'get'"):
        compile_ir(
            """
            struct point { x: int32; }
            fn point::get(const self: point) -> int32 { return self.x; }
            fn main() -> int32 {
                let p: point; p.x = 1;
                let f = p.get;
                return 0;
            }
            """
        )


def test_arrow_stays_fields_only():
    with pytest.raises(LangError, match=r"struct point has no field 'get'"):
        compile_ir(
            """
            struct point { x: int32; }
            fn point::get(const self: point) -> int32 { return self.x; }
            fn main() -> int32 {
                let p: point; p.x = 3;
                let q = &p;
                return q->get();
            }
            """
        )


def test_builtin_without_the_method_keeps_the_no_fields_error():
    with pytest.raises(LangError, match=r"int32 is not a struct"):
        compile_ir(
            """
            fn main() -> int32 {
                let x: int32 = 1;
                return x.nope();
            }
            """
        )


# --- pointer receivers: one auto-deref hop ---------------------------------------


def test_pointer_receiver_auto_derefs(capfd):
    assert run(
        """
        import "std/io";
        struct counter { n: int32; }
        fn counter::bump(mut self: counter) { self.n += 1; }
        fn counter::get(const self: counter) -> int32 { return self.n; }
        fn main() -> int32 {
            let c: counter; c.n = 0;
            let q = &c;
            q.bump();
            println("{}", q.get());
            return c.n - 1;
        }
        """
    ) == 0
    assert capfd.readouterr().out == "1\n"


def test_double_pointer_receiver_stays_an_error():
    # Exactly one hop: a T** receiver keeps today's pointer diagnostics.
    with pytest.raises(LangError, match=r"counter\*\* is not a struct"):
        compile_ir(
            """
            struct counter { n: int32; }
            fn counter::bump(mut self: counter) { self.n += 1; }
            fn main() -> int32 {
                let c: counter; c.n = 0;
                let q = &c;
                let qq = &q;
                qq.bump();
                return 0;
            }
            """
        )


def test_pointee_field_still_needs_the_arrow():
    # A fn-typed field of the pointee is not reachable through `.` -- the
    # dot-on-pointer space is claimed for methods only.
    with pytest.raises(LangError, match=r"holder\* is not a struct"):
        compile_ir(
            """
            struct holder { cb: fn(int32) -> int32; }
            fn double(v: int32) -> int32 { return v * 2; }
            fn main() -> int32 {
                let h: holder; h.cb = double;
                let q = &h;
                return q.cb(5);
            }
            """
        )


# --- builtin, alias, and generic receivers ---------------------------------------


def test_builtin_receiver_dispatches_the_family(capfd):
    assert run(
        """
        import "std/io";
        import "std/char";
        fn main() -> int32 {
            println("{}{}", 'c'.upper(), 'D'.lower());
            return 0;
        }
        """
    ) == 0
    assert capfd.readouterr().out == "Cd\n"


def test_cast_receiver_probes_through_the_target():
    assert run(
        """
        import "std/char";
        fn main() -> int32 {
            let code: int32 = 99;
            let up = (code as char).upper();
            return up as int32 - 67;
        }
        """
    ) == 0


def test_alias_typed_receiver_uses_the_canonical_family():
    assert run(
        """
        struct point<T> { x: T; y: T; }
        fn point<T>::sum(const self: struct point<T>) -> T {
            return self.x + self.y;
        }
        type pointi = point<int32>;
        fn main() -> int32 {
            let p: pointi;
            p.x = 20; p.y = 22;
            return p.sum() - 42;
        }
        """
    ) == 0


def test_generic_receiver_specialization_outranks_the_generic(capfd):
    assert run(
        """
        import "std/io";
        struct box<T> { v: T; }
        fn box<T>::tag(self: box<T>) -> int32 { return 1; }
        fn box<float64>::tag(self: box<float64>) -> int32 { return 2; }
        fn main() -> int32 {
            let bi: box<int32> = { v = 7 };
            let bf: box<float64> = { v = 1.0 };
            println("{} {}", bi.tag(), bf.tag());
            return 0;
        }
        """
    ) == 0
    assert capfd.readouterr().out == "1 2\n"


def test_slice_receiver_dispatches_a_builtin_family():
    assert run(
        """
        fn slice<T>::first(self: slice<T>) -> T { return self[0]; }
        fn main() -> int32 {
            let xs: slice<int32> = [42, 1, 2];
            return xs.first() - 42;
        }
        """
    ) == 0


# --- rvalue receivers: evaluate once, const spill ---------------------------------


def test_chained_calls_evaluate_the_receiver_once(capfd):
    assert run(
        """
        import "std/io";
        import "std/char";
        struct wrap { c: char; }
        fn wrap::get(const self: wrap) -> char { return self.c; }
        fn wrap::mk(c: char) -> wrap {
            println("mk");
            let w: wrap; w.c = c;
            return w;
        }
        fn main() -> int32 {
            println("{}", wrap::mk('a').get().upper().lower().upper());
            return 0;
        }
        """
    ) == 0
    assert capfd.readouterr().out == "mk\nA\n"


def test_mut_self_on_a_temporary_errors():
    # The spilled receiver is const-typed on purpose: a writable temporary
    # would launder rvalue-ness and let a mut-self method silently mutate a
    # value about to be discarded.
    with pytest.raises(
        LangError, match=r"cannot pass a read-only const counter as a mut"
    ):
        compile_ir(
            """
            struct counter { n: int32; }
            fn counter::bump(mut self: counter) { self.n += 1; }
            fn counter::mk(n: int32) -> counter {
                let c: counter; c.n = n;
                return c;
            }
            fn main() -> int32 { counter::mk(1).bump(); return 0; }
            """
        )


def test_mut_return_receiver_re_lends():
    # `b.ref().grow()` writes b's own storage: the receiver's carried lvalue
    # re-lends, exactly as `box::grow(box::ref(b))` does.
    assert run(
        """
        struct box { v: int32; }
        fn box::grow(mut self: box) { self.v += 1; }
        fn box::ref(mut self: box) -> mut box { return self; }
        fn main() -> int32 {
            let b: box; b.v = 0;
            b.ref().grow();
            return b.v - 1;
        }
        """
    ) == 0


# --- mut-return methods as lvalues ------------------------------------------------


def test_dot_call_is_an_assignment_and_compound_target(capfd):
    assert run(
        """
        import "std/io";
        struct list8 { data: int32[8]; }
        fn list8::at(mut self: list8, i: int32) -> mut int32 {
            return self.data[i];
        }
        fn main() -> int32 {
            let l: list8;
            l.at(3) = 42;
            l.at(3) += 1;
            println("{}", l.at(3));
            return l.data[3] - 43;
        }
        """
    ) == 0
    assert capfd.readouterr().out == "43\n"


def test_chained_mut_return_dot_calls_form_a_store_target():
    assert run(
        """
        struct arena { data: int32[8]; }
        fn arena::at(mut self: arena, i: int32) -> mut int32 {
            return self.data[i];
        }
        fn arena::view(mut self: arena) -> mut arena { return self; }
        fn main() -> int32 {
            let a: arena;
            a.view().at(2) = 7;
            return a.data[2] - 7;
        }
        """
    ) == 0


def test_formation_chain_through_dot_methods():
    # `return self.inner().at(i);` -- the mut-return formation walk judges
    # dot-methods by their family, like named calls.
    assert run(
        """
        struct arena { data: int32[8]; }
        fn arena::at(mut self: arena, i: int32) -> mut int32 {
            return self.data[i];
        }
        struct outer { a: arena; }
        fn outer::inner(mut self: outer) -> mut arena { return self.a; }
        fn outer::pick(mut self: outer, i: int32) -> mut int32 {
            return self.inner().at(i);
        }
        fn main() -> int32 {
            let o: outer;
            o.pick(1) = 5;
            return o.a.data[1] - 5;
        }
        """
    ) == 0


def test_formation_chain_through_a_non_mut_method_rejects():
    # A by-value method in the chain hands out a temporary; the spilled
    # receiver is const, so the mut hand-off is rejected.
    with pytest.raises(
        LangError, match=r"cannot pass a read-only const arena as a mut"
    ):
        compile_ir(
            """
            struct arena { data: int32[8]; }
            fn arena::at(mut self: arena, i: int32) -> mut int32 {
                return self.data[i];
            }
            struct outer { a: arena; }
            fn outer::snapshot(mut self: outer) -> arena { return self.a; }
            fn outer::pick(mut self: outer, i: int32) -> mut int32 {
                return self.snapshot().at(i);
            }
            fn main() -> int32 { return 0; }
            """
        )


def test_non_mut_dot_call_is_not_assignable():
    with pytest.raises(
        LangError,
        match=r"the call to 'counter::get' does not return mut, so its "
        r"result is not assignable",
    ):
        compile_ir(
            """
            struct counter { n: int32; }
            fn counter::get(mut self: counter) -> int32 { return self.n; }
            fn main() -> int32 {
                let c: counter; c.n = 1;
                c.get() = 5;
                return 0;
            }
            """
        )


# --- interface stubs --------------------------------------------------------------


def test_inline_bodies_with_sugar_round_trip_through_mci(tmp_path):
    # @inline bodies travel verbatim through the stub and re-parse with the
    # same parser -- both sugars included.
    lib = tmp_path / "lib.mc"
    lib.write_text(
        "struct point<T> { x: T; y: T; }\n"
        "fn point<T>::constructor(mut self: struct point<T>, x: T, y: T)"
        " { self.x = x; self.y = y; }\n"
        "fn point<T>::sum(const self: struct point<T>) -> T"
        " { return self.x + self.y; }\n"
        "@inline\n"
        "fn diagsum<T>(v: T) -> T {\n"
        "    let p = point<T>(v, v);\n"
        "    return p.sum();\n"
        "}\n"
    )
    out = tmp_path / "lib.mci"
    assert emit_interface(lib, (tmp_path,), None, {}, out) == 0
    stub = out.read_text()
    assert "point<T>(v, v)" in stub  # the sugar travels verbatim
    assert "p.sum()" in stub
    lib.unlink()  # force the import to resolve through the stub
    main = tmp_path / "main.mc"
    main.write_text(
        'import "lib";\nfn main() -> int32 { return diagsum(21) - 42; }\n'
    )
    assert run_path(main) == 0


# --- constructor/destructor are qualified-only ------------------------------------

# The two semantic method names cannot be called with method syntax: the
# fully qualified forms `T::constructor(t, args)` / `T::destructor(t)` are
# the only spellings, intended mainly for chaining a base's from a derived
# body. Construction is the `S(args)` sugar and destruction is automatic
# (see test_destructors.py); the dot forms are refused where the rewrite
# would have happened -- a genuine FIELD of either name keeps its field
# behavior, and a receiver with no such family keeps today's diagnostics.

PAIR = """
struct res { id: int32; }
fn res::constructor(mut self: res, id: int32) { self.id = id; }
fn res::destructor(mut self: res) { }
"""


def test_dot_constructor_is_refused_with_the_qualified_hint():
    with pytest.raises(
        LangError,
        match=re.escape(
            "'constructor' cannot be called with method syntax; "
            "use res::constructor(r, ...)"
        ),
    ):
        compile_ir(
            PAIR
            + """
            fn main() -> int32 {
                let r: res;
                r.constructor(1);
                return 0;
            }
            """
        )


def test_dot_destructor_is_refused_with_the_qualified_hint():
    with pytest.raises(
        LangError,
        match=re.escape(
            "'destructor' cannot be called with method syntax; "
            "use res::destructor(r)"
        ),
    ):
        compile_ir(
            PAIR
            + """
            fn main() -> int32 {
                let r = res(1);
                r.destructor();
                return 0;
            }
            """
        )


def test_dot_ban_on_a_generic_receiver_names_the_template():
    # An instantiated generic receiver suggests the family's canonical
    # (template) qualifier, exactly the name a chaining call would use.
    with pytest.raises(
        LangError,
        match=re.escape(
            "'destructor' cannot be called with method syntax; "
            "use point::destructor(p)"
        ),
    ):
        compile_ir(
            """
            struct point<T> { x: T; y: T; }
            fn point<T>::destructor(mut self: struct point<T>) { }
            fn main() -> int32 {
                let p: point<float64>;
                p.destructor();
                return 0;
            }
            """
        )


def test_dot_ban_through_an_alias_receiver():
    # The alias-typed receiver resolves to the canonical family and the ban
    # (with the canonical qualifier) applies the same.
    with pytest.raises(
        LangError,
        match=re.escape(
            "'constructor' cannot be called with method syntax; "
            "use point::constructor(p, ...)"
        ),
    ):
        compile_ir(
            """
            struct point<T> { x: T; y: T; }
            fn point<T>::constructor(mut self: struct point<T>, x: T, y: T) {
                self.x = x; self.y = y;
            }
            type pointf = point<float64>;
            fn main() -> int32 {
                let p: pointf;
                p.constructor(1.0, 2.0);
                return 0;
            }
            """
        )


def test_dot_ban_on_a_builtin_receiver():
    with pytest.raises(
        LangError,
        match=re.escape(
            "'destructor' cannot be called with method syntax; "
            "use int32::destructor(x)"
        ),
    ):
        compile_ir(
            """
            fn int32::destructor(mut self: int32) { }
            fn main() -> int32 {
                let x: int32 = 1;
                x.destructor();
                return 0;
            }
            """
        )


def test_dot_ban_on_a_pointer_receiver_spells_the_deref():
    # A pointer receiver's dot-call would auto-deref one hop; the suggestion
    # spells that deref.
    with pytest.raises(
        LangError,
        match=re.escape(
            "'destructor' cannot be called with method syntax; "
            "use res::destructor(*q)"
        ),
    ):
        compile_ir(
            PAIR
            + """
            fn main() -> int32 {
                let r = res(1);
                let q = &r;
                q.destructor();
                return 0;
            }
            """
        )


def test_dot_ban_on_an_rvalue_receiver_does_not_leak_the_spill_name():
    # A spilled receiver re-dispatches on a hidden local; the suggestion
    # renders a generic `value`, never the unlexable spill name.
    with pytest.raises(
        LangError,
        match=re.escape(
            "'destructor' cannot be called with method syntax; "
            "use res::destructor(value)"
        ),
    ):
        compile_ir(
            PAIR
            + """
            fn make() -> res { return res(1); }
            fn main() -> int32 {
                make().destructor();
                return 0;
            }
            """
        )


def test_field_named_constructor_keeps_field_behavior():
    # Fields shadow methods BEFORE the ban is judged: a fn-typed field named
    # `constructor` stays an ordinary field call.
    assert run(
        """
        struct odd { constructor: fn(int32) -> int32; }
        fn double(v: int32) -> int32 { return v * 2; }
        fn main() -> int32 {
            let o: odd;
            o.constructor = double;
            return o.constructor(21) - 42;
        }
        """
    ) == 0


def test_qualified_chaining_spellings_stay_legal(capfd):
    # The qualified forms remain first-class -- their main use, chaining a
    # base's constructor and destructor from a derived body.
    assert run(
        'import "std/io";'
        + PAIR
        + """
        struct tagged extends res { tag: int32; }
        fn tagged::constructor(mut self: tagged, id: int32, tag: int32) {
            res::constructor(self, id);
            self.tag = tag;
        }
        fn tagged::destructor(mut self: tagged) {
            println("drop tag {}", self.tag);
            res::destructor(self);
        }
        fn main() -> int32 {
            let t = tagged(3, 9);
            return t.tag - 9;
        }
        """
    ) == 0
    assert capfd.readouterr().out == "drop tag 9\n"
