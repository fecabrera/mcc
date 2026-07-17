"""Declaration identity and ``@static`` name mangling (SIE-195).

Owned invariant: type comparisons go through DECLARATION IDENTITY, never
bare names. A spelled head resolves under the file that SPELLED it -- a
file-scoped ``@static`` struct there shadows a global template -- and the
resolved declaration object (or its mangled ``@static`` base) is the
name's program-wide identity, so same-named types from different files
compare unequal. This is the SIE-189 cross-file ``@static`` conflation
fix, generalized from a private antidote inside the covariance checker
into a reusable service.

No module-level state: a :class:`DeclIndex` is constructed with the
registries it reads -- references to the generator's dicts, which are
built in place and never rebound -- and owns the ``symbol_bases``
mangling map.
"""

from __future__ import annotations

from dataclasses import replace as dataclasses_replace
from typing import TYPE_CHECKING, Callable

from mcc.nodes import TypeRef

from mcc.codegen.types import RESERVED_TYPE_NAMES, TYPES

if TYPE_CHECKING:
    from mcc.codegen.types import Alias, EnumType, LangType
    from mcc.nodes import StructDecl, UnionDecl

    # A resolver scoped to an explicit file with no live bindings -- the
    # generator's ``resolve_ref_at`` (see mcc.codegen.resolution).
    Resolver = Callable[[TypeRef, str | None], "LangType | None"]


class DeclIndex:
    """Name -> declaration resolution over the generator's registries.

    Holds references to the registry dicts the generator builds in place
    (they are never rebound, so the references stay live) plus the
    ``used_symbols`` set :meth:`static_base` mints against, and owns the
    ``symbol_bases`` map from ``(source, name)`` to the mangled ``@static``
    symbol base.
    """

    def __init__(
        self,
        *,
        static_structs: "dict[tuple[str | None, str], StructDecl | UnionDecl]",
        struct_templates: "dict[str, StructDecl | UnionDecl]",
        static_enums: "dict[tuple[str | None, str], EnumType]",
        enums: "dict[str, EnumType]",
        static_type_aliases: "dict[tuple[str | None, str], Alias]",
        type_aliases: "dict[str, Alias]",
        used_symbols: set[str],
    ):
        self.static_structs = static_structs
        self.struct_templates = struct_templates
        self.static_enums = static_enums
        self.enums = enums
        self.static_type_aliases = static_type_aliases
        self.type_aliases = type_aliases
        self.used_symbols = used_symbols
        # (source, name) -> mangled symbol base: static name mangling.
        self.symbol_bases: dict[tuple[str | None, str], str] = {}

    def static_base(self, name: str, source: str | None) -> str:
        """Mint a unique LLVM symbol for a file-scoped (``@static``) name.

        The separator is ``.`` rather than ``@``: a ``.`` cannot appear in an
        mcc identifier (so it never collides with a real name), and it is safe
        in an ELF symbol, whereas ELF's ``ld`` reads ``@`` as the
        symbol-versioning marker (``symbol@version``) and rejects a shared
        library that exports one.

        Args:
            name: The source-level name.
            source: The defining file, used to build the symbol stem.

        Returns:
            A unique symbol such as ``f.set``, disambiguated with a numeric
            suffix when needed.
        """
        stem = source.rsplit("/", 1)[-1].removesuffix(".mc") if source else "static"
        base = candidate = f"{name}.{stem}"
        counter = 1
        while candidate in self.used_symbols:
            counter += 1
            candidate = f"{base}.{counter}"
        self.used_symbols.add(candidate)
        return candidate

    def lookup_struct_decl(
        self, name: str, source: str | None
    ) -> "StructDecl | UnionDecl | None":
        """Find a struct declaration by name, preferring a file-scoped one.

        A same-named ``@static`` struct in ``source``'s file shadows a global
        template, exactly as ``CodeGen.lang_type`` resolves struct types.

        Args:
            name: The struct's name.
            source: The file the name is spelled in.

        Returns:
            The matching ``StructDecl``, or ``None`` when no struct has that
            name in scope.
        """
        decl = self.static_structs.get((source, name))
        if decl is not None:
            return decl
        return self.struct_templates.get(name)

    def lookup_alias(self, name: str, source: str | None) -> "Alias | None":
        """Resolve a type-alias name, preferring a file-scoped ``@static`` one.

        Args:
            name: The alias's name.
            source: The file the name is spelled in.

        Returns:
            The matching ``Alias``, or ``None`` when no alias has that name in
            scope.
        """
        static = self.static_type_aliases.get((source, name))
        return static if static is not None else self.type_aliases.get(name)

    def lookup_enum(self, name: str, source: str | None) -> "EnumType | None":
        """Resolve an enum name, preferring a file-scoped ``@static`` one.

        Args:
            name: The enum's name.
            source: The file the name is spelled in.

        Returns:
            The matching ``EnumType``, or ``None`` when no enum has that name in
            scope. A same-named ``@static`` enum in ``source``'s file shadows a
            global one, exactly as ``@static`` structs do.
        """
        static = self.static_enums.get((source, name))
        return static if static is not None else self.enums.get(name)

    def template_walk_head(self, name: str, source: "str | None"):
        """The declaration (or builtin) a template-walk head names.

        Resolution happens under the file that SPELLED the name -- a
        file-scoped ``@static`` struct there shadows a global template,
        exactly as ``CodeGen.lang_type`` resolves -- and the returned
        declaration object is the head's program-wide identity, so
        same-named types from different files compare unequal.

        Args:
            name: The head's spelled name.
            source: The file the spelling belongs to.

        Returns:
            The ``StructDecl``/``UnionDecl``, the name itself for a builtin
            head, or ``None`` for an unknown (alias/unresolvable) one.
        """
        decl = self.lookup_struct_decl(name, source)
        if decl is not None:
            return decl
        if name in TYPES or name in RESERVED_TYPE_NAMES:
            return name
        return None

    def canon_template_args(
        self, args: "list[TypeRef]", source: "str | None", resolve: "Resolver"
    ) -> "list[TypeRef] | None":
        """:meth:`canon_template_arg` over an argument list, or ``None``."""
        out = []
        for arg in args:
            canon = self.canon_template_arg(arg, source, resolve)
            if canon is None:
                return None
            out.append(canon)
        return out

    def canon_template_arg(
        self, ref: TypeRef, source: "str | None", resolve: "Resolver"
    ) -> "TypeRef | None":
        """A template-walk argument spelling as a program-wide identity.

        A fully concrete spelling resolves under its declaring file
        (the ``resolve`` callable, the generator's ``resolve_ref_at`` -- so
        aliases chase and a file-scoped ``@static`` type canonicalizes to
        its unique instantiation name); a ``$``-placeholder passes through
        to compare positionally; a spelling structured OVER a placeholder
        (``slice<$0>``) keeps its shape with the head name replaced by its
        unique identity (the ``@static`` mangled base where file-scoped).
        ``None`` -- an unresolvable name, or a placeholder under an alias
        or function-type head -- keeps the conservative same-spelling
        requirement.

        Args:
            ref: The argument spelling.
            source: The file the spelling belongs to.
            resolve: Resolves a concrete ``TypeRef`` under an explicit file
                with no live bindings.

        Returns:
            The canonicalized spelling, or ``None``.
        """
        if "$" not in str(ref):
            resolved = resolve(ref, source)
            return None if resolved is None else TypeRef(str(resolved))
        if ref.name.startswith("$"):
            return ref if not ref.args else None
        if ref.params is not None:
            return None  # a fn(...) type over a placeholder: conservative
        head = self.template_walk_head(ref.name, source)
        if head is None:
            return None
        token = (
            head
            if isinstance(head, str)
            else self.symbol_bases.get((head.source, head.name), head.name)
        )
        args = self.canon_template_args(ref.args, source, resolve)
        if args is None:
            return None
        return dataclasses_replace(ref, name=token, args=args)
