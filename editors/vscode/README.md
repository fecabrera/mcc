# mcc syntax highlighting for VS Code

A TextMate grammar that highlights [mcc](../../README.md) source files (`.mc`):
keywords (`fn`, `let`, `defer`, `until`, `case`/`when`, …), the `intN`/`uintN`/
`bool`/`float64`/`va_list` types, `@`-annotations (`@extern`, `@private`,
`@symbol`, …), function definitions and calls, and string/char/number literals
with escapes.

It is a declarative extension — no build step.

## Install

### Symlink into your extensions folder (quickest)

```bash
ln -s "$(pwd)/editors/vscode" ~/.vscode/extensions/mcc-language
```

Then run **Developer: Reload Window** (or restart VS Code). Open any `.mc`
file; the language indicator in the status bar should read **mcc**.

(Use `cp -r` instead of `ln -s` if you prefer a copy.)

### Package as a `.vsix`

```bash
cd editors/vscode
npx @vscode/vsce package
code --install-extension mcc-language-0.1.0.vsix
```

### Develop / iterate on the grammar

Open the `editors/vscode/` folder in VS Code and press **F5** to launch an
Extension Development Host with the extension loaded. Edit
`syntaxes/mcc.tmLanguage.json` and reload to see changes. The command
**Developer: Inspect Editor Tokens and Scopes** shows the scope under the
cursor, which is handy for tuning.

## Files

- `package.json` — declares the `mcc` language and the `.mc` association.
- `language-configuration.json` — comments, brackets, auto-closing pairs.
- `syntaxes/mcc.tmLanguage.json` — the grammar (scope `source.mcc`).
