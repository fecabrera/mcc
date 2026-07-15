"""Parser: recursive descent over the token stream, producing an AST."""

from __future__ import annotations

import re
from contextlib import contextmanager

from mcc.errors import LangError
from mcc.lexer import Token, tokenize
from mcc.nodes import (
    AlignOf,
    ArrayLit,
    Asm,
    Assign,
    Binary,
    Block,
    BlockExpr,
    BoolLit,
    Break,
    Call,
    CallExpr,
    Case,
    CaseType,
    Cast,
    CharLit,
    Coalesce,
    CompoundAssign,
    Conditional,
    Const,
    Continue,
    Defer,
    Emit,
    EnumAccess,
    EnumDecl,
    ErrorDirective,
    ErrorName,
    Except,
    ExprStmt,
    Import,
    FloatLit,
    For,
    FStrHole,
    FStrLit,
    Func,
    GlobalVar,
    If,
    Index,
    IntLit,
    Len,
    Let,
    Logical,
    Member,
    NonnullAssert,
    NullLit,
    OffsetOf,
    Program,
    Move,
    ResultLit,
    Return,
    SizeOf,
    Slice,
    StaticAssert,
    StoreCall,
    StoreDeref,
    StoreIndex,
    StoreMember,
    StrLit,
    StructDecl,
    StructLit,
    Ternary,
    Try,
    TryFallback,
    TryStmt,
    TupleLit,
    TypeAlias,
    TypeName,
    TypeRef,
    Unary,
    UnionDecl,
    Unreachable,
    Var,
    While,
)

# C's simple escape sequences, plus \e for ESC (a GCC/Clang extension, handy
# for ANSI terminal codes). Any other escape (e.g. \q) keeps the bare
# character. \0 is the NUL byte.
STRING_ESCAPES = {
    "a": "\a",
    "b": "\b",
    "e": "\x1b",
    "f": "\f",
    "n": "\n",
    "r": "\r",
    "t": "\t",
    "v": "\v",
    "0": "\0",
    "'": "'",
    '"': '"',
    "?": "?",
    "\\": "\\",
}


def _unescape(raw: str) -> str:
    """Apply :data:`STRING_ESCAPES` to a string literal's inner text.

    ``raw`` is the token text with its surrounding quotes already stripped.
    Every ``\\x`` sequence is replaced by its escape (an unknown escape keeps
    the bare character), matching how ordinary string literals are decoded so
    directive messages read the same as any other string.
    """
    return re.sub(
        r"\\(.)", lambda m: STRING_ESCAPES.get(m.group(1), m.group(1)), raw
    )


def type_ref_names(ref: TypeRef) -> set[str]:
    """Every base type name a ``TypeRef`` mentions, recursively.

    Walks generic arguments and function-pointer parameter/return types.
    Used to validate that a type-parameter default references only earlier
    parameters.
    """
    names: set[str] = set()
    if ref.params is not None:  # a fn(...) -> ret function-pointer type
        for p in ref.params:
            names |= type_ref_names(p)
        if ref.ret is not None:
            names |= type_ref_names(ref.ret)
    else:
        names.add(ref.name)
        for a in ref.args:
            names |= type_ref_names(a)
    return names


# Compound-assignment operators mapped to the base binary operator they apply:
# `target op= value` means `target = target op value` (target evaluated once).
COMPOUND_ASSIGN_OPS = {
    "+=": "+",
    "-=": "-",
    "*=": "*",
    "/=": "/",
    "%=": "%",
    "&=": "&",
    "|=": "|",
    "^=": "^",
    "<<=": "<<",
    ">>=": ">>",
}


def int_value(text: str) -> int:
    """Parse the integer value of an INT token.

    Args:
        text: The token text, in decimal or with a ``0x``/``0X`` hex prefix.

    Returns:
        The integer value.
    """
    return int(text, 16 if text[:2] in ("0x", "0X") else 10)


def is_lvalue(expr) -> bool:
    """Whether an expression is a valid assignment target.

    The forms an assignment accepts: a variable, ``*ptr``, an index
    ``base[i]``, a member ``base.field``/``base->field``, or a call --
    named ``f(...)`` or through a function-pointer expression
    ``s.handler(...)`` -- assignable when the callee returns ``mut``
    (checked at codegen, where the callee is resolved).

    Args:
        expr: The parsed target expression.

    Returns:
        ``True`` when ``expr`` is an assignable lvalue.
    """
    return (
        isinstance(expr, (Var, Index, Member, Call, CallExpr))
        or (isinstance(expr, Unary) and expr.op == "*")
    )


class Parser:
    """A recursive-descent parser over a token list.

    Builds an AST from the tokens produced by the lexer, using precedence
    climbing for binary expressions. The parser holds a cursor (``pos``) into
    ``tokens`` and never backtracks except for the bounded speculation in
    :meth:`try_type_args`.

    Attributes:
        tokens: The token list being parsed, ending with an EOF token.
        pos: Index of the current token.
    """

    def __init__(self, tokens: list[Token]):
        """Initialize the parser.

        Args:
            tokens: The tokens to parse, as returned by ``tokenize``.
        """
        self.tokens = tokens
        self.pos = 0
        # Whether a keyword-free struct literal `T { ... }` may appear here.
        # Turned off only while parsing a `for x in <expr>` header, where a
        # trailing `{` would otherwise be ambiguous with the loop body; any
        # bracket/paren-delimited sub-expression turns it back on.
        self.struct_lit_ok = True
        # Undo log for `>>` splits (expect_close_angle rewrites the token
        # list in place). Non-None only while try_struct_method_args
        # speculates: restoring the cursor alone would otherwise leave a
        # nested list's `>>` half-consumed for the re-parse.
        self.angle_splits: list[tuple[int, Token]] | None = None

    @contextmanager
    def _struct_literals(self, ok: bool):
        """Scope whether a bare `T { ... }` literal is allowed (see cur flag)."""
        saved = self.struct_lit_ok
        self.struct_lit_ok = ok
        try:
            yield
        finally:
            self.struct_lit_ok = saved

    @property
    def cur(self) -> Token:
        """The token at the cursor, without consuming it.

        Returns:
            The current token.
        """
        return self.tokens[self.pos]

    def advance(self) -> Token:
        """Consume the current token and advance the cursor.

        Returns:
            The token that was current before advancing.
        """
        tok = self.cur
        self.pos += 1
        return tok

    def accept(self, kind: str) -> Token | None:
        """Consume the current token if it matches ``kind``.

        Args:
            kind: The token kind to match.

        Returns:
            The consumed token, or ``None`` when the current token did not
            match.
        """
        if self.cur.kind == kind:
            return self.advance()
        return None

    def expect(self, kind: str) -> Token:
        """Consume the current token, requiring it to match ``kind``.

        Args:
            kind: The token kind that must appear next.

        Returns:
            The consumed token.

        Raises:
            LangError: When the current token is not of kind ``kind``.
        """
        if self.cur.kind != kind:
            raise LangError(f"expected {kind!r}, got {self.cur.text!r}", self.cur.line)
        return self.advance()

    def parse_program(self) -> Program:
        """Parse a whole file: leading imports, then top-level declarations.

        Returns:
            A ``Program`` with imports collected and each declaration sorted
            into structs, functions, globals, consts, or conditionals.

        Raises:
            LangError: On any syntax error in the file.
        """
        imports = []
        structs, functions, globals_, consts, conditionals, enums, aliases = (
            [], [], [], [], [], [], [])
        directives = []
        while self.cur.kind == "import":
            line = self.advance().line
            path = self.expect("STRING").text[1:-1]
            self.expect(";")
            imports.append((path, line))
        while self.cur.kind != "EOF":
            item = self.parse_toplevel_item()
            if isinstance(item, Import):
                raise LangError(
                    "an import must precede all declarations or appear inside an @if",
                    item.line,
                )
            target = {
                StructDecl: structs,
                UnionDecl: structs,
                Func: functions,
                GlobalVar: globals_,
                Const: consts,
                Conditional: conditionals,
                EnumDecl: enums,
                TypeAlias: aliases,
                StaticAssert: directives,
                ErrorDirective: directives,
            }[type(item)]
            target.append(item)
        return Program(
            imports, structs, functions, globals_, consts, conditionals, enums,
            aliases, directives,
        )

    def parse_toplevel_block(self) -> list:
        """Parse a brace-delimited group of top-level declarations.

        Used for a branch of a top-level ``@if``. A branch may hold ``import``
        statements (resolved by the driver once the condition is evaluated)
        alongside ordinary declarations and nested ``@if`` blocks.

        Returns:
            The items parsed from the block.
        """
        self.expect("{")
        items = []
        while self.cur.kind != "}":
            items.append(self.parse_toplevel_item())
        self.expect("}")
        return items

    def parse_conditional(self, parse_block):
        """Parse an ``@if`` / ``@else @if`` / ``@else`` compile-time selection.

        The dead branch is still parsed (so it must be syntactically valid) but
        never compiled. An ``@else @if`` chain is handled by recursing.

        Args:
            parse_block: Callback that parses one branch body -- top-level
                declarations or statements, depending on the context.

        Returns:
            A ``Conditional`` node for the selection.
        """
        line = self.advance().line  # @if
        self.expect("(")
        cond = self.parse_expr()
        self.expect(")")
        then = parse_block()
        otherwise = []
        if self.cur.kind == "ANNOT" and self.cur.text == "@else":
            self.advance()
            if self.cur.kind == "ANNOT" and self.cur.text == "@if":
                otherwise = [self.parse_conditional(parse_block)]  # @else @if chain
            else:
                otherwise = parse_block()
        return Conditional(cond, then, otherwise, line)

    def parse_static_assert(self):
        """Parse a ``@static_assert(cond, "msg");`` directive.

        The condition is any expression; it is not evaluated here -- the fold
        waits for code generation, where ``sizeof``/``alignof``/``offsetof`` and
        ``const`` references resolve against the type system. The message is a
        string literal, decoded with the usual escapes.

        Returns:
            A ``StaticAssert`` node.
        """
        line = self.advance().line  # @static_assert
        self.expect("(")
        cond = self.parse_expr()
        self.expect(",")
        message = _unescape(self.expect("STRING").text[1:-1])
        self.expect(")")
        self.expect(";")
        return StaticAssert(cond, message, line)

    def parse_error_directive(self):
        """Parse an ``@error("msg");`` or ``@warning("msg");`` directive.

        Returns:
            An ``ErrorDirective`` node, with ``warning`` set for ``@warning``.
        """
        annot = self.advance()  # @error or @warning
        self.expect("(")
        message = _unescape(self.expect("STRING").text[1:-1])
        self.expect(")")
        self.expect(";")
        return ErrorDirective(message, annot.line, warning=annot.text == "@warning")

    def parse_toplevel_item(self):
        """Parse one top-level item and record its source byte span.

        Wraps :meth:`_parse_toplevel_item`, stamping ``span`` (start/end byte
        offsets, including any leading annotations) on declarations that carry
        it. The interface generator slices a declaration's verbatim source from
        this span.

        Returns:
            The parsed declaration, with ``span`` set when it has the field.
        """
        start = self.cur.offset
        item = self._parse_toplevel_item()
        last = self.tokens[self.pos - 1]
        if hasattr(item, "span"):
            item.span = (start, last.offset + len(last.text))
        return item

    def _parse_toplevel_item(self):
        """Parse one top-level item: a struct, global, const, function, or @if.

        Leading annotations (``@private``, ``@static``, ``@extern``,
        ``@packed``, ``@volatile``, ``@inline``, ``@align``, ``@symbol``,
        ``@deprecated``, ``@removed``) are collected and validated against
        the declaration they precede.

        Returns:
            The parsed ``StructDecl``, ``GlobalVar``, ``Const``, ``Func``, or
            ``Conditional``.

        Raises:
            LangError: On a misplaced ``@else``, an unknown or misapplied
                annotation, or an otherwise invalid declaration.
        """
        if self.cur.kind == "ANNOT" and self.cur.text == "@if":
            return self.parse_conditional(self.parse_toplevel_block)
        if self.cur.kind == "ANNOT" and self.cur.text == "@else":
            raise LangError("@else without a matching @if", self.cur.line)
        if self.cur.kind == "ANNOT" and self.cur.text == "@static_assert":
            return self.parse_static_assert()
        if self.cur.kind == "ANNOT" and self.cur.text in ("@error", "@warning"):
            return self.parse_error_directive()
        if self.cur.kind == "import":
            # Only valid inside an @if branch (parse_toplevel_block); a stray one
            # in the declaration section is rejected by parse_program.
            line = self.advance().line
            path = self.expect("STRING").text[1:-1]
            self.expect(";")
            return Import(path, line)
        private = static = extern = packed = volatile = inline = asm = False
        noreturn = False
        align = None
        symbol = None
        deprecated = None
        removed = None
        override = False
        prop = None
        acc = None
        clobbers = []
        while self.cur.kind == "ANNOT":
            annot = self.advance()
            if annot.text in ("@if", "@else"):
                raise LangError(
                    f"{annot.text} cannot be combined with other annotations",
                    annot.line,
                )
            if annot.text == "@private":
                private = True
            elif annot.text == "@static":
                static = True
            elif annot.text == "@extern":
                extern = True
            elif annot.text == "@packed":
                packed = True
            elif annot.text == "@volatile":
                volatile = True
            elif annot.text == "@inline":
                inline = True
            elif annot.text == "@asm":
                asm = True
            elif annot.text == "@noreturn":
                noreturn = True
            elif annot.text == "@clobbers":
                clobbers = self.parse_clobber_list(annot.line)
            elif annot.text == "@align":
                self.expect("(")
                align = int_value(self.expect("INT").text)
                self.expect(")")
                if align == 0 or align & (align - 1):
                    raise LangError(
                        f"@align needs a power of two, not {align}", annot.line
                    )
            elif annot.text == "@symbol":
                self.expect("(")
                symbol = self.expect("STRING").text[1:-1]
                self.expect(")")
                if not symbol:
                    raise LangError("@symbol needs a non-empty name", annot.line)
            elif annot.text == "@deprecated":
                self.expect("(")
                # The message decodes like any string literal, so it reads the
                # same as an @error/@warning directive message.
                deprecated = _unescape(self.expect("STRING").text[1:-1])
                self.expect(")")
                if not deprecated:
                    raise LangError(
                        "@deprecated needs a non-empty message", annot.line
                    )
            elif annot.text == "@removed":
                self.expect("(")
                # The message decodes like any string literal, so it reads the
                # same as an @error/@warning directive message.
                removed = _unescape(self.expect("STRING").text[1:-1])
                self.expect(")")
                if not removed:
                    raise LangError(
                        "@removed needs a non-empty message", annot.line
                    )
            elif annot.text == "@override":
                override = True
            elif annot.text == "@property":
                # A bare @property is the mut-return-lvalue form; the
                # parenthesized kind picks the explicit accessor-pair form:
                # @property("get") / @property("set").
                if self.cur.kind == "(":
                    self.advance()
                    kind = _unescape(self.expect("STRING").text[1:-1])
                    self.expect(")")
                    if kind not in ("get", "set"):
                        raise LangError(
                            '@property takes "get" or "set" '
                            f"(not {kind!r}), or no argument",
                            annot.line,
                        )
                    prop = kind
                else:
                    prop = "bare"
            elif annot.text == "@accessor":
                # A bare @accessor is the mut-return-lvalue form of the
                # type's `[]` operator; the parenthesized kind picks the
                # explicit pair form: @accessor("get") / @accessor("set").
                if self.cur.kind == "(":
                    self.advance()
                    kind = _unescape(self.expect("STRING").text[1:-1])
                    self.expect(")")
                    if kind not in ("get", "set"):
                        raise LangError(
                            '@accessor takes "get" or "set" '
                            f"(not {kind!r}), or no argument",
                            annot.line,
                        )
                    acc = kind
                else:
                    acc = "bare"
            else:
                raise LangError(f"unknown annotation {annot.text!r}", annot.line)
        if extern and static:
            raise LangError("@extern and @static cannot be combined", self.cur.line)
        if symbol is not None and not extern:
            raise LangError(
                "@symbol only applies to @extern functions and variables",
                self.cur.line,
            )
        if align is not None and self.cur.kind not in ("struct", "union"):
            raise LangError("@align only applies to structs", self.cur.line)
        if packed and self.cur.kind not in ("struct", "union"):
            raise LangError("@packed only applies to structs", self.cur.line)
        if inline and (extern or self.cur.kind in ("struct", "union", "let", "const")):
            raise LangError(
                "@inline only applies to functions with a body", self.cur.line
            )
        if asm and (extern or self.cur.kind != "fn"):
            raise LangError("@asm only applies to functions with a body", self.cur.line)
        if clobbers and not asm:
            raise LangError("@clobbers only applies to @asm", self.cur.line)
        if noreturn and self.cur.kind != "fn":
            # Functions only: definitions, @extern/@asm declarations, protos.
            raise LangError("@noreturn only applies to functions", self.cur.line)
        if prop:
            if self.cur.kind != "fn":
                raise LangError("@property only applies to methods", self.cur.line)
            if extern or asm:
                raise LangError(
                    "@property only applies to a method with a body "
                    "(not @extern or @asm)",
                    self.cur.line,
                )
        if acc:
            if prop:
                raise LangError(
                    "@property and @accessor cannot be combined "
                    "(field syntax and `[]` are separate surfaces)",
                    self.cur.line,
                )
            if self.cur.kind != "fn":
                raise LangError("@accessor only applies to methods", self.cur.line)
            if extern or asm:
                raise LangError(
                    "@accessor only applies to a method with a body "
                    "(not @extern or @asm)",
                    self.cur.line,
                )
        if deprecated is not None and self.cur.kind != "fn":
            # v1 scope: functions only (types, enums, and globals later).
            raise LangError("@deprecated only applies to functions", self.cur.line)
        if removed is not None:
            if self.cur.kind != "fn":
                # v1 scope: functions only (types, enums, and globals later).
                raise LangError("@removed only applies to functions", self.cur.line)
            if deprecated is not None:
                raise LangError(
                    "@deprecated and @removed cannot be combined (a removed "
                    "function already errors at every call site)",
                    self.cur.line,
                )
            if inline:
                raise LangError(
                    "@removed and @inline cannot be combined (a removed "
                    "function is uncallable, so there is nothing to inline)",
                    self.cur.line,
                )
            if asm:
                raise LangError(
                    "@removed and @asm cannot be combined (a removed "
                    "function is uncallable, so an asm body is meaningless)",
                    self.cur.line,
                )
            if static:
                raise LangError(
                    "@removed and @static cannot be combined (a file-local "
                    "tombstone serves no caller in another file)",
                    self.cur.line,
                )
        if override:
            if self.cur.kind != "fn":
                # A value-supplier promise on a function -- like @deprecated.
                raise LangError("@override only applies to functions", self.cur.line)
            if extern:
                raise LangError(
                    "@override and @extern cannot be combined (an @extern has "
                    "no mcc body to replace another with)",
                    self.cur.line,
                )
            if static:
                raise LangError(
                    "@override and @static cannot be combined (@override "
                    "replaces a member of another module's set, but a @static "
                    "function is file-local and never joins one)",
                    self.cur.line,
                )
            if removed is not None:
                raise LangError(
                    "@override and @removed cannot be combined (a definition "
                    "cannot both replace an existing overload and be a "
                    "tombstone)",
                    self.cur.line,
                )
            if private:
                # An @override reuses the target's public symbol and drops the
                # original wholesale (a global replacement); a @private symbol
                # is salted and file-local, so it cannot take over the public
                # one. The file-local variant would need distinct shadowing
                # semantics (keep the target, prefer the local member in its
                # own module) -- deferred.
                raise LangError(
                    "@override and @private cannot yet be combined (a private "
                    "override would need file-local shadowing semantics, not "
                    "the global replacement @override performs)",
                    self.cur.line,
                )
        if self.cur.kind in ("struct", "union"):
            if extern:
                raise LangError("@extern does not apply to structs", self.cur.line)
            return self.parse_struct(
                private, static, align, packed, volatile,
                union=self.cur.kind == "union",
            )
        # `type` is a contextual keyword: `type <name> = <type>;` at top level.
        # Elsewhere (a field, variable, or parameter) `type` stays an identifier.
        if (
            self.cur.kind == "IDENT"
            and self.cur.text == "type"
            and self.tokens[self.pos + 1].kind == "IDENT"
        ):
            if extern:
                raise LangError("@extern does not apply to type aliases", self.cur.line)
            if inline:
                raise LangError(
                    "@inline only applies to functions with a body", self.cur.line
                )
            if volatile:
                raise LangError(
                    "@volatile only applies to structs and extern variables",
                    self.cur.line,
                )
            return self.parse_type_alias(private, static)
        # `error` is a contextual keyword too: `error <name> { ... }` at top
        # level declares an error type. Elsewhere `error` stays an identifier,
        # and `error(` in expression position is the result constructor.
        if (
            self.cur.kind == "IDENT"
            and self.cur.text == "error"
            and self.tokens[self.pos + 1].kind == "IDENT"
        ):
            if extern:
                raise LangError(
                    "@extern does not apply to error declarations", self.cur.line
                )
            if inline:
                raise LangError(
                    "@inline only applies to functions with a body", self.cur.line
                )
            if volatile:
                raise LangError(
                    "@volatile only applies to structs and extern variables",
                    self.cur.line,
                )
            return self.parse_error_decl(private, static)
        if self.cur.kind == "enum":
            if extern:
                raise LangError("@extern does not apply to enums", self.cur.line)
            if inline:
                raise LangError(
                    "@inline only applies to functions with a body", self.cur.line
                )
            if volatile:
                raise LangError(
                    "@volatile only applies to structs and extern variables",
                    self.cur.line,
                )
            return self.parse_enum(private, static)
        if self.cur.kind == "let":
            line = self.advance().line
            if not extern and not static:
                raise LangError("top-level variables must be @extern or @static", line)
            name = self.expect("IDENT").text
            # The type may be omitted for an @static variable with an
            # initializer, which infers it (like a local `let`).
            type_name = self.parse_type_ref() if self.accept(":") else None
            init = None
            if self.accept("="):
                if extern:
                    raise LangError(
                        "an @extern variable cannot have an initializer", line
                    )
                init = self.parse_expr()
            self.expect(";")
            if type_name is None and init is None:
                raise LangError(
                    f"a top-level variable without an initializer needs a type: "
                    f"@static let {name}: int32;",
                    line,
                )
            return GlobalVar(
                name,
                type_name,
                line,
                private=private,
                volatile=volatile,
                static=static,
                init=init,
                symbol=symbol,
            )
        if self.cur.kind == "const":
            line = self.advance().line
            if static or extern or volatile:
                raise LangError(
                    "a const is already compile-time; @static/@extern/@volatile "
                    "do not apply",
                    line,
                )
            name = self.expect("IDENT").text
            type_name = self.parse_type_ref() if self.accept(":") else None
            self.expect("=")
            value = self.parse_expr()
            self.expect(";")
            return Const(name, type_name, value, line, private=private)
        if volatile:
            raise LangError(
                "@volatile only applies to structs and extern variables",
                self.cur.line,
            )
        return self.parse_function(
            private, static, extern, symbol, inline, asm, clobbers, deprecated,
            removed, noreturn, override, prop, acc,
        )

    # Tokens that can begin an expression; used to settle the `as T * x`
    # ambiguity (multiplication, not a pointer type).
    EXPR_START = {
        "INT",
        "FLOAT",
        "STRING",
        "CHAR",
        "IDENT",
        "true",
        "false",
        "null",
        "sizeof",
        "alignof",
        "offsetof",
        "typename",
        "len",
        "(",
        "[",
        "-",
        "!",
        "&",
        "~",
    }

    def parse_type_ref(
        self, greedy_stars: bool = True, allow_ref: bool = False
    ) -> TypeRef:
        """Parse a type reference.

        Handles ``[struct] name[<type, ...>][*...][[N]...]``, the
        function-pointer form ``fn(type, ...) -> ret``, and parenthesized
        grouping so a ``*`` can bind outside a function type. The ``struct``
        keyword is optional (C habit); struct-ness is resolved later by name. A
        trailing ``[N]`` makes a fixed-size array, so ``int32[10]`` is ten
        int32s. A leading ``const`` makes a read-only type, as in the element
        of a ``slice<const T>``.

        A leading ``&`` spells a reference type (``&T`` -- the by-hidden-
        reference writable convention, today's ``mut``). It is only allowed in
        a parameter-type or return-type slot, marked by ``allow_ref``; anywhere
        else it is rejected, keeping the no-reference-locals invariant and
        leaving ``&`` unambiguously the address-of operator in expressions.

        Args:
            greedy_stars: When ``True``, take every following ``*`` as pointer
                depth; when ``False``, stop where a ``*`` begins a
                multiplication (used after ``as``).
            allow_ref: When ``True``, a leading ``&`` is consumed and sets the
                result's ``mut`` flag; when ``False``, a leading ``&`` is a
                compile error (a reference type outside a parameter/return
                slot).

        Returns:
            The parsed ``TypeRef``.

        Raises:
            LangError: On a pointer to an array type, a misplaced ``&``, or
                other malformed type.
        """
        if self.cur.kind == "&":
            # `&T` -- a reference type (today's `mut` convention). Legal only
            # where a parameter or return type is expected; the inner parse
            # runs without allow_ref, so `&&T` is rejected as a nested `&`.
            amp = self.cur
            if not allow_ref:
                raise LangError(
                    "a '&' reference type is only allowed in a parameter or "
                    "return type",
                    amp.line,
                )
            self.advance()
            ref = self.parse_type_ref(greedy_stars)
            ref.mut = True
            return ref
        if self.cur.kind == "const":
            # A `const T` read-only qualifier (the element of a slice<const T>).
            # Binds to the whole following type ref; the qualifier rides on the
            # resulting TypeRef.
            self.advance()
            ref = self.parse_type_ref(greedy_stars)
            ref.const = True
            return ref
        if self.cur.kind == "fn":
            return self.parse_fn_type(greedy_stars)
        if self.cur.kind == "(":
            # A grouped type, so a `*` or `[N]` binds outside a function type:
            # (fn(int32) -> int32)* is a pointer to a function pointer, and
            # (fn(int32) -> int32)[8] is an array of eight function pointers.
            self.advance()
            inner = self.parse_type_ref()
            self.expect(")")
            extra = self.parse_stars(greedy_stars)
            if extra and inner.dims:
                raise LangError(
                    "pointer to an array type is not supported", self.cur.line
                )
            inner.stars += extra
            # Dimensions on the group are the outermost, so they come first.
            inner.dims = self.parse_dims() + inner.dims
            return inner
        if not self.accept("struct"):
            self.accept("union")
        name = self.expect("IDENT").text
        args = []
        if self.accept("<"):
            # `tuple<>` spells the empty tuple -- the one empty argument
            # list, so `int32<>` and an all-defaulted `pair<>` stay rejected
            # (a `>>` here is a nested close, e.g. `list<tuple<>>`).
            if not (name == "tuple" and self.cur.kind in (">", ">>")):
                args.append(self.parse_type_ref())
                while self.accept(","):
                    args.append(self.parse_type_ref())
            self.expect_close_angle()
        return TypeRef(
            name, args, self.parse_stars(greedy_stars), dims=self.parse_dims()
        )

    def parse_dims(self) -> list:
        """Parse trailing fixed-array dimensions ``[N]``, ``[expr]``, or ``[]``.

        Each non-empty dimension is a constant expression, evaluated in codegen.
        The two common forms are kept in a simpler shape: a plain integer literal
        becomes an ``int`` and a lone ``const`` name a ``str``; anything else is
        kept as its expression node. ``[]`` is an inferred dimension (``None``).

        Returns:
            The dimensions, outermost first.

        Raises:
            LangError: When an explicit integer-literal size is less than 1.
        """
        dims = []
        while self.cur.kind == "[":
            line = self.advance().line
            if self.cur.kind == "]":
                dims.append(None)  # an inferred [] dimension
            else:
                expr = self.parse_expr()
                if isinstance(expr, IntLit):
                    if expr.value < 1:
                        raise LangError(
                            f"array size must be at least 1, not {expr.value}", line
                        )
                    dims.append(expr.value)  # a literal size
                elif isinstance(expr, Var):
                    dims.append(expr.name)  # a const name, resolved in codegen
                else:
                    dims.append(expr)  # a constant expression, resolved in codegen
            self.expect("]")
        return dims

    def parse_fn_type(self, greedy_stars: bool) -> TypeRef:
        """Parse a function-pointer type ``fn(A, B) -> R``.

        A missing ``-> R`` means the function returns ``void``, as in a
        declaration; ``-> mut R`` spells a ``mut`` return, riding on the
        return ``TypeRef``'s ``mut`` flag like a parameter's.

        Args:
            greedy_stars: Passed to :meth:`parse_stars` for any ``*`` that
                follows the type.

        Returns:
            A ``TypeRef`` named ``"fn"`` with its ``params`` and ``ret`` set.
        """
        self.expect("fn")
        self.expect("(")
        params = []
        variadic = False
        while self.cur.kind != ")":
            if params:
                self.expect(",")
            if self.cur.kind == "...":
                ellipsis = self.advance()
                if not params:
                    raise LangError(
                        "'...' needs at least one parameter type before it",
                        ellipsis.line,
                    )
                if self.cur.kind != ")":
                    raise LangError("'...' must be the last parameter", ellipsis.line)
                variadic = True
                break
            # The per-parameter annotation slot, mirroring a declaration's
            # parameter list. Only @nonnull is part of the call contract a
            # function value carries; the other parameter annotations are
            # rejected with a pointer to why.
            is_nonnull = False
            while self.cur.kind == "ANNOT":
                if self.cur.text != "@nonnull":
                    raise LangError(
                        f"{self.cur.text} does not apply in a function type; "
                        "only @nonnull is part of a function value's call "
                        "contract",
                        self.cur.line,
                    )
                is_nonnull = True
                self.advance()
            # `&T` (or the deprecated `mut T`) in the type slot marks a
            # by-reference writable parameter, riding on the TypeRef's `mut`
            # flag; the declaration-side compose bans apply verbatim. A leading
            # `const` marks the read-only view: `fn(const &T)` composes the
            # `const` and the `&` reference into the read-only hidden reference
            # (parse_type_ref only consumes a leading `&`, so `const` is read
            # here to sit in front of it, then folded onto the TypeRef).
            slot_line = self.cur.line
            const_kw = bool(self.accept("const"))
            mut_kw = self.cur.kind == "mut"
            if mut_kw:
                self.advance()
            ref = self.parse_type_ref(allow_ref=True)
            if mut_kw:
                ref.mut = True
                ref.mut_deprecated = True
            if const_kw:
                ref.const = True
            if is_nonnull and ref.mut:
                raise LangError(
                    "a parameter cannot be both @nonnull and a reference "
                    "(a reference parameter is passed by hidden reference "
                    "and is never null)",
                    slot_line,
                )
            # `const &T` in a function type is the read-only reference view
            # (Phase B): the const rides on the TypeRef's `const` flag, the
            # reference on its `mut` flag, and function_type reconciles the pair
            # into a read-only hidden reference.
            ref.nonnull = is_nonnull
            params.append(ref)
        self.expect(")")
        ret = TypeRef("void")
        if self.accept("->"):
            # `-> &T`: the type spells a reference return (a call through the
            # value is an lvalue expression), riding on the return TypeRef's
            # `mut` flag exactly as a parameter's does. The deprecated `-> mut`
            # keyword sets the same flag. `-> own T` spells an own return (a
            # call through the value hands the caller the cleanup obligation);
            # the two never combine.
            slot_line = self.cur.line
            mut_kw = self.cur.kind == "mut"
            if mut_kw:
                self.advance()
            own_tok = self.cur if self.cur.kind == "own" else None
            is_own = bool(self.accept("own"))
            if mut_kw and is_own:
                raise LangError(
                    "a return cannot be both a reference and own (a reference "
                    "lends the caller a view of existing storage, own hands it "
                    "an owned value)",
                    own_tok.line,
                )
            ret = self.parse_type_ref(allow_ref=True)
            if mut_kw:
                ret.mut = True
                ret.mut_deprecated = True
            if is_own and ret.mut:
                raise LangError(
                    "a return cannot be both own and a reference (a reference "
                    "lends the caller a view of existing storage, own hands it "
                    "an owned value)",
                    slot_line,
                )
            if ret.mut and ret.const:
                raise LangError(
                    "a return cannot be both a reference and const "
                    "(a reference return must be writable)",
                    slot_line,
                )
            if is_own and (
                ret.name == "void" and not ret.stars and not ret.dims
            ):
                raise LangError(
                    "an own return needs a value to hand over; void owns "
                    "nothing",
                    own_tok.line,
                )
            ret.own = is_own
        return TypeRef(
            "fn",
            [],
            self.parse_stars(greedy_stars),
            params=params,
            ret=ret,
            variadic=variadic,
        )

    def parse_stars(self, greedy_stars: bool) -> int:
        """Count the pointer ``*`` tokens following a type.

        Args:
            greedy_stars: When ``False``, stop at a ``*`` whose next token can
                begin an expression, leaving it as multiplication (the
                ``as T * x`` ambiguity); when ``True``, consume every ``*``.

        Returns:
            The number of ``*`` consumed -- the pointer depth.
        """
        stars = 0
        while self.cur.kind == "*" and (
            greedy_stars or self.tokens[self.pos + 1].kind not in self.EXPR_START
        ):
            self.advance()
            stars += 1
        return stars

    def expect_close_angle(self):
        """Consume the ``>`` closing a type-argument list.

        A ``>>`` token here closes two nested generics (e.g.
        ``list<list<int32>>``): it is split, consuming the first ``>`` and
        leaving the second as the current token.

        Raises:
            LangError: When the current token is neither ``>`` nor ``>>``.
        """
        if self.cur.kind == ">>":
            if self.angle_splits is not None:
                self.angle_splits.append((self.pos, self.cur))
            self.tokens[self.pos] = Token(">", ">", self.cur.line)
            return
        self.expect(">")

    def try_struct_method_args(
        self,
    ) -> (
        tuple[
            list[TypeRef],
            dict[str, list[TypeRef]],
            dict[str, TypeRef],
            dict[str, TypeRef],
        ]
        | None
    ):
        """Speculatively read a method's pre-``::`` ``<...>`` as type-REFERENCES.

        A method namespaced to a generic struct writes the struct's type
        arguments before the ``::`` -- ``fn point<T>::m`` (a generic method),
        ``fn point<float64>::m`` (a specialization for one instantiation), or
        ``fn pair<int32, U>::m`` (a partial specialization). Whether an
        argument is a fresh type-parameter *name* or a concrete *type* is a
        question for codegen (which resolves names against the type
        environment, so any concrete type -- a builtin, a user struct, or a
        structured ``point<int32>`` / ``int32*`` -- may specialize a method),
        so the list is captured verbatim here as ``TypeRef``s.

        A DECORATION on a bare name -- a ``:`` type group, an ``extends``
        bound, or a ``=`` default, as in ``fn pair<int32, U: int8 | int16>::m``
        -- is captured alongside the argument; codegen requires the decorated
        name to be a fresh type parameter and runs the declaration-shape checks
        :meth:`parse_type_params` performs at parse time (it cannot run here:
        which bare names are parameters is unknown until the type environment
        exists). A decoration on a structured type, a parameter carrying both
        a group and a bound, or any other malformed list restores the cursor
        and returns ``None`` -- as does a non-method ``<...>`` (no ``::`` after
        the list) -- leaving :meth:`parse_type_params` to read the declaration
        list (and report its errors) as before.

        Returns:
            The struct type-argument references plus their ``{name: ...}``
            group, bound, and default decorations, with the cursor left on the
            ``::``; or ``None`` with the cursor restored.
        """
        if self.cur.kind != "<":
            return None
        saved = self.pos
        splits = self.angle_splits = []

        def backtrack() -> None:
            # Undo the in-place `>>` splits this speculation performed (a
            # nested `slice<char>>` would otherwise re-parse one `>` short),
            # then restore the cursor.
            for pos, tok in splits:
                self.tokens[pos] = tok
            self.pos = saved
            return None

        self.advance()  # '<'
        args: list[TypeRef] = []
        groups: dict[str, list[TypeRef]] = {}
        bounds: dict[str, TypeRef] = {}
        defaults: dict[str, TypeRef] = {}
        try:
            while True:
                ref = self.parse_type_ref()
                args.append(ref)
                # Only a bare name (a would-be fresh type parameter) may carry
                # a decoration; a marker after anything structured falls
                # through to the backtrack below.
                bare = not (
                    ref.args
                    or ref.stars
                    or ref.dims
                    or ref.const
                    or ref.nonnull
                    or ref.mut
                    or ref.params is not None
                )
                if bare and self.cur.kind == ":":
                    self.advance()
                    members = [self.parse_type_ref()]
                    while self.accept("|"):
                        members.append(self.parse_type_ref())
                    groups[ref.name] = members
                if bare and self.cur.kind == "extends":
                    if ref.name in groups:
                        # Both a group and a bound: parse_type_params owns
                        # that error, with its exact message.
                        return backtrack()
                    self.advance()
                    bounds[ref.name] = self.parse_type_ref()
                if bare and self.cur.kind == "=":
                    self.advance()
                    defaults[ref.name] = self.parse_type_ref()
                if self.cur.kind == ">":
                    self.advance()
                    break
                if not self.accept(","):
                    # Any unexpected token means this is not a struct
                    # type-argument list: hand it back to parse_type_params.
                    return backtrack()
        except LangError:
            return backtrack()
        finally:
            self.angle_splits = None
        if self.cur.kind != "::":
            return backtrack()
        return args, groups, bounds, defaults

    def parse_type_params(
        self,
    ) -> tuple[
        list[str],
        dict[str, TypeRef],
        dict[str, list[TypeRef]],
        dict[str, TypeRef],
    ]:
        """Parse an optional generic parameter list ``<A, B = type, ...>``.

        A parameter may declare a default type (``<T = int64>``), used when a
        type argument is neither supplied nor inferred from a *typed* value.
        Defaults must be trailing (every parameter after a defaulted one must
        also have a default) and a default may reference only type parameters
        declared before it -- ``<T = T>`` and ``<T = U, U = int32>`` are both
        errors, so a later parameter's name can never fall through to a
        same-named global type.

        A parameter may also declare a **closed type group** -- a
        pipe-separated list of types after a ``:`` (``<T: int64 | int32>``),
        the only types the parameter may instantiate to. Group members must be
        concrete types: a member may not reference any of the declaration's
        type parameters. The group composes with a default
        (``<T: int64 | int32 = int32>``), which must then name a group member
        (membership is checked at declaration, where members resolve).

        A parameter may instead declare a **nominal bound** -- a struct after
        ``extends`` (``<T extends shape>``), constraining the parameter to that
        struct and its declared ``extends`` lineage. Unlike a group member,
        the bound target may reference type parameters -- the enclosing
        method qualifier's (``fn list<T>::equals<U extends slice<T>>``) or
        the list's own (``<S, T extends box<S>>``) -- forming a *dependent*
        bound that codegen resolves per instantiation instead of at the
        declaration. The set is open-ended, so a bound composes with a
        default (``<T extends shape = circle>``) whose satisfaction is
        checked where the bound resolves. A parameter may not carry both a
        bound and a group.

        Returns:
            The type-parameter names, the ``{name: TypeRef}`` defaults, the
            ``{name: [TypeRef, ...]}`` closed type groups, and the
            ``{name: TypeRef}`` nominal bounds.

        Raises:
            LangError: On a non-trailing default, a default referencing the
                parameter itself or a later parameter, a group member, a
                grouped parameter's default, or a bound referencing a type
                parameter, or a parameter carrying both a bound and a group.
        """
        type_params: list[str] = []
        defaults: dict[str, TypeRef] = {}
        groups: dict[str, list[TypeRef]] = {}
        bounds: dict[str, TypeRef] = {}
        lines: dict[str, int] = {}
        if self.accept("<"):
            while True:
                tok = self.expect("IDENT")
                type_params.append(tok.text)
                lines[tok.text] = tok.line
                if self.accept(":"):
                    members = [self.parse_type_ref()]
                    while self.accept("|"):
                        members.append(self.parse_type_ref())
                    groups[tok.text] = members
                if self.accept("extends"):
                    if tok.text in groups:
                        raise LangError(
                            f"type parameter {tok.text!r} cannot have both a "
                            "closed type group and an 'extends' bound",
                            tok.line,
                        )
                    bounds[tok.text] = self.parse_type_ref()
                if self.accept("="):
                    defaults[tok.text] = self.parse_type_ref()
                elif defaults:
                    raise LangError(
                        f"type parameter {tok.text!r} without a default cannot "
                        "follow a defaulted one",
                        tok.line,
                    )
                if not self.accept(","):
                    break
            self.expect(">")
            for i, pname in enumerate(type_params):
                ref = defaults.get(pname)
                if ref is None:
                    continue
                # A default may name only *earlier* parameters: check its
                # referenced names against this parameter and every later one.
                bad = type_ref_names(ref) & set(type_params[i:])
                if bad:
                    raise LangError(
                        f"default for type parameter {pname!r} references "
                        f"{min(bad)!r}, which is not declared before it",
                        lines[pname],
                    )
            for pname, members in groups.items():
                # Group members are concrete types only: a member referencing
                # a type parameter (any of them -- there is no earlier-only
                # allowance) is not a closed set of types.
                for member in members:
                    bad = type_ref_names(member) & set(type_params)
                    if bad:
                        raise LangError(
                            f"type group member {member} for parameter "
                            f"{pname!r} references type parameter "
                            f"{min(bad)!r}; group members must be concrete "
                            "types",
                            lines[pname],
                        )
                # A grouped parameter's default must name a group member, and
                # members are concrete -- so a default referencing an earlier
                # parameter can never qualify. Rejecting it here keeps the
                # declaration-time membership check (which resolves types)
                # from reporting a confusing unknown-type error.
                ref = defaults.get(pname)
                if ref is not None:
                    bad = type_ref_names(ref) & set(type_params)
                    if bad:
                        raise LangError(
                            f"default for type parameter {pname!r} references "
                            f"{min(bad)!r}; a grouped parameter's default "
                            "must name a group member",
                            lines[pname],
                        )
        return type_params, defaults, groups, bounds

    def parse_struct(
        self,
        private: bool = False,
        static: bool = False,
        align: int | None = None,
        packed: bool = False,
        volatile: bool = False,
        union: bool = False,
    ) -> "StructDecl | UnionDecl":
        """Parse a ``struct`` or ``union`` declaration with its fields.

        A ``union`` shares the surface syntax (generic type parameters, the same
        annotations) but its members share one storage, so the struct-only forms
        -- ``extends``, field defaults, and flexible array members -- are
        rejected for it, and it parses into its own :class:`UnionDecl` node so a
        struct-only code path can never accept it.

        Args:
            private: Whether ``@private`` was applied.
            static: Whether ``@static`` was applied.
            align: The ``@align(N)`` value, or ``None``.
            packed: Whether ``@packed`` was applied.
            volatile: Whether ``@volatile`` was applied.
            union: ``True`` to parse a ``union`` declaration.

        Returns:
            The parsed ``StructDecl``, or ``UnionDecl`` for a ``union``.

        Raises:
            LangError: On ``extends`` or a member default in a ``union``.
        """
        line = self.expect("union" if union else "struct").line
        name = self.expect("IDENT").text
        type_params, type_param_defaults, groups, bounds = self.parse_type_params()
        if groups:
            # Closed type groups are a function-level constraint (they drive
            # call viability, overload partitioning, and eager instantiation
            # checks); a struct has no call site to filter.
            raise LangError(
                "type groups are only supported on function type parameters",
                line,
            )
        if bounds:
            # `extends` bounds, like closed groups, constrain call viability
            # and overload ranking -- both function-only in v1. (A struct's own
            # `extends` clause is the base-layout mechanism, unrelated.)
            raise LangError(
                "type-parameter bounds are only supported on function type "
                "parameters",
                line,
            )
        base = None
        if self.cur.kind == "extends":
            if union:
                raise LangError("a union cannot extend another type", self.cur.line)
            self.advance()
            base = self.parse_base_ref()
        # The body is required, except `struct B extends A;` (a specialization
        # that adds no fields of its own).
        defaults = {}
        if base is not None and self.accept(";"):
            fields = []
        else:
            self.expect("{")
            fields = []
            while self.cur.kind != "}":
                fname = self.expect("IDENT").text
                self.expect(":")
                ftype = self.parse_type_ref()
                if self.cur.kind == "=":  # name: type = default;
                    if union:
                        raise LangError(
                            "a union member cannot declare a default value",
                            self.cur.line,
                        )
                    self.advance()
                    defaults[fname] = self.parse_expr()
                fields.append((fname, ftype))
                self.expect(";")
            self.expect("}")
        if union:
            # A union has no `extends` base and no member defaults (both
            # rejected above), so its node carries neither -- it is its own
            # kind, not a struct wearing a flag.
            return UnionDecl(
                name,
                type_params,
                fields,
                line,
                private=private,
                static=static,
                align=align,
                packed=packed,
                volatile=volatile,
                type_param_defaults=type_param_defaults,
            )
        return StructDecl(
            name,
            type_params,
            fields,
            line,
            base=base,
            private=private,
            static=static,
            align=align,
            packed=packed,
            volatile=volatile,
            defaults=defaults,
            type_param_defaults=type_param_defaults,
        )

    def parse_base_ref(self) -> TypeRef:
        """Parse the base in ``extends Base``: a struct name, optionally generic.

        The ``struct`` keyword is optional, as elsewhere, and generic arguments
        are allowed (``extends pair<K, V>``). The base may not be a pointer,
        array, or function type.

        Raises:
            LangError: When the base is a pointer, array, or function type.
        """
        ref = self.parse_type_ref()
        if ref.stars or ref.dims or ref.params is not None:
            raise LangError("a struct can only extend a struct name", self.cur.line)
        return ref

    def parse_enum(self, private: bool = False, static: bool = False) -> EnumDecl:
        """Parse an ``enum`` declaration.

        ``enum Name[: type] { Member = value, ... }`` -- the underlying type is
        optional (defaulting to ``int32``), each member carries an explicit
        ``= value`` expression, and a trailing comma is allowed.

        Args:
            private: Whether ``@private`` was applied.
            static: Whether ``@static`` was applied.

        Returns:
            The parsed ``EnumDecl``.

        Raises:
            LangError: When the enum has no members.
        """
        line = self.expect("enum").line
        name = self.expect("IDENT").text
        underlying = self.parse_type_ref() if self.accept(":") else None
        self.expect("{")
        members = []
        while self.cur.kind != "}":
            mname = self.expect("IDENT").text
            self.expect("=")
            members.append((mname, self.parse_expr()))
            if not self.accept(","):  # a trailing comma is allowed
                break
        self.expect("}")
        if not members:
            raise LangError(f"enum {name!r} has no members", line)
        return EnumDecl(name, underlying, members, line, private=private, static=static)

    def parse_error_decl(
        self, private: bool = False, static: bool = False
    ) -> EnumDecl:
        """Parse an ``error`` declaration.

        ``error name { VARIANT, VARIANT = "display", ... }`` -- the leading
        ``error`` is a contextual keyword (an identifier elsewhere), already
        confirmed by the caller. Variants always auto-number from 1 in
        declaration order; error values are automatic, so there is no explicit
        ``= n`` form. The ``=`` slot instead carries an optional display string
        (stored, not a value -- the variant still auto-numbers), and a bare
        ``= <int>`` is a compile error. The type is always ``int32``-backed, so
        there is no ``:`` underlying slot. A trailing comma is allowed, as in an
        ``enum``.

        Args:
            private: Whether ``@private`` was applied.
            static: Whether ``@static`` was applied.

        Returns:
            The parsed ``EnumDecl`` with ``is_error`` set.

        Raises:
            LangError: When the declaration has no variants, or when a variant's
                ``=`` slot carries anything but a display string.
        """
        line = self.advance().line  # the 'error' identifier
        name = self.expect("IDENT").text
        self.expect("{")
        members = []
        displays: dict[str, str] = {}
        while self.cur.kind != "}":
            mname = self.expect("IDENT").text
            if self.accept("="):
                if self.cur.kind != "STRING":
                    raise LangError(
                        f"error {name!r} member {mname!r}: error values are "
                        "automatic; '=' sets a display string, not a value",
                        self.cur.line,
                    )
                tok = self.advance()
                displays[mname] = _unescape(tok.text[1:-1])
            members.append((mname, None))
            if not self.accept(","):  # a trailing comma is allowed
                break
        self.expect("}")
        if not members:
            raise LangError(f"error {name!r} has no members", line)
        return EnumDecl(
            name, None, members, line,
            private=private, static=static, is_error=True, displays=displays,
        )

    def parse_type_alias(
        self, private: bool = False, static: bool = False
    ) -> TypeAlias:
        """Parse a ``type <name> = <type>;`` declaration.

        The leading ``type`` is a contextual keyword (an identifier elsewhere),
        already confirmed by the caller.

        Args:
            private: Whether ``@private`` was applied.
            static: Whether ``@static`` was applied.

        Returns:
            The parsed ``TypeAlias``.
        """
        line = self.advance().line  # the 'type' identifier
        name = self.expect("IDENT").text
        # A generic alias names a family: `type entry<T> = pair<char*, T>;`.
        # Constraints (closed type groups and `extends` bounds) do not extend
        # to alias parameters yet, so only the names and their defaults are
        # carried.
        type_params, type_param_defaults, _, _ = self.parse_type_params()
        self.expect("=")
        target = self.parse_type_ref()
        self.expect(";")
        return TypeAlias(
            name,
            target,
            line,
            type_params=type_params,
            type_param_defaults=type_param_defaults,
            private=private,
            static=static,
        )

    def parse_function(
        self,
        private: bool = False,
        static: bool = False,
        extern: bool = False,
        symbol: str | None = None,
        inline: bool = False,
        asm: bool = False,
        clobbers: list[str] | None = None,
        deprecated: str | None = None,
        removed: str | None = None,
        noreturn: bool = False,
        override: bool = False,
        prop: str | None = None,
        acc: str | None = None,
    ) -> Func:
        """Parse a function definition, an ``@extern`` declaration, or a proto.

        Reads the (optionally generic) signature, an optional trailing ``...``
        for an extern variadic (or the ``name...`` native-variadic sugar, a
        ``const`` parameter of type ``slice<const any>``), and then either a
        body or a terminating ``;``.
        A bare ``;`` on a non-extern function makes a bodyless prototype: a
        concrete mcc function defined in another object, called with the mcc
        convention (interface stubs emit these).

        Args:
            private: Whether ``@private`` was applied.
            static: Whether ``@static`` was applied.
            extern: Whether ``@extern`` was applied (declaration only).
            symbol: The ``@symbol("...")`` linker name, or ``None``.
            inline: Whether ``@inline`` was applied (``alwaysinline``).
            asm: Whether ``@asm`` was applied (the body is one asm expression).
            clobbers: Registers clobbered by an ``@asm fn`` body, or ``None``.
            deprecated: The ``@deprecated("...")`` message, or ``None``.
            removed: The ``@removed("...")`` tombstone message, or ``None``.
            noreturn: Whether ``@noreturn`` was applied (never returns).
            override: Whether ``@override`` was applied (replaces a
                same-pattern member of another module's overload set).

        Returns:
            The parsed ``Func``.

        Raises:
            LangError: On a generic-extern, generic-variadic, or malformed
                ``...`` parameter, or a generic/``@inline``/``@asm``/``@static``
                prototype (their body or symbol cannot live elsewhere) -- except
                a generic ``@removed`` tombstone, which never instantiates and
                so may (and idiomatically does) go bodiless.
        """
        line = self.expect("fn").line
        name = self.expect("IDENT").text
        # `fn Type::method(...)` namespaces the function to a struct: the name
        # becomes the single string `"Type::method"`, which threads unchanged
        # through registration, overloading, and symbol emission. Codegen
        # validates that `Type` is a declared struct.
        #
        # For a generic struct the type parameters sit *before* the `::`
        # (`fn point<T>::method`), so parse a type-parameter list first and,
        # only when a `::` follows, treat it as the struct's parameters: read
        # the method name, then parse the method's *own* parameter list after
        # it (`fn box<T>::map<U>`). The two lists merge into one uniform
        # generic template -- concatenated names, merged defaults/groups/bounds
        # -- so the whole existing generic machinery treats a method's struct
        # and method parameters identically. A method parameter may not shadow
        # one of the struct's, so a name that appears in both lists is an error.
        # A pre-`::` `<...>` (`fn point<T>::m`, `fn point<float64>::m`,
        # `fn pair<int32, U>::m`) is held verbatim as `struct_type_args` for
        # codegen to classify: all fresh names is a generic method, all
        # concrete types a specialization, and a mix a PARTIAL specialization
        # (the concrete positions bind, the fresh names stay type parameters).
        # A decorated bare name (`fn pair<int32, U: int8 | int16>::m`) rides
        # along with its group/bound/default -- codegen checks the decorated
        # name is fresh. A method's OWN type parameters (after `::method`)
        # still parse as an ordinary declaration list; the struct-vs-method
        # shadow check waits until codegen knows which of the struct arguments
        # are parameter names.
        struct_args = self.try_struct_method_args()
        struct_arg_groups: dict[str, list[TypeRef]] = {}
        struct_arg_bounds: dict[str, TypeRef] = {}
        struct_arg_defaults: dict[str, TypeRef] = {}
        if struct_args is not None:
            (
                struct_type_args,
                struct_arg_groups,
                struct_arg_bounds,
                struct_arg_defaults,
            ) = struct_args
            self.expect("::")
            method = self.expect("IDENT").text
            name = f"{name}::{method}"
            (
                type_params,
                type_param_defaults,
                type_param_groups,
                type_param_bounds,
            ) = self.parse_type_params()
        else:
            struct_type_args = None
            type_params, type_param_defaults, type_param_groups, type_param_bounds = (
                self.parse_type_params()
            )
            if self.cur.kind == "::":
                # A method with no pre-`::` list at all (`fn point::m` -- the
                # non-generic-struct form: the empty parse_type_params above
                # consumed nothing). Read the method name and merge its own
                # parameter list, exactly as before.
                self.advance()
                method = self.expect("IDENT").text
                name = f"{name}::{method}"
                (
                    m_params,
                    m_defaults,
                    m_groups,
                    m_bounds,
                ) = self.parse_type_params()
                shadowed = set(type_params) & set(m_params)
                if shadowed:
                    raise LangError(
                        f"method type parameter {min(shadowed)!r} shadows a type "
                        f"parameter of struct {name.split('::', 1)[0]!r}",
                        line,
                    )
                type_params = type_params + m_params
                type_param_defaults = {**type_param_defaults, **m_defaults}
                type_param_groups = {**type_param_groups, **m_groups}
                type_param_bounds = {**type_param_bounds, **m_bounds}
        if extern and type_params:
            raise LangError("extern functions cannot be generic", line)
        self.expect("(")
        params = []
        const_params: set[str] = set()
        constref_params: set[str] = set()
        mut_params: set[str] = set()
        noalias_params: set[str] = set()
        nonnull_params: set[str] = set()
        format_params: set[str] = set()
        mut_kw_param_lines: list[int] = []  # deprecated `mut` binder sites
        mut_kw_return_line: int | None = None  # a deprecated `-> mut` site
        variadic = False
        while self.cur.kind != ")":
            if params:
                self.expect(",")
            if self.cur.kind == "...":
                ellipsis = self.advance()
                if not params:
                    raise LangError(
                        "'...' needs at least one named parameter before it",
                        ellipsis.line,
                    )
                if self.cur.kind != ")":
                    raise LangError("'...' must be the last parameter", ellipsis.line)
                variadic = True
                break
            is_noalias = False
            is_nonnull = False
            is_format = False
            # Per-parameter annotations come before const/mut, in any order.
            while self.cur.kind == "ANNOT" and self.cur.text in (
                "@noalias",
                "@nonnull",
                "@format",
            ):
                if self.cur.text == "@noalias":
                    is_noalias = True
                elif self.cur.text == "@nonnull":
                    is_nonnull = True
                else:
                    is_format = True
                self.advance()
            is_const = bool(self.accept("const"))
            # The deprecated `mut x: T` binder spelling: `mut` before the name.
            # The blessed spelling puts the reference marker in the type slot
            # (`x: &T`), read off the parsed TypeRef's `mut` flag below.
            mut_kw_tok = self.cur if self.cur.kind == "mut" else None
            if mut_kw_tok is not None:
                self.advance()
            pname = self.expect("IDENT").text
            if self.cur.kind == "...":
                # `args...` -- native variadic sugar: a const parameter of type
                # slice<const any>, the trailing type that marks a collecting
                # function (the call site boxes its extra arguments into it).
                ellipsis = self.advance()
                if (
                    is_const
                    or mut_kw_tok is not None
                    or is_noalias
                    or is_nonnull
                    or is_format
                ):
                    raise LangError(
                        f"'{pname}...' cannot take const, a reference, "
                        "@noalias, @nonnull, or @format (it is already a const "
                        "slice<const any>)",
                        ellipsis.line,
                    )
                if self.cur.kind != ")":
                    raise LangError(
                        f"'{pname}...' must be the last parameter", ellipsis.line
                    )
                # The collector is a plain `const args: slice<const any>`: a
                # by-value read-only copy of the caller-built slice header (no
                # owned resource to double-free), so it is an ordinary const
                # param -- not the `const &` view.
                const_params.add(pname)
                params.append(
                    (pname, TypeRef("slice", [TypeRef("any", const=True)]))
                )
                break
            self.expect(":")
            slot_line = self.cur.line
            ptype = self.parse_type_ref(allow_ref=True)
            # A parameter's mutability lives in `mut_params`, not on the stored
            # TypeRef, so a `&T` marker is lifted off the type and the ref is
            # left pristine (zero representation change from `mut x: T`).
            is_mut = ptype.mut or mut_kw_tok is not None
            ptype.mut = False
            ptype.mut_deprecated = False
            # `const x: &T` is the read-only reference view (Phase B): the
            # `const` binder annotation applied to a `&T` reference type. It is
            # read-only (in const_params) yet passed by hidden reference (in
            # constref_params), never writable -- so it does NOT join
            # mut_params. A plain `&T` (no const) stays the writable reference.
            is_constref = is_const and is_mut
            if is_noalias and is_mut:
                raise LangError(
                    "a parameter cannot be both @noalias and a reference "
                    "(aliasing reference parameters is allowed by design)",
                    slot_line,
                )
            if is_nonnull and is_mut:
                raise LangError(
                    "a parameter cannot be both @nonnull and a reference "
                    "(a reference parameter is passed by hidden reference "
                    "and is never null)",
                    slot_line,
                )
            if is_format and is_mut and not is_const:
                # A writable reference to a format string is rejected (it is
                # read, never written); a read-only `const &` view is allowed.
                raise LangError(
                    "a parameter cannot be both @format and a reference "
                    "(a format string is read, never written)",
                    slot_line,
                )
            if is_const:
                const_params.add(pname)
            if is_constref:
                constref_params.add(pname)
            elif is_mut:
                mut_params.add(pname)
                if mut_kw_tok is not None:
                    mut_kw_param_lines.append(mut_kw_tok.line)
            if is_noalias:
                noalias_params.add(pname)
            if is_nonnull:
                nonnull_params.add(pname)
            if is_format:
                format_params.add(pname)
            params.append((pname, ptype))
        self.expect(")")
        ret_type = TypeRef("void")
        mut_return = False
        own_return = False
        if self.accept("->"):
            # `-> &T`: the function returns an lvalue (a reference to
            # caller-reachable storage). A flag on the declaration; the
            # fn(...) -> &T pointer type spells the same convention. The
            # deprecated `-> mut T` keyword spells the same thing. `-> own T`:
            # the function returns an owned value (the caller adopts the
            # cleanup obligation). Also a flag, and the two are mutually
            # exclusive: a reference lends a view, own hands over a value.
            ret_slot_line = self.cur.line
            mut_kw_tok = self.cur if self.cur.kind == "mut" else None
            if mut_kw_tok is not None:
                self.advance()
            own_tok = self.cur if self.cur.kind == "own" else None
            own_return = bool(self.accept("own"))
            if mut_kw_tok is not None and own_return:
                raise LangError(
                    "a return cannot be both a reference and own (a reference "
                    "lends the caller a view of existing storage, own hands it "
                    "an owned value)",
                    own_tok.line,
                )
            ret_type = self.parse_type_ref(allow_ref=True)
            mut_return = ret_type.mut or mut_kw_tok is not None
            # Mutability lives in the `mut_return` flag, not on the TypeRef.
            ret_type.mut = False
            ret_type.mut_deprecated = False
            if mut_kw_tok is not None:
                mut_kw_return_line = mut_kw_tok.line
            if own_return and mut_return:
                raise LangError(
                    "a return cannot be both own and a reference (a reference "
                    "lends the caller a view of existing storage, own hands it "
                    "an owned value)",
                    ret_slot_line,
                )
            if mut_return and ret_type.const:
                raise LangError(
                    "a return cannot be both a reference and const "
                    "(a reference return must be writable)",
                    ret_slot_line,
                )
            if own_return and (
                ret_type.name == "void"
                and not ret_type.stars
                and not ret_type.dims
            ):
                raise LangError(
                    "an own return needs a value to hand over; void owns "
                    "nothing",
                    own_tok.line,
                )
        if variadic and type_params:
            raise LangError("a generic function cannot be variadic", line)
        if (mut_params or constref_params) and extern:
            # A by-value `const x: T` is the ordinary C by-value convention, so
            # it is allowed on @extern (a callee-side read-only discipline C
            # cannot see). A reference parameter -- writable `&T` or the
            # read-only `const &T` view -- is a hidden pointer that would change
            # the C calling convention, so it stays rejected.
            raise LangError(
                "reference parameters are not allowed on @extern functions "
                "(they would change the C calling convention)",
                line,
            )
        if mut_return and extern:
            raise LangError(
                "a reference return is not allowed on @extern functions "
                "(it would change the C calling convention)",
                line,
            )
        if own_return and extern:
            raise LangError(
                "an own return is not allowed on @extern functions "
                "(C has no destructor obligation to hand over)",
                line,
            )
        if own_return and asm:
            raise LangError(
                "an own return is not allowed on @asm functions "
                "(an asm template constructs nothing to hand over)",
                line,
            )
        if own_return and (prop or acc):
            raise LangError(
                "a @property or @accessor method cannot return own "
                "(reads through field or index syntax never transfer "
                "ownership)",
                line,
            )
        if extern:  # a declaration: signature only, no body
            self.expect(";")
            return Func(
                name,
                type_params,
                params,
                ret_type,
                [],
                line,
                private=private,
                extern=True,
                variadic=variadic,
                symbol=symbol,
                # Both attribute-only: no ABI change, so allowed on @extern.
                noalias_params=noalias_params,
                nonnull_params=nonnull_params,
                # Threaded so codegen rejects it (an @extern never collects).
                format_params=format_params,
                noreturn=noreturn,
                deprecated_msg=deprecated,
                removed_msg=removed,
                type_param_defaults=type_param_defaults,
                type_param_groups=type_param_groups,
                type_param_bounds=type_param_bounds,
            )
        if asm:
            # `@asm fn` is sugar for a function whose body is one @asm(...)
            # expression over its parameters: the params are the inputs, the
            # return type is the output. No `ret` -- the epilogue returns.
            if self.cur.kind == ";":
                raise LangError(
                    "an @asm function cannot be a bodyless prototype "
                    "(its body is the asm template)",
                    line,
                )
            if variadic:
                raise LangError("an @asm function cannot be variadic", line)
            if const_params:
                raise LangError(
                    "const parameters are not allowed on @asm functions", line
                )
            if mut_params:
                raise LangError(
                    "reference parameters are not allowed on @asm functions",
                    line,
                )
            if mut_return:
                raise LangError(
                    "a reference return is not allowed on @asm functions "
                    "(the template computes a value, not a reference)",
                    line,
                )
            if noalias_params:
                raise LangError(
                    "@noalias parameters are not allowed on @asm functions", line
                )
            if nonnull_params:
                raise LangError(
                    "@nonnull parameters are not allowed on @asm functions", line
                )
            if format_params:
                raise LangError(
                    "@format parameters are not allowed on @asm functions", line
                )
            template = self.parse_asm_body(line)
            inputs = [Var(pname, line) for pname, _ in params]
            is_void = (
                ret_type.name == "void" and not ret_type.stars and not ret_type.dims
            )
            out_type = None if is_void else ret_type
            node = Asm(template, inputs, out_type, line, clobbers or [])
            body = [ExprStmt(node, line) if is_void else Return(node, line)]
            return Func(
                name,
                type_params,
                params,
                ret_type,
                body,
                line,
                private=private,
                static=static,
                inline=inline,
                noreturn=noreturn,
                deprecated_msg=deprecated,
                override=override,
                type_param_defaults=type_param_defaults,
                type_param_groups=type_param_groups,
                type_param_bounds=type_param_bounds,
                struct_type_args=struct_type_args,
                struct_arg_groups=struct_arg_groups,
                struct_arg_bounds=struct_arg_bounds,
                struct_arg_defaults=struct_arg_defaults,
            )
        if self.cur.kind == ";":
            # A bodyless prototype: a concrete mcc function defined in another
            # object, called with the mcc convention (hidden references for
            # mut/const-struct parameters included). The convention is a pure
            # function of the signature, so the prototype carries everything a
            # caller needs. Interface stubs are the usual writer.
            self.advance()
            if prop:
                raise LangError(
                    "a @property method needs a body (a bodyless prototype "
                    "has none to call)",
                    line,
                )
            if acc:
                raise LangError(
                    "an @accessor method needs a body (a bodyless prototype "
                    "has none to call)",
                    line,
                )
            if type_params and removed is None:
                # An @removed tombstone is the one generic that may go
                # bodyless: it never instantiates, so no body needs to travel.
                raise LangError(
                    "a generic function cannot be a bodyless prototype "
                    "(its body must travel to be instantiated)",
                    line,
                )
            if inline:
                raise LangError(
                    "an @inline function cannot be a bodyless prototype "
                    "(its body must travel to be inlined)",
                    line,
                )
            if static:
                raise LangError(
                    "a @static function cannot be a bodyless prototype "
                    "(its symbol is file-local, so no other object can "
                    "define it)",
                    line,
                )
            if override:
                raise LangError(
                    "an @override function cannot be a bodyless prototype "
                    "(there is no body to replace the existing overload with)",
                    line,
                )
            return Func(
                name,
                type_params,
                params,
                ret_type,
                [],
                line,
                private=private,
                proto=True,
                variadic=variadic,
                const_params=const_params,
                constref_params=constref_params,
                mut_params=mut_params,
                noalias_params=noalias_params,
                nonnull_params=nonnull_params,
                format_params=format_params,
                noreturn=noreturn,
                mut_return=mut_return,
                own_return=own_return,
                deprecated_msg=deprecated,
                removed_msg=removed,
                type_param_defaults=type_param_defaults,
                type_param_groups=type_param_groups,
                type_param_bounds=type_param_bounds,
                struct_type_args=struct_type_args,
                struct_arg_groups=struct_arg_groups,
                struct_arg_bounds=struct_arg_bounds,
                struct_arg_defaults=struct_arg_defaults,
                mut_kw_param_lines=mut_kw_param_lines,
                mut_kw_return_line=mut_kw_return_line,
            )
        if prop:
            if "::" not in name:
                raise LangError(
                    "@property only applies to a method "
                    "(a qualified `fn Type::name`)",
                    line,
                )
            if variadic:
                raise LangError("a @property method cannot be variadic", line)
            if prop == "set":
                # The setter: exactly (self, value). Its return, if any, is
                # ignored at the assignment that calls it.
                if len(params) != 2:
                    raise LangError(
                        'a @property("set") method takes its receiver and '
                        "the assigned value (exactly two parameters)",
                        line,
                    )
            else:
                # A bare @property or @property("get"): a receiver-only,
                # value-returning accessor.
                if len(params) != 1:
                    raise LangError(
                        "a @property method takes only its receiver "
                        "(no other parameters)",
                        line,
                    )
                if ret_type.name == "void" and not ret_type.stars and not ret_type.dims:
                    raise LangError("a @property method must return a value", line)
                if prop == "get" and mut_return:
                    raise LangError(
                        'a @property("get") method cannot return a reference; '
                        "a bare @property is the reference-lvalue form, "
                        '@property("set") the explicit write path',
                        line,
                    )
        if acc:
            if "::" not in name:
                raise LangError(
                    "@accessor only applies to a method "
                    "(a qualified `fn Type::name`)",
                    line,
                )
            if variadic:
                raise LangError("an @accessor method cannot be variadic", line)
            if acc == "set":
                # The setter: (self, indices..., value). Its return, if any,
                # is ignored at the assignment that calls it.
                if len(params) < 3:
                    raise LangError(
                        'an @accessor("set") method takes its receiver, at '
                        "least one index, and the assigned value last "
                        "(at least three parameters)",
                        line,
                    )
            else:
                # A bare @accessor or @accessor("get"): receiver plus the
                # indices, value-returning.
                if len(params) < 2:
                    raise LangError(
                        "an @accessor method takes its receiver and at "
                        "least one index (at least two parameters)",
                        line,
                    )
                if ret_type.name == "void" and not ret_type.stars and not ret_type.dims:
                    raise LangError(
                        "an @accessor method must return a value", line
                    )
                if acc == "get" and mut_return:
                    raise LangError(
                        'an @accessor("get") method cannot return a reference; '
                        "a bare @accessor is the reference-lvalue form, "
                        '@accessor("set") the explicit write path',
                        line,
                    )
        return Func(
            name,
            type_params,
            params,
            ret_type,
            self.parse_block(),
            line,
            private=private,
            static=static,
            variadic=variadic,
            inline=inline,
            const_params=const_params,
            constref_params=constref_params,
            mut_params=mut_params,
            noalias_params=noalias_params,
            nonnull_params=nonnull_params,
            format_params=format_params,
            noreturn=noreturn,
            mut_return=mut_return,
            own_return=own_return,
            deprecated_msg=deprecated,
            removed_msg=removed,
            override=override,
            property=prop,
            accessor=acc,
            type_param_defaults=type_param_defaults,
            type_param_groups=type_param_groups,
            type_param_bounds=type_param_bounds,
            struct_type_args=struct_type_args,
            struct_arg_groups=struct_arg_groups,
            struct_arg_bounds=struct_arg_bounds,
            struct_arg_defaults=struct_arg_defaults,
            mut_kw_param_lines=mut_kw_param_lines,
            mut_kw_return_line=mut_kw_return_line,
        )

    def parse_asm(self):
        """Parse an inline-assembly expression.

        ``@asm [@clobbers(...)] (in0, in1, ...) [-> type] { "line"... }`` -- the
        optional ``@clobbers`` clause comes right after ``@asm``, the
        parenthesized operands become the inputs (``$0``, ``$1``, ...), the
        optional ``-> type`` is the output (``$out``), and the braced body is
        one bare string literal per instruction.

        Returns:
            The parsed ``Asm`` node.
        """
        line = self.expect("ANNOT").line  # @asm
        clobbers = []
        if self.cur.kind == "ANNOT" and self.cur.text == "@clobbers":
            clobbers = self.parse_clobber_list(self.advance().line)
        self.expect("(")
        inputs = []
        while self.cur.kind != ")":
            if inputs:
                self.expect(",")
            inputs.append(self.parse_expr())
        self.expect(")")
        out_type = self.parse_type_ref() if self.accept("->") else None
        return Asm(self.parse_asm_body(line), inputs, out_type, line, clobbers)

    def parse_clobber_list(self, line: int) -> list[str]:
        """Parse a ``@clobbers("reg", ...)`` list, the ``@clobbers`` already eaten.

        Lists the registers and flags the asm clobbers (e.g. ``"memory"``,
        ``"cc"``, or a register name like ``"x0"``) as string literals.

        Args:
            line: Source line of the ``@clobbers``, for diagnostics.

        Returns:
            The clobber names in order.

        Raises:
            LangError: When the list is empty.
        """
        self.expect("(")
        clobbers = []
        while self.cur.kind != ")":
            if clobbers:
                self.expect(",")
            clobbers.append(self.expect("STRING").text[1:-1])
        self.expect(")")
        if not clobbers:
            raise LangError("@clobbers needs at least one register", line)
        return clobbers

    def parse_asm_body(self, line: int) -> str:
        """Parse an ``@asm`` body: bare string literals joined with newlines.

        Args:
            line: Source line of the ``@asm``, for diagnostics.

        Returns:
            The instruction lines joined with ``\\n``.

        Raises:
            LangError: When the body has no instruction line.
        """
        self.expect("{")
        lines = []
        while self.cur.kind != "}":
            tok = self.expect("STRING")
            lines.append(_unescape(tok.text[1:-1]))
        self.expect("}")
        if not lines:
            raise LangError("an @asm block needs at least one instruction line", line)
        return "\n".join(lines)

    def parse_block(self) -> list:
        """Parse a brace-delimited ``{ ... }`` block of statements.

        Returns:
            The statements in the block.
        """
        self.expect("{")
        statements = []
        while self.cur.kind != "}":
            statements.append(self.parse_statement())
        self.expect("}")
        return statements

    def parse_body(self) -> list:
        """Parse a control-flow body: a braced block or a single statement.

        Returns:
            The body's statements, as a list either way.
        """
        if self.cur.kind == "{":
            return self.parse_block()
        return [self.parse_statement()]

    def parse_statement(self):
        """Parse one statement inside a function body.

        Covers ``@if``, blocks, ``return``, ``emit``, ``let``, ``if``/``else``,
        ``case``, ``while``/``until``, ``break``, ``continue``,
        ``unreachable``, ``defer``, ``for``, and expression statements --
        including assignments, recognized by their target form (a variable,
        ``*ptr``, ``base[i]``, or a member).

        Returns:
            The parsed statement node.

        Raises:
            LangError: On a syntax error or an invalid assignment target.
        """
        tok = self.cur
        if tok.kind == "ANNOT" and tok.text == "@if":
            return self.parse_conditional(self.parse_body)
        if tok.kind == "ANNOT" and tok.text == "@else":
            raise LangError("@else without a matching @if", tok.line)
        if tok.kind == "{":
            return Block(self.parse_block(), tok.line)
        if tok.kind == "return":
            self.advance()
            value = None if self.cur.kind == ";" else self.parse_expr()
            self.reject_bare_except()
            self.expect(";")
            return Return(value, tok.line)
        if tok.kind == "emit":
            self.advance()
            value = self.parse_expr()
            self.expect(";")
            return Emit(value, tok.line)
        if tok.kind == "let":
            self.advance()
            name = self.expect("IDENT").text
            # Destructuring: `let a, b = t;` with an optional trailing-`...`
            # rest binder (`let a, rest... = t;`). `...` marks the last
            # binder only, mirroring the variadic-parameter rule.
            names, rest = [name], False
            if self.cur.kind == "...":
                self.advance()
                rest = True
            while self.accept(","):
                if rest:
                    raise LangError(
                        f"'{names[-1]}...' must be the last binder", tok.line
                    )
                names.append(self.expect("IDENT").text)
                if self.cur.kind == "...":
                    self.advance()
                    rest = True
            if len(names) > 1 or rest:
                if self.cur.kind == ":":
                    raise LangError(
                        "destructuring binders take their types from the "
                        "source; drop the annotation",
                        tok.line,
                    )
                self.expect("=")
                value = self.parse_expr()
                self.reject_bare_except()
                if isinstance(value, Except):
                    raise LangError(
                        "a destructuring let does not take an except "
                        "handler; bind the value alone (let ret = try f() "
                        "except (err) { ... };) or test the error "
                        "(let ret, err = f();)",
                        tok.line,
                    )
                self.expect(";")
                return Let(name, None, value, tok.line, names[1:], rest)
            type_name = self.parse_type_ref() if self.accept(":") else None
            if self.cur.kind == ";":
                if type_name is None:
                    raise LangError(
                        f"an uninitialized variable needs a type: let {name}: int32;",
                        tok.line,
                    )
                self.advance()
                return Let(name, type_name, None, tok.line)
            self.expect("=")
            value = self.parse_expr()
            self.reject_bare_except()
            self.expect(";")
            return Let(name, type_name, value, tok.line)
        if tok.kind == "if":
            self.advance()
            self.expect("(")
            cond = self.parse_expr()
            self.expect(")")
            then = self.parse_body()
            otherwise = []
            # `else:` belongs to an enclosing `case`, never to this `if`.
            if self.cur.kind == "else" and self.tokens[self.pos + 1].kind != ":":
                self.advance()
                otherwise = self.parse_body()
            return If(cond, then, otherwise, tok.line)
        if tok.kind == "case":
            return self.parse_case()
        if tok.kind == "with":
            return self.parse_with()
        if tok.kind == "try":
            # `try ( IDENT =` opens the try statement (the with-head probe
            # -- assignment is not an expression, so the disambiguation is
            # total); anything else after a statement-position `try` is an
            # expression statement (`try f();`, `try (g());`,
            # `try f() ?? v;`, `try f() except (err) { ... };`).
            ahead = self.tokens[self.pos + 1 : self.pos + 4]
            if (
                len(ahead) == 3
                and ahead[0].kind == "("
                and ahead[1].kind == "IDENT"
                and ahead[2].kind == "="
            ):
                return self.parse_try_stmt()
        if tok.kind in ("while", "until"):
            self.advance()
            self.expect("(")
            cond = self.parse_expr()
            self.expect(")")
            return While(cond, self.parse_body(), tok.line, until=tok.kind == "until")
        if tok.kind == "break":
            self.advance()
            self.expect(";")
            return Break(tok.line)
        if tok.kind == "continue":
            self.advance()
            self.expect(";")
            return Continue(tok.line)
        if tok.kind == "unreachable":
            self.advance()
            self.expect(";")
            return Unreachable(tok.line)
        if tok.kind == "defer":
            self.advance()
            # `defer stmt;` or `defer { ... }` -- parse_body handles both.
            return Defer(self.parse_body(), tok.line)
        if tok.kind == "for":
            self.advance()
            var = self.expect("IDENT").text
            self.expect("in")
            # A bare `T { ... }` here would be ambiguous with the loop body, so
            # it is disallowed; parenthesize (`for x in (T { ... })`) to force it.
            with self._struct_literals(False):
                iterable = self.parse_expr()
            return For(var, iterable, self.parse_body(), tok.line)
        expr = self.parse_expr()
        self.reject_bare_except()
        if self.accept("="):
            value = self.parse_expr()
            self.expect(";")
            if isinstance(expr, Var):
                return Assign(expr.name, value, tok.line)
            if isinstance(expr, Unary) and expr.op == "*":
                return StoreDeref(expr.operand, value, tok.line)
            if isinstance(expr, Index):
                return StoreIndex(expr.base, expr.indices, value, tok.line)
            if isinstance(expr, Member):
                return StoreMember(expr.base, expr.field, expr.arrow, value, tok.line)
            if isinstance(expr, (Call, CallExpr)):
                # `f(s, i) = v;` -- assignment through a mut-returning call,
                # named or through a function-pointer expression (a struct
                # field, a parenthesized value). Whether the callee actually
                # returns mut is a codegen check (the callee resolves there).
                return StoreCall(expr, value, tok.line)
            raise LangError("invalid assignment target", tok.line)
        if self.cur.kind in COMPOUND_ASSIGN_OPS:
            op = COMPOUND_ASSIGN_OPS[self.cur.kind]
            self.advance()
            value = self.parse_expr()
            self.expect(";")
            if not is_lvalue(expr):
                raise LangError("invalid assignment target", tok.line)
            return CompoundAssign(expr, op, value, tok.line)
        self.expect(";")
        return ExprStmt(expr, tok.line)

    def parse_case(self):
        """Parse a ``case (subject) { when v: ... else: ... }`` statement.

        A ``when`` arm may list several comma-separated values (``when a, b:``)
        and matches if the subject equals any of them. Each arm runs only its
        own statements -- there is no fall-through -- and the optional ``else:``
        is the default.

        Returns:
            The parsed ``Case`` node.
        """
        line = self.expect("case").line
        # `type` is a contextual keyword: the plain case grammar expects `(`
        # right after `case`, so an identifier here can only start a
        # type-switch (and `type` stays a valid name everywhere else).
        if self.cur.kind == "IDENT" and self.cur.text == "type":
            self.advance()
            return self.parse_case_type(line)
        self.expect("(")
        subject = self.parse_expr()
        self.expect(")")
        self.expect("{")
        arms = []
        while self.cur.kind == "when":
            self.advance()
            values = [self.parse_expr()]
            while self.accept(","):  # `when a, b, c:` matches any of them
                values.append(self.parse_expr())
            self.expect(":")
            body = []
            while self.cur.kind not in ("when", "else", "}"):
                body.append(self.parse_statement())
            arms.append((values, body))
        otherwise = []
        if self.accept("else"):
            self.expect(":")
            while self.cur.kind != "}":
                otherwise.append(self.parse_statement())
        self.expect("}")
        return Case(subject, arms, otherwise, line)

    def parse_case_type(self, line: int):
        """Parse a ``case type (a) { when int32 n: ... else: ... }`` type-switch.

        Each ``when`` arm names one or more comma-separated types over a
        single binding -- the binding holds the recovered value, scoped to
        the arm. A multi-type arm (``when int32, int16 n:``) shares one body:
        the binding is an implicit generic, so codegen compiles the body once
        per listed type with the binding typed as that type. Generic arms
        (``when T* ptr:``, ``when T v:``) need no new syntax here: whether a
        bare arm-type name is concrete or introduces an arm-scoped type
        parameter is decided by name resolution at codegen. The ``else:``
        arm is mandatory: the set of types an ``any`` can hold is open, so a
        type-switch is never exhaustive without it.

        Args:
            line: The line of the ``case`` keyword, already consumed.

        Returns:
            The parsed ``CaseType`` node.

        Raises:
            LangError: When an arm lacks its binding name or the ``else:`` arm
                is missing.
        """
        self.expect("(")
        subject = self.parse_expr()
        self.expect(")")
        self.expect("{")
        arms = []
        while self.cur.kind == "when":
            when_line = self.advance().line
            type_refs = [self.parse_type_ref()]
            while self.accept(","):  # `when int32, int16 n:` -- one binding
                type_refs.append(self.parse_type_ref())
            if self.cur.kind != "IDENT":
                raise LangError(
                    "a case type arm needs a binding name, as in 'when int32 n:'",
                    when_line,
                )
            name = self.advance().text
            self.expect(":")
            body = []
            while self.cur.kind not in ("when", "else", "}"):
                body.append(self.parse_statement())
            arms.append((type_refs, name, body, when_line))
        otherwise = None
        if self.accept("else"):
            self.expect(":")
            otherwise = []
            while self.cur.kind != "}":
                otherwise.append(self.parse_statement())
        self.expect("}")
        if otherwise is None:
            raise LangError(
                "case type needs an else arm; the set of types an any can "
                "hold is open",
                line,
            )
        return CaseType(subject, arms, otherwise, line)

    def parse_with(self):
        """Parse a ``with (t = v as T) body; else other;`` checked-``as`` test.

        Tests an ``any`` subject's boxed tag against ``T`` and, on a match,
        binds ``t`` to the recovered value, scoped to the true branch (the
        ``else`` branch has no binding). Pure sugar over a single-arm
        ``case type``: the pattern follows the same rules as a ``case type``
        arm type -- a resolvable name is a concrete test, an unresolved bare
        name (``T``, ``T*``) a generic pattern monomorphized per boxed tag
        -- but names exactly one type. The head is initializer-style and is
        itself the checked context: inside it ``t = v as T`` is the tag test
        plus bind (deliberately the same spelling as the planned bare unwrap
        ``let t = v as T;``, with ``with``/``else`` supplying the mismatch
        handling), while ``as`` everywhere else keeps its cast semantics.
        The binding is required -- ``with (v as T)`` without ``t =`` does
        not parse -- and the head does not compose with ``and``/``or``.
        Both bodies take a single statement or a braced block, like ``if``;
        the ``else`` is optional -- an unmatched tag (including a zeroed
        ``any``'s tag 0) falls through a lone ``with`` doing nothing.

        Returns:
            The desugared ``CaseType`` node, ``is_with`` set.

        Raises:
            LangError: When the binding is missing or the pattern lists
                more than one type.
        """
        line = self.expect("with").line
        self.expect("(")
        if self.cur.kind != "IDENT" or self.tokens[self.pos + 1].kind != "=":
            raise LangError(
                "a with head binds a name first, as in "
                "'with (n = v as int32)'",
                line,
            )
        name = self.advance().text
        self.advance()  # the '='
        # The subject sits below `as` in the precedence chain: whole-
        # expression parsing would swallow `as T` as a cast of the subject
        # instead of leaving it as the head's pattern.
        subject = self.parse_unary()
        self.expect("as")
        type_ref = self.parse_type_ref()
        if self.cur.kind == ",":
            raise LangError(
                "a with pattern tests exactly one type; dispatch over "
                "several with case type",
                line,
            )
        self.expect(")")
        body = self.parse_body()
        otherwise = []
        # `else:` belongs to an enclosing `case`, never to this `with`.
        if self.cur.kind == "else" and self.tokens[self.pos + 1].kind != ":":
            self.advance()
            otherwise = self.parse_body()
        return CaseType(
            subject, [([type_ref], name, body, line)], otherwise, line,
            is_with=True,
        )

    def parse_except(self, subject):
        """Parse an ``except (err) { H } [else { S }]`` handler clause.

        The clause of the ``try`` expression (see :meth:`parse_unary`):
        ``try g() except (err) { ... }``. ``except`` never appears without
        its ``try`` (:meth:`reject_bare_except` gives the hint at the old
        attachment positions). The binder is parenthesized (statement-head
        house style) and both bodies are braced blocks: the handler is an
        ``emit`` target, so a brace-less statement would leave the
        enclosing statement's own ``;`` ambiguous. The optional ``else`` is
        the ok-arm block; an ``else:`` belongs to an enclosing ``case``,
        never to this clause.

        Args:
            subject: The already-parsed ``try`` operand the clause tests.

        Returns:
            The ``Except`` node.

        Raises:
            LangError: When the handler or else body is not a braced block.
        """
        line = self.expect("except").line
        self.expect("(")
        binder = self.expect("IDENT").text
        self.expect(")")
        if self.cur.kind != "{":
            raise LangError(
                "an except handler is a braced block, as in "
                "'try f() except (err) { ... }'",
                line,
            )
        handler = self.parse_block()
        otherwise = None
        if self.cur.kind == "else" and self.tokens[self.pos + 1].kind != ":":
            else_line = self.advance().line
            if self.cur.kind != "{":
                raise LangError(
                    "an except else is a braced block, as in "
                    "'try f() except (err) { ... } else { ... }'",
                    else_line,
                )
            otherwise = self.parse_block()
        return Except(subject, binder, handler, otherwise, line)

    def parse_try_stmt(self):
        """Parse ``try (ret = f()) { B } except (err) { H }``.

        The statement form of the ``try`` production: a fresh ``ret`` (no
        ``let`` -- the deliberate ``with``-head spelling, see
        :meth:`parse_with`) bound in the parenthesized head and scoped to
        the block ``B``; the required ``except`` handler binds ``err``
        scoped to ``H`` and is obligation-free. There is no ``else`` arm --
        the block already is the no-error arm -- so a trailing ``else``
        names that rule (an ``else:`` belongs to an enclosing ``case``,
        never to this statement). Reached only through the
        ``try ( IDENT =`` probe in :meth:`parse_statement`.

        Returns:
            The ``TryStmt`` node.

        Raises:
            LangError: When a body is not a braced block, the handler is
                missing, or a trailing ``else`` arm appears.
        """
        line = self.expect("try").line
        self.expect("(")
        name = self.expect("IDENT").text
        self.expect("=")
        with self._struct_literals(True):
            value = self.parse_expr()
        self.expect(")")
        if self.cur.kind != "{":
            raise LangError(
                "a try statement's block is braced, as in "
                "'try (ret = f()) { ... } except (err) { ... }'",
                line,
            )
        body = self.parse_block()
        if self.cur.kind != "except":
            raise LangError(
                "a try statement needs its except handler: "
                "try (ret = f()) { ... } except (err) { ... }",
                line,
            )
        except_line = self.advance().line
        self.expect("(")
        binder = self.expect("IDENT").text
        self.expect(")")
        if self.cur.kind != "{":
            raise LangError(
                "an except handler is a braced block, as in "
                "'try (ret = f()) { ... } except (err) { ... }'",
                except_line,
            )
        handler = self.parse_block()
        if self.cur.kind == "else" and self.tokens[self.pos + 1].kind != ":":
            raise LangError(
                "a try statement takes no else arm: the block already is "
                "the no-error arm",
                self.cur.line,
            )
        return TryStmt(name, value, body, binder, handler, line)

    def reject_bare_except(self):
        """Reject an ``except`` trailing an expression without its ``try``.

        The handler is a clause of the ``try`` expression, never a postfix
        attachment; at the statement heads where the un-prefixed spelling
        would otherwise die on the generic "expected ';'" this names the
        fix.

        Raises:
            LangError: When the current token is ``except``.
        """
        if self.cur.kind == "except":
            raise LangError(
                "except needs try: try f() except (err) { ... }",
                self.cur.line,
            )

    # Expressions, by descending precedence level. `??` is loosest of all --
    # looser than the ternary and every binary operator (just above
    # assignment, which is a statement) -- so the coalesce production is the
    # entry point. Below it: the ternary `?:`, then `or`, then `and` (both
    # bind looser than comparisons, so `a > 0 or b < 0` needs no parentheses;
    # they short-circuit, so they are not part of PRECEDENCE).
    def parse_expr(self):
        """Parse a full expression (the lowest-precedence entry point).

        Returns:
            The parsed expression node.
        """
        return self.parse_coalesce()

    def parse_ternary(self):
        """Parse a ``cond ? then : otherwise`` conditional expression.

        The ``?:`` operator binds looser than every other operator (just as in
        C) and is right-associative, so ``a ? b : c ? d : e`` reads as
        ``a ? b : (c ? d : e)``. With no ``?`` it is just the ``or`` expression
        below it.

        Returns:
            A ``Ternary`` node, or the inner expression when no ``?`` appears.
        """
        cond = self.parse_or()
        if self.cur.kind != "?":
            return cond
        line = self.advance().line
        then = self.parse_expr()
        self.expect(":")
        otherwise = self.parse_ternary()  # right-associative
        return Ternary(cond, then, otherwise, line)

    def parse_or(self):
        """Parse a left-associative ``or`` chain (the loosest operator).

        Returns:
            A ``Logical`` node, or the inner expression when no ``or`` appears.
        """
        expr = self.parse_and()
        while self.cur.kind == "or":
            line = self.advance().line
            expr = Logical("or", expr, self.parse_and(), line)
        return expr

    def parse_and(self):
        """Parse a left-associative ``and`` chain.

        Returns:
            A ``Logical`` node, or the inner expression when no ``and`` appears.
        """
        expr = self.parse_binary(0)
        while self.cur.kind == "and":
            line = self.advance().line
            expr = Logical("and", expr, self.parse_binary(0), line)
        return expr

    # Bitwise operators bind tighter than comparisons (unlike C), so
    # `a & b == c` means `(a & b) == c`.
    PRECEDENCE = [
        ["==", "!="],
        ["<", "<=", ">", ">="],
        ["|"],
        ["^"],
        ["&"],
        ["<<", ">>"],
        ["+", "-"],
        ["*", "/", "%"],
    ]

    def parse_binary(self, level: int):
        """Parse a binary-operator expression by precedence climbing.

        Args:
            level: Index into ``PRECEDENCE``; higher levels bind tighter.
                Calling with ``0`` parses a full binary expression.

        Returns:
            The parsed expression node -- a ``Binary`` tree, or a tighter
            expression when no operator at this level appears.
        """
        if level == len(self.PRECEDENCE):
            return self.parse_as()
        lhs = self.parse_binary(level + 1)
        while self.cur.kind in self.PRECEDENCE[level]:
            op = self.advance()
            rhs = self.parse_binary(level + 1)
            lhs = Binary(op.kind, lhs, rhs, op.line)
        return lhs

    def parse_coalesce(self):
        """Parse a right-associative general ``??`` coalesce chain.

        ``??`` binds **looser** than the ternary and every binary operator --
        it is the lowest-precedence expression form (just above assignment),
        so its right-hand side extends greedily to the end of the expression:
        ``p ?? q + 1`` is ``p ?? (q + 1)`` and ``v > p ?? q`` is
        ``(v > p) ?? q`` (the comparison binds first). It chains
        **right**-associatively, so ``p ?? q ?? r`` is ``p ?? (q ?? r)``. To
        operate on the unwrapped value, parenthesize: ``(try f() ?? 0) + 1``.
        A ``??`` directly after a bare ``try`` operand is the try's own
        fallback clause (consumed in :meth:`parse_unary`), whose RHS is this
        same greedy low-precedence expression -- so ``try g() ?? p ?? q`` is
        ``try g() ?? (p ?? q)``, the inner ``p ?? q`` being this general
        production.

        Returns:
            A ``Coalesce`` node, or the inner expression when no ``??``
            appears.
        """
        expr = self.parse_ternary()
        if self.cur.kind == "??":
            line = self.advance().line
            # Right-associative and greedy: the RHS re-enters at this same
            # coalesce level, so a trailing `?? r` nests under this `??`.
            expr = Coalesce(expr, self.parse_coalesce_rhs(), line)
        return expr

    def parse_coalesce_rhs(self):
        """Parse a ``??`` clause's right-hand side.

        A full low-precedence expression (:meth:`parse_coalesce`), greedy to
        the end of the expression -- unless the RHS opens with ``{``, which
        is always the emit-block form ``{ ...; emit v; }`` (never a bare
        struct literal), and which may instead diverge. The fallback runs
        only on the error path, so a leading brace is a block of statements
        first.

        Returns:
            The parsed fallback expression node.
        """
        if self.cur.kind == "{":
            tok = self.advance()
            body = []
            while self.cur.kind != "}":
                body.append(self.parse_statement())
            self.expect("}")
            return BlockExpr(body, tok.line)
        return self.parse_coalesce()

    def parse_as(self):
        """Parse a chain of ``as`` casts.

        ``as`` binds tighter than the binary operators, so ``a + b as int64``
        parses as ``a + (b as int64)``.

        Returns:
            A ``Cast`` node, or the inner expression when no ``as`` appears.
        """
        expr = self.parse_unary()
        while self.cur.kind == "as":
            line = self.advance().line
            expr = Cast(expr, self.parse_type_ref(greedy_stars=False), line)
        return expr

    def parse_unary(self):
        """Parse a prefix unary operator (``-``, ``!``, ``*``, ``&``) or below.

        Also the home of the ``try`` expression: ``try`` binds the call
        chain that follows (a unary expression, per the epic's grammar) and
        takes exactly one of its three endings -- nothing (propagate the
        error up: the enclosing return type must carry the same error
        type), ``?? fallback`` (discard the error and default -- the try's
        own clause, consumed here; its RHS is a greedy low-precedence
        expression, the same one :meth:`parse_coalesce` parses, so a trailing
        ``?? q`` nests inside it), or
        ``except (err) { ... }`` (handle it). The endings do not combine.
        Sitting at unary level, a ``try`` expression is an ordinary
        operand: it composes into larger expressions
        (``1 + try f() except ...``) and the binding forms recognize an
        ``except`` form when it is the *whole* initializer, return value,
        or statement.

        Returns:
            A ``Unary`` node; a ``Try``, ``TryFallback``, or ``Except``
            node for a ``try`` expression; or a postfix expression when no
            prefix operator appears.
        """
        if self.cur.kind == "try":
            line = self.advance().line
            operand = self.parse_unary()
            if self.cur.kind == "??":
                self.advance()
                fallback = self.parse_coalesce_rhs()
                if self.cur.kind == "except":
                    raise LangError(
                        "a try takes one ending -- nothing (propagate), "
                        "'?? fallback' (default), or 'except (err) { ... }' "
                        "(handle) -- not two",
                        self.cur.line,
                    )
                return TryFallback(operand, fallback, line)
            if self.cur.kind == "except":
                return self.parse_except(operand)
            return Try(operand, line)
        if self.cur.kind in ("-", "!", "*", "&", "~"):
            op = self.advance()
            return Unary(op.kind, self.parse_unary(), op.line)
        return self.parse_postfix()

    def parse_postfix(self):
        """Parse postfix operators: indexing, member access, calls, and ``!``.

        Applies ``[i]``, ``.field`` / ``->field``, ``(args)`` (a call through
        a function-pointer expression), and the non-null assertion ``!`` left
        to right onto a primary. Postfix ``!`` never collides with the ``!=``
        comparison: the lexer folds ``!=`` into a single token greedily, so
        ``p != q`` is always a comparison and asserting before comparing
        needs parentheses (``(p!) == q``).

        Returns:
            The parsed expression node.
        """
        expr = self.parse_primary()
        while True:
            if self.cur.kind == "[":
                # `base[i]` indexes; a `:` decision point inside the brackets
                # makes it a sub-slice, `base[start:end]`, either bound
                # optional. A full expression parses first, so a ternary start
                # binds its own `:` greedily -- `s[flag ? 1 : 2 : 3]` is
                # `start = flag ? 1 : 2` with `end = 3`, deterministic. There
                # is no step form: `::` lexes as one token, so `s[::2]` never
                # reads as two slice colons. A `,` decision point instead
                # makes it a multi-index, `base[i, j, ...]` (an @accessor
                # call form); a slice bound never follows a comma.
                line = self.advance().line
                sliced = False
                start = end = None
                indices = []
                with self._struct_literals(True):
                    if self.cur.kind != ":":
                        start = self.parse_expr()
                    if self.cur.kind == ":":
                        sliced = True
                        self.advance()
                        if self.cur.kind != "]":
                            end = self.parse_expr()
                    elif start is not None:
                        indices.append(start)
                        while self.accept(","):
                            indices.append(self.parse_expr())
                self.expect("]")
                if sliced:
                    expr = Slice(expr, start, end, line)
                else:
                    expr = Index(expr, indices, line)
            elif self.cur.kind in (".", "->"):
                arrow = self.advance()
                field = self.expect("IDENT").text
                expr = Member(expr, field, arrow.kind == "->", arrow.line)
            elif self.cur.kind == "(":
                # Calling a function-pointer expression, e.g. table[i](x) or
                # widget->on_click(x). A bare name call is handled in
                # parse_primary, where it can also carry generic type arguments.
                line = self.cur.line
                expr = CallExpr(expr, self.parse_call_args(), line)
            elif self.cur.kind == "!":
                line = self.advance().line
                expr = NonnullAssert(expr, line)
            else:
                return expr

    def parse_call_args(self) -> list:
        """Parse a parenthesized, comma-separated argument list.

        Returns:
            The argument expressions.
        """
        self.expect("(")
        args = []
        with self._struct_literals(True):
            while self.cur.kind != ")":
                if args:
                    self.expect(",")
                args.append(self.parse_expr())
        self.expect(")")
        return args

    def parse_fstring(self, tok: Token):
        """Desugar an interpolated string literal ``f"..."`` at parse time.

        The literal's text is unescaped once -- restoring real quotes for
        string literals nested in holes, so hole text is valid source -- and
        scanned: ``{{`` / ``}}`` spell literal braces, and every other ``{``
        opens an expression hole (braces nested in the hole, including inside
        its string and char literals, are skipped). Each hole is sub-parsed
        by :meth:`parse_fstring_hole`, which reduces it to its runtime
        ``{modifier}`` placeholder. The pieces concatenate into the
        sequential runtime's format text (escapes and inspector labels kept
        ``{{``-escaped), carried with the hole expressions as an
        :class:`FStrLit`. A literal with no holes (only escaped braces or
        plain text, e.g. ``f"{{}}"``) still builds an :class:`FStrLit`, with
        an empty ``holes`` list -- it keeps its f-string identity so the
        ``@format``-only rule governs it exactly as a hole-bearing one, never
        degrading to a plain :class:`StrLit` that a verbatim overload could
        bind.

        Args:
            tok: The FSTRING token.

        Returns:
            The ``FStrLit`` node (with an empty ``holes`` list when the
            literal interpolates nothing).

        Raises:
            LangError: On a stray ``}``, an unclosed ``{``, or a malformed
                hole.
        """
        text = _unescape(tok.text[2:-1])
        pieces: list[str] = []
        holes: list[FStrHole] = []
        i = 0
        while i < len(text):
            c = text[i]
            if c == "{":
                if text.startswith("{", i + 1):
                    pieces.append("{{")
                    i += 2
                    continue
                end = self.fstring_hole_end(text, i + 1, tok.line)
                pieces.append(
                    self.parse_fstring_hole(text[i + 1 : end], holes, tok.line)
                )
                i = end + 1
            elif c == "}":
                if text.startswith("}", i + 1):
                    pieces.append("}}")
                    i += 2
                    continue
                raise LangError(
                    "single '}' in f-string (write }} for a literal '}')",
                    tok.line,
                )
            else:
                pieces.append(c)
                i += 1
        value = "".join(pieces)
        return FStrLit(value, tok.line, holes)

    @staticmethod
    def fstring_hole_end(text: str, start: int, line: int) -> int:
        """Find the index of the ``}`` closing the hole opened before ``start``.

        Counts brace depth so struct literals and block expressions nest, and
        skips string and char literals (with their ``\\x`` pairs), so a brace
        inside quotes -- ``f"{s == \\"}\\"}"`` -- never closes the hole.

        Args:
            text: The f-string's unescaped text.
            start: The index just past the hole's opening ``{``.
            line: Source line for diagnostics.

        Returns:
            The index of the closing ``}``.

        Raises:
            LangError: When the hole never closes.
        """
        depth = 1
        i = start
        while i < len(text):
            c = text[i]
            if c in ('"', "'"):
                i += 1
                while i < len(text) and text[i] != c:
                    i += 2 if text[i] == "\\" else 1
                if i >= len(text):
                    break  # an unterminated literal: the hole never closes
            elif c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    return i
            i += 1
        raise LangError(
            "unclosed '{' in f-string (write {{ for a literal '{')", line
        )

    def parse_fstring_hole(
        self, content: str, holes: list[FStrHole], line: int
    ) -> str:
        """Parse one f-string hole and append its record to ``holes``.

        The hole text is tokenized and sub-parsed as a single expression.
        After it, an optional ``=`` marks the Python-style inspector -- the
        hole's verbatim text (expression spelling, ``=``, and whitespace)
        becomes a label printed ahead of the value -- and an optional ``:``
        starts the runtime modifier, taken as raw text to the hole's end.

        Args:
            content: The hole's text (between the braces, already unescaped).
            holes: The literal's hole list, appended to in place.
            line: The literal's source line, stamped on the hole's tokens so
                sub-parse diagnostics point at the literal.

        Returns:
            The hole's desugared piece of format text: its ``{modifier}``
            placeholder, preceded by the ``{{``-escaped label for an
            inspector hole.

        Raises:
            LangError: On an empty or malformed hole.
        """
        if "\n" in content:
            # The literal's \n escape already unescaped to a real newline the
            # sub-lexer cannot take; a nested literal's escape needs the
            # backslash itself escaped so it survives into the hole text.
            raise LangError(
                "a \\n escape inside an f-string placeholder becomes a real "
                "newline; write \\\\n to put the escape in a nested string "
                "literal",
                line,
            )
        try:
            tokens = tokenize(content)
        except LangError as err:
            raise LangError(err.message, line) from None
        for t in tokens:
            t.line = line
        if tokens[0].kind in ("EOF", "=", ":"):
            raise LangError(
                f"empty expression in f-string placeholder {{{content}}}", line
            )
        sub = Parser(tokens)
        expr = sub.parse_expr()
        label = None
        if sub.cur.kind == "=":
            # The inspector: the label is the hole's own text, verbatim --
            # whitespace and all -- up to the modifier's colon.
            sub.advance()
            label = content
        modifier = ""
        if sub.cur.kind == ":":
            modifier = content[sub.cur.offset + 1 :]
            if label is not None:
                label = content[: sub.cur.offset]
        elif sub.cur.kind != "EOF":
            raise LangError(
                f"unexpected {sub.cur.text!r} in f-string placeholder "
                f"{{{content}}}",
                line,
            )
        holes.append(FStrHole(expr, label, modifier))
        piece = "{" + modifier + "}"
        if label is not None:
            # The label splices into format text, so its own braces (an
            # expression with a struct literal, say) must re-escape.
            piece = label.replace("{", "{{").replace("}", "}}") + piece
        return piece

    def parse_primary(self):
        """Parse a primary expression.

        Covers literals (int, float, bool, ``null``, string, char), a
        parenthesized expression, a block-expression ``{ ...; emit v; }``, an
        array literal, ``sizeof`` / ``len``, and an identifier -- which becomes a
        ``Var`` or, with ``(...)``, a ``Call`` (optionally carrying generic type
        arguments).

        Returns:
            The parsed expression node.

        Raises:
            LangError: On an unexpected token or an out-of-range char literal.
        """
        if self.cur.kind == "ANNOT" and self.cur.text == "@asm":
            return self.parse_asm()
        tok = self.advance()
        if tok.kind == "INT":
            return IntLit(int_value(tok.text), tok.line)
        if tok.kind == "FLOAT":
            return FloatLit(float(tok.text), tok.line)
        if tok.kind in ("true", "false"):
            return BoolLit(tok.kind == "true", tok.line)
        if tok.kind == "null":
            return NullLit(tok.line)
        if tok.kind == "STRING":
            return StrLit(_unescape(tok.text[1:-1]), tok.line)
        if tok.kind == "FSTRING":
            return self.parse_fstring(tok)
        if tok.kind == "CHAR":
            inner = tok.text[1:-1]  # the single character between the quotes
            char = STRING_ESCAPES.get(inner[1], inner[1]) if inner[0] == "\\" else inner
            if ord(char) > 0xFF:
                raise LangError(
                    f"character literal {tok.text} is not a single byte", tok.line
                )
            return CharLit(ord(char), tok.line)
        if tok.kind == "(":
            if self.cur.kind == ")":
                # `()` is the empty tuple literal; there is no expression it
                # could be grouping.
                self.advance()
                return TupleLit([], tok.line)
            with self._struct_literals(True):
                expr = self.parse_expr()
                if self.cur.kind == ",":
                    # A top-level comma makes the parenthesized expression a
                    # tuple literal; `(x)` stays plain grouping (a 1-tuple
                    # needs the trailing comma: `(x,)`). A trailing comma is
                    # allowed, as in array and struct literals.
                    elements = [expr]
                    while self.accept(","):
                        if self.cur.kind == ")":
                            break
                        elements.append(self.parse_expr())
                    self.expect(")")
                    return TupleLit(elements, tok.line)
            self.expect(")")
            return expr
        if tok.kind == "{":
            # A bare, type-inferred struct literal `{ field = expr, ... }`: the
            # type comes from context (a typed let/assignment/return/argument/
            # element/field), the way `[...]` and `"..."` adapt. Told apart from
            # a block-expression by shape (see `_bare_struct_lit_ahead`) and
            # disabled where a `{` would be ambiguous with a loop body (the same
            # `struct_lit_ok` gate the `Name { ... }` form uses).
            if self.struct_lit_ok and self._bare_struct_lit_ahead():
                return self.parse_struct_lit_body(None, tok.line)
            # A block-expression: { stmts; emit value; }. A `{` only reaches
            # here in expression position; in statement position parse_statement
            # claims it first as a block statement.
            body = []
            while self.cur.kind != "}":
                body.append(self.parse_statement())
            self.expect("}")
            return BlockExpr(body, tok.line)
        if tok.kind == "[":
            elements = []
            with self._struct_literals(True):
                while self.cur.kind != "]":
                    elements.append(self.parse_expr())
                    if not self.accept(","):  # a trailing comma is allowed
                        break
            self.expect("]")
            return ArrayLit(elements, tok.line)
        if tok.kind in ("struct", "union"):
            return self.parse_struct_lit(tok.line)
        if tok.kind == "sizeof":
            self.expect("(")
            type_name = self.parse_type_ref()
            self.expect(")")
            return SizeOf(type_name, tok.line)
        if tok.kind == "alignof":
            self.expect("(")
            type_name = self.parse_type_ref()
            self.expect(")")
            return AlignOf(type_name, tok.line)
        if tok.kind == "offsetof":
            self.expect("(")
            type_name = self.parse_type_ref()
            self.expect(",")
            field = self.expect("IDENT").text
            self.expect(")")
            return OffsetOf(type_name, field, tok.line)
        if tok.kind == "typename":
            self.expect("(")
            type_name = self.parse_type_ref()
            self.expect(")")
            return TypeName(type_name, tok.line)
        if tok.kind == "len":
            self.expect("(")
            operand = self.parse_expr()
            self.expect(")")
            return Len(operand, tok.line)
        if tok.kind == "IDENT":
            # `ok(` and `error(` are the builtin result constructors -- claimed
            # by shape (the name directly followed by `(`), like the
            # sizeof-family builtins, so bare `ok`/`error` stay ordinary
            # identifiers everywhere else and no keyword is reserved.
            if tok.text in ("ok", "error") and self.cur.kind == "(":
                self.advance()
                value = None
                if self.cur.kind != ")":
                    with self._struct_literals(True):
                        value = self.parse_expr()
                    if self.cur.kind == ",":
                        raise LangError(
                            f"{tok.text}() takes a single value", tok.line
                        )
                self.expect(")")
                return ResultLit(tok.text, value, tok.line)
            # `move(` is the transfer assertion of an `-> own` return --
            # claimed by the same call shape (it behaves like a builtin
            # fn move<T>(v: T) -> T), so a bare `move` stays an ordinary
            # identifier.
            if tok.text == "move" and self.cur.kind == "(":
                self.advance()
                if self.cur.kind == ")":
                    raise LangError(
                        "move() takes the value being relinquished", tok.line
                    )
                with self._struct_literals(True):
                    value = self.parse_expr()
                if self.cur.kind == ",":
                    raise LangError("move() takes a single value", tok.line)
                self.expect(")")
                return Move(value, tok.line)
            # `error_name(` / `error_message(` are the error accessors, claimed
            # by the same call shape so the names stay ordinary identifiers.
            if (
                tok.text in ("error_name", "error_message")
                and self.cur.kind == "("
            ):
                self.advance()
                operand = self.parse_expr()
                self.expect(")")
                return ErrorName(operand, tok.text == "error_message", tok.line)
            if self.cur.kind == "::":
                self.advance()
                member = self.expect("IDENT").text
                # `Type::method(...)` is a qualified method call, claimed by the
                # trailing `(` just like the `ok(`/`error(` builtins above. Enum
                # and error members fold to integer constants (never callable),
                # so `Enum::Member(...)` never meant anything -- nothing
                # regresses. A `::` member NOT followed by `(` stays an
                # `EnumAccess`.
                if self.cur.kind == "(":
                    return Call(
                        f"{tok.text}::{member}", [], self.parse_call_args(), tok.line
                    )
                return EnumAccess(tok.text, member, tok.line)
            type_args = self.try_type_args() if self.cur.kind == "<" else []
            if type_args and self.cur.kind == "::":
                # `Type<args>::method(...)`: a qualified method call whose
                # qualifier spells the receiver instantiation. The one
                # type-argument list belongs to the STRUCT; a method's own
                # type parameters stay inference-only (as at a dot call), so
                # a second list after the member name is a parse error -- as
                # is anything but a call (enum members fold to integer
                # constants, so a generic-annotated `::` member can only be
                # a method call).
                self.advance()
                member = self.expect("IDENT").text
                if self.cur.kind == "<":
                    raise LangError(
                        f"type arguments after {member!r} are not supported; "
                        "the qualifier's list names the struct instantiation "
                        "and a method's own type parameters are inferred",
                        tok.line,
                    )
                if self.cur.kind != "(":
                    raise LangError(
                        f"expected '(' after '{tok.text}<...>::{member}': a "
                        "qualifier with type arguments forms a method call",
                        tok.line,
                    )
                return Call(
                    f"{tok.text}::{member}",
                    type_args,
                    self.parse_call_args(),
                    tok.line,
                )
            if self.cur.kind == "{" and self.struct_lit_ok:
                return self.parse_struct_lit_fields(tok.text, type_args, tok.line)
            if self.cur.kind != "(":
                return Var(tok.text, tok.line)
            return Call(tok.text, type_args, self.parse_call_args(), tok.line)
        raise LangError(f"unexpected token {tok.text!r}", tok.line)

    def parse_struct_lit(self, line: int) -> StructLit:
        """Parse a struct literal ``struct Name[<args>] { field = expr, ... }``.

        The leading ``struct`` keyword has already been consumed. A trailing
        comma is allowed, and an empty ``{ }`` zero-initializes every field.

        Args:
            line: Source line of the ``struct`` keyword, for diagnostics.

        Returns:
            The parsed ``StructLit``.
        """
        name = self.expect("IDENT").text
        args = []
        if self.accept("<"):
            args.append(self.parse_type_ref())
            while self.accept(","):
                args.append(self.parse_type_ref())
            self.expect_close_angle()
        return self.parse_struct_lit_fields(name, args, line)

    def parse_struct_lit_fields(self, name: str, args: list, line: int) -> StructLit:
        """Parse the ``{ field = expr, ... }`` body of a struct literal.

        Shared by the keyword form ``struct Name { ... }`` and the keyword-free
        ``Name { ... }``; the name and any type arguments are already parsed.
        A trailing comma is allowed, and an empty ``{ }`` zero-initializes.

        Args:
            name: The struct type name.
            args: The parsed type arguments (possibly empty).
            line: Source line for diagnostics.

        Returns:
            The parsed ``StructLit``.
        """
        self.expect("{")
        return self.parse_struct_lit_body(TypeRef(name, args), line)

    def parse_struct_lit_body(self, type_ref, line: int) -> StructLit:
        """Parse ``field = expr, ... }`` with the opening ``{`` already consumed.

        Shared by the named forms and the bare, type-inferred form ``{ field =
        expr, ... }`` (``type_ref`` is ``None``, and the struct type comes from
        context). A trailing comma is allowed, and an empty body zero-initializes.

        Args:
            type_ref: The struct type ``TypeRef``, or ``None`` for a bare literal.
            line: Source line for diagnostics.

        Returns:
            The parsed ``StructLit``.
        """
        fields = []
        # Field values sit inside the literal's braces, so a bare `T { ... }`
        # value is unambiguous again here.
        with self._struct_literals(True):
            while self.cur.kind != "}":
                fname = self.expect("IDENT").text
                self.expect("=")
                fields.append((fname, self.parse_expr()))
                if not self.accept(","):  # a trailing comma is allowed
                    break
        self.expect("}")
        return StructLit(type_ref, fields, line)

    def _bare_struct_lit_ahead(self) -> bool:
        """Whether the just-consumed ``{`` opens a bare struct literal.

        In expression position ``{`` is otherwise a block-expression ``{ stmts;
        emit v; }``. The two are told apart syntactically: a struct literal's
        fields are ``IDENT = expr`` separated by commas, a block's statements by
        semicolons. So a bare literal opens with ``IDENT =`` and reaches its
        first *top-level* separator as a ``,`` or the closing ``}`` -- never a
        ``;`` (which would make it a block whose first statement is an
        assignment ``x = expr;``). An empty ``{}`` stays a block.

        The cursor sits just past the ``{``; this only peeks.

        Returns:
            ``True`` when the following tokens form a bare struct-literal body.
        """
        toks = self.tokens
        i = self.pos
        if toks[i].kind != "IDENT" or toks[i + 1].kind != "=":
            return False
        depth = 0
        while True:
            kind = toks[i].kind
            if kind == "EOF":
                return False
            if kind in ("(", "[", "{"):
                depth += 1
            elif kind in (")", "]"):
                depth -= 1
            elif kind == "}":
                if depth == 0:
                    return True  # closed with no top-level `;`: a struct literal
                depth -= 1
            elif depth == 0:
                if kind == ",":
                    return True
                if kind == ";":
                    return False
            i += 1

    def try_type_args(self) -> list[TypeRef]:
        """Speculatively parse ``<type, ...>`` generic arguments at a call site.

        Only commits when the closing ``>`` is immediately followed by ``(``
        (a call), ``::`` (a qualified method call whose qualifier spells its
        instantiation, ``point<float64>::magnitude(p)``), or a struct
        literal's ``{``; otherwise the ``<`` was a comparison and the cursor
        is restored.

        Returns:
            The parsed type arguments (e.g. for ``sum<int32>(...)``), or an
            empty list when the ``<`` was not a generic-argument list.
        """
        saved = self.pos
        # Log the in-place `>>` splits this speculation performs, so a
        # backtrack can undo them (a committed nested `slice<char>>` mutates
        # the token stream; re-parsing the span as comparisons would
        # otherwise run one `>` short).
        outer_splits = self.angle_splits
        splits = self.angle_splits = []
        args = []
        try:
            self.advance()  # '<'
            while True:
                args.append(self.parse_type_ref())
                # Commit when the `>` closes onto a call `(`, onto a
                # qualified-call `::`, or -- for a keyword-free struct
                # literal `Box<int32> { ... }` -- onto a `{` in a context
                # where such a literal is allowed.
                after = self.tokens[self.pos + 1].kind
                if self.cur.kind == ">" and (
                    after in ("(", "::")
                    or (after == "{" and self.struct_lit_ok)
                ):
                    self.advance()
                    return args
                if not self.accept(","):
                    break
        except LangError:
            pass
        finally:
            self.angle_splits = outer_splits
        for pos, tok in splits:
            self.tokens[pos] = tok
        self.pos = saved
        return []
