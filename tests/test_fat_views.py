"""SIE-101 Stage 2: fat base views + dynamic dispatch.

The staged build. This file grows a slice at a time:

- S2.1 -- the fatness predicate ``is_fat_base``: a reference ``&A`` is a
  two-word fat pointer (carrying a dispatch table) iff struct ``A`` is
  ``extends``-extended somewhere the forming site can see. The gate mirrors the
  open-overload-set import-closure rule. Fatness is a property of the base
  TYPE, uniform across its references and independent of whether any family is
  overridden -- so adding the first override to a hierarchy never flips a
  type's width (ABI stability). Committed at ``extends`` time.
"""

import pytest

from mcc.codegen import CodeGen
from mcc.driver import compile_to_ir, emit_interface
from mcc.errors import LangError
from helpers import _resolve, compile_ir, run, run_path


def _gen(source: str) -> CodeGen:
    """Run codegen and hand back the generator, so its post-compile tables
    (here ``extended_by`` / ``is_fat_base``) can be inspected."""
    cg = CodeGen(_resolve(source), "test")
    cg.generate()
    return cg


# --- S2.1: the fatness predicate ----------------------------------------------


def test_extended_base_is_fat():
    # `struct b extends a`: a reference `&a` must carry a table, so `a` is fat.
    cg = _gen(
        """
        struct a { n: int32; }
        struct b extends a { m: int32; }
        fn main() -> int32 { return 0; }
        """
    )
    assert cg.is_fat_base("a", None) is True


def test_unextended_leaf_is_thin():
    # `b` is extended by nothing, so `&b` stays a one-word thin reference --
    # the leaf of the chain never carries a table.
    cg = _gen(
        """
        struct a { n: int32; }
        struct b extends a { m: int32; }
        fn main() -> int32 { return 0; }
        """
    )
    assert cg.is_fat_base("b", None) is False


def test_standalone_struct_is_thin():
    # A struct nothing extends is thin -- the zero-cost common case.
    cg = _gen("struct s { x: int32; } fn main() -> int32 { return 0; }")
    assert cg.is_fat_base("s", None) is False


def test_unknown_name_is_thin():
    # A name with no recorded extends edge is thin (no crash on a miss).
    cg = _gen("fn main() -> int32 { return 0; }")
    assert cg.is_fat_base("nope", None) is False


def test_transitive_middle_base_is_fat():
    # a <- b <- c: both `a` and `b` are extended (by b and c respectively), so
    # both are fat; only the leaf `c` is thin.
    cg = _gen(
        """
        struct a { n: int32; }
        struct b extends a { m: int32; }
        struct c extends b { k: int32; }
        fn main() -> int32 { return 0; }
        """
    )
    assert cg.is_fat_base("a", None) is True
    assert cg.is_fat_base("b", None) is True
    assert cg.is_fat_base("c", None) is False


def test_generic_base_is_fat_by_template_name():
    # `pointf extends point<float64>`: fatness is keyed by the base's template
    # name, so every `&point<...>` reference is fat.
    cg = _gen(
        """
        struct point<T> { x: T; y: T; }
        struct pointf extends point<float64> { label: int32; }
        fn main() -> int32 { return 0; }
        """
    )
    assert cg.is_fat_base("point", None) is True
    assert cg.is_fat_base("pointf", None) is False


def test_fatness_ignores_override_presence():
    # Fatness is a property of the base type, NOT its method set: `a` is fat
    # purely because `b` extends it, with no `@override` anywhere.
    cg = _gen(
        """
        struct a { n: int32; }
        struct b extends a { m: int32; }
        fn a::describe(const self: &a) -> int32 { return 1; }
        fn main() -> int32 { return 0; }
        """
    )
    assert cg.is_fat_base("a", None) is True


def test_extends_type_parameter_contributes_no_edge():
    # `struct entry<T> extends T` is intrusive reuse -- no declared base
    # family, so it forms no fat-base edge (struct_base_ref returns None).
    cg = _gen(
        """
        struct entry<T> extends T { next: entry<T>*; }
        fn main() -> int32 { return 0; }
        """
    )
    assert cg.extended_by == {}


def test_stdlib_slice_is_fat_under_the_literal_rule():
    # The maintainer's literal gate: `list<T> extends slice<T>` in the stdlib,
    # so importing it makes `&slice` fat program-wide (the accepted stdlib
    # regression -- ABI stability was chosen over slice staying thin).
    cg = _gen('import "std/list";\nfn main() -> int32 { return 0; }')
    assert cg.is_fat_base("slice", None) is True


# --- S2.2: the slot-index model -----------------------------------------------

_ABC_CHAIN = """
    struct a { n: int32; }
    struct b extends a { m: int32; }
    struct c extends b { k: int32; }
    fn a::greet(const self: &a) -> int32 { return 1; }
    @override fn b::greet(const self: &b) -> int32 { return 2; }
    @override fn c::greet(const self: &c) -> int32 { return 3; }
    fn main() -> int32 { return 0; }
    """


def test_overridden_family_earns_a_slot_at_its_introducer():
    # greet is introduced at `a` and overridden below, so it is a dispatch
    # family keyed at `a` -- one slot, index 0, in every table down the chain.
    cg = _gen(_ABC_CHAIN)
    assert cg.dispatch_families == {("a", "greet")}
    assert cg.dispatch_slots("a") == [("greet", "a")]
    assert cg.dispatch_slots("b") == [("greet", "a")]
    assert cg.dispatch_slots("c") == [("greet", "a")]
    assert cg.is_dispatch_family("c", "greet") is True


def test_slot_list_is_prefix_compatible_down_the_chain():
    # A base's slots are a prefix of every descendant's: `b` introduces a
    # second overridden family `tag`, appended AFTER `a`'s `greet` slot, so
    # `a`'s [greet] stays a prefix of `b`'s [greet, tag].
    cg = _gen(
        """
        struct a { n: int32; }
        struct b extends a { m: int32; }
        struct c extends b { k: int32; }
        fn a::greet(const self: &a) -> int32 { return 1; }
        @override fn b::greet(const self: &b) -> int32 { return 2; }
        fn b::tag(const self: &b) -> int32 { return 10; }
        @override fn c::tag(const self: &c) -> int32 { return 20; }
        fn main() -> int32 { return 0; }
        """
    )
    assert cg.dispatch_slots("a") == [("greet", "a")]
    assert cg.dispatch_slots("b") == [("greet", "a"), ("tag", "b")]
    assert cg.dispatch_slots("c") == [("greet", "a"), ("tag", "b")]


def test_non_overridden_family_gets_no_slot():
    # A family declared but never overridden is a plain direct call -- no slot,
    # even on a fat base. So a fat type can have an empty table.
    cg = _gen(
        """
        struct a { n: int32; }
        struct b extends a { m: int32; }
        fn a::describe(const self: &a) -> int32 { return 1; }
        fn main() -> int32 { return 0; }
        """
    )
    assert cg.is_fat_base("a", None) is True   # fat...
    assert cg.dispatch_slots("a") == []        # ...but empty table
    assert cg.dispatch_slots("b") == []
    assert cg.is_dispatch_family("a", "describe") is False


def test_unextended_struct_has_no_slots():
    cg = _gen(
        """
        struct s { x: int32; }
        fn s::area(const self: &s) -> int32 { return self.x; }
        fn main() -> int32 { return 0; }
        """
    )
    assert cg.dispatch_slots("s") == []


def test_generic_base_override_earns_a_slot():
    # `pointf extends point<float64>` with `@override pointf::describe`: the
    # family is introduced at the generic base `point`, keyed by template name.
    cg = _gen(
        """
        struct point<T> { x: T; y: T; }
        struct pointf extends point<float64> { label: int32; }
        fn point<T>::describe(const self: &point<T>) -> int32 { return 1; }
        fn point<T>::sum(const self: &point<T>) -> T { return self.x + self.y; }
        @override fn pointf::describe(const self: &pointf) -> int32 { return 2; }
        fn main() -> int32 { return 0; }
        """
    )
    assert ("point", "describe") in cg.dispatch_families
    assert cg.dispatch_slots("pointf") == [("describe", "point")]
    # sum is inherited but never overridden -> no slot.
    assert cg.is_dispatch_family("pointf", "sum") is False


def test_destructor_override_is_not_a_dispatch_family():
    # Special members sit outside dispatch (destructor slot deferred): a
    # derived destructor never earns a table slot even though it "overrides".
    cg = _gen(
        """
        struct a { n: int32; }
        struct b extends a { m: int32; }
        fn a::destructor(self: &a) { }
        @override fn b::destructor(self: &b) { }
        fn main() -> int32 { return 0; }
        """
    )
    assert cg.dispatch_families == set()
    assert cg.dispatch_slots("b") == []


def test_unknown_family_is_not_a_dispatch_family():
    # A family no type in the chain declares has no introducer -> not a
    # dispatch family (no crash on a miss).
    cg = _gen("struct s { x: int32; } fn main() -> int32 { return 0; }")
    assert cg.family_introducer("s", "ghost") is None
    assert cg.is_dispatch_family("s", "ghost") is False


# --- S2.3: the fat reference parameter ABI ------------------------------------


def test_extended_base_receiver_is_two_word():
    # A method on an extended base takes a fat `{a*, i8*}` receiver: the object
    # pointer plus a (currently null) dispatch-table word.
    ir = compile_ir(
        """
        struct a { n: int32; }
        struct b extends a { m: int32; }
        fn a::describe(const self: &a) -> int32 { return self.n; }
        fn main() -> int32 { let v: a = { n = 5 }; return a::describe(v); }
        """
    )
    assert 'define i32 @"a::describe"({%"a"*, i8*} %"self")' in ir


def test_unextended_receiver_stays_one_word():
    # A method on a struct nothing extends keeps its zero-cost one-word `s*`
    # receiver -- no table word.
    ir = compile_ir(
        """
        struct s { x: int32; }
        fn s::area(const self: &s) -> int32 { return self.x; }
        fn main() -> int32 { let v: s = { x = 5 }; return s::area(v); }
        """
    )
    assert 'define i32 @"s::area"(%"s"* %"self")' in ir
    assert '{%"s"*, i8*}' not in ir  # never a two-word receiver


def test_fat_free_function_parameter_is_two_word():
    # Fatness is a property of the base type, not a method receiver: any `&a`
    # parameter (a extended) is two-word, receiver or not.
    ir = compile_ir(
        """
        struct a { n: int32; }
        struct b extends a { m: int32; }
        fn describe(const it: &a) -> int32 { return it.n; }
        fn main() -> int32 { let v: a = { n = 7 }; return describe(v); }
        """
    )
    assert 'define i32 @"describe"({%"a"*, i8*} %"it")' in ir


def test_fat_call_site_passes_a_null_table_word():
    # The widening is inert: the call site forms `{ptr, null}` -- the table
    # word is null until Stage 2's dispatch wiring fills it, so behavior is
    # identical to a thin call.
    ir = compile_ir(
        """
        struct a { n: int32; }
        struct b extends a { m: int32; }
        fn a::describe(const self: &a) -> int32 { return self.n; }
        fn main() -> int32 { let v: a = { n = 5 }; return a::describe(v); }
        """
    )
    # the fat value is built by insertvalue-ing the pointer then a null i8*.
    assert 'insertvalue {%"a"*, i8*}' in ir
    assert "i8* null, 1" in ir


def test_fat_reference_in_function_pointer_type_is_rejected():
    # A fat `&a` may not ride in a function-pointer type yet (its width can
    # differ across closures): the clear compile error.
    with pytest.raises(
        LangError,
        match=(
            r"a fat reference type \(&a, whose base is extended\) may not "
            r"appear in a function-pointer type yet"
        ),
    ):
        compile_ir(
            """
            struct a { n: int32; }
            struct b extends a { m: int32; }
            fn main() -> int32 { let g: fn(&a); return 0; }
            """
        )


def test_thin_reference_in_function_pointer_type_still_allowed():
    # A reference to an un-extended struct is thin, so it still rides in a
    # function-pointer type unchanged (only FAT refs are rejected there).
    ir = compile_ir(
        """
        struct s { x: int32; }
        fn poke(x: &s) { x.x = 9; }
        fn main() -> int32 {
            let g: fn(&s) = poke;
            let v: s = { x = 3 };
            g(v);
            return v.x - 9;
        }
        """
    )
    assert '@"poke"' in ir   # compiled, not rejected -- thin ref is fine


def test_fat_abi_is_inert_calls_stay_static(capfd):
    # S2.3 is a pure ABI widening: all calls are still DIRECT (dispatch is
    # Stage 2's next slice). The explicit base-qualified call upcasts the
    # receiver b -> &a and calls a::describe directly ("a"); the dot call
    # resolves to the nearest member b::describe ("b"). The null table is
    # never indexed -- no dynamic dispatch happens yet.
    assert run(
        """
        import "std/io";
        struct a { n: int32; }
        struct b extends a { m: int32; }
        fn a::describe(const self: &a) { println("a"); }
        @override fn b::describe(const self: &b) { println("b"); }
        fn main() -> int32 {
            let v: b = { n = 1, m = 2 };
            a::describe(v);   // receiver upcasts b -> &a; direct -> "a"
            v.describe();     // dot resolves nearest member -> "b"
            return 0;
        }
        """
    ) == 0
    assert capfd.readouterr().out == "a\nb\n"


# --- S2.4: dynamic dispatch through fat views ---------------------------------

_GREET_CHAIN = """
    import "std/io";
    struct a { n: int32; }
    struct b extends a { m: int32; }
    struct c extends b { k: int32; }
    fn a::greet(const self: &a) { println("A"); }
    @override fn b::greet(const self: &b) { println("B"); }
    @override fn c::greet(const self: &c) { println("C"); }
    fn f(const it: &a) { it.greet(); }
"""


def test_acceptance_dispatch_through_base_view_prints_C(capfd):
    # THE acceptance test: a C value passed to `f(const a: &A)` dispatches
    # through the fat view's table to C::greet -- prints "C", not "A".
    assert run(
        _GREET_CHAIN
        + """
        fn main() -> int32 {
            let v: c = { n = 1, m = 2, k = 3 };
            f(v);
            return 0;
        }
        """
    ) == 0
    assert capfd.readouterr().out == "C\n"


def test_dispatch_through_a_mid_chain_view(capfd):
    # A view typed at the mid-chain base B: a C dispatches to C::greet, a B to
    # B::greet -- the slot index is shared down the chain (prefix compatible).
    assert run(
        _GREET_CHAIN
        + """
        fn g(const it: &b) { it.greet(); }
        fn main() -> int32 {
            let x: c = { n = 1, m = 2, k = 3 };
            let y: b = { n = 4, m = 5 };
            g(x);   // -> C
            g(y);   // -> B
            return 0;
        }
        """
    ) == 0
    assert capfd.readouterr().out == "C\nB\n"


def test_non_dispatch_family_stays_a_direct_call(capfd):
    # A family with no override anywhere is not a dispatch family: a call
    # through a base view binds STATICALLY (no table slot), so it prints the
    # base's version even when the object is a derived one.
    assert run(
        """
        import "std/io";
        struct a { n: int32; }
        struct b extends a { m: int32; }
        fn a::tag(const self: &a) { println("a"); }
        fn viewit(const it: &a) { it.tag(); }   // tag never overridden -> direct
        fn main() -> int32 {
            let v: b = { n = 1, m = 2 };
            viewit(v);
            return 0;
        }
        """
    ) == 0
    assert capfd.readouterr().out == "a\n"


def test_devirtualization_on_statically_known_receiver(capfd):
    # A concrete receiver of statically-known type dispatches with no table
    # load -- devirtualized to a direct call. The IR shows the direct call and
    # no getelementptr into a vtable for it.
    src = """
        import "std/io";
        struct a { n: int32; }
        struct b extends a { m: int32; }
        fn a::greet(const self: &a) { println("A"); }
        @override fn b::greet(const self: &b) { println("B"); }
        fn main() -> int32 {
            let v: b = { n = 1, m = 2 };
            v.greet();     // statically a b -> direct call to b::greet
            return 0;
        }
    """
    assert run(src) == 0
    assert capfd.readouterr().out == "B\n"
    ir = compile_ir(src)
    assert '@"b::greet"' in ir  # a direct call, not only a thunk


def test_re_dispatch_inside_a_method_reaches_the_derived_override(capfd):
    # A base method calls another overridden family on `self`; when reached
    # through a base view of a derived object, that inner call must dispatch to
    # the DERIVED override -- self's table propagates through the call.
    assert run(
        """
        import "std/io";
        struct a { n: int32; }
        struct b extends a { m: int32; }
        fn a::name(const self: &a) { println("a"); }
        @override fn b::name(const self: &b) { println("b"); }
        fn a::show(const self: &a) { self.name(); }   // re-dispatch on self
        fn f(const it: &a) { it.show(); }
        fn main() -> int32 {
            let v: b = { n = 1, m = 2 };
            f(v);   // show is inherited a::show; self.name() -> b::name
            return 0;
        }
        """
    ) == 0
    assert capfd.readouterr().out == "b\n"


def test_copy_on_read_prefix_extraction_drops_the_view(capfd):
    # Copying a value OUT of a fat view is prefix extraction: a plain `a` with
    # NO table. A method call on the copy binds to its static type -- the view
    # (and any dynamic type) is gone, behavioral slicing by design.
    assert run(
        """
        import "std/io";
        struct a { n: int32; }
        struct b extends a { m: int32; }
        fn a::greet(const self: &a) { println("A"); }
        @override fn b::greet(const self: &b) { println("B"); }
        fn f(const it: &a) {
            let copy: a = it;   // prefix-extraction copy, no view
            copy.greet();       // plain `a` -> a::greet
        }
        fn main() -> int32 {
            let v: b = { n = 1, m = 2 };
            f(v);
            return 0;
        }
        """
    ) == 0
    assert capfd.readouterr().out == "A\n"


# --- S2.5: fatness across the .mci boundary -----------------------------------


def test_mci_stub_renders_the_fat_hierarchy(tmp_path):
    # The stub keeps the `extends` edge and the `&` reference spelling, so a
    # consumer can re-derive the base's fatness from it.
    lib = tmp_path / "geo.mc"
    lib.write_text(
        "struct a { n: int32; }\n"
        "struct b extends a { m: int32; }\n"
        "fn a::greet(const self: &a) -> int32 { return self.n; }\n"
        "@override fn b::greet(const self: &b) -> int32 { return self.m; }\n"
    )
    out = tmp_path / "geo.mci"
    assert emit_interface(lib, (tmp_path,), None, {}, out) == 0
    stub = out.read_text()
    assert "struct b extends a" in stub
    assert "fn a::greet(const self: &a)" in stub  # the & reference preserved


def test_mci_fat_hierarchy_produces_matching_fat_abi(tmp_path):
    # When the stub itself carries the extension, a consumer re-derives `&a` as
    # fat from the stub's OWN closure -- so its declaration and call match the
    # two-word ABI the defining object was compiled with.
    (tmp_path / "geo.mci").write_text(
        "struct a { n: int32; }\n"
        "struct b extends a { m: int32; }\n"
        "fn viewit(const it: &a) -> int32;\n"
    )
    main = tmp_path / "main.mc"
    main.write_text(
        'import "geo";\n'
        "fn main() -> int32 { let x: a = { n = 9 }; return viewit(x); }\n"
    )
    ir = str(compile_to_ir(main, (tmp_path,)))
    assert 'declare i32 @"viewit"({%"a"*, i8*}' in ir  # fat proto
    assert 'call i32 @"viewit"({%"a"*, i8*}' in ir      # fat call site


def test_mci_fatness_is_pinned_to_the_stub_own_closure(tmp_path):
    # The stub never saw `a` extended, so its `viewit(&a)` is THIN -- and stays
    # thin even though the CONSUMER adds `b extends a`. A local `&a` function in
    # the consumer, which does see the extension, is fat. Fatness is pinned per
    # compilation's import closure, so a separately-compiled object keeps the
    # ABI it was built with (the ruling).
    (tmp_path / "api.mci").write_text(
        "struct a { n: int32; }\nfn viewit(const it: &a) -> int32;\n"
    )
    main = tmp_path / "main.mc"
    main.write_text(
        'import "api";\n'
        "struct b extends a { m: int32; }\n"
        "fn local(const it: &a) -> int32 { return it.n; }\n"
        "fn main() -> int32 {\n"
        "    let x: a = { n = 1 };\n"
        "    return local(x) + viewit(x);\n"
        "}\n"
    )
    ir = str(compile_to_ir(main, (tmp_path,)))
    assert 'declare i32 @"viewit"(%"a"* ' in ir            # stub stays THIN
    assert 'define i32 @"local"({%"a"*, i8*}' in ir        # consumer's is FAT
    assert 'call i32 @"viewit"(%"a"* ' in ir               # call matches thin


def test_mci_proto_def_fatness_mismatch_is_a_clean_error(tmp_path):
    # A stub that did not see `a` extended declares a THIN viewit; a definition
    # that DOES see the extension is FAT. One- vs two-word ABIs are genuinely
    # different, so pairing them is the prototype-mismatch error, not a silent
    # miscompile (the fat-ref check in the prototype comparison).
    (tmp_path / "api.mci").write_text(
        "struct a { n: int32; }\nfn viewit(const it: &a) -> int32;\n"
    )
    (tmp_path / "impl.mc").write_text(
        'import "api";\n'
        "struct b extends a { m: int32; }\n"
        "fn viewit(const it: &a) -> int32 { return it.n; }\n"
    )
    main = tmp_path / "main.mc"
    main.write_text(
        'import "api";\nimport "impl";\nfn main() -> int32 { return 0; }'
    )
    with pytest.raises(
        LangError,
        match=r"definition of 'viewit' does not match its prototype",
    ):
        compile_to_ir(main, (tmp_path,))


# --- S2.6: overload-aware slots + rvalue table sourcing -----------------------


def test_overloaded_family_dispatches_the_resolved_sibling():
    # Two overloads of one method name earn DISTINCT slots. `b` overrides only
    # the no-arg `pick`; a `pick(5)` through a base view must resolve and
    # dispatch the (non-overridden) `pick(int32)` overload -- returning 15 --
    # not silently invoke the wrong-arity no-arg override (which returned 2
    # when overloads shared a single method-name-keyed slot).
    assert run(
        """
        struct a { n: int32; }
        struct b extends a { m: int32; }
        fn a::pick(const self: &a) -> int32 { return 1; }
        fn a::pick(const self: &a, x: int32) -> int32 { return x + 10; }
        @override fn b::pick(const self: &b) -> int32 { return 2; }
        fn via(const x: &a) -> int32 { return x.pick(5); }
        fn main() -> int32 {
            let obj: b = { n = 0, m = 0 };
            return via(obj);
        }
        """
    ) == 15


def test_each_overload_dispatches_to_its_own_override():
    # Both overloads overridden on `b`: each base-view call must reach the
    # sibling with the matching signature -- `pick()` -> b's no-arg (2),
    # `pick(5)` -> b's one-arg (105) -- so the sum is 107, proving the two
    # overloads occupy independent slots down the chain.
    assert run(
        """
        struct a { n: int32; }
        struct b extends a { m: int32; }
        fn a::pick(const self: &a) -> int32 { return 1; }
        fn a::pick(const self: &a, x: int32) -> int32 { return x + 10; }
        @override fn b::pick(const self: &b) -> int32 { return 2; }
        @override fn b::pick(const self: &b, x: int32) -> int32 { return x + 100; }
        fn via0(const x: &a) -> int32 { return x.pick(); }
        fn via1(const x: &a) -> int32 { return x.pick(5); }
        fn main() -> int32 {
            let obj: b = { n = 0, m = 0 };
            return via0(obj) + via1(obj);
        }
        """
    ) == 107


def test_overloaded_slots_stay_prefix_compatible():
    # The two `pick` overloads take two adjacent slots at their introducer `a`;
    # `b`'s slot list is a prefix-compatible extension (identical here), so a
    # `&a` view indexing a `b` object reaches the right slot for each overload.
    cg = _gen(
        """
        struct a { n: int32; }
        struct b extends a { m: int32; }
        fn a::pick(const self: &a) -> int32 { return 1; }
        fn a::pick(const self: &a, x: int32) -> int32 { return x + 10; }
        @override fn b::pick(const self: &b) -> int32 { return 2; }
        @override fn b::pick(const self: &b, x: int32) -> int32 { return x + 100; }
        fn main() -> int32 { return 0; }
        """
    )
    specs = cg.dispatch_slot_specs("b")
    # both overloads earn a slot, keyed by their receiver-stripped patterns,
    # both introduced at `a`, and `a`'s specs are a prefix of `b`'s.
    assert [(m, p) for m, p, _i in specs] == [("pick", ()), ("pick", ("int32",))]
    assert all(intro == "a" for _m, _p, intro in specs)
    assert cg.dispatch_slot_specs("a") == cg.dispatch_slot_specs("b")


def test_derived_rvalue_argument_carries_the_derived_table():
    # A call-result rvalue (`make()` returns the derived `b`) passed into a fat
    # `&a` parameter must carry `b`'s table, so the base view dispatches to
    # b::kind (2) -- not slice back to the declared base `a` (1), which the
    # lvalue-only table probe did by falling through to the parameter type.
    assert run(
        """
        struct a { n: int32; }
        struct b extends a { m: int32; }
        fn a::kind(const self: &a) -> int32 { return 1; }
        @override fn b::kind(const self: &b) -> int32 { return 2; }
        fn make() -> b { return b { n = 0, m = 0 }; }
        fn via(const x: &a) -> int32 { return x.kind(); }
        fn main() -> int32 { return via(make()); }
        """
    ) == 2


def test_as_upcast_temporary_stays_base_dispatched():
    # The counterpart: a by-value `as` upcast produces a GENUINE base value, so
    # it must carry the BASE table and dispatch to a::kind (1) -- the intended
    # slicing. Only a plain derived rvalue keeps its derived table.
    assert run(
        """
        struct a { n: int32; }
        struct b extends a { m: int32; }
        fn a::kind(const self: &a) -> int32 { return 1; }
        @override fn b::kind(const self: &b) -> int32 { return 2; }
        fn via(const x: &a) -> int32 { return x.kind(); }
        fn main() -> int32 {
            let obj: b = { n = 0, m = 0 };
            return via(obj as a);
        }
        """
    ) == 1


# --- S2.7: dispatch-soundness gates -------------------------------------------
#
# A base-chain override shares its base member's single vtable slot, so it must
# stay ABI-compatible with it, and a few constructs a slot cannot represent are
# rejected cleanly rather than miscompiled. Each of these was a silent
# wrong-answer or a crash before the gate (SIE-101 review round 2).


def test_override_with_a_different_return_type_is_rejected():
    # The slot's indirect call is typed with the BASE member's return type; a
    # float64 override of an int32 base would reinterpret the returned bytes
    # (the review's garbage 1801667688). Reject at declaration.
    with pytest.raises(
        LangError,
        match=(
            r"@override method 'b::val' returns float64 but the base member it "
            r"overrides returns int32; an override must return the same type"
        ),
    ):
        compile_ir(
            """
            struct a { n: int32; }
            struct b extends a { m: int32; }
            fn a::val(const self: &a) -> int32 { return 7; }
            @override fn b::val(const self: &b) -> float64 { return 3.0; }
            fn via(const x: &a) -> int32 { return x.val(); }
            fn main() -> int32 { let o: b = { n = 0, m = 0 }; return via(o); }
            """
        )


def test_override_widening_a_const_receiver_to_mutable_is_rejected():
    # A read-only `const self: &a` base overridden by a writable `self: &b`:
    # a call dispatched through a `const &a` view would then mutate through a
    # promise not to (the review's 198 = mutation leaking to the caller object).
    with pytest.raises(
        LangError,
        match=(
            r"@override method 'b::peek' takes a writable 'self: &T' receiver "
            r"but the base member it overrides takes a read-only "
            r"'const self: &T' receiver"
        ),
    ):
        compile_ir(
            """
            struct a { n: int32; }
            struct b extends a { m: int32; }
            fn a::peek(const self: &a) -> int32 { return self.n; }
            @override fn b::peek(self: &b) -> int32 { self.m = 99; return self.m; }
            fn via(const x: &a) -> int32 { return x.peek(); }
            fn main() -> int32 { let o: b = { n = 1, m = 0 }; return via(o); }
            """
        )


def test_override_may_narrow_a_mutable_receiver_to_const(capfd):
    # The safe direction is allowed: a writable base receiver overridden by a
    # read-only one accepts strictly less capability, so a call through a
    # writable `&a` view dispatches to the const override with no soundness
    # loss. Runs and dispatches to b (2).
    assert run(
        """
        struct a { n: int32; }
        struct b extends a { m: int32; }
        fn a::poke(self: &a) -> int32 { self.n = 5; return self.n; }
        @override fn b::poke(const self: &b) -> int32 { return 2; }
        fn via(x: &a) -> int32 { return x.poke(); }
        fn main() -> int32 { let o: b = { n = 0, m = 0 }; return via(o); }
        """
    ) == 2


def test_shadowing_a_fat_parameter_drops_its_dispatch_table():
    # A local shadowing a fat-view parameter is fresh storage with no view: it
    # must NOT keep the parameter's dispatch table, or a method call on the
    # local would index the shadowed object's (larger) table -- reading past
    # the new object. The shadow is a plain `a`, so `.kind()` binds to a::kind
    # (1); before the fix the stale `b` table dispatched to b::kind (2).
    assert run(
        """
        struct a { n: int32; }
        struct b extends a { m: int32; }
        fn a::kind(const self: &a) -> int32 { return 1; }
        @override fn b::kind(const self: &b) -> int32 { return 2; }
        fn f(const p: &a) -> int32 {
            let p: a = { n = 0 };   // shadows the fat parameter
            return p.kind();
        }
        fn main() -> int32 {
            let obj: b = { n = 0, m = 0 };
            return f(obj);
        }
        """
    ) == 1


def test_generic_method_dispatch_through_a_base_view_is_rejected():
    # A method-owned generic override cannot occupy one vtable slot (a slot per
    # instantiation of its type parameter would be needed). Dispatching one
    # through a base view is a clean error -- not a KeyError crash in slot
    # construction (the review's finding), nor a silent slice to the base.
    with pytest.raises(
        LangError,
        match=r"cannot dispatch the generic method 'a::pick' through a base view",
    ):
        compile_ir(
            """
            struct a { n: int32; }
            struct b extends a { m: int32; }
            fn a::pick<T>(const self: &a, x: T) -> int32 { return 1; }
            @override fn b::pick<T>(const self: &b, x: T) -> int32 { return 2; }
            fn via(const x: &a) -> int32 { return x.pick(5); }
            fn main() -> int32 { let o: b = { n = 0, m = 0 }; return via(o); }
            """
        )


def test_generic_method_override_on_a_concrete_receiver_still_resolves(capfd):
    # The same generic override is a valid STATIC shadow: called on a concrete
    # derived receiver (no dynamic dispatch) it resolves to the derived member
    # by hop, exactly as before -- only the dynamic path is gated.
    assert run(
        """
        import "std/io";
        struct a { n: int32; }
        struct b extends a { m: int32; }
        fn a::pick<T>(const self: &a, x: T) -> int32 { return 1; }
        @override fn b::pick<T>(const self: &b, x: T) -> int32 { return 2; }
        fn main() -> int32 {
            let o: b = { n = 0, m = 0 };
            println(f"{o.pick(5)}");   // concrete receiver -> static -> 2
            return 0;
        }
        """
    ) == 0
    assert capfd.readouterr().out == "2\n"


def test_reference_return_forwards_the_view_and_dispatches():
    # SIE-183: a `-> &a` return of a fat base is the two-word {address, table}
    # view, so a forwarded reference keeps its dispatch table -- the review's
    # relay() case now dispatches the runtime override (2) instead of being
    # rejected (the round-2 stopgap) or slicing to the base (1).
    assert run(
        """
        struct a { n: int32; }
        struct b extends a { m: int32; }
        fn a::kind(const self: &a) -> int32 { return 1; }
        @override fn b::kind(const self: &b) -> int32 { return 2; }
        fn relay(x: &a) -> &a { return x; }
        fn via(x: &a) -> int32 { return relay(x).kind(); }
        fn main() -> int32 {
            let obj: b = { n = 0, m = 0 };
            return via(obj);
        }
        """
    ) == 2


def test_reference_return_of_an_empty_table_fat_base_is_allowed():
    # A base that is fat (extended) but has NO overridden methods carries only a
    # null table word, so a reference return loses nothing and stays legal --
    # the accessor pattern the stdlib `slice` accessors rely on. `a` is fat
    # (b extends it) yet has an empty table.
    ir = compile_ir(
        """
        struct a { n: int32; }
        struct b extends a { m: int32; }
        fn firstref(x: &a, y: &a) -> &a { return x; }
        fn main() -> int32 {
            let p: a = { n = 3 };
            let q: a = { n = 4 };
            return firstref(p, q).n;
        }
        """
    )
    assert '@"firstref"' in ir  # compiled, not rejected


def test_reference_return_of_a_thin_base_is_allowed():
    # A reference to an un-extended (thin) struct is a plain pointer with no
    # table at all, so a reference return is unaffected regardless of the
    # struct's own methods.
    ir = compile_ir(
        """
        struct s { x: int32; }
        fn firstref(a: &s, b: &s) -> &s { return a; }
        fn main() -> int32 {
            let p: s = { x = 7 };
            let q: s = { x = 8 };
            return firstref(p, q).x;
        }
        """
    )
    assert '@"firstref"' in ir


def test_generic_reference_return_instantiated_fat_dispatches():
    # SIE-183: the generic counterpart -- `firstref<T> -> &T` instantiated at
    # a fat base widens its return per instance and forwards the view: a
    # derived argument's table survives the hop, so the returned reference
    # dispatches the override (2); the base argument's dispatches the base
    # (1). A thin instantiation (below) is untouched.
    assert run(
        """
        struct a { n: int32; }
        struct b extends a { m: int32; }
        fn a::kind(const self: &a) -> int32 { return 1; }
        @override fn b::kind(const self: &b) -> int32 { return 2; }
        fn firstref<T>(x: &T, y: &T) -> &T { return x; }
        fn main() -> int32 {
            let p: b = { n = 0, m = 0 };
            let q: a = { n = 0 };
            if (firstref<a>(q, q).kind() != 1) { return 10; }
            return firstref<a>(p, q).kind();
        }
        """
    ) == 2


def test_generic_reference_return_of_a_thin_instance_is_allowed():
    # The counterpart: the same generic reference-return template instantiated
    # at a thin type (int32) compiles -- the gate is per-instance, not on the
    # template.
    ir = compile_ir(
        """
        fn firstref<T>(x: &T, y: &T) -> &T { return x; }
        fn main() -> int32 {
            let p: int32 = 7;
            let q: int32 = 8;
            return firstref<int32>(p, q);
        }
        """
    )
    assert '@"firstref' in ir


# --- S2.8: non-receiver parameter ABI + precise generic gate ------------------
#
# The override compatibility check covers EVERY parameter, not just the
# receiver and return: a shared slot means the slot's indirect call and the
# stored thunk must agree on each argument's ABI. And the method-generic
# dispatch gate is overload-precise, distinguishing method-owned type
# parameters from a struct's and never rejecting a non-generic sibling
# (SIE-101 review round 3).


def test_override_widening_a_non_receiver_const_reference_is_rejected():
    # A read-only `const p: &payload` base parameter overridden by a writable
    # `p: &payload`: a call dispatched through a base view would mutate the
    # caller's payload through the base's const promise (repro returned 198).
    with pytest.raises(
        LangError,
        match=(
            r"@override method 'b::apply' passes parameter 'p' by a writable "
            r"'&' reference where the base member it overrides passes it by a "
            r"read-only 'const &' reference"
        ),
    ):
        compile_ir(
            """
            struct payload { v: int32; }
            struct a { n: int32; }
            struct b extends a { m: int32; }
            fn a::apply(const self: &a, const p: &payload) -> int32 { return p.v; }
            @override fn b::apply(const self: &b, p: &payload) -> int32 {
                p.v = 99; return p.v;
            }
            fn main() -> int32 { return 0; }
            """
        )


def test_override_changing_a_parameter_from_value_to_reference_is_rejected():
    # A by-value `p: payload` base parameter overridden by a by-reference
    # `p: &payload`: the slot is called as `(..., payload)` but the thunk is
    # `(..., payload*)` -- incompatible LLVM ABIs, undefined behavior. Rejected.
    with pytest.raises(
        LangError,
        match=(
            r"@override method 'b::apply' passes parameter 'p' by a writable "
            r"'&' reference where the base member it overrides passes it by "
            r"value"
        ),
    ):
        compile_ir(
            """
            struct payload { v: int32; }
            struct a { n: int32; }
            struct b extends a { m: int32; }
            fn a::apply(const self: &a, p: payload) -> int32 { return p.v; }
            @override fn b::apply(const self: &b, p: &payload) -> int32 { return p.v; }
            fn main() -> int32 { return 0; }
            """
        )


def test_override_changing_a_parameter_ownership_is_rejected():
    # `own p: payload` (a consuming move) vs a by-value copy is the same LLVM
    # ABI but a different ownership contract -- dispatching the wrong one would
    # consume an argument the caller never relinquished. Rejected.
    with pytest.raises(
        LangError,
        match=(
            r"@override method 'b::apply' passes parameter 'p' by value where "
            r"the base member it overrides passes it by an owning 'own' value"
        ),
    ):
        compile_ir(
            """
            struct payload { v: int32; }
            struct a { n: int32; }
            struct b extends a { m: int32; }
            fn a::apply(const self: &a, own p: payload) -> int32 { return p.v; }
            @override fn b::apply(const self: &b, p: payload) -> int32 { return p.v; }
            fn main() -> int32 { return 0; }
            """
        )


def test_override_adding_nonnull_to_a_parameter_is_rejected():
    # An override may not mark a parameter @nonnull where the base accepts null:
    # it would assume a guarantee the base -- and therefore the caller -- never
    # makes.
    with pytest.raises(
        LangError,
        match=(
            r"@override method 'b::apply' marks parameter 'p' @nonnull where "
            r"the base member it overrides accepts null"
        ),
    ):
        compile_ir(
            """
            struct payload { v: int32; }
            struct a { n: int32; }
            struct b extends a { m: int32; }
            fn a::apply(const self: &a, p: payload*) -> int32 { return 0; }
            @override fn b::apply(const self: &b, @nonnull p: payload*) -> int32 {
                return 0;
            }
            fn main() -> int32 { return 0; }
            """
        )


def test_override_may_narrow_a_non_receiver_mutable_reference_to_const():
    # The safe direction is allowed for non-receiver parameters too: a writable
    # `&payload` base parameter narrowed to a read-only `const &payload` in the
    # override is ABI-identical (same pointer) and promises less, so it
    # dispatches (to b, returning 2).
    assert run(
        """
        struct payload { v: int32; }
        struct a { n: int32; }
        struct b extends a { m: int32; }
        fn a::apply(const self: &a, p: &payload) -> int32 { return 1; }
        @override fn b::apply(const self: &b, const p: &payload) -> int32 { return 2; }
        fn via(const x: &a, p: &payload) -> int32 { return x.apply(p); }
        fn main() -> int32 {
            let o: b = { n = 0, m = 0 };
            let pl: payload = { v = 0 };
            return via(o, pl);
        }
        """
    ) == 2


def test_non_generic_sibling_of_a_generic_override_dispatches_directly():
    # A method-owned generic override (`pick<T>(x)`) must not make an unrelated,
    # never-overridden no-arg `pick()` sibling reject through a base view. The
    # gate is overload-precise: `pick()` resolves to its own concrete member and
    # is a plain direct call, returning 10.
    assert run(
        """
        struct a { n: int32; }
        struct b extends a { m: int32; }
        fn a::pick(const self: &a) -> int32 { return 10; }
        fn a::pick<T>(const self: &a, x: T) -> int32 { return 1; }
        @override fn b::pick<T>(const self: &b, x: T) -> int32 { return 2; }
        fn via(const x: &a) -> int32 { return x.pick(); }
        fn main() -> int32 {
            let o: b = { n = 0, m = 0 };
            return via(o);
        }
        """
    ) == 10


def test_struct_generic_override_dispatches_through_a_base_view():
    # A struct-generic override (`gb<T>::kind` over `ga<T>::kind`) declares no
    # method-OWNED type parameter -- only the struct's -- so it dispatches like
    # any concrete override: each concrete instance has its own table with
    # concrete slot types. A `&ga<int32>` view of a `gb<int32>` reaches the
    # derived override, returning 2. (Before the fix the gate conflated the
    # struct parameter with a method-owned one and rejected the call.)
    assert run(
        """
        struct ga<T> { x: T; }
        struct gb<T> extends ga<T> { y: T; }
        fn ga<T>::kind(const self: &ga<T>) -> int32 { return 1; }
        @override fn gb<T>::kind(const self: &gb<T>) -> int32 { return 2; }
        fn via(const x: &ga<int32>) -> int32 { return x.kind(); }
        fn main() -> int32 {
            let o: gb<int32> = { x = 0, y = 0 };
            return via(o);
        }
        """
    ) == 2


def test_struct_generic_override_returning_the_struct_parameter_dispatches():
    # A struct-generic override whose RETURN names the struct parameter
    # (`-> T`, unresolved at the pre-body validation pass): the override and its
    # base clone both spell the return in the derived struct's own parameter
    # name, so the ABI comparison matches on the raw spelling and the override
    # is accepted and dispatches. `via` reads the derived override's value (20).
    assert run(
        """
        struct ga<T> { x: T; }
        struct gb<T> extends ga<T> { y: T; }
        fn ga<T>::first(const self: &ga<T>) -> T { return self.x; }
        @override fn gb<T>::first(const self: &gb<T>) -> T { return self.y; }
        fn via(const x: &ga<int32>) -> int32 { return x.first(); }
        fn main() -> int32 {
            let o: gb<int32> = { x = 10, y = 20 };
            return via(o);
        }
        """
    ) == 20


def test_struct_generic_dispatch_reads_the_derived_object_not_a_slice(capfd):
    # The receiver of a dispatched struct-generic override must reach the ACTUAL
    # derived object, not a base-sized prefix copy: `gb<int32>::get` reads the
    # derived-only field `y` (offset past the `ga` prefix). Through a `&ga`
    # view, sharing the caller's storage (not spilling a slice) is what lets
    # the read land on the real `y` (20). A slice would read past the copy.
    assert run(
        """
        struct ga<T> { x: T; }
        struct gb<T> extends ga<T> { y: T; }
        fn ga<T>::get(const self: &ga<T>) -> int32 { return 1; }
        @override fn gb<T>::get(const self: &gb<T>) -> int32 { return self.y; }
        fn via(const x: &ga<int32>) -> int32 { return x.get(); }
        fn main() -> int32 {
            let o: gb<int32> = { x = 10, y = 20 };
            return via(o);
        }
        """
    ) == 20


def test_struct_generic_override_with_a_mismatched_return_is_rejected():
    # The counterpart guard: a struct-generic override that returns a DIFFERENT
    # type than the base member it overrides is still rejected -- the raw-spelling
    # fallback compares `int32` against `T` and finds them unequal.
    with pytest.raises(
        LangError,
        match=r"@override method 'gb::first' returns .* but the base member",
    ):
        compile_ir(
            """
            struct ga<T> { x: T; }
            struct gb<T> extends ga<T> { y: T; }
            fn ga<T>::first(const self: &ga<T>) -> T { return self.x; }
            @override fn gb<T>::first(const self: &gb<T>) -> int32 { return 1; }
            fn main() -> int32 { return 0; }
            """
        )


def test_shadowing_let_restores_the_outer_dispatch_table_after_the_block():
    # A `let` shadowing a fat reference parameter drops the shadowed name's
    # dispatch table for the block's duration (the inner local is a plain,
    # statically-dispatched value). The OUTER parameter's table must be
    # restored on scope exit, or a dynamic-dispatch call on it after the block
    # would fall back to a static (base) call. Through a `&a` view of a `b`,
    # the post-block `p.kind()` must still reach the derived override (2).
    assert run(
        """
        struct a { n: int32; }
        struct b extends a { m: int32; }
        fn a::kind(const self: &a) -> int32 { return 1; }
        @override fn b::kind(const self: &b) -> int32 { return 2; }
        fn via(const p: &a) -> int32 {
            { let p: a = { n = 0 }; let inner = p.kind(); }
            return p.kind();
        }
        fn main() -> int32 {
            let o: b = { n = 0, m = 0 };
            return via(o);
        }
        """
    ) == 2


def test_renamed_generic_struct_qualifier_parameter_dispatches():
    # A generic qualifier parameter is POSITIONAL: `fn ga<X>::kind` names the
    # struct parameter `X`, not the declaration's `T`. A struct-generic override
    # spelled with a different name (`gb<Y>::kind`) declares no method-OWNED
    # type parameter, so it dispatches like any concrete override -- a `&ga<int32>`
    # view of a `gb<int32>` reaches the derived member (2). (Before the fix the
    # name-equality classification misread `X`/`Y` as method-owned and rejected
    # the base-view call with "declares its own type parameter".)
    assert run(
        """
        struct ga<T> { x: T; }
        struct gb<T> extends ga<T> { y: T; }
        fn ga<X>::kind(const self: &ga<X>) -> int32 { return 1; }
        @override fn gb<Y>::kind(const self: &gb<Y>) -> int32 { return 2; }
        fn via(const x: &ga<int32>) -> int32 { return x.kind(); }
        fn main() -> int32 {
            let o: gb<int32> = { x = 0, y = 0 };
            return via(o);
        }
        """
    ) == 2


def test_override_adding_noalias_to_a_parameter_is_rejected():
    # An override may not mark a parameter @noalias where the base does not: the
    # override body would assume non-aliasing, but a call dispatched through the
    # base signature -- which permits aliasing -- may pass aliasing pointers,
    # making the derived LLVM `noalias` assumption unsound.
    with pytest.raises(
        LangError,
        match=(
            r"@override method 'b::apply' marks parameter 'p' @noalias where "
            r"the base member it overrides permits aliasing"
        ),
    ):
        compile_ir(
            """
            struct payload { v: int32; }
            struct a { n: int32; }
            struct b extends a { m: int32; }
            fn a::apply(const self: &a, p: payload*) -> int32 { return 0; }
            @override fn b::apply(const self: &b, @noalias p: payload*) -> int32 {
                return 0;
            }
            fn main() -> int32 { return 0; }
            """
        )


def test_overloaded_call_result_argument_keeps_its_derived_table():
    # A fat argument's table word is sourced from the type the argument
    # EVALUATED to, not re-probed from the AST: an OVERLOADED function's call
    # result (a shape the old by-name probe bailed on) forming a `&a` view
    # keeps the derived `b` table, so the callee's dispatch reaches b::who (2)
    # -- the object was always a full `b`; only the table used to slice.
    assert run(
        """
        struct a { n: int32; }
        struct b extends a { m: int32; }
        fn a::who(const self: &a) -> int32 { return 1; }
        @override fn b::who(const self: &b) -> int32 { return 2; }
        fn make(x: int32) -> b { let r: b = { n = x, m = x }; return r; }
        fn make(x: float64) -> a { let r: a = { n = 0 }; return r; }
        fn via(const it: &a) -> int32 { return it.who(); }
        fn main() -> int32 { return via(make(1)); }
        """
    ) == 2


def test_method_call_result_argument_keeps_its_derived_table():
    # Same rvalue-shape coverage for a METHOD call's result: `fc.make()`
    # returns a derived `b`, and the view formed from it dispatches to the
    # derived override (2), not the sliced base (1).
    assert run(
        """
        struct a { n: int32; }
        struct b extends a { m: int32; }
        struct factory { seed: int32; }
        fn a::who(const self: &a) -> int32 { return 1; }
        @override fn b::who(const self: &b) -> int32 { return 2; }
        fn factory::make(const self: &factory) -> b {
            let r: b = { n = self.seed, m = 0 }; return r;
        }
        fn via(const it: &a) -> int32 { return it.who(); }
        fn main() -> int32 {
            let fc: factory = { seed = 0 };
            return via(fc.make());
        }
        """
    ) == 2


def test_ternary_argument_keeps_its_derived_table():
    # And for a ternary of two derived values: either arm is a full `b`, so
    # the view's table must be b's (2).
    assert run(
        """
        struct a { n: int32; }
        struct b extends a { m: int32; }
        fn a::who(const self: &a) -> int32 { return 1; }
        @override fn b::who(const self: &b) -> int32 { return 2; }
        fn via(const it: &a) -> int32 { return it.who(); }
        fn main() -> int32 {
            let x: b = { n = 0, m = 0 };
            let y: b = { n = 1, m = 1 };
            let cond: bool = true;
            return via(cond ? x : y);
        }
        """
    ) == 2


def test_as_upcast_argument_carries_the_base_table():
    # The guard for the other direction: `x as a` BUILDS a genuine base value
    # (intended, explicit data slicing), so the view formed from it carries the
    # BASE table and dispatches a::who (1). Sourcing the table from the
    # evaluated type keeps this correct for free -- the cast's result type IS
    # the base.
    assert run(
        """
        struct a { n: int32; }
        struct b extends a { m: int32; }
        fn a::who(const self: &a) -> int32 { return 1; }
        @override fn b::who(const self: &b) -> int32 { return 2; }
        fn via(const it: &a) -> int32 { return it.who(); }
        fn main() -> int32 {
            let x: b = { n = 0, m = 0 };
            return via(x as a);
        }
        """
    ) == 1


def test_writable_reference_view_of_a_derived_lvalue_dispatches_and_writes():
    # The writable `&a` (mut) marshal path: a derived lvalue lends its actual
    # storage as the view, so the callee's dispatch reaches b::who (2) AND its
    # write through the reference lands in the caller's derived object.
    assert run(
        """
        struct a { n: int32; }
        struct b extends a { m: int32; }
        fn a::who(const self: &a) -> int32 { return 1; }
        @override fn b::who(const self: &b) -> int32 { return 2; }
        fn via(it: &a) -> int32 { it.n = 9; return it.who(); }
        fn main() -> int32 {
            let x: b = { n = 0, m = 0 };
            let r = via(x);
            if (x.n != 9) { return 100; }
            return r;
        }
        """
    ) == 2


def test_derived_argument_at_a_non_receiver_fat_position_via_inheritance():
    # The overload/inherited call path (gen_generic_call): the receiver
    # inherits `pick` from the base, and the derived `other` argument sits at
    # a NON-receiver fat position -- the derived->base view forms there too
    # (any-position reference conversion), with the derived table (2). Before
    # the fix this path never upcast a non-receiver fat argument: it spilled a
    # base-coerced copy carrying the base table (a silent slice).
    assert run(
        """
        struct a { n: int32; }
        struct b extends a { m: int32; }
        fn a::who(const self: &a) -> int32 { return 1; }
        @override fn b::who(const self: &b) -> int32 { return 2; }
        fn a::pick(const self: &a, const other: &a) -> int32 {
            return other.who();
        }
        fn main() -> int32 {
            let d1: b = { n = 0, m = 0 };
            let d2: b = { n = 0, m = 0 };
            return d1.pick(d2);
        }
        """
    ) == 2


def test_constructor_call_argument_keeps_its_derived_table():
    # Constructor sugar's result is a fresh derived rvalue: `via(b())` forms
    # the view from a full `b`, so dispatch reaches the override (2).
    assert run(
        """
        struct a { n: int32; }
        struct b extends a { m: int32; }
        fn a::constructor(self: &a) { self.n = 0; }
        fn b::constructor(self: &b) { self.n = 0; self.m = 0; }
        fn a::who(const self: &a) -> int32 { return 1; }
        @override fn b::who(const self: &b) -> int32 { return 2; }
        fn via(const it: &a) -> int32 { return it.who(); }
        fn main() -> int32 { return via(b()); }
        """
    ) == 2


def test_block_expression_argument_keeps_its_derived_table():
    # A block expression's emitted derived value is one more rvalue shape the
    # old AST probe could not type: the view formed from it must carry the
    # derived table (2).
    assert run(
        """
        struct a { n: int32; }
        struct b extends a { m: int32; }
        fn a::who(const self: &a) -> int32 { return 1; }
        @override fn b::who(const self: &b) -> int32 { return 2; }
        fn via(const it: &a) -> int32 { return it.who(); }
        fn main() -> int32 {
            return via({ let r: b = { n = 0, m = 0 }; emit r; });
        }
        """
    ) == 2


def test_three_level_rvalue_dispatches_through_top_and_mid_chain_views():
    # A leaf-typed rvalue (`make()` returning c) forms a view at ANY hop of
    # its chain -- `&a` and `&b` alike -- and both dispatch to the leaf
    # override (3): the table travels with the runtime type, not the view's
    # declared hop.
    assert run(
        """
        struct a { n: int32; }
        struct b extends a { m: int32; }
        struct c extends b { k: int32; }
        fn a::who(const self: &a) -> int32 { return 1; }
        @override fn b::who(const self: &b) -> int32 { return 2; }
        @override fn c::who(const self: &c) -> int32 { return 3; }
        fn make() -> c { let r: c = { n = 0, m = 0, k = 0 }; return r; }
        fn via_a(const it: &a) -> int32 { return it.who(); }
        fn via_b(const it: &b) -> int32 { return it.who(); }
        fn main() -> int32 {
            if (via_a(make()) != 3) { return 10; }
            if (via_b(make()) != 3) { return 20; }
            return 3;
        }
        """
    ) == 3


def test_struct_generic_call_result_argument_keeps_its_derived_table():
    # The generic-instance flavor: a call returning `gb<int32>` forms a
    # `&ga<int32>` view carrying the derived instance's table (2).
    assert run(
        """
        struct ga<T> { x: T; }
        struct gb<T> extends ga<T> { y: T; }
        fn ga<T>::kind(const self: &ga<T>) -> int32 { return 1; }
        @override fn gb<T>::kind(const self: &gb<T>) -> int32 { return 2; }
        fn make() -> gb<int32> { let r: gb<int32> = { x = 0, y = 0 }; return r; }
        fn via(const x: &ga<int32>) -> int32 { return x.kind(); }
        fn main() -> int32 { return via(make()); }
        """
    ) == 2


def test_decayed_pointer_argument_carries_its_static_type_table():
    # The raw-pointer contract: tables live in REFERENCES, never objects, so
    # an `a*` -- even one actually aimed at a derived `b` -- has genuinely
    # erased the runtime type. The view formed from `*p` carries `a`'s table
    # and binds the base (1). Static one-word pointers stay static by design.
    assert run(
        """
        struct a { n: int32; }
        struct b extends a { m: int32; }
        fn a::who(const self: &a) -> int32 { return 1; }
        @override fn b::who(const self: &b) -> int32 { return 2; }
        fn via(const it: &a) -> int32 { return it.who(); }
        fn main() -> int32 {
            let x: b = { n = 0, m = 0 };
            let p: a* = &x as a*;
            if (p == null) { return 100; }
            return via(*p);
        }
        """
    ) == 1


def test_destructor_owning_rvalue_view_dispatches_and_drops_once():
    # A destructor-owning derived rvalue lent as a view: the callee's dispatch
    # reaches the override AND the spilled temporary drops exactly once, after
    # the call (statement end) -- the upcast spill must not lose or double the
    # own obligation.
    assert run(
        """
        struct a { n: int32; }
        struct b extends a { m: int32; }
        fn a::who(const self: &a) -> int32 { return 1; }
        @override fn b::who(const self: &b) -> int32 { return 2; }
        @static let drops: int32 = 0;
        fn b::destructor(self: &b) { drops = drops + 1; }
        fn make() -> own b { let r: b = { n = 0, m = 0 }; return move(r); }
        fn via(const it: &a) -> int32 { return it.who(); }
        fn main() -> int32 {
            let r = via(make());
            if (drops != 1) { return 50 + drops; }
            return r;
        }
        """
    ) == 2


def test_view_re_lent_at_a_non_receiver_fat_position_propagates_its_table():
    # A fat view parameter re-lent at a NON-receiver fat position forwards its
    # runtime table (the propagation is per-argument, not receiver-only), so
    # the inner callee's dispatch still reaches the derived override (2).
    assert run(
        """
        struct a { n: int32; }
        struct b extends a { m: int32; }
        fn a::who(const self: &a) -> int32 { return 1; }
        @override fn b::who(const self: &b) -> int32 { return 2; }
        fn inner(x: int32, const other: &a) -> int32 { return other.who(); }
        fn outer(const p: &a) -> int32 { return inner(0, p); }
        fn main() -> int32 {
            let o: b = { n = 0, m = 0 };
            return outer(o);
        }
        """
    ) == 2


def test_ternary_of_two_views_is_a_value_read_carrying_the_base_table():
    # `cond ? l : r` over two fat view parameters is an ordinary a-typed
    # EXPRESSION, not a re-lend: per the copy-on-read ruling (prefix
    # extraction), reading a value out of a view yields a genuine base value
    # carrying no view, so the re-formed view dispatches the base (1) even
    # though `l` came in viewing a derived `b`. (Table propagation is
    # per-argument and follows a BARE view name only; if ternary re-lending
    # is ever wanted, this is the test to revisit -- it asserts today's
    # semantics as implied by the ruling, not a separate explicit ruling.)
    assert run(
        """
        struct a { n: int32; }
        struct b extends a { m: int32; }
        fn a::who(const self: &a) -> int32 { return 1; }
        @override fn b::who(const self: &b) -> int32 { return 2; }
        fn via(const it: &a) -> int32 { return it.who(); }
        fn pick(cond: bool, const l: &a, const r: &a) -> int32 {
            return via(cond ? l : r);
        }
        fn main() -> int32 {
            let x: b = { n = 0, m = 0 };
            let y: a = { n = 5 };
            return pick(true, x, y);
        }
        """
    ) == 1


def test_overload_introduced_mid_chain_dispatches_through_its_own_views():
    # An overload family may be INTRODUCED partway down the chain: `who()`
    # roots at `a`, `who(int32)` roots at `b`. Each slot keys on its own
    # introducer, so a leaf `c` dispatches both -- `who()` through a root
    # `&a` view (3) and `who(1)` through a mid-chain `&b` view (31).
    assert run(
        """
        struct a { n: int32; }
        struct b extends a { m: int32; }
        struct c extends b { k: int32; }
        fn a::who(const self: &a) -> int32 { return 1; }
        @override fn b::who(const self: &b) -> int32 { return 2; }
        @override fn c::who(const self: &c) -> int32 { return 3; }
        fn b::who(const self: &b, x: int32) -> int32 { return 20 + x; }
        @override fn c::who(const self: &c, x: int32) -> int32 { return 30 + x; }
        fn via_a(const it: &a) -> int32 { return it.who(); }
        fn via_b(const it: &b) -> int32 { return it.who(1); }
        fn main() -> int32 {
            let leaf: c = { n = 0, m = 0, k = 0 };
            if (via_a(leaf) != 3) { return 10; }
            if (via_b(leaf) != 31) { return 20; }
            return 3;
        }
        """
    ) == 3


def test_override_may_drop_a_base_noalias_promise():
    # The safe direction of the @noalias ABI rule: the BASE promises callers
    # pass non-aliasing pointers, so an override that merely stops ASSUMING
    # the promise is sound (contrast adding @noalias, which is rejected).
    # The relaxed override compiles and dispatches (2).
    assert run(
        """
        struct payload { v: int32; }
        struct a { n: int32; }
        struct b extends a { m: int32; }
        fn a::apply(const self: &a, @noalias p: payload*) -> int32 { return 1; }
        @override fn b::apply(const self: &b, p: payload*) -> int32 { return 2; }
        fn via(const x: &a, p: payload*) -> int32 { return x.apply(p); }
        fn main() -> int32 {
            let o: b = { n = 0, m = 0 };
            let pl: payload = { v = 0 };
            return via(o, &pl);
        }
        """
    ) == 2


def test_shadowing_across_loops_breaks_and_early_returns_restores_the_table():
    # The scope-exit table restore under heavier control flow: shadows inside
    # a while body (with continue and break), a doubly-nested block shadow,
    # and a shadow followed by an early return -- after every exit shape the
    # OUTER fat parameter dispatches its runtime type again (2), and every
    # shadowed inner `p` stays static (1).
    assert run(
        """
        struct a { n: int32; }
        struct b extends a { m: int32; }
        fn a::who(const self: &a) -> int32 { return 1; }
        @override fn b::who(const self: &b) -> int32 { return 2; }
        fn via(const p: &a, early: bool) -> int32 {
            let i: int32 = 0;
            while (i < 3) {
                let p: a = { n = i };
                if (i == 1) { i = i + 1; continue; }
                { let q = p.who(); if (q != 1) { return 90; } }
                if (i == 2) { break; }
                i = i + 1;
            }
            if (early) { { let p: a = { n = 7 }; } return p.who(); }
            { { let p: a = { n = 9 }; let inner = p.who(); } }
            return p.who();
        }
        fn main() -> int32 {
            let o: b = { n = 0, m = 0 };
            if (via(o, true) != 2) { return 70; }
            return via(o, false);
        }
        """
    ) == 2


def test_inherited_generic_method_takes_a_derived_non_receiver_view():
    # An inherited method-owned GENERIC (`pick<T>`, never overridden, so a
    # legal static call on a derived receiver) with a fat non-receiver
    # parameter: the derived `other` forms its view on the instantiate path
    # and the body's re-dispatch reaches the override (2).
    assert run(
        """
        struct a { n: int32; }
        struct b extends a { m: int32; }
        fn a::who(const self: &a) -> int32 { return 1; }
        @override fn b::who(const self: &b) -> int32 { return 2; }
        fn a::pick<T>(const self: &a, const other: &a, x: T) -> int32 {
            return other.who();
        }
        fn main() -> int32 {
            let d1: b = { n = 0, m = 0 };
            let d2: b = { n = 0, m = 0 };
            return d1.pick(d2, 42);
        }
        """
    ) == 2


def test_writable_and_const_views_of_one_object_in_one_call():
    # One object lent twice in a single call -- writable `&a` and `const &a`.
    # Both views carry the derived table (2 + 2 = 4) and the write through
    # the writable one lands in the caller's object.
    assert run(
        """
        struct a { n: int32; }
        struct b extends a { m: int32; }
        fn a::who(const self: &a) -> int32 { return 1; }
        @override fn b::who(const self: &b) -> int32 { return 2; }
        fn both(w: &a, const r: &a) -> int32 { w.n = 9; return w.who() + r.who(); }
        fn main() -> int32 {
            let x: b = { n = 0, m = 0 };
            let s = both(x, x);
            if (x.n != 9) { return 80; }
            return s;
        }
        """
    ) == 4


def test_as_base_stays_sliced_inside_ternaries_and_generic_wrappers():
    # Explicit `as base` slicing composes: inside ternary arms and through a
    # generic identity wrapper the result is a genuine `a` (base table, 1),
    # while the same wrapper handed the derived value returns a full `b`
    # whose view dispatches the override (2).
    assert run(
        """
        struct a { n: int32; }
        struct b extends a { m: int32; }
        fn a::who(const self: &a) -> int32 { return 1; }
        @override fn b::who(const self: &b) -> int32 { return 2; }
        fn via(const it: &a) -> int32 { return it.who(); }
        fn wrap<T>(v: T) -> T { return v; }
        fn main() -> int32 {
            let x: b = { n = 0, m = 0 };
            let y: b = { n = 1, m = 1 };
            let cond: bool = true;
            if (via(cond ? (x as a) : (y as a)) != 1) { return 10; }
            if (via(wrap(x as a)) != 1) { return 20; }
            if (via(wrap(x)) != 2) { return 30; }
            return 4;
        }
        """
    ) == 4


def test_dispatch_across_a_multi_module_hierarchy(tmp_path):
    # The hierarchy, the override, the view-taking function, and the dispatch
    # site each live in a DIFFERENT module: the merged import closure sees the
    # extension, so `&a` is fat program-wide (fatness never differs within one
    # linked program) and `via` -- whose own module never imports the derived
    # type -- still dispatches the runtime override (2).
    (tmp_path / "base.mc").write_text(
        "struct a { n: int32; }\n"
        "fn a::who(const self: &a) -> int32 { return 1; }\n"
    )
    (tmp_path / "derived.mc").write_text(
        'import "base";\n'
        "struct b extends a { m: int32; }\n"
        "@override fn b::who(const self: &b) -> int32 { return 2; }\n"
    )
    (tmp_path / "lib.mc").write_text(
        'import "base";\n'
        "fn via(const it: &a) -> int32 { return it.who(); }\n"
    )
    main = tmp_path / "prog.mc"
    main.write_text(
        'import "base";\n'
        'import "derived";\n'
        'import "lib";\n'
        "fn make() -> b { let r: b = { n = 0, m = 0 }; return r; }\n"
        "fn main() -> int32 { return via(make()); }\n"
    )
    assert run_path(main) == 2


def test_mci_stub_carries_the_override_marker(tmp_path):
    # The stub re-emits @override on a base-chain override's prototype: the
    # marker IS the dispatch relationship, so a consumer's closure keeps the
    # family. (Before the fix the stub dropped it, and every dispatch site
    # compiled against the stub silently bound the base -- a behavioral
    # slice across the interface boundary.)
    lib = tmp_path / "geo.mc"
    lib.write_text(
        "struct a { n: int32; }\n"
        "struct b extends a { m: int32; }\n"
        "fn a::greet(const self: &a) -> int32 { return self.n; }\n"
        "@override fn b::greet(const self: &b) -> int32 { return self.m; }\n"
    )
    out = tmp_path / "geo.mci"
    assert emit_interface(lib, (tmp_path,), None, {}, out) == 0
    stub = out.read_text()
    assert "fn a::greet(const self: &a) -> int32;" in stub
    assert "@override fn b::greet(const self: &b) -> int32;" in stub


def test_consumer_against_an_override_stub_dispatches_indirectly(tmp_path):
    # A view-taking function compiled against the stub alone: the @override
    # prototype keeps (a, greet) a dispatch family, so `it.greet()` loads the
    # table slot and calls indirectly -- never a direct call to the base
    # member.
    (tmp_path / "geo.mci").write_text(
        "struct a { n: int32; }\n"
        "struct b extends a { m: int32; }\n"
        "fn a::greet(const self: &a) -> int32;\n"
        "@override fn b::greet(const self: &b) -> int32;\n"
    )
    main = tmp_path / "main.mc"
    main.write_text(
        'import "geo";\n'
        "fn via(const it: &a) -> int32 { return it.greet(); }\n"
        "fn main() -> int32 { let x: a = { n = 0 }; return via(x); }\n"
    )
    ir = str(compile_to_ir(main, (tmp_path,)))
    via = ir.split('define i32 @"via"')[1].split("\n}")[0]
    assert 'call i32 @"a::greet"' not in via  # not devirtualized to the base
    assert "load i8*, i8**" in via            # the table slot load


def test_override_proto_shadowing_nothing_is_rejected():
    # An @override prototype is only the interface spelling of a base-chain
    # override; one that shadows no inherited member is the same typo error
    # as its bodied counterpart.
    with pytest.raises(
        LangError,
        match=r"@override method 'b::who' overrides no inherited base member",
    ):
        compile_ir(
            """
            struct b { n: int32; }
            @override fn b::who(const self: &b) -> int32;
            fn main() -> int32 { return 0; }
            """
        )


def test_chained_reference_returns_forward_the_view():
    # `return relay(x);` forwards the INNER call's returned view -- the table
    # channel -- so a two-hop relay still dispatches the runtime type (2).
    assert run(
        """
        struct a { n: int32; }
        struct b extends a { m: int32; }
        fn a::kind(const self: &a) -> int32 { return 1; }
        @override fn b::kind(const self: &b) -> int32 { return 2; }
        fn relay(x: &a) -> &a { return x; }
        fn relay2(x: &a) -> &a { return relay(x); }
        fn main() -> int32 {
            let obj: b = { n = 0, m = 0 };
            return relay2(obj).kind();
        }
        """
    ) == 2


def test_fat_call_result_re_lent_as_a_const_view_argument_dispatches():
    # A fat `-> &a` result re-lent as a `const &a` argument shares its storage
    # AND forwards its table, so the callee's re-dispatch reaches the runtime
    # override (2) -- on the direct path and, with an unrelated overload
    # sibling forcing the overload-set path, on the generic path too.
    assert run(
        """
        struct a { n: int32; }
        struct b extends a { m: int32; }
        fn a::kind(const self: &a) -> int32 { return 1; }
        @override fn b::kind(const self: &b) -> int32 { return 2; }
        fn relay(x: &a) -> &a { return x; }
        fn look(const it: &a) -> int32 { return it.kind(); }
        fn peek(const it: &a) -> int32 { return it.kind(); }
        fn peek(x: int32) -> int32 { return x; }
        fn main() -> int32 {
            let obj: b = { n = 0, m = 0 };
            if (look(relay(obj)) != 2) { return 10; }
            return peek(relay(obj));
        }
        """
    ) == 2


def test_fat_call_result_re_lent_as_a_writable_view_dispatches_and_writes():
    # The writable re-lend: the fat result's storage re-lends as `&a`, its
    # table riding along -- the callee dispatches the override (2) and its
    # write lands in the caller's derived object.
    assert run(
        """
        struct a { n: int32; }
        struct b extends a { m: int32; }
        fn a::kind(const self: &a) -> int32 { return 1; }
        @override fn b::kind(const self: &b) -> int32 { return 2; }
        fn relay(x: &a) -> &a { return x; }
        fn poke(it: &a) -> int32 { it.n = 9; return it.kind(); }
        fn main() -> int32 {
            let obj: b = { n = 0, m = 0 };
            let r = poke(relay(obj));
            if (obj.n != 9) { return 80; }
            return r;
        }
        """
    ) == 2


def test_assignment_through_a_fat_reference_result_writes_the_caller_storage():
    # The lvalue surfaces consume the unpacked object pointer exactly as a
    # thin mut return's: assignment through the fat result lands in the
    # caller's derived object.
    assert run(
        """
        struct a { n: int32; }
        struct b extends a { m: int32; }
        fn a::kind(const self: &a) -> int32 { return 1; }
        @override fn b::kind(const self: &b) -> int32 { return 2; }
        fn relay(x: &a) -> &a { return x; }
        fn main() -> int32 {
            let obj: b = { n = 0, m = 0 };
            relay(obj).n = 42;
            return obj.n;
        }
        """
    ) == 42


def test_field_carved_out_of_a_fat_result_keeps_its_own_static_table():
    # A field projected OUT of a fat call result is its own exactly-typed
    # object: the returned view's runtime table must NOT transfer to it (the
    # field is a genuine `a`, not a view of the derived receiver), so its
    # method binds the base (1). Guards the channel's producer-shape gating.
    assert run(
        """
        struct a { n: int32; }
        struct b extends a { m: int32; }
        struct holder { pad: int32; inner: a; }
        fn a::kind(const self: &a) -> int32 { return 1; }
        @override fn b::kind(const self: &b) -> int32 { return 2; }
        fn hrelay(h: &holder) -> &holder { return h; }
        fn inner_of(h: &holder) -> &a { return h.inner; }
        fn main() -> int32 {
            let h: holder = { pad = 0, inner = { n = 0 } };
            if (inner_of(h).kind() != 1) { return 10; }
            return hrelay(h).inner.kind();
        }
        """
    ) == 1


def test_property_returning_a_fat_reference_forwards_through_the_sugar():
    # An @accessor's mut return riding the index sugar: the fat view returned
    # by `box::at` reaches the dot-call through gen_addr's accessor rewrite,
    # and the element -- exactly the base type -- binds its static table (1),
    # while a forwarded fat parameter through a plain method call keeps the
    # runtime table (2).
    assert run(
        """
        struct a { n: int32; }
        struct b extends a { m: int32; }
        struct box { item: a; }
        fn a::kind(const self: &a) -> int32 { return 1; }
        @override fn b::kind(const self: &b) -> int32 { return 2; }
        @accessor fn box::at(self: &box, i: int64) -> &a { return self.item; }
        fn box::pass(self: &box, w: &a) -> &a { return w; }
        fn main() -> int32 {
            let bx: box = { item = { n = 0 } };
            let obj: b = { n = 0, m = 0 };
            if (bx[0].kind() != 1) { return 10; }
            return bx.pass(obj).kind();
        }
        """
    ) == 2


def test_function_value_of_a_fat_signature_is_rejected():
    # A fat reference may not ride in a function-pointer type, so taking a
    # function VALUE whose inferred signature carries one -- a fat parameter
    # or a fat return -- is the same clean rejection (previously the fat
    # parameter case crashed the compiler with an LLVM type mismatch).
    for fn_line in (
        "fn takes(const it: &a) -> int32 { return it.kind(); }",
        "fn relay(x: &a) -> &a { return x; }",
    ):
        name = fn_line.split()[1].split("(")[0]
        with pytest.raises(
            LangError,
            match=(
                rf"cannot take '{name}' as a function value: its signature "
                r"carries a fat reference"
            ),
        ):
            compile_ir(
                f"""
                struct a {{ n: int32; }}
                struct b extends a {{ m: int32; }}
                fn a::kind(const self: &a) -> int32 {{ return 1; }}
                @override fn b::kind(const self: &b) -> int32 {{ return 2; }}
                {fn_line}
                fn main() -> int32 {{
                    let f = {name};
                    return 0;
                }}
                """
            )


def test_fat_reference_return_in_a_function_pointer_type_is_rejected():
    # The spelled counterpart: a `fn(...) -> &a` function-pointer TYPE where
    # `&a` is fat is rejected per use, mirroring the fat-parameter rule.
    with pytest.raises(
        LangError,
        match=(
            r"a fat reference return type \(-> &a, whose base is extended\) "
            r"may not appear in a function-pointer type yet"
        ),
    ):
        compile_ir(
            """
            struct a { n: int32; }
            struct b extends a { m: int32; }
            fn main() -> int32 {
                let f: fn(a*) -> &a = null;
                return 0;
            }
            """
        )


def test_mci_fat_return_produces_matching_fat_abi(tmp_path):
    # A stub whose own closure sees the extension declares the `-> &a` return
    # fat, so its declaration and call sites match the two-word ABI the
    # defining object was compiled with.
    (tmp_path / "geo.mci").write_text(
        "struct a { n: int32; }\n"
        "struct b extends a { m: int32; }\n"
        "fn relay(x: &a) -> &a;\n"
    )
    main = tmp_path / "main.mc"
    main.write_text(
        'import "geo";\n'
        "fn main() -> int32 { let x: a = { n = 9 }; return relay(x).n; }\n"
    )
    ir = str(compile_to_ir(main, (tmp_path,)))
    assert 'declare {%"a"*, i8*} @"relay"' in ir  # fat return proto
    assert 'call {%"a"*, i8*} @"relay"' in ir     # fat return call site


def test_mci_proto_def_fat_return_mismatch_is_a_clean_error(tmp_path):
    # A stub that did not see `a` extended declares a THIN `-> &a`; a
    # definition that DOES see it declares a fat one. One- vs two-word
    # returns are different ABIs, so the pairing is the prototype-mismatch
    # error, not a silent miscompile.
    (tmp_path / "api.mci").write_text(
        "struct a { n: int32; }\nfn relay(x: &a) -> &a;\n"
    )
    (tmp_path / "impl.mc").write_text(
        'import "api";\n'
        "struct b extends a { m: int32; }\n"
        "fn relay(x: &a) -> &a { return x; }\n"
    )
    main = tmp_path / "main.mc"
    main.write_text(
        'import "api";\nimport "impl";\nfn main() -> int32 { return 0; }'
    )
    with pytest.raises(
        LangError,
        match=r"definition of 'relay' does not match its prototype",
    ):
        compile_to_ir(main, (tmp_path,))


def test_method_returning_fat_self_forwards_the_receiver_view():
    # `fn a::me(self: &a) -> &a { return self; }` -- self is the fat receiver
    # view, so the return forwards its RUNTIME table and the chained call
    # dispatches the derived override (2).
    assert run(
        """
        struct a { n: int32; }
        struct b extends a { m: int32; }
        fn a::kind(const self: &a) -> int32 { return 1; }
        @override fn b::kind(const self: &b) -> int32 { return 2; }
        fn a::me(self: &a) -> &a { return self; }
        fn via(x: &a) -> int32 { return x.me().kind(); }
        fn main() -> int32 {
            let obj: b = { n = 0, m = 0 };
            return via(obj);
        }
        """
    ) == 2


def test_dispatched_override_with_a_fat_reference_return():
    # A fat-returning family that IS overridden: the slot's indirect call and
    # the stored thunk both carry the two-word return (same-type returns, per
    # the override return-ABI rule), and the override's forwarded view keeps
    # its runtime table -- pick dispatches to b's body (self.n = 5 lands) and
    # the returned view dispatches w's runtime type (2).
    assert run(
        """
        struct a { n: int32; }
        struct b extends a { m: int32; }
        fn a::kind(const self: &a) -> int32 { return 1; }
        @override fn b::kind(const self: &b) -> int32 { return 2; }
        fn a::pick(self: &a, w: &a) -> &a { self.n = 4; return w; }
        @override fn b::pick(self: &b, w: &a) -> &a { self.n = 5; return w; }
        fn via(x: &a, w: &a) -> int32 { return x.pick(w).kind(); }
        fn main() -> int32 {
            let recv: b = { n = 0, m = 0 };
            let arg: b = { n = 0, m = 0 };
            let r = via(recv, arg);
            if (recv.n != 5) { return 50 + recv.n; }  // b::pick ran
            return r;
        }
        """
    ) == 2


def test_return_position_reference_upcast_forms_the_view():
    # SIE-186 gap 1: a derived lvalue returns as a declared base reference --
    # the view forms at the return site, paired with the DERIVED type's
    # table, so the returned reference dispatches the runtime type (2).
    assert (
        run(
            """
            struct a { n: int32; }
            struct b extends a { m: int32; }
            fn a::kind(const self: &a) -> int32 { return 1; }
            @override fn b::kind(const self: &b) -> int32 { return 2; }
            fn first(x: &b, y: &b) -> &a { return x; }
            fn main() -> int32 {
                let u: b = { n = 0, m = 0 };
                let v: b = { n = 0, m = 0 };
                return first(u, v).kind();
            }
            """
        )
        == 2
    )


def test_return_position_upcast_writes_through_to_the_derived_object():
    # The upcast view references the derived object's base prefix in place:
    # a write through it lands in the caller's object, and the suffix
    # fields are untouched (a reference upcast never slices).
    assert (
        run(
            """
            struct a { n: int32; }
            struct b extends a { m: int32; }
            fn a::kind(const self: &a) -> int32 { return 1; }
            @override fn b::kind(const self: &b) -> int32 { return 2; }
            fn pick(x: &b) -> &a { return x; }
            fn main() -> int32 {
                let u: b = { n = 0, m = 9 };
                pick(u).n = 42;
                if (u.n != 42) { return 50; }
                if (u.m != 9) { return 51; }
                return 0;
            }
            """
        )
        == 0
    )


def test_return_position_upcast_forwards_a_view_parameters_runtime_table():
    # `return x` where x is itself a fat `&b` view: the upcast keeps the
    # PARAMETER's runtime table (here a c), not b's static one -- the same
    # forwarding rule as an exact-typed view return.
    assert (
        run(
            """
            struct a { n: int32; }
            struct b extends a { m: int32; }
            struct c extends b { k: int32; }
            fn a::kind(const self: &a) -> int32 { return 1; }
            @override fn b::kind(const self: &b) -> int32 { return 2; }
            @override fn c::kind(const self: &c) -> int32 { return 3; }
            fn relay(x: &b) -> &a { return x; }
            fn main() -> int32 {
                let obj: c = { n = 0, m = 0, k = 0 };
                return relay(obj).kind();
            }
            """
        )
        == 3
    )


def test_return_position_upcast_never_downcasts_or_crosses_hierarchies():
    # The relaxation is exact-or-declared-descendant only: a base lvalue
    # cannot return as a derived reference ...
    with pytest.raises(
        LangError, match=r"reference return: expected a b lvalue, got a"
    ):
        compile_ir(
            """
            struct a { n: int32; }
            struct b extends a { m: int32; }
            fn f(x: &a) -> &b { return x; }
            fn main() -> int32 { return 0; }
            """
        )
    # ... and an unrelated struct still errors.
    with pytest.raises(
        LangError, match=r"reference return: expected a a lvalue, got z"
    ):
        compile_ir(
            """
            struct a { n: int32; }
            struct b extends a { m: int32; }
            struct z { q: int32; }
            fn f(x: &z) -> &a { return x; }
            fn main() -> int32 { return 0; }
            """
        )


def test_value_return_of_a_derived_still_requires_the_explicit_as():
    # The asymmetry stands: only a REFERENCE upcasts at the return site. A
    # by-value `-> a` return of a b still slices and must spell the `as`.
    with pytest.raises(LangError, match=r"return value: expected a, got b"):
        compile_ir(
            """
            struct a { n: int32; }
            struct b extends a { m: int32; }
            fn f(x: &b) -> a { return x; }
            fn main() -> int32 { return 0; }
            """
        )


def test_covariant_override_return_dispatches_through_the_base_view():
    # SIE-186 gap 2: the override declares `-> &b` over a base `-> &a`
    # (spelling-level covariance). Through the base view the slot still
    # returns the base-shaped fat view -- formed by the slot thunk from the
    # override's thin `&b` result -- and the returned view dispatches the
    # runtime type (2).
    assert (
        run(
            """
            struct a { n: int32; }
            struct b extends a { m: int32; }
            fn a::kind(const self: &a) -> int32 { return 1; }
            @override fn b::kind(const self: &b) -> int32 { return 2; }
            fn a::me(self: &a) -> &a { return self; }
            @override fn b::me(self: &b) -> &b { return self; }
            fn via(x: &a) -> int32 { return x.me().kind(); }
            fn main() -> int32 {
                let obj: b = { n = 0, m = 0 };
                return via(obj);
            }
            """
        )
        == 2
    )


def test_covariant_override_narrows_at_a_static_call_site():
    # A direct call on a concrete receiver types the result as the
    # override's own spelling: the derived suffix field is reachable
    # without a cast, and the write lands in the receiver.
    assert (
        run(
            """
            struct a { n: int32; }
            struct b extends a { m: int32; }
            fn a::kind(const self: &a) -> int32 { return 1; }
            @override fn b::kind(const self: &b) -> int32 { return 2; }
            fn a::me(self: &a) -> &a { return self; }
            @override fn b::me(self: &b) -> &b { return self; }
            fn main() -> int32 {
                let obj: b = { n = 0, m = 0 };
                obj.me().m = 7;
                if (obj.m != 7) { return 60; }
                return 0;
            }
            """
        )
        == 0
    )


def test_covariant_returns_compose_down_a_three_level_chain():
    # Covariance at every hop: the leaf's thin `-> &c` widens through the
    # slot, an intermediate FAT `-> &b` passes through, and a dispatch from
    # either view level reaches the runtime type (3).
    assert (
        run(
            """
            struct a { n: int32; }
            struct b extends a { m: int32; }
            struct c extends b { k: int32; }
            fn a::kind(const self: &a) -> int32 { return 1; }
            @override fn b::kind(const self: &b) -> int32 { return 2; }
            @override fn c::kind(const self: &c) -> int32 { return 3; }
            fn a::me(self: &a) -> &a { return self; }
            @override fn b::me(self: &b) -> &b { return self; }
            @override fn c::me(self: &c) -> &c { return self; }
            fn via_a(x: &a) -> int32 { return x.me().kind(); }
            fn via_b(x: &b) -> int32 { return x.me().kind(); }
            fn main() -> int32 {
                let obj: c = { n = 0, m = 0, k = 0 };
                if (via_a(obj) != 3) { return 10; }
                if (via_b(obj) != 3) { return 20; }
                return 0;
            }
            """
        )
        == 0
    )


def test_covariant_return_over_an_unrelated_reference_hierarchy():
    # The covariance is between the RETURN types' own extends chain (y
    # extends x), independent of the receiver chain: dispatch returns the
    # derived view (tag() -> 8), and a static call narrows to &y.
    assert (
        run(
            """
            struct x { v: int32; }
            struct y extends x { w: int32; }
            fn x::tag(const self: &x) -> int32 { return 7; }
            @override fn y::tag(const self: &y) -> int32 { return 8; }
            struct a { n: int32; store: y; }
            struct b extends a { m: int32; }
            fn a::get(self: &a) -> &x { return self.store; }
            @override fn b::get(self: &b) -> &y { return self.store; }
            fn via(q: &a) -> int32 { return q.get().tag(); }
            fn main() -> int32 {
                let obj: b = { n = 0, store = { v = 0, w = 0 }, m = 0 };
                if (via(obj) != 8) { return 10; }
                obj.get().w = 5;
                if (obj.store.w != 5) { return 20; }
                return 0;
            }
            """
        )
        == 0
    )


def test_covariant_override_must_still_be_a_reference():
    # A BY-VALUE return of a descendant would slice through the shared
    # slot, so only references participate in covariance.
    with pytest.raises(
        LangError,
        match=r"@override method 'b::me' returns b but the base member",
    ):
        compile_ir(
            """
            struct a { n: int32; }
            struct b extends a { m: int32; }
            fn a::kind(const self: &a) -> int32 { return 1; }
            @override fn b::kind(const self: &b) -> int32 { return 2; }
            fn a::me(const self: &a) -> a { return self; }
            @override fn b::me(const self: &b) -> b { return self; }
            fn main() -> int32 { return 0; }
            """
        )


def test_override_returning_an_unrelated_reference_is_still_rejected():
    # The relaxation is exactly "a declared descendant of the base's
    # reference return": an unrelated reference stays the return-ABI error.
    with pytest.raises(
        LangError,
        match=r"@override method 'b::me' returns int32 but the base member",
    ):
        compile_ir(
            """
            struct a { n: int32; }
            struct b extends a { m: int32; }
            fn a::kind(const self: &a) -> int32 { return 1; }
            @override fn b::kind(const self: &b) -> int32 { return 2; }
            fn a::me(self: &a) -> &a { return self; }
            @override fn b::me(self: &b) -> &int32 { return self.n; }
            fn main() -> int32 { return 0; }
            """
        )


def test_mci_stub_preserves_the_covariant_return_spelling(tmp_path):
    # The stub re-emits the override's own `-> &b` return: the narrowing
    # lives only in the checker, so a static caller importing through the
    # interface would silently lose it if the stub flattened the spelling
    # to the base's.
    lib = tmp_path / "geo.mc"
    lib.write_text(
        "struct a { n: int32; }\n"
        "struct b extends a { m: int32; }\n"
        "fn a::kind(const self: &a) -> int32 { return 1; }\n"
        "@override fn b::kind(const self: &b) -> int32 { return 2; }\n"
        "fn a::me(self: &a) -> &a { return self; }\n"
        "@override fn b::me(self: &b) -> &b { return self; }\n"
    )
    out = tmp_path / "geo.mci"
    assert emit_interface(lib, (tmp_path,), None, {}, out) == 0
    stub = out.read_text()
    assert "fn a::me(self: &a) -> &a;" in stub
    assert "@override fn b::me(self: &b) -> &b;" in stub


def test_consumer_against_a_covariant_stub_keeps_the_narrowing(tmp_path):
    # A consumer compiled against the stub alone: a static call on a
    # concrete b still types me()'s result as &b (the derived field is
    # assignable through it), and a base-view call dispatches indirectly.
    (tmp_path / "geo.mci").write_text(
        "struct a { n: int32; }\n"
        "struct b extends a { m: int32; }\n"
        "fn a::kind(const self: &a) -> int32;\n"
        "@override fn b::kind(const self: &b) -> int32;\n"
        "fn a::me(self: &a) -> &a;\n"
        "@override fn b::me(self: &b) -> &b;\n"
    )
    main = tmp_path / "main.mc"
    main.write_text(
        'import "geo";\n'
        "fn narrow(x: &b) -> int32 { x.me().m = 7; return x.m; }\n"
        "fn via(x: &a) -> int32 { return x.me().kind(); }\n"
        "fn main() -> int32 { let obj: b = { n = 0, m = 0 };\n"
        "    return narrow(obj) + via(obj); }\n"
    )
    ir = str(compile_to_ir(main, (tmp_path,)))
    via = ir.split('define i32 @"via"')[1].split("\n}")[0]
    assert "load i8*, i8**" in via  # the me() slot dispatches indirectly


def test_extending_a_stubs_covariant_thin_return_is_rejected(tmp_path):
    # The stub pinned b::me's covariant `-> &b` THIN (nothing extends b in
    # its own closure). A consumer that declares `c extends b` holds objects
    # that one-word return cannot describe: the slot thunk would stamp b's
    # STATIC table on a returned reference whose runtime referent is a c,
    # silently mis-dispatching -- so the pinned-width disagreement is a
    # compile error, like every other ABI drift across the .mci boundary.
    (tmp_path / "geo.mci").write_text(
        "struct a { n: int32; }\n"
        "struct b extends a { m: int32; }\n"
        "fn a::kind(const self: &a) -> int32;\n"
        "@override fn b::kind(const self: &b) -> int32;\n"
        "fn a::me(self: &a) -> &a;\n"
        "@override fn b::me(self: &b) -> &b;\n"
    )
    main = tmp_path / "main.mc"
    main.write_text(
        'import "geo";\n'
        "struct c extends b { k: int32; }\n"
        "@override fn c::kind(const self: &c) -> int32 { return 3; }\n"
        "fn via(x: &a) -> int32 { return x.me().kind(); }\n"
        "fn main() -> int32 {\n"
        "    let obj: c = { n = 0, m = 0, k = 0 };\n"
        "    return via(obj);\n"
        "}\n"
    )
    with pytest.raises(
        LangError,
        match=r"@override method 'b::me' occupies a covariant dispatch slot,"
        r" but it returns a thin &b",
    ):
        compile_to_ir(main, (tmp_path,))


def test_extending_a_stubs_thin_base_return_under_covariance_is_rejected(
    tmp_path,
):
    # The other face: the consumer's own covariant override is fine, but the
    # BASE member it overrides was pinned with a thin `-> &x` (the stub's
    # closure sees no extension of x) while the consumer declares
    # `y extends x`. A dispatch through the base view would type the slot
    # call by the pinned-thin callee and drop the runtime table on the way
    # out -- rejected instead.
    (tmp_path / "geo2.mci").write_text(
        "struct x { v: int32; }\n"
        "struct a { n: int32; store: x; }\n"
        "struct a2 extends a { p: int32; }\n"
        "fn a::get(self: &a) -> &x;\n"
    )
    main = tmp_path / "main.mc"
    main.write_text(
        'import "geo2";\n'
        "struct y extends x { w: int32; }\n"
        "fn x::tag(const self: &x) -> int32 { return 7; }\n"
        "@override fn y::tag(const self: &y) -> int32 { return 8; }\n"
        "struct b extends a { m: int32; store2: y; }\n"
        "@override fn b::get(self: &b) -> &y { return self.store2; }\n"
        "fn via(q: &a) -> int32 { return q.get().tag(); }\n"
        "fn main() -> int32 {\n"
        "    let obj: b = { n = 0, store = { v = 0 }, m = 0,\n"
        "                   store2 = { v = 0, w = 0 } };\n"
        "    return via(obj);\n"
        "}\n"
    )
    with pytest.raises(
        LangError,
        match=r"@override method 'b::get' occupies a covariant dispatch "
        r"slot, but the base member it overrides returns a thin &x",
    ):
        compile_to_ir(main, (tmp_path,))


def test_stub_generic_thin_return_rejects_the_upcast(tmp_path):
    # A generic body shipped in a stub instantiates under the STUB's pinned
    # closure: `pick<a, b>`'s `-> &a` return is thin there (the stub sees no
    # extension of a), so the return-position upcast has no table word to
    # carry b -- rejected, where it would otherwise silently devirtualize
    # the caller's dispatch to a::kind.
    (tmp_path / "geo3.mci").write_text(
        "fn pick<T, U>(x: &T, y: &U) -> &T { return y; }\n"
    )
    main = tmp_path / "main.mc"
    main.write_text(
        'import "geo3";\n'
        "struct a { n: int32; }\n"
        "struct b extends a { m: int32; }\n"
        "fn a::kind(const self: &a) -> int32 { return 1; }\n"
        "@override fn b::kind(const self: &b) -> int32 { return 2; }\n"
        "fn main() -> int32 {\n"
        "    let u: a = { n = 0 };\n"
        "    let v: b = { n = 0, m = 0 };\n"
        "    return pick(u, v).kind();\n"
        "}\n"
    )
    with pytest.raises(
        LangError,
        match=r"reference return: a b lvalue upcasts to the declared a only"
        r" through a fat reference, and this return is thin",
    ):
        compile_to_ir(main, (tmp_path,))


def test_stdlib_generic_container_instantiates_a_fat_reference_return():
    # `list<a>::at` instantiated at a fat base widens its `-> &T` return per
    # instance: element reads dispatch the element's own (base) table, and
    # writes through the returned reference land in the container.
    assert (
        run(
            """
            import "std/list";
            struct a { n: int32; }
            struct b extends a { m: int32; }
            fn a::kind(const self: &a) -> int32 { return 1; }
            @override fn b::kind(const self: &b) -> int32 { return 2; }
            fn main() -> int32 {
                let xs = list<a>();
                defer list::destructor(xs);
                xs.push({ n = 7 });
                if (xs.at(0).kind() != 1) { return 10; }
                xs.at(0).n = 42;
                if (xs.at(0).n != 42) { return 20; }
                return 0;
            }
            """
        )
        == 0
    )


def test_let_binding_a_fat_reference_result_is_copy_on_read():
    # References are not storable types, so `let r = relay(o);` binds the
    # eagerly loaded VALUE -- prefix extraction into a plain base carrying no
    # table, exactly like a `let` from a view parameter (ruling #3's
    # copy-on-read). The copy's data is real (r.n reads through) and its call
    # binds statically (1); only an expression-position result -- chained,
    # re-lent, re-returned -- keeps the view.
    assert run(
        """
        struct a { n: int32; }
        struct b extends a { m: int32; }
        fn a::kind(const self: &a) -> int32 { return 1; }
        @override fn b::kind(const self: &b) -> int32 { return 2; }
        fn relay(x: &a) -> &a { return x; }
        fn main() -> int32 {
            let o: b = { n = 5, m = 0 };
            let r = relay(o);
            if (r.n != 5) { return 10; }
            return r.kind();
        }
        """
    ) == 1


# --- SIE-184 + SIE-181: resolution ranks the derived->base view conversion ---


def test_overloaded_callee_accepts_a_derived_argument_at_a_fat_position():
    # SIE-184: overload resolution now considers the derived->base view
    # conversion, so a derived rvalue at an overloaded callee's `const &a`
    # position resolves (previously "no overload of 'via' with signature
    # via(b)" -- adding an unrelated overload broke a working call) and the
    # formed view dispatches the runtime override (2).
    assert run(
        """
        struct a { n: int32; }
        struct b extends a { m: int32; }
        fn a::who(const self: &a) -> int32 { return 1; }
        @override fn b::who(const self: &b) -> int32 { return 2; }
        fn make(x: int32) -> b { let r: b = { n = x, m = x }; return r; }
        fn make(x: float64) -> a { let r: a = { n = 0 }; return r; }
        fn via(const it: &a) -> int32 { return it.who(); }
        fn via(x: int32) -> int32 { return x; }
        fn main() -> int32 { return via(make(1)); }
        """
    ) == 2


def test_overloaded_method_family_accepts_a_derived_non_receiver_argument():
    # SIE-184's method-family shape: the overloaded `pick` resolves its
    # `const other: &a` member for a derived argument and dispatches (2).
    assert run(
        """
        struct a { n: int32; }
        struct b extends a { m: int32; }
        fn a::who(const self: &a) -> int32 { return 1; }
        @override fn b::who(const self: &b) -> int32 { return 2; }
        fn a::pick(const self: &a, const other: &a) -> int32 {
            return other.who();
        }
        fn a::pick(const self: &a, x: int32) -> int32 { return x; }
        fn main() -> int32 {
            let base: a = { n = 0 };
            let derived: b = { n = 0, m = 0 };
            return base.pick(derived);
        }
        """
    ) == 2


def test_generic_inference_binds_through_a_fat_reference_position():
    # SIE-181: a generic `&point<T>` reference parameter infers T through the
    # derived argument's declared base instantiation (pointf extends
    # point<float64> binds T = float64), receiver and free positions alike.
    assert run(
        """
        struct point<T> { x: T; y: T; }
        struct pointf extends point<float64> { tag: int32; }
        fn f<T>(const a: &point<T>) -> int32 {
            if (a.x == (1.5 as T)) { return 7; }
            return 0;
        }
        fn main() -> int32 {
            let p: pointf = { x = 1.5, y = 2.5, tag = 0 };
            return f(p);
        }
        """
    ) == 7


def test_generic_inference_unifies_derived_and_base_arguments():
    # Two arguments at `&point<T>` positions -- one derived, one the base
    # instantiation itself -- unify to the same binding (no conflict): both
    # view to point<float64>.
    assert run(
        """
        struct point<T> { x: T; y: T; }
        struct pointf extends point<float64> { tag: int32; }
        fn same<T>(const a: &point<T>, const b: &point<T>) -> int32 {
            if (a.x == b.x) { return 3; }
            return 0;
        }
        fn main() -> int32 {
            let p: pointf = { x = 1.5, y = 2.5, tag = 0 };
            let q: point<float64> = { x = 1.5, y = 0.0 };
            return same(p, q);
        }
        """
    ) == 3


def test_exact_candidates_outrank_view_conversion_candidates():
    # The ranking rule: an exact-typed candidate beats one the argument only
    # reaches through the view conversion -- reference vs reference, by-value
    # vs reference, and nearer base vs farther base. Every previously-viable
    # candidate has view distance zero, so no pre-existing resolution moves.
    assert run(
        """
        struct a { n: int32; }
        struct b extends a { m: int32; }
        struct c extends b { k: int32; }
        fn f(const it: &a) -> int32 { return 10; }
        fn f(const it: &b) -> int32 { return 20; }
        fn g(x: b) -> int32 { return 30; }
        fn g(const x: &a) -> int32 { return 40; }
        fn h(const it: &a) -> int32 { return 50; }
        fn h(const it: &b) -> int32 { return 60; }
        fn main() -> int32 {
            let vb: b = { n = 0, m = 0 };
            let vc: c = { n = 0, m = 0, k = 0 };
            if (f(vb) != 20) { return 1; }   // exact &b beats upcast &a
            if (g(vb) != 30) { return 2; }   // exact by-value beats &a view
            if (h(vc) != 60) { return 3; }   // &b (1 hop) beats &a (2 hops)
            return 0;
        }
        """
    ) == 0


def test_view_viability_matches_what_emission_can_form():
    # Viability through the conversion is exactly emission's upcast: a
    # candidate whose reference names the right FAMILY but the wrong
    # INSTANCE (gb's chain holds ga<int32>, never ga<float64>) stays
    # non-viable -- the name-keyed walk alone would admit it and emission
    # could not form the view.
    with pytest.raises(
        LangError,
        match=r"no overload of 'f' with signature f\(gb\)",
    ):
        compile_ir(
            """
            struct ga<T> { x: T; }
            struct gb extends ga<int32> { y: int32; }
            fn f(const v: &ga<float64>) -> int32 { return 1; }
            fn f(x: int32) -> int32 { return x; }
            fn main() -> int32 {
                let o: gb = { x = 0, y = 0 };
                return f(o);
            }
            """
        )


def test_by_value_positions_still_require_the_explicit_as():
    # The conversion is reference-only: a derived argument at an overloaded
    # callee's BY-VALUE base position stays rejected (the honest data slice
    # takes an explicit `as`, per the copy ruling).
    with pytest.raises(
        LangError,
        match=r"no overload of 'f' with signature f\(b\)",
    ):
        compile_ir(
            """
            struct a { n: int32; }
            struct b extends a { m: int32; }
            fn f(x: a) -> int32 { return 1; }
            fn f(x: int32) -> int32 { return x; }
            fn main() -> int32 {
                let o: b = { n = 0, m = 0 };
                return f(o);
            }
            """
        )


def test_overloaded_writable_reference_takes_a_derived_lvalue():
    # The writable flavor through the overload set: the `&a` member resolves
    # for a derived lvalue, lends its storage as the view (the write lands),
    # and dispatches the override (2).
    assert run(
        """
        struct a { n: int32; }
        struct b extends a { m: int32; }
        fn a::who(const self: &a) -> int32 { return 1; }
        @override fn b::who(const self: &b) -> int32 { return 2; }
        fn poke(it: &a) -> int32 { it.n = 9; return it.who(); }
        fn poke(x: int32) -> int32 { return x; }
        fn main() -> int32 {
            let o: b = { n = 0, m = 0 };
            let r = poke(o);
            if (o.n != 9) { return 80; }
            return r;
        }
        """
    ) == 2


# --- SIE-184 review round: viability, ranking, and emission are ONE predicate -


def test_decayed_pointer_argument_keeps_the_exact_overload():
    # The view runs AFTER pointer decay and its distance is recorded on the
    # same post-decay actuals, so a `b*` argument decaying to `b` charges the
    # `&a` candidate its hop and the exact `&b` wins (a distance computed on
    # the raw pointer type saw 0 for both and manufactured an ambiguity).
    assert run(
        """
        struct a { n: int32; }
        struct b extends a { m: int32; }
        fn f(const it: &a) -> int32 { return 1; }
        fn f(const it: &b) -> int32 { return 2; }
        fn main() -> int32 {
            let v: b = { n = 1, m = 2 };
            let p: b* = &v;
            return f(p);
        }
        """
    ) == 2


def test_enclosing_generic_binding_does_not_hijack_a_concrete_pattern():
    # Inside g<int32>, the live binding T = int32 must not capture the
    # candidate's parameter STRUCT named T: the concrete pattern resolves in
    # the candidate's own context (empty bindings), and the shape filter
    # trusts the formed view instead of re-resolving the bare name in the
    # caller's scope.
    assert run(
        """
        struct T { n: int32; }
        struct d extends T { m: int32; }
        fn f(const v: &T) -> int32 { return 7; }
        fn f(x: int32) -> int32 { return x; }
        fn g<T>(x: T) -> int32 {
            let o: d = { n = 1, m = 2 };
            return f(o);
        }
        fn main() -> int32 { return g(0); }
        """
    ) == 7


def test_qualified_member_overloads_rank_the_receiver_position():
    # A qualified `a::f(d)` charges position 0 its view distance like any
    # other reference position (the old method-position exclusion dropped it
    # and left the pair tied), so the exact `&b` member wins.
    assert run(
        """
        struct a { n: int32; }
        struct b extends a { m: int32; }
        fn a::f(const it: &a) -> int32 { return 1; }
        fn a::f(const it: &b) -> int32 { return 2; }
        fn main() -> int32 {
            let d: b = { n = 1, m = 2 };
            return a::f(d);
        }
        """
    ) == 2


def test_alias_spelled_view_candidate_is_charged_its_distance():
    # `&abase` (a plain alias of `a`) views -- and is charged -- exactly like
    # the direct spelling: the distance comes from the formed view itself,
    # never a separate name walk that an alias spelling slips past.
    assert run(
        """
        struct a { n: int32; }
        struct b extends a { m: int32; }
        type abase = a;
        fn h(const it: &abase) -> int32 { return 1; }
        fn h(const it: &b) -> int32 { return 2; }
        fn main() -> int32 {
            let vb: b = { n = 1, m = 2 };
            return h(vb);
        }
        """
    ) == 2


def test_concrete_receiver_specialization_does_not_capture_the_family():
    # The receiver position routes CONCRETE patterns through the same
    # emission-parity resolution as every other position: the
    # `ga<const char>` specialization (whose instance gb's chain never holds)
    # stays non-viable, and the generic member wins -- adding the
    # specialization must not break the working call with a late marshal
    # error.
    assert run(
        """
        struct ga<T> { x: int32; }
        struct gb extends ga<char> { y: int32; }
        fn ga<T>::m(const self: &ga<T>) -> int32 { return 1; }
        fn ga<const char>::m(const self: &ga<const char>) -> int32 {
            return 3;
        }
        fn main() -> int32 {
            let b: gb = { x = 1, y = 2 };
            return ga::m(b);
        }
        """
    ) == 1


def test_thin_mci_parameter_stays_cleanly_non_viable(tmp_path):
    # Resolution carries emission's fatness gate: the stub never saw `a`
    # extended, so its `&a` is THIN and emission cannot form a view for a
    # derived argument -- the candidate is cleanly non-viable (the honest
    # no-overload diagnostic), never admitted and then failed at the marshal
    # with a misleading coercion error.
    (tmp_path / "api.mci").write_text(
        "struct a { n: int32; }\n"
        "fn viewit(const it: &a) -> int32;\n"
        "fn viewit(x: int32) -> int32;\n"
    )
    main = tmp_path / "main.mc"
    main.write_text(
        'import "api";\n'
        "struct b extends a { m: int32; }\n"
        "fn main() -> int32 {\n"
        "    let v: b = { n = 1, m = 2 };\n"
        "    return viewit(v);\n"
        "}\n"
    )
    with pytest.raises(
        LangError,
        match=r"no overload of 'viewit' with signature viewit\(b\)",
    ):
        compile_to_ir(main, (tmp_path,))


def test_mixed_view_distances_are_ambiguous():
    # The dominance rule (maintainer ruling): a candidate wins on view
    # distance only when it is no farther at EVERY position. Nearer here,
    # farther there is incomparable -- the call is ambiguous, never resolved
    # by a silent total that lets one position outvote another's exact match.
    with pytest.raises(
        LangError,
        match=r"call to 'f' is ambiguous between overloads: no candidate "
        r"is uniformly nearest through the derived->base view",
    ):
        compile_ir(
            """
            struct a { n: int32; }
            struct b extends a { m: int32; }
            struct c extends b { o: int32; }
            struct d extends c { p: int32; }
            fn f(const x: &c, const y: &c) -> int32 { return 1; }
            fn f(const x: &d, const y: &a) -> int32 { return 2; }
            fn main() -> int32 {
                let v: d = { n = 1, m = 2, o = 3, p = 4 };
                return f(v, v);
            }
            """
        )


def test_uniformly_nearer_candidate_wins_across_positions():
    # The dominance rule's resolving half: nearer (or equal) at BOTH
    # positions is a genuine win, so the `&b` pair beats the `&a` pair for
    # two `c` arguments.
    assert run(
        """
        struct a { n: int32; }
        struct b extends a { m: int32; }
        struct c extends b { o: int32; }
        fn f(const x: &a, const y: &a) -> int32 { return 1; }
        fn f(const x: &b, const y: &b) -> int32 { return 2; }
        fn main() -> int32 {
            let v: c = { n = 1, m = 2, o = 3 };
            return f(v, v);
        }
        """
    ) == 2


def test_losing_candidates_do_not_instantiate_generic_structs():
    # The name pre-walk: a losing candidate's generic reference pattern
    # (box<heavy>, a family the argument's chain never reaches) is rejected
    # before its parameter type resolves, so the trial leaves no orphan
    # monomorphized struct in the module IR.
    ir = compile_ir(
        """
        struct heavy { n: int32; }
        struct box<T> { it: T; }
        struct a { n: int32; }
        struct b extends a { m: int32; }
        fn f<U>(const it: &box<heavy>, u: U) -> int32 { return 1; }
        fn f<U>(const it: &a, u: U) -> int32 { return 2; }
        fn main() -> int32 {
            let v: b = { n = 1, m = 2 };
            return f(v, 0);
        }
        """
    )
    assert 'box<heavy>' not in ir


# --- SIE-184 review round 2: viability halves the round-1 asserts left open --


def test_alias_spelled_view_candidate_stays_viable_alone():
    # The complement of the round-1 alias RANKING test (which an over-eager
    # rejection would also pass): with no exact competitor, the alias-spelled
    # `&abase` candidate must still be VIABLE through the view -- the name
    # pre-walk chases the plain alias to the family, it does not reject it.
    assert run(
        """
        struct a { n: int32; }
        struct b extends a { m: int32; }
        type abase = a;
        fn h(const it: &abase) -> int32 { return 1; }
        fn h(x: int32) -> int32 { return x; }
        fn main() -> int32 {
            let vb: b = { n = 1, m = 2 };
            return h(vb);
        }
        """
    ) == 1


def test_qualified_member_view_candidate_stays_viable_alone():
    # The complement of the round-1 qualified-member RANKING test: charging
    # position 0 of `a::f(d)` its view distance must not have cost the
    # position its upcast -- the sole `&a` member still takes the derived
    # argument through the view.
    assert run(
        """
        struct a { n: int32; }
        struct b extends a { m: int32; }
        fn a::f(const it: &a) -> int32 { return 1; }
        fn a::f(x: int32) -> int32 { return x; }
        fn main() -> int32 {
            let d: b = { n = 1, m = 2 };
            return a::f(d);
        }
        """
    ) == 1


def test_concrete_receiver_specialization_wins_when_the_chain_holds_it():
    # The capture test's other half: routing concrete receiver patterns
    # through emission's predicate rejects the WRONG instance, but the RIGHT
    # instance (gb's chain holds ga<char>) must keep beating the generic
    # member -- specialization-over-generic ranking is undisturbed when the
    # view really can form.
    assert run(
        """
        struct ga<T> { x: int32; }
        struct gb extends ga<char> { y: int32; }
        fn ga<T>::m(const self: &ga<T>) -> int32 { return 1; }
        fn ga<char>::m(const self: &ga<char>) -> int32 { return 3; }
        fn main() -> int32 {
            let b: gb = { x = 1, y = 2 };
            return ga::m(b);
        }
        """
    ) == 3


def test_exact_candidate_wins_over_incomparable_view_candidates():
    # Dominance's all-zero fast path in a crowd: two view candidates that are
    # incomparable to each other (nearer here, farther there) must not drag
    # the call into ambiguity when a third candidate is exact at every
    # position -- the zero vector dominates both.
    assert run(
        """
        struct a { n: int32; }
        struct b extends a { m: int32; }
        struct c extends b { o: int32; }
        struct d extends c { p: int32; }
        fn f(const x: &c, const y: &c) -> int32 { return 1; }
        fn f(const x: &d, const y: &a) -> int32 { return 2; }
        fn f(const x: &d, const y: &d) -> int32 { return 3; }
        fn main() -> int32 {
            let v: d = { n = 1, m = 2, o = 3, p = 4 };
            return f(v, v);
        }
        """
    ) == 3


def test_writable_reference_positions_charge_the_view_distance():
    # The distance gate covers mut positions too (`it: &a` vs `it: &b`): the
    # exact `&b` wins for a derived lvalue, and its write lands -- the const
    # tests alone would not catch a gate keyed only to constref_params.
    assert run(
        """
        struct a { n: int32; }
        struct b extends a { m: int32; }
        fn f(it: &a) -> int32 { it.n = 9; return 1; }
        fn f(it: &b) -> int32 { it.m = 9; return 2; }
        fn main() -> int32 {
            let v: b = { n = 0, m = 0 };
            if (f(v) != 2) { return 10; }
            if (v.m != 9) { return 11; }
            if (v.n != 0) { return 12; }
            return 2;
        }
        """
    ) == 2


def test_cross_module_alias_candidate_ranks_and_stays_viable(tmp_path):
    # The alias chase runs in the CANDIDATE's own context: a `&abase`
    # parameter spelled through an alias private to the candidate's module
    # is charged its distance (the caller's exact `&b` wins) yet stays
    # viable through the view when it is the only reference candidate.
    (tmp_path / "shapes.mc").write_text(
        "struct a { n: int32; }\n"
        "type abase = a;\n"
        "fn h(const it: &abase) -> int32 { return 1; }\n"
    )
    main = tmp_path / "main.mc"
    main.write_text(
        'import "shapes";\n'
        "struct b extends a { m: int32; }\n"
        "fn h(const it: &b) -> int32 { return 2; }\n"
        "fn main() -> int32 {\n"
        "    let vb: b = { n = 1, m = 2 };\n"
        "    return h(vb);\n"
        "}\n"
    )
    assert run_path(main) == 2
    sole = tmp_path / "sole.mc"
    sole.write_text(
        'import "shapes";\n'
        "struct b extends a { m: int32; }\n"
        "fn main() -> int32 {\n"
        "    let vb: b = { n = 1, m = 2 };\n"
        "    return h(vb);\n"
        "}\n"
    )
    assert run_path(sole) == 1


# --- SIE-187: decay composes with the base view (deref-then-view) -------------


def test_sole_candidate_decayed_pointer_forms_the_view_and_dispatches():
    # The residual gap: resolution admitted the decay+view reading but
    # emission demanded the exact pointee. A sole `f(const it: &a)` now takes
    # a proven `b*` -- the pointer sheds its level, the pointee lends its
    # base prefix, and the pointee's (derived) table dispatches the
    # @override -- identical to f(*p).
    assert run(
        """
        struct a { n: int32; }
        struct b extends a { m: int32; }
        fn a::who(const self: &a) -> int32 { return 1; }
        @override fn b::who(const self: &b) -> int32 { return 2; }
        fn f(const it: &a) -> int32 { return it.who(); }
        fn main() -> int32 {
            let v: b = { n = 5, m = 2 };
            let p: b* = &v;
            return f(p);
        }
        """
    ) == 2


def test_decayed_pointer_call_is_identical_to_the_explicit_deref():
    # The ruling's shape: `f(p)` IS `f(*p)`, differing only in who carries
    # the null obligation -- so the two spellings dispatch identically.
    assert run(
        """
        struct a { n: int32; }
        struct b extends a { m: int32; }
        fn a::who(const self: &a) -> int32 { return 1; }
        @override fn b::who(const self: &b) -> int32 { return 2; }
        fn f(const it: &a) -> int32 { return it.who(); }
        fn main() -> int32 {
            let v: b = { n = 5, m = 2 };
            let p: b* = &v;
            if (f(p) != f(*p)) { return 90; }
            return 0;
        }
        """
    ) == 0


def test_overload_set_admits_and_emits_the_decayed_view():
    # The ticket's exact shape: the decay tier admits `b*` at the fat `&a`
    # candidate (the int32 sibling is not viable), and emission now forms
    # the view it admitted -- previously "argument 1 of 'f': expected a,
    # got b*" after resolution had already said yes.
    assert run(
        """
        struct a { n: int32; }
        struct b extends a { m: int32; }
        fn a::who(const self: &a) -> int32 { return 1; }
        @override fn b::who(const self: &b) -> int32 { return 2; }
        fn f(const it: &a) -> int32 { return it.who(); }
        fn f(x: int32) -> int32 { return x; }
        fn main() -> int32 {
            let v: b = { n = 5, m = 2 };
            let p: b* = &v;
            return f(p);
        }
        """
    ) == 2


def test_write_through_a_decayed_writable_view_lands():
    # Mut-path parity (the decay_viable re-check adopts the shared view
    # predicate): `b*` decays into the writable `&a`, the callee's write
    # lands in the derived value's leading fields, and the view still
    # dispatches the override.
    assert run(
        """
        struct a { n: int32; }
        struct b extends a { m: int32; }
        fn a::who(const self: &a) -> int32 { return 1; }
        @override fn b::who(const self: &b) -> int32 { return 2; }
        fn poke(it: &a) -> int32 { it.n = 9; return it.who(); }
        fn poke(x: int32) -> int32 { return x; }
        fn main() -> int32 {
            let o: b = { n = 0, m = 0 };
            let p: b* = &o;
            let r = poke(p);
            if (o.n != 9) { return 80; }
            return r;
        }
        """
    ) == 2


def test_sole_writable_reference_takes_a_decayed_derived_pointer():
    # The direct (non-overloaded) marshal's mut path: mut_ref_arg composes
    # the same view instead of demanding the exact pointee.
    assert run(
        """
        struct a { n: int32; }
        struct b extends a { m: int32; }
        fn poke(it: &a) { it.n = 9; }
        fn main() -> int32 {
            let v: b = { n = 5, m = 2 };
            let p: b* = &v;
            poke(p);
            return v.n;
        }
        """
    ) == 9


def test_rvalue_pointer_composes_with_the_view():
    # `&v` is an rvalue pointer expression; the pointee is real storage, so
    # it decays and views like the named pointer (the rvalue marshal arm).
    assert run(
        """
        struct a { n: int32; }
        struct b extends a { m: int32; }
        fn a::who(const self: &a) -> int32 { return 1; }
        @override fn b::who(const self: &b) -> int32 { return 2; }
        fn f(const it: &a) -> int32 { return it.who(); }
        fn main() -> int32 {
            let v: b = { n = 5, m = 2 };
            return f(&v);
        }
        """
    ) == 2


def test_decayed_pointer_views_across_a_two_hop_chain():
    # The composition inherits the view's chain walk: a `c*` at `&a` views
    # two hops up and still dispatches c's override.
    assert run(
        """
        struct a { n: int32; }
        struct b extends a { m: int32; }
        struct c extends b { k: int32; }
        fn a::who(const self: &a) -> int32 { return 1; }
        @override fn c::who(const self: &c) -> int32 { return 3; }
        fn f(const it: &a) -> int32 { return it.who(); }
        fn main() -> int32 {
            let v: c = { n = 1, m = 2, k = 3 };
            let p: c* = &v;
            return f(p);
        }
        """
    ) == 3


def test_qualified_receiver_decays_into_the_base_view():
    # A method-family call's receiver composes too: `a::get(p)` with a `b*`
    # receiver is `a::get(*p)`.
    assert run(
        """
        struct a { n: int32; }
        struct b extends a { m: int32; }
        fn a::get(const self: &a) -> int32 { return self.n; }
        fn main() -> int32 {
            let v: b = { n = 7, m = 2 };
            let p: b* = &v;
            return a::get(p);
        }
        """
    ) == 7


def test_generic_inference_composes_decay_with_the_view():
    # SIE-181's inference composes with decay: a `gb*` at `const v: &ga<T>`
    # sheds its level, then binds T = int32 through the pointee's declared
    # base instantiation.
    assert run(
        """
        struct ga<T> { x: T; }
        struct gb extends ga<int32> { y: int32; }
        fn f<T>(const v: &ga<T>) -> T { return v.x; }
        fn main() -> int32 {
            let o: gb = { x = 41, y = 1 };
            let p: gb* = &o;
            return f(p) + 1;
        }
        """
    ) == 42


def test_unproven_pointer_error_still_fires_at_a_viewed_const_slot():
    # The null story is inherited unchanged: the decay proof guards the view
    # exactly as it guards an exact-pointee decay.
    with pytest.raises(
        LangError,
        match=r"cannot pass a possibly-null b\* as argument 1 of 'f': "
        r"decaying into a const a parameter forms a hidden reference",
    ):
        compile_ir(
            """
            struct a { n: int32; }
            struct b extends a { m: int32; }
            fn f(const it: &a) -> int32 { return it.n; }
            fn get() -> b* { return null; }
            fn main() -> int32 {
                let p: b* = get();
                return f(p);
            }
            """
        )


def test_unproven_pointer_error_still_fires_at_a_viewed_writable_slot():
    # The writable flavor of the same inherited obligation.
    with pytest.raises(
        LangError,
        match=r"cannot pass a possibly-null b\* as argument 1 of 'poke': "
        r"decaying into a reference a parameter forms a hidden reference",
    ):
        compile_ir(
            """
            struct a { n: int32; }
            struct b extends a { m: int32; }
            fn poke(it: &a) { it.n = 9; }
            fn get() -> b* { return null; }
            fn main() -> int32 {
                let p: b* = get();
                poke(p);
                return 0;
            }
            """
        )


def test_thin_mci_parameter_rejects_the_decayed_derived_pointer(tmp_path):
    # The fatness gate applies unchanged: the stub never saw `a` extended,
    # so its `&a` is thin, emission cannot form a view there, and the
    # decayed reading is cleanly non-viable -- the honest no-overload
    # diagnostic on the pointer's own signature.
    (tmp_path / "api.mci").write_text(
        "struct a { n: int32; }\n"
        "fn viewit(const it: &a) -> int32;\n"
        "fn viewit(x: int32) -> int32;\n"
    )
    main = tmp_path / "main.mc"
    main.write_text(
        'import "api";\n'
        "struct b extends a { m: int32; }\n"
        "fn main() -> int32 {\n"
        "    let v: b = { n = 1, m = 2 };\n"
        "    let p: b* = &v;\n"
        "    return viewit(p);\n"
        "}\n"
    )
    with pytest.raises(
        LangError,
        match=r"no overload of 'viewit' with signature viewit\(b\*\)",
    ):
        compile_to_ir(main, (tmp_path,))


def test_exact_pointer_overload_still_beats_the_composed_reading():
    # Two-tier viability is untouched: the decay tier (and with it the
    # composed view) opens only when no candidate matches the pointer type
    # directly, so the `b*` overload wins without ambiguity.
    assert run(
        """
        struct a { n: int32; }
        struct b extends a { m: int32; }
        fn f(x: b*) -> int32 { return 3; }
        fn f(const it: &a) -> int32 { return 1; }
        fn main() -> int32 {
            let v: b = { n = 1, m = 2 };
            let p: b* = &v;
            return f(p);
        }
        """
    ) == 3


def test_rvalue_pointer_composes_at_a_sole_writable_slot():
    # The direct marshal's rvalue arm: `&v` is a b* rvalue, and the pointee
    # is real storage, so it decays into the writable `&a` and views.
    assert run(
        """
        struct a { n: int32; }
        struct b extends a { m: int32; }
        fn poke(it: &a) { it.n = 9; }
        fn main() -> int32 {
            let v: b = { n = 5, m = 2 };
            poke(&v);
            return v.n;
        }
        """
    ) == 9


def test_rvalue_pointer_composes_at_an_overloaded_writable_slot():
    # The overload path's rvalue arm: same composition through the decay
    # tier, write landing and override dispatching.
    assert run(
        """
        struct a { n: int32; }
        struct b extends a { m: int32; }
        fn a::who(const self: &a) -> int32 { return 1; }
        @override fn b::who(const self: &b) -> int32 { return 2; }
        fn poke(it: &a) -> int32 { it.n = 9; return it.who(); }
        fn poke(x: int32) -> int32 { return x; }
        fn main() -> int32 {
            let o: b = { n = 0, m = 0 };
            let r = poke(&o);
            if (o.n != 9) { return 80; }
            return r;
        }
        """
    ) == 2


def test_const_pointee_cannot_compose_into_a_writable_slot():
    # decay_view_target's mut guard: a pointer to a CONST pointee never
    # composes into a writable `&a` -- the callee writes through the view.
    # (The const slot flavor is fine; see the next test.)
    with pytest.raises(
        LangError,
        match=r"argument 1 of 'poke': expected a a lvalue, got const b\*",
    ):
        compile_ir(
            """
            struct a { n: int32; }
            struct b extends a { m: int32; }
            fn poke(it: &a) { it.n = 9; }
            fn g(p: const b*) {
                if (p == null) { return; }
                poke(p);
            }
            fn main() -> int32 { return 0; }
            """
        )


def test_const_pointee_stays_non_viable_at_an_overloaded_writable_slot():
    # The same guard through decay_viable's re-check: the writable candidate
    # is cleanly non-viable for a const-pointee pointer, so the honest
    # no-overload diagnostic fires.
    with pytest.raises(
        LangError,
        match=r"no overload of 'poke' with signature poke\(const b\*\)",
    ):
        compile_ir(
            """
            struct a { n: int32; }
            struct b extends a { m: int32; }
            fn poke(it: &a) { it.n = 9; }
            fn poke(x: int32) { }
            fn g(@nonnull p: const b*) { poke(p); }
            fn main() -> int32 { return 0; }
            """
        )


def test_const_pointee_composes_at_a_read_only_slot():
    # The const flavor of the guard's other half: a `const b*` pointee
    # composes into `const &a` fine (a const slot also accepts a pointer to
    # a const pointee, exactly as for an exact-pointee decay).
    assert compile_ir(
        """
        struct a { n: int32; }
        struct b extends a { m: int32; }
        fn f(const it: &a) -> int32 { return it.n; }
        fn g(@nonnull p: const b*) -> int32 { return f(p); }
        fn main() -> int32 { return 0; }
        """
    )


def test_decayed_pointer_writes_across_a_two_hop_chain():
    # The writable analog of the two-hop const test: a `c*` at `&a` views
    # two hops up, the write lands in the derived value's leading fields,
    # and the view still dispatches c's override.
    assert run(
        """
        struct a { n: int32; }
        struct b extends a { m: int32; }
        struct c extends b { k: int32; }
        fn a::who(const self: &a) -> int32 { return 1; }
        @override fn c::who(const self: &c) -> int32 { return 3; }
        fn poke(it: &a) -> int32 { it.n = 9; return it.who(); }
        fn main() -> int32 {
            let v: c = { n = 1, m = 2, k = 3 };
            let p: c* = &v;
            let r = poke(p);
            if (v.n != 9) { return 80; }
            return r;
        }
        """
    ) == 3


def test_generic_inference_composes_at_a_writable_slot():
    # The writable analog of the generic-inference composition: a `gb*` at
    # `v: &ga<T>` binds T = int32 through the decayed pointee's declared
    # base, and the instantiated function's own fatness gates the view (the
    # winner-marshal's fat check runs against the instance), so the write
    # lands through the composed view.
    assert run(
        """
        struct ga<T> { x: T; }
        struct gb extends ga<int32> { y: int32; }
        fn bump<T>(v: &ga<T>, by: T) { v.x = v.x + by; }
        fn main() -> int32 {
            let o: gb = { x = 40, y = 1 };
            let p: gb* = &o;
            bump(p, 2);
            return o.x;
        }
        """
    ) == 42
