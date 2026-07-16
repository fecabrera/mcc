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
from helpers import _resolve, compile_ir, run


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
