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
   / ``decl.source``) instead of adding another ``self.current_source =``
   save/restore or another bare raise.

2. **No internal keys in messages.** Compiler-internal keys (struct-table
   tuples, ``"<unresolved>"`` placeholders) must never be interpolated into
   a user-facing message verbatim — the SIE-189 tuple-repr leak. Render a
   user-level spelling instead.

Each count below is pinned at its census value and may only go down: when
your change removes sites, lower the pin; a failure that asks you to raise a
pin means the change reintroduces a footgun and should pass ``source=`` (or
take an explicit scope parameter) instead.
"""

import ast
from pathlib import Path

CODEGEN_DIR = Path(__file__).resolve().parent.parent / "mcc" / "codegen"

# Census pins (SIE-194). Only lower these, never raise them.
MAX_CURRENT_SOURCE_ASSIGNMENTS = 61
MAX_LANGERROR_WITHOUT_SOURCE = 458


def _codegen_trees():
    """Yield ``(filename, parsed AST)`` for every module in mcc/codegen."""
    for path in sorted(CODEGEN_DIR.glob("*.py")):
        yield path.name, ast.parse(path.read_text(), filename=str(path))


def _current_source_assignment_sites():
    """Every ``self.current_source = ...`` assignment, as (file, line)."""
    sites = []
    for name, tree in _codegen_trees():
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                targets = node.targets
            elif isinstance(node, (ast.AugAssign, ast.AnnAssign)):
                targets = [node.target]
            else:
                continue
            for target in targets:
                if (
                    isinstance(target, ast.Attribute)
                    and target.attr == "current_source"
                    and isinstance(target.value, ast.Name)
                    and target.value.id == "self"
                ):
                    sites.append((name, node.lineno))
    return sites


def _langerror_sites_without_source():
    """Every ``LangError(...)`` construction lacking a source, as (file, line).

    A third positional argument or a ``source=`` keyword counts as carrying
    the site; everything else inherits the ambient ``current_source`` at the
    ``generate()`` boundary.
    """
    sites = []
    for name, tree in _codegen_trees():
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "LangError"
            ):
                has_source = len(node.args) >= 3 or any(
                    kw.arg == "source" for kw in node.keywords
                )
                if not has_source:
                    sites.append((name, node.lineno))
    return sites


def _check_ratchet(sites, pin, pin_name, fix_hint):
    """Assert the census equals the pin, with actionable messages both ways."""
    count = len(sites)
    if count > pin:
        per_file = {}
        for name, _ in sites:
            per_file[name] = per_file.get(name, 0) + 1
        breakdown = ", ".join(f"{n}: {c}" for n, c in sorted(per_file.items()))
        raise AssertionError(
            f"{count} sites found but the ratchet pins {pin_name} at {pin} "
            f"({breakdown}). This count may only go down: instead of adding "
            f"another site, {fix_hint} See this module's docstring for the "
            f"rule this protects."
        )
    if count < pin:
        raise AssertionError(
            f"only {count} sites remain — nice, the ratchet tightens: lower "
            f"{pin_name} in tests/test_codegen_ratchet.py to {count} so the "
            f"improvement can't regress."
        )


def test_current_source_assignments_only_go_down():
    """Rule 1 ratchet: no new ``self.current_source = ...`` save/restore."""
    _check_ratchet(
        _current_source_assignment_sites(),
        MAX_CURRENT_SOURCE_ASSIGNMENTS,
        "MAX_CURRENT_SOURCE_ASSIGNMENTS",
        "thread the owning file explicitly — take the declaration's "
        "source (or a scope object carrying it) as a parameter rather than "
        "mutating the ambient current_source.",
    )


def test_langerror_raises_carry_their_source():
    """Rule 1 ratchet: new ``LangError``s must say which file they're about."""
    _check_ratchet(
        _langerror_sites_without_source(),
        MAX_LANGERROR_WITHOUT_SOURCE,
        "MAX_LANGERROR_WITHOUT_SOURCE",
        "pass source= explicitly (e.g. LangError(msg, node.line, "
        "source=node.source)) so the error carries its site instead of "
        "inheriting whatever file current_source last pointed at.",
    )
