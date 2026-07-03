"""Compile-error type shared by every stage of the compiler."""

from dataclasses import dataclass


@dataclass
class Note:
    """A secondary diagnostic line attached to a :class:`LangError`.

    Rendered by the driver as ``file: note: line N: message`` after the
    primary error line -- e.g. the ``in instantiation of ...`` frames that
    trace how the compiler reached an error inside a monomorphized body.

    Attributes:
        message: Human-readable description (e.g. ``in instantiation of
            list<char>``).
        line: The 1-based line number the note refers to.
        source: Path of the file the line belongs to, or ``None`` when the
            line comes from a program parsed directly from a string.
    """

    message: str
    line: int
    source: str | None = None


class LangError(Exception):
    """A compile error carrying the source line where it occurred.

    Raised by every stage (lexer, parser, codegen, driver) and caught at the
    top level, where it is rendered as ``file: error: line N: message``,
    followed by one ``file: note: line N: message`` line per attached note.

    Attributes:
        message: Human-readable description of the problem.
        line: The 1-based line number the error refers to.
        source: Path of the file the line belongs to, or ``None`` when the
            line comes from a program parsed directly from a string.
        notes: Instantiation-backtrace :class:`Note` frames, innermost first,
            appended as the error unwinds out of nested monomorphizations.
            Deliberately *not* part of ``str()``: the primary error text stays
            byte-identical whether or not a note chain is attached.
    """

    def __init__(self, message: str, line: int, source: str | None = None):
        """Initialize the error.

        Args:
            message: Human-readable description of the problem.
            line: The 1-based line number the error refers to.
            source: Path of the file the line belongs to, when known.
        """
        super().__init__(f"line {line}: {message}")
        self.message = message
        self.line = line
        self.source = source  # path of the file the line belongs to, when known
        self.notes: list[Note] = []  # instantiation frames, innermost first
