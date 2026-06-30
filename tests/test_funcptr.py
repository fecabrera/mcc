"""Function-pointer types: fn(...) -> ret values, variables, and parameters."""

import pytest

from mcc.errors import LangError
from mcc.nodes import CallExpr, TypeRef
from helpers import compile_ir, parse, run


def first_expr(body):
    (func,) = parse("fn main() { " + body + " }").functions
    return func.body[0].expr


def test_fn_type_parses():
    (func,) = parse(
        "fn apply(op: fn(int32, int32) -> int32, x: int32) -> int32 { return op(x, x); }"
    ).functions
    op_type = func.params[0][1]
    assert isinstance(op_type, TypeRef)
    assert str(op_type) == "fn(int32, int32) -> int32"
    assert [str(p) for p in op_type.params] == ["int32", "int32"]
    assert str(op_type.ret) == "int32"


def test_fn_type_without_arrow_returns_void():
    (func,) = parse("fn f(cb: fn(uint8)) {}").functions
    assert str(func.params[0][1]) == "fn(uint8) -> void"


def test_call_through_a_variable():
    source = """
    fn add(a: int32, b: int32) -> int32 { return a + b; }
    fn sub(a: int32, b: int32) -> int32 { return a - b; }
    fn main() -> int32 {
        let op: fn(int32, int32) -> int32 = add;
        let first: int32 = op(20, 2);   // 22
        op = sub;
        return first + op(20, 2);       // 22 + 18
    }
    """
    assert run(source) == 40


def test_pass_function_as_parameter():
    source = """
    fn twice(op: fn(int32) -> int32, x: int32) -> int32 { return op(op(x)); }
    fn inc(x: int32) -> int32 { return x + 1; }
    fn main() -> int32 { return twice(inc, 5); }
    """
    assert run(source) == 7


def test_return_a_function_pointer():
    source = """
    fn dbl(x: int32) -> int32 { return x * 2; }
    fn chooser() -> fn(int32) -> int32 { return dbl; }
    fn main() -> int32 { let f = chooser(); return f(21); }
    """
    assert run(source) == 42


def test_function_pointer_in_a_struct_field():
    source = """
    struct box { run: fn(int32) -> int32; }
    fn neg(x: int32) -> int32 { return 0 - x; }
    fn main() -> int32 {
        let b: struct box;
        b.run = neg;
        let f = b.run;
        return f(-5);
    }
    """
    assert run(source) == 5


def test_null_function_pointer_and_comparison():
    source = """
    fn real(x: int32) -> int32 { return x; }
    fn main() -> int32 {
        let cb: fn(int32) -> int32 = null;
        let before: int32 = 0;
        if (cb == null) { before = 1; }
        cb = real;
        let after: int32 = 0;
        if (cb != null) { after = cb(40); }
        return before + after;        // 1 + 40
    }
    """
    assert run(source) == 41


def test_extern_function_as_a_value():
    # A pointer to a libc function, called indirectly.
    source = """
    @extern fn abs(x: int32) -> int32;
    fn main() -> int32 {
        let f: fn(int32) -> int32 = abs;
        return f(0 - 7);
    }
    """
    assert run(source) == 7


def test_signature_mismatch_is_rejected():
    with pytest.raises(
        LangError, match=r"expected fn\(int32\) -> int32, got fn\(int32, int32\)"
    ):
        compile_ir(
            "fn add(a: int32, b: int32) -> int32 { return a + b; }\n"
            "fn main() -> int32 { let op: fn(int32) -> int32 = add; return op(1); }"
        )


def test_generic_function_has_no_single_value():
    with pytest.raises(
        LangError, match="is generic; a function value needs a single function"
    ):
        compile_ir(
            "fn id<T>(x: T) -> T { return x; }\n"
            "fn main() -> int32 { let f: fn(int32) -> int32 = id; return f(1); }"
        )


def test_arity_mismatch_through_a_pointer():
    with pytest.raises(LangError, match="'op' expects 2 argument"):
        compile_ir(
            "fn add(a: int32, b: int32) -> int32 { return a + b; }\n"
            "fn main() -> int32 { let op: fn(int32, int32) -> int32 = add; return op(1); }"
        )


def test_local_shadows_a_same_named_function():
    # `inc` the local function pointer is called, not a function named inc.
    source = """
    fn inc(x: int32) -> int32 { return x + 1; }
    fn dec(x: int32) -> int32 { return x - 1; }
    fn main() -> int32 {
        let inc: fn(int32) -> int32 = dec;
        return inc(10);   // 9, the local (dec), not the function inc
    }
    """
    assert run(source) == 9


def test_calling_an_expression_parses_as_a_call_expr():
    expr = first_expr("chooser()(5);")
    assert isinstance(expr, CallExpr)
    assert [a.value for a in expr.args] == [5]


def test_call_the_result_of_a_call():
    source = """
    fn dbl(x: int32) -> int32 { return x * 2; }
    fn chooser() -> fn(int32) -> int32 { return dbl; }
    fn main() -> int32 { return chooser()(21); }
    """
    assert run(source) == 42


def test_call_a_struct_field_in_place():
    source = """
    struct widget { on_click: fn(int32) -> int32; }
    fn inc(x: int32) -> int32 { return x + 1; }
    fn main() -> int32 {
        let w: struct widget;
        w.on_click = inc;
        return w.on_click(40) + 1;     // 41 + 1
    }
    """
    assert run(source) == 42


def test_call_a_parenthesized_value():
    source = """
    fn dbl(x: int32) -> int32 { return x * 2; }
    fn main() -> int32 { let f = dbl; return (f)(21); }
    """
    assert run(source) == 42


def test_call_an_list_element():
    # A pointer to function pointers needs the grouped type (fn(...) -> ...)*.
    source = """
    @extern fn malloc(size: uint64) -> uint8*;
    @extern fn free(ptr: uint8*);
    fn dbl(x: int32) -> int32 { return x * 2; }
    fn inc(x: int32) -> int32 { return x + 1; }
    fn main() -> int32 {
        let table = malloc(2 * sizeof(fn(int32) -> int32)) as (fn(int32) -> int32)*;
        table[0] = dbl;
        table[1] = inc;
        let r: int32 = table[0](20) + table[1](1);   // 40 + 2
        free(table);
        return r;
    }
    """
    assert run(source) == 42


def test_grouped_pointer_to_function_pointer_type():
    (func,) = parse("fn f(t: (fn(int32) -> int32)*) {}").functions
    assert str(func.params[0][1]) == "fn(int32) -> int32*"  # printed; the * is outer


def test_star_binds_to_the_return_type_without_grouping():
    # fn(int32) -> int32* is a function returning int32*, not a pointer to a
    # function pointer.
    (func,) = parse("fn f(g: fn(int32) -> int32*) {}").functions
    ret = func.params[0][1].ret
    assert str(ret) == "int32*" and ret.stars == 1


def test_calling_a_non_function_is_an_error():
    with pytest.raises(LangError, match="'n' is not callable; it is a int32"):
        compile_ir("fn main() -> int32 { let n: int32 = 5; return n(1); }")


def test_arity_mismatch_on_an_expression_call():
    with pytest.raises(
        LangError, match=r"call to fn\(int32\) -> int32 expects 1 argument"
    ):
        compile_ir(
            "fn dbl(x: int32) -> int32 { return x * 2; }\n"
            "fn main() -> int32 { return (dbl)(1, 2); }"
        )


def test_emitted_call_is_indirect():
    ir_text = compile_ir(
        "fn add(a: int32, b: int32) -> int32 { return a + b; }\n"
        "fn main() -> int32 {\n"
        "    let op: fn(int32, int32) -> int32 = add;\n"
        "    return op(2, 3);\n"
        "}"
    )
    # The call goes through the loaded pointer, not a direct @add call.
    assert "call i32 %" in ir_text


def test_function_address_as_uint64():
    # A function value casts to its integer address, like a function name in C.
    ir_text = compile_ir(
        "fn add(a: int32, b: int32) -> int32 { return a + b; }\n"
        "fn main() -> uint64 {\n"
        "    let op: fn(int32, int32) -> int32 = add;\n"
        "    return op as uint64;\n"
        "}"
    )
    assert "ptrtoint i32 (i32, i32)*" in ir_text


def test_address_round_trips_through_an_integer():
    # int -> function pointer (inttoptr) yields a callable pointer again.
    source = """
    fn add(a: int32, b: int32) -> int32 { return a + b; }
    fn main() -> int32 {
        let addr: uint64 = add as uint64;
        let op: fn(int32, int32) -> int32 = addr as fn(int32, int32) -> int32;
        return op(2, 3);
    }
    """
    assert run(source) == 5


def test_function_pointer_bitcasts_to_a_data_pointer():
    # Pointers all the way down: a function value bitcasts to/from uint8*.
    source = """
    fn add(a: int32, b: int32) -> int32 { return a + b; }
    fn main() -> int32 {
        let raw: uint8* = add as uint8*;
        let op: fn(int32, int32) -> int32 = raw as fn(int32, int32) -> int32;
        return op(4, 5);
    }
    """
    assert run(source) == 9


# --- arrays of function pointers ---


def test_list_of_function_pointers_parses():
    (func,) = parse("fn main() { let t: (fn(int32, int32) -> int32)[4]; }").functions
    t = func.body[0].type_name
    assert t.params is not None and t.dims == [4]


def test_dispatch_table_indexed_and_called():
    source = """
    fn add(a: int32, b: int32) -> int32 { return a + b; }
    fn sub(a: int32, b: int32) -> int32 { return a - b; }
    fn mul(a: int32, b: int32) -> int32 { return a * b; }
    fn main() -> int32 {
        let ops: (fn(int32, int32) -> int32)[3];
        ops[0] = add; ops[1] = sub; ops[2] = mul;
        let total: int32 = 0;
        let i: int32 = 0;
        while (i < 3) { total = total + ops[i](10, 3); i = i + 1; }
        return total;     // 13 + 7 + 30
    }
    """
    assert run(source) == 50


def test_function_pointer_list_literal():
    source = """
    fn add(a: int32, b: int32) -> int32 { return a + b; }
    fn mul(a: int32, b: int32) -> int32 { return a * b; }
    fn main() -> int32 {
        let ops: (fn(int32, int32) -> int32)[] = [add, mul];
        return ops[0](4, 5) + ops[1](4, 5);     // 9 + 20
    }
    """
    assert run(source) == 29


def test_static_function_pointer_table():
    # A @static table folds each function name to its address at compile time.
    source = """
    fn add(a: int32, b: int32) -> int32 { return a + b; }
    fn mul(a: int32, b: int32) -> int32 { return a * b; }
    @static let ops: (fn(int32, int32) -> int32)[] = [add, mul];
    fn main() -> int32 { return ops[0](4, 5) + ops[1](4, 5); }
    """
    assert run(source) == 29
    ir_text = compile_ir(source)
    assert "[2 x i32 (i32, i32)*]" in ir_text


def test_list_of_function_pointer_pointers():
    # The group lets `*` and `[N]` both bind outside the function type.
    (func,) = parse("fn main() { let t: (fn(int32) -> int32)*[2]; }").functions
    t = func.body[0].type_name
    assert t.params is not None and t.stars == 1 and t.dims == [2]


# ----------------------------------------- function aliases (const / @static)

# A const (or @static global) may name a function and be called by that name.
# The function is declared after consts/globals are folded, so the folding of
# such an initializer is deferred until functions exist.


def test_const_function_alias_inferred():
    source = """
    fn greet(n: int32) -> int32 { return n + 1; }
    const hello = greet;                       // unannotated: type inferred
    fn main() -> int32 { return hello(41); }   // 42
    """
    assert run(source) == 42


def test_const_function_alias_annotated():
    source = """
    fn dbl(n: int32) -> int32 { return n * 2; }
    type op = fn(int32) -> int32;
    const twice: op = dbl;
    fn main() -> int32 { return twice(21); }   // 42
    """
    assert run(source) == 42


def test_unannotated_static_function_alias():
    source = """
    fn greet(n: int32) -> int32 { return n + 10; }
    @static let hello = greet;                 // unannotated @static, inferred
    fn main() -> int32 { return hello(32); }   // 42
    """
    assert run(source) == 42


def test_const_alias_of_a_variadic_function(capfd):
    # The reported case: aliasing variadic println-style output through a const,
    # called with varargs. printf is a variadic extern.
    run(
        """
        import "libc/stdio";
        const out = printf;
        fn main() -> int32 { out("v=%d\\n", 42); return 0; }
        """
    )
    assert capfd.readouterr().out == "v=42\n"


def test_function_const_used_in_a_static_table():
    # An array initializer may mix a function name and a function-pointer const;
    # the const reference inside the literal also defers the table's folding.
    source = """
    fn add(a: int32, b: int32) -> int32 { return a + b; }
    const plus = add;
    type binop = fn(int32, int32) -> int32;
    @static let ops: binop[2] = [add, plus];
    fn main() -> int32 { return ops[0](1, 2) + ops[1](3, 4); }   // 3 + 7
    """
    assert run(source) == 10


def test_non_function_const_is_not_callable():
    with pytest.raises(LangError, match="not callable; it is a int32"):
        compile_ir("const x: int32 = 5; fn main() -> int32 { return x(); }")
