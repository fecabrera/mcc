# mcc

A small, modern-C-style language with generics, structs, and pointers,
compiled to native code (or JIT-executed) via [LLVM](https://llvm.org/)
using [llvmlite](https://llvmlite.readthedocs.io/). The compiler lives in
the [mcc/](mcc/) package, with one module per stage: lexer, parser, code
generator, and driver.

```c
import "std";

fn main() -> int32 {
    println("hello, world");

    return 0;
}
```

`println` comes from the [standard library](#standard-library); you can also
call libc's `printf` directly:

```c
import "libc/stdio";

fn main() -> int32 {
    printf("hello, world\n");

    return 0;
}
```

## Contents

- [Requirements](#requirements)
- [Install](#install)
- [Usage](#usage)
- [Quickstart](#quickstart)
- [Examples](#examples)
- [Standard library](#standard-library)
- [C ABI compatibility](#c-abi-compatibility)
- [Roadmap](#roadmap)
- [Language reference](docs/language.md) — the complete guide to every feature
- [Editor support](#editor-support)
- [Tests](#tests)
- [How it works](#how-it-works)

## Requirements

- Python 3.14 (see [Pipfile](Pipfile))
- llvmlite
- A C compiler on `PATH` (`cc`) for linking native executables

## Install

### Homebrew

```bash
brew tap fecabrera/mcc
brew install mcc
mcc program.mc --run
```

Or in one shot: `brew install fecabrera/mcc/mcc`.

If you have `HOMEBREW_REQUIRE_TAP_TRUST` set, run `brew trust fecabrera/mcc`
once after tapping.

### pip

The compiler is a regular Python package that installs an `mcc` command and
bundles the [standard library](libmc/README.md):

```bash
pip install git+https://github.com/fecabrera/mcc
mcc examples/helloworld.mc --run
```

### From source

For development, work in a checkout with [pipenv](https://pipenv.pypa.io/):

```bash
pipenv install
pipenv run python -m mcc examples/helloworld.mc --run
```

`pipenv run python -m mcc` and an installed `mcc` are interchangeable; the
examples below use the `mcc` command.

## Usage

```bash
mcc examples/helloworld.mc              # compile to a native executable
mcc examples/helloworld.mc -o hello     # choose the output name
mcc examples/helloworld.mc --run        # JIT-compile and run immediately
mcc examples/helloworld.mc --emit-llvm  # print the LLVM IR instead of compiling
mcc libmc/list.mc -c                      # compile to an object (.o), don't link
mcc libmc/list.mc --emit-interface        # write an importable .mci stub
mcc examples/helloworld.mc -O3          # optimization level (0-3, default 2)
mcc main.mc -I vendor -I deps           # extra import search paths
mcc main.mc --nostdlib                  # don't put libmc/ on the import path
mcc main.mc --target aarch64-unknown-none-elf   # cross-compile to an object file
mcc main.mc --general-regs-only         # never use FP/SIMD registers
```

| Option                    | Description                                                                                                                                                                                   |
| ------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `source`                  | The `.mc` file to compile (required). Its imports are resolved and compiled with it.                                                                                                          |
| `-o`, `--output FILE`     | Name of the generated file. Defaults to the source name without its extension (a native executable), or with a `.o` suffix when `--target` is given.                                          |
| `-c`, `--compile`         | Compile to an object file (`.o`) for the host and stop, without linking an executable. Defaults the output to the source name with a `.o` suffix.                                             |
| `--emit-interface`        | Write a [`.mci` interface stub](docs/language.md#interface-files) describing the file's public surface (to ship beside an object) and exit.                                                   |
| `-O 0`–`3`                | Optimization level, from `0` (none) to `3` (most aggressive). Default `2`.                                                                                                                    |
| `--run`                   | JIT-compile and run the program immediately instead of writing a file; its exit code becomes mcc's. Cannot be combined with `--target`.                                                       |
| `--emit-llvm`             | Print the generated LLVM IR to stdout and exit, without compiling or linking.                                                                                                                 |
| `-I`, `--import-path DIR` | Add a directory to the import search path. Repeatable; later paths are searched after earlier ones.                                                                                           |
| `--nostdlib`              | Do not put the bundled `libmc/` directory on the import path, dropping the standard library (for freestanding builds that supply their own).                                                    |
| `--target TRIPLE`         | Cross-compile for the given LLVM target triple, emitting an object file instead of a host executable.                                                                                         |
| `--general-regs-only`     | Generate code that uses only general-purpose registers, never the floating-point/SIMD ones.                                                                                                   |
| `--strict-align`          | Never emit unaligned memory accesses (gcc's `-mstrict-align`); needed for bare-metal targets running with the MMU off, where an unaligned wide load/store traps.                               |
| `--freestanding`          | Don't assume a hosted C library, so LLVM won't rewrite standard-named calls (e.g. `printf("…\n")` → `puts`) into symbols a bare-metal program never defines. The `-ffreestanding` equivalent. |
| `-D NAME[=VALUE]`         | Define a name for [`@if`](docs/language.md#conditional-compilation) conditions: `NAME` alone is `1`, `NAME=VALUE` sets an integer. Repeatable; a name with no `-D` reads as `0`.              |

`--target` accepts any LLVM triple and emits an object file instead of a
host executable; link it with that target's toolchain (e.g.
`aarch64-elf-gcc`). See [examples/baremetal/](examples/baremetal/) for a
freestanding kernel built this way.

`--general-regs-only` keeps generated code off the floating-point and SIMD
registers — the equivalent of gcc's `-mgeneral-regs-only`. It stops the
backend from quietly using a vector register (say, to copy a struct) in
code that must not touch FP state, such as a kernel or an interrupt
handler. Supported for aarch64, x86, and riscv targets.

`--strict-align` forbids the backend from emitting unaligned memory accesses —
the equivalent of gcc's `-mstrict-align`. It is needed for a bare-metal target
brought up with the MMU off: until paging is enabled all RAM is treated as
Device memory, where an unaligned wide load or store (which the backend would
otherwise merge or generate freely) traps as an alignment fault. Both feature
flags merge into the one `target-features` attribute LLVM honors per function,
so they compose.

`--freestanding` is the `-ffreestanding` equivalent: it tells LLVM there is
no hosted C library, so its optimizer won't recognize standard-named
functions and rewrite calls between them. At `-O2`, a `printf("done\n")`
(constant string, no args) is otherwise turned into a `puts` call, and
`printf("%c", c)` into `putchar` — synthesizing references to libc symbols a
bare-metal program never defines. Pass it when building a kernel or any
target with no libc. (`--nostdlib` only drops mcc's `libmc/` from the import
path; it does not change this optimizer assumption.)

## Quickstart

Write a `.mc` file and run it with `mcc file.mc --run`. A short taste of the
language — typed `fn`s, `let` with type inference, structs, a monomorphized
generic, `defer`, control flow, and the standard library:

```c
import "std";        // print / println, from the standard library

struct point {
    x: int32;
    y: int32;
}

// Generic and @inline: stamped out per call type, folded into the caller.
@inline fn max<T>(a: T, b: T) -> T {
    return a > b ? a : b;
}

fn main() -> int32 {
    let p = struct point { x = 3, y = 7 };

    let hi = max(p.x, p.y);     // type inferred: int32
    println("max = %d", hi);

    let i: int32 = 0;
    while (i < hi) {
        defer i = i + 1;            // runs at the end of every iteration
        if (i % 2 == 0) {
            println("%d is even", i);
        }
    }
    return 0;
}
```

That covers the basics; for the full language — generics, `defer`, block
expressions, pointers, compile-time `@if`, inline assembly, and the rest — see
the **[Language reference](docs/language.md)**.

## Examples

[examples/](examples/) is a runnable tour of the whole language — one
feature per file, from hello world through unsigned arithmetic and generics
to fizzbuzz and a prime sieve. See the [index](examples/README.md).

## Standard library

The modules under [libmc/](libmc/) are on the import search path by default, so
they import by bare name. For everyday output, `import "std";` provides `print`
and `println` — printf-style formatting, written in mcc on top of the libc
bindings:

```c
import "std";

fn main() -> int32 {
    println("answer = %d", 42);
    return 0;
}
```

Alongside `std` are `memory` (typed `alloc`/`dealloc`), the
`list`/`stack`/`queue`/`set`/`dict` containers, the `range` iterable, and the
`hashing/*` functions.

The [`libc/`](libmc/libc/) modules are instead `@extern` bindings for the C
library itself — `printf`, `malloc`, the `str*`/`mem*` functions, `FILE*`
streams, and so on — for when you want C directly; see
[Reaching libc](docs/language.md#reaching-libc). The [standard library index](libmc/README.md)
lists every module.

The standard library is **compiled from source** with each program. Shipping it
as a precompiled native library (`libmc.a`/`.so`, built by [build.sh](build.sh))
is **experimental**: the stdlib itself compiles cleanly into those archives, but
*linking a program against them* is not ready — some exported symbols (`errno`,
`crc32`, …) collide with system symbols, which needs
[namespaced exported symbols](#planned). Until then the precompiled archives
aren't linked.

## C ABI compatibility

mcc follows the platform C ABI for **scalars and pointers**, so any function
whose signature is built from them interoperates with C in both directions —
call C from mcc with `@extern`, or expose mcc functions to a C linker. This is
why the [libc bindings](libmc/libc/) work directly:

| mcc type | C type |
| --- | --- |
| `int8`–`int64`, `uint8`–`uint64` | `char`/`short`/`int`/`long`/`long long` (and `unsigned`) |
| `float64` | `double` |
| `T*` | `T *` |
| `va_list`, variadic `...` | `va_list`, varargs |

Generics don't change this: a generic is monomorphized to concrete types before
codegen, so an instantiation obeys the same ABI as a hand-written one.

**Structs passed or returned by value are not ABI-compatible yet.** mcc hands
LLVM the raw aggregate, but LLVM does not apply the platform ABI's
register/`byval`/`sret` classification automatically the way a C compiler does,
so a `struct` argument or return won't match a C function expecting the same
struct. Across the C boundary, pass a pointer (`struct point*`) instead. Fixing
this is [on the roadmap](#planned). (`bool` is `i1`; it matches C's 1-byte
`_Bool` inside structs but isn't strictly the `_Bool` parameter ABI — rarely a
concern in practice.)

## Roadmap

What the compiler does today, and what is planned next. Checked items are
implemented and covered by the [test suite](#tests); each links to its
reference section.

### Language

- [x] [Functions](docs/language.md#functions) — typed params, `->` return type, recursion,
      implicit `return 0` from `main`
- [x] [`@inline` functions](docs/language.md#functions) — LLVM `alwaysinline`, across files
- [x] [Variadic functions](docs/language.md#variadic-functions) — C's variadic arguments: `...`
      and `va_list` forwarding
- [x] [Generics](docs/language.md#generics) — monomorphized, on functions and structs
- [x] [Variables](docs/language.md#variables) — `let` with type inference
- [x] [Constants](docs/language.md#constants) — `const`, folded at compile time
- [x] [Conditional compilation](docs/language.md#conditional-compilation) — structured `@if`,
      including conditional `import`s
- [x] [Control flow](docs/language.md#control-flow) — `if`/`else`, `while`, `until`,
      `for … in`, `break`/`continue`, braceless bodies
- [x] [`defer`](docs/language.md#defer) — statement and block forms, reverse order
- [x] [Block expressions](docs/language.md#block-expressions) — `{ ...; emit v; }` as a
      value, with contained temporaries
- [x] [Types](docs/language.md#types) — `int8`–`int64`, `uint8`–`uint64` (with
      `byte` a transparent alias for `uint8`), `char`, `bool`, `float64`, `void`;
      untyped integer constants with range-checked adaptation
- [x] [`char`](docs/language.md#strings) — a distinct one-byte text type
      (ABI-compatible with `uint8`, but a separate type) so a NUL-terminated
      string is told apart from a raw byte buffer. `'a'` literals default to
      `char`, `"hi"` is a `char[N]`, and a `char[N]` borrows to a `slice<char>`
      that **drops the trailing NUL** (the text) — while a `uint8[N]` stays raw
      bytes whose `slice<uint8>` keeps every byte. A `char` literal adapts to a
      `uint8`/integer slot, but a `char` *value* needs an explicit `as`; `char*`
      coerces to `uint8*` like any pointer, so libc still takes string literals
- [x] [Operators](docs/language.md#operators) — arithmetic, comparison, logical
      (`and`/`or`/`!`), bitwise (`&` `|` `^` `<<` `>>` `~`), `cond ? a : b`
- [x] [Casts](docs/language.md#casts) — explicit `as`
- [x] [Pointers](docs/language.md#pointers) — address-of, deref, `null`
- [x] [Function pointers](docs/language.md#function-pointers)
- [x] [Arrays](docs/language.md#arrays) — fixed-size `T[N]` (`N` any constant
      expression), indexing, `len`, `sizeof`
- [x] [Structs](docs/language.md#structs) — `.`/`->` access, generics, struct
      literals (`struct point { x = 6, y = 4 }`, omitted fields zeroed or set to
      a field's `= default`, generic type arguments inferred from typed field
      values),
      `@packed`/`@align`/`@volatile`, `extends` (prefix specialization),
      struct value upcast
- [x] [Enums](docs/language.md#enums) — `enum Name[: type] { … }`, `Name::Member`
      constants over any underlying type, the name usable as a type
- [x] [Type aliases](docs/language.md#type-aliases) — `type <name> = <type>;`,
      transparent (e.g. `type callback = fn(int32, uint8**) -> int32;`)
- [x] [Imports](docs/language.md#imports) — bare-name resolution, search paths
- [x] [Visibility](docs/language.md#visibility) — `@private`, `@static`
- [x] [Extern declarations](docs/language.md#extern-declarations) — `@extern`, `@symbol`
- [x] [Strings](docs/language.md#strings) — string and char literals with C escapes
- [x] [Comments](docs/language.md#comments) — line, block, doc

### Standard library

- [x] Core — `memory` (typed `alloc`/`dealloc`), `std` (`print`/`println`)
- [x] Containers — `list`, `stack`, `queue`, `set`, `dict`, `string`
- [x] Iterables — `range` (half-open integer range for `for ... in`) and the
      shared `iteration/iterator` cursor the containers iterate through
- [x] Hashing — `splitmix64`, `fnv1a`, `murmur3`, `crc32`, `md5`
- [x] [libc bindings](docs/language.md#reaching-libc) — `stdio`, `stdlib`, `string`, `ctype`,
      `math`, `limits`, `float`, `time`, `errno`

### Tooling

- [x] Native compilation and linking
- [x] JIT execution (`--run`)
- [x] LLVM IR output (`--emit-llvm`)
- [x] Optimization levels `-O0`–`-O3`
- [x] Cross-compilation (`--target`), `--general-regs-only`, `--strict-align`,
      `--nostdlib`, `-I`
- [x] Separate compilation across files
- [x] Object-only compilation (`-c`) — emit a `.o` without linking
- [x] [Interface files](docs/language.md#interface-files) — `--emit-interface`
      writes a `.mci` stub (`@extern` prototypes plus full types/consts/generics)
      to ship beside an object; a bare `import` resolves to `.mc` then `.mci`
- [x] [Editor support](#editor-support) — VS Code syntax highlighting

### Planned

Grouped by scope.

#### Types and generics

- [ ] `typeof(expr)` — use an expression's static type in a type position,
      including in an alias: `type t = typeof(var);`
- [ ] Generic type parameters:
  - [ ] defaults — a declared fallback type parameter, on functions
        (`fn myfunc<T = uint8*>(x: T) { ... }`) and structs
        (`struct range<T = int64> { ... }`), used when a type argument isn't
        supplied or inferable from a *typed* value. The strongly-typed way to
        pick a default — declared at the definition, not guessed from an untyped
        literal at the use site (`let a = 0` and a no-anchor `struct range { … }`
        should stay ambiguous errors, not silently become `int32`)
  - [ ] bounds — constrain a parameter with `fn myfunc<T extends mystruct>(x: T)`
        (a struct and its `extends` specializations) or
        `fn myfunc<T in (t1, t2, ...)>(x: T)` (an explicit set of types)

#### Expressions and operators

- [ ] Compound assignment — `+= -= *= /= %= &= |= ^= <<= >>=`, where `x op= y`
      means `x = x op y` but evaluates the target `x` once (so the index/field of
      a complex lvalue like `arr[next()] += 1` is computed a single time)

#### Modules and imports

- [ ] Selective imports — `import { a, b, fnc } from "<path>";` to bring in only
      named declarations (today `import "<path>";` pulls in the whole module)
- [ ] Import aliasing — rename selected names with `as`:
      `import { a as _a, b, fnc } from "<path>";`

#### Structs, arrays, and data layout

- [ ] Struct ergonomics and C-layout interop:
  - [ ] flexible array members — a trailing `field: T[]` that adds 0 to
        `sizeof` and decays to a pointer at the struct's tail, for C structs
        like `linux_dirent64`'s `d_name[]` (today needs the `T[1]` "struct hack")
  - [ ] `offsetof(struct S, field)` — the byte offset of a field as a constant
        (today only `sizeof`, which includes trailing padding)
  - [ ] `alignof(T)` — the alignment requirement of a type as a constant, for
        laying out buffers and matching the C ABI (today only `sizeof`)
- [ ] Unions — `union Name { i: int64; f: float64; p: void*; }`, members
      sharing one storage (size of the largest, all at offset 0), for C-layout
      interop (`epoll_data`, `sigval`, most syscall structs embed a union) and
      type punning. The unsafe primitive under `any`
- [ ] `any` — a tagged union over the above: a union payload plus a
      `typeof`-checked type discriminant, so the live member is recovered safely
      (`case type`). The element type of the [variadic](#functions-and-methods)
      pack's `slice<any>`. Depends on unions and typeof/typeid
- [ ] `slice<T>` — a builtin non-owning view, `{ data: T*; length: uint64 }`, over a
      contiguous run of `T`: runtime `length`, indexing, and `for … in`. It borrows
      and never allocates, distinct from a C array `T[]` (which stays length-less
      and decays to a bare pointer for the ABI). A 2-word struct, so not C-ABI by
      value — across the C boundary pass `T*` plus a length instead. Built in
      dependency-ordered stages:
  - [x] **Stage 1 — the type + explicit borrows.** The builtin `slice<T>`
        (`{ data: T*; length: uint64 }`) with `.length` and indexing `s[i]`.
        Construction is an explicit `as` borrow from an owned `list<T>` or `T[N]` —
        `xs as slice<T>` reads `{data, length}` and drops `capacity` (a new "borrow"
        form of `as`, since `as` rejects struct casts today). Self-contained:
        depends on nothing else here.
  - [x] **Stage 2 — iteration.** `for … in` over a slice, its own builtin
        iteration (walking `ptr` from `0` to `length`), with no library
        `_it`/`_next` protocol. Depends on Stage 1.
  - [x] **Stage 3 — the element-const axis.** A `slice<const T>` is a read-only
        view (the element-mutability distinction, like Rust's `&[T]` vs
        `&mut [T]`): indexing yields a non-assignable element. A `list<T>`/`T[N]`
        borrows to a `slice<T>`; a `const` one borrows to a `slice<const T>`, so
        the conversion preserves immutability (and stays sound if `const` ever
        deepens to the pointee). A mutable `slice<T>` also widens implicitly to
        `slice<const T>`, so the read-only form is the common one — the place
        the variadic pack's `slice<const any>` and a format string's
        `slice<const uint8>` will land. Depends on Stage 1.
  - [x] **Stage 4 — borrowing in (literal adaptation).** A string literal
        **adapts** to a `slice<char>` (or `slice<const char>`) from context at
        compile time (length baked in, the NUL dropped; the buffer stays
        NUL-terminated so the same literal also serves as `char*`/`uint8*` for
        C), the way an untyped constant takes its type from context — at a
        function argument (including a `const`-by-hidden-reference slice
        parameter, so `writeln("hi")` just works), a `let` slot, or a `return`.
        Typed owned values still convert **explicitly** with `as` (Stage 1);
        only literals adapt. Depends on Stage 3.
- [ ] builtin `range` — fold the standard-library [`range<T>`](docs/language.md)
      into the compiler so a counting loop reads `for i in range(0, 5)` (or
      `for i in range(5)`, `start` defaulting to 0) instead of constructing a
      `struct range<T> { start = …, end = … }` and iterating `&r`. The bound type
      is inferred from the arguments (so `i` takes their integer width). Because
      the compiler owns the lowering, the loop is emitted directly — the counter's
      init/compare/step inline, with no range struct built, no `range_it`/
      `range_next` calls, and nothing to borrow — so the setup is done at compile
      time with no runtime footprint. Subsumes the `range` library module
- [ ] `tuple<A, B, ...>` — a builtin heterogeneous, fixed-arity product: each
      position keeps its own statically-known type, accessed by position (`t.0`,
      `t.1`). For multiple return values
      (`fn divmod(a: int32, b: int32) -> tuple<int32, int32>`) and ad-hoc
      grouping without a one-off struct. Distinct from `slice<any>`: a tuple
      keeps each element's static type and a compile-time arity, where erasing
      every slot to `any` would collapse into a fixed-length `slice<any>`. Also
      the door to a statically-typed variadic later (no erasure), if wanted
- [ ] `new T { ... }` sugar — desugars to a block that calls a user-defined
      `fn new<T>() -> T*`, writes a [struct literal](docs/language.md#structs)
      through the result, and emits the pointer:
  ```c
  let var = new T { ... };
  // desugars to
  let var = {
      let tmp = new<T>();
      *tmp = T { ... };
      emit tmp;
  };
  ```
  Every piece already works (block expressions, `new<T>` in
  [memory](libmc/memory.mc), struct literals, deref-assign, whole-struct copy),
  so the only remaining work is the surface-syntax rewrite into the block above
  — no new codegen.

#### Functions and methods

- [ ] Methods / OOP — `fn <struct>::<method>(self: <struct>*, ...)` definitions
      keyed to a struct, including a `constructor` and `@private` methods (the
      `for … in` protocol already dispatches by struct name to pave the way):
  ```c
  struct point { x: int32; y: int32; }
  fn point::constructor(self: struct point*, x: int32, y: int32) { ... }
  fn point::length2(self: struct point*) -> int32 { ... }
  @private fn point::helper(self: struct point*) { ... }
  ```
  - [ ] method-call sugar — `var->method(...)` desugars to
        `point::method(var, ...)`, passing the receiver as `self` (so `var` is a
        `struct point*`):
    ```c
    var->length2();   // desugars to point::length2(var)
    ```
  - [ ] `new <struct>(...)` sugar — desugars to a block that allocates with
        `new<<struct>>()`, runs the constructor, and emits the pointer (the
        constructor counterpart to the [`new T { ... }`](#structs-arrays-and-data-layout)
        literal sugar):
    ```c
    let var = new point(3, 4);
    // desugars to
    let var = {
        let tmp = new<struct point>();
        point::constructor(tmp, 3, 4);
        emit tmp;
    };
    ```
- [ ] `const` parameters — an immutable parameter (`fn f(const s: struct big)`)
  the callee promises not to mutate:
  - [x] pass by hidden reference: a large value (a struct) is passed by a hidden
        pointer instead of copied, so you get value semantics without
        hand-writing a pointer (see [const parameters](docs/language.md#const-parameters))
  - [ ] literal promotion: because the parameter is read-only, a literal
        argument can be promoted to its type at compile time. (For string
        formatting this is now done by a literal adapting to `slice<const uint8>`
        — see [`slice<T>`](#structs-arrays-and-data-layout) — so `println("{}", a)`
        needs no `struct string`.)
- [ ] Native variadic arguments — `fn f(args: slice<const any>)` (with
      `fn f(args...)` as sugar): a trailing `slice<const any>` parameter collects
      the call's extra arguments, so `f(x, a, b, c)` (after `f`'s fixed
      parameters) gathers `a, b, c` into `args`. The caller boxes each into a
      caller-stack [`any`](#structs-arrays-and-data-layout) and passes a read-only
      [`slice<const any>`](#structs-arrays-and-data-layout) over them —
      allocation-free.
      The callee walks it with `for a in args` and a
      `case type (a) { when int32 n: … else: … }` type-switch (the open `any`
      universe makes the `else` required). This is the runtime, type-erased
      variadic model (printf / `{}`-placeholder formatting); a statically-typed
      `tuple<…>` variant, processed by compile-time iteration, is a possible
      later path. Depends on any, slice, and typeof/typeid.
- [ ] C `va_arg` interop — read individual arguments from a C-ABI `va_list`
      in mcc (today a `va_list` can only be forwarded to a C `v*` function)
- [ ] `@noreturn` and `unreachable` — `@noreturn` marks a function that never
      returns (`exit`, `abort`, an infinite loop), so a call needs no dummy
      return after it and the backend drops the dead path; `unreachable` is a
      statement asserting a path is never reached (lowering to LLVM
      `unreachable`), for the fall-through of an exhaustive `case` or an
      impossible branch

#### Metaprogramming and builtins

- [ ] Compile-time macros:
  - [ ] macro functions — `@macro <name>(<args>) { ... }`, compile-time
        expansion (`@inline` already covers the call-overhead case)
  - [ ] `@define <name> = <value>` — a named compile-time substitution
- [ ] Bit-twiddling builtins — `byte_swap<T>` (`llvm.bswap`) and
      `bit_reverse<T>` (`llvm.bitreverse`) over the integer types
- [~] [Inline assembly](docs/language.md#inline-assembly) — arch-specific (pair with `@if` on
  `TARGET_ARCH`), preferring intrinsics where they exist:
  - [x] `@asm(...)` expression/block — an LLVM inline-asm call with an
        operand model (`$out`/`$N` operands, `=r`/`r` register class, `${N:w}`
        modifiers); output-less asm is implicitly volatile. Works on the host
        arch, including a same-arch cross `--target`
  - [x] `@asm fn` — sugar for a function whose body is one `@asm(...)`
        expression over its parameters: operands, register-allocated, no
        `ret` (the function's epilogue returns)
  - [x] `@clobbers("memory", "cc", "x0", ...)` — declare registers/flags the
        asm touches beyond its operands, right after `@asm`
  - [ ] pinning an operand to a fixed physical register (for
        syscalls/fixed-register instructions); foreign-arch cross-`--target`
        support
  - [ ] `@naked` — separate opt-in for no-prologue/epilogue functions
        (`_start`, interrupt entry, trampolines): args arrive in the ABI
        registers and the body writes its own `ret`

#### Standard library

- [ ] Formatted `print`/`println` — Rust/Python-style `{}` placeholders,
      type-driven (no `%`-letters), written in mcc over the
      [native variadic](#functions-and-methods) `slice<const any>`; enables
      compile-time format checking and per-struct `format` methods later. The
      signature is `fn println(format: slice<const uint8>, args: slice<const any>)`:
      a string literal adapts to `format` at the call site (so `println("{}", a)`
      works directly), and an owned `struct string` borrows in with
      `str as slice<uint8>` — both via the
      [`slice<T>`](#structs-arrays-and-data-layout) borrowing rules:
  - [ ] formatting over the `slice<const uint8>` format with bare/sequential and
        positional placeholders (`"{d} {f} {x} {s}"`, `"{0:d} {1:f} {2:x} {3:s}"`),
        parsed at runtime
  - [ ] format modifiers — precision and zero-padded width (`.Nf`, `Nx`, `0Nx`,
        `0x0Nx`, `Ns`, and `sN`), e.g. `{.8f}`, `{08x}`, `{0x08x}`, `{20s}`, `{s20}`

#### Tooling and C interop

- [ ] Linker passthrough — link against libraries and extra objects: `-l<name>`,
      `-L<dir>` library search paths, and forwarding object/library inputs to
      `cc` (today only `libm` is linked, and there is no `-l`/`-L`; `-c` plus a
      manual `cc` is the only route)
- [ ] Linker selection — `--linker=/path/to/ld` to pick a specific linker
      (today whatever the driver `cc` defaults to)
- [ ] Compiler-driver selection — `--cc=/path/to/cc` to choose the C driver used
      for linking (today the system `cc` on `PATH`)
- [ ] Library output — compile to a static (`.a`) or shared (`.so`/`.dylib`)
      library, paired with the `.mci` interface so consumers can link against it
- [ ] Namespaced exported symbols — emit mcc functions under a mangled/prefixed
      symbol (the `@extern` libc bindings keep their real names via `@symbol`),
      so a precompiled mcc library does not clash with libc/system symbols when
      linked. Required for shipping the standard library precompiled: today
      names like `errno` (a libSystem thread-local) and `crc32` (zlib) collide,
      so the stdlib is compiled from source instead of linked as `libmc`
- [ ] C header generation — emit a `.h` of the public surface (like
      `--emit-interface` does for `.mci`), so C code can call into an mcc library
- [ ] Assembly output — `--emit-asm` (`-S`) to write target `.s` assembly text
      (alongside `--emit-llvm` for IR and `-c` for an object), for inspection or
      handing to an external assembler
- [ ] C struct-passing ABI — classify by-value struct arguments and returns
      into registers/`byval`/`sret` per the platform ABI, so structs cross the
      C boundary correctly (today only scalars and pointers are ABI-compatible;
      see [C ABI compatibility](#c-abi-compatibility))

<!-- Add upcoming features here, e.g. - [ ] feature — short note -->

## Editor support

[editors/vscode/](editors/vscode/) is a VS Code extension that syntax-highlights
`.mc` files — keywords, the `intN`/`uintN`/`float64` types, `@`-annotations, and
string/char/number literals. Symlink it into your extensions folder and reload:

```bash
ln -s "$(pwd)/editors/vscode" ~/.vscode/extensions/mcc-language
```

See its [README](editors/vscode/README.md) for packaging and other install
options.

[editors/helix/](editors/helix/) brings the same to [Helix](https://helix-editor.com):
a `languages.toml` entry plus a [tree-sitter grammar](editors/helix/tree-sitter-mcc/)
for syntax highlighting, comment toggling, auto-indent, and text objects. See its
[README](editors/helix/README.md) for the install steps (add the language, copy
the queries, `hx --grammar fetch && build`).

## Tests

```bash
pipenv install --dev
pipenv run pytest
```

The suite in [tests/](tests/) covers each stage in isolation — token streams
([test_lexer.py](tests/test_lexer.py)), AST shapes and precedence
([test_parser.py](tests/test_parser.py)), emitted IR and compile-error
diagnostics ([test_codegen.py](tests/test_codegen.py)) — plus end-to-end
programs that JIT-compile in-process and assert on their real printf output
([test_execution.py](tests/test_execution.py)), and the command-line
interface as a subprocess ([test_cli.py](tests/test_cli.py)).

## How it works

The `mcc` package is a classic four-stage pipeline, one module per stage:

1. **[lexer.py](mcc/lexer.py)** — a single regex alternation turns source
   text into tokens, tracking line numbers for error messages.
2. **[parser.py](mcc/parser.py)** — recursive descent over the token
   stream, with precedence climbing for expressions, producing a dataclass
   AST (node classes live in [nodes.py](mcc/nodes.py)).
3. **[codegen.py](mcc/codegen.py)** — walks the AST and emits LLVM IR with
   `llvmlite.ir`: locals become `alloca` slots, strings become private global
   constants, control flow becomes basic blocks and branches, and generic
   functions are monomorphized into one LLVM function per instantiation.
4. **[driver.py](mcc/driver.py)** — resolves the `import` graph into a
   single merged program, then `llvmlite.binding` verifies the module, runs
   LLVM's pass pipeline at the requested `-O` level, and either executes
   `main` through MCJIT (`--run`) or emits an object file and links it with
   `cc`.

All compile errors are reported as `file: error: line N: message`.
