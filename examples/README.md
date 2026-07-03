# Examples

A tour of the language, one feature per file. Every example is runnable:

```bash
pipenv run python -m mcc examples/<name>.mc --run
```

| Example | Shows |
|---------|-------|
| [helloworld.mc](helloworld.mc) | the smallest program: `import "std"`, `fn main`, `println` (see [helloworld-libc.mc](helloworld-libc.mc) for the raw-libc `printf` version) |
| [variables.mc](variables.mc) | `let`, type inference, annotations, every integer width, mutation, uninitialized `let x: T;` |
| [constants.mc](constants.mc) | `const` compile-time constants, constant expressions, sizing arrays, string consts |
| [conditional.mc](conditional.mc) | `@if` / `@else` compile-time selection over `TARGET_OS` / `TARGET_ARCH`, `@symbol` per platform |
| [literals.mc](literals.mc) | hexadecimal integer literals, `char` character literals and escapes |
| [arithmetic.mc](arithmetic.mc) | operators, precedence, comparisons, `!`, float math, `abs` |
| [compound_assignment.mc](compound_assignment.mc) | `+= -= *= /= %= &= \|= ^= <<= >>=`, target evaluated once, through variables/pointers/elements/fields, floats |
| [control_flow.mc](control_flow.mc) | `if` / `else if` / `else`, integer conditions, `and` / `or`, `while`, `until`, nested loops, `break` / `continue` |
| [case_when.mc](case_when.mc) | `case` / `when` / `else:` with no fall-through, integer and character subjects, multi-value arms |
| [defer.mc](defer.mc) | `defer` cleanup at scope exit (return/break included), LIFO order, the block form |
| [block_expressions.mc](block_expressions.mc) | `{ ...; emit v; }` as a value, contained temporaries, branch emits, `defer` inside |
| [iteration.mc](iteration.mc) | `for x in` over the iter/next protocol (array, set, dict), the builtin `enumerate` position counter, the builtin `iterator<T>`/`pair<K, V>` structs, `break`/`continue`, bare `{ }` block scopes |
| [ranges.mc](ranges.mc) | the builtin `range` — `for i in range(start, end)` / `range(end)` counting loops, lowered directly with no allocation, element type inferred from the bounds |
| [functions.mc](functions.mc) | void functions, any-order definitions, recursion, mutual recursion |
| [const_params.mc](const_params.mc) | `const` read-only parameters, structs passed by hidden reference (no copy), `const` on pointers vs values |
| [mut_params.mc](mut_params.mc) | `mut` write-through parameters: out-params with no pointer in the signature, re-lending, struct field projection, a generic `swap<T>` |
| [noalias.mc](noalias.mc) | `@noalias` pointer parameters (C's `restrict`): the unchecked no-overlap promise that lets the optimizer treat a copy's regions as disjoint |
| [variadic.mc](variadic.mc) | variadic `...` definitions, `va_list`, `va_start`/`va_end`, forwarding to `vsnprintf` |
| [function_pointers.mc](function_pointers.mc) | `fn(...) -> R` types (incl. variadic `fn(A, ...)`), callbacks in structs, dispatch tables, `const`/`@static` function aliases, `null` callbacks |
| [type_aliases.mc](type_aliases.mc) | `type <name> = <type>;` transparent aliases for builtins, pointers, function pointers, and structs; `type` as an identifier |
| [arrays.mc](arrays.mc) | fixed-size `T[N]` arrays (`N` a constant expression), indexing, `sizeof`, pointer decay, multi-dim, a `@static` buffer |
| [io.mc](io.mc) | printf format specifiers, `puts`, `putchar`, string escapes |
| [strings.mc](strings.mc) | string literals as `char[N]` text arrays (NUL counted): owned vs `char*`, inferred/oversize sizes, decay, mutation, `len`, indexing, borrowing as `slice<char>`; contrast with a raw `uint8[N]` byte buffer |
| [unsigned.mc](unsigned.mc) | unsigned division/comparison semantics, zero-extension |
| [extern.mc](extern.mc) | `@extern` functions (including variadic `...`), interfacing with libc |
| [inline_asm.mc](inline_asm.mc) | `@asm fn` and the `@asm(...)` expression, `$out`/`$N` operands and `:w` register modifiers, gated by `@if` on `TARGET_ARCH` |
| [enums.mc](enums.mc) | `enum Name: T { M = v, ... }`, `Enum::Member`, the enum name as a type, custom underlying types (uint64 flags, string members), members referencing earlier ones |
| [structs.mc](structs.mc) | structs, generic structs, `->` / `.`, `null`, struct literals, a hand-built linked list |
| [struct_literals.mc](struct_literals.mc) | `Name { field = value, ... }` literals (the `struct` keyword optional): omitted fields zeroed or set to a `= default`, free field order, generics (inferred type args), nesting, as args/returns/through a pointer |
| [flexible_array_members.mc](flexible_array_members.mc) | a trailing `field: T[]` flexible array member: adds 0 to `sizeof`, decays to a `T*` at the struct's tail, one allocation for header plus elements |
| [unions.mc](unions.mc) | `union Name { ... }` members sharing one storage (all at offset 0): literals with one live member, cross-member byte reinterpretation (float bit patterns), generic unions |
| [data_structures.mc](data_structures.mc) | the growable lib containers: `list<T>`, `stack<T>` (LIFO), `queue<T>` (FIFO ring buffer) |
| [slices.mc](slices.mc) | the builtin `slice<T>` view: borrowing a `list<T>` or `T[N]` with `as`, `.length`, indexing, `for x in`, passing by value, writing through |
| [pointers.mc](pointers.mc) | `import`, heap allocation, `&` `*` `[]`, `sizeof`, `as` casts |
| [generics.mc](generics.mc) | type inference, generic recursion, multiple type parameters |
| [fizzbuzz.mc](fizzbuzz.mc) | the classic, with `%` and an `else if` chain |
| [primes.mc](primes.mc) | trial division: bool-returning helper, nested loops |

The exception to "runnable with `--run`": [baremetal/](baremetal/) is a
freestanding qemu kernel cross-compiled with `--target`, with `@volatile`
MMIO and its own build instructions.
