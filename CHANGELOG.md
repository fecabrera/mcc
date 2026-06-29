# Changelog

All notable changes to mcc are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and the project follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html)
(pre-1.0: new language/tooling features bump the minor version).

## [Unreleased]

### Added

- **Struct literals** — `struct Name { field = value, ... }`: omitted fields are
  zeroed (or set to their declared default), fields may be given in any order,
  and a literal works as an argument, a return value, or written through a
  pointer. Generic type arguments are inferred from the field values
  (`struct box { value = 5 }` infers `box<int32>`), anchored only by typed
  values. See [Structs](docs/language.md#structs).
- **Default field values** — `field: type = expr;` gives a struct field a
  default, used both by struct literals that omit the field and by a bare
  `let s: struct S;` declaration. See [Structs](docs/language.md#structs).
- **Type aliases** — `type <name> = <type>;`, a transparent alias (not a new
  distinct type) for builtins, pointers, function pointers, and structs;
  `@private` / `@static` apply. See [Type aliases](docs/language.md#type-aliases).
- **Slices** — `slice<T>`, a builtin non-owning view `{ ptr: T*; length: uint64 }`
  over a contiguous run of `T`, with a runtime `.length`, indexing `s[i]`, and
  native `for x in s` iteration. Constructed by an explicit borrow — `xs as
  slice<T>` from an owned `list<T>` (reads `{data, length}`, drops `capacity`) or
  a fixed array `T[N]` (`{&arr[0], N}`). A `char[N]` is NUL-terminated text, so
  its borrow drops the terminator (`length` is `N - 1`); a `uint8[N]` raw buffer
  keeps every byte. See [Slices](docs/language.md#slices) and
  [examples/slices.mc](examples/slices.mc).
- **Read-only slices** — `slice<const T>`, the element-mutability axis: indexing
  yields a non-assignable element (`s[i] = x` is rejected), while a loaded value
  or `for`-loop variable is a mutable copy. A mutable `slice<T>` widens
  implicitly to `slice<const T>`, and a borrow of a mutable source may target
  either; a read-only source (a `slice<const T>`, a `const` parameter, or a
  `const`-typed value) borrows only to `slice<const T>`, preserving immutability.
  `const` is a general type qualifier (`let pi: const float64 = 3.14;`). See
  [Read-only slices](docs/language.md#read-only-slices) and
  [examples/slices.mc](examples/slices.mc).
- **`char` type** — a distinct one-byte text type, ABI-identical to `uint8` (an
  unsigned byte) but a separate type, so NUL-terminated text is told apart from a
  raw byte buffer. Character literals (`'a'`) are untyped constants that default
  to `char` but adapt to a `uint8`/integer slot; a `char` *value* needs an
  explicit `as` to become a `uint8`. `char*` coerces to `uint8*` like any
  pointer, so libc still takes string literals. A `char[N]` borrows to a
  `slice<char>` that drops the trailing NUL (the text); a `uint8[N]` keeps every
  byte. See [Strings](docs/language.md#strings).
- **`byte` type** — a transparent builtin alias for `uint8`, the raw one-byte
  unit of memory. Unlike `char` it is not a distinct type: `byte` and `uint8`
  values and pointers are interchangeable without a cast. The memory-handling
  APIs now read in terms of it — the `memory` allocators and `set_bytes`, libc's
  `malloc`/`calloc`/`realloc`/`free`, `memcpy`/`memmove`/`memset`/`memchr`/
  `memcmp`, `qsort`/`bsearch`, and the raw stream buffers of
  `fread`/`fwrite`/`setbuf`/`setvbuf`. See [Types](docs/language.md#types).
- **Constant-expression array sizes** — an array dimension may be any constant
  integer expression (`int32[N + 1]`, `uint8[2 * SIZE]`), not just a literal or a
  lone `const` name.
- **`sizeof` of a variable** — `sizeof(v)` is the size of `v`'s type, so the type
  need not be spelled out; the operand is never evaluated. See
  [Pointers](docs/language.md#pointers).
- **`new<T>()`** — a typed single-element heap allocator in the `memory` library,
  alongside `alloc` / `resize` / `dealloc`.
- **`range<T>` library** — a half-open `[start, end)` integer interval that
  supplies the iterator protocol, so `for i in &r` counts; generic over the
  integer width. See [examples/ranges.mc](examples/ranges.mc).
- **`--strict-align`** — forbid the backend from emitting unaligned memory
  accesses (gcc's `-mstrict-align`), for bare-metal targets running with the MMU
  off where an unaligned wide load/store traps. Composes with
  `--general-regs-only` (both merge into the one per-function `target-features`).

### Changed

- **String literals are `char[N]` arrays** (NUL included) rather than bare
  `uint8*`. They decay to a `char*` (which coerces to `uint8*` like any pointer)
  wherever a pointer is used (call arguments, returns, comparisons, indexing), so
  existing libc code is unaffected, but an owned binding keeps its array type:
  `let s = "hi";` is a mutable `char[3]`, `let s: char[] = "hi";` infers the
  size, and `let s: char[8] = "hi";` zero-fills the rest (a `uint8[N]` annotation
  still accepts the same bytes as a raw buffer). This makes `len(s)` / `len("hi")`
  work and lets a string be borrowed as a `slice<char>` (the borrow drops the
  trailing NUL, so the slice spans the text). Annotating `char*`/`uint8*` keeps
  the pointer-to-constant behavior (no copy). See
  [Strings](docs/language.md#strings).
- **`string` is now `type string = list<char>`** — a transparent
  specialization with the same layout, so a `struct string*` upcasts to a
  `struct list<char>*` and every `list` operation works on a string. The
  list/string API distinguishes `push` (append one element) from `append`
  (concatenate another whole list).
- **Standard-library and libc string APIs adopt `char`** — `dict` keys are now
  `char*`, the libc bindings that carry text (`strcpy`/`strlen`/`strcmp`/
  `printf`/`fgets`/`getenv`/`strftime`, …) take and return `char*`, and `std`'s
  `print`/`writestr`/`writeln` follow suit. Raw-byte and stream operations stay
  `uint8` — `memcpy`/`memset`, `fread`/`fwrite`, and the hashing functions.
  Because `uint8*` does not coerce to `char*`, a buffer handed to a libc string
  function must now be a `char[N]`/`char*` (or an explicit cast); string literals
  are unaffected.
- **The standard library moved to `libmc/`** and is now compiled from source
  (previously `lib/`); `import "<module>"` by name is unchanged.
- **File-scoped symbols are mangled with `.`** instead of `@`, so the emitted
  names for `@static` / `@private` declarations read like `file.name`.
- The compiler no longer prints a `wrote <output>` line on a successful compile.

### Fixed

- A compile error raised while generating a generic function instance is now
  attributed to the template's own file, not the root module. Previously an error
  on a line inside an imported library (e.g. a failed type-parameter inference in
  a `for ... in` over a generic container) was blamed on the file being compiled.

## [0.2.0] - 2026-06-26

### Added

- **Enums** — `enum Name[: type] { Member = value, ... }` over any underlying
  type (`int32` by default), accessed as `Name::Member`. The name is usable as a
  type, members may reference earlier members of the same enum, and `@private` /
  `@static` apply. See [Enums](docs/language.md#enums).
- **Ternary operator** — `cond ? a : b`, an expression that evaluates exactly one
  arm.
- **`const` parameters** — an immutable parameter the callee promises not to
  mutate; a `const` struct is passed by a hidden pointer instead of copied, so
  you get value semantics without the copy. See
  [const parameters](docs/language.md#const-parameters).
- **In-expression integer widening** — two same-signedness integer operands
  widen to the wider type within an expression (e.g. `a + b * c` over mixed
  widths) without explicit casts; assignments, returns, and arguments still
  require a cast.
- **Conditional imports** — a top-level `@if` branch may contain `import`
  statements, so a dependency can be pulled in only for the targets that need it;
  only the live branch is resolved.
- **Interface files** — `mcc src.mc --emit-interface` writes an importable `.mci`
  stub (concrete functions as `@extern` prototypes; types, constants, and
  generic/`@inline` functions in full), to ship a precompiled library as an
  object plus a thin interface. See
  [Interface files](docs/language.md#interface-files).
- **Object-only compilation** — `-c` / `--compile` emits a native `.o` without
  linking.
- **`.mci` import resolution** — a bare `import "foo"` resolves to `foo.mc` if
  present, otherwise `foo.mci`.
- **`--freestanding`** — disable hosted-libc assumptions so LLVM does not rewrite
  standard-named calls (e.g. `printf("…\n")` → `puts`), for bare-metal builds.
- **Helix editor support** — a tree-sitter grammar (`editors/helix/`) with syntax
  highlighting, indentation, comment toggling, and text objects.
- **`.mci` highlighting** — the VS Code and Helix grammars recognize interface
  files.
- **`string_duplicate`** in the string library.

### Changed

- Renamed `lib/array.mc` to `lib/list.mc`.
- Renamed the `memory` byte-copy helpers (docs refreshed).
- `set` and `dict` now track slot state with a `uint8`-backed enum.

### Removed

- `string_append_string` from the string library.

### Fixed

- `return <void value>;` is rejected with a diagnostic instead of emitting
  invalid IR.
- A shift no longer forces its count to the value's type, so `1 << count` with an
  unsigned `count` compiles again.

## [0.1.2] - 2026-06-19

### Added

- Inline assembly: `@asm(...)` expressions and `@asm fn` sugar, with
  `@clobbers(...)` lists; enabled for same-arch cross `--target` builds.
- Block expressions: `{ ...; emit v; }` as a value.
- Struct `extends` (prefix-layout specialization), generic `extends`, and
  explicit upcast to the base.
- `@inline` functions; `for … in` dispatched by struct name; comma-separated
  `when` arms; arrays of function pointers; unary bitwise NOT (`~`).
- Exhaustive `if`/`else` and `case` count as guaranteed exits.
- `@static let` type inference and constant-expression `@static` initializers.
- Untyped integer literals default to the narrowest fitting width.
- `-D NAME[=VALUE]` defines for `@if` conditions.

### Changed

- Renamed the CLI flag `--naked` to `--nostdlib`.

### Fixed

- Forwarding a `va_list` parameter to another function works across all ABIs.

## [0.1.1] - 2026-06-15

### Fixed

- Imported `@static` globals get `linkonce_odr` linkage so identically mangled
  copies merge into one instance (previously globals could silently split state
  across separately compiled objects).
- Cross builds use the small code model with static relocations (ADRP-based
  addressing), fixing `@static` globals reading back as zero in fixed-load
  freestanding images.

## [0.1.0] - 2026-06-14

### Added

- Packaged mcc as a pip-installable distribution: a `pyproject.toml` exposing an
  `mcc` console script and bundling the `lib/` standard library into the wheel,
  with the stdlib resolved from the installed location or a source checkout.

[0.2.0]: https://github.com/fecabrera/mcc/compare/v0.1.2...v0.2.0
[0.1.2]: https://github.com/fecabrera/mcc/compare/v0.1.1...v0.1.2
[0.1.1]: https://github.com/fecabrera/mcc/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/fecabrera/mcc/releases/tag/v0.1.0
