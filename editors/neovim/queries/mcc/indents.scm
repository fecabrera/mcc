; Indent queries in nvim-treesitter's format (the indent module comes from
; the nvim-treesitter plugin; core Neovim does not read this file).

[
  (block)
  (block_expression)
  (declaration_block)
  (field_list)
  (parameter_list)
  (argument_list)
  (asm_block)
  (case_statement)
] @indent.begin

[
  "}"
  ")"
  "]"
] @indent.branch @indent.end
