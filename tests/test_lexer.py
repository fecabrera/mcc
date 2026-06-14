import pytest

from mcc.errors import LangError
from mcc.lexer import tokenize


def kinds(source):
    return [t.kind for t in tokenize(source)]


def test_simple_function():
    assert kinds("fn main() -> int32 { return 0; }") == [
        "fn", "IDENT", "(", ")", "->", "IDENT", "{", "return", "INT", ";", "}", "EOF",
    ]


def test_keyword_prefix_stays_identifier():
    tokens = tokenize("let letter = 1;")
    assert tokens[0].kind == "let"
    assert tokens[1].kind == "IDENT"
    assert tokens[1].text == "letter"


def test_hash_is_rejected():
    # #include was removed; '#' is no longer a valid character.
    with pytest.raises(LangError, match="unexpected character"):
        tokenize("#include <stdio.h>\n")


def test_comments_and_whitespace_skipped():
    assert kinds("// a comment\n  42") == ["INT", "EOF"]


def test_block_comments_skipped():
    assert kinds("/* one */ 1 /* multi\n * line\n * @param x: doc\n */ 2") == [
        "INT", "INT", "EOF",
    ]


def test_block_comment_tracks_line_numbers():
    tokens = tokenize("/**\n * doc\n */\nfn")
    assert tokens[0].kind == "fn" and tokens[0].line == 4


def test_unterminated_block_comment():
    with pytest.raises(LangError, match="unterminated block comment"):
        tokenize("1\n/* oops")


def test_multi_char_operators_win_over_single():
    assert kinds("== != <= >= ->")[:-1] == ["==", "!=", "<=", ">=", "->"]


def test_float_and_int_literals():
    assert kinds("1.5 2")[:-1] == ["FLOAT", "INT"]


def test_float_literals_with_exponents():
    tokens = tokenize("1.5e10 2.0E-3 1e9 1.7976931348623157e+308")
    assert [(t.kind, t.text) for t in tokens[:-1]] == [
        ("FLOAT", "1.5e10"), ("FLOAT", "2.0E-3"),
        ("FLOAT", "1e9"), ("FLOAT", "1.7976931348623157e+308"),
    ]


def test_hex_literal_is_one_token():
    tokens = tokenize("0x1F 0XdeadBEEF")
    assert [(t.kind, t.text) for t in tokens[:-1]] == [
        ("INT", "0x1F"), ("INT", "0XdeadBEEF"),
    ]


def test_ellipsis_is_one_token():
    assert kinds("...")[:-1] == ["..."]


def test_string_literal_with_escape():
    token = tokenize(r'"hello\n"')[0]
    assert token.kind == "STRING"
    assert token.text == r'"hello\n"'


def test_char_literal_is_one_token():
    tokens = tokenize(r"'a' '\n' '\'' '\\'")
    assert [(t.kind, t.text) for t in tokens[:-1]] == [
        ("CHAR", "'a'"), ("CHAR", r"'\n'"), ("CHAR", r"'\''"), ("CHAR", r"'\\'"),
    ]


def test_empty_char_literal_is_an_error():
    with pytest.raises(LangError, match="unexpected character"):
        tokenize("''")


def test_line_numbers():
    tokens = tokenize("1\n2\n\n3")
    assert [t.line for t in tokens[:3]] == [1, 2, 4]


def test_unexpected_character_reports_line():
    with pytest.raises(LangError, match="line 2"):
        tokenize("1\n@")
