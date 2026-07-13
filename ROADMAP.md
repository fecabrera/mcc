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
      longer-lived target)
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
      `format` overload set behind the `{}` placeholders), `equality`
      (the open `equals` set), `hash` (generic `hash<T>`)
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
  - [x] generic type aliases — a type-parameter list on a `type` declaration,
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
    (`entry<U>` with `U` the outer parameter), and a method lookup through
    an alias sees the underlying instantiation, not a separate namespace
    (since shipped: the alias/builtin method qualifiers under
    [Methods / OOP](#functions-and-methods) canonicalize an alias
    qualifier to its target's family). The rules: arity is checked at
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
    parameters when they land. The one deliberate exclusion — a
    convention-carrying comparator type (`fn(const T, const T) -> bool`)
    — has since been filled: the [`mut` item](#functions-and-methods)'s
    convention-carrying function types carry `const`/`mut` in the
    function-pointer type (a const-scalar spelling erases, a
    const-aggregate one records the hidden reference, classified per
    binding), so `cmp<T>` picked it up transparently at scalar and
    struct `T` alike, exactly as planned
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
  - [x] [bounds](docs/language.md#bounds) — constrain a parameter with `fn myfunc<T extends mystruct>(x: T)`
        (a struct and the structs in its declared `extends` lineage). The bound
        is **nominal**: satisfied only by `mystruct` and its declared `extends`
        specializations, never by a struct that merely shares its field prefix,
        per the shipped single
        [nominal struct subtyping](docs/language.md#structs) model the
        upcast and slice-borrow also follow (without it this bound would reject
        a layout twin that the upcast beside it still accepts, the asymmetry
        that model removed). The target was concrete-only when this
        shipped; since generalized, it may reference type parameters (the
        dependent-bounds sub-item below). The explicit-set form, once
        sketched here as `T in (t1, t2, ...)`, is settled under a different
        spelling as the closed-type-groups sub-item below
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
    - [x] [dependent bounds](docs/language.md#bounds) — a bound's target
          may reference type parameters: the enclosing method qualifier's
          (`fn list<T>::equals<U extends slice<T>>(const self: list<T>,
          const lst: U)`) or an earlier (indeed any) parameter of the
          same list (`fn f<S, T extends S>`), both previously rejected by
          the parser's "a bound must be a concrete struct" error (the
          same-list form the parser had called a deliberately deferred
          follow-up). The bound is collected at the declaration
          (classified dependent when its target names any of the
          function's type parameters) and resolved at each call in the
          declaring file's scope, once deduction has bound the parameters
          it names (`slice<T>` at `T = int32` resolves to `slice<int32>`),
          then checked by the same nominal-subtype relation as a concrete
          bound; a rejection names the resolved bound (`box<char> does
          not satisfy the bound slice<int32> of 'box::eq'`). Deduction is
          unchanged: a parameter mentioned only in a bound is not
          inferred from it, a bound whose referenced parameter is still
          unbound passes the lenient partial trial, and a target that
          resolves to a non-struct (`U extends T` at `T = int32`) is
          unsatisfiable, rejecting whatever was deduced. Carried over
          byte-for-byte: concrete-bound behavior, tier ranking (concrete
          beats bounded beats unbounded) and the v1
          one-bounded-overload-beside-an-unbounded-fallback shape, method
          inheritance through `extends` (the bound resolves through the
          seeded base parameters, verified for concrete and generic
          derivations), template
          [symbol bases](docs/language.md#template-symbols) (a dependent
          bound substitutes placeholders,
          `matches<$1 extends slice<$0>>`), and `.mci` round-trips
          (templates travel verbatim); a bounded parameter's default is
          now checked where the bound resolves (declaration for concrete,
          per call for dependent). The driving use case is the stdlib
          container pattern: `list<T>::equals<U extends slice<T>>`
          accepts any `slice<T>`-extending value with no `as` at the
          call site
    - [ ] interface bounds — `fn myfunc<T implements I>(x: T)`, asserting
          that `T` implements interface `I`: checked at each monomorphized
          instantiation (the concrete type must define every method `I`
          names), then calls dispatch statically — no fat pointer, no
          dispatch table is ever formed (monomorphization knows the
          concrete type, so every call binds directly). The static
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
      silently changing which `case type` arm matches. The planned
      [error declarations](#types-and-generics) below already commit to
      the nominal model from birth, inside a brand-new declaration kind
      where no existing code can break. It is the
      genuine prerequisite for both dependents nested below: the directional
      conversion safety that [enum member reuse](#types-and-generics) above
      suggests, and enum-aware `case` exhaustiveness (today `case` is pure
      integer equality with no enum awareness):
  - [ ] directional conversion safety — once enums are nominal, a
        [member-reuse](#types-and-generics) derivation gains real conversions:
        base-to-derived is implicit widening (the derived value set is a
        superset of the base's), derived-to-base is explicit and checked. Note
        this is the **inverse** of struct `extends`'s derived-to-base value
        upcast, because here the extending enum is the value-set superset.
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
- [ ] Error handling — recoverable errors as values: a dedicated `error`
      declaration naming the causes, a builtin `result<T, E>` / `result<E>`
      template type carrying either the ok value or the error, explicit
      construction, and consumption forms that keep the error path a local,
      visible branch. The recoverable complement of the shipped stdlib
      [`panic`/`assert`](#functions-and-methods) (the unrecoverable lane),
      and what retires the out-param-plus-`bool` and sentinel-return idioms
      the stdlib leans on today (`dict_get(self, key, mut out) -> bool`).
      No exceptions, no unwinding, no hidden control flow, and no `void`
      anywhere: a function that can only fail returns `result<E>`:
  ```c
  error my_error {
      NOT_FOUND = "Not Found",
      IO_ERROR = "I/O Error",
  }

  fn my_func() -> result<int64, my_error> {
      if (missing) { return error(my_error::NOT_FOUND); }
      return ok(value);
  }
  ```
  The `error` declaration is enum-like but **nominal** and `int32`-backed:
  variants always auto-number from 1 in declaration order — error values are
  automatic, with no explicit `= n` form (a bare `= <int>` rejects) — so the
  values are dense `1..N`, every variant is non-zero by construction, and
  zero is the reserved, **unnameable** no-error state that makes `if (err)` a
  total check. A variant's `=` slot may instead set an optional display
  string, as above (it does not affect the numbering). Deliberately
  nominal from birth, front-running the
  [nominal enums](#types-and-generics) migration above inside a new
  declaration kind where no existing code can break; an `error` declaration
  is the **only** admissible `E` (primitives, structs, and plain enums
  reject at instantiation). `result` itself is a compiler-built interned
  template on the `slice`/`tuple`/[`any`](#structs-arrays-and-data-layout)
  pattern, laid out as a tag plus a union of the arms over the shipped
  union layout machinery; `ok(v)` / `ok()` (`result<E>` only) / `error(e)`
  are the **only** constructors, behaving as the builtins
  `ok<T, E>(v: T) -> result<T, E>` / `error<T, E>(e: E) -> result<T, E>`
  (one arm fixed by the argument, the other bound by context or a sibling
  ternary arm), with no implicit value-to-result coercion in either
  direction (and
  `error(` the builtin, `error name {` the declaration, and the
  `@error(msg)` directive are three spellings that never collide). Staged:
  - [x] stage 1: `error` declarations, the `result` type, and the
        constructors — the declaration kind (nominal registration,
        auto-numbering, display strings, `==`/`!=` against its own
        members, truthiness, `case`, `.mci` round-trip), the
        `result<T, E>` / `result<E>` builtin (arity 1 or 2; `E` must be an
        `error` declaration), and the `ok()`/`error()` builtins with their
        context-required and wrong-arity diagnostics — implemented, see
        [Error handling](docs/language.md#error-handling). Follow-ups:
    - [x] constructors compose as values — `ok`/`error` bind their free arm
          against a sibling ternary arm, so `cond ? ok(v) : error(e)` is a
          full `result<T, E>` with no annotation (and one arm may be an
          already-typed result). A direct result sink still builds eagerly,
          keeping struct-literal/string ok-value adaptation; a same-kind
          ternary (both `ok`, both `error`) must be annotated or the value
          lifted out — implemented, see
          [Construction](docs/language.md#construction-ok-and-error)
    - [ ] variant payloads — `SHORT_READ(uint64)`, a variant carrying
          data: rides the same tag-plus-union machinery `result`
          introduces, and the declaration head is chosen so payload parens
          are purely additive
    - [ ] `case` over an error — exhaustiveness over a declared error's
          variants, the error-decl counterpart of the enum
          [exhaustiveness item](#types-and-generics) above, with the same
          warning-first staging; unlike the enum item it waits on no
          nominal migration, the declaration is nominal from birth
    - [ ] `result<E>` tag folding — a layout optimization the reserved
          zero state makes possible: fold the tag into the error value
          itself, so `result<E>` is the size of bare `E`
    - [ ] `errdefer` — deferred cleanup that runs only when the enclosing
          scope exits with an error, composing with the shipped
          [`defer`](docs/language.md#defer) machinery
          (cleanup-on-failure without restating it in every handler)
  - [x] stage 2: the binding forms — form 1, the C-flavored destructure
        `let ret, err = f();` (`err` is the variant or the zero state,
        `ret` the ok value or zero-filled, `if (err)` the check; lowered
        as a tag select, never as a union-arm pun; rejects `result<E>`,
        which has no value to bind), and form A, the handler form
        `let ret = try f() except (err) { H } [else { S }];` — `try`
        binds the call chain that follows and carries its `except`
        clause (both reserved keywords; `except` never appears without
        `try`) — also on `return` and as a whole expression statement
        (`try f() except (err) { H };`, the `result<E>` consumer, where
        the handler is obligation-free). Where a value escapes, the
        handler must diverge or `emit` a fallback; `else` is the ok-arm
        only, Python-style: it runs on `ok(v)` and is skipped on the
        handler's emit-fallback path. A bare `try g()` (no handler) is a
        staged compile error until stage 3 — implemented, see
        [Consuming a result](docs/language.md#consuming-a-result-the-destructure)
  - [x] stage 3: the rest of the `try` production in one change set
        (the keyword and its `except` handler clause landed in stage 2;
        this stage adds the forms without one, settling the remaining
        grammar once: `try ( IDENT =` opens the statement, anything else
        is the expression, the shipped `with`-head discipline). The
        statement form:
        `try (ret = f()) { B } except (err) { H }` binds a fresh `ret`
        scoped to `B` with an obligation-free handler, and takes **no
        `else` arm**: the `try` block already is the no-error arm. The
        propagation expression: `let ret = try g();` desugars to
        `try g() except (err) { return error(err); }` and requires the
        enclosing return type to carry the **same** `E` (a compile error
        naming both types otherwise; mapping between error types is a
        handler's job). The `??` fallback: `try g() ?? v` discards the
        error and lazily evaluates the fallback instead of propagating
        (no requirement on the enclosing return type; the fallback
        coerces to `T`, so `result<E>`, which has no value to default,
        rejects). This stage owns the `??` token (a lexer `OP2`
        alternative) and its production: the right-hand side is a full
        greedy expression, or an emit-block `{ ...; emit v; }` that may
        diverge; `??` binds **looser** than the ternary and every
        binary operator (the lowest-precedence expression form, just
        above assignment) and chains **right**-associatively, so the
        fallback extends greedily to the end of the expression:
        `try g() ?? 2 + 1` is `try g() ?? (2 + 1)`,
        `try g() ?? c ? a : b` is `try g() ?? (c ? a : b)`, and
        `try g() ?? p ?? q + 1` is `try g() ?? (p ?? (q + 1))`
        (parenthesize to operate on the unwrapped value:
        `(try g() ?? 0) + base`). A result left of `??` without `try`
        rejects with the hint that results unwrap through `try`, and a
        pointer left-hand side rejects with a forward hint until the
        [pointer-truthiness item](#functions-and-methods) turns on its
        null-coalescing arm over this same production — implemented, see
        [Propagation: bare try](docs/language.md#propagation-bare-try)
  - [x] stage 4: diagnostics and rendering — `-Wunused-result`, an opt-in
        class over the shipped
        [warning registry](#metaprogramming-and-builtins) for a statement
        that discards a `result` (the accidental-ignore hole the design
        exists to close; every consuming form is silent, and `let _ = f();`
        is the deliberate-discard suppressor); and per-declaration variant
        name tables behind two builtins — `error_name(err)` (the variant
        identifier) and `error_message(err)` (its declared display string,
        falling back to the identifier) — implemented, see
        [Rendering](docs/language.md#rendering-error_name-and-error_message).
        Follow-up:
    - [ ] automatic `{}` rendering of an error value through
          [formatted print](#strings-and-formatting) — `println("{}", err)`
          printing the variant name directly, once formatting
          user-declared types has a general answer (the format machinery is
          a closed set that cannot yet enumerate user error declarations)
  - [ ] stdlib adoption wave — migrate the mcc-native out-param surface
        (the `dict_get`/`list_get`/`set_get`/`string_get` family and
        future file/parse APIs) to `result` returns, explicitly **after**
        the language stages above and coordinated with the
        [receiver-kind migration](#functions-and-methods) so stdlib
        signatures churn once, not twice; `libc/` bindings keep their C
        sentinel returns (fixed ABI), with mcc-native wrappers as the
        result-typed surface

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
        [variadic](docs/language.md#native-variadic-arguments) pack's
        `slice<const any>`, and what unblocked the since-shipped strings
        chain: `any`, then native variadics, then
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
        slices; an **owning** `any` of a struct or array (a `let`, a
        return, a global) stays rejected (by value the payload is
        unbounded, by pointer the lifetime goes implicit; `&s` is the
        explicit escape), while a struct boxed **by reference into a
        `const any` target** (the `slice<const any>` a native variadic
        collects into) is the call-scoped, non-escaping
        borrow-context case now broken out and taken in the struct-boxing
        sub-item below (the owning/sound case stays gated there). Values
        wrap implicitly at the coerce choke point,
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
        payload-carrying-enum `when Variant(x):` reads as kin, and the
        shipped [tuples](docs/language.md#tuples) stay the complementary
        non-erased product.
        A transparent enum boxes under its underlying type's tag;
        [nominal enums](#types-and-generics) give an enum its own tag, a
        silent `case type` change folded into that item's migration story.
        Implemented as settled, see
        [The any type](docs/language.md#the-any-type); the nested items
        below are the follow-ups:
    - [x] global/`@static` `any` initializers — the const-initializer path
          boxes a compile-time constant into a constant tagged aggregate,
          under the same tags and owning-box rules as runtime boxing
          (implemented, see [The any type](docs/language.md#the-any-type))
    - [x] struct boxing — lifts the v1 struct rejection for the
          call-scoped borrow case (implemented, see
          [The any type](docs/language.md#the-any-type)). A struct boxes into
          an `any` **only when the target is a `const any`**, a
          by-hidden-reference position (the same slot a `const`/`mut` struct
          parameter already travels through, per `hidden_ref_indices` in the
          generator). There the aggregate boxes **by reference**: the payload
          holds a pointer to the caller's existing storage, tagged as the
          struct type itself (`point`, not `point*`), so it unboxes with no
          copy and dispatches to a user `format(const value: point, ...)`
          overload. The motivating case is `println("{}", p)`, whose variadic
          args collect into `slice<const any>`, exactly such a by-reference
          position. Scoping the borrow to a slot that cannot outlive the call
          contains the lifetime-escape this item was gated on by construction
          (the residual, copying a `const any` param into an owning slot, is
          the same `slice<const T>` borrow discipline mcc already accepts). An
          **owning** `any` of an aggregate (`let a: any = p;`, returning an
          `any`, a global/`@static` `any`) stays rejected: the payload then
          either exceeds 16 bytes by value or holds a borrow that escapes. Two
          follow-ups stay open:
      - [ ] owning aggregate boxing — the general ceiling-lift: a by-value
            payload when it fits (≤16B), or a sound borrow-marked `any` for
            the rest. Blocked on a representation question the by-reference
            cut sidesteps: today an aggregate tag unambiguously means "payload
            is a pointer to caller storage", which is what makes the
            by-reference cut sound, but once owning by-value boxing exists a
            `point`-tagged `any` could be either by-value or by-reference, so
            the tag alone no longer says which and the unbox path needs a
            borrow/representation marker beyond the type-id tag
      - [ ] unions and fixed arrays — extend aggregate boxing past structs.
            A union tag does not name its live member, and a fixed array
            compounds the by-value-vs-by-reference payload question above, so
            both wait behind the struct cut
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
          an identifier in `lib/` and `examples/`. The statement is
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
          arms (the sketched stdlib-formatter use,
          `when T* ptr: l = snprintf(buf, MAX_BUF_LEN, "%p", ptr);` after
          `when char* s:`; the
          [native variadics](docs/language.md#native-variadic-arguments)
          stdlib flip consumed this item, though in a simpler shape,
          the lone generic `with` arm in `std/io`'s `format_args`;
          formatter adoption was deliberately not a stage below). The lowering family is the one the
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
          operations. The motivating end-state shipped with the
          native-variadics stdlib flip, condensed past the sketch here:
          `std/io`'s `format_args` is a lone generic
          `with (t = args[i] as T)` arm (the `with` statement's defined
          fall-through standing in for the `else`) dispatching into the
          open `format` overload set of `lib/std/format.mc`, per-type
          behavior living in the set's members rather than in
          `case type` arms, and every boxed type without a viable
          formatting path a compile error instead of a runtime gap.
          Settled in the same discussion: multi-type arms in type
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
  - [x] global/`@static` union initializers — teach the const-initializer
        path to emit a union constant (zero-fill plus the one written member),
        and, in the same change, a struct-literal constant (the const path had
        no struct-literal arm before). A union constant is typed as its written
        member plus trailing pad, not the union's own IR type, so a single
        `var_addr` bitcast normalizes the divergent storage; see
        [Unions](docs/language.md#unions) and
        [Structs](docs/language.md#structs)
  - [x] dedicated union declaration — migrate unions off the shared struct
        declaration onto their own AST node and type kind, so a struct-only
        code path (sequential layout, `extends`, prefix upcast) can never
        silently accept a union. A pure compiler refactor, no language change:
        a `union` now parses into its own `UnionDecl` node (parallel to
        `StructDecl`), and the "any aggregate" predicate split into
        `is_aggregate` (struct or union) versus a record-only `is_struct`, so
        the struct-only layout/`extends`/upcast/nominal-subtype paths key off
        the narrower test. Surface syntax, semantics, and emitted IR are
        unchanged
- [ ] Bitfields — `field: uint32 : 5;`, packing consecutive narrow fields into
      one storage unit, for hardware registers, protocol headers, and C-layout
      interop (many syscall/kernel structs use them; `@packed` doesn't
      substitute). Follows the platform C ABI's per-target layout rules, so it
      pairs with the [C struct-passing ABI](#tooling-and-c-interop) work; the
      read-modify-write granularity under a `@volatile` struct must be
      specified
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
  [memory](lib/std/memory.mc), struct literals, deref-assign, whole-struct copy),
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
        formatting this is now done by a literal adapting to `slice<const char>`
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
      parameters (ABI mismatch). A function using it initially could not be
      taken as a plain `fn(...)` value — the hidden-reference convention wasn't
      carried by the bare `fn(...)` type, a source-level type-system
      simplification, not an ABI limit — a restriction since lifted by the
      convention-carrying function types nested below, which are also what
      makes the view-table reconciliation below rigorous. Note
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
  - [x] convention-carrying function types — `mut`/`const` became part of the
        function-pointer type: `fn(mut char)` / `fn(const struct big)` is a
        **distinct, non-coercible** type — in either direction, with no `as`
        hatch, since the hidden-reference convention is a calling-convention
        fact no cast could bridge (the error says so instead of offering one).
        The bare name of a `mut`/`const`-taking function infers the carrying
        type, and calls through the value pass the same by-reference arguments
        and run the same lvalue/storage checks (and proven pointer decay) as a
        direct call. `const` carries only where it changes the convention —
        on an aggregate; on a by-value scalar it erases at type formation, so
        `fn(const int32)` *is* `fn(int32)` (no spelled-but-uninhabitable
        types), and the generic-aliases item's deferred comparator
        (`type cmp<T> = fn(const T, const T) -> bool`) became inhabitable
        transparently at scalar and struct `T` alike. Collecting functions
        ride along (`args...` is sugar for a `const` slice parameter): legal
        values whose calls take the trailing slice explicitly — collection
        and the `@format` desugars stay direct-call affordances; implemented,
        see [mut/const-carrying function
        types](docs/language.md#mutconst-carrying-function-types):
    - [x] `-> mut T` in function types — the return convention joins the
          parameter ones: `fn(...) -> mut T` is a distinct, non-coercible
          type (either direction, no `as` hatch — a mut return is passed as
          a pointer to the returned storage, a fact no cast could bridge),
          the bare name of a `mut`-returning function infers it (the last
          function-value ban is gone), and a call through the value is the
          same lvalue expression a direct call is — assignable (field-held
          callees included), projectable, re-lendable as a `mut` argument,
          and vouching in formation chains like a named `-> mut` candidate.
          `-> mut void` rejects per use (generic aliases validate per
          binding) and `-> mut const T` is banned at parse time in both
          the declaration and fn-type slots; implemented, see
          [mut/const-carrying function
          types](docs/language.md#mutconst-carrying-function-types)
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
        the since-shipped destructor machinery (Methods / OOP below) keeps
        that promise trivially — its trigger surface is only the
        constructor-sugar let, and parameters are never destroyed
        automatically. The since-shipped method-call sugar's receiver
        auto-deref landed as an explicit one-hop dereference riding the
        dereference machinery (`-Wunchecked-dereference` included) rather
        than as an instance of this rule; decay composed with the
        since-shipped method-inheritance receiver upcast is likewise
        scoped out (a derived `pointf*` does not decay-and-upcast into a
        base `mut self: point<float64>` slot in one step — a dot-call on
        the derived pointer works via the sugar's auto-deref, the
        explicit deref spelling stays available), while decay remains
        the mechanism that let the `libmc` container-self migration to
        `mut` receivers keep one call shape for stack containers and heap
        `T*`s alike before method syntax landed (the migration is the
        item nested below); implemented, see
        [pointer decay](docs/language.md#pointer-decay-into-constmut-parameters):
    - [ ] `libmc` receiver migration — flip the standard library's struct
          functions from raw pointer selves to receiver markers: read-only
          accessors become `const self` (the
          `get`/`peek`/`len`/`is_empty`/`eq` families; `at` settled as
          `mut self` in the accessor triad below), mutators become
          `mut self`
          (`init`/`from_*`/`destroy`/`reset`/`set`/`push`/`pop`/`append`/
          `remove`/`grow`), across `list`, `string` (a transparent alias of
          `list<char>`, so its `@inline` wrappers re-lend the same reference
          into the `list_*` slots), `dict`, `set`, `stack`, and `queue`,
          plus the companion struct pointers of the same APIs (`append`'s
          source, `eq`'s right-hand side, and `duplicate`'s `src` become
          `const`; `duplicate`'s `dst` and the `format_args`
          accumulator in `std` become `mut`). The accessor families flip
          to `const self` here and **stay read-only**: the mutable element
          accessor the now-shipped [`mut` returns](#functions-and-methods)
          item allows must form its return from a `mut`/pointer parameter,
          and overloads differing only in markers are banned under
          concrete overloading, so one name cannot serve both; mutable
          access arrives as the `_at` half of the `_get`/`_has`/`_at`
          accessor triad nested under `mut` returns below, and is
          explicitly not part of this migration. Strictly depends on the
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
          proves the rule covers real call patterns) and pre-positioned
          [Methods / OOP](#functions-and-methods): the since-shipped
          dot-call sugar dispatches `c.push(v)` over the already-correct
          `mut self`/`const self` signatures, with the formal receiver
          kinds still to land. Lands **staged**, in a forced order (each stage its
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
      - [ ] stage 5: `std` — implemented for `format_args`: the shipped
            [native variadics](docs/language.md#native-variadic-arguments)
            stdlib-flip stage (its vehicle, as planned) replaced the `format_arg`
            WIP with the open `format` overload set and landed the
            accumulator `mut` from the start — `format_args` and every
            `format` member take `mut str: string` today. Ticks once
            the rest of `std` is audited for remaining pointer selves
            (the `*_it` iterator signatures stay pointers by design,
            per the parent item)
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
  - [x] `mut` returns — a function that returns an lvalue:
        `fn string_at(mut self: string, i: uint64) -> mut char` makes
        `string_at(str, 0) = '/'` legal (as well as comparing it or copying it
        out with `let c = string_at(str, 0)`). A call returning `mut T` is an
        assignable expression: pointer-lowered, loaded eagerly in value
        contexts, and on the lvalue side an assignment or
        compound-assignment target, a base for projections, and re-lendable
        as a `mut` argument on both call paths. To keep the reference from dangling
        without a lifetime system, a `mut` return may only be **formed from a
        `mut`/pointer parameter or a global — never from a local or a by-value
        parameter** (roots traced through projections, derefs, and
        `mut`-returning calls; returning a pointer parameter itself or
        forming from a `const` receiver is rejected); this conservative,
        checkable rule fits the `string_at`
        case (the result derives from `self`) and preserves the non-escape
        guarantee, with `&` of a `mut`-returning call banned and the marker
        rejected on `@extern`, `main`, `void` returns, and `fn(...)`
        values. The example is deliberately a **separate name** beside
        the `const self` `_get` accessors of the `libmc` receiver
        migration above: a `mut` return cannot form from a `const`
        receiver, and overloads differing only in markers are banned
        under concrete overloading (an lvalue receiver keeps both
        candidates in a same-shape tie), so one name cannot serve checked
        `const` reads and mutable access both; stdlib adoption ships as
        the accessor triad nested below (its first stage landed the first
        live `mut` returns in `libmc`: `list_at`, `string_at`, and the
        flipped `ring_at`), which also owns
        the consequence that a `-> mut T` accessor has no `bool` failure
        channel. The implementation landed on groundwork that had already
        shipped: generic overloads mixing `mut` (above) defer the
        lvalue/value decision past overload resolution, exactly where an
        assignable call expression decides, and a `-> mut T` stub in a
        `.mci` is rendered and prototype-paired over the shipped
        [bodyless prototypes](docs/language.md#bodyless-fn-prototypes),
        with stores through a `mut` return tracked by the write effect;
        implemented, see [mut returns](docs/language.md#mut-returns):
    - [ ] stdlib accessor triad: `_get` / `_has` / `_at` — the settled
          shape of container element access, three names with three
          distinct jobs (supersedes the earlier `_ref`-style sketch:
          `_ref` is dropped as a name, the mutable accessor is `_at`).
          `_get` is the shipped checked read and does not move:
          `const self`, `bool` failure channel, `mut out` element.
          `_has` is the domain predicate,
          `fn dict_has<V>(const self: struct dict<V>, key: char*) -> bool`
          and friends: it answers exactly "is `_at` defined here", so on
          keyed containers it is key membership (the load-bearing case;
          today `dict_get` doubles as the membership test and forces an
          out-copy) and on sequences it is index-in-range (thin sugar
          over `i < len`, kept for the uniform guard idiom and generic
          code). Value containment is deliberately **not** `_has`: a
          search needs equality over a generic `T`, which the language
          has no protocol for, so that is a separate future family, not
          an overload of this one. `_at` is the unchecked lvalue
          accessor and the reason this nests here:
          `fn list_at<T>(mut self: list<T>, i: uint64) -> mut T`, usable
          on both sides of `=`. A `-> mut T` accessor has **no failure
          channel** (the return slot is the element; there is no `bool`
          half), so out-of-range must be UB, abort, or clamp: settled as
          **documented UB**, matching `ring_at`'s contract today, slice
          indexing (bounds carried, reads and writes go straight
          through), and pointer indexing. The checked story is not
          "later", it already ships as `_get`/`_has`; the genuine later
          is an opt-in checked `_at` mode (a debug bounds check landing
          on the planned stdlib `panic(msg)` under `@noreturn` below).
          The marker ban decides the receiver: overloads of one name
          differing only in `const`/`mut` are uncallable, so `_at` is
          `mut self` only, `const` code reads through `_get`/`_has`, and
          the since-shipped method-call sugar changed nothing (`c.at(i)`
          resolves to the
          one `mut self` overload and is an assignable mut-returning
          dot-call, `l.at(i) = v`; a `const` receiver simply cannot call
          it), and the since-shipped `@accessor` annotation on
          `list<T>::at` only adds the bracket spelling over that same
          one overload (`l[i] = v`). `dict` settles as guard-then-access: `dict_has` then
          `dict_at`, UB on a missing key, and **no insert-if-missing**
          (C++ `operator[]`'s implicit insert means a hidden allocation
          plus a default-constructed `V`, neither of which exists here);
          the honest cost is that the guarded idiom hashes twice where
          `operator[]` hashes once, accepted for v1 with a find/entry-style
          API lending the slot as the recorded future escape valve.
          Since SHIPPED with the `@accessor` adoption:
          `dict<V>::has` plus a `dict<V>::at` get/set pair, the read
          half exactly as settled here (`d[key]` unchecked, guard with
          `.has`) and the write half reshaped from a `-> mut` `_at`
          into the pair's explicit setter, `d[key] = v` inserting or
          updating through `.set` — the no-insert-if-missing ruling
          holds on the READ path (a read never allocates or
          default-constructs a `V`; the setter's insert is the declared
          write, not C++'s hidden lvalue insert), and the twice-hashing
          cost stands, `op=` through the pair hashing for the get and
          the set both.
          `ring` reconciles in the same pass: `ring_at` was the naming
          precedent (unchecked, documented UB) but carried the pre-triad
          signature (`const self -> T` by value), so it flipped to
          `mut self -> mut T` and `ring` gained `ring_has`, with
          `ring_get` still owed to keep `const` rings readable (until it
          lands, a `const` ring's only read is the front-only
          `ring_peek`). Lands **staged** (adoption split by what shipped
          together, not the earlier `_has`-everywhere-then-`_at` plan):
      - [x] stage 1: sequence adoption — `list_has`/`list_at`,
            `string_has`/`string_at` (`@inline` wrappers re-lending into
            the `list` pair), `ring_has`, and the `ring_at` flip to
            `mut self -> mut T`, the first live `mut` returns in `libmc`
      - [ ] stage 2: keyed containers and the `const`-ring read —
            the `dict` half shipped with the `@accessor` adoption
            (`dict<V>::has`, the load-bearing membership case, plus the
            `dict<V>::at` get/set pair above); remaining are `set_has`
            and `ring_get` to close the `const`-ring read
            gap the stage-1 flip opened
  - [ ] motivating use case: method receivers — `const`/`mut`/by-value on
        `self` express read-only / mutating / consuming methods directly,
        and the since-shipped method-call sugar
        ([Methods / OOP](#functions-and-methods) below) already dispatches
        `var.method()` over these ordinary parameters; what remains is the
        formal receiver-kind check, and a `mut`
        return formed from `self` as the memory-safe mutable accessor.
        See the receiver-kind note for the
        field-projection and view-table details
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
      intended protocol behavior (deliberately extended by the
      subsumption ordering below: an imported equal-rank but strictly
      more specialized candidate now wins what was formerly a tie).
      Non-overloadable functions stay
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
      shipped as `lib/std/format.mc` (the baseline stage of
      [formatted `{}` print](#strings-and-formatting)), declares
      the baseline
      `format(mut str: string, value: X, const modifier: string)`
      overload family (closed signed/unsigned groups, concretes, a
      generic slice list-renderer, an unbounded `<typename>` fallback),
      and `println`'s `format_args` dispatches into the set through a
      generic `with`/`case type` arm resolved per boxed tag at end of
      codegen over the whole-program overload set; with open sets, a
      programmer makes a type printable by writing one `format`
      overload for it in their own module (a `const value: point`
      overload directly, the shipped
      [struct boxing](#structs-arrays-and-data-layout) follow-up under
      `any` boxing the struct by reference into the `const any`
      slot) — the whole chain now works cross-module, with the
      deliberate privacy consequence that a `@private` `format`
      overload is invisible to `println`'s dispatch (it resolves in
      the stdlib's module) while direct `format(...)` calls in the
      owning module see it. Implemented, see
      [Function overloading](docs/language.md#function-overloading).
      Iteration is the protocol family's second member: the
      `for … in` protocol sub-item above is slated to desugar into
      `iterate`/`get_next` overloads riding this same mechanism; the
      slicing protocol nested below is the third, `c[a:b]` desugaring
      to a slicer overload set (the third member's indexing half,
      `c[i]` desugaring to an accessor overload set, was since
      SUPERSEDED by the shipped `@accessor` methods of the
      Methods / OOP item below). The
      family is free-function overload sets rather than per-struct
      member rules deliberately: a protocol built as an overload set
      is joinable by any type (builtins, pointers, enums, slices,
      types from modules the programmer does not own), which a
      member-function or interface rule can never be (no one can add
      a method to `slice<T>` or to a foreign library's struct). The
      overload-set protocol family is the language's protocol story,
      and the Methods / OOP item below is checked against it rather
      than the reverse: methods must not become the privileged
      mechanism for protocols (a tenet since breached for indexing,
      where the `@accessor` ship settled the brackets as method
      dispatch; formatting, iteration, and slicing keep the
      free-function shape)
  - [x] `@override` — replace a same-pattern member of an open set.
        With open sets, replacing group-covered or generic-covered
        behavior already falls out of the shipped ranking with no
        annotation (a user's concrete
        `format(mut str: string, value: int32, const modifier: string)`
        outranks the stdlib's closed-group template), which scopes
        the annotation to the one remaining case: replacing a
        same-pattern member of the set — the stdlib's concrete bool
        formatter (same `params_key`), or its unbounded `<typename>`
        fallback replaced by the user's own unbounded template (same
        template base). Both targets ship together in one change, not
        concrete-first. Marking an overload `@override` suppresses
        the declare-time duplicate-pattern collision; the annotated
        definition replaces the unannotated one regardless of import
        order, and the replaced body is never emitted. Settled: an
        `@override` whose pattern matches no existing overload is a
        declare-time error (typo protection, the C++
        override-specifier rationale); two `@override`s of one
        pattern are a hard compile error like any duplicate; and the
        target must be cross-module — an `@override` of a same-pattern
        member declared in its own file is a declare-time error
        (overriding your own definition is pointless; the use case is
        replacing another module's member). The original must be
        source-visible in this compilation: replacement reuses the
        original's mangled symbol and emits only the winner's body,
        so a separately-compiled original (a future ABI-pinned `.mci`
        object whose symbol is already defined in that other object)
        cannot be overridden without a link collision — fine today
        since the stdlib is source-merged into the `Program`,
        documented rather than left to surface at link time.
        Combinability: `@override` does not combine with `@extern`,
        `@static`, `@removed` (a contradiction — cannot replace-with
        and remove at once), or a bodyless prototype (no body to
        emit), each rejected; `@override @deprecated` stays allowed
        (the parser permits it, though it is not separately tested).
        `@override @private` was deferred, not shipped: public
        `@override` works by *symbol replacement* — it reuses the
        target's public mangled symbol and drops the original, so
        exactly one body is emitted under that symbol (a global
        replacement) — but a `@private` function's symbol is *salted
        and file-local*, so a private override cannot take over the
        target's public symbol. A coherent `@override @private` would
        need a different mechanism, *file-local shadowing*: keep the
        target, register the private member alongside it (its salted
        symbol already coexists), and have in-module resolution prefer
        the local private member over the same-pattern public sibling
        — a targeted resolution tie-break, where today that pairing is
        an ambiguity — a distinct, larger piece of work. So the
        shipped compiler rejects `@override @private` with a "cannot
        yet be combined" error; a `@private` original in another
        module is invisible regardless, so an override never targets
        it and simply falls through to ordinary resolution.
        Implementation
        shape: because replacement is order-independent and a
        no-match `@override` can only be judged once the whole set is
        merged, the collision is reconciled not inline in the
        single-pass registration loop but in a post-merge sweep
        (beside the group-overlap / bound-overlap checks) that picks
        the winner per symbol, marks the loser for skip-emission (the
        emission pass iterates the raw AST, and both `Func`s share one
        symbol slot), and errors on any override with no matching
        target or a same-file target. Taxonomy: an `@X` value-supplier
        promise, "this definition replaces an existing one", the
        `@deprecated`/`@removed` family. The driving use case is the
        same protocol story: a programmer setting their own formatters
        for stdlib-covered types
    - [ ] `@override @private` via file-local shadowing — the deferred
          combination. Unlike public `@override`'s symbol replacement,
          this keeps the target and registers the private member
          alongside it (its salted, file-local symbol already
          coexists), then teaches in-module resolution to prefer the
          local private member over the same-pattern public sibling —
          a targeted resolution tie-break that today is an ambiguity.
          A distinct mechanism from the shipped global replacement,
          which is why the shipped compiler rejects the combination
          rather than approximating it
  - [ ] slicing protocol — the family's third member, RE-SCOPED by the
        shipped `@accessor` methods (the Methods / OOP item below): this
        item sketched `c[i]` and `c[a:b]` both desugaring to
        free-function overload sets, and the indexing half is
        SUPERSEDED — `c[i]`, the store and compound forms through it,
        and multi-index `c[r, c]` now dispatch the receiver type's
        `@accessor` method family, settling indexing as a method affair
        against this item's free-function premise (the sketch was
        recorded as direction, not settled design, and its open
        question, whether the accessor half is the triad's `_at` set or
        a dedicated protocol name, resolved as the `_at` set itself,
        annotated `@accessor`). What remains is the bracket slicer:
        `c[a:b]` on a user-defined struct desugars to an overloadable
        free-function call resolved through ordinary whole-program
        overload resolution, so containers slice without
        compiler-blessed types and any type opts in by writing overloads
        in its own module (direction, not settled design):
    ```c
    let lst = list<int32>(10);
    lst[1];     // shipped: the item at 1, list<T>'s @accessor family
    lst[1:];    // this item: the sublist from 1 on, the slicer overload set
    ```
        Today `lst[1]` dispatches the shipped accessor family while
        `lst[1:]` remains
        the sub-slicing item's borrow-suggesting non-slice-receiver
        rejection, a single site deliberately shaped as this protocol's
        dispatch point; the protocol turns that rejection path (the
        bracket slice) into overload-set dispatch, while builtin receivers keep their primitive lowerings
        and never route through the protocol, the same builtins
        carve-out the [`for … in` protocol](#functions-and-methods)
        item records for its lowerings and the `@accessor` ship
        confirmed for indexing (a natively indexable base never
        consults an accessor). The split is the design, not an
        optimization: `slice<T>`'s static `{ data, length }` layout
        fully describes the view, so a compiler-constructed sub-view
        cannot misrepresent anything and slices stay primitive (the
        shipped [sub-slicing](docs/language.md#sub-slicing)),
        while a
        user struct may carry derived state beyond the view (`list`'s
        `capacity` the standing example) that only the type's author
        knows how to rebuild, so the overload is where that knowledge
        lives, and a direct `arr[a:b]`/`p[a:b]`, if it ever lands, is
        the sub-slicing item's recorded primitive extension, not a
        protocol overload. No syntax of its own: the `[a:b]` grammar
        and the primitive slice
        lowering shipped with the sub-slicing item, so the build
        dependency this strictly sat on is satisfied (a slicer
        overload body is typically one primitive sub-slice over the
        container's storage). The write path through the index
        brackets shipped with the accessor half exactly as predicted
        here, one `-> mut` return serving both sides of `=` over the
        shipped [`mut` returns](docs/language.md#mut-returns)
        (`list<T>::at`, usable as `lst[i] = v`), with the
        `@accessor("get")`/`("set")` pair form covering the write-path
        logic a raw-storage return cannot.
        Sequencing, not dependency, with the `for … in` protocol: the
        two share the desugar-to-overloads mechanism, whichever ships
        first establishes the pattern (builtin carve-outs, stdlib
        baseline overloads, `.mci` travel), and this one is the simpler
        pilot, a single call per bracket form with no iteration-state
        pair. Genuinely open: the slicer overload spelling the brackets
        desugar to, and whether the slicer stays a free-function set at
        all or follows indexing into a method annotation (the
        `@accessor` ship is precedent squarely against this item's
        premise); the slicer's return type, which is
        the overload author's freedom (a `list<int32>` slicer may return
        a genuine sub-list or a `slice<int32>` view; the sub-slicing
        item's lying-`capacity` concern becomes the author's
        responsibility, the protocol constrains nothing); `const`
        receivers (the triad settled `_at` as `mut self` only and
        overloads differing only in markers are banned, so whether
        `c[a:b]`, or a read-only `c[i]` spelling, has a target on a
        `const` container, via a
        distinct read spelling or not at all, is unresolved); and the
        bounds posture, presumably each overload's own contract with
        `_at`-style documented UB as the stdlib baseline. Collision and
        ambiguity posture is inherited wholesale from the open sets
        above, `@override` included
- [x] Subsumption ordering of rank-tied generic overloads — a rank-tied
      cohort (same tier, same specificity) is no longer automatically
      ambiguous: the cohort resolves to its unique MAXIMUM, the candidate
      whose parameter pattern maps into EVERY other member's under a
      one-way match (the other's type params are wildcards binding
      consistently, so repeated names count; the candidate's own params
      stay opaque; patterns are dealias-normalized, so an alias-spelled
      diagonal participates like its target spelling; `const` markers and
      return types are ignored; arity, the collecting flag, and `mut`
      positions must agree outright). The motivating case, whose argument
      IS the rule (USER RULING, explicitly confirmed 2026-07-12): a
      diagonal `fn point<T>::constructor(self, x: T, y: T)` beside the
      converting `fn point<T>::constructor<U>(self, x: U, y: U)` really
      is not ambiguous, because the first is the second with `U = T`,
      strictly more constrained. USER RULING on uniqueness: the winner is
      the unique maximum, not merely maximal
      (maximal-but-not-unique leaves no winner), so ambiguity remains
      exactly for incomparable patterns, two rank-tied partial
      specializations included. Constraints participate in the relation
      (USER RULING, choosing the deeper option over pattern-only):
      implication is judged per wildcard, type groups imply by subset,
      `extends` bounds by the nominal chain, either implies unbounded,
      group-vs-extends is incomparable, and an unconstrained parameter
      implies nothing; the recorded consequence is that a looser-bounded
      diagonal beside a tighter-bounded open pattern is incomparable and
      stays ambiguous (while same-pattern tighter-group-wins is
      unreachable: overlapping groups collide at declaration, and
      disjoint groups are never co-viable at one call). Tiers stay
      supreme: the tie-break runs within a rank-tied cohort only, so
      resolution reads viability, then rank tier, then specificity, then
      subsumption (pattern plus constraint implication), then ambiguity.
      It covers free functions and qualified `Type::method` sets alike
      (the trigger was the constructor pair above, on a generic struct).
      Shipped alongside a viability fix that had been manufacturing
      phantom ties: an adaptable integer literal no longer satisfies a
      bare type-param slot whose deduced binding is non-integer (mcc has
      no int-to-float literal adaptation), so the non-emittable candidate
      drops from the trial; that fix alone resolved the motivating
      `float64` case (the converting constructor wins outright), while
      subsumption is load-bearing for genuinely tied cases (an integer
      receiver: the diagonal wins). Deliberate v1 conservatisms, prose
      limits rather than commitments: a function-pointer pattern matches
      only its exact spelling (differently-spelled fn types are
      incomparable, keeping the ambiguity), array dimensions must spell
      equally, and the literal-viability policing covers bare type-param
      slots only. This SUPERSEDES the diagonal-beside-open-sibling
      ambiguity ruling recorded in the alias/builtin qualifiers item
      below, rewrites the tied-partials rationale there (incomparability
      under the ordering, not the absence of one), and gives the
      open-sets doctrine above one deliberate edge: an imported
      equal-rank but strictly more specialized candidate now wins what
      was formerly a tie, exactly the supplying-a-better-candidate
      behavior open sets intend. Implemented, see
      [rank-tied templates: subsumption](docs/language.md#rank-tied-templates-subsumption)
- [ ] `fn` types in overload viability and generic unification — close a
      pre-existing resolver gap the callback story sits behind: a
      concrete overload with a fn-typed parameter is never viable today
      (fn-typed arguments are invisible to the viability filter), and
      generic unification never recurses into a written fn signature,
      so `fn pick<T>(f: fn(T) -> T)` cannot infer `T`. Both halves are
      resolver-internal, no new syntax or types: viability learns to
      match a function value against a fn-typed slot, and unification
      walks a fn type's parameter and return patterns the way it
      already walks pointer depth and generic struct arguments. This
      gates any callback-taking generic API, the
      `sort<T>(items: slice<T>, cmp: fn(T, T) -> bool)` shape the
      [generic type aliases](#types-and-generics) item's `cmp<T>`
      comparator example implies; it depends on nothing new, and the
      shipped `@nonnull`- and `mut`/`const`-carrying function types
      neither fix nor worsen it, since their assignability checks live
      on the coerce path, not in viability or unification
- [ ] Methods / OOP — `fn <type>::<method>(...)` definitions
      keyed to a type, structs foremost (the explicit qualified-call
      foundation, the `recv.method(args)` dot-call sugar, the `S(args)`
      constructor sugar, method inheritance through `extends`,
      `@property` field-syntax access, and `@accessor` `[]` indexing have
      shipped, see the checked sub-items; the receiver is an ordinary parameter, not the
      raw `self: <struct>*` this line once sketched), including `@private`
      methods and the special
      constructor (call sugar shipped) / destructor (automatic stack
      cleanup shipped) below (the
      `for … in` protocol already dispatches
      by struct name to pave the way, though iteration itself is slated to
      move to the overload-set protocol family of the open overload sets item
      above). Method calls through raw pointers and by-value receivers
      are direct, statically-bound calls; dynamic dispatch rides the fat
      `const`/`mut` views below, base-typed or interface-typed (the
      dispatch table lives in the view, never in the object, so a struct
      never carries hidden state), and code that never forms a view pays
      nothing. A settled scope boundary
      from the open overload
      sets item above, since NARROWED by the shipped `@accessor`
      sub-item below (indexing moved to methods, `c[i]` dispatching the
      receiver type's accessor family): protocols (formatting,
      iteration, slicing) are free-function
      overload sets, not methods; methods must not become the privileged
      mechanism for those protocols, and this item is checked against that
      family rather than the reverse:
  ```c
  struct point { x: int32; y: int32; }
  fn point::length2(const self: point) -> int32 { ... }   // shipped: var.length2() or point::length2(var)
  @private fn point::helper(mut self: point) { ... }        // shipped: @private on the qualified name
  fn point::constructor(mut self: point, x: int32, y: int32) { ... }   // shipped: let var = point(3, 4)
  ```

  - [x] qualified `fn Type::method` definitions + explicit `Type::method(...)`
        calls — the explicit-call foundation. The qualified name is a single
        string (`"point::magnitude"`) threaded through Func/Call, the
        registration key, and the LLVM symbol (`@"point::magnitude"`), so
        registration, overloading (`point::area` and `circle::area` are distinct
        names; a `Type::method` set overloads by argument like any name),
        `@private`, and `@override` all work unchanged on the string. The
        receiver is an ordinary already-shipped `mut` / `const` / by-value
        parameter, so this slice added no receiver-kind machinery. Deliberate
        scope ruling: `Type::` is purely a namespace, enforcing no `self`
        convention (no required receiver, name, or first-param type); the one
        check is that the qualifier names a declared type (this slice ruled
        struct-only, an enum, alias, builtin, or undeclared qualifier the
        error; the alias/builtin qualifiers sub-item below has since AMENDED
        that ruling, legalizing aliases and builtins, while enums and
        undeclared names remain the error — and ADDED a second check: a
        bare qualifier naming a GENERIC type must annotate its type
        parameters; this slice's acceptance of bare `fn point::m` on a
        generic struct was an unvalidated accident, since reversed by
        USER RULING there), the `self`-conventions
        having become load-bearing (positionally — the receiver is the
        first argument) with the since-shipped call sugar below, which
        stayed a dumb desugar enforcing no convention. Parser:
        `fn Type::method` is claimed in definition position (this slice parsed
        it before type-params; generic-struct methods `fn Type<T>::m` followed
        as the next slice below, since shipped, which reordered the parse);
        in expression position `Type::member(` is claimed as a qualified call
        (the same shape-claim as the `ok(` / `error(` builtins), while
        `Enum::Member` not followed by `(` stays enum member access, no
        regression. Object-file symbol mangling for a precompiled stdlib stays
        with the separate
        [namespaced exported symbols](#tooling-and-c-interop) item; the shipped
        `@"point::method"` symbol suits the source-merged / JIT model
  - [x] generic-struct methods — `fn Type<T>::method(...)`, a method on a
        generic struct. The struct's type params (written `<T>` before the
        `::`) are in scope in the method signature and body; a method may also
        declare its own type params after `::method`
        (`fn box<T>::map<U>(const self: box<T>, ...) -> box<U>`), the two lists
        merging into one uniform generic template — a method type param
        shadowing a struct type param (same name in both lists) is a declare-time
        error. The receiver names its type args explicitly
        (`mut self: point<T>`); there is deliberately no
        bare-`point`-means-`point<T>` sugar (a bare `self: point` keeps the
        existing missing-type-argument error), and qualified calls infer the
        type args from the receiver and value args (`point::magnitude(p)` with
        `p: point<float64>`) — this slice allowed no explicit type args at
        the call, since extended by the shipped explicit-type-args sub-item
        below (the qualifier may now spell the instantiation). The qualified
        name stays a single string registered as an ordinary generic template,
        so monomorphization, argument-driven inference, overloading, `@private`,
        and `@override` all ride the existing generic machinery — codegen needed
        no change; the only edits were a parser reorder (the struct's type-param
        list before `::`, the method's own after) and an `interface.py` fix so a
        method whose signature never names its struct still pulls it into the
        `.mci` stub
    - [x] method specialization — `fn Type<Concrete>::method(...)`, a concrete
          method body for ONE instantiation of a generic struct, coexisting with
          the generic `fn Type<T>::method(...)` and OUTRANKING it for a matching
          receiver (a `point<float64>` receiver binds `fn point<float64>::magnitude`;
          every other instantiation falls to the generic `fn point<T>::magnitude`).
          Dispatch is free: the specialization registers as an ordinary CONCRETE
          overload of the qualified name `point::magnitude` (empty type_params), and
          the shipped concrete-beats-generic overload ranking picks it — no new
          dispatch machinery. The work was CLASSIFICATION: a pre-`::` `<...>` can
          DECLARE type params (`<T>`, generic method) or SUPPLY concrete args
          (`<float64>`, specialization), and since primitive type names are plain
          identifiers this cannot be decided at parse time. The parser holds the
          undecorated pre-`::` list verbatim (a new `Func.struct_type_args`) and
          codegen classifies it against the registered type environment (a new
          `normalize_struct_method_args` pass): all fresh type-param names → a
          generic method (struct params prepend the method's own into one template);
          all concrete types → a specialization (bind the struct's param names to the
          concrete args, substitute through the signature, register as a concrete
          overload). USER RULING (option B) — because classification runs at codegen,
          ANY concrete type specializes: builtins, USER STRUCTS, and structured
          `point<int32>` / `int32*` alike, not just primitives. A DECORATED pre-`::`
          list (`<T: a|b>`, `<T extends S>`, `<T = D>`) is unambiguously a parameter
          declaration → generic method, still merged at parse time (unchanged). A
          LONE specialization (no generic base of the same name) is legal — just a
          concrete overload
      - [x] partial specialization — a MIX of concrete and type-parameter
            struct args (`fn pair<int32, U>::m`) now classifies: the concrete
            positions bind, `U` stays a free method type param, and the
            template matches only `pair<int32, X>` receivers. Classification
            stayed in codegen (a mixed arm in `normalize_struct_method_args`);
            dispatch is the pre-existing overload ranking (full specialization
            beats partial beats fully generic), and two rank-tied partials
            remain the standard ambiguity error (USER RULING kept that
            outcome; its recorded rationale, "no C++-style partial
            ordering", is superseded by the shipped subsumption ordering
            above: mcc now DOES partially order a rank-tied cohort, and
            tied partials like `pair<int32, U>` vs `pair<T, int8>` stay
            ambiguous because each holds a concrete type where the other
            holds a wildcard, incomparable patterns the ordering never
            rescues). BOUNDED partials shipped in the same slice
            (USER RULING): `fn pair<int32, U: int8|int16>::m` works — the
            parser's speculative pre-`::` capture now carries decorations
            (`: group`, `extends`, `= default`) into codegen classification,
            a decoration on a concrete position rejected; this also closed a
            silent-wrong-code trap where a decorated list took the parse-time
            generic path and `int32` became a type parameter NAMED "int32".
            Ranking nuance, now documented: tier beats specificity — a bounded
            generic (tier 1) outranks an UNBOUNDED partial (tier 0),
            pre-existing semantics — while a bounded partial (tier 1) beats a
            bounded generic on specificity. USER RULING on capture: a fresh
            type-param name colliding with a struct parameter name bound to a
            concrete arg is a compile error. Bonus fix: a parser speculation
            bug where backtracking left `>>` token splits applied, corrupting
            nested-generic declarations
    - [x] explicit type args at a `::` call —
          `point<float64>::method(args)`, the qualifier of a qualified call
          spelling the receiver instantiation (previously
          `unexpected token '::'`). USER RULING (2026-07-13), closing this
          item's deliberately deferred (a)/(b)/(c) trio: option (a) — the
          list is the STRUCT FRAME's only. The written qualifier resolves
          ONCE as an ordinary type use through `lang_type`, so arity checks,
          trailing-default fill, enclosing type parameters at
          monomorphization (a `point<T>` spelled inside a generic body
          resolves at the live `T`), and generic-alias substitution with
          permutation honored (`swap<int32, float64>::first(p)` over
          `type swap<X, Y> = pair<Y, X>` pins `pair<float64, int32>`) all
          come free; the resolved instantiation then PINS dispatch by a
          per-candidate frame match against each member's classified
          qualifier annotation (`Func.qualifier_args`, the `rebase_member`
          discipline applied at the call site): a fresh position seeds that
          parameter's binding, a concrete (specialized) position must agree
          or the member does not apply. So full AND partial specializations
          DISPATCH under explicit args, a no-receiver member becomes
          callable at a chosen instantiation (`point<float64>::origin()`),
          builtin generic families take the form (`slice<int32>::first(s)`),
          and a pin no member matches reports it (`'box::get' has no member
          for box<float64>: the qualifier's type arguments pin the receiver
          instantiation`; a set-level miss appends `the qualifier pins ...`
          too). Method-own type params stay inference-only at BOTH
          spellings: a second list after the member name
          (`point<float64>::map<int32>(...)`) is a parse error mirroring the
          dot-call limit — so option (a) governs mixed methods
          (`fn box<T>::wrap<U>`: the list fixes `T`, `U` still infers) and
          option (c)'s second list is closed. Provenance, superseding this
          item's planned sketch: the "minimal parser-only" approach once
          recorded here (thread the list positionally into the merged
          type-param list, struct params leading) PREDATED shipped method
          specializations and exploration proved it WRONG — a specialization
          registers as a concrete overload with an EMPTY type-param list, so
          positional threading would have silently SKIPPED every
          specialization the pin should reach; the shipped qualifier-frame
          binding replaces it. SECOND USER RULING: a bare alias of a
          COMPLETE type INJECTS the instantiation it names — `pointf::sum(q)`
          IS `point<float64>::sum(q)` — deliberately flipping a
          working-but-unsound program: a cross-instantiation receiver
          through a complete alias (`pointf::get(q)` with `q: point<int32>`)
          previously compiled via name-only chasing and is now the
          receiver-mismatch error; a generic/incomplete alias still
          canonicalizes name-only and infers. This satisfies the
          type-arg-injection deferral recorded at the alias/builtin
          qualifiers item below. Motivating use, now real: constructor and
          destructor CHAINING — the pair's only callable spelling is
          qualified (the corrective slice at the constructor item below),
          and `point<T>::constructor(self, x as T, y as T)` inside a
          converting constructor (or `inner<T>::destructor(self.i)` inside
          an owner's destructor) now monomorphizes correctly through the
          enclosing frame. Drive-bys shipped alongside: `try_type_args` now
          undoes its in-place `>>` angle splits on backtrack (a latent
          hazard of the speculative parse, the same family as the partial
          specialization slice's fix above), and inherited-member clones
          carry a qualifier annotation respelled over the deriving struct's
          parameters, so pins reach inherited methods. Implemented, see
          [Explicit type arguments at a qualified call](docs/language.md#explicit-type-arguments-at-a-qualified-call)
    - [ ] bare-`point` receiver sugar — inside `fn point<T>::method(...)` a
          bare `point` (no type args) means `point<T>`, the qualifier struct
          applied to its own type params — a sugar for type USES in the
          signature and body only; the qualifier itself stays annotated
          (bare `fn point::method` remains the annotation-required error
          per the alias/builtin qualifiers item below, which this sugar
          does not relax). Those struct params are the leading
          entries of the method's merged type-param list, in scope at parse time
          in `parse_function`, so the mechanism is a parse-time rewrite (each
          bare `TypeRef` whose name is the qualifier and which carries no type
          args gets the struct's params attached) with no codegen change:
          `lang_type` then resolves it through the existing `type_bindings` path
          exactly as the explicit `self: point<T>` form does today. USER RULING
          on scope — the target is the WHOLE METHOD BODY, not just the
          signature: bare `point` expands across all params and the return type
          AND in body type expressions (`let q: point`, `sizeof(point)`, a cast
          `x as point`). This is broader than the signature-only spike (which
          leaves a `self: point` works / `let r: point` errors seam); whole-body
          needs either a body `TypeRef` walk or a resolution-time rewrite that
          carries the current-method context. Explicit `self: point<T>` stays
          valid alongside the sugar (additive — existing code depends on it).
          Shipping note: this FLIPS a shipped ruling — `tests/test_methods.py`
          has `test_bare_generic_receiver_still_requires_type_args` asserting the
          arity error, and `docs/language.md` (the "#### Methods on a generic
          struct" area) states there is no such sugar; both must be updated when
          it lands. Independent of the since-shipped explicit-type-args item
          above (parser-only here, no shared prerequisite — that item
          shipping first confirms the recorded either-can-ship-first claim)
  - [x] methods on type aliases and builtin types — the qualifier left of
        `::` may be ANY nameable type, not just a struct. USER RULING, the
        governing principle: methods register to a TYPE; an alias is just an
        alias, so `fn pointf::magnitude` with `type pointf = point<float64>`
        is the same declaration as `fn point<float64>::magnitude`, and vice
        versa. Alias qualifiers canonicalize early (a chase-and-rewrite in
        `gen_program` before classification, `resolve_method_qualifier`) to
        the target's family with the struct type args injected, so the alias
        spelling IS a full specialization: it outranks the generic, collides
        with a duplicate `fn point<float64>::magnitude`, `@override` matches
        across spellings, and the `.mci` round-trips. Chains, permuting
        generic aliases (`type swap<X, Y> = pair<Y, X>` canonicalizes to a
        partial), and defaulted alias args all compose via substitution;
        inert alias params (`type always<T> = point<float64>`) vanish per
        alias transparency. Call sites as this slice shipped canonicalized
        name-only (`pointf::m(p)` as `point::m(p)`, the type-arg injection
        deferred to the explicit-type-args refinement above — since SHIPPED
        and SATISFIED there, USER RULING: a bare alias of a complete type
        now INJECTS its instantiation, `pointf::m(p)` IS
        `point<float64>::m(p)`, and only a generic/incomplete alias still
        chases name-only and infers). Builtin
        qualifiers (USER RULING: any type) are legal the same way:
        `fn char::lower`, `fn int32::m`, and aliases to builtins
        (`type myint = int32;` then `fn myint::m`) register one family per
        builtin name; generic builtins take fresh names
        (`fn slice<T>::first` works) but CANNOT be specialized
        (`cannot specialize builtin type 'slice'; spell the receiver type
        in the method's signature instead`). A GENERIC type as a BARE
        qualifier is a compile error (USER RULING, 2026-07-12):
        `fn pf::magnitude` on a generic alias is invalid just as
        `fn point::magnitude` on a generic struct is — the qualifier must
        annotate the type parameter(s), either a fresh param
        (`fn point<T>::mk`) or a concrete arg (`fn point<float64>::mk`);
        the method's OWN post-name type params do not satisfy it, a chain
        through a non-generic alias to a generic struct errors with the
        struct wording, and a FULLY-defaulted generic counts as complete
        (defaults fill in, mirroring bare type uses; partially-defaulted
        stays the error) — while a complete type stays bare: plain
        structs, builtins, and aliases to complete types (`fn pointf::m`
        IS `fn point<float64>::m`, per the governing ruling above). Call
        sites are untouched: bare `point::m(p)` / alias-name calls still
        chase by name and infer from the receiver — the pure-namespace
        doctrine still governs WHICH family a method joins and every call
        site; only the declaration qualifier of a generic type must be
        annotated. Provenance: as first shipped (unreleased) this slice
        instead ruled the bare generic-alias qualifier a namespace
        passthrough symmetric with bare `fn point::m` — a ruling elicited
        on the false premise that bare struct qualifiers on generic
        structs were deliberate design, when that was an unvalidated
        accident of the foundation slice; the user explicitly reversed
        BOTH in a corrective slice, and the CHANGELOG was amended in
        place since the passthrough never released. The corrective sweep
        covered struct templates and generic aliases (builtin
        `pair`/`iterator` ARE struct templates, so they error); reserved
        builtin generics (a bare `fn slice::m`) were not swept — a known
        seam, noted without a committed follow-up. Fallout hardening:
        `.mci` stubs re-spell a specialization's annotated qualifier
        (`fn box<float64>::tag(...)`) so stubs remain valid source under
        the rule, and types named only in the qualifier now travel into
        the stub (previously a specialization could silently re-classify
        as generic on re-import; a new `Func.spec_qualifier_args` carries
        this). USER RULING on the diagonal:
        `type diag<T> = pair<T, T>` with `fn diag<U>::m` dedupes the
        repeated fresh name into a template matching only `pair<X, X>`,
        unification enforcing consistency (a `pair<int32, float64>`
        receiver is the conflicting-types error). As first shipped, this
        slice also ruled a diagonal beside an open generic sibling on an
        agreeing receiver the standard ambiguity error; that ruling is
        SUPERSEDED (USER RULING, explicitly confirmed 2026-07-12) by the
        subsumption ordering above, under which the diagonal wins: its
        pattern is the open sibling's with the wildcards identified,
        strictly more constrained (the constructor argument that
        motivated the ordering is exactly this rule), and the
        dealias-normalization this slice built is what lets the
        alias-spelled diagonal participate in it.
        Bonus: generic-alias spellings in signatures are now transparent to
        template inference (`dealias_pattern` in unify / shape_matches), a
        pre-existing gap the diagonal ruling forced closed. This SUBSUMED
        the epic's deferred non-struct receivers item on the definition
        side; the `.method()` dot sugar on scalars (`'C'.lower()` over
        the since-shipped `std/char`) shipped with
        the method-call sugar item below, where its receiver-kind notes now
        live. Still errors: enums as qualifiers (enum receivers wait for
        [nominal enums](#types-and-generics), where a method name must not
        collide with a member name, both spelling `Name::x`), undeclared
        names, structured alias targets (`type ip = int32*`; pointer,
        array, and fn types have no `Name::` spelling, future work only if
        ever wanted), and builtin specialization (above). AMENDS the
        foundation slice's qualifier ruling: aliases and builtins are now
        legal qualifiers; enums and undeclared names remain the error
  - [x] method-call sugar — `recv.method(args)` desugars to
        `Type::method(recv, args)`: a dot-shaped call whose receiver type
        registers a `Type::method` family rewrites to the qualified
        spelling, the receiver passing VERBATIM as the first argument, so
        overload resolution (specializations, partials, subsumption),
        `mut`-receiver legality, evaluate-once addressing, and every
        diagnostic are the desugared call's own. Adopted standing
        recommendations, shipped as recorded: fields shadow methods (a
        fn-typed field named `m` keeps the field-call behavior, the
        method reachable as `Type::m(s, args)`; only a call shape with
        NEITHER gets the new `struct '...' has no field or method '...'`
        error, and a bare member access `p.m` keeps the exact field
        diagnostics — there are no bound-method values; since AMENDED
        by the shipped `@property` item below, whose annotation makes a
        bare access CALL the method, the one field-shaped method
        access), and `->` stays
        fields-only (the language keeps C's `.`/`->` distinction for
        FIELDS, so `p->x` and `p.method()` coexist on one pointer, the
        Go/Rust/Swift receiver-adaptation model; `q->m()` where `m` is
        not a field errors as before). A pointer receiver auto-derefs
        exactly one hop: `q.m()` on an `S*` is `S::m(*q, ...)` — shipped
        as an explicit dereference riding the deref machinery
        (`-Wunchecked-dereference` included) rather than as a
        pointer-decay instance — and an `S**` receiver stays an error.
        Builtin, alias, and slice receivers dispatch their canonical
        families: `'C'.lower()` over the shipped `std/char` works, this
        item's motivating stdlib case, with the scalar receiver-kind
        notes holding as recorded (no lvalue sits behind a literal
        receiver, so `std/char` declares `const self: char` throughout
        and a `mut self` method is never callable on `'C'`). The same
        reasoning generalizes: an rvalue receiver evaluates once into a
        hidden CONST local, so a `mut self` method on a temporary is an
        error (`mk().bump()` rejects — the mutation would vanish with
        the temporary), while a `mut`-returning receiver re-lends its
        carried lvalue (`b.ref().grow()` writes the caller's storage),
        and a `mut`-returning dot-call is an lvalue on every surface:
        assignment (`l.at(i) = v`), compound assignment, chained store
        targets, and the mut-return formation walk. Explicit type
        arguments at a dot call do not parse (`p.m<int32>(...)`) —
        method type params stay inference-only at both spellings, the
        pattern since SET by the shipped explicit-type-args-at-`::` item
        above (USER RULING, option (a): a type-arg list belongs to the
        struct frame only, and a dot receiver already fixes that frame,
        so the dot spelling takes no list at all).
        Sugared bodies round-trip `.mci` verbatim, and the explicit
        `Type::method(...)` spelling stays valid alongside. AMENDED by
        the qualified-only corrective slice (the USER RULING recorded
        verbatim at the constructor item below): the two semantic names
        are carved out of the sugar — `p.constructor(args)` and
        `p.destructor()` are compile errors teaching the qualified form
        (`'destructor' cannot be called with method syntax; use
        point::destructor(p)`), so the dot-to-`Type::` equivalence
        holds for every method name BUT those two; a genuine FIELD of
        either name keeps the field-first behavior above (fields shadow
        methods before the ban is judged), receivers of every kind are
        covered (struct, union, builtin, alias, pointer — the pointer
        suggestion spells the one-hop deref), and a spilled rvalue
        receiver's suggestion renders `value`, never the hidden local's
        name. Provenance:
        as planned, this item staged a pre-receiver-kinds
        `var->method(...)` form, `.` arriving only once the receiver
        kinds landed; that staging is SUPERSEDED — the sugar shipped
        directly as `.` over the ordinary shipped
        `mut`/`const`/by-value receiver parameters, no receiver-kind
        machinery added (the formal receiver-kind item below stays
        open), and `->` was never a method spelling. Implemented, see
        [Calling methods: dot syntax](docs/language.md#calling-methods-dot-syntax)
  - [x] constructor call sugar — a method named `constructor` makes its
        type callable: `let s = S(args);` is exactly
        `let s: S; S::constructor(s, args);`, a DUMB DESUGAR by adopted
        recommendation — `Type::` still enforces no `self` convention, so
        a `const self` or by-value `self` "constructor" compiles and
        initializes nothing, and a non-void constructor's return value is
        discarded — with overload resolution, receiver legality, privacy,
        and every diagnostic the family call's own. `let p = S(args);`
        ELIDES into the let slot (one construction, no temporary, no
        copy: a `mut self` constructor writes `p`'s own storage), the
        load-bearing property the since-shipped destructor item nested
        below leans on (RAII wants exactly one construction site to hang
        the deferred destructor on, and the shipped automatic call
        attaches exactly there), and the sugar works in any expression position
        (`f(point<int32>(1, 2))`, returns, nested constructor
        arguments). The head follows type-use spelling: explicit type
        arguments (`point<float64>(1, 1)`), a non-generic type bare, a
        FULLY-defaulted generic bare (defaults fill in, the typed path —
        a deliberate deviation), a plain alias transparently
        (`pointf(1, 2)`); a GENERIC alias used bare keeps the type-use
        arity error, bareness judged on the written spelling. Four
        USER RULINGS: (1) name resolution is unchanged — a same-named
        function, variable, constant, or `@static` wins UNCONDITIONALLY
        over the constructor interpretation (the sugar hooks the call
        path's last resort, sitting exactly where the call was
        previously `undefined function`); (2) a BARE GENERIC head infers
        its type params from the constructor arguments
        (`point(1.5, 2.5)`: the receiver enters the family's ordinary
        overload resolution as a placeholder and the winner's first
        parameter fixes the constructed type) — OVERRULING the
        explorer's recommendation to error, on consistency grounds: call
        sites are bare-and-infer throughout the language, the
        annotate-the-generic-qualifier rule of the alias/builtin item
        above is declaration-side only; (3) a type with NO declared
        `constructor` family gets a bespoke error
        (`struct 'point' has no constructor`), NEVER a cast, and the
        `S{...}` struct literal remains the no-constructor spelling;
        (4) ANY type with a declared constructor family is constructible,
        builtins and aliases-to-builtins included (`char(65)` calls a
        declared `fn char::constructor`; an undeclared `int32(5)` stays
        the no-constructor error) — also OVERRULING the explorer's
        recommendation. Naming settled by shipping: the pair is
        `constructor`/`destructor` (the once-open `init`/`destroy`
        alternative closes). AMENDED by a corrective slice: the pair is
        QUALIFIED-ONLY (USER RULING, verbatim: "for a type T calling
        t.destructor() or t.constructor(args) directly should be
        forbidden, they can only be called by their fully qualified
        form T::constructor(t, args) and T::destructor(t), which should
        be mainly used for chaining constructors and destructors") —
        the dot spellings are compile errors, `T::constructor(t, args)`
        / `T::destructor(t)` the only callable spellings, kept mainly
        for chaining a base's from a derived body; the `S(args)` sugar
        and the automatic destructor are unaffected (the synthesized
        auto-defer was always the qualified call over the hidden
        rebind), and the method-call sugar above records the dot-side
        carve-out. Overloaded constructors (empty / copy /
        converting / from raw parts) ride the shipped
        [function overloading](docs/language.md#function-overloading);
        the diagonal-beside-converting constructor pair is what
        motivated the shipped subsumption ordering above. Provenance:
        this item as planned bundled heap `new <struct>(...)`
        construction and the implicit destructor-defer RAII with the
        stack form; the stack-value `S(args)` half shipped (since
        extended by the implicit empty constructor nested below), the
        destructor half has since shipped too (the [x] item nested
        below), and only the heap `new` half stays open. Adoption
        direction: the stdlib's containers are picking up declared
        constructor families over this sugar (in progress), and with the
        destructor shipped, `list` and `string` are the natural adopters
        of `T::destructor` — an adoption must migrate the manual
        `list_destroy`/`string_destroy` call sites in the same change,
        or every constructor-sugar let double-frees.
        Implemented, see
        [Constructors](docs/language.md#constructors):
    - [x] implicit empty constructors — every type has one: `T()` with no
          arguments is exactly `let t: T;`, the slot default-initialized
          as the bare declaration is (declared field defaults apply,
          anything else starts uninitialized). USER SPEC, recorded
          verbatim: "types have an implicit empty constructor" —
          `char()` ≡ `let c: char;`, `point<float64>()` ≡
          `let p: point<float64>;`, a derived `pointf()` ≡
          `let p: pointf;` — for every head the sugar accepts (builtins,
          structs, unions, aliases, fully-defaulted generics bare),
          expression position and the let-elision included, and, un-C++,
          declaring constructors does NOT suppress it: a family with only
          argument-taking members leaves `T()` default-initializing, no
          family instance emitted or called. USER RULING (reinforced): a
          defined empty constructor
          `fn my_type::constructor(mut self: my_type)` claims
          `my_type()` — the defined body runs, no implicit one is
          created. Shipped as an arity-judged claim over the visible
          merged family: a member that accepts exactly the receiver (a
          `(mut self)`-only constructor, or a collecting one whose fixed
          prefix is only the receiver) claims the zero-argument call and
          resolves normally — inherited members included, per the
          method-inheritance item below (an inherited `(mut self)`-only
          base constructor claims `derived()`) — and resolution errors
          then PROPAGATE rather than falling back, so the implicit form
          can never mask a broken declared constructor and no ambiguity
          between the two can arise. A literal zero-PARAMETER member
          (`fn s::constructor()`, legal since `Type::` enforces no
          `self` convention) can never accept the hidden receiver, so it
          never claims. The edges are all unchanged: calls WITH
          arguments keep their behavior (`int32(5)` stays the
          no-constructor error), same-named functions/variables/
          constants/`@static` still win unconditionally, a bare generic
          head keeps the cannot-infer error (no arguments to infer
          from), a cross-module `@private` zero-arg member does not
          claim, and `void()` errs
          (`cannot construct a void value`). Implemented, see
          [Constructors](docs/language.md#constructors)
    - [x] destructor — `fn <struct>::destructor(mut self: <struct>)`, the
          cleanup counterpart: releases what the constructor acquired.
          USER SPEC, recorded verbatim: "if a type T declares a
          destructor, `let t = T(args)` automatically defers
          `t.destructor()` at the end of the scope" (the quote's dot
          spelling predates the qualified-only corrective ruling at the
          parent item — that spelling is now a compile error, and the
          synthesized call was always the qualified `T::destructor`
          over the hidden rebind, so the machinery is unaffected), with the
          user-authored desugar equivalence `let p = point<float64>();`
          ≡ `let p: point<float64>; point<float64>::constructor(p);
          defer point<float64>::destructor(p);` — RAII over the existing
          [`defer`](docs/language.md#defer) machinery, hung off the
          shipped sugar above exactly as planned: the let-elision gives
          each construction ONE slot, and that slot is where the defer
          attaches. The trigger surface is SPEC-LITERAL (USER RULING):
          only the constructor-sugar let (`let t = T(args)` /
          `let t = T()`, a declared or implicit empty constructor, the
          family declared or inherited) schedules; manual construction,
          struct-literal lets, copies, and assignments schedule
          nothing — the documented opt-out hatches — and an any-coercing
          annotation binds a copy, so it schedules nothing either. The
          scheduled call shares the defer stack verbatim (adopted
          recommendation): LIFO with explicit defers, per loop
          iteration, unwinding on early return/break/continue/
          try-propagation, `@noreturn` exits skip. Resolution is the
          dumb desugar again (adopted): a visible family means the
          qualified receiver-only call is synthesized, and every
          diagnostic (arity, overloads, cross-module `@private`) is the
          family call's own at the let's line, propagated and never
          masked. Sharp edges, each a USER RULING: (1) a manual
          `T::destructor(p)` beside the automatic call is UNDEFINED
          BEHAVIOR, a C double-free — no suppression, no warning
          (originally recorded over the dot spelling; the corrective
          qualified-only slice made `p.destructor()` a compile error,
          so the stance is unchanged in substance and its reachable
          spelling is now qualified-only);
          (2) `return t` / `emit t` of the whole auto-destructed local
          is a HARD ERROR (the copy would carry already-destroyed
          state) — hatches: return the constructor expression directly
          (an expression-position temporary owns no automatic cleanup)
          or construct manually and own the cleanup; field escapes are
          not caught, and emitting a local declared OUTSIDE the block
          expression survives the emit and stays a legal copy (the
          RETURN half of this error now lifts under an `-> own`
          signature, the shipped move-out sub-item below; `emit`
          keeps it);
          (3) a CONST-viewed constructor let IS destroyed — destruction
          is scope teardown, not user mutation (the C++ stance,
          OVERRULING the explorer's error-first recommendation): the
          synthesized call alone bypasses the const view, a user-written
          destructor call on const keeps the mut-receiver error, and the
          bypass and shadow-safety are ONE mechanism, a hidden
          const-stripped rebinding of the slot (`0dtor{n}`) immune to
          name and field shadowing; (4) implicit-empty construction
          stays UNINITIALIZED like `let t: T;` ("keep stack luck"), so a
          destructor may observe uninitialized fields. Base cleanup
          chains MANUALLY (USER RULING), mirroring constructor chaining:
          a derived destructor ends with `point::destructor(self);` via
          the shipped receiver upcast, and a derived type with NO
          destructor of its own inherits the base's through the merged
          family, the automatic call resolving it — this SUPERSEDES the
          auto-run/order design point this item used to own. Destruction
          still follows the dispatch boundary below: the automatic call
          is exact by construction (a constructed stack value's type is
          statically known) and a call through a raw base-typed `T*`
          stays static, so owning a derived value through a raw base
          pointer is not a blessed pattern; whether a fat base view's
          table reserves a destructor slot remains the polymorphic base
          views item's open point below. Copies are bitwise and alias
          (the C stance, documented — two views naming one resource,
          only the constructed let destroyed); globals, `@static`s,
          parameters, heap values, and expression-position temporaries
          are never destroyed automatically (heap destruction travels
          with the `new` sugar sibling below). `destructor` was an
          unclaimed method name, now semantic: any existing family under
          it gains the automatic call. Implemented, see
          [Destructors](docs/language.md#destructors):
      - [x] move-out returns — `-> own T` and the `move(v)` assertion:
            a function declared `fn make() -> own T` hands its caller
            an owned value, lifting the whole-value RETURN hard error
            exactly there. Returning the auto-destructed local cancels
            its scheduled destructor ON THAT PATH ONLY (other exits
            still destroy it) and the caller's let ADOPTS the
            obligation, scheduling `T::destructor` exactly like a
            constructor-sugar let: both halves of this item's
            cancel-and-transfer design, shipped together. `own` is a
            keyword flag on the declaration beside `mut` (mutually
            exclusive: mut lends a view, own hands over a value; no
            ABI change), a no-op on a destructor-less type (generic
            `-> own T` stays writable), rides `.mci` stubs (a
            prototype mismatch rejects like a mut mismatch), and is
            barred on `@extern`, `@asm`, and `@property`/`@accessor`.
            The formation rule is STRICT (USER RULING, chosen over the
            permissive alternative): an unmarked return must visibly
            hold the obligation it hands over (the constructed local,
            a fresh constructor expression, or a chained own call, a
            bare `try` unwrap of one included); any plain copy needs
            the explicit `move(v)` assertion, a builtin-shaped
            `fn move<T>(v: T) -> T` claimed by call shape exactly like
            `ok(`/`error(` (no keyword reserved for `move`; `own` IS a
            keyword), legal only in the return value of an own
            function, around the whole value (`return move(v);`) or on
            the ok payload (`return ok(move(v));`), and rejected
            anywhere else ("no transfer target here"); a wrong
            `move()` is the recorded aliasing double-free UB, made
            visible instead of silent. Result composition shipped in
            v1 (USER RULING, "support now"): ownership rides the ok
            payload, so `return ok(local)` transfers,
            `return error(...)` destroys normally, and
            `let s = try f();` and the except-let adopt the unwrapped
            payload (the handler's emitted fallback rides the same
            schedule); an error-only `own result<E>` rejects at the
            declaration. Every other consumption is INERT, the
            expression-temporary C stance (documented leaks): discard,
            argument position, assignment, chaining, and
            `try f() ?? fallback`. Also closed a shipped soundness
            hole in EVERY function: `return ok(local)` of an
            auto-destructed local is now the same hard error as the
            bare `return local` (the result wrap no longer smuggles a
            destroyed copy out), and the bare error gained a
            "declare `-> own`" hint. `emit` keeps its whole-value hard
            error, deliberately unlifted (a block expression has no
            signature to carry the marker). With the caller adoption
            this opens the
            [string-valued f-strings](#strings-and-formatting) gate
            for let position only; that item records the
            argument-position temporary gap that remains. Implemented,
            see
            [Move-out returns](docs/language.md#move-out-returns-own):
        - [x] fn-pointer-type `own` parity — `fn(int32) -> own res`
              now parses and carries an `ownret` bit on the function
              type, spelled into the type name. Unlike `mut` (a
              calling convention), `own` is pure policy: NO ABI/LLVM
              change. The bit is fed from a new `own_ret` registry at
              the same four sites as `mut_ret`, so a function VALUE
              derives it from its declaration and an inferred
              `let factory = make;` adopts. Indirect calls through such
              a value vouch for adoption — a typed factory local, a
              field-held callback, and the inferred let all schedule
              the caller's destructor (`known_own_call` reads the
              type's `ownret` bit instead of refusing). Because `own`
              is a contract, not a convention, implicit retyping across
              the marker rejects in BOTH directions (dropping it leaks
              the handed-over obligation, fabricating it destroys a
              value never handed over), with an explicit `as` cast as
              the hatch — a deliberate divergence from how `mut`
              fn-type mismatches are framed. Rides `.mci` stubs for
              free (`fn take(cb: fn(int32) -> own res) -> int32;`)
        - [ ] `-Wdiscarded-own` — warn when an own call's obligation
              is dropped (discard, argument position, assignment), the
              diagnostic direction the documented-leak stance above
              points at; `-Wdestructor-copy` below is its sibling
      - [ ] `-Wdestructor-copy` — warn on a bitwise copy of a value
            whose type declares a destructor (two views naming one
            resource, C's aliasing problem), the direction the shipped
            copies-are-not-tracked stance records; the shipped
            `move(v)` assertion is the sanctioned relinquishing
            spelling such a warning would exempt
    - [ ] `new <struct>(...)` sugar — heap construction: desugars to a
          block that allocates with `new<<struct>>()`, runs the shipped
          constructor family on the allocation, and emits the pointer
          (the constructor counterpart to the
          [`new T { ... }`](#structs-arrays-and-data-layout) literal
          sugar):
      ```c
      let var = new point(3, 4);
      // desugars to
      let var = {
          let tmp = new<struct point>();
          point::constructor(tmp, 3, 4);
          emit tmp;
      };
      ```
          The family call is the shipped desugar's own; the design point
          the shipped receiver shapes add is that `tmp` is a nullable
          `point*` entering a `mut self` slot, which the shipped
          [pointer decay](docs/language.md#pointer-decay-into-constmut-parameters)
          admits only proven non-null — the emitted block guards or
          asserts (`tmp!`) at the allocation, or leans on a future
          non-null-returning `new`. The destructor half is now concrete:
          the shipped automatic destructor (the [x] item above) is
          stack-lets-only by ruling, so a heap construction owns its
          cleanup explicitly — the destructor runs before the memory is
          freed, the delete-shaped counterpart this item designs — and
          expression-position temporary lifetimes (`f(T(args))` owns no
          automatic cleanup in the shipped v1) are deferred here with it
  - [x] method inheritance through `extends` — a derived struct exposes its
        base chain's method families, constructors included: a family call
        on the derived type (dot sugar or the qualified spelling) resolves
        over the MERGED set of its own members and every base hop's, the
        latter entering as resolution-only clones REBASED at the declared
        base instantiation — on `pointf extends point<float64>`, the
        inherited diagonal `fn point<T>::constructor` is a concrete
        `(float64, float64)` member, so `pointf(1.0, 1.0)` prefers it over
        a derived generic while `pointf(1, 1)` still picks the converting
        `<U>`. USER RULING (merged set, no cascade): the rank key gains a
        hop component — `(no-collect, tier, −hop, specificity, fixed)` —
        with the TIER before the HOP before SPECIFICITY. The consequence
        the user confirmed explicitly: an inherited exact/concrete match
        beats a derived generic ("exactness beats genericity, wherever
        declared" — a deliberate divergence from C++'s derived-hides-base),
        while the hop beating specificity gives override semantics with no
        marker: a derived same-shape member shadows an inherited one, a
        nearer base's shadows a farther one, and a DIFFERENT signature
        simply overloads the merged set (the Java-shaped merged surface;
        never C++ name hiding). Constructors merge unconditionally (no
        suppression), and membership filters by the declared
        instantiation: a base specialization is inherited only where the
        `extends` clause names its instantiation, a diagonal qualifier
        only where the base arguments agree, a grouped/bounded member is
        filtered where the instantiation violates the constraint (a
        generic derivation carries it along instead); generic derivations
        (`pd<T> extends point<T>`) stay generic, bare-head constructor
        inference included. USER RULING (upcast surface, v1): the RECEIVER
        position of ANY method-family call upcasts along the declared
        lineage — dot AND explicit qualified calls, which is what enables
        constructor chaining (`point::constructor(self, x, y)` from a
        derived constructor). `mut`/`const` receivers lend the base prefix
        in place (a `mut self`'s writes land in the derived value's
        leading fields), a by-value receiver prefix-copies (the honest
        DATA slicing the `as` upcast performs); every NON-receiver
        argument keeps the explicit `as` — program-wide implicit base
        coercion is deferred to the polymorphic base views item below.
        Emission always instantiates the ORIGIN template: one instance per
        base instantiation, SHARED across derived types (no
        monomorphization bloat), with ambiguity notes attributing to the
        origin (`candidate is here (inherited from point<float64>)`).
        Return types stay spelled at the base. Out of scope, recorded: the
        bare-parameter base (`extends T`) inherits NOTHING (no declared
        family exists at the declaration; documented, deferred), a
        file-scoped `@static` base member stays file-scoped, a
        cross-module `@private` member filters per file as in any open
        set, no override marker ships (deferred to the views item below,
        where the question was already recorded), and pointer decay
        COMPOSED with the upcast is scoped out (a derived `pointf*` does
        not decay-and-upcast into a base `mut self: point<float64>` slot
        in one step; dot-calls on a derived pointer work via the sugar's
        existing one-hop auto-deref). USER PROCESS RULING (audit first):
        the merge was gated on a stdlib audit, which found the stdlib's
        inherited surface EMPTY today (its only `::` families are the nine
        `char::` ones; `list`/`set_entry`/`dict_entry` gain capability
        only) — the user reviewed the audit and approved the merge, with
        two watch items recorded for the stdlib's future method-family
        adoption: (a) never name future `slice::` methods
        `data`/`length`/`capacity` (fields shadow methods, so `list`'s
        fields would eclipse them), and (b) a derived type cannot LOOSEN
        an inherited concrete signature with a generic override (the tier
        beats the hop, by the ruling above). PROVENANCE: this ship
        deliberately REVERSED two long-documented `extends` non-goals —
        "no implicit upcast" (now: receiver-position only) and "no method
        inheritance / no constructor chaining" — both by USER RULING;
        docs/language.md's Structs non-goals are rewritten accordingly.
        Implemented, see
        [Inherited methods](docs/language.md#inherited-methods)
  - [x] `@property` methods — field-syntax access to a method: a method
        annotated `@property` is reachable without parentheses,
        `s.length` calling `stack<T>::length(s)` (the call spelling
        `s.length()` stays valid beside it; the annotation only adds
        the field spelling). A `@property` takes ONLY its receiver and
        returns a value, checked at the declaration: it applies to a
        qualified method (`fn Type::name`) with a body, never
        `@extern`/`@asm`/a bodyless prototype, and a void return or an
        extra parameter rejects. A `-> mut` return makes the access an
        assignable lvalue through the shipped
        [mut returns](docs/language.md#mut-returns): `s.value = v` is
        `T::value(s) = v`, plain and compound assignment both, while a
        read-only (non-`mut`) property rejects assignment with the
        standard does-not-return-mut error. Dispatch is the dot-call's
        own (the sugar item above, which this AMENDS at its bare-access
        clause): a real struct field of the name shadows the property
        (field-first, the property then reachable only as
        `Type::name(s)`), inheritance through `extends` carries
        properties along the merged family, a generic receiver binds
        the struct's type params, and a pointer receiver auto-derefs
        exactly one hop. The `-> mut` form hands out raw storage, so it
        cannot run logic on the write path; for accessors that must —
        validation, clamping, bookkeeping — the same annotation takes an
        argument: `@property("get")` / `@property("set")` declare an
        explicit pair. The getter is receiver-only and value-returning
        like the bare form but may NOT return `mut` (rejected at the
        declaration); the setter takes exactly `(self, value)`, and a
        declared return type is legal but the value is discarded
        (assignment is a statement). `g.level` calls the getter,
        `g.level = v` the setter, and `g.level op= v` is
        read-modify-write — `gauge::level(g, gauge::level(g) op v)`,
        one get, the operator, one set, the receiver expression
        evaluated twice. A pair may be partial: getter-only rejects
        writes with the standard does-not-return-mut error; setter-only
        is write-only — reads reject with
        `property 'T::f' is write-only`, and `op=` with a bespoke
        has-no-getter error. USER RULING: the bare mut-return-lvalue
        form and the pair form are SEPARATE mechanisms — one family
        mixing them, directly or across the `extends` chain, is a
        compile error. Pair members remain ordinary overloads at the
        call spelling (`g.level()` / `g.level(v)` dispatch by arity),
        generic receivers bind `T`, and inheritance through `extends`
        reaches the pair like any method. Adoption: `std/stack` marks
        `stack<T>::length` `@property`, the pattern for the stdlib's
        receiver-only getters. Implemented, see
        [Properties](docs/language.md#properties)
  - [x] `@accessor` methods — overloading `[]`: a method annotated
        `@accessor` is the type's `[]` operator, `xs[i]` calling
        `list<T>::at(xs, i)`. It is `@property`'s indexed sibling, and
        the method stays ordinary (the `xs.at(i)` and `Type::at(...)`
        spellings remain valid beside the brackets; generics,
        inheritance, and overload dispatch carry through), with the
        same bare-vs-pair split: the bare form's `-> mut` return makes
        `xs[i]` an assignable lvalue through the shipped
        [mut returns](docs/language.md#mut-returns) (`xs[i] = v` is
        `Type::at(xs, i) = v`, and `op=` compounds through it), while
        `@accessor("get")` / `@accessor("set")` declare the explicit
        pair for write-path logic (indices first, the assigned value
        last, `op=` as read-modify-write through both, the setter's
        declared return discarded), under the property pair's rules
        (the getter never returns `mut`; getter-only is read-only,
        setter-only write-only; one family cannot mix the bare form
        with the pair, directly or across `extends`). Indices are
        ordinary arguments: any number (the grammar gained multi-index
        `m[r, c]`, accessor-only; native indexing stays single) of any
        type (`d["key"]`), dispatched as overloads within the family.
        Two rules are `[]`'s own: ONE FAMILY PER TYPE (`[]` carries no
        method name to pick by, so all of a type's `@accessor` methods
        must share one name; a derived type reaches a base's family
        through `extends`, its own declaration winning the name), and
        natively indexable bases (pointer, array, slice, tuple) keep
        their primitive `[]`, an accessor never competing with a
        native lowering (the builtins carve-out the protocol items
        record, confirmed here). Deliberate parity gaps shared with
        `@property`: an rvalue base (`f()[i]`) is unsupported (bind it
        first), and concrete accessor methods do not travel through
        `.mci` interface stubs. This ship SUPERSEDES the accessor half
        of the indexing-and-slicing protocol sketch under the open
        overload sets item above: `c[i]` on a user type settled as a
        method affair, and that item is re-scoped to the bracket
        slicer `c[a:b]`. Adoption: `list<T>::at` is a bare `@accessor`
        (list elements, and `string`'s bytes by inheritance, read and
        write like array slots), and `dict<V>` gained `has` plus an
        `at` get/set pair (`d[key]` reads unchecked, guarded with
        `.has`; `d[key] = v` inserts or updates through `.set`).
        Implemented, see
        [Accessors](docs/language.md#accessors-overloading-)
  - [ ] receiver kind — the shipped foundation already lets the receiver be any
        ordinary `const` / `mut` / by-value parameter with no enforced `self`
        convention; this item makes the three receiver flavors a formal, checked
        receiver concept, still with no OOP-specific mechanism:
        `const self: point` (read-only method), `mut self: point` (mutating
        method — `&self` cannot escape, the memory-safe replacement for today's
        raw `self: point*`), and `self: point` (consuming/copying method). None
        require the caller to write `&`; the since-shipped method-call sugar
        above already dispatches plain `var.method()` with the hidden
        reference formed at the call, so this item adds the formal check
        over the same shapes. Two
        pieces of design work this pulls in: (1) `mut self` field projection
        to an lvalue (`self.x = ...`) is proven live by the shipped
        constructor sugar above; the check that remains is that a
        constructor never fires
        its rvalue "copy on read" on the still-uninitialized whole `self`;
        (2) it must reconcile with the fat view's table ABI below (the
        base-typed and interface-typed views): a `mut`-using function is
        now a legal function value of the shipped
        [mut/const-carrying function types](docs/language.md#mutconst-carrying-function-types),
        and in the dispatch table the receiver is anyway already behind
        the view's `object*`, so the table slot's first param is a
        genuine `T*` under an ABI the compiler controls internally. A `mut` return formed from `self` is then
        the natural spelling for a mutable accessor method (and the
        shipped `@property` item above already gives such an accessor
        its field spelling, `s.value = v`)
  - [ ] polymorphic base views — dynamic dispatch, built on the language's
        one data type kind: there is no `class`, everything falls under
        `struct`, and polymorphism arrives through dispatch tables that
        live in the **view**, never in the object. The polymorphic view
        is the plain base-typed `const`/`mut` reference itself: a
        `const a: A` parameter accepting a `B extends A` dispatches
        `a.greet()` to `B::greet`, with no separate interface type
        required for within-hierarchy dispatch. The canonical acceptance
        test:
    ```c
    struct A { ... }            fn A::greet(const self: A) { println("A"); }
    struct B extends A { ... }  fn B::greet(const self: B) { println("B"); }
    struct C extends B { ... }  fn C::greet(const self: C) { println("C"); }
    fn f(const a: A) { a.greet(); }
    let a = A(); let b = B(); let c = C();
    f(a);   // prints "A"
    f(b);   // prints "B"
    f(c);   // prints "C"; must never print "A"
    ```
        Each call prints the dynamic type's answer or the program is
        rejected; no legal program observes `A` for a passed `B` or `C`.
        The mechanism is what makes this fit the language: `const T` and
        `mut T` are already mcc-controlled hidden-reference conventions,
        explicitly not the C ABI (the standing `@extern` exclusion), so
        the reference itself can carry the table: a two-word fat pointer
        `{ object*, table* }`, formed at the conversion site (a concrete
        `B` entering a `const A` slot pairs the object pointer with `B`'s
        table, the compiler knowing the concrete type right there), and a
        view re-lent onward forwards both words unchanged. Tables are
        prefix-compatible down the single [nominal](docs/language.md#structs) `extends` chain (inherited
        methods keep their slot, an override replaces the entry, new
        methods append), which is what lets a `const B` view re-lend as
        `const A` keeping the same table pointer. The object never
        carries any of this: every struct keeps full value semantics and
        its byte-exact layout (MMIO overlays, wire formats, `@extern`
        interop, and the `extends` prefix rule with its zero-cost value
        upcasts, all untouched), and behavioral slicing is
        unrepresentable, since copying a view copies two words, both
        still true. That is why the earlier alternative died: a
        vtable-in-object `class` type kind (fully sketched here, then
        deleted) either corrupts those layout promises or, wherever
        copies remain legal, reintroduces slicing through them, both
        unacceptable, while the view-side table reaches the same
        no-slicing guarantee with one type kind. The dispatch boundary is
        convention-shaped: mcc-native reference forms dispatch
        dynamically, while raw pointers (`T*`, `@nonnull T*`) and
        `@extern` functions keep the one-word C convention and
        statically-bound calls (an `A*` receiver calls `A::greet`; an
        `@extern` function can neither take a fat view nor sit in a
        dispatch table, the same rule that already keeps `const`/`mut`
        parameters and `mut` returns off `@extern`, and C interop passes
        the object pointer explicitly, the view never crossing the extern
        boundary). The MMIO reading stands: `A*` stays static and
        one-word, `const A` is the dynamic view, and `mut A` over
        `@volatile` storage is rejected anyway. Hard problems, recorded
        as open rather than papered over: (1) **when is a reference
        fat?** Making every struct `const`/`mut` two words taxes
        everything (the stdlib's containers use `const`/`mut self`
        throughout and want direct one-word calls); candidate rules: fat
        only for structs that are extended somewhere in the program (a
        whole-program property that collides with `.mci` and
        prebuilt-object ABI pinning, the same problem class the shipped
        open overload sets item answered with per-module import closures,
        presumably wanting the same style of answer); fat only where the
        method set actually has overrides; or an explicit spelling, where
        a full type-kind split is rejected by the one-type-kind decision
        but a layout-neutral marker on the base struct may still be
        distinguishable from a type kind, an open spelling question.
        (2) **which calls dispatch?** Method calls (`a.greet()`, keyed to
        the receiver) dispatch through a fat view where an override chain
        exists; free-function calls stay statically overload-resolved.
        The protocol tenet survives through that split: protocols
        (formatting, iteration, slicing; indexing since shipped as the
        `@accessor` methods above, already a receiver-keyed affair)
        remain compile-time
        free-function overload sets, dynamic dispatch is a
        receiver-method affair, and neither becomes the other's
        privileged mechanism. (3) **copy-on-read of a fat view**: a
        `const A` rvalue copy over a dynamic `B` reads the `A` prefix,
        the C++ slicing shape. Leaning: define the copy as prefix
        extraction, legal and honest **data** slicing (the copy is a
        genuine `A` by construction, byte-exact per the prefix rule, and
        a plain value carrying no view), while behavioral slicing stays
        impossible because tables never live in objects; the alternative,
        rejecting copy-on-read only when the view is fat, makes behavior
        differ by fatness and is recorded as the disfavored option.
        (4) whether an override wants an explicit marker (the deleted
        lane diagnosed both typo directions: silent shadowing, and a
        marker with no base method) is open — the shipped static method
        inheritance above also went markerless, a derived same-shape
        member shadowing by hop, so a marker would arrive here for both
        the static and dynamic surfaces — as is (5) whether the table
        reserves a destructor slot so destroying through a base view runs
        the dynamic type's destructor: the since-shipped automatic
        destructor above deliberately left this open — its call is exact
        by construction on stack lets and base chaining is manual, so
        static destruction is settled and dynamic destruction lives here.
        Depends on the receiver kinds above (`const`/`mut self` is how a
        dispatching receiver travels) and the parent item's method
        machinery, whose STATIC half is now in place: the shipped method
        inheritance through `extends` (the [x] item above) supplies the
        merged families, the hop-ranked resolution, and the
        receiver-position upcast this item dynamizes — today that
        conversion site binds statically (a derived receiver in a base
        slot runs the base's member over the lent prefix), and this
        item makes the same site form the fat view so the dynamic
        type's override wins. Also deferred HERE by that ship's USER
        RULING: whether a base-typed `const`/`mut` parameter accepts a
        derived value at NON-receiver positions (program-wide implicit
        coercion; today an explicit `as`), since that acceptance site
        is exactly where the view would form
  - [ ] interfaces — cross-hierarchy contracts over the same view
        machinery: a named set of required operations
        (`interface writer { fn write(self, buf: slice<const uint8>) -> int64; }`)
        that **unrelated** structs satisfy, an interface-typed parameter
        or binding being the same two-word `{ object*, table* }` fat
        view, formed at the concrete-struct-to-interface conversion
        site. The base views above already carry within-hierarchy
        dispatch, so this item's scope is what an `extends` chain cannot
        give: unrelated types presenting as one thing (heterogeneous
        lists, plugin-style APIs), and one struct presenting as several,
        since contracts multiply freely (a struct converts to any number
        of interface views, the `object*` indirection making interior
        sub-object offsets a non-issue) while state keeps its single
        `extends` chain, the Java-shaped split with `struct` in the
        state role. Direction, recorded not settled: the interface table
        is plausibly built from the whole-program overload sets (an
        interface as a named requirement that certain overloads exist
        for `T`, the conversion site instantiating the table from the
        set), which makes interfaces the **runtime** counterpart of the
        compile-time
        [overload-set protocol family](#functions-and-methods)
        (formatting shipped, iteration and slicing planned; indexing
        since shipped as `@accessor` methods)
        rather than a rival method-privileged mechanism, preserving the
        standing rule that methods must not become the privileged
        mechanism for protocols. Open points under that direction:
        structural versus declared conformance (does defining the
        operations suffice, or does a struct state `implements`); how
        table slots map to overload members (the slot ABI needs a
        deterministic signature-to-slot mapping, the discipline concrete
        overloading's symbol mangling already set); receiver-marker
        handling (`const`/`mut self` slots, the same `const`-receiver
        tension the slicing protocol item records); and the fat-to-fat
        conversion, since boxing an already-fat base view (a `const A`
        holding a dynamic `B`) into an interface at a site that only
        knows the static type either needs the dynamic answer reachable
        through the base view's own table or such conversions
        restricted, so that the dynamic type's behavior is never
        silently dropped at the interface boundary. Receiver ABI as in
        the base views above: the receiver is behind `object*`, so a
        `const`/`mut self` slot's first param is a genuine `T*` under a
        compiler-internal ABI (the receiver-kind note above); a by-value
        (consuming) `self` cannot cross the table without a copy, so
        whether interfaces admit by-value receivers at all is undecided.
        The `@extern` boundary above applies unchanged: an
        interface-typed parameter is not `@extern`-expressible and an
        `@extern` function is never a table member. Shares the slot ABI
        and table-emission machinery with the base views above,
        whichever ships first building it. Depends on methods (above)
    - [ ] `case type` over interfaces — once interfaces land,
          `case type (x)` admits an interface arm (`when writer w:`), a
          set-membership test over the same compile-time FNV-1a-64 type
          tag that shipped `case type` arms already lower to
          integer-equality chains on: whole-program compilation
          statically knows every type implementing the interface and
          every type that boxes into `any`, so the arm lowers to a
          small multi-tag equality chain, each hit forming the fat
          pointer from a per-tag table constant plus the payload, no
          runtime registry. What matches: structs cannot box into `any`
          (the shipped escape-hatch rejection), so the base match is a
          boxed `T*` whose pointee type implements the interface, the
          pointer supplying the view's `object*` directly; and the complementary
          direction is first-class, since a fat pointer is two words
          and the `any` payload is exactly `[2 x uint64]`, so an
          interface value itself boxes into `any` and matches its own
          interface arm, which is what makes a heterogeneous
          `slice<const any>` of interface values work and is the
          natural endpoint for the stdlib's `format_args` dispatch (a
          `when printable p:` arm; `format_args` shipped with the
          [native variadics](docs/language.md#native-variadic-arguments)
          stdlib flip as a lone generic `with` arm in `std/io` over the
          open `format` set).
          Arm ordering becomes semantic: today every arm is a distinct
          concrete type so order cannot matter, but an interface arm
          overlaps concrete arms, so the spec is first-match-wins in
          textual order (an unreachable-later-arm diagnostic is worth
          considering), and the mandatory `else` is unchanged, since
          interface arms widen individual arms without closing the open
          `any` universe. The nesting is the dependency: this hangs off
          interfaces, whose own chain (methods above, over the shipped
          concrete overloading) is unblocked from the bottom. Open
          question, recorded not solved: the tag-to-table mapping is a
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
      reassigned or have its address taken; the initial ban on a function
      with `@nonnull` parameters being a function value was lifted by the
      `@nonnull`-carrying function types sub-item below, which spells the
      contract in the function type. Composes
      with `const` and
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
    - [x] `libc/` bindings, wave 2 — annotated the `@extern` libc surface
          (attribute-only there, like `@noalias` on the `restrict` family:
          the C side is never checked, only callers are), a separate change
          set from wave 1 above, and a follow-on to the now-shipped
          [`-Wextern-nonnull`](#metaprogramming-and-builtins) class, its
          prerequisite (the three-posture enforcement is what let these
          annotations land without re-imposing a hard null-proof wall on
          ported C code): the class landed as the immediately prior change
          set, so this wave rode in right behind it. 57 pointer
          parameters across four modules, 58 with
          `getenv`. `libc/string.mc`: 36 parameters across the `str*`/`mem*`
          externs, excluding `strtok`'s `str` (null continues a
          tokenization) and `strxfrm`'s `dest` (null is allowed when
          `count` is 0). `libc/stdlib.mc`: the `str` of
          `atoi`/`atol`/`atoll`/`atof`, the `strto*` family, and `getenv`'s
          `name` (`getenv(null)` is UB) (10), excluding all five `endptr`
          parameters (documented "if non-null"), `free`/`realloc`'s `ptr`
          (null is meaningful there), and `system`'s `command`
          (`system(null)` probes shell availability); `qsort`/`bsearch`'s
          function-pointer parameters are skipped. `libc/math.mc`: `frexp`'s
          `exp`, `modf`'s `iptr`, `remquo`'s `quo`, and `nan`'s `tagp` (4).
          `libc/time.mc`: `mktime`'s `tm`, `asctime`'s `tm`, `strftime`'s
          `s`/`format`/`tm`, `localtime`/`gmtime`'s `timer`, and `ctime`'s
          `timer` (8), excluding `time`'s `timer` (null is documented OK).
          `libc/stdio.mc` is deferred indefinitely, not part of this wave:
          it has real null-meaningful carve-outs (`freopen`'s `filename`,
          `setbuf`'s `buf`), and annotating `fwrite`'s `ptr` would ask
          `std.mc`'s `writestr` for a proof. The shipped projection facts
          (above) have since dissolved the guard-then-single-call shape
          (`if (str.data == null) return; fwrite(str.data, ...)` now
          proves), but a `str.data` used across calls or inside a loop
          still needs a `let`-seeded binding or a `str.data!` hatch, so
          stdio remains the highest downstream friction for the lowest
          value. The caller blast radius was essentially zero: wave 1 already
          funneled every in-repo raw-libc string/mem call through proven
          wrappers (`dict.mc`'s `strlen`, `memory.mc`'s `memcpy`/`memset`,
          `md5.mc`'s `memset`), so these four call sites already carried the
          proof the annotations demand. The annotations ship unconditionally,
          in the source and in `.mci` stubs alike: no `-D`/`@if` gate (the
          declared contract never varies per build; a gate would also have to
          duplicate every declaration, since `@if` is declaration-granular).
          This wave ships with `-Wextern-nonnull` enabled in CI (the
          example-compile line and `test.sh`), verified green off exactly
          those four proven call sites, so the wave is enforceable at home
          (the `extern_nonnull.mc` class demo stays compiled at plain
          `-Werror` there, not under the enabled class, since a
          warning-class demo cannot run with its own class turned on).
          Enablement is `-Wextern-nonnull` specifically, NOT `-Wall`: `-Wall`
          would also pull in `-Wunchecked-dereference`, and while the shipped
          [`libmc` sweep](#metaprogramming-and-builtins) made `libmc`
          warn-free under that class, the examples are not yet clean, so the
          whole-build `-Wall` flip is still pending (the
          [CI `-Wall` flip](#metaprogramming-and-builtins)); implemented, see
          [Reaching libc](docs/language.md#reaching-libc)
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
          the now-shipped [`mut` returns](#functions-and-methods) did, whose
          plumbing is the precedent to follow if this happens
  - [x] `@nonnull`-carrying function types — lifted the parent's remaining
        soundness ban (a function with `@nonnull` parameters could not be a
        function value) by letting the function type spell the
        per-parameter contract: `fn(@nonnull char*, @nonnull char*)`.
        `let f = my_func;` infers the annotated type, the old rejection
        site having become the inference site, so the old error is gone;
        a call through such a value runs the same call-site null-proof
        as a direct call (the proof machinery is index-keyed and
        indirect calls funnel through the same argument-marshalling
        path, so flow-narrowing and the `p!` hatch apply identically).
        Assignability is contravariant: a plain fn value flows into a
        `@nonnull`-typed slot (it tolerates more), while a `@nonnull` fn
        value may not flow into a plain fn type, with a hinted error
        explaining the contract cannot be dropped because calls through
        the plain type would skip the proof;
        `f as fn(char*, char*)` is the explicit contract-stripping
        hatch, a free bitcast whose calls skip the proof (UB if the
        argument is actually null, mirroring `p!`). Variance is flat:
        fn values only, no deep variance through slices or nested fn
        types. Scope was `@nonnull` alone: `@noalias` stays an unchecked
        hint that drops silently from a fn value, while `mut`/`const` in
        fn types — the [`mut` item](#functions-and-methods)'s separate,
        non-coercible lift — have since shipped reusing the
        annotated-fn-type grammar slot this item built (of the two
        sibling bans at the old rejection site only the `mut`-return ban
        remains, as that convention is still inexpressible). One accepted
        asymmetry, documented with the feature: a fn value of a
        `@nonnull` `@extern` (`let f = strlen;`) carries the contract,
        so calls through the pointer check strictly, while direct
        extern calls keep grading by the
        [`-Wextern-nonnull`](#metaprogramming-and-builtins) posture;
        implemented, see [@nonnull-carrying function
        types](docs/language.md#nonnull-carrying-function-types)
- [ ] Pointer truthiness and `p ?? q` null coalescing — pointers become
      testable in conditions: `if (p)` means `if (p != null)` and `!p`
      means `p == null`, so `if (!p) { return; }` is the null guard (a
      bare `!p` yields a plain `bool`; `and`/`or` stay bool-yielding,
      pointer operands simply becoming legal condition operands through
      the truthiness arm). The `??` operator, whose token, precedence,
      and `try`-fallback arm are owned by stage 3 of the
      [error-handling epic](#types-and-generics), gains its pointer arm:
      `p ?? q` yields `p` when non-null, else `q`, lazily (the
      right-hand side evaluates only on the null path), operands
      agreeing on one pointer type, with the same greedy low-precedence
      right-hand side (a full expression, or an emit-block, which may
      diverge: `p ?? { panic("was null"); }` falls through only on the
      non-null edge, so the result is provably non-null). Narrowing parity ships in the same set: bare-pointer
      and `!p` conditions join the `p != null` / `p == null` guard
      recognizer of the flow-narrowing item above, so `if (!p) return;`
      narrows `p` for the remainder exactly like the spelled-out guard
      (the `!` arm covers a single atom; distributing `!(...)` over
      compound conditions is a follow-up), and a coalesce whose
      right-hand side is provably non-null or diverges seeds the
      result's fact (`let q = p ?? default!;`)
- [ ] C variadics — the C-ABI `...`/`va_list` machinery, beyond forwarding:
  - [x] variadic declarations and `va_list` forwarding — implemented, see
        [Variadic functions](docs/language.md#variadic-functions)
  - [ ] `va_list` function values crash on x86-64 SysV — a known gap, not a
        feature: taking a function value of a native `va_list`-taking
        function crashes llvmlite on x86-64 System V, because the
        function-type builder in the codegen type layer spells the
        `va_list` parameter in its *storage* IR form while declaration
        sites use the *passed* form (the generator's `va_list_passed_ir`);
        the two forms only coincide (`i8*`) on darwin arm64, which is why
        the host suite never trips it. Pre-existing (present before the
        carrying-function-type lifts; surfaced during the `fn(mut T)`
        work), and the proper fix needs target knowledge in the type
        layer, where `function_type` today has none
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
  - [x] stdlib `panic`/`assert` — the `@noreturn` "print to stderr and
        abort" guards in `std/io`, a verbatim-message and an
        `@format`-collecting overload of each (`panic(msg)`,
        `panic(fmt, args...)`, `assert(cond, msg)`,
        `assert(cond, fmt, args...)`): `panic: ...` / `assertion failed:
        ...` on stderr, then `abort()` with stdout flushed first (no
        defers, no atexit handlers), the idiomatic guard body
        (`if (p == null) { panic("..."); }` narrows; `assert` itself does
        not — facts stop at the call). An f-string resolves to the
        collector because the sink rule filters overload candidates
        before ranking (a non-`@format` slot can never receive one);
        implemented, see
        [Panic and assert](docs/language.md#panic-and-assert):
    - [ ] release-stripping asserts — a `-D MC_NDEBUG=1`-style `@if` gate
          inside the assert bodies compiling the check away (undefined
          `-D` names read as 0, so opting in later is drop-in)
    - [ ] caller location — panics carry no file/line today; the hook is
          a compiler-filled caller-location parameter attribute
          (call-site compile-time rewriting, which the `@format`
          positional desugar already proves out), its own item when it
          comes
  - [x] `-Wdead-code` — an opt-in class (via the shipped warning registry)
        reporting statements silently dropped after a `return`, a
        `@noreturn` call, or an `unreachable` (also `break`/`continue`/
        `emit` and all-paths-diverging statements; one warning per dead
        region, naming the killing construct type-free so generic
        re-emissions dedup); defers dropped because another *defer*
        diverged are deliberately out of scope — a different diagnostic;
        implemented, see [-Wdead-code](docs/language.md#-wdead-code)
  - [x] constant-condition loop folding — recognize `while (true)`-style
        loops during generation so the never-taken exit edge (and its empty
        `end` block before a `@noreturn` body's auto-`unreachable`) never
        gets emitted, as optimizer cleanliness — and, with the exit edge
        gone, extend `-Wdead-code`'s reach to the code after such a loop;
        implemented (any constant-folded condition, `until (false)` as the
        dual, gated on a `break`-free body — a `break` keeps the exit
        block and the code after the loop live; the divergence also lifts
        the missing-return and missing-emit checks). Non-goals: the
        never-runs duals (`while (false)` keeps its type-checked body,
        like `if (false)`) and `for` loops (every form exits on a runtime
        comparison); see [Control flow](docs/language.md#control-flow)

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
        `for ... in` `_it`/`_next` protocol), with one suppression — a call
        made from inside the body of a function that is itself `@deprecated`
        does not warn, so a deprecation shim delegating among the deprecated
        cluster stays quiet while a live caller still warns — and the
        driver deduplicates repeats of one (file, line, message) at print
        time so a call site inside a generic body reports once, not once per
        instantiation. It round-trips through `.mci`: for free for generic
        and `@inline` functions (verbatim source-span emission), and by
        explicit re-emission (message re-escaped) on concrete exported
        prototypes. Default severity is warn deliberately: a hard error would
        make a deprecated alias uncallable and break importers, defeating the
        purpose. The motivating use case shipped with it: the four generic
        forwarders in [memory](lib/std/memory.mc) (`copy_bytes`, `copy_items`,
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
      - [x] the `libmc` sweep, the acceptance test by dogfooding: wave-1
            `@nonnull` adoption cleared the loudest sites, and this pass
            took the remaining 96 invariant-backed dereferences their
            postfix `!` assertion (or a `let …!` seed) across
            dict/set/queue/list/ring/stack/equality and
            hashing/{md5,murmur3,fnv1a} (`self.data` index bodies and
            iterator `obj->data` chains; `string.mc` forwards to `list`
            and carries none of its own), so `libmc` now compiles
            warn-free under `-Wunchecked-dereference`. Every `!` is
            provably IR-identical (it emits zero instructions), so the
            sweep is a pure annotation pass that changes no behavior; an
            acceptance test compiles a container/hashing exerciser under
            `-Werror=unchecked-dereference` and asserts it passes. This
            unblocks but does not itself perform the CI `-Wall` flip (the
            [example warn-free sweep](#metaprogramming-and-builtins)
            below); implemented, see
            [-Wunchecked-dereference](docs/language.md#-wunchecked-dereference)
    - [x] `-Wextern-nonnull` — graded enforcement for `@nonnull` on
          `@extern` declarations, three postures over one warning class.
          Built first, its own change set, ahead of the
          [wave-2 libc annotations](#tooling-and-c-interop) that depend on
          it: those annotations without this class would re-impose a hard
          null-proof wall on ported C code, so the class was the real
          prerequisite and landed ahead of the wave. Extern `@nonnull`
          already parsed, emitted attributes, enforced callers (literal-null
          and possibly-null were both hard errors before this), and
          round-tripped in `.mci`; this item replaced the flat possibly-null
          error with the graded posture below. Two pieces stay outside the
          grading: passing
          a literal `null` to an annotated slot is always a hard error at
          every posture (never porting noise, it is equally broken C), and
          native mcc `@nonnull` never joins the class at all (the callee
          body holds the parameter as a non-null fact, so its caller proof
          is load-bearing and stays a hard error). Hint-emission is the axis
          the postures move: the LLVM `nonnull`/`dereferenceable` attributes
          are sound only under unconditional caller proof, so on an
          `@extern` declaration they are emitted only at the strict posture
          and skipped otherwise (native functions always keep them). This is
          a per-declaration codegen fact keyed off the global posture, which
          is knowable from the CLI `-W`/`-Werror` state before codegen runs.
          The compiler work was narrow: registered `extern-nonnull` in
          `WARNING_CLASSES`; made `mark_nonnull` skip the hint on externs
          unless the posture is strict (the extern-vs-native fork is
          `symbol in self.extern_decls`); and forked `check_nonnull_arg`'s
          possibly-null branch three ways (accept / warn / error) by
          posture. The annotations themselves ship unconditionally in
          source and `.mci` stubs (the declared promise never varies per
          build), and the rejected alternative stands recorded: a `-D`/`@if`
          `SAFE_LIBC` gate would duplicate the declaration surface per
          branch (`@if` is declaration-granular), flip the whole program's
          libc contract on one define, and break `.mci` identity (stubs
          re-emit `@nonnull`) plus the merge collapse of matching `@extern`
          redeclarations; implemented, see
          [-Wextern-nonnull](docs/language.md#-wextern-nonnull):
      - [x] relaxed (default, no flag) — a possibly-null argument to an
            annotated extern slot is silently accepted, and no LLVM
            `nonnull`/`dereferenceable` is emitted on the extern declare
            (the hint is unsound here, `mark_nonnull` skips it via the
            `self.extern_decls` fork). This is the posture a mechanical C
            port builds under with no flag at all, so it never hits a
            null-proof wall on `strcpy`/`strlen`/`memcpy` calls; strictness
            on the C boundary is what a codebase reaches for, not what a
            port escapes from
      - [x] warn (`-Wextern-nonnull`, opt-in) — a possibly-null argument
            warns over the channel (`[-Wextern-nonnull]` in the rendering),
            `-Wall` includes it; still no LLVM hint (a warning is not a
            proof, so the extern declare stays hint-free). Default-silent
            rather than warn-by-default is deliberate, the no-unavoidable-
            noise principle above: a fresh port would drown in per-call
            warnings it never asked for, so discoverability rides `-Wall`
            and the flag-suffix convention instead
      - [x] strict (`-Werror=extern-nonnull`) — the class is error-level, so
            a possibly-null argument is a hard error, restoring the
            unconditional caller proof the default trades away, which is what
            makes it sound for `mark_nonnull` to re-emit the LLVM
            `nonnull`/`dereferenceable` hints on the extern declares. This is
            the posture that recovers the codegen quality the relaxed default
            gives up. Reachable two ways. First, whole-build `-Werror`: this
            repo's CI and `test.sh` already run it, and global `-Werror` plus
            `-Wextern-nonnull` is exactly "this class is error-level."
            Second, and settled to ship with the class: selective per-class
            `-Werror=<class>` input parsing, so `-Werror=extern-nonnull`
            alone makes strict a targeted posture on the C boundary without
            promoting the whole build. This is a general driver feature, not
            special-cased to this class: it parses `-Werror=<name>` for any
            registered warning class (an unknown name is the same hard CLI
            error `-W<name>` already gives), and the output render already
            spoke `[-Werror=<name>]`, so this only added the matching input
            spelling
    - [x] CI `-Wall` flip — `-Wall -Werror` is on in the example-compile
          loop, the bare-metal and cross-ABI steps (`ci.yml`), the wheel
          smoke tests (the package job and `test.sh`), and `build.sh`,
          promoting every opt-in class over the whole build, not just
          `libmc`. The examples went warn-free for it: their
          `unchecked-dereference` sites took their `!` or a seeded
          `let ...!` binding, and `libc/errno`'s two `*errno_location()`
          sites joined the container sweep. Landing it surfaced (and fixed)
          three checker false-positive classes: a member/index chain over
          arrays is address arithmetic and now proves its decay
          (`grid[0][1]`, `unit.sizes[2]`, a flexible `p->data[i]`), a
          reassignment's right-hand side is judged before the fact dies
          (`cur = cur->next`), and pointer `+=`/`-=` keep a narrowed fact by
          the `p + n` axiom — so the sweep needed fewer hatches than the
          projected ~62, and dead-code gating across examples turned out to
          be a non-issue (only the class demo has live triggers). The two
          own-class demos with live triggers
          (`types/unchecked_dereference.mc`, `control-flow/dead_code.mc`)
          are compiled at plain `-Werror`, extending the carve-out
          `systems/extern_nonnull.mc` already had (a demo cannot compile
          under its own class turned error-level); `types/warnings.mc`,
          projected as a third carve-out, needed none — it compiles under
          `-Wall -Werror` untouched
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

- [x] Formatted `print`/`println` — Rust/Python-style `{}` placeholders,
      type-driven (no `%`-letters), written in mcc over the
      [native variadic](docs/language.md#native-variadic-arguments)
      `slice<const any>`; enables
      compile-time format checking later. Per-type rendering is not
      per-struct `format` methods but the shipped stdlib `format` overload
      set below, per the [open overload sets](#functions-and-methods)
      rule that protocols are free-function overload sets. The shipped
      signature is `fn println(const fmt: slice<const char>, args...)`: a
      string literal adapts to `fmt` at the call site (so `println("{}", a)`
      works directly), and an owned `struct string` borrows in with
      `str as slice<char>` — both via the
      [`slice<T>`](docs/language.md#slices) borrowing rules. **This is now
      the only `print`/`println`**, every stage nested below shipped and
      the legacy toggle retired; the open sub-items are the remaining
      scraps and the silent-edge spec:
  - [x] printf-style `%` formatting — the previous `print`/`println` in the
        [standard library](README.md#standard-library), superseded by the
        `{}` model and kept behind `-D PRINTF_PRINTLN=1` for programs
        mid-migration until the toggle's retirement below; with the
        modifier stages all landed, libc's `printf` stays only the
        scientific-notation (`%g`/`%e`) tool
    - [x] retire the `PRINTF_PRINTLN` toggle — the recorded decision:
          deleted rather than extended when `panic`/`assert` joined
          `std/io`. The legacy pair and its `@if`/`@else` branches are
          gone, the slice-typed `{}` pair is unconditional, and the docs
          carry one format grammar (the flip audit's lean); the `-D`
          mechanism itself is untouched
  - [x] the stdlib `format` overload-set module — `lib/std/format.mc`, the
        type-driven per-type rendering layer the placeholder stages below
        dispatch into: a
        `format(mut str: string, value: X, const modifier: string)`
        baseline set with a closed signed group sign-extending into an
        `int64` worker, a closed unsigned group, concretes for
        `float64`/`bool`/`char`/`char*`/`slice<char>`, a generic
        `slice<T>` list-renderer, and an unbounded `<typename>` fallback
        rendering the type's name in angle brackets; integer modifiers
        `x`/`X`/`p`, bool `y`/`yes`, and slices apply the modifier
        per element. The formatting member of the overload-set protocol
        family, riding the shipped
        [open overload sets](#functions-and-methods): the set is open,
        so making a type printable is one `format` overload written in
        the user's own module
  - [x] formatting over the format string with bare/sequential
        `{[modifiers]}` placeholders, parsed at runtime: each `{}` renders
        the next argument in sequence through the `format` set, the bracket
        content travels verbatim as the per-type modifier (`{x}`, `{yes}`),
        and `{{`/`}}` escape literal braces
    - [ ] spec the silent formatting edges — the shipped parser's two
          unspecified behaviors, carried over from the
          [native variadics](docs/language.md#native-variadic-arguments)
          stdlib-flip stage:
          an excess placeholder with no argument left renders nothing
          (the collector's `i < args.length` guard), and a trailing
          unclosed `{` silently discards the accumulated modifier text;
          the
          [formatted print docs](docs/language.md#formatted-print--println)
          document neither. Open, not settled: whether the spec blesses
          the silently-permissive behavior or turns the edges into
          diagnostics (the compile-time format checking the positional
          and interpolation items reference is the natural vehicle for
          the diagnostic option)
  - [x] positional placeholders — `{n}` selecting an argument manually, as
        compile-time sugar over the sequential form:
        `println("{0}, {0}", x)` desugars to `println("{}, {}", x, x)`
        (duplicating/reordering the once-evaluated arguments at the call
        site), so the runtime parser stays sequential-only. In the
        positional form a `:` separates the index from the modifiers —
        `println("{0} {0:x}", n)` desugars to `println("{} {x}", n, n)`,
        the colon dropping with the index. One format string commits to
        one placeholder style: manual numbering (`{n}`), auto numbering
        (`{}`), and interpolation (`{expr}`, below) cannot mix — a mixed
        string is a compile error, not a guess at the intent, as are an
        out-of-range index and an argument no placeholder references.
        Shipped with the `@format` parameter attribute as the hook
        (`std/io` marks `print`/`println`/`format_args`; valid on the
        `slice<const char>` just before a collecting `args...`, carried
        through `.mci` stubs like `@nonnull`) and the index-less `{:N}`
        escape spelling the bare all-digit field width the positional
        grammar now claims (`{:2}` desugars to the runtime `{2}` width;
        a *variable* format string keeps today's runtime reading). Pairs
        with the compile-time format checking that string interpolation
        (below) also builds on; implemented, see
        [Formatted print/println](docs/language.md#formatted-print--println)
  - [x] integer format modifiers — width and zero-padded width over every
        base, the `[0][width][x|X|b|p]` grammar (`{8x}`, `{08x}`, `{08p}`,
        a bare `{6}` decimal — spelled `{:6}` in a literal since the
        positional item above claimed the all-digit bracket), hand-rolled
        in the integer digit worker (no
        snprintf round-trip): a space width counts the whole field, a zero
        width the digits alone (the sign and `0x` sit outside the zeros,
        so `-42` under `{08p}` is `-0x0000002a`), negatives render
        sign-and-magnitude (the base applies to `|value|`), and int64's
        minimum renders exactly (its magnitude is taken by
        two's-complement negation in uint64 space)
  - [x] string format modifiers — field widths over the string members,
        the `[N][s][N]` grammar: `{20s}` (or a bare `{20}` — `{:20}` in a
        literal since the positional item above) right-aligns
        the text in an N-wide field, `{s20}` left-aligns, text at or past
        the width appends unpadded. `char*` gains them by wrapping in a
        strlen-measured slice and delegating to the `slice<const char>`
        member — and a null `char*` now renders `(null)` instead of being
        undefined
  - [x] float format modifiers — precision and field width, the `[[N].M]f`
        grammar: `{.2f}` rounds to two decimals (`{.0f}` drops the point
        entirely), an optional leading width space-pads the whole field,
        sign included (`{8.2f}`), and a bare `{f}` keeps the six-decimal
        default. Parsed in the same per-type channel the integer and
        string grammars ride (the `format` set's `modifier` parameter),
        and rendered through the member's existing snprintf engine — the
        parsed width and precision feed a `%*.*f`, so the rounding is the
        C library's. The last runtime modifier stage: libc's `printf`
        remains only the scientific-notation (`%g`/`%e`) tool
- [x] String interpolation — `println(f"x = {x}")`: an `f`-prefixed string
      literal with `{expr}` holes desugars at parse time into the formatted
      `println("x = {}", x)` call above (`{{`/`}}` escape a literal brace),
      so it is surface syntax only — no new runtime. The prefix is what
      keeps the two brace grammars apart: in a plain literal `{x}` is a
      *modifier* placeholder (hex the next argument), while in an `f`-string
      `{x}` is the *expression* `x` — `println("{x}", x)` and
      `println(f"{x}")` are both meaningful and unambiguous, and
      `f"{x:08x}"` carries a modifier through (the hole is parsed first, so
      a ternary's own colon stays inside the expression). The inspector form
      is Python's, spelling and semantics: `f"{n=}"` splices the hole's
      verbatim source text as a label ahead of the value — whitespace
      preserved (`f"{n = }"` prints `n = 7`), a modifier composing after
      the `=` (`f"{x=:08x}"`). An interpolated string is its own
      placeholder style: it does not mix with the auto-numbered `{}` or
      manually numbered `{n}` forms, and extra arguments after one are a
      compile error. Legal only as the format string of an `@format` call —
      every other sink is a compile error, never a silently dropped hole;
      implemented, see
      [Formatted print/println](docs/language.md#formatted-print--println)
  - [ ] string-valued f-strings — an f-string used outside an `@format`
        argument, as a standalone value (`let s = f"{x}";`), rendering into a
        runtime `string` so it can go anywhere a string can, not only into an
        `@format` argument position. The gate is now HALF OPEN: the shipped
        move-out returns (`-> own`, nested under the destructor item in
        [Methods / OOP](#functions-and-methods)) plus caller-adopted
        destruction cover LET position — `let s = f"..."` can desugar to
        `let s = format("...", args...)` over a renderer
        `fn format(str, args...) -> own string` whose returned `string` is
        constructed in the callee, adopted by the caller's let, and destroyed
        at the end of that scope (RAII over
        [`defer`](docs/language.md#defer), the same discipline the destructor
        item establishes). What remains is ARGUMENT position:
        `println("{}", f"{x}")` produces an expression temporary, and
        temporaries own no cleanup (the shipped inert-consumption stance), so
        the rendered `string` would leak; end-of-statement temporary
        destruction is machinery no item owns yet. The shipped `@format`-only
        rule above (an f-string anywhere but a format-string argument is a
        compile error) is exactly this deferral, and lifts to the desugar
        position by position as the lifecycle allows

### Tooling and C interop

- [x] Instantiation backtraces on errors — an error inside a monomorphized body
      used to print as a bare line in the template file with no trace of how
      the compiler reached it; a source-level note chain on `LangError`
      (which previously carried only message/line/source) has the driver print
      `file: note: line N: ...` lines after the unchanged primary
      `file: error: line N: msg`:
  ```
  lib/std/hashing/splitmix64.mc: error: line 10: cannot cast box to uint64
  lib/std/hash.mc: note: line 12: in instantiation of splitmix64<box>
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
      C boundary correctly (see
      [C ABI compatibility](README.md#c-abi-compatibility)). Applied only at the
      `@extern` boundary; mcc's own calls keep their raw-aggregate convention
  - [x] AArch64 (Apple/AAPCS64), mcc calling C — homogeneous float aggregates
        in FP registers, ≤16-byte aggregates in GPRs, larger ones indirect
        (pointer-to-copy arguments, `sret` returns); a by-value-struct `@extern`
        hard-errors on any non-AArch64 target. See
        [c_struct_abi.mc](examples/systems/c_struct_abi.mc)
  - [x] x86-64 (System V and Windows x64) — the same classification for their
        register/`byval`/`sret` rules, lifting the non-AArch64 compile error.
        System V eightbyte-classifies aggregates into GPR/SSE registers with
        frontend register accounting (demoting a no-longer-fitting aggregate to
        a `byval` stack argument) and Win64 uses one integer register for a
        1/2/4/8-byte aggregate; both are link-verified (System V) or IR-shape
        tested (Win64, no CI runner). riscv64 and unknown targets still error
  - [ ] C calling mcc — exporting an mcc function that takes/returns a struct by
        value to a C caller (today mcc's own definitions keep the native
        raw-aggregate convention; only the `@extern` call *into* C is classified)
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
