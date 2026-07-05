"""Generate a ``.mci`` interface stub from a compiled program.

An interface is valid mcc source that another program ``import``\\ s to compile
and link against a precompiled object: concrete functions become bodyless
``fn`` prototypes (the real bodies live in the ``.o``, called with the mcc
convention, hidden ``mut``/``const``-struct references included), while types,
constants, and generic/``@inline`` functions are emitted in full because the
consumer needs their layout, value, or body to type-check and re-instantiate
them. A real ``@extern`` declaration in the source stays verbatim -- it keeps
meaning "C calling convention".

The stub is the root file's **public surface plus its transitive closure**: any
declaration a shipped body or signature reaches is pulled in, even a ``@private``
one (a generic helper called by a public generic must travel as source). Pulled-in
``@private`` declarations keep their marker, so they stay private to the ``.mci`` --
the consumer can use the public API that needs them but cannot name them directly.
Unreachable ``@private``/``@static`` declarations are dropped, the original
``import`` lines are preserved (a dependency's interface is pulled in
transitively), and ``@if`` is already resolved before generation, so each
interface reflects the target it was compiled for.
"""

from __future__ import annotations

from dataclasses import fields, is_dataclass

from mcc.codegen import CodeGen
from mcc.errors import LangError
from mcc.nodes import (
    Call,
    Const,
    EnumAccess,
    EnumDecl,
    Func,
    GlobalVar,
    StructDecl,
    TypeAlias,
    TypeRef,
    Var,
)


# The parser decodes string escapes at parse time, so re-emitting a message
# into a stub must re-encode it: the inverse of the lexer/parser STRING_ESCAPES
# table for every character that cannot appear bare inside a string literal
# (a STRING token cannot span lines, so the control characters must re-encode
# too). Characters outside this table are emitted as themselves.
_STRING_ESCAPES = {
    "\\": "\\\\",
    '"': '\\"',
    "\a": "\\a",
    "\b": "\\b",
    "\x1b": "\\e",
    "\f": "\\f",
    "\n": "\\n",
    "\r": "\\r",
    "\t": "\\t",
    "\v": "\\v",
    "\0": "\\0",
}


def _escape(text: str) -> str:
    """Re-encode ``text`` as the inside of an mcc string literal."""
    return "".join(_STRING_ESCAPES.get(c, c) for c in text)


def _is_void(ref: TypeRef) -> bool:
    """Whether a return ``TypeRef`` is plain ``void`` (no pointer/array)."""
    return (
        ref.name == "void"
        and not ref.stars
        and not ref.dims
        and ref.params is None
    )


def _type_names(ref: TypeRef) -> set[str]:
    """Collect every base type name a type mentions, recursively.

    Walks generic arguments and a function type's parameter/return types, so
    ``list<hidden>*`` yields ``{"list", "hidden"}``.
    """
    names = set()
    if ref.params is not None:
        for p in ref.params:
            names |= _type_names(p)
        if ref.ret is not None:
            names |= _type_names(ref.ret)
    else:
        names.add(ref.name)
        for a in ref.args:
            names |= _type_names(a)
    return names


def _collect_refs(obj, names: set[str]) -> None:
    """Accumulate every name an AST fragment references into ``names``.

    Adds function-call targets (``Call``), value references (``Var``), enum
    scopes (``EnumAccess``), and the base names of any ``TypeRef`` reached, then
    recurses through dataclass fields and lists -- enough to find every top-level
    declaration a signature or body depends on.

    Args:
        obj: An AST node, list, or scalar to scan.
        names: The set to add referenced names to, in place.
    """
    if isinstance(obj, TypeRef):
        names |= _type_names(obj)
        return  # _type_names already walked args/params/ret
    if isinstance(obj, Var):
        names.add(obj.name)
    elif isinstance(obj, Call):
        names.add(obj.name)
    elif isinstance(obj, EnumAccess):
        names.add(obj.enum)
    if is_dataclass(obj):
        for f in fields(obj):
            _collect_refs(getattr(obj, f.name), names)
    elif isinstance(obj, (list, tuple)):
        for item in obj:
            _collect_refs(item, names)


class InterfaceWriter:
    """Renders a compiled program's public surface (and closure) as a ``.mci``.

    Attributes:
        cg: A code generator run through :meth:`CodeGen.generate`, for its type
            checks and flattened program.
        source: The root file's full source text, sliced for verbatim
            declarations.
        imports: The root file's own ``(path, line)`` imports, re-emitted.
        root: The root file's resolved path; only its declarations are emitted.
        by_name: Each root declaration indexed by name (a name may map to
            several functions -- an overload set).
    """

    def __init__(self, cg: CodeGen, source: str, imports: list):
        """Initialize the writer.

        Args:
            cg: A code generator whose :meth:`generate` has already run.
            source: The root file's source text.
            imports: The root file's ``(path, line)`` import list.
        """
        self.cg = cg
        self.source = source
        self.imports = imports
        self.root = cg.root_source
        self.by_name: dict[str, list] = {}
        for decl in self._root_decls():
            self.by_name.setdefault(decl.name, []).append(decl)

    def _root_decls(self):
        """Yield every top-level declaration defined in the root file."""
        p = self.cg.program
        for group in (p.structs, p.enums, p.aliases, p.consts, p.globals,
                      p.functions):
            for decl in group:
                if decl.source == self.root:
                    yield decl

    def _decl_refs(self, decl) -> set[str]:
        """Names a declaration depends on, given how it will be emitted.

        A function's body counts only when it travels (generic or ``@inline``);
        a concrete function contributes its signature alone, since its body
        stays in the object.

        Args:
            decl: The declaration to scan.

        Returns:
            The set of referenced names, with the declaration's own type
            parameters removed.
        """
        names: set[str] = set()
        if isinstance(decl, Func):
            for _, t in decl.params:
                _collect_refs(t, names)
            _collect_refs(decl.ret_type, names)
            if decl.type_params or decl.inline:  # the body travels
                _collect_refs(decl.body, names)
            # Type-parameter defaults are types too (and _collect_refs does
            # not recurse dicts); the subtraction below strips a default's
            # references to earlier parameters, e.g. the T in <T, U = T*>.
            for t in decl.type_param_defaults.values():
                _collect_refs(t, names)
            names -= set(decl.type_params)
        elif isinstance(decl, StructDecl):
            for _, t in decl.fields:
                _collect_refs(t, names)
            if decl.base is not None:
                _collect_refs(decl.base, names)
            for t in decl.type_param_defaults.values():
                _collect_refs(t, names)
            names -= set(decl.type_params)
        elif isinstance(decl, EnumDecl):
            if decl.underlying is not None:
                _collect_refs(decl.underlying, names)
            for _, value in decl.members:
                _collect_refs(value, names)
        elif isinstance(decl, Const):
            _collect_refs(decl.value, names)
        elif isinstance(decl, TypeAlias):
            _collect_refs(decl.target, names)
        elif isinstance(decl, GlobalVar):
            if decl.type_name is not None:
                _collect_refs(decl.type_name, names)
        return names

    def _closure(self) -> list:
        """The declarations to emit: the public surface plus all it reaches.

        Seeds with every public (non-``@private``, non-``@static``) root
        declaration, then pulls in whatever their signatures and shipped bodies
        reference -- including ``@private``/``@static`` helpers -- transitively.

        Returns:
            The reachable declarations, in source order.
        """
        included: dict[int, object] = {}
        work = [
            decl
            for decl in self._root_decls()
            if not decl.private and not getattr(decl, "static", False)
        ]
        while work:
            decl = work.pop()
            if id(decl) in included:
                continue
            included[id(decl)] = decl
            for name in self._decl_refs(decl):
                work.extend(self.by_name.get(name, ()))
        return sorted(
            (d for d in included.values() if d.span is not None),
            key=lambda d: d.span[0],
        )

    def _slice(self, decl) -> str:
        """Return a declaration's verbatim source text from its span."""
        start, end = decl.span
        return self.source[start:end]

    def _prototype(self, func: Func) -> str:
        """Render a concrete function as a bodyless ``fn`` prototype.

        The prototype means "a concrete mcc function defined in another
        object, called with the mcc convention" -- the hidden-reference
        convention for ``mut``/``const``-struct parameters is a pure function
        of the signature, so re-emitting every parameter marker carries it in
        full. Keeps a ``@private`` marker so a pulled-in helper stays private
        to the interface; the bare name resolves to the compiled symbol (the
        parser forbids ``@symbol`` on non-extern functions, so there is none
        to carry).

        Args:
            func: The function to render as a prototype.

        Returns:
            The ``fn ...;`` declaration text.

        Raises:
            LangError: When the function is ``@static`` (its symbol is
                file-local, so no stable name exists).
        """
        if func.static:
            raise LangError(
                f"cannot export @static function {func.name!r} in an interface: its "
                "symbol is file-local; make the helper @private or generic/@inline",
                func.line,
                source=func.source,
            )
        # Every parameter marker rides along: @noalias/@nonnull carry the
        # overlap and non-null contracts, const/mut carry the read-only and
        # by-reference conventions -- the prototype must match the definition's
        # signature exactly for the call to be compiled correctly.
        params = [
            f"{'@noalias ' if pname in func.noalias_params else ''}"
            f"{'@nonnull ' if pname in func.nonnull_params else ''}"
            f"{'const ' if pname in func.const_params else ''}"
            f"{'mut ' if pname in func.mut_params else ''}"
            f"{pname}: {ptype}"
            for pname, ptype in func.params
        ]
        if func.variadic:
            params.append("...")
        ret = "" if _is_void(func.ret_type) else f" -> {func.ret_type}"
        head = "@private fn" if func.private else "fn"
        # @deprecated is re-emitted so the importer's call sites warn too
        # (generic/@inline functions get this for free from their verbatim
        # source span); the message is re-escaped, undoing the parse-time
        # decode.
        if func.deprecated_msg is not None:
            head = f'@deprecated("{_escape(func.deprecated_msg)}") {head}'
        # @removed is re-emitted so the importer's call sites error too (a
        # generic tombstone travels verbatim, span included); the parser
        # rejects combining it with @deprecated, so at most one prefix applies.
        if func.removed_msg is not None:
            head = f'@removed("{_escape(func.removed_msg)}") {head}'
        return f"{head} {func.name}({', '.join(params)}){ret};"

    def _render(self, decl) -> str:
        """Render one declaration for the interface.

        A concrete function becomes a bodyless ``fn`` prototype; everything
        else -- a type, constant, global, ``@extern`` declaration, or
        generic/``@inline`` function -- is emitted verbatim from its source
        span.

        Args:
            decl: The declaration to render.

        Returns:
            The interface text for the declaration.
        """
        if isinstance(decl, Func) and not (
            decl.extern or decl.type_params or decl.inline
        ):
            return self._prototype(decl)
        return self._slice(decl)

    def render(self) -> str:
        """Render the full interface stub.

        Returns:
            The ``.mci`` source: a header comment, the preserved imports, then
            every reachable declaration in source order, separated by blank
            lines.
        """
        # Concrete overload sets are not representable yet: their members
        # would render as same-name prototypes, which the importer rejects
        # (overload sets do not support prototypes until stage 2 of the
        # overloading work re-keys pairing per signature).
        for name, decls in self.by_name.items():
            members = [
                d
                for d in decls
                if isinstance(d, Func)
                and not (d.extern or d.static or d.proto or d.type_params)
                and d.removed_msg is None
            ]
            if len(members) > 1:
                raise LangError(
                    f"cannot emit an interface for overloaded function "
                    f"{name!r} (overload sets do not support interfaces yet)",
                    members[1].line,
                    source=members[1].source,
                )
        name = self.root.rsplit("/", 1)[-1] if self.root else "module"
        blocks = [
            f"// Interface generated from {name} by mcc -- do not edit.\n"
            "// Import this alongside the matching object file."
        ]
        if self.imports:
            blocks.append("\n".join(f'import "{path}";' for path, _ in self.imports))
        blocks.extend(self._render(decl) for decl in self._closure())
        return "\n\n".join(blocks) + "\n"


def render_interface(cg: CodeGen, source: str, imports: list) -> str:
    """Render a ``.mci`` interface from a generated program.

    Args:
        cg: A code generator whose :meth:`CodeGen.generate` has already run.
        source: The root file's source text.
        imports: The root file's ``(path, line)`` import list.

    Returns:
        The interface stub as mcc source text.
    """
    return InterfaceWriter(cg, source, imports).render()
