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
- [x] [Function overloading](docs/language.md#function-overloading) — one name,
      several parameter lists, resolved at the call site by arity and argument
      types: generic, concrete, and mixed sets (a concrete overload beats a
      generic on an exact match), signature-derived mangled symbols (a single
      definition keeps its plain C-linkable name), `.mci` support, and
      [order-independent template symbol bases](docs/language.md#template-symbols);
      all overloads of a name currently live in one defining module, a
      rule slated to be lifted by the planned open overload sets item
      in [Functions and methods](#functions-and-methods)
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
- [x] [Struct extension of a type parameter](docs/language.md#structs) — a bare
      type parameter in the `extends` slot (`struct wrapper<T> extends T`)
      embeds `T`'s fields as the layout prefix per instantiation (the
      intrusive-container shape); single base by design
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

- [x] Core — `memory` (typed `alloc`/`dealloc`), `std` (`print`/`println`,
      `swap`/`replace`)
- [x] Containers — `list`, `stack`, `queue`, `ring`, `set`, `dict`, `string` (counting
      loops use the builtin [`range`](docs/language.md#control-flow))
- [x] Hashing — `splitmix64`, `fnv1a`, `murmur3`, `crc32`, `md5`
- [x] [libc bindings](docs/language.md#reaching-libc) — `stdio`, `stdlib`, `string`, `ctype`,
      `math`, `limits`, `float`, `time`, `errno`

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

Grouped by scope. Within a group, a feature that depends on another comes
after it — as a subitem when it strictly builds on its parent. A planned
feature that expands an existing one is grouped with it, the implemented half
appearing as a checked subitem (as `const` parameters and inline assembly
already do).

### Types and generics

- [ ] `@typeof(expr)` — use an expression's static type in a type position,
      including in an alias: `type t = @typeof(var);`. The `@` prefix is
      deliberate: it is resolved entirely at compile time and yields a
      type, never a value, so it joins the `@if`/`@else` compile-time
      family, while the value-level
      [`typename`](#metaprogramming-and-builtins) builtin stays bare next
      to `sizeof` because its result folds into an actual variable. Its
      own hard problem is typing an expression without emitting IR in the
      single-pass compiler, so a v1 is restricted to emission-free forms
      like the `@typeof(var)` above. Shares the type-identity concept with
      [`any`](#structs-arrays-and-data-layout)'s tag scheme but is not a
      build dependency of it (`any`'s boxing site knows the source's static
      type, and a `case type` arm names its type literally)
- [ ] Generic type parameters — beyond the monomorphized basics:
  - [x] generics on functions and structs — implemented, see
        [Generics](docs/language.md#generics)
  - [ ] generic type aliases — a type-parameter list on a `type` declaration,
        naming a family of existing types (a wider generic partially applied,
        a comparator shape over any element):
    ```c
    type entry<T> = pair<char*, T>;
    type cmp<T> = fn(T, T) -> bool;
    ```
    An alias stays **transparent**, so a generic alias is a type-level
    function expanded at use: `entry<int32>` *is* `pair<char*, int32>`, the
    two spellings share one struct instantiation (expansion happens in the
    type resolver, before the instantiation cache is keyed), and the alias
    mints no monomorphized artifact of its own. Everything downstream
    follows from transparency: an alias instantiation works in the
    `extends` slot (a concrete alias there already works today) and
    composes with the shipped
    [bare-parameter base](docs/language.md#structs),
    serves as a generic bound, appears inside another generic's body
    (`entry<U>` with `U` the outer parameter), and a method lookup under
    [non-struct receivers](#functions-and-methods) sees the underlying
    instantiation, not a separate namespace. The rules: arity is checked at
    the use site (a bare `entry` or a wrong argument count is an error,
    replacing today's blanket "type alias is not generic"); the target
    resolves at the declaration site with **only the alias's own
    parameters bound** (the use site resolves the arguments, then hands
    over, extending the hygiene of the existing declaration-site source
    switch), so an outer generic's same-named parameter never leaks into
    the target; the name-based cyclic-alias rule stays, so a
    self-referential generic alias (`type node<T> = pair<T, node<T>*>`)
    remains an error (recursive types stay structs' job, via the
    self-reference-through-a-pointer rule); and an unused parameter is
    accepted, as on structs and functions, but is inert where a struct's
    is not: transparency makes a phantom `m<bool>` and `m<char>` the same
    type, where a struct's unused-parameter instantiations stay nominally
    distinct. The `.mci` round-trip renders the parameter list and stops
    counting the alias's own parameters as external references, mirroring
    structs; import merging keeps rejecting duplicates by name. Parameter
    defaults (the item below) and bounds extend naturally to alias
    parameters when they land. One deliberate exclusion: a
    convention-carrying comparator type (`fn(const T, const T) -> bool`)
    is not this item's job. Today a const-scalar function's value type
    erases the `const` and a const-struct function cannot be a function
    value at all, so `const` in a written `fn(...)` type has nothing to
    match; carrying `const`/`mut` in the function-pointer type is the
    [`mut` item](#functions-and-methods)'s planned lift, which `cmp`
    then picks up transparently
  - [x] [defaults](docs/language.md#type-parameter-defaults) — a declared
        fallback type parameter, on functions
        (`fn myfunc<T = uint8*>(x: T) { ... }`) and structs
        (`struct range<T = int64> { ... }`), used when a type argument isn't
        supplied or inferable from a _typed_ value. The strongly-typed way to
        pick a default — declared at the definition, not guessed from an untyped
        literal at the use site (`let a = 0` and a no-anchor `struct range { … }`
        stay ambiguous errors, not silently `int32`). Trailing-only, earlier
        parameters may be referenced (`<T, U = T*>`), a defaulted tail may be
        omitted from an explicit type-argument list, and a bare defaulted
        struct name is a complete written type
  - [ ] bounds — constrain a parameter with `fn myfunc<T extends mystruct>(x: T)`
        (a struct and its `extends` specializations); the explicit-set form,
        once sketched here as `T in (t1, t2, ...)`, is settled under a
        different spelling as the closed-type-groups sub-item below
    - [x] [closed type groups](docs/language.md#closed-type-groups) — a
          pipe-separated closed group of types after
          the parameter name, `fn f<T: int64 | int32>(x: T)`, constrains
          what `T` may instantiate to. The pipe over a comma is deliberate:
          a comma list is ambiguous against multiple parameters and
          defaults (`<T: int64, U, V: char*>` cannot be parsed), while the
          pipe composes cleanly (`<T: int64 | int32 = int32, U>`, and a
          default must name a group member); group members are concrete
          types only in v1, no patterns like `T*`. Deduction is unchanged,
          the group is a post-deduction viability filter: a call whose
          deduced `T` falls outside the group is a compile error at the
          call site naming the offending type and the group. Checking is
          **eager**: every listed member is instantiated and fully
          type-checked at end of codegen whether or not it is ever called
          (the finalize hook that shipped with
          [generic arms in `case type`](#structs-arrays-and-data-layout)
          is the natural place), so a group member the body does not
          compile for errors at the declaration, matching the
          multi-type-arm precedent and the same stance that an undefined
          use is a compile error; never-called member instantiations are
          dead code the linker strips in object mode, and groups are
          small by nature. The big payoff is overload partitioning:
          same-pattern templates with **disjoint** groups become a
          resolvable overload set, `fn show<T: int32 | int16 | int8>(x: T)`
          and `fn show<T: uint32 | uint16 | uint8>(x: T)` coexist with
          deduction plus the group filter picking one, deliberately
          relaxing the shipped same-pattern declare-time template
          collision; same-pattern templates with **overlapping** groups
          still collide at declare time (the marker-collision philosophy
          of the shipped
          [function overloading](docs/language.md#function-overloading)
          duplicate rule, cross-module like today's template collision).
          Overload ranking gains a middle tier, concrete beats bounded
          generic beats unbounded generic, extending the shipped
          concrete-beats-generic rule; consequently the group becomes
          part of the template's
          [symbol base](docs/language.md#template-symbols) and collision
          key, since two disjoint-group same-pattern templates are
          distinct symbols, and `.mci` prototypes render the group. This
          is the function-declaration counterpart of the shipped
          multi-type `case type` arms, the same bounded genericity
          without interfaces: the check set is written in source, with no
          action at a distance. It is complementary to the planned
          [interfaces](#functions-and-methods), an identity-enumerated
          constraint beside their behavioral one, not a substitute;
          `typename(T)` composes trivially. The motivating example is the
          signed/unsigned formatter grouping at the function level, with
          no `case type` needed
    - [ ] interface bounds — `fn myfunc<T implements I>(x: T)`, asserting
          that `T` implements interface `I`: checked at each monomorphized
          instantiation (the concrete type must define every method `I`
          names), then calls dispatch statically — no fat pointer, no
          interface vtable (a [polymorphic struct](#functions-and-methods)
          bound as `T` keeps its own embedded-vtable dispatch; "static"
          here means no interface machinery is formed). The static
          counterpart of the dynamic
          [interfaces](#functions-and-methods) dispatch; depends on
          interface declarations and the methods they are made of, so it
          lands after both
- [x] Enum member reuse — a derived enum inherits a base enum's members by
      naming it in the existing `:` slot:
      `enum x_status: x_error { SUCCESS = 0 }` copies `x_error`'s member table
      and adopts its underlying type, then adds its own, so `x_status::NOT_FOUND`
      resolves and folds equal to `x_error::NOT_FOUND`. Compile-time only, with
      no runtime or ABI change: a single-function change in `register_enum`
      (merge the base member table, adopt its underlying type, run the base's
      access check so a `@private` base cannot be extended cross-file), leaving
      the parser, the tree-sitter/tmLanguage grammars, and the `.mci`
      round-trip untouched (the `:` slot already parses an enum name). A name
      collision with an inherited member is a hard error, even with an
      identical value (the one newly-rejected pattern: a derived enum
      redeclaring a base member's name used to get an independent member);
      value aliasing across base and derived is allowed (enums already allow
      it); the base must be a single, direct enum name (not a `type` alias to
      an enum) appearing textually before the derived enum or in an imported
      file. Delivers DRY reuse plus the `x_status::NOT_FOUND` spelling, but
      **zero new type safety**: enum values are transparent integers today, so
      a derived value stays indistinguishable from its base and from a plain
      int. The directional base-to-derived safety that reuse suggests needs
      [nominal enums](#types-and-generics) below; implemented, see
      [Enums](docs/language.md#enums):
  - [ ] empty derived enum as re-export — `enum local_status: x_error {}`
        (a base, no members of its own) re-exports the base's member table
        under a new name; today the "enum has no members" rule rejects the
        empty body even when a base is named. Relax that rule only when a
        base is present — a baseless `enum e {}` stays an error, since it
        genuinely has no values. A naming convenience while enums are
        transparent; under [nominal enums](#types-and-generics) below it
        becomes a genuine distinct type over the same value set
- [ ] Nominal enums — make an enum value carry its type identity instead of
      collapsing to its underlying integer. Today an enum used as a type
      becomes a raw `int32` (or its declared underlying), so `x_status` and
      `x_error` values mix freely with each other and with plain ints. A large,
      **backward-incompatible** semantics change: nominal enums begin rejecting
      some code that compiles now (implicit enum/int and enum/enum mixing), so
      it needs a migration story: staged warnings, over the shipped
      [warning subsystem](#metaprogramming-and-builtins), before errors. That
      story also covers how an enum boxes into the planned
      [`any`](#structs-arrays-and-data-layout): a transparent enum carries
      its underlying type's tag, a nominal enum gets its own type-id,
      silently changing which `case type` arm matches. It is the
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
          non-fatal first phase rides the shipped
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
  - [x] `any` — a tagged union over the above: a union-style payload plus a
        compile-time type-id discriminant, so the live member is recovered
        safely (`case type`). The element type of the
        [variadic](#functions-and-methods) pack's `slice<const any>`, and the
        deepest remaining unblocker of the strings chain: `any`, then
        [native variadics](#functions-and-methods), then
        [formatted `{}` print](#strings-and-formatting), then
        [string interpolation](#strings-and-formatting). Depends on unions
        (above) and a compile-time type-id scheme;
        [`@typeof`](#types-and-generics) shares the type-identity concept but
        is **not** a build dependency (the boxing site knows the source's
        static type, and a `case type` arm names its type literally).
        Settled design: `any` is a compiler-built interned `LangType` on the
        `slice<T>` builtin pattern (a reserved-name resolution arm plus an
        interned constructor), no source declaration, no `.mci`
        implications. Layout is `{ tag: uint64; payload: 16 bytes, align 8 }`,
        24 bytes, the payload sized so `slice<char>` fits by value (the
        formatted-print chain passes strings as slices); the existing
        dual-site layout invariant (types and generator agree) applies. The
        tag is the 64-bit FNV-1a hash of the canonical mangled type name,
        registry-free by design: a sequential whole-program registry would
        break under the precompiled-stdlib direction (a prebuilt object's
        boxed `any`s would carry the producer's ids), while hashes are
        deterministic across compilations, fold to constants, and lower
        `case type` onto the existing integer-equality `case` codegen. An
        in-compile hash collision is detected and errored; a per-type
        `linkonce_odr` descriptor pointer (RTTI-style) is the recorded
        upgrade path if runtime type names are ever wanted, though the
        [`typename`](#metaprogramming-and-builtins) builtin inside
        the generic-arms sub-item below covers the arm-side need
        statically, leaving a bare `else` arm as the only descriptor-only
        case. The v1 boxable
        set is primitives, pointers (each pointer type its own tag), and
        slices; structs and arrays are rejected (by value the payload is
        unbounded, by pointer the lifetime goes implicit; `&s` is the
        explicit escape). Values wrap implicitly at the coerce choke point,
        an untyped literal anchoring via the adaptable-placeholder rule
        (`5` boxes as `int32`, the same rule as call-site inference, needed
        for `println("{}", 5)`); there is no unwrap outside `case type` in
        v1, since with no exceptions in the language an unchecked `as` would
        be either a tag-ignoring pun or a new trap mechanism. The
        type-switch is `case type (a) { when int32 n: ... else: ... }`:
        `type` stays a contextual keyword (it is not reserved, and `case`
        expects `(` next, so the grammar has room), a binding is required,
        multi-type arms are excluded in v1 (the lift is recorded in the
        generic-arms sub-item below), and `else` is required (the `any`
        universe is open); the scrutinee is an `any`, with `any*`
        auto-dereferencing per the member-access precedent. The
        `when T name:` arm is deliberately shaped so a future
        payload-carrying-enum `when Variant(x):` reads as kin, and
        `tuple<A, B, ...>` below stays the complementary non-erased product.
        A transparent enum boxes under its underlying type's tag;
        [nominal enums](#types-and-generics) give an enum its own tag, a
        silent `case type` change folded into that item's migration story.
        Implemented as settled, see
        [The any type](docs/language.md#the-any-type); the nested items
        below are the follow-ups:
    - [ ] global/`@static` `any` initializers — teach the const-initializer
          path to box a constant; until then rejected with an explicit
          compile error, the same shape as the global union initializer gap
          below
    - [ ] struct boxing — lift the v1 struct/array rejection once the
          by-value-vs-by-pointer payload and lifetime questions are settled
    - [ ] checked `as` — recover a value outside `case type`. The core
          primitive is the initializer-style head `t = v as T` inside
          `with (...)`, with `v` an `any` (binding name first, chosen
          because it reads easier): it tests the boxed tag and, on a
          match, binds `t` to the unwrapped typed value, scoped to the
          true branch. The binding is mandatory (`with (v as T)` is a
          parse error), but the disambiguation lives in the head
          itself: the `with (...)` head is the checked context, `as`
          keeps its cast semantics everywhere else, and a non-`any`
          subject in a with head stays a compile error. The consuming
          surface is a single dedicated statement under a new `with`
          keyword, `with (t = v as T) f(t); else do_something();`,
          braces optional as usual
          (`with (t = v as T) { f(t); } else { do_something(); }`),
          chosen because it is clearly distinct from `if ... else` and
          from the ternary; an expression (ternary) form was
          considered and set aside in favor of the one distinct
          statement surface (it is also the harder form: with a
          generic `T`, every per-tag instantiation of the true
          expression would have to agree with the else expression on
          one result type). The head deliberately mirrors stage 2's
          bare unwrap `let t = v as T;`: identical spelling, with
          `with`/`else` supplying the mismatch handling that stage 2
          gets from the trap. `with` becomes a new reserved word, a
          pre-1.0 break with the same treatment as `typename` (lexer
          keyword plus editor grammars); verified currently unused as
          an identifier in `libmc/` and `examples/`. The statement is
          pure sugar over a two-arm `case type`
          (`when <pattern> t: ...; else: ...`), riding the shipped
          generic-arms machinery below unchanged: `T` may be a
          concrete resolvable type (single tag compare), an arm-scoped
          generic introduced by an unresolved bare name (monomorphized
          per boxed tag over the closed whole-program tag set, each
          instantiation's body type-checked with the same compile
          error naming an offending boxed type), or a `T*` pointer
          pattern, the same detection rule as generic case-type arms.
          Crucially the statement carries its else inline or has
          defined fall-through, so it does not wait for the
          checked-failure trap: an unmatched tag (including tag-0
          zeroed anys) takes the `else`, or falls through a lone
          `with` doing nothing (defined behavior); only the bare
          prefix unwrap of stage 2 stays parked behind the trap.
          v1 restrictions, settled as defaults: the binding
          initializer is the entire `with (...)` head (no
          `&&`/`||`/`!` composition, keeping the binding's scope
          obvious), and `while (t = v as T)` is not admitted (a
          possible later extension). Condenses the stdlib formatter's
          `case type (arg) { when T t:
          format(str, t, modifier); else:
          string_append(str, "(unknown)"); }` to a one-liner, and is
          the explicit-else alternative to the separately discussed
          implicit any-to-overload dispatch idea. Staged:
      - [x] stage 1: the `with` statement — `with (t = v as T) ...;
            else ...;`, reserving the keyword; optional `else` with
            defined fall-through, both brace styles, lowering to the
            two-arm `case type`; implemented, see
            [The with statement](docs/language.md#the-with-statement)
      - [ ] stage 2: bare `let t = v as T;` unwrap — trap on tag
            mismatch, still parked behind a checked-failure mechanism
            to hang the mismatch on; the generic-arms item below parks
            its else-optional carve-out behind the same mechanism
            (both want the same trap)
    - [x] generic arms in `case type` — `when T* ptr:` matches any
          boxed pointer type, the pointer fallback after concrete pointer
          arms (in the stdlib formatter,
          `when T* ptr: l = snprintf(buf, MAX_BUF_LEN, "%p", ptr);` after
          `when char* s:`; that fallback lands with the
          [native variadics](#functions-and-methods) item's stage 3,
          which consumes this item: formatter adoption is deliberately
          not a stage below). The lowering family is the one the
          [`case type` over interfaces](#functions-and-methods) sub-item
          records: a set-membership test over the compile-time FNV-1a
          tags of every pointer type that boxes into `any` anywhere in
          the whole program, statically known, no runtime registry. It
          is a true generic arm: each matching tag branches to the arm
          body monomorphized with `T` bound to that tag's pointee type
          (the monomorphize-everything stance, one instantiation per
          matching tag, the whole-program tag set bounding the
          duplication), so genuinely generic bodies work
          (`when T* p: h = fnv1a(p);`). An address-only body like the
          `%p` one never uses `T`, and its identical instantiations are
          expected to collapse under optimization (LLVM tail-merging),
          a non-normative expectation rather than a compiler guarantee:
          reliably proving `T`-independence at source level is fragile,
          nothing in the semantics depends on collapse, and a spike
          measures a synthetic many-tag program before stage 2 commits.
          The arm is a real generic context, which is the
          strongest argument for per-tag monomorphization over an
          erased, address-only binding: `when T* ptr: handle(ptr);`
          dispatching into a generic `handle<T>(p: T*)` (or an overload
          set) compiles per tag like any generic call, with generic
          dispatch, overload resolution, and the shipped
          concrete-beats-generic ranking all applying inside the arm,
          and a boxed pointer type for which no viable instantiation or
          overload of the called function exists is a **compile-time
          error** at the `case type` site naming the offending type,
          not a runtime gap; the closed whole-program tag set is
          exactly what makes this statically checkable, every
          instantiation the arm can ever take being enumerated and
          type-checked at compile time. A `T*` arm overlaps concrete
          pointer arms, so it rides the same first-match-wins
          textual-order rule the
          interface arm establishes (`when char* s:` stays ahead of the
          fallback). The reachability diagnostics (an unreachable later
          arm, firing per listed type for multi-type arms, and a
          `when T v:` arm ahead of `when T* ptr:` making the latter
          dead) are **hard errors** like the shipped duplicate-arm
          error, never warnings: arm order is statically wrong or
          right, never environment-dependent. A lone `T*` arm widens
          over pointers only, so non-pointer tags still need arms or
          `else`. Unlike the interface arm this depends only on the
          shipped `any`/`case type` machinery above, which is why it
          lives here and not under interfaces. Settled: the fully
          generic `when T v:` arm is admitted too, riding the identical
          machinery (the closed whole-program boxed-tag set, the same
          set-membership test over the compile-time FNV-1a tags, the
          arm body monomorphized once per matching tag with `T` bound
          to that tag's type, each instantiation fully type-checked
          with the same no-viable-overload compile error at the
          `case type` site naming the offending type). It is not
          redundant with a binding `else`, which is exactly the
          argument for admitting it: an `else` binding would hand over
          the erased `any` itself, through which nothing type-specific
          can be called, while `when T v:` hands over the unwrapped typed
          value. Composition with the pointer arm stays
          first-match-wins textual order, no overload-style specificity
          ranking between `T*` and `T` arms: `when T* ptr:` first
          consumes every pointer tag (binding `T` to the pointee type),
          then `when T v:` consumes every remaining tag (binding `T` to
          the boxed type itself), and a `T v` arm written before a `T*`
          arm makes the latter unreachable, the hard error above (a
          lone `when T v:` does match pointer tags too, binding e.g.
          `v: char*` with `T = char*`). `else` stays **mandatory** in
          v1 even beside a trailing `when T v:` arm. The else-optional
          carve-out (a trailing fully generic arm covers the closed tag
          set by
          construction, alone or with a `T*` arm in front, so `else`
          could be dropped and one written anyway flagged unreachable)
          is not retracted but deferred behind the bare-unwrap stage 2
          of the checked `as` sub-item above and its checked-failure
          mechanism (the `with` statement stage there carries its else
          inline and does not wait): tag-0 `any`
          values are defined behavior today (a struct literal omitting
          an `any` field zero-fills it, and that value flows into
          `case type` and lands in `else`), so making the unmatched
          edge `unreachable` would turn a defined shape into UB; the
          carve-out waits for a trap to hang that edge on, the same
          trap the bare checked-`as` unwrap wants. Generic-arm
          detection needs no new
          syntax: a bare name in arm position that resolves (builtin,
          struct, alias, enum, or an enclosing generic's active type
          binding) is a concrete arm, and an unresolved bare name with
          no args, dims, or fn shape and zero or one stars introduces
          an arm-scoped type parameter. The accepted trade-off, to be
          documented loudly: a typo like `when in32 n:` silently
          becomes a fully generic arm, though the failure is partially
          self-catching (later arms become unreachable and error, and
          per-tag type checks fire). The rule preserves verified
          behavior: inside `fn g<T>(...)`, `when T v:` is a concrete
          arm per instantiation today, and stays one because the
          enclosing binding resolves. The accepted trade
          is action at a distance, the same trade the pointer arm
          already accepted but wider: the generic arm is type-checked
          against every type boxed anywhere in the program, so a new
          `any` use in one module can newly fail a distant `case type`;
          the mitigation is structural, concrete arms and (future)
          interface arms in front consuming the tags handled specially,
          the generic arm body written against genuinely generic
          operations. The motivating end-state is the stdlib formatter:
          concrete arms for bespoke types, `when T* ptr:` printing `%p`
          as the pointer fallback, `when T v:` dispatching into the
          shipped `format` overload set of `libmc/format.mc` (an `else`
          still present until the deferred carve-out above lands), and
          every boxed type without a
          viable formatting path a compile error instead of a runtime
          gap. Settled in the same discussion: multi-type arms in type
          mode, `when int32, int16, int8 n: printf("%d", n);`, a
          comma-separated list of concrete types over one binding, the
          third member of the same arm family with identical lowering
          (set-membership over the listed types' FNV-1a tags) and the
          binding treated as an implicit generic: the body is
          monomorphized once per listed type, each instantiation fully
          type-checked, and a listed type with no viable overload or
          instantiation for a called function is the same compile-time
          error at the `case type` site naming the offending type,
          exactly like the `T v` and `T* ptr` arms. This supersedes
          the parent item's v1 "no multi-type arms in type mode" rule:
          that ban existed because a single binding could not carry
          one static type, and the generic reinterpretation dissolves
          the objection (`n` is never union-typed, each instantiation
          has a concrete type). Unlike the `T*` and `T` arms there is
          no action at a distance: the check set is written in source,
          the arm type-checked against precisely the listed types
          whether or not each is ever boxed anywhere in the program,
          making it the most predictable arm of the family, bounded
          genericity without interfaces (the planned closed type groups
          on generic parameters, in
          [Types and generics](#types-and-generics), are the
          function-declaration counterpart). Ordering and reachability
          are
          uniform with the rest: first-match-wins textual order, the
          unreachable-later-arm hard error firing per listed type
          (after `when char* s:`, an arm `when char*, int32 n:` has a
          dead `char*` member and is flagged). List hygiene: a
          duplicate type within one list is a compile error, and v1
          lists take concrete types only, no named generic patterns
          like `T*` as list members (what `T` would bind to per member
          is unresolved and nothing needs it). Exhaustiveness is
          unchanged: an explicit list does not close the universe, so
          `else` or later arms are still required. The motivating use
          is formatter-style grouping: `when int32, int16, int8 n:`
          shares one `%d` body, mechanically sound because the
          narrower instantiations pass through C default argument
          promotions at the varargs `printf` call, and
          `when uint32, uint16, uint8 n:` likewise shares `%u`. The
          arm family is now enumerated in full: a concrete arm (one
          tag), a multi-type arm (the explicit tag list), `when T*
          ptr:` (every pointer tag), `when T v:` (every tag), and the
          future [interface arm](#functions-and-methods) (the tags
          implementing the interface), all lowering to the same tag
          set-membership test with per-tag monomorphized bodies.
          Mechanics settled by exploration: single-pass compilation
          holds via deferred lowering with an end-of-codegen fixpoint
          worklist (a pre-scan is rejected: boxing is type-driven at
          the coerce choke point, and generics box types discovered
          only during instantiation). A generic arm lowers to a pending
          block plus a snapshot of the per-function compilation context
          (the same state `instantiate` already saves and restores);
          after the top-level body loop a worklist compiles arm copies
          per boxed tag, feeding back new boxing and instantiations
          until fixpoint. The llvmlite mechanics are probe-verified
          (late block appends, late `switch.add_case`, and late entry
          allocas all verify and JIT correctly), and `defer` semantics
          fall out correct via the snapshot. Termination has parity
          with recursive generic instantiation (a body boxing `T*`
          derives forever, same as `f<T>` calling `f<T*>`; no guard
          today, a depth cap is a cheap later add). Arm bodies
          monomorphize inline: the statement list compiles once per tag
          in fresh blocks of the enclosing function under a
          `type_bindings` overlay; outlining is rejected (closure
          conversion in a language without closures, and
          `return`/`break`/`defer` crossing the boundary need protocols
          that don't exist). Per-tag compile failures wrap in a Note
          ("in case type arm for {type}"), keeping the exact
          `file: error: line N: message` head. The boxed-tag registry
          must be boxed-only: today `any_tag` conflates boxing sites
          with arm mentions, and the generic arms need the set fed only
          from `gen_box_any`'s two callers (the coerce choke point and
          variadic extras collection). Fact soundness at generic-arm
          sites: at initial lowering, run the existing `loop_kill_set`
          walker over the arm bodies against `narrowed_nonnull` and
          blanket-drop `narrowed_paths` (the call-site blanket-kill
          precedent); facts entering arms are sound as-is. Accepted v1
          conservatisms: deferred arms are assumed to reach the end
          block for missing-return analysis (a finalize-time recheck is
          a clean follow-up), and warnings inside deferred bodies fire
          once per tag instantiation, matching generic function bodies
          (dedupe later if noisy). Precompiled-stdlib constraint, held
          from day one because it is cheap now and expensive to
          retrofit: a function containing generic arms closes over the
          compiling program's boxed set, so any stdlib function using
          them (the formatter) must be generic or `@inline` so it
          travels in `.mci` and monomorphizes in the consumer program.
          The pending-arm record is shaped as (tag-predicate,
          body-strategy) so the future interface arm (a single body
          over a fat pointer, no per-tag monomorphization) rides the
          same machinery. Two pinned tests are affected by design:
          `test_no_multi_type_arms` in `tests/test_any.py` flips (its
          comment documents the superseded v1 rule), and the parse-time
          mandatory-`else` test survives if the check moves to codegen
          with the message kept verbatim; implemented, see
          [The any type](docs/language.md#the-any-type). Staged:
      - [x] stage 1: multi-type arms — S-sized, zero deferral
            machinery (the check set is written in source): a comma
            list in `parse_case_type`, the existing concrete-arm
            lowering looped over the listed types sharing one body AST,
            per-listed-type seen entries giving the duplicate and
            unreachable diagnostics, per-type failures Note-wrapped;
            flips the pinned no-multi-type-arms test by design;
            implemented, see [The any type](docs/language.md#the-any-type)
      - [x] stage 2: `when T* ptr:` and `when T v:` together — L-sized,
            both riding the same machinery (the boxed-only tag
            registry, the snapshot/pending worklist, the finalize
            fixpoint, the detection rule, the hard-error reachability
            diagnostics); folds in factoring the `instantiate`
            save/restore tuple into a shared context dataclass, so
            future fact sets cannot silently miss either snapshot, and
            starts with a spike compiling one concrete arm body twice
            under two `type_bindings` overlays, validating the core
            before the deferral machinery; implemented, see
            [The any type](docs/language.md#the-any-type)
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
- [ ] Pointer arithmetic — pointers join the shipped binary and compound
      operator surface, no bespoke syntax: `p + n` and `p - n` advance by
      elements, the compound forms `p += n` / `p -= n` follow, `p - q` is
      the pointer difference, and the relational comparisons
      `<` `<=` `>` `>=` extend to pointers (equality, the `!= null`
      checks, and the ternary already work). This deliberately reverses
      the reference's documented exclusion ("there is no pointer
      arithmetic; use `&p[1]`"), on the finding that the exclusion buys
      no safety: `&p[i]` already compiles to the exact typed
      `getelementptr` that `p + i` would emit, for any `i`, so the
      capability exists today and only two things lack a spelling,
      pointer difference and pointer ordering (the `while (p < end)`
      scan loop). Semantics are C's, element-scaled, one semantics under
      two spellings: `p + n` is exactly `&p[n]` (`n` any integer type,
      scaled by `sizeof(pointee)`; `uint8*`, the raw-memory pointer,
      gives byte arithmetic, and there is no `void*` to make that
      ambiguous), `p - q` requires identical pointer types and yields
      the signed element distance as an `int64` (byte distance is
      `uint8*` arithmetic or the shipped pointer/integer `as` casts),
      and the relationals likewise require identical pointer types.
      Everything else keeps today's exact rejections: `p + q`, `*` `/`
      `%`, bitwise and shifts (tag-bit tricks keep the explicit
      `as uint64` round-trip), any arithmetic on function pointers
      (they keep `==`/`!=` only), and any `null` operand. Mechanically
      small and parser-free: the forms already parse and are rejected in
      `apply_binary`'s operand unification, so the work is a
      pointer/integer arm ahead of coercion (emitting the same GEP as
      indexing), widening the pointer comparison carve-out past
      `==`/`!=`, and a `ptrtoint`/`sub`/exact-division lowering for the
      difference; the compound forms fall out of compound assignment's
      existing `apply_binary` reuse. Composition with the shipped
      [`@nonnull` machinery](docs/language.md#nonnull-parameters)
      mirrors `&p[n]` exactly: the result is an always-non-null source
      just as `&p[n]` is today, `p += n` is an ordinary reassignment (it
      kills a narrowed local's fact, and stays rejected on a `@nonnull`
      parameter, which cannot be reassigned), and
      [`-Wunchecked-dereference`](docs/language.md#-wunchecked-dereference)
      is untouched, since arithmetic never dereferences (the deref of a
      derived pointer warns or proves by the existing rules). Not a
      posture change: [slices](docs/language.md#slices) remain the
      bounds-carrying view and the preferred API over runs of elements,
      the [libmc receiver migration](#functions-and-methods) to
      `mut`/`const` selves proceeds regardless (this lands in buffer
      internals and interop code, not on call surfaces), and pointer
      arithmetic is the explicit systems and interop lane (allocators,
      byte scanners, `memchr`-style loops, MMIO, ported C) the
      language's C lineage already promises. v1 is runtime-expression
      only: no pointer arithmetic inside `const` initializers or `@if`
      conditions. Addition is pointer-left only: `p + n` is the one
      accepted shape, and the commuted `n + p` is rejected, unlike C
      (the pointer is the base operand being advanced, and `n - p` has
      no meaning anyway, so addition matches subtraction's shape)
- [ ] `tuple<A, B, ...>` — a builtin heterogeneous, fixed-arity product: each
      position keeps its own statically-known type, accessed by position (`t.0`,
      `t.1`). For multiple return values
      (`fn divmod(a: int32, b: int32) -> tuple<int32, int32>`) and ad-hoc
      grouping without a one-off struct. Distinct from `slice<any>`: a tuple
      keeps each element's static type and a compile-time arity, where erasing
      every slot to `any` would collapse into a fixed-length `slice<any>`. Also
      the door to a statically-typed variadic later (no erasure), if wanted
- [ ] Literal adaptation to `slice<T>` — a literal in a slice-typed slot
      borrows from context, the compiler materializing the backing storage.
      (Known gap, family-wide: plain **assignment** to an existing slice
      variable — `s = "hi";` — is not an adaptation position even in the
      shipped string-literal form; the positions are argument / `let` /
      `return` / array element / `@static`):
  - [x] string literals — `"hi"` adapts to a `slice<char>`/`slice<const char>`
        expected by a `let` or a parameter (NUL dropped), borrowing the string
        constant's bytes; implemented, see [Slices](docs/language.md#slices)
  - [x] string-literal elements — the adaptation reaches nested/element
        positions: `let dirs: slice<char>[2] = ["bin", "usr/bin"];` (an owned
        array *of slices* whose elements are string literals) works with no
        per-element `as`, nested literals included, each element borrowing
        its **global string constant** (no backing-storage or lifetime
        question). The `@static` route emits constant `{pointer, length}`
        views, so a `@static` array of slices — and, as a bonus, the scalar
        `@static let g: slice<const char> = "hi";` — works too; implemented,
        see [Strings](docs/language.md#strings)
  - [x] ternaries of string literals — the adaptation reaches through a
        conditional expression whose arms are all string literals
        (`string_append(s, b ? "true" : "false")`, nested ternaries included):
        each arm borrows in its own branch, so the merged view carries the
        chosen literal's own length. An explicit `as slice<char>` borrow
        distributes over a ternary of owned arrays the same way. `@static`
        stays literal-only (a runtime branch has no constant view);
        implemented, see [Operators](docs/language.md#operators)
  - [ ] string-literal struct fields — the remaining position: a string
        literal in a struct-literal field whose type is a char slice
        (`struct cmd { name: slice<const char>; ... }` built from
        `{ name: "ls", ... }`). Blocked on evaluation order: `gen_struct_lit`
        evaluates every field expression *before* field types are resolved
        (discarding the AST node the adaptation gate needs), and generic
        struct literals infer their type arguments **from** those evaluated
        field types — the very evaluation the gate would have to precede. Needs
        a deferred-evaluation restructure of the struct-literal path first
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
  - [x] pointer decay into `const`/`mut` parameters — a `T*` argument in a
        `const T` or `mut T` slot implicitly dereferences, so the callee sees
        the pointee (read-only or writable) without the caller writing
        `*var`: with `fn append(mut self: mystruct, const rhs: mystruct)`, a
        heap `mystruct*` from `new<mystruct>()` and a stack value call it
        identically. Mechanically cheap: a `const`-struct or `mut` parameter
        already travels as a hidden reference, so decay forwards the pointer
        value instead of forming `&lvalue`, which is also why an **rvalue**
        `T*` may decay to `mut T` (the pointee is real storage even when the
        pointer itself is a temporary), deliberately unlike the plain rule
        that a `mut` argument must be an lvalue. Generic inference
        participates in decay: the example above is concrete, but every
        `libmc` container function is generic, and today a pointer argument
        at a generic `const`/`mut` slot fails inference outright
        (`cannot infer type parameter(s) T`), so at such a slot unification
        also tries the argument's **pointee** against the parameter
        pattern, exactly one level down (`list<int32>*` against
        `mut self: list<T>` binds `T = int32`); the one-level guardrail
        below falls out naturally there, since the pointee of a `T**` is
        itself a pointer. A decay is a **two-sided
        promise**, the point where the language's two marker kinds meet:
        the caller's side is a value-supplier promise in the `@nonnull`
        family (this pointer is non-null, so the reference formed from it
        is real), and the callee's side is the bare-keyword receiver
        contract (`const` will not write through it, `mut` writes through
        it and nothing escapes). Four guardrails keep it
        sound and explicit: (1) decay fires only into **hidden-reference**
        slots, meaning `const` struct parameters and `mut` parameters of
        any type, whose declaration announces the reference semantics; a
        `const` scalar parameter is a by-value copy with no hidden
        reference behind it, so there is no pointer to forward and decay
        does not apply, and a plain by-value `T` parameter still needs an
        explicit `*var`, keeping the copy visible. (2) Exactly one level:
        `T*` decays to `const`/`mut T`, a `T**` only to `const`/`mut T*`,
        never twice. (3) The pointer must
        be **proven non-null** (a `@nonnull` parameter, a flow-narrowed
        local, or a `p!` assertion, all via the shipped `@nonnull` machinery
        below): `const`/`mut` references are never null by construction,
        the invariant the `libmc` wave-1 adoption leans on, and decaying an
        unproven pointer would smuggle a hidden null dereference into the
        one parameter kind that promises there is none. An unproven heap
        `T*` takes the usual one-line guard or hatch; a non-null `T!`
        return from `new`, if the deferred first-class `T!` item happens,
        would prove it at the source. (4) Under the shipped
        [function overloading](docs/language.md#function-overloading), an
        exact pointer
        match always beats a decayed one, and the mechanism is **two-tier
        viability**, not a specificity tweak: decayed candidates enter
        resolution only when no candidate matches the pointer type
        directly, so `f(x: T*)` alongside `f(mut x: T)` stays unambiguous.
        A decayed argument is a borrowed reference, never a transfer of ownership:
        the planned constructor/destructor machinery never runs a
        destructor on it. The method-call sugar's receiver auto-deref
        (Methods / OOP below) becomes an instance of this rule, and it is
        the mechanism that lets the `libmc` container-self migration to
        `mut` receivers keep one call shape for stack containers and heap
        `T*`s alike, even before method syntax lands (the migration is the
        item nested below); implemented, see
        [pointer decay](docs/language.md#pointer-decay-into-constmut-parameters):
    - [ ] `libmc` receiver migration — flip the standard library's struct
          functions from raw pointer selves to receiver markers: read-only
          accessors become `const self` (the
          `get`/`at`/`peek`/`len`/`is_empty`/`eq` families), mutators become
          `mut self`
          (`init`/`from_*`/`destroy`/`reset`/`set`/`push`/`pop`/`append`/
          `remove`/`grow`), across `list`, `string` (a transparent alias of
          `list<char>`, so its `@inline` wrappers re-lend the same reference
          into the `list_*` slots), `dict`, `set`, `stack`, and `queue`,
          plus the companion struct pointers of the same APIs (`append`'s
          source, `eq`'s right-hand side, and `duplicate`'s `src` become
          `const`; `duplicate`'s `dst` and the `format_arg`/`format_args`
          accumulator in `std` become `mut`). The accessor families flip
          to `const self` here and **stay read-only**: the mutable element
          accessor the [`mut` returns](#functions-and-methods) item
          sketches must form its return from a `mut`/pointer parameter,
          and overloads differing only in markers are banned under
          concrete overloading, so one name cannot serve both; mutable
          access arrives as a separate refactor once `mut` returns land,
          leaning on a new name (`list_ref`-style), and is explicitly not
          part of this migration. Strictly depends on the
          pointer decay above: callers holding a heap `string*`/`list<T>*`
          keep one call shape only because the pointer decays into the new
          slots (an always-non-null `&s` keeps compiling unchanged; an
          unproven `T*` from `new` takes the usual one-line guard or `p!`
          hatch, the migration's only source-breaking surface), and the
          selves go non-null by construction, closing the loop the
          `@nonnull` wave-1 item deliberately left open. Excluded by
          design: the iterator protocol keeps its pointer signatures.
          `*_it` stores its receiver into the cursor it returns
          (`iterator { obj = self, idx = 0 }`), and a `const`/`mut`
          reference's defining contract is that its address cannot escape,
          so `*_it` over a receiver marker is inexpressible, not merely
          inconvenient; `*_next`'s receiver is the iterator, not the
          container, with its call emitted by the compiler under the
          `_it`/`_next` convention, so its `out: T*` half belongs to the
          [`for … in` protocol over `mut`](#functions-and-methods) item
          below and the `obj` pointer stored inside `iterator<T>` stays a
          pointer regardless. The destroy family migrates whole, verified:
          every `*_destroy` releases owned internals through `self`
          (`dealloc(self->data)`, `dict`'s keys and entry array) and never
          deallocates the receiver's own box, which remains the caller's
          separate `dealloc(p)` on a pointer the caller still holds, so
          nothing needs to stay pointer-taking or be split. A
          transitional both-signatures period is skipped by design: a
          forward declaration pairs with its definition rather than
          overloading it, and though the shipped
          [function overloading](docs/language.md#function-overloading)
          could express a dual-shape set, each stage flips its containers
          atomically instead of carrying legacy overloads. The migration
          doubles as the decay rule's acceptance test (the whole stdlib
          plus its tests and examples compiling over decayed call sites
          proves the rule covers real call patterns) and pre-positions
          [Methods / OOP](#functions-and-methods): the receiver kinds land
          as method sugar over already-correct `mut self`/`const self`
          signatures. Lands **staged**, in a forced order (each stage its
          own change set with its own CHANGELOG entry; this box ticks
          when the last stage lands):
      - [x] stage 1: pointer decay — the enabling rule ships on its own,
            the parent item above
      - [x] stage 2: `stack` + `queue` — every call site is `&x`-shaped,
            so decay proves them for free and no guards appear
      - [x] stage 3: `dict` + `set` — about thirteen heap-pointer test
            call sites take guards
      - [x] stage 4: `list` + `string` as one unit — the ten `@inline`
            `string` wrappers re-lend `self` into the `list_*` slots, and
            `&` of a `mut` parameter is banned, so `string` cannot flip
            before `list`
      - [ ] stage 5: `std` — the `format_arg`/`format_args` accumulator
            flips to `mut` together with the
            [native variadics](#functions-and-methods) stage-3 format
            work (its vehicle), since those functions break the moment
            `string` flips
  - [ ] `for … in` protocol over `mut` — `_next` still takes its element slot
        as a raw pointer (`fn list_next<T>(it: …, out: T*) -> bool`) because
        the compiler emits the `_next(&it, &slot)` call itself; teaching that
        protocol codegen to form a `mut` argument makes
        `fn list_next<T>(it: …, mut out: T) -> bool` the expected shape and
        removes the last stdlib out-pointer (the `get` family already
        migrated). The `_it`/`_next` signatures are a compiler-checked
        convention today; the settled direction is that they stop
        being one: `for x in c` desugars to overloadable protocol
        calls (sketched as `iterate(c)` obtaining the iteration state
        and `get_next(...)` advancing it), resolved through ordinary
        whole-program overload resolution rather than compiler-magic
        underscore names, making iteration the second member of the
        overload-protocol family beside formatting and a dependent of
        the open overload sets item below: the stdlib provides the
        baseline overloads (slices, `range`, `enumerate`), and a user
        type becomes for-in-iterable by adding its own overloads in
        its own module. Open, not settled: the spelling, whether
        `get_next` writes the element through a `mut` binding and
        returns `bool`, and how the desugar interacts with the
        existing builtin for-in lowerings (slices/`range`/`enumerate`
        must not regress, presumably by those lowerings migrating into
        the stdlib baseline overloads). The `mut` shape survives
        regardless, iterator state advanced through a `mut` parameter
        and the element delivered without an out-pointer, which is
        what makes the protocol shapes work; either way this stays a
        coordinated compiler + stdlib change
  - [ ] `mut` returns — a function that returns an lvalue:
        `fn string_ref(mut self: string, i: uint64) -> mut char` makes
        `string_ref(str, 0) = '/'` legal (as well as comparing it or copying it
        out with `let c = string_ref(str, 0)`). A call returning `mut T` is a
        new assignable expression category. To keep the reference from dangling
        without a lifetime system, a `mut` return may only be **formed from a
        `mut`/pointer parameter or a global — never from a local or a by-value
        parameter**; this conservative, checkable rule fits the `string_ref`
        case (the result derives from `self`) and preserves the non-escape
        guarantee. The example is deliberately a **new name** beside the
        `const self` accessors of the `libmc` receiver migration above:
        a `mut` return cannot form from a `const` receiver, and overloads
        differing only in markers are banned under concrete overloading,
        so the migrated `get`/`at` families stay read-only and mutable
        access ships as a separate `_ref`-style refactor once this lands.
        The groundwork has shipped: generic overloads mixing `mut`
        (above) already defer the lvalue/value decision past overload
        resolution — the exact decision point an assignable call expression
        needs — and a `-> mut T` stub in a `.mci` is pure return-type
        rendering on the shipped
        [bodyless prototypes](docs/language.md#bodyless-fn-prototypes)
  - [ ] motivating use case: method receivers — once methods / OOP (the item
        below) land, `const`/`mut`/by-value on `self` express
        read-only / mutating / consuming methods directly, replacing today's raw
        `self: <struct>*` receiver, and a `mut` return formed from `self` gives a
        memory-safe mutable accessor. See its receiver-kind note for the
        field-projection and vtable details
- [x] Open overload sets — lifted the rule that all overloads of a
      name live in one defining module: sets are open by default, with
      no opt-in marker — any module may add overloads to an existing
      name, and the set is the whole-program union at import merge, in
      any import order. The gate is the declare-time collision rules,
      now run cross-module for concretes too: same-pattern duplicates
      collide (citing the prior member's site in a note),
      alpha-renamed same-base templates collide, and overlapping
      [closed type groups](#types-and-generics) collide; cross-module
      ambiguities cite both declaration sites. Resolution semantics
      are unchanged (shape filter, specificity, concrete beats bounded
      generic beats unbounded), so adding an import can only add
      candidates or collide loudly, never silently rewire a call
      except by supplying a better-ranked candidate, which is the
      intended protocol behavior. Non-overloadable functions stay
      non-overloadable exactly as shipped: `main`, variadic (`...`)
      functions, and collecting (`args...`) functions. The two
      per-name liabilities were resolved as per-overload semantics: an
      `@private` overload is a candidate only inside its own module —
      foreign calls fall through to the members they can see, and its
      mangled symbol is salted with the file stem
      (`format(int32).util`, normalized across `.mc`/`.mci`) so it
      never collides with foreign members; `@deprecated` warns only
      when resolution picks the deprecated member. Symbol choice is
      judged per declaring file over the signatures it can *see*
      (whole-program minus foreign `@private` members), and a `.mci`
      stub is ABI-pinned: its members' symbols re-derive from the stub
      plus its own import closure (the driver records the per-module
      import graph for this), so a consumer extending a stub's set
      never re-mangles the compiled object's symbols, and two
      singleton stubs both claiming one plain symbol collide loudly —
      correct, as the two objects could never link. The shipped
      order-independent
      [template symbol bases](docs/language.md#template-symbols) are
      what make the union linkable and order-independent, so this
      item was only sound because they shipped. The driving use case
      is the formatting protocol: the stdlib format module, now
      shipped as `libmc/format.mc` (the baseline stage of
      [formatted `{}` print](#strings-and-formatting)), declares
      the baseline
      `format(mut str: string, value: X, const modifier: string)`
      overload family (closed signed/unsigned groups, concretes, a
      generic slice list-renderer, an unbounded `<typename>` fallback),
      and `println`'s `format_arg` dispatches into the set through a
      generic `with`/`case type` arm resolved per boxed tag at end of
      codegen over the whole-program overload set; with open sets, a
      programmer makes a type printable by writing one `format`
      overload for it in their own module (today pointer-shaped,
      `value: point*` boxed via `&p`, until the
      [struct boxing](#structs-arrays-and-data-layout) follow-up under
      `any` lands) — the whole chain now works cross-module, with the
      deliberate privacy consequence that a `@private` `format`
      overload is invisible to `println`'s dispatch (it resolves in
      the stdlib's module) while direct `format(...)` calls in the
      owning module see it. Implemented, see
      [Function overloading](docs/language.md#function-overloading).
      Iteration is the protocol family's second member: the
      `for … in` protocol sub-item above is slated to desugar into
      `iterate`/`get_next` overloads riding this same mechanism. The
      family is free-function overload sets rather than per-struct
      member rules deliberately: a protocol built as an overload set
      is joinable by any type (builtins, pointers, enums, slices,
      types from modules the programmer does not own), which a
      member-function or interface rule can never be (no one can add
      a method to `slice<T>` or to a foreign library's struct). The
      overload-set protocol family is the language's protocol story,
      and the Methods / OOP item below is checked against it rather
      than the reverse: methods must not become the privileged
      mechanism for protocols
  - [ ] `@override` — replace a same-pattern member of an open set.
        With open sets, replacing group-covered or generic-covered
        behavior already falls out of the shipped ranking with no
        annotation (a user's concrete
        `format(mut str: string, value: int32, const modifier: string)`
        outranks the stdlib's closed-group template), which scopes
        the annotation to the one remaining case: replacing a
        same-pattern member of the set, e.g. the stdlib's concrete
        bool formatter, or its unbounded `<typename>` fallback
        replaced by the user's own unbounded template. Marking an
        overload `@override` suppresses the declare-time
        duplicate-pattern collision; the annotated definition
        replaces the unannotated one regardless of import order, and
        the replaced body is never emitted. Defaults settled: an
        `@override` whose pattern matches no existing overload is a
        declare-time error (typo protection, the C++
        override-specifier rationale), and two `@override`s of one
        pattern collide like any duplicate. Taxonomy: an `@X`
        value-supplier promise, "this definition replaces an existing
        one", the `@deprecated`/`@removed` family. The driving use
        case is the same protocol story: a programmer setting their
        own formatters for stdlib-covered types
- [ ] Methods / OOP — `fn <struct>::<method>(self: <struct>*, ...)` definitions
      keyed to a struct, including `@private` methods and the special
      constructor/destructor below (the `for … in` protocol already dispatches
      by struct name to pave the way, though iteration itself is slated to
      move to the overload-set protocol family of the open overload sets item
      above). On a plain `struct`, every method call
      is a direct, statically-bound call; dynamic dispatch exists only behind
      the explicit opt-in of the polymorphic lane below, so code that does not
      opt in pays nothing. A settled scope boundary from the open overload
      sets item above: protocols (formatting, iteration) are free-function
      overload sets, not methods; methods must not become the privileged
      mechanism for protocols, and this item is checked against that family
      rather than the reverse:
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
        (2) it must reconcile with both vtable ABIs below (the polymorphic
        struct's embedded vtable and the interface fat pointer): a
        `mut`-using function is normally not expressible as a plain
        `fn(...)` value, but in either vtable the receiver is already behind
        a pointer (the object pointer itself in a polymorphic call, `data*`
        in the fat pointer), so the vtable slot's first param is a genuine
        `T*` under an ABI the compiler controls internally. A `mut` return formed from `self` is then
        the natural spelling for a mutable accessor method
  - [ ] constructor — `fn <struct>::constructor(self: <struct>*, ...)`, the
        method that initializes a value: run by the `new <struct>(...)` sugar
        below, or invoked on a stack value. Constructing a stack-allocated
        struct implicitly `defer`s its destructor to the end of the enclosing
        scope, so a stack value cleans up after itself — RAII over the
        existing [`defer`](docs/language.md#defer) machinery. Naming is still
        open: `constructor`/`destructor` or `init`/`destroy` (both pairs on
        the table for now; examples use the former). A struct wanting several
        initialization signatures (empty / copy / from raw parts) declares
        overloaded constructors, riding the shipped
        [function overloading](docs/language.md#function-overloading)
  - [ ] destructor — `fn <struct>::destructor(self: <struct>*)`, the cleanup
        counterpart: releases what the constructor acquired. Deferred
        automatically for a stack-constructed value (above); for a heap
        `new`, run explicitly before the memory is freed. On a polymorphic
        struct (the item below) the destructor occupies a reserved vtable
        slot unconditionally, so destroying through a base pointer always
        runs the dynamic type's destructor, never the static type's (the
        C++ forgot-to-mark-it-`virtual` bug is not expressible), while the
        implicitly-deferred destructor of a stack value devirtualizes (a
        constructed value's dynamic type is exact)
  - [ ] method-call sugar — `var->method(...)` desugars to
        `point::method(var, ...)`, passing the receiver as `self` (so `var` is a
        `struct point*`). That `->` form is the pre-receiver-kinds starting
        point; once the receiver kinds above land, calls are uniformly
        `var.method()` — the method's declared `self` kind dictates the receiver
        convention (`const`/`mut self` forms a hidden reference from the
        receiver's storage, by-value `self` copies), and a `point*` receiver
        (e.g. from `new`) auto-derefs one level first, an instance of the
        pointer-decay rule above that inherits its proven-non-null
        requirement. No ambiguity, since
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
  - [ ] non-struct receivers — extend the `fn <type>::<method>` definition
        form beyond structs to the builtin scalar types, so
        `fn char::tolower(self: char) -> char` makes `'C'.tolower()` and
        `c.tolower()` work (mcc-native ergonomics over the shipped
        [libc ctype](docs/language.md#reaching-libc) bindings is the
        motivating stdlib case). Dispatch keys on the receiver's static type
        exactly as struct methods key on the struct, and import merging
        rejects two files defining the same type/method pair as a duplicate
        definition. The parser delta is accepting a type keyword (not just
        an identifier) before `::` in the definition position; the
        expression grammar is untouched (`Name::MEMBER` stays enum member
        access). Depends on the receiver kinds above even more than the
        struct form does: a scalar receiver is by-value `self: char` or
        `const self: char`, because the `self: <struct>*` starting form has
        no lvalue behind a literal receiver, and for the same reason a
        `mut self` method needs the caller's own writable `char` and is
        never callable on `'C'` (a value-returning `tolower` takes `self`
        by value; a `mut self` variant is the in-place editor). A
        transparent `type` alias shares the underlying type's methods
        rather than opening a separate namespace; enum receivers wait for
        [nominal enums](#types-and-generics), where a method name must not
        collide with a member name (both spell `Name::x`)
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
  - [ ] polymorphic structs — the opt-in dynamic-dispatch lane: a type
        declared with a distinct declaration form (`class`, name tentative)
        carries a hidden vtable pointer at offset 0 and dispatches every
        method call through it, so the dynamic type's override always wins
        (Java/Python method semantics), adopted so that C++'s object
        slicing is unrepresentable rather than surprising. The canonical
        acceptance test:
    ```c
    class A { ... }            fn A::f(const self: A) { println("A"); }
    class B extends A { ... }  override fn B::f(const self: B) { println("B"); }
    class C extends B { ... }  override fn C::f(const self: C) { println("C"); }
    fn func(const a: A) { a.f(); }   // the receiver travels by reference
    let c = C();  func(c);           // prints "C"; must never print "A"
    ```
    This program prints `C` or is rejected; no legal program observes `A`
    here. The opt-in is a type-kind keyword like `struct`/`union`/`enum`,
    declared and never inferred: defining methods on a plain `struct`
    changes nothing (those calls stay direct), a per-method `virtual` is
    ruled out because methods are defined apart from the struct, possibly
    in another file, and a distant declaration must not change a type's
    layout (which reads off the declaration in the single-pass compiler),
    and an `@`-marker is ruled out by the annotation taxonomy (`@X` is a
    value-supplier promise, where polymorphism is a different kind of
    type: mandatory reference semantics and different conversion rules,
    a type-kind fact rather than a per-value promise). Layout:
    the vtable pointer at offset 0, then the base's fields as a prefix
    (the shipped `extends` prefix rule shifted one slot down); the derived
    vtable is prefix-compatible with the base's (inherited methods keep
    their slot, an override replaces the entry, new methods append), so
    the polymorphic pointer upcast `C*` to `A*` stays zero-cost; single
    inheritance only, which `extends` already is, with multiple bases
    rejected by design for the same offset-0 uniqueness plus the
    vtable's own version of it: a second base means this-adjusting
    thunks in every dispatch, upcasts that adjust instead of bitcast,
    and diamond bases forcing duplicated sub-objects or C++-style
    virtual inheritance (the pile-up every post-C++ language declined).
    The Java-shaped split: state has one layout chain (one `extends`
    base, additional state as named fields), contracts multiply freely
    (a class implements any number of the interfaces below). No
    slicing, by
    construction: a polymorphic type has reference semantics, so every
    implicit whole-value copy is rejected (by-value parameters, returns,
    fields, `let`/assignment from another value, and in particular the
    derived-to-base **value** upcast that plain structs keep), including
    the `const`/`mut` reference forms' rvalue copy-on-read (a `const A`
    may refer to a `C`, so that copy would itself be a slice) and a
    generic instantiation that copies a `T` bound to a polymorphic type
    (rejected per instantiation, traced by the shipped
    [instantiation backtraces](#tooling-and-c-interop)). Polymorphic use
    is `T*`, `const T`, or `mut T`; values are constructed in place, on
    the stack (exact dynamic type, so those calls devirtualize) or on the
    heap via `new`; an explicit user-written clone method is the copy
    escape. Every construction path (constructor, literal, zero-init)
    writes the vtable pointer before user code sees the value. Overriding
    is marked: an override must match the base method's signature exactly
    (covariance out of scope) and carry an explicit `override` marker
    (spelling and position open; the example shows a prefix), with both
    typo directions caught: the same name and signature without the
    marker is an error (no silent shadowing), and a marker with no base
    method to override is an error. Overload selection stays static: the
    argument list picks the signature by static types (riding the shipped
    [function overloading](docs/language.md#function-overloading)) and only the
    receiver's dynamic type picks the body, the Java rule. C
    compatibility does not enter this design at all: `extends` is not a
    C construct, so a straight-ported C program cannot contain it and no
    port ever meets these rules; ABI compatibility constrains only the
    C-shaped surface (plain structs, `@extern`, C headers), and a
    feature C does not have owes C nothing. What remains are natural
    boundaries, not costs paid: the constructs that describe C-shaped
    memory simply do not admit a vtable-carrying object, so a
    polymorphic type does not cross an `@extern` boundary, take
    `@packed`, join a union, or appear in
    [C header generation](#tooling-and-c-interop) output. The lane wall
    (a plain struct cannot extend a polymorphic type, nor a polymorphic
    type a plain struct) stands on semantics alone: in either direction
    a copyable plain type would reach into a reference-semantics object,
    since a plain struct embedding a polymorphic prefix puts a
    vtable-carrying value in the freely-copying lane, and a polymorphic
    type over a plain base would let any `base*` into the object copy
    the base prefix out by value, rebuilding data slicing through the
    plain lane's own legal copies; mechanically the vtable slot and the
    plain field prefix also fight for offset 0; and the intrusive
    `struct entry<T> extends T` depends on vtable-free prefix embedding,
    so its instantiation over a polymorphic payload is rejected per
    instantiation exactly as a non-struct `T` is today. Everything the
    struct lane ships stays intact: layout, prefix embedding, zero-cost
    value upcasts, unions, and value semantics throughout. Depends on
    the receiver kinds above
    (`const`/`mut self` are how a polymorphic receiver travels) and on
    the parent item's method machinery; the destructor's reserved vtable
    slot is noted on the destructor above, and the shared-vtable
    reconciliation on the interfaces item below
  - [ ] interfaces — a named set of method signatures
        (`interface writer { fn write(self, buf: slice<const uint8>) -> int64; }`)
        that a struct satisfies by defining those methods, carried as a
        `{ data*, vtable* }` fat pointer for runtime polymorphism
        (heterogeneous lists, plugin-style APIs) — the dynamic counterpart to
        the static [generic bounds](#types-and-generics). Depends on methods
        (above). Open question: dynamic dispatch can only carry **reference**
        receivers (`const`/`mut self`) — the receiver travels as `data*`, so a
        by-value (consuming) `self` cannot cross the vtable without a copy;
        whether interfaces admit by-value receivers at all is undecided.
        Deliberately a separate item from the polymorphic structs above: an
        interface is a cross-hierarchy contract (any struct, plain or
        polymorphic, can implement one, and any number of them: with
        multiple inheritance rejected by design, this is how a type
        presents as several things, the fat pointer's `data*` making
        interior sub-object offsets a non-issue) where the polymorphic
        lane is
        within-hierarchy, and implementing an interface leaves the
        struct itself untouched: the vtable lives in the fat pointer,
        not the object, so there is no hidden field, no layout change,
        and value semantics are kept. The two share one vtable
        machinery, whichever ships first
        building it: the slot ABI (signature-to-slot mapping, receiver
        behind a pointer per the receiver-kind note above) and the
        table-emission path. And they compose soundly: when a polymorphic
        struct is boxed into an interface, the fat pointer's entries
        dispatch through the object's embedded vtable, so the dynamic
        type's override wins across an interface call too (a `C*` upcast
        to `A*` and boxed as a `writer` still calls `C::write`, never
        `A::write`)
    - [ ] `case type` over interfaces — once interfaces land,
          `case type (x)` admits an interface arm (`when writer w:`), a
          set-membership test over the same compile-time FNV-1a-64 type
          tag that shipped `case type` arms already lower to
          integer-equality chains on: whole-program compilation
          statically knows every type implementing the interface and
          every type that boxes into `any`, so the arm lowers to a
          small multi-tag equality chain, each hit forming the fat
          pointer from a per-tag vtable constant plus the payload, no
          runtime registry. What matches: structs cannot box into `any`
          (the shipped escape-hatch rejection), so the base match is a
          boxed `T*` whose pointee type implements the interface, the
          pointer supplying `data*` directly; and the complementary
          direction is first-class, since a fat pointer is two words
          and the `any` payload is exactly `[2 x uint64]`, so an
          interface value itself boxes into `any` and matches its own
          interface arm, which is what makes a heterogeneous
          `slice<const any>` of interface values work and is the
          natural endpoint for the stdlib's `format_arg` dispatch (a
          `when printable p:` arm replacing one arm per concrete type;
          `format_arg` lives in the
          [native variadics](#functions-and-methods) item's stage 3).
          Arm ordering becomes semantic: today every arm is a distinct
          concrete type so order cannot matter, but an interface arm
          overlaps concrete arms, so the spec is first-match-wins in
          textual order (an unreachable-later-arm diagnostic is worth
          considering), and the mandatory `else` is unchanged, since
          interface arms widen individual arms without closing the open
          `any` universe. The nesting is the dependency: this hangs off
          interfaces, whose own chain (methods above, over the shipped
          concrete overloading) is unblocked from the bottom. Open
          question, recorded not solved: the tag-to-vtable table is a
          whole-program artifact, so the `.mci` story needs the same
          care the shipped
          [function overloading](docs/language.md#function-overloading)
          gave the plain-vs-mangled symbol choice
- [x] `@nonnull` parameters — a checked "definitely non-null" refinement over
      C's nullable-by-default `T*`, opt-in per parameter: the callee is
      statically guaranteed a non-null argument and skips the re-check, and the
      guarantee travels transitively (a plain-`T*` caller must check before
      passing to a `@nonnull` callee, but a `@nonnull` callee passing its own
      parameter onward needs no check). This is a *checked* type refinement, not
      an unchecked optimizer hint: passing a plain `T*` to a `@nonnull` slot
      without proof is a compile error. Attribute-only at runtime, sharing
      `T*`'s representation and reusing the shipped
      [`@noalias`](docs/language.md#noalias-parameters) machinery (LLVM
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
  - [x] escape hatch — crossing from a heap or returned `T*` into a `@nonnull`
        slot needs an explicit programmer assertion: postfix `p!`, a purely
        static, zero-runtime-cost claim (no check is ever emitted; asserting
        an actually-null pointer is undefined behavior). The assertion covers
        only the expression it wraps, though since loop-body fact preservation
        below shipped, a `let q = p!;` binding seeds a narrowed fact for `q`.
        `null!` and a non-pointer operand are compile errors; `p!` is
        the identity anywhere else. `!=` lexes greedily, so `p != q` stays a
        comparison and `(p!) == q` needs the parentheses; implemented, see
        [@nonnull parameters](docs/language.md#nonnull-parameters)
  - [x] flow-narrowing — narrows a plain `T*` local to non-null from a null
        check, so idiomatic code needs no escape hatch: `if (p != null)`
        narrows the then branch, `if (p == null) {A} else {B}` narrows `B`,
        and the C-idiomatic else-less guard `if (p == null)` whose body
        always diverges narrows the remainder of the enclosing scope.
        Divergence is read off the builder's terminated-block state rather
        than a `return`/`break`/`continue` scan, so nested all-diverging
        `if`s already count, and the since-shipped
        [`@noreturn`/`unreachable`](#functions-and-methods) (letting
        `if (p == null) abort();` guard) was absorbed with zero narrowing
        changes, as designed. Sound and conservative: bare local pointer variables
        narrow (globals and index expressions never do; `mut` parameters
        carry no per-name fact; member projections gained their own
        path-keyed facts in the follow-on below), taking `&p` anywhere in
        the function bans narrowing of `p`, facts die on reassignment, on
        passing as a `mut` argument, and
        on a shadowing `let`, and every fact drops at loop entry (see the
        follow-on below). This opened the gate to adopting `@nonnull` across
        `libmc` (the adoption items below; wave 1 shipped); implemented, see
        [@nonnull parameters](docs/language.md#nonnull-parameters):
    - [x] `libmc` adoption, wave 1 — `@nonnull` on the standard library's
          data/source/key/destination pointer parameters: the `memory`
          copy/fill family (`bytecopy`, `copy`, `bytezero`, `zero`,
          `bytefill`, `fill`, and the four `@deprecated` forwarders), the
          `hashing/` digests (`md5` data and digest, `crc32` data, `murmur3`
          key), `dict`'s string keys (`dict_set`, `dict_get`, `dict_remove`,
          and the `@private` `str_eq`/`str_clone` helpers), and the raw-array
          sources of `list_from_array`/`string_from_array`, so an unproven
          pointer at those call sites is a compile error instead of a latent
          null dereference (a heap or returned `T*` takes a one-line null
          guard, preserved across loops that cannot invalidate it since
          loop-body fact preservation below shipped). Container `self` parameters
          are deliberately not annotated: every container self is slated to
          become a `mut`/`const` receiver in the
          [receiver-kind migration](#functions-and-methods), and `@nonnull`
          is rejected on `mut` (a `mut` parameter is passed by reference and
          is never null), so annotating selves now would be throwaway work;
          they pick up non-null by construction when that migration lands,
          and the iteration-protocol functions (`*_it`/`*_next`) ride the
          same receiver rework via the
          [`for … in` protocol over `mut`](#functions-and-methods) item.
          Null-meaningful parameters stay plain `T*`: `resize` (null
          allocates fresh) and `dealloc` (null is a no-op), and `set`'s
          generic `key: K` is untouched (it is a non-pointer per
          instantiation, so the `hash<T>`/`fnv1a`/`splitmix64` chain stays
          unannotated); implemented, see
          [@nonnull parameters](docs/language.md#nonnull-parameters)
    - [ ] `libc/` bindings, wave 2 — annotate the `@extern` libc surface
          (attribute-only there, like `@noalias` on the `restrict` family:
          the C side is never checked, only callers are), a separate change
          set from wave 1 above, roughly fifty parameters across four
          modules. `libc/string.mc`: 36 parameters across the `str*`/`mem*`
          externs, excluding `strtok`'s `str` (null continues a
          tokenization) and `strxfrm`'s `dest` (null is allowed when
          `count` is 0). `libc/stdlib.mc`: the `str` of
          `atoi`/`atol`/`atoll`/`atof` and the `strto*` family (9),
          excluding all five `endptr` parameters (documented "if
          non-null"), `free`/`realloc`'s `ptr` (null is meaningful there),
          and `system`'s `command` (`system(null)` probes shell
          availability); `qsort`/`bsearch`'s function-pointer parameters
          are skipped. `libc/math.mc`: `frexp`'s `exp`, `modf`'s `iptr`,
          `remquo`'s `quo`, and `nan`'s `tagp` (4). `libc/time.mc`: the
          pointer parameters of
          `mktime`/`asctime`/`strftime`/`localtime`/`gmtime`/`ctime`,
          excluding `time`'s `timer` (null is documented OK).
          `libc/stdio.mc` is deferred indefinitely, not part of this wave:
          it has real null-meaningful carve-outs (`freopen`'s `filename`,
          `setbuf`'s `buf`), and annotating `fwrite`'s `ptr` would ask
          `std.mc`'s `writestr` for a proof. The shipped projection facts
          (above) have since dissolved the guard-then-single-call shape
          (`if (str.data == null) return; fwrite(str.data, ...)` now
          proves), but a `str.data` used across calls or inside a loop
          still needs a `let`-seeded binding or a `str.data!` hatch, so
          stdio remains the highest downstream friction for the lowest
          value. The annotations ship unconditionally, in the
          source and in `.mci` stubs alike: no `-D`/`@if` gate (the
          declared contract never varies per build; a gate would also
          have to duplicate every declaration, since `@if` is
          declaration-granular). Enforcement on externs is opt-in: by
          default the only teeth are the unconditional literal-`null`
          error, and the proof obligation rides the
          [`-Wextern-nonnull`](#metaprogramming-and-builtins) class, so
          ported C code never hits a null-proof wall and no ordering
          between this wave and that class is forced; this repo opts in
          by enabling the class under its existing `-Werror` CI, which
          is what makes the wave enforceable at home
    - [x] loop-body fact preservation — replaced the shipped blanket rule
          (all narrowed facts drop at loop entry) with a pre-scan of the
          whole loop (condition and body, nested statements, `defer`
          bodies, and both `@if` branches) that kills only the facts the
          loop could invalidate (an assignment, a shadowing `let`, or a
          bare-name `mut` lend, resolved by callee name across all
          overloads, conservatively), preserving the guard-then-loop idiom
          (`if (p == null) return; while (...) { use(p); }`) that the
          shipped `libmc` adoption above leans on; surviving facts also
          hold past the loop's exit. Folded in the remaining proof-plumbing
          follow-ons: `and`/`or` threading (`if (p != null and q != null)`
          proves both in the then branch, a diverging
          `if (p == null or q == null)` proves both after, and a
          short-circuit rhs sees the lhs's fact), `while (p != null)` /
          `until (p == null)` header narrowing (re-proven per back edge, so
          body invalidations are fine) plus the exit-edge fact after a
          `while (p == null)`-style loop (disabled when the body can
          `break`, which skips the re-test), fact-seeding through
          `let q = p;` (any provably non-null initializer, `p!` included,
          under the usual eligibility rules), and proof threading through
          `as` casts whose *resolved* target is a pointer type (alias
          targets count; a non-pointer intermediate severs the proof — so
          `md5("abc" as uint8*, n)` now proves like `md5("abc", n)`);
          implemented, see
          [@nonnull parameters](docs/language.md#nonnull-parameters)
    - [x] projection facts — the same guard shapes narrow a pointer-typed
          *field projection* (`if (b->data != null)`, the diverging
          `== null` guard, loop headers and exit conditions, `and`/`or`
          chains threading projections and names together), keyed by
          access path at any depth, arrow-insensitively (`(*b).data` is
          `b->data`); a proven projection crosses `@nonnull` slots on the
          direct and generic call paths alike (both check-and-load
          arguments left to right, so `f(b->data, g())` proves while
          `f(g(), b->data)` is rejected), decays into `const`/`mut`, and
          seeds a name fact through `let q = b->data;` (the idiom for
          carrying a checked field across calls and loops, with
          `b->data!` as the hatch). Bases must be locals — `mut` and
          `@nonnull` parameter bases included (the receiver-migration
          consumer), globals and array elements excluded — and a
          `@volatile` owner anywhere along the path (`extends`-inherited
          too) never forms a fact. Soundness is a kill model instead of
          formation bans: every call emission and every through-memory
          store (deref/element/field, compound forms included, so union
          siblings and `&b->data` aliases are covered wholesale) drops
          all path facts, loop entry drops them wholesale (no pre-scan
          parity yet), reassigning/shadowing/`mut`-lending the base
          prefix-kills its paths, and a guard whose later operand can
          call (`b->data != null and check()`) forms no path fact at all.
          Recorded follow-ups: a loop pre-scan for paths (mirroring
          loop-body fact preservation above), element paths
          (`a->xs[0]`), global bases, and the blanket call kill's
          refinement (the item below); implemented, see
          [@nonnull parameters](docs/language.md#nonnull-parameters)
    - [x] call write-effect analysis — refine projection facts' blanket
          rule above (every call kills all path facts) with a
          per-function, transitive **write-effect bit**: a call to a
          bit-clear callee preserves path facts, everything else keeps
          the shipped kill. A function's bit is set if it performs any
          through-memory store (the `StoreDeref`/`StoreIndex`/
          `StoreMember` statement forms or their compound-assignment
          arms — the strict rule shipped in v1: a store to the
          function's own local struct counts too), assigns a `mut`
          parameter (a store through the hidden reference into the
          caller's storage) or a global, calls anything opaque
          (`@extern`, `@asm`, a call through a function-pointer value,
          the `va_*` intrinsics, a bodyless prototype, or a
          protocol/slice `for` loop, whose `_it`/`_next` callees a
          syntactic pass cannot name — the builtin `range`/`enumerate`
          counting loops emit no call and are exempt), or calls a
          function whose bit is set; computed bottom-up over the whole
          program's call graph (whole-program compilation makes this
          closed-world, the same property the projection kills already
          lean on), recursion cycles resolving by an optimistic-clear
          least fixpoint (a write-free cycle stays clear; any base
          condition taints its whole cycle), call edges unioning every
          same-name candidate (templates, overloads, concrete and
          `@static` declarations), and generic functions taking their
          bit per-template with candidate-union in v1, a per-instance
          refinement if measurement demands it. At an emission site
          where resolution already picked the winner (direct, generic,
          protocol `_next`), the winner's own bit is consulted.
          Parameter signatures can never be the trigger, recorded here
          so `const`-argument-based skipping is not re-proposed:
          (1) a path fact lives in the struct's storage (`a->ptr`
          inside `*a`), not in the argument, so a callee never
          receiving `a` proves nothing (`*a` may be reachable from a
          global regardless of the parameter list); (2) self-aliasing:
          `a->ptr` may point into `*a` itself, so a write through any
          received pointer can be a write to the fact's storage; and
          (3) `const` launders through opaque callees: a
          `const buf: T*` parameter's value can cross into `@extern`
          varargs with no const contract (`println` wrapping `printf`
          is the canonical case), so const-ness of the signature says
          nothing about the transitive body. The concrete consequence:
          `fn f1<T>(@nonnull const buf: T*, n: uint64)` whose body
          calls `println` still kills path facts under the refinement
          (`println` wraps `@extern printf`), while a pure leaf like a
          math helper stops killing. Name facts are unaffected either
          way (they already survive calls), and `let q = a->ptr;`
          remains the practical idiom for carrying a checked field
          across writing calls and loops; implemented, see
          [@nonnull parameters](docs/language.md#nonnull-parameters).
          Recorded follow-ups: (1) the **two-effect refinement** —
          split the strict bit so only caller-reachable writes count
          (`mut`-parameter, global, and through-pointer stores), while
          writes to a function's own locals stay clear. Pre-measured
          delta: under the strict rule 20/106 libmc functions come out
          clear but only ~6% of libmc-internal call sites preserve
          (vs ~39% of functions and ~19% of sites in example code);
          the two-effect rule lifts that to ~27% usable functions and
          ~15% of libmc sites, unlocking the hashing chain and the
          getters. (2) a `.mci` effect-bit — a supplier-promise
          annotation (`@pure`-shaped, per the annotation taxonomy) so
          an imported stub's prototypes can carry a clear bit instead
          of reading as bodyless/opaque
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
      later path. The foundations this once waited on are shipped:
      [`any`](docs/language.md#the-any-type) landed with its tagged 24-byte
      box, compile-time FNV-1a-64 type-ids (collision-checked, so no
      `@typeof` is needed), implicit boxing at
      assignment/argument/return/store (structs and arrays reject with an
      escape-hatch message), and `case type` with its mandatory `else`. The
      callee side described above works end to end today, verified by
      running programs (a `slice<const any>` parameter, `for a in args`,
      indexing, `case type` dispatch), and the whole model is already
      expressible with a manual `any[N]`, element stores boxing, and a
      borrow at the call site; the remaining scope is caller-side
      collection, the `args...` sugar, and the stdlib flip. Settled v1
      rules, all type-shaped: the trailing `slice<const any>` parameter
      type itself marks a collecting function, `args...` being pure sugar
      for it, which makes `.mci` support free (the interface renderer
      already emits the desugared parameter and the type is the marker on
      re-import; function-pointer types carry no marker, so calls through
      `fn(...)` values stay explicit-slice, documented). The pass-through
      rule keeps the change purely additive: when the argument count
      equals the parameter count and the final argument is already exactly
      `slice<const any>` (or `slice<any>`, which coerces), it passes
      through uncollected, and every possible call to such a function
      today has exactly that shape; anything else at that position
      collects (a single `any` becomes a one-element slice, a
      `slice<int32>` boxes as one slice element), and zero extras
      synthesize an empty `{ null, 0 }` slice. A collecting function is
      non-overloadable in v1 and cannot share a generic name, extending
      the shipped variadic-cannot-overload rule (a collecting candidate
      would make arity-based viability ambiguous against the last-position
      rule); the pre-evaluate path gets an explicit diagnostic, not a
      confusing arity error. Boxes are entry allocas with function
      lifetime, so `defer` bodies and loops are safe; the
      callee-must-not-retain caveat is the same one every slice borrow
      documents. The two real costs: collection parity across both
      marshaling paths (`marshal_args` and the generic pre-evaluate path),
      and the `{}`-grammar migration of every existing print caller. Lands
      **staged** (the receiver-migration pattern: each stage is its own
      complete change set with its own CHANGELOG entry; this box ticks
      when the last stage lands):
  - [x] stage 1: trailing collection and the `args...` sugar — parser sugar
        (`IDENT...` desugars to `slice<const any>`), collection in
        `marshal_args` mirroring the literal-adaptation borrow (entry
        `[N x any]` alloca, box each extra, form the slice; zero extras
        synthesize the empty slice), the pass-through rule, the v1
        overload/generic ban with its explicit diagnostic, and
        `check_boxable`'s struct/array rejections firing at the
        collection site; implemented, see
        [Native variadic arguments](docs/language.md#native-variadic-arguments)
  - [ ] stage 2: generic and overload-set parity — collection through the
        pre-evaluate path (its arity filter and viability arity error
        exclude collecting candidates today), mirroring the
        literal-adaptation parity lesson from
        [function overloading](docs/language.md#function-overloading)'s
        first stage, and lifting the stage-1 ban
  - [ ] stage 3: the stdlib flip — fix the five recorded bugs in the
        dormant `format_arg` WIP (the `char*` arm appends the
        uninitialized `buf` instead of `s`, with the fix needing a null
        guard, since a boxed `char*` can hold `null` while
        `string_append`'s `char*` overload is `@nonnull`; the
        unconditional trailing `buf` append; the unused `snprintf` length
        `l`, fixed by the bounded `(char*, n)` append overload; `%llf` to
        `%f`; and the silent excess-placeholder and trailing-brace edges
        get spec'd), flip `print`/`println` to the slice signatures, and
        migrate every printf-grammar caller to `{}` placeholders in the
        same change set (279 `println` sites across 69 example files plus
        test assertions; mechanical, but it cannot trail the flip, since
        a printf string through the `{}` formatter prints its specifiers
        literally). Two decisions defer here: whether `NATIVE_VARGS`
        survives as a one-release migration toggle or the fact and its
        `@else` branches delete outright at the flip (the audit leans
        delete: a half-flipped ecosystem means two format grammars in
        every doc), and the final `{}` grammar spec (`{x}`/`{X}`
        modifiers already work in the WIP). Also the vehicle for the
        [libmc receiver migration](#functions-and-methods)'s final `std`
        stage; the downstream
        [formatted `{}` print](#strings-and-formatting) and
        [string interpolation](#strings-and-formatting) items key off
        this stage, not stage 1
- [ ] C variadics — the C-ABI `...`/`va_list` machinery, beyond forwarding:
  - [x] variadic declarations and `va_list` forwarding — implemented, see
        [Variadic functions](docs/language.md#variadic-functions)
  - [ ] `va_arg` interop — read individual arguments from a C-ABI `va_list`
        in mcc (today a `va_list` can only be forwarded to a C `v*` function)
- [x] `@noreturn` and `unreachable` — `@noreturn` marks a void function that
      never returns (`exit`, `abort`, an infinite loop): a direct call
      terminates the caller's block, so no dummy return is needed after it,
      the backend drops the dead path (LLVM's `noreturn` attribute), the
      `if (p == null) abort();` guard flow-narrows, defers deliberately do
      not run at the call (matching C's `exit`), fall-off-the-end is UB
      (C11 `_Noreturn`, so `while (true) {}` bodies are legal), and the
      flag rides `@extern`/`@asm`/generics/prototypes and `.mci` stubs
      (mismatches are conflict errors) but is dropped by `&f` function
      values (keeping `abort` usable as an `atexit` handler); `unreachable`
      is a reserved-word statement asserting a path is never reached
      (lowering to LLVM `unreachable`, UB if executed), the exhaustiveness
      bridge for an [exhaustive `case`](#types-and-generics)'s `else` arm or
      an impossible branch; implemented, see
      [@noreturn functions](docs/language.md#noreturn-functions) and
      [The unreachable statement](docs/language.md#the-unreachable-statement):
  - [ ] stdlib `panic(msg)` — a `@noreturn` "print to stderr and abort"
        entry point in `std`, the idiomatic guard body once formatted
        printing settles
  - [x] `-Wdead-code` — an opt-in class (via the shipped warning registry)
        reporting statements silently dropped after a `return`, a
        `@noreturn` call, or an `unreachable` (also `break`/`continue`/
        `emit` and all-paths-diverging statements; one warning per dead
        region, naming the killing construct type-free so generic
        re-emissions dedup); defers dropped because another *defer*
        diverged are deliberately out of scope — a different diagnostic;
        implemented, see [-Wdead-code](docs/language.md#-wdead-code)
  - [ ] constant-condition loop folding — recognize `while (true)`-style
        loops during generation so the never-taken exit edge (and its empty
        `end` block before a `@noreturn` body's auto-`unreachable`) never
        gets emitted, as optimizer cleanliness — and, with the exit edge
        gone, extend `-Wdead-code`'s reach to the code after such a loop

### Metaprogramming and builtins

- [ ] Compile-time macros:
  - [ ] macro functions — `@macro <name>(<args>) { ... }`, compile-time
        expansion (`@inline` already covers the call-overhead case)
  - [ ] `@define <name> = <value>` — a named compile-time substitution
- [ ] Bit-twiddling builtins — `byte_swap<T>` (`llvm.bswap`) and
      `bit_reverse<T>` (`llvm.bitreverse`) over the integer types
- [x] `typename` builtin — recover the canonical name of a type as a `const`
      string: mirrors `sizeof` in taking a type or an expression
      (`typename(int64)`, `typename(x)`) and folds at compile time to a
      deduplicated rodata string literal, typed like any other string
      literal, zero runtime machinery. Value-level by design: the result
      flows into a variable if needed (`let n = typename(T);`), a parameter,
      a struct, a `println`, which is what distinguishes it from the planned
      [`@typeof`](#types-and-generics) (compile-time-only, yielding a type
      usable in type position, its `@` prefix placing it with the
      `@if`/`@else` compile-time family, and not part of this item) and why
      the bare name reads next to `sizeof` instead of implying C's
      `typeof`. The canonical
      spelling already exists in the compiler: `str(LangType)` drives the
      [`any`](#structs-arrays-and-data-layout) tags (`any_tag` hashes
      exactly this string), the signature mangles, and diagnostics, so
      `typename` surfaces that string unchanged, deterministic across
      compilations (the property the tag hashing relies on) and precisely
      the preimage of the value's `any` tag. Monomorphization gives
      per-instantiation resolution for free: in `fn f<T>(...)`,
      `typename(T)` resolves to its own literal per instantiation. The
      powerful combination is with the
      [generic arms in `case type`](#structs-arrays-and-data-layout):
      inside `when T v:` the arm is a real generic context, so
      `typename(T)` names the dynamic type of the boxed `any` per tag,
      statically, covering most of what the `any` item's descriptor-pointer
      upgrade path was reserved for without descriptors (the one remaining
      descriptor-only case is naming a type in a bare `else` arm, where
      only the erased `any` exists). Semantics pinned: `typename(expr)`
      uses the expression's **static** type, so an `any` names as `"any"`,
      never its dynamic type. A top-level `const` strips, matching what
      boxing does with tags, keeping the name the tag's preimage —
      implemented, see
      [The typename builtin](docs/language.md#the-typename-builtin)
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
      error emission and `eval_const`. Top-level position (where struct-layout
      assertions live) is implemented, see
      [Error directives](docs/language.md#error-directives):
  - [ ] statement position — allow both directives inside function bodies.
        Unlocks the generics synergy: inside a generic body each fires *per
        instantiation* at monomorphization, a lightweight type-parameter
        constraint that complements the planned
        [interface bounds](#types-and-generics) (an assert in a
        never-instantiated generic correctly never fires), and a failing
        assert in a generic body gains the shipped
        [instantiation-backtrace](#tooling-and-c-interop) notes for free
- [x] Warning subsystem — a non-fatal diagnostic channel, the foundation the
      `@deprecated` directive below and enum-exhaustiveness checking both build
      on. Warnings collect on the `CodeGen` instance and the driver prints
      `file: warning: line N: msg` to stderr *after* generation succeeds,
      without aborting; the `-Werror` toggle promotes warnings to the failure
      exit path (each rendered as `file: error: line N: msg [-Werror]`, exit 1,
      no outputs written). Shipped with the decided default: `-Werror` off in
      normal builds, on in this repo's CI and `test.sh`. `@warning(msg)`
      shipped alongside as the channel's first producer (moved up from the
      directives item below): `@error`'s non-fatal twin, a top-level directive
      emitting a warning at its position, most useful under `@if`, riding the
      existing `ErrorDirective` node and parse rule. The enum
      [`case` exhaustiveness](#types-and-generics) consumer remains future
      work; implemented, see
      [Error directives](docs/language.md#error-directives)
  - [x] `@deprecated(msg)` over the channel above — a declaration attribute
        on a function that fires a diagnostic (a **warning by default**, not
        an error) at each *call site*, pointing at the caller with a migration
        message (`'copy_bytes' is deprecated: use bytecopy instead`). Message
        storage landed on the `Func` node; the warning fires at each name
        resolution point (direct call, generic overload pick — a mixed set
        warns only when a deprecated overload wins — function values, and the
        `for ... in` `_it`/`_next` protocol), with no suppression, and the
        driver deduplicates repeats of one (file, line, message) at print
        time so a call site inside a generic body reports once, not once per
        instantiation. It round-trips through `.mci`: for free for generic
        and `@inline` functions (verbatim source-span emission), and by
        explicit re-emission (message re-escaped) on concrete exported
        prototypes. Default severity is warn deliberately: a hard error would
        make a deprecated alias uncallable and break importers, defeating the
        purpose. The motivating use case shipped with it: the four generic
        forwarders in [memory](libmc/memory.mc) (`copy_bytes`, `copy_items`,
        `set_bytes`, `set_items`) now carry `@deprecated` with their
        replacements, and the internal stdlib/test callers were repointed to
        the new names (CI runs `-Werror`). Scope v1 is functions only
        (types/enums/globals later); the terminal escalation to a hard error
        is not a flag on `@deprecated` but the shipped
        [`@removed` tombstone](docs/language.md#removed-functions) directive;
        implemented, see
        [Deprecated functions](docs/language.md#deprecated-functions)
  - [ ] dedup relaxation — warnings deduplicate at print time on their
        (file, line, message), so a call site inside a generic body reports
        once across instantiations; an opt-in to name the triggering
        instantiations — per-instantiation repeats, or the
        [instantiation-backtrace](#tooling-and-c-interop) note chain attached
        to a warning the way errors already carry it — for when the collapsed
        repeats hide which type is at fault. Print-time only either way: the
        collected list embedders read already keeps every emission
  - [x] opt-in warning flags — named, default-off warning classes over the
        channel: a producer tags its warnings with a class name, the driver
        enables a class with a repeatable `-W<name>` (an unknown name is a
        hard CLI error, so a typo cannot silently enable nothing; `error`
        and `all` are reserved by never being registered as class names,
        and a class name never starts with `no-`, keeping the `-Wno-<name>`
        spelling claimable for per-class disabling later), and `-Wall`
        enables every opt-in class at once; an enabled class names its flag
        in the rendering (`file: warning: line N: msg [-W<name>]`, the
        discoverability convention `[-Werror]` already established), and
        `-Werror` composes unchanged, promoting exactly what printed — an
        enabled class as `msg [-Werror=<name>]`, while a disabled class
        neither prints nor fails the build. The author-placed producers
        (`@warning`, `@deprecated`) stay unconditional — they are explicit
        requests, not analyses — and keep their plain `[-Werror]` tail
        byte-identical; opt-in classes are reserved for analysis-derived
        diagnostics that can fire on legal, C-idiomatic code. Filtering
        happens at print time, like the dedup above (the collected list
        embedders read keeps every emission, tagged with its class), and a
        warning class never changes codegen; implemented, see
        [Opt-in warning classes](docs/language.md#opt-in-warning-classes):
    - [x] `-Wunchecked-dereference` — the first opt-in class and the
          motivating one: warns on `*x`, `x->field`, and `x[i]` (reads,
          writes, and compound assignments alike) where `x` is
          a nullable `T*` not **proven non-null** at that site, "proven"
          being exactly the shipped `@nonnull` proof relation
          ([Functions and methods](#functions-and-methods)): a `@nonnull`
          parameter, a flow-narrowed local or field projection, an
          always-non-null source, or a
          postfix `!` assertion — no new analysis, just the existing proof
          query asked at every dereference site, reporting instead of
          rejecting. Off by default deliberately: mcc pointers are
          nullable-by-default like C's, so a default-on warning would greet
          every ported C idiom with noise; `-Wall` includes it. Postfix `!`
          doubles as the per-site suppressor, and narrowing's conservative
          limits transfer as the warning's noise floor: index elements
          (`a[i]`) and globals never carry facts, so they always take `!`
          (field projections narrow since the 0.6.1 path facts), and the
          shipped [loop-body fact preservation](#functions-and-methods)
          keeps the false-positive rate down wherever a guard precedes a
          loop. [Pointer decay](#functions-and-methods) sites never warn
          (decay already requires the proof), slice indexing never warns
          (the borrow's data pointer is the slice's invariant), and the
          print-time dedup above keeps a dereference inside a generic body
          to one report; implemented, see
          [-Wunchecked-dereference](docs/language.md#-wunchecked-dereference):
      - [ ] the `libmc` sweep, the acceptance test by dogfooding: wave-1
            `@nonnull` adoption cleared the loudest sites, but the
            containers still hold ~60 invariant-backed dereferences that
            warn under the class (`self.data` index bodies and iterator
            `obj->data` chains across dict/set/list/queue/ring/stack/
            hashing/string) until each takes its `!` assertion or guard.
            Once
            `libmc` compiles warn-free under `-Wunchecked-dereference`,
            this repo's `-Werror` CI can add `-Wall`, which stays gated on
            this sweep
    - [ ] `-Wextern-nonnull` — the enforcement class for `@nonnull` on
          `@extern` declarations, which is opt-in by design: by default an
          unproven pointer reaching an annotated extern slot compiles
          silently, so mechanically ported C code (which would otherwise
          hit a null-proof error on every `strcpy`/`strlen`/`memcpy`
          call) builds with no flag at all; strictness on the C boundary
          is what a codebase reaches for, not what a port escapes from.
          Enabling the class warns at each unproven site over the channel
          (`[-Wextern-nonnull]` in the rendering), `-Wall` includes it,
          and `-Werror` promotes it to the failure path, which is how
          this repo opts in: CI and `test.sh` already run `-Werror`, so
          adding the class there turns libc-call proof violations into
          build failures at home while user ports stay unaffected (the
          same dogfooding endgame as `-Wunchecked-dereference` above).
          Default silent rather than warn-by-default, deliberately: the
          no-unavoidable-noise principle above cuts both ways, and a
          fresh port would drown in per-call warnings it never asked for;
          discoverability rides `-Wall` and the flag-suffix convention
          instead. Two pieces stay unconditional: passing a literal
          `null` to an annotated slot is always a hard error (never
          porting noise, it is equally broken C), and native mcc
          `@nonnull` never joins this class (the callee body holds the
          parameter as a non-null fact, so its caller proof is
          load-bearing). The class never changes codegen, which forces
          one redefinition of the shipped `@extern` allowance: the LLVM
          `nonnull`/`dereferenceable` attributes are justified only by
          unconditional caller proof, so `mark_nonnull` stops emitting
          them on `@extern` declarations entirely (native functions keep
          them; docs and any exact-error-string tests for extern
          violations update with the implementation). A hard-strict
          posture flag restoring the optimizer hint alongside error-level
          enforcement stays possible later, only if demand appears. The
          annotations themselves still ship unconditionally in source and
          `.mci` stubs (the declared promise never varies per build), and
          the rejected alternative stands recorded: a `-D`/`@if`
          `SAFE_LIBC` gate would duplicate the declaration surface per
          branch (`@if` is declaration-granular), flip the whole
          program's libc contract on one define, and break `.mci`
          identity (stubs re-emit `@nonnull`) plus the merge collapse of
          matching `@extern` redeclarations
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
      compile-time format checking later. Per-type rendering is not
      per-struct `format` methods but the shipped stdlib `format` overload
      set below, per the [open overload sets](#functions-and-methods)
      rule that protocols are free-function overload sets. The
      signature is `fn println(format: slice<const uint8>, args: slice<const any>)`:
      a string literal adapts to `format` at the call site (so `println("{}", a)`
      works directly), and an owned `struct string` borrows in with
      `str as slice<uint8>` — both via the
      [`slice<T>`](docs/language.md#slices) borrowing rules:
  - [x] printf-style `%` formatting — today's `print`/`println` in the
        [standard library](README.md#standard-library), which the `{}` model
        will supersede
  - [x] the stdlib `format` overload-set module — `libmc/format.mc`, the
        type-driven per-type rendering layer the placeholder stages below
        dispatch into: a
        `format(mut str: string, value: X, const modifier: string)`
        baseline set with a closed signed group sign-extending into an
        `int64` worker, a closed unsigned group, concretes for
        `float64`/`bool`/`char`/`char*`/`slice<char>`, a generic
        `slice<T>` list-renderer, and an unbounded `<typename>` fallback
        rendering the type's name in angle brackets; integer modifiers
        `:x`/`:X`/`:p`, bool `:y`/`:yes`, and slices apply the modifier
        per element. The formatting member of the overload-set protocol
        family, riding the shipped
        [open overload sets](#functions-and-methods): the set is open,
        so making a type printable is one `format` overload written in
        the user's own module
  - [ ] formatting over the `slice<const uint8>` format with bare/sequential and
        positional placeholders (`"{d} {f} {x} {s}"`, `"{0:d} {1:f} {2:x} {3:s}"`),
        parsed at runtime
  - [ ] format modifiers — precision and zero-padded width (`.Nf`, `Nx`, `0Nx`,
        `0x0Nx`, `Ns`, and `sN`), e.g. `{.8f}`, `{08x}`, `{0x08x}`, `{20s}`,
        `{s20}`; the per-type channel these travel through (the `format`
        set's `modifier` parameter, already routing `:x`/`:X`/`:p` and
        `:y` per type) shipped with the baseline module above
- [ ] String interpolation — `println("x = {x}")`: a string literal with
      `{expr}` holes desugars at compile time into the formatted
      `println("{}", ...)` call above (`{{`/`}}` escape a literal brace), so it
      is surface syntax only — no new runtime. Depends on formatted print
      (above) and the [native variadics](#functions-and-methods) it builds on

### Tooling and C interop

- [x] Instantiation backtraces on errors — an error inside a monomorphized body
      used to print as a bare line in the template file with no trace of how
      the compiler reached it; a source-level note chain on `LangError`
      (which previously carried only message/line/source) has the driver print
      `file: note: line N: ...` lines after the unchanged primary
      `file: error: line N: msg`:
  ```
  libmc/hashing/splitmix64.mc: error: line 10: cannot cast box to uint64
  libmc/hash.mc: note: line 12: in instantiation of splitmix64<box>
  yourcode.mc: note: line 5: in instantiation of hash<box>
  ```
  the "in instantiation of ..." note chain of C++ and Rust. Frames are built on
  the exception-unwind path through the `try`/`except`/`finally` at the
  monomorphization entry points (`instantiate` for generic functions,
  `instantiate_struct` for generic structs, plus type-alias resolution, so a
  chain through `string` names `string`), so function and struct instances
  interleave (one `string` call nests a generic-function instance and a
  generic-struct instance) and there is no live push/pop stack to corrupt.
  Instantiations are memoized, so a cached instantiation reports the first
  triggering path, matching C++/Rust. Independent of the
  [warning subsystem](#metaprogramming-and-builtins): errors already have their
  own terminal render path, so this extends that path and never touches the
  non-fatal warning channel; the two share only a one-line severity-formatting
  helper (`{where}: {severity}: line N: {msg}`), introduced here and reused by
  whatever ships next. Test-safe: the primary error line stays
  byte-identical and notes appear only when the instantiation chain is
  non-empty, so the suite's `str(LangError)` matches hold and the
  substring/`startswith` stderr checks in `test_cli.py` are undisturbed;
  implemented, see
  [Instantiation backtraces](docs/language.md#instantiation-backtraces):
  - [ ] Import / inclusion / macro frames — additive frame sources for the same
        note chain, no new render machinery: the import chain (`merge_imports`),
        `@if`-inclusion (`flatten_conditionals`), and eventual macro expansion.
        The macro-frame part is gated on
        [`@macro`](#metaprogramming-and-builtins) existing, so it rides in
        whenever macros land; the import and inclusion frames can come first
  - [ ] note-depth cap — a deeply nested instantiation chain prints every
        frame today; cap the rendered chain, keeping the innermost and
        outermost frames and eliding the middle (`... N more`). A print-time
        change to the note renderer only — the primary error line stays
        byte-identical, so the suite's exact-string matches hold
- [ ] Linker selection — `--linker=/path/to/ld` to pick a specific linker
      (today whatever the driver `cc` defaults to)
- [ ] Compiler-driver selection — `--cc=/path/to/cc` to choose the C driver used
      for linking (today the system `cc` on `PATH`)
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
    - [ ] driver-level module dedup — treat a same-directory, same-stem
          `foo.mc`/`foo.mci` pair as one module with the `.mc` winning (the
          same `.mc`-first priority `_import_candidates` applies to a bare
          `import "foo"`), via a pre-pass over the resolved import graph so
          load order can never let a first-loaded `.mci` suppress the
          `.mc`'s bodies. This is what actually delivers a build where a
          library's `.mci` and its `.mc` source coexist: it drops the
          stub's structs, consts, generic templates, and tombstones
          wholesale, with the shipped
          [forward declarations](docs/language.md#bodyless-fn-prototypes)
          covering the function-prototype level. One deliberate carve-out: generated
          `.mci`s re-emit `@removed` tombstones, so a tombstone plus an
          identical-message tombstone must collapse (differing messages
          stay an error), a documented amendment to the one-tombstone rule
          scoped to this item, not to forward declarations
- [ ] C header generation — emit a `.h` of the public surface (like
      `--emit-interface` does for `.mci`), so C code can call into an mcc
      object or library

<!-- Add upcoming features here, e.g. - [ ] feature — short note -->
