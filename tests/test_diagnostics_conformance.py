"""Diagnostics conformance: errors carry their site, no internal keys leak.

Runtime enforcement of the two rules pinned by ``tests/test_codegen_ratchet.py``
(SIE-194; see that module's docstring for the full statement):

1. **Errors carry their site.** Every ``LangError`` escaping codegen for a
   file-compiled program names the file the offending line belongs to — the
   file the error is *about*, not whatever file codegen was walking last
   (the wrong-file attribution bugs from the SIE-101 review). The corpus
   below compiles multi-file programs in both stale-``current_source``
   directions: an error inside an imported module, and an error in the main
   file after imports have been compiled.

2. **No internal keys in messages.** No user-facing message contains a
   compiler-internal key shape — the ``("<unresolved>", 'b<T>')`` tuple-repr
   leak of SIE-189. Every ``LangError`` constructed while the corpus
   compiles (including speculative ones that are caught and swallowed) is
   shape-checked, not just the one that escapes.
"""

import re
from contextlib import contextmanager
from pathlib import Path

import pytest

from mcc.driver import compile_to_ir
from mcc.errors import LangError

# Message shapes that mean an internal key leaked into user-facing text:
# the "<unresolved>" struct-key placeholder, and the repr of a tuple key
# (an opening paren immediately followed by a quoted string and a comma).
INTERNAL_KEY_SHAPES = [
    ("'<unresolved>' placeholder", re.compile(r"<unresolved>")),
    ("bare tuple repr", re.compile(r"\((['\"])[^'\"]*\1,")),
]


def assert_no_internal_keys(message: str):
    for shape_name, shape in INTERNAL_KEY_SHAPES:
        assert not shape.search(message), (
            f"internal key leaked into a user-facing diagnostic "
            f"({shape_name}): {message!r} — render the user-level spelling "
            f"instead of interpolating the compiler-internal key."
        )


@contextmanager
def recording_lang_errors():
    """Record every LangError constructed inside the block, even swallowed ones."""
    recorded = []
    original = LangError.__init__

    def wrapped(self, message, line, source=None):
        recorded.append(message)
        original(self, message, line, source)

    LangError.__init__ = wrapped
    try:
        yield recorded
    finally:
        LangError.__init__ = original


LIB = """
@private
fn secret() -> int32 { return 7; }

fn helper() -> int32 { return 40; }

fn get_count<T>(v: T) -> int32 {
    return v.count;
}
"""

BROKEN_LIB = """
fn broken() -> int32 {
    return no_such_ident;
}
"""

# Each corpus case: the files to write, the file the error must be
# attributed to, and the expected message/line — representative of the
# codegen diagnostics a user actually hits, compiled from real files so
# attribution is observable.
CORPUS = {
    "single_file_type_error": (
        {"main.mc": "fn main() -> int32 { let s: int32* = 5; return 0; }"},
        "main.mc",
        r"let s: expected int32\*, got int32",
        1,
    ),
    "single_file_unknown_call": (
        {"main.mc": "fn main() -> int32 { return no_such_fn(); }"},
        "main.mc",
        r"undefined function 'no_such_fn' \(missing import\?\)",
        1,
    ),
    # Stale-current_source direction 1: the error is inside an imported
    # module's body; it must be attributed to lib.mc, not the entry file.
    "error_in_imported_body": (
        {
            "lib.mc": BROKEN_LIB,
            "main.mc": 'import "lib";\nfn main() -> int32 { return 0; }',
        },
        "lib.mc",
        r"undefined variable 'no_such_ident'",
        3,
    ),
    # Stale-current_source direction 2: imports compiled fine, the error is
    # in the entry file; it must be attributed to main.mc, not the last
    # import codegen walked.
    "error_in_main_after_imports": (
        {
            "lib.mc": LIB,
            "main.mc": 'import "lib";\n'
            "fn main() -> int32 { return helper() + missing; }",
        },
        "main.mc",
        r"undefined variable 'missing'",
        2,
    ),
    # Cross-file diagnosis at the call site: the violation happens in
    # main.mc even though the private function lives in lib.mc.
    "private_cross_file": (
        {
            "lib.mc": LIB,
            "main.mc": 'import "lib";\nfn main() -> int32 { return secret(); }',
        },
        "main.mc",
        r"function 'secret' is private to lib\.mc",
        2,
    ),
    # A stdlib-heavy compile: merging std/io pushes current_source through
    # many files before the error in the one-line entry file.
    "stdlib_bound_violation": (
        {
            "main.mc": 'import "std/io";\n'
            "fn main() -> int32 { println(main); return 0; }",
        },
        "main.mc",
        r"does not satisfy the bound slice<const char> of 'println'",
        2,
    ),
}


@pytest.mark.parametrize("case", CORPUS)
def test_escaping_error_is_attributed_to_its_file(case, tmp_path):
    files, expected_file, message_re, expected_line = CORPUS[case]
    for name, text in files.items():
        (tmp_path / name).write_text(text)
    with recording_lang_errors() as recorded:
        with pytest.raises(LangError, match=message_re) as excinfo:
            compile_to_ir(tmp_path / "main.mc")
    err = excinfo.value
    assert err.source is not None, (
        "a LangError escaping codegen for a file-compiled program must "
        "carry its source file — pass source= at the raise site."
    )
    assert Path(err.source) == tmp_path / expected_file, (
        f"error attributed to {err.source!r}, but the offending line lives "
        f"in {expected_file} — the raise site inherited a stale "
        f"current_source; pass source= explicitly."
    )
    assert err.line == expected_line
    for message in recorded:
        assert_no_internal_keys(message)
    for note in err.notes:
        assert_no_internal_keys(note.message)


def test_monomorphization_error_attributes_template_and_instantiation(tmp_path):
    """A generic body error names the template's file; the instantiation
    note names the caller's file — both sides of the attribution must
    survive the monomorphization detour through another module."""
    (tmp_path / "lib.mc").write_text(LIB)
    main = tmp_path / "main.mc"
    main.write_text(
        'import "lib";\nfn main() -> int32 { return get_count(3 as int32); }'
    )
    with recording_lang_errors() as recorded:
        with pytest.raises(LangError, match="int32 is not a struct") as excinfo:
            compile_to_ir(main)
    err = excinfo.value
    assert err.source is not None and Path(err.source) == tmp_path / "lib.mc"
    assert err.line == 8  # `return v.count;` inside the template body
    assert err.notes, "monomorphization errors carry instantiation notes"
    note = err.notes[0]
    assert note.message == "in instantiation of get_count<int32>"
    assert note.source is not None and Path(note.source) == main
    assert note.line == 2
    for message in recorded:
        assert_no_internal_keys(message)


def test_internal_key_shapes_catch_the_sie189_leak():
    """Self-test: the shapes match the historical leaks they exist to stop."""
    leaked = (
        "return-type covariance on generic hierarchies is not supported: "
        "(\"<unresolved>\", 'b<T>') overrides ('a', 'a<T>')"
    )
    with pytest.raises(AssertionError):
        assert_no_internal_keys(leaked)
    # And they pass ordinary diagnostics, including quoted names and tuples
    # spelled at the user level.
    assert_no_internal_keys("function 'secret' is private to lib.mc")
    assert_no_internal_keys("expected (int32, char), got (int32, int32)")
