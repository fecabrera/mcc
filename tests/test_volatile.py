"""@volatile: loads and stores the optimizer must not touch."""

import pytest

from mcc.codegen import CodeGen
from mcc.driver import build_native_module
from mcc.errors import LangError
from helpers import compile_ir, parse, run


def test_member_accesses_are_volatile():
    ir_text = compile_ir(
        "@volatile\nstruct port { dr: uint32; }\n"
        "fn f(p: struct port*) -> uint32 { p->dr = 1; return p->dr; }"
    )
    assert "store volatile i32" in ir_text
    assert "load volatile i32" in ir_text


def test_volatility_propagates_to_nested_fields():
    ir_text = compile_ir(
        "struct pair { x: int32; y: int32; }\n"
        "@volatile\nstruct regs { p: struct pair; }\n"
        "fn f(r: struct regs*) -> int32 { r->p.x = 1; return r->p.y; }"
    )
    assert "store volatile i32" in ir_text
    assert "load volatile i32" in ir_text


def test_deref_and_index_of_volatile_structs():
    ir_text = compile_ir(
        "@volatile\nstruct port { dr: uint32; }\n"
        "fn f(p: struct port*) -> struct port { let one = p[1]; return *p; }"
    )
    assert ir_text.count("load volatile %") == 2


def test_extern_variable_can_be_volatile():
    ir_text = compile_ir(
        "@extern\n@volatile\nlet ticks: int32;\n"
        "fn main() -> int32 { ticks = 1; return ticks; }"
    )
    assert "store volatile i32" in ir_text
    assert "load volatile i32" in ir_text


def test_volatile_stores_survive_optimization():
    # Two back-to-back stores to the same field: dead-store elimination
    # removes the first one unless the struct is @volatile.
    def optimized(annotation):
        source = (
            f"{annotation}struct port {{ dr: uint32; }}\n"
            "fn pulse(p: struct port*) { p->dr = 1; p->dr = 0; }"
        )
        module = CodeGen(parse(source), "test").generate()
        native, _ = build_native_module(module, opt_level=2)
        return str(native)

    assert optimized("@volatile\n").count("store volatile") == 2
    assert optimized("").count("store") == 1


def test_volatile_struct_still_computes_correctly():
    source = """
    import "libc/stdlib";
    @volatile
    struct reg { value: int32; }
    fn main() -> int32 {
        let r = malloc(sizeof(struct reg)) as struct reg*;
        r->value = 21;
        r->value = r->value * 2;
        let result = r->value;
        free(r);
        return result;
    }
    """
    assert run(source) == 42


def test_volatile_on_a_function_is_an_error():
    with pytest.raises(LangError, match="only applies to structs and extern variables"):
        parse("@volatile\nfn f() {}")
