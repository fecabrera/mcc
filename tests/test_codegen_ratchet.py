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
census (don't add one — thread ``source=`` instead); ``*args``/
``**kwargs`` splats are given the benefit of the doubt (counted compliant)
so dynamic-but-compliant spellings can't fail the build — except explicit
spellings, which are judged no matter what splats surround them: a
``source=`` keyword, and any positional that can land in the source slot
(``args[2]``, or one written after a ``*splat``); an alias stored on an
attribute (``self.E = LangError``; ``raise self.E(...)``) is not chased —
only ``Name`` bindings join the alias set; a local that merely aliases
the ambient value
(``saved = self.current_source`` threaded into ``source=saved``) is
accepted as real, the census does not chase dataflow; and the census
stops at ``mcc/codegen`` — code moved OUT of the package leaves it, so an
extraction (SIE-193) must widen the census boundary along with the move,
not just delete the orphaned pin.
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


def _codegen_trees():
    """``(relative filename, parsed AST)`` for every module under mcc/codegen.

    Recursive (``rglob``), so a module moved into a subpackage stays in the
    census under its new relative name rather than silently leaving it.
    """
    return [
        (
            path.relative_to(CODEGEN_DIR).as_posix(),
            ast.parse(path.read_text(), filename=str(path)),
        )
        for path in sorted(CODEGEN_DIR.rglob("*.py"))
    ]


@cache
def _censuses():
    """Both per-file censuses from one read+parse sweep.

    The cache holds the two small Counters, not the parsed trees —
    generator.py's AST alone is tens of MB, and pinning it for the rest of
    the pytest session would buy nothing once the counts exist.
    """
    trees = _codegen_trees()
    return (
        _current_source_assignment_census(trees),
        _langerror_without_source_census(trees),
    )


def _flatten_targets(targets):
    """Yield leaf assignment targets, descending through tuple unpacks."""
    for target in targets:
        if isinstance(target, (ast.Tuple, ast.List)):
            yield from _flatten_targets(target.elts)
        elif isinstance(target, ast.Starred):
            yield target.value
        else:
            yield target


def _current_source_assignment_census(trees):
    """Writes to ``current_source``, counted per file.

    Counts every binding form (through tuple unpacks) whose target is
    ``<anything>.current_source`` — not just ``self.`` — or a bare
    ``current_source`` name: plain/annotated/augmented assignment, walrus,
    ``for`` targets, ``with ... as``, comprehension targets, plus literal
    ``setattr``/``obj.__setattr__``/``object.__setattr__`` calls naming
    ``"current_source"`` (the frozen-dataclass spelling). The name binding
    matters: a context-carrier dataclass field
    (``GenContext.current_source``) is an ambient-mutation surface too —
    ``GenContext.restore`` writes it back onto the generator via a dynamic
    ``setattr`` the walker cannot see, so the field declaration itself is
    what gets pinned.
    """
    files = []
    for name, tree in trees:
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                targets = node.targets
            elif isinstance(
                node,
                (
                    ast.AugAssign,
                    ast.AnnAssign,
                    ast.NamedExpr,
                    ast.For,
                    ast.AsyncFor,
                ),
            ):
                targets = [node.target]
            elif isinstance(node, ast.withitem):
                if node.optional_vars is None:
                    continue
                targets = [node.optional_vars]
            elif isinstance(node, ast.comprehension):
                targets = [node.target]
            elif isinstance(node, ast.Call):
                is_setattr = (
                    isinstance(node.func, ast.Name)
                    and node.func.id == "setattr"
                ) or (
                    isinstance(node.func, ast.Attribute)
                    and node.func.attr in ("setattr", "__setattr__")
                )
                # The attribute name sits at args[1] for setattr(obj, n, v)
                # and object.__setattr__(obj, n, v), at args[0] for the
                # bound obj.__setattr__(n, v) — check both slots.
                if is_setattr and any(
                    isinstance(arg, ast.Constant)
                    and arg.value == "current_source"
                    for arg in node.args[:2]
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


def _names_langerror(node, aliases):
    """True when an expression names the LangError class (or an alias)."""
    return (isinstance(node, ast.Name) and node.id in aliases) or (
        isinstance(node, ast.Attribute) and node.attr == "LangError"
    )


def _langerror_alias_names(tree):
    """Every name this module binds to the LangError class.

    Imports (``from ... import LangError [as X]``), module-level rebindings
    (``Err = LangError``), and subclasses (``class E(LangError)``) all
    join the alias set — a subclass constructor raises the same footgun.
    One walk collects the candidate binding nodes; the fixpoint then runs
    over that short list, so chains (``Err2 = Err``, ``class E2(E1)``)
    stay covered regardless of definition order without re-walking the
    whole tree per iteration.
    """
    names = {"LangError"}
    # Each entry is (value, targets): plain ``Err = LangError`` and the
    # annotated spelling ``Err: type = LangError`` (an AnnAssign, whose
    # single target is normalized into a one-element list) both rebind an
    # alias and must be chased.
    assigns, classes = [], []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            names |= {
                alias.asname or alias.name
                for alias in node.names
                if alias.name == "LangError"
            }
        elif isinstance(node, ast.Assign):
            assigns.append((node.value, node.targets))
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            assigns.append((node.value, [node.target]))
        elif isinstance(node, ast.ClassDef):
            classes.append(node)
    changed = True
    while changed:
        changed = False
        for value, targets in assigns:
            if _names_langerror(value, names):
                found = {
                    target.id
                    for target in _flatten_targets(targets)
                    if isinstance(target, ast.Name)
                }
                if not found <= names:
                    names |= found
                    changed = True
        for node in classes:
            if node.name not in names and any(
                _names_langerror(base, names) for base in node.bases
            ):
                names.add(node.name)
                changed = True
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


def _langerror_without_source_census(trees):
    """``LangError(...)`` constructions lacking a real source, per file.

    Matches bare, import-aliased (``LangError as Err``), rebound,
    subclassed, and attribute-qualified (``errors.LangError``)
    constructions. A third positional argument or a ``source=`` keyword
    counts as carrying the site — unless it is an ambient re-spelling (see
    :func:`_is_ambient_source_value`). Splats get the benefit of the doubt
    only where the source slot has no explicit spelling
    (``LangError(*parts)``, a trailing ``**kwargs``); any explicit value
    that can land in the source slot — ``args[2]``, or a positional
    written after a ``*splat`` — is judged like an explicit ``source=``
    keyword, so no surrounding splat can launder an ambient value back to
    compliant.
    """
    files = []
    for name, tree in trees:
        aliases = _langerror_alias_names(tree)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if not _names_langerror(node.func, aliases):
                continue
            source_kw = next(
                (kw for kw in node.keywords if kw.arg == "source"), None
            )
            if source_kw is not None:
                compliant = not _is_ambient_source_value(source_kw.value)
            else:
                # Explicit positionals that can land in the source slot:
                # index >= 2, or any index once a *splat precedes them.
                seen_splat = False
                slot_args = []
                for index, arg in enumerate(node.args):
                    if isinstance(arg, ast.Starred):
                        seen_splat = True
                    elif index >= 2 or seen_splat:
                        slot_args.append(arg)
                if any(_is_ambient_source_value(arg) for arg in slot_args):
                    # An ambient value written where it can land in the
                    # source slot: no surrounding splat launders it.
                    compliant = False
                else:
                    compliant = (
                        bool(slot_args)  # explicit, non-ambient source
                        or seen_splat  # *splat may carry the source
                        # **splat may carry source=
                        or any(kw.arg is None for kw in node.keywords)
                    )
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
                f"stays counted. If the code moved OUTSIDE mcc/codegen, do "
                f"NOT just delete the pin: widen the census (CODEGEN_DIR) "
                f"to keep covering it, or the guardrail silently ends."
            )
    if problems:
        raise AssertionError(
            "\n".join(problems)
            + "\nSee this module's docstring for the rule this protects."
        )


def test_current_source_assignments_only_go_down():
    """Rule 1 ratchet: no new ``current_source`` ambient-mutation site."""
    _check_ratchet(
        _censuses()[0],
        CURRENT_SOURCE_ASSIGNMENT_PINS,
        "CURRENT_SOURCE_ASSIGNMENT_PINS",
        "thread the owning file explicitly — take the declaration's "
        "source (or a scope object carrying it) as a parameter rather than "
        "mutating the ambient current_source.",
    )


def test_langerror_raises_carry_their_source():
    """Rule 1 ratchet: new ``LangError``s must say which file they're about."""
    _check_ratchet(
        _censuses()[1],
        LANGERROR_WITHOUT_SOURCE_PINS,
        "LANGERROR_WITHOUT_SOURCE_PINS",
        "pass source= explicitly (e.g. LangError(msg, node.line, "
        "source=node.source)) so the error carries its site instead of "
        "inheriting whatever file current_source last pointed at — "
        "source=None and source=...current_source do not count.",
    )
