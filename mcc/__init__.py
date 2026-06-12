"""mcc -- a compiler for the .mc language, built on llvmlite.

The compiler is a four-stage pipeline, one module per stage:

    lexer    source text -> tokens
    parser   tokens -> AST (node classes live in `nodes`)
    codegen  AST -> LLVM IR
    driver   IR -> JIT execution or a linked native executable

Run it with `python -m mcc`.
"""

from mcc.driver import compile_to_ir, main

__all__ = ["compile_to_ir", "main"]
