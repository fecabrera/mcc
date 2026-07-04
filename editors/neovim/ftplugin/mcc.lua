-- Buffer-local settings for mcc source files (.mc) and interface stubs (.mci).

-- Comment toggling (gc) and formatting.
vim.bo.commentstring = '// %s'
vim.bo.comments = 's1:/*,mb:*,ex:*/,://'

-- Four-space indentation, matching the stdlib and examples.
vim.bo.expandtab = true
vim.bo.shiftwidth = 4
vim.bo.softtabstop = 4

-- Start tree-sitter highlighting when the mcc parser is installed; without
-- it the pcall swallows the error and the buffer stays unhighlighted.
pcall(vim.treesitter.start)
