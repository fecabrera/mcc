"""Compile-error type shared by every stage of the compiler."""


class LangError(Exception):
    def __init__(self, message: str, line: int):
        super().__init__(f"line {line}: {message}")
        self.line = line
