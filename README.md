# mcc

A modern C-style language with generics, structs, and pointers,
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

## Goals

mcc aims to be **one language for both systems and application programming**.
It stays close enough to the metal to write a driver or run on bare metal, yet
comfortable enough to write an everyday application, so you never reach for a
different tool at each end.

- **Safe and unsafe code, side by side.** You can write memory-safe code _and_
  drop to raw pointers and manual memory when you need to. Safety is
  **encouraged through syntax, never enforced by the compiler**: constructs like
  [`const`](docs/language.md#const-parameters) parameters, [slices](docs/language.md#slices),
  and planned `mut` references make the safe path the natural, ergonomic one,
  while the language never forbids the low-level one.
- **Familiar to C programmers.** The syntax, type system, and
  [C ABI](#c-abi-compatibility) stay close to C, so a C programmer can read mcc
  on day one and explore its additions (generics, `defer`, block expressions) a
  feature at a time, without relearning the basics.
- **Staged porting, no big rewrite.** Because mcc is
  [ABI-compatible with C](#c-abi-compatibility) and links directly against libc
  and existing objects, an existing C application can be ported one file at a
  time, with mcc and C translation units side by side and no disruptive
  all-at-once rewrite.

## Contents

- [Goals](#goals)
- [Requirements](#requirements)
- [Install](#install)
- [Usage](#usage)
- [Quickstart](#quickstart)
- [Examples](#examples)
- [Standard library](#standard-library)
- [C ABI compatibility](#c-abi-compatibility)
- [Roadmap](#roadmap)
- [Language reference](docs/language.md), the complete guide to every feature
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
mcc examples/basics/helloworld.mc --run
```

### From source

For development, work in a checkout with [pipenv](https://pipenv.pypa.io/):

```bash
pipenv install
pipenv run python -m mcc examples/basics/helloworld.mc --run
```

`pipenv run python -m mcc` and an installed `mcc` are interchangeable; the
examples below use the `mcc` command.

## Usage

```bash
mcc examples/basics/helloworld.mc              # compile to a native executable
mcc examples/basics/helloworld.mc -o hello     # choose the output name
mcc examples/basics/helloworld.mc --run        # JIT-compile and run immediately
mcc examples/basics/helloworld.mc --emit-llvm  # print the LLVM IR instead of compiling
mcc libmc/list.mc -c                      # compile to an object (.o), don't link
mcc libmc/list.mc -S                      # emit target assembly (.s), don't assemble
mcc libmc/list.mc --emit-interface        # write an importable .mci stub
mcc examples/basics/helloworld.mc -O3          # optimization level (0-3, default 2)
mcc main.mc -I vendor -I deps           # extra import search paths
mcc main.mc --nostdlib                  # don't put libmc/ on the import path
mcc main.mc util.o -lcurl               # link extra objects and libraries
mcc main.mc -L build/lib -lmylib        # with a library search path

mcc main.mc --target aarch64-unknown-none-elf   # cross-compile to an object file
mcc main.mc --general-regs-only         # never use FP/SIMD registers
```

| Option                    | Description                                                                                                                                                                                   |
| ------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `source`                  | The `.mc` file to compile (exactly one). Its imports are resolved and compiled with it. Any other input (a `.o` object, `.a` archive, or shared library) is forwarded to the linker.          |
| `-o`, `--output FILE`     | Name of the generated file. Defaults to the source name without its extension (a native executable), with a `.o` suffix when `--target` is given, or with a `.s` suffix under `-S`.           |
| `-c`, `--compile`         | Compile to an object file (`.o`) for the host and stop, without linking an executable. Defaults the output to the source name with a `.o` suffix.                                             |
| `-S`, `--emit-asm`        | Write the target's assembly text (`.s`) and stop, without assembling or linking, for inspection or handing to an external assembler. Honors `-O` and `--target` (cross assembly).             |
| `-l NAME`                 | Link against a library, forwarded to `cc` as `-lNAME` (repeatable). `libm` is always linked, so `-lm` is implied.                                                                             |
| `-L DIR`                  | Add a library search path, forwarded to `cc` as `-LDIR` (repeatable).                                                                                                                         |
| `--emit-interface`        | Write a [`.mci` interface stub](docs/language.md#interface-files) describing the file's public surface (to ship beside an object) and exit.                                                   |
| `-O 0`–`3`                | Optimization level, from `0` (none) to `3` (most aggressive). Default `2`.                                                                                                                    |
| `--run`                   | JIT-compile and run the program immediately instead of writing a file; its exit code becomes mcc's. Cannot be combined with `--target`.                                                       |
| `--emit-llvm`             | Print the generated LLVM IR to stdout and exit, without compiling or linking.                                                                                                                 |
| `-I`, `--import-path DIR` | Add a directory to the import search path. Repeatable; later paths are searched after earlier ones.                                                                                           |
| `--nostdlib`              | Do not put the bundled `libmc/` directory on the import path, dropping the standard library (for freestanding builds that supply their own).                                                  |
| `--target TRIPLE`         | Cross-compile for the given LLVM target triple, emitting an object file instead of a host executable.                                                                                         |
| `--general-regs-only`     | Generate code that uses only general-purpose registers, never the floating-point/SIMD ones.                                                                                                   |
| `--strict-align`          | Never emit unaligned memory accesses (gcc's `-mstrict-align`); needed for bare-metal targets running with the MMU off, where an unaligned wide load/store traps.                              |
| `--freestanding`          | Don't assume a hosted C library, so LLVM won't rewrite standard-named calls (e.g. `printf("…\n")` → `puts`) into symbols a bare-metal program never defines. The `-ffreestanding` equivalent. |
| `-D NAME[=VALUE]`         | Define a name for [`@if`](docs/language.md#conditional-compilation) conditions: `NAME` alone is `1`, `NAME=VALUE` sets an integer. Repeatable; a name with no `-D` reads as `0`.              |
| `-Werror`                 | Promote [warnings](docs/language.md#error-directives) to errors: each renders as `file: error: line N: msg [-Werror]`, the build fails with exit 1, and no output is written.                 |

`--target` accepts any LLVM triple and emits an object file instead of a
host executable; link it with that target's toolchain (e.g.
`aarch64-elf-gcc`). See [examples/baremetal/](examples/baremetal/) for a
freestanding kernel built this way.

`--general-regs-only` keeps generated code off the floating-point and SIMD
registers, the equivalent of gcc's `-mgeneral-regs-only`. It stops the
backend from quietly using a vector register (say, to copy a struct) in
code that must not touch FP state, such as a kernel or an interrupt
handler. Supported for aarch64, x86, and riscv targets.

`--strict-align` forbids the backend from emitting unaligned memory accesses,
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
`printf("%c", c)` into `putchar`, synthesizing references to libc symbols a
bare-metal program never defines. Pass it when building a kernel or any
target with no libc. (`--nostdlib` only drops mcc's `libmc/` from the import
path; it does not change this optimizer assumption.)

## Quickstart

Write a `.mc` file and run it with `mcc file.mc --run`. A short taste of the
language, showing typed `fn`s, `let` with type inference, structs, a monomorphized
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
    let p = point { x = 3, y = 7 };

    let hi = max(p.x, p.y);     // type inferred: int32
    println("max = %d", hi);

    let i: int32 = 0;
    while (i < hi) {
        defer i += 1;            // runs at the end of every iteration
        if (i % 2 == 0) {
            println("%d is even", i);
        }
    }
    return 0;
}
```

That covers the basics; for the full language (generics, `defer`, block
expressions, pointers, compile-time `@if`, inline assembly, and the rest), see
the **[Language reference](docs/language.md)**.

## Examples

[examples/](examples/) is a runnable tour of the whole language, one
feature per file, from hello world through unsigned arithmetic and generics
to fizzbuzz and a prime sieve. See the [index](examples/README.md).

## Standard library

The modules under [libmc/](libmc/) are on the import search path by default, so
they import by bare name. For everyday output, `import "std";` provides `print`
and `println`, printf-style formatting written in mcc on top of the libc
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
library itself (`printf`, `malloc`, the `str*`/`mem*` functions, `FILE*`
streams, and so on), for when you want C directly; see
[Reaching libc](docs/language.md#reaching-libc). The [standard library index](libmc/README.md)
lists every module.

The standard library is **compiled from source** with each program. Shipping it
as a precompiled native library (`libmc.a`/`.so`, built by [build.sh](build.sh))
is **experimental**: the stdlib itself compiles cleanly into those archives, but
_linking a program against them_ is not ready: some exported symbols (`errno`,
`crc32`, …) collide with system symbols, which needs
[namespaced exported symbols](ROADMAP.md#planned). Until then the precompiled archives
aren't linked.

## C ABI compatibility

mcc follows the platform C ABI for **scalars and pointers**, so any function
whose signature is built from them interoperates with C in both directions:
call C from mcc with `@extern`, or expose mcc functions to a C linker. This is
why the [libc bindings](libmc/libc/) work directly:

| mcc type                         | C type                                                   |
| -------------------------------- | -------------------------------------------------------- |
| `int8`–`int64`, `uint8`–`uint64` | `char`/`short`/`int`/`long`/`long long` (and `unsigned`) |
| `float64`                        | `double`                                                 |
| `T*`                             | `T *`                                                    |
| `va_list`, variadic `...`        | `va_list`, varargs                                       |

Generics don't change this: a generic is monomorphized to concrete types before
codegen, so an instantiation obeys the same ABI as a hand-written one.

**Structs passed or returned by value are not ABI-compatible yet.** mcc hands
LLVM the raw aggregate, but LLVM does not apply the platform ABI's
register/`byval`/`sret` classification automatically the way a C compiler does,
so a `struct` argument or return won't match a C function expecting the same
struct. Across the C boundary, pass a pointer (`struct point*`) instead. Fixing
this is [on the roadmap](ROADMAP.md#planned). (`bool` is `i1`; it matches C's 1-byte
`_Bool` inside structs but isn't strictly the `_Bool` parameter ABI, rarely a
concern in practice.)

## Roadmap

What the compiler does today, and what is planned next, lives in
[ROADMAP.md](ROADMAP.md): the full checklist of implemented features (each
linking to its reference section) and the planned work, grouped by scope and
ordered by dependency.

## Editor support

[editors/vscode/](editors/vscode/) is a VS Code extension that syntax-highlights
`.mc` files: keywords, the `intN`/`uintN`/`float64` types, `@`-annotations, and
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

The suite in [tests/](tests/) covers each stage in isolation: token streams
([test_lexer.py](tests/test_lexer.py)), AST shapes and precedence
([test_parser.py](tests/test_parser.py)), emitted IR and compile-error
diagnostics ([test_codegen.py](tests/test_codegen.py)), plus end-to-end
programs that JIT-compile in-process and assert on their real printf output
([test_execution.py](tests/test_execution.py)), and the command-line
interface as a subprocess ([test_cli.py](tests/test_cli.py)).

## How it works

The `mcc` package is a classic four-stage pipeline, one module per stage:

1. **[lexer.py](mcc/lexer.py)**: a single regex alternation turns source
   text into tokens, tracking line numbers for error messages.
2. **[parser.py](mcc/parser.py)**: recursive descent over the token
   stream, with precedence climbing for expressions, producing a dataclass
   AST (node classes live in [nodes.py](mcc/nodes.py)).
3. **[codegen/](mcc/codegen/)**: walks the AST and emits LLVM IR with
   `llvmlite.ir`, lowering locals to `alloca` slots, strings to private global
   constants, control flow to basic blocks and branches, and generic
   functions to one monomorphized LLVM function per instantiation.
4. **[driver.py](mcc/driver.py)**: resolves the `import` graph into a
   single merged program, then `llvmlite.binding` verifies the module, runs
   LLVM's pass pipeline at the requested `-O` level, and either executes
   `main` through MCJIT (`--run`) or emits an object file and links it with
   `cc`.

All compile errors are reported as `file: error: line N: message`.
