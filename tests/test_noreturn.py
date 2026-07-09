"""@noreturn functions and the `unreachable` statement.

`@noreturn` marks a void function that never returns to its caller (exit,
abort, an infinite loop): a direct call terminates the caller's block, so no
dummy return is needed past it, dead code after it drops like after a return,
and a diverging guard body narrows `@nonnull` facts. `unreachable` is a
statement asserting a path never executes (LLVM `unreachable`; reaching it is
undefined behavior), the exhaustiveness bridge for a `case` else arm.

Anything that actually calls exit()/abort() at runtime must NOT run through
the in-process JIT helpers (it would take pytest down with it); those paths
are exercised in IR or via the CLI subprocess tests in test_cli.py.
"""

import pytest

from mcc.codegen import CodeGen
from mcc.driver import compile_to_ir, emit_interface
from mcc.errors import LangError
from mcc.lexer import tokenize
from mcc.parser import Parser
from mcc.interface import render_interface
from helpers import compile_ir, run, run_path


def compile_error(source: str) -> LangError:
    """Compile a failing source string and return the LangError."""
    with pytest.raises(LangError) as excinfo:
        compile_ir(source)
    return excinfo.value


def iface(source: str) -> str:
    """Render the interface stub for an import-free source string."""
    program = Parser(tokenize(source)).parse_program()
    imports = list(program.imports)
    cg = CodeGen(program, "test")
    cg.generate()
    return render_interface(cg, source, imports)


# ------------------------------------------- a @noreturn call diverges

def test_noreturn_call_terminates_the_block():
    # No dummy return is needed after the call: the block is terminated
    # (today's "may end without a return" would otherwise fire).
    src = """
    import "std/io";
    fn f(x: int32) -> int32 {
        if (x >= 0) { return x; }
        abort();
    }
    fn main() -> int32 { return f(3); }
    """
    ir = compile_ir(src)
    assert 'call void @"abort"()' in ir
    assert "unreachable" in ir
    assert run(src) == 3  # the non-taken path still runs normally


def test_code_after_a_noreturn_call_is_silently_dropped():
    # Same treatment as code after a return: skipped, not type-checked.
    src = """
    import "std/io";
    fn f() -> int32 {
        abort();
        this_function_does_not_exist();
        return "not an int32";
    }
    fn main() -> int32 { return 0; }
    """
    compile_ir(src)


def test_user_defined_noreturn_body_falls_off_into_unreachable():
    # C11 _Noreturn semantics: the promise is the author's; falling off the
    # end is undefined behavior (auto-`unreachable`), not a compile error.
    src = """
    @noreturn fn die() { }
    fn main() -> int32 { return 0; }
    """
    ir = compile_ir(src)
    assert 'define void @"die"()' in ir
    assert "unreachable" in ir


def test_infinite_loop_is_legal_as_a_noreturn_body():
    src = """
    @noreturn fn spin() { while (true) {} }
    fn f(x: int32) -> int32 {
        if (x == 0) { spin(); }
        return x;
    }
    fn main() -> int32 { return f(2); }
    """
    assert run(src) == 2


def test_defers_do_not_run_at_a_noreturn_call():
    # Matching C's exit(): a @noreturn call is not a return, so enclosing
    # defers never emit on that path -- the IR contains no printf call.
    src = """
    import "libc/stdio";
    import "libc/stdlib";
    fn main() -> int32 {
        defer printf("cleanup\\n");
        exit(0);
    }
    """
    ir = compile_ir(src)
    calls = [line for line in ir.splitlines() if "call" in line]
    assert not any("printf" in line for line in calls)
    assert any("exit" in line for line in calls)
    assert "unreachable" in ir


# ------------------------------------------------------- flow-narrowing

def test_diverging_abort_guard_narrows_nonnull():
    # The C-idiomatic guard: `if (p == null) abort();` proves p non-null for
    # the remainder of the scope with zero narrowing changes.
    src = """
    import "std/io";
    fn use(@nonnull p: int32*) -> int32 { return *p; }
    fn f(p: int32*) -> int32 {
        if (p == null) abort();
        return use(p);
    }
    fn main() -> int32 { let x: int32 = 5; return f(&x); }
    """
    assert run(src) == 5


def test_non_noreturn_guard_body_still_does_not_narrow():
    # Pin the negative: a guard whose body calls a plain (returning)
    # function does not diverge, so the fact is not established.
    src = """
    fn log_it() { }
    fn use(@nonnull p: int32*) -> int32 { return *p; }
    fn f(p: int32*) -> int32 {
        if (p == null) log_it();
        return use(p);
    }
    fn main() -> int32 { return 0; }
    """
    err = compile_error(src)
    assert "possibly-null pointer" in str(err)


def test_unreachable_diverging_guard_narrows_nonnull():
    src = """
    fn use(@nonnull p: int32*) -> int32 { return *p; }
    fn f(p: int32*) -> int32 {
        if (p == null) { unreachable; }
        return use(p);
    }
    fn main() -> int32 { let x: int32 = 8; return f(&x); }
    """
    assert run(src) == 8


# ------------------------------------------------- declaration checking

def test_return_inside_a_noreturn_body_errors():
    src = """
    @noreturn fn nope() { return; }
    fn main() -> int32 { return 0; }
    """
    err = compile_error(src)
    assert str(err) == (
        "line 2: cannot return from @noreturn function 'nope' "
        "(it promises never to return)"
    )


def test_non_void_noreturn_errors():
    src = """
    @noreturn fn bad() -> int32 { while (true) {} }
    fn main() -> int32 { return 0; }
    """
    err = compile_error(src)
    assert str(err) == (
        "line 2: @noreturn function 'bad' must return void, not int32 "
        "(a call never yields a value)"
    )


def test_noreturn_main_errors():
    src = "@noreturn fn main() -> int32 { while (true) {} }"
    err = compile_error(src)
    assert str(err) == "line 1: function 'main' cannot be @noreturn"


def test_noreturn_on_a_non_function_errors():
    err = compile_error("@noreturn struct s { x: int32; }")
    assert str(err) == "line 1: @noreturn only applies to functions"


def test_conflicting_extern_noreturn_declarations_error():
    src = """
    @noreturn @extern fn quit(code: int32);
    @extern fn quit(code: int32);
    fn main() -> int32 { return 0; }
    """
    err = compile_error(src)
    assert str(err) == "line 3: conflicting extern declarations for 'quit'"


def test_matching_extern_noreturn_declarations_collapse():
    src = """
    @noreturn @extern fn quit(code: int32);
    @noreturn @extern fn quit(code: int32);
    fn f() -> int32 { quit(1); }
    fn main() -> int32 { return 0; }
    """
    compile_ir(src)


def test_llvm_noreturn_attribute_is_attached():
    src = """
    @noreturn @extern fn quit(code: int32);
    @noreturn fn die() { quit(2); }
    fn main() -> int32 { return 0; }
    """
    ir = compile_ir(src)
    declare = next(line for line in ir.splitlines() if '@"quit"' in line)
    define = next(line for line in ir.splitlines() if 'define' in line and '@"die"' in line)
    assert "noreturn" in declare
    assert "noreturn" in define


# --------------------------------------------- function values (&f, atexit)

def test_function_value_of_a_noreturn_function_is_allowed():
    # Unlike @nonnull, losing @noreturn through a plain fn() type is only a
    # convenience loss -- so exit/abort stay usable as atexit-style handlers.
    src = """
    import "std/io";
    fn main() -> int32 {
        let handler: fn() = abort;
        return 0;
    }
    """
    assert run(src) == 0


def test_indirect_call_through_the_pointer_does_not_diverge():
    # The plain fn() type dropped the flag: the call is assumed to return,
    # so the non-void body still needs its return path.
    src = """
    import "std/io";
    fn f() -> int32 {
        let handler: fn() = abort;
        handler();
    }
    fn main() -> int32 { return 0; }
    """
    err = compile_error(src)
    assert str(err) == "line 3: function 'f' may end without a return"


# ----------------------------------------------------------- generics/@asm

def test_generic_noreturn_instance_diverges():
    src = """
    import "std/io";
    @noreturn fn die<T>(code: T) { exit(code as int32); }
    fn f(x: int32) -> int32 {
        if (x >= 0) { return x; }
        die(1);
    }
    fn main() -> int32 { return f(7); }
    """
    ir = compile_ir(src)
    assert "noreturn" in ir
    assert run(src) == 7


def test_generic_noreturn_must_still_be_void_per_instance():
    src = """
    @noreturn fn die<T>(code: T) -> T { while (true) {} }
    fn main() -> int32 { die(1); }
    """
    err = compile_error(src)
    assert "@noreturn function 'die' must return void, not int32" in str(err)


def test_noreturn_asm_function_is_allowed():
    src = """
    @noreturn @asm fn halt() { "nop" }
    fn f() -> int32 { halt(); }
    fn main() -> int32 { return 0; }
    """
    ir = compile_ir(src)
    define = next(
        line for line in ir.splitlines() if "define" in line and '@"halt"' in line
    )
    assert "noreturn" in define
    assert "unreachable" in ir  # the asm body's fall-off, plus the call site


# ------------------------------------------------- unreachable statement

def test_unreachable_statement_terminates_and_lowers():
    src = """
    fn f(x: int32) -> int32 {
        if (x > 0) { return x; }
        unreachable;
    }
    fn main() -> int32 { return f(4); }
    """
    ir = compile_ir(src)
    assert "unreachable" in ir
    assert run(src) == 4


def test_case_else_unreachable_is_the_exhaustiveness_bridge():
    # All arms return and the else asserts the universe is closed: the case
    # diverges, so the old forced dummy trailing return is gone.
    src = """
    fn f(x: int32) -> int32 {
        case (x) {
            when 0: return 10;
            when 1: return 20;
            else: unreachable;
        }
    }
    fn main() -> int32 { return f(1); }
    """
    assert run(src) == 20


def test_case_type_else_unreachable_asserts_a_closed_universe():
    src = """
    fn f(a: any) -> int32 {
        case type (a) {
            when int32 n: return n;
            else: unreachable;
        }
    }
    fn main() -> int32 { return f(6); }
    """
    assert run(src) == 6


def test_code_after_unreachable_is_silently_dropped():
    src = """
    fn f() -> int32 {
        unreachable;
        this_function_does_not_exist();
    }
    fn main() -> int32 { return 0; }
    """
    compile_ir(src)


def test_unreachable_is_a_reserved_word_now():
    # Pre-1.0 breaking change, called out in the changelog: `unreachable`
    # can no longer be used as an identifier.
    err = compile_error("fn main() -> int32 { let unreachable = 1; return 0; }")
    assert str(err) == "line 1: expected 'IDENT', got 'unreachable'"


# ------------------------------------------------------------------- .mci

def test_noreturn_prototype_is_re_emitted_in_the_interface():
    out = iface("@noreturn fn die() { while (true) {} }")
    assert "@noreturn fn die();" in out


def test_noreturn_round_trips_through_mci(tmp_path):
    # The importer's call sites diverge exactly like the definer's: the
    # consumer needs no dummy return after die().
    lib = tmp_path / "lib.mc"
    lib.write_text("@noreturn fn die() { while (true) {} }\n")
    out = tmp_path / "lib.mci"
    assert emit_interface(lib, (tmp_path,), None, {}, out) == 0
    assert "@noreturn fn die();" in out.read_text()
    lib.unlink()  # force the import to resolve through the stub
    main = tmp_path / "main.mc"
    main.write_text(
        'import "lib";\n'
        "fn f(x: int32) -> int32 {\n"
        "    if (x >= 0) { return x; }\n"
        "    die();\n"
        "}\n"
        "fn main() -> int32 { return f(9); }\n"
    )
    assert run_path(main) == 9


def test_mci_stub_and_definition_must_agree_on_noreturn(tmp_path):
    # The prototype pair check gained the flag: a stub promising divergence
    # over a definition that returns would miscompile every importer.
    (tmp_path / "api.mci").write_text("@noreturn fn die();\n")
    (tmp_path / "impl.mc").write_text("fn die() { }\n")
    main = tmp_path / "main.mc"
    main.write_text(
        'import "api";\nimport "impl";\nfn main() -> int32 { return 0; }\n'
    )
    with pytest.raises(LangError, match="does not match its prototype"):
        compile_to_ir(main)
