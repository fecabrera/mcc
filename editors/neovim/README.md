# mcc support for Neovim

A [Neovim](https://neovim.io) (0.10+) plugin for [mcc](../../README.md) source
files (`.mc`) and interface stubs (`.mci`), reusing the
[tree-sitter grammar](../helix/tree-sitter-mcc/) that also powers the Helix
support:

- filetype detection and syntax highlighting (keywords, types,
  `@`-annotations, functions, parameters, strings/chars/numbers, operators),
- comment toggling with `gc` (line `//`, block `/* */`),
- four-space indent defaults, plus indent queries for nvim-treesitter,
- fold queries for `vim.treesitter.foldexpr()`,
- function/parameter text objects for nvim-treesitter-textobjects.

## Install

### 1. Put this directory on the runtime path

Clone the repository, then either add the directory directly:

```lua
vim.opt.runtimepath:append('/absolute/path/to/mcc/editors/neovim')
```

or hand it to your plugin manager as a local plugin (lazy.nvim shown):

```lua
{ dir = '/absolute/path/to/mcc/editors/neovim', name = 'mcc' }
```

This alone gives filetype detection, comment toggling, and indent settings.
Highlighting additionally needs the compiled parser.

### 2. Install the parser

**With nvim-treesitter** — register the grammar, then `:TSInstall mcc`.
On the `main` branch (needs the `tree-sitter` CLI):

```lua
require('nvim-treesitter.parsers').mcc = {
  install_info = {
    url = 'https://github.com/fecabrera/mcc',
    location = 'editors/helix/tree-sitter-mcc',
    branch = 'main',
  },
}
```

On the frozen `master` branch:

```lua
require('nvim-treesitter.parsers').get_parser_configs().mcc = {
  install_info = {
    url = 'https://github.com/fecabrera/mcc',
    files = { 'src/parser.c' },
    location = 'editors/helix/tree-sitter-mcc',
    branch = 'main',
  },
  filetype = 'mcc',
}
```

For local development against a checkout, use an absolute path as the `url`.

**Without any plugin** — the parser is a single checked-in C file, so any C
compiler can build it:

```bash
cd editors/helix/tree-sitter-mcc
cc -O2 -fPIC -shared -Isrc src/parser.c -o mcc.so
mkdir -p ~/.local/share/nvim/site/parser
mv mcc.so ~/.local/share/nvim/site/parser/
```

Open any `.mc` file; `:set filetype?` should report **mcc** and the buffer
should be highlighted (the `ftplugin` calls `vim.treesitter.start()`).

### 3. Optional extras

Tree-sitter folds:

```lua
vim.wo.foldmethod = 'expr'
vim.wo.foldexpr = 'v:lua.vim.treesitter.foldexpr()'
```

Tree-sitter indentation, with nvim-treesitter `main` installed:

```lua
vim.bo.indentexpr = "v:lua.require'nvim-treesitter'.indentexpr()"
```

(on `master`, enable its `indent` module instead). Text objects need
[nvim-treesitter-textobjects](https://github.com/nvim-treesitter/nvim-treesitter-textobjects);
this plugin ships the `@function.outer`/`.inner` and
`@parameter.outer`/`.inner` queries it reads.

## Files

- `ftdetect/mcc.lua` — maps `.mc`/`.mci` to the `mcc` filetype.
- `ftplugin/mcc.lua` — comments, indentation, starts highlighting.
- `queries/mcc/` — `highlights.scm`, `indents.scm`, `folds.scm`,
  `textobjects.scm`, written against Neovim's capture conventions (the Helix
  queries under the grammar's own `queries/` use Helix's names — the two sets
  are siblings, not copies).

The grammar itself lives in
[editors/helix/tree-sitter-mcc/](../helix/tree-sitter-mcc/); see the
[Helix README](../helix/README.md#the-grammar) for how to regenerate
`src/parser.c` after editing `grammar.js`.
