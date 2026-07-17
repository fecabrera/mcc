"""Diagnostics conformance: errors carry their site, no internal keys leak.

Runtime enforcement of the two rules pinned by ``tests/test_codegen_ratchet.py``
(SIE-194; see that module's docstring for the full statement):

1. **Errors carry their site.** Every ``LangError`` escaping codegen for a
   file-compiled program names the file the offending line belongs to — the
   file the error is *about*, not whatever file codegen was walking last
   (the wrong-file attribution bugs from the SIE-101 review). The corpus
   below compiles multi-file programs in both stale-``current_source``
   directions: an error inside an imported module, and an error in the main
   file after imports have been compiled. Honesty note: today most raise
   sites are sourceless and these cases pass via ``generate()``'s
   ``current_source`` back-fill — the corpus pins the observable behavior,
   whichever mechanism provides it. It cannot see a wrong ``source=`` at an
   individual raise site that the back-fill would have gotten right; the
   per-site pressure is the ratchet's job.

2. **No internal keys in messages.** No user-facing message contains a
   compiler-internal key shape — the ``("<unresolved>", 'b<T>')`` tuple-repr
   leak of SIE-189. Every ``LangError`` message and every ``Note`` message
   (instantiation frames on any error, escaping or swallowed, and the
   warnings channel — both are ``Note`` constructions) recorded while the
   corpus compiles is shape-checked automatically when the recorder exits,
   not just the error that escapes. The corpus drives the override/
   covariance validation path (``validate_dispatch_overrides``) that
   produced the historical SIE-189 leak on a GENERIC hierarchy — the only
   shape whose return spellings hit ``return_abi``'s ``("<unresolved>",
   ...)`` fallback key — so a reintroduction at the original site fails
   here, not just in the regex self-test.

The corpus asserts attribution (``err.source``/``err.line``) plus a minimal
stable token of each message — enough to prove the intended diagnostic
fired, without pinning wording owned by the per-diagnostic codegen tests.
"""

import re
from contextlib import contextmanager
from pathlib import Path

import pytest
from helpers import compile_files

from mcc.errors import LangError, Note

# Message shapes that mean an internal key leaked into user-facing text:
# the "<unresolved>" struct-key placeholder, and the repr of a tuple key.
# Codegen tables are keyed by the 2-tuple (current_source, name), so the
# tuple shape targets exactly that: a quoted, word/path-like first element
# followed by one quoted second element and the closing paren. The open
# paren must NOT follow an identifier (that spelling is a call, e.g.
# "foo('a', 'b')") — but a preceding QUOTE must still match, because the
# dominant codegen spelling interpolates into a quoted message
# (f"no such struct '{key}'" renders "...'('/w/lib.mc', 'point')'"), and
# that leak must be caught. The first element must look like a *file* —
# a resolved current_source always carries at least one of `< > / \ .`
# (a path separator, an extension dot, or the "<...>" placeholder shape) —
# so a user-level pair of plain words like "expected ('a', 'b')" or of
# punctuation literals like (',', ' ') passes, and longer tuples like
# ('r', 'w', 'a') don't match — table keys are pairs. current_source can
# be None for string-compiled programs, so a leak can also render as
# "(None, 'point')" — its own shape below, constrained to the same
# quoted-second-element pair and the same not-after-an-identifier guard,
# so a user call like "foo(None, 3)" or "foo(None, 'a')" is not a false
# positive.
INTERNAL_KEY_SHAPES = [
    ("'<unresolved>' placeholder", re.compile(r"<unresolved>")),
    (
        "table-key tuple repr",
        re.compile(
            r"(?<!\w)\((['\"])[\w<>./\\ :-]*[<>/\\.][\w<>./\\ :-]*\1,"
            r"\s*(['\"])[^'\"]*\2\)"
        ),
    ),
    (
        "None-keyed table tuple",
        re.compile(r"(?<!\w)\(None,\s*(['\"])[^'\"]*\1\)"),
    ),
]


def assert_no_internal_keys(message: str):
    for shape_name, shape in INTERNAL_KEY_SHAPES:
        assert not shape.search(message), (
            f"internal key leaked into a user-facing diagnostic "
            f"({shape_name}): {message!r} — render the user-level spelling "
            f"instead of interpolating the compiler-internal key."
        )


@contextmanager
def recording_diagnostics():
    """Record every diagnostic message constructed inside the block.

    Wraps ``LangError.__init__`` and ``Note.__init__`` (notes cover both
    instantiation frames and the warnings channel), recording each message
    after delegating — signature-agnostic, so the shims survive signature
    evolution in ``mcc.errors``. On clean exit, every recorded message is
    shape-checked against :data:`INTERNAL_KEY_SHAPES`; a test using this
    recorder cannot forget rule 2. Messages on speculative errors that
    codegen catches and swallows are recorded (and checked) too. When the
    block itself fails (say, an expected error never raised), that failure
    propagates untouched — the shape sweep is skipped rather than raised
    from a ``finally`` where it would mask the real diagnosis.
    """
    recorded = []
    originals = {cls: cls.__init__ for cls in (LangError, Note)}

    def recording(original):
        def wrapped(self, *args, **kwargs):
            original(self, *args, **kwargs)
            recorded.append(self.message)

        return wrapped

    for cls, original in originals.items():
        cls.__init__ = recording(original)
    try:
        yield recorded
    finally:
        for cls, original in originals.items():
            cls.__init__ = original
    for message in recorded:
        assert_no_internal_keys(message)


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

# A base with a dispatchable method, for the override/covariance corpus
# case — the diagnostic family (validate_dispatch_overrides) that produced
# the SIE-189 internal-key leak.
VIEWS_LIB = """
struct base { n: int32; }

fn base::val(const self: &base) -> int32 { return 7; }
"""

# A GENERIC base hierarchy for the SIE-189 corpus case proper: the internal
# ("<unresolved>", ...) fallback key exists only for struct-generic return
# spellings that fail to resolve pre-body, so only a generic family can
# re-leak it — the non-generic case above covers plain attribution only.
GENERIC_VIEWS_LIB = """
struct gbase<T> { v: T; }

fn gbase<T>::get(const self: &gbase<T>) -> T { return self.v; }
"""

# Each corpus case: the files to write, the file the error must be
# attributed to, and the expected message token/line — representative of
# the codegen diagnostics a user actually hits, compiled from real files so
# attribution is observable. Message regexes are minimal stable tokens
# (identifier + diagnostic kind); exact wording belongs to the
# per-diagnostic codegen tests.
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
        r"undefined function 'no_such_fn'",
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
    # many files before the error in the one-line entry file. The token
    # deliberately omits the bound's spelling — that belongs to std/io.
    "stdlib_bound_violation": (
        {
            "main.mc": 'import "std/io";\n'
            "fn main() -> int32 { println(main); return 0; }",
        },
        "main.mc",
        r"does not satisfy the bound .+ of 'println'",
        2,
    ),
    # The SIE-189 diagnostic family: an @override return mismatch, validated
    # in validate_dispatch_overrides, whose messages render return types via
    # ret_desc — the exact surface that historically leaked the raw
    # ("<unresolved>", ...) table key. The base lives in views.mc; the error
    # must be attributed to the override's file, main.mc.
    "override_return_mismatch_cross_file": (
        {
            "views.mc": VIEWS_LIB,
            "main.mc": 'import "views";\n'
            "struct derived extends base { m: int32; }\n"
            "@override fn derived::val(const self: &derived) -> float64 "
            "{ return 3.0; }\n"
            "fn main() -> int32 { return 0; }",
        },
        "main.mc",
        r"@override method 'derived::val' returns float64",
        3,
    ),
    # The SIE-189 leak surface proper: a GENERIC hierarchy, where both
    # return spellings resolve only per-instantiation, so ret_desc must
    # render the source spellings — never return_abi's ("<unresolved>", ...)
    # fallback key. A reintroduced leak trips the recorder's shape check on
    # this case's message.
    "generic_override_return_mismatch_cross_file": (
        {
            "gviews.mc": GENERIC_VIEWS_LIB,
            "main.mc": 'import "gviews";\n'
            "struct gderived<T> extends gbase<T> { w: T; }\n"
            "@override fn gderived<T>::get(const self: &gderived<T>) "
            "-> gderived<T> { return self; }\n"
            "fn main() -> int32 { return 0; }",
        },
        "main.mc",
        r"@override method 'gderived::get' returns gderived<T>",
        3,
    ),
}


@pytest.mark.parametrize("case", CORPUS)
def test_escaping_error_is_attributed_to_its_file(case, tmp_path):
    files, expected_file, message_re, expected_line = CORPUS[case]
    with recording_diagnostics():
        with pytest.raises(LangError, match=message_re) as excinfo:
            compile_files(tmp_path, files)
    err = excinfo.value
    assert err.source is not None, (
        "a LangError escaping codegen for a file-compiled program must "
        "carry its source file — either from source= at the raise site or "
        "from the current_source back-fill at the codegen boundary."
    )
    assert Path(err.source) == tmp_path / expected_file, (
        f"error attributed to {err.source!r}, but the offending line lives "
        f"in {expected_file} — either the ambient current_source machinery "
        f"went stale or a raise site passed the wrong source. The durable "
        f"fix is threading source= explicitly at the raise site, not "
        f"another current_source save/restore."
    )
    assert err.line == expected_line


def test_monomorphization_error_attributes_template_and_instantiation(tmp_path):
    """A generic body error names the template's file; the instantiation
    note names the caller's file — both sides of the attribution must
    survive the monomorphization detour through another module."""
    main = tmp_path / "main.mc"
    files = {
        "lib.mc": LIB,
        "main.mc": 'import "lib";\n'
        "fn main() -> int32 { return get_count(3 as int32); }",
    }
    with recording_diagnostics():
        with pytest.raises(LangError, match="int32 is not a struct") as excinfo:
            compile_files(tmp_path, files)
    err = excinfo.value
    assert err.source is not None and Path(err.source) == tmp_path / "lib.mc"
    assert err.line == 8  # `return v.count;` inside the template body
    assert err.notes, "monomorphization errors carry instantiation notes"
    note = err.notes[0]
    # Token, not wording: the note must name the instantiation; its exact
    # phrasing belongs to the codegen tests that own the message.
    assert "get_count<int32>" in note.message
    assert note.source is not None and Path(note.source) == main
    assert note.line == 2


def test_internal_key_shapes_catch_the_sie189_leak():
    """Self-test: the shapes match the historical leaks they exist to stop."""
    leaked = (
        "return-type covariance on generic hierarchies is not supported: "
        "(\"<unresolved>\", 'b<T>') overrides ('a', 'a<T>')"
    )
    with pytest.raises(AssertionError):
        assert_no_internal_keys(leaked)
    # A file-keyed table tuple — the (current_source, name) pair rendered
    # with a real resolved path — is the canonical leak shape.
    with pytest.raises(AssertionError):
        assert_no_internal_keys("no such struct ('/w/lib.mc', 'point')")
    # The same key interpolated into a quoted message — codegen's dominant
    # spelling, f"no such struct '{key}'" — must still be caught: the
    # opening paren sits right after a quote, which the shape must not
    # treat as a call spelling.
    with pytest.raises(AssertionError):
        assert_no_internal_keys("no such struct '('/w/lib.mc', 'point')'")
    # A None-keyed table tuple — (current_source, name) with current_source
    # None — is the same leak class in a different costume.
    with pytest.raises(AssertionError):
        assert_no_internal_keys("no such struct (None, 'point')")
    # And they pass ordinary diagnostics: quoted names, tuples spelled at
    # the user level, and quoted arguments in a call spelling.
    assert_no_internal_keys("function 'secret' is private to lib.mc")
    assert_no_internal_keys("expected (int32, char), got (int32, int32)")
    assert_no_internal_keys("call to foo('a', 'b') is ambiguous")
    # Quoted-literal tuples a diagnostic may legitimately render: table keys
    # are pairs whose first element is file-like (path chars, an extension
    # dot, or a <placeholder>), so pairs of plain words, punctuation
    # elements, and longer tuples are user-level spellings, not leaks.
    assert_no_internal_keys("expected ('a', 'b')")
    assert_no_internal_keys("candidates ('int32', 'char')")
    assert_no_internal_keys("expected (',', ' ')")
    assert_no_internal_keys("expected one of ('r', 'w', 'a')")
    # A user call echoing the identifier None as its first argument is not a
    # None-keyed table leak: the table shape is a pair, so a bare "(None,"
    # in a call spelling must pass — including with a quoted second
    # argument, which only the not-after-an-identifier guard distinguishes
    # from a real (None, 'point') table key.
    assert_no_internal_keys("call to foo(None, 3) is ambiguous")
    assert_no_internal_keys("call to foo(None, 'a') is ambiguous")
    # The quoted-interpolation costume of the None-keyed leak — the open
    # paren sits after a quote, not an identifier — must still be caught.
    with pytest.raises(AssertionError):
        assert_no_internal_keys("no such struct '(None, 'point')'")
