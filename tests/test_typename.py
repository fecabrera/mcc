"""The `typename` builtin: a type's canonical name as a string literal.

`typename(...)` mirrors `sizeof` in every surface respect -- it takes a type
or, as a bare name in scope, a variable (never evaluated) -- and folds at
compile time to an ordinary deduplicated rodata string literal (a `char*`).
The spelling is the compiler's canonical `str(LangType)`: exactly the string
the `any` tags hash, with a top-level `const` stripped to match what boxing
does, so `typename(T)` is precisely the preimage of a `T` value's tag.
`typename(expr)` uses the expression's STATIC type: an `any` names as "any",
never its dynamic type. Monomorphization resolves `typename(T)` per
instantiation, including inside generic `case type` arms.
"""

import pytest

from mcc.codegen.types import fnv1a64
from mcc.driver import emit_interface
from mcc.errors import LangError
from helpers import compile_ir, run, run_path

STRCMP = "@extern fn strcmp(a: uint8*, b: uint8*) -> int32;\n"


# ------------------------------------------------------------ type operands

def test_type_operands_fold_to_canonical_spellings():
    # The canonical spelling across the kinds: scalars, pointers (nested
    # too), slices, structs, and generic struct instantiations.
    assert run(
        STRCMP
        + """
        struct point { x: int32; y: int32; }
        struct Pair<T> { a: T; b: T; }
        fn main() -> int32 {
            if (strcmp(typename(int64), "int64") != 0)                { return 1; }
            if (strcmp(typename(char*), "char*") != 0)                { return 2; }
            if (strcmp(typename(int32**), "int32**") != 0)            { return 3; }
            if (strcmp(typename(slice<int32>), "slice<int32>") != 0)  { return 4; }
            if (strcmp(typename(point), "point") != 0)                { return 5; }
            if (strcmp(typename(Pair<int64>), "Pair<int64>") != 0)    { return 6; }
            return 0;
        }
        """
    ) == 0


def test_result_is_an_ordinary_string_value():
    # Value-level: the literal flows into a variable, a parameter, a struct
    # field -- anywhere a string literal can.
    assert run(
        STRCMP
        + """
        struct named { name: char*; }
        fn first(s: char*) -> char { return *s; }
        fn main() -> int32 {
            let n: char* = typename(int64);
            let boxed = struct named { name = typename(bool) };
            if (strcmp(n, "int64") != 0)          { return 1; }
            if (strcmp(boxed.name, "bool") != 0)  { return 2; }
            if (first(typename(char)) != 'c')     { return 3; }
            return 0;
        }
        """
    ) == 0


# ------------------------------------------------------ expression operands

def test_expression_operand_uses_the_static_type():
    # Like sizeof, a bare name in scope is that variable; the operand is
    # typed, never evaluated. An `any` names as "any" -- the static type,
    # never the boxed dynamic one.
    assert run(
        STRCMP
        + """
        fn main() -> int32 {
            let x: int64 = 5;
            let p: char* = "hi";
            let a: any = 3;
            if (strcmp(typename(x), "int64") != 0)  { return 1; }
            if (strcmp(typename(p), "char*") != 0)  { return 2; }
            if (strcmp(typename(a), "any") != 0)    { return 3; }
            return 0;
        }
        """
    ) == 0


# ------------------------------------------------------------ const strips

def test_const_strips_from_type_and_expression_operands():
    # A top-level const strips, matching what boxing does with tags: the
    # name stays the preimage of the value's tag.
    assert run(
        STRCMP
        + """
        fn main() -> int32 {
            let x: const int64 = 5;
            if (strcmp(typename(const int64), "int64") != 0)  { return 1; }
            if (strcmp(typename(x), "int64") != 0)            { return 2; }
            return 0;
        }
        """
    ) == 0


# ---------------------------------------------------------------- generics

def test_generic_resolves_per_instantiation():
    assert run(
        STRCMP
        + """
        fn name_of<T>(x: T) -> char* { return typename(T); }
        fn main() -> int32 {
            if (strcmp(name_of(5 as int32), "int32") != 0)  { return 1; }
            if (strcmp(name_of(1.5), "float64") != 0)       { return 2; }
            return 0;
        }
        """
    ) == 0


def test_generic_value_arm_names_the_boxed_type():
    # Inside `when T v:` the arm is a real generic context: typename(T)
    # names the dynamic type of the boxed any per tag, statically.
    assert run(
        STRCMP
        + """
        fn name(a: any) -> char* {
            case type (a) {
                when T v: return typename(T);
                else:     return "none";
            }
            return "unreached";
        }
        fn main() -> int32 {
            let f: float64 = 1.5;
            if (strcmp(name(7), "int32") != 0)      { return 1; }
            if (strcmp(name(f), "float64") != 0)    { return 2; }
            if (strcmp(name(true), "bool") != 0)    { return 3; }
            return 0;
        }
        """
    ) == 0


def test_generic_pointer_arm_names_the_pointee_and_the_pointer():
    # In `when T* ptr:` T binds to the pointee, and the binding's static
    # type is the pointer -- typename sees both, per tag.
    assert run(
        STRCMP
        + """
        struct point { x: int32; y: int32; }
        fn pointee(a: any) -> char* {
            case type (a) {
                when T* ptr: return typename(T);
                else:        return "none";
            }
            return "unreached";
        }
        fn pointer(a: any) -> char* {
            case type (a) {
                when T* ptr: return typename(ptr);
                else:        return "none";
            }
            return "unreached";
        }
        fn main() -> int32 {
            let x: int64 = 7;
            let p = struct point { x = 1, y = 2 };
            if (strcmp(pointee(&x), "int64") != 0)    { return 1; }
            if (strcmp(pointee(&p), "point") != 0)    { return 2; }
            if (strcmp(pointer(&x), "int64*") != 0)   { return 3; }
            if (strcmp(pointer(&p), "point*") != 0)   { return 4; }
            return 0;
        }
        """
    ) == 0


# -------------------------------------------------------- consts and statics

def test_folds_in_const_and_static_initializers():
    # eval_const handles typename like sizeof: a const or @static initializer
    # folds without a runtime.
    assert run(
        STRCMP
        + """
        const NAME = typename(uint8);
        @static
        let global_name: char* = typename(int16);
        fn main() -> int32 {
            if (strcmp(NAME, "uint8") != 0)        { return 1; }
            if (strcmp(global_name, "int16") != 0) { return 2; }
            return 0;
        }
        """
    ) == 0


# ------------------------------------------------------------------- rodata

def test_literals_dedup_with_each_other_and_with_source_literals():
    # One rodata constant: two typename(int32) results and a spelled-out
    # "int32" literal all share bytes.
    ir = compile_ir(
        """
        fn main() -> int32 {
            let a: char* = typename(int32);
            let b: char* = typename(int32);
            let c: char* = "int32";
            return 0;
        }
        """
    )
    defs = [line for line in ir.splitlines() if 'c"int32\\00"' in line]
    assert len(defs) == 1
    assert "private unnamed_addr constant" in defs[0]


def test_typename_is_the_tag_preimage():
    # The emitted literal is exactly the string the boxing tag hashes: the
    # same compilation carries c"int32\00" and the fnv1a64 of "int32" as the
    # box's tag constant.
    ir = compile_ir(
        """
        fn main() -> int32 {
            let a: any = 5;
            let n: char* = typename(int32);
            return 0;
        }
        """
    )
    assert 'c"int32\\00"' in ir
    assert str(fnv1a64("int32")) in ir


# ------------------------------------------------------------------- errors

def test_unknown_type_is_rejected_like_sizeof():
    with pytest.raises(LangError, match="unknown type 'nosuch'"):
        compile_ir("fn main() -> int32 { let n = typename(nosuch); return 0; }")


def test_malformed_generic_operand_is_rejected_like_sizeof():
    with pytest.raises(
        LangError, match="type 'slice' takes 1 type argument, got 2"
    ):
        compile_ir(
            "fn main() -> int32 { let n = typename(slice<int32, int32>); return 0; }"
        )


def test_typename_is_a_reserved_word():
    # Pre-1.0 breaking change, called out in the changelog: `typename` can
    # no longer be used as an identifier.
    with pytest.raises(LangError, match="expected 'IDENT', got 'typename'"):
        compile_ir("fn main() -> int32 { let typename = 1; return 0; }")


# --------------------------------------------------------------------- .mci

def test_typename_in_a_traveling_generic_refolds_through_mci(tmp_path):
    # A generic body ships verbatim in the interface stub; typename(T)
    # re-folds in the consumer, per instantiation.
    lib = tmp_path / "lib.mc"
    lib.write_text("fn name_of<T>() -> char* { return typename(T); }\n")
    out = tmp_path / "lib.mci"
    assert emit_interface(lib, (tmp_path,), None, {}, out) == 0
    assert "typename(T)" in out.read_text()
    lib.unlink()  # force the import to resolve through the stub
    main = tmp_path / "main.mc"
    main.write_text(
        'import "lib";\n' + STRCMP + "fn main() -> int32 {\n"
        '    if (strcmp(name_of<int64>(), "int64") != 0) { return 1; }\n'
        '    if (strcmp(name_of<bool>(), "bool") != 0)   { return 2; }\n'
        "    return 0;\n"
        "}\n"
    )
    assert run_path(main) == 0
