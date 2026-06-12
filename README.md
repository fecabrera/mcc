# mcc

A small, modern-C-style language with generics, structs, and pointers,
compiled to native code (or JIT-executed) via [LLVM](https://llvm.org/)
using [llvmlite](https://llvmlite.readthedocs.io/). The compiler lives in
the [mcc/](mcc/) package, with one module per stage: lexer, parser, code
generator, and driver.

```c
#include <stdio.h>

fn main() -> int32 {
    printf("hello, world\n");

    return 0;
}
```

## Contents

- [Requirements](#requirements)
- [Usage](#usage)
- [Examples](#examples)
- [Language reference](#language-reference)
  - [Functions](#functions)
  - [Generics](#generics)
  - [Variables](#variables)
  - [Control flow](#control-flow)
  - [Types](#types)
  - [Operators](#operators)
  - [Casts](#casts)
  - [Pointers](#pointers)
  - [Structs](#structs)
  - [Imports](#imports)
  - [Visibility](#visibility)
  - [Strings](#strings)
  - [Includes](#includes)
  - [Comments](#comments)
- [Tests](#tests)
- [How it works](#how-it-works)

## Requirements

- Python 3.14 (see [Pipfile](Pipfile))
- llvmlite
- A C compiler on `PATH` (`cc`) for linking native executables

```bash
pipenv install
```

## Usage

```bash
pipenv run python -m mcc examples/helloworld.mc              # compile to a native executable
pipenv run python -m mcc examples/helloworld.mc -o hello     # choose the output name
pipenv run python -m mcc examples/helloworld.mc --run        # JIT-compile and run immediately
pipenv run python -m mcc examples/helloworld.mc --emit-llvm  # print the LLVM IR instead of compiling
pipenv run python -m mcc examples/helloworld.mc -O3          # optimization level (0-3, default 2)
pipenv run python -m mcc main.mc -I vendor -I deps           # extra import search paths
pipenv run python -m mcc main.mc --naked                     # don't put lib/ on the import path
```

## Examples

[examples/](examples/) is a runnable tour of the whole language — one
feature per file, from hello world through unsigned arithmetic and generics
to fizzbuzz and a prime sieve. See the [index](examples/README.md).

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
    puts("hi");
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
[examples/templates.mc](examples/templates.mc).

Generic functions with the same name form an *overload set*, dispatched by
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

### Control flow

```c
if (x > 10) {
    puts("big");
} else if (x > 5) {
    puts("medium");
} else {
    puts("small");
}

while (x < 10) {
    x = x + 1;
}

until (x == 0) {     // inverse of while: stops when the condition is true
    x = x - 1;
}
```

Conditions accept `bool` or any integer (compared against zero, as in C).
A body that is a single statement does not need braces:
`if (x > 10) return x;`

### Types

| Type                                  | LLVM equivalent                                                   |
| ------------------------------------- | ----------------------------------------------------------------- |
| `int8`, `int16`, `int32`, `int64`     | `i8`, `i16`, `i32`, `i64` (signed)                                |
| `uint8`, `uint16`, `uint32`, `uint64` | `i8`, `i16`, `i32`, `i64` (unsigned)                              |
| `bool`                                | `i1`                                                              |
| `float64`                             | `double`                                                          |
| `T*` (any type + `*`s)                | pointer                                                           |
| `void`                                | `void` (return type only; `void*` is not allowed -- use `uint8*`) |

Literals with a decimal point are `float64` and `true`/`false` are `bool`.
Integer literals are _untyped constants_: they adapt to the integer type
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

By descending precedence: unary `-` `!` `*` `&`, `as` casts, then `*` `/`
`%`, `+` `-`, shifts `<<` `>>`, bitwise `&`, `^`, `|`, comparisons
`<` `<=` `>` `>=`, and `==` `!=`. Comparisons yield `bool`; `%` and the
bitwise/shift operators are integer-only. `>>` is an arithmetic shift for
signed types and logical for unsigned. Unlike C, bitwise operators bind
tighter than comparisons, so `a & 4 == 4` means `(a & 4) == 4`. Integer
constant expressions fold at compile time.

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
default so the standard library is importable by bare name. Pass `--naked`
to leave `lib/` off the path.

```c
import "memory";   // found in lib/ via the search path
#include <stdio.h>

fn main() -> int32 {
    let p = alloc<int32>(3);   // defined in lib/memory.mc
    ...
}
```

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
Different files can each define their own `@static` function, struct, or
generic with the same name, and a file's `@static` definition shadows a
public one imported from elsewhere. From any other file the name is simply
undefined.

### Strings

String literals (with `\n`, `\t`, `\r`, `\0`, `\"`, `\\` escapes) compile to
constant C strings. They can currently only be passed to functions — there is
no string variable type yet.

### Includes

`#include <header>` at the top of a file makes the corresponding libc
functions callable (use `import` for `.mc` files):

| Header     | Functions                                            |
| ---------- | ---------------------------------------------------- |
| `stdio.h`  | `printf` (variadic), `puts`, `putchar`, `getchar`    |
| `stdlib.h` | `malloc`, `free`, `exit`, `abs`                      |
| `string.h` | `memcpy`, `memset`, `strlen`                         |
| `math.h`   | `sin`, `cos`, `sqrt`, `pow`, `floor`, `ceil`, `fabs` |

Variadic arguments to `printf` follow C promotion rules (small integers are
widened to `int32`).

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

See [lib/](lib/) for documented code.

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
