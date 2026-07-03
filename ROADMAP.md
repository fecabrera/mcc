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
- [x] [Generics](docs/language.md#generics) — monomorphized, on functions and structs
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
- [x] [Structs](docs/language.md#structs) — `.`/`->` access, generics, struct
      literals (`point { x = 6, y = 4 }`, the `struct` keyword optional, omitted
      fields zeroed or set to a field's `= default`, generic type arguments
      inferred from typed field values),
      `@packed`/`@align`/`@volatile`, `extends` (prefix specialization),
      struct value upcast, flexible array members (a trailing `field: T[]` that
      adds 0 to `sizeof` and decays to a `T*` at the struct's tail)
- [x] [Builtin structs](docs/language.md#control-flow) — `iterator<T>` (the
      shared `_it`/`_next` cursor), `pair<K, V>` (what the keyed containers
      yield), and `enumerated<T>` (what `enumerate` yields), available with no
      import; a same-named user struct takes precedence, as with the builtin
      `range`
- [x] [Unions](docs/language.md#unions) — `union Name { … }` members sharing
      one storage (all at offset 0): one-member zero-filled literals, defined
      cross-member byte reinterpretation (type punning), generics,
      `@packed`/`@align`/`@volatile`
- [x] [Enums](docs/language.md#enums) — `enum Name[: type] { … }`, `Name::Member`
      constants over any underlying type, the name usable as a type
- [x] [Type aliases](docs/language.md#type-aliases) — `type <name> = <type>;`,
      transparent (e.g. `type callback = fn(int32, uint8**) -> int32;`)
- [x] [Imports](docs/language.md#imports) — bare-name resolution, search paths
- [x] [Visibility](docs/language.md#visibility) — `@private`, `@static`
- [x] [Extern declarations](docs/language.md#extern-declarations) — `@extern`, `@symbol`
- [x] [Strings](docs/language.md#strings) — string and char literals with C escapes
- [x] [Comments](docs/language.md#comments) — line, block, doc

## Standard library

- [x] Core — `memory` (typed `alloc`/`dealloc`), `std` (`print`/`println`,
      `swap`/`replace`)
- [x] Containers — `list`, `stack`, `queue`, `set`, `dict`, `string` (counting
      loops use the builtin [`range`](docs/language.md#control-flow))
- [x] Hashing — `splitmix64`, `fnv1a`, `murmur3`, `crc32`, `md5`
- [x] [libc bindings](docs/language.md#reaching-libc) — `stdio`, `stdlib`, `string`, `ctype`,
      `math`, `limits`, `float`, `time`, `errno`

## Tooling

- [x] Native compilation and linking
- [x] JIT execution (`--run`)
- [x] LLVM IR output (`--emit-llvm`)
- [x] Optimization levels `-O0`–`-O3`
- [x] Cross-compilation (`--target`), `--general-regs-only`, `--strict-align`,
      `--nostdlib`, `-I`
- [x] Separate compilation across files
- [x] Object-only compilation (`-c`) — emit a `.o` without linking
- [x] Linker passthrough — `-l<name>` libraries, `-L<dir>` search paths, and
      extra object/archive inputs on the command line, all forwarded to the
      `cc` link step
- [x] [Interface files](docs/language.md#interface-files) — `--emit-interface`
      writes a `.mci` stub (`@extern` prototypes plus full types/consts/generics)
      to ship beside an object; a bare `import` resolves to `.mc` then `.mci`
- [x] [Editor support](README.md#editor-support) — VS Code syntax highlighting

## Planned

Grouped by scope. Within a group, a feature that depends on another comes
after it — as a subitem when it strictly builds on its parent. A planned
feature that expands an existing one is grouped with it, the implemented half
appearing as a checked subitem (as `const` parameters and inline assembly
already do).

### Types and generics

- [ ] `typeof(expr)` — use an expression's static type in a type position,
      including in an alias: `type t = typeof(var);`. Also the checker behind
      [`any`](#structs-arrays-and-data-layout)'s type discriminant
- [ ] Generic type parameters — beyond the monomorphized basics:
  - [x] generics on functions and structs — implemented, see
        [Generics](docs/language.md#generics)
  - [ ] defaults — a declared fallback type parameter, on functions
        (`fn myfunc<T = uint8*>(x: T) { ... }`) and structs
        (`struct range<T = int64> { ... }`), used when a type argument isn't
        supplied or inferable from a _typed_ value. The strongly-typed way to
        pick a default — declared at the definition, not guessed from an untyped
        literal at the use site (`let a = 0` and a no-anchor `struct range { … }`
        should stay ambiguous errors, not silently become `int32`)
  - [ ] bounds — constrain a parameter with `fn myfunc<T extends mystruct>(x: T)`
        (a struct and its `extends` specializations) or
        `fn myfunc<T in (t1, t2, ...)>(x: T)` (an explicit set of types)
    - [ ] interface bounds — `fn myfunc<T implements I>(x: T)`, asserting
          that `T` implements interface `I`: checked at each monomorphized
          instantiation (the concrete type must define every method `I`
          names), then calls dispatch statically — no fat pointer, no
          vtable. The static counterpart of the dynamic
          [interfaces](#functions-and-methods) dispatch; depends on
          interface declarations and the methods they are made of, so it
          lands after both
- [ ] Enum member reuse — a derived enum inherits a base enum's members by
      naming it in the existing `:` slot:
      `enum x_status: x_error { SUCCESS = 0 }` copies `x_error`'s member table
      and adopts its underlying type, then adds its own, so `x_status::NOT_FOUND`
      resolves and folds equal to `x_error::NOT_FOUND`. Compile-time only and
      purely additive (no currently-legal program changes meaning), with no
      runtime or ABI change: a single-function change in `register_enum` (merge
      the base member table, adopt its underlying type, run the base's access
      check so a `@private` base cannot be extended cross-file), leaving the
      parser, the tree-sitter/tmLanguage grammars, and the `.mci` round-trip
      untouched (the `:` slot already parses an enum name). A name collision
      with an inherited member is a hard error; value aliasing across base and
      derived is allowed (enums already allow it); the base must be a single,
      direct enum name (not a `type` alias to an enum) appearing textually
      before the derived enum or in an imported file. Delivers DRY reuse plus
      the `x_status::NOT_FOUND` spelling, but **zero new type safety**: enum
      values are transparent integers today, so a derived value stays
      indistinguishable from its base and from a plain int. The directional
      base-to-derived safety that reuse suggests needs
      [nominal enums](#types-and-generics) below
- [ ] Nominal enums — make an enum value carry its type identity instead of
      collapsing to its underlying integer. Today an enum used as a type
      becomes a raw `int32` (or its declared underlying), so `x_status` and
      `x_error` values mix freely with each other and with plain ints. A large,
      **backward-incompatible** semantics change: nominal enums begin rejecting
      some code that compiles now (implicit enum/int and enum/enum mixing), so
      it needs a migration story (staged warnings before errors). It is the
      genuine prerequisite for both dependents nested below: the directional
      conversion safety that [enum member reuse](#types-and-generics) above
      suggests, and enum-aware `case` exhaustiveness (today `case` is pure
      integer equality with no enum awareness):
  - [ ] directional conversion safety — once enums are nominal, a
        [member-reuse](#types-and-generics) derivation gains real conversions:
        base-to-derived is implicit widening (the derived value set is a
        superset of the base's), derived-to-base is explicit and checked. Note
        this is the **inverse** of OOP class inheritance's derived-to-base
        conversion, because here the extending enum is the value-set superset.
        Meaningful only once enums are nominal (transparent enums have nothing
        to convert)
  - [ ] `case` over an enum — exhaustiveness:
    - [x] `case`/`when` value matching with an optional `else` — implemented,
          see [Control flow](docs/language.md#control-flow)
    - [ ] exhaustiveness checking — when the scrutinee is an enum and there is
          no `else`, check that every member is covered, catching the "added an
          enum member, forgot a `case` site" bug; the natural pair of the
          planned [`unreachable`](#functions-and-methods) (an exhaustive `case`
          whose fall-through is `unreachable`). Introduced as a warning first —
          a hard error would break today's legal non-exhaustive `case`s — with
          a later flip to an error once the stdlib and examples are clean. That
          non-fatal first phase depends on the
          [warning subsystem](#metaprogramming-and-builtins)

### Modules and imports

- [ ] Imports — beyond pulling in a whole module:
  - [x] whole-module imports — `import "<path>";`, bare-name resolution, and
        search paths; implemented, see [Imports](docs/language.md#imports)
  - [ ] selective imports — `import { a, b, fnc } from "<path>";` to bring in
        only named declarations
    - [ ] import aliasing — rename selected names with `as`:
          `import { a as _a, b, fnc } from "<path>";` (extends the selective
          form)

### Structs, arrays, and data layout

- [ ] Unions — `union Name { i: int64; f: float64; p: void*; }`, members
      sharing one storage (size of the largest, all at offset 0), for C-layout
      interop (`epoll_data`, `sigval`, most syscall structs embed a union) and
      type punning. The unsafe primitive under `any`:
  - [x] core unions — declarations, one-member zero-filled literals, `.`/`->`
        access with defined cross-member byte reinterpretation, generics,
        `@packed`/`@align`/`@volatile`, `const` parameters, and `.mci`
        interfaces, riding on the struct machinery (a union flag on the
        declaration and its `LangType`; layout is max-member size and
        alignment, lowered to an LLVM struct of the most-aligned member plus
        pad bytes, member access by pointer cast). `extends` (either
        direction), member defaults, and flexible array members are rejected;
        implemented, see [Unions](docs/language.md#unions)
  - [ ] `any` — a tagged union over the above: a union payload plus a
        `typeof`-checked type discriminant, so the live member is recovered
        safely (`case type`). The element type of the
        [variadic](#functions-and-methods) pack's `slice<any>`. Depends on
        unions (above) and [`typeof`](#types-and-generics)
  - [ ] global/`@static` union initializers — teach the const-initializer
        path to emit a union constant (zero-fill plus the one written member).
        Until then a global/`@static` union initializer is rejected with an
        explicit compile error
  - [ ] dedicated union declaration — migrate unions off the shared struct
        declaration onto their own AST node and type kind, so a struct-only
        code path (sequential layout, `extends`, prefix upcast) can never
        silently accept a union. A pure compiler refactor, no language change
- [ ] Bitfields — `field: uint32 : 5;`, packing consecutive narrow fields into
      one storage unit, for hardware registers, protocol headers, and C-layout
      interop (many syscall/kernel structs use them; `@packed` doesn't
      substitute). Follows the platform C ABI's per-target layout rules, so it
      pairs with the [C struct-passing ABI](#tooling-and-c-interop) work; the
      read-modify-write granularity under a `@volatile` struct must be
      specified
- [ ] `tuple<A, B, ...>` — a builtin heterogeneous, fixed-arity product: each
      position keeps its own statically-known type, accessed by position (`t.0`,
      `t.1`). For multiple return values
      (`fn divmod(a: int32, b: int32) -> tuple<int32, int32>`) and ad-hoc
      grouping without a one-off struct. Distinct from `slice<any>`: a tuple
      keeps each element's static type and a compile-time arity, where erasing
      every slot to `any` would collapse into a fixed-length `slice<any>`. Also
      the door to a statically-typed variadic later (no erasure), if wanted
- [ ] Literal adaptation to `slice<T>` — a literal in a slice-typed slot
      borrows from context, the compiler materializing the backing storage:
  - [x] string literals — `"hi"` adapts to a `slice<char>`/`slice<const char>`
        expected by a `let` or a parameter (NUL dropped), borrowing the string
        constant's bytes; implemented, see [Slices](docs/language.md#slices)
  - [ ] string-literal elements — reach the adaptation into nested/element
        positions, so `let dirs: slice<char>[2] = ["bin", "usr/bin"];` (an
        owned array *of slices* whose elements are string literals) works,
        replacing today's explicit `as` per element
        (`["bin" as slice<char>, ...]`). The adaptation fires only at
        top-level slots today, so the array-element path
        (`store_list_literal`'s plain `coerce`) rejects it with
        `array element: expected slice<char>, got char*`. The most tractable
        of this family: each element borrows from a **global string
        constant** (its `data` points at a `.str` global), so there is no
        backing-storage or lifetime question — safe even for a `@static`/global
        array of slices, unlike the array-literals case below
  - [ ] array literals — the generalization: `let dirs: slice<char*> = ["/bin"];`
        (or `["/bin", "/usr/bin"]` passed to a `slice<T>` parameter)
        materializes a hidden fixed-size backing array in the enclosing scope
        and borrows it, replacing today's two-step
        `let dirs: char*[2] = [...]; let view = dirs as slice<char*>;`. Design
        points: a global/`@static` slice needs the backing array promoted to a
        global constant (or is rejected initially); `slice<const T>` is the
        safe default target — adapting to a mutable `slice<T>` hands out a
        writable view of a compiler-materialized temporary and may be
        rejected; a bare `let v = [1, 2];` stays an ambiguous error (the
        annotation picks the storage: array = owned, slice = borrowed view)
- [ ] `new T { ... }` sugar — desugars to a block that calls a user-defined
      `fn new<T>() -> T*`, writes a [struct literal](docs/language.md#structs)
      through the result, and emits the pointer:
  ```c
  let var = new T { ... };
  // desugars to
  let var = {
      let tmp = new<T>();
      *tmp = T { ... };
      emit tmp;
  };
  ```
  Every piece already works (block expressions, `new<T>` in
  [memory](libmc/memory.mc), struct literals, deref-assign, whole-struct copy),
  so the only remaining work is the surface-syntax rewrite into the block above
  — no new codegen.

### Functions and methods

- [ ] `const` parameters — an immutable parameter (`fn f(const s: struct big)`)
      the callee promises not to mutate:
  - [x] pass by hidden reference: a large value (a struct) is passed by a hidden
        pointer instead of copied, so you get value semantics without
        hand-writing a pointer (see [const parameters](docs/language.md#const-parameters))
  - [ ] literal promotion: because the parameter is read-only, a literal
        argument can be promoted to its type at compile time. (For string
        formatting this is now done by a literal adapting to `slice<const uint8>`
        — see [`slice<T>`](docs/language.md#slices) — so `println("{}", a)`
        needs no `struct string`.)
- [ ] `mut` parameters and returns — the writable dual of `const`: a value
      passed (or returned) by hidden reference to the caller's storage, mutable
      *through* the reference but, like `const`, with its address unable to
      escape (`&` on it is rejected). The reference is scoped to the current
      context, so the underlying memory never leaks — the memory-safe
      counterpart to handing out a raw `T*`. In an rvalue position a `mut T`
      auto-derefs to `T` (copy on read, compare); in an lvalue position it
      writes through. Inherits `const`'s restrictions: not allowed on `@extern`
      parameters (ABI mismatch) and a function using it cannot **initially** be
      taken as a plain `fn(...)` value — the hidden-reference convention isn't
      carried by the bare `fn(...)` type. That is a source-level type-system
      simplification, not an ABI limit (LLVM already types the two conventions
      distinctly), and can later be lifted by making `mut`/`const` part of the
      function-pointer type — a distinct, non-coercible `fn(mut T)` — which is
      also what makes the interface-vtable reconciliation below rigorous. Note
      that, unlike `const`, `mut` on a **scalar**
      changes the calling convention (always by hidden reference) — that is the
      only way a write reaches the caller:
  - [x] `mut` parameters — `fn find(key: int32, mut out: int32) -> bool`: the
        callee may write `out` but cannot take its address or store it, so it is
        the memory-safe version of `fn find(key: int32, out: int32*) -> bool`.
        The non-escape guarantee is local and total, enforced with the same
        machinery `const` uses. The argument must be the caller's own writable
        storage of exactly the parameter's type; generics are supported
        (`swap<T>(mut a: T, mut b: T)`); implemented, see
        [mut parameters](docs/language.md#mut-parameters)
  - [x] generic overloads mixing `mut` — overloads of one generic name may
        disagree on which positions are `mut`: at a position any candidate
        marks `mut`, an lvalue argument's address is formed up front and the
        lvalue/value decision is deferred until after overload resolution. An
        rvalue rules out the overloads that are `mut` at its position (an
        lvalue rules nothing out, so a same-shape `mut`/non-`mut` pair stays
        ambiguous), and the writability checks (`const`, `@volatile`,
        `@packed`) are judged against the chosen overload only; implemented,
        see [mut parameters](docs/language.md#mut-parameters)
  - [ ] `for … in` protocol over `mut` — `_next` still takes its element slot
        as a raw pointer (`fn list_next<T>(it: …, out: T*) -> bool`) because
        the compiler emits the `_next(&it, &slot)` call itself; teaching that
        protocol codegen to form a `mut` argument makes
        `fn list_next<T>(it: …, mut out: T) -> bool` the expected shape and
        removes the last stdlib out-pointer (the `get` family already
        migrated). The `_it`/`_next` signatures are a compiler-checked
        convention, so this is a coordinated compiler + stdlib change
  - [ ] `mut` returns — a function that returns an lvalue:
        `fn string_at(self: string*, i: uint64) -> mut char` makes
        `string_at(&str, 0) = '/'` legal (as well as comparing it or copying it
        out with `let c = string_at(&str, 0)`). A call returning `mut T` is a
        new assignable expression category. To keep the reference from dangling
        without a lifetime system, a `mut` return may only be **formed from a
        `mut`/pointer parameter or a global — never from a local or a by-value
        parameter**; this conservative, checkable rule fits the `string_at`
        case (the result derives from `self`) and preserves the non-escape
        guarantee
  - [ ] motivating use case: method receivers — once methods / OOP (the item
        below) land, `const`/`mut`/by-value on `self` express
        read-only / mutating / consuming methods directly, replacing today's raw
        `self: <struct>*` receiver, and a `mut` return formed from `self` gives a
        memory-safe mutable accessor. See its receiver-kind note for the
        field-projection and vtable details
- [ ] Methods / OOP — `fn <struct>::<method>(self: <struct>*, ...)` definitions
      keyed to a struct, including `@private` methods and the special
      constructor/destructor below (the `for … in` protocol already dispatches
      by struct name to pave the way):
  ```c
  struct point { x: int32; y: int32; }
  fn point::constructor(self: struct point*, x: int32, y: int32) { ... }
  fn point::length2(self: struct point*) -> int32 { ... }
  @private fn point::helper(self: struct point*) { ... }
  ```

  - [ ] receiver kind — the `self: struct point*` above is the starting form,
        but once the `const` / `mut` / by-value parameters above exist the three
        receiver flavors fall out of them with no OOP-specific mechanism:
        `const self: point` (read-only method), `mut self: point` (mutating
        method — `&self` cannot escape, the memory-safe replacement for today's
        raw `self: point*`), and `self: point` (consuming/copying method). None
        require the caller to write `&`, so the method-call sugar below becomes
        plain `var.method()` with the hidden reference formed at the call. Two
        pieces of design work this pulls in: (1) `mut self` must project a
        field to an lvalue (`self.x = ...`) and, in a constructor, never fire
        its rvalue "copy on read" on the still-uninitialized whole `self`;
        (2) it must reconcile with the interface vtable — a `mut`-using function
        is normally not expressible as a plain `fn(...)` value, but in the
        `{ data*, vtable* }` fat pointer the receiver is already behind `data*`,
        so the vtable slot's first param is a genuine `T*` under an ABI the
        compiler controls internally. A `mut` return formed from `self` is then
        the natural spelling for a mutable accessor method
  - [ ] constructor — `fn <struct>::constructor(self: <struct>*, ...)`, the
        method that initializes a value: run by the `new <struct>(...)` sugar
        below, or invoked on a stack value. Constructing a stack-allocated
        struct implicitly `defer`s its destructor to the end of the enclosing
        scope, so a stack value cleans up after itself — RAII over the
        existing [`defer`](docs/language.md#defer) machinery. Naming is still
        open: `constructor`/`destructor` or `init`/`destroy` (both pairs on
        the table for now; examples use the former)
  - [ ] destructor — `fn <struct>::destructor(self: <struct>*)`, the cleanup
        counterpart: releases what the constructor acquired. Deferred
        automatically for a stack-constructed value (above); for a heap
        `new`, run explicitly before the memory is freed
  - [ ] method-call sugar — `var->method(...)` desugars to
        `point::method(var, ...)`, passing the receiver as `self` (so `var` is a
        `struct point*`). That `->` form is the pre-receiver-kinds starting
        point; once the receiver kinds above land, calls are uniformly
        `var.method()` — the method's declared `self` kind dictates the receiver
        convention (`const`/`mut self` forms a hidden reference from the
        receiver's storage, by-value `self` copies), and a `point*` receiver
        (e.g. from `new`) auto-derefs one level first. No ambiguity, since
        methods key on the struct type, not the pointer type: `ptr.method()` can
        only mean the method on the pointee. The one honest tradeoff: this splits
        method calls from field access — the language keeps C's `.`/`->`
        distinction for **fields**, so `p->x` (field) and `p.method()` (method)
        on the same pointer use different operators (the Go/Rust/Swift
        receiver-adaptation model); `->` is retained only for field access:
    ```c
    var->length2();   // pre-receiver-kinds: desugars to point::length2(var)
    var.length2();    // once receiver kinds land
    ```
  - [ ] `new <struct>(...)` sugar — desugars to a block that allocates with
        `new<<struct>>()`, runs the constructor (above), and emits the pointer
        (the constructor counterpart to the
        [`new T { ... }`](#structs-arrays-and-data-layout) literal sugar):
    ```c
    let var = new point(3, 4);
    // desugars to
    let var = {
        let tmp = new<struct point>();
        point::constructor(tmp, 3, 4);
        emit tmp;
    };
    ```
  - [ ] interfaces — a named set of method signatures
        (`interface writer { fn write(self, buf: slice<const uint8>) -> int64; }`)
        that a struct satisfies by defining those methods, carried as a
        `{ data*, vtable* }` fat pointer for runtime polymorphism
        (heterogeneous lists, plugin-style APIs) — the dynamic counterpart to
        the static [generic bounds](#types-and-generics). Depends on methods
        (above). Open question: dynamic dispatch can only carry **reference**
        receivers (`const`/`mut self`) — the receiver travels as `data*`, so a
        by-value (consuming) `self` cannot cross the vtable without a copy;
        whether interfaces admit by-value receivers at all is undecided
- [x] `@noalias` parameters — C's `restrict`: mark a pointer parameter
      (`fn copy(@noalias dst: uint8*, @noalias src: uint8*, n: uint64)`) as
      not overlapping any other pointer the function can reach, mapping to
      LLVM's `noalias` attribute so loads/stores can be reordered and
      vectorized — meaningful for the `mem*`-shaped functions in the standard
      library. The promise is unchecked: overlapping arguments are undefined
      behavior, as in C. Attribute-only, so it is allowed on `@extern` (the
      libc `restrict` family is marked); rejected on `mut` (aliasing is
      allowed there) and non-pointer parameters; implemented, see
      [@noalias parameters](docs/language.md#noalias-parameters)
- [x] `@nonnull` parameters — a checked "definitely non-null" refinement over
      C's nullable-by-default `T*`, opt-in per parameter: the callee is
      statically guaranteed a non-null argument and skips the re-check, and the
      guarantee travels transitively (a plain-`T*` caller must check before
      passing to a `@nonnull` callee, but a `@nonnull` callee passing its own
      parameter onward needs no check). This is a *checked* type refinement, not
      an unchecked optimizer hint: passing a plain `T*` to a `@nonnull` slot
      without proof is a compile error. Attribute-only at runtime, sharing
      `T*`'s representation and reusing the `@noalias` machinery above (LLVM
      `nonnull`/`dereferenceable` param attributes, the per-param annotation
      slot, `.mci` round-trip). Represented as a per-binding fact set like
      `const_locals`, not a new type. Always-non-null sources (`&x`,
      string/array-literal decay, array decay) construct non-null directly,
      and passing the `null` literal to a `@nonnull` parameter is a compile
      error. To keep the fact sound, a `@nonnull` parameter cannot be
      reassigned or have its address taken, and a function with `@nonnull`
      parameters cannot be a function value. Composes with `const` and
      `@noalias`; allowed on `@extern` (attribute-only, like `@noalias`);
      `@nonnull mut` rejected initially; implemented, see
      [@nonnull parameters](docs/language.md#nonnull-parameters):
  - [ ] escape hatch — crossing from a heap or returned `T*` into a `@nonnull`
        slot needs an explicit programmer assertion (postfix `p!` or
        `assume_nonnull(p)`, spelling undecided); until it (or flow-narrowing
        below) lands, only the always-non-null sources cross
  - [ ] flow-narrowing — narrow a plain `T*` to non-null from a null check, so
        idiomatic code needs no escape hatch: `if (p != null) { ... }` narrows
        the then-branch, and the C-idiomatic guard `if (p == null) return;`
        narrows the remainder of the enclosing scope. Tractable because mcc has
        only structured control flow (no `goto`): syntax-directed narrowing on
        the AST, not a general CFG dataflow pass. Starts with those two `if`
        guards; `and`/`or` threading, loop bodies, and divergence-awareness are
        follow-on. Synergy with
        [`@noreturn`/`unreachable`](#functions-and-methods): once `@noreturn`
        lets `if (p == null) abort();` count as divergence, early-guard
        narrowing covers more cases (not a blocker, since
        `return`/`break`/`continue` already diverge):
    - [ ] first-class `T!` type — non-null on return types, locals, struct
          fields, and function-pointer types, which needs a real distinct type
          rather than a per-binding fact (a larger blast radius). Optional and
          deferred; pursue only if demand for non-null returns or fields
          appears. A non-null return type extends return types the same way
          [`mut` returns](#functions-and-methods) does, so sequence it after
          that work if it happens
- [ ] Native variadic arguments — `fn f(args: slice<const any>)` (with
      `fn f(args...)` as sugar): a trailing `slice<const any>` parameter collects
      the call's extra arguments, so `f(x, a, b, c)` (after `f`'s fixed
      parameters) gathers `a, b, c` into `args`. The caller boxes each into a
      caller-stack [`any`](#structs-arrays-and-data-layout) and passes a read-only
      [`slice<const any>`](#structs-arrays-and-data-layout) over them —
      allocation-free.
      The callee walks it with `for a in args` and a
      `case type (a) { when int32 n: … else: … }` type-switch (the open `any`
      universe makes the `else` required). This is the runtime, type-erased
      variadic model (printf / `{}`-placeholder formatting); a statically-typed
      `tuple<…>` variant, processed by compile-time iteration, is a possible
      later path. Depends on any, slice, and typeof/typeid.
- [ ] C variadics — the C-ABI `...`/`va_list` machinery, beyond forwarding:
  - [x] variadic declarations and `va_list` forwarding — implemented, see
        [Variadic functions](docs/language.md#variadic-functions)
  - [ ] `va_arg` interop — read individual arguments from a C-ABI `va_list`
        in mcc (today a `va_list` can only be forwarded to a C `v*` function)
- [ ] `@noreturn` and `unreachable` — `@noreturn` marks a function that never
      returns (`exit`, `abort`, an infinite loop), so a call needs no dummy
      return after it and the backend drops the dead path; `unreachable` is a
      statement asserting a path is never reached (lowering to LLVM
      `unreachable`), for the fall-through of an
      [exhaustive `case`](#types-and-generics) or an impossible branch

### Metaprogramming and builtins

- [ ] Compile-time macros:
  - [ ] macro functions — `@macro <name>(<args>) { ... }`, compile-time
        expansion (`@inline` already covers the call-overhead case)
  - [ ] `@define <name> = <value>` — a named compile-time substitution
- [ ] Bit-twiddling builtins — `byte_swap<T>` (`llvm.bswap`) and
      `bit_reverse<T>` (`llvm.bitreverse`) over the integer types
- [ ] Builtin `enumerate` — pairing each element with its `uint64` position:
  - [x] over containers, arrays, and slices — implemented, see
        [Control flow](docs/language.md#control-flow)
  - [ ] over the builtin `range` — today `enumerate` rejects `range` (the
        counter *is* the value); allow it for a non-zero `start`, where the
        index (from 0) and the counter (from `start`) genuinely differ
- [x] Error directives — `@static_assert(cond, msg)` and `@error(msg)`, both
      emitting a hard compile error through the existing error path, with the
      condition folded by `eval_const` **during code generation** (not during
      parsing: `sizeof(T)`/`alignof`/`offsetof`/`const` references need the
      type system, so the fold has to wait for codegen). `@static_assert(cond,
      msg)` fails the compile when the folded boolean is false (a nonzero
      int/bool constant passes), for validating struct layouts, alignment
      requirements, or type sizes before linking; `@error(msg)` fails
      unconditionally at its position, useful guarded by `@if`
      (`@if(!TARGET_OS) @error("unsupported OS");`). No new subsystem, reusing
      error emission and `eval_const`. Minimal surface first: top-level
      position (where struct-layout assertions live), with statement position
      a later add. Generics synergy: inside a generic body each fires *per
      instantiation* at monomorphization, a lightweight type-parameter
      constraint that complements the planned
      [interface bounds](#types-and-generics), and an assert in a
      never-instantiated generic correctly never fires. Top-level position is
      implemented, see
      [Error directives](docs/language.md#error-directives); statement position
      (and the per-instantiation generic behavior it unlocks) is the later add
- [ ] Warning subsystem — a non-fatal diagnostic channel, the foundation the
      warning directives below and enum-exhaustiveness checking both build on.
      Today every diagnostic is a hard `file: error: line N: msg` that aborts;
      this collects warnings on the `CodeGen` instance and has the driver print
      `file: warning: line N: msg` to stderr *after* generation succeeds,
      without aborting, plus a `-Werror` toggle that promotes warnings to the
      failure exit path. Decided default: `-Werror` off in normal builds, on in
      CI. No user-facing surface of its own; its consumers are the warning
      directives below and enum
      [`case` exhaustiveness](#types-and-generics):
  - [ ] Warning directives — `@warning(msg)` and `@deprecated(msg)` over the
        channel above. `@warning(msg)` is `@error`'s non-fatal twin, emitting a
        warning at its position. `@deprecated(msg)` is different in kind: a
        declaration attribute on a function that fires a diagnostic (a
        **warning by default**, not an error) at each *call site*, pointing at
        the caller with a migration message. Storage mirrors the
        `@noalias`/`const`/`mut` param-set pattern on the `Func` node; the
        call-site hook lives in `gen_call`. It round-trips through `.mci` for
        free for generic and `@inline` functions (verbatim source-span
        emission), needing explicit re-emission only for concrete exported
        prototypes. Default severity is warn deliberately: a hard error would
        make a deprecated alias uncallable and break importers, defeating the
        purpose. Motivating use case: the four generic `// deprecated`
        forwarders in [memory](libmc/memory.mc) (`copy_bytes`, `copy_items`,
        `set_bytes`, `set_items`) forward silently today, where a
        `@deprecated("use bytecopy instead")` that warns is exactly right and,
        being generic, round-trips through `.mci` with no extra work. Scope v1
        to functions (types/enums/globals later); the terminal escalation to a
        hard error is not a flag on `@deprecated` but its own
        [`@removed` tombstone](#metaprogramming-and-builtins) directive below.
        Known task before it lands: repoint the internal stdlib and example
        calls to the deprecated forwarders onto the new names (a one-time
        cleanup, since CI runs `-Werror`)
- [ ] `@removed(msg)` tombstones (the `@removed` name is tentative) — the
      terminal state of the function-availability lifecycle, one step past
      [`@deprecated`](#metaprogramming-and-builtins) above: a function goes from
      available, to `@deprecated(msg)` (warns, still callable), to `@removed(msg)`
      (a hard compile **error** at every call site), to finally deleted (the name
      gone, a generic "unknown function"). A declaration attribute on a function
      that turns each *call site* into a compile error carrying the migration
      message, so pulling an implementation still gives callers a targeted
      `copy_bytes was removed: use bytecopy instead` for a release cycle rather
      than a bare `unknown function 'copy_bytes'`. A small delta on `@deprecated`,
      reusing the same machinery: the call-site hook in `gen_call`, the `.mci`
      round-trip (so importers of a removed stdlib function get the error), and
      the `Func`-node message storage. Two differences only: (1) it emits through
      the existing error/abort path, **not** the warning channel, so unlike
      `@warning`/`@deprecated` it does **not** depend on the
      [warning subsystem](#metaprogramming-and-builtins); its only real
      dependency is `@deprecated`'s call-site attribution plumbing above; (2) it
      allows a **bodiless tombstone** declaration
      (`@removed("use bytecopy") fn copy_bytes<T>(dst: T*, src: T*, n: uint64);`
      with no body, a small parser allowance like an `@extern` prototype), since
      the implementation is gone. Prior art: Swift's `@available(..., obsoleted:)`
      and C#'s `[Obsolete(msg, error: true)]`. Open question: the bodiless
      tombstone (recommended) versus keeping a dead stub body
- [ ] [Inline assembly](docs/language.md#inline-assembly) — arch-specific (pair with `@if` on
      `TARGET_ARCH`), preferring intrinsics where they exist:
  - [x] `@asm(...)` expression/block — an LLVM inline-asm call with an
        operand model (`$out`/`$N` operands, `=r`/`r` register class, `${N:w}`
        modifiers); output-less asm is implicitly volatile. Works on the host
        arch, including a same-arch cross `--target`
  - [x] `@asm fn` — sugar for a function whose body is one `@asm(...)`
        expression over its parameters: operands, register-allocated, no
        `ret` (the function's epilogue returns)
  - [x] `@clobbers("memory", "cc", "x0", ...)` — declare registers/flags the
        asm touches beyond its operands, right after `@asm`
  - [ ] pinning an operand to a fixed physical register (for
        syscalls/fixed-register instructions); foreign-arch cross-`--target`
        support
  - [ ] `@naked` — separate opt-in for no-prologue/epilogue functions
        (`_start`, interrupt entry, trampolines): args arrive in the ABI
        registers and the body writes its own `ret`

### Strings and formatting

- [ ] Formatted `print`/`println` — Rust/Python-style `{}` placeholders,
      type-driven (no `%`-letters), written in mcc over the
      [native variadic](#functions-and-methods) `slice<const any>`; enables
      compile-time format checking and per-struct `format` methods later. The
      signature is `fn println(format: slice<const uint8>, args: slice<const any>)`:
      a string literal adapts to `format` at the call site (so `println("{}", a)`
      works directly), and an owned `struct string` borrows in with
      `str as slice<uint8>` — both via the
      [`slice<T>`](docs/language.md#slices) borrowing rules:
  - [x] printf-style `%` formatting — today's `print`/`println` in the
        [standard library](README.md#standard-library), which the `{}` model
        will supersede
  - [ ] formatting over the `slice<const uint8>` format with bare/sequential and
        positional placeholders (`"{d} {f} {x} {s}"`, `"{0:d} {1:f} {2:x} {3:s}"`),
        parsed at runtime
  - [ ] format modifiers — precision and zero-padded width (`.Nf`, `Nx`, `0Nx`,
        `0x0Nx`, `Ns`, and `sN`), e.g. `{.8f}`, `{08x}`, `{0x08x}`, `{20s}`, `{s20}`
- [ ] String interpolation — `println("x = {x}")`: a string literal with
      `{expr}` holes desugars at compile time into the formatted
      `println("{}", ...)` call above (`{{`/`}}` escape a literal brace), so it
      is surface syntax only — no new runtime. Depends on formatted print
      (above) and the [native variadics](#functions-and-methods) it builds on

### Tooling and C interop

- [ ] Instantiation backtraces on errors — an error inside a monomorphized body
      today prints as a bare line in the template file with no trace of how the
      compiler reached it; attach a source-level note chain to `LangError`
      (which today carries only message/line/source) so the driver prints
      `file: note: line N: ...` lines after the unchanged primary
      `file: error: line N: msg`:
  ```
  list.mc: error: line N: <the actual problem>
    note: in instantiation of list<char> (from string) at string.mc:LL
    note: in instantiation of string here at yourcode.mc:MM
  ```
  the "in instantiation of ..." note chain of C++ and Rust. Frames are built on
  the exception-unwind path through the existing `try`/`except`/`finally` at the
  two monomorphization entry points (`instantiate` for generic functions,
  `instantiate_struct` for generic structs), so function and struct instances
  interleave (one `string` call nests a generic-function instance and a
  generic-struct instance) and there is no live push/pop stack to corrupt.
  Instantiations are memoized, so a cached instantiation reports the first
  triggering path, matching C++/Rust. Independent of the
  [warning subsystem](#metaprogramming-and-builtins): errors already have their
  own terminal render path, so this extends that path and never touches the
  non-fatal warning channel; the two share only a one-line severity-formatting
  helper (`{where}: {severity}: line N: {msg}`), introduced by whichever ships
  first and reused by the other. Test-safe: the primary error line stays
  byte-identical and notes appear only when the instantiation chain is
  non-empty, so the suite's `str(LangError)` matches hold and the
  substring/`startswith` stderr checks in `test_cli.py` are undisturbed:
  - [ ] Import / inclusion / macro frames — additive frame sources for the same
        note chain, no new render machinery: the import chain (`merge_imports`),
        `@if`-inclusion (`flatten_conditionals`), and eventual macro expansion.
        The macro-frame part is gated on
        [`@macro`](#metaprogramming-and-builtins) existing, so it rides in
        whenever macros land; the import and inclusion frames can come first
- [ ] Linker selection — `--linker=/path/to/ld` to pick a specific linker
      (today whatever the driver `cc` defaults to)
- [ ] Compiler-driver selection — `--cc=/path/to/cc` to choose the C driver used
      for linking (today the system `cc` on `PATH`)
- [ ] Assembly output — `--emit-asm` (`-S`) to write target `.s` assembly text
      (alongside `--emit-llvm` for IR and `-c` for an object), for inspection or
      handing to an external assembler
- [ ] C struct-passing ABI — classify by-value struct arguments and returns
      into registers/`byval`/`sret` per the platform ABI, so structs cross the
      C boundary correctly (today only scalars and pointers are ABI-compatible;
      see [C ABI compatibility](README.md#c-abi-compatibility))
- [ ] Namespaced exported symbols — emit mcc functions under a mangled/prefixed
      symbol (the `@extern` libc bindings keep their real names via `@symbol`),
      so a precompiled mcc library does not clash with libc/system symbols when
      linked. Required for shipping the standard library precompiled: today
      names like `errno` (a libSystem thread-local) and `crc32` (zlib) collide,
      so the stdlib is compiled from source instead of linked as `libmc`
  - [ ] Library output — compile to a static (`.a`) or shared (`.so`/`.dylib`)
        library, paired with the `.mci` interface so consumers can link
        against it. Depends on namespaced exported symbols (above) so the
        archive links without collisions
- [ ] C header generation — emit a `.h` of the public surface (like
      `--emit-interface` does for `.mci`), so C code can call into an mcc
      object or library

<!-- Add upcoming features here, e.g. - [ ] feature — short note -->
