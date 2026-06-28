# Changelog

All notable changes to mcc are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and the project follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html)
(pre-1.0: new language/tooling features bump the minor version).

## [Unreleased]

### Added

- **Slices** — `slice<T>`, a builtin non-owning view `{ ptr: T*; length: uint64 }`
  over a contiguous run of `T`, with a runtime `.length`, indexing `s[i]`, and
  native `for x in s` iteration. Constructed by an explicit borrow — `xs as
  slice<T>` from an owned `list<T>` (reads `{data, length}`, drops `capacity`) or
  a fixed array `T[N]` (`{&arr[0], N}`). See [Slices](docs/language.md#slices) and
  [examples/slices.mc](examples/slices.mc).

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
