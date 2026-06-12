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

from dataclasses import dataclass, field

from llvmlite import ir

from mcc.errors import LangError
from mcc.nodes import (
    Assign, Binary, BoolLit, Call, Cast, ExprStmt, FloatLit, Func, If, Index,
    IntLit, Let, Member, NullLit, Program, Return, SizeOf, StoreDeref,
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
    # LLVM element index of each field; in explicitly laid out structs
    # (see instantiate_struct) padding elements shift the indices.
    elem_indices: tuple | None = field(default=None, compare=False)

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


VOID = LangType("void", ir.VoidType())
BOOL = LangType("bool", ir.IntType(1), signed=False)
FLOAT64 = LangType("float64", ir.DoubleType())

TYPES = {"void": VOID, "bool": BOOL, "float64": FLOAT64}
for _width in (8, 16, 32, 64):
    TYPES[f"int{_width}"] = LangType(f"int{_width}", ir.IntType(_width), signed=True)
    TYPES[f"uint{_width}"] = LangType(f"uint{_width}", ir.IntType(_width), signed=False)

INT32 = TYPES["int32"]
UINT64 = TYPES["uint64"]
# uint8* doubles as the "raw memory" pointer (C's void*/char*); string
# literals have this type, and any pointer implicitly coerces to it.
RAWPTR = pointer_to(TYPES["uint8"])
# The type of a bare `null`: a pointer that adapts to any pointer type.
NULLT = LangType("null", RAWPTR.ir, signed=False, pointee=TYPES["uint8"])

POINTER_SIZE = 8  # bytes; native codegen targets 64-bit platforms

I32_ZERO = ir.Constant(ir.IntType(32), 0)


def is_integer(lang_type: LangType) -> bool:
    """True for the intN/uintN types (not bool, which is i1 underneath)."""
    return (isinstance(lang_type.ir, ir.IntType) and lang_type is not BOOL
            and lang_type.pointee is None)


def is_pointer(lang_type: LangType) -> bool:
    return lang_type.pointee is not None


def is_struct(lang_type: LangType) -> bool:
    return lang_type.fields is not None


def type_align(lang_type: LangType) -> int:
    if is_pointer(lang_type):
        return POINTER_SIZE
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


class CodeGen:
    def __init__(self, program: Program, name: str):
        self.program = program
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
        self.globals: dict[str, tuple[ir.GlobalVariable, LangType]] = {}
        # name -> (private, source file); for @private access checks
        self.global_privacy: dict[str, tuple[bool, str | None]] = {}
        self.type_bindings: dict[str, LangType] = {}  # active type-parameter bindings
        # name -> (private, source file); for @private access checks
        self.func_privacy: dict[str, tuple[bool, str | None]] = {}
        self.current_source: str | None = None  # file owning the code being generated
        self.builder: ir.IRBuilder | None = None
        self.locals: dict[str, tuple[ir.AllocaInstr, LangType]] = {}
        self.ret_type: LangType = VOID
        self.str_count = 0

    def check_access(self, private: bool, source: str | None, what: str, line: int):
        if private and source != self.current_source:
            owner = source.rsplit("/", 1)[-1] if source else "its file"
            raise LangError(f"{what} is private to {owner}", line)

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

    def lang_type(self, ref: TypeRef, line: int) -> LangType:
        if ref.name in self.type_bindings and not ref.args:
            base = self.type_bindings[ref.name]
        elif ref.name in TYPES:
            if ref.args:
                raise LangError(f"type {ref.name!r} is not generic", line)
            base = TYPES[ref.name]
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
        return base

    def instantiate_struct(self, decl: StructDecl, args: tuple[LangType, ...]) -> LangType:
        """Return the struct instance for these type arguments, creating its
        LLVM identified type (and resolving field types) on first use."""
        mangled = self.symbol_bases.get((decl.source, decl.name), decl.name)
        if args:
            mangled += "<" + ", ".join(str(a) for a in args) + ">"
        if mangled in self.struct_types:
            return self.struct_types[mangled]
        identified = self.module.context.get_identified_type(mangled)
        struct_type = LangType(mangled, identified, signed=False, template=decl.name,
                               args=args, align=decl.align, packed=decl.packed)
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
            raise LangError(
                f"@align({decl.align}) is below struct {decl.name!r}'s "
                f"natural alignment of {natural}",
                decl.line,
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
        if self.program.imports:
            raise LangError(
                "imports must be resolved before code generation (compile via the driver)",
                self.program.imports[0][1],
            )
        for decl in self.program.structs:
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
        for var in self.program.globals:
            self.current_source = var.source  # the type may name private structs
            var_type = self.lang_type(var.type_name, var.line)
            if var_type is VOID:
                raise LangError(f"cannot declare a void variable {var.name!r}", var.line)
            if var.name in self.globals:
                if self.globals[var.name][1] != var_type:
                    raise LangError(
                        f"conflicting extern declarations for {var.name!r}", var.line
                    )
                continue
            if var.name in self.funcs:
                raise LangError(f"variable {var.name!r} already defined", var.line)
            glob = ir.GlobalVariable(self.module, var_type.ir, name=var.name)
            self.globals[var.name] = (glob, var_type)
            self.global_privacy[var.name] = (var.private, var.source)
            self.used_symbols.add(var.name)
        declared: set[tuple[str | None, str]] = set()
        for func in self.program.functions:
            if func.extern:
                self.current_source = func.source  # signatures may name private structs
                ret = self.lang_type(func.ret_type, func.line)
                params = [self.lang_type(t, func.line) for _, t in func.params]
                if func.name in self.extern_decls:
                    if self.signatures[func.name] != (ret, params, False):
                        raise LangError(
                            f"conflicting extern declarations for {func.name!r}", func.line
                        )
                    continue
                if func.name in self.funcs or func.name in self.templates \
                        or func.name in self.globals:
                    raise LangError(f"function {func.name!r} already defined", func.line)
                fnty = ir.FunctionType(ret.ir, [p.ir for p in params])
                self.funcs[func.name] = ir.Function(self.module, fnty, name=func.name)
                self.signatures[func.name] = (ret, params, False)
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
                fnty = ir.FunctionType(ret.ir, [p.ir for p in params])
                self.funcs[symbol] = ir.Function(self.module, fnty, name=symbol)
                self.signatures[symbol] = (ret, params, False)
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
            fnty = ir.FunctionType(ret.ir, [p.ir for p in params])
            self.funcs[func.name] = ir.Function(self.module, fnty, name=func.name)
            self.signatures[func.name] = (ret, params, False)
        for func in self.program.functions:
            if not func.type_params and not func.extern:
                symbol = self.static_funcs.get((func.source, func.name), func.name)
                ret, params, _ = self.signatures[symbol]
                self.gen_function(func, self.funcs[symbol], ret, params)
        return self.module

    def gen_function(self, func: Func, fn: ir.Function, ret: LangType, params: list[LangType]):
        self.ret_type = ret
        self.current_source = func.source
        self.builder = ir.IRBuilder(fn.append_basic_block("entry"))
        self.locals = {}
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
        for stmt in statements:
            if self.builder.block.is_terminated:
                break  # unreachable code after return
            self.gen_statement(stmt)

    def coerce(self, tv: TypedValue, expected: LangType, line: int, context: str) -> TypedValue:
        """Check that `tv` has type `expected`, adapting untyped constants.

        An adaptable integer constant may take on any integer type its value
        fits into (so `let x: uint64 = 5;` works), and `null` adapts to any
        pointer type. Any pointer coerces to uint8* (raw memory, like C's
        void*). Other values never convert implicitly -- use `as`.
        """
        if tv.type == expected:
            return tv
        if tv.type is NULLT and is_pointer(expected):
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

    def gen_statement(self, stmt):
        if isinstance(stmt, Return):
            if stmt.value is None:
                if self.ret_type is not VOID:
                    raise LangError(f"return needs a {self.ret_type} value", stmt.line)
                self.builder.ret_void()
            else:
                tv = self.coerce(self.gen_expr(stmt.value), self.ret_type, stmt.line, "return value")
                self.builder.ret(tv.value)
        elif isinstance(stmt, Let):
            if stmt.name in self.locals:
                raise LangError(f"variable {stmt.name!r} already declared", stmt.line)
            tv = self.gen_expr(stmt.value)
            if stmt.type_name is not None:
                declared = self.lang_type(stmt.type_name, stmt.line)
                if declared is VOID:
                    raise LangError("cannot declare a void variable", stmt.line)
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
            self.locals[stmt.name] = (slot, tv.type)
        elif isinstance(stmt, Assign):
            slot, var_type = self.var_addr(stmt.name, stmt.line)
            tv = self.coerce(self.gen_expr(stmt.value), var_type, stmt.line, f"assignment to {stmt.name}")
            self.builder.store(tv.value, slot)
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
            self.gen_block(stmt.body)
            if not self.builder.block.is_terminated:
                self.builder.branch(cond_bb)
            self.builder.position_at_end(end_bb)
        elif isinstance(stmt, StoreDeref):
            ptr = self.gen_expr(stmt.ptr)
            if not is_pointer(ptr.type):
                raise LangError(f"cannot dereference a {ptr.type}", stmt.line)
            value = self.coerce(self.gen_expr(stmt.value), ptr.type.pointee,
                                stmt.line, "assignment through pointer")
            self.builder.store(value.value, ptr.value)
        elif isinstance(stmt, StoreIndex):
            addr, element = self.gen_index_addr(stmt.base, stmt.index, stmt.line)
            value = self.coerce(self.gen_expr(stmt.value), element,
                                stmt.line, "assignment to element")
            self.builder.store(value.value, addr)
        elif isinstance(stmt, StoreMember):
            addr, ftype, align = self.gen_member_addr(
                stmt.base, stmt.field, stmt.arrow, stmt.line
            )
            value = self.coerce(self.gen_expr(stmt.value), ftype,
                                stmt.line, f"assignment to field {stmt.field!r}")
            self.builder.store(value.value, addr, align=align)
        elif isinstance(stmt, ExprStmt):
            self.gen_expr(stmt.expr)
        else:
            raise LangError(f"cannot compile statement {stmt!r}", stmt.line)

    def gen_cond(self, expr) -> ir.Value:
        tv = self.gen_expr(expr)
        if tv.type is BOOL:
            return tv.value
        if is_integer(tv.type):
            return self.builder.icmp_signed("!=", tv.value, ir.Constant(tv.type.ir, 0))
        raise LangError("condition must be a bool or integer", expr.line)

    def gen_expr(self, expr) -> TypedValue:
        if isinstance(expr, IntLit):
            return TypedValue(ir.Constant(INT32.ir, expr.value), INT32, adaptable=True)
        if isinstance(expr, FloatLit):
            return TypedValue(ir.Constant(FLOAT64.ir, expr.value), FLOAT64)
        if isinstance(expr, BoolLit):
            return TypedValue(ir.Constant(BOOL.ir, int(expr.value)), BOOL)
        if isinstance(expr, NullLit):
            return TypedValue(ir.Constant(RAWPTR.ir, None), NULLT, adaptable=True)
        if isinstance(expr, StrLit):
            return self.gen_string(expr.value)
        if isinstance(expr, Var):
            slot, var_type = self.var_addr(expr.name, expr.line)
            return TypedValue(self.builder.load(slot, name=expr.name), var_type)
        if isinstance(expr, Call):
            return self.gen_call(expr)
        if isinstance(expr, Unary):
            return self.gen_unary(expr)
        if isinstance(expr, Binary):
            return self.gen_binary(expr)
        if isinstance(expr, Cast):
            return self.gen_cast(expr)
        if isinstance(expr, SizeOf):
            size = type_size(self.lang_type(expr.type_name, expr.line))
            return TypedValue(ir.Constant(UINT64.ir, size), UINT64)
        if isinstance(expr, Index):
            addr, element = self.gen_index_addr(expr.base, expr.index, expr.line)
            return TypedValue(self.builder.load(addr), element)
        if isinstance(expr, Member):
            if not expr.arrow and not isinstance(expr.base, (Var, Member, Index, Unary)):
                # Field of a non-addressable struct value, e.g. f().field.
                base = self.gen_expr(expr.base)
                index, ftype = self.struct_field(base.type, expr.field, expr.line)
                return TypedValue(self.builder.extract_value(base.value, index), ftype)
            addr, ftype, align = self.gen_member_addr(expr.base, expr.field, expr.arrow, expr.line)
            return TypedValue(self.builder.load(addr, align=align), ftype)
        raise LangError(f"cannot compile expression {expr!r}", expr.line)

    def var_addr(self, name: str, line: int) -> tuple[ir.Value, LangType]:
        """A variable's storage slot: a local alloca, or an @extern global
        (locals shadow globals). Returns (pointer value, variable type)."""
        if name in self.locals:
            return self.locals[name]
        if name in self.globals:
            private, source = self.global_privacy[name]
            self.check_access(private, source, f"variable {name!r}", line)
            return self.globals[name]
        raise LangError(f"undefined variable {name!r}", line)

    def gen_addr(self, expr, line: int) -> tuple[ir.Value, LangType, int | None]:
        """Address of an lvalue expression: a variable, *deref, element, or
        struct field. Returns (pointer value, type pointed to, guaranteed
        alignment) -- the alignment is None when the address is naturally
        aligned for its type, 1 when it may not be (a field of a @packed
        struct, directly or through nesting)."""
        if isinstance(expr, Var):
            return (*self.var_addr(expr.name, line), None)
        if isinstance(expr, Unary) and expr.op == "*":
            tv = self.gen_expr(expr.operand)
            if not is_pointer(tv.type):
                raise LangError(f"cannot dereference a {tv.type}", line)
            return tv.value, tv.type.pointee, None
        if isinstance(expr, Index):
            return (*self.gen_index_addr(expr.base, expr.index, line), None)
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
                        line: int) -> tuple[ir.Value, LangType, int | None]:
        """Address of base.field / base->field; returns (pointer, field type,
        guaranteed alignment as in gen_addr)."""
        if arrow:
            base = self.gen_expr(base_expr)
            if not is_pointer(base.type):
                raise LangError(f"'->' requires a struct pointer, got {base.type}", line)
            owner, base_addr, base_align = base.type.pointee, base.value, None
        else:
            base_addr, owner, base_align = self.gen_addr(base_expr, line)
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
        return addr, ftype, align

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
            addr, lang_type, _ = self.gen_addr(expr.operand, expr.line)
            return TypedValue(addr, pointer_to(lang_type))
        tv = self.gen_expr(expr.operand)
        if expr.op == "*":
            if not is_pointer(tv.type):
                raise LangError(f"cannot dereference a {tv.type}", expr.line)
            return TypedValue(self.builder.load(tv.value), tv.type.pointee)
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
        if is_pointer(src) and is_pointer(target):
            return TypedValue(self.builder.bitcast(tv.value, target.ir), target)
        if is_pointer(src) and is_integer(target) and target.ir.width == 64:
            return TypedValue(self.builder.ptrtoint(tv.value, target.ir), target)
        if is_integer(src) and is_pointer(target):
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

    def gen_string(self, text: str) -> TypedValue:
        data = bytearray(text.encode("utf8") + b"\0")
        array_ty = ir.ArrayType(ir.IntType(8), len(data))
        glob = ir.GlobalVariable(self.module, array_ty, name=f".str.{self.str_count}")
        self.str_count += 1
        glob.linkage = "private"
        glob.global_constant = True
        glob.unnamed_addr = True
        glob.initializer = ir.Constant(array_ty, data)
        return TypedValue(self.builder.bitcast(glob, RAWPTR.ir), RAWPTR)

    def gen_call(self, expr: Call) -> TypedValue:
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

    def gen_direct_call(self, expr: Call, symbol: str) -> TypedValue:
        if expr.type_args:
            raise LangError(f"{expr.name!r} is not a generic function", expr.line)
        fn = self.funcs[symbol]
        ret, params, variadic = self.signatures[symbol]
        if len(expr.args) < len(params) or (len(expr.args) > len(params) and not variadic):
            raise LangError(
                f"{expr.name!r} expects {len(params)} argument(s), got {len(expr.args)}", expr.line
            )
        args = []
        for i, arg_expr in enumerate(expr.args):
            tv = self.gen_expr(arg_expr)
            if i < len(params):
                tv = self.coerce(tv, params[i], expr.line, f"argument {i + 1} of {expr.name!r}")
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
                    "cannot pass a struct to a variadic function; pass a pointer", expr.line
                )
            else:
                value = tv.value
            args.append(value)
        return TypedValue(self.builder.call(fn, args), ret)

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
        self.type_bindings = bindings
        self.current_source = func.source  # the signature may name private structs
        try:
            ret = self.lang_type(func.ret_type, func.line)
            params = [self.lang_type(t, func.line) for _, t in func.params]
            fnty = ir.FunctionType(ret.ir, [p.ir for p in params])
            fn = ir.Function(self.module, fnty, name=mangled)
            # Register before generating the body so recursive calls resolve.
            self.funcs[mangled] = fn
            self.signatures[mangled] = (ret, params, False)
            self.instances[key] = mangled
            saved = self.builder, self.locals, self.ret_type
            self.gen_function(func, fn, ret, params)
            self.builder, self.locals, self.ret_type = saved
        finally:
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
            if is_pointer(op_type):
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
