# Roadmap

What the compiler does today, and what is planned next. Checked items are
implemented and covered by the [test suite](README.md#tests); each links to
its reference section in the [language reference](docs/language.md).

## Language

- [x] [Functions](docs/language.md#functions) — typed params, `->` return type, recursion,
      implicit `return 0` from `main`
- [x] [`@inline` functions](docs/language.md#functions) — LLVM `alwaysinline`, across files
- [x] [Variadic functions](docs/language.md#variadic-functions) — C's variadic arguments: `...`
      and `va_list` forwarding
- [x] [Native variadic arguments](docs/language.md#native-variadic-arguments) —
      the type-erased mcc model: a trailing `slice<const any>` parameter
      (`fn f(args...)` as sugar) collects the call's extra arguments as
      caller-stack boxed `any`s, allocation-free, with pass-through for an
      exact slice argument, overload-set and generic participation
      (a non-collecting match always outranks a collecting one), and the
      stdlib's `{}` print flipped onto it
- [x] [Generics](docs/language.md#generics) — monomorphized, on functions and structs
- [x] [Function overloading](docs/language.md#function-overloading) — one name,
      several parameter lists, resolved at the call site by arity and argument
      types: generic, concrete, and mixed sets (a concrete overload beats a
      generic on an exact match; rank-tied templates are partially ordered by
      [subsumption](docs/language.md#rank-tied-templates-subsumption), the
      strictly more constrained pattern winning), signature-derived mangled
      symbols (a single
      definition keeps its plain C-linkable name), `.mci` support, and
      [order-independent template symbol bases](docs/language.md#template-symbols);
      overload sets are open across modules, the original
      one-defining-module rule since lifted by the open overload sets
      item in [Functions and methods](#functions-and-methods)
- [x] [Variables](docs/language.md#variables) — `let` with type inference
- [x] [Constants](docs/language.md#constants) — `const`, folded at compile time
- [x] [Conditional compilation](docs/language.md#conditional-compilation) — structured `@if`,
      including conditional `import`s
- [x] [Control flow](docs/language.md#control-flow) — `if`/`else`, `while`, `until`,
      `for … in` (incl. the builtin `range` counting loop and `enumerate`, which
      pairs each element with its `uint64` position), `break`/`continue`,
      braceless bodies
- [x] [`defer`](docs/language.md#defer) — statement and block forms, reverse order
- [x] [Block expressions](docs/language.md#block-expressions) — `{ ...; emit v; }` as a
      value, with contained temporaries
- [x] [Types](docs/language.md#types) — `int8`–`int64`, `uint8`–`uint64` (with
      `byte` a transparent alias for `uint8`), `char`, `bool`, `float64`, `void`;
      untyped integer constants with range-checked adaptation
- [x] [`char`](docs/language.md#strings) — a distinct one-byte text type
      (ABI-compatible with `uint8`, but a separate type) so a NUL-terminated
      string is told apart from a raw byte buffer. `'a'` literals default to
      `char`, `"hi"` is a `char[N]`, and a `char[N]` borrows to a `slice<char>`
      that **drops the trailing NUL** (the text) — while a `uint8[N]` stays raw
      bytes whose `slice<uint8>` keeps every byte. A `char` literal adapts to a
      `uint8`/integer slot, but a `char` _value_ needs an explicit `as`; `char*`
      coerces to `uint8*` like any pointer, so libc still takes string literals
- [x] [Operators](docs/language.md#operators) — arithmetic, comparison, logical
      (`and`/`or`/`!`), bitwise (`&` `|` `^` `<<` `>>` `~`), `cond ? a : b`
- [x] [Compound assignment](docs/language.md#variables) — `+= -= *= /= %= &= |= ^= <<= >>=`,
      `x op= y` meaning `x = x op y` with the target evaluated once
- [x] [Casts](docs/language.md#casts) — explicit `as`
- [x] [Pointers](docs/language.md#pointers) — address-of, deref, `null`,
      `sizeof`/`alignof` (of a type or a variable) and `offsetof(struct S, field)`
      as compile-time `uint64` layout constants
- [x] [Pointer arithmetic](docs/language.md#pointer-arithmetic) — C's
      element-scaled `p + n` / `p - n` (pointer-left only) and the compound
      forms, pointer difference (`p - q` over identical pointer types, an
      `int64`), and relational comparisons; function-pointer arithmetic and
      `null` operands stay rejected
- [x] [Function pointers](docs/language.md#function-pointers)
- [x] [Arrays](docs/language.md#arrays) — fixed-size `T[N]` (`N` any constant
      expression), indexing, `len`, `sizeof`
- [x] [Slices](docs/language.md#slices) — the builtin non-owning view
      `slice<T>` (`{ data: T*; length: uint64 }`) over a contiguous run of `T`:
      runtime `.length`, indexing, native `for … in`. Borrows from an owned
      `list<T>`/`T[N]` with an explicit `as` (dropping `capacity`); a
      `slice<const T>` is the read-only form a mutable slice widens into. A
      string literal **adapts** to a `slice<char>`/`slice<const char>` from
      context (NUL dropped), so `writeln("hi")` just works
- [x] [Sub-slicing](docs/language.md#sub-slicing) — `s[start:end]` on a slice
      yields a new slice over the same storage (`s[1:]`, `s[:2]`, `s[:]`),
      omitted bounds defaulting to `0`/`.length`; a plain rvalue, unchecked
      like `s[i]`
- [x] [Literal adaptation to `slice<T>`](docs/language.md#slices) — a string or
      array literal in a slice-typed slot adapts to it, the compiler
      materializing (array) or borrowing (string) the backing storage, across
      the family of positions: argument, `let`, `return`, array element,
      `@static`, struct field, and assignment. Array-literal assignment is the
      one documented non-goal (a frame-local backing would dangle past a
      longer-lived target). A string literal at a
      [dot-call](#functions-and-methods) receiver is the last position wired
      up: `"{}{}".format(a, b)` and `"hello".first()` borrow into
      `slice<const char>` so a `slice::<method>` family reaches them, matching
      the explicit `("{}{}" as slice<const char>).format(a, b)` (same borrow
      path, byte-identical IR). It is a pure fallback, firing only when the
      literal's own `char[N]` resolves no method or field of that name and a
      matching `slice::<method>` exists, so a genuine array/char method is
      never shadowed; the literal keeps its default `char[N]`-decaying-to-`char*`
      type, so `@extern char*` bindings (`strlen("hi")`, printf) still receive a
      pointer, not a fat slice (the broader "string literals are `slice<char>`
      by default" was rejected for breaking that decay and the owned-array
      `let`). v1 is the string-literal receiver only: a named `char[N]` array or
      a `char*` value still does not reach slice methods by dot
      (`arr.format(...)` stays the char-array call-shape error), a possible
      later generalization; `constructor`/`destructor` stay qualified-only
      through the adaptation
- [x] [Structs](docs/language.md#structs) — `.`/`->` access, generics, struct
      literals (`point { x = 6, y = 4 }`, the `struct` keyword optional, omitted
      fields zeroed or set to a field's `= default`, generic type arguments
      inferred from typed field values, or the bare `{ x = 6, y = 4 }` form where
      context fixes the type — the aggregate sibling of the slice-literal
      adaptation above, across the same family of positions, with overloads
      resolved by field names),
      `@packed`/`@align`/`@volatile`, `extends` (prefix specialization),
      struct value upcast, flexible array members (a trailing `field: T[]` that
      adds 0 to `sizeof` and decays to a `T*` at the struct's tail)
- [x] [Struct extension of a type parameter](docs/language.md#structs) — a bare
      type parameter in the `extends` slot (`struct wrapper<T> extends T`)
      embeds `T`'s fields as the layout prefix per instantiation (the
      intrusive-container shape); single base by design, and the one
      `extends` form the since-shipped method inheritance
      ([Methods / OOP](#functions-and-methods)) leaves out: no declared
      base family exists at the declaration, so a payload's methods are
      reached through the explicit upcast (documented, deferred)
- [x] [Nominal struct subtyping](docs/language.md#structs) — the struct
      subtype relation (value/pointer upcast and slice-borrow) follows the
      declared `extends` lineage, not a matching layout prefix, so a
      coincidental layout twin no longer upcasts or borrows
- [x] [Builtin structs](docs/language.md#control-flow) — `iterator<T>` (the
      shared `_it`/`_next` cursor), `pair<K, V>` (what the keyed containers
      yield), and `enumerated<T>` (what `enumerate` yields), available with no
      import; a same-named user struct takes precedence, as with the builtin
      `range`
- [x] [Unions](docs/language.md#unions) — `union Name { … }` members sharing
      one storage (all at offset 0): one-member zero-filled literals, defined
      cross-member byte reinterpretation (type punning), generics,
      `@packed`/`@align`/`@volatile`
- [x] [Tuples](docs/language.md#tuples) — the builtin heterogeneous
      fixed-arity product `tuple<A, B, ...>` (any arity, `()`/`(x,)`
      included): paren literals, compile-time-constant indexing, slicing,
      and `len`, destructuring with a trailing-`...` rest binder (slice
      sources included), `as` casts to layout-equivalent structs, and
      `@extern` crossing as the layout-equivalent C struct
- [x] [Enums](docs/language.md#enums) — `enum Name[: type] { … }`, `Name::Member`
      constants over any underlying type, the name usable as a type
- [x] [Type aliases](docs/language.md#type-aliases) — `type <name> = <type>;`,
      transparent (e.g. `type callback = fn(int32, uint8**) -> int32;`)
- [x] [`typename`](docs/language.md#the-typename-builtin) — recover a type's
      canonical name as a `const` string, taking a type or an expression
      (`typename(int64)`, `typename(x)`), folded at compile time; resolves
      per instantiation inside generics
- [x] [Imports](docs/language.md#imports) — bare-name resolution, search paths
- [x] [Visibility](docs/language.md#visibility) — `@private`, `@static`
- [x] [Extern declarations](docs/language.md#extern-declarations) — `@extern`, `@symbol`
- [x] [Bodyless `fn` prototypes](docs/language.md#bodyless-fn-prototypes) — a
      plain `fn` ending in `;`: concrete prototypes for functions defined in
      another object (mcc calling convention, which `@extern`'s C ABI rejects)
      and forward declarations (a prototype pairs with its definition and is
      discarded)
- [x] [`@noalias` parameters](docs/language.md#noalias-parameters) — C's
      `restrict`: an unchecked per-parameter promise mapped to LLVM `noalias`;
      allowed on `@extern`, rejected on `mut` and non-pointer parameters
- [x] [`@removed(msg)` tombstones](docs/language.md#removed-functions) — the
      terminal state of the availability lifecycle, one step past
      `@deprecated`: a bodiless declaration turning every call site into a
      hard compile error that carries the migration message
- [x] [Strings](docs/language.md#strings) — string and char literals with C escapes
- [x] [Comments](docs/language.md#comments) — line, block, doc

## Standard library

- [x] Core — `memory` (typed `alloc`/`dealloc`), `io` (the formatted
      `print`/`println`, `swap`/`replace`), `format` (the open per-type
      `format` overload set behind the `{}` placeholders), `slice`
      (methods on the builtin `slice<T>`: `slice<T>::equals` is the
      equality protocol as a per-type method, `a.equals(b)`, which
      replaced the free-function `equals<T>` in the now-removed
      `std/equality` — always an explicit stopgap before methods, per its
      own "should turn to `slice::equals()` once OOP lands" note, so this
      is that migration landing, not a regression; a `slice<const char>`
      compares against a `string` through a bridging overload. Also
      `slice::format` / its `string::format` delegate, a format-string
      builder filling `{}` holes from variadic args through the `format`
      set (`{modifier}` carries a modifier, `{{`/`}}` escape braces) and
      returning an `own string`, reached on a bare literal —
      `"{}".format(x)` — via the string-literal dot-call adaptation and
      owning via move-out returns), `hash` (generic `hash<T>`)
- [x] Containers — `list`, `stack`, `queue`, `ring`, `set`, `dict`, `string` (counting
      loops use the builtin [`range`](docs/language.md#control-flow))
- [x] Hashing — `splitmix64`, `fnv1a`, `murmur3`, `crc32`, `md5`
- [x] [libc bindings](docs/language.md#reaching-libc) — `stdio`, `stdlib`, `string`, `ctype`,
      `math`, `limits`, `float`, `time`, `errno`
- [x] [Char methods](docs/language.md#methods-on-type-aliases-and-builtin-types) — `char`
      (`import "std/char";` registers the ctype family as methods on the
      builtin `char` type: `char::is_alpha`, `is_alnum`, `is_digit`,
      `is_hex`, `is_space`, `is_upper`, `is_lower`, and
      `char::upper`/`char::lower` with non-letters unchanged, all taking
      `const self: char`; `@inline` over the libc `ctype` bindings, and
      the stdlib's first use of the builtin-qualifier method form —
      called as `'C'.lower()` since the method-call sugar shipped)

## Tooling

- [x] Native compilation and linking
- [x] JIT execution (`--run`)
- [x] LLVM IR output (`--emit-llvm`)
- [x] Assembly output (`--emit-asm`/`-S`) — target `.s` text, alongside
      `--emit-llvm` and `-c`
- [x] Optimization levels `-O0`–`-O3`
- [x] Cross-compilation (`--target`), `--general-regs-only`, `--strict-align`,
      `--nostdlib`, `-I`
- [x] Separate compilation across files
- [x] Object-only compilation (`-c`) — emit a `.o` without linking
- [x] Linker passthrough — `-l<name>` libraries, `-L<dir>` search paths, and
      extra object/archive inputs on the command line, all forwarded to the
      `cc` link step
- [x] [Interface files](docs/language.md#interface-files) — `--emit-interface`
      writes a `.mci` stub (bodyless `fn` prototypes, keeping the mcc calling
      convention with `const`/`mut` markers, plus full types/consts/generics)
      to ship beside an object; a bare `import` resolves to `.mc` then `.mci`
- [x] [Editor support](README.md#editor-support) — VS Code syntax highlighting;
      Helix and Neovim on a shared tree-sitter grammar (highlighting, comment
      toggling, indents, folds, text objects)

## Planned

The forward-looking roadmap. **Full detail, staging, and design rulings for every item now live in Linear** (the MCC project) — this section is a high-level index; each feature there has its complete write-up, and its sub-issues track the individual stages. Status: `[x]` shipped · `[~]` in progress · `[ ]` planned.

### Types and generics

- [ ] **@typeof(expr)** — use an expression's static type in a type position, including in an alias: `type t = @typeof(var);`.
- [~] **Generic type parameters** — beyond the monomorphized basics
- [~] **Enum member reuse** — a derived enum inherits a base enum's members by naming it in the existing `:` slot: `enum x_status: x_error { SUCCESS = 0 }` copies `x_error`'s member table and adopt…
- [~] **Nominal enums** — make an enum value carry its type identity instead of collapsing to its underlying integer.
- [~] **Error handling** — recoverable errors as values: a dedicated `error` declaration naming the causes, a builtin `result<T, E>` / `result<E>` template type carrying either the ok value or t…

### Modules and imports

- [~] **Imports** — beyond pulling in a whole module

### Structs, arrays, and data layout

- [~] **Unions** — `union Name { i: int64; f: float64; p: void*; }`, members sharing one storage (size of the largest, all at offset 0), for C-layout interop (`epoll_data`, `sigval`, mos…
- [ ] **Bitfields** — `field: uint32 : 5;`, packing consecutive narrow fields into one storage unit, for hardware registers, protocol headers, and C-layout interop (many syscall/kernel stru…
- [ ] **new T { ... } sugar** — desugars to a block that calls a user-defined `fn new<T>() -> T*`, writes a struct literal through the result, and emits the pointer

### Functions and methods

- [~] **const parameters** — an immutable parameter (`fn f(const s: struct big)`) the callee promises not to mutate
- [~] **mut parameters and returns** — the writable dual of `const`: a value passed (or returned) by hidden reference to the caller's storage, mutable *through* the reference but, like `const`, with its add…
- [~] **Open overload sets** — lifted the rule that all overloads of a name live in one defining module: sets are open by default, with no opt-in marker — any module may add overloads to an existing…
- [x] **Subsumption ordering of rank-tied generic overloads** — a rank-tied cohort (same tier, same specificity) is no longer automatically ambiguous: the cohort resolves to its unique MAXIMUM, the candidate whose parameter pattern…
- [ ] **fn types in overload viability and generic unification** — close a pre-existing resolver gap the callback story sits behind: a concrete overload with a fn-typed parameter is never viable today (fn-typed arguments are invisible…
- [~] **Methods / OOP** — `fn <type>::<method>(...)` definitions keyed to a type, structs foremost (the explicit qualified-call foundation, the `recv.method(args)` dot-call sugar, the `S(args)`…
- [~] **& reference parameters and the const split** — a wholesale respell and refinement of the parameter/return mutability convention, the capstone of the shipped `const`/`mut`/`own` surface it depends on (`const`/`mut`…
- [~] **@nonnull parameters** — a checked "definitely non-null" refinement over C's nullable-by-default `T*`, opt-in per parameter: the callee is statically guaranteed a non-null argument and skips t…
- [ ] **Pointer truthiness and p ?? q null coalescing** — pointers become testable in conditions: `if (p)` means `if (p != null)` and `!p` means `p == null`, so `if (!p) { return; }` is the null guard (a bare `!p` yields a pl…
- [~] **C variadics** — the C-ABI `...`/`va_list` machinery, beyond forwarding
- [~] **@noreturn and unreachable** — `@noreturn` marks a void function that never returns (`exit`, `abort`, an infinite loop): a direct call terminates the caller's block, so no dummy return is needed aft…

### Metaprogramming and builtins

- [~] **Compile-time macros** — Compile-time macros
- [ ] **Bit-twiddling builtins** — `byte_swap<T>` (`llvm.bswap`) and `bit_reverse<T>` (`llvm.bitreverse`) over the integer types
- [~] **Builtin enumerate** — pairing each element with its `uint64` position
- [~] **Error directives** — `@static_assert(cond, msg)` and `@error(msg)`, both emitting a hard compile error through the existing error path, with the condition folded by `eval_const` **during c…
- [~] **Warning subsystem** — a non-fatal diagnostic channel, the foundation the `@deprecated` directive below and enum-exhaustiveness checking both build on.
- [~] **Inline assembly** — arch-specific (pair with `@if` on `TARGET_ARCH`), preferring intrinsics where they exist

### Strings and formatting

- [x] **Formatted print/println** — Rust/Python-style `{}` placeholders, type-driven (no `%`-letters), written in mcc over the native variadic `slice<const any>`; enables compile-time format checking lat…
- [x] **String interpolation** — `println(f"x = {x}")`: an `f`-prefixed string literal with `{expr}` holes desugars at parse time into the sequential `@format` form above (`"x = {}".format(x)`; `{{`/`…

### Tooling and C interop

- [~] **Instantiation backtraces on errors** — an error inside a monomorphized body used to print as a bare line in the template file with no trace of how the compiler reached it; a source-level note chain on `Lang…
- [ ] **Linker selection** — `--linker=/path/to/ld` to pick a specific linker (today whatever the driver `cc` defaults to)
- [ ] **Compiler-driver selection** — `--cc=/path/to/cc` to choose the C driver used for linking (today the system `cc` on `PATH`)
- [~] **C struct-passing ABI** — classify by-value struct arguments and returns into registers/`byval`/`sret` per the platform ABI, so structs cross the C boundary correctly (see C ABI compatibility).
- [ ] **Namespaced exported symbols** — emit mcc functions under a mangled/prefixed symbol (the `@extern` libc bindings keep their real names via `@symbol`), so a precompiled mcc library does not clash with…
- [ ] **C header generation** — emit a `.h` of the public surface (like `--emit-interface` does for `.mci`), so C code can call into an mcc object or library

<!-- Add upcoming features here. Full write-ups live in Linear (MCC project); keep this list one line per feature. -->
