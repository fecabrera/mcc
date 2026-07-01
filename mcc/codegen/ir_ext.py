"""Volatile memory instructions rendered with the ``volatile`` IR flag."""

from llvmlite import ir


# llvmlite.ir has no volatile flag on memory instructions, so patch the
# printed form; llvmlite renders modules to IR text before LLVM parses them,
# making the textual form authoritative.
class VolatileLoad(ir.LoadInstr):
    """A load instruction rendered with the ``volatile`` flag.

    llvmlite.ir has no volatile flag on memory instructions, so the printed IR
    text -- which is authoritative, as LLVM parses it -- is patched directly.
    """

    def descr(self, buf):
        """Append this instruction's IR text with ``load`` made volatile.

        Args:
            buf: The output buffer list llvmlite appends rendered text to.
        """
        inner: list[str] = []
        super().descr(inner)
        buf.append("".join(inner).replace("load ", "load volatile ", 1))


class VolatileStore(ir.StoreInstr):
    """A store instruction rendered with the ``volatile`` flag.

    The companion of :class:`VolatileLoad`; see its note on why the printed IR
    is patched.
    """

    def descr(self, buf):
        """Append this instruction's IR text with ``store`` made volatile.

        Args:
            buf: The output buffer list llvmlite appends rendered text to.
        """
        inner: list[str] = []
        super().descr(inner)
        buf.append("".join(inner).replace("store ", "store volatile ", 1))
