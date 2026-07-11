# Language feature analysis

Candidate **language** features (not library features) for mcc's stated goal —
a language for both applications and systems programming — that are not yet on
the [roadmap](../ROADMAP.md). Each is classified, sized, and assessed
for breakage.

Date: 2026-07-01. Based on the roadmap and [language reference](language.md)
as of commit `a15b1ab`.

> **Update (2026-07-02):** interfaces, bitfields, string interpolation,
> `case` exhaustiveness over enums, and `@noalias` parameters have since been
> promoted to the [roadmap](../ROADMAP.md#planned).

## Gap analysis

The roadmap is strong on **data layout and C interop** (unions, struct ABI,
bitwise builtins, asm) and on **generic expressiveness** (bounds, defaults,
tuples, `any`). What it is missing, for the stated goal, clusters into five
areas:

- **Error handling** — nothing at all today; the biggest gap for both domains
- **Safety guarantees** — nullability, immutability, overflow semantics
- **First-class behavior** — closures, interfaces (the applications half)
- **Concurrency primitives** — atomics, thread-local storage (the systems half)
- **Compile-time evaluation** — the roadmap has macros, but CTFE is the more
  principled core

## Overview

| Feature | Class | Size | Breaking? | Depends on (roadmap) |
| --- | --- | --- | --- | --- |
| Error unions + `try`/`errdefer` | Safety | L | No | enums; pairs with tuples |
| Optionals / non-null pointers | Safety | M–L | **Option-dependent** | — |
| Defined overflow semantics + checked ops | Safety | M | **Semantics change** | — |
| `case` exhaustiveness over enums | Safety | S | Only if a hard error | `unreachable` (planned) |
| Closures (staged) | Apps | M–L | No | function pointers (done) |
| Interfaces / traits | Apps | L | No | methods, bounds (planned) |
| Labeled `break`/`continue` | Apps | S | No | — |
| String interpolation | Apps | S–M | No | native variadics, `any`, formatted print (planned) |
| Bitfields | Systems | M | No | C struct ABI work (planned) |
| Volatile loads/stores at use site | Systems | S | No | — |
| Placement attributes (`@section`, `@weak`, `@used`, `@aligned`) | Systems | S | No | — |
| `@noalias` (restrict) parameters | Systems | S | No | — |
| Atomics with memory orderings | Concurrency | M | No | — |
| `@thread_local` | Concurrency | S | No | — |
| CTFE (compile-time function evaluation) | Compile-time | L | No | subsumes much of planned macros |
| `@embed("file")` | Compile-time | S | No | — |

---

## Safety & correctness

### 1. Error handling model — the single biggest gap

> **Update (2026-07-11):** designed and promoted to the
> [roadmap](../ROADMAP.md#types-and-generics) as `result<T, E>` /
> `result<E>` over dedicated `error` declarations, with `try`
> propagation and `errdefer` as staged items — the error-union
> recommendation below, satisfied in spirit under a different surface.

Today errors are libc-style: sentinel returns and `errno`. Nothing on the
roadmap addresses this, yet it shapes every API the stdlib will ever grow.

Three viable models:

- **Zig-style error unions** —
  `fn open(path: slice<const char>) -> int32 ! io_error`, with `try` to
  propagate and `errdefer` for cleanup-on-failure.
  - *Pros:* allocation-free; no hidden control flow; `defer` already exists so
    `errdefer` slots in naturally; excellent for kernels (no unwinding) *and*
    applications (ergonomic propagation).
  - *Cons:* new type-system concept (an implicit tagged union per return), new
    keywords, a calling-convention decision (flag register vs. wide return).
  - *Requires:* enums (done); benefits from the planned tagged-union machinery.
- **Go-style tuple returns** — `-> tuple<int32, error>`.
  - *Pros:* falls out of the planned `tuple<>` almost for free.
  - *Cons:* no propagation sugar; errors ignorable by accident; verbose —
    weakest for the stated goal.
- **Rust-style `Result<T, E>`** as a library type plus a `?` operator.
  - *Pros:* just a generic struct plus one operator.
  - *Cons:* really wants pattern matching richer than the current `case`, and
    adds monomorphization pressure.

**Recommendation:** error unions. They fit a monomorphizing, no-runtime
language best, and `errdefer` composes with the existing `defer`. Not breaking
— pure addition — but decide it *before* the formatted-print/stdlib expansion,
because every stdlib signature depends on it.

### 2. Optionals / non-null pointers

Every `T*` today is nullable. Two designs:

- **Opt-in optional `T?`** — a `null`-checkable wrapper; over pointers it is
  free, over values it is `{ bool, T }`.
  - *Pros:* non-breaking; gives applications a "no value" type that isn't a
    magic sentinel.
  - *Cons:* pointers stay nullable, so the safety win is partial.
- **Non-null by default** — `T*` never null, `T*?` nullable, dereferencing a
  nullable requires a check.
  - *Pros:* the real systems-safety prize — most pointer bugs die at compile
    time.
  - *Cons:* **breaking** — every existing program using `null` in a `T*` slot
    stops compiling; C interop needs every extern binding audited (which C
    pointers are nullable?).

**If non-null-by-default, the break looks like:** `let p: node* = null;`
becomes a type error. Options to manage it:

1. Introduce `T*?` now, warn on `null`-into-`T*` for a few releases, then flip
   the default.
2. A per-file or per-module opt-in (`@strict_pointers`), letting the stdlib
   migrate first.
3. Stay opt-in forever (`T?` only) and accept the partial win.

Given the project's size (pre-1.0, one stdlib), option 1 with a short window
is realistic; a language with thousands of users would need option 2.

### 3. Integer overflow semantics

Right now overflow does whatever LLVM's flag-free `add` does — wraps — but
that is *incidental*, not specified. Choices:

- **Specify wrapping** — document two's-complement everywhere.
  - *Pros:* zero work, zero breakage, predictable for systems code.
  - *Cons:* silent bugs in application arithmetic.
- **Trap in debug, wrap in release** — plus explicit wrapping operators
  (`+%`, `-%`, `*%`) and checked builtins (`@add_overflow(a, b)` returning a
  planned-tuple result).
  - *Pros:* catches real bugs where they are cheap to catch; hash/crypto/
    ring-buffer code says `+%` and is self-documenting.
  - *Cons:* **a semantics change** — code that (deliberately or not) relies on
    wrap now traps under `-O0`; needs a build-mode notion the CLI doesn't have
    yet (`-O` level currently implies nothing about checks).

**Break management:** since wrap was never documented, the semantics can be
defensibly defined now. If trap-in-debug is the pick: ship `+%` and
`@*_overflow` first (non-breaking), then enable trapping under a new flag
(`--overflow-checks`), then consider making it the `-O0` default. Nothing has
to break on day one.

### 4. `case` exhaustiveness over enums

When a `case` scrutinizes an enum, check that every member is covered or an
`else` exists. Pairs directly with the planned `unreachable` — an exhaustive
`case` plus `unreachable` fall-through is the idiom.

- *Pros:* cheap; catches the classic "added an enum member, forgot a site" bug.
- *Cons:* if introduced as a **hard error** it breaks existing non-exhaustive
  `case`s; introduce as a warning, or gate the error on the `case` having no
  `else`.

## Expressiveness (the applications half)

### 5. Closures — do it in stages

Function pointers exist; anonymous functions don't. Stage it:

1. **Non-capturing lambdas** — `fn(x: int32) -> int32 { return x * 2; }` as an
   expression, coercing to existing function pointers. Small, non-breaking,
   immediately useful for container/sort/callback APIs.
2. **Capturing closures** — a distinct fat type (`{ fnptr, env* }`) with
   by-value capture into a caller-stack environment. This is where the systems
   constraint bites: no allocation, which means an escape question (what
   happens when the closure outlives the frame?). The honest allocation-free
   answer is "non-escaping only" — fine as a parameter type, not storable.
   *Requires:* a real design decision; interacts with generics (a generic
   `fn f<F>(callback: F)` can monomorphize over the closure type and avoid the
   fat pointer entirely — the zero-cost path).

- *Pros:* the largest single ergonomics win for application code.
- *Cons:* stage 2 opens lifetime questions the language has so far avoided;
  keep stage 1 independent so it ships early.

### 6. Interfaces / traits (dynamic dispatch)

The planned `T extends S` / `T in (…)` bounds handle static polymorphism.
What's missing is *runtime* polymorphism — heterogeneous lists, plugin-style
APIs. A minimal design:

```c
interface writer {
    fn write(self, buf: slice<const uint8>) -> int64;
}
```

implemented by the planned methods, carried as a fat pointer
`{ data*, vtable* }`.

- *Pros:* completes the OOP story the methods item starts; `any` + `case type`
  already commits to one form of dynamic typing, this is the structured
  counterpart.
- *Cons:* large — vtable layout, coercion rules, interaction with
  monomorphized generics.
- *Requires:* methods first; sequence it after. Non-breaking.

### 7. Small ergonomics: labeled loops, string interpolation

- **Labeled `break`/`continue`** — `outer: while (…) { … break outer; }`.
  Tiny, non-breaking, and systems code with nested scan loops wants it
  constantly.
- **String interpolation** — `println("x = {x}")`, sugar that desugars into
  the *planned* formatted-print + native-variadic machinery, so it is nearly
  free once those land — but it is a language (lexer) feature, hence listed
  here. Non-breaking; new escape rules inside string literals need care
  (`{{`).

## Systems control

### 8. Bitfields

`field: uint32 : 5;` inside structs. Hardware registers, protocol headers,
and C interop (many kernel/syscall structs use them) all want this; `@packed`
doesn't substitute.

- *Pros:* removes the last big C-layout gap after unions land.
- *Cons:* the C bitfield layout algorithm is per-ABI and genuinely fiddly;
  interaction with `@volatile` structs (read-modify-write granularity) must be
  specified.
- *Requires:* nothing, but do it alongside the planned C struct-passing ABI
  work while ABI rules are in cache. Non-breaking.

### 9. Volatile at the use site

`@volatile` exists on whole structs and extern variables, but MMIO code often
has an ordinary `uint32*` computed at runtime. Either volatile load/store
builtins (`volatile_load(p)` / `volatile_store(p, v)`) or a `volatile T*`
pointer qualifier.

- *Pros:* builtins are a day of work and unblock bare-metal drivers; the
  [baremetal example](../examples/baremetal/) is the customer.
- *Cons:* the qualifier form ripples through the type system (coercions,
  generics); builtins don't.

**Recommendation:** builtins now, qualifier maybe never. Non-breaking.

### 10. Placement & linkage attributes

`@section("name")`, `@weak`, `@used`, `@aligned(N)` on functions and globals.
Interrupt vector tables, linker-script-driven layouts, and library-override
patterns all need these; each is a small LLVM attribute pass-through in the
existing `@`-annotation namespace (which is exactly why new ones are
non-breaking). Pairs with the planned `@naked`.

### 11. `@noalias` parameters

C's `restrict`: promise two pointer parameters don't overlap, unlocking
vectorization. Trivial LLVM mapping, non-breaking, meaningful perf for the
memcpy-shaped stdlib functions. The unchecked-promise footgun is the only con
— document it as UB-if-violated.

## Concurrency

### 12. Atomics

No threads story exists at all; atomics are the language-level prerequisite
(the rest — threads, mutexes — is library over pthreads/futex). Builtins over
integer/pointer types with explicit orderings: `atomic_load<T>(p, .acquire)`,
`atomic_store`, `atomic_rmw` (add/and/or/xchg), `atomic_cas`, plus `fence`.

- *Pros:* required for both a kernel (the baremetal target has interrupts
  today, effectively concurrency) and any threaded application; maps 1:1 to
  LLVM instructions; the builtin form avoids inventing an `atomic<T>` type.
- *Cons:* orderings need an enum and documentation discipline; misuse is
  UB-adjacent.

Non-breaking, medium effort, high leverage — arguably the top systems item not
on the roadmap.

### 13. `@thread_local`

A storage-class annotation on top-level `let`. Small, non-breaking, and it is
also the exit ramp for the roadmap's own `errno` symbol-collision problem (a
namespaced, thread-local mcc `errno`). Freestanding targets need a documented
fallback (plain global or compile error).

## Compile-time

### 14. CTFE — consider it *instead of* macro functions

The roadmap plans `@macro` functions. Compile-time function evaluation —
letting `const` initializers, array sizes `T[f(n)]`, and the planned
`@static_assert` call ordinary functions — covers most macro use cases without
a second language. The compiler is a Python AST walker, so an evaluator over
the same AST is very tractable.

- *Pros:* one semantics (real functions) instead of expansion rules; makes
  `@static_assert` genuinely useful (checksum tables, layout math); Zig proves
  the model fits a systems language.
- *Cons:* large surface — what is allowed at comptime (no extern calls, no
  pointers into runtime memory) must be specified; evaluation limits needed.

Non-breaking. **Worth deciding before implementing `@macro`, because they
compete for the same niche.**

### 15. `@embed("file")`

File contents as a `uint8[N]` constant. Firmware blobs, shaders, test vectors,
web assets — both domains. Trivial, non-breaking.

---

## Breaking changes, collected

Only three candidates genuinely break, and each has a managed path:

1. **Non-null-by-default pointers** — breaks every `null` assignment into a
   plain `T*`. Manage via: introduce `T*?` first → warn → flip; or per-module
   opt-in; or stay opt-in (`T?`) permanently. Decide early — this gets more
   expensive with every line of `.mc` written.
2. **Overflow trapping** — breaks (at runtime, in debug) code relying on
   incidental wrap. Manage via: define wrap as the spec today, ship `+%` and
   checked builtins, gate trapping behind a flag, flip the `-O0` default later
   or never.
3. **Immutable `let`** — today `let` is reassignable. If immutable-by-default
   is ever wanted (`let` frozen, `var` mutable — the modern default, and it
   helps the optimizer), that breaks *almost every existing program*. Options:
   (a) do it now while the corpus is just `lib/` plus the examples — the
   cheapest it will ever be; (b) add an opt-in `final`/`val` and keep `let`
   mutable forever; (c) `let mut` Rust-style, same break as (a). If (a) is
   tempting at all, it should jump the queue ahead of everything else in this
   document.

There is also a **soft** breakage class: every new keyword (`try`,
`interface`, `var`, `atomic`, …) can collide with existing identifiers. The
language's `@`-annotation namespace is a built-in mitigation — prefer
`@thread_local`/`@section`/`@noalias` forms where the feature is
annotation-shaped, and contextual keywords where it isn't.

## Suggested sequencing

1. **Decide the three semantic defaults now** (mutability, nullability,
   overflow) — they are cheap pre-1.0 and ruinous later.
2. **Error unions** next — every future stdlib signature depends on the
   answer.
3. **Atomics + volatile builtins + placement attributes** — small,
   independent, and they complete the bare-metal story the compiler flags
   (`--strict-align`, `--freestanding`) already invest in.
4. **CTFE vs `@macro`** — settle the competition before building either.
5. **Closures stage 1, then interfaces after methods land** — the
   applications half, sequenced behind their roadmap dependencies.
