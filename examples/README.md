# Examples

A tour of the language, one feature per file. Every example is runnable:

```bash
pipenv run python -m mcc examples/<name>.mc --run
```

| Example | Shows |
|---------|-------|
| [helloworld.mc](helloworld.mc) | the smallest program: `#include`, `fn main`, `printf` |
| [variables.mc](variables.mc) | `let`, type inference, annotations, every integer width, mutation |
| [arithmetic.mc](arithmetic.mc) | operators, precedence, comparisons, `!`, float math, `abs` |
| [control_flow.mc](control_flow.mc) | `if` / `else if` / `else`, integer conditions, `while`, `until`, nested loops |
| [functions.mc](functions.mc) | void functions, any-order definitions, recursion, mutual recursion |
| [io.mc](io.mc) | printf format specifiers, `puts`, `putchar`, string escapes |
| [unsigned.mc](unsigned.mc) | unsigned division/comparison semantics, zero-extension |
| [templates.mc](templates.mc) | generic functions with explicit instantiation |
| [structs.mc](structs.mc) | structs, generics structs, `->` / `.`, `null`, linked list, the array lib |
| [pointers.mc](pointers.mc) | `import`, heap allocation, `&` `*` `[]`, `sizeof`, `as` casts |
| [generics.mc](generics.mc) | type inference, generic recursion, multiple type parameters |
| [fizzbuzz.mc](fizzbuzz.mc) | the classic, with `%` and an `else if` chain |
| [primes.mc](primes.mc) | trial division: bool-returning helper, nested loops |
