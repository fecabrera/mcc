"""Lexer: turns source text into a flat list of tokens."""

from __future__ import annotations

import re
from dataclasses import dataclass

from mcc.errors import LangError

KEYWORDS = {"fn", "return", "let", "const", "if", "else", "while", "until",
            "break", "continue", "defer", "for", "in", "case", "when", "and",
            "or", "true", "false", "import", "as", "sizeof", "len", "struct",
            "null"}

TOKEN_SPEC = [
    ("INCLUDE", r"#include\s*<[^>\n]+>"),
    ("COMMENT", r"//[^\n]*|/\*(?s:.*?)\*/"),
    ("WS", r"[ \t\r\n]+"),
    ("ARROW", r"->"),
    ("ELLIPSIS", r"\.\.\."),
    ("OP2", r"==|!=|<=|>=|<<|>>"),
    ("ANNOT", r"@[A-Za-z_]\w*"),
    ("FLOAT", r"\d+\.\d+"),
    ("INT", r"0[xX][0-9a-fA-F]+|\d+"),
    ("IDENT", r"[A-Za-z_]\w*"),
    ("STRING", r'"(\\.|[^"\\\n])*"'),
    ("CHAR", r"'(\\.|[^'\\\n])'"),
    ("OP", r"[{}()<>;:,=+\-*/%!\[\]&.^|]"),
]

MASTER_RE = re.compile("|".join(f"(?P<{name}>{pat})" for name, pat in TOKEN_SPEC))


@dataclass
class Token:
    kind: str  # INCLUDE, FLOAT, INT, IDENT, STRING, EOF, or the literal text
    text: str
    line: int


def tokenize(source: str) -> list[Token]:
    tokens = []
    line = 1
    pos = 0
    while pos < len(source):
        if source.startswith("/*", pos) and "*/" not in source[pos + 2:]:
            raise LangError("unterminated block comment", line)
        match = MASTER_RE.match(source, pos)
        if match is None:
            raise LangError(f"unexpected character {source[pos]!r}", line)
        kind, text = match.lastgroup, match.group()
        if kind not in ("WS", "COMMENT"):
            if kind in ("ARROW", "ELLIPSIS", "OP", "OP2") \
                    or (kind == "IDENT" and text in KEYWORDS):
                kind = text
            tokens.append(Token(kind, text, line))
        line += text.count("\n")
        pos = match.end()
    tokens.append(Token("EOF", "", line))
    return tokens
