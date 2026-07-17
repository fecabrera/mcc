"""Ratchet lints: the two diagnostics rules for ``mcc/codegen`` (SIE-194).

This module is the citable home of the rules; the runtime side is enforced
by ``tests/test_diagnostics_conformance.py``.

1. **Errors carry their site.** A ``LangError`` must know which file its
   line number belongs to. Most existing sites omit ``source=`` and rely on
   ``CodeGen.generate()`` back-filling the ambient ``self.current_source``
   at the codegen boundary — which attributes the error to whatever file
   codegen happened to be walking last, not necessarily the file the error
   is about (the wrong-file bugs fixed twice in the SIE-101 review). New
   code must pass ``source=`` explicitly (``node.source`` / ``func.source``
   / ``decl.source``) instead of adding another ``current_source``
   save/restore or another bare raise. ``source=None`` and
   ``source=<anything>.current_source`` count as violations: both just
   re-spell the ambient back-fill.

2. **No internal keys in messages.** Compiler-internal keys (struct-table
   tuples, ``"<unresolved>"`` placeholders) must never be interpolated into
   a user-facing message verbatim — the SIE-189 tuple-repr leak. Render a
   user-level spelling instead.

Each census below is pinned **per file** at its census value and may only go
down: when your change removes sites, lower that file's pin (drop the entry
at 0); a failure that asks you to raise a pin — or to add an entry for a new
file — means the change reintroduces a footgun and should pass ``source=``
(or take an explicit scope parameter) instead. The census walks
``mcc/codegen`` recursively and keys pins by path relative to it, so a
module split into a subpackage keeps its sites counted under the new name
instead of silently leaving the census.

Known static limits, on purpose: a raise-helper that constructs the
``LangError`` inside a wrapper function hides its call sites from the
census (don't add one — thread ``source=`` instead), and ``*args``/
``**kwargs`` splats are given the benefit of the doubt (counted compliant)
so dynamic-but-compliant spellings can't fail the build.
"""

import ast
from collections import Counter
from functools import cache
from pathlib import Path

CODEGEN_DIR = Path(__file__).resolve().parent.parent / "mcc" / "codegen"

# Per-file census pins (SIE-194), keyed by path relative to mcc/codegen.
# Counts may only go down, per file; new files pin implicitly at 0.
CURRENT_SOURCE_ASSIGNMENT_PINS = {
    "generator.py": 62,
}
LANGERROR_WITHOUT_SOURCE_PINS = {
    "generator.py": 451,
    "targets.py": 3,
    "types.py": 4,
}


@cache
def _codegen_trees():
    """``(relative filename, parsed AST)`` for every module under mcc/codegen.

    Recursive (``rglob``), so a module moved into a subpackage stays in the
    census under its new relative name rather than silently leaving it.
    Cached: both ratchet tests share one read+parse sweep.
    """
    return [
        (
            path.relative_to(CODEGEN_DIR).as_posix(),
            ast.parse(path.read_text(), filename=str(path)),
        )
        for path in sorted(CODEGEN_DIR.rglob("*.py"))
    ]


def _flatten_targets(targets):
    """Yield leaf assignment targets, descending through tuple unpacks."""
    for target in targets:
        if isinstance(target, (ast.Tuple, ast.List)):
            yield from _flatten_targets(target.elts)
        elif isinstance(target, ast.Starred):
            yield target.value
        else:
            yield target


def _current_source_assignment_census():
    """Writes to ``current_source``, counted per file.

    Counts every plain/annotated/augmented assignment (through tuple
    unpacks) whose target is ``<anything>.current_source`` — not just
    ``self.`` — plus literal ``setattr(obj, "current_source", ...)`` calls
    and bare ``current_source`` name bindings. The name binding matters:
    a context-carrier dataclass field (``GenContext.current_source``) is an
    ambient-mutation surface too — ``GenContext.restore`` writes it back
    onto the generator via a dynamic ``setattr`` the walker cannot see, so
    the field declaration itself is what gets pinned.
    """
    files = []
    for name, tree in _codegen_trees():
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                targets = node.targets
            elif isinstance(node, (ast.AugAssign, ast.AnnAssign)):
                targets = [node.target]
            elif isinstance(node, ast.Call):
                if (
                    isinstance(node.func, ast.Name)
                    and node.func.id == "setattr"
                    and len(node.args) >= 2
                    and isinstance(node.args[1], ast.Constant)
                    and node.args[1].value == "current_source"
                ):
                    files.append(name)
                continue
            else:
                continue
            for target in _flatten_targets(targets):
                if (
                    isinstance(target, ast.Attribute)
                    and target.attr == "current_source"
                ) or (
                    isinstance(target, ast.Name)
                    and target.id == "current_source"
                ):
                    files.append(name)
    return Counter(files)


def _langerror_alias_names(tree):
    """Every name this module binds to the LangError class via imports."""
    names = {"LangError"}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name == "LangError":
                    names.add(alias.asname or alias.name)
    return names


def _is_ambient_source_value(value):
    """True when a source argument merely re-spells the ambient back-fill.

    ``source=None`` behaves exactly like omitting the argument
    (``generate()`` back-fills ``current_source`` onto it), and
    ``source=<anything>.current_source`` is the ambient value written out
    by hand — both are the footgun rule 1 exists to stop.
    """
    if isinstance(value, ast.Constant) and value.value is None:
        return True
    return isinstance(value, ast.Attribute) and value.attr == "current_source"


def _langerror_without_source_census():
    """``LangError(...)`` constructions lacking a real source, per file.

    Matches bare, import-aliased (``LangError as Err``), and
    attribute-qualified (``errors.LangError``) constructions. A third
    positional argument or a ``source=`` keyword counts as carrying the
    site — unless it is an ambient re-spelling (see
    :func:`_is_ambient_source_value`). ``**kwargs`` splats count as
    compliant (benefit of the doubt).
    """
    files = []
    for name, tree in _codegen_trees():
        aliases = _langerror_alias_names(tree)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            is_langerror = (
                isinstance(func, ast.Name) and func.id in aliases
            ) or (isinstance(func, ast.Attribute) and func.attr == "LangError")
            if not is_langerror:
                continue
            compliant = False
            if len(node.args) >= 3:
                arg = node.args[2]
                compliant = isinstance(arg, ast.Starred) or (
                    not _is_ambient_source_value(arg)
                )
            for kw in node.keywords:
                if kw.arg == "source":
                    compliant = not _is_ambient_source_value(kw.value)
                elif kw.arg is None:  # **splat: benefit of the doubt
                    compliant = True
            if not compliant:
                files.append(name)
    return Counter(files)


def _check_ratchet(census, pins, pins_name, fix_hint):
    """Assert the per-file census equals the pins, actionable both ways."""
    problems = []
    for name in sorted(set(census) | set(pins)):
        count, pin = census.get(name, 0), pins.get(name, 0)
        if count > pin:
            problems.append(
                f"{name}: {count} sites found but {pins_name} pins it at "
                f"{pin}. Counts may only go down: instead of adding a site, "
                f"{fix_hint}"
            )
        elif count < pin:
            problems.append(
                f"{name}: only {count} sites remain — nice, the ratchet "
                f"tightens: lower {pins_name}[{name!r}] to {count} (drop the "
                f"entry at 0) so the improvement can't regress. If the file "
                f"was renamed or split, move its pin to the new relative "
                f"path(s) instead — the census is recursive, so moved code "
                f"stays counted."
            )
    if problems:
        raise AssertionError(
            "\n".join(problems)
            + "\nSee this module's docstring for the rule this protects."
        )


def test_current_source_assignments_only_go_down():
    """Rule 1 ratchet: no new ``current_source`` ambient-mutation site."""
    _check_ratchet(
        _current_source_assignment_census(),
        CURRENT_SOURCE_ASSIGNMENT_PINS,
        "CURRENT_SOURCE_ASSIGNMENT_PINS",
        "thread the owning file explicitly — take the declaration's "
        "source (or a scope object carrying it) as a parameter rather than "
        "mutating the ambient current_source.",
    )


def test_langerror_raises_carry_their_source():
    """Rule 1 ratchet: new ``LangError``s must say which file they're about."""
    _check_ratchet(
        _langerror_without_source_census(),
        LANGERROR_WITHOUT_SOURCE_PINS,
        "LANGERROR_WITHOUT_SOURCE_PINS",
        "pass source= explicitly (e.g. LangError(msg, node.line, "
        "source=node.source)) so the error carries its site instead of "
        "inheriting whatever file current_source last pointed at — "
        "source=None and source=...current_source do not count.",
    )
