# mcc support for Helix

A [Helix](https://helix-editor.com) configuration for [mcc](../../README.md)
source files (`.mc`), with a tree-sitter grammar for syntax highlighting:

- syntax highlighting (keywords, types, `@`-annotations, functions, parameters,
  strings/chars/numbers, operators) via [tree-sitter-mcc](tree-sitter-mcc/),
- comment toggling with `gc` (line `//`, block `/* */`),
- auto-indentation and bracket/quote auto-pairs,
- `[f]`/`]f` and `mi`/`ma` text objects for functions and parameters.

## Install

Helix builds tree-sitter grammars itself and reads queries from its runtime
directory, so installing takes three steps. Paths below are for Linux/macOS
(`~/.config/helix/`); on Windows use `%AppData%\helix\`.

### 1. Add the language and grammar

Append the `[[language]]` and `[[grammar]]` blocks from
[languages.toml](languages.toml) to `~/.config/helix/languages.toml` (create the
file if it does not exist).

The grammar source points at this repository. For local development against a
checkout, replace the source line with a path instead:

```toml
[[grammar]]
name = "mcc"
source = { path = "/absolute/path/to/mcc/editors/helix/tree-sitter-mcc" }
```

### 2. Install the highlight queries

Helix loads queries from its runtime directory, not from the grammar repo, so
copy them in:

```bash
mkdir -p ~/.config/helix/runtime/queries/mcc
cp editors/helix/tree-sitter-mcc/queries/*.scm ~/.config/helix/runtime/queries/mcc/
```

### 3. Fetch and build the grammar

```bash
hx --grammar fetch
hx --grammar build
```

Open any `.mc` file; `:lang` should report **mcc** and the buffer should be
highlighted. (`comment-tokens` / `block-comment-tokens` need Helix 24.03 or
newer; on older versions replace them with `comment-token = "//"`.)

## The grammar

[tree-sitter-mcc/](tree-sitter-mcc/) is a standalone tree-sitter grammar that
mirrors the compiler's parser (`mcc/parser.py`). The checked-in `src/parser.c`
is what Helix compiles; regenerate it after editing `grammar.js` with:

```bash
cd editors/helix/tree-sitter-mcc
tree-sitter generate
tree-sitter parse ../../../examples/structs.mc   # sanity-check a file
```

One known limitation: a cast immediately followed by a multiplication without
parentheses (`x as int32 * y`) parses as a pointer type; write `(x as int32) * y`.
Pointer casts (`x as int32*`) — by far the common case — highlight correctly.

## Files

- `languages.toml` — the `mcc` language definition and grammar source.
- `tree-sitter-mcc/grammar.js` — the grammar.
- `tree-sitter-mcc/queries/` — `highlights.scm`, `indents.scm`, `textobjects.scm`.
- `tree-sitter-mcc/src/` — the generated parser Helix compiles.
