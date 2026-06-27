"""Parser: recursive descent over the token stream, producing an AST."""

from __future__ import annotations

import re

from mcc.errors import LangError
from mcc.lexer import Token
from mcc.nodes import (
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
    Cast,
    CharLit,
    Conditional,
    Const,
    Continue,
    Defer,
    Emit,
    EnumAccess,
    EnumDecl,
    ExprStmt,
    Import,
    FloatLit,
    For,
    Func,
    GlobalVar,
    If,
    Index,
    IntLit,
    Len,
    Let,
    Logical,
    Member,
    NullLit,
    Program,
    Return,
    SizeOf,
    StoreDeref,
    StoreIndex,
    StoreMember,
    StrLit,
    StructDecl,
    StructLit,
    Ternary,
    TypeAlias,
    TypeRef,
    Unary,
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


def int_value(text: str) -> int:
    """Parse the integer value of an INT token.

    Args:
        text: The token text, in decimal or with a ``0x``/``0X`` hex prefix.

    Returns:
        The integer value.
    """
    return int(text, 16 if text[:2] in ("0x", "0X") else 10)


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
                Func: functions,
                GlobalVar: globals_,
                Const: consts,
                Conditional: conditionals,
                EnumDecl: enums,
                TypeAlias: aliases,
            }[type(item)]
            target.append(item)
        return Program(
            imports, structs, functions, globals_, consts, conditionals, enums,
            aliases,
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
        ``@packed``, ``@volatile``, ``@inline``, ``@align``, ``@symbol``) are
        collected and validated against the declaration they precede.

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
        if self.cur.kind == "import":
            # Only valid inside an @if branch (parse_toplevel_block); a stray one
            # in the declaration section is rejected by parse_program.
            line = self.advance().line
            path = self.expect("STRING").text[1:-1]
            self.expect(";")
            return Import(path, line)
        private = static = extern = packed = volatile = inline = asm = False
        align = None
        symbol = None
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
            else:
                raise LangError(f"unknown annotation {annot.text!r}", annot.line)
        if extern and static:
            raise LangError("@extern and @static cannot be combined", self.cur.line)
        if symbol is not None and not extern:
            raise LangError(
                "@symbol only applies to @extern functions and variables",
                self.cur.line,
            )
        if align is not None and self.cur.kind != "struct":
            raise LangError("@align only applies to structs", self.cur.line)
        if packed and self.cur.kind != "struct":
            raise LangError("@packed only applies to structs", self.cur.line)
        if inline and (extern or self.cur.kind in ("struct", "let", "const")):
            raise LangError(
                "@inline only applies to functions with a body", self.cur.line
            )
        if asm and (extern or self.cur.kind != "fn"):
            raise LangError("@asm only applies to functions with a body", self.cur.line)
        if clobbers and not asm:
            raise LangError("@clobbers only applies to @asm", self.cur.line)
        if self.cur.kind == "struct":
            if extern:
                raise LangError("@extern does not apply to structs", self.cur.line)
            return self.parse_struct(private, static, align, packed, volatile)
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
            private, static, extern, symbol, inline, asm, clobbers
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
        "len",
        "(",
        "[",
        "-",
        "!",
        "&",
        "~",
    }

    def parse_type_ref(self, greedy_stars: bool = True) -> TypeRef:
        """Parse a type reference.

        Handles ``[struct] name[<type, ...>][*...][[N]...]``, the
        function-pointer form ``fn(type, ...) -> ret``, and parenthesized
        grouping so a ``*`` can bind outside a function type. The ``struct``
        keyword is optional (C habit); struct-ness is resolved later by name. A
        trailing ``[N]`` makes a fixed-size array, so ``int32[10]`` is ten
        int32s.

        Args:
            greedy_stars: When ``True``, take every following ``*`` as pointer
                depth; when ``False``, stop where a ``*`` begins a
                multiplication (used after ``as``).

        Returns:
            The parsed ``TypeRef``.

        Raises:
            LangError: On a pointer to an array type, or other malformed type.
        """
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
        self.accept("struct")
        name = self.expect("IDENT").text
        args = []
        if self.accept("<"):
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
        declaration.

        Args:
            greedy_stars: Passed to :meth:`parse_stars` for any ``*`` that
                follows the type.

        Returns:
            A ``TypeRef`` named ``"fn"`` with its ``params`` and ``ret`` set.
        """
        self.expect("fn")
        self.expect("(")
        params = []
        while self.cur.kind != ")":
            if params:
                self.expect(",")
            params.append(self.parse_type_ref())
        self.expect(")")
        ret = self.parse_type_ref() if self.accept("->") else TypeRef("void")
        return TypeRef("fn", [], self.parse_stars(greedy_stars), params=params, ret=ret)

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
            self.tokens[self.pos] = Token(">", ">", self.cur.line)
            return
        self.expect(">")

    def parse_type_params(self) -> list[str]:
        """Parse an optional generic parameter list ``<A, B, ...>``.

        Returns:
            The type-parameter names, or an empty list when absent.
        """
        type_params = []
        if self.accept("<"):
            type_params.append(self.expect("IDENT").text)
            while self.accept(","):
                type_params.append(self.expect("IDENT").text)
            self.expect(">")
        return type_params

    def parse_struct(
        self,
        private: bool = False,
        static: bool = False,
        align: int | None = None,
        packed: bool = False,
        volatile: bool = False,
    ) -> StructDecl:
        """Parse a ``struct`` declaration with its (optionally generic) fields.

        Args:
            private: Whether ``@private`` was applied.
            static: Whether ``@static`` was applied.
            align: The ``@align(N)`` value, or ``None``.
            packed: Whether ``@packed`` was applied.
            volatile: Whether ``@volatile`` was applied.

        Returns:
            The parsed ``StructDecl``.
        """
        line = self.expect("struct").line
        name = self.expect("IDENT").text
        type_params = self.parse_type_params()
        base = None
        if self.accept("extends"):
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
                if self.accept("="):  # name: type = default;
                    defaults[fname] = self.parse_expr()
                fields.append((fname, ftype))
                self.expect(";")
            self.expect("}")
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
        self.expect("=")
        target = self.parse_type_ref()
        self.expect(";")
        return TypeAlias(name, target, line, private=private, static=static)

    def parse_function(
        self,
        private: bool = False,
        static: bool = False,
        extern: bool = False,
        symbol: str | None = None,
        inline: bool = False,
        asm: bool = False,
        clobbers: list[str] | None = None,
    ) -> Func:
        """Parse a function definition or an ``@extern`` declaration.

        Reads the (optionally generic) signature, an optional trailing ``...``
        for an extern variadic, and then either a body or, for an extern, a
        terminating ``;``.

        Args:
            private: Whether ``@private`` was applied.
            static: Whether ``@static`` was applied.
            extern: Whether ``@extern`` was applied (declaration only).
            symbol: The ``@symbol("...")`` linker name, or ``None``.
            inline: Whether ``@inline`` was applied (``alwaysinline``).
            asm: Whether ``@asm`` was applied (the body is one asm expression).
            clobbers: Registers clobbered by an ``@asm fn`` body, or ``None``.

        Returns:
            The parsed ``Func``.

        Raises:
            LangError: On a generic-extern, generic-variadic, or malformed
                ``...`` parameter.
        """
        line = self.expect("fn").line
        name = self.expect("IDENT").text
        type_params = self.parse_type_params()
        if extern and type_params:
            raise LangError("extern functions cannot be generic", line)
        self.expect("(")
        params = []
        const_params: set[str] = set()
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
            is_const = bool(self.accept("const"))
            pname = self.expect("IDENT").text
            if is_const:
                const_params.add(pname)
            self.expect(":")
            params.append((pname, self.parse_type_ref()))
        self.expect(")")
        ret_type = TypeRef("void")
        if self.accept("->"):
            ret_type = self.parse_type_ref()
        if variadic and type_params:
            raise LangError("a generic function cannot be variadic", line)
        if const_params and extern:
            raise LangError(
                "const parameters are not allowed on @extern functions "
                "(they would change the C calling convention)",
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
            )
        if asm:
            # `@asm fn` is sugar for a function whose body is one @asm(...)
            # expression over its parameters: the params are the inputs, the
            # return type is the output. No `ret` -- the epilogue returns.
            if variadic:
                raise LangError("an @asm function cannot be variadic", line)
            if const_params:
                raise LangError(
                    "const parameters are not allowed on @asm functions", line
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
            raw = tok.text[1:-1]
            lines.append(
                re.sub(
                    r"\\(.)", lambda m: STRING_ESCAPES.get(m.group(1), m.group(1)), raw
                )
            )
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
        ``case``, ``while``/``until``, ``break``, ``continue``, ``defer``,
        ``for``, and expression statements -- including assignments, recognized
        by their target form (a variable, ``*ptr``, ``base[i]``, or a member).

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
        if tok.kind == "defer":
            self.advance()
            # `defer stmt;` or `defer { ... }` -- parse_body handles both.
            return Defer(self.parse_body(), tok.line)
        if tok.kind == "for":
            self.advance()
            var = self.expect("IDENT").text
            self.expect("in")
            iterable = self.parse_expr()
            return For(var, iterable, self.parse_body(), tok.line)
        expr = self.parse_expr()
        if self.accept("="):
            value = self.parse_expr()
            self.expect(";")
            if isinstance(expr, Var):
                return Assign(expr.name, value, tok.line)
            if isinstance(expr, Unary) and expr.op == "*":
                return StoreDeref(expr.operand, value, tok.line)
            if isinstance(expr, Index):
                return StoreIndex(expr.base, expr.index, value, tok.line)
            if isinstance(expr, Member):
                return StoreMember(expr.base, expr.field, expr.arrow, value, tok.line)
            raise LangError("invalid assignment target", tok.line)
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

    # Expressions, by descending precedence level. `or` is loosest, then
    # `and`; both bind looser than comparisons, so `a > 0 or b < 0` needs no
    # parentheses. They short-circuit, so they are not part of PRECEDENCE.
    def parse_expr(self):
        """Parse a full expression (the lowest-precedence entry point).

        Returns:
            The parsed expression node.
        """
        return self.parse_ternary()

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

        Returns:
            A ``Unary`` node, or a postfix expression when no prefix operator
            appears.
        """
        if self.cur.kind in ("-", "!", "*", "&", "~"):
            op = self.advance()
            return Unary(op.kind, self.parse_unary(), op.line)
        return self.parse_postfix()

    def parse_postfix(self):
        """Parse postfix operators: indexing, member access, and calls.

        Applies ``[i]``, ``.field`` / ``->field``, and ``(args)`` (a call
        through a function-pointer expression) left to right onto a primary.

        Returns:
            The parsed expression node.
        """
        expr = self.parse_primary()
        while True:
            if self.cur.kind == "[":
                line = self.advance().line
                index = self.parse_expr()
                self.expect("]")
                expr = Index(expr, index, line)
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
            else:
                return expr

    def parse_call_args(self) -> list:
        """Parse a parenthesized, comma-separated argument list.

        Returns:
            The argument expressions.
        """
        self.expect("(")
        args = []
        while self.cur.kind != ")":
            if args:
                self.expect(",")
            args.append(self.parse_expr())
        self.expect(")")
        return args

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
            raw = tok.text[1:-1]
            text = re.sub(
                r"\\(.)", lambda m: STRING_ESCAPES.get(m.group(1), m.group(1)), raw
            )
            return StrLit(text, tok.line)
        if tok.kind == "CHAR":
            inner = tok.text[1:-1]  # the single character between the quotes
            char = STRING_ESCAPES.get(inner[1], inner[1]) if inner[0] == "\\" else inner
            if ord(char) > 0xFF:
                raise LangError(
                    f"character literal {tok.text} is not a single byte", tok.line
                )
            return CharLit(ord(char), tok.line)
        if tok.kind == "(":
            expr = self.parse_expr()
            self.expect(")")
            return expr
        if tok.kind == "{":
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
            while self.cur.kind != "]":
                elements.append(self.parse_expr())
                if not self.accept(","):  # a trailing comma is allowed
                    break
            self.expect("]")
            return ArrayLit(elements, tok.line)
        if tok.kind == "struct":
            return self.parse_struct_lit(tok.line)
        if tok.kind == "sizeof":
            self.expect("(")
            type_name = self.parse_type_ref()
            self.expect(")")
            return SizeOf(type_name, tok.line)
        if tok.kind == "len":
            self.expect("(")
            operand = self.parse_expr()
            self.expect(")")
            return Len(operand, tok.line)
        if tok.kind == "IDENT":
            if self.cur.kind == "::":
                self.advance()
                member = self.expect("IDENT").text
                return EnumAccess(tok.text, member, tok.line)
            type_args = self.try_type_args() if self.cur.kind == "<" else []
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
        self.expect("{")
        fields = []
        while self.cur.kind != "}":
            fname = self.expect("IDENT").text
            self.expect("=")
            fields.append((fname, self.parse_expr()))
            if not self.accept(","):  # a trailing comma is allowed
                break
        self.expect("}")
        return StructLit(TypeRef(name, args), fields, line)

    def try_type_args(self) -> list[TypeRef]:
        """Speculatively parse ``<type, ...>`` generic arguments at a call site.

        Only commits when the closing ``>`` is immediately followed by ``(``;
        otherwise the ``<`` was a comparison and the cursor is restored.

        Returns:
            The parsed type arguments (e.g. for ``sum<int32>(...)``), or an
            empty list when the ``<`` was not a generic-argument list.
        """
        saved = self.pos
        self.advance()  # '<'
        args = []
        try:
            while True:
                args.append(self.parse_type_ref())
                if self.cur.kind == ">" and self.tokens[self.pos + 1].kind == "(":
                    self.advance()
                    return args
                if not self.accept(","):
                    break
        except LangError:
            pass
        self.pos = saved
        return []
