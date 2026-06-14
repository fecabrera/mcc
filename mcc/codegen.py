"""Code generation: walks the AST and emits LLVM IR with llvmlite.ir.

LLVM integer types carry no signedness -- `i32` is just 32 bits -- so the
distinction between int32 and uint32 lives here, in which instructions get
emitted (sdiv/udiv, signed/unsigned compares, sext/zext). `LangType` tracks
the source-level type alongside the LLVM type, and every expression evaluates
to a `TypedValue` pairing the LLVM value with its `LangType`.

Structs use LLVM identified types, instantiated (and cached) per set of type
arguments, exactly like generic functions monomorphize.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace as dataclasses_replace

from llvmlite import ir

from mcc.errors import LangError
from mcc.nodes import (
    ArrayLit, Assign, Binary, Block, BoolLit, Break, Call, CallExpr, Case, Cast,
    CharLit, Conditional, Const, Continue, Defer, ExprStmt, FloatLit, For, Func,
    GlobalVar, If, Index, IntLit,
    Let, Logical, Len, Member, NullLit, Program, Return, SizeOf, StoreDeref,
    StoreIndex, StoreMember, StrLit, StructDecl, TypeRef, Unary, Var, While,
)


@dataclass(frozen=True)
class LangType:
    name: str
    ir: ir.Type
    signed: bool = True  # only meaningful for integer types
    pointee: "LangType | None" = None  # set for pointer types
    template: str | None = None  # struct template name, for unification
    args: tuple = ()  # struct type arguments
    # (field name, LangType) pairs; set for struct types. Excluded from
    # eq/hash: struct types are interned per mangled name, and recursive
    # structs would otherwise make comparison loop forever.
    fields: tuple | None = field(default=None, compare=False)
    # @align(N) override, for struct types declared with one.
    align: int | None = field(default=None, compare=False)
    # @packed: fields at unpadded offsets; the struct's alignment is 1.
    packed: bool = field(default=False, compare=False)
    # @volatile: loads and stores must not be elided, merged, or reordered.
    volatile: bool = field(default=False, compare=False)
    # LLVM element index of each field; in explicitly laid out structs
    # (see instantiate_struct) padding elements shift the indices.
    elem_indices: tuple | None = field(default=None, compare=False)
    # (return type, param types, variadic) for a function-pointer type. Part of
    # equality, so two `fn(int32) -> int32` types match structurally.
    signature: tuple | None = None
    # element type and length for a fixed-size array type (int32[10]).
    element: "LangType | None" = None
    count: int | None = None

    def __str__(self) -> str:
        return self.name


@dataclass
class TypedValue:
    value: ir.Value
    type: LangType
    # True for constants that have not been given a definite type yet (bare
    # integer literals, constant arithmetic on them, and null). Adaptable
    # values may still take on a compatible type; everything else keeps its
    # type unless explicitly cast.
    adaptable: bool = False


def pointer_to(lang_type: LangType) -> LangType:
    return LangType(lang_type.name + "*", lang_type.ir.as_pointer(),
                    signed=False, pointee=lang_type)


def function_type(ret: LangType, params: tuple, variadic: bool = False) -> LangType:
    """A function-pointer type, e.g. fn(int32, int32) -> int32. Its LLVM type
    is a pointer to the LLVM function type, so a value is callable directly."""
    fnty = ir.FunctionType(ret.ir, [p.ir for p in params], var_arg=variadic)
    name = "fn(" + ", ".join(p.name for p in params) + ") -> " + ret.name
    return LangType(name, fnty.as_pointer(), signed=False,
                    signature=(ret, tuple(params), variadic))


def array_of(element: LangType, count: int) -> LangType:
    """A fixed-size array type, e.g. int32[10]. In value contexts it decays to
    a pointer to its first element (see CodeGen.value_at)."""
    return LangType(f"{element.name}[{count}]", ir.ArrayType(element.ir, count),
                    signed=False, element=element, count=count)


VOID = LangType("void", ir.VoidType())
BOOL = LangType("bool", ir.IntType(1), signed=False)
FLOAT64 = LangType("float64", ir.DoubleType())

TYPES = {"void": VOID, "bool": BOOL, "float64": FLOAT64}
for _width in (8, 16, 32, 64):
    TYPES[f"int{_width}"] = LangType(f"int{_width}", ir.IntType(_width), signed=True)
    TYPES[f"uint{_width}"] = LangType(f"uint{_width}", ir.IntType(_width), signed=False)

INT32 = TYPES["int32"]
UINT8 = TYPES["uint8"]
UINT64 = TYPES["uint64"]
# uint8* doubles as the "raw memory" pointer (C's void*/char*); string
# literals have this type, and any pointer implicitly coerces to it.
RAWPTR = pointer_to(TYPES["uint8"])
# The type of a bare `null`: a pointer that adapts to any pointer type.
NULLT = LangType("null", RAWPTR.ir, signed=False, pointee=TYPES["uint8"])

POINTER_SIZE = 8  # bytes; native codegen targets 64-bit platforms

# Compile-time facts about the target, exposed to source as the built-in
# integer constants TARGET_OS and TARGET_ARCH (see seed_target_consts). The
# OS_*/ARCH_* names below are also defined as constants, so code can compare
# `TARGET_OS == OS_DARWIN` to select platform-specific bindings. The numeric
# values are an ABI between the compiler and library code -- keep them stable.
TARGET_OS_VALUES = {
    "OS_UNKNOWN": 0,
    "OS_DARWIN": 1,
    "OS_LINUX": 2,
    "OS_WINDOWS": 3,
    "OS_NONE": 4,  # freestanding: bare metal, no operating system
}
TARGET_ARCH_VALUES = {
    "ARCH_UNKNOWN": 0,
    "ARCH_X86_64": 1,
    "ARCH_AARCH64": 2,
    "ARCH_RISCV64": 3,
}


def classify_os(triple: str) -> str:
    """The OS_* name for an LLVM triple's operating-system component. A triple
    with no OS (e.g. aarch64-unknown-none-elf for bare metal) reports OS_NONE."""
    if any(s in triple for s in ("darwin", "macos", "ios", "apple")):
        return "OS_DARWIN"
    if "linux" in triple:
        return "OS_LINUX"
    if any(s in triple for s in ("windows", "win32", "mingw", "msvc")):
        return "OS_WINDOWS"
    if "none" in triple:
        return "OS_NONE"
    return "OS_UNKNOWN"


def classify_arch(triple: str) -> str:
    """The ARCH_* name for an LLVM triple's architecture component."""
    arch = triple.split("-", 1)[0]
    if arch in ("x86_64", "amd64"):
        return "ARCH_X86_64"
    if arch in ("aarch64", "arm64"):
        return "ARCH_AARCH64"
    if arch == "riscv64":
        return "ARCH_RISCV64"
    return "ARCH_UNKNOWN"

I32_ZERO = ir.Constant(ir.IntType(32), 0)


# llvmlite.ir has no volatile flag on memory instructions, so patch the
# printed form; llvmlite renders modules to IR text before LLVM parses them,
# making the textual form authoritative.
class VolatileLoad(ir.LoadInstr):
    def descr(self, buf):
        inner: list[str] = []
        super().descr(inner)
        buf.append("".join(inner).replace("load ", "load volatile ", 1))


class VolatileStore(ir.StoreInstr):
    def descr(self, buf):
        inner: list[str] = []
        super().descr(inner)
        buf.append("".join(inner).replace("store ", "store volatile ", 1))


def is_integer(lang_type: LangType) -> bool:
    """True for the intN/uintN types (not bool, which is i1 underneath)."""
    return (isinstance(lang_type.ir, ir.IntType) and lang_type is not BOOL
            and lang_type.pointee is None)


def is_pointer(lang_type: LangType) -> bool:
    return lang_type.pointee is not None


def is_function(lang_type: LangType) -> bool:
    return lang_type.signature is not None


def is_array(lang_type: LangType) -> bool:
    return lang_type.element is not None


def is_struct(lang_type: LangType) -> bool:
    return lang_type.fields is not None


def is_valist(lang_type: LangType) -> bool:
    """The platform va_list type. Only CodeGen.valist() builds one, and
    lang_type() reserves the name, so the name is an unambiguous marker."""
    return lang_type.name == "va_list"


def _host_triple() -> str:
    """The host target triple, for picking the native va_list layout when no
    --target was given. Imported lazily so codegen has no hard dependency on
    the LLVM binding layer when va_list is unused."""
    import llvmlite.binding as llvm
    return llvm.get_default_triple()


def type_align(lang_type: LangType) -> int:
    if is_pointer(lang_type):
        return POINTER_SIZE
    if is_array(lang_type):
        return type_align(lang_type.element)
    if is_struct(lang_type):
        if lang_type.packed:
            return max(1, lang_type.align or 1)
        natural = max((type_align(ft) for _, ft in lang_type.fields), default=1)
        return max(natural, lang_type.align or 1)
    if isinstance(lang_type.ir, ir.IntType):
        return max(1, lang_type.ir.width // 8)
    return 8  # float64


def over_aligned(lang_type: LangType) -> bool:
    """True for structs whose alignment exceeds what LLVM would compute from
    their IR type alone -- an @align override, here or on a nested field --
    so the layout must be spelled out explicitly (and allocas aligned by
    hand) rather than left to LLVM's natural rules."""
    if not is_struct(lang_type):
        return False
    if lang_type.packed:  # its IR body is packed (alignment 1) already
        return (lang_type.align or 1) > 1
    return (lang_type.align is not None
            or any(over_aligned(ftype) for _, ftype in lang_type.fields))


def type_size(lang_type: LangType) -> int:
    """Size in bytes, as sizeof() reports it (matching LLVM's natural layout
    on 64-bit targets, including struct padding)."""
    if is_pointer(lang_type):
        return POINTER_SIZE
    if is_array(lang_type):
        return lang_type.count * type_size(lang_type.element)
    if is_struct(lang_type):
        offset = 0
        for _, ftype in lang_type.fields:
            if not lang_type.packed:
                align = type_align(ftype)
                offset = (offset + align - 1) // align * align
            offset += type_size(ftype)
        align = type_align(lang_type)
        return (offset + align - 1) // align * align
    if isinstance(lang_type.ir, ir.IntType):
        return max(1, lang_type.ir.width // 8)
    return 8  # float64


# Functions made available by `#include <header>`: name -> (ret, params, variadic)
HEADER_FUNCS = {
    "stdio.h": {
        "printf": (INT32, [RAWPTR], True),
        "puts": (INT32, [RAWPTR], False),
        "putchar": (INT32, [INT32], False),
        "getchar": (INT32, [], False),
    },
    "stdlib.h": {
        "malloc": (RAWPTR, [UINT64], False),
        "free": (VOID, [RAWPTR], False),
        "exit": (VOID, [INT32], False),
        "abs": (INT32, [INT32], False),
    },
    "string.h": {
        "memcpy": (RAWPTR, [RAWPTR, RAWPTR, UINT64], False),
        "memset": (RAWPTR, [RAWPTR, INT32, UINT64], False),
        "strlen": (UINT64, [RAWPTR], False),
    },
    "math.h": {
        "sin": (FLOAT64, [FLOAT64], False),
        "cos": (FLOAT64, [FLOAT64], False),
        "sqrt": (FLOAT64, [FLOAT64], False),
        "pow": (FLOAT64, [FLOAT64, FLOAT64], False),
        "floor": (FLOAT64, [FLOAT64], False),
        "ceil": (FLOAT64, [FLOAT64], False),
        "fabs": (FLOAT64, [FLOAT64], False),
    },
}

COMPARISON_OPS = ("==", "!=", "<", "<=", ">", ">=")


def fold_int_arithmetic(op: str, a: int, b: int, lang_type: LangType) -> int | None:
    """Evaluate a op b at compile time (C semantics: division truncates
    toward zero, >> is arithmetic for signed types), wrapped to the type's
    range. None if it cannot fold."""
    width = lang_type.ir.width
    if op in ("/", "%") and b == 0:
        return None  # leave division by zero to the runtime instruction
    if op in ("<<", ">>") and not 0 <= b < width:
        return None  # out-of-range shifts are poison; leave to the runtime
    if op in ("+", "-", "*"):
        value = {"+": a + b, "-": a - b, "*": a * b}[op]
    elif op in ("/", "%"):
        quotient = abs(a) // abs(b) * (1 if (a >= 0) == (b >= 0) else -1)
        value = quotient if op == "/" else a - b * quotient
    else:
        # Python's >> is arithmetic; stored constants are already in the
        # type's range, so this matches ashr/lshr per signedness.
        value = {"&": a & b, "|": a | b, "^": a ^ b,
                 "<<": a << b, ">>": a >> b}[op]
    if lang_type.signed:
        half = 1 << (width - 1)
        return (value + half) % (1 << width) - half
    return value % (1 << width)


def wrap_int(value: int, lang_type: LangType) -> int:
    """Wrap a Python integer into lang_type's range (two's complement), as a
    narrowing or signedness-changing cast does."""
    width = lang_type.ir.width
    if lang_type.signed:
        half = 1 << (width - 1)
        return (value + half) % (1 << width) - half
    return value % (1 << width)


class CodeGen:
    def __init__(self, program: Program, name: str, root_source: str | None = None,
                 target: str | None = None):
        self.program = program
        # Target triple (None = host); fixes the platform va_list layout.
        self.target = target
        # va_list is platform-specific, so it is built lazily on first use.
        self.va_list_type: "LangType | None" = None
        self.va_list_passed_ir = None  # IR type a va_list takes as an argument
        self.va_list_align = 8
        self.va_list_supported = True  # False on a target with no known layout
        self.va_list_arch = ""
        self.current_variadic = False  # is the function being generated variadic?
        # The entry file's resolved path. Definitions from it are this
        # translation unit's own (external linkage); everything reached through
        # `import` is shared and gets merged across objects (see def_linkage).
        self.root_source = root_source
        # A private context so identified struct types don't collide across
        # separate compilations in one process (llvmlite defaults to a
        # global context).
        self.module = ir.Module(name=name, context=ir.Context())
        self.funcs: dict[str, ir.Function] = {}
        # name -> (return type, param types, variadic)
        self.signatures: dict[str, tuple[LangType, list[LangType], bool]] = {}
        # Generic functions: a name maps to its overload set, distinguished
        # by parameter patterns (e.g. hash<T>(T) vs hash<T>(T*)).
        self.templates: dict[str, list[Func]] = {}
        self.template_bases: dict[int, str] = {}  # id(Func) -> mangle base
        # (id(template Func), bound types) -> mangled instance name
        self.instances: dict[tuple[int, tuple[str, ...]], str] = {}
        self.struct_templates: dict[str, StructDecl] = {}
        self.struct_types: dict[str, LangType] = {}  # mangled name -> instance
        # @static declarations: file-scoped names, keyed by (source, name)
        self.static_funcs: dict[tuple[str | None, str], str] = {}  # -> symbol
        self.static_templates: dict[tuple[str | None, str], Func] = {}
        self.static_structs: dict[tuple[str | None, str], StructDecl] = {}
        self.symbol_bases: dict[tuple[str | None, str], str] = {}  # static name mangling
        self.used_symbols: set[str] = set()
        # @extern declarations refer to symbols defined elsewhere; identical
        # redeclarations across files collapse onto the first one.
        self.extern_decls: set[str] = set()
        self.globals: dict[str, tuple[ir.GlobalVariable, LangType, bool]] = {}
        # @static globals are file-scoped storage, keyed by (source, name) so
        # other files may reuse the name -- like @static functions.
        self.static_globals: dict[tuple[str | None, str],
                                  tuple[ir.GlobalVariable, LangType, bool]] = {}
        # name -> (private, source file); for @private access checks
        self.global_privacy: dict[str, tuple[bool, str | None]] = {}
        # Named compile-time constants: name -> folded TypedValue (no storage is
        # emitted; references are substituted). Used as values and array sizes.
        self.consts: dict[str, TypedValue] = {}
        self.const_privacy: dict[str, tuple[bool, str | None]] = {}
        self.type_bindings: dict[str, LangType] = {}  # active type-parameter bindings
        # name -> (private, source file); for @private access checks
        self.func_privacy: dict[str, tuple[bool, str | None]] = {}
        self.current_source: str | None = None  # file owning the code being generated
        self.builder: ir.IRBuilder | None = None
        self.locals: dict[str, tuple[ir.AllocaInstr, LangType]] = {}
        self.scope_names: set[str] = set()  # names declared in the current block
        # One list of deferred action bodies per active block scope (innermost
        # last); each runs in LIFO order when its block exits.
        self.defer_stack: list[list[list]] = []
        self.ret_type: LangType = VOID
        # Enclosing loops, innermost last: (continue target, break target).
        self.loops: list[tuple[ir.Block, ir.Block]] = []
        self.str_count = 0

    def check_access(self, private: bool, source: str | None, what: str, line: int):
        if private and source != self.current_source:
            owner = source.rsplit("/", 1)[-1] if source else "its file"
            raise LangError(f"{what} is private to {owner}", line)

    def link_shared(self, fn: ir.Function, source: str | None):
        """Give `fn` mergeable linkage if it is shared across objects. The root
        file's own definitions keep the default external linkage (a genuine
        duplicate is a link error); a definition reached through `import`, or a
        monomorphized generic, is copied into every object that uses it, so it
        gets `linkonce_odr` and the identical copies merge at link time instead
        of colliding. With no root_source (single-module JIT or the test
        helpers) there is nothing to link against, so everything stays
        external."""
        if self.root_source is not None and source != self.root_source:
            fn.linkage = "linkonce_odr"

    def static_base(self, name: str, source: str | None) -> str:
        """A unique LLVM-level symbol for a file-scoped name, e.g. f@set."""
        stem = source.rsplit("/", 1)[-1].removesuffix(".mc") if source else "static"
        base = candidate = f"{name}@{stem}"
        counter = 1
        while candidate in self.used_symbols:
            counter += 1
            candidate = f"{base}.{counter}"
        self.used_symbols.add(candidate)
        return candidate

    def valist(self, line: int) -> LangType:
        """The platform's va_list type, built once per target architecture.
        Records both the storage layout (`.ir`, what `let ap: va_list;`
        allocates) and the form it takes when passed to a function
        (`va_list_passed_ir`); these differ on every ABI -- see the table in
        the README's Variadic functions section.

        An architecture without a known layout gets a harmless i8* placeholder
        and `va_list_supported = False`: that lets a binding merely *declare* an
        extern with a va_list parameter (e.g. importing libc/stdio) on any
        target, while actually *using* va_list -- a local, va_start/va_end, or
        passing one -- is rejected by require_valist()."""
        if self.va_list_type is not None:
            return self.va_list_type
        triple = (self.target or _host_triple()).lower()
        self.va_list_arch = triple.split("-")[0]
        apple = any(s in triple for s in ("apple", "darwin", "macos", "ios"))
        i8p = ir.IntType(8).as_pointer()
        ctx = self.module.context
        self.va_list_supported = True
        if self.va_list_arch in ("arm64", "aarch64") and apple:
            storage, passed, align = i8p, i8p, 8           # va_list is char*
        elif self.va_list_arch in ("arm64", "aarch64"):    # AAPCS __va_list
            st = ctx.get_identified_type("struct.__va_list")
            st.set_body(i8p, i8p, i8p, ir.IntType(32), ir.IntType(32))
            storage, passed, align = st, st.as_pointer(), 8
        elif self.va_list_arch in ("x86_64", "amd64"):     # SysV __va_list_tag[1]
            tag = ctx.get_identified_type("struct.__va_list_tag")
            tag.set_body(ir.IntType(32), ir.IntType(32), i8p, i8p)
            storage, passed, align = ir.ArrayType(tag, 1), tag.as_pointer(), 16
        else:                                              # unknown: declare-only
            storage, passed, align = i8p, i8p, 8
            self.va_list_supported = False
        self.va_list_type = LangType("va_list", storage, signed=False)
        self.va_list_passed_ir = passed
        self.va_list_align = align
        return self.va_list_type

    def require_valist(self, line: int):
        """Reject actually using a va_list on a target with no known layout
        (declaring an extern that takes one is still allowed)."""
        self.valist(line)
        if not self.va_list_supported:
            raise LangError(
                f"va_list is not supported for target architecture "
                f"{self.va_list_arch!r}", line
            )

    def seed_target_consts(self):
        """Define the built-in target facts as compile-time constants before any
        user const is folded: TARGET_OS and TARGET_ARCH (the current target's
        values), plus every OS_*/ARCH_* enum name. These reserve their names and
        let library code select platform-specific bindings -- e.g. stdout's
        linker symbol -- at compile time. A bare-metal triple such as
        aarch64-unknown-none-elf reports OS_NONE / ARCH_AARCH64."""
        triple = (self.target or _host_triple()).lower()
        values = {**TARGET_OS_VALUES, **TARGET_ARCH_VALUES}
        values["TARGET_OS"] = TARGET_OS_VALUES[classify_os(triple)]
        values["TARGET_ARCH"] = TARGET_ARCH_VALUES[classify_arch(triple)]
        for name, value in values.items():
            self.consts[name] = TypedValue(
                ir.Constant(INT32.ir, value), INT32, adaptable=True
            )
            self.const_privacy[name] = (False, None)  # public, compiler-owned
        # The same facts as plain ints, for evaluating @if conditions.
        self.target_facts = values

    def eval_static_cond(self, expr) -> bool:
        """Whether a compile-time @if branch is taken. The condition is a
        constant expression over the target facts (TARGET_OS, TARGET_ARCH, and
        the OS_*/ARCH_* names); a nonzero result is true, as in C's #if."""
        return self.eval_static_value(expr) != 0

    def eval_static_value(self, expr) -> int:
        """Evaluate an @if condition to an integer. Only the target facts,
        integer/bool literals, comparisons, logical and/or/not, and integer
        arithmetic are allowed -- nothing that needs the runtime."""
        if isinstance(expr, IntLit) or isinstance(expr, CharLit):
            return expr.value
        if isinstance(expr, BoolLit):
            return int(expr.value)
        if isinstance(expr, Var):
            if expr.name not in self.target_facts:
                raise LangError(
                    f"{expr.name!r} is not allowed in an @if condition; use the "
                    "target facts TARGET_OS, TARGET_ARCH, and the OS_*/ARCH_* "
                    "constants", expr.line
                )
            return self.target_facts[expr.name]
        if isinstance(expr, Unary):
            v = self.eval_static_value(expr.operand)
            if expr.op == "!":
                return int(not v)
            if expr.op == "-":
                return -v
            raise LangError(
                f"operator {expr.op!r} is not allowed in an @if condition", expr.line
            )
        if isinstance(expr, Logical):
            if expr.op == "and":
                return int(bool(self.eval_static_value(expr.lhs))
                           and bool(self.eval_static_value(expr.rhs)))
            return int(bool(self.eval_static_value(expr.lhs))
                       or bool(self.eval_static_value(expr.rhs)))
        if isinstance(expr, Binary):
            a, b = self.eval_static_value(expr.lhs), self.eval_static_value(expr.rhs)
            if expr.op in COMPARISON_OPS:
                return int({"==": a == b, "!=": a != b, "<": a < b, "<=": a <= b,
                            ">": a > b, ">=": a >= b}[expr.op])
            if expr.op in ("/", "%") and b == 0:
                raise LangError("division by zero in an @if condition", expr.line)
            ops = {"+": lambda: a + b, "-": lambda: a - b, "*": lambda: a * b,
                   "/": lambda: int(a / b) if (a < 0) != (b < 0) else a // b,
                   "%": lambda: a - b * (int(a / b) if (a < 0) != (b < 0) else a // b),
                   "&": lambda: a & b, "|": lambda: a | b, "^": lambda: a ^ b,
                   "<<": lambda: a << b, ">>": lambda: a >> b}
            if expr.op in ops:
                return ops[expr.op]()
        raise LangError(
            "an @if condition must be a constant expression over the target facts",
            getattr(expr, "line", 0),
        )

    def flatten_conditionals(self):
        """Resolve top-level @if blocks before anything is emitted: evaluate
        each condition over the target facts and splice the live branch's
        declarations into the program, in source order, dropping the dead
        branch entirely (it is parsed but never type-checked). Branches may
        nest, so newly spliced conditionals are resolved in turn."""
        pending = list(self.program.conditionals)
        while pending:
            cond = pending.pop(0)
            taken = cond.then if self.eval_static_cond(cond.cond) else cond.otherwise
            for item in taken:
                if isinstance(item, Conditional):
                    pending.append(item)
                elif isinstance(item, StructDecl):
                    self.program.structs.append(item)
                elif isinstance(item, GlobalVar):
                    self.program.globals.append(item)
                elif isinstance(item, Const):
                    self.program.consts.append(item)
                else:
                    self.program.functions.append(item)

    def param_irs(self, params) -> list:
        """LLVM types for a function's parameters: a va_list lowers to the form
        it is passed in (a pointer on every ABI), not its storage layout."""
        return [self.va_list_passed_ir if is_valist(p) else p.ir for p in params]

    def lang_type(self, ref: TypeRef, line: int) -> LangType:
        if ref.params is not None:  # a fn(...) -> ret function-pointer type
            ret = self.lang_type(ref.ret, line)
            params = tuple(self.lang_type(p, line) for p in ref.params)
            base = function_type(ret, params)
            for _ in range(ref.stars):
                base = pointer_to(base)
            return self.apply_dims(base, ref.dims, line)
        if ref.name in self.type_bindings and not ref.args:
            base = self.type_bindings[ref.name]
        elif ref.name in TYPES:
            if ref.args:
                raise LangError(f"type {ref.name!r} is not generic", line)
            base = TYPES[ref.name]
        elif ref.name == "va_list":
            if ref.args:
                raise LangError("type 'va_list' is not generic", line)
            base = self.valist(line)
        elif (self.current_source, ref.name) in self.static_structs \
                or ref.name in self.struct_templates:
            decl = self.static_structs.get((self.current_source, ref.name))
            if decl is None:
                decl = self.struct_templates[ref.name]
                self.check_access(decl.private, decl.source, f"struct {ref.name!r}", line)
            if len(ref.args) != len(decl.type_params):
                raise LangError(
                    f"struct {ref.name!r} expects {len(decl.type_params)} "
                    f"type argument(s), got {len(ref.args)}",
                    line,
                )
            args = tuple(self.lang_type(a, line) for a in ref.args)
            base = self.instantiate_struct(decl, args)
        else:
            raise LangError(f"unknown type {ref.name!r}", line)
        if ref.stars and base is VOID:
            raise LangError("no void pointers; use uint8* for raw memory", line)
        for _ in range(ref.stars):
            base = pointer_to(base)
        return self.apply_dims(base, ref.dims, line)

    def apply_dims(self, base: LangType, dims: list, line: int) -> LangType:
        """Wrap `base` in fixed-size array types, innermost dimension last:
        int32[3][4] is [3 x [4 x i32]]."""
        if dims and base is VOID:
            raise LangError("cannot make an array of void", line)
        for size in reversed(dims):
            if size is None:
                raise LangError(
                    "an inferred array size [] is only allowed on an initialized "
                    "variable's outermost dimension", line
                )
            if isinstance(size, str):  # a const name, e.g. int32[N]
                size = self.const_dim(size, line)
            base = array_of(base, size)
        return base

    def const_dim(self, name: str, line: int) -> int:
        """Resolve a const used as an array size to its positive integer value."""
        const = self.consts.get(name)
        if const is None:
            raise LangError(
                f"unknown array size {name!r}; expected an integer constant", line
            )
        self.check_access(*self.const_privacy[name], f"constant {name!r}", line)
        if not is_integer(const.type):
            raise LangError(
                f"array size {name!r} must be an integer constant, not {const.type}", line
            )
        size = const.value.constant
        if size < 1:
            raise LangError(f"array size must be at least 1, not {size}", line)
        return size

    def array_type_for(self, ref: TypeRef, value, line: int) -> LangType:
        """Resolve a declared type, filling an inferred outer `[]` from the
        length of the array-literal initializer. Only the outermost dimension
        may be inferred."""
        if ref.dims and ref.dims[0] is None:
            if any(d is None for d in ref.dims[1:]):
                raise LangError("only the outermost array dimension can be inferred", line)
            if not isinstance(value, ArrayLit):
                raise LangError(
                    "an inferred array size [] needs an array-literal initializer", line
                )
            ref = dataclasses_replace(ref, dims=[len(value.elements), *ref.dims[1:]])
        return self.lang_type(ref, line)

    def instantiate_struct(self, decl: StructDecl, args: tuple[LangType, ...]) -> LangType:
        """Return the struct instance for these type arguments, creating its
        LLVM identified type (and resolving field types) on first use."""
        mangled = self.symbol_bases.get((decl.source, decl.name), decl.name)
        if args:
            mangled += "<" + ", ".join(str(a) for a in args) + ">"
        if mangled in self.struct_types:
            return self.struct_types[mangled]
        identified = self.module.context.get_identified_type(mangled)
        struct_type = LangType(mangled, identified, signed=False,
                               template=decl.name, args=args, align=decl.align,
                               packed=decl.packed, volatile=decl.volatile)
        # Register before resolving fields so self-referential structs
        # (e.g. node<T> holding a node<T>*) can refer to themselves.
        self.struct_types[mangled] = struct_type
        outer = self.type_bindings
        outer_source = self.current_source
        self.type_bindings = dict(zip(decl.type_params, args))
        self.current_source = decl.source  # fields may name private structs
        try:
            fields = tuple(
                (fname, self.lang_type(ftype, decl.line)) for fname, ftype in decl.fields
            )
        finally:
            self.type_bindings = outer
            self.current_source = outer_source
        natural = 1 if decl.packed \
            else max((type_align(ftype) for _, ftype in fields), default=1)
        if decl.align is not None and decl.align < natural:
            # current_source was restored to the instantiating file above,
            # but decl.line belongs to the declaring file.
            raise LangError(
                f"@align({decl.align}) is below struct {decl.name!r}'s "
                f"natural alignment of {natural}",
                decl.line,
                source=decl.source,
            )
        object.__setattr__(struct_type, "fields", fields)  # frozen; fields excluded from eq
        if decl.packed or over_aligned(struct_type):
            # @packed and @align depart from LLVM's natural layout, so spell
            # the layout out: a packed body with explicit padding, keeping
            # field offsets and the LLVM size in agreement with type_size().
            elements, indices, offset = [], [], 0
            for _, ftype in fields:
                pad = 0 if decl.packed else -offset % type_align(ftype)
                if pad:
                    elements.append(ir.ArrayType(ir.IntType(8), pad))
                indices.append(len(elements))
                elements.append(ftype.ir)
                offset += pad + type_size(ftype)
            tail = type_size(struct_type) - offset
            if tail:
                elements.append(ir.ArrayType(ir.IntType(8), tail))
            identified.packed = True
            identified.set_body(*elements)
        else:
            indices = range(len(fields))
            identified.set_body(*(ftype.ir for _, ftype in fields))
        object.__setattr__(struct_type, "elem_indices", tuple(indices))
        return struct_type

    def struct_field(self, owner: LangType, fname: str, line: int) -> tuple[int, LangType]:
        """Field lookup: returns (LLVM element index, field type)."""
        if not is_struct(owner):
            raise LangError(f"{owner} is not a struct", line)
        for index, (name, ftype) in enumerate(owner.fields):
            if name == fname:
                return owner.elem_indices[index], ftype
        raise LangError(f"struct {owner} has no field {fname!r}", line)

    def generate(self) -> ir.Module:
        try:
            return self.gen_program()
        except LangError as err:
            if err.source is None:
                err.source = self.current_source
            raise

    def gen_program(self) -> ir.Module:
        if self.program.imports:
            raise LangError(
                "imports must be resolved before code generation (compile via the driver)",
                self.program.imports[0][1],
            )
        # Resolve compile-time @if first: seed the target facts its conditions
        # read, then splice each live branch's declarations into the program so
        # the loops below see a flat, target-specific set of declarations.
        self.seed_target_consts()
        self.flatten_conditionals()
        for decl in self.program.structs:
            self.current_source = decl.source
            if decl.name in TYPES:
                raise LangError(f"type {decl.name!r} already defined", decl.line)
            if decl.static:
                key = (decl.source, decl.name)
                if key in self.static_structs:
                    raise LangError(f"type {decl.name!r} already defined", decl.line)
                self.static_structs[key] = decl
                self.symbol_bases[key] = self.static_base(decl.name, decl.source)
                continue
            if decl.name in self.struct_templates:
                raise LangError(f"type {decl.name!r} already defined", decl.line)
            self.struct_templates[decl.name] = decl
            self.used_symbols.add(decl.name)
        for header in dict.fromkeys(self.program.includes):
            for name, (ret, params, variadic) in HEADER_FUNCS.get(header, {}).items():
                fnty = ir.FunctionType(ret.ir, [p.ir for p in params], var_arg=variadic)
                self.funcs[name] = ir.Function(self.module, fnty, name=name)
                self.signatures[name] = (ret, params, variadic)
                self.used_symbols.add(name)
                self.extern_decls.add(name)  # header funcs are extern declarations
        # Constants are folded before globals, so a global's type (or a later
        # const) may use one as an array size. They are evaluated in source
        # order, so a const may reference any declared earlier (as in C). The
        # built-in target facts were seeded above, so user consts may use them
        # (and may not shadow them).
        for const in self.program.consts:
            self.current_source = const.source
            if const.name in self.consts:
                raise LangError(f"constant {const.name!r} already defined", const.line)
            value = self.eval_const(const.value, const.line)
            if const.type_name is not None:
                declared = self.lang_type(const.type_name, const.line)
                value = self.const_coerce(value, declared, const.line,
                                          f"const {const.name}")
            self.consts[const.name] = value
            self.const_privacy[const.name] = (const.private, const.source)
        for var in self.program.globals:
            self.current_source = var.source  # the type may name private structs
            # An initializer can supply an inferred outermost [] dimension.
            var_type = self.array_type_for(var.type_name, var.init, var.line)
            if var_type is VOID:
                raise LangError(f"cannot declare a void variable {var.name!r}", var.line)
            if var.static:
                # File-scoped storage with its own definition; the mangled
                # symbol has internal linkage. An initializer must be constant;
                # without one the storage is zero-initialized.
                key = (var.source, var.name)
                if key in self.static_globals:
                    raise LangError(f"variable {var.name!r} already defined", var.line)
                symbol = self.static_base(var.name, var.source)
                glob = ir.GlobalVariable(self.module, var_type.ir, name=symbol)
                glob.linkage = "internal"
                glob.initializer = (self.const_initializer(var.init, var_type, var.line)
                                    if var.init is not None
                                    else ir.Constant(var_type.ir, None))
                self.static_globals[key] = (glob, var_type, var.volatile)
                self.global_privacy[var.name] = (var.private, var.source)
                continue
            if var.name in self.globals:
                if self.globals[var.name][1] != var_type:
                    raise LangError(
                        f"conflicting extern declarations for {var.name!r}", var.line
                    )
                continue
            if var.name in self.funcs:
                raise LangError(f"variable {var.name!r} already defined", var.line)
            # @symbol overrides the linker name; mcc still refers to it by var.name.
            glob = ir.GlobalVariable(self.module, var_type.ir,
                                     name=var.symbol or var.name)
            self.globals[var.name] = (glob, var_type, var.volatile)
            self.global_privacy[var.name] = (var.private, var.source)
            self.used_symbols.add(var.name)
        declared: set[tuple[str | None, str]] = set()
        for func in self.program.functions:
            if func.extern:
                self.current_source = func.source  # signatures may name private structs
                ret = self.lang_type(func.ret_type, func.line)
                params = [self.lang_type(t, func.line) for _, t in func.params]
                if func.name in self.extern_decls:
                    if self.signatures[func.name] != (ret, params, func.variadic):
                        raise LangError(
                            f"conflicting extern declarations for {func.name!r}", func.line
                        )
                    continue
                if func.name in self.funcs or func.name in self.templates \
                        or func.name in self.globals:
                    raise LangError(f"function {func.name!r} already defined", func.line)
                fnty = ir.FunctionType(ret.ir, self.param_irs(params),
                                       var_arg=func.variadic)
                # @symbol overrides the linker name; mcc still calls it by func.name.
                self.funcs[func.name] = ir.Function(
                    self.module, fnty, name=func.symbol or func.name)
                self.signatures[func.name] = (ret, params, func.variadic)
                self.func_privacy[func.name] = (func.private, func.source)
                self.extern_decls.add(func.name)
                self.used_symbols.add(func.name)
                continue
            key = (func.source, func.name)
            is_overloadable = func.type_params and not func.static
            if not is_overloadable:
                if key in declared:
                    raise LangError(f"function {func.name!r} already defined", func.line)
                declared.add(key)
            self.current_source = func.source  # signatures may name private structs
            if func.static:
                self.symbol_bases[key] = self.static_base(func.name, func.source)
                if func.type_params:
                    self.static_templates[key] = func
                    continue
                symbol = self.symbol_bases[key]
                ret = self.lang_type(func.ret_type, func.line)
                params = [self.lang_type(t, func.line) for _, t in func.params]
                fnty = ir.FunctionType(ret.ir, self.param_irs(params),
                                       var_arg=func.variadic)
                fn = ir.Function(self.module, fnty, name=symbol)
                self.link_shared(fn, func.source)
                self.funcs[symbol] = fn
                self.signatures[symbol] = (ret, params, func.variadic)
                self.static_funcs[key] = symbol
                continue
            if func.type_params:
                # Generic: no code yet -- instances are stamped out per call.
                # Several templates may share a name (an overload set).
                if func.name in self.funcs:
                    raise LangError(f"function {func.name!r} already defined", func.line)
                overloads = self.templates.setdefault(func.name, [])
                if overloads:
                    base = f"{func.name}#{len(overloads)}"
                    while base in self.used_symbols:
                        base += "'"
                else:
                    base = func.name
                self.template_bases[id(func)] = base
                self.used_symbols.add(base)
                overloads.append(func)
                continue
            if func.name in self.funcs or func.name in self.templates \
                    or func.name in self.globals:
                raise LangError(f"function {func.name!r} already defined", func.line)
            self.func_privacy[func.name] = (func.private, func.source)
            self.used_symbols.add(func.name)
            ret = self.lang_type(func.ret_type, func.line)
            params = [self.lang_type(t, func.line) for _, t in func.params]
            fnty = ir.FunctionType(ret.ir, self.param_irs(params),
                                   var_arg=func.variadic)
            fn = ir.Function(self.module, fnty, name=func.name)
            self.link_shared(fn, func.source)
            self.funcs[func.name] = fn
            self.signatures[func.name] = (ret, params, func.variadic)
        for func in self.program.functions:
            if not func.type_params and not func.extern:
                symbol = self.static_funcs.get((func.source, func.name), func.name)
                ret, params, _ = self.signatures[symbol]
                self.gen_function(func, self.funcs[symbol], ret, params)
        return self.module

    def gen_function(self, func: Func, fn: ir.Function, ret: LangType, params: list[LangType]):
        self.ret_type = ret
        self.current_source = func.source
        self.current_variadic = func.variadic  # gates va_start
        self.builder = ir.IRBuilder(fn.append_basic_block("entry"))
        self.locals = {}
        self.scope_names = set()  # the body block resets this, but be explicit
        self.defer_stack = []
        self.loops = []  # break/continue cannot escape into a caller's loop
        for (pname, _), ptype, arg in zip(func.params, params, fn.args):
            arg.name = pname
            slot = self.builder.alloca(arg.type, name=pname)
            if over_aligned(ptype):
                slot.align = type_align(ptype)
            self.builder.store(arg, slot)
            self.locals[pname] = (slot, ptype)
        self.gen_block(func.body)
        if not self.builder.block.is_terminated:
            if ret is VOID:
                self.builder.ret_void()
            elif func.name == "main":
                self.builder.ret(ir.Constant(ret.ir, 0))
            else:
                raise LangError(f"function {func.name!r} may end without a return", func.line)

    def gen_block(self, statements: list):
        # Each block is its own scope: outer names stay visible, names declared
        # here vanish at the end, and an inner declaration may shadow an outer
        # one (the outer binding is restored on exit). Allocas live for the
        # whole function regardless, so this only governs name visibility.
        outer_locals, outer_names = dict(self.locals), self.scope_names
        self.scope_names = set()
        self.defer_stack.append([])
        try:
            for stmt in statements:
                if self.builder.block.is_terminated:
                    break  # unreachable code after return/break/continue
                self.gen_statement(stmt)
            # Reached the end without diverging: run this block's defers (LIFO).
            # An early return/break/continue already ran them on its own path.
            if not self.builder.block.is_terminated:
                self.run_deferred_scope(self.defer_stack[-1])
        finally:
            self.defer_stack.pop()
            self.locals, self.scope_names = outer_locals, outer_names

    def run_deferred_scope(self, scope: list):
        """Emit one block's deferred actions, last-registered first. Each body
        runs while the block's locals are still in scope, so it can refer to
        them."""
        for body in reversed(scope):
            if self.builder.block.is_terminated:
                break
            self.gen_block(body)

    def run_defers_through(self, depth: int):
        """Unwind the deferred scopes from the innermost down to `depth`,
        emitting each in LIFO order -- used when control jumps out of several
        blocks at once (a return unwinds all; break/continue to the loop body).
        The stack is snapshotted first, since emitting a body pushes scopes."""
        for scope in reversed([list(s) for s in self.defer_stack[depth:]]):
            if self.builder.block.is_terminated:
                break
            self.run_deferred_scope(scope)

    def bind_local(self, name: str, slot, lang_type: LangType):
        """Record a local in the current scope. The name shadows any outer one
        until the enclosing block ends; redeclaring it in the same block is an
        error (checked by the caller against scope_names)."""
        self.locals[name] = (slot, lang_type)
        self.scope_names.add(name)

    def coerce(self, tv: TypedValue, expected: LangType, line: int, context: str) -> TypedValue:
        """Check that `tv` has type `expected`, adapting untyped constants.

        An adaptable integer constant may take on any integer type its value
        fits into (so `let x: uint64 = 5;` works), and `null` adapts to any
        pointer or function-pointer type. Any pointer coerces to uint8* (raw
        memory, like C's void*). Other values never convert implicitly -- use
        `as`.
        """
        if tv.type == expected:
            return tv
        if tv.type is NULLT and (is_pointer(expected) or is_function(expected)):
            return TypedValue(ir.Constant(expected.ir, None), expected)
        if expected == RAWPTR and is_pointer(tv.type):
            return TypedValue(self.builder.bitcast(tv.value, RAWPTR.ir), RAWPTR)
        if (
            tv.adaptable
            and isinstance(tv.value, ir.Constant)
            and is_integer(tv.type)
            and is_integer(expected)
            and isinstance(tv.value.constant, int)
        ):
            width = expected.ir.width
            if expected.signed:
                lo, hi = -(1 << (width - 1)), 1 << (width - 1)
            else:
                lo, hi = 0, 1 << width
            if lo <= tv.value.constant < hi:
                return TypedValue(ir.Constant(expected.ir, tv.value.constant), expected)
            raise LangError(
                f"constant {tv.value.constant} is out of range for {expected}", line
            )
        raise LangError(f"{context}: expected {expected}, got {tv.type}", line)

    def gen_load(self, addr, *, align: int | None = None,
                 volatile: bool = False, name: str = "") -> ir.Instruction:
        if not volatile:
            return self.builder.load(addr, name=name, align=align)
        instr = VolatileLoad(self.builder.block, addr, name=name)
        instr.align = align
        self.builder._insert(instr)
        return instr

    def gen_store(self, value, addr, *, align: int | None = None,
                  volatile: bool = False):
        if not volatile:
            self.builder.store(value, addr, align=align)
            return
        instr = VolatileStore(self.builder.block, value, addr)
        instr.align = align
        self.builder._insert(instr)

    def gen_statement(self, stmt):
        if isinstance(stmt, Return):
            if stmt.value is None:
                if self.ret_type is not VOID:
                    raise LangError(f"return needs a {self.ret_type} value", stmt.line)
                self.run_defers_through(0)  # all enclosing blocks
                if not self.builder.block.is_terminated:
                    self.builder.ret_void()
            else:
                # Evaluate the result before the defers run, so a defer that
                # frees a buffer cannot clobber what is being returned.
                tv = self.coerce(self.gen_expr(stmt.value), self.ret_type, stmt.line, "return value")
                self.run_defers_through(0)
                if not self.builder.block.is_terminated:
                    self.builder.ret(tv.value)
        elif isinstance(stmt, Let):
            if stmt.name in self.scope_names:
                raise LangError(
                    f"variable {stmt.name!r} already declared in this scope", stmt.line
                )
            if stmt.value is None:  # let x: T; -- uninitialized, like a C local
                declared = self.lang_type(stmt.type_name, stmt.line)
                if declared is VOID:
                    raise LangError("cannot declare a void variable", stmt.line)
                slot = self.builder.alloca(declared.ir, name=stmt.name)
                if over_aligned(declared):
                    slot.align = type_align(declared)
                elif is_valist(declared):
                    self.require_valist(stmt.line)
                    slot.align = self.va_list_align  # ABI alignment for va_start
                self.bind_local(stmt.name, slot, declared)
                return
            if isinstance(stmt.value, ArrayLit):  # let xs: T[N] = [...]
                if stmt.type_name is None:
                    raise LangError(
                        "an array literal needs a type annotation, "
                        "e.g. let xs: int32[3] = [...]", stmt.line
                    )
                declared = self.array_type_for(stmt.type_name, stmt.value, stmt.line)
                if not is_array(declared):
                    raise LangError(f"an array literal cannot initialize a {declared}", stmt.line)
                slot = self.builder.alloca(declared.ir, name=stmt.name)
                self.store_array_literal(slot, stmt.value, declared, stmt.line)
                self.bind_local(stmt.name, slot, declared)
                return
            tv = self.gen_expr(stmt.value)
            if stmt.type_name is not None:
                declared = self.lang_type(stmt.type_name, stmt.line)
                if declared is VOID:
                    raise LangError("cannot declare a void variable", stmt.line)
                if is_array(declared):
                    raise LangError(
                        f"an array variable is initialized from an array literal, "
                        f"not a {tv.type}", stmt.line
                    )
                tv = self.coerce(tv, declared, stmt.line, f"let {stmt.name}")
            elif tv.adaptable:
                raise LangError(
                    f"type of {stmt.name!r} is ambiguous: the value is an untyped "
                    f"constant; annotate the variable "
                    f"(let {stmt.name}: int32 = ...) or cast the value (... as int32)",
                    stmt.line,
                )
            elif tv.type is VOID:
                raise LangError(f"cannot assign a void value to {stmt.name!r}", stmt.line)
            slot = self.builder.alloca(tv.type.ir, name=stmt.name)
            if over_aligned(tv.type):
                slot.align = type_align(tv.type)
            self.builder.store(tv.value, slot)
            self.bind_local(stmt.name, slot, tv.type)
        elif isinstance(stmt, Assign):
            slot, var_type, volatile = self.var_addr(stmt.name, stmt.line)
            tv = self.coerce(self.gen_expr(stmt.value), var_type, stmt.line, f"assignment to {stmt.name}")
            self.gen_store(tv.value, slot, volatile=volatile)
        elif isinstance(stmt, If):
            cond = self.gen_cond(stmt.cond)
            if stmt.otherwise:
                with self.builder.if_else(cond) as (then, otherwise):
                    with then:
                        self.gen_block(stmt.then)
                    with otherwise:
                        self.gen_block(stmt.otherwise)
            else:
                with self.builder.if_then(cond):
                    self.gen_block(stmt.then)
        elif isinstance(stmt, While):
            kind = "until" if stmt.until else "while"
            cond_bb = self.builder.append_basic_block(f"{kind}.cond")
            body_bb = self.builder.append_basic_block(f"{kind}.body")
            end_bb = self.builder.append_basic_block(f"{kind}.end")
            self.builder.branch(cond_bb)
            self.builder.position_at_end(cond_bb)
            cond = self.gen_cond(stmt.cond)
            if stmt.until:
                self.builder.cbranch(cond, end_bb, body_bb)
            else:
                self.builder.cbranch(cond, body_bb, end_bb)
            self.builder.position_at_end(body_bb)
            # Record the defer depth so break/continue unwind the body's defers.
            self.loops.append((cond_bb, end_bb, len(self.defer_stack)))
            try:
                self.gen_block(stmt.body)
            finally:
                self.loops.pop()
            if not self.builder.block.is_terminated:
                self.builder.branch(cond_bb)
            self.builder.position_at_end(end_bb)
        elif isinstance(stmt, Conditional):
            # Compile-time @if: emit only the live branch's statements, inline
            # in the current scope. The dead branch is never type-checked.
            taken = stmt.then if self.eval_static_cond(stmt.cond) else stmt.otherwise
            for inner in taken:
                if self.builder.block.is_terminated:
                    break
                self.gen_statement(inner)
        elif isinstance(stmt, Block):
            self.gen_block(stmt.body)  # a bare { } -- its own scope
        elif isinstance(stmt, For):
            self.gen_for(stmt)
        elif isinstance(stmt, Defer):
            # Register only; the body runs when the enclosing block exits.
            self.defer_stack[-1].append(stmt.body)
        elif isinstance(stmt, Break):
            if not self.loops:
                raise LangError("'break' outside a loop", stmt.line)
            self.run_defers_through(self.loops[-1][2])  # the loop body and inner
            if not self.builder.block.is_terminated:
                self.builder.branch(self.loops[-1][1])
        elif isinstance(stmt, Continue):
            if not self.loops:
                raise LangError("'continue' outside a loop", stmt.line)
            self.run_defers_through(self.loops[-1][2])
            if not self.builder.block.is_terminated:
                self.builder.branch(self.loops[-1][0])
        elif isinstance(stmt, Case):
            subject = self.gen_expr(stmt.subject)
            if is_struct(subject.type) or subject.type is VOID:
                raise LangError(f"cannot match a {subject.type} in a case", stmt.line)
            end_bb = self.builder.append_basic_block("case.end")
            for value_expr, body in stmt.arms:
                value = self.gen_expr(value_expr)
                cond = self.gen_equals(subject, value, value_expr.line)
                arm_bb = self.builder.append_basic_block("case.arm")
                next_bb = self.builder.append_basic_block("case.next")
                self.builder.cbranch(cond, arm_bb, next_bb)
                self.builder.position_at_end(arm_bb)
                self.gen_block(body)  # no fall-through: each arm exits to the end
                if not self.builder.block.is_terminated:
                    self.builder.branch(end_bb)
                self.builder.position_at_end(next_bb)
            self.gen_block(stmt.otherwise)  # the else arm, or empty
            if not self.builder.block.is_terminated:
                self.builder.branch(end_bb)
            self.builder.position_at_end(end_bb)
        elif isinstance(stmt, StoreDeref):
            ptr = self.gen_expr(stmt.ptr)
            if not is_pointer(ptr.type):
                raise LangError(f"cannot dereference a {ptr.type}", stmt.line)
            value = self.coerce(self.gen_expr(stmt.value), ptr.type.pointee,
                                stmt.line, "assignment through pointer")
            self.gen_store(value.value, ptr.value, volatile=ptr.type.pointee.volatile)
        elif isinstance(stmt, StoreIndex):
            addr, element = self.gen_index_addr(stmt.base, stmt.index, stmt.line)
            value = self.coerce(self.gen_expr(stmt.value), element,
                                stmt.line, "assignment to element")
            self.gen_store(value.value, addr, volatile=element.volatile)
        elif isinstance(stmt, StoreMember):
            addr, ftype, align, volatile = self.gen_member_addr(
                stmt.base, stmt.field, stmt.arrow, stmt.line
            )
            value = self.coerce(self.gen_expr(stmt.value), ftype,
                                stmt.line, f"assignment to field {stmt.field!r}")
            self.gen_store(value.value, addr, align=align, volatile=volatile)
        elif isinstance(stmt, ExprStmt):
            self.gen_expr(stmt.expr)
        else:
            raise LangError(f"cannot compile statement {stmt!r}", stmt.line)

    def gen_for(self, stmt: For):
        """Lower `for x in obj { body }` to the iter/next protocol:

            { let _it = iter(obj); let x: T; while (next(&_it, &x)) { body } }

        The iterator is a compiler-held temporary -- never a named local -- so
        it cannot collide with user code, and the element variable lives in a
        fresh block scope, gone once the loop ends. The element type T is read
        from the resolved `next` overload's out-parameter."""
        # iter(obj) -- resolves and instantiates iter for obj's type.
        if not self.callable_exists("iter"):
            raise LangError(
                "'for ... in' needs an 'iter' function for the iterable; "
                "none is in scope", stmt.line
            )
        iterator = self.gen_call(Call("iter", [], [stmt.iterable], stmt.line))
        it_slot = self.builder.alloca(iterator.type.ir, name="for.iter")
        self.builder.store(iterator.value, it_slot)

        next_fn, element = self.resolve_protocol_next(iterator.type, stmt.line)

        # A fresh scope for the element variable and the loop's defers.
        outer_locals, outer_names = dict(self.locals), self.scope_names
        self.scope_names = set()
        self.defer_stack.append([])
        try:
            x_slot = self.builder.alloca(element.ir, name=stmt.var)
            if over_aligned(element):
                x_slot.align = type_align(element)
            self.bind_local(stmt.var, x_slot, element)

            cond_bb = self.builder.append_basic_block("for.cond")
            body_bb = self.builder.append_basic_block("for.body")
            end_bb = self.builder.append_basic_block("for.end")
            self.builder.branch(cond_bb)
            self.builder.position_at_end(cond_bb)
            more = self.builder.call(next_fn, [it_slot, x_slot])  # next(&_it, &x)
            self.builder.cbranch(more, body_bb, end_bb)
            self.builder.position_at_end(body_bb)
            self.loops.append((cond_bb, end_bb, len(self.defer_stack)))
            try:
                self.gen_block(stmt.body)
            finally:
                self.loops.pop()
            if not self.builder.block.is_terminated:
                self.builder.branch(cond_bb)
            self.builder.position_at_end(end_bb)
        finally:
            self.defer_stack.pop()
            self.locals, self.scope_names = outer_locals, outer_names

    def callable_exists(self, name: str) -> bool:
        key = (self.current_source, name)
        return (name in self.templates or name in self.funcs
                or key in self.static_templates or key in self.static_funcs)

    def resolve_protocol_next(self, iter_type: LangType, line: int):
        """Find the `next` overload that consumes `iter_type` and return its
        instantiated function plus the element type it yields (its out-param's
        pointee). `next` is dispatched on the iterator alone, so the element
        type can be learned before the loop variable is declared."""
        want = pointer_to(iter_type)
        candidates = list(self.templates.get("next", []))
        static = self.static_templates.get((self.current_source, "next"))
        if static is not None:
            candidates.append(static)
        viable = []
        for func in candidates:
            if len(func.params) != 2:
                continue
            bindings: dict[str, LangType] = {}
            try:
                self.unify(func.params[0][1], want, func.type_params, bindings,
                           True, "for-loop 'next'", line)
            except LangError:
                continue
            if any(t not in bindings for t in func.type_params):
                continue
            if not self.shape_matches(func.params[0][1], want, False,
                                      func.type_params, line):
                continue
            viable.append((self.specificity(func), func, bindings))
        if not viable:
            raise LangError(
                f"no 'next' overload iterates a {iter_type} (for ... in)", line
            )
        viable.sort(key=lambda entry: entry[0], reverse=True)
        if len(viable) > 1 and viable[0][0] == viable[1][0]:
            raise LangError(f"ambiguous 'next' for {iter_type}", line)
        _, func, bindings = viable[0]
        fn, ret, params = self.instantiate(func, bindings)
        if ret is not BOOL:
            raise LangError("'next' must return bool", line)
        if not is_pointer(params[1]):
            raise LangError("'next' second parameter must be an out-pointer", line)
        return fn, params[1].pointee

    def gen_cond(self, expr) -> ir.Value:
        tv = self.gen_expr(expr)
        if tv.type is BOOL:
            return tv.value
        if is_integer(tv.type):
            return self.builder.icmp_signed("!=", tv.value, ir.Constant(tv.type.ir, 0))
        raise LangError("condition must be a bool or integer", expr.line)

    def gen_logical(self, expr: Logical) -> TypedValue:
        """Short-circuiting `and` / `or`. Each operand is tested like a
        condition (bool or integer); the result is a bool. The right operand is
        evaluated only when the left does not already decide the answer."""
        lhs = self.gen_cond(expr.lhs)
        lhs_block = self.builder.block
        rhs_block = self.builder.append_basic_block(f"{expr.op}.rhs")
        end_block = self.builder.append_basic_block(f"{expr.op}.end")
        # `and` runs the rhs only when lhs is true; `or` only when lhs is false.
        if expr.op == "and":
            self.builder.cbranch(lhs, rhs_block, end_block)
        else:
            self.builder.cbranch(lhs, end_block, rhs_block)
        self.builder.position_at_end(rhs_block)
        rhs = self.gen_cond(expr.rhs)
        rhs_block = self.builder.block  # the rhs may have added blocks of its own
        self.builder.branch(end_block)
        self.builder.position_at_end(end_block)
        phi = self.builder.phi(BOOL.ir)
        # When short-circuited, the answer is the lhs's deciding value: false
        # for `and` (lhs was false), true for `or` (lhs was true).
        phi.add_incoming(ir.Constant(BOOL.ir, expr.op == "or"), lhs_block)
        phi.add_incoming(rhs, rhs_block)
        return TypedValue(phi, BOOL)

    def gen_equals(self, subject: TypedValue, value: TypedValue, line: int) -> ir.Value:
        """An i1 for `subject == value`, used to test a `when` arm. The subject
        is the authoritative type: a `when` value adapts (or must coerce) to it,
        unless the subject is itself an untyped constant. Equality is
        sign-agnostic, so integers, pointers, and bools share an integer
        compare; float64 uses an ordered float compare."""
        if subject.type != value.type:
            if subject.adaptable and not value.adaptable:
                subject = self.coerce(subject, value.type, line, "case subject")
            else:
                value = self.coerce(value, subject.type, line, "when value")
        if subject.type is FLOAT64:
            return self.builder.fcmp_ordered("==", subject.value, value.value)
        return self.builder.icmp_unsigned("==", subject.value, value.value)

    def gen_expr(self, expr) -> TypedValue:
        if isinstance(expr, IntLit):
            return TypedValue(ir.Constant(INT32.ir, expr.value), INT32, adaptable=True)
        if isinstance(expr, CharLit):
            return TypedValue(ir.Constant(UINT8.ir, expr.value), UINT8)
        if isinstance(expr, FloatLit):
            return TypedValue(ir.Constant(FLOAT64.ir, expr.value), FLOAT64)
        if isinstance(expr, BoolLit):
            return TypedValue(ir.Constant(BOOL.ir, int(expr.value)), BOOL)
        if isinstance(expr, NullLit):
            return TypedValue(ir.Constant(RAWPTR.ir, None), NULLT, adaptable=True)
        if isinstance(expr, StrLit):
            return self.gen_string(expr.value)
        if isinstance(expr, ArrayLit):
            raise LangError(
                "an array literal is only allowed as a variable initializer", expr.line
            )
        if isinstance(expr, Var):
            # A name that is not a variable may be a constant or a function used
            # as a value.
            if self.var_type_of(expr.name) is None:
                const = self.consts.get(expr.name)
                if const is not None:
                    self.check_access(*self.const_privacy[expr.name],
                                      f"constant {expr.name!r}", expr.line)
                    return const
                fv = self.func_value(expr.name, expr.line)
                if fv is not None:
                    return fv
            slot, var_type, volatile = self.var_addr(expr.name, expr.line)
            return self.value_at(slot, var_type, volatile=volatile, name=expr.name)
        if isinstance(expr, Call):
            return self.gen_call(expr)
        if isinstance(expr, CallExpr):
            callee = self.gen_expr(expr.callee)
            return self.gen_indirect_call(callee, expr.args,
                                          f"call to {callee.type}", expr.line)
        if isinstance(expr, Unary):
            return self.gen_unary(expr)
        if isinstance(expr, Logical):
            return self.gen_logical(expr)
        if isinstance(expr, Binary):
            return self.gen_binary(expr)
        if isinstance(expr, Cast):
            return self.gen_cast(expr)
        if isinstance(expr, SizeOf):
            size = type_size(self.lang_type(expr.type_name, expr.line))
            return TypedValue(ir.Constant(UINT64.ir, size), UINT64)
        if isinstance(expr, Len):
            # The element count is a compile-time property of the array's type;
            # read it through the address so the array does not decay first. It
            # is an adaptable constant -- like writing the literal count -- so it
            # compares against an int32 counter as readily as a uint64 one.
            _, lang_type, _, _ = self.gen_addr(expr.operand, expr.line)
            if not is_array(lang_type):
                raise LangError(f"len() requires an array, got {lang_type}", expr.line)
            return TypedValue(ir.Constant(UINT64.ir, lang_type.count), UINT64, adaptable=True)
        if isinstance(expr, Index):
            addr, element = self.gen_index_addr(expr.base, expr.index, expr.line)
            return self.value_at(addr, element, volatile=element.volatile)
        if isinstance(expr, Member):
            if not expr.arrow and not isinstance(expr.base, (Var, Member, Index, Unary)):
                # Field of a non-addressable struct value, e.g. f().field.
                base = self.gen_expr(expr.base)
                index, ftype = self.struct_field(base.type, expr.field, expr.line)
                return TypedValue(self.builder.extract_value(base.value, index), ftype)
            addr, ftype, align, volatile = self.gen_member_addr(
                expr.base, expr.field, expr.arrow, expr.line
            )
            return self.value_at(addr, ftype, align=align, volatile=volatile)
        raise LangError(f"cannot compile expression {expr!r}", expr.line)

    def value_at(self, addr, lang_type: LangType, *, align=None, volatile=False,
                 name="") -> TypedValue:
        """The value held at `addr`. An array decays to a pointer to its first
        element (C array-to-pointer decay), so indexing, passing it as a
        pointer argument, and assigning it all go through the pointer; every
        other type is loaded normally."""
        if is_array(lang_type):
            first = self.builder.gep(addr, [I32_ZERO, I32_ZERO], inbounds=True)
            return TypedValue(first, pointer_to(lang_type.element))
        return TypedValue(self.gen_load(addr, align=align, volatile=volatile, name=name),
                          lang_type)

    def var_addr(self, name: str, line: int) -> tuple[ir.Value, LangType, bool]:
        """A variable's storage slot: a local alloca, a file-scoped @static
        global, or an @extern global (in that order, so locals shadow globals
        and a file's own @static shadows a same-named extern). Returns (pointer
        value, variable type, volatile) -- volatile for @volatile globals and
        for variables of @volatile struct types."""
        if name in self.locals:
            slot, var_type = self.locals[name]
            return slot, var_type, var_type.volatile
        static = self.static_globals.get((self.current_source, name))
        entry = static or self.globals.get(name)
        if entry is not None:
            private, source = self.global_privacy[name]
            self.check_access(private, source, f"variable {name!r}", line)
            glob, var_type, volatile = entry
            return glob, var_type, volatile or var_type.volatile
        if name in self.consts:
            raise LangError(f"cannot assign to constant {name!r}", line)
        raise LangError(f"undefined variable {name!r}", line)

    def var_type_of(self, name: str) -> "LangType | None":
        """The type of `name` if it is a variable in scope (local, @static, or
        @extern global), else None -- so a bare name that is not a variable can
        fall back to being a function value."""
        if name in self.locals:
            return self.locals[name][1]
        static = self.static_globals.get((self.current_source, name))
        if static is not None:
            return static[1]
        if name in self.globals:
            return self.globals[name][1]
        return None

    def gen_addr(self, expr, line: int) -> tuple[ir.Value, LangType, int | None, bool]:
        """Address of an lvalue expression: a variable, *deref, element, or
        struct field. Returns (pointer value, type pointed to, guaranteed
        alignment, volatile). The alignment is None when the address is
        naturally aligned for its type, 1 when it may not be (a field of a
        @packed struct, directly or through nesting); volatile is True when
        accesses through the address must not be optimized away."""
        if isinstance(expr, Var):
            slot, var_type, volatile = self.var_addr(expr.name, line)
            return slot, var_type, None, volatile
        if isinstance(expr, Unary) and expr.op == "*":
            tv = self.gen_expr(expr.operand)
            if not is_pointer(tv.type):
                raise LangError(f"cannot dereference a {tv.type}", line)
            return tv.value, tv.type.pointee, None, tv.type.pointee.volatile
        if isinstance(expr, Index):
            addr, element = self.gen_index_addr(expr.base, expr.index, line)
            return addr, element, None, element.volatile
        if isinstance(expr, Member):
            return self.gen_member_addr(expr.base, expr.field, expr.arrow, line)
        raise LangError("expression is not addressable", line)

    def gen_index_addr(self, base_expr, index_expr, line: int) -> tuple[ir.Value, LangType]:
        """Address of base[index]; returns (pointer value, element type)."""
        base = self.gen_expr(base_expr)
        if not is_pointer(base.type):
            raise LangError(f"cannot index a {base.type}", line)
        index = self.gen_expr(index_expr)
        if not is_integer(index.type):
            raise LangError(f"index must be an integer, not {index.type}", line)
        addr = self.builder.gep(base.value, [index.value])
        return addr, base.type.pointee

    def gen_member_addr(self, base_expr, fname: str, arrow: bool,
                        line: int) -> tuple[ir.Value, LangType, int | None, bool]:
        """Address of base.field / base->field; returns (pointer, field type,
        guaranteed alignment, volatile) as in gen_addr."""
        if arrow:
            base = self.gen_expr(base_expr)
            if not is_pointer(base.type):
                raise LangError(f"'->' requires a struct pointer, got {base.type}", line)
            owner, base_addr = base.type.pointee, base.value
            base_align, base_volatile = None, False
        else:
            base_addr, owner, base_align, base_volatile = self.gen_addr(base_expr, line)
        index, ftype = self.struct_field(owner, fname, line)
        addr = self.builder.gep(
            base_addr, [I32_ZERO, ir.Constant(ir.IntType(32), index)], inbounds=True
        )
        # A @packed owner (or a base that already sits at a packed offset)
        # gives no alignment guarantee; loads and stores must say so, or
        # LLVM would assume the field type's natural alignment.
        if owner.packed:
            align = 1
        elif base_align is not None:
            align = min(base_align, type_align(ftype))
        else:
            align = None
        # @volatile propagates from the owner down through nested fields.
        return addr, ftype, align, owner.volatile or base_volatile

    def gen_unary(self, expr: Unary) -> TypedValue:
        # Fold minus on literals so negative constants stay constants (and can
        # still coerce to other integer types).
        if expr.op == "-" and isinstance(expr.operand, IntLit):
            return TypedValue(ir.Constant(INT32.ir, -expr.operand.value), INT32, adaptable=True)
        if expr.op == "-" and isinstance(expr.operand, FloatLit):
            return TypedValue(ir.Constant(FLOAT64.ir, -expr.operand.value), FLOAT64)
        if expr.op == "&":
            # The pointer type does not carry the (possibly reduced)
            # alignment: taking the address of a packed field and
            # dereferencing it elsewhere is unsafe, exactly as in C.
            addr, lang_type, _, _ = self.gen_addr(expr.operand, expr.line)
            return TypedValue(addr, pointer_to(lang_type))
        tv = self.gen_expr(expr.operand)
        if expr.op == "*":
            if not is_pointer(tv.type):
                raise LangError(f"cannot dereference a {tv.type}", expr.line)
            return TypedValue(
                self.gen_load(tv.value, volatile=tv.type.pointee.volatile),
                tv.type.pointee,
            )
        if expr.op == "-":
            if is_integer(tv.type) and tv.type.signed:
                return TypedValue(self.builder.neg(tv.value), tv.type)
            if tv.type is FLOAT64:
                return TypedValue(self.builder.fneg(tv.value), tv.type)
            raise LangError(f"cannot negate a {tv.type}", expr.line)
        if tv.type is not BOOL:
            raise LangError("'!' requires a bool operand", expr.line)
        return TypedValue(self.builder.not_(tv.value), BOOL)

    def gen_cast(self, expr: Cast) -> TypedValue:
        tv = self.gen_expr(expr.value)
        target = self.lang_type(expr.type_name, expr.line)
        src = tv.type
        if src == target:
            return TypedValue(tv.value, target)
        # A function value is a pointer underneath (LLVM `ret (args)*`), so it
        # casts like one: between pointer kinds, and to/from a 64-bit integer
        # address -- exactly as a function name converts to an address in C.
        src_addr = is_pointer(src) or is_function(src)
        target_addr = is_pointer(target) or is_function(target)
        if src_addr and target_addr:
            return TypedValue(self.builder.bitcast(tv.value, target.ir), target)
        if src_addr and is_integer(target) and target.ir.width == 64:
            return TypedValue(self.builder.ptrtoint(tv.value, target.ir), target)
        if is_integer(src) and target_addr:
            return TypedValue(self.builder.inttoptr(tv.value, target.ir), target)
        if is_struct(src) or is_struct(target):
            raise LangError(f"cannot cast {src} to {target}", expr.line)
        if isinstance(src.ir, ir.IntType) and target is BOOL:
            zero = ir.Constant(src.ir, 0)
            return TypedValue(self.builder.icmp_signed("!=", tv.value, zero), BOOL)
        if isinstance(src.ir, ir.IntType) and isinstance(target.ir, ir.IntType):
            src_width, dst_width = src.ir.width, target.ir.width
            if dst_width == src_width:
                value = tv.value  # same bits, different signedness
            elif dst_width < src_width:
                value = self.builder.trunc(tv.value, target.ir)
            elif src.signed:
                value = self.builder.sext(tv.value, target.ir)
            else:
                value = self.builder.zext(tv.value, target.ir)
            return TypedValue(value, target)
        if isinstance(src.ir, ir.IntType) and target is FLOAT64:
            convert = self.builder.sitofp if src.signed else self.builder.uitofp
            return TypedValue(convert(tv.value, target.ir), target)
        if src is FLOAT64 and is_integer(target):
            convert = self.builder.fptosi if target.signed else self.builder.fptoui
            return TypedValue(convert(tv.value, target.ir), target)
        raise LangError(f"cannot cast {src} to {target}", expr.line)

    def string_global(self, text: str) -> ir.GlobalVariable:
        """A private constant global holding the NUL-terminated bytes of text."""
        data = bytearray(text.encode("utf8") + b"\0")
        array_ty = ir.ArrayType(ir.IntType(8), len(data))
        glob = ir.GlobalVariable(self.module, array_ty, name=f".str.{self.str_count}")
        self.str_count += 1
        glob.linkage = "private"
        glob.global_constant = True
        glob.unnamed_addr = True
        glob.initializer = ir.Constant(array_ty, data)
        return glob

    def gen_string(self, text: str) -> TypedValue:
        return TypedValue(self.builder.bitcast(self.string_global(text), RAWPTR.ir), RAWPTR)

    def const_string(self, text: str) -> ir.Constant:
        """A constant uint8* to a string's first byte, for static initializers."""
        return self.string_global(text).gep([I32_ZERO, I32_ZERO])

    def store_array_literal(self, addr, lit, arr_type: LangType, line: int):
        """Fill an array's storage at `addr` from an array literal, element by
        element (each may be any expression). Nested literals recurse."""
        if not isinstance(lit, ArrayLit):
            raise LangError(f"expected {arr_type.count} array elements", line)
        if len(lit.elements) != arr_type.count:
            raise LangError(
                f"array literal has {len(lit.elements)} elements, expected {arr_type.count}",
                line,
            )
        for i, element in enumerate(lit.elements):
            slot = self.builder.gep(addr, [I32_ZERO, ir.Constant(ir.IntType(32), i)],
                                    inbounds=True)
            if is_array(arr_type.element):
                self.store_array_literal(slot, element, arr_type.element, line)
            else:
                tv = self.coerce(self.gen_expr(element), arr_type.element, line,
                                 "array element")
                self.gen_store(tv.value, slot)

    def const_initializer(self, expr, expected: LangType, line: int) -> ir.Constant:
        """A constant of type `expected` for a @static initializer. Arrays use
        nested literals; scalars must be compile-time constants (number, char,
        or string literal, or null)."""
        if isinstance(expr, ArrayLit):
            if not is_array(expected):
                raise LangError(f"an array literal cannot initialize a {expected}", line)
            if len(expr.elements) != expected.count:
                raise LangError(
                    f"array literal has {len(expr.elements)} elements, "
                    f"expected {expected.count}", line
                )
            return ir.Constant(expected.ir, [
                self.const_initializer(e, expected.element, line) for e in expr.elements
            ])
        if isinstance(expr, StrLit) and expected == RAWPTR:
            return self.const_string(expr.value)
        if isinstance(expr, NullLit) and is_pointer(expected):
            return ir.Constant(expected.ir, None)
        if isinstance(expr, (IntLit, CharLit)) and is_integer(expected):
            return self.coerce(self.gen_const_scalar(expr), expected, line,
                               "initializer").value
        if isinstance(expr, FloatLit) and expected is FLOAT64:
            return ir.Constant(FLOAT64.ir, expr.value)
        raise LangError(
            f"a @static initializer must be a constant of type {expected}", line
        )

    def gen_const_scalar(self, expr) -> TypedValue:
        """An adaptable constant TypedValue for an integer/char literal, used
        outside a function body (no builder), for const_initializer."""
        if isinstance(expr, CharLit):
            return TypedValue(ir.Constant(UINT8.ir, expr.value), UINT8)
        return TypedValue(ir.Constant(INT32.ir, expr.value), INT32, adaptable=True)

    def eval_const(self, expr, line: int) -> TypedValue:
        """Fold a `const` initializer to a TypedValue whose value is an
        ir.Constant: literals, references to other consts, sizeof, numeric
        casts, and integer/float arithmetic. An untyped integer result stays
        adaptable, like a literal. Anything needing the runtime is an error.
        Built without a builder -- consts are folded before any function."""
        if isinstance(expr, IntLit):
            return TypedValue(ir.Constant(INT32.ir, expr.value), INT32, adaptable=True)
        if isinstance(expr, CharLit):
            return TypedValue(ir.Constant(UINT8.ir, expr.value), UINT8)
        if isinstance(expr, FloatLit):
            return TypedValue(ir.Constant(FLOAT64.ir, expr.value), FLOAT64)
        if isinstance(expr, BoolLit):
            return TypedValue(ir.Constant(BOOL.ir, int(expr.value)), BOOL)
        if isinstance(expr, NullLit):
            return TypedValue(ir.Constant(RAWPTR.ir, None), NULLT, adaptable=True)
        if isinstance(expr, StrLit):
            return TypedValue(self.const_string(expr.value), RAWPTR)
        if isinstance(expr, SizeOf):
            size = type_size(self.lang_type(expr.type_name, line))
            return TypedValue(ir.Constant(UINT64.ir, size), UINT64)
        if isinstance(expr, Var):
            const = self.consts.get(expr.name)
            if const is None:
                raise LangError(
                    f"{expr.name!r} is not a constant; a const initializer must be "
                    "a compile-time constant", expr.line
                )
            self.check_access(*self.const_privacy[expr.name],
                              f"constant {expr.name!r}", expr.line)
            return const
        if isinstance(expr, Unary):
            return self.eval_const_unary(expr)
        if isinstance(expr, Cast):
            return self.eval_const_cast(expr)
        if isinstance(expr, Binary):
            return self.eval_const_binary(expr)
        raise LangError("a const initializer must be a compile-time constant", line)

    def const_coerce(self, tv: TypedValue, expected: LangType, line: int,
                     context: str) -> TypedValue:
        """coerce() for constants: equality, null -> pointer, and adaptable
        integer narrowing, all without a builder."""
        if tv.type == expected:
            return tv
        if tv.type is NULLT and (is_pointer(expected) or is_function(expected)):
            return TypedValue(ir.Constant(expected.ir, None), expected)
        if (tv.adaptable and is_integer(tv.type) and is_integer(expected)
                and isinstance(tv.value.constant, int)):
            width = expected.ir.width
            lo, hi = ((-(1 << (width - 1)), 1 << (width - 1)) if expected.signed
                      else (0, 1 << width))
            if lo <= tv.value.constant < hi:
                return TypedValue(ir.Constant(expected.ir, tv.value.constant), expected)
            raise LangError(
                f"constant {tv.value.constant} is out of range for {expected}", line
            )
        raise LangError(f"{context}: expected {expected}, got {tv.type}", line)

    def eval_const_unary(self, expr: Unary) -> TypedValue:
        operand = self.eval_const(expr.operand, expr.line)
        if expr.op == "-" and is_integer(operand.type):
            return TypedValue(
                ir.Constant(operand.type.ir, wrap_int(-operand.value.constant, operand.type)),
                operand.type, adaptable=operand.adaptable,
            )
        if expr.op == "-" and operand.type is FLOAT64:
            return TypedValue(ir.Constant(FLOAT64.ir, -operand.value.constant), FLOAT64)
        if expr.op == "!" and operand.type is BOOL:
            return TypedValue(ir.Constant(BOOL.ir, int(not operand.value.constant)), BOOL)
        raise LangError(
            f"operator {expr.op!r} is not a compile-time constant for {operand.type}",
            expr.line,
        )

    def eval_const_cast(self, expr: Cast) -> TypedValue:
        tv = self.eval_const(expr.value, expr.line)
        target = self.lang_type(expr.type_name, expr.line)
        src = tv.type
        if src == target:
            return TypedValue(tv.value, target)
        if is_integer(src) and is_integer(target):
            return TypedValue(ir.Constant(target.ir, wrap_int(tv.value.constant, target)), target)
        if is_integer(src) and target is BOOL:
            return TypedValue(ir.Constant(BOOL.ir, int(tv.value.constant != 0)), BOOL)
        if is_integer(src) and target is FLOAT64:
            return TypedValue(ir.Constant(FLOAT64.ir, float(tv.value.constant)), FLOAT64)
        if src is FLOAT64 and is_integer(target):
            return TypedValue(ir.Constant(target.ir, wrap_int(int(tv.value.constant), target)), target)
        raise LangError(f"cannot cast {src} to {target} in a constant", expr.line)

    def eval_const_binary(self, expr: Binary) -> TypedValue:
        lhs = self.eval_const(expr.lhs, expr.line)
        rhs = self.eval_const(expr.rhs, expr.line)
        if lhs.type != rhs.type:
            if rhs.adaptable:
                rhs = self.const_coerce(rhs, lhs.type, expr.line, f"operand of {expr.op!r}")
            elif lhs.adaptable:
                lhs = self.const_coerce(lhs, rhs.type, expr.line, f"operand of {expr.op!r}")
            else:
                raise LangError(
                    f"operands of {expr.op!r} have different types: "
                    f"{lhs.type} and {rhs.type}", expr.line
                )
        op_type = lhs.type
        a, b = lhs.value.constant, rhs.value.constant
        if expr.op in COMPARISON_OPS and (is_integer(op_type) or op_type in (BOOL, FLOAT64)):
            result = {"==": a == b, "!=": a != b, "<": a < b, "<=": a <= b,
                      ">": a > b, ">=": a >= b}[expr.op]
            return TypedValue(ir.Constant(BOOL.ir, int(result)), BOOL)
        if is_integer(op_type):
            folded = fold_int_arithmetic(expr.op, a, b, op_type)
            if folded is None:
                raise LangError(
                    f"{expr.op!r} is not a compile-time constant here "
                    "(division by zero or out-of-range shift)", expr.line
                )
            return TypedValue(ir.Constant(op_type.ir, folded), op_type,
                              adaptable=lhs.adaptable and rhs.adaptable)
        if op_type is FLOAT64 and expr.op in ("+", "-", "*", "/"):
            result = {"+": a + b, "-": a - b, "*": a * b, "/": a / b}[expr.op]
            return TypedValue(ir.Constant(FLOAT64.ir, result), FLOAT64)
        raise LangError(
            f"operator {expr.op!r} is not a compile-time constant for {op_type}", expr.line
        )

    def gen_call(self, expr: Call) -> TypedValue:
        # va_start/va_end are builtins, not real functions: they lower to LLVM
        # intrinsics over the va_list's address.
        if expr.name in ("va_start", "va_end") and not expr.type_args:
            return self.gen_va_builtin(expr)
        # A variable (local, parameter, @static or @extern global) shadows any
        # same-named function. A function-pointer one is called indirectly;
        # anything else is simply not callable.
        var_type = self.var_type_of(expr.name)
        if var_type is not None:
            if not is_function(var_type):
                raise LangError(
                    f"{expr.name!r} is not callable; it is a {var_type}", expr.line
                )
            if expr.type_args:
                raise LangError(f"{expr.name!r} is not a generic function", expr.line)
            callee = self.gen_expr(Var(expr.name, expr.line))
            return self.gen_indirect_call(callee, expr.args, repr(expr.name), expr.line)
        # File-scoped (@static) names shadow the global namespace.
        key = (self.current_source, expr.name)
        if key in self.static_templates:
            return self.gen_generic_call(expr, [self.static_templates[key]])
        if key in self.static_funcs:
            return self.gen_direct_call(expr, self.static_funcs[key])
        if expr.name in self.templates:
            return self.gen_generic_call(expr, self.templates[expr.name])
        if expr.name not in self.funcs:
            raise LangError(f"undefined function {expr.name!r} (missing #include?)", expr.line)
        private, source = self.func_privacy.get(expr.name, (False, None))
        self.check_access(private, source, f"function {expr.name!r}", expr.line)
        return self.gen_direct_call(expr, expr.name)

    def valist_arg(self, expr, line: int) -> ir.Value:
        """The value passed when a va_list is handed to a function, derived from
        its storage address: load the cursor (scalar va_list, e.g. Apple arm64),
        decay to the first tag (array va_list, x86-64 SysV), or pass the address
        itself (struct va_list, AArch64 AAPCS) -- a pointer on every ABI."""
        addr, t, _, _ = self.gen_addr(expr, line)
        if not is_valist(t):
            raise LangError(f"expected a va_list argument, got {t}", line)
        self.require_valist(line)
        storage = t.ir
        if isinstance(storage, ir.ArrayType):
            return self.builder.gep(addr, [I32_ZERO, I32_ZERO], inbounds=True)
        if isinstance(storage, ir.PointerType):
            return self.gen_load(addr)
        return addr  # struct: pass its address

    def gen_va_builtin(self, expr: Call) -> TypedValue:
        """va_start(ap, last) / va_end(ap): initialise or finalise a va_list via
        the LLVM intrinsics. The intrinsic takes only the va_list's address; the
        named `last` parameter is accepted for C familiarity but unused."""
        arity = 2 if expr.name == "va_start" else 1
        if len(expr.args) != arity:
            form = "va_start(ap, last_named_param)" if arity == 2 else "va_end(ap)"
            raise LangError(f"{form} takes {arity} argument(s)", expr.line)
        if expr.name == "va_start" and not self.current_variadic:
            raise LangError("va_start is only valid inside a variadic function", expr.line)
        addr, t, _, _ = self.gen_addr(expr.args[0], expr.line)
        if not is_valist(t):
            raise LangError(f"{expr.name} requires a va_list, got {t}", expr.line)
        self.require_valist(expr.line)
        i8ptr = self.builder.bitcast(addr, RAWPTR.ir)
        return TypedValue(self.builder.call(self.va_intrinsic(expr.name), [i8ptr]), VOID)

    def va_intrinsic(self, kind: str) -> ir.Function:
        """The void(i8*) llvm.va_start / llvm.va_end intrinsic, declared once."""
        name = "llvm.va_start" if kind == "va_start" else "llvm.va_end"
        fn = self.funcs.get(name)
        if fn is None:
            fnty = ir.FunctionType(ir.VoidType(), [RAWPTR.ir])
            fn = ir.Function(self.module, fnty, name=name)
            self.funcs[name] = fn
        return fn

    def func_value(self, name: str, line: int) -> "TypedValue | None":
        """A bare function name used as a value: its address, typed as a
        function pointer. Only a single monomorphic function qualifies -- a
        generic or overloaded name has no one address. Returns None if the name
        is not a function at all (so the caller can report it as a variable)."""
        key = (self.current_source, name)
        if key in self.static_templates or name in self.templates:
            raise LangError(
                f"{name!r} is generic; a function value needs a single function", line
            )
        symbol = self.static_funcs.get(key)
        if symbol is None and name in self.funcs:
            private, source = self.func_privacy.get(name, (False, None))
            self.check_access(private, source, f"function {name!r}", line)
            symbol = name
        if symbol is None:
            return None
        ret, params, variadic = self.signatures[symbol]
        return TypedValue(self.funcs[symbol], function_type(ret, tuple(params), variadic))

    def gen_direct_call(self, expr: Call, symbol: str) -> TypedValue:
        if expr.type_args:
            raise LangError(f"{expr.name!r} is not a generic function", expr.line)
        ret, params, variadic = self.signatures[symbol]
        args = self.marshal_args(expr.args, params, variadic, repr(expr.name), expr.line)
        return TypedValue(self.builder.call(self.funcs[symbol], args), ret)

    def gen_indirect_call(self, callee: TypedValue, arg_exprs: list,
                          label: str, line: int) -> TypedValue:
        """Call through a function-pointer value -- a variable, a parameter, or
        any expression of function-pointer type (e.g. a struct field)."""
        if not is_function(callee.type):
            raise LangError(f"cannot call a value of type {callee.type}", line)
        ret, params, variadic = callee.type.signature
        args = self.marshal_args(arg_exprs, params, variadic, label, line)
        return TypedValue(self.builder.call(callee.value, args), ret)

    def marshal_args(self, arg_exprs: list, params, variadic: bool,
                     label: str, line: int) -> list:
        """Evaluate and coerce a call's arguments against the callee's
        parameter types, applying C varargs promotions past a variadic tail."""
        if len(arg_exprs) < len(params) or (len(arg_exprs) > len(params) and not variadic):
            raise LangError(
                f"{label} expects {len(params)} argument(s), got {len(arg_exprs)}", line
            )
        args = []
        for i, arg_expr in enumerate(arg_exprs):
            if i < len(params) and is_valist(params[i]):
                # A va_list is handed over in its platform-specific passed form,
                # derived from its storage; coerce/decay do not apply.
                args.append(self.valist_arg(arg_expr, line))
                continue
            tv = self.gen_expr(arg_expr)
            if i < len(params):
                tv = self.coerce(tv, params[i], line, f"argument {i + 1} of {label}")
                value = tv.value
            elif is_integer(tv.type) and tv.type.ir.width < 32:
                # C varargs promote small integers to int (sign- or
                # zero-extending to match the source type's signedness).
                extend = self.builder.sext if tv.type.signed else self.builder.zext
                value = extend(tv.value, INT32.ir)
            elif tv.type is BOOL:
                value = self.builder.zext(tv.value, INT32.ir)
            elif is_struct(tv.type):
                raise LangError(
                    "cannot pass a struct to a variadic function; pass a pointer", line
                )
            else:
                value = tv.value
            args.append(value)
        return args

    def unify(self, pattern: TypeRef, actual: LangType, type_params: list[str],
              bindings: dict[str, LangType], strict: bool, context: str, line: int):
        """Match a parameter's TypeRef against an argument type, binding any
        type parameters it mentions. `array<T>*` against `array<int32>*`
        binds T = int32.

        When `strict`, two typed arguments that disagree about the same
        parameter are reported as a conflict. Non-strict matches (untyped
        constants, or parameters fixed by explicit type arguments) never
        override or conflict with an existing binding; any mismatch there
        surfaces as an ordinary coercion error afterwards."""
        peeled = actual
        for _ in range(pattern.stars):
            if not is_pointer(peeled):
                return
            peeled = peeled.pointee
        if pattern.name in type_params and not pattern.args:
            bound = bindings.get(pattern.name)
            if bound is None:
                bindings[pattern.name] = peeled
            elif strict and bound != peeled:
                raise LangError(
                    f"conflicting types for type parameter {pattern.name} in {context}: "
                    f"{bound} vs {peeled}",
                    line,
                )
            return
        if pattern.args and peeled.template == pattern.name \
                and len(peeled.args) == len(pattern.args):
            for sub_pattern, sub_actual in zip(pattern.args, peeled.args):
                self.unify(sub_pattern, sub_actual, type_params, bindings,
                           strict, context, line)

    def gen_generic_call(self, expr: Call, candidates: list[Func]) -> TypedValue:
        arg_tvs = [self.gen_expr(arg) for arg in expr.args]
        if len(candidates) == 1:
            func = candidates[0]
            bindings = self.resolve_bindings(func, expr, arg_tvs, lenient=False)
        else:
            # Overload set: keep the viable candidates and pick the one with
            # the most specific parameter patterns (T* beats T, and so on).
            viable = []
            for func in candidates:
                bindings = self.resolve_bindings(func, expr, arg_tvs, lenient=True)
                if bindings is not None:
                    viable.append((self.specificity(func), func, bindings))
            if not viable:
                arg_types = ", ".join(str(tv.type) for tv in arg_tvs)
                raise LangError(
                    f"no overload of {expr.name!r} matches argument types ({arg_types})",
                    expr.line,
                )
            viable.sort(key=lambda entry: entry[0], reverse=True)
            if len(viable) > 1 and viable[0][0] == viable[1][0]:
                raise LangError(f"call to {expr.name!r} is ambiguous between overloads",
                                expr.line)
            _, func, bindings = viable[0]
        self.check_access(func.private, func.source, f"function {expr.name!r}", expr.line)
        for tparam, bound in bindings.items():
            if bound is VOID:
                raise LangError(f"cannot bind type parameter {tparam} to {bound}", expr.line)
        fn, ret, params = self.instantiate(func, bindings)
        args = [
            self.coerce(tv, p, expr.line, f"argument {i + 1} of {expr.name!r}").value
            for i, (tv, p) in enumerate(zip(arg_tvs, params))
        ]
        return TypedValue(self.builder.call(fn, args), ret)

    def resolve_bindings(self, func: Func, expr: Call, arg_tvs: list[TypedValue],
                         lenient: bool) -> dict[str, LangType] | None:
        """Determine the type-parameter bindings for calling `func`.

        Inference takes typed values first, then untyped constants (whose
        int32 default should not win over a typed value bound to the same
        parameter). `null` carries no type information and never
        participates. Disagreement between typed arguments is a conflict,
        unless the parameters were fixed explicitly (then plain coercion
        errors point at the bad argument).

        When `lenient` (overload trial), any failure returns None instead of
        raising, and argument shapes must match the parameter patterns.
        """
        if len(expr.args) != len(func.params):
            if lenient:
                return None
            raise LangError(
                f"{expr.name!r} expects {len(func.params)} argument(s), got {len(expr.args)}",
                expr.line,
            )
        bindings: dict[str, LangType] = {}
        if expr.type_args:
            if len(expr.type_args) != len(func.type_params):
                if lenient:
                    return None
                raise LangError(
                    f"{expr.name!r} expects {len(func.type_params)} type argument(s), "
                    f"got {len(expr.type_args)}",
                    expr.line,
                )
            for tparam, targ in zip(func.type_params, expr.type_args):
                bindings[tparam] = self.lang_type(targ, expr.line)
        try:
            for adaptable_pass in (False, True):
                strict = not adaptable_pass and not expr.type_args
                for (_, ptype), tv in zip(func.params, arg_tvs):
                    if tv.adaptable == adaptable_pass and tv.type is not NULLT:
                        self.unify(ptype, tv.type, func.type_params, bindings,
                                   strict, f"call to {expr.name!r}", expr.line)
        except LangError:
            if lenient:
                return None
            raise
        missing = [t for t in func.type_params if t not in bindings]
        if missing:
            if lenient:
                return None
            raise LangError(
                f"cannot infer type parameter(s) {', '.join(missing)} for {expr.name!r}; "
                f"specify them explicitly, e.g. {expr.name}<int32>(...)",
                expr.line,
            )
        if lenient:
            for (_, ptype), tv in zip(func.params, arg_tvs):
                if not self.shape_matches(ptype, tv.type, tv.adaptable,
                                          func.type_params, expr.line):
                    return None
        return bindings

    def shape_matches(self, pattern: TypeRef, actual: LangType, adaptable: bool,
                      type_params: list[str], line: int) -> bool:
        """Whether an argument type structurally fits a parameter pattern
        (used only to filter overload candidates)."""
        peeled = actual
        for _ in range(pattern.stars):
            if not is_pointer(peeled):
                return False
            peeled = peeled.pointee
            adaptable = False
        if pattern.name in type_params and not pattern.args:
            return True
        if pattern.args:
            return (peeled.template == pattern.name
                    and len(peeled.args) == len(pattern.args)
                    and all(self.shape_matches(p, a, False, type_params, line)
                            for p, a in zip(pattern.args, peeled.args)))
        try:
            resolved = self.lang_type(TypeRef(pattern.name), line)
        except LangError:
            return False
        if peeled == resolved:
            return True
        if adaptable and is_integer(resolved) and is_integer(peeled):
            return True
        return resolved == RAWPTR and is_pointer(peeled)

    def specificity(self, func: Func) -> int:
        """Rank an overload: concrete types beat structured patterns, which
        beat bare type parameters; pointer depth adds specificity."""
        def score(pattern: TypeRef) -> int:
            value = pattern.stars
            if pattern.args:
                value += 4 + sum(score(a) for a in pattern.args)
            elif pattern.name not in func.type_params:
                value += 8
            return value
        return sum(score(p) for _, p in func.params)

    def instantiate(
        self, func: Func, bindings: dict[str, LangType]
    ) -> tuple[ir.Function, LangType, list[LangType]]:
        """Return the monomorphized instance of `func` for `bindings`,
        generating (and caching) it on first use."""
        key = (id(func), tuple(str(bindings[t]) for t in func.type_params))
        if key in self.instances:
            mangled = self.instances[key]
            ret, params, _ = self.signatures[mangled]
            return self.funcs[mangled], ret, params
        base = self.template_bases.get(id(func)) \
            or self.symbol_bases.get((func.source, func.name), func.name)
        mangled = f"{base}<{', '.join(str(bindings[t]) for t in func.type_params)}>"
        outer_bindings = self.type_bindings
        outer_source = self.current_source
        saved = (self.builder, self.locals, self.ret_type, self.loops,
                 self.current_variadic, self.scope_names, self.defer_stack)
        self.type_bindings = bindings
        self.current_source = func.source  # the signature may name private structs
        try:
            ret = self.lang_type(func.ret_type, func.line)
            params = [self.lang_type(t, func.line) for _, t in func.params]
            fnty = ir.FunctionType(ret.ir, self.param_irs(params))
            fn = ir.Function(self.module, fnty, name=mangled)
            # A generic instance is emitted in every object that uses it, so it
            # merges like an imported definition rather than colliding.
            self.link_shared(fn, func.source)
            # Register before generating the body so recursive calls resolve.
            self.funcs[mangled] = fn
            self.signatures[mangled] = (ret, params, False)
            self.instances[key] = mangled
            self.gen_function(func, fn, ret, params)
        finally:
            (self.builder, self.locals, self.ret_type, self.loops,
             self.current_variadic, self.scope_names, self.defer_stack) = saved
            self.type_bindings = outer_bindings
            self.current_source = outer_source
        return fn, ret, params

    def gen_binary(self, expr: Binary) -> TypedValue:
        lhs = self.gen_expr(expr.lhs)
        rhs = self.gen_expr(expr.rhs)
        if lhs.type != rhs.type:
            # An untyped constant operand may adapt to the other side's type.
            if rhs.adaptable:
                rhs = self.coerce(rhs, lhs.type, expr.line, f"operand of {expr.op!r}")
            else:
                lhs = self.coerce(lhs, rhs.type, expr.line, f"operand of {expr.op!r}")
        op_type = lhs.type
        if expr.op in COMPARISON_OPS:
            if is_pointer(op_type) or is_function(op_type):
                if expr.op not in ("==", "!="):
                    raise LangError(f"operator {expr.op!r} not supported for {op_type}", expr.line)
                return TypedValue(
                    self.builder.icmp_unsigned(expr.op, lhs.value, rhs.value), BOOL
                )
            if isinstance(op_type.ir, ir.IntType):
                icmp = self.builder.icmp_signed if op_type.signed else self.builder.icmp_unsigned
                return TypedValue(icmp(expr.op, lhs.value, rhs.value), BOOL)
            if op_type is FLOAT64:
                return TypedValue(self.builder.fcmp_ordered(expr.op, lhs.value, rhs.value), BOOL)
        elif is_integer(op_type):
            # Fold constant operands so expressions like 10 * sizeof(int64)
            # remain constants (and can still adapt to other integer types).
            if isinstance(lhs.value, ir.Constant) and isinstance(rhs.value, ir.Constant):
                folded = fold_int_arithmetic(
                    expr.op, lhs.value.constant, rhs.value.constant, op_type
                )
                if folded is not None:
                    return TypedValue(ir.Constant(op_type.ir, folded), op_type,
                                      adaptable=lhs.adaptable and rhs.adaptable)
            ops = {
                "+": self.builder.add,
                "-": self.builder.sub,
                "*": self.builder.mul,
                "/": self.builder.sdiv if op_type.signed else self.builder.udiv,
                "%": self.builder.srem if op_type.signed else self.builder.urem,
                "&": self.builder.and_,
                "|": self.builder.or_,
                "^": self.builder.xor,
                "<<": self.builder.shl,
                ">>": self.builder.ashr if op_type.signed else self.builder.lshr,
            }
            return TypedValue(ops[expr.op](lhs.value, rhs.value), op_type)
        elif op_type is FLOAT64 and expr.op != "%":
            ops = {"+": self.builder.fadd, "-": self.builder.fsub,
                   "*": self.builder.fmul, "/": self.builder.fdiv}
            return TypedValue(ops[expr.op](lhs.value, rhs.value), op_type)
        raise LangError(f"operator {expr.op!r} not supported for {op_type}", expr.line)
