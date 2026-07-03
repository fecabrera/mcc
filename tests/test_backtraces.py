"""Instantiation backtraces: the note chain attached to a ``LangError``.

An error inside a monomorphized body carries ``in instantiation of ...``
:class:`~mcc.errors.Note` frames, innermost first, tracing how the compiler
reached it. The primary error text (``str(LangError)``) never includes the
notes -- the whole suite's ``str`` matches depend on that.
"""

import pytest

from mcc.errors import LangError
from helpers import compile_ir


def notes_for(source: str) -> tuple[LangError, list]:
    """Compile a failing source string and return (error, its notes)."""
    with pytest.raises(LangError) as excinfo:
        compile_ir(source)
    return excinfo.value, excinfo.value.notes


# -------------------------------------------------------------- frame capture


def test_generic_function_frame():
    err, notes = notes_for(
        "fn f<T>(x: T) -> T {\n"
        "    return oops;\n"
        "}\n"
        "fn main() -> int32 {\n"
        "    return f(1);\n"
        "}\n"
    )
    assert str(err) == "line 2: undefined variable 'oops'"
    assert [(n.message, n.line) for n in notes] == [
        ("in instantiation of f<int32>", 5),
    ]


def test_generic_struct_frame():
    _, notes = notes_for(
        "struct box<T> {\n"
        "    p: badtype;\n"
        "}\n"
        "fn main() -> int32 {\n"
        "    let b: box<int32>;\n"
        "    return 0;\n"
        "}\n"
    )
    assert [(n.message, n.line) for n in notes] == [
        ("in instantiation of box<int32>", 5),
    ]


def test_interleaved_function_and_struct_frames():
    # A generic function whose body instantiates a generic struct: the struct
    # frame (innermost) comes first, then the function frame, each carrying
    # its own request line.
    _, notes = notes_for(
        "struct box<T> {\n"
        "    p: badtype;\n"
        "}\n"
        "fn get<T>(v: T) -> int32 {\n"
        "    let b: box<T>;\n"
        "    return 0;\n"
        "}\n"
        "fn main() -> int32 {\n"
        "    return get(7);\n"
        "}\n"
    )
    assert [(n.message, n.line) for n in notes] == [
        ("in instantiation of box<int32>", 5),
        ("in instantiation of get<int32>", 9),
    ]


def test_alias_frame_names_the_alias():
    # A chain through a type alias gets a frame for the alias itself, so a
    # trace through `string` (= list<char>) says `string`. The aliased
    # struct's frame points at the alias declaration line, where its target
    # is spelled; the alias frame points at the use site.
    _, notes = notes_for(
        "struct list<T> {\n"
        "    p: badtype;\n"
        "    x: T;\n"
        "}\n"
        "type string = list<char>;\n"
        "fn main() -> int32 {\n"
        "    let s: struct string;\n"
        "    return 0;\n"
        "}\n"
    )
    assert [(n.message, n.line) for n in notes] == [
        ("in instantiation of list<char>", 5),
        ("in instantiation of string", 7),
    ]


# ------------------------------------------------------- empty chain / caching


def test_error_outside_instantiation_has_no_notes():
    err, notes = notes_for("fn main() -> int32 { return oops; }")
    assert str(err) == "line 1: undefined variable 'oops'"
    assert notes == []


def test_recursive_call_reports_the_first_triggering_path():
    # An instance is memoized before its body generates, so the recursive
    # call inside f's own body is a cache hit that captures no frame: the
    # chain reports only the first triggering path (as C++/Rust do), one
    # frame at main's call, not a second at the self-call.
    _, notes = notes_for(
        "fn f<T>(x: T) -> int32 {\n"
        "    f(x);\n"
        "    return oops;\n"
        "}\n"
        "fn main() -> int32 {\n"
        "    return f(1);\n"
        "}\n"
    )
    assert [(n.message, n.line) for n in notes] == [
        ("in instantiation of f<int32>", 6),
    ]


def test_cached_struct_instance_adds_no_frame():
    # box<int32> is instantiated (successfully) before the failing call, so
    # the mention inside use<int32>'s body is a cache hit: only the function
    # frame appears in the chain.
    _, notes = notes_for(
        "struct box<T> {\n"
        "    x: T;\n"
        "}\n"
        "fn use<T>(v: T) -> int32 {\n"
        "    let b: box<T>;\n"
        "    return oops;\n"
        "}\n"
        "fn main() -> int32 {\n"
        "    let a: box<int32>;\n"
        "    return use(1);\n"
        "}\n"
    )
    assert [(n.message, n.line) for n in notes] == [
        ("in instantiation of use<int32>", 10),
    ]


# ------------------------------------------------------------------ test-safety


def test_str_never_includes_notes():
    # ~250 tests match against str(LangError); a note chain must not change
    # the primary error text.
    err, notes = notes_for(
        "fn f<T>(x: T) -> T {\n"
        "    return oops;\n"
        "}\n"
        "fn main() -> int32 {\n"
        "    return f(1);\n"
        "}\n"
    )
    assert notes  # frames were captured...
    assert str(err) == "line 2: undefined variable 'oops'"  # ...but str is bare
    assert "instantiation" not in str(err)
