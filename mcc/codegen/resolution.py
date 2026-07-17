"""The pure spelling algebra over ``TypeRef`` patterns (SIE-195).

Owned invariant: no function in this module reads ambient compiler state.
Everything arrives as an explicit parameter -- the spellings themselves,
the declaring template's ``type_params``, a lookup callable already
scoped to a file, or (for :func:`resolve_ref_at`) the generator together
with its explicit pinning primitive, so the ambient
``current_source``/``type_bindings`` writes stay inside ``generator.py``
where the SIE-194 ratchet pins them.
"""

from __future__ import annotations

from dataclasses import replace as dataclasses_replace
from typing import TYPE_CHECKING, Callable

from mcc.errors import LangError
from mcc.nodes import TypeRef

if TYPE_CHECKING:
    from mcc.codegen.generator import CodeGen
    from mcc.codegen.types import Alias, LangType
    from mcc.nodes import Func


def subst_struct_args(ref: TypeRef, binding: dict[str, TypeRef]) -> TypeRef:
    """Substitute a specialization's struct parameter names in a type.

    The struct's declared parameter names (``T`` in ``struct point<T>``)
    bind to the specialization's concrete arguments, so a signature written
    with ``point<T>`` or a bare ``T`` resolves to the concrete
    instantiation -- mirroring how a generic instance substitutes.

    Args:
        ref: A parameter or return ``TypeRef`` from the specialization.
        binding: ``{struct parameter name: concrete TypeRef}``.

    Returns:
        The type with the bound names replaced.
    """
    if ref.params is not None:  # a fn(...) -> ret function-pointer type
        return dataclasses_replace(
            ref,
            params=[subst_struct_args(p, binding) for p in ref.params],
            ret=(
                subst_struct_args(ref.ret, binding)
                if ref.ret is not None
                else None
            ),
        )
    if ref.name in binding:
        # A bare parameter name carries no arguments of its own; keep any
        # pointer depth, array dimensions, and qualifiers written on it.
        sub = binding[ref.name]
        return dataclasses_replace(
            sub,
            stars=sub.stars + ref.stars,
            dims=sub.dims + ref.dims,
            const=sub.const or ref.const,
            nonnull=sub.nonnull or ref.nonnull,
            mut=sub.mut or ref.mut,
            own=sub.own or ref.own,
        )
    return dataclasses_replace(
        ref, args=[subst_struct_args(a, binding) for a in ref.args]
    )


def names_type_param(ref: TypeRef, type_params: list[str]) -> bool:
    """Whether ``ref`` names one of ``type_params``, at any depth."""
    if ref.name in type_params:
        return True
    if any(names_type_param(arg, type_params) for arg in ref.args):
        return True
    if ref.params is not None and any(
        names_type_param(p, type_params) for p in ref.params
    ):
        return True
    return ref.ret is not None and names_type_param(ref.ret, type_params)


def generic_ret_spelling(func: "Func") -> TypeRef:
    """A member's return spelling, alpha-renamed for cross-hop comparison.

    The struct-generic parameter names in a return spelling are the
    member's own choice (the qualifier's, or the declaration's for a
    rebased clone), so two hops of one hierarchy may spell one return
    differently. Renaming each struct parameter to its qualifier
    POSITION (``$0``, ``$1``, ...) -- the identity the ``extends``
    composition preserves -- makes the spellings of the override and its
    rebased base clone directly comparable. A concrete (specialized)
    qualifier position binds no name and passes through unchanged. A
    fresh position is judged by ``type_params`` MEMBERSHIP -- the
    registration-time fact -- not by re-probing
    ``CodeGen.struct_arg_is_param``, which reads whatever type bindings
    happen to be live and would misclassify under a caller's
    instantiation context (the ``slot_winner`` hazard).
    """
    qargs = func.qualifier_args or []
    binding = {
        qa.name: TypeRef(f"${i}")
        for i, qa in enumerate(qargs)
        if qa.name in func.type_params
    }
    if not binding:
        return func.ret_type
    return subst_struct_args(func.ret_type, binding)


def dealias_pattern(
    pattern: TypeRef,
    type_params: list[str],
    lookup_alias: "Callable[[str], Alias | None]",
) -> TypeRef:
    """Expand a generic-alias application at a parameter pattern's head.

    A pattern spelled through a generic alias -- ``diag<U>`` with
    ``type diag<T> = pair<T, T>`` -- unifies and shape-checks as the type
    it names: the written arguments bind the alias's parameters (a
    shorter list fills from trailing defaults) and substitute through its
    target, so ``diag<U>`` matches ``pair<int32, int32>`` binding
    ``U = int32`` -- and the repeated position rejects a
    ``pair<int32, float64>`` receiver, the diagonal constraint. Pointer
    depth, array dimensions, and ``const`` written on the alias spelling
    carry onto the expansion; chasing stops at a name that is a type
    parameter, a non-alias, a plain (non-generic) alias, an arity
    mismatch (the declaration's own resolution reports those), or a
    cycle.

    Args:
        pattern: The parameter's ``TypeRef`` pattern.
        type_params: The function's type-parameter names.
        lookup_alias: Resolves an alias name in the pattern's scope (the
            generator's ``lookup_alias``).

    Returns:
        The expanded pattern, or ``pattern`` unchanged when its head is
        not a generic-alias application.
    """
    seen: set[str] = set()
    while (
        pattern.args
        and pattern.params is None
        and pattern.name not in type_params
        and pattern.name not in seen
        and (alias := lookup_alias(pattern.name)) is not None
        and alias.type_params
        and (
            len(alias.type_params) - len(alias.type_param_defaults)
            <= len(pattern.args)
            <= len(alias.type_params)
        )
    ):
        seen.add(pattern.name)
        binding = dict(zip(alias.type_params, pattern.args))
        for pname in alias.type_params[len(pattern.args):]:
            binding[pname] = subst_struct_args(
                alias.type_param_defaults[pname], binding
            )
        target = subst_struct_args(alias.target, binding)
        pattern = dataclasses_replace(
            target,
            stars=target.stars + pattern.stars,
            dims=target.dims + pattern.dims,
            const=target.const or pattern.const,
        )
    return pattern


def resolve_ref_at(
    ref: TypeRef, source: "str | None", gen: "CodeGen"
) -> "LangType | None":
    """Resolve a ``TypeRef`` under a given file, with no live bindings.

    Used while rebasing inherited members, where an ``extends`` clause's
    type arguments belong to the deriving struct's file. This function
    never reads the generator's ambient state: it pins the scope
    explicitly through ``pinned_resolution_scope`` (which restores the
    outer scope on exit) and resolves inside it.

    Args:
        ref: The reference to resolve.
        source: The file whose scope it resolves in.
        gen: The generator, supplying the pinning primitive and
            ``lang_type``.

    Returns:
        The resolved type, or ``None`` when it does not resolve.
    """
    with gen.pinned_resolution_scope(source):
        try:
            return gen.lang_type(ref, 0)
        except LangError:
            return None
