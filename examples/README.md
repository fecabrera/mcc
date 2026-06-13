# Examples

A tour of the language, one feature per file. Every example is runnable:

```bash
pipenv run python -m mcc examples/<name>.mc --run
```

| Example | Shows |
|---------|-------|
| [helloworld.mc](helloworld.mc) | the smallest program: `#include`, `fn main`, `printf` |
| [variables.mc](variables.mc) | `let`, type inference, annotations, every integer width, mutation, uninitialized `let x: T;` |
| [constants.mc](constants.mc) | `const` compile-time constants, constant expressions, sizing arrays, string consts |
| [literals.mc](literals.mc) | hexadecimal integer literals, `uint8` character literals and escapes |
| [arithmetic.mc](arithmetic.mc) | operators, precedence, comparisons, `!`, float math, `abs` |
| [control_flow.mc](control_flow.mc) | `if` / `else if` / `else`, integer conditions, `and` / `or`, `while`, `until`, nested loops, `break` / `continue` |
| [case_when.mc](case_when.mc) | `case` / `when` / `else:` with no fall-through, integer and character subjects |
| [functions.mc](functions.mc) | void functions, any-order definitions, recursion, mutual recursion |
| [function_pointers.mc](function_pointers.mc) | `fn(...) -> R` types, callbacks in structs, dispatch tables, `null` callbacks |
| [arrays.mc](arrays.mc) | fixed-size `T[N]` arrays, indexing, `sizeof`, pointer decay, multi-dim, a `@static` buffer |
| [io.mc](io.mc) | printf format specifiers, `puts`, `putchar`, string escapes |
| [unsigned.mc](unsigned.mc) | unsigned division/comparison semantics, zero-extension |
| [extern.mc](extern.mc) | `@extern` functions (including variadic `...`), interfacing with libc |
| [structs.mc](structs.mc) | structs, generics structs, `->` / `.`, `null`, linked list, the array lib |
| [pointers.mc](pointers.mc) | `import`, heap allocation, `&` `*` `[]`, `sizeof`, `as` casts |
| [generics.mc](generics.mc) | type inference, generic recursion, multiple type parameters |
| [fizzbuzz.mc](fizzbuzz.mc) | the classic, with `%` and an `else if` chain |
| [primes.mc](primes.mc) | trial division: bool-returning helper, nested loops |

The exception to "runnable with `--run`": [baremetal/](baremetal/) is a
freestanding qemu kernel cross-compiled with `--target`, with `@volatile`
MMIO and its own build instructions.
