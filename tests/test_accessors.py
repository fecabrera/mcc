"""`@accessor`: the method behind a type's `[]` operator.

`xs[i]` calls `T::at(xs, i)`; both `xs[i]` and `xs.at(i)` reach the same
method. A bare `@accessor` returning `-> mut` makes the element an
assignable lvalue, so `xs[i] = v` is `T::at(xs, i) = v`. Any number of
indices of any type are allowed (`m[r, c]`, `d["key"]`), and the usual
method machinery (generics, inheritance, overloads) carries through.

For elements that need logic on the write path, `@accessor("get")` /
`@accessor("set")` declare an explicit pair: `d[k]` calls the getter,
`d[k] = v` calls the setter (the indices then the assigned value last; its
return is ignored), and `d[k] op= v` is read-modify-write through both.
All `@accessor` methods of one type must share one name (`[]` carries none
to pick by), and one family cannot mix a bare `@accessor` with the pair
form. Natively indexable types (a pointer, array, slice, or tuple) keep
native `[]`.
"""

import pytest

from mcc.errors import LangError
from helpers import compile_ir, run


# --- the bare mut-lvalue form ---------------------------------------------------

def test_index_reads_through_the_accessor():
    # The headline: `[]` on a struct calls the @accessor method.
    assert run(
        """
        struct box { items: int32[4]; }
        @accessor fn box::at(self: &box, i: uint64) -> &int32 {
            return self.items[i];
        }
        fn main() -> int32 {
            let b: box;
            b.items[2] = 42;
            return b[2];                   // box::at(b, 2) -> 42
        }
        """
    ) == 42


def test_both_spellings_reach_the_method():
    # @accessor ADDS the `[]` spelling; the call spellings still work.
    assert run(
        """
        struct box { items: int32[4]; }
        @accessor fn box::at(self: &box, i: uint64) -> &int32 {
            return self.items[i];
        }
        fn main() -> int32 {
            let b: box;
            b.items[0] = 7;
            return b[0] * 10 + b.at(0) - box::at(b, 0) + b[0];  // 70 -> 77
        }
        """
    ) == 77


def test_mut_accessor_is_an_assignable_lvalue():
    # A `-> mut` accessor returns the element's lvalue, so plain and
    # compound assignment write straight through it -- `b[i] = v` is
    # `box::at(b, i) = v`.
    assert run(
        """
        struct box { items: int32[4]; }
        @accessor fn box::at(self: &box, i: uint64) -> &int32 {
            return self.items[i];
        }
        fn main() -> int32 {
            let b: box;
            b[1] = 40;                     // accessor write
            b[1] += 2;                     // compound: 42
            return b[1];                   // -> 42
        }
        """
    ) == 42


def test_multi_index_maps_to_the_argument_list():
    # `m[r, c]` is `grid::at(m, r, c)`: every index becomes an argument.
    assert run(
        """
        struct grid { cells: int32[16]; }
        @accessor fn grid::at(self: &grid, r: uint64, c: uint64) -> &int32 {
            return self.cells[r * 4 + c];
        }
        fn main() -> int32 {
            let g: grid;
            g[2, 3] = 40;
            g[2, 3] += 2;
            return g[2, 3];                // -> 42
        }
        """
    ) == 42


def test_non_integer_index_dispatches_by_type():
    # The index is an ordinary argument, so any type works and overloads
    # within the family dispatch over it.
    assert run(
        """
        struct table { pos: int32[4]; neg: int32[4]; }
        @accessor fn table::at(self: &table, i: uint64) -> &int32 {
            return self.pos[i];
        }
        @accessor fn table::at(self: &table, flip: bool) -> &int32 {
            if (flip)
                return self.neg[0];
            return self.pos[0];
        }
        fn main() -> int32 {
            let t: table;
            t[0] = 2;                      // uint64 overload
            t[true] = 40;                  // bool overload (neg[0])
            return t[true] + t[0 as uint64];  // 40 + 2 -> 42
        }
        """
    ) == 42


def test_write_to_non_mut_bare_accessor_is_rejected():
    # A non-mut bare accessor is read-only: assigning through `[]` rejects
    # exactly as a non-mut-returning call target would.
    with pytest.raises(
        LangError,
        match=r"the call to 'box::at' does not return a reference",
    ):
        compile_ir(
            """
            struct box { v: int32; }
            @accessor fn box::at(const self: &box, i: uint64) -> int32 {
                return self.v;
            }
            fn main() -> int32 {
                let b = box { v = 1 };
                b[0] = 9;
                return 0;
            }
            """
        )


# --- generics and inheritance ---------------------------------------------------

def test_generic_accessor_monomorphizes():
    # The accessor is an ordinary generic method: one instance per T, and
    # an alias instantiation reaches it like the spelled type does.
    assert run(
        """
        struct pair<T> { a: T; b: T; }
        @accessor fn pair<T>::at(self: &pair<T>, i: uint64) -> &T {
            if (i == 0)
                return self.a;
            return self.b;
        }
        type ipair = pair<int32>;
        fn main() -> int32 {
            let p: ipair;
            p[0] = 40;
            p[1] = 2;
            return p[0] + p[1];            // -> 42
        }
        """
    ) == 42


def test_accessor_inherited_through_extends():
    # A derived struct reaches the base's @accessor through its extends
    # chain, like any method family.
    assert run(
        """
        struct base { vals: int32[4]; }
        @accessor fn base::at(self: &base, i: uint64) -> &int32 {
            return self.vals[i];
        }
        struct derived extends base { extra: int32; }
        fn main() -> int32 {
            let d: derived;
            d[1] = 42;
            return d[1];                   // -> 42
        }
        """
    ) == 42


def test_native_indexing_wins_for_pointers_and_arrays():
    # A pointer or array base never consults an accessor: native `[]` wins,
    # even when the ELEMENT type has one.
    assert run(
        """
        struct box { v: int32; }
        @accessor fn box::at(self: &box, i: uint64) -> &int32 {
            return self.v;
        }
        fn main() -> int32 {
            let boxes: box[2];
            boxes[0].v = 42;               // native array indexing
            let p = &boxes[0];
            return p[0].v;                 // native pointer indexing
        }
        """
    ) == 42


# --- the get/set pair form --------------------------------------------------------

PAIR = """
struct celsius { kelvin: float64; }
@accessor("get") fn celsius::deg(const self: &celsius, i: uint64) -> float64 {
    return self.kelvin - 273.15;
}
@accessor("set") fn celsius::deg(self: &celsius, i: uint64, v: float64) {
    self.kelvin = v + 273.15;
}
"""


def test_get_set_pair_reads_and_writes():
    # `c[i]` calls the getter; `c[i] = v` calls the setter.
    assert run(
        PAIR
        + """
        fn main() -> int32 {
            let c = celsius { kelvin = 0.0 };
            c[0] = 42.0;
            return c[0] as int32;          // -> 42
        }
        """
    ) == 42


def test_compound_assignment_is_read_modify_write():
    # `c[i] op= v` is one get, the operator, one set.
    assert run(
        PAIR
        + """
        fn main() -> int32 {
            let c = celsius { kelvin = 0.0 };
            c[0] = 20.0;
            c[0] += 22.0;
            return c[0] as int32;          // -> 42
        }
        """
    ) == 42


def test_setter_return_value_is_ignored():
    # A setter may return (e.g. for chaining as a plain call); the
    # assignment form discards it.
    assert run(
        """
        struct cell { v: int32; }
        @accessor("get") fn cell::at(const self: &cell, i: uint64) -> int32 {
            return self.v;
        }
        @accessor("set") fn cell::at(self: &cell, i: uint64, v: int32) -> int32 {
            self.v = v;
            return -1;                     // ignored by `c[i] = v`
        }
        fn main() -> int32 {
            let c = cell { v = 0 };
            c[0] = 42;
            return c[0];                   // -> 42
        }
        """
    ) == 42


def test_read_of_write_only_accessor_is_rejected():
    with pytest.raises(
        LangError,
        match=r"accessor 'cell::at' is write-only",
    ):
        compile_ir(
            """
            struct cell { v: int32; }
            @accessor("set") fn cell::at(self: &cell, i: uint64, v: int32) {
                self.v = v;
            }
            fn main() -> int32 {
                let c = cell { v = 1 };
                return c[0];
            }
            """
        )


def test_write_to_get_only_accessor_is_rejected():
    with pytest.raises(
        LangError,
        match=r"accessor 'cell::at' is read-only",
    ):
        compile_ir(
            """
            struct cell { v: int32; }
            @accessor("get") fn cell::at(const self: &cell, i: uint64) -> int32 {
                return self.v;
            }
            fn main() -> int32 {
                let c = cell { v = 1 };
                c[0] = 9;
                return 0;
            }
            """
        )


def test_compound_on_write_only_accessor_is_rejected():
    # `op=` must read first: a setter-only accessor has no getter to read
    # the current value through.
    with pytest.raises(
        LangError,
        match=r"accessor 'cell::at' has no getter",
    ):
        compile_ir(
            """
            struct cell { v: int32; }
            @accessor("set") fn cell::at(self: &cell, i: uint64, v: int32) {
                self.v = v;
            }
            fn main() -> int32 {
                let c = cell { v = 1 };
                c[0] += 1;
                return 0;
            }
            """
        )


# --- one family per type, no mixing ---------------------------------------------

def test_two_accessor_names_on_one_type_are_rejected():
    # `[]` carries no method name to pick by, so all @accessor methods of a
    # type must share one.
    with pytest.raises(
        LangError,
        match=r"type 'box' declares @accessor on two method names",
    ):
        compile_ir(
            """
            struct box { v: int32; }
            @accessor fn box::at(self: &box, i: uint64) -> &int32 {
                return self.v;
            }
            @accessor fn box::item(self: &box, i: uint64) -> &int32 {
                return self.v;
            }
            fn main() -> int32 {
                let b = box { v = 1 };
                return b[0];
            }
            """
        )


def test_mixing_bare_with_pair_is_rejected():
    with pytest.raises(
        LangError,
        match=r"mixes a bare @accessor",
    ):
        compile_ir(
            """
            struct box { v: int32; }
            @accessor fn box::at(self: &box, i: uint64) -> &int32 {
                return self.v;
            }
            @accessor("set") fn box::at(self: &box, i: uint64, v: int32) {
                self.v = v;
            }
            fn main() -> int32 {
                let b = box { v = 1 };
                return b[0];
            }
            """
        )


# --- declaration-shape rejections ------------------------------------------------

def test_accessor_on_a_free_function_is_rejected():
    with pytest.raises(
        LangError,
        match=r"@accessor only applies to a method",
    ):
        compile_ir(
            """
            @accessor fn at(i: uint64, j: uint64) -> int32 { return 0; }
            fn main() -> int32 { return 0; }
            """
        )


def test_accessor_without_an_index_is_rejected():
    with pytest.raises(
        LangError,
        match=r"takes its receiver and at least one index",
    ):
        compile_ir(
            """
            struct box { v: int32; }
            @accessor fn box::at(self: &box) -> &int32 { return self.v; }
            fn main() -> int32 { return 0; }
            """
        )


def test_setter_arity_is_checked():
    with pytest.raises(
        LangError,
        match=r"takes its receiver, at least one index, and the assigned value",
    ):
        compile_ir(
            """
            struct box { v: int32; }
            @accessor("set") fn box::at(self: &box, v: int32) { self.v = v; }
            fn main() -> int32 { return 0; }
            """
        )


def test_accessor_returning_void_is_rejected():
    with pytest.raises(
        LangError,
        match=r"an @accessor method must return a value",
    ):
        compile_ir(
            """
            struct box { v: int32; }
            @accessor fn box::at(self: &box, i: uint64) { self.v = 0; }
            fn main() -> int32 { return 0; }
            """
        )


def test_get_returning_mut_is_rejected():
    with pytest.raises(
        LangError,
        match=r'an @accessor\("get"\) method cannot return a reference',
    ):
        compile_ir(
            """
            struct box { v: int32; }
            @accessor("get") fn box::at(self: &box, i: uint64) -> &int32 {
                return self.v;
            }
            fn main() -> int32 { return 0; }
            """
        )


def test_unknown_accessor_kind_is_rejected():
    with pytest.raises(
        LangError,
        match=r'@accessor takes "get" or "set"',
    ):
        compile_ir(
            """
            struct box { v: int32; }
            @accessor("put") fn box::at(self: &box, i: uint64) -> &int32 {
                return self.v;
            }
            fn main() -> int32 { return 0; }
            """
        )


def test_combining_property_and_accessor_is_rejected():
    with pytest.raises(
        LangError,
        match=r"@property and @accessor cannot be combined",
    ):
        compile_ir(
            """
            struct box { v: int32; }
            @property @accessor fn box::at(self: &box, i: uint64) -> &int32 {
                return self.v;
            }
            fn main() -> int32 { return 0; }
            """
        )


def test_multi_index_on_a_native_base_is_rejected():
    # Only an accessor takes several indices; native indexing is single.
    with pytest.raises(
        LangError,
        match=r"cannot index with 2 indices",
    ):
        compile_ir(
            """
            fn main() -> int32 {
                let a: int32[4];
                return a[0, 1];
            }
            """
        )


# --- the stdlib adopters ----------------------------------------------------------

def test_list_and_string_index_through_at(capfd):
    # list<T>::at is the stdlib's bare @accessor; string inherits it.
    assert run(
        """
        import "std/io";
        import "std/string";
        fn main() -> int32 {
            let xs = list<int32>();
            xs.push(40);
            xs.push(0);
            xs[1] = 2;
            let s = string("hallo");
            s[1] = 'e';
            println(f"{xs[0] + xs[1]}");   // 42
            return s.equals("hello") ? 0 : 1;
        }
        """
    ) == 0
    assert capfd.readouterr().out == "42\n"


def test_dict_indexes_by_key():
    # dict<V>::at is the stdlib's get/set pair: `d[k]` reads (unchecked --
    # guard with .has), `d[k] = v` inserts or updates, `d[k] op= v` is RMW.
    assert run(
        """
        import "std/dict";
        fn main() -> int32 {
            let d = dict<int32>();
            d["answer"] = 40;              // insert
            d["answer"] += 2;              // read-modify-write
            if (!d.has("answer") or d.has("question"))
                return 1;
            return d["answer"];            // -> 42
        }
        """
    ) == 42
