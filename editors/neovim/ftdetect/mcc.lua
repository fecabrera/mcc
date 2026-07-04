-- Filetype detection for mcc source files (.mc) and interface stubs (.mci).
vim.filetype.add({
  extension = {
    mc = 'mcc',
    mci = 'mcc',
  },
})
