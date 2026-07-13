# Changelog

All notable changes to mcc are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and the project follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html)
(pre-1.0: new language/tooling features bump the minor version).

## [Unreleased]

### Removed

- **The `PRINTF_PRINTLN` toggle and the legacy printf-style
  `print`/`println`** — the `-D PRINTF_PRINTLN=1` escape hatch is retired:
  `std/io`'s `@if`/`@else` branches are deleted and the slice-typed `{}`
  pair is unconditional, so the docs carry one format grammar. Programs
  still on the C-variadic pair migrate to `{}` placeholders (or call
  libc's `printf` directly — still the scientific-notation tool). The
  `-D` define mechanism itself is unchanged.

### Changed

- **A bare alias qualifier at a call now injects the instantiation it
  names** — with `type pointf = point<float64>`, `pointf::sum(q)` means
  exactly `point<float64>::sum(q)`. Previously the call qualifier chased the
  alias *by name only*, so a `point<int32>` receiver silently re-dispatched
  under the bare family — a soundness gap, now a receiver-mismatch error
  (`argument 1 of 'point::sum': expected point<float64>, got point<int32>`).
  An alias that is not a complete type (`type pf = point` over a generic
  `point`) still canonicalizes by name and infers; a fully-defaulted generic
  alias used bare is a complete type and pins its defaults' instantiation.

- **A `@deprecated` function may call other deprecated functions without
  warning** — a deprecation warning is now suppressed when the call is made
  from inside the body of a function that is itself `@deprecated`. A
  deprecation shim delegating among the deprecated cluster (a deprecated
  `writeln` forwarding to a deprecated `writestr`) is not a misuse and no
  longer emits a warning on every program that imports the module. A *live*
  function calling a deprecated one still warns — the exemption is exactly
  the enclosing-function-is-deprecated case, and it holds for monomorphized
  deprecated generic bodies and for function values formed inside them too.
- **`swap` and `replace` moved to `std/utils`** — the generic in-place
  helpers now live in their own module, `import "std/utils";`, instead
  of riding along with `std/io`. Programs that reached them through
  `import "std/io";` add the one import; `std/io` keeps the printing
  and writing families only.

### Added

- **Explicit type arguments at a qualified call —
  `point<float64>::magnitude(p)`** — a qualified method call's qualifier may
  spell the receiver instantiation. The written reference resolves as an
  ordinary type use (arity checks, trailing-default fill, generic-alias
  substitution with permutation honored — `swap<int32, float64>::first(p)`
  over `type swap<X, Y> = pair<Y, X>` pins `pair<float64, int32>`), and
  inside a generic method body the enclosing type parameters resolve through
  the live instantiation, so **constructor and destructor chaining** is now
  expressible: `point<T>::constructor(self, x as T, y as T)` inside a
  converting constructor, `inner<T>::destructor(self.i)` inside an owner's
  destructor (the qualified form is the pair's only callable spelling). The
  resolved instantiation *pins* the receiver — dispatch matches it against
  each member's declared qualifier annotation, so a matching full or partial
  specialization is reached, a mismatching receiver is the ordinary coercion
  error, a pin no member matches reports `'box::get' has no member for
  box<float64>: the qualifier's type arguments pin the receiver
  instantiation`, and a no-receiver member (`point<float64>::origin()`)
  becomes callable with nothing to infer from. Builtin generic families take
  the form too (`slice<int32>::first(s)`). The list belongs to the struct
  frame only: a method's own type parameters stay inference-only (a second
  list after the member name is a parse error, mirroring the dot-call rule),
  and bare struct qualifiers (`point::m(p)`) are unchanged.

- **Destructors: automatic cleanup of stack-constructed values** — a method
  named `destructor` completes the constructor pair: when a type declares
  (or inherits) a `T::destructor` family, the constructor-sugar let
  schedules the cleanup call on the enclosing block's defers, so
  `let p = point<float64>();` is `let p: point<float64>;` plus the
  constructor call plus `defer point<float64>::destructor(p);`. Exactly the
  constructor-sugar let triggers — manual construction, struct-literal
  lets, copies, and assignments schedule nothing (the opt-out spellings) —
  and the call shares the defer machinery verbatim: LIFO with explicit
  defers, per loop iteration, and on every unwinding exit (early
  return/break/continue/try-propagation; `@noreturn` exits run no
  destructors, as no defers). Destruction ignores a `const` view (scope
  teardown, not user mutation — a user-written call on a const value still
  errors), and returning or emitting the whole auto-destructed local is a
  hard error (return the constructor expression directly, or construct
  manually to own the cleanup; field escapes are not caught). The pair is
  **qualified-only**: `p.destructor()` / `p.constructor(args)` are compile
  errors (`'destructor' cannot be called with method syntax; use
  point::destructor(p)`) — `T::constructor(t, args)` and
  `T::destructor(t)` are the only callable spellings, kept mainly for
  chaining a base's from a derived body (a genuine *field* of either name
  keeps its field behavior). Manually calling `T::destructor(p)` beside
  the automatic call is undefined behavior, like a C double-free.
  `destructor` was previously an unclaimed method name; any existing
  family under it gains the automatic call. See
  [Destructors](docs/language.md#destructors) and
  [destructors.mc](examples/types/destructors.mc).

- **Implicit empty constructors** — every type now has one: `T()` with no
  arguments is exactly `let t: T;` (the slot default-initialized as the
  bare declaration — declared field defaults apply, anything else starts
  uninitialized), for any head the constructor sugar accepts: `char()`,
  `int32()`, `point<float64>()`, a derived `pointf()`, an alias. Unlike
  C++, declaring constructors does not suppress it — a family with only
  argument-taking members leaves `T()` default-initializing — but declared
  members win: a visible member that accepts just the receiver (a
  `(mut self)`-only constructor, or a collecting one whose fixed prefix is
  only the receiver) claims the zero-argument call and resolves normally,
  so the implicit form is strictly the fallback and no ambiguity can
  arise. Calls **with** arguments are untouched (`int32(5)` stays
  `type 'int32' has no constructor`), and a bare generic head keeps the
  cannot-infer error — there are no arguments to infer from. See
  [Constructors](docs/language.md#constructors) and
  [constructors.mc](examples/types/constructors.mc).

- **Method inheritance through `extends`** — a derived struct now exposes
  its base chain's method families, constructors included: a family call on
  the derived type (dot sugar or the qualified spelling) resolves over the
  merged set of its own members and every base hop's, the latter rebased at
  the declared base instantiation — on `pointf extends point<float64>`, the
  inherited `fn point<T>::constructor(mut self, x: T, y: T)` is a concrete
  `(float64, float64)` member that outranks a derived generic for float
  arguments, while `pointf(1, 1)` still picks the converting `<U>`. The
  rank key gains a hop component — `(no-collect, tier, −hop, specificity,
  fixed)` — so a derived same-shape member shadows an inherited one and a
  nearer base shadows a farther one, with no override marker and no name
  hiding (different signatures overload). Base specializations, diagonal
  qualifiers, and constrained members filter by the declared instantiation;
  generic derivations (`pd<T> extends point<T>`) stay generic, bare-head
  constructor inference included. The **receiver position** of any
  method-family call upcasts along the declared lineage — `mut`/`const`
  receivers lend the base prefix in place (a `mut self`'s writes land in
  the derived value's leading fields), by-value receivers prefix-copy, and
  explicit qualified calls get it too, so `point::constructor(self, x, y)`
  chains from a derived constructor — while every non-receiver argument
  keeps the explicit `as`. Emission instantiates the origin template (one
  instance per base instantiation, shared across derived types), ambiguity
  notes name the origin (`candidate is here (inherited from
  point<float64>)`), and the bare-type-parameter base (`extends T`) stays
  out — no declared family to inherit. See
  [Inherited methods](docs/language.md#inherited-methods) and
  [method_inheritance.mc](examples/types/method_inheritance.mc).

- **Method call sugar `recv.method(args)`** — a dot-shaped call whose
  receiver type registers a `Type::method` family desugars to
  `Type::method(recv, args)`, the receiver passing verbatim so overload
  resolution, `mut`-receiver legality, and every diagnostic are the
  desugared call's own. Fields shadow methods (a fn-typed field keeps
  today's field-call behavior; only a call with *neither* gets the new
  `struct 'point' has no field or method 'name'` error), `->` stays
  fields-only, and a pointer receiver auto-derefs one hop (`q.m()` is
  `Type::m(*q, ...)`). Builtin and alias receivers dispatch their canonical
  family (`'c'.upper()` with `std/char`). An rvalue receiver evaluates once
  into a hidden **const** local — so a mut-self method on a temporary stays
  an error — while a `mut`-returning receiver re-lends its carried lvalue
  (`b.ref().grow()` writes the caller's storage), and mut-returning
  dot-calls are lvalues (`l.at(i) = v`, `a.view().at(2) = 7`). The two
  semantic method names are excluded: `p.constructor(args)` and
  `p.destructor()` error with a hint toward their qualified-only forms.
  See
  [Calling methods: dot syntax](docs/language.md#calling-methods-dot-syntax).

- **Constructor call sugar `S(args)`** — a method named `constructor` makes
  its type callable: `let s = S(args);` is exactly `let s: S;
  S::constructor(s, args);`, with `let` binding the constructed slot
  directly (no temporary, no copy). The head follows type-use spelling —
  explicit type arguments (`point<float64>(1, 1)`), a non-generic or
  fully-defaulted type bare, a transparent alias (`pointf(1, 2)`) — plus
  call-side inference for a bare generic head: `point(1.5, 2.5)` deduces the
  instantiation from the constructor's arguments through the family's
  ordinary overload resolution. Any type with a declared `constructor`
  family is constructible, builtins included; without one the call errors
  (`struct 'point' has no constructor` / `type 'int32' has no constructor` —
  never a cast), and a same-named function, variable, or constant still wins
  unconditionally. See [Constructors](docs/language.md#constructors).

- **Subsumption ordering of rank-tied generic overloads** — a rank tie
  (same tier, same pattern specificity) is no longer automatically
  ambiguous: among the tied cohort, the candidate whose parameter pattern
  is strictly an **instance** of every other member's — and whose
  type-parameter constraints **imply** theirs — is the more specialized
  declaration and wins. The canonical case: the diagonal `f(x: T, y: T)`
  now beats the open `f(x: T, y: U)` for agreeing arguments (repeated
  names must bind consistently, so the open pattern's wildcards map onto
  the diagonal but not vice versa); the alias-spelled diagonal
  (`fn diag<U>::m` with `type diag<T> = pair<T, T>`) beats an open
  `fn pair<A, B>::m` the same way. Constraints participate: closed type
  groups imply by **subset** (`T: int8` implies `U: int8 | int16`),
  `extends` bounds by the declared **nominal chain**; a group never
  implies a bound nor vice versa, and an unconstrained parameter implies
  nothing — so a looser-bounded diagonal against a tighter-bounded open
  pattern is incomparable and stays ambiguous. The winner must be the
  cohort's unique **maximum** (strictly subsuming into *every* other
  member): mutually non-subsuming forks, mutual subsumption via a
  defaulted extra parameter, and rank-tied partial specializations
  (`pair<int32, U>` vs `pair<T, int8>`) all still report the standard
  ambiguity error, and the tie-break never crosses tiers or specificity.
  Alongside it, an **adaptable-literal viability fix**: an untyped integer
  literal at a bare type-parameter slot keeps a candidate viable only when
  the deduced binding is an *integer* type (mcc has no int-to-float
  literal adaptation), so a diagonal whose `T` deduced `float64` from
  another argument no longer manufactures a phantom tie — `f(fv, 1)`
  simply picks the sibling that can emit the literal. Open sets gain the
  matching deliberate edge: an imported *equal-rank but strictly more
  specialized* candidate now wins a former tie. See
  `examples/functions/overload_subsumption.mc`.
- **`std/char` — character classification and case conversion** — a new
  stdlib module (`import "std/char";`) registering the ctype family as
  methods on the builtin `char` type: `char::is_alpha`, `is_alnum`,
  `is_digit`, `is_hex`, `is_space`, `is_upper`, `is_lower` (predicates,
  `-> bool`) and `char::upper` / `char::lower` (conversions; a character
  with no counterpart in the target case is returned unchanged). All are
  `@inline` wrappers over `libc/ctype`, and the module is the standard
  library's first use of the builtin-qualifier method form. See
  `examples/systems/char_methods.mc`.
- **Methods on type aliases and builtin types — `fn Alias::method(...)`,
  `fn int32::method(...)`** — methods register to a TYPE, and a `type` alias
  is just an alias: declaring `fn pointf::magnitude` with
  `type pointf = point<float64>;` **is** declaring
  `fn point<float64>::magnitude` (a specialization, outranking the generic
  for a `point<float64>` receiver), and both spellings call **one family** —
  `pointf::magnitude(p)` calls the `point::magnitude` family (and, since the
  explicit-type-argument entry above, a complete alias pins the
  instantiation it names rather than hopping by name). The chase follows alias
  chains, is access-checked per hop (`@private`/`@static` aliases behave as
  everywhere else), and composes through a generic alias's substitution:
  written pre-`::` arguments check arity against the *alias* (trailing
  defaults fill), so `fn swap<int32, U>::pick` with
  `type swap<X, Y> = pair<Y, X>` is the partial `fn pair<U, int32>::pick`,
  and a duplicate-position alias (`type diag<T> = pair<T, T>`) becomes a
  **diagonal constraint** — one parameter that must unify consistently, so a
  `pair<int32, float64>` receiver is rejected, and (since the subsumption
  entry above) an agreeing receiver picks the diagonal over an open
  `fn pair<A, B>::m` sibling. A *bare* generic-alias
  qualifier is an error like any bare generic qualifier (`type alias 'pf' is
  generic; the method qualifier must annotate its type parameter(s), e.g.
  'fn pf<T>::m' or 'fn pf<float64>::m'`) — with the fully-defaulted
  exception: `fn pf::m` with `type pf<T = float64> = point<T>` is a complete
  bare type use and **is** `fn point<float64>::m`. Generic-alias spellings
  are now **transparent to inference**
  everywhere: a parameter pattern written `self: diag<U>` unifies and
  shape-checks as `pair<U, U>`. The same one-principle change admits
  **builtin-type qualifiers**: `fn int32::clamp` (or via `type myint =
  int32;`, `fn myint::clamp` — the same family) and
  `fn slice<T>::first(s: slice<T>) -> T` all work; specializing a builtin
  (`fn slice<int32>::first`) is rejected — `cannot specialize builtin type
  'slice'; spell the receiver type in the method's signature instead`. The
  two spellings of one signature collide as ordinary duplicates, `@override`
  pairs across them, and an alias-declared generic method's `.mci` stub
  pulls the alias declaration along even when the signature never names it.
- **Partial specialization of generic-struct methods —
  `fn Type<Concrete, U>::method(...)`** — a method's pre-`::` arguments may
  now **mix** concrete types and fresh type parameters: the concrete
  positions bind, the fresh names stay free, and the method becomes a
  template matching only receivers that agree on the concrete positions
  (`fn pair<int32, U>::m` matches every `pair<int32, X>`). Dispatch is the
  **existing overload ranking** — full specialization (concrete tier) beats
  a partial, whose concrete positions in turn out-score the fully generic
  method's bare names on pattern specificity; two rank-tied partials stay
  the standard ambiguity error (incomparable under the subsumption entry
  above — each holds a concrete type where the other holds a wildcard, so
  neither pattern is an instance of the other). A fresh
  position may carry a **closed type group, `extends` bound, or default**
  (`fn pair<int32, U: int8 | int16>::m`) exactly as in a declaration list —
  note the tier rule: a bounded *generic* method outranks an *unbounded*
  partial (a written commitment to a type set beats the open pattern), while
  a bounded partial reclaims the win on specificity. Fresh names prepend the
  method's own type parameters (`fn pair<int32, U>::pick<W>`), may not
  shadow them, and may not reuse a struct parameter name that a concrete
  position binds; decorating a concrete argument is rejected rather than
  silently declaring a parameter named `int32`. Partials travel verbatim
  through `.mci` interface stubs, bounds included.
- **Method specialization — `fn Type<Concrete>::method(...)`** — a method may
  now provide a **concrete body for one instantiation** of a generic struct,
  coexisting with the generic method and **outranking** it for a matching
  receiver. `fn point<float64>::magnitude(...)` runs for a `point<float64>`
  receiver while `fn point<T>::magnitude(...)` handles every other; the
  specialization is registered as an ordinary concrete overload of the
  qualified name, so the existing concrete-beats-generic ranking does the
  dispatch. Whether a pre-`::` argument is a type-parameter *name* or a
  concrete *type* is classified at codegen against the type environment, so
  **any** concrete type may specialize — a builtin, a user struct
  (`fn holder<widget>::m`), or a structured type (`box<int32>`, `int32*`). The
  arguments may be all-concrete (this specialization), all-parameter (a
  generic method), or — per the entry above — a mix (a partial
  specialization). A generic base is **not** required — a lone
  `fn box<int32>::only(...)` is just a concrete namespaced overload — and two
  bodies for one instantiation collide like any duplicate overload. A
  specialization's `.mci` stub prototype re-spells the annotated qualifier
  (`fn box<float64>::tag(self: box<float64>) -> int32;`), pulling in a type
  named only there, so the stub re-parses under the same annotation rule.
- **Generic-struct methods — `fn Type<T>::method(...)`** — a method may now be
  namespaced to a *generic* struct, with the struct's type parameters written
  before the `::` (`fn point<T>::magnitude(self: point<T>) -> float64`). The
  existing generic machinery applies unchanged, so one instance is
  monomorphized per element type and type arguments are inferred from the call
  (`point::magnitude(p)` binds `T` from `p`). **The qualifier must annotate
  its type parameters**: a declaration's bare `fn point::m` over a generic
  struct is the error `struct 'point' is generic; the method qualifier must
  annotate its type parameter(s), e.g. 'fn point<T>::m' or
  'fn point<float64>::m'` — only a complete type (non-generic, or fully
  defaulted so the bare name is a complete type use, the defaults filling)
  may be named bare, while *calls* stay bare (`point::magnitude(p)` looks
  the family up). The receiver is likewise **explicit**:
  there is no `point`-means-`point<T>` sugar, so the receiver and every
  parameter and return type must name their type arguments; a bare
  `self: point` keeps the ordinary arity error. A method may also declare its
  **own** type parameters after `::method`
  (`fn box<T>::combine<U>(const self: box<T>, extra: U) -> U`); the struct's and
  the method's parameters merge into one template, and a method type parameter
  may not shadow one of the struct's (`method type parameter 'T' shadows a type
  parameter of struct 'point'`). Explicit type arguments at a `::` call
  (`point<float64>::magnitude(...)`), call sugar, and constructors remain
  future work.
- **Methods — `fn Type::method(...)` namespaced to a struct, called as
  `Type::method(args)`** — the foundational, explicit-call slice of the
  Methods/OOP work. A function may be namespaced to a declared struct by
  qualifying its name with `Type::`, and is invoked by that same explicit
  qualified name. The qualified name is a single identity everywhere —
  registration, overloading, `@private`/`@override`, and the LLVM symbol all
  key on the `"Type::method"` string — so two structs may share a method name
  without colliding and a `Type::method` set overloads by argument like any
  other. `Type::` is purely a namespace: **no `self` convention is enforced**
  (no required receiver, name, or first-parameter type); the only rule is that
  the qualifier is a struct in scope (otherwise `no struct type 'foo' for
  method 'foo::bar'`). `Enum::Member` still parses as a value — only a `::`
  member followed by `(` is a qualified call. Call sugar (`p.method()`),
  constructors, dynamic dispatch, and non-struct/generic-struct receivers are
  not part of this slice; generic-struct methods (`fn list<T>::m`) do not
  parse.
- **`@override` — replace a same-pattern member of an open overload set** —
  adding an overload extends a set; `@override` is the escape valve for the
  one thing it cannot do, *replacing* a member that already covers a shape
  (the case that otherwise collides as a duplicate). It suppresses the
  duplicate-pattern collision and drops the overridden (unannotated)
  definition before code generation, so only the `@override` body is emitted
  under the member's shared symbol — a **global, order-independent**
  replacement, in effect everywhere the original was, including `println`'s
  own dispatch. The driving use case is replacing a stdlib formatter: a
  concrete `format(mut str, value: bool, ...)` or the unbounded `<typename>`
  fallback, swapped for your own. An `@override` needs exactly one
  source-visible, body-bearing, cross-module target of the same pattern; a
  missing target (typo protection), a same-file target, a prototype-only
  target, and two `@override` of one pattern are all compile errors, and it
  does not combine with `@extern`, `@static`, `@removed`, a bodyless
  prototype, or (for now) `@private`. See
  [Function overloading](docs/language.md#function-overloading) and
  [examples/functions/override.mc](examples/functions/override.mc).
- **`ok`/`error` compose as values in ternaries** — the constructors now
  behave as the builtins `ok<T, E>(v: T) -> result<T, E>` and `error<T, E>(e:
  E) -> result<T, E>`: the argument fixes one arm and the other is a free
  parameter bound by context *or by the sibling arm of a ternary*. So
  `return cond ? ok(v) : error(e);` type-checks with no annotation — the `ok`
  arm supplies `T`, the `error` arm supplies `E` — and the same holds when one
  arm is a constructor and the other an already-typed result. A direct result
  sink (a typed `let`/return/assignment/field/argument) still builds eagerly,
  so a struct-literal or string ok value keeps adapting to its arm. A ternary
  whose two arms are the *same* kind leaves one arm undetermined
  (`cond ? ok(1) : ok(2)` cannot know `E`) and must be annotated or the value
  lifted out (`ok(cond ? 1 : 2)`); a bare `ok(5);` with no result context is
  still rejected. See
  [Construction](docs/language.md#construction-ok-and-error) and
  [examples/types/error_handling.mc](examples/types/error_handling.mc).
- **Error handling stage 4: `-Wunused-result` and the `error_name` /
  `error_message` accessors** — the final language stage of the epic. **A
  new opt-in warning class**, `-Wunused-result` (default-off like the rest;
  `-Wall` enables it, `-Werror=unused-result` promotes it), reports a
  `result` produced in statement position and silently dropped — the
  accidental-error-discard hole the design exists to close. Every consuming
  form stays silent (a `let` binding, the `let v, err =` destructure, a
  `try` in any ending, passing the result as an argument, returning it);
  only a truly-dropped `f();` warns. The deliberate-discard suppressor is a
  `_` binding — `let _ = maybe_fail();` — using the conventional throwaway
  name (mcc has no special blank identifier). **Two rendering builtins**
  turn an error value into a `char*` at runtime: `error_name(err)` yields
  the variant's fully qualified name (`"my_error::NOT_FOUND"`), and
  `error_message(err)` yields its declared
  [display string](docs/language.md#error-declarations) when it has one,
  falling back to the bare variant identifier (`"NOT_FOUND"`) otherwise —
  the human-facing "why it failed". Both render through a compiler-synthesized per-declaration
  lookup keyed on the error's value (the reserved zero no-error state
  renders as the empty string),
  and both stay ordinary identifiers unless directly followed by `(`. The
  operand must be a declared error value (`error_name(5)` rejects).
  Automatic `{}` rendering of an error value stays a follow-up (the format
  machinery cannot yet enumerate user-declared types). See
  [Rendering](docs/language.md#rendering-error_name-and-error_message),
  [-Wunused-result](docs/language.md#-wunused-result), and
  [examples/types/error_handling.mc](examples/types/error_handling.mc).

- **Error handling stage 3: the rest of the `try` production — bare
  propagation, the `??` fallback, and the `try` statement** — a `try` now
  takes exactly one of three endings. **Bare `try g()`** propagates: on
  error the enclosing function returns `error(err)`, so its return type
  must be a result carrying the **same** declared error type
  (`result<T2, E>` or `result<E>`) — anything else, including `main`, is a
  compile error naming both types; on ok it yields `T`, composing as an
  ordinary operand, and is *not* implicitly wrapped (`return try g();` in
  a `-> result<T, E>` function errors — spell `return ok(try g());`). Over
  an error-only `result<E>` the bare form is statement position only:
  `try f();` is the propagate-or-continue consumer. A bare try inside a
  `defer` body is banned like the return it desugars to. **The `??`
  fallback**, `try g() ?? v`, discards the error and lazily evaluates a
  default instead (side effects never run on the ok path), coercing it to
  `T` with no requirement on the enclosing return type; the right-hand
  side is a full greedy expression, or an emit-block `{ ...; emit v; }`
  that may instead diverge. `??` (a new two-character token) is the
  loosest expression form — it binds **looser** than the ternary and every
  binary operator (just above assignment) and chains **right**, so the
  fallback extends greedily to the end of the expression:
  `try g() ?? 2 + 1` is `try g() ?? (2 + 1)`, `try g() ?? c ? a : b` is
  `try g() ?? (c ? a : b)`, and `try g() ?? p ?? q + 1` is
  `try g() ?? (p ?? (q + 1))`; parenthesize to operate on the unwrapped
  value (`(try g() ?? 0) + base`). The `??` directly after a bare try
  operand is always the try's own clause (structural, by production), its
  RHS that same greedy expression, so a trailing `?? q` nests inside it;
  the general coalesce production ships with every arm reserved: a result
  left of `??` unwraps through `try`, and the pointer arm waits on the
  pointer-truthiness roadmap item. A try takes one ending only
  (`try g() ?? v except (err) { }` is a parse error), and with bare try
  legal, `try g() + 1 except ...` now reads `(try g()) + 1` with a
  displaced handler — a parse error at `except`.
  **The `try` statement**, `try (ret = f()) { B } except (err) { H }`,
  binds a fresh `ret` scoped to `B` only (the `with`-head spelling and its
  `try ( IDENT =` statement probe), with an obligation-free handler and
  **no `else` arm** (the block already is the no-error arm); arity 2 only.
  All three editor grammars pick up the new forms (tree-sitter
  `try_expression` clauses + `try_statement` + `coalesce_expression`,
  helix/neovim queries, VS Code tmLanguage `??`). See
  [Propagation: bare try](docs/language.md#propagation-bare-try) and
  [examples/types/error_handling.mc](examples/types/error_handling.mc).

- **Error handling stage 2: the binding forms — `let ret, err = f();` and
  `try ... except`** — a `result` can now be *consumed*. Form 1, the
  C-flavored destructure `let ret, err = f();`, splits a `result<T, E>`
  into exactly two binders: on success `err` is the reserved zero no-error
  state (falsy by construction, so `if (err)` is a total check for every
  declared error type) and on failure `ret` is the zero value of `T` —
  lowered as a tag select that zero-fills the unselected binder, never a
  raw read of the other union arm's bytes. The error-only `result<E>`
  rejects (nothing to bind); tuple/slice destructuring is unchanged. Form
  A, the handler form: `try expr except (err) { H } [else { S }]` — `try`
  binds the call chain that follows and hands its error to the `except`
  clause, with `err` a plain copy of `E` scoped to the handler. Where a
  value escapes (a `let` initializer, a `return` value) the handler must
  diverge (`return`/`break`/`continue`/`panic`) or `emit` a fallback
  coercing to `T`; as a whole expression statement it is obligation-free,
  and that form is the `result<E>` consumer. `try` sits at unary level, so
  the whole form also composes as an ordinary operand
  (`1 + try f() except (err) { emit 0; }`). The optional `else` block is
  the **ok arm only** (Python's `try`/`except`/`else`): it runs on `ok(v)`
  and is *skipped* on the handler's emit-fallback path, though code after
  the statement still runs with the fallback. `emit` inside a handler
  targets the `try` expression like a block expression, which is exactly
  what keeps `try f() except (err) { emit fallback; }` legal inside a
  `defer` body while a handler that `return`s stays banned there by the
  defer-escape rules. Propagation is the explicit idiom
  `let v = try g() except (err) { return error(err); };` (same `E`;
  mapping between error types is the handler's job — no implicit
  coercion). `try` and `except` become reserved keywords (`except` never
  appears without its `try`; a bare `try g()` — the propagation
  expression — stays a staged compile error until the epic's next stage),
  and all three editor grammars highlight the form (tree-sitter
  `try_expression`, helix/neovim queries, VS Code tmLanguage). See
  [Consuming a result](docs/language.md#consuming-a-result-the-destructure)
  and [examples/types/error_handling.mc](examples/types/error_handling.mc).

- **Error handling stage 1: `error` declarations, `result<T, E>`, and the
  `ok()`/`error()` constructors** — recoverable errors as values, the
  recoverable complement of `panic`/`assert`. An `error` declaration
  (`error my_error { NOT_FOUND = "Not Found", IO_ERROR }`) names the
  failure causes as a **nominal**, `int32`-backed type: variants always
  auto-number from 1 in declaration order — error values are automatic,
  so there is no explicit `= n` form (a bare `= <int>` rejects) — giving
  dense `1..N` values where every variant is non-zero by construction and
  zero stays the reserved, unnameable no-error state. An error value
  supports truthiness (`if (err)`), `==`/`!=` against its own
  declaration, and `case` — but no arithmetic, no ordering, and no
  implicit integer conversion (`err as int32` reads the value out
  explicitly; nothing casts *into* an error type). A variant's `=` slot
  sets an optional display string (`NOT_FOUND = "Not Found"`), stored for
  the rendering stage and never affecting the numbering. `result<T, E>` / the error-only `result<E>`
  (no `void` type arguments, ever) is a builtin template on the
  `slice`/`tuple` pattern — a one-byte tag plus a union of the arms —
  whose `E` must be an `error` declaration; it returns, passes, stores,
  and infers through generics like any value, but exposes no fields.
  `ok(v)` / `ok()` / `error(e)` are the **only** constructors,
  context-typed like a bare struct literal (a `return`, typed `let`,
  assignment, argument, or field), with no implicit value↔result
  coercion in either direction. Declarations and result signatures
  round-trip through `.mci` stubs. The binding forms (`let ret, err =`,
  `except`, `try`) are the epic's next stages. All three editor grammars
  learn the `error name { ... }` declaration head (variants highlight as
  constants; `error(` stays an ordinary call). See
  [Error handling](docs/language.md#error-handling) and
  [examples/types/error_handling.mc](examples/types/error_handling.mc).

- **Constant-condition loop folding** — a loop whose condition folds to
  always-run at compile time (`while (true)`, `while (1)`, the dual
  `until (false)`, `const` references, constant arithmetic) no longer
  emits its never-taken exit edge, and with no `break` in the body no
  exit block at all: the loop **diverges**. That lifts two checks that
  used to demand dummy code — `fn f() -> int32 { while (true) {...} }`
  no longer needs a trailing `return` after the loop, and a block
  expression may end in a forever-loop that `emit`s from inside. The
  gate is the `break`: one anywhere in the body (a `case` arm, a nested
  block expression, a `defer`) keeps the exit block and the code after
  the loop live, while `return`/`emit`/`continue`/`@noreturn` calls
  never gate the fold. Code after a `break`-free forever-loop can now
  never run, so [`-Wdead-code`](docs/language.md#-wdead-code) reports it
  (`unreachable code: nothing runs after a loop that never exits`).
  `-O0` objects and `--emit-llvm` output lose the dead blocks (default
  `-O2` output was already clean). Out of scope by design: the
  never-runs duals (`while (false)` keeps its fully type-checked body,
  like `if (false)`) and `for` loops (every form exits on a runtime
  comparison). See [Control flow](docs/language.md#control-flow) and
  [forever.mc](examples/control-flow/forever.mc).

- **stdlib `panic` and `assert`** — `std/io` grows the report-and-abort
  guards: `panic(msg)` writes `panic: <msg>` verbatim to standard error
  (braces stay literal, so runtime text is always safe), and
  `panic(fmt, args...)` renders `{}` placeholders through the `std/format`
  set first — f-strings included, `panic(f"x = {x}")` being the idiomatic
  spelling. `assert(cond, msg)` / `assert(cond, fmt, args...)` panic with
  `assertion failed: <msg>` when the condition is false and do nothing
  otherwise (always enabled; a `-D`-gated release-stripping variant is a
  recorded follow-up). Termination is `abort()` — SIGABRT, exit status 134
  under a shell, no defers, no atexit handlers — with pending stdout
  flushed first so interleaved output survives. `panic` is
  [`@noreturn`](docs/language.md#noreturn-functions), so a trailing call
  satisfies missing-return and the `if (p == null) { panic("..."); }`
  guard narrows `p` (`assert(p != null, ...)` does not — facts stop at the
  call). En route, the f-string sink rule became a pre-ranking viability
  filter in overload sets: an f-string argument rules out every candidate
  that would not receive it at an `@format` format-string slot, so
  `panic(f"...")` resolves to the collector instead of erroring against
  the rank-winning verbatim member. See
  [Panic and assert](docs/language.md#panic-and-assert) and
  [panic_assert.mc](examples/functions/panic_assert.mc).

- **Editors: f-string interpolation highlighting** — the tree-sitter
  grammar (Helix, and Neovim through the shared parser) now parses the
  interior of an `f"..."` literal structurally: hole expressions are real
  expression nodes and highlight natively, with dedicated captures for the
  `{`/`}` hole delimiters, the trailing `=` inspector, the `:modifier`
  format spec, and `\x`/`{{`/`}}` escapes; plain `"..."` strings stay one
  opaque token, and the grammar gains its first `tree-sitter test` corpus
  (`test/corpus/f_strings.txt`). The VS Code TextMate grammar mirrors it:
  the quotes and literal-text runs carry the plain-string scope
  (`string.quoted.double`, so every theme colors them like any string),
  the holes sit outside any string scope and re-enter the full language
  (`meta.embedded`, so hole expressions render as code in every theme),
  and the hole braces use the JS-template punctuation scope
  (`punctuation.definition.template-expression`) that virtually every
  theme styles. One divergence from the compiler, which unescapes the
  literal before sub-parsing holes: a nested string literal spelled with
  escaped quotes (`f"{s == \"x\"}"`) doesn't parse in the tree-sitter
  grammar (TextMate reads it as escapes).

- **`mut` returns in function types — `fn(...) -> mut T`** — the return
  convention joins the parameter ones, lifting the last function-value
  ban: a [`mut`-returning](docs/language.md#mut-returns) function is now a
  legal function value, `let f = counter_ref;` infers the carrying type
  (the old "cannot take a function value ... it returns mut" rejection is
  gone), and a call through the value is the **same lvalue expression a
  direct call is** — assignable (`f() = v`, `f() += v`, field-held
  callees like `table.get() = v` included), projectable
  (`f(s).field = v`, `f(t)[i] = v`), re-lendable as another call's `mut`
  argument, and vouching in `mut`-return formation chains exactly like a
  named `-> mut` candidate (`return get(s).field;` through a fn-value
  `get`), with the same guarantees — the callee's own body passed the
  formation and storage rules when it compiled. Like the parameter
  conventions there is **no variance and no hatch**: a `fn() -> mut int32`
  call returns a pointer to the vouched storage where a `fn() -> int32`
  call returns the value, so the types are not convertible in either
  direction, and an `as` between them is rejected with an error that
  explains why no cast is offered (same-convention reinterprets and the
  `@nonnull`-stripping hatch still work; laundering through `uint8*`
  remains UB like any forged address). `fn() -> mut void` rejects per
  use, so `type getter<T> = fn() -> mut T` validates per binding, and
  `-> mut const T` is banned at parse time in both the declaration and
  fn-type slots (a mut return must be writable). The convention is part
  of the type's identity: `.mci` interface stubs spell it, templates
  instantiate the carrying and plain forms distinctly, and `&f()` stays
  rejected (the reference must not escape its full expression). See
  [mut/const-carrying function types](docs/language.md#mutconst-carrying-function-types)
  and [mut_return_callbacks.mc](examples/functions/mut_return_callbacks.mc).

- **`mut`/`const`-carrying function types — `fn(mut char)`,
  `fn(const struct big) -> int64`** — a function type now spells the
  per-parameter hidden-reference calling conventions, lifting the last
  parameter-side function-value ban: a function with `mut` or
  hidden-reference `const` (aggregate) parameters is a legal function
  value, `let f = my_func;` infers the carrying type (the old "cannot
  take a function value ... passed by hidden reference" rejection is
  gone), and a call through the value passes the same by-reference
  arguments and enforces the **same call-site rules as a direct call** —
  writable-lvalue-of-exact-type for `mut`, the
  `const`-parameter/`@volatile`/`@packed` rejections, and proven-non-null
  pointer decay included. Unlike the `@nonnull` contract there is **no
  variance and no hatch**: `fn(mut char)` and `fn(char)` receive their
  argument differently at the machine level, so the types are not
  convertible in either direction, and an `as` directly between two
  function types of differing `mut`/`const` shape is rejected with an
  error that explains why no cast is offered (same-shape reinterprets,
  including the `@nonnull`-stripping hatch, still work; laundering
  through `uint8*` remains UB like any forged address). `const` carries
  only where it changes the convention: on a by-value scalar it erases at
  type formation — `fn(const int32)` *is* `fn(int32)` — so a generic
  alias like `type cmp<T> = fn(const T, const T) -> bool` is inhabitable
  transparently at scalar and struct `T` alike, each binding classified
  per use. The convention is part of the type's identity: `.mci`
  interface stubs spell it, templates instantiate the carrying and plain
  forms distinctly, and prototypes must spell it exactly. Collecting
  functions ride along: `fn total(args...)` is a legal value of type
  `fn(const slice<const any>) -> ...`, whose calls take the trailing
  slice explicitly — collection and the compile-time `@format` desugars
  stay direct-call affordances. A `-> mut T` return was the one
  convention still inexpressible here; it ships in the entry above. See
  [mut/const-carrying function types](docs/language.md#mutconst-carrying-function-types)
  and [mut_callbacks.mc](examples/functions/mut_callbacks.mc).

- **`@nonnull`-carrying function types — `fn(@nonnull char*) -> int32`** —
  a function type now spells the per-parameter `@nonnull` contract,
  lifting the parent feature's remaining soundness ban: a function with
  `@nonnull` parameters is a legal function value, `let f = my_func;`
  infers the annotated type (the old "cannot take a function value"
  rejection is gone), and a call through the value runs the **same
  call-site null proof as a direct call** — literal null and unproven
  pointers are compile errors, while flow narrowing and the postfix `!`
  hatch apply identically. Assignability along the contract axis is
  contravariant: a plain function value flows into an annotated slot
  (`let`/assignment, struct fields, array elements, `@static` dispatch
  tables, arguments, returns), while dropping the contract is a compile
  error whose hint names the escape hatch — `f as fn(char*) -> int32`
  strips it explicitly as a free bitcast whose calls skip the proof (UB
  if an argument is actually null, mirroring `p!`). The contract is part
  of the type's identity: `.mci` interface prototypes spell it, a
  template instantiated with the annotated type is a distinct instance
  from the plain form, and a prototype must spell it exactly as its
  definition. One accepted asymmetry, by design: a value of a `@nonnull`
  `@extern` (`let f = strlen;`) checks strictly through the pointer,
  while direct extern calls keep grading by the `-Wextern-nonnull`
  posture (an indirect call can no longer be attributed). `@nonnull` in a
  function type applies to pointer parameters only, validated where the
  type is used, so a generic alias like `type cb<T> = fn(@nonnull T)`
  checks per binding; `@noalias` stays an unchecked hint that drops
  silently from a function value. See
  [@nonnull-carrying function types](docs/language.md#nonnull-carrying-function-types)
  and [nonnull_callbacks.mc](examples/functions/nonnull_callbacks.mc).

### Fixed

- **A hole-free f-string lost its `@format`-only semantics** — a hole-free
  `f"..."` (only plain text or escaped braces, `f"{{}}"`, `f"no holes"`)
  was collapsed to a plain string literal at parse time, so it slipped past
  the f-string sink rule: a verbatim overload could bind it (with the
  enlarged `print`/`println` families, `println(f"{{}}")` printed `{{}}`
  instead of `{}`) and a plain parameter accepted it silently. A hole-free
  f-string now keeps its f-string identity — it renders through the
  `@format` runtime like any other (`println(f"{{}}")` prints `{}`), and a
  non-`@format` sink rejects it with the usual *an f-string is only allowed
  as the format string of an @format call* error. The plain literal
  `println("{{}}")` is still the verbatim path (prints `{{}}`, unchanged).
- **An f-string passed as a collected extra was not always rejected** —
  with an `@format` callee carrying several overloads (`println`'s stdout
  and `FILE*` collectors), an f-string in a trailing collected-argument
  position (`println("{}", f"...")`) slipped through and compiled instead
  of raising *an f-string is only allowed as the format string of an
  @format call*. The rejection now fires against the resolved collector
  regardless of how many `@format` overloads the name has.
- **`void` as a generic type argument crashed the compiler** —
  `box<void>` (any generic struct instantiated with `void`) surfaced as
  a raw LLVM verifier error instead of a compile error. It is now
  rejected up front: `struct 'box' cannot take void as a type argument`
  (and the same guard is what keeps `void` out of `result<...>` through
  a generic).

- **Control flow escaping a `defer` body crashed the compiler** — `defer
  break;` (and `continue`/`return`/`emit` jumping out of a defer body)
  sent the generator into infinite recursion: the jump re-ran the very
  defer scope being unwound, aborting compilation with a Python
  `RecursionError`. Each is now a compile-time error at the offending
  statement — `'break' inside a defer body cannot exit the enclosing
  loop`, and likewise for `continue` (…`cannot continue the enclosing
  loop`), `return` (…`cannot exit the enclosing function`), and `emit`
  (…`cannot exit the enclosing block expression`). A loop or block
  expression opened *inside* the defer body still breaks/emits as usual.
  See [Defer](docs/language.md#defer).

## [0.7.0] - 2026-07-10

### Added

- **String interpolation — `f"..."` literals with `{expr}` holes and the
  Python-style `{n=}` inspector** — an `f`-prefixed string literal
  interpolates expressions directly: `println(f"x = {x}")` desugars at
  parse time into the sequential `println("x = {}", x)` — surface syntax
  only, no new runtime. The prefix separates the two brace grammars (in a
  plain literal `{x}` is the runtime *modifier*, in an f-string it is the
  *expression* `x`), a leftover `:` after the parsed expression carries a
  modifier through (`f"{x:08x}"`; a ternary's own colon stays inside the
  hole), and the inspector `f"{n=}"` follows Python wholesale: the hole's
  verbatim source text splices in as a label, whitespace preserved
  (`f"{n = }"` prints `n = 7`), with a modifier composing after the `=`
  (`f"{x=:08x}"`). An f-string is its own placeholder style — it never
  mixes with automatic `{}` or positional `{n}` placeholders, and extra
  arguments after one (`println(f"{x}", y)`) are a compile error, as are
  an empty hole, a bare `{:mods}`, and a stray or unclosed brace. `{{`/`}}`
  still escape literal braces, a hole-free `f"..."` degrades to a plain
  string literal, and an f-string is legal *only* as the format string of
  an `@format` call (`print`/`println`/`format_args`, both marshal paths)
  — every other sink is a compile error rather than a silently dropped
  hole (string-*valued* f-strings are a possible later extension; the
  legacy `-D PRINTF_PRINTLN=1` pair is unmarked and rejects them). See
  [Formatted print/println](docs/language.md#formatted-print--println)
  and [formatting.mc](examples/systems/formatting.mc).

- **Positional `{n}` format placeholders — compile-time sugar for `{}`
  print** — in a format string *literal*, `{n}` selects the n-th argument
  after the format string manually: `println("{0}, {0}", x)` desugars at
  compile time to the sequential `println("{}, {}", x, x)`, duplicating or
  reordering the arguments at the call site (each still evaluates exactly
  once, in source order), so the runtime parser stays sequential-only. In
  the positional form a `:` separates the index from the modifiers —
  `println("{0} {0:x}", n)` desugars to `println("{} {x}", n, n)` — and
  one string commits to one placeholder style: mixing automatic `{}` and
  positional `{n}`, an out-of-range index, and an argument no placeholder
  references are compile errors. Because an all-digit bracket now selects
  an argument, a bare field width in a literal is spelled with the
  index-less escape `{:N}` (`{:2}` desugars to the runtime `{2}` width;
  digit-leading modifiers with a base letter like `{06x}` are untouched,
  as is a *variable* format string — runtime brackets stay modifiers).
  The hook is the new `@format` parameter attribute: `std/io` marks
  `print`/`println`/`format_args`, and any collecting function may opt
  its own format string in by marking the `slice<const char>` parameter
  just before its `args...` — validated at declaration, desugared on both
  the direct and the overload/generic call paths, and carried through
  `.mci` interface stubs like `@nonnull`. See
  [Formatted print/println](docs/language.md#formatted-print--println).

- **Float format modifiers — precision and field width for `{}` print** —
  the `format` set's `float64` member now parses the `[[N].M]f` modifier
  grammar: `{.2f}` rounds to two decimals, `{.0f}` drops the point
  entirely, `{8.2f}` right-aligns the rendering in an 8-wide space-padded
  field (sign included), and a bare `{f}` (or `{}`) keeps the six-decimal
  default. The parsed width and precision feed the member's existing
  snprintf engine as `%*.*f`, so the rounding is the C library's, and
  out-of-grammar input degrades silently like the integer and string
  parsers (`{12f}` is a bare field width at the default precision). This
  was the last runtime modifier stage: libc's `printf` remains only the
  scientific-notation (`%g`/`%e`) tool. See
  [Formatting](docs/language.md#formatting) and
  [examples/systems/formatting.mc](examples/systems/formatting.mc).

- **Collecting functions overload and go generic (native variadics
  stage 2, the final stage)** — the stage-1 ban is lifted: a collecting
  function (trailing `args...` / `slice<const any>`) may now join an
  overload set or share a generic name — `fn log(args...)` beside
  `fn log(level: int32, args...)`, or `fn acc<T>(seed: T, args...)`,
  whose `T` binds from the fixed arguments only (the extras are
  type-erased). A collecting candidate is viable from its fixed count
  up, and the ranking is settled: a candidate that matches without
  collecting beats any that must collect, as the outermost rank
  component regardless of tier — an exact-arity generic beats a
  concrete collecting fallback (the C++ ellipsis-ranks-worst
  analogue) — a pass-through-shaped final argument counts as
  not-collecting at full specificity, a collecting candidate's
  specificity counts its fixed prefix only, more fixed parameters wins
  between collectors, and equal fixed counts with a tying fixed-prefix
  specificity stay the ambiguity error. No boxing happens before the
  winner is known — collection is emitted from the already-evaluated
  arguments (only a deferred array or bare-struct literal extra
  re-generates, reproducing the direct path's exact errors) — so
  overload resolution never changes what, or in what order, a call
  evaluates. C-style `...` variadics stay banned from overload sets
  (that lift belongs to the C variadics roadmap item), and `.mci`
  interfaces needed zero changes: the desugared type is the marker and
  the params-key mangling already distinguishes the members. This
  closes the native variadic arguments roadmap item. See
  [Native variadic arguments](docs/language.md#native-variadic-arguments),
  [Function overloading](docs/language.md#function-overloading), and
  [examples/functions/native_variadics.mc](examples/functions/native_variadics.mc).

- **Layout-equivalent struct casts (tuples stage 4, the final stage)** —
  a tuple casts to any struct with the same field types in the same
  order, and a struct back to its positional form: `(3, 4) as point`
  builds the struct (the literal's elements lower against the target's
  field types, like a typed `let`, so untyped constants adapt), and
  `p as tuple<int32, int32>` converts the other way, composing with
  destructuring to consume an existing struct by position
  (`let x, y = p as tuple<int32, int32>;`). Equivalence is exact and one
  level deep — field names never matter, a struct-typed field takes only
  the same struct type (never a recursively-equivalent tuple), and a
  `@packed`/`@align(N)` struct is never equivalent, its offsets or size
  diverging from the tuple's. Struct-to-struct casts stay nominal-only
  (`extends` upcasts), tuple-to-tuple conversion stays rejected, the
  result is a fresh value copy either way, the cast chains
  (`(1, 2) as point as tuple<int32, int32>` round-trips), and a rejected
  cast names the first divergence. The empty tuple and an empty struct
  convert on the same rule. This closes the `tuple<A, B, ...>` roadmap
  item: the `@extern` half of stage 4 had already shipped with stage 1,
  tuples crossing C boundaries as the layout-equivalent struct. See
  [Tuples](docs/language.md#tuples), [Casts](docs/language.md#casts),
  and [examples/types/tuples.mc](examples/types/tuples.mc).

- **Destructuring with the rest binder (tuples stage 3, slices too)** —
  `let a, b = t;` binds a tuple's positions to names, no parens, one
  ordinary local per position (`let q, r = divmod(9, 4);` — multiple
  return values bound by name at the call site), and the trailing-`...`
  rest binder takes the tail: `let a, rest... = t;` is `a = t[0]`,
  `rest = t[1:]`. Pure sugar over the shipped constant indexing and
  slicing — the source evaluates once, each binder takes its position's
  type (annotations are rejected), binders are fresh locals a `const`
  source never feels, and the binder count is checked against the arity
  at compile time: exact without a rest binder, at most the arity with
  one, the tail narrowing uniformly (`tuple<char, float64>`, the 1-tuple,
  `tuple<>`). The same rest binder lands on slice sources
  (`let first, rest... = s;`), the identical desugar onto unchecked
  indexing and sub-slicing: no length check, exactly like `s[i]`, and the
  tail is a view of the same storage where a tuple's is a copy. Arrays,
  `list<T>`, and string literals borrow first, as everywhere
  (`let a, b = arr as slice<int32>;`). See
  [Tuples](docs/language.md#tuples),
  [Sub-slicing](docs/language.md#sub-slicing), and
  [examples/types/tuples.mc](examples/types/tuples.mc).

- **`len()` on tuples** — a tuple's arity is recovered with the existing
  `len()` builtin, the same spelling arrays use: `len(())` is `0`,
  `len((x,))` is `1`, and the count is the same adaptable compile-time
  constant an array's `len` yields, comparing against an `int32` counter
  without a cast. Arity is purely a property of the type, so an rvalue
  operand needs no address — `len(divmod(7, 2))` works, the call still
  running for its effects. `len` also folds in constant expressions now,
  arrays included (`let ys: int32[len(xs)];` sizes an array), so it
  composes with tuples' constant index and slice bounds: `t[len(t) - 1]`
  is the last position and `t[1:len(t)]` the tail, both still checked at
  compile time. See [Tuples](docs/language.md#tuples) and
  [examples/types/tuples.mc](examples/types/tuples.mc).

- **Tuples of arity 0 and 1** — the tuple surface is now fully
  arity-agnostic, like the internals always were. `tuple<T>` spells the
  1-tuple and the trailing comma constructs it (`(x,)`; `(x)` stays plain
  grouping); `tuple<>` spells the empty tuple and `()` constructs it — a
  zero-sized unit value on the empty-struct precedent (`sizeof` 0), which
  declares, assigns, passes, returns, nests in arrays/fields/generic
  arguments, boxes by reference into a `const any`, and matches a `case
  type` arm like any other tuple. A slice may now keep any number of
  positions (`t[1:]` on a pair is the 1-tuple tail, `t[n:n]` the empty
  tuple), dissolving stage 3's rest-binder carve-out — `let a, t2... = t1;`
  will narrow uniformly all the way down — and a future statically-typed
  variadic's `T...` expansions need no arity carve-out either. Indexing an
  empty tuple is out of bounds (it has no positions), and `<>` stays
  tuple's alone: every other generic still takes at least one argument.
  Amends the stage 1 and 2 entries below. See
  [Tuples](docs/language.md#tuples) and
  [examples/types/tuples.mc](examples/types/tuples.mc).

- **`tuple<A, B, ...>`: constant slicing (stage 2)** — `t[n:m]` with
  compile-time-constant bounds narrows to the smaller tuple of positions
  `n` to `m-1`: the same half-open `[a:b]` grammar as sub-slicing, open
  ends included (`t[1:]`, `t[:2]`, the plain copy `t[:]`), each omitted
  bound folding against the arity. The result is a new tuple value, not a
  view — the kept positions are copied into the narrowed interned tuple, so
  slicing works on rvalue bases (`divmod(7, 2)[:]`), composes with indexing
  and itself (`t[1:3][0]`, `t[0:3][1:3]`), and is never a write target.
  Bounds share the constant-index discipline (they pick the result type)
  and are range- and order-checked at compile time
  (`0 <= n <= m <= arity`). See
  [Tuples](docs/language.md#tuples) and
  [examples/types/tuples.mc](examples/types/tuples.mc).

- **`tuple<A, B, ...>`: the core type, paren literal, and constant indexing
  (stage 1)** — a builtin heterogeneous, fixed-arity product: each position
  keeps its own statically-known type, so
  `fn divmod(a: int32, b: int32) -> tuple<int32, int32>` returns multiple
  values with no one-off struct. Constructed by the paren literal
  (`(a, b)` — a parenthesized expression with a top-level comma; `(x)` stays
  grouping) with struct-literal-style context coercion in typed positions
  and `int32`-anchored inference without one; indexed by compile-time
  constants only (`t[0]`, bounds-checked at compile time), elements being
  full lvalues (`t[0] = v;`, `t[1] += 1;`, `t[1][0]` nesting). Realized as
  an interned struct with positional fields, so whole-value assignment,
  by-value passing, `const` hidden references, `mut` lending, `sizeof` with
  padding, arrays/struct fields of tuples, generic inference through the
  shape, `.mci` stubs, and the by-reference `const any` box (with `case
  type` recovery) all ride the struct machinery; two same-shape tuples are
  one type across modules. `extends tuple<...>`, `==`, and owning `any`
  boxes are rejected; `type polar = tuple<int64, float64>;`
  names a tuple via the transparent alias. Slicing, destructuring, and the
  layout-equivalent struct cast land in later stages. See
  [Tuples](docs/language.md#tuples) and
  [examples/types/tuples.mc](examples/types/tuples.mc).

- **Global/`@static` `any` initializers** — an `any` global now takes a
  constant initializer: the const-initializer path boxes any compile-time
  constant a scalar global accepts into a constant tagged 24-byte aggregate,
  under the same tags runtime boxing produces (`@static let g: any = 5;`
  boxes as int32 by the untyped-literal placeholder rule, a string literal
  boxes as `char*`, a constant pointer cast under its own pointer tag), and
  a type boxed only at global scope still reaches generic `case type` arm
  monomorphization. The owning-box rules are unchanged: a struct, union,
  array, or bare `null` initializer is rejected with the same messages as
  runtime boxing — a global is an owning slot even declared `const any`.
  See [The any type](docs/language.md#the-any-type) and
  [examples/types/any.mc](examples/types/any.mc).

- **`writestr`/`writeln` take any char-slice-shaped value** — the io
  writers gain `T extends slice<char>` overloads, so a `string` or
  `list<char>` writes with no explicit `as slice<char>` borrow at the call
  site (`writeln(s)`), re-lending into the concrete slice member a bare
  string literal still adapts to — the same bounded pattern
  `string_append` and the string `equals` members already use.
  `print`/`println` dogfood it internally.

- **String format modifiers: field widths, and a null-safe `char*`** — the
  `format` set's string members (`char*` and `slice<const char>`) take the
  `[N][s][N]` field-width grammar: digits before the `s` right-align the
  text in an N-wide field (`{20s}`, or a bare `{20}`), digits after it
  left-align (`{s20}`); text at or past the width appends unpadded.
  `char*` wraps itself in a strlen-measured slice and delegates to the
  slice member, so both speak the same grammar — and a null `char*` now
  renders `(null)` instead of being undefined behavior. String field
  widths leave the libc-`printf` escape hatch; float precision followed
  in its own stage (the entry above).

- **Integer format modifiers: base, width, and zero-padding** — the
  `format` set's integer members now speak the `[0][width][x|X|b|p]`
  modifier grammar, hand-rolled in one digit worker with no snprintf
  round-trip: `"x"`/`"X"` hex, the new `"b"` binary, `"p"` pointer-style
  (`0x2a`), an optional decimal width, and a leading `0` for zero-padding
  — so `println("{08x}", n)` works. A space width counts the whole field
  (sign and `0x` included); a zero width counts the digits alone, the sign
  and `0x` sitting outside the zeros (`-42` under `{08p}` is
  `-0x0000002a`). Negative values now render **sign-and-magnitude** — the
  base applies to `|value|`, so `-4` with `x` is `-4`, no longer the
  64-bit two's-complement pattern (cast the bits unsigned to render that)
  — and the magnitude is taken by two's-complement negation in uint64
  space, so `int64`'s minimum renders exactly. String field widths and
  float precision landed in their own stages (the entries above).

- **Formatted `{}` `print`/`println` is now the default** — `std/io`'s
  `print` and `println` format with `{}` placeholders, type-driven through
  the `std/format` overload set: `println("{} + {} = {}", 2, 3, 2 + 3)`,
  with `{[modifiers]}` passing the bracket content verbatim as the per-type
  modifier (`{x}`/`{X}`/`{p}` on integers, `{y}`/`{yes}` on bools, per
  element on slices) and `{{`/`}}` escaping literal braces. The signature is
  `fn println(const fmt: slice<const char>, args...)`: a string literal
  adapts to `fmt`, the native variadic collects the arguments into a
  `slice<const any>`, and a `format` overload you write makes your type
  printable straight through `println("{}", value)` (a struct boxes by
  reference, no copy). The legacy printf-style pair is kept behind
  `-D PRINTF_PRINTLN=1` for programs mid-migration, and with the `{...}`
  modifier stages landed (the entries above) libc's `printf` remains only
  the scientific-notation (`%g`/`%e`) tool (positional `{n}` landed as
  exactly the planned compile-time sugar — the entry above). The example
  suite, the docs, and the smoke tests all
  speak `{}` now; the only visible renderings that changed are deliberate
  (`true`/`false` for bools instead of printf's `1`/`0`). See
  [Formatted print/println](docs/language.md#formatted-print--println).

- **Whole-build `-Wall -Werror` in CI and the stdlib build** — the CI
  example-compile loop, the wheel smoke tests (CI's package job and
  `test.sh`), and `build.sh` now run `-Wall -Werror`, promoting all three
  opt-in warning classes (`-Wunchecked-dereference`, `-Wdead-code`,
  `-Wextern-nonnull`) to hard errors over the whole build — examples,
  bare-metal kernel, cross-compiled ABI example, and standard library alike.
  The example suite went warn-free for it: each invariant-backed dereference
  asserts with postfix `!` or seeds a narrowed `let ...!` binding (and
  `libc/errno`'s two `*errno_location()` sites join the earlier container
  sweep). The three own-class demos that keep live triggers on purpose —
  `systems/extern_nonnull.mc`, `control-flow/dead_code.mc`,
  `types/unchecked_dereference.mc` — are compiled at plain `-Werror` instead
  (a warning-class demo cannot build with its own class promoted to error),
  extending the carve-out `extern_nonnull.mc` already had.

- **Boxing a struct into `any` by reference** — a struct now boxes into a
  `const any` target, lifting the v1 aggregate rejection for the call-scoped
  borrow case. The box is **by hidden reference**: the payload holds a pointer
  to the value's existing storage (the same convention a `const`/`mut` struct
  parameter already travels through), tagged as the struct type itself
  (`point`, not `point*`), so `case type` recovers it as a read-only alias
  with no copy — a `when point p:` arm reads the caller's fields directly and
  can hand `p` on to a `format(const value: point, …)` overload sharing that
  same storage. The archetypal `const any` position is the `slice<const any>`
  a variadic collects into, so `println("{}", p)` now boxes `p` by reference
  and dispatches it to a user formatter (native `println`). A bare variable's
  storage is shared directly; an rvalue struct (a literal, a function return)
  spills to a call-scoped temporary first. `point` and `point*` keep distinct
  tags, and a generic `when T v:` arm recovers a struct tag by reference too.
  Scoping the borrow to a slot that cannot outlive the call is what keeps it
  sound: an **owning** `any` of a struct (`let a: any = s;`, a `return`, a
  global) stays rejected with a reworded error pointing at the `const any`
  allowance, and **unions** and **fixed arrays** keep their pointer
  escape-hatch errors. See [The any type](docs/language.md#the-any-type) and
  [examples/types/any_struct_boxing.mc](examples/types/any_struct_boxing.mc).

- **Bare, type-inferred struct literals** — a struct literal may drop its type
  name where the position already fixes the struct type: `let p: point = { x =
  1, y = 2 };` instead of `point { x = 1, y = 2 }`, the aggregate sibling of the
  way `[...]` and `"..."` adapt to a `slice<T>`. It is allowed in every position
  a slice literal is — a type-annotated `let`, an assignment (`p = { ... }`,
  `*out = { ... }`, `a[i] = { ... }`, `s.field = { ... }`, and a mut return), a
  `return`, a function argument, an array/slice element, and a nested struct
  field — and, being a value copy rather than a borrow, adapts in a `return`
  with no lifetime concern. Unions work the same (`let v: u = { i = 5 };`), and a
  bare literal of constant fields initializes a `@static` global. As an argument
  it resolves against the parameter's struct type and, among **overloads**, is
  picked by its field names (`{ x, y }` fits `point`, not a `box` of `w`/`h`); it
  can never itself infer a generic type parameter (it carries no type). A bare
  `{` is told from a block-expression syntactically — struct fields are
  comma-separated, block statements semicolon-terminated — so existing
  block-expressions are unaffected. Two positions do not infer it: a `for x in
  <expr> { ... }` header (as with the keyword-free named form) and a ternary arm
  (name the type there). See
  [examples/types/struct_literals.mc](examples/types/struct_literals.mc).
- **libc `div` / `ldiv` / `lldiv` bindings** — the integer division functions
  that return their quotient and remainder together, now bindable because a
  by-value struct return crosses the `@extern` C boundary correctly. Adds the
  `div_t` / `ldiv_t` / `lldiv_t` struct types and the three functions to
  [libc/stdlib](lib/libc/stdlib.mc); they were previously impossible to bind
  (a struct return was not ABI-compatible). `div_t` is one 8-byte register,
  `ldiv_t`/`lldiv_t` a 16-byte register pair.
- **C struct-passing ABI: x86-64 (System V and Windows)** — the by-value
  struct/union `@extern` classification now covers **x86-64 System V** and
  **x86-64 Windows (Win64)** in addition to AArch64, lifting the previous
  non-AArch64 compile error for those targets. System V splits an aggregate of
  ≤16 bytes into eight-byte chunks, passing each in a general-purpose (`i64`) or
  SSE (`double`) register — so `{ float64; float64; }` returns in two SSE
  registers and `{ int32; float64; }` uses one GPR and one SSE — and passes a
  larger aggregate `byval` on the stack; a return over 16 bytes uses `sret`.
  Because the LLVM backend will not demote a register aggregate when the
  argument registers run low, the frontend now replicates the C compiler's
  register accounting (six integer, eight SSE; an `sret` return consumes the
  first integer register) and demotes a no-longer-fitting aggregate whole to a
  `byval` argument rather than splitting it. Win64 passes an aggregate of
  exactly 1/2/4/8 bytes in one integer register (no SSE for aggregates) and any
  other size indirectly (`sret` for a return over 8 bytes). `@packed`/`@align`
  are honored on every target. Unsupported targets (riscv64, unknown) keep the
  reworded, target-named compile error. The AArch64 and System V paths are
  link-verified against a C fixture in CI (arm64 and x86-64 runners); Win64,
  which has no runner, is verified by IR shape only. The classifier moves behind
  a new `classify_signature(ret, params, target)` dispatcher in
  `mcc/codegen/abi.py`; the shipped AArch64 classification is unchanged. See
  [C ABI compatibility](README.md#c-abi-compatibility),
  [Extern declarations](docs/language.md#passing-structs-by-value-across-the-c-boundary),
  and [examples/systems/c_struct_abi.mc](examples/systems/c_struct_abi.mc).
- **C struct-passing ABI (AArch64), mcc calling C** — an `@extern` function may
  now take or return a `struct`/`union` **by value**, classified for the
  platform C ABI so the aggregate crosses the boundary correctly. Only the
  `@extern` call boundary is affected: mcc's own calls keep their raw-aggregate
  convention (whole struct as an LLVM aggregate, `const`/`mut` struct parameters
  by hidden reference), and the two conventions stay distinct. The
  classification is Apple/AAPCS64: a homogeneous float aggregate (1–4 `float64`
  members) passes in FP registers, any other aggregate ≤16 bytes in
  general-purpose registers (`i64` or `[2 x i64]`), and a larger one indirectly
  — an argument by a pointer to a caller-owned copy, a return through a hidden
  `sret` pointer (the function returns `void`). A `union` is never a float
  aggregate. `@packed`/`@align` are honored. This is why libc's `div`/`ldiv`
  (small structs returned in a register / a register pair) now work directly. A
  new `mcc/codegen/abi.py` module holds the classifier; the shape matrix
  round-trips against a linked C fixture in the test suite. See
  [C ABI compatibility](README.md#c-abi-compatibility),
  [Extern declarations](docs/language.md#passing-structs-by-value-across-the-c-boundary),
  and [examples/systems/c_struct_abi.mc](examples/systems/c_struct_abi.mc).
  **On any non-AArch64 target** (x86-64, Windows), a by-value-struct `@extern` is
  a compile error (`passing a struct by value across the C boundary is not
  supported on target '…' yet; pass a pointer instead`) — pass a pointer there
  instead; those targets, and the reverse (C calling into mcc) direction, remain
  [on the roadmap](ROADMAP.md#planned).
- **String literals adapt in slice assignment** — a string literal (or a
  ternary of them) now borrows into an existing char-slice lvalue with no
  explicit `as`, the final position in the string/array-literal adaptation
  family (joining `let`, `return`, array element, function argument, `@static`,
  and struct field). `s = "hi";` repoints `s` at the literal's global string
  constant (NUL-dropped, so `.length` is the new literal's) — the same borrow a
  `let` does. Because a string constant is static-lifetime, the reborrow is
  safe even when the target outlives the frame, so it reaches all five
  assignment lvalue forms: a plain name, a deref (`*out = "hi";`), an index
  (`a[i] = "hi";`), a member (`c.name = "hi";`), and a mut return
  (`f(...) = "hi";`). The member form closes a real inconsistency the
  struct-field work opened — `cmd { name = "hi" }` (struct literal) worked, but
  `c.name = "hi"` (member assignment) did not. **Array-literal assignment stays
  a compile error** (`s = [1, 2, 3];`): the materialized backing array is
  frame-local, but an assignment target can outlive the frame, so the borrowed
  view would dangle — the same lifetime hazard that rejects
  `return [..] as slice<T>;`. See [Slices](docs/language.md#slices) and
  [examples/memory/slice_assignment.mc](examples/memory/slice_assignment.mc).
- **String and array literals adapt in struct-literal fields** — a string or
  array literal (or a ternary of them) in a struct-literal field whose declared
  type is a char slice / `slice<T>` now borrows into that field with no explicit
  `as`, the last position in the string/array-literal adaptation family (joining
  `let`, `return`, array element, and function argument). `cmd { name = "ls" }`
  on `struct cmd { name: slice<const char>; ... }` borrows the string
  (NUL-dropped, so `.length` is `2`), and `nums { xs = [1, 2, 3] }` on
  `struct nums { xs: slice<int32>; ... }` views a hidden backing array; a
  `= default` field whose default is such a literal adapts the same way. To
  make this possible, `gen_struct_lit` was restructured to thread each field's
  *raw* AST node to the store step and resolve the struct type first, instead of
  pre-evaluating every field before any field type was known. A literal field
  never drives a generic struct's type inference: `box { v = "hi" }` on
  `struct box<T> { v: T; }` still monomorphizes to `box<char*>` (a bare type
  parameter is no adaptation target), and a literal adapts only once the field
  type is a concrete slice — from the declaration, a companion typed field that
  fixes the parameter (`row { name = "x", val = seven }` infers `T` from `val`),
  or explicit type arguments. **Evaluation order:** inferring a generic struct's
  type arguments requires evaluating the non-literal fields before borrowing the
  literal ones, so in the generic-without-explicit-args case an array-literal
  field's element expressions run after a later non-literal field's — a narrow,
  documented reorder mirroring the argument path; string-literal fields are
  side-effect-free and the non-generic path evaluates strictly left to right.
  `@static` struct and union literals already folded such fields to constant
  `{pointer, length}` views and are unchanged. See
  [Slices](docs/language.md#slices), [Strings](docs/language.md#strings), and
  [examples/types/struct_literals.mc](examples/types/struct_literals.mc).
- **Struct and union `@static` global initializers** — a struct or union
  literal may now initialize a `@static`/global variable, folded to a data
  constant at compile time instead of requiring a runtime assignment. This
  lifts the former "a global union initializer is not supported yet" rejection
  and, in the same change, fills the const-initializer path's missing
  struct-literal arm (so `@static let p: struct point = point { x = 1, y = 2 };`
  compiles at all). Fields fold recursively — nested struct, array, and slice
  fields all compose, omitted fields stay zero or take their `= default`, and
  generic aggregates monomorphize before folding. A union constant is sized to
  the whole union with the written member's bytes first and the rest zero,
  exactly the storage the runtime literal produces; because the written member
  is usually narrower than the union's widest (representative) member, the
  constant takes an ad-hoc `{member, [pad x i8]}` storage type (what clang
  emits), and a single normalizing bitcast in `var_addr` presents the global
  as the union type for whole-value loads and by-value passing. The `any`-typed
  global initializer stays rejected. See
  [Unions](docs/language.md#unions), [Structs](docs/language.md#structs), and
  [examples/types/static_initializers.mc](examples/types/static_initializers.mc).
- **Pointer arithmetic** — pointers join the binary and compound operator
  surface with C's element-scaled semantics and no bespoke syntax. `p + n` and
  `p - n` advance a pointer by `n` elements (`p + n` is exactly `&p[n]`, so `n`
  is any integer type, scaled by `sizeof(pointee)`), and the compound forms
  `p += n` / `p -= n` follow from compound assignment's existing operator
  reuse. `p - q` requires two pointers of identical type and yields their
  signed element distance as an `int64`; the ordering relationals
  `<` `<=` `>` `>=` extend to pointers of identical type (the `while (p < end)`
  scan-loop idiom), joining the `==` / `!=` and `!= null` checks that already
  worked. `uint8*` is the raw-memory pointer, so its element size is 1 and its
  arithmetic is byte arithmetic. In `p - q` and the relationals a `const`
  qualifier on the pointee is ignored, so `int32*` and `const int32*` compare
  and subtract without an explicit cast. Pointer arithmetic is an
  always-non-null source: `p + n` proves non-null at a
  [`@nonnull`](docs/language.md#nonnull-parameters) slot exactly as `&p[n]`
  does, and `*(p + n)` never warns under
  [`-Wunchecked-dereference`](docs/language.md#-wunchecked-dereference) (the
  derived address is proven like `*&p[n]`; v1 does not look through to the base
  pointer); `p += n` is a reassignment that drops a narrowed local's non-null
  fact and stays rejected on a `@nonnull` parameter. Everything else keeps its
  rejection: addition is **pointer-left only** (`p + n` is accepted, the
  commuted `n + p` is rejected with a spelling hint), and `p + q`, the
  multiplicative operators `*` `/` `%`, the bitwise operators, the shifts,
  any arithmetic on a function pointer (they keep `==` / `!=` only), and any
  `null` operand all stay errors. v1 is a runtime expression only — not
  available inside a `const` initializer or an `@if` condition. This reverses
  the language reference's former exclusion ("there is no pointer arithmetic;
  use `&p[1]`"). Documented under
  [Pointers](docs/language.md#pointer-arithmetic); see
  [examples/systems/byte_scan.mc](examples/systems/byte_scan.mc).
- **Nominal type-parameter bounds** — a generic function parameter may now be
  constrained to a struct and its declared `extends` lineage:
  `fn describe<T extends shape>(x: T*)` admits `shape` and any struct that
  reaches it through an `extends` clause, transitively. The bound is
  **nominal** — the same [nominal struct subtyping](docs/language.md#structs)
  relation the upcast and slice-borrow use — so a struct that merely shares
  `shape`'s field prefix, with no declared lineage, is **rejected** where a
  structural rule would have accepted it. Deduction is unchanged: the bound is a
  post-deduction viability filter, and a call whose deduced `T` is not a subtype
  (a layout twin, an unrelated struct, or a non-struct like `int32`) is a
  compile error at the call site naming both — `blob does not satisfy the bound
  shape of 'describe'` — with explicit type arguments (`describe<blob>(...)`)
  checked the same way. Unlike a [closed type group](docs/language.md#closed-type-groups),
  the satisfying set is **open-ended**, so checking is **lazy** per
  instantiation rather than eager. The bound target must be a concrete struct
  (an unknown, non-struct, or union target errors at the declaration; referencing
  a type parameter, `<S, T extends S>`, is deferred) and may be a fully-applied
  generic or alias instance (`extends pair<int32, V>`, `extends ipair<char>`). A
  bound composes with a [default](docs/language.md#type-parameter-defaults)
  (`<T extends shape = circle>`), which must itself satisfy the bound (checked at
  the declaration), and may not sit beside a group on one parameter. Bounds slot
  into the same overload-ranking middle tier as groups — **concrete beats
  bounded generic beats unbounded generic** — so one bounded overload may coexist
  with an unbounded fallback (two same-pattern bounded overloads still collide,
  an open set being unprovable disjoint). The bound joins the template
  [symbol base](docs/language.md#template-symbols)
  (`describe<$0 extends shape>($0*)`) and `.mci` interface stubs (pulling its
  target struct in), so a re-imported bounded template enforces identically. This
  is the open-set, function-declaration sibling of closed type groups, built on
  the nominal subtyping foundation. Bounds on generic *struct* parameters remain
  unsupported in this version. See [Bounds](docs/language.md#bounds) and
  [examples/types/bounds.mc](examples/types/bounds.mc).
- **Nominal struct subtyping** — the struct subtype relation now follows the
  declared `extends` lineage instead of a matching layout prefix. The two sites
  that accept one struct where another is expected — the value/pointer upcast
  (`p as struct point`) and the borrow of a struct to a `slice<T>`
  (`list as slice<T>`) — participate only when the source **is** the target or
  names it, transitively, in an `extends` clause. A struct that merely shares a
  base's field prefix, with no `extends` between them, no longer upcasts or
  borrows; sibling brands over one base still never interconvert (each upcasts
  to the shared base, neither to its sibling). The bare-parameter base
  (`struct entry<T> extends T`), generic bases (`extends pair<K, V>`), the
  bodyless specialization (`struct meters extends int_wrapper;`), and the
  element-const axis (a `list<T>` borrowing to both `slice<T>` and
  `slice<const T>`) all keep working — every one routes through a declared base.
  The prefix layout stays the mechanism (base fields first, so the upcast is a
  zero-cost reinterpret and the borrow reads `{data, length}` straight across),
  but the declared lineage, not a coincidental layout twin, is now the
  definition. This settles a single nominal subtyping model across the language
  and is the foundation for generic-parameter `extends` bounds. See
  [struct `extends`](docs/language.md#structs) and
  [Slices](docs/language.md#slices).
- **Generic type aliases** — a `type` declaration may now carry a
  type-parameter list, naming a *family* of existing types:
  `type entry<T> = pair<char*, T>;` (a wider generic partially applied) and
  `type cmp<T> = fn(T, T) -> bool;` (a comparator shape over any element). The
  alias stays **transparent** — a type-level function expanded at each use,
  minting no monomorphized artifact of its own — so `entry<int32>` *is*
  `pair<char*, int32>` and the two spellings share one struct instantiation.
  Arity is checked at the use site (a bare `entry` or a wrong argument count is
  an error, e.g. `type alias 'entry' expects 1 type argument(s), got 0`,
  replacing the old blanket "type alias is not generic"), the target resolves
  with only the alias's own parameters bound (an outer generic's same-named
  parameter never leaks in), and the name-based cyclic-alias rule still rejects
  `type node<T> = pair<T, node<T>*>;`. An unused parameter is inert
  (transparency makes `boxed<bool>` and `boxed<char>` the same type, unlike a
  struct's nominally-distinct instantiations). Alias parameters take
  [defaults](docs/language.md#type-parameter-defaults)
  (`type record<T = int64> = ...;`); parameter **bounds** do not extend to
  alias parameters yet (deferred to the bounds item). The `.mci` round-trip
  renders the parameter list and stops counting the alias's own parameters as
  external references. See
  [Generic aliases](docs/language.md#generic-aliases).
- **libmc swept warn-free under `-Wunchecked-dereference`** — every
  warning-bearing standard-library module now asserts its invariant-backed
  dereferences with the postfix `!`, so importing them and enabling the class
  reports only the caller's own unproven sites, never libmc-internal ones. The
  sweep covers the container modules (`list`, `ring`, `stack`, `queue`,
  `dict`, `set`, and `equality`) and the hashing modules (`md5`, `murmur3`,
  `fnv1a`): index bodies (`self.data![i]`), the iterator protocols
  (`it!->obj!->data![i]`), the linked-node chains in `queue`, and the raw
  `uint8*` buffer walks in hashing. Because `!` is a purely static assertion
  that emits no instructions, the sweep is IR-identical — the code generated
  for any program is byte-for-byte unchanged. A postfix `!` is now also
  accepted inside a `mut`-return lvalue chain (e.g. `list_at`'s
  `return self.data![i];`), which is likewise IR-neutral. A CLI acceptance
  test instantiates and drives every swept module under
  `-Werror=unchecked-dereference` to pin libmc warn-free. See
  [-Wunchecked-dereference](docs/language.md#-wunchecked-dereference).
- **libc bindings annotated `@nonnull`** — the `@extern` libc binding surface
  now marks its null-hostile pointer parameters `@nonnull`: the `str*`/`mem*`
  functions (`libc/string`), the `strto*`/`ato*` inputs and `getenv`
  (`libc/stdlib`), the pointer-out math functions `frexp`/`modf`/`remquo`/`nan`
  (`libc/math`), and `mktime`/`asctime`/`strftime`/`localtime`/`gmtime`/`ctime`
  (`libc/time`). Slots where C gives `null` a meaning are deliberately left
  plain — `strtok`'s continuation, a `strxfrm` with count `0`, the `strto*`
  `endptr`, `free`/`realloc`, `system(null)`, `time(null)`. The annotations
  only bite under [`-Wextern-nonnull`](docs/language.md#-wextern-nonnull) (the
  default relaxed posture accepts any argument), and this repository's CI now
  compiles the example suite with `-Wextern-nonnull`, so the libc contract is
  enforced there. `libc/stdio` is left unannotated for now (its
  `null`-meaningful slots need a closer pass). See
  [Reaching libc](docs/language.md#reaching-libc).
- **`-Wextern-nonnull` — graded enforcement for `@nonnull` on `@extern`
  declarations** — a possibly-null argument to a `@nonnull` slot on a foreign
  `@extern` declaration is now graded by three postures over one default-off
  warning class, instead of the flat hard error it shared with native
  `@nonnull`. **relaxed** (the default, no flag) silently accepts it — the
  posture a mechanical C port builds under, so `strcpy`/`strlen`/`memcpy`
  calls no longer hit a null-proof wall; **warn** (`-Wextern-nonnull`, or
  `-Wall`) reports it as a `[-Wextern-nonnull]` warning; **strict**
  (`-Werror=extern-nonnull`, or a global `-Werror` with the class enabled)
  makes it a hard error again. Native (non-extern) `@nonnull` never joins the
  class — its possibly-null case stays a hard error at every posture — and
  passing the `null` literal to an extern `@nonnull` slot is always a hard
  error. The LLVM `nonnull`/`dereferenceable` hint on the extern declare is
  sound only under unconditional caller proof, so it is emitted only at the
  strict posture (native declarations always keep it). The class is off by
  default: CI and existing builds are unaffected. See
  [-Wextern-nonnull](docs/language.md#-wextern-nonnull) and
  [examples/systems/extern_nonnull.mc](examples/systems/extern_nonnull.mc).
- **Selective `-Werror=<class>`** — a new driver input form that promotes a
  single warning class to error level without the whole-build promotion of a
  bare `-Werror`. It enables the class and marks it error-level (repeatable),
  is general to any registered class (e.g. `-Werror=unchecked-dereference`),
  composes with a global `-Werror`, and rejects an unknown name with the same
  `mcc: error: unknown warning class 'name'` an unknown `-W<name>` gives. The
  spelling mirrors the `[-Werror=<name>]` promotion render that already
  existed. See [Selective -Werror=<class>](docs/language.md#selective--werrorclass).

- **Sub-slicing** — `s[start:end]` on a `slice<T>` yields a new rvalue slice
  viewing the same storage, `{ &s.data[start], end - start }`. Either bound
  may be omitted: `s[1:]` defaults the end to `s.length`, `s[:2]` the start
  to `0`, and `s[:]` is a plain copy of the view. The result type is the
  receiver's verbatim — a sub-slice of `slice<const T>` stays
  `slice<const T>` — and bounds have index parity (any integer type, widened
  by its own signedness). Bounds are unchecked, like indexing: an
  out-of-range pair is undefined behavior, while `s[n:n]` is the defined
  empty view over a real one-past-end pointer. Receivers are slice-typed
  expressions only; arrays, lists, and string literals borrow first
  (`(arr as slice<int32>)[1:]`), with a compile error suggesting the
  spelling. No negative indices and no step form (`::` stays one token, so
  `s[::2]` is a parse error). The tree-sitter grammar follows (the
  tmLanguage needed no change). See
  [Sub-slicing](docs/language.md#sub-slicing).
- **stdlib: the accessor triad lands in `list`, `string`, and `ring`** —
  the containers grow the settled `_get`/`_has`/`_at` accessor shape.
  `list_has`/`string_has`/`ring_has` are the domain predicates: `const
  self`, true exactly when the index is in bounds (`ring_has` takes the
  logical index from the front). `list_at`/`string_at`/`ring_at` are the
  unchecked mutable accessors — the first `mut` returns in libmc: each is
  `(mut self, index) -> mut T`, so `list_at(xs, i) = v`,
  `string_at(s, 0) = '/'`, and `ring_at(r, i) += 1` write in place
  (`ring_at` through the head-offset modular position), while value
  context copies out. Out of bounds is undefined — guard with `_has`, or
  use the checked `_get`; the returned lvalue points into the container's
  heap storage, so consume it before anything that can grow the
  container. The string members are `@inline` wrappers over the list
  ones, and `list_get`/`list_set` now route their bounds checks through
  `list_has` (behavior unchanged). **Breaking** (pre-1.0): `ring_at`
  flips from `(const self, index) -> T` to `(mut self, index) -> mut T`
  — read-only call sites keep working (value context loads a copy), but
  a `const ring<T>` receiver no longer re-lends into it. See
  [mut returns](docs/language.md#mut-returns).
- **`mut` returns** — a function declared `-> mut T` returns an lvalue: a
  reference to caller-reachable storage, so
  `fn buf_at(mut self: struct buf, i: uint64) -> mut char` makes
  `buf_at(b, 0) = '/'` legal. The call expression is assignable,
  compound-assignable (addressed once), a base for projections
  (`f(s).field = v`, `f(s)[i] = v`), and re-lendable as a `mut` argument on
  both call paths; in value context it loads the current value. To keep the
  reference from dangling, the callee's `return` obeys a strict formation
  rule: the lvalue must be formed from a `mut`/pointer parameter or a
  global, traced through members, elements, dereferences, and other
  `mut`-returning calls — every local root is rejected (as are by-value and
  `const` parameter roots, and returning a pointer parameter itself), the
  lvalue's type must match the declared return exactly, and
  `@volatile`/`@packed`/read-only storage is refused, like a `mut`
  argument. `&f(...)` is banned, `-> mut` is rejected on `@extern`, `@asm`,
  `main`, `void`, and function values, overloads differing only in `-> mut`
  collide, `.mci` stubs re-emit the marker (prototype pairing checks it),
  and stores through a returned reference are tracked by the write-effect
  analysis. Generics declare `-> mut T` per instance. See
  [mut returns](docs/language.md#mut-returns) and
  [examples/functions/mut_returns.mc](examples/functions/mut_returns.mc).
- **stdlib: `fnv1a` gains a `slice<T>` member** — length-bounded hashing
  beside the zero-terminated pointer member: the new overload folds exactly
  `length` elements, so zeros in the data are hashed (the right member for
  binary buffers), and an empty slice hashes to the FNV offset basis. Both
  members agree on the same bytes.
- **`format` renders `slice<char*>` as a quoted list** — a new concrete
  member of the `format` overload set appends a slice of C strings as a
  quoted, bracketed list (`["ls", "cat"]`; the modifier is ignored, and
  elements must not be null). Being concrete, it beats the generic
  `slice<T>` list-renderer, which used to render the elements unquoted
  through the `char*` member. See
  [Formatting](docs/language.md#formatting).
- **Array literals adapt to `slice<T>`** — an array literal now borrows
  directly to a slice, backed by a hidden array in the enclosing function's
  frame: explicitly in any expression slot (`[1, 2, 3] as slice<int32>`,
  argument positions included), and implicitly from an annotated `let`
  (`let nums: slice<int32> = [0x10, 0x1F, 0xFF];`) or an array/slice
  element slot (`let m: slice<int32>[2] = [[1, 2], [3, 4]];`, nested
  `slice<slice<T>>` literals, string elements in `slice<slice<char>>`).
  The length is the exact element count (no NUL logic — `['h','i'] as
  slice<char>` has length 2, unlike a named `char[2]`'s borrow), the empty
  literal `[]` is the `{ null, 0 }` view with no backing storage, ternaries
  of literals adapt arm by arm, and mutable slice targets are allowed (the
  backing storage is fresh, so writes go through). `@static let g:
  slice<const int32> = [1, 2];` becomes a constant view over an anonymous
  rodata array (the mutable form is rejected, pointing at
  `slice<const T>`). A **bare argument** now adapts too — `f([1, 2, 3])`
  against a `slice<T>` parameter, no `as` and no intermediate `let` — on the
  direct call path and the overload-set path alike (a second overload of a
  name flips it onto the set path, so both must adapt or `f([1, 2, 3])`
  silently breaks). A plain (non-`mut`) `slice<T>` parameter accepts the
  literal (its fresh backing array is writable), a `mut slice<T>` parameter
  still rejects it, an overloaded `f([1, 2, 3])` picks the `slice<int32>`
  candidate over an `int32*` one, and a literal argument anchors no type
  inference (a bare generic `f([1, 2, 3])` cannot infer `T` — pass
  `f<int32>(...)` or a companion argument). The direct
  `return [...] as slice<T>` is still rejected up front — the view would
  dangle. See [Slices](docs/language.md#slices) and
  [examples/memory/slice_literals.mc](examples/memory/slice_literals.mc).
- **The `format` module** — the formatting protocol's baseline overload
  set: every member of `import "format";`'s
  `format(mut str: string, value: X, const modifier: slice<char>)` set
  appends `value`'s rendering to `str`, steered by `modifier` (`""` for the
  default). Because the modifier is a `slice<char>`, a bare string literal
  adapts to it at the call (`format(s, 255 as int32, "x")`). Closed signed
  and unsigned integer groups render decimal (the narrow signed widths
  sign-extend into a concrete `int64` worker, so `-4` renders `-4` at every
  width) with `"x"`/`"X"`/`"p"` hex and pointer modifiers; concretes cover
  `float64` (fixed-point), `bool` (`true`/`false`, with `"y"` and `"yes"`
  spellings), and `char`/`char*`/`slice<char>` as text; a generic
  `slice<T>` member renders a bracketed list whose elements recurse through
  the set (the modifier applies per element, so nesting works); and an
  unbounded `format<T>` fallback renders `<typename>` for anything
  uncovered. The set is the first of the overload-set protocols riding open
  overload sets (below): one `format` overload in your own module makes your
  type printable. See [Formatting](docs/language.md#formatting) and
  [examples/systems/formatting.mc](examples/systems/formatting.mc).
- **The `equality` module** — `import "equality";` provides the equality
  protocol's baseline overload set: a generic
  `equals<T>(const self: slice<T>, const str: slice<T>) -> bool` compares
  two slices element by element (different lengths are never equal, empty
  slices compare equal; `T` must support `!=`). A string borrows in and a
  string literal adapts, so `equals(s, "hi")` works directly. Like the
  `format` set, it is open: a type joins the protocol by adding an `equals`
  overload in its own module. **Breaking** (pre-1.0): `string`'s comparison
  is now the `equals` members of this protocol (string-vs-slice and
  string-vs-string) rather than the standalone `string_eq` from 0.6.0, which
  is removed — `string_eq(s, x)` becomes `equals(s, x)`.
- **Open overload sets** — overload sets are open by default (a minor
  version bump, pre-1.0): any module may add overloads to an existing
  name — concrete, generic, or mixed — and the set is the whole-program
  union at import merge, in any import order; the one-defining-module rule
  and its `function 'f' already defined` cross-module join error are gone.
  The gate is the declare-time collision rules, now cross-module for
  concretes too: same-pattern duplicates collide with a note citing the
  prior member's site, and cross-module ambiguities cite both declaration
  sites. Resolution is unchanged (concrete beats bounded generic beats
  unbounded), so an import can only add candidates or collide loudly —
  and a concrete overload replacing group-covered behavior is the intended
  protocol move: one overload in your own module plugs your type into a
  foreign set (the example joins the string module's `string_append` set;
  the planned stdlib formatting protocol rides on the same mechanism).
  Privacy and deprecation are per overload now: an `@private`
  overload is a candidate only inside its own module (foreign calls fall
  through to the members they can see; its mangled symbol is salted with
  the file stem, `f(int32).util`, so it never collides with foreign
  members), and `@deprecated` warns only when resolution picks that
  member. Symbol choice is judged per declaring file over the signatures
  it can see, and `.mci` stubs stay ABI-pinned: a stub's members re-derive
  their symbols from the stub plus its own import closure, so consumers
  may extend a stub's set without re-mangling the compiled object's
  symbols (two singleton stubs claiming one plain symbol still collide —
  those objects could never link). `main`, variadic, and collecting
  (`args...`) functions stay non-overloadable. See
  [Function overloading](docs/language.md#function-overloading) and
  [examples/functions/open_overloads.mc](examples/functions/open_overloads.mc).

- **The `with` statement** — `with (t = v as T) body; else other;` is the
  checked-`as` test: it tests an `any` subject's boxed tag against one type
  and, on a match, binds `t` to the recovered value, scoped to the true
  branch. Pure sugar over a single-arm `case type`, riding its machinery
  unchanged: the pattern follows the exact generic-arm detection rule — a
  resolvable name is a concrete tag test, an unresolved bare name a generic
  `T` (monomorphized per boxed tag over the whole program's boxed set) or
  `T*` pointer pattern, each copy fully type-checked with the failure note
  naming the offending type. The initializer-style head is itself the
  checked context — inside it `t = v as T` is the tag test plus bind, the
  same spelling as the planned bare unwrap `let t = v as T;`, while `as`
  everywhere else keeps its cast meaning — the binding is required
  (`with (v as T)` without `t =` does not parse), both bodies take a
  statement or a braced block like `if`, and the `else` is optional: an
  unmatched tag (a zero-filled `any`'s tag 0 included) takes the `else` or
  falls through a lone `with` doing nothing, defined behavior. The checked
  bind is the entire parenthesized head (no `and`/`or` composition, no
  `while` form).
  **Breaking**: `with` is now a reserved word and can no longer be used as
  an identifier. See [The with statement](docs/language.md#the-with-statement)
  and [examples/types/with_unwrap.mc](examples/types/with_unwrap.mc).

- **Closed type groups** — a pipe-separated closed group of types after a
  generic parameter name, `fn f<T: int64 | int32>(x: T)`, constrains what
  `T` may instantiate to. Deduction is unchanged; the group is a
  post-deduction viability filter — a call whose deduced `T` falls outside
  it is a call-site error naming the type and the group, explicit type
  arguments included. Members are concrete types only (resolved and
  validated at declaration: unknown or duplicate members and a grouped
  parameter's default outside the group all error there), and checking is
  **eager**: every listed member is instantiated and fully type-checked at
  end of codegen whether or not it is ever called, so a member the body
  does not compile for errors at the declaration. Same-pattern templates
  with **disjoint** groups now form a resolvable overload set (deduction
  plus the group filter picks one — the signed/unsigned formatter split at
  the function level), while **overlapping** groups collide at declaration,
  cross-module like the duplicate-template rule. Overload ranking gains a
  middle tier — concrete beats bounded generic beats unbounded generic —
  and the group joins the template's symbol base and collision key
  (`show<$0: int32|int16|int8>($0)`). `.mci` interfaces carry the group, so
  a re-imported template enforces and partitions identically. See
  [Closed type groups](docs/language.md#closed-type-groups) and
  [examples/types/type_groups.mc](examples/types/type_groups.mc).

- **`typename` builtin** — `typename(...)` recovers the canonical name of a
  type as a string, mirroring `sizeof` in every surface respect: it takes a
  type or an expression (`typename(int64)`, `typename(x)`, `typename(T)` in
  a generic) and folds at compile time to an ordinary deduplicated rodata
  string literal (a `char*`), usable anywhere a string literal is — a
  variable, a parameter, a `const`/`@static` initializer. The spelling is
  the compiler's canonical one, the exact string the `any` tags hash, so
  `typename(T)` is precisely the preimage of a `T` value's tag; a top-level
  `const` strips to match what boxing does, and `typename(expr)` uses the
  expression's *static* type (an `any` names as `"any"`, never its dynamic
  type). In a generic, `typename(T)` resolves per instantiation — including
  inside generic `case type` arms, where `typename(T)` names the dynamic
  type of the boxed `any` per tag with no runtime machinery. Identical
  string literals (source strings and `typename` results alike) now share
  one rodata constant at emission, rather than leaving the merge to the
  optimizer. **Breaking** (pre-1.0): `typename` is now a reserved word and
  can no longer be used as an identifier. See
  [The typename builtin](docs/language.md#the-typename-builtin) and
  [examples/types/typename.mc](examples/types/typename.mc).

- **Generic `case type` arms** — `when T* ptr:` matches every boxed pointer
  tag not claimed by an earlier arm, with `T` bound to the pointee and the
  binding typed as the pointer; `when T v:` matches every remaining boxed
  tag, with `T` bound to the boxed type itself (pointer tags included). No
  new syntax: a bare arm-type name that resolves is a concrete arm, an
  unresolved bare name with at most one `*` introduces an arm-scoped type
  parameter (so inside `fn g<T>`, `when T v:` stays a concrete arm per
  instantiation). The arm is a real generic context: the body monomorphizes
  once per matching tag drawn from the whole program's boxed set — deferred
  to an end-of-codegen fixpoint, since body copies can box new types and
  instantiate new generics — and each copy is fully type-checked, so
  `handle(ptr)` dispatching into a generic or overload set compiles per tag
  and a boxed type with no viable callee is a compile error at the
  `case type` site whose note names the offending type. Dispatch stays
  first-match-wins textual order (`when char* s:` shields the string tag
  from a later `when T* ptr:`); an arm subsumed by a generic arm above it
  (anything after `T v`; a concrete pointer arm or second `T*` arm after
  `T*`) is a hard unreachable-arm error. `else` stays mandatory — a
  zero-filled `any` (tag 0) matches no arm — and a deferred arm is assumed
  to reach the case's end, so an all-arms-return `case type` still needs a
  trailing `return` in a value-returning function (stage 2, completing the
  generic-arms-in-`case type` roadmap item). See
  [The any type](docs/language.md#the-any-type).

- **Multi-type `case type` arms** — an arm may list several comma-separated
  concrete types over one binding: `when int32, int16, int8 n:` is one arm,
  three tags, one shared body. The binding is an implicit generic: the body
  compiles once per listed type with the binding typed as that type (never
  a union), so an overload set called in the body resolves per copy, and
  every copy is fully type-checked — a listed type for which the shared
  body doesn't compile (say, a call with no viable overload) fails the
  compile with a note naming the offending type. Each listed type claims
  its own tag, so a type listed twice in one arm and a type repeated
  across arms both hit the existing `duplicate case type arm` error, and
  an explicit list doesn't close the universe: `else` stays mandatory.
  This supersedes the v1 "no comma-separated type lists in type mode"
  rule (stage 1 of the generic-arms-in-`case type` roadmap item). See
  [The any type](docs/language.md#the-any-type) and
  [examples/types/case_type_groups.mc](examples/types/case_type_groups.mc).

- **Call write-effect analysis** — projection facts (a guarded `b->data`)
  now survive calls to callees the compiler proves transitively
  write-free, refining the blanket rule that every call kills every path
  fact. The proof is a per-function **write-effect bit**, computed
  bottom-up over the whole program's call graph before any body is
  emitted: a function is write-free when its body has no through-memory
  store (`*p = v`, `a[i] = v`, `s.f = v`, compound forms included; the
  strict v1 rule counts a store to the function's own local struct too),
  no assignment to a `mut` parameter or a global, nothing opaque
  (`@asm`, a call through a function-pointer value, `va_start`/`va_end`,
  a bodyless callee such as `@extern` or an unpaired prototype, or a
  protocol/slice `for` loop; the builtin `range`/`enumerate` counting
  loops are exempt), and only write-free callees. Call edges union every
  same-name candidate, a generic template takes one bit for all its
  instances, and recursion resolves by an optimistic-clear fixpoint, so
  a write-free cycle stays clear; at an emission site where resolution
  picked the winner, the winner's own bit is consulted. The upshot: a
  pure math leaf between a null guard and a `@nonnull` call no longer
  forces a rebind or a `!` hatch, while `println` (wrapping `@extern
  printf`) still kills. Name facts are unaffected (they always survived
  calls), loop-entry and store kills are unchanged, and
  `let q = b->data;` remains the idiom for crossing a writing call or a
  loop. See [@nonnull parameters](docs/language.md#nonnull-parameters)
  and
  [examples/functions/nonnull_projections.mc](examples/functions/nonnull_projections.mc).

- **`-Wdead-code`** — a new opt-in warning class reporting the statements
  the generator has always silently dropped as unreachable: everything
  after a `return`, `break`, `continue`, `unreachable`, or `emit`, after a
  direct call to a `@noreturn` function, and after an `if`/`case`/`@if`
  statement all of whose paths diverge. One warning per dead region, at its
  first statement, naming the killing construct
  (`unreachable code: nothing runs after the 'return' above [-Wdead-code]`);
  the messages are deliberately type-free (dead code is never type-checked),
  so a generic body's per-instantiation re-emissions dedup to one printed
  diagnostic. Code after `while (true)` does not warn yet (the loop's exit
  edge is still emitted; the constant-condition folding roadmap item will
  extend the class), dead `@if` branches are structurally unseen and never
  warn, and defers dropped because another *defer* diverged are a separate
  planned diagnostic. Default-off; enabled by `-Wdead-code`/`-Wall`,
  promoted by `-Werror` as `[-Werror=dead-code]`, and it never changes the
  code generated. See [-Wdead-code](docs/language.md#-wdead-code) and
  [examples/control-flow/dead_code.mc](examples/control-flow/dead_code.mc).

- **`@noreturn` and `unreachable`** — `@noreturn` marks a function that
  never returns to its caller (`exit`, `abort`, an infinite loop): a direct
  call terminates the caller's block, so no dummy return is needed past it,
  code after it drops silently like code after a `return`, and the
  C-idiomatic `if (p == null) abort();` guard now flow-narrows `p` for the
  rest of the scope. `@noreturn` is void-only (so a call never sits in
  expression position), rejects `return` in the body and `@noreturn main`,
  and makes fall-off-the-end undefined behavior instead of an error (C11
  `_Noreturn` semantics — `@noreturn fn spin() { while (true) {} }` is
  legal); defers deliberately do **not** run at a `@noreturn` call,
  matching C's `exit`. The flag works on `@extern`/`@asm`/generic functions
  and prototypes, travels through `.mci` stubs (a stub/definition or
  extern-redeclaration mismatch is a conflict error), lowers to LLVM's
  `noreturn` attribute, and is dropped by `&f` function values (the plain
  `fn()` type cannot carry it — `abort` stays usable as an `atexit`
  handler); libc's `exit`, `abort`, and `_Exit` ship annotated. The new
  `unreachable;` statement asserts a path never executes (LLVM
  `unreachable`; reaching it is undefined behavior) — the exhaustiveness
  bridge for a `case` `else` arm, ending the forced dummy trailing return.
  **Breaking** (pre-1.0): `unreachable` is now a reserved word and can no
  longer be used as an identifier. See
  [@noreturn functions](docs/language.md#noreturn-functions) and
  [The unreachable statement](docs/language.md#the-unreachable-statement).

- **Opt-in warning classes and `-Wunchecked-dereference`** — the warning
  channel gains named, **default-off** classes: a repeatable `-W<name>`
  flag enables one, `-Wall` enables them all, and an unknown name is a
  hard error (`mcc: error: unknown warning class 'name'`). An enabled
  class names its flag in each warning it prints
  (`msg [-W<name>]`), and `-Werror` composes unchanged, promoting exactly
  what printed — an enabled class as `msg [-Werror=<name>]`, while a
  disabled class neither prints nor fails the build; the unconditional
  producers (`@warning`, `@deprecated`) keep their plain `[-Werror]` tail
  byte-identical. Filtering is print-time only: the collected list
  embedders read keeps every emission, now tagged with its class, and a
  warning class never changes codegen. The first class,
  `unchecked-dereference`, warns on `*p`, `p->field`, and `p[i]` (reads,
  writes, and compound assignments alike) where the pointer is not proven
  non-null by the `@nonnull` proof relation — a `@nonnull` parameter, a
  flow-narrowed local or field projection, an always-non-null source, a
  decayed array, or the postfix `!` assertion, which doubles as the
  per-site suppressor; slice indexing never warns. Off by default
  deliberately (mcc pointers are nullable-by-default like C's); the
  `libmc` container internals have not yet been swept clean under it. See
  [Opt-in warning classes](docs/language.md#opt-in-warning-classes) and
  [examples/types/unchecked_dereference.mc](examples/types/unchecked_dereference.mc).

### Changed

- **`-Wunchecked-dereference` / `@nonnull` proof precision** — three
  false-positive classes are gone, surfaced by the `-Wall` example sweep.
  (1) An array reached through a member/index chain is a proven base:
  `grid[0][1]`, `unit.sizes[2]`, and a flexible `p->data[i]` decay by
  address arithmetic (a GEP off the chain's base, the derived address
  `p + n` is), not by a load, so only genuine pointer hops in the chain are
  sites. (2) A reassignment kills a narrowed fact only *with its store*:
  the right-hand side evaluates first, so the list-walking
  `cur = cur->next` no longer warns while `p = null; *p` still does.
  (3) The pointer compounds `p += n` / `p -= n` keep a narrowed fact —
  including across a loop back edge — by the same axiom that proves
  `p + n`, so the canonical `let p = start!; while (p < end) { ...*p...;
  p += 1; }` scan stays warn-free on one seed (a `@nonnull` parameter
  still rejects the reassignment outright). The precision feeds the
  `@nonnull` proof relation too, so these sources now also cross into
  `@nonnull` slots. Existing programs only lose warnings/errors, never
  gain any.
- **Unions parse into their own AST node and type kind** (internal refactor, no
  language change) — a `union` now becomes its own `UnionDecl` node, parallel to
  `StructDecl` rather than a `StructDecl` carrying a `union` flag, and the type
  predicate that meant "any aggregate" splits in two: `is_aggregate` is the old
  "has a field list" test (structs *and* unions), while `is_struct` is now
  record-only (structs, never unions). A struct-only code path — sequential
  layout, `extends`, the prefix upcast, and the nominal-subtype relation — keys
  off `is_struct`, so it can no longer silently accept a union; the shared
  aggregate machinery (by-value copies, `sizeof`, `const`-parameter hidden
  references, member lookup, the `.mci` round-trip) keys off `is_aggregate` and
  is unchanged. Surface syntax, semantics, error messages, and emitted IR are
  all identical — the whole union test suite passes untouched and the union
  examples compile byte-for-byte the same. This removes the former hazard where
  the load-bearing `is_struct(union) == True` let a union reach a layout path
  that assumed record shape.
- **Layout-identical structs without `extends` no longer interconvert** — a
  documented-only behavior change from nominal struct subtyping (above). A
  struct laid out exactly like another — same field prefix — but with no
  `extends` clause between them can no longer be upcast (`v as struct twin`) or,
  when shaped like a slice (`{ T*, integer }`), borrowed to a `slice<T>`. Both
  now raise (`cannot cast ...` / `cannot borrow ...`). No shipped code, example,
  or `libmc` module relied on the old structural acceptance — every upcast and
  slice-borrow already routes through a declared base — so the change is inert
  in practice; the structural check was a pre-`extends` vestige, now retired.
- **No-overload errors show the call signature** — when no overload of a
  name fits a call, the error now renders the attempted call as a
  signature, `no overload of 'format' with signature format(char*)`,
  instead of `no overload of 'format' matches argument types (char*)`.
  Same information, but an arity mismatch is now visible at a glance: a
  one-argument call against three-argument overloads reads as the
  one-argument signature nothing declares, rather than a seemingly
  matching type list.

- **Order-independent template symbol bases** — generic templates now link
  their instances by a signature-derived base spelled from the declaration
  alone: type parameters alpha-rename to positional `$i` placeholders (a
  defaulted parameter spells `$i = <default>`) and the parameter patterns
  follow — `hash<$0>($0*)`, with instances appending bindings,
  `hash<$0>($0*)<char>`. This retires the recorded wrong-merge hazard of
  the declaration-order bases (`name`, `name#1`, ...), under which two
  separately compiled objects that merged one overload set in different
  import orders could emit *different templates'* instances under one
  `linkonce_odr` symbol. A `mut` parameter keeps its marker in the pattern
  (a same-shape `mut`/by-value pair is a genuine, resolvable overload);
  `const` markers and the return type stay out. Mild tightening: two
  templates of one name spelling the same base — alpha-renamed copies and
  return-type-only variants, previously declarable but ambiguous at every
  call — are now rejected at declaration
  (`function 'f<$0>($0)' already defined; overloads must differ in
  parameter patterns`), across modules too. Diagnostics are untouched:
  instantiation backtrace notes keep the source-level `hash<char>`
  spelling, never the mangled symbol. See
  [Template symbols](docs/language.md#template-symbols).

### Fixed

- **A slice boxed into an `any` survives the boxing frame again** — the
  struct-boxing-by-reference feature's aggregate test also caught slices
  (a slice is a struct under the hood), so a slice boxed by hidden
  reference: the payload held a pointer to a call-scoped temporary instead
  of the 16-byte `{data, length}` view itself. Within one frame the two
  conventions round-trip identically, which is why it passed the suite —
  but an `any` *returned* out of the boxing frame carried a dangling
  pointer, so `case type` recovered garbage (`t.length` read 0 or a stray
  address). Slices now box by value again on all three paths (the coerce
  choke point, the variadic collection, and arm recovery), and a
  cross-frame regression test pins the convention.

- **Overload resolution against a `slice<const T>` parameter** — an argument
  whose type is exactly `slice<const T>` now matches a `slice<const T>`
  parameter on the overload-set path. The candidate filter rebuilt each
  slice element's type from its name alone, dropping the `const` qualifier,
  so it compared the parameter's bare `T` against the argument's `const T`
  and filtered the correct candidate out — reporting `no overload of 'f'
  with signature f(slice<const T>)` even though the overload existed. (A
  mutable `slice<T>` argument still widens into a `slice<const T>` parameter,
  and a `slice<const T>` argument is still rejected by a `slice<T>` parameter,
  matching the coercion rules exactly.) Single, non-overloaded functions were
  never affected.

## [0.6.1] - 2026-07-06

### Added

- **`@nonnull` flow-narrowing for field projections** — null-check guards
  now prove pointer-typed *field projections* non-null, not just bare
  locals: `if (b->data != null)` narrows the then branch, a diverging
  `if (b->data == null)` narrows the remainder, loop headers and exit
  conditions narrow the same way, and `and`/`or` chains thread projections
  and names together (`if (b == null or b->data == null) return -1;`). A
  proven projection crosses `@nonnull` slots (direct and generic calls
  alike), decays into `const`/`mut` parameters, threads through `as`
  casts, and seeds a name fact via `let q = b->data;`. Facts are keyed by
  access path at any depth, arrow-insensitively (`(*b).data` is
  `b->data`); the base must be a local (`mut` and `@nonnull` parameter
  bases included; globals and array elements carry no fact), and a
  `@volatile` owner anywhere along the path (`extends`-inherited too)
  never forms one. Because the field lives in reachable memory, the fact
  dies far more eagerly than a name fact: at every call (so
  `f(b->data, g())` compiles while `f(g(), b->data)` does not; arguments
  check and load left to right on both call paths), at every
  through-memory store (`*p`/element/field, compound forms included, any
  base: aliases and union siblings are covered wholesale), wholesale at
  loop entry, on reassignment/shadowing/`mut`-lending of the base, and a
  guard whose later operand can call (`b->data != null and check()`)
  forms no fact at all. `&b->data` alone is not an event: only an
  aliasing write can null the field, and every channel for one is a store
  or a call. To carry a checked field across a call or loop, bind it
  (`let q = b->data;`) or assert (`b->data!`). See
  [@nonnull parameters](docs/language.md#nonnull-parameters).

## [0.6.0] - 2026-07-05

### Added

- **Native variadic arguments (stage 1)** — a trailing `slice<const any>`
  parameter now marks a *collecting* function, with `fn f(args...)` as pure
  sugar for `fn f(const args: slice<const any>)`: the call site boxes each
  extra argument into a caller-stack [`any`](docs/language.md#the-any-type)
  (entry allocas, function lifetime, so loops and `defer` bodies are safe)
  and passes a read-only slice over the run — allocation-free — which the
  callee walks with `for` and a `case type` type-switch. The pass-through
  rule keeps the change purely additive: at exact arity a final argument
  that is already exactly `slice<const any>` (or `slice<any>`, which
  widens) hands over uncollected, so every call that compiled before means
  what it always did; anything else at that position collects (a single
  `any` becomes a one-element slice, a `slice<int32>` boxes as one
  element), and zero extras synthesize an empty `{ null, 0 }` slice.
  Boxing is the standard `any` boxing, escape hatches included (a struct
  or array extra still errors naming `&value` / `&value[0]`), and the
  `.mci` renderer's desugared parameter makes the marker survive re-import
  for free. Stage-1 restrictions, lifted by later stages: a collecting
  function cannot be overloaded or share a generic name (`collecting
  function 'f' cannot be overloaded`; the direct-call path is the only one
  that collects), function-pointer calls stay explicit-slice (`fn(...)`
  types carry no marker), and a collecting function cannot also take C
  varargs, be `@extern`, or be `main`. Stage 2 brings generic/overload-set
  parity; stage 3 flips `print`/`println` in `std`. See
  [Native variadic arguments](docs/language.md#native-variadic-arguments).

- **Concrete function overloading (stage 2)** — overload sets now work with
  prototypes, interfaces, and generics, lifting all three stage-1
  restrictions. Prototype pairing is per signature: a bodyless prototype
  names the member with its parameter list, a same-signature
  prototype/definition pair keeps every shipped pairing rule (return-type
  or convention drift on one parameter list is still `definition of 'f'
  does not match its prototype`), and a different-signature prototype
  simply joins the set as its own member (an unmatched one stays a
  link-time error). `--emit-interface` renders a set as same-name
  prototypes and force-pulls every same-name sibling into the stub — an
  unreferenced `@private` overload included — so the importer derives the
  same plain-vs-mangled symbols the defining object emitted; the `.mci`
  counts as the defining module, pairing member by member with the
  module's own source. Mixed generic/concrete sets: a template may share
  its name with concrete functions from its own module, resolving under
  the (is-concrete, specificity) rank — a concrete overload beats a
  generic on an exact match, the generic covers the rest, explicit type
  arguments select among the generic candidates, and same-tier ties stay
  the ambiguity error. The whole set — generic members included — lives in
  one defining module, and the non-overloadables (`main`, variadics,
  `va_list` parameters) hold whichever side declares first. `libmc`
  adoption is stage 3. See
  [Function overloading](docs/language.md#function-overloading).

- **Concrete function overloading (stage 1)** — plain definitions sharing a
  name in one module now form an overload set, dispatched by the argument
  list through the same viability + specificity order as generic overload
  sets (with a new leading rank tier: a concrete candidate beats a generic
  of equal pattern specificity), so a constructor-flavored
  `counter_init(self)` / `counter_init(self, start)` family reads as one
  operation. Resolution is by arguments only: variants differing solely in
  return type, in `const`/`mut` markers, or in `@nonnull`/`@noalias`
  annotations are duplicate definitions (`function 'f(int32)' already
  defined; overloads must differ in parameter types`), and width-only
  overloads are ambiguous for an untyped literal — `f(0)` between `int32`
  and `int64` errors; a cast or typed variable disambiguates. A name with a
  single definition keeps its plain, C-linkable symbol and the direct-call
  fast path; only sets of two or more take signature-derived mangled
  symbols (`f(int32, char*)`), and string literals (ternaries of literals
  included) still adapt to `slice<char>` parameters when a function becomes
  overloaded. `main`, variadic functions, functions with a `va_list`
  parameter, `@extern`/`@symbol`, and `@static` functions cannot overload,
  and an overloaded name cannot be taken as a function value. Stage-1
  restrictions, lifted by the next stage: a concrete set may not share its
  name with a generic template, prototypes cannot name an overloaded
  function, and `--emit-interface` rejects a module whose public surface
  contains a set. See
  [Function overloading](docs/language.md#function-overloading) and
  `examples/functions/overloading.mc`.

- **Pointer decay into `const`/`mut` parameters** — a proven-non-null `T*`
  argument at a `const T` (struct) or `mut T` slot implicitly dereferences:
  the slot already travels as a hidden reference, so the pointer value is
  forwarded instead of forming `&lvalue`, and a heap `point*` calls
  `fn shift(mut p: point, const by: point)` exactly like a stack value. A
  decay is a two-sided promise: the callee's `const`/`mut` keyword supplies
  the reference discipline, and the caller must prove the pointer non-null
  through the `@nonnull` machinery (`&x`, a `@nonnull` parameter, a
  null-check-narrowed local, or postfix `p!`) — an unproven pointer is a
  compile error naming the guard and the hatch. An **rvalue** `T*` may decay
  into `mut T` (the pointee is real storage even when the pointer expression
  is a temporary), generic inference unifies through the pointee one level
  down (`list<int32>*` at `mut self: list<T>` binds `T = int32`), and under
  overloading decayed readings enter resolution only when no candidate
  matches the pointer type directly, so `f(x: T*)` beside `f(mut x: T)`
  stays unambiguous. Fenced: hidden-reference slots only (a `const` scalar
  or plain by-value `T` still needs `*var`), exactly one level (`T**` only
  reaches `const`/`mut T*`), string literals never decay into `mut`, and a
  decayed argument is a borrowed reference, never a transfer of ownership.
  The explicit `*p` spelling stays legal and proof-free. First stage of the
  `libmc` receiver migration (stages 2 through 4 flip `stack`/`queue`,
  `dict`/`set`, and `list`/`string` in this release; see Changed below). See
  [Pointer decay](docs/language.md#pointer-decay-into-constmut-parameters)
  and `examples/functions/pointer_decay.mc`.

- **String-literal elements adapt to `slice<char>`** — the Stage 4 borrow-in
  now reaches array-element and `@static` positions:
  `let dirs: slice<char>[2] = ["bin", "usr/bin"];` works with no per-element
  `as`, each element borrowing its string constant's bytes with the NUL
  dropped (`"bin"` → length 3), nested array literals included, and literal
  elements mix freely with explicit-`as` ones. A `@static` initializer takes
  the constant form — a constant `{pointer, length}` view into the string
  global, no runtime code — so a `@static` array of slices works, and so does
  the scalar `@static let g: slice<const char> = "hi";` (previously rejected).
  Safe even for globals: the pointee is a global constant, so there is no
  backing-storage or lifetime question. The adaptation rules are unchanged
  otherwise: only *literals* adapt (a typed value in element position still
  needs `as`), and a string literal still does not adapt to a `slice<uint8>`.
  See [Strings](docs/language.md#strings) and
  `examples/types/string_tables.mc`.

- **Ternaries of string literals adapt to `slice<char>`** — the Stage 4
  borrow-in reaches through a conditional expression whose arms are all
  string literals: `string_append(s, b ? "true" : "false")`,
  `let s: slice<char> = flag ? "y" : "yes";`, and
  `return flag ? "on" : "off";` all work with no per-arm `as`, nested
  ternaries included. Each arm borrows its constant's bytes in its own branch
  (NUL dropped), so the merged view carries the chosen literal's own length.
  An explicit borrow distributes the same way: `(flag ? a : b) as slice<char>`
  borrows whichever owned array the condition picks, keeping its static
  length. Only literals adapt, as before — one typed arm makes the ternary a
  plain `char*` — and a `@static` initializer stays literal-only (a runtime
  branch has no constant view). See
  [Operators](docs/language.md#operators) and `examples/types/strings.mc`.

- **The `any` type and the `case type` type-switch (stage 1)** — `any` is a
  builtin 24-byte tagged box, `{ tag: uint64; payload: 16 bytes, align 8 }`,
  the safe counterpart to a union: the payload travels with a compile-time
  type id, so the live value is recovered checked instead of punned. Values
  box **implicitly** wherever a typed slot expects an `any` (assignment,
  argument passing, `return`, field/element stores); an untyped literal
  anchors at its default placeholder (`5` boxes as `int32`, the call-site
  inference rule), and a transparent enum boxes under its underlying type's
  tag. The v1 boxable set is primitives, pointers (each pointer type its own
  tag), and slices (`slice<char>` fits by value); structs, unions, and arrays
  are rejected with the escape hatch named (`&value`; `&value[0]` for an
  array), and an `any` never boxes another `any`. Recovery is only via
  `case type (a) { when int32 n: ... else: ... }` — `type` stays a contextual
  keyword, each arm names one type and must bind a name (scoped to the arm,
  typed as the arm's type), `else:` is mandatory (the boxed universe is
  open), duplicate and never-boxable arms are compile errors, and an `any*`
  subject auto-dereferences. There is no `as` unwrap (and no `.tag`/
  `.payload` access): with no exceptions in the language, an unchecked
  unwrap would be a pun or a trap. Tags are the 64-bit FNV-1a hash of the
  canonical type name — registry-free, deterministic across compilations,
  folding to constants so `case type` lowers onto the integer-equality
  `case` codegen; an in-compile hash collision is detected and fails the
  compile. `any` works as a struct field, array element, behind pointers,
  and in `.mci` interfaces; a global/`@static` `any` initializer is rejected
  for now (assign at runtime), the same shape as the global union
  initializer gap. See [The any type](docs/language.md#the-any-type) and
  `examples/types/any.mc`.

- **A bare type parameter as an `extends` base** — the intrusive-container
  shape, `struct linked_list_entry<T> extends T { next: linked_list_entry<T>*; }`,
  is now a supported, documented, and pinned rule set. Each instantiation
  embeds its payload struct's fields as the layout prefix and appends its
  own, so the payload is reached directly on the entry (`e->value`, no
  wrapper member, no indirection) and an entry pointer or value explicitly
  upcasts to the payload (`cur as struct my*`) — field embedding, not a named
  member, so the shipped `extends` upcasts, attribute inheritance
  (`@packed`/`@align`/`@volatile`), and field defaults all apply per
  instance. Struct-ness is checked per instantiation (`entry<int32>`:
  `int32 is not a struct; cannot extend it`), and the union-base,
  flexible-array-member-base, and field-collision rejections all carry the
  `in instantiation of ...` backtrace note to the triggering request. Literal
  caveat: type-argument inference walks only the extender's own fields, so a
  literal naming base fields needs explicit type arguments. Distinct from the
  planned `T extends base` *bound* (same keyword, different position; the two
  will compose). See the bare-parameter paragraphs under
  [Structs](docs/language.md#structs) and `examples/memory/intrusive_list.mc`.

- **Generic type-parameter defaults** — a type parameter may declare a
  fallback type, on functions (`fn parse<T = int64>(s: uint8*) -> T`) and
  structs (`struct range<T = int64> { ... }`), used when a type argument is
  neither supplied nor inferred from a *typed* value. The priority order is
  strict: explicit type argument > typed-value inference > declared default >
  untyped-constant anchoring — so the fallback is declared at the definition,
  never guessed from a bare literal at the use site, and `parse("42")` means
  `parse<int64>`. (Corollary: adding a default to an existing function
  retypes `f(0)`-style calls, whose literal previously anchored `int32` —
  audit untyped-literal call sites when you add one. It can also make a
  previously-nonviable overload viable; a resulting tie reports the usual
  ambiguity error.) Defaults are trailing-only and may reference only
  earlier parameters (`<T, U = T*>` works; `<T = T>` and
  `<T = U, U = int32>` are parse errors). An explicit type-argument list may
  omit a fully-defaulted tail (`g<int32>(1)` with `fn g<T, U = int8>`),
  filling it from the defaults alone, and the arity error becomes a range
  (`expects between 1 and 2 type argument(s)`) only when a default makes a
  range legal. A defaulted generic struct's bare name is a complete written
  type — `let r: range;`, `sizeof(range)`, and `extends range` all mean
  `range<int64>` — and a struct literal with no typed field for a defaulted
  parameter fills it from the default, the untyped fields adapting to it.
  Defaulted and explicit spellings share one monomorphized instance, `.mci`
  interface stubs round-trip defaults (including a default naming the
  defining file's `@private` type, resolved against that file), and the
  tree-sitter grammar highlights the `= type` clause. See
  [type-parameter defaults](docs/language.md#type-parameter-defaults).

- **Loop-body fact preservation and full proof plumbing for flow-narrowing**
  — narrowed non-null facts no longer all drop at loop entry. A pre-scan of
  the whole loop (condition and body, nested statements, `defer` bodies, and
  both `@if` branches) kills only the facts the loop could invalidate: an
  assignment (`p = ...`, `p += n`), a shadowing `let p`, or lending the bare
  name as a `mut` argument (resolved by callee name across all overloads,
  conservatively). The guard-then-loop idiom
  (`if (p == null) return 1; while (...) { use(p); }`) the annotated stdlib
  leans on now compiles without in-body guards or `!` hatches, and a
  surviving fact holds past the loop's exit. The remaining proof-plumbing
  follow-ons landed with it: `and`/`or` guard threading
  (`if (p != null and q != null)` proves both in the then branch, a
  diverging `if (p == null or q == null)` proves both after it, and a
  short-circuit right operand sees the left's fact, so
  `p != null and use(p)` proves); `while (p != null)` / `until (p == null)`
  header narrowing, re-proven per back edge so mid-body reassignment is
  fine, plus the exit-edge fact after a `while (p == null)`-style loop
  (disabled when the body can `break` past the re-test); fact-seeding
  through `let` (`let q = p;` under a guard, `let q = p!;`, `let p = &x;`
  all start narrowed, under the usual eligibility rules); and proof
  threading through `as` casts whose resolved target is a pointer type
  (aliases like `type cstr = uint8*` count; a non-pointer intermediate
  severs the proof), so `md5("abc" as uint8*, n)` now proves like
  `md5("abc", n)`. Narrowing stays purely static and syntax-directed: no
  instructions emitted, no CFG pass. See
  [@nonnull parameters](docs/language.md#nonnull-parameters),
  [nonnull_loops.mc](examples/functions/nonnull_loops.mc), and
  [nonnull_narrowing.mc](examples/functions/nonnull_narrowing.mc).
- **Forward declarations** — a bodyless `fn` prototype plus its matching
  definition in one program is now accepted, same-file or cross-file: the
  prototype is checked against the definition and discarded (the body
  generates into the prototype's declaration), identical prototypes collapse
  onto one declaration like repeated `@extern` declarations, and a prototype
  arriving after its definition is discarded the same way. Matching is
  strict — the signature plus the derived `const`-struct/`mut`
  hidden-reference positions, the `@noalias`/`@nonnull` markers, and the
  `@private` flag (parameter names may differ; an `@inline` definition never
  pairs with a prototype) — and a mismatch is a new declaration-time error,
  `definition of 'f' does not match its prototype`, with a note citing the
  earlier declaration. `@deprecated` follows the definition: its message, or
  its absence, wins over the prototype's. Cross-kind collisions are
  unchanged: a second definition, an `@extern` declaration, an `@removed`
  tombstone, or a generic template against a prototype stays a
  duplicate-definition error. This removes the function-level collisions of
  a build that imports a module's `.mci` while also compiling its `.mc`
  source (the module's duplicated structs/consts and re-imported generic
  templates still await the driver-level module dedup). See
  [Bodyless fn prototypes](docs/language.md#bodyless-fn-prototypes) and
  [forward_declarations.mc](examples/functions/forward_declarations.mc).
- **Neovim editor support** — `editors/neovim/` is a runtime-path plugin
  for Neovim 0.10+ that reuses the Helix tree-sitter grammar (whose
  checked-in `src/parser.c` compiles with a single `cc` command, so
  nvim-treesitter is optional): `.mc`/`.mci` filetype detection, syntax
  highlighting through queries written against Neovim's capture
  conventions, `gc` comment toggling, four-space indent defaults plus
  nvim-treesitter indent queries, fold queries for
  `vim.treesitter.foldexpr()`, and function/parameter text objects for
  nvim-treesitter-textobjects. Install steps in `editors/neovim/README.md`.
- **Flow-narrowing for `@nonnull`** — a plain `T*` local now narrows to
  non-null from a null check, so idiomatic guarded code needs no escape
  hatch: `if (p != null) { first(p); }` proves `p` inside the then branch
  (and `if (p == null) {A} else {B}` proves it in `B`), while the
  C-idiomatic early guard — an else-less `if (p == null)` whose body always
  diverges (`return`/`break`/`continue`, or every nested path returning) —
  proves `p` for the remainder of the enclosing scope. The narrowing is
  syntax-directed on the AST (no CFG pass), purely static (no instructions
  emitted), and deliberately conservative: only bare local pointer
  variables narrow (never globals, `mut` parameters, or member/index
  expressions), taking `&p` anywhere in the function disables narrowing of
  `p`, the fact dies on reassignment / a `mut` argument / a shadowing
  `let`, and all narrowed facts drop at loop entry (guard inside the body
  instead). Compound conditions (`and`/`or`), `while (p != null)` headers,
  and fact-seeding through `let` are follow-on work. See
  [@nonnull parameters](docs/language.md#nonnull-parameters).
- **Postfix `p!` non-null assertion** — the `@nonnull` escape hatch: a heap
  or returned `T*` carries no syntactic non-null proof, and `p!` is the
  programmer's explicit assertion that lets it cross into a `@nonnull`
  parameter slot (both the concrete and the generic call path accept it).
  The assertion is purely static and costs nothing at runtime: it evaluates
  to its operand unchanged and emits no instructions, so **asserting a
  pointer that is actually null is undefined behavior**. It covers exactly
  the expression it wraps: `let q = p!;` leaves `q` a plain, unproven `T*`
  (fact-seeding through bindings waits for flow-narrowing). `null!` and a
  non-pointer operand are compile errors; anywhere outside a `@nonnull`
  argument, `p!` is simply the identity. `!=` still lexes greedily as one
  token, so `p != q` is always a comparison and asserting before comparing
  needs parentheses (`(p!) == q`). Round-trips through `.mci` interface
  stubs in generic and `@inline` bodies. See
  [@nonnull parameters](docs/language.md#nonnull-parameters).

### Changed

- **`queue<T>` becomes a linked list; the ring buffer moves to `ring<T>`**
  (**breaking**) — `queue<T>` is now a singly-linked FIFO: push links a
  node at the tail and pop unlinks the head, both O(1), one heap node per
  queued value. `queue_init` loses its capacity parameter,
  `queue_len`/`queue_at` leave the queue API, `queue_pop`/`queue_peek` on
  an empty queue become undefined (guard with `queue_is_empty`), and the
  queue gains `queue_it`/`queue_next` iteration (`for v in &q`, front to
  back, non-consuming). The former array-backed implementation lives on
  unchanged in spirit as `ring<T>` (`import "ring";`):
  `ring_init(capacity)`/`ring_destroy`,
  `ring_push`/`ring_pop`/`ring_peek`/`ring_at`, `ring_len`/`ring_is_empty`,
  doubling when full and re-laying wrapped elements in logical order. See
  `examples/memory/queues.mc`, `examples/memory/rings.mc`, and
  `libmc/README.md`.

- **`libmc` copy/build functions collapse into overload sets** (**breaking**)
  — the constructor- and append-flavored families adopt function overloading
  (stage 3): `list_duplicate`/`list_from_array` fold into `list_init`
  overloads (`list_init(b, a as slice<T>)` deep-copies a borrowed run,
  `list_init(a, &raw[0], n)` copies a raw array),
  `string_duplicate`/`string_from_array` fold into `string_init` overloads,
  and `string_append_array` folds into `string_append`'s `char*` overload
  (walks to the NUL terminator); the retired names are **removed**.
  `string_init` also gains explicit-capacity and `(char*, n)` overloads,
  `string_append` and `list_append` gain `(T*, n)` raw-run overloads, and
  `hashing/splitmix64` splits into a `uint64` core plus a generic
  converting wrapper — a mixed generic/concrete set. Resolution note: a
  bare string literal selects the `slice<char>` overload (literal
  adaptation beats `char*` decay), so `string_init(s, "hey")` copies 3
  bytes with the NUL dropped; a `char*` variable selects the until-NUL
  overload. See `examples/memory/lists.mc` and `libmc/README.md`.

- **`list`/`string` sources become slices** (**breaking**) — `list_append`/
  `list_duplicate` and `string_append`/`string_duplicate`/`string_eq` take
  their source side as a `const slice<T>`/`slice<char>` view instead of a
  companion container: any borrowed run works — a container borrows in with
  `as` (its slice prefix: `list_append(a, b as slice<int32>)`), and a string
  literal adapts directly, so `string_eq(s, "hi")` and
  `string_append(s, ", world")` need no ceremony.
  `list_from_slice`/`string_from_slice` fold into the now-slice-sourced
  `list_duplicate`/`string_duplicate` and are **removed**;
  `string_from_array` copies up to the NUL terminator, delegating to the new
  `string_append_array` (append a NUL-terminated `char*`); and `duplicate`
  reserves `src.length` instead of mirroring the source's capacity. See
  `examples/memory/lists.mc` and `libmc/README.md`.

- **`libmc` receiver migration (stage 4 of 5): `list` and `string`**
  (**breaking**) — the workhorse container and its text alias flip their
  `self` parameters from raw pointers (`struct list<T>*`, `struct string*`)
  to receiver markers: mutators take `mut self`
  (`init`/`from_array`/`from_slice`/`destroy`/`reset`/`set`/`push`/`append`
  and the private `list_grow`), the read-only accessors take `const self`
  (`list_get`/`string_get`, whose `mut out` parameters are unchanged, and
  `string_eq`, both of whose sides are now `const`). The companion struct
  pointers of the same APIs flip with them: `append`'s source and
  `duplicate`'s `src` become `const`, `duplicate`'s `dst` becomes `mut`.
  Every `@inline` `string_*` wrapper re-lends its receiver straight into
  the `list_*` slots through the transparent `type string = list<char>`
  alias, which is why the two flip as one stage (`&` of a `mut` parameter
  is banned, so `string` could not flip before `list`). A local container
  now passes directly with no `&` (`list_push(xs, 7)`), and every existing
  `&x` call site keeps compiling unchanged via pointer decay; **a heap
  `list<T>*`/`string*` now needs a one-line null guard after the
  allocation** (`if (p == null) return 1;`) **or a `!` assertion** (inside
  loops, where a bare pointer at a `mut` receiver drops its narrowed fact)
  before it decays into the receiver slots — this stage's only
  source-breaking surface. Excluded by design: `list_it`/`list_next` and
  `string_it`/`string_next` keep their pointer signatures, as in the
  earlier stages (`for … in` loops are emitted against the unchanged
  protocol and need no edits). Landing the stage also repaired a
  decayed-receiver diagnostic: when the decay reading is the only emittable one
  and its inference genuinely conflicts (`list_push(&xs, 'a')` on a
  `list<uint8>`), the error is the real `conflicting types` report again
  instead of a misleading `not assignable`. Only `std` remains, in the
  final stage (landing with the format work in flight). See
  `examples/memory/lists.mc`.

- **`libmc` receiver migration (stage 3 of 5): `dict` and `set`**
  (**breaking**) — the two hash containers' `self` parameters flip from raw
  pointers (`struct dict<V>*`, `struct set<K, V>*`) to receiver markers:
  mutators take `mut self` (the `init`/`destroy`/`set`/`remove` families
  and the private `grow` helpers), the lookups take `const self`
  (`dict_get`/`set_get`; their `mut out` parameters are unchanged). A local
  container now passes directly with no `&` (`dict_set(d, "k", 1)`), and
  every existing `&x` call site keeps compiling unchanged via pointer
  decay; **a heap `dict<V>*`/`set<K, V>*` now needs a one-line null guard
  after the allocation** (`if (d == null) return 1;`) **or a `!`
  assertion** (inside loops, where a bare pointer at a `mut` receiver
  drops its narrowed fact) before it decays into the receiver slots — this
  stage's only source-breaking surface. Excluded by design:
  `dict_it`/`dict_next` and `set_it`/`set_next` keep their pointer
  signatures — `*_it` stores its receiver into the iterator it returns,
  and a `const`/`mut` reference's address cannot escape, so an iterator
  over a receiver marker is inexpressible (`for … in` loops are emitted
  against the unchanged protocol and need no edits). Landing the stage
  also fixed generic inference at decayed receivers: a pointer argument at
  a struct-shaped `const`/`mut` pattern (`mut self: struct dict<V>`) now
  binds the type parameters through its pointee even when an untyped
  literal argument leaned `int32` first, so `dict_set(d, "k", 10)` on a
  heap `dict<uint64>*` infers `V = uint64`. `list` + `string` have since
  flipped (see the stage-4 entry above); only `std` remains, in the final
  stage. See `examples/control-flow/iteration.mc`.

- **`libmc` receiver migration (stage 2 of 5): `stack` and `queue`** — the
  two containers' `self` parameters flip from raw pointers
  (`struct stack<T>*`, `struct queue<T>*`) to receiver markers: mutators
  take `mut self` (the `init`/`destroy`/`push`/`pop` families and the
  private `grow` helpers), read-only accessors take `const self`
  (`stack_peek`/`stack_len`/`stack_is_empty`,
  `queue_at`/`queue_peek`/`queue_len`/`queue_is_empty`). A local container
  now passes directly with no `&` (`stack_push(s, 'a')`), and every
  existing `&x` call site keeps compiling unchanged via pointer decay; a
  heap `stack<T>*`/`queue<T>*` decays into the new slots after the usual
  `@nonnull` proof (a one-line null guard or `p!`), so the selves are
  non-null by construction. `dict` + `set` and `list` + `string` have
  since flipped (see the stage-3 and stage-4 entries above); only `std`
  remains, in the final stage. See `examples/memory/stacks.mc` and
  `examples/memory/queues.mc`.

- **The standard library's pointer contracts are now `@nonnull`-checked**
  (**breaking**) — the data, source, key, and destination pointer
  parameters of the stdlib annotate themselves `@nonnull`: the `memory`
  copy/fill family (`bytecopy`, `copy`, `bytezero`, `zero`, `bytefill`,
  `fill`, and the deprecated forwarders), the `hashing/` digests (`md5`,
  `crc32`, `murmur3`), `dict`'s string keys
  (`dict_set`/`dict_get`/`dict_remove`), and the raw-array sources of
  `list_from_array`/`string_from_array`. An unproven pointer at one of
  those call sites is now a compile error instead of a latent null
  dereference. Code passing `&x`, an array, or a string literal is
  unaffected; **a heap buffer or heap-built key now needs a one-line null
  guard after the allocation** (`if (p == null) return 1;`) **or a `!`
  assertion** (inside loops, where narrowed facts drop). Container `self`
  parameters deliberately stayed plain `T*` in this pass, since they are
  slated to become `mut`/`const` receivers, where non-null holds by
  construction (every container has since flipped; see the
  receiver-migration entries above).
  Parameters for which null is meaningful also stay plain: `resize` (null
  allocates fresh) and `dealloc` (null is a no-op). The `libc/` bindings
  follow as a separate pass. See
  [@nonnull parameters](docs/language.md#nonnull-parameters).

### Fixed

- **Field defaults no longer drop through a bare-parameter base** — with
  `struct item { value: int32 = 40; tag: int32; }`, a
  `entry<struct item> { tag = 2 }` literal (and a bare
  `let e: struct entry<struct item>;`) zero-filled `value` instead of
  applying the base's default, because defaults were collected by walking
  declarations by name and `extends T` has no base declaration to find.
  Merged defaults are now resolved per instance at instantiation time, so
  the documented "`extends` carries the base's defaults down" rule holds for
  bare-parameter bases too; named-base behavior is unchanged. Relatedly, the
  flexible-array-member-base and field-collision instantiation errors were
  the only two `extends` rejections missing the `in instantiation of ...`
  backtrace note; they now carry it like the rest.
- **Editor grammar catch-up** — the Helix tree-sitter grammar now parses the
  syntax it had fallen behind on: the `@static_assert`/`@error`/`@warning`
  compile-time directives (standalone, `;`-terminated, full constant
  expressions as arguments), generic bodiless prototypes (the `@removed`
  tombstone form `fn f<T>(...);`), and stacked per-parameter annotations
  (`@noalias @nonnull p: T*`). The documented `as T * n` cast-star ambiguity
  is gone: the GLR parser now forks on `x as T * ...` and keeps the reading
  that survives, breaking genuine ties toward multiplication exactly like
  the compiler's lookahead rule — so `md5.mc`'s `g as uint64 * 4`, the one
  known exception, parses. Every `.mc` file in the repo now parses with zero
  errors. The VS Code grammar needed no change (its generic `@`-annotation
  pattern already covers the new directive names).
- **`@nonnull` parameters can no longer be passed as `mut` arguments** —
  a `mut` callee writes through a hidden reference into the caller's
  storage, so `fn clobber(mut q: int32*) { q = null; }` called as
  `clobber(p)` could silently null a `@nonnull p` while it stayed "known
  non-null" — a soundness hole in the shipped reassignment/address-of bans.
  Lending a `@nonnull` parameter's storage to a `mut` slot is now a compile
  error on both the concrete and the generic call path; passing its *value*
  to ordinary (non-`mut`) parameters is unaffected.

## [0.5.0] - 2026-07-03

### Added

- **`-S` / `--emit-asm` assembly output** — writes the target's `.s` assembly
  text and stops, without assembling or linking: the textual sibling of `-c`
  (object) and `--emit-llvm` (IR), for inspecting generated code or handing
  it to an external assembler. The output defaults to the source name with a
  `.s` suffix, `-o` overrides it, and the flag honors `-O` and codegen flags
  like `--general-regs-only`. Combined with `--target` it emits the *cross*
  target's assembly, making it the quickest way to eyeball bare-metal codegen
  without a foreign-toolchain `objdump`. Like the other compile-only modes it
  rejects `--run` and any `-l`/`-L`/extra link inputs, and `-Werror` fails
  the build before any `.s` is written.

- **`@removed(msg)` function tombstones** — the terminal state of the
  function-availability lifecycle, one step past `@deprecated`: a declaration
  attribute that turns every *call site* into a hard compile error carrying
  the migration message (`file: error: line N: 'copy_bytes' was removed: use
  bytecopy instead`), so pulling an implementation still points callers at
  the replacement for a release cycle rather than leaving them a bare
  unknown-function error. The tombstone is a bodiless declaration — including
  a generic one
  (`@removed("use bytecopy instead") fn copy_bytes<T>(dst: T*, src: T*, n: uint64);`),
  the one generic function allowed to go bodiless, since it never
  instantiates. The error fires wherever the name would resolve — direct
  calls (explicit type arguments included, before any instantiation),
  function values, `for ... in` over a removed `_it`/`_next` — and gains the
  usual instantiation-backtrace notes when the call sits inside a generic
  body; an uncalled tombstone compiles clean, warns nothing, and passes
  `-Werror`. The signature is parsed but never resolved, so a tombstone stays
  valid even when its parameter types were deleted along with the
  implementation, and one tombstone claims the whole name — mixing it with a
  live definition or a live generic overload is a declaration-time error.
  Combines with `@private` and `@extern`; rejects `@deprecated`, `@inline`,
  `@asm`, and `@static`. Round-trips through `.mci` interface stubs (verbatim
  for generic tombstones, re-emitted on concrete prototypes), so importers of
  a compiled library get the targeted call-site error. Functions only for
  now, matching `@deprecated`. See
  [Removed functions](docs/language.md#removed-functions).

- **`@deprecated(msg)` function attribute** — marks a function deprecated
  without breaking its callers: the function stays fully callable, and every
  call site emits `file: warning: line N: 'name' is deprecated: msg` on the
  warning channel, pointing at the caller with the migration message. The
  warning fires wherever the name resolves to the deprecated function —
  direct calls, generic calls (a mixed overload set warns only when a
  deprecated overload wins), `for ... in` over a deprecated `_it`/`_next`
  protocol, and taking the function as a value — with no suppression (a call
  from another deprecated function warns too). Repeats of one (file, line,
  message) print once, so a call site inside a generic body reports once
  across instantiations, and `-Werror` promotes deprecations like any
  warning. The attribute round-trips through `.mci` interface stubs: verbatim
  for generic/`@inline` functions, re-emitted (message re-escaped) on
  concrete prototypes, so importers of a compiled library are warned at their
  own call sites. Functions only for now; the escalation to a hard error is
  the `@removed` tombstone above. See
  [Deprecated functions](docs/language.md#deprecated-functions).

- **Bodyless `fn` prototypes** — a plain `fn` may end with `;` instead of a
  body: `fn bump(mut n: int32);` declares a concrete mcc function defined in
  another object and called with the **mcc** convention, so `const`-struct
  and `mut` parameters keep their hidden-reference passing (which `@extern`,
  meaning C ABI, deliberately rejects). Every signature marker (`const`,
  `mut`, `@noalias`, `@nonnull`) means what it does on a definition, and the
  usual gates follow from the signature — no function values of prototypes
  with hidden-reference parameters, and a prototype plus a definition in one
  program is still a duplicate-definition error (it is not a forward
  declaration). Generic, `@inline`, `@asm`, and `@static` functions cannot be
  prototypes. Interface stubs are the intended writer; see
  [Bodyless fn prototypes](docs/language.md#bodyless-fn-prototypes).

- **Warning subsystem and the `@warning` directive** — a non-fatal diagnostic
  channel: the compiler collects warnings during code generation and the
  driver prints each as `file: warning: line N: msg` to stderr, in emission
  order, once generation has succeeded and before any output is produced
  (under `--run`, before the program executes). `@warning("msg")` is the
  channel's first producer and `@error`'s non-fatal twin: a top-level
  directive that reports at its position instead of aborting, most useful
  guarded by an `@if` to flag a suspect build configuration without rejecting
  it. The new `-Werror` flag promotes warnings to the failure exit path:
  every collected warning still prints (collect-all-then-fail), each rendered
  as `file: error: line N: msg [-Werror]`, the exit status is 1, and no
  outputs are written — no executable, no object, no `.mci`, and `--run` does
  not execute the program. The channel reports only after success, so
  warnings collected before a hard compile error are dropped with the failed
  build. For embedders, `compile_to_ir` gains a backward-compatible
  `warnings` out-list keyword. `-Werror` is off by default and on in this
  repo's CI, keeping the examples warning-clean. See
  [Error directives](docs/language.md#error-directives).

- **Enum member reuse** — a derived enum inherits a base enum's members by
  naming it in the existing `:` slot: `enum x_status: x_error { RETRY = 100 }`
  copies `x_error`'s member table and adopts its underlying type (pointer
  underlyings included), then folds its own members on top, so
  `x_status::NOT_FOUND` resolves and folds equal to `x_error::NOT_FOUND`,
  in compile-time contexts too, and a new member may reference an inherited
  one (`enum b: a { Y = b::X + 1 }`). Chains are transitive, and a `@private`
  base cannot be extended from another file. Only a bare, direct enum name in
  the slot derives; a pointer to an enum, a `const`-qualified type, or a
  `type` alias to an enum keeps its plain underlying-type meaning with no
  member merge. Compile-time reuse only: no runtime or ABI change, and no new
  type safety (enum values remain transparent integers; nominal enums stay on
  the roadmap). One previously-legal pattern is now rejected: a derived enum
  redeclaring an inherited member's name used to compile as an independent
  member and is now a hard error, even with an identical value. See
  [Enums](docs/language.md#enums).

- **Instantiation backtraces on errors** — an error inside a monomorphized
  body used to print as a bare line in the template's file with no trace of
  how the compiler reached it; it now carries a note chain, one
  `file: note: line N: in instantiation of ...` line per frame after the
  unchanged primary `file: error: line N: msg` line, innermost first — the
  "in instantiation of" backtrace of C++ and Rust. Generic functions, generic
  structs, and type aliases each contribute a frame (a chain through `string`,
  the alias for `list<char>`, names `string`), the frames interleave freely,
  and each names the instance plus the file and line that requested it.
  Instantiations are memoized, so a cached instance reports the first
  triggering path; an error outside any instantiation renders exactly as
  before, with no notes, and `str(LangError)` never includes the chain. The
  error and note channels share one severity formatter
  (`{where}: {severity}: line N: {msg}`), ready for reuse by the planned
  warning subsystem. See
  [Instantiation backtraces](docs/language.md#instantiation-backtraces).

- **Generic overloads mixing `mut`** — overloads of one generic name may now
  disagree on which positions are `mut` (previously a compile error), so a
  `mut`-taking overload can sit next to a pointer- or value-taking one
  (`fn set<T>(mut a: T)` / `fn set<T>(p: T*)`). At a position any candidate
  marks `mut`, an lvalue argument's address is formed up front and its value
  read once through it, deferring the lvalue/value decision until after
  overload resolution: an rvalue rules out the overloads that are `mut` at
  its position (so `pick(3)` selects the by-value overload), while an lvalue
  rules nothing out — a same-shape `mut`/non-`mut` pair stays ambiguous for
  an lvalue. The writability checks (`const` parameter, read-only `const T`
  lvalue, `@volatile` storage, `@packed` field) are judged against the
  *chosen* overload only, so a read-only or `@volatile` lvalue is now a legal
  argument when a non-`mut` overload wins (a `@volatile` one keeps its
  volatile read) and remains an error when a `mut` one does. Arguments are
  still evaluated exactly once, and single-overload generics and non-generic
  `mut` calls are unchanged. See
  [mut parameters](docs/language.md#mut-parameters) and
  [mut_overloads.mc](examples/functions/mut_overloads.mc).

- **Error directives**: two top-level directives that turn a bad build into a
  compile error before it links. `@static_assert(cond, "message")` fails when
  its condition is false; the condition is folded during code generation (like
  a `const` initializer), so it may use `sizeof`/`alignof`/`offsetof`, other
  `const`s, and `Enum::Member` values, useful for guarding struct layouts,
  sizes, and alignment. Any nonzero integer or `true` passes; a zero or `false`
  fails with `static assertion failed: {message}`, and a condition that folds
  to a non-integer/non-bool constant is rejected. `@error("message")` fails
  unconditionally at its position, meant to be guarded by an `@if` so it only
  fires on an unsupported target (a dead `@if` branch drops it). Both are
  checked once types, constants, enums, and globals are known but before any
  function body, fire in source order (first failure wins), work across
  imported modules (reporting the defining file), and decode the usual string
  escapes in their messages. Top-level only for now; a statement-position form
  is planned. Reuses the existing error path and `eval_const`, with no new
  subsystem. See
  [Error directives](docs/language.md#error-directives) and
  [static_assert.mc](examples/types/static_assert.mc).

- **`@nonnull` parameters** — a *checked* "definitely non-null" refinement
  over the nullable-by-default `T*`: mark a pointer parameter
  (`fn first(@nonnull p: int32*) -> int32`) and the callee is statically
  guaranteed a non-null argument. Every call site must prove the argument
  non-null — `&x`, a string/array literal, an array decaying to a pointer, or
  (transitively) a `@nonnull` parameter of the caller; the `null` literal or
  an unproven plain `T*` is a compile error. To keep the per-binding fact
  sound, a `@nonnull` parameter cannot be reassigned or have its address
  taken, and a function with `@nonnull` parameters cannot be used as a
  function value. Attribute-only at runtime (same representation as `T*`,
  lowered to LLVM's `nonnull` + `dereferenceable` argument attributes), so it
  is allowed on `@extern` and round-trips through `.mci` interfaces; rejected
  on `mut`, non-pointer, and `@asm` parameters; combines with `const` and
  `@noalias`. Flow-narrowing from null checks and an explicit escape hatch
  for heap pointers are planned follow-ons. See
  [@nonnull parameters](docs/language.md#nonnull-parameters) and
  [nonnull.mc](examples/functions/nonnull.mc).

- **`@noalias` parameters** — mcc's `restrict`: mark a pointer parameter
  (`fn copy(@noalias dst: uint8*, @noalias src: uint8*, n: uint64)`) as not
  overlapping any other pointer the function reaches, lowered to LLVM's
  `noalias` argument attribute so the optimizer skips runtime overlap checks
  and recognizes bulk moves. The promise is unchecked (overlapping pointers
  are undefined behavior, as in C). It changes no ABI, so it is allowed on
  `@extern` (the libc `restrict` family — `memcpy`, `strcpy`, and friends —
  and `bytecopy`/`copy` in `memory` are now marked); it is rejected on `mut`,
  non-pointer, and `@asm` parameters. `@noalias` combines with `const`. See
  [@noalias parameters](docs/language.md#noalias-parameters) and
  [noalias.mc](examples/functions/noalias.mc).

### Changed

- **The `memory` forwarders now warn as `@deprecated`** — the four renamed
  aliases `copy_bytes`/`copy_items`/`set_bytes`/`set_items` carry
  `@deprecated` attributes naming their replacements (`bytecopy`, `copy`,
  `bytefill`, `fill`), so each call site gets a targeted migration warning
  instead of silently forwarding. The standard library's own internal callers
  ([dict](libmc/dict.mc), [md5](libmc/hashing/md5.mc)) were repointed to the
  new names, keeping the stdlib warning-clean.
- **`memory` copy/fill API reshaped** — the canonical names are now `bytecopy`
  and `copy` (byte-wise vs. item-at-a-time copy) and `bytefill` and `fill`
  (byte-wise vs. item-at-a-time fill); the old `copy_bytes`/`copy_items`/
  `set_bytes`/`set_items` remain as deprecated `@inline` aliases. The copy and
  fill functions now return the count they processed (bytes for the
  `memcpy`/`memset`-backed variants, elements otherwise), and `bytezero`/`zero`
  return their counts too.
- **Examples grouped into topical folders** — the flat `examples/` tour is now
  organized into `basics/`, `control-flow/`, `functions/`, `types/`, `memory/`,
  `systems/`, and `programs/` (with `baremetal/` unchanged), so the progression
  is legible from the directory tree. Every example keeps its name; only its
  path changed (`examples/helloworld.mc` is now
  [examples/basics/helloworld.mc](examples/basics/helloworld.mc)). The
  [index](examples/README.md) and doc links were updated to match, and CI now
  compiles the suite recursively.

### Fixed

- **Interfaces for functions with `mut` or `const`-struct parameters** —
  `--emit-interface` rejected any concrete exported function with a `mut`
  parameter or a `const` struct parameter, because stubs rendered concrete
  functions as `@extern` prototypes and the C ABI cannot express the
  hidden-reference convention. Stubs now emit every concrete function as a
  bodyless `fn` prototype carrying its `const`/`mut` markers, so those
  functions export cleanly and consumers call them correctly. Scalar `const`
  markers, previously dropped silently from stubs, are re-emitted for
  signature fidelity too. Only a reachable `@static` concrete function
  remains inexpressible (its symbol is file-local).

## [0.4.0] - 2026-07-02

### Added

- **`swap` and `replace` in `std`** — the first stdlib helpers built on `mut`
  parameters: `swap(a, b)` exchanges two values in place and
  `replace(dst, value)` stores a new value and returns the old one, both
  generic (`@inline`) and pointer-free at the call site. See
  [libmc/std.mc](libmc/std.mc) and [mut_params.mc](examples/functions/mut_params.mc).
- **Editor support catch-up** — the VS Code grammar and the Helix tree-sitter
  grammar now highlight `mut` and `union`; the tree-sitter grammar also
  learned the syntax it was missing: compound assignment operators, `const T`
  in type positions, struct/union literals (`point { x = 1 }`), field
  defaults, constant-expression array dimensions (`[N + 1]`), variadic
  function types (`fn(char*, ...)`), and `alignof`/`offsetof`. Every file in
  `examples/` and all of `libmc/` (except one line hitting the grammar's
  documented `as T * n` cast-star ambiguity) now parses with no errors.
- **`mut` parameters** — `fn find(key: int32, mut out: int32) -> bool`: the
  writable dual of `const`, passed by hidden reference to the caller's storage
  for every type (scalars included — that is how the write reaches the
  caller). Assignments in the callee land in the caller's variable; reads copy
  out; `&` on it is rejected so the reference cannot escape — the memory-safe
  replacement for an out-pointer parameter, with no `&` at the call site. The
  argument must be the caller's own writable storage of exactly the
  parameter's type. Works on generic parameters (`swap<T>(mut a: T, mut b: T)`);
  re-lending to another `mut` parameter (recursion included) is allowed. Not
  allowed on `@extern`/`@asm` parameters, and a `mut` function cannot be a
  function value or export to a `.mci` interface (the hidden-reference
  convention is not expressible there). `mut` is now a reserved keyword. See
  [mut parameters](docs/language.md#mut-parameters) and
  [mut_params.mc](examples/functions/mut_params.mc).
- **Unions** — `union Name { i: int64; f: float64; }`: an aggregate whose
  members share one storage, sized by the largest member with every member at
  offset 0, for C-layout interop and deliberate type punning (a cross-member
  read is defined byte reinterpretation). Union literals set at most one
  member over zero-filled storage, members read and write through `.`/`->`,
  and unions take generics, `@packed`/`@align`/`@volatile`, `const`
  parameters, and `.mci` interfaces like structs. The struct-only forms
  (`extends`, member defaults, flexible array members) are rejected, and a
  global/`@static` union initializer is not supported yet. See
  [Unions](docs/language.md#unions) and [unions.mc](examples/types/unions.mc).
- **Compound assignment** — `target op= value` for every arithmetic, bitwise,
  and shift operator (`+= -= *= /= %= &= |= ^= <<= >>=`), meaning
  `target = target op value`. The target may be any assignable lvalue (a
  variable, `*p`, `a[i]`, or a field), obeys the same read-only rules as a
  plain assignment, and is evaluated exactly once — so a complex lvalue like
  `arr[next()] += 1` runs its side effects a single time. See
  [Variables](docs/language.md#variables) and
  [compound_assignment.mc](examples/basics/compound_assignment.mc).
- **`for x in` over a struct value** — the `_it`/`_next` protocol takes the
  container by pointer, but `for x in r` no longer needs the `&`: a struct
  value is borrowed automatically (iterating a snapshot), while `for x in &r`
  still iterates by reference and a pointer passes straight through. Because
  the snapshot is a real local, an rvalue is now iterable too —
  `for x in make_iter() { ... }`, which `&` could not address. See
  [Control flow](docs/language.md#control-flow).
- **Builtin `range`** — `for i in range(start, end)` (or `for i in range(end)`,
  from 0) is a compiler builtin: a counting loop over `[start, end)` that lowers
  straight to a counter, with no import, no struct built, and no `_it`/`_next`
  calls. The element type is inferred from the bounds or set with `range<T>(...)`.
  See [Control flow](docs/language.md#control-flow).
- **Builtin `iterator<T>` and `pair<K, V>` structs** — the shared cursor behind
  the `_it`/`_next` protocol (`{ obj: T*; idx: uint64 }`) and the key/value
  element the keyed containers yield are now compiler-provided struct templates,
  available in every program with no import. They are ordinary names, not
  reserved: a user struct named `iterator` or `pair` takes precedence, as with
  the builtin `range`.
- **Keyword-free struct literals** — `Name { field = value, ... }` is now a
  shorthand for `struct Name { field = value, ... }`, so a stack struct value
  reads `let p = point { x = 1, y = 2 };`. Parser-only: it builds the same
  literal, so codegen, defaults, and generic type-argument inference
  (`pair<int32, char*> { ... }` or inferred) are unchanged. The one barred
  position is the `for x in <expr> { ... }` header, where the `{` always starts
  the loop body — parenthesize (`for x in (A { ... })`) or use the keyword form
  there. See [Structs](docs/language.md#structs) and
  [struct_literals.mc](examples/types/struct_literals.mc).
- **Builtin `enumerate`** — `for e in enumerate(obj)` runs `obj`'s ordinary
  iteration (the `_it`/`_next` protocol, or a slice's native walk) while
  keeping a position counter, yielding a builtin
  `enumerated<T> { index: uint64; value: T }` per element, read as `e.index` /
  `e.value`. No import, no extra copy per turn (`_next` writes straight into
  the element's `value` field), and `obj` is borrowed exactly like a bare
  `for x in obj` — a value is snapshot, `&` iterates by reference, an rvalue
  works. A `continue` still consumes its index. A user-defined `enumerate`
  function takes precedence, as does a user `enumerated` struct;
  `enumerate(range(...))` is rejected since the counter is the value. See
  [Control flow](docs/language.md#control-flow) and
  [iteration.mc](examples/control-flow/iteration.mc).
- **Linker passthrough** — the `mcc` command line now takes `-l<name>` libraries
  and `-L<dir>` search paths, plus extra object/archive inputs alongside the
  `.mc` source (`mcc app.mc util.o -L build/lib -lmylib`), all forwarded to the
  `cc` link step. They apply only when linking an executable (not with `--run`,
  `-c`, `--target`, or the `--emit-*` modes, which stop before the link), and a
  failed link is reported cleanly after cc's own diagnostics. `libm` is still
  always linked. See [Usage](README.md#usage).

### Changed

- **The stdlib `get` family takes `mut` out-parameters** — `list_get`,
  `string_get`, `dict_get`, and `set_get` now declare their out-parameter as
  `mut out: T` instead of `out: T*`. Call them with the variable itself
  (`list_get(&nums, 6, value)`), not its address — the `&` at the call site
  is gone, and the callee can no longer leak the address. The `_it`/`_next`
  iteration protocol still uses `out: T*` (the compiler emits those calls;
  migrating the protocol to `mut` is on the roadmap).

### Removed

- The `range` **library** module (`import "range"`, `struct range<T>`,
  `range_it`/`range_next`) is gone, subsumed by the builtin above. Counting
  loops that built a `struct range` and iterated `&r` become `for i in range(…)`.
- The `iteration` **library** modules (`import "iteration/iterator"` and
  `import "iteration/pair"`) are gone, subsumed by the builtin structs above.
  Drop the imports; the struct names resolve as before.

## [0.3.1] - 2026-06-30

### Added

- **Variadic function-pointer types** — `fn(A, ...) -> R`, a trailing `...`
  after at least one fixed parameter, is the type of a pointer to a variadic
  function (matching a C `R (*)(A, ...)`). It is distinct from the non-variadic
  form and usable anywhere a type is — a parameter, a struct field, a `let`, or
  a `const` alias — so a variadic like `printf` can be held, passed, and called
  through with varargs. See [Function pointers](docs/language.md#function-pointers).

### Fixed

- A `const` or `@static` global may now name a function (a compile-time alias),
  e.g. `const log = println;`, and be called by that name. Previously only a
  local `let` could; a `const` always failed with "not a constant" and an
  unannotated `@static let f = fn;` reported a misleading error, because their
  initializers were folded before functions were declared. Such initializers
  are now deferred until functions exist, and the type is inferred from the
  function — so even a variadic like `println` aliases cleanly.

## [0.3.0] - 2026-06-29

### Added

- **Struct literals** — `struct Name { field = value, ... }`: omitted fields are
  zeroed (or set to their declared default), fields may be given in any order,
  and a literal works as an argument, a return value, or written through a
  pointer. Generic type arguments are inferred from the field values
  (`struct box { value = 5 }` infers `box<int32>`), anchored only by typed
  values. See [Structs](docs/language.md#structs).
- **Default field values** — `field: type = expr;` gives a struct field a
  default, used both by struct literals that omit the field and by a bare
  `let s: struct S;` declaration. See [Structs](docs/language.md#structs).
- **Type aliases** — `type <name> = <type>;`, a transparent alias (not a new
  distinct type) for builtins, pointers, function pointers, and structs;
  `@private` / `@static` apply. See [Type aliases](docs/language.md#type-aliases).
- **Slices** — `slice<T>`, a builtin non-owning view `{ data: T*; length: uint64 }`
  over a contiguous run of `T`, with a runtime `.length`, indexing `s[i]`, and
  native `for x in s` iteration. Constructed by an explicit borrow — `xs as
  slice<T>` from an owned `list<T>` (reads `{data, length}`, drops `capacity`) or
  a fixed array `T[N]` (`{&arr[0], N}`). A `char[N]` is NUL-terminated text, so
  its borrow drops the terminator (`length` is `N - 1`); a `uint8[N]` raw buffer
  keeps every byte. See [Slices](docs/language.md#slices) and
  [examples/memory/slices.mc](examples/memory/slices.mc).
- **Read-only slices** — `slice<const T>`, the element-mutability axis: indexing
  yields a non-assignable element (`s[i] = x` is rejected), while a loaded value
  or `for`-loop variable is a mutable copy. A mutable `slice<T>` widens
  implicitly to `slice<const T>`, and a borrow of a mutable source may target
  either; a read-only source (a `slice<const T>`, a `const` parameter, or a
  `const`-typed value) borrows only to `slice<const T>`, preserving immutability.
  `const` is a general type qualifier (`let pi: const float64 = 3.14;`). See
  [Read-only slices](docs/language.md#read-only-slices) and
  [examples/memory/slices.mc](examples/memory/slices.mc).
- **String-literal slice adaptation** — a string literal now *adapts* to a
  `slice<char>` (or `slice<const char>`) from context with no `as`, the way an
  untyped constant takes its type: at a function argument (including a
  `const`-by-reference slice parameter, so `writeln("hi")` works), a `let` slot,
  or a `return`. The borrow drops the trailing NUL; only literals adapt — a typed
  value still needs the explicit `as`. See [Strings](docs/language.md#strings).
- **`char` type** — a distinct one-byte text type, ABI-identical to `uint8` (an
  unsigned byte) but a separate type, so NUL-terminated text is told apart from a
  raw byte buffer. Character literals (`'a'`) are untyped constants that default
  to `char` but adapt to a `uint8`/integer slot; a `char` *value* needs an
  explicit `as` to become a `uint8`. `char*` coerces to `uint8*` like any
  pointer, so libc still takes string literals. A `char[N]` borrows to a
  `slice<char>` that drops the trailing NUL (the text); a `uint8[N]` keeps every
  byte. See [Strings](docs/language.md#strings).
- **`byte` type** — a transparent builtin alias for `uint8`, the raw one-byte
  unit of memory. Unlike `char` it is not a distinct type: `byte` and `uint8`
  values and pointers are interchangeable without a cast. The memory-handling
  APIs now read in terms of it — the `memory` allocators and `set_bytes`, libc's
  `malloc`/`calloc`/`realloc`/`free`, `memcpy`/`memmove`/`memset`/`memchr`/
  `memcmp`, `qsort`/`bsearch`, and the raw stream buffers of
  `fread`/`fwrite`/`setbuf`/`setvbuf`. See [Types](docs/language.md#types).
- **Flexible array members** — a struct's last field may be written `field: T[]`
  with no size: a trailing run of `T` that adds **0** to `sizeof` and decays to a
  `T*` at the struct's tail, so one allocation holds a header plus a contiguous
  run of elements (the C `struct { int len; T data[]; }` idiom, without the
  `T[1]` "struct hack"). It must be the last field with `[]` as its only
  dimension; a struct ending in one cannot be an `extends` base, and the member
  cannot be set in a literal or borrowed as a `slice<T>` (its length is not
  static) — index it through its pointer. See [Structs](docs/language.md#structs)
  and [examples/types/flexible_array_members.mc](examples/types/flexible_array_members.mc).
- **`alignof` and `offsetof`** — two more compile-time `uint64` layout
  constants, the C counterparts of the same name. `alignof(T)` is a type's
  alignment in bytes (and, like `sizeof`, also accepts a variable —
  `alignof(v)`); `offsetof(struct S, field)` is a field's byte offset within a
  struct, honoring padding, `@packed`, and `@align`. Both fold at compile time,
  so they can size arrays and initialize a `const`. For a flexible array member,
  `offsetof(struct S, data)` is where its elements begin — the tight base for an
  allocation — and `alignof` counts the element type. See
  [Pointers](docs/language.md#pointers) and [Structs](docs/language.md#structs).
- **Constant-expression array sizes** — an array dimension may be any constant
  integer expression (`int32[N + 1]`, `uint8[2 * SIZE]`), not just a literal or a
  lone `const` name.
- **`sizeof` of a variable** — `sizeof(v)` is the size of `v`'s type, so the type
  need not be spelled out; the operand is never evaluated. See
  [Pointers](docs/language.md#pointers).
- **`new<T>()`** — a typed single-element heap allocator in the `memory` library,
  alongside `alloc` / `resize` / `dealloc`.
- **`range<T>` library** — a half-open `[start, end)` integer interval that
  supplies the iterator protocol, so `for i in &r` counts; generic over the
  integer width. See [examples/control-flow/ranges.mc](examples/control-flow/ranges.mc).
- **`--strict-align`** — forbid the backend from emitting unaligned memory
  accesses (gcc's `-mstrict-align`), for bare-metal targets running with the MMU
  off where an unaligned wide load/store traps. Composes with
  `--general-regs-only` (both merge into the one per-function `target-features`).

### Changed

- **String literals are `char[N]` arrays** (NUL included) rather than bare
  `uint8*`. They decay to a `char*` (which coerces to `uint8*` like any pointer)
  wherever a pointer is used (call arguments, returns, comparisons, indexing), so
  existing libc code is unaffected, but an owned binding keeps its array type:
  `let s = "hi";` is a mutable `char[3]`, `let s: char[] = "hi";` infers the
  size, and `let s: char[8] = "hi";` zero-fills the rest (a `uint8[N]` annotation
  still accepts the same bytes as a raw buffer). This makes `len(s)` / `len("hi")`
  work and lets a string be borrowed as a `slice<char>` (the borrow drops the
  trailing NUL, so the slice spans the text). Annotating `char*`/`uint8*` keeps
  the pointer-to-constant behavior (no copy). See
  [Strings](docs/language.md#strings).
- **`string` is now `type string = list<char>`** — a transparent
  specialization with the same layout, so a `struct string*` upcasts to a
  `struct list<char>*` and every `list` operation works on a string. The
  list/string API distinguishes `push` (append one element) from `append`
  (concatenate another whole list).
- **Standard-library and libc string APIs adopt `char`** — `dict` keys are now
  `char*`, the libc bindings that carry text (`strcpy`/`strlen`/`strcmp`/
  `printf`/`fgets`/`getenv`/`strftime`, …) take and return `char*`, and `std`'s
  `print`/`writestr`/`writeln` follow suit. Raw-byte and stream operations stay
  `uint8` — `memcpy`/`memset`, `fread`/`fwrite`, and the hashing functions.
  Because `uint8*` does not coerce to `char*`, a buffer handed to a libc string
  function must now be a `char[N]`/`char*` (or an explicit cast); string literals
  are unaffected.
- **The standard library moved to `libmc/`** and is now compiled from source
  (previously `lib/`); `import "<module>"` by name is unchanged.
- **File-scoped symbols are mangled with `.`** instead of `@`, so the emitted
  names for `@static` / `@private` declarations read like `file.name`.
- The compiler no longer prints a `wrote <output>` line on a successful compile.

### Fixed

- A compile error raised while generating a generic function instance is now
  attributed to the template's own file, not the root module. Previously an error
  on a line inside an imported library (e.g. a failed type-parameter inference in
  a `for ... in` over a generic container) was blamed on the file being compiled.

## [0.2.0] - 2026-06-26

### Added

- **Enums** — `enum Name[: type] { Member = value, ... }` over any underlying
  type (`int32` by default), accessed as `Name::Member`. The name is usable as a
  type, members may reference earlier members of the same enum, and `@private` /
  `@static` apply. See [Enums](docs/language.md#enums).
- **Ternary operator** — `cond ? a : b`, an expression that evaluates exactly one
  arm.
- **`const` parameters** — an immutable parameter the callee promises not to
  mutate; a `const` struct is passed by a hidden pointer instead of copied, so
  you get value semantics without the copy. See
  [const parameters](docs/language.md#const-parameters).
- **In-expression integer widening** — two same-signedness integer operands
  widen to the wider type within an expression (e.g. `a + b * c` over mixed
  widths) without explicit casts; assignments, returns, and arguments still
  require a cast.
- **Conditional imports** — a top-level `@if` branch may contain `import`
  statements, so a dependency can be pulled in only for the targets that need it;
  only the live branch is resolved.
- **Interface files** — `mcc src.mc --emit-interface` writes an importable `.mci`
  stub (concrete functions as `@extern` prototypes; types, constants, and
  generic/`@inline` functions in full), to ship a precompiled library as an
  object plus a thin interface. See
  [Interface files](docs/language.md#interface-files).
- **Object-only compilation** — `-c` / `--compile` emits a native `.o` without
  linking.
- **`.mci` import resolution** — a bare `import "foo"` resolves to `foo.mc` if
  present, otherwise `foo.mci`.
- **`--freestanding`** — disable hosted-libc assumptions so LLVM does not rewrite
  standard-named calls (e.g. `printf("…\n")` → `puts`), for bare-metal builds.
- **Helix editor support** — a tree-sitter grammar (`editors/helix/`) with syntax
  highlighting, indentation, comment toggling, and text objects.
- **`.mci` highlighting** — the VS Code and Helix grammars recognize interface
  files.
- **`string_duplicate`** in the string library.

### Changed

- Renamed `lib/array.mc` to `lib/list.mc`.
- Renamed the `memory` byte-copy helpers (docs refreshed).
- `set` and `dict` now track slot state with a `uint8`-backed enum.

### Removed

- `string_append_string` from the string library.

### Fixed

- `return <void value>;` is rejected with a diagnostic instead of emitting
  invalid IR.
- A shift no longer forces its count to the value's type, so `1 << count` with an
  unsigned `count` compiles again.

## [0.1.2] - 2026-06-19

### Added

- Inline assembly: `@asm(...)` expressions and `@asm fn` sugar, with
  `@clobbers(...)` lists; enabled for same-arch cross `--target` builds.
- Block expressions: `{ ...; emit v; }` as a value.
- Struct `extends` (prefix-layout specialization), generic `extends`, and
  explicit upcast to the base.
- `@inline` functions; `for … in` dispatched by struct name; comma-separated
  `when` arms; arrays of function pointers; unary bitwise NOT (`~`).
- Exhaustive `if`/`else` and `case` count as guaranteed exits.
- `@static let` type inference and constant-expression `@static` initializers.
- Untyped integer literals default to the narrowest fitting width.
- `-D NAME[=VALUE]` defines for `@if` conditions.

### Changed

- Renamed the CLI flag `--naked` to `--nostdlib`.

### Fixed

- Forwarding a `va_list` parameter to another function works across all ABIs.

## [0.1.1] - 2026-06-15

### Fixed

- Imported `@static` globals get `linkonce_odr` linkage so identically mangled
  copies merge into one instance (previously globals could silently split state
  across separately compiled objects).
- Cross builds use the small code model with static relocations (ADRP-based
  addressing), fixing `@static` globals reading back as zero in fixed-load
  freestanding images.

## [0.1.0] - 2026-06-14

### Added

- Packaged mcc as a pip-installable distribution: a `pyproject.toml` exposing an
  `mcc` console script and bundling the `lib/` standard library into the wheel,
  with the stdlib resolved from the installed location or a source checkout.

[Unreleased]: https://github.com/fecabrera/mcc/compare/v0.7.0...HEAD
[0.7.0]: https://github.com/fecabrera/mcc/compare/v0.6.1...v0.7.0
[0.6.1]: https://github.com/fecabrera/mcc/compare/v0.6.0...v0.6.1
[0.6.0]: https://github.com/fecabrera/mcc/compare/v0.5.0...v0.6.0
[0.5.0]: https://github.com/fecabrera/mcc/compare/v0.4.0...v0.5.0
[0.4.0]: https://github.com/fecabrera/mcc/compare/v0.3.1...v0.4.0
[0.3.1]: https://github.com/fecabrera/mcc/compare/v0.3.0...v0.3.1
[0.3.0]: https://github.com/fecabrera/mcc/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/fecabrera/mcc/compare/v0.1.2...v0.2.0
[0.1.2]: https://github.com/fecabrera/mcc/compare/v0.1.1...v0.1.2
[0.1.1]: https://github.com/fecabrera/mcc/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/fecabrera/mcc/releases/tag/v0.1.0
