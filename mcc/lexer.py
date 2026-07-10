"""Lexer: turns source text into a flat list of tokens."""

from __future__ import annotations

import re
from dataclasses import dataclass

from mcc.errors import LangError

KEYWORDS = {"fn", "return", "let", "const", "if", "else", "while", "until",
            "break", "continue", "defer", "for", "in", "case", "when", "and",
            "or", "true", "false", "import", "as", "sizeof", "alignof",
            "offsetof", "typename", "len", "struct", "extends", "null", "emit",
            "enum", "union", "mut", "unreachable", "with"}

TOKEN_SPEC = [
    ("COMMENT", r"//[^\n]*|/\*(?s:.*?)\*/"),
    ("WS", r"[ \t\r\n]+"),
    ("ARROW", r"->"),
    ("ELLIPSIS", r"\.\.\."),
    # Compound-assignment operators. Listed before OP2/OP so the longest match
    # wins: `<<=`/`>>=` beat OP2's `<<`/`>>`, and `+=` etc. beat OP's `+`.
    ("OPASSIGN", r"<<=|>>=|[-+*/%&|^]="),
    ("OP2", r"==|!=|<=|>=|<<|>>|::"),
    ("ANNOT", r"@[A-Za-z_]\w*"),
    ("FLOAT", r"\d+\.\d+(?:[eE][+-]?\d+)?|\d+[eE][+-]?\d+"),
    ("INT", r"0[xX][0-9a-fA-F]+|\d+"),
    # An interpolated string literal, f"...". Listed before IDENT so the `f`
    # glued to a quote reads as the prefix; `f` followed by anything else
    # (including whitespace before a string) stays an ordinary identifier.
    ("FSTRING", r'f"(\\.|[^"\\\n])*"'),
    ("IDENT", r"[A-Za-z_]\w*"),
    ("STRING", r'"(\\.|[^"\\\n])*"'),
    ("CHAR", r"'(\\.|[^'\\\n])'"),
    ("OP", r"[{}()<>;:,=+\-*/%!\[\]&.^|~?]"),
]

MASTER_RE = re.compile("|".join(f"(?P<{name}>{pat})" for name, pat in TOKEN_SPEC))


@dataclass
class Token:
    """A single lexical token.

    Attributes:
        kind: The token category -- FLOAT, INT, IDENT, STRING, CHAR, or EOF --
            or, for punctuation and keywords, the literal text itself (e.g.
            "->", "fn", ";").
        text: The exact source text the token was matched from.
        line: The 1-based line number where the token begins.
        offset: The 0-based byte offset where the token begins, used to slice a
            declaration's verbatim source span (see the interface generator).
    """

    kind: str
    text: str
    line: int
    offset: int = 0  # start byte offset in the source, for span slicing


def tokenize(source: str) -> list[Token]:
    """Scan source text into a flat list of tokens.

    Whitespace and comments are discarded. Operator, arrow, ellipsis, and
    keyword tokens are normalized so their ``kind`` is the literal text, while
    INT, FLOAT, IDENT, STRING, and CHAR keep their category. A terminating EOF
    token is always appended.

    Args:
        source: The full source text of one file.

    Returns:
        The tokens in source order, ending with an EOF token.

    Raises:
        LangError: On an unterminated block comment or an unexpected character.
    """
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
            if kind in ("ARROW", "ELLIPSIS", "OP", "OP2", "OPASSIGN") \
                    or (kind == "IDENT" and text in KEYWORDS):
                kind = text
            tokens.append(Token(kind, text, line, match.start()))
        line += text.count("\n")
        pos = match.end()
    tokens.append(Token("EOF", "", line, len(source)))
    return tokens
