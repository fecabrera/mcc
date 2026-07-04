; Fold regions for vim.treesitter.foldexpr(): function bodies, type
; declaration blocks, inline assembly, and comment runs.

[
  (function_definition)
  (declaration_block)
  (field_list)
  (asm_block)
  (comment)
] @fold
