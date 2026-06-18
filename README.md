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
- [Examples](#examples)
- [Standard library](#standard-library)
- [Roadmap](#roadmap)
- [Language reference](#language-reference)
  - [Functions](#functions)
  - [Variadic functions](#variadic-functions)
  - [Generics](#generics)
  - [Variables](#variables)
  - [Constants](#constants)
  - [Conditional compilation](#conditional-compilation)
  - [Control flow](#control-flow)
  - [Defer](#defer)
  - [Block expressions](#block-expressions)
  - [Types](#types)
  - [Operators](#operators)
  - [Casts](#casts)
  - [Pointers](#pointers)
  - [Function pointers](#function-pointers)
  - [Arrays](#arrays)
  - [Structs](#structs)
  - [Imports](#imports)
  - [Visibility](#visibility)
  - [Extern declarations](#extern-declarations)
  - [Strings](#strings)
  - [Reaching libc](#reaching-libc)
  - [Comments](#comments)
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
bundles the [standard library](lib/README.md):

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
mcc examples/helloworld.mc -O3          # optimization level (0-3, default 2)
mcc main.mc -I vendor -I deps           # extra import search paths
mcc main.mc --naked                     # don't put lib/ on the import path
mcc main.mc --target aarch64-unknown-none-elf   # cross-compile to an object file
mcc main.mc --general-regs-only         # never use FP/SIMD registers
```

| Option | Description |
| --- | --- |
| `source` | The `.mc` file to compile (required). Its imports are resolved and compiled with it. |
| `-o`, `--output FILE` | Name of the generated file. Defaults to the source name without its extension (a native executable), or with a `.o` suffix when `--target` is given. |
| `-O 0`–`3` | Optimization level, from `0` (none) to `3` (most aggressive). Default `2`. |
| `--run` | JIT-compile and run the program immediately instead of writing a file; its exit code becomes mcc's. Cannot be combined with `--target`. |
| `--emit-llvm` | Print the generated LLVM IR to stdout and exit, without compiling or linking. |
| `-I`, `--import-path DIR` | Add a directory to the import search path. Repeatable; later paths are searched after earlier ones. |
| `--naked` | Do not put the bundled `lib/` directory on the import path, dropping the standard library (for freestanding builds that supply their own). |
| `--target TRIPLE` | Cross-compile for the given LLVM target triple, emitting an object file instead of a host executable. |
| `--general-regs-only` | Generate code that uses only general-purpose registers, never the floating-point/SIMD ones. |

`--target` accepts any LLVM triple and emits an object file instead of a
host executable; link it with that target's toolchain (e.g.
`aarch64-elf-gcc`). See [examples/baremetal/](examples/baremetal/) for a
freestanding kernel built this way.

`--general-regs-only` keeps generated code off the floating-point and SIMD
registers — the equivalent of gcc's `-mgeneral-regs-only`. It stops the
backend from quietly using a vector register (say, to copy a struct) in
code that must not touch FP state, such as a kernel or an interrupt
handler. Supported for aarch64, x86, and riscv targets.

## Examples

[examples/](examples/) is a runnable tour of the whole language — one
feature per file, from hello world through unsigned arithmetic and generics
to fizzbuzz and a prime sieve. See the [index](examples/README.md).

## Standard library

The modules under [lib/](lib/) are on the import search path by default, so
they import by bare name. For everyday output, `import "std";` provides `print`
and `println` — printf-style formatting, written in mcc on top of the libc
bindings:

```c
import "std";
fn main() -> int32 { println("answer = %d", 42); return 0; }
```

Alongside `std` are `memory` (typed `alloc`/`dealloc`), the
`array`/`stack`/`queue`/`set`/`dict` containers, and the `hashing/*` functions. The [`libc/`](lib/libc/) modules are
instead `@extern` bindings for the C library itself — `printf`, `malloc`, the
`str*`/`mem*` functions, `FILE*` streams, and so on — which you reach for when
you want C directly; see [Reaching libc](#reaching-libc). The
[standard library index](lib/README.md) lists every module.

## Roadmap

What the compiler does today, and what is planned next. Checked items are
implemented and covered by the [test suite](#tests); each links to its
reference section.

### Language

- [x] [Functions](#functions) — typed params, `->` return type, recursion,
      implicit `return 0` from `main`
- [x] [`@inline` functions](#functions) — LLVM `alwaysinline`, across files
- [x] [Variadic functions](#variadic-functions) — C's variadic arguments: `...`
      and `va_list` forwarding
- [x] [Generics](#generics) — monomorphized, on functions and structs
- [x] [Variables](#variables) — `let` with type inference
- [x] [Constants](#constants) — `const`, folded at compile time
- [x] [Conditional compilation](#conditional-compilation) — structured `@if`
- [x] [Control flow](#control-flow) — `if`/`else`, `while`, `until`,
      `for … in`, `break`/`continue`, braceless bodies
- [x] [`defer`](#defer) — statement and block forms, reverse order
- [x] [Block expressions](#block-expressions) — `{ ...; emit v; }` as a
      value, with contained temporaries
- [x] [Types](#types) — `int8`–`int64`, `uint8`–`uint64`, `bool`, `float64`,
      `void`; untyped integer constants with range-checked adaptation
- [x] [Operators](#operators) — arithmetic, comparison, logical
      (`and`/`or`/`!`), bitwise (`&` `|` `^` `<<` `>>` `~`)
- [x] [Casts](#casts) — explicit `as`
- [x] [Pointers](#pointers) — address-of, deref, `null`
- [x] [Function pointers](#function-pointers)
- [x] [Arrays](#arrays) — fixed-size `T[N]`, indexing, `len`, `sizeof`
- [x] [Structs](#structs) — `.`/`->` access, generics,
      `@packed`/`@align`/`@volatile`, `extends` (prefix specialization),
      struct value upcast
- [x] [Imports](#imports) — bare-name resolution, search paths
- [x] [Visibility](#visibility) — `@private`, `@static`
- [x] [Extern declarations](#extern-declarations) — `@extern`, `@symbol`
- [x] [Strings](#strings) — string and char literals with C escapes
- [x] [Comments](#comments) — line, block, doc

### Standard library

- [x] Core — `memory` (typed `alloc`/`dealloc`), `std` (`print`/`println`)
- [x] Containers — `array`, `stack`, `queue`, `set`, `dict`, `string`
- [x] Hashing — `splitmix64`, `fnv1a`, `murmur3`, `crc32`, `md5`
- [x] [libc bindings](#reaching-libc) — `stdio`, `stdlib`, `string`, `ctype`,
      `math`, `limits`, `float`, `time`, `errno`

### Tooling

- [x] Native compilation and linking
- [x] JIT execution (`--run`)
- [x] LLVM IR output (`--emit-llvm`)
- [x] Optimization levels `-O0`–`-O3`
- [x] Cross-compilation (`--target`), `--general-regs-only`, `--naked`, `-I`
- [x] Separate compilation across files
- [x] [Editor support](#editor-support) — VS Code syntax highlighting

### Planned

- [ ] Methods / OOP — `<struct>::<method>` definitions (the `for … in`
      protocol already dispatches by struct name to pave the way)
- [ ] Constant-expression array sizes — `T[N]` where `N` is any constant
      expression (today only a literal, a single `const` name, or `[]`)
- [ ] Macro functions — compile-time expansion (`@inline` already covers the
      call-overhead case)
- [ ] Inline assembly — `asm` blocks for emitting raw instructions
- [ ] Full C-style variadic support — C's `va_arg` to read individual arguments in mcc
      (today a `va_list` can only be forwarded to a C `v*` function)
- [ ] mcc-native variadic arguments — a type-safe, mcc-specific variadic
      mechanism, distinct from C's ABI-compatible `...`

<!-- Add upcoming features here, e.g. - [ ] feature — short note -->

## Language reference

### Functions

Functions are declared with `fn`. Parameters are typed, and the return type
follows `->`; omitting it means `void`. `main` gets an implicit `return 0`
if it falls off the end.

```c
fn add(a: int32, b: int32) -> int32 {
    return a + b;
}

fn greet() {
    println("hi");
}
```

Recursion works:

```c
fn fib(n: int32) -> int32 {
    if (n < 2) {
        return n;
    }
    return fib(n - 1) + fib(n - 2);
}
```

`@inline` asks for a function's body to be folded into each of its call
sites (LLVM's `alwaysinline`). It is a small generic-friendly helper's best
friend — the call overhead disappears and the optimizer sees through to the
arguments:

```c
@inline fn min(a: int64, b: int64) -> int64 {
    if (a < b) { return a; }
    return b;
}
```

The inliner runs as part of optimization, so the folding happens at `-O1`
and above; at `-O0` an `@inline` function is emitted normally and called
like any other. It works across separately compiled files too: an imported
definition is copied into every object that uses it (the same mechanism that
lets generics and `@static` cross files), so the body is present to inline
wherever it is called. `@inline` needs a body, so it cannot combine with
`@extern`.

`@inline` on its own forces the inlining — unlike C, there is no need for the
`static inline` idiom. That idiom works around C's `inline` linkage rules
(a plain `inline` emits no out-of-line definition, so an un-inlined call
becomes an "undefined reference"); mcc has no such rule, since imported
bodies are copied into each object. `@static` is therefore orthogonal: it
only makes the helper file-private (so it leaks no global symbol and its
spare out-of-line copy can be dead-stripped), and has no bearing on whether
the inlining happens. Combine them as `@static @inline` when you want both.

### Variadic functions

A trailing `...` after at least one named parameter makes a function
variadic. These are **C's variadic arguments** — the same ABI-compatible
mechanism C uses — so the form works both in
[`@extern` declarations](#extern-declarations) (C's `printf`) and in
functions you define. A defined variadic function can
**forward** its extra arguments to a C `v*` function (`vsnprintf`,
`vfprintf`, …) through a `va_list`:

```c
import "libc/stdio";   // @extern fn vsnprintf(..., args: va_list) -> int32;

fn logf(fmt: uint8*, ...) -> int32 {
    let buf: uint8[256];
    let ap: va_list;
    va_start(ap, fmt);                       // ap, then the last named param
    let n = vsnprintf(&buf[0], 256, fmt, ap);
    va_end(ap);
    puts(&buf[0]);
    return n;
}

logf("%s = %d (0x%X)", "answer", 42, 255);   // answer = 42 (0xFF)
```

`va_list` is the C argument-cursor type; `va_start(ap, last)` initializes it
(naming the parameter just before the `...`), and `va_end(ap)` releases it.
Its layout is platform-specific, so `va_list` is opaque — you can hand it to
a function but not read individual arguments from it in mcc (there is no
`va_arg`); let a C `v*` function consume it. The right layout is chosen for
the target (it works for x86-64, arm64/aarch64, and Apple arm64).

### Generics

Functions can take type parameters, declared in `<...>` after the name and
usable anywhere a type is expected:

```c
fn sum<T>(a: T, b: T) -> T {
    return a + b;
}

fn main() -> int32 {
    let a: uint8 = sum<uint8>(1, 2);   // explicit instantiation
    let x: int64 = 9000000000;
    let y: int64 = sum(x, 1);          // T inferred from the arguments
    return 0;
}
```

Generics compile by monomorphization: each distinct set of type arguments
stamps out its own specialized function (`sum<uint8>`, `sum<int64>`, ...),
generated on first use and reused after that — there is no boxing or runtime
dispatch. When type arguments are omitted, they are inferred from the
argument types (variables take priority over literals), and typed arguments
that disagree are an error: `conflicting types for type parameter T`.
Generic functions can call themselves recursively. See
[examples/generics.mc](examples/generics.mc).

Generic functions with the same name form an _overload set_, dispatched by
parameter pattern — a call picks the most specific viable variant (`T*`
beats `T`, `box<T>*` beats both). This is how libraries specialize by type
shape: [lib/hash.mc](lib/hash.mc) hashes integer keys by value (splitmix64)
and pointer keys by content (FNV-1a), and [lib/set.mc](lib/set.mc) simply
calls `hash(key)`:

```c
fn hash<T>(key: T) -> uint64 { return splitmix64(key); }
fn hash<T>(key: T*) -> uint64 { return fnv1a(key); }
```

Imported files can extend an overload set with new variants. Two equally
specific viable variants make the call ambiguous — a compile error.

### Variables

`let` declares a variable; the type is inferred from the initializer when
the initializer already has a definite type. A bare integer constant does
not -- it must be given a type with an annotation or a cast, so declarations
are never ambiguous. Assignment uses plain `=`.

```c
let x: int64 = 0;       // annotated
let y = 0 as int64;     // or typed by a cast
let z = 0;              // error: type of 'z' is ambiguous

let pi = 3.14;          // fine: float64 (the only float type)
let ok = true;          // fine: bool
let w = x + 1;          // fine: int64, from x
x = x + 1;
```

A declaration may omit the initializer if it has a type annotation. Like a
C local, the variable holds garbage until assigned — reading it first is
undefined:

```c
let n: int32;           // declared, not yet initialized
if (fancy()) { n = 1; } else { n = 2; }

let p: struct point;    // works for structs too: fill in the fields
p.x = 4;
p.y = 2;
```

Variables are block-scoped, as in C: a `let` is visible only until the end of
its enclosing `{ }` — including the body of an `if`/`else` branch or a
`while`/`until` loop — so sibling and sequential blocks can reuse a name. An
inner block may **shadow** a variable from an outer one; the outer binding
returns when the block ends. Redeclaring a name in the _same_ block is an
error.

```c
let x: int32 = 1;
if (cond) {
    let x: int32 = 2;   // shadows the outer x, only within this block
    use(x);             // 2
}
use(x);                 // 1 again

while (i < n) {
    let row = grid[i];  // fresh each iteration; not visible after the loop
    i = i + 1;
}
```

A bare `{ }` is a statement too, so you can open a scope anywhere — handy for a
short-lived local (and its `defer`) without leaking it into the rest of the
function:

```c
{
    let tmp = alloc<uint8>(64);
    defer dealloc(tmp);
    fill(tmp);
}   // tmp is freed and out of scope here
```

### Constants

`const` declares a named compile-time constant — mcc's answer to C's
`#define NAME value`, but typed and scoped rather than textual. It has **no
storage**: each use is folded in at compile time.

```c
const DEBUG = 1;             // untyped int: adapts like a literal
const MAX_USERS: uint64 = 1024;
const GREETING = "hello";

let buf: int32[MAX_USERS];   // an integer const can size an array
```

The initializer must be a constant expression — literals, other constants,
`sizeof`, casts, and integer/float arithmetic — evaluated when the program
is compiled:

```c
const WIDTH  = 80;
const HEIGHT = 24;
const CELLS  = WIDTH * HEIGHT;       // 1920, folded
const ROW_BYTES = WIDTH * sizeof(int32);
```

An untyped integer const stays _adaptable_ like a literal, so it takes on
whatever integer type the context needs (`uint64`, `int32`, …) without a
cast. Add an annotation (`const N: uint8 = 4;`) to pin the type. Constants
follow the same [visibility](#visibility) rules as other declarations:
file-scoped names are shared across the program, and `@private` keeps one to
its file. Assigning to a const, or using a non-constant initializer, is a
compile error.

#### Target facts

The compiler predefines two integer constants describing the target it is
building for, derived from the [target triple](#usage) (the host triple when
no `--target` is given):

| Constant      | Values                                                         |
| ------------- | -------------------------------------------------------------- |
| `TARGET_OS`   | `OS_DARWIN`, `OS_LINUX`, `OS_WINDOWS`, `OS_NONE`, `OS_UNKNOWN` |
| `TARGET_ARCH` | `ARCH_X86_64`, `ARCH_AARCH64`, `ARCH_RISCV64`, `ARCH_UNKNOWN`  |

The `OS_*`/`ARCH_*` names are constants too, so code can branch on them to pick
platform-specific bindings — for instance, the linker symbol behind a libc
stream (see [`@symbol`](#extern-declarations)):

```c
@extern @symbol("__stdoutp") let macos_stdout: struct FILE*;   // when TARGET_OS == OS_DARWIN
@extern @symbol("stdout")    let linux_stdout: struct FILE*;   // when TARGET_OS == OS_LINUX
```

`OS_NONE` is a freestanding target with no operating system: a bare-metal
triple like `aarch64-unknown-none-elf` reports `TARGET_OS == OS_NONE` and
`TARGET_ARCH == ARCH_AARCH64`. Such code uses no libc, so it never needs the
stream symbols above — but `TARGET_ARCH` still lets a kernel select
architecture-specific code (MMIO addresses, register layouts). These names are
reserved: a user `const` may read them but not redefine them.

### Conditional compilation

`@if` selects code at compile time, the way C's `#if` does — but it is
_structured_, not textual: each branch is a real brace-delimited block of the
surrounding grammar, not an arbitrary span of tokens. Only the live branch is
compiled; the dead branch is parsed (so it must be syntactically valid) but
never type-checked or emitted.

The condition is a constant expression over the [target facts](#target-facts) —
`TARGET_OS`, `TARGET_ARCH`, and the `OS_*`/`ARCH_*` constants — with
comparisons, `and`/`or`/`!`, and integer arithmetic. A nonzero result is true.

It works at the top level, to select whole declarations — the intended use is
binding a symbol that differs by platform (see [`@symbol`](#extern-declarations)):

```c
struct FILE {}

@if (TARGET_OS == OS_DARWIN) {
    @extern @symbol("__stdoutp") let stdout: struct FILE*;
} @else @if (TARGET_OS == OS_LINUX) {
    @extern @symbol("stdout")    let stdout: struct FILE*;
} @else {
    @extern let stdout: struct FILE*;
}
```

and as a statement, to select code inside a function. As a statement it does
_not_ open a scope — the chosen statements are spliced in inline, so a binding
they declare is visible afterwards:

```c
fn page_size() -> uint64 {
    @if (TARGET_ARCH == ARCH_AARCH64) {
        let size = 16384 as uint64;     // Apple silicon
    } @else {
        let size = 4096 as uint64;
    }
    return size;
}
```

`@else @if` chains, and blocks may nest. `import` is not allowed inside an `@if`
(imports must precede all declarations). Note `@if`/`@else` are compile-time and
distinct from the runtime `if`/`else`.

### Control flow

```c
if (x > 10) {
    println("big");
} else if (x > 5) {
    println("medium");
} else {
    println("small");
}

while (x < 10) {
    x = x + 1;
}

until (x == 0) {     // inverse of while: stops when the condition is true
    x = x - 1;
}

while (true) {
    x = next();
    if (x == 0) { continue; }   // skip to the next iteration
    if (x < 0)  { break; }      // leave the loop
    handle(x);
}
```

Conditions accept `bool` or any integer (compared against zero, as in C).
A body that is a single statement does not need braces:
`if (x > 10) return x;`
`break` and `continue` apply to the innermost enclosing loop.

`for x in obj` iterates anything that supplies the **`_it`/`_next` protocol** —
a pair of functions named after the iterable's struct, which the compiler
dispatches by name. For an `obj` of type `struct array<T>` it calls `array_it`
and `array_next`:

```c
fn array_it<T>(self: struct array<T>*) -> struct array_iter<T>;   // make a cursor
fn array_next<T>(it: struct array_iter<T>*, out: T*) -> bool;      // false when done
```

```c
for v in &nums {            // nums: array<int32>; v is int32, inferred from array_next
    if (v < 0) { continue; }
    if (v > 99) { break; }
    use(v);
}
```

The element type of `x` is inferred from `<struct>_next`'s out-parameter; `x` is
scoped to the loop and `break`/`continue` work as usual. It lowers to
`{ let it = <struct>_it(obj); while (<struct>_next(&it, &x)) { ... } }` with the
iterator held as a hidden, collision-proof temporary. Define `<struct>_it` and
`<struct>_next` for your own types to make them iterable — name dispatch means
no overloading is needed, and a struct built with [`extends`](#structs) can
reuse its base's by forwarding through an upcast. [lib/array.mc](lib/array.mc),
[lib/set.mc](lib/set.mc), [lib/dict.mc](lib/dict.mc), and
[lib/string.mc](lib/string.mc) all do this; see
[examples/iteration.mc](examples/iteration.mc).

`case` matches a value against a series of `when` arms, with an optional
`else:` default. The subject is evaluated once, and there is **no
fall-through** — a matching arm runs only its own statements and then the
`case` is done:

```c
case (c) {
    when 'a':           handle_a();
    when 'b':           handle_b();   // arms hold any number of statements
    when '0', '1', '2': handle_digit();   // an arm matches any listed value
    else:               handle_other();
}
```

A `when` arm may list several comma-separated values and matches if the
subject equals **any** of them. Each value may be any expression of the
subject's type (untyped constants adapt to it), and the subject can be any
type comparable with `==` — integers, `uint8` characters, pointers, `bool`,
or `float64`.
`break` and `continue` inside an arm act on the enclosing loop, not the
`case`; the no-fall-through semantics mean `break` is never needed to end
an arm.

### Defer

`defer` schedules a statement (or a `{ }` block) to run when the enclosing
block exits — by _any_ path: falling off the end, a `return`, or a
`break`/`continue` out of a loop. It keeps a resource's release next to its
acquisition, so cleanup can't be forgotten on an early exit:

```c
fn process() -> int32 {
    let buffer: uint8* = alloc<uint8>(4096);
    defer dealloc(buffer);          // freed however this function returns

    if (bad()) {
        return -1;                  // buffer is still freed
    }
    use(buffer);
    return 0;                       // and here too
}
```

Multiple defers run in **reverse order** (last deferred, first to run), so
resources unwind in the opposite order they were acquired. The block form
groups several actions:

```c
let a = open(...);
defer close(a);
let b = open(...);
defer close(b);
defer {                            // runs first: close(b), then close(a)
    flush();
    sync();
}
```

A defer is tied to the block it appears in, so one inside an `if` or a loop
body fires at the end of that block — each loop iteration runs its own. The
deferred code is evaluated when it runs, not when it is scheduled, so it sees
the latest values of the variables it names (unlike Go, which snapshots the
arguments). A returned value is computed _before_ the defers run, so freeing a
buffer in a defer can't clobber what you return. See
[examples/defer.mc](examples/defer.mc).

### Block expressions

A `{ ... }` in expression position is a **block expression**: it runs its
statements in their own scope and yields a value with `emit`. Think of it as
an inlined, single-use, anonymous function — temporaries declared inside stay
inside, and only the `emit`ted value escapes:

```c
let value: uint64 = {
    let hi = read_hi() as uint64;
    let lo = read_lo() as uint64;
    emit (hi << 32) | lo;
};
// hi and lo don't exist out here; only `value` does
```

`emit` is to a block expression what `return` is to a function — it fills in
the block's value and jumps to the block's end. The two are orthogonal:
`emit` targets the nearest enclosing block expression, `return` always leaves
the whole function, and `break`/`continue` still act on the enclosing loop. So
a `return` inside a block expression exits the function, not just the block.

A block expression's type is the type of what it emits, and — like a function
that must `return` on every path — it must `emit` on every path that can reach
its end. An exhaustive `if`/`else` (or a `case` with an `else`) where every arm
emits is enough; otherwise a trailing `emit` supplies the fall-through value:

```c
let label: uint8* = {
    if (n < 0) emit "negative";
    else if (n == 0) emit "zero";
    else emit "positive";          // every path emits -- no trailing one needed
};
```

`emit` is only valid inside a block expression; using it in a plain block
statement or at a function's top level is an error (use `return` there). The
trivial `{ emit e; }` is just `e`, so an untyped constant still adapts to its
context (`let n: uint64 = { emit 1; };`). Any `defer` inside runs when the
block yields, before the value leaves — exactly as a function's defers run
before its `return`.

### Types

| Type                                                  | LLVM equivalent                                                   |
| ----------------------------------------------------- | ----------------------------------------------------------------- |
| `int8`, `int16`, `int32`, `int64`                     | `i8`, `i16`, `i32`, `i64` (signed)                                |
| `uint8`, `uint16`, `uint32`, `uint64`                 | `i8`, `i16`, `i32`, `i64` (unsigned)                              |
| `bool`                                                | `i1`                                                              |
| `float64`                                             | `double`                                                          |
| `T*` (any type + `*`s)                                | pointer                                                           |
| `T[N]` (fixed-size [array](#arrays))                  | `[N x T]`                                                         |
| `fn(A) -> R` ([function pointer](#function-pointers)) | `R (A)*`                                                          |
| `void`                                                | `void` (return type only; `void*` is not allowed -- use `uint8*`) |

Literals with a decimal point are `float64` and `true`/`false` are `bool`.
Integer literals are written in decimal or hexadecimal (`0xFF`), and are
_untyped constants_: they adapt to the integer type
they are used with as long as the value fits (so `let x: uint64 = 5;` and
`x % 7` both work; `let y: uint8 = 300;` is a compile error). Where no
context provides a type -- most notably `let` without an annotation -- an
untyped constant is a compile error rather than silently becoming `int32`.
Constant integer arithmetic folds at compile time and stays untyped
(`10 * sizeof(int64)` is a `uint64` because `sizeof` is typed; `2 + 3` is
still untyped). There are no other implicit conversions: operands of a
binary operator must have the same type.

Signedness changes behavior, not representation: unsigned types use unsigned
division, remainder, and comparisons, and zero-extend instead of sign-extend
when promoted. Unary `-` is not allowed on unsigned values. See
[examples/unsigned.mc](examples/unsigned.mc).

### Operators

By descending precedence: unary `-` `~` `!` `*` `&`, `as` casts, then `*` `/`
`%`, `+` `-`, shifts `<<` `>>`, bitwise `&`, `^`, `|`, comparisons
`<` `<=` `>` `>=`, `==` `!=`, then `and`, and loosest of all `or`.
Comparisons yield `bool`; `%` and the bitwise/shift operators are
integer-only. `>>` is an arithmetic shift for signed types and logical for
unsigned. Unary `~` is bitwise complement (integer-only); `!` is logical
NOT on a `bool`. Unlike C, bitwise operators bind tighter than comparisons,
so `a & 4 == 4` means `(a & 4) == 4`. Integer constant expressions fold at
compile time.

`and` and `or` are the logical operators (there is no `&&` / `||`). They
short-circuit — the right side is evaluated only when the left does not
already decide the result — take a `bool` or integer on each side (non-zero
is true, as in a condition), and yield a `bool`. They bind looser than
comparisons, so parentheses are usually unnecessary:

```c
if (a > 0 or a < 0 and b < 0) { ... }   // a > 0 or (a < 0 and b < 0)
if (p != null and p->ready) { ... }     // p->ready read only when p != null
```

### Casts

`expr as type` converts explicitly (there are no implicit conversions
between variables):

```c
let a: int32 = 300;
let b = a as uint8;        // truncates: 44
let c = a as int64;        // sign-extends (zero-extends from unsigned types)
let d = a as float64;      // 300.0
let e = 3.99 as int32;     // truncates toward zero: 3
let p = malloc(4) as int32*;  // pointer casts
let n = p as uint64;       // pointer <-> integer
```

`as` binds tighter than binary operators: `a + b as int64` is
`a + (b as int64)`.

### Pointers

`T*` is a pointer to `T`. `&x` takes a variable's address, `*p` dereferences
(both to read and to assign), and `p[i]` indexes:

```c
fn bump(p: int32*) {
    *p = *p + 1;
}

fn main() -> int32 {
    let x = 41;
    bump(&x);                                  // x is now 42

    let nums = malloc(5 * sizeof(int32)) as int32*;
    nums[0] = 7;
    free(nums);
    return 0;
}
```

`sizeof(type)` is a compile-time `uint64` constant (pointers are 8 bytes).
`uint8*` doubles as the raw-memory pointer (C's `void*`): any pointer
implicitly coerces to it, which is why `free(nums)` works without a cast.
String literals have type `uint8*`, so `"hi"[0]` is the byte `104`. There is
no pointer arithmetic (`p + 1`); use `&p[1]`. See
[examples/pointers.mc](examples/pointers.mc) and
[lib/memory.mc](lib/memory.mc) for a generic typed allocator.

### Function pointers

`fn(A, B) -> R` is the type of a pointer to a function taking `A, B` and
returning `R` (a missing `-> R` means `void`, as in a declaration). A bare
function name — written without the call parentheses — is a value of that
type, so functions can be stored in variables and struct fields, passed as
arguments, and returned:

```c
fn add(a: int32, b: int32) -> int32 { return a + b; }
fn sub(a: int32, b: int32) -> int32 { return a - b; }

fn apply(op: fn(int32, int32) -> int32, x: int32, y: int32) -> int32 {
    return op(x, y);            // call through the parameter
}

fn main() -> int32 {
    let op: fn(int32, int32) -> int32 = add;
    op = sub;                                    // reassignable
    return apply(op, 10, 3) + apply(add, 1, 1);
}
```

The signature must match exactly — `add` does not fit a `fn(int32) -> int32`.
`null` is a valid function pointer and they compare with `==` / `!=`, so an
optional callback works:

```c
if (cb != null) { cb(x); }
```

Any expression of function-pointer type is callable, not just a variable —
a struct field, an array element, or the result of another call:

```c
widget->on_click(x);   // a callback stored in a struct
table[i](x);           // an entry in a dispatch table
chooser()(x);          // the function a call returned
```

In a type, `*` and `[N]` bind to the return type, so `fn(int32) -> int32*`
is a function returning `int32*`. Group with parentheses to bind them
outside the function type instead — `(fn(int32) -> int32)*` is a pointer to
a function pointer, and `(fn(int32) -> int32)[N]` is an array of `N`
function pointers (a dispatch table):

```c
fn add(a: int32, b: int32) -> int32 { return a + b; }
fn mul(a: int32, b: int32) -> int32 { return a * b; }

@static let ops: (fn(int32, int32) -> int32)[] = [add, mul];

fn main() -> int32 {
    return ops[0](2, 3) + ops[1](2, 3);   // 5 + 6
}
```

A function name folds to a constant address, so a `@static` table can be
initialized with one at compile time.

A function value is a pointer underneath, so it casts like one: `add as
uint64` is the function's address as an integer, `addr as fn(...) -> R`
turns an address back into a callable pointer, and it bitcasts to/from a
data pointer such as `uint8*`.

Only a single, non-generic function has an address; a generic name like
`id` cannot be used as a value (there is no one instance to point at).

### Arrays

`T[N]` is a fixed-size array of `N` elements, laid out inline. A local one
is stack-allocated; `@static` makes a zero-initialized file-scoped buffer.
Index with `[]`, and `sizeof` reports the whole array's bytes:

```c
fn main() -> int32 {
    let squares: int32[5];                 // five int32s on the stack
    let i: int32 = 0;
    while (i < 5) { squares[i] = i * i; i = i + 1; }
    return squares[4];                      // sizeof(int32[5]) == 20
}
```

An array literal `[a, b, c]` (a trailing comma is allowed) initializes an
array, nesting for more dimensions. The outermost dimension can be left as
`[]` and is inferred from the literal's length:

```c
let primes: int32[] = [2, 3, 5, 7, 11];          // length inferred as 5
let grid: int32[2][2] = [[1, 2], [3, 4]];        // nested
```

A local literal's elements may be any expressions; a `@static` one must be a
constant expression — literals, `const` references, `sizeof`, `as` casts, or
arithmetic — so a lookup table lives in read-only data:

```c
@static let cmds: uint8*[][2] = [
    ["help", "show this help"],
    ["quit", "exit the program"],
];
```

`len(arr)` is the element count — a compile-time constant, handy as a loop
bound and the way to read a size you let `[]` infer. It adapts to its
context like a literal, so it compares against any integer counter (`int32`
or `uint64`) without a cast. For a multi-dimensional array, `len(grid)` is
the outer length and `len(grid[0])` the inner one:

```c
let i: int32 = 0;
while (i < len(cmds)) { use(cmds[i]); i = i + 1; }
```

Like C, an array decays to a pointer to its first element wherever a value
is used — so it passes to a `T*` parameter and `&arr[i]` gives an element
address — and there is no whole-array assignment or copy. Arrays work as
struct fields. In a type, `*` binds to the element, so `int32*[8]` is an
array of eight pointers; group for the other order. Each `N` must be a
positive integer literal (`[]` only as the inferred outermost dimension).

### Structs

`struct` declares an aggregate type; fields use the same `name: type;` form
as everything else, and structs can be generic. In type positions the
`struct` keyword is optional. `->` accesses a field through a pointer, `.`
accesses a field of a struct value, and `&` takes field addresses. `null` is
an untyped pointer constant that adapts to any pointer type, and pointers
compare with `==` / `!=`.

```c
struct point {
    x: int32;
    y: int32;
}

struct node<T> {            // generic; monomorphized like functions
    value: T;
    next: struct node<T>*;  // self-reference through a pointer
}

fn main() -> int32 {
    let p = alloc<struct point>(1);
    p->x = 3;
    let copy = *p;          // dereferencing copies the struct
    let n = copy.x + 1;
    bump(&p->x);            // address of a field
    if (p != null)
        dealloc(p);
    return 0;
}
```

`@align(N)` raises a struct's alignment to `N` bytes — a power of two; asking
for less than the natural alignment is an error. `sizeof` rounds up to a
multiple of the alignment, and field offsets and array strides stay
consistent with it, including when an aligned struct is nested inside
another:

```c
@align(64)
struct counter {     // sizeof is 64: one per cache line
    hits: uint64;
}
```

`@packed` is the opposite: it removes the padding between fields, placing
them at consecutive byte offsets, and drops the struct's alignment to 1 —
the layout for wire formats and file headers. Member accesses are compiled
as unaligned, but (as in C) taking a pointer _into_ a packed struct with
`&` and dereferencing it elsewhere is unsafe. `@packed` combines with
`@align(N)`, which then sets the overall alignment and rounds `sizeof`
back up:

```c
@packed
struct header {      // sizeof is 9, not 16
    tag: uint8;
    length: uint64;
}
```

`@volatile` marks a struct whose loads and stores must all happen exactly
as written — the optimizer may not elide, merge, or hoist them. This is for
memory-mapped hardware registers, where reading or writing _is_ the side
effect; it propagates through nested fields, and also applies to `@extern`
variables:

```c
@volatile
struct pl011 {       // a UART's register block; see examples/baremetal/
    dr: uint32;      // data register: write a byte to transmit
    ...
}
```

A struct can `extends` another to reuse its layout. The base's fields are
placed **first**, followed by the new struct's own, so the base occupies the
start of the derived struct and a pointer to the derived struct is
layout-compatible with a pointer to the base:

```c
struct point  { x: int32; y: int32; }
struct point3 extends point { z: int32; }   // laid out as x, y, z

fn length2(p: struct point*) -> int32 { return p->x * p->x + p->y * p->y; }

fn main() -> int32 {
    let p: struct point3;
    p.x = 3; p.y = 4; p.z = 5;        // inherited fields are reached directly
    return length2(&p as struct point*);   // upcast is explicit
}
```

Because the base is a true prefix, the upcast `&p as struct point*` reads the
same storage; casting the value, `p as struct point`, copies just the base
prefix. Both are _explicit_ — there is no implicit upcast, so a
`struct point3*` is a distinct type that won't silently pass where a
`struct point*` is expected (and two structs that extend the same base never
interconvert). Only the upcast direction is allowed: narrowing a base value
back to a derived one would read past it. With no body of its own,
`struct meters extends int_wrapper;` is a **specialization** — a distinct
type with the base's exact layout, useful for branding values so the compiler
keeps them apart.

The base's `@packed`, `@align`, and `@volatile` are **inherited**: an
extending struct is volatile if its base is, takes at least the base's
alignment, and is packed iff its base is (packing changes field offsets, so
it can't differ from the base — `@packed` on a struct whose base is not
packed is an error).

Generics work on both sides — a generic struct can extend a generic base,
which is monomorphized together with it:

```c
struct pair<K, V>  { key: K; value: V; }
struct entry<K, V> extends pair<K, V> { state: uint8; }   // key, value, state
```

A struct extends a single base, named as a struct (optionally generic); a
pointer, array, or function type is not a valid base.

`sizeof` understands struct layout (including padding), so
`alloc<struct node<int32>>(n)` allocates correctly. Struct values can be
passed to and returned from functions, but not to variadic functions like
printf — pass a pointer or a field instead. See
[examples/structs.mc](examples/structs.mc) and the data structures built on
them: the growable [lib/array.mc](lib/array.mc), the open-addressing hash
table [lib/set.mc](lib/set.mc) (borrowing, identity-keyed), and the
string-keyed [lib/dict.mc](lib/dict.mc), which owns copies of its keys and
compares them by content.

### Imports

`import "file";` at the top of a file compiles another `.mc` file into the
same module. The `.mc` suffix is optional, and a file imported through
several routes (or cyclically) is only loaded once.

Imports resolve relative to the importing file first, then through the
import search path: directories added with `-I`/`--import-path` (in order),
and finally the project's [lib/](lib/) directory, which is on the path by
default so the [standard library](lib/README.md) is importable by bare name.
Pass `--naked` to leave `lib/` off the path.

```c
import "memory";       // found in lib/ via the search path
import "libc/stdio";   // libc bindings, also in lib/

fn main() -> int32 {
    let p = alloc<int32>(3);   // defined in lib/memory.mc
    ...
}
```

`import` copies the imported definitions into the module, much like a C
header. When two separately compiled objects both import the same file —
or instantiate the same generic, such as `alloc<uint8>` — that definition
lands in each object. To keep the linker from rejecting it as a duplicate,
imported and monomorphized-generic definitions are emitted with
`linkonce_odr` linkage so the identical copies merge. The file you compile
directly keeps strong linkage, so a real name clash between two such files
is still a link error.

### Visibility

Everything is public by default. Marking a function or struct `@private`
restricts it to the file that defines it — referencing it from any other
file (however it was imported) is a compile error naming the owning file:

```c
/**
 * Doubles the array's capacity. Internal; called by array_append.
 */
@private
fn array_grow<T>(self: struct array<T>*) { ... }
```

```
error: line 5: function 'array_grow' is private to array.mc
```

`@static` goes further, like C's `static`: the name is file-scoped rather
than merely access-restricted, so it leaves the global namespace entirely.
Different files can each define their own `@static` function, struct,
generic, or variable with the same name, and a file's `@static` definition
shadows a public one imported from elsewhere. From any other file the name
is simply undefined.

The two differ in what they do to the _name_, not just who may use it. A
`@private` definition keeps its real linker symbol (`helper`) and stays in
the global namespace; `@private` only stops _other files_ from referencing
it. A `@static` definition is renamed to a file-scoped symbol (`helper@file`)
and leaves the global namespace, so several files can each carry their own
`helper` with no clash. So reach for `@private` to hide an internal helper
that has a unique name, and for `@static` when you want a hidden helper with
a _common_ name (`init`, `dump`) that several files define independently —
and because a `@static` symbol is file-local rather than global, an unused
out-of-line copy of it can also be dead-stripped. Both compose freely with
[`@inline`](#functions), which is orthogonal: it forces the inlining either
way, and the choice of `@private` vs `@static` only governs the leftover
symbol.

`@static` on a top-level `let` makes a file-scoped variable with its own
storage that persists for the life of the program — a static counter,
buffer, or lookup table. It is zero-initialized unless given an initializer,
which may be any constant expression (a `const`, an `as` cast, `sizeof`,
arithmetic), folded at compile time like a [`const`](#constants) — so a fixed
pointer such as a memory-mapped register address works (see
[Arrays](#arrays) for a static table). With an initializer the type may be
omitted and is inferred from it, like a local `let`; without one (or for
`@extern`) the type is required:

```c
@static let calls: int32;            // starts at 0, kept across calls
@static let lookup: uint8[256];      // a static buffer
const UART_BASE: uint64 = 0x9000000;
@static let uart = UART_BASE as struct pl011*;   // type inferred: struct pl011*

fn next_id() -> int32 { calls = calls + 1; return calls; }
```

### Extern declarations

`@extern` declares a function or global variable that is _defined
elsewhere_ — in libc, or in another object linked into the program. An
extern function gives its signature and ends with `;` instead of a body; an
extern variable is a top-level `let` with a type and no initializer:

```c
@extern
fn atoi(s: uint8*) -> int32;

@extern
let optind: int32;

fn main() -> int32 {
    return atoi("41") + optind;
}
```

A trailing `...` declares a C-style variadic function, such as `printf` or a
kernel's `printk`; extra arguments follow C's promotion rules (small integers
widen to `int32`):

```c
@extern
fn printk(fmt: uint8*, ...);
```

Extern functions cannot be generic. (A `...` is also allowed on functions you
define, which can forward their extra arguments through a `va_list` but not
read them directly — see [Variadic functions](#variadic-functions).) Identical
extern declarations may appear in any number of imported files — they all name the
same symbol — but declarations that disagree about the signature are a
compile error. `@private` applies to extern declarations as usual, and
`@volatile` marks an extern variable whose accesses must not be optimized
away; `@static` cannot be combined with `@extern`, since an external
symbol's name is fixed. (The [libc bindings](#reaching-libc) are exactly
this: files full of predeclared extern functions.)

`@symbol("name")` binds an extern to a linker symbol that differs from its mcc
name — for symbols that aren't valid identifiers, are versioned, or vary by
platform:

```c
@extern @symbol("__stdoutp") let stdout: struct FILE*;   // macOS spelling
@extern @symbol("strlen") fn length(s: uint8*) -> uint64;
```

Code still refers to the declaration by its mcc name (`stdout`, `length`); only
the emitted symbol changes.

### Strings

String literals compile to constant C strings and have type `uint8*`, so a
string is just a pointer to its bytes: it can be stored in a `uint8*`
variable, array, or struct field, indexed (`"hi"[0]` is `104`), and passed
to functions — there is no separate `string` type or built-in mutable string
storage. They support C's simple escape sequences — `\a` `\b` `\f` `\n` `\r`
`\t` `\v`, the quotes `\'` `\"`, `\\`, `\?`, and `\0` for NUL — plus `\e` for
ESC (a GCC/Clang extension, handy for ANSI terminal codes). Any other escape
keeps the bare character.

A character literal in single quotes is a `uint8` — the byte value of a
single character, using the same escapes (`'a'`, `'\n'`, `'\0'`, `'\''`,
`'\\'`). Being a plain byte, it indexes, compares, and does arithmetic like
any other `uint8`:

```c
fn digit_value(c: uint8) -> uint8 {
    return c - '0';      // '7' - '0' == 7
}
```

### Reaching libc

To call into the C library, import a binding module from
[lib/libc/](lib/libc/) — `import "libc/stdio";`, `import "libc/string";`, and
so on. These are ordinary [`@extern` declarations](#extern-declarations) for the
C functions, covering most of the standard headers (the `printf`/`scanf`
families, the `str*`/`mem*` functions, `malloc`/`qsort`/`strtol`, `FILE*`
streams, math, time, errno, …); see the
[standard library index](lib/README.md) for the full list.

```c
import "libc/stdio";
fn main() -> int32 { printf("hello\n"); return 0; }
```

For ordinary output you usually want the [`std`](#standard-library) `print` /
`println` wrappers rather than `printf` directly; the `libc/` bindings are for
reaching the rest of the C library.

Anything the bindings do not cover, you can [declare yourself](#extern-declarations)
with `@extern`. Variadic arguments to functions like `printf` follow C promotion
rules (small integers are widened to `int32`).

### Comments

```c
// line comments

/* block comments */

/**
 * Doc comments are block comments by convention, in this format:
 *
 * @param self:  array to write into
 * @param index: zero-based index; must be < self->length
 *
 * @return true on success, false if index is out of bounds
 */
```

See the [standard library index](lib/README.md) for the modules under `lib/`,
all written in this style.

## Editor support

[editors/vscode/](editors/vscode/) is a VS Code extension that syntax-highlights
`.mc` files — keywords, the `intN`/`uintN`/`float64` types, `@`-annotations, and
string/char/number literals. Symlink it into your extensions folder and reload:

```bash
ln -s "$(pwd)/editors/vscode" ~/.vscode/extensions/mcc-language
```

See its [README](editors/vscode/README.md) for packaging and other install
options.

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
