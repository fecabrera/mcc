"""Compile-error type shared by every stage of the compiler."""


class LangError(Exception):
    def __init__(self, message: str, line: int, source: str | None = None):
        super().__init__(f"line {line}: {message}")
        self.line = line
        self.source = source  # path of the file the line belongs to, when known
