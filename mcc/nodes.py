"""AST node classes produced by the parser and consumed by codegen."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class TypeRef:
    """A type as written in source: base name, generic arguments, pointer
    depth. `struct array<T>**` is TypeRef("array", [TypeRef("T")], 2).

    A function-pointer type `fn(A, B) -> R` sets `params` and `ret` instead of
    `args`; `name` is "fn" and `stars` still applies for `fn(...) -> R*`."""
    name: str
    args: list["TypeRef"] = field(default_factory=list)
    stars: int = 0
    params: list["TypeRef"] | None = None  # set for fn(...) -> ret types
    ret: "TypeRef | None" = None

    def __str__(self) -> str:
        if self.params is not None:
            sig = "fn(" + ", ".join(str(p) for p in self.params) + ") -> " + str(self.ret)
            return sig + "*" * self.stars
        text = self.name
        if self.args:
            text += "<" + ", ".join(str(a) for a in self.args) + ">"
        return text + "*" * self.stars


@dataclass
class Program:
    imports: list[tuple[str, int]]  # (path, line); resolved and merged by the driver
    includes: list[str]
    structs: list["StructDecl"]
    functions: list["Func"]
    globals: list["ExternVar"]

@dataclass
class StructDecl:
    name: str
    type_params: list[str]  # generic type parameters, e.g. struct array<T>
    fields: list[tuple[str, TypeRef]]
    line: int
    private: bool = False  # @private: only usable within its source file
    static: bool = False  # @static: file-scoped name; other files may reuse it
    align: int | None = None  # @align(N): raised alignment, a power of two
    packed: bool = False  # @packed: no padding between fields; alignment 1
    volatile: bool = False  # @volatile: field accesses cannot be optimized away
    source: str | None = None  # defining file; stamped by the driver

@dataclass
class Func:
    name: str
    type_params: list[str]  # generic type parameters, e.g. fn sum<T>(...)
    params: list[tuple[str, TypeRef]]
    ret_type: TypeRef
    body: list
    line: int
    private: bool = False  # @private: only callable within its source file
    static: bool = False  # @static: file-scoped name; other files may reuse it
    extern: bool = False  # @extern: declaration only; defined elsewhere
    variadic: bool = False  # extern only: trailing `...`, C-style varargs
    source: str | None = None  # defining file; stamped by the driver

@dataclass
class ExternVar:  # @extern let name: type;  -- a global defined elsewhere
    name: str
    type_name: TypeRef
    line: int
    private: bool = False  # @private: only usable within its source file
    volatile: bool = False  # @volatile: accesses cannot be optimized away
    source: str | None = None  # declaring file; stamped by the driver

@dataclass
class Let:
    name: str
    type_name: TypeRef | None
    value: object | None  # None: `let x: T;`, declared but uninitialized
    line: int

@dataclass
class Assign:
    name: str
    value: object
    line: int

@dataclass
class Return:
    value: object | None
    line: int

@dataclass
class If:
    cond: object
    then: list
    otherwise: list
    line: int

@dataclass
class While:
    cond: object
    body: list
    line: int
    until: bool = False  # `until` loops run while the condition is false

@dataclass
class Break:
    line: int

@dataclass
class Continue:
    line: int

@dataclass
class Case:  # case (subject) { when v: ... else: ... } -- no fall-through
    subject: object
    arms: list  # (value expr, body statements) for each `when`
    otherwise: list  # the `else:` body, empty if absent
    line: int

@dataclass
class ExprStmt:
    expr: object
    line: int

@dataclass
class StoreDeref:  # *ptr = value;
    ptr: object
    value: object
    line: int

@dataclass
class StoreIndex:  # base[index] = value;
    base: object
    index: object
    value: object
    line: int

@dataclass
class StoreMember:  # base.field = value;  or  base->field = value;
    base: object
    field: str
    arrow: bool
    value: object
    line: int

@dataclass
class IntLit:
    value: int
    line: int

@dataclass
class CharLit:  # 'a', '\n', '\0' -- a one-byte uint8 constant
    value: int
    line: int

@dataclass
class FloatLit:
    value: float
    line: int

@dataclass
class BoolLit:
    value: bool
    line: int

@dataclass
class StrLit:
    value: str
    line: int

@dataclass
class NullLit:
    line: int

@dataclass
class Var:
    name: str
    line: int

@dataclass
class Call:
    name: str
    type_args: list[TypeRef]  # explicit generic arguments, e.g. sum<int32>(...)
    args: list
    line: int

@dataclass
class Unary:
    op: str
    operand: object
    line: int

@dataclass
class Cast:  # value as type
    value: object
    type_name: TypeRef
    line: int

@dataclass
class SizeOf:  # sizeof(type)
    type_name: TypeRef
    line: int

@dataclass
class Index:  # base[index]
    base: object
    index: object
    line: int

@dataclass
class Member:  # base.field  or  base->field
    base: object
    field: str
    arrow: bool
    line: int

@dataclass
class Binary:
    op: str
    lhs: object
    rhs: object
    line: int
