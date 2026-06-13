"""Parser: recursive descent over the token stream, producing an AST."""

from __future__ import annotations

import re

from mcc.errors import LangError
from mcc.lexer import Token
from mcc.nodes import (
    Assign, Binary, BoolLit, Break, Call, CallExpr, Case, Cast, CharLit,
    Continue, ExprStmt, FloatLit, Func, GlobalVar, If, Index, IntLit, Let,
    Logical, Member, NullLit, Program, Return, SizeOf, StoreDeref, StoreIndex,
    StoreMember, StrLit, StructDecl, TypeRef, Unary, Var, While,
)

# C's simple escape sequences, plus \e for ESC (a GCC/Clang extension, handy
# for ANSI terminal codes). Any other escape (e.g. \q) keeps the bare
# character. \0 is the NUL byte.
STRING_ESCAPES = {
    "a": "\a", "b": "\b", "e": "\x1b", "f": "\f", "n": "\n", "r": "\r",
    "t": "\t", "v": "\v", "0": "\0", "'": "'", '"': '"', "?": "?", "\\": "\\",
}


def int_value(text: str) -> int:
    """The value of an INT token; a 0x/0X prefix means hexadecimal."""
    return int(text, 16 if text[:2] in ("0x", "0X") else 10)


class Parser:
    def __init__(self, tokens: list[Token]):
        self.tokens = tokens
        self.pos = 0

    @property
    def cur(self) -> Token:
        return self.tokens[self.pos]

    def advance(self) -> Token:
        tok = self.cur
        self.pos += 1
        return tok

    def accept(self, kind: str) -> Token | None:
        if self.cur.kind == kind:
            return self.advance()
        return None

    def expect(self, kind: str) -> Token:
        if self.cur.kind != kind:
            raise LangError(f"expected {kind!r}, got {self.cur.text!r}", self.cur.line)
        return self.advance()

    def parse_program(self) -> Program:
        imports, includes, structs, functions, globals_ = [], [], [], [], []
        while self.cur.kind in ("INCLUDE", "import"):
            if self.cur.kind == "INCLUDE":
                header = re.search(r"<([^>]+)>", self.advance().text).group(1)
                includes.append(header)
            else:
                line = self.advance().line
                path = self.expect("STRING").text[1:-1]
                self.expect(";")
                imports.append((path, line))
        while self.cur.kind != "EOF":
            private = static = extern = packed = volatile = False
            align = None
            while self.cur.kind == "ANNOT":
                annot = self.advance()
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
                elif annot.text == "@align":
                    self.expect("(")
                    align = int_value(self.expect("INT").text)
                    self.expect(")")
                    if align == 0 or align & (align - 1):
                        raise LangError(
                            f"@align needs a power of two, not {align}", annot.line
                        )
                else:
                    raise LangError(f"unknown annotation {annot.text!r}", annot.line)
            if extern and static:
                raise LangError("@extern and @static cannot be combined", self.cur.line)
            if align is not None and self.cur.kind != "struct":
                raise LangError("@align only applies to structs", self.cur.line)
            if packed and self.cur.kind != "struct":
                raise LangError("@packed only applies to structs", self.cur.line)
            if self.cur.kind == "struct":
                if extern:
                    raise LangError("@extern does not apply to structs", self.cur.line)
                structs.append(self.parse_struct(private, static, align, packed, volatile))
            elif self.cur.kind == "let":
                line = self.advance().line
                if not extern and not static:
                    raise LangError("top-level variables must be @extern or @static", line)
                name = self.expect("IDENT").text
                self.expect(":")
                type_name = self.parse_type_ref()
                self.expect(";")
                globals_.append(GlobalVar(name, type_name, line, private=private,
                                          volatile=volatile, static=static))
            else:
                if volatile:
                    raise LangError(
                        "@volatile only applies to structs and extern variables",
                        self.cur.line,
                    )
                functions.append(self.parse_function(private, static, extern))
        return Program(imports, includes, structs, functions, globals_)

    # Tokens that can begin an expression; used to settle the `as T * x`
    # ambiguity (multiplication, not a pointer type).
    EXPR_START = {"INT", "FLOAT", "STRING", "CHAR", "IDENT", "true", "false",
                  "null", "sizeof", "(", "-", "!", "&"}

    def parse_type_ref(self, greedy_stars: bool = True) -> TypeRef:
        """A type: `[struct] name[<type, ...>][*...][[N]...]`, or a
        function-pointer type `fn(type, ...) -> ret`. The `struct` keyword is
        optional (C habit); struct-ness is resolved by name. A trailing `[N]`
        makes a fixed-size array, so `int32[10]` is ten int32s."""
        if self.cur.kind == "fn":
            return self.parse_fn_type(greedy_stars)
        if self.cur.kind == "(":
            # A grouped type, so the pointer binds outside a function type:
            # (fn(int32) -> int32)* is a pointer to a function pointer.
            self.advance()
            inner = self.parse_type_ref()
            self.expect(")")
            extra = self.parse_stars(greedy_stars)
            if extra and inner.dims:
                raise LangError("pointer to an array type is not supported", self.cur.line)
            inner.stars += extra
            return inner
        self.accept("struct")
        name = self.expect("IDENT").text
        args = []
        if self.accept("<"):
            args.append(self.parse_type_ref())
            while self.accept(","):
                args.append(self.parse_type_ref())
            self.expect_close_angle()
        return TypeRef(name, args, self.parse_stars(greedy_stars), dims=self.parse_dims())

    def parse_dims(self) -> list[int]:
        dims = []
        while self.cur.kind == "[":
            line = self.advance().line
            size = int_value(self.expect("INT").text)
            if size < 1:
                raise LangError(f"array size must be at least 1, not {size}", line)
            self.expect("]")
            dims.append(size)
        return dims

    def parse_fn_type(self, greedy_stars: bool) -> TypeRef:
        """A function-pointer type: `fn(A, B) -> R`. A missing `-> R` means
        the function returns void, as in a declaration."""
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
        stars = 0
        while self.cur.kind == "*" and (
            greedy_stars or self.tokens[self.pos + 1].kind not in self.EXPR_START
        ):
            self.advance()
            stars += 1
        return stars

    def expect_close_angle(self):
        """Close a type-argument list. A `>>` token here is two closings of
        nested generics (e.g. array<array<int32>>): split it, consuming the
        first `>` and leaving the second as the current token."""
        if self.cur.kind == ">>":
            self.tokens[self.pos] = Token(">", ">", self.cur.line)
            return
        self.expect(">")

    def parse_type_params(self) -> list[str]:
        type_params = []
        if self.accept("<"):
            type_params.append(self.expect("IDENT").text)
            while self.accept(","):
                type_params.append(self.expect("IDENT").text)
            self.expect(">")
        return type_params

    def parse_struct(self, private: bool = False, static: bool = False,
                     align: int | None = None, packed: bool = False,
                     volatile: bool = False) -> StructDecl:
        line = self.expect("struct").line
        name = self.expect("IDENT").text
        type_params = self.parse_type_params()
        self.expect("{")
        fields = []
        while self.cur.kind != "}":
            fname = self.expect("IDENT").text
            self.expect(":")
            fields.append((fname, self.parse_type_ref()))
            self.expect(";")
        self.expect("}")
        return StructDecl(name, type_params, fields, line, private=private,
                          static=static, align=align, packed=packed, volatile=volatile)

    def parse_function(self, private: bool = False, static: bool = False,
                       extern: bool = False) -> Func:
        line = self.expect("fn").line
        name = self.expect("IDENT").text
        type_params = self.parse_type_params()
        if extern and type_params:
            raise LangError("extern functions cannot be generic", line)
        self.expect("(")
        params = []
        variadic = False
        while self.cur.kind != ")":
            if params:
                self.expect(",")
            if self.cur.kind == "...":
                ellipsis = self.advance()
                if not extern:
                    raise LangError(
                        "'...' is only allowed in extern declarations", ellipsis.line
                    )
                if not params:
                    raise LangError(
                        "'...' needs at least one named parameter before it",
                        ellipsis.line,
                    )
                if self.cur.kind != ")":
                    raise LangError("'...' must be the last parameter", ellipsis.line)
                variadic = True
                break
            pname = self.expect("IDENT").text
            self.expect(":")
            params.append((pname, self.parse_type_ref()))
        self.expect(")")
        ret_type = TypeRef("void")
        if self.accept("->"):
            ret_type = self.parse_type_ref()
        if extern:  # a declaration: signature only, no body
            self.expect(";")
            return Func(name, type_params, params, ret_type, [], line,
                        private=private, extern=True, variadic=variadic)
        return Func(name, type_params, params, ret_type, self.parse_block(), line,
                    private=private, static=static)

    def parse_block(self) -> list:
        self.expect("{")
        statements = []
        while self.cur.kind != "}":
            statements.append(self.parse_statement())
        self.expect("}")
        return statements

    def parse_body(self) -> list:
        """A control-flow body: a braced block, or a single statement."""
        if self.cur.kind == "{":
            return self.parse_block()
        return [self.parse_statement()]

    def parse_statement(self):
        tok = self.cur
        if tok.kind == "return":
            self.advance()
            value = None if self.cur.kind == ";" else self.parse_expr()
            self.expect(";")
            return Return(value, tok.line)
        if tok.kind == "let":
            self.advance()
            name = self.expect("IDENT").text
            type_name = self.parse_type_ref() if self.accept(":") else None
            if self.cur.kind == ";":
                if type_name is None:
                    raise LangError(
                        f"an uninitialized variable needs a type: "
                        f"let {name}: int32;",
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
        """case (subject) { when v: stmts... else: stmts... }

        Each `when` arm runs only its own statements -- there is no
        fall-through -- and the optional `else:` is the default."""
        line = self.expect("case").line
        self.expect("(")
        subject = self.parse_expr()
        self.expect(")")
        self.expect("{")
        arms = []
        while self.cur.kind == "when":
            self.advance()
            value = self.parse_expr()
            self.expect(":")
            body = []
            while self.cur.kind not in ("when", "else", "}"):
                body.append(self.parse_statement())
            arms.append((value, body))
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
        return self.parse_or()

    def parse_or(self):
        expr = self.parse_and()
        while self.cur.kind == "or":
            line = self.advance().line
            expr = Logical("or", expr, self.parse_and(), line)
        return expr

    def parse_and(self):
        expr = self.parse_binary(0)
        while self.cur.kind == "and":
            line = self.advance().line
            expr = Logical("and", expr, self.parse_binary(0), line)
        return expr

    # Bitwise operators bind tighter than comparisons (unlike C), so
    # `a & b == c` means `(a & b) == c`.
    PRECEDENCE = [["==", "!="], ["<", "<=", ">", ">="], ["|"], ["^"], ["&"],
                  ["<<", ">>"], ["+", "-"], ["*", "/", "%"]]

    def parse_binary(self, level: int):
        if level == len(self.PRECEDENCE):
            return self.parse_as()
        lhs = self.parse_binary(level + 1)
        while self.cur.kind in self.PRECEDENCE[level]:
            op = self.advance()
            rhs = self.parse_binary(level + 1)
            lhs = Binary(op.kind, lhs, rhs, op.line)
        return lhs

    def parse_as(self):
        # `as` binds tighter than binary operators: a + b as int64
        # parses as a + (b as int64).
        expr = self.parse_unary()
        while self.cur.kind == "as":
            line = self.advance().line
            expr = Cast(expr, self.parse_type_ref(greedy_stars=False), line)
        return expr

    def parse_unary(self):
        if self.cur.kind in ("-", "!", "*", "&"):
            op = self.advance()
            return Unary(op.kind, self.parse_unary(), op.line)
        return self.parse_postfix()

    def parse_postfix(self):
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
        self.expect("(")
        args = []
        while self.cur.kind != ")":
            if args:
                self.expect(",")
            args.append(self.parse_expr())
        self.expect(")")
        return args

    def parse_primary(self):
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
            text = re.sub(r"\\(.)", lambda m: STRING_ESCAPES.get(m.group(1), m.group(1)), raw)
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
        if tok.kind == "sizeof":
            self.expect("(")
            type_name = self.parse_type_ref()
            self.expect(")")
            return SizeOf(type_name, tok.line)
        if tok.kind == "IDENT":
            type_args = self.try_type_args() if self.cur.kind == "<" else []
            if self.cur.kind != "(":
                return Var(tok.text, tok.line)
            return Call(tok.text, type_args, self.parse_call_args(), tok.line)
        raise LangError(f"unexpected token {tok.text!r}", tok.line)

    def try_type_args(self) -> list[TypeRef]:
        """Speculatively parse `<type, ...>` at a call site, e.g. sum<int32>(...).

        Only commits when the closing `>` is immediately followed by `(`;
        otherwise the `<` was a comparison and the position is restored.
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
