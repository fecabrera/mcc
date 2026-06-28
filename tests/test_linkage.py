"""Linkage: imported and generic definitions must merge across objects.

When several files `import` the same code and are compiled to separate
objects, the shared definitions appear in each object. They are emitted
with `linkonce_odr` linkage so the linker merges the identical copies
instead of reporting a multiple-definition error, while the root file's
own definitions stay external so a genuine name clash is still caught.
"""

from mcc.driver import compile_to_ir


def compile_file_ir(tmp_path, name, source, search_paths=None):
    path = tmp_path / name
    path.write_text(source)
    return str(compile_to_ir(path, search_paths))


def defining_line(ir_text, symbol):
    (line,) = [ln for ln in ir_text.splitlines()
               if ln.startswith("define") and f'@"{symbol}"' in ln]
    return line


def test_imported_generic_instance_is_linkonce(tmp_path):
    ir_text = compile_file_ir(
        tmp_path, "main.mc",
        'import "memory";\n'
        "fn main() -> int32 {\n"
        "    let p = alloc<uint8>(4);\n"
        "    dealloc(p);\n"
        "    return 0;\n"
        "}\n",
    )
    assert "linkonce_odr" in defining_line(ir_text, "alloc<uint8>")


def test_imported_non_generic_function_is_linkonce(tmp_path):
    (tmp_path / "lib.mc").write_text(
        "fn helper(x: int32) -> int32 { return x + 1; }"
    )
    ir_text = compile_file_ir(
        tmp_path, "main.mc",
        'import "lib";\n'
        "fn main() -> int32 { return helper(41); }\n",
        search_paths=(tmp_path,),
    )
    assert "linkonce_odr" in defining_line(ir_text, "helper")
    # The root file's own main stays external (no linkonce_odr).
    assert "linkonce_odr" not in defining_line(ir_text, "main")


def test_root_definitions_stay_external(tmp_path):
    ir_text = compile_file_ir(
        tmp_path, "main.mc",
        "fn box<T>(v: T) -> T { return v; }\n"
        "fn helper() -> int32 { return 7; }\n"
        "fn main() -> int32 { return box<int32>(helper()); }\n",
    )
    # Nothing here is imported, so every definition is this object's own.
    assert "linkonce_odr" not in ir_text


def global_line(ir_text, symbol):
    (line,) = [ln for ln in ir_text.splitlines()
               if ln.startswith(f'@"{symbol}"') and "global" in ln]
    return line


def test_imported_static_global_is_linkonce(tmp_path):
    # Regression: a @static global is copied into every object that imports its
    # module (its functions are). It must get linkonce_odr so the identically
    # mangled copies merge into a single instance -- `internal` would give each
    # object its own private storage, silently splitting the variable's state.
    (tmp_path / "lib.mc").write_text(
        "@static let counter: int32;\n"
        "fn bump() { counter = counter + 1; }\n"
        "fn count() -> int32 { return counter; }\n"
    )
    ir_text = compile_file_ir(
        tmp_path, "main.mc",
        'import "lib";\n'
        "fn main() -> int32 { bump(); return count(); }\n",
        search_paths=(tmp_path,),
    )
    line = global_line(ir_text, "counter.lib")
    assert "linkonce_odr" in line and "internal" not in line
