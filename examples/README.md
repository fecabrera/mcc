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

Branching, looping, compile-time selection, the never-executed-path
assertion (`unreachable`), the scope-based constructs (`defer`, block
expressions, iteration), and the opt-in dead-code report on statements the
diverging constructs make unreachable.

| Example | Shows |
|---------|-------|
| [branching.mc](control-flow/branching.mc) | `if` / `else if` / `else`, integer (non-zero) conditions, the short-circuiting `and` / `or` logical operators |
| [while.mc](control-flow/while.mc) | `while` loops, `break` / `continue`, nested loops |
| [until.mc](control-flow/until.mc) | `until`, the inverse of `while`: loop while the condition is false |
| [conditional.mc](control-flow/conditional.mc) | `@if` / `@else` compile-time selection over `TARGET_OS` / `TARGET_ARCH`, `@symbol` per platform |
| [case_when.mc](control-flow/case_when.mc) | `case` / `when` / `else:` with no fall-through, integer and character subjects, multi-value arms |
| [unreachable.mc](control-flow/unreachable.mc) | the `unreachable;` statement asserting a path never executes (UB if reached): `else: unreachable;` marking a `case` exhaustive, no dummy trailing return needed after it |
| [defer.mc](control-flow/defer.mc) | `defer` cleanup at scope exit (return/break included), LIFO order, the block form |
| [block_expressions.mc](control-flow/block_expressions.mc) | `{ ...; emit v; }` as a value, contained temporaries, branch emits, `defer` inside |
| [dead_code.mc](control-flow/dead_code.mc) | the opt-in `-Wdead-code` class reporting statements the generator silently drops: one warning per dead region (at its first statement) after `return` / `break` / `continue` / `unreachable` / `emit` / a `@noreturn` call / an all-paths-diverging `if`, a dead `defer` that never registers, and the deliberate `while (true)` non-case |
| [iteration.mc](control-flow/iteration.mc) | `for x in` over the `_it`/`_next` protocol (list, set, dict), the builtin `pair<K, V>`, `break` / `continue` |
| [enumerate.mc](control-flow/enumerate.mc) | the builtin `enumerate` position counter — the `enumerated<T> { index; value }` it yields, and value-vs-`&` borrowing |
| [ranges.mc](control-flow/ranges.mc) | the builtin `range` — `for i in range(start, end)` / `range(end)` counting loops, lowered directly with no allocation, element type inferred from the bounds |

## functions/

Defining and calling functions: void/recursion, forward declarations, the
parameter modifiers (`const`, `mut`, `@noalias`, `@nonnull`), `mut` returns
(functions returning lvalues), overload sets
(concrete, generic, mixed, and open across modules), never-returning
functions (`@noreturn`),
variadics (C `...` and native collecting), and function pointers.

| Example | Shows |
|---------|-------|
| [functions.mc](functions/functions.mc) | void functions, any-order definitions, recursion, mutual recursion |
| [forward_declarations.mc](functions/forward_declarations.mc) | bodyless `fn` prototypes as forward declarations: a header-style prototype block with the definitions below, identical prototypes collapsing onto one declaration, the strict signature-match error; never required for ordering, but lets an imported `.mci` stub coexist with the `.mc` source |
| [const_params.mc](functions/const_params.mc) | `const` read-only parameters, structs passed by hidden reference (no copy), `const` on pointers vs values |
| [mut_params.mc](functions/mut_params.mc) | `mut` write-through parameters: out-params with no pointer in the signature, re-lending, struct field projection, a generic `swap<T>` |
| [mut_returns.mc](functions/mut_returns.mc) | `-> mut T` functions returning lvalues: an `_at`-style accessor whose call is assignable, compound-assignable (addressed once), projectable, re-lendable as a `mut` argument, and auto-loading in value context, plus the formation rule rooting the reference in a mut/pointer parameter or a global |
| [mut_overloads.mc](functions/mut_overloads.mc) | generic overloads mixing `mut` and non-`mut` positions: a `mut` overload next to a pointer one, rvalues dropping `mut` candidates, writability judged against the chosen overload, single argument evaluation |
| [overloading.mc](functions/overloading.mc) | concrete function overloading: a constructor-flavored `counter_init` family dispatched by arity and by argument type, a string literal still adapting to a `slice<const char>` member, the must-differ-in-parameter-types rule |
| [mixed_overloads.mc](functions/mixed_overloads.mc) | a generic template and concrete functions sharing one name: the (is-concrete, specificity) rank, concrete fast paths winning exact matches, the generic catch-all covering the rest, explicit type args selecting among the generic candidates only |
| [open_overloads.mc](functions/open_overloads.mc) | open overload sets: a module making its own struct appendable by declaring one `string_append` overload into the stdlib's foreign concrete set, the whole-program union at import merge, members from two modules resolving side by side, the join that used to be a duplicate-definition error |
| [pointer_decay.mc](functions/pointer_decay.mc) | a proven-non-null `T*` decaying into a `const`-struct or `mut` slot: one call shape for a stack value and a null-guarded heap pointer, rvalue `&x` decaying too, the narrowed fact surviving the call |
| [noalias.mc](functions/noalias.mc) | `@noalias` pointer parameters (C's `restrict`): the unchecked no-overlap promise that lets the optimizer treat a copy's regions as disjoint |
| [nonnull.mc](functions/nonnull.mc) | `@nonnull` pointer parameters: the checked "definitely non-null" refinement — call sites must prove the argument non-null, the callee skips the re-check |
| [nonnull_narrowing.mc](functions/nonnull_narrowing.mc) | flow-narrowing for @nonnull: the three null-check guard shapes (the `if (p != null)` then branch, the diverging early `if (p == null)` guard, `and`/`or` chains threading both) that prove a plain `T*` local with no `p!`, purely at compile time |
| [nonnull_loops.mc](functions/nonnull_loops.mc) | narrowed facts crossing loops: a loop kills only the facts it could invalidate (so guard-then-loop just works, in the body and past the exit), `while (p != null)` proves p on every iteration, and a `while (p == null)` retry loop proves p after it |
| [nonnull_projections.mc](functions/nonnull_projections.mc) | flow-narrowing for field projections: the same guards prove a pointer-typed struct field (`b->data`, keyed by access path at any depth) into @nonnull slots, the call write-effect refinement (a call kills the fact unless the callee is proven transitively write-free; stores and loop entry always kill), and `let q = b->data;` binding a checked field to a name fact that survives writing calls and loops |
| [nonnull_assert.mc](functions/nonnull_assert.mc) | the postfix `p!` non-null assertion, @nonnull's escape hatch where narrowing cannot see the invariant: a zero-cost static proof for heap/returned pointers (null is then UB), one hatch at a `let` seeding all later uses, and the `!=` lexing gotcha |
| [noreturn.mc](functions/noreturn.mc) | `@noreturn` void functions that never return: a panic-style exit helper, calls diverging like a `return` (no dummy return after), the C-idiomatic `if (p == null) abort();` guard narrowing into @nonnull, the legal `while (true)` spin body, defers skipped at the call |
| [variadic.mc](functions/variadic.mc) | variadic `...` definitions, `va_list`, `va_start`/`va_end`, forwarding to `vsnprintf` |
| [native_variadics.mc](functions/native_variadics.mc) | native variadic collection: `args...` as sugar for a trailing `const args: slice<const any>`, extras boxed caller-side into a read-only slice walked with `for` + `case type`, zero extras giving an empty slice, the explicit spelling collecting the same |
| [function_pointers.mc](functions/function_pointers.mc) | `fn(...) -> R` types (incl. variadic `fn(A, ...)`), callbacks in structs, dispatch tables, `const`/`@static` function aliases, `null` callbacks |

## types/

The type system: aliases, arrays and strings, enums, structs (and their
literals, `extends` bases, flexible array members), unions, tuples, the `any`
box, the `typename` builtin, and generics, plus the compile-time
directives that check a build's invariants, its configuration, and its use of
deprecated or removed functions, and the opt-in warning classes that report
on legal-but-unproven code.

| Example | Shows |
|---------|-------|
| [type_aliases.mc](types/type_aliases.mc) | `type <name> = <type>;` transparent aliases for builtins, pointers, function pointers, and structs; `type` as an identifier |
| [generic_alias.mc](types/generic_alias.mc) | `type entry<T> = pair<char*, T>;` generic aliases: a type-parameter list naming a family of types, staying transparent (`entry<int32>` *is* `pair<char*, int32>`, one shared instantiation), a `cmp<T>` comparator shape, an inert unused parameter (unlike a struct's), and a defaulted alias parameter |
| [arrays.mc](types/arrays.mc) | fixed-size `T[N]` arrays (`N` a constant expression), indexing, `sizeof`, pointer decay, multi-dim, a `@static` buffer |
| [strings.mc](types/strings.mc) | string literals as `char[N]` text arrays (NUL counted): owned vs `char*`, inferred/oversize sizes, decay, mutation, `len`, indexing, borrowing as `slice<char>`; contrast with a raw `uint8[N]` byte buffer |
| [string_tables.mc](types/string_tables.mc) | string-literal elements adapting to `slice<char>` / `slice<const char>` with no per-element `as`: a local lookup table, a `@static` table and scalar as constant `{pointer, length}` views, NUL-free lengths, runtime indexing |
| [enums.mc](types/enums.mc) | `enum Name: T { M = v, ... }`, `Enum::Member`, the enum name as a type, custom underlying types (uint64 flags, string members), members referencing earlier ones |
| [derived_enums.mc](types/derived_enums.mc) | `enum b: a` member reuse: the derived enum copies the base's members and adopts its underlying type (pointers too), inherited members fold equal and are referenceable by new ones, transitive chains; compile-time only, no new type checking |
| [structs.mc](types/structs.mc) | structs, generic structs, `->` / `.`, `null`, struct literals, a hand-built linked list |
| [struct_literals.mc](types/struct_literals.mc) | `Name { field = value, ... }` literals (the `struct` keyword optional): omitted fields zeroed or set to a `= default`, free field order, generics (inferred type args), nesting, as args/returns/through a pointer, a string/array literal borrowing into a slice-typed field (never driving generic inference), and the bare, type-inferred `{ field = value, ... }` form that drops the name where context fixes the struct type (let/assignment/return/argument/element/nested field, overloads resolved by field names) |
| [extends.mc](types/extends.mc) | named-base `struct point3 extends point`: base fields laid out first as a true prefix, inherited fields named directly (in literals too), the explicit pointer/value upcasts, base field defaults carrying into a derived literal, bodyless specialization as a branding type |
| [generic_extends.mc](types/generic_extends.mc) | a generic struct extending a generic base built from its own parameters (`entry<K, V> extends cell<K, V>`), monomorphized together per instantiation; the per-instantiation upcast, and the literal caveat: inference sees only the extender's own fields, so base fields need explicit type args |
| [flexible_array_members.mc](types/flexible_array_members.mc) | a trailing `field: T[]` flexible array member: adds 0 to `sizeof`, decays to a `T*` at the struct's tail, one allocation for header plus elements |
| [unions.mc](types/unions.mc) | `union Name { ... }` members sharing one storage (all at offset 0): literals with one live member, cross-member byte reinterpretation (float bit patterns), generic unions |
| [static_initializers.mc](types/static_initializers.mc) | `@static` globals initialized with struct and union literals, folded to data constants: omitted/`= default` fields, nested struct/array fields, a union member narrower than the widest (zero-padded to the union's size), an empty `u{}`, struct-inside-union, and generic aggregates |
| [tuples.mc](types/tuples.mc) | `tuple<A, B, ...>`, the ad-hoc unnamed product type: paren literals `(a, b)` with per-position struct-literal coercion, multiple return values as the headline, compile-time-constant bounds-checked indexing with lvalue elements, uninitialized declaration and whole-value copies, `const` params by hidden reference, generic inference through the shape, a `type` alias naming a shape, arrays of tuples, struct layout under `sizeof`, the `<tuple<...>>` println fallback |
| [any.mc](types/any.mc) | the builtin `any` tagged box (24 bytes: tag + payload): implicit boxing at assignment/argument/return, the primitive/pointer/slice boxable set (each pointer type its own tag), recovery only via `case type` with its mandatory `else`, boxing `&s` as the struct escape hatch, `@static` globals folding a constant initializer into a constant tagged box under the same tags |
| [case_type_groups.mc](types/case_type_groups.mc) | multi-type `case type` arms: comma-listed types over one binding sharing one body, the binding an implicit generic compiled once per listed type, an overload set resolving per copy (`width(n)` picks a different member per width), the duplicate-type rejection, `else` still mandatory |
| [generic_case_arms.mc](types/generic_case_arms.mc) | generic `case type` arms: an unresolved bare name introducing an arm-scoped type parameter, `when T* ptr:` catching every unclaimed pointer tag (T bound to the pointee) and `when T v:` every remaining tag, bodies monomorphized per tag from the whole program's boxed set, first-match-wins order carving concrete arms out of the fallbacks, the zero-filled `any` keeping `else` mandatory |
| [with_unwrap.mc](types/with_unwrap.mc) | the `with` statement, single-pattern sugar over a one-arm `case type`: `with (n = v as int32) body; else other;` with the binding scoped to the true branch, the `else` optional (an unmatched tag, tag 0 included, takes it, or falls through a lone `with` doing nothing), generic `T` / `T*` patterns per the case-type arm rule monomorphized per boxed tag, and the trailing-return caveat those share |
| [any_struct_boxing.mc](types/any_struct_boxing.mc) | a struct boxing into a `const any` by hidden reference: the payload a pointer to the value's storage tagged as the struct itself (`point`, not `point*`), recovered by a `when point p:` arm or `with (p = xs[0] as point)` as a read-only alias with no copy, passed on to a `const value: point` function sharing that storage, boxed from the archetypal `slice<const any>` a variadic collects; the distinct `point` vs `point*` tags, and the still-rejected owning box / union / array |
| [typename.mc](types/typename.mc) | the `typename` builtin recovering a type's canonical name as a compile-time string: type and in-scope-variable operands (never evaluated, `sizeof`-style), generic instantiations spelled with their type args, top-level `const` stripping, folding into a `const` initializer, the static-type rule (`any` names as "any"), `typename(T)` per generic instantiation, and generic `case type` arms naming a box's dynamic type statically |
| [static_assert.mc](types/static_assert.mc) | the top-level compile-time directives `@static_assert(cond, "msg")` and `@error("msg")`: guarding a struct's layout with `sizeof`/`offsetof`/`alignof`, `const`- and `Enum::Member`-based checks, and an `@error` guarded by a dead `@if` branch to reject a target |
| [warnings.mc](types/warnings.mc) | `@warning("msg")`, `@error`'s non-fatal twin: `-D`-gated `@if` branches flagging a suspect build configuration without rejecting it, the collect-then-print warning channel, and the `-Werror` flag promoting warnings to a failed build |
| [deprecated.mc](types/deprecated.mc) | the `@deprecated("msg")` function attribute: a renamed function kept as a still-callable forwarder, every call site (and fn-as-value use) warning with the migration message, the old-API calls behind a `-D`-gated `@if` branch, `-Werror` promotion |
| [removed.mc](types/removed.mc) | the `@removed("msg")` tombstone, the lifecycle step after `@deprecated`: a bodiless declaration (the one bodiless generic allowed) turning every call into a hard compile error with the migration message, the erroring old-API call behind a `-D`-gated `@if` branch |
| [unchecked_dereference.mc](types/unchecked_dereference.mc) | the opt-in warning-class framework via its first class: `-Wunchecked-dereference` reporting possibly-null `*p` / `p->field` / `p[i]` sites in legal code, each silencer (null-check guards, proven `let`s, `@nonnull` params, postfix `!`), `-Wall`, unknown-class hard error, and the `[-Werror=<name>]` promotion |
| [generics.mc](types/generics.mc) | type inference, generic recursion, multiple type parameters |
| [generic_defaults.mc](types/generic_defaults.mc) | `<T = int64>` type-parameter defaults: the priority order (explicit > typed inference > default > untyped anchoring), a `U = T*` default referencing an earlier parameter, bare `span` / `pair<int32>` omitting a defaulted tail, struct literals filling from the default |
| [type_groups.mc](types/type_groups.mc) | closed type groups `<T: int32 \| int16 \| int8>`: the pipe-separated closed set a parameter may instantiate to as a post-deduction call-site filter, disjoint-group same-pattern templates partitioning into an overload set (the signed/unsigned formatter with no `case type`), the concrete > bounded > unbounded rank, eager checking of every member, a default naming a group member |
| [bounds.mc](types/bounds.mc) | nominal type-parameter bounds `<T extends shape>`: a parameter constrained to a struct and its declared `extends` lineage as a post-deduction filter, the layout twin rejected where the nominal rule (not a field prefix) decides, a bounded overload beside an unbounded fallback in the concrete > bounded > unbounded rank, a bound composing with a default that must satisfy it, the bound joining the template symbol base and `.mci` stub |

## memory/

Pointers and the builtin container/view types built on them.

| Example | Shows |
|---------|-------|
| [pointers.mc](memory/pointers.mc) | `import`, heap allocation, `&` `*` `[]`, `sizeof`, `as` casts |
| [slices.mc](memory/slices.mc) | the builtin `slice<T>` view: borrowing a `list<T>` or `T[N]` with `as`, `.length`, indexing, `for x in`, passing by value, writing through |
| [slice_literals.mc](memory/slice_literals.mc) | array literals borrowed straight into a `slice<T>` over a hidden backing array: the explicit `as`, implicit adaptation at an annotated `let`, in slice-typed elements (nesting recurses), and at a bare function argument (through an overload set and a ternary too), the exact no-NUL length vs a named `char[N]`, the `{null, 0}` empty literal, writes through a mutable target, and the `@static slice<const T>` rodata view |
| [slice_assignment.mc](memory/slice_assignment.mc) | a string literal reborrowing into an existing char-slice lvalue by assignment (`s = "hi";`, no `as`, NUL dropped): all five lvalue forms — plain name, member (the `c.name = "hi"` gap-closer mirroring the struct literal), deref, index, and mut return — plus a `@static` char-slice global reassigned at runtime and a ternary of literals adapting arm by arm; array-literal assignment stays rejected (frame-local backing would dangle) |
| [sub_slices.mc](memory/sub_slices.mc) | `s[start:end]` narrowing a slice into a new view over the same storage: all four bound forms, writes landing in the shared storage, the verbatim `slice<const T>` result, chaining and direct iteration, index parity and unchecked bounds, the defined `s[n:n]` empty view, the borrow-first rule for non-slice receivers, and the greedy ternary start |
| [lists.mc](memory/lists.mc) | `list<T>`, a growable random-access sequence: `push`, `get` (mut out-param), `from_array`, `append`, `duplicate` |
| [stacks.mc](memory/stacks.mc) | `stack<T>`, a growable LIFO: push and pop at the top |
| [queues.mc](memory/queues.mc) | `queue<T>`, a linked-list FIFO: push at the back, pop from the front, walk it with `for … in` |
| [rings.mc](memory/rings.mc) | `ring<T>`, an array-backed FIFO ring buffer: slot reuse as the indices wrap, `ring_at` logical indexing, doubling that re-lays wrapped elements in order |
| [intrusive_list.mc](memory/intrusive_list.mc) | the intrusive-container shape, `extends T` with a bare type parameter as the base: the payload embedded as the entry's layout prefix, its fields reached directly on the entry, the explicit `as` upcast handing the payload to list-unaware code |
| [nonnull_heap_buffers.mc](memory/nonnull_heap_buffers.mc) | a heap buffer crossing the stdlib's @nonnull contracts (memory copy/fill family, hashing digests): one diverging null guard after `alloc` covers every later call, the loops that leave the buffer alone included, and the same buffer behind a struct field surviving the write-free crc32 |

## systems/

Interfacing with the outside world: libc, the graded `@nonnull` promise on a
foreign declaration, formatted I/O (raw printf and the stdlib's native
`format` protocol), shipping compiled libraries, and inline assembly.

| Example | Shows |
|---------|-------|
| [extern.mc](systems/extern.mc) | `@extern` functions (including variadic `...`), interfacing with libc |
| [extern_nonnull.mc](systems/extern_nonnull.mc) | `@nonnull` on an `@extern` declaration, graded by three postures over the opt-in `-Wextern-nonnull` class: a possibly-null argument silently accepted (relaxed default), warned (`-Wextern-nonnull` / `-Wall`), or a hard error with the LLVM hint restored (`-Werror=extern-nonnull`, or global `-Werror`); the always-rejected `null` literal and native-`@nonnull` contrasts, and the selective `-Werror=<class>` input form |
| [c_struct_abi.mc](systems/c_struct_abi.mc) | passing and returning a `struct` BY VALUE across the `@extern` C boundary, classified for the platform ABI (Apple/AAPCS64): libc's `div`/`ldiv` returning small structs in a register and a register pair, the homogeneous-float / ≤16-byte-GPR / >16-byte-indirect rules, and the compile error on non-AArch64 targets. Cross-compiled to an AArch64 object in CI; the full shape matrix round-trips against a linked C fixture in the test suite |
| [byte_scan.mc](systems/byte_scan.mc) | pointer arithmetic, C's element-scaled semantics under the shipped operators: `p + n` / `p - n` (exactly `&p[n]`) and the compound `p += n`, `p - q` as the signed element distance, the `while (p < end)` ordering scan-loop idiom, and `uint8*` byte arithmetic in a memchr-style byte scanner |
| [io.mc](systems/io.mc) | printf format specifiers, `puts`, `putchar`, string escapes |
| [formatting.mc](systems/formatting.mc) | the stdlib `format` overload set: direct calls appending each value's rendering into a `string`, the string-valued modifiers (integer `[0][width][x|X|b|p]`, string `[N][s][N]`, bool `"yes"`), per-element slice rendering (nesting recurses), the `<typename>` fallback, and one user overload making a struct printable |
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
