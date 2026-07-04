# Examples

A tour of the language, grouped into topical folders and ordered roughly by
complexity within each. Every example is runnable:

```bash
pipenv run python -m mcc examples/<folder>/<name>.mc --run
```

Start with [basics/helloworld.mc](basics/helloworld.mc) and read down; each
folder assumes the concepts from the ones above it.

## basics/

The smallest programs and the scalar core: printing, bindings, constants,
literals, and integer/float arithmetic.

| Example | Shows |
|---------|-------|
| [helloworld.mc](basics/helloworld.mc) | the smallest program: `import "std"`, `fn main`, `println` (see [helloworld-libc.mc](basics/helloworld-libc.mc) for the raw-libc `printf` version) |
| [variables.mc](basics/variables.mc) | `let`, type inference, annotations, every integer width, mutation, uninitialized `let x: T;` |
| [constants.mc](basics/constants.mc) | `const` compile-time constants, constant expressions, sizing arrays, string consts |
| [literals.mc](basics/literals.mc) | hexadecimal integer literals, `char` character literals and escapes |
| [arithmetic.mc](basics/arithmetic.mc) | operators, precedence, comparisons, `!`, float math, `abs` |
| [compound_assignment.mc](basics/compound_assignment.mc) | `+= -= *= /= %= &= \|= ^= <<= >>=`, target evaluated once, through variables/pointers/elements/fields, floats |
| [unsigned.mc](basics/unsigned.mc) | unsigned division/comparison semantics, zero-extension |

## control-flow/

Branching, looping, compile-time selection, and the scope-based constructs
(`defer`, block expressions, iteration).

| Example | Shows |
|---------|-------|
| [branching.mc](control-flow/branching.mc) | `if` / `else if` / `else`, integer (non-zero) conditions, the short-circuiting `and` / `or` logical operators |
| [while.mc](control-flow/while.mc) | `while` loops, `break` / `continue`, nested loops |
| [until.mc](control-flow/until.mc) | `until`, the inverse of `while`: loop while the condition is false |
| [conditional.mc](control-flow/conditional.mc) | `@if` / `@else` compile-time selection over `TARGET_OS` / `TARGET_ARCH`, `@symbol` per platform |
| [case_when.mc](control-flow/case_when.mc) | `case` / `when` / `else:` with no fall-through, integer and character subjects, multi-value arms |
| [defer.mc](control-flow/defer.mc) | `defer` cleanup at scope exit (return/break included), LIFO order, the block form |
| [block_expressions.mc](control-flow/block_expressions.mc) | `{ ...; emit v; }` as a value, contained temporaries, branch emits, `defer` inside |
| [iteration.mc](control-flow/iteration.mc) | `for x in` over the `_it`/`_next` protocol (list, set, dict), the builtin `pair<K, V>`, `break` / `continue` |
| [enumerate.mc](control-flow/enumerate.mc) | the builtin `enumerate` position counter — the `enumerated<T> { index; value }` it yields, and value-vs-`&` borrowing |
| [ranges.mc](control-flow/ranges.mc) | the builtin `range` — `for i in range(start, end)` / `range(end)` counting loops, lowered directly with no allocation, element type inferred from the bounds |

## functions/

Defining and calling functions: void/recursion, forward declarations, the
parameter modifiers (`const`, `mut`, `@noalias`, `@nonnull`), variadics, and
function pointers.

| Example | Shows |
|---------|-------|
| [functions.mc](functions/functions.mc) | void functions, any-order definitions, recursion, mutual recursion |
| [forward_declarations.mc](functions/forward_declarations.mc) | bodyless `fn` prototypes as forward declarations: a header-style prototype block with the definitions below, identical prototypes collapsing onto one declaration, the strict signature-match error; never required for ordering, but lets an imported `.mci` stub coexist with the `.mc` source |
| [const_params.mc](functions/const_params.mc) | `const` read-only parameters, structs passed by hidden reference (no copy), `const` on pointers vs values |
| [mut_params.mc](functions/mut_params.mc) | `mut` write-through parameters: out-params with no pointer in the signature, re-lending, struct field projection, a generic `swap<T>` |
| [mut_overloads.mc](functions/mut_overloads.mc) | generic overloads mixing `mut` and non-`mut` positions: a `mut` overload next to a pointer one, rvalues dropping `mut` candidates, writability judged against the chosen overload, single argument evaluation |
| [noalias.mc](functions/noalias.mc) | `@noalias` pointer parameters (C's `restrict`): the unchecked no-overlap promise that lets the optimizer treat a copy's regions as disjoint |
| [nonnull.mc](functions/nonnull.mc) | `@nonnull` pointer parameters: the checked "definitely non-null" refinement — call sites must prove the argument non-null, the callee skips the re-check |
| [nonnull_narrowing.mc](functions/nonnull_narrowing.mc) | flow-narrowing for @nonnull: the three null-check guard shapes (the `if (p != null)` then branch, the diverging early `if (p == null)` guard, `and`/`or` chains threading both) that prove a plain `T*` local with no `p!`, purely at compile time |
| [nonnull_loops.mc](functions/nonnull_loops.mc) | narrowed facts crossing loops: a loop kills only the facts it could invalidate (so guard-then-loop just works, in the body and past the exit), `while (p != null)` proves p on every iteration, and a `while (p == null)` retry loop proves p after it |
| [nonnull_assert.mc](functions/nonnull_assert.mc) | the postfix `p!` non-null assertion, @nonnull's escape hatch where narrowing cannot see the invariant: a zero-cost static proof for heap/returned pointers (null is then UB), one hatch at a `let` seeding all later uses, and the `!=` lexing gotcha |
| [variadic.mc](functions/variadic.mc) | variadic `...` definitions, `va_list`, `va_start`/`va_end`, forwarding to `vsnprintf` |
| [function_pointers.mc](functions/function_pointers.mc) | `fn(...) -> R` types (incl. variadic `fn(A, ...)`), callbacks in structs, dispatch tables, `const`/`@static` function aliases, `null` callbacks |

## types/

The type system: aliases, arrays and strings, enums, structs (and their
literals, flexible array members), unions, and generics, plus the compile-time
directives that check a build's invariants, its configuration, and its use of
deprecated or removed functions.

| Example | Shows |
|---------|-------|
| [type_aliases.mc](types/type_aliases.mc) | `type <name> = <type>;` transparent aliases for builtins, pointers, function pointers, and structs; `type` as an identifier |
| [arrays.mc](types/arrays.mc) | fixed-size `T[N]` arrays (`N` a constant expression), indexing, `sizeof`, pointer decay, multi-dim, a `@static` buffer |
| [strings.mc](types/strings.mc) | string literals as `char[N]` text arrays (NUL counted): owned vs `char*`, inferred/oversize sizes, decay, mutation, `len`, indexing, borrowing as `slice<char>`; contrast with a raw `uint8[N]` byte buffer |
| [enums.mc](types/enums.mc) | `enum Name: T { M = v, ... }`, `Enum::Member`, the enum name as a type, custom underlying types (uint64 flags, string members), members referencing earlier ones |
| [derived_enums.mc](types/derived_enums.mc) | `enum b: a` member reuse: the derived enum copies the base's members and adopts its underlying type (pointers too), inherited members fold equal and are referenceable by new ones, transitive chains; compile-time only, no new type checking |
| [structs.mc](types/structs.mc) | structs, generic structs, `->` / `.`, `null`, struct literals, a hand-built linked list |
| [struct_literals.mc](types/struct_literals.mc) | `Name { field = value, ... }` literals (the `struct` keyword optional): omitted fields zeroed or set to a `= default`, free field order, generics (inferred type args), nesting, as args/returns/through a pointer |
| [flexible_array_members.mc](types/flexible_array_members.mc) | a trailing `field: T[]` flexible array member: adds 0 to `sizeof`, decays to a `T*` at the struct's tail, one allocation for header plus elements |
| [unions.mc](types/unions.mc) | `union Name { ... }` members sharing one storage (all at offset 0): literals with one live member, cross-member byte reinterpretation (float bit patterns), generic unions |
| [static_assert.mc](types/static_assert.mc) | the top-level compile-time directives `@static_assert(cond, "msg")` and `@error("msg")`: guarding a struct's layout with `sizeof`/`offsetof`/`alignof`, `const`- and `Enum::Member`-based checks, and an `@error` guarded by a dead `@if` branch to reject a target |
| [warnings.mc](types/warnings.mc) | `@warning("msg")`, `@error`'s non-fatal twin: `-D`-gated `@if` branches flagging a suspect build configuration without rejecting it, the collect-then-print warning channel, and the `-Werror` flag promoting warnings to a failed build |
| [deprecated.mc](types/deprecated.mc) | the `@deprecated("msg")` function attribute: a renamed function kept as a still-callable forwarder, every call site (and fn-as-value use) warning with the migration message, the old-API calls behind a `-D`-gated `@if` branch, `-Werror` promotion |
| [removed.mc](types/removed.mc) | the `@removed("msg")` tombstone, the lifecycle step after `@deprecated`: a bodiless declaration (the one bodiless generic allowed) turning every call into a hard compile error with the migration message, the erroring old-API call behind a `-D`-gated `@if` branch |
| [generics.mc](types/generics.mc) | type inference, generic recursion, multiple type parameters |

## memory/

Pointers and the builtin container/view types built on them.

| Example | Shows |
|---------|-------|
| [pointers.mc](memory/pointers.mc) | `import`, heap allocation, `&` `*` `[]`, `sizeof`, `as` casts |
| [slices.mc](memory/slices.mc) | the builtin `slice<T>` view: borrowing a `list<T>` or `T[N]` with `as`, `.length`, indexing, `for x in`, passing by value, writing through |
| [lists.mc](memory/lists.mc) | `list<T>`, a growable random-access sequence: `push`, `get` (mut out-param), `from_array`, `append`, `duplicate` |
| [stacks.mc](memory/stacks.mc) | `stack<T>`, a growable LIFO: push and pop at the top |
| [queues.mc](memory/queues.mc) | `queue<T>`, a growable FIFO ring buffer: push at the back, pop from the front |
| [nonnull_heap_buffers.mc](memory/nonnull_heap_buffers.mc) | a heap buffer crossing the stdlib's @nonnull contracts (memory copy/fill family, hashing digests): one diverging null guard after `alloc` covers every later call, the loops that leave the buffer alone included |

## systems/

Interfacing with the outside world: libc, formatted I/O, shipping compiled
libraries, and inline assembly.

| Example | Shows |
|---------|-------|
| [extern.mc](systems/extern.mc) | `@extern` functions (including variadic `...`), interfacing with libc |
| [io.mc](systems/io.mc) | printf format specifiers, `puts`, `putchar`, string escapes |
| [interfaces.mc](systems/interfaces.mc) | bodyless `fn` prototypes (the mcc-convention counterpart to `@extern`) and the `--emit-interface` / `.mci` flow for shipping a compiled library: `mut`/`const`-struct exports, what ships as a prototype vs in full, the compile-then-link consumer side |
| [inline_asm.mc](systems/inline_asm.mc) | `@asm fn` and the `@asm(...)` expression, `$out`/`$N` operands and `:w` register modifiers, gated by `@if` on `TARGET_ARCH` |

## programs/

Complete little programs that put the pieces together.

| Example | Shows |
|---------|-------|
| [fizzbuzz.mc](programs/fizzbuzz.mc) | the classic, with `%` and an `else if` chain |
| [primes.mc](programs/primes.mc) | trial division: bool-returning helper, nested loops |

## baremetal/

The exception to "runnable with `--run`": [baremetal/](baremetal/) is a
freestanding qemu kernel cross-compiled with `--target`, with `@volatile`
MMIO and its own build instructions.
