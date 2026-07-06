"""Compile-error type shared by every stage of the compiler."""

from dataclasses import dataclass

# Every opt-in warning class the compiler can tag a warning with. The driver
# validates each `-W<name>` flag against this set (and expands `-Wall` to all
# of it); producers assert their tag is in it, so a typo fails tests instead
# of silently minting an unenableable class. Two names are reserved by never
# being registered: "error" (`-Werror` is its own flag) and "all" (`-Wall`
# expands here). A class name may not start with "no-", keeping the
# `-Wno-<name>` spelling claimable for per-class disabling later.
WARNING_CLASSES = frozenset({"unchecked-dereference"})


@dataclass
class Note:
    """A secondary diagnostic line attached to a :class:`LangError`.

    Rendered by the driver as ``file: note: line N: message`` after the
    primary error line -- e.g. the ``in instantiation of ...`` frames that
    trace how the compiler reached an error inside a monomorphized body.
    Also the record type of the warning channel
    (:attr:`~mcc.codegen.CodeGen.warnings`), where ``wclass`` may tag the
    entry with its opt-in warning class.

    Attributes:
        message: Human-readable description (e.g. ``in instantiation of
            list<char>``).
        line: The 1-based line number the note refers to.
        source: Path of the file the line belongs to, or ``None`` when the
            line comes from a program parsed directly from a string.
        wclass: The opt-in warning class the entry belongs to (a member of
            :data:`WARNING_CLASSES`), or ``None`` for notes and for
            unconditional warnings, which always print.
    """

    message: str
    line: int
    source: str | None = None
    wclass: str | None = None


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
