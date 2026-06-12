"""Parser: recursive descent over the token stream, producing an AST."""

from __future__ import annotations

import re

from mcc.errors import LangError
from mcc.lexer import Token
from mcc.nodes import (
    Assign, Binary, BoolLit, Call, Cast, ExprStmt, FloatLit, Func, If, Index,
    IntLit, Let, Member, NullLit, Program, Return, SizeOf, StoreDeref,
    StoreIndex, StoreMember, StrLit, StructDecl, TypeRef, Unary, Var, While,
)

STRING_ESCAPES = {"n": "\n", "t": "\t", "r": "\r", "0": "\0", '"': '"', "\\": "\\"}


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
        imports, includes, structs, functions = [], [], [], []
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
            if self.cur.kind == "struct":
                structs.append(self.parse_struct())
            else:
                functions.append(self.parse_function())
        return Program(imports, includes, structs, functions)

    # Tokens that can begin an expression; used to settle the `as T * x`
    # ambiguity (multiplication, not a pointer type).
    EXPR_START = {"INT", "FLOAT", "STRING", "IDENT", "true", "false", "null",
                  "sizeof", "(", "-", "!", "&"}

    def parse_type_ref(self, greedy_stars: bool = True) -> TypeRef:
        """A type: `[struct] name[<type, ...>][*...]`. The `struct` keyword
        is optional (C habit); struct-ness is resolved by name."""
        self.accept("struct")
        name = self.expect("IDENT").text
        args = []
        if self.accept("<"):
            args.append(self.parse_type_ref())
            while self.accept(","):
                args.append(self.parse_type_ref())
            self.expect(">")
        stars = 0
        while self.cur.kind == "*" and (
            greedy_stars or self.tokens[self.pos + 1].kind not in self.EXPR_START
        ):
            self.advance()
            stars += 1
        return TypeRef(name, args, stars)

    def parse_type_params(self) -> list[str]:
        type_params = []
        if self.accept("<"):
            type_params.append(self.expect("IDENT").text)
            while self.accept(","):
                type_params.append(self.expect("IDENT").text)
            self.expect(">")
        return type_params

    def parse_struct(self) -> StructDecl:
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
        return StructDecl(name, type_params, fields, line)

    def parse_function(self) -> Func:
        line = self.expect("fn").line
        name = self.expect("IDENT").text
        type_params = self.parse_type_params()
        self.expect("(")
        params = []
        while self.cur.kind != ")":
            if params:
                self.expect(",")
            pname = self.expect("IDENT").text
            self.expect(":")
            params.append((pname, self.parse_type_ref()))
        self.expect(")")
        ret_type = TypeRef("void")
        if self.accept("->"):
            ret_type = self.parse_type_ref()
        return Func(name, type_params, params, ret_type, self.parse_block(), line)

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
            if self.accept("else"):
                otherwise = self.parse_body()
            return If(cond, then, otherwise, tok.line)
        if tok.kind in ("while", "until"):
            self.advance()
            self.expect("(")
            cond = self.parse_expr()
            self.expect(")")
            return While(cond, self.parse_body(), tok.line, until=tok.kind == "until")
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

    # Expressions, by descending precedence level.
    def parse_expr(self):
        return self.parse_binary(0)

    PRECEDENCE = [["==", "!="], ["<", "<=", ">", ">="], ["+", "-"], ["*", "/", "%"]]

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
            else:
                return expr

    def parse_primary(self):
        tok = self.advance()
        if tok.kind == "INT":
            return IntLit(int(tok.text), tok.line)
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
            self.advance()
            args = []
            while self.cur.kind != ")":
                if args:
                    self.expect(",")
                args.append(self.parse_expr())
            self.expect(")")
            return Call(tok.text, type_args, args, tok.line)
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
