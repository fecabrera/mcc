# Changelog

All notable changes to mcc are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and the project follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html)
(pre-1.0: new language/tooling features bump the minor version).

## [Unreleased]

### Added

- **`@noalias` parameters** — mcc's `restrict`: mark a pointer parameter
  (`fn copy(@noalias dst: uint8*, @noalias src: uint8*, n: uint64)`) as not
  overlapping any other pointer the function reaches, lowered to LLVM's
  `noalias` argument attribute so the optimizer skips runtime overlap checks
  and recognizes bulk moves. The promise is unchecked (overlapping pointers
  are undefined behavior, as in C). It changes no ABI, so it is allowed on
  `@extern` (the libc `restrict` family — `memcpy`, `strcpy`, and friends —
  and `bytecopy`/`copy` in `memory` are now marked); it is rejected on `mut`,
  non-pointer, and `@asm` parameters. `@noalias` combines with `const`. See
  [@noalias parameters](docs/language.md#noalias-parameters) and
  [noalias.mc](examples/noalias.mc).

### Changed

- **`memory` copy/fill API reshaped** — the canonical names are now `bytecopy`
  and `copy` (byte-wise vs. item-at-a-time copy) and `bytefill` and `fill`
  (byte-wise vs. item-at-a-time fill); the old `copy_bytes`/`copy_items`/
  `set_bytes`/`set_items` remain as deprecated `@inline` aliases. The copy and
  fill functions now return the count they processed (bytes for the
  `memcpy`/`memset`-backed variants, elements otherwise), and `bytezero`/`zero`
  return their counts too.

## [0.4.0] - 2026-07-02

### Added

- **`swap` and `replace` in `std`** — the first stdlib helpers built on `mut`
  parameters: `swap(a, b)` exchanges two values in place and
  `replace(dst, value)` stores a new value and returns the old one, both
  generic (`@inline`) and pointer-free at the call site. See
  [libmc/std.mc](libmc/std.mc) and [mut_params.mc](examples/mut_params.mc).
- **Editor support catch-up** — the VS Code grammar and the Helix tree-sitter
  grammar now highlight `mut` and `union`; the tree-sitter grammar also
  learned the syntax it was missing: compound assignment operators, `const T`
  in type positions, struct/union literals (`point { x = 1 }`), field
  defaults, constant-expression array dimensions (`[N + 1]`), variadic
  function types (`fn(char*, ...)`), and `alignof`/`offsetof`. Every file in
  `examples/` and all of `libmc/` (except one line hitting the grammar's
  documented `as T * n` cast-star ambiguity) now parses with no errors.
- **`mut` parameters** — `fn find(key: int32, mut out: int32) -> bool`: the
  writable dual of `const`, passed by hidden reference to the caller's storage
  for every type (scalars included — that is how the write reaches the
  caller). Assignments in the callee land in the caller's variable; reads copy
  out; `&` on it is rejected so the reference cannot escape — the memory-safe
  replacement for an out-pointer parameter, with no `&` at the call site. The
  argument must be the caller's own writable storage of exactly the
  parameter's type. Works on generic parameters (`swap<T>(mut a: T, mut b: T)`);
  re-lending to another `mut` parameter (recursion included) is allowed. Not
  allowed on `@extern`/`@asm` parameters, and a `mut` function cannot be a
  function value or export to a `.mci` interface (the hidden-reference
  convention is not expressible there). `mut` is now a reserved keyword. See
  [mut parameters](docs/language.md#mut-parameters) and
  [mut_params.mc](examples/mut_params.mc).
- **Unions** — `union Name { i: int64; f: float64; }`: an aggregate whose
  members share one storage, sized by the largest member with every member at
  offset 0, for C-layout interop and deliberate type punning (a cross-member
  read is defined byte reinterpretation). Union literals set at most one
  member over zero-filled storage, members read and write through `.`/`->`,
  and unions take generics, `@packed`/`@align`/`@volatile`, `const`
  parameters, and `.mci` interfaces like structs. The struct-only forms
  (`extends`, member defaults, flexible array members) are rejected, and a
  global/`@static` union initializer is not supported yet. See
  [Unions](docs/language.md#unions) and [unions.mc](examples/unions.mc).
- **Compound assignment** — `target op= value` for every arithmetic, bitwise,
  and shift operator (`+= -= *= /= %= &= |= ^= <<= >>=`), meaning
  `target = target op value`. The target may be any assignable lvalue (a
  variable, `*p`, `a[i]`, or a field), obeys the same read-only rules as a
  plain assignment, and is evaluated exactly once — so a complex lvalue like
  `arr[next()] += 1` runs its side effects a single time. See
  [Variables](docs/language.md#variables) and
  [compound_assignment.mc](examples/compound_assignment.mc).
- **`for x in` over a struct value** — the `_it`/`_next` protocol takes the
  container by pointer, but `for x in r` no longer needs the `&`: a struct
  value is borrowed automatically (iterating a snapshot), while `for x in &r`
  still iterates by reference and a pointer passes straight through. Because
  the snapshot is a real local, an rvalue is now iterable too —
  `for x in make_iter() { ... }`, which `&` could not address. See
  [Control flow](docs/language.md#control-flow).
- **Builtin `range`** — `for i in range(start, end)` (or `for i in range(end)`,
  from 0) is a compiler builtin: a counting loop over `[start, end)` that lowers
  straight to a counter, with no import, no struct built, and no `_it`/`_next`
  calls. The element type is inferred from the bounds or set with `range<T>(...)`.
  See [Control flow](docs/language.md#control-flow).
- **Builtin `iterator<T>` and `pair<K, V>` structs** — the shared cursor behind
  the `_it`/`_next` protocol (`{ obj: T*; idx: uint64 }`) and the key/value
  element the keyed containers yield are now compiler-provided struct templates,
  available in every program with no import. They are ordinary names, not
  reserved: a user struct named `iterator` or `pair` takes precedence, as with
  the builtin `range`.
- **Keyword-free struct literals** — `Name { field = value, ... }` is now a
  shorthand for `struct Name { field = value, ... }`, so a stack struct value
  reads `let p = point { x = 1, y = 2 };`. Parser-only: it builds the same
  literal, so codegen, defaults, and generic type-argument inference
  (`pair<int32, char*> { ... }` or inferred) are unchanged. The one barred
  position is the `for x in <expr> { ... }` header, where the `{` always starts
  the loop body — parenthesize (`for x in (A { ... })`) or use the keyword form
  there. See [Structs](docs/language.md#structs) and
  [struct_literals.mc](examples/struct_literals.mc).
- **Builtin `enumerate`** — `for e in enumerate(obj)` runs `obj`'s ordinary
  iteration (the `_it`/`_next` protocol, or a slice's native walk) while
  keeping a position counter, yielding a builtin
  `enumerated<T> { index: uint64; value: T }` per element, read as `e.index` /
  `e.value`. No import, no extra copy per turn (`_next` writes straight into
  the element's `value` field), and `obj` is borrowed exactly like a bare
  `for x in obj` — a value is snapshot, `&` iterates by reference, an rvalue
  works. A `continue` still consumes its index. A user-defined `enumerate`
  function takes precedence, as does a user `enumerated` struct;
  `enumerate(range(...))` is rejected since the counter is the value. See
  [Control flow](docs/language.md#control-flow) and
  [iteration.mc](examples/iteration.mc).
- **Linker passthrough** — the `mcc` command line now takes `-l<name>` libraries
  and `-L<dir>` search paths, plus extra object/archive inputs alongside the
  `.mc` source (`mcc app.mc util.o -L build/lib -lmylib`), all forwarded to the
  `cc` link step. They apply only when linking an executable (not with `--run`,
  `-c`, `--target`, or the `--emit-*` modes, which stop before the link), and a
  failed link is reported cleanly after cc's own diagnostics. `libm` is still
  always linked. See [Usage](README.md#usage).

### Changed

- **The stdlib `get` family takes `mut` out-parameters** — `list_get`,
  `string_get`, `dict_get`, and `set_get` now declare their out-parameter as
  `mut out: T` instead of `out: T*`. Call them with the variable itself
  (`list_get(&nums, 6, value)`), not its address — the `&` at the call site
  is gone, and the callee can no longer leak the address. The `_it`/`_next`
  iteration protocol still uses `out: T*` (the compiler emits those calls;
  migrating the protocol to `mut` is on the roadmap).

### Removed

- The `range` **library** module (`import "range"`, `struct range<T>`,
  `range_it`/`range_next`) is gone, subsumed by the builtin above. Counting
  loops that built a `struct range` and iterated `&r` become `for i in range(…)`.
- The `iteration` **library** modules (`import "iteration/iterator"` and
  `import "iteration/pair"`) are gone, subsumed by the builtin structs above.
  Drop the imports; the struct names resolve as before.

## [0.3.1] - 2026-06-30

### Added

- **Variadic function-pointer types** — `fn(A, ...) -> R`, a trailing `...`
  after at least one fixed parameter, is the type of a pointer to a variadic
  function (matching a C `R (*)(A, ...)`). It is distinct from the non-variadic
  form and usable anywhere a type is — a parameter, a struct field, a `let`, or
  a `const` alias — so a variadic like `printf` can be held, passed, and called
  through with varargs. See [Function pointers](docs/language.md#function-pointers).

### Fixed

- A `const` or `@static` global may now name a function (a compile-time alias),
  e.g. `const log = println;`, and be called by that name. Previously only a
  local `let` could; a `const` always failed with "not a constant" and an
  unannotated `@static let f = fn;` reported a misleading error, because their
  initializers were folded before functions were declared. Such initializers
  are now deferred until functions exist, and the type is inferred from the
  function — so even a variadic like `println` aliases cleanly.

## [0.3.0] - 2026-06-29

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
- **Slices** — `slice<T>`, a builtin non-owning view `{ data: T*; length: uint64 }`
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
- **String-literal slice adaptation** — a string literal now *adapts* to a
  `slice<char>` (or `slice<const char>`) from context with no `as`, the way an
  untyped constant takes its type: at a function argument (including a
  `const`-by-reference slice parameter, so `writeln("hi")` works), a `let` slot,
  or a `return`. The borrow drops the trailing NUL; only literals adapt — a typed
  value still needs the explicit `as`. See [Strings](docs/language.md#strings).
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
- **Flexible array members** — a struct's last field may be written `field: T[]`
  with no size: a trailing run of `T` that adds **0** to `sizeof` and decays to a
  `T*` at the struct's tail, so one allocation holds a header plus a contiguous
  run of elements (the C `struct { int len; T data[]; }` idiom, without the
  `T[1]` "struct hack"). It must be the last field with `[]` as its only
  dimension; a struct ending in one cannot be an `extends` base, and the member
  cannot be set in a literal or borrowed as a `slice<T>` (its length is not
  static) — index it through its pointer. See [Structs](docs/language.md#structs)
  and [examples/flexible_array_members.mc](examples/flexible_array_members.mc).
- **`alignof` and `offsetof`** — two more compile-time `uint64` layout
  constants, the C counterparts of the same name. `alignof(T)` is a type's
  alignment in bytes (and, like `sizeof`, also accepts a variable —
  `alignof(v)`); `offsetof(struct S, field)` is a field's byte offset within a
  struct, honoring padding, `@packed`, and `@align`. Both fold at compile time,
  so they can size arrays and initialize a `const`. For a flexible array member,
  `offsetof(struct S, data)` is where its elements begin — the tight base for an
  allocation — and `alignof` counts the element type. See
  [Pointers](docs/language.md#pointers) and [Structs](docs/language.md#structs).
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

[Unreleased]: https://github.com/fecabrera/mcc/compare/v0.4.0...HEAD
[0.4.0]: https://github.com/fecabrera/mcc/compare/v0.3.1...v0.4.0
[0.3.1]: https://github.com/fecabrera/mcc/compare/v0.3.0...v0.3.1
[0.3.0]: https://github.com/fecabrera/mcc/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/fecabrera/mcc/compare/v0.1.2...v0.2.0
[0.1.2]: https://github.com/fecabrera/mcc/compare/v0.1.1...v0.1.2
[0.1.1]: https://github.com/fecabrera/mcc/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/fecabrera/mcc/releases/tag/v0.1.0
