"""Compile-error type shared by every stage of the compiler."""


class LangError(Exception):
    """A compile error carrying the source line where it occurred.

    Raised by every stage (lexer, parser, codegen, driver) and caught at the
    top level, where it is rendered as ``file: error: line N: message``.

    Attributes:
        line: The 1-based line number the error refers to.
        source: Path of the file the line belongs to, or ``None`` when the
            line comes from a program parsed directly from a string.
    """

    def __init__(self, message: str, line: int, source: str | None = None):
        """Initialize the error.

        Args:
            message: Human-readable description of the problem.
            line: The 1-based line number the error refers to.
            source: Path of the file the line belongs to, when known.
        """
        super().__init__(f"line {line}: {message}")
        self.line = line
        self.source = source  # path of the file the line belongs to, when known
