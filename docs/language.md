# Language reference

## Functions

Functions are declared with `fn`. Parameters are typed, and the return type
follows `->`; omitting it means `void`. `main` gets an implicit `return 0`
if it falls off the end.

```c
fn add(a: int32, b: int32) -> int32 {
    return a + b;
}

fn greet() {
    println("hi");
}
```

Recursion works:

```c
fn fib(n: int32) -> int32 {
    if (n < 2) {
        return n;
    }
    return fib(n - 1) + fib(n - 2);
}
```

`@inline` asks for a function's body to be folded into each of its call
sites (LLVM's `alwaysinline`). It is ideal for small helpers, especially
generic ones — the call overhead disappears and the optimizer sees through to
the arguments:

```c
@inline fn min(a: int64, b: int64) -> int64 {
    if (a < b) { return a; }
    return b;
}
```

The inliner runs as part of optimization, so the folding happens at `-O1`
and above; at `-O0` an `@inline` function is emitted normally and called
like any other. It works across separately compiled files too: an imported
definition is copied into every object that uses it (the same mechanism that
lets generics and `@static` cross files), so the body is present to inline
wherever it is called. `@inline` needs a body, so it cannot combine with
`@extern`.

Unlike C, `@inline` alone forces inlining — there is no need for the
`static inline` idiom (which only exists to work around C's `inline` linkage
rules). `@static` is orthogonal: it just makes the helper file-private, so its
unused out-of-line copy can be dead-stripped. Combine as `@static @inline` for
both.

### const parameters

A parameter marked `const` is read-only: the body may not assign to it, to one
of its fields, or to one of its array elements, and may not take its address
with `&`. The `const` *binder annotation* comes in two forms that differ only
in **how the value is received**:

- **`const x: T` — a by-value read-only copy.** The callee gets its own copy
  (the ordinary calling convention), read-only to the body. On a scalar this
  is all `const` ever meant; on a struct it is a genuine copy.
- **`const x: &T` — a read-only hidden reference (the *view*).** A pointer to
  the caller's storage, available uniformly on every type, read like a plain
  value (`a.x`, not `a->x`). No copy is made, and the callee still cannot write
  through it.

```c
struct matrix { m: float64[16]; }

fn trace(const a: &struct matrix) -> float64 {   // the view: no copy
    return a.m[0] + a.m[5] + a.m[10] + a.m[15];
}

fn label(const tag: int32) -> int32 {            // by-value read-only copy
    return tag * 2;
}
```

The view shares the argument's storage when it has an address (a variable, a
field); a temporary argument (a struct returned by value, say) is spilled to a
stack slot first. Both forms work on generic parameters.

Prefer the by-value copy for scalars and small plain-data structs (it is the
simpler convention with no aliasing), and the `const &` view for larger
structs. For a type that **owns a resource** (declares a destructor), take it
by `const &`: a by-value copy would be a bitwise copy that aliases the owned
resource — both copies would free it — which
[`-Wdestructor-copy`](#opt-in-warning-classes) flags at the copy site
(`move(v)` blesses a deliberate one).

> Historical note: before the `&`-reference redesign's Phase B, a `const`
> *struct* parameter was implicitly the hidden-reference view. It is now a
> by-value copy like every other type, and the view moved to the explicit
> `const &T` spelling — so an old view-intended `const s: T` must be written
> `const s: &T`. This is one of three unrelated meanings of the `const`
> keyword: the binder annotation here, the `&`-reference view `const &T`, and
> the type-level `const` of a read-only element in `slice<const char>`.

`const` on a **pointer** parameter freezes the pointer itself, not what it
points at — `const p: struct node*` means `p = ...` is rejected but `p->next =
...` is fine, the same distinction as C's `node* const` versus `const node*`.

A **by-value** `const x: T` **is** allowed on `@extern` parameters — it is the
ordinary C by-value convention, a callee-side discipline C never sees. The
`const &T` view is a hidden pointer that would change the C calling convention,
so the reference form is rejected on `@extern` (like `&T`).

`const` erases from **function types**: a by-value `const` carries no caller
contract, so `fn(const struct matrix)` *is* `fn(struct matrix)` (generalizing
the long-standing `fn(const int32)` ≡ `fn(int32)` rule to every type). The
`const &T` view is a real calling convention that stays spelled in the type —
`fn(const &struct matrix) -> float64` — distinct from, and not convertible
with, the by-value `fn(struct matrix) -> float64` (see
[&/const-carrying function types](#referenceconst-carrying-function-types)).

### Reference parameters

A parameter whose type is `&T` — a **reference** — is the writable dual of
the [`const &T`](#const-parameters) view: it is passed by hidden reference to
the caller's storage — for **every** type, scalars included, since that is the
only way a write can reach the caller — and the callee's assignments land in
the caller's
variable. Reading it loads the current value (copy on read); `&` on it is
rejected, so the reference can never outlive the call. It is the memory-safe
replacement for an out-pointer parameter:

```c
fn find(key: int32, out: &int32) -> bool {   // instead of out: int32*
    out = 42;          // writes the caller's variable
    return true;
}

fn main() -> int32 {
    let x: int32 = 0;
    find(7, x);        // no & at the call site; x is now 42
    return x;
}
```

The argument must be the caller's own writable storage — a variable, a field,
an array element, or a dereference — of **exactly** the parameter's type (the
callee writes through the reference, so nothing can adapt or widen; `int32`
and `uint32` may share bits, but not a `&` reference). A literal, a plain
call result (a [`&` return](#reference-returns) re-lends instead), a `const`
parameter, a read-only `const T` lvalue, `@volatile` storage, and a
`@packed` field (whose alignment is not guaranteed) are all rejected.

Inside the body a `&` parameter behaves like the variable it references:
assignment and compound assignment write through, a struct's fields project
(`p.x = 3` writes the caller's field), and it can be **re-lent** — passed
onward as another function's `&` argument (recursion included), which
forwards the reference without letting it escape. Two `&` parameters may
alias the same variable; as with two pointers, the last write wins.

`&` works on generic parameters (`fn swap<T>(a: &T, b: &T)`), with
the argument's type matching the instantiated parameter exactly. Overloads of
one generic name may freely mix `&` and non-`&` positions — for example a
`&`-taking overload next to a pointer-taking one:

```c
fn set<T>(a: &T) { a = 7 as T; }    // for the caller's own variable
fn set<T>(p: T*)    { *p = 9 as T; }   // for storage reached by pointer
```

The call resolves the overload in a defined order:

1. **Shape** — candidates whose parameter patterns the argument types cannot
   match are dropped, and a candidate that is `&` at a position receiving
   something that is not an lvalue (a literal, a call result, an `&x`, a bare
   function name) is dropped with them.
2. **Specificity** — among the viable candidates the most specific parameter
   patterns win (`T*` beats `T`, concrete types beat both), exactly as for
   overloads without `&`.
3. A remaining tie is an error, and lvalue-ness never breaks it: the
   same-shape pair `fn f<T>(a: &T)` / `fn f<T>(a: T)` is ambiguous for an
   lvalue argument (an rvalue picks the non-`&` one, the only viable
   candidate).

The argument is still evaluated exactly once, before the winner is known: at
a position any candidate marks `&`, an lvalue's address is formed up front
and its value read through that address, so the callee's writes land in the
caller's storage when a `&` overload wins, and the single read keeps the
storage's semantics (a `@volatile` lvalue gets a volatile load) when a
non-`&` one does. The writability rules above are judged against the
**chosen** overload only: a `const` parameter, a read-only `const T` lvalue,
`@volatile` storage, or a `@packed` field is a fine argument when a non-`&`
overload wins, and remains an error when a `&` one does.

Like the `const &` view, a writable `&` is not allowed on `@extern`
parameters (the hidden-reference ABI would not match the C function; a
by-value `const T` is fine there, being the ordinary C convention). A
function with a `&`
parameter is a legal function value: its type spells the convention —
`fn(&int32) -> bool` — and calls through the value enforce the same
writable-lvalue rules as a direct call (see
[&/const-carrying function types](#referenceconst-carrying-function-types)).

`&` is a reference type only in a parameter- or return-type slot; a stray
`&` anywhere else — `let r: &T`, a `list<&T>` element, an `x as &T` cast, a
struct field — is a compile error (`a '&' reference type is only allowed in
a parameter or return type`). There are no reference locals, and `&` stays
unambiguously the address-of operator in expressions.

`mut` is not a keyword: the reference marker used to have a deprecated
`mut`-binder spelling (`fn find(key: int32, mut out: int32)`), removed once
the deprecation window closed, so `mut` is now an ordinary identifier.

See [examples/functions/reference_params.mc](../examples/functions/reference_params.mc)
and, for overloads mixing `&`,
[examples/functions/reference_overloads.mc](../examples/functions/reference_overloads.mc).

### Reference returns

A function declared `-> &T` returns an **lvalue**: a reference to
caller-reachable storage of type `T`, rather than a copy of the value. The
call expression is then usable on both sides of `=` — it is the accessor
shape (`_at`-style element access without handing out a raw `T*`):

```c
struct buf { data: char*; length: uint64; }

fn buf_at(self: &struct buf, i: uint64) -> &char {
    return self.data[i];       // formed from the receiver: &legal
}

fn bump(c: &char) { c += 1; }

fn main() -> int32 {
    let bytes: char[4];
    bytes[0] = 'a'; bytes[1] = 'b'; bytes[2] = 'c'; bytes[3] = '\0';
    let b = struct buf { data = &bytes[0], length = 3 };
    buf_at(b, 0) = '/';         // assignment through the returned lvalue
    buf_at(b, 1) += 1;          // compound assignment (addressed once)
    bump(buf_at(b, 2));         // re-lent as a reference argument
    let c = buf_at(b, 0);       // value context: loads the current value
    return c == '/' ? 0 : 1;
}
```

There are no reference locals: a `&` return is **consumed at the call
expression**. In value position it loads; on the lvalue side it is an
assignment or compound-assignment target, a base for projections
(`f(s).field = v`, and `f(s)[i] = v` through a `-> &T*` result), and
re-lendable as another call's `&` argument on both call paths (concrete
and generic/overloaded). `&f(...)` is rejected — the reference must not
outlive the full expression, the same non-escape guarantee a `&`
parameter carries.

**The formation rule.** Without a lifetime system, what keeps the reference
from dangling is a strict, checkable rule at the callee's `return`: the
returned lvalue may only be formed from a **`&` or pointer parameter or a
global**, traced through member accesses (`.`/`->`), elements,
dereferences, and calls that themselves return `&` (composition:
`return buf_at(self, 0);` is fine). Everything rooted in the call's own
frame is rejected:

- a **local** — even a provably-safe alias like `let d = self.data;
  return d[i];` is rejected (inline the chain into the return expression);
- a **by-value parameter** (its storage is the call's frame copy);
- a **`const` parameter** (read-only, wherever the chain crosses);
- the **pointer parameter itself**: `return p` would reference the
  parameter's own frame slot and is rejected, while `return *p`,
  `return p[i]`, and `return p->f` reach the storage the caller handed in
  and are legal. A `&` parameter *is* legal as the returned lvalue itself
  (`return x;`) — it already names the caller's storage.

Casts, arithmetic, `null`, and calls without a `&` return are never part
of a legal chain. The storage rules `&` arguments obey apply at the
`return` too: `@volatile` storage, `@packed` fields, and read-only
`const T` lvalues are rejected, and the lvalue's type must match the
declared return **exactly** (the caller writes through the reference, so
nothing adapts or widens).

`-> &` works on generics (`fn pick<T>(a: &T, b: &T, f: bool) ->
&T`), with the formation and void rules checked per instance. It is
rejected on `@extern` and `@asm` functions (the pointer-typed return would
change the C calling convention), on `main`, on `void` (there is no
storage to reference), and composed with `const` (a reference return must be
writable, so `-> &const T` is banned at parse time); overloads
differing only in `-> &` collide as duplicates, like any
return-type-only pair. A `-> &` function is a legal [function
value](#function-pointers): the `fn(...) -> &T` type spells the return
convention, and a call through the value is the same lvalue expression a
direct call is (see [&/const-carrying function
types](#referenceconst-carrying-function-types)). In an [interface
file](#interface-files) the marker is re-emitted on the prototype and must
match the definition exactly.

One programmer's-problem caveat, the same one [container
cursors](#control-flow) have: a `&` return that points into a
container's heap storage is a borrow of that storage — an operation that
reallocates it (a growing `xs.push`, `s.append`, ...) within the same
full expression, or between forming the reference and the surrounding
statement's store, invalidates the reference. The formation rule prevents
frame escapes, not heap staleness; keep the access and the mutation in
separate statements, exactly as with an in-flight cursor.

See [examples/functions/reference_returns.mc](../examples/functions/reference_returns.mc).

### Pointer decay into const/reference parameters

A `T*` argument at a `const &T` (read-only) or `&T` (writable) reference slot
implicitly dereferences — the pointer **decays** — so the callee sees the
pointee, read-only or writable, without the caller writing `*var`. A stack
value and a heap pointer then call the same function identically. (A by-value
`const T` or plain `T` slot has no reference behind it, so nothing decays
there — the value must be passed directly.)

```c
import "std/memory";

struct point { x: int32; y: int32; }

fn shift(p: &struct point, const by: &struct point) {
    p.x += by.x;
    p.y += by.y;
}

fn main() -> int32 {
    let a = point { x = 1, y = 2 };
    let d = point { x = 10, y = 20 };
    let hp = new<point>();      // point*, heap storage
    if (hp == null) { return 1; }
    *hp = a;
    shift(a, &d);               // stack value and rvalue pointer
    shift(hp, &d);              // heap pointer decays the same way
    dealloc(hp);
    return 0;
}
```

Mechanically the feature is cheap: a `const &` and a `&` parameter already
travel as a hidden reference, so decay forwards the pointer value instead of
forming `&lvalue`. That is also why an **rvalue** `T*` may
decay into `&T` — the pointee is real storage even when the pointer
expression is a temporary (`shift(&a, ...)`, a call result, a `p!`) —
deliberately unlike the plain rule that a `&` argument must be an lvalue.

A decay is a **two-sided promise**. The callee's side is the receiver
contract already in the declaration: `const` will not write through the
reference, `&` writes through it and never lets it escape. The caller's
side is a value-supplier promise in the `@nonnull` family: the pointer must
be **provably non-null**, because a `const`/`&` reference is never null by
construction. The proof is the same machinery `@nonnull` uses — an `&x`, a
`@nonnull` parameter, a local seeded or narrowed by a null check, or the
postfix `p!` assertion:

```c
fn consume(p: struct point*, const by: &struct point) {
    if (p == null) { return; }
    shift(p, by);               // narrowed: proven for this whole scope
}
```

An unproven pointer at a decaying slot is a compile error naming the fix:

```
example.mc: error: line 3: cannot pass a possibly-null point* as argument 1
of 'shift': decaying into a reference point parameter forms a hidden reference, which is
never null (narrow with a null check or assert with postfix '!')
```

The explicit spelling `shift(*p, ...)` also stays legal and needs no proof:
the dereference is visible at the call site and carries the usual
null-dereference responsibility, exactly as it did before decay existed.

The rule is fenced in four ways:

- **Hidden-reference slots only.** `const` struct parameters and `&`
  parameters of any type. A `const` scalar parameter is a by-value copy with
  no reference behind it, and a plain by-value `T` parameter still needs an
  explicit `*var` — the copy stays visible.
- **Exactly one level.** `T*` decays to `const`/`&T`; a `T**` decays only
  to `const`/`&T*` (its pointee is itself a pointer), never twice.
- **Proven non-null**, as above. A string literal never decays into `&` —
  its bytes live in a constant global.
- **An exact match beats a decayed one.** Under overloading, decayed
  readings enter resolution only when no candidate matches the pointer type
  directly, so `fn f(x: T*)` beside `fn f(x: &T)` stays unambiguous.

Generic inference participates: at a `const`/`&` slot, unification also
tries the argument's pointee against the parameter pattern, one level down,
so a `list<int32>*` at `self: &list<T>` binds `T = int32` (previously
"cannot infer type parameter(s) T"). Facts about the *pointer's own storage*
are irrelevant to the callee — a `const` or `@volatile` pointer variable
decays fine (the load of the pointer itself honors them) — and because the
pointer is passed **by value**, a flow-narrowed non-null fact survives the
call, unlike lending the pointer variable itself as `&`. A decayed
argument is a borrowed reference, never a transfer of ownership.

See
[examples/functions/pointer_decay.mc](../examples/functions/pointer_decay.mc).

### @noalias parameters

`@noalias` on a **pointer** parameter is mcc's `restrict`: a promise, kept by
the caller, that the pointer does not overlap any other pointer the function
can reach. It maps to LLVM's `noalias` argument attribute, letting the
optimizer assume the regions are disjoint, so a copy loop can skip the runtime
overlap check and be recognized as a bulk move:

```c
fn copy(@noalias dst: uint8*, @noalias src: uint8*, n: uint64) {
    for i in range(n) { dst[i] = src[i]; }
}
```

The annotation precedes any `const` and the parameter name (`@noalias const
p: T*` is allowed — a `const` pointer is read-only but still a single pointer).
It changes **no ABI**, unlike `const`/`&`, so it is allowed on `@extern`
declarations too — the [libc bindings](#reaching-libc) mark `memcpy`, `strcpy`,
and the rest of the C11 `restrict` family this way (but not `memmove`, whose
regions may overlap by design).

The promise is **unchecked**: if the pointers actually overlap, the behavior is
undefined, exactly as with C's `restrict`. mcc does not verify it and does not
warn when the same variable is passed to two `@noalias` parameters. `@noalias`
is only a pointer-parameter annotation: it is rejected on a non-pointer
parameter, on a `&` parameter (aliasing two `&` parameters is allowed by
design, which would contradict the promise), and on `@asm` functions. See
[examples/functions/noalias.mc](../examples/functions/noalias.mc).

### @nonnull parameters

`@nonnull` on a **pointer** parameter is a *checked* "definitely non-null"
refinement over the nullable-by-default `T*`: the callee is statically
guaranteed a non-null argument and can skip the defensive re-check. Unlike
`@noalias`, the promise is not the caller's to break — every call site must
**prove** the argument non-null, or the program does not compile:

```c
fn first(@nonnull p: int32*) -> int32 {
    return *p;    // no null check needed: the compiler guaranteed it
}

fn main() -> int32 {
    let x: int32 = 42;
    return first(&x);      // ok: &x is always non-null
    // first(null);        // compile error: null literal
    // first(make());      // compile error: a returned T* carries no proof
}
```

The accepted proofs are the always-non-null sources: `&x` (the address of
named storage), a string or array literal, an array decaying to a pointer
(local, `@static`, or global — including an array reached through a
member/index chain: `grid[0]`, `unit.sizes`, a flexible `p->data`; an
array step is address arithmetic off the chain's base, never a load, so
its decay is a derived address like `p + n`), transitively a `@nonnull` parameter of
the calling function (so a `@nonnull` callee forwards its own parameter
onward with no check), a plain pointer local or a pointer-typed field
projection flow-narrowed by a null check (below), a conditional
`c ? a : b` whose arms are both proven sources (whichever arm executes
is proven, recursively, so `flag ? "y" : "n"` compiles while
`flag ? "y" : maybe_null` stays rejected; the condition is irrelevant),
an `as` cast to a
pointer type of any proven source (aliases of
pointer types count; a non-pointer intermediate like `p as uint64 as T*`
severs the proof), and the explicit escape hatch (further below).

**Flow-narrowing.** Idiomatic null-checked code needs no escape hatch: the
compiler narrows a plain `T*` local to non-null from either of the two `if`
guard shapes. `if (p != null) { ... }` proves `p` inside the then branch
(with an `else`, `if (p == null) {A} else {B}` symmetrically proves `p` in
`B`), and the C-idiomatic early guard — an else-less `if (p == null)` whose
body always diverges (`return`, `break`, `continue`, a call to a
[`@noreturn` function](#noreturn-functions) like `abort()`, an
[`unreachable;`](#the-unreachable-statement), or every nested path
returning) — proves `p` for the remainder of the enclosing scope.
`and`/`or` chains thread through both shapes:
`if (p != null and q != null)` proves both in the then branch, and a
diverging `if (p == null or q == null)` proves both for the remainder
(a true `or` / false `and` pins down neither operand, so those directions
prove nothing). Short-circuiting itself narrows too: in
`p != null and use(p)` the right operand only runs when the left held, so
`p` is proven while it evaluates (symmetrically after a false
`p == null or ...`):

```c
fn get(p: int32*) -> int32 {
    if (p == null) { return 0; }
    return first(p);      // ok: the early guard proved p non-null
}

fn show(p: int32*) -> int32 {
    if (p != null) {
        return first(p);  // ok: narrowed inside the then branch
    }
    return -1;            // outside the guard p is unproven again
}
```

Narrowing is purely static — it emits no instructions — and it is
deliberately conservative, so the fact only exists where nothing can null
the pointer between the check and the use:

- **Bare local pointer variables narrow, and so do field projections**
  like `s.p` or `b->data` (see the projection rules below, which are
  stricter). Globals never narrow (any call in between could store null
  into one), `&` parameters never carry a *name* fact (a callee taking
  two `&` references can alias one, so a call could null it without
  naming it here), and index expressions like `a[i]` carry no fact —
  assert those with `!` instead.
- **Taking `&p` anywhere in the function disables narrowing of `p`**
  entirely: once its address exists, a stored pointer could null `p`
  without ever naming it.
- **The fact dies on anything that could null the variable**: reassigning
  `p`, passing `p` as a `&` argument, or a shadowing `let p`. A
  reassignment kills only *with its store* — the right-hand side evaluates
  first, so `cur = cur->next` reads through the still-narrowed name — and
  the pointer compounds `p += n` / `p -= n` are exempt entirely:
  arithmetic off a non-null pointer is the same always-non-null derived
  address `p + n` is, so the fact survives the move. An invalidation
  inside a nested block persists outward, and it is path-insensitive:
  invalidating `p` in one branch of an inner `if` drops the fact for the
  code after it, whichever branch runs.
- **A loop drops exactly the facts it could invalidate.** A loop's body and
  condition re-run on the back edge, where a later iteration may already
  have nulled the pointer, so at loop entry (`while`, `until`, `for`) a
  pre-scan of the whole loop kills the facts for every name the loop
  reassigns (`p = ...`; a pointer's `p += n` is arithmetic and keeps its
  fact, here as everywhere), shadows with a `let p`, or lends as a
  bare `&` argument, anywhere in the subtree (nested branches, `case`
  arms, `defer` bodies, and both branches of an `@if` included; `&`
  positions are resolved by callee name across all overloads,
  conservatively). Everything else survives (the guard-then-loop idiom
  `if (p == null) return; while (...) { use(p); }` just works), and a
  surviving fact holds past the loop's end too. When the loop does kill a
  fact, guard *inside* the body; a body-local guard re-establishes it every
  iteration.

Loop headers narrow like guards. `while (p != null)` (or
`until (p == null)`) proves `p` at the top of every iteration, even when
the body reassigns `p`, since the condition re-proves it on each back edge.
And a loop that can only exit through its condition proves the exit
direction after it: `while (p == null) { p = next(); }` leaves `p` non-null
for the code after the loop, whatever the body did (a `break` in the body
disables this, because it reaches the end without re-testing the
condition).

Facts also seed through `let`: a pointer binding whose initializer is
provably non-null (`let q = p;` under a guard, `let p = &x;`,
`let s: uint8* = "...";`, `let m = flag ? "y" : "n";`, `let q = p!;`)
starts narrowed, under the same eligibility rules, and dies on the same
events.

**Projection facts.** The same guard shapes also narrow a pointer-typed
*field projection*: `if (b->data != null)` proves `b->data` in the then
branch, a diverging `if (b->data == null)` proves it for the remainder,
loop headers and exit conditions prove it the same way, and `and`/`or`
chains thread projections and bare names together
(`if (b == null or b->data == null) { return -1; }` proves both). A
proven projection crosses `@nonnull` slots and decays into `const`/`&`
parameters exactly like a proven local. The fact is keyed by the access
path, so any depth works (`p->inner->data`), `.` and `->` spell the same
fact, and `(*b).data` is the same fact as `b->data`. The base must be a
local variable; `&` and `@nonnull` parameters are fine as *bases*
(`fn f(b: &Buf)` may guard and use `b.data`), while globals,
call results, and array elements (`bs[0].data`) carry no path fact. A
`@volatile` owner anywhere along the path (directly or inherited via
`extends`) never forms a fact: volatile means the field can change
between the check and the use, the same reason volatile accesses are
never elided or reordered.

Because the field itself lives in memory that other code can reach, a
projection fact dies far more eagerly than a name fact:

- **at every function call, unless the compiler proves the callee,
  transitively, writes no non-local memory**: any writing callee could
  store to the field through an escaped or global pointer. The proof is
  a per-function, whole-program **write-effect bit**, computed
  bottom-up over the call graph before bodies are emitted: a function
  is write-free when its body has no through-memory store (`*p = v`,
  `a[i] = v`, `s.f = v`, compound forms included -- a store to its own
  local struct counts too, under the strict rule), no assignment to a
  `&` parameter or a global, nothing opaque (`@asm`, a call through
  a function-pointer value, `va_start`/`va_end`, a bodyless callee
  such as `@extern` or an unpaired prototype, a protocol/slice
  `for` loop -- the builtin `range`/`enumerate` counting loops are
  fine -- and, in a program where some struct declares a call-bearing
  field default, any struct literal or bare annotated `let`, since
  defaults evaluate at the application site), and every callee is
  likewise write-free (candidate sets union
  over a name's overloads, and a generic template takes one bit for
  all its instances; write-free recursion cycles resolve as
  write-free). So a pure math leaf no longer kills, while `println`
  still does (it wraps `@extern printf`). Calls in a later argument
  follow the same rule: `f(b->data, g())` always compiles, since
  arguments check and load left to right, while `f(g(), b->data)`
  compiles only when `g` is proven write-free;
- **at every through-memory store**: `*p = ...`, `arr[i] = ...`, and
  `s.f = ...` (any base, any field: a write through one base may alias
  another's field, and a union member store hits its siblings), plus
  their compound forms;
- **wholesale at every loop entry** (unlike name facts, no pre-scan
  yet); a `while (b->data != null)` header still proves the body top,
  since the condition re-proves it on each back edge;
- **on the base**: reassigning, shadowing, or `&`-lending `b` kills
  every `b...` path fact.

One asymmetry in guard chains: when a *later* operand of the chain can
call (`if (b->data != null and check())`), the projection fact does not
form, because `check()` runs after the null test and could null the
field before the branch; a bare local has no such window, so name facts
still form there. This formation ban is syntactic and does not consult
the write-effect bit -- a write-free `check()` still blocks formation.
Taking `&b->data` is not itself an event (unlike `&p` for a name
fact): only an actual aliasing write can null the field, and every
channel for one (a store or a killing call) already drops the fact.

Where a checked field must cross a writing call or a loop, bind it:
`let q = b->data;` under the guard seeds a *name* fact for `q` that
survives both, and `b->data!` remains the explicit hatch.

Each null comparison must be exactly `p != null` / `p == null` (either
operand order), possibly chained with `and`/`or` as above. Ternary
conditions do not narrow, and index expressions carry no fact. Where
narrowing cannot see the invariant, the escape hatch below is the
pressure valve.

**The escape hatch: postfix `!`.** A heap or returned `T*` carries no
syntactic proof, so it cannot cross into a `@nonnull` slot on its own. The
postfix non-null assertion `p!` is the programmer's explicit claim that it
is safe:

```c
let p: int32* = malloc(4) as int32*;
*p = 42;
first(p!);    // ok: the assertion is the proof
```

`p!` is **purely static and costs nothing at runtime**: it evaluates to its
operand unchanged, emits no instructions, and no check is ever inserted.
**Asserting a pointer that is actually null is undefined behavior**: the
callee was promised non-null and skips its defensive check. Use it only
where you know the invariant holds (e.g. right after a checked allocation).

The assertion covers exactly the expression it wraps, but a binding
initialized from it seeds a narrowed fact (above), so
`let q = p!; first(q);` compiles: one hatch at the binding covers every
later use, until an invalidation kills the fact as usual. `p!` is legal on
any pointer expression anywhere; outside a `@nonnull` argument it is simply
the identity. `null!` is rejected outright (always wrong), as is `!` on a
non-pointer operand.

One lexing gotcha: `!=` is a single comparison token, so `p != q` always
compares; asserting and then comparing needs parentheses: `(p!) == q`.

To keep the per-binding fact sound, a `@nonnull` parameter cannot be
reassigned, cannot have its address taken (a `T**` could store null through
it), cannot be passed as a `&` argument (the callee writes through a
hidden reference and could store null into the parameter's storage), and a
shadowing `let` of the same name is a fresh, unproven binding. A
function with `@nonnull` parameters is a legal function value: the function
type spells the contract (`fn(@nonnull int32*) -> int32`), `let f = first;`
infers it, and a call through the value runs the same call-site proof as a
direct call — see
[@nonnull-carrying function types](#nonnull-carrying-function-types).

Like `@noalias`, the annotation precedes any `const` (`@nonnull const p: T*`
composes; the two annotations combine in either order), changes **no ABI**,
and so is allowed on `@extern` declarations and rides along on
[interface files](#interface-files). It is rejected on non-pointer
parameters, on `&` (a `&` parameter is passed by reference and is never
null), and on `@asm` functions. At the LLVM level the established fact is
handed to the optimizer as the `nonnull` and `dereferenceable(sizeof(T))`
argument attributes.

**The standard library is annotated.** The data, source, key, and
destination pointer parameters of the stdlib declare their contracts with
`@nonnull`: the `memory` copy/fill family (`bytecopy`, `copy`, `bytezero`,
`zero`, `bytefill`, `fill`), the `hashing/` digests (`md5`, `crc32`,
`murmur3`), `dict`'s string keys (`dict::set`/`dict::get`/`dict::remove`),
and the raw-array `(T*, n)` source overload of the `list<T>` constructor
(which `string` inherits).
Passing an unproven pointer to any of them is a compile error instead of
a latent crash. A stack buffer (`&x`, an array) or a string literal is
already a proof; a heap buffer needs a one-line diverging guard after the
allocation (`if (p == null) return 1;`), which loops that do not touch
the pointer preserve. Container `self` parameters are `&`/`const`
receivers, where non-null holds by construction, so they carry the
guarantee without annotations; a heap `list<T>*` or `dict<V>*` reaches
them through the same one-line guard, by
[decaying](#pointer-decay-into-constreference-parameters) into the receiver
slot. Parameters for which null is meaningful stay plain: `resize` (null
allocates fresh) and `dealloc` (null is a no-op). The `libc/` bindings follow as a separate pass
([roadmap](../ROADMAP.md#planned)).

See
[examples/functions/nonnull.mc](../examples/functions/nonnull.mc); for
flow-narrowing,
[examples/functions/nonnull_narrowing.mc](../examples/functions/nonnull_narrowing.mc);
for narrowed facts crossing loops,
[examples/functions/nonnull_loops.mc](../examples/functions/nonnull_loops.mc);
for projection facts,
[examples/functions/nonnull_projections.mc](../examples/functions/nonnull_projections.mc);
for the escape hatch,
[examples/functions/nonnull_assert.mc](../examples/functions/nonnull_assert.mc);
and for the heap-buffer migration,
[examples/memory/nonnull_heap_buffers.mc](../examples/memory/nonnull_heap_buffers.mc).

### Function overloading

Plain (concrete) functions sharing a name form an **overload set**: the call
picks the member whose parameter list fits the arguments, so a
constructor-flavored family reads as one operation:

```c
fn counter_init(self: &counter)                          // zeroed
fn counter_init(self: &counter, start: int32)            // seeded
fn counter_init(self: &counter, start: int32, step: int32)
```

Resolution follows the same order as
[generic overload sets](#generics) — viability by argument shape, then the
most specific candidate — and **resolution is by arguments only**. The rules
that keep it C-simple:

- **Overloads must differ in parameter types.** Two overloads may not differ
  solely in return type; that stays a duplicate definition. Nor solely in
  `const`/`&` markers on the same types (a same-type `&`/non-`&` pair
  is uncallable under the resolution rules — an rvalue argument filters out
  the `&` candidate, and an lvalue keeps both in a same-shape tie — so
  allowing it buys nothing), nor solely in `@nonnull`/`@noalias` annotations
  (caller promises about the supplied value, not part of the call shape).
  Each of these reports
  `function 'f(int32)' already defined; overloads must differ in parameter
  types`, naming the shared signature.
- **Width-only differences are ambiguous for untyped literals.** With
  `f(x: int32)` beside `f(x: int64)`, the call `f(0)` is a compile error —
  the literal adapts to either width, and mcc declares rather than guesses.
  `f(0 as int64)` or a typed variable disambiguates.
- **Sets are open.** Any module may add overloads to an existing name, with
  no opt-in marker: the set is the whole-program union at import merge, in
  any import order. The gate is the declare-time collision rules, which run
  cross-module: same-pattern duplicates collide (with a note citing the
  prior member's site), alpha-renamed same-base templates collide, and
  overlapping [closed type groups](#closed-type-groups) collide. Resolution
  is unchanged, so adding an import can only add candidates or collide
  loudly — it never silently rewires a call, with three deliberate
  edges: an import may supply a *better-ranked* candidate (a concrete
  beating a group template is the intended protocol behavior, see below),
  an *equal-ranked* candidate makes existing calls ambiguous (a loud error
  citing both declaration sites) — unless it is strictly *more specialized*
  than the incumbents, in which case
  [subsumption](#rank-tied-templates-subsumption) hands it the former tie,
  the same deliberate better-candidate-wins edge one rung down — and a
  name growing into a set moves its
  calls off the direct-call fast path and makes `let g = f;` an error
  (below).
- **A single visible signature keeps its plain symbol.** A name mangles per
  declaring file, counting the signatures that file can see (the
  whole-program set minus other modules' `@private` members): with two or
  more, each of the file's members links by a signature-derived symbol
  spelled from its parameter types (`f(int32, char*)`). Generic templates
  mangle the same way, by parameter *pattern*: every template takes a base
  spelled from its declaration (`f<$0>($0*)`) and an instance appends its
  bindings — see [Template symbols](#template-symbols). A lone concrete
  function keeps its plain, C-linkable symbol and the direct-call fast
  path — zero cost until a name actually overloads. (The accepted cost on overloaded calls: they route
  through the overload-resolution path, so a `const`-struct argument spills
  to a temporary instead of sharing the caller's storage.)
- **Privacy is per overload.** An `@private` overload is a candidate only
  inside its own module: a foreign call simply does not see it — resolution
  falls through to the members the calling file can see (when *no* member
  is visible the call errors, `function 'f' is private to util.mc`). It
  never collides with other modules' members either: a `@private` member's
  mangled symbol is salted with its file's stem (`f(int32).util`), so two
  modules may each keep a private overload of the same shape. Note the
  flip side: a dispatch site that lives in another module resolves *there*,
  so a `@private` overload never influences it — a library's deferred
  `case type` dispatch falls to the members visible in the library even
  when the calling module holds a private overload that its own direct
  calls resolve to. Within one file, a `@private`/public pair on one parameter
  list is still a duplicate.
- **An overloaded name is not a function value.** `let g = f;` needs a
  single address; with a set there is no one `f` — and since sets are
  open, adding an import can be what turns a working `let g = f;` into
  this error.
- **Non-overloadables:** `main` (JIT and `cc` resolve the plain symbol),
  C-style variadic (`...`) functions,
  functions with a `va_list` parameter, `@extern`/`@symbol` functions
  (their C symbol is fixed), and `@static` functions. A
  [collecting function](#native-variadic-arguments) *does* overload; see
  the ranking rule below.

String and array literals keep adapting when a function becomes overloaded: a
literal (or a ternary of literals) still borrows to a `slice<char>` /
`slice<const char>` (or, for an array literal, a `slice<T>`) parameter exactly
as at a [non-overloaded call](#slices) — a literal argument contributes nothing
to overload resolution, so `f([1, 2, 3])` picks the `slice<int32>` overload
over an `int32*` one rather than adapting to the pointer.

**Mixed generic/concrete sets.** A generic template may share its name with
concrete functions — from any module, sets being open; the candidates join
one set and resolve under a leading (tier, specificity) rank with three
tiers: a **concrete** overload beats a **bounded** generic (one with a
[closed type group](#closed-type-groups) or a nominal
[`extends` bound](#bounds)) beats an **unbounded** generic.
The concrete tier wins on an exact match — including against a generic
whose *effective* parameter list ties the concrete one — the bounded tier's
written, closed commitment to a type set beats the fully open pattern, and
the unbounded generic covers everything else. Explicit type arguments
(`f<int32>(...)`) select among the generic candidates only. One component
ranks outside the tiers: a [collecting](#native-variadic-arguments)
candidate that must collect loses to *any* candidate that matches without
collecting, whatever their tiers (an exact-arity unbounded generic beats a
concrete collecting fallback), a pass-through-shaped final argument counts
as not collecting, a collecting candidate's specificity counts its fixed
prefix only, and more fixed parameters breaks a tie between collectors.
Two same-tier candidates of equal specificity go to one last arbiter —
[subsumption](#rank-tied-templates-subsumption) — and only a cohort it
cannot order stays the
ambiguity error, which is also the enforced collision rule between the
classes (a generic whose substituted parameter list duplicates a concrete
one is not statically detectable in general). The concrete side of a mixed
set keeps the concrete rules: `main`, C-variadic, and `va_list`-taking
functions cannot join, whichever side declares first. The symbol choice
counts concrete signatures alone, so one concrete member beside a template
still keeps its plain, C-linkable symbol.

#### Rank-tied templates: subsumption

The full resolution order for a call is **viability, then rank, then
subsumption, then ambiguity**:

1. **Viability** — each candidate must match the arguments by shape, with
   the group/bound filters applied to its deduced bindings. An untyped
   integer literal at a bare type-parameter slot keeps a candidate viable
   only when the deduced binding is an **integer type** — the generic
   mirror of the concrete `is_integer` shape rule, since mcc has no
   int-to-float literal adaptation. So with a diagonal
   `f(x: T, y: T)` beside a converting `f(x: T, y: U)`, the call
   `f(fv, 1)` on a `float64` variable is *not* a tie: the diagonal deduces
   `T = float64` at the literal's slot, cannot emit it, and drops out.
2. **Rank** — `(collecting?, tier, specificity)` as above: tiers and
   specificity are supreme, and no comparison ever crosses them (a bounded
   template still beats an unbounded one outright, tier-over-specificity).
3. **Subsumption** — among the top rank-tied cohort, the candidate that is
   **strictly more specialized than every other member** wins; only a
   cohort with no such maximum is the ambiguity error.

Template `A` **subsumes into** `B` (`A ⊑ B`, "A is at least as specialized
as B") when `A`'s parameter pattern is an *instance* of `B`'s **and** `A`'s
constraints *imply* `B`'s:

- **Pattern instance.** `B`'s type parameters act as wildcards that must
  bind **consistently** to sub-patterns of `A` — a repeated name must bind
  the same sub-pattern every time. That is exactly what orders the
  diagonal: `f(x: T, y: T) ⊑ f(x: T, y: U)` (`T := T`, `U := T` binds
  fine), while the reverse mapping fails (`A`'s single `T` cannot stand for
  both wildcards' occurrences at once). A wildcard absorbs surplus pointer
  stars (`T` matches `int32*`); a concrete name needs the exact name and
  equal pointer depth; [generic-alias](#generic-aliases) spellings are
  expanded first, exactly as in inference, so an alias-spelled diagonal
  (`diag<V>` for `pair<V, V>`) orders identically. Arity, the
  [collecting](#native-variadic-arguments) flag, and the `&` positions
  must agree outright (`&` markers are template identity); `const`
  markers and return types are ignored, as in the duplicate rules.
- **Constraint implication.** For every wildcard of `B` that carries a
  constraint — a [closed type group](#closed-type-groups) or an
  [`extends` bound](#bounds); a *default* is a fill-in, not a constraint —
  the sub-pattern it bound must provably satisfy it. A concrete sub-pattern
  is checked directly (group membership / the nominal subtype relation). A
  type parameter of `A` must carry a constraint that **implies** the
  wildcard's: groups imply by **subset** (`T: int8` implies
  `U: int8 | int16`), bounds by the declared **nominal chain**
  (`T extends circle` implies `U extends shape` when `circle` extends
  `shape`, transitively). A group never implies a bound nor vice versa —
  incomparable, conservatively — and an **unconstrained** parameter implies
  nothing. So a bounded diagonal with the *tighter* constraint still wins
  (`f<T: int8 | int16>(x: T, y: T)` over
  `f<A: int8 | int16, B: int8 | int16>(x: A, y: B)`), while a *looser*
  diagonal against a tighter open pattern has its pattern direction and
  constraint direction in conflict: incomparable, and the tie stands.

The winner must be the cohort's unique **maximum** — strictly subsuming
into *every* other member. A three-way tie between
`f(T, T, T)`, `f(T, T, U)`, and `f(T, U, V)` therefore resolves to the full
diagonal, while the fork `f(T, T, U)` / `f(T, U, U)` / `f(T, U, V)` stays
ambiguous: the two partial diagonals are mutually non-subsuming, so no
member beats *all* others. Mutual subsumption (alpha-equal value patterns
that are nonetheless distinct templates, e.g. via an extra defaulted
parameter) likewise leaves no strict winner. Two distinct maxima are
impossible — they would strictly subsume each other, and alpha-equivalent
patterns already collide at declaration. Note what subsumption does *not*
do: it never reorders across tiers or specificity, and it never rescues a
genuinely incomparable pair — rank-tied
[partial specializations](#partial-specialization) like `pair<int32, U>`
vs `pair<T, int8>` each hold a concrete type where the other holds a
wildcard, and stay the ambiguity error. See
[examples/functions/overload_subsumption.mc](../examples/functions/overload_subsumption.mc).

**Prototypes pair per signature.** A
[bodyless prototype](#bodyless-fn-prototypes) names the member with its
parameter list: a same-signature prototype/definition pair keeps every
shipped pairing rule (return-type or convention drift on the same parameter
list is still `definition of 'f' does not match its prototype`), while a
prototype with a *different* parameter list simply joins the overload set as
its own member. A prototype with no matching definition stays what it
already is — a link-time error.

**Open sets as protocols.** Because a concrete overload outranks a bounded
generic, a module can *replace* group-covered behavior for its own types
with no annotation: where a library covers the signed integers with one
closed-group template, a user's concrete overload at `int32` simply wins
the exact match. This is the language's protocol story — make a type
appendable (see
[examples/functions/open_overloads.mc](../examples/functions/open_overloads.mc))
or printable (the stdlib [formatting protocol](#formatting)), and, once the
planned iteration protocol lands, iterable, by writing one overload for it
in your own module.
Only *same-pattern* replacement (swapping out a library's concrete `bool`
member, say) still collides; the `@override` annotation below covers that
last case.

**`@override` replaces a same-pattern member.** Adding an overload can only
*extend* a set; the one thing it cannot do is replace a member that already
covers a shape, because a second same-pattern definition collides as a
duplicate. `@override` is the escape valve for exactly that case — the
stdlib's own concrete `bool` formatter, or its unbounded `<typename>`
fallback, replaced by one of the same pattern. It suppresses the
duplicate-pattern collision and **drops the overridden (unannotated)
definition** before code generation, so only the `@override` body is emitted,
under the member's shared mangled symbol. The replacement is therefore
**global** — in effect everywhere the original was, including `println`'s own
dispatch, which resolves inside the stdlib module — and **order-independent**:
the winner is chosen over the whole merged program, not the import prefix seen
so far.
An `@override` needs exactly **one source-visible, body-bearing, cross-module
target of the same pattern** (a concrete member's parameter list, or a
template's [order-independent base](#template-symbols)). Each of these is a
compile error: no matching target (typo protection, the C++
override-specifier rationale); a same-pattern target **in the same file**
(`@override` replaces *another* module's member, never a local one); a target
visible only as a [prototype](#bodyless-fn-prototypes) (its body lives in an
object that already defines the symbol, so it cannot be replaced by
re-emission); and a second `@override` of one pattern (they collide like any
duplicate). `@override` does not combine with `@extern` (no mcc body to
replace with), `@static` (file-local, never joins a cross-module set),
`@removed` (a definition cannot both replace and be a tombstone), a bodyless
prototype (no body to emit), or — for now — `@private` (a private symbol is
salted and file-local, so it cannot take over the target's public symbol; the
file-local variant would need distinct shadowing semantics). Because
replacement works by reusing the target's symbol and dropping the original,
the target must be **compiled from source** in the same build; a
separately-compiled original (a future ABI-pinned `.mci` object) already
defines the symbol in its own object and cannot be overridden — which is why a
prototype-only target is rejected rather than silently mislinked. See
[examples/functions/override.mc](../examples/functions/override.mc).

**Interfaces.** `--emit-interface` renders an overload set's members from
the emitting file as same-name prototypes, and the file's whole
contribution always travels: an included function pulls in every same-name
sibling, even an unreferenced `@private` overload (kept `@private` in the
stub) and the generic members of a mixed set. A stub describes an
already-compiled object, so its symbols are **pinned**: the importer
re-derives each member's symbol from the stub's own declarations plus its
import closure — exactly what the defining object was compiled seeing —
and consumer-side extensions never re-mangle a stub's members. A consumer
may extend a stub's set with new signatures (the set then mixes the stub's
pinned symbols with the consumer's), and two stubs that each pin the same
plain symbol collide at compile time — correct, since the two objects they
describe could never link together.

See [examples/functions/overloading.mc](../examples/functions/overloading.mc),
[examples/functions/open_overloads.mc](../examples/functions/open_overloads.mc),
and [examples/functions/override.mc](../examples/functions/override.mc).

### Methods

A function may be **namespaced to a struct** by writing its name as
`Type::method`, and is called by that explicit qualified name — or through
the [dot-call sugar](#calling-methods-dot-syntax), `p.magnitude()`:

```c
struct point { x: float64; y: float64; }

fn point::magnitude(const self: &point) -> float64 {
    return sqrt(self.x * self.x + self.y * self.y);
}

fn main() -> int32 {
    let p: point = { x = 3.0, y = 4.0 };
    return point::magnitude(p) as int32;   // 5
}
```

This is the foundational, **explicit-call** form. Its rules today:

- **`self` is a checked receiver.** A first parameter named `self` is the
  method's *receiver*, and a receiver must be either **reference-shaped** or the
  consuming **`own self: T`**:
  `const self: &T` reads the receiver, `self: &T` mutates it in place (the write
  is visible to the caller), `@nonnull self: T*` is the pointer-class
  receiver, and `own self: T` **consumes** it — see
  [consuming receivers](#consuming-receivers-own-self) below. A **by-value copy
  receiver** (`self: T`) is rejected — it would copy the receiver, slicing a
  derived value and never reaching a dynamic-dispatch entry. The caller never
  writes the `&`: an ordinary value argument (`point::magnitude(p)`, or the
  dot-call `p.magnitude()`) forms the hidden reference automatically. The check
  is *name-based*, so a **receiverless** method — one whose first parameter is
  not named `self` — is untouched: `fn point::origin() -> point` and
  `fn point::of(x: float64, y: float64)` are legal.
- **The qualifier is a namespace.** `point::` names the struct the method
  belongs to and nothing more — the only rule on the qualifier itself is that it
  is a declared, complete type (below).
- **The qualifier must be a declared, complete type.** The segment before
  `::` must name a type in scope: a struct, a builtin type
  (`fn int32::m` — see [methods on type
  aliases and builtin types](#methods-on-type-aliases-and-builtin-types)), or
  a `type` alias of either, which canonicalizes to the type it names. An
  enum, an undeclared name, or an alias of a type with no bare-name spelling
  (a pointer, array, or function type) is the error
  `no struct type 'foo' for method 'foo::bar'`; a *generic* struct or alias
  may be named only with its type parameters annotated — see
  [methods on a generic struct](#methods-on-a-generic-struct).
  (`Enum::Member` remains a value expression — only a `::` member *followed
  by* `(` is a qualified call.)
- **The qualified name is the whole identity.** `point::magnitude` is a
  single name everywhere — registration, the LLVM symbol, and
  [overloading](#function-overloading) all key on the string, so two structs
  may share a method name without colliding (`point::area` vs `rect::area`),
  a `Type::method` set overloads by argument exactly like a plain name, and
  `@private`/`@override` work unchanged.

#### Methods on a generic struct

A **generic** struct namespaces methods the same way, with the struct's type
parameters written *before* the `::`:

```c
struct point<T> { x: T; y: T; }

fn point<T>::magnitude(const self: &point<T>) -> float64 {
    return sqrt((self.x * self.x + self.y * self.y) as float64);
}

fn main() -> int32 {
    let p: point<float64> = { x = 3.0, y = 4.0 };
    return point::magnitude(p) as int32;   // 5
}
```

The whole existing generic machinery applies unchanged — one instance is
monomorphized per element type, so `point::magnitude` over `point<int32>` and
`point<float64>` are distinct functions. Its extra rules:

- **The qualifier must annotate its type parameters.** A declaration may not
  name a generic struct bare: `fn point::magnitude` is the error
  `struct 'point' is generic; the method qualifier must annotate its type
  parameter(s), e.g. 'fn point<T>::magnitude' or 'fn point<float64>::magnitude'`.
  Only a **complete** type may be named bare — a non-generic struct, a builtin,
  an alias of a complete type, or a **fully-defaulted** generic (where the bare
  name is already a complete type use: with `struct box<T = int32>`,
  `fn box::tag` *is* the specialization `fn box<int32>::tag`, the tail filling
  from the defaults exactly as in a type use). The method's own type parameters
  (`fn point::m<W>`) sit after the name and never satisfy the requirement.
  **Calls are different**: a *bare* call qualifier is a pure namespace —
  `point::magnitude(p)` looks the registered family up and infers from the
  arguments — while an annotated one
  [pins the instantiation](#explicit-type-arguments-at-a-qualified-call).
- **The receiver is explicit.** There is no `point`-means-`point<T>` sugar:
  inside a `point<T>::` method the receiver (and every parameter and the return
  type) must name its type arguments — `const self: &point<T>`. A bare
  `self: &point` keeps the ordinary generic arity error
  (`struct 'point' expects 1 type argument(s), got 0`). Type arguments are
  **inferred** from the call arguments as usual (`point::magnitude(p)` binds
  `T` from `p`) — or the call qualifier may
  [spell the instantiation](#explicit-type-arguments-at-a-qualified-call)
  (`point<float64>::magnitude(p)`), pinning the receiver instantiation.
- **A method may declare its own type parameters**, written *after* `::method`:
  `fn box<T>::combine<U>(const self: &box<T>, extra: U) -> U`. The struct's
  parameters and the method's own parameters merge into one uniform template
  (concatenated names, merged defaults, groups, and bounds), so both are
  inferred at the call. A method type parameter may **not shadow** one of the
  struct's — a name that appears in both lists is the error
  `method type parameter 'T' shadows a type parameter of struct 'point'`.

[Dot-call sugar](#calling-methods-dot-syntax) (`p.magnitude()`),
[constructors](#constructors) (`point(1, 2)`),
[destructors](#destructors), and
[explicit type arguments at the call qualifier](#explicit-type-arguments-at-a-qualified-call)
(`point<float64>::magnitude(p)`) build on this form; dynamic dispatch remains
future work, and a method's **own** type parameters are always inferred. See
[examples/types/methods.mc](../examples/types/methods.mc).

##### Specializing a method for one instantiation

A method may name a **concrete** type before the `::` instead of a type
parameter — `fn point<float64>::method`. That is a **specialization**: a
concrete body for one instantiation of the struct, coexisting with the generic
method and **outranking** it for a matching receiver (the existing
concrete-beats-generic overload ranking does the dispatch — a specialization is
just an ordinary concrete overload of the qualified name).

```c
struct point<T> { x: T; y: T; }

// The generic fallback: any point<T>.
fn point<T>::magnitude(self: &point<T>) -> float64 {
    return sqrt(pow(self.x as float64, 2.0) + pow(self.y as float64, 2.0));
}

// A specialization: point<float64> needs no `as float64` casts.
fn point<float64>::magnitude(self: &point<float64>) -> float64 {
    return sqrt(pow(self.x, 2.0) + pow(self.y, 2.0));
}
```

A `point<float64>` receiver runs the specialization; a `point<int64>` receiver
falls to the generic. Its rules:

- **Concrete is decided by resolution.** Whether a pre-`::` argument is a
  type-parameter *name* or a concrete *type* is decided by resolving it
  against the type environment, so any concrete type may specialize a method —
  a builtin (`point<float64>`), a user struct (`holder<widget>`), or a
  structured type (`box<int32>`, `pair<int32*>`).
- **A generic base is not required.** A lone `fn box<int32>::only(...)` with no
  generic `box<T>::only` is simply a concrete namespaced overload.
- **Two bodies for one instantiation collide** — a duplicate specialization
  spells the same concrete parameter list and is rejected like any duplicate
  overload.

See
[examples/types/method_specialization.mc](../examples/types/method_specialization.mc).

##### Partial specialization

A method may also **mix** concrete types and fresh type parameters before the
`::` — a **partial specialization**: the concrete positions bind, the fresh
names stay free, and the method becomes a template matching only receivers
that agree on the concrete positions:

```c
struct pair<A, B> { a: A; b: B; }

fn pair<T, U>::describe(const self: &pair<T, U>) -> int32 { return 0; }        // any pair
fn pair<int32, U>::describe(const self: &pair<int32, U>) -> int32 { return 1; } // pair<int32, X>
fn pair<int32, int8>::describe(const self: &pair<int32, int8>) -> int32 { return 2; }
```

A `pair<int32, int8>` receiver runs the full specialization, a
`pair<int32, int64>` the partial, and anything else the generic. The ordering
is the **existing overload ranking**, no special dispatch: a full
specialization is a concrete overload (the top tier); a partial and the fully
generic method share the open-template tier, where the partial's concrete
positions score higher *pattern specificity* than bare parameter names. Two
partials that tie on rank for one receiver (`pair<int32, U>` and
`pair<T, int8>` for `pair<int32, int8>`) are the standard ambiguity error —
they are **incomparable** under the
[subsumption tie-break](#rank-tied-templates-subsumption): each holds a
concrete type where the other holds a wildcard, so neither pattern is an
instance of the other and the tie stands. The rules:

- **Fresh names are real type parameters.** They are inferred at the call,
  prepend the method's own `<...>` list (`fn pair<int32, U>::pick<W>` works),
  and may not shadow it. A fresh name may not reuse a struct parameter name
  that a concrete position binds: in `struct pair<A, B>`,
  `fn pair<int32, A>::m` is `type parameter 'A' shadows a type parameter of
  struct 'pair' bound to a concrete type by the partial specialization`
  (reusing the name of the position the parameter itself occupies —
  `fn pair<int32, B>::m` — is fine).
- **A fresh position may be bounded.** A closed [type group](#closed-type-groups)
  (`fn pair<int32, U: int8 | int16>::m`), an [`extends`
  bound](#bounds), or a [default](#type-parameter-defaults)
  decorates a fresh name exactly as in an ordinary declaration list; a
  decoration on a concrete type is rejected (`struct type argument 'int32'
  names a concrete type; ...`). **Bounding interacts with the ranking**: a
  group or bound lifts a template one tier, and the tier rule is
  tier-over-specificity — so a *bounded generic* method
  (`fn pair<K: int8 | int32, V>::m`) beats an *unbounded partial*
  (`fn pair<int32, U>::m`), a written commitment to a type set beating the
  open pattern. Bounding the partial too levels the tiers, and its concrete
  positions win on specificity again.
- **A mismatched receiver simply doesn't match.** A partial whose concrete
  positions disagree with the receiver is filtered out like any non-viable
  overload: the call falls to the generic method, or — with no generic to
  fall to — reports the pattern the partial demands (`argument 1 of
  'pair::m': expected pair<int32, int8>, got pair<int64, int8>`).

See
[examples/types/method_partial_specialization.mc](../examples/types/method_partial_specialization.mc).

#### Methods on type aliases and builtin types

Methods register to a **type**, and a [`type` alias](#type-aliases) is just an
alias: declaring a method for the alias *is* declaring it for the type it
names, and vice versa — both spellings register to, and call, one family.

```c
struct point<T> { x: T; y: T; }
type pointf = point<float64>;

// Declared through the alias: exactly `fn point<float64>::magnitude` — a
// specialization, outranking the generic for a point<float64> receiver.
fn pointf::magnitude(self: &pointf) -> float64 {
    return sqrt(pow(self.x, 2.0) + pow(self.y, 2.0));
}

fn point<T>::magnitude(self: &point<T>) -> float64 {   // the generic
    return sqrt(pow(self.x as float64, 2.0) + pow(self.y as float64, 2.0));
}

fn main() -> int32 {
    let p: pointf;
    p.x = 1.0; p.y = 1.0;
    point::magnitude(p);    // the specialization -- and
    pointf::magnitude(p);   // the alias spelling calls the same family
    return 0;
}
```

The qualifier is chased through the alias (chains included) to the type it
names, and the method registers under the **canonical** name. The same holds
at a call — with the alias's type arguments **honored**: `pointf::magnitude(p)`
resolves the alias as a type use, so it means exactly
`point<float64>::magnitude(p)` — the complete alias
[pins the receiver instantiation](#explicit-type-arguments-at-a-qualified-call),
and a receiver of another instantiation is an error, not a silent
re-dispatch. Only an alias that is *not* a complete type (`type pf = point`
over a generic `point`) canonicalizes by name alone and leaves dispatch to
infer. Each hop is access-checked, so a cross-file `@private` alias qualifier
errors like any other use of it. The rules:

- **A plain alias contributes its target's type arguments.**
  `fn pointf::m` is `fn point<float64>::m` — a
  [specialization](#specializing-a-method-for-one-instantiation). The two
  spellings of one signature collide as ordinary duplicates. Writing type
  arguments on a plain alias (`fn pointf<float64>::m`) is the error
  `type alias 'pointf' is not generic`.
- **A generic alias applied to written arguments substitutes them through
  its target** — the argument count checks against the *alias* (trailing
  defaults fill as usual). The substitution composes: with
  `type swap<X, Y> = pair<Y, X>`, `fn swap<int32, U>::pick` is the
  [partial specialization](#partial-specialization) `fn pair<U, int32>::pick`.
  A **duplicate-position** alias is a *diagonal constraint*: with
  `type diag<T> = pair<T, T>`, `fn diag<U>::m` declares **one** parameter
  `U` that must unify consistently — a `pair<int32, int32>` receiver binds
  it, a `pair<int32, float64>` receiver is rejected (or falls through to a
  generic sibling). The diagonal **beats** an open `fn pair<A, B>::m` for
  an agreeing receiver: repeated names score no extra pattern specificity,
  so the two tie on rank, and the
  [subsumption tie-break](#rank-tied-templates-subsumption) picks the
  diagonal — `pair<U, U>` is strictly an instance of `pair<A, B>` (the
  alias spelling participates through the same expansion inference uses).
- **A bare generic-alias qualifier is an error, like a bare generic
  struct.** A declaration must [annotate a generic qualifier's type
  parameters](#methods-on-a-generic-struct): with `type pf<T> = point<T>`,
  `fn pf::m` is `type alias 'pf' is generic; the method qualifier must
  annotate its type parameter(s), e.g. 'fn pf<T>::m' or 'fn pf<float64>::m'`.
  A **fully-defaulted** alias is complete, so its bare name works: with
  `type pf<T = float64> = point<T>`, `fn pf::m` *is* `fn point<float64>::m`.
  Calls through an **incomplete** alias stay bare-friendly — `pf::m(p)` over
  `type pf<T> = point<T>` chases the name and infers — while a *complete*
  spelling (written arguments, `pf<int32>::m(p)`, or a bare fully-defaulted
  alias) [pins its instantiation](#explicit-type-arguments-at-a-qualified-call).
  An alias parameter its target never uses is *inert*:
  written arguments for it vanish with the substitution, so a signature
  naming that parameter fails as an unknown type — alias transparency, not a
  special case.
- **Generic alias spellings are transparent to inference.** A parameter
  pattern written through a generic alias unifies as the type it names:
  `fn diag<V>::grab(const self: &diag<V>) -> V` unifies and shape-checks as
  `pair<V, V>`, binding `V` from a `pair<int32, int32>` argument (and
  enforcing the diagonal).
- **Builtin types are qualifiers too.** `fn int32::clamp(...)` (or any
  builtin name — with `type myint = int32`, `fn myint::clamp` is the same
  family) namespaces to the name string; `int32::clamp(x)` and
  `myint::clamp(x)` call it. Fresh names before the `::` still declare type
  parameters (`fn slice<T>::first(s: slice<T>) -> T`), but a builtin cannot
  be *specialized* — it has no declared parameter names for a concrete
  argument to bind — so `fn slice<int32>::first` is the error
  `cannot specialize builtin type 'slice'; spell the receiver type in the
  method's signature instead` (the signature alone already drives dispatch:
  a concrete receiver type outranks a generic pattern).

The standard library uses the builtin-qualifier form:
[lib/std/char.mc](../lib/std/char.mc) (`import "std/char";`) registers the
ctype classification and case-conversion methods on `char` —
`char::is_alpha`, `is_alnum`, `is_digit`, `is_hex`, `is_space`, `is_upper`,
`is_lower`, and `char::upper` / `char::lower` (non-letters unchanged).

See [examples/types/method_alias.mc](../examples/types/method_alias.mc) for
the feature and
[examples/systems/char_methods.mc](../examples/systems/char_methods.mc) for
the `std/char` module.

#### Explicit type arguments at a qualified call

A qualified call's qualifier may spell the receiver instantiation —
`point<float64>::magnitude(p)`. The written reference resolves as an
ordinary **type use**: a wrong count is the type-use arity error, a
fully-defaulted tail fills from the defaults, and a
[generic alias](#methods-on-type-aliases-and-builtin-types) substitutes
through its target, permutation included (`swap<int32, float64>::first(p)`
over `type swap<X, Y> = pair<Y, X>` pins `pair<float64, int32>`). Inside a
generic method body the enclosing type parameters resolve through the live
instantiation — the same channel `x as T` uses — which is what makes
**constructor and destructor chaining** expressible (their qualified form is
their only callable spelling):

```c
struct point<T> { x: T; y: T; }

fn point<T>::constructor(self: &point<T>, x: T, y: T) {
    self.x = x; self.y = y;
}

// A converting constructor chains to the direct member at the enclosing T.
fn point<T>::constructor<U>(self: &point<T>, x: U, y: U) {
    point<T>::constructor(self, x as T, y as T);
}

fn main() -> int32 {
    let p = point<float64>(1, 2);   // converting ctor -> chains at T = float64
    return (p.x + p.y) as int32;    // 3
}
```

The resolved instantiation **pins the receiver**: dispatch matches it
against each member's declared qualifier annotation — a fresh-parameter
position fixes that parameter's binding, and a concrete
([specialized](#specializing-a-method-for-one-instantiation)) position must
agree or the member does not apply. In particular:

- **The pin is authoritative.** `point<int32>::magnitude(p)` with
  `p: point<float64>` is the ordinary coercion error (`argument 1 of
  'point::magnitude': expected point<int32>, got point<float64>`), and a
  pin no member matches reports it (`'box::get' has no member for
  box<float64>: the qualifier's type arguments pin the receiver
  instantiation`).
- **Specializations dispatch.** A pin matching a declared
  [full specialization](#specializing-a-method-for-one-instantiation) (or a
  [partial](#partial-specialization)'s concrete positions) reaches it
  through the ordinary rank tiers; ranking and
  [subsumption](#rank-tied-templates-subsumption) are otherwise
  unperturbed.
- **A no-receiver member becomes callable** at a chosen instantiation with
  nothing to infer from: `point<float64>::origin()`.
- **Builtin generic families take the form too**: `slice<int32>::first(s)`
  (the *declaration*-side ban on specializing a builtin is unchanged).
- **The list is the struct's only.** A method's own type parameters stay
  inference-only, so a second list after the member name
  (`point<float64>::map<int32>(...)`) is a parse error — exactly the
  [dot-call rule](#calling-methods-dot-syntax). And `Type<args>::member`
  not followed by `(` is a parse error too: enum members fold to constants
  and enums are never generic, so only a call can follow.
- **Bare complete aliases pin.** `pointf::sum(q)` over
  `type pointf = point<float64>` means `point<float64>::sum(q)` — see
  [methods on type aliases](#methods-on-type-aliases-and-builtin-types).
- **Bare struct qualifiers are unchanged**: `point::magnitude(p)` stays
  pure namespace + inference.

See [examples/types/constructors.mc](../examples/types/constructors.mc) for
the chaining form.

#### Calling methods: dot syntax

`recv.method(args)` is sugar for `Type::method(recv, args)`, where `Type` is
the receiver's type. The receiver expression passes **verbatim** as the first
argument, so overload resolution (specializations, partials, subsumption),
`&`-receiver legality, evaluate-once addressing, and every diagnostic are
the desugared call's own:

```c
struct counter { n: int32; }
fn counter::bump(self: &counter) { self.n += 1; }
fn counter::get(const self: &counter) -> int32 { return self.n; }

fn main() -> int32 {
    let c: counter;
    c.n = 0;
    c.bump();                  // counter::bump(c) -- reference self writes c
    counter::bump(c);          // the qualified spelling stays valid
    let q = &c;
    q.bump();                  // a pointer receiver auto-derefs one hop:
    return q.get() - 3;        // counter::bump(*q), counter::get(*q)
}
```

The rules:

- **Fields shadow methods.** When the receiver's type declares a field of
  the name, `s.name(args)` keeps today's field-call behavior (calling the
  fn-typed field, or the usual not-callable error) — the method stays
  reachable as `Type::name(s, args)`. Only the call shape with *neither* a
  field nor a method gets the combined error, `struct 'point' has no field
  or method 'name'`; a bare member access `p.name` keeps the exact field
  diagnostics — there are no bound-method values.
- **A pointer receiver auto-derefs exactly one hop.** `q.m()` on a `S*` is
  `S::m(*q, ...)` — `.` on a pointer was an error, so the space is free —
  and inherits the dereference machinery (including
  [`-Wunchecked-dereference`](#-wunchecked-dereference)). Fields of the
  pointee still need `->`, and `->` stays fields-only: `q->m()` where `m`
  is not a field errors as before. A `S**` receiver is an error, as today.
- **Builtin and alias receivers dispatch their canonical family.** With
  `import "std/char";`, `'c'.upper()` is `char::upper('c')`; a
  `slice<int32>` receiver dispatches `fn slice<T>::first`, an alias-typed
  receiver its target's family.
- **A derived receiver reaches its base chain's families.** A struct that
  `extends` another dispatches the merged set of its own and its bases'
  members — see [inherited methods](#inherited-methods).
- **An rvalue receiver evaluates once.** A chained receiver
  (`p.get().upper().lower()`) is a call result: it evaluates once into a
  hidden local. A plain rvalue spills to a **const** slot, so a reference-self
  method on a temporary is an error (`mk().bump()` rejects — the mutation
  would vanish with the temporary); a [`&`-returning](#reference-returns)
  receiver re-lends its carried lvalue instead, so `b.ref().grow()` writes
  the caller's storage.
- **A `&`-returning method call is an lvalue** through the dot spelling
  too: `l.at(i) = v`, `l.at(i) += 1`, chained store targets
  (`a.view().at(2) = 7`), and the reference-return formation walk all judge the
  desugared family.
- **Explicit type arguments at a dot-call do not parse** (`p.m<int32>(...)`)
  — method type parameters are inference-only, exactly as at a `::` call.
- **The two semantic method names are excluded**: `p.constructor(args)` and
  `p.destructor()` are compile errors (`'destructor' cannot be called with
  method syntax; use point::destructor(p)`) — the qualified forms are their
  only callable spellings, kept mainly for chaining. See
  [Constructors](#constructors) and [Destructors](#destructors). A genuine
  *field* of either name keeps its field behavior, as always.

See [examples/types/method_calls.mc](../examples/types/method_calls.mc).

#### Properties

A method annotated `@property` is reachable through **field syntax**: `s.length`
calls `stack<T>::length(s)`, dropping the parentheses a dot-call would need.
The annotation says "read me like a field"; the method is otherwise ordinary,
so the call spelling `s.length()` stays valid beside the field spelling, and
overload machinery, inheritance, and pointer auto-deref all carry through.

```c
struct temperature { celsius: int32; }

@property
fn temperature::fahrenheit(const self: &temperature) -> int32 {
    return self.celsius * 9 / 5 + 32;
}

let t = temperature { celsius = 100 };
println(f"{t.fahrenheit}");   // 212 -- s.field, no parentheses
let f = t.fahrenheit();       // the call spelling still works
```

A `@property` takes **only its receiver** and **returns a value**. A `-> &`
return makes the access an assignable lvalue — `s.field = v` is exactly
`Type::field(s) = v` through the [reference return](#reference-returns), so plain and
compound assignment write straight through the accessor:

```c
struct cell { n: int32; }

@property
fn cell::value(self: &cell) -> &int32 { return self.n; }

let c = cell { n = 5 };
c.value = 40;    // cell::value(c) = 40
c.value += 2;    // 42
```

A read-only (non-`&`) property rejects assignment, exactly as a
non-reference-returning call target does.

##### Explicit get/set pairs

The `-> &` form hands out raw storage, so it cannot run logic on the write
path. For accessors that must — validation, clamping, bookkeeping —
`@property("get")` / `@property("set")` declare an explicit pair:

```c
struct gauge { raw: int32; }

@property("get")
fn gauge::level(const self: &gauge) -> int32 { return self.raw; }

@property("set")
fn gauge::level(self: &gauge, value: int32) {
    self.raw = value < 0 ? 0 : (value > 100 ? 100 : value);
}

let g = gauge { raw = 10 };
let n = g.level;    // gauge::level(g)         -- the getter
g.level = 999;      // gauge::level(g, 999)    -- the setter (clamps to 100)
g.level += 5;       // gauge::level(g, gauge::level(g) + 5) -- read-modify-write
```

- **The getter** is receiver-only and value-returning, like a bare
  `@property` — but it may **not** return `&` (the bare form is the
  reference-lvalue mechanism; the pair is the call mechanism, and they do not mix).
- **The setter** takes exactly `(self, value)`; the assigned expression
  passes as its second argument with the call's own overload dispatch,
  literal adaptation, and coercion. A setter may declare a return type, but
  assignment is a statement, so the value is **discarded**.
- **Compound assignment is read-modify-write**: `t.field op= v` is one get,
  the operator, one set — the receiver expression is evaluated for each, both
  reaching the same storage.
- **A pair may be partial**: getter-only rejects assignment (the standard
  "does not return a reference" error); setter-only is **write-only** and rejects
  reads (`property 'gauge::level' is write-only`), including `op=` (which
  needs the getter).
- **One family, one mechanism**: declaring both a bare `@property` and a
  `("get")`/`("set")` member on the same `Type::name` is a compile error.
- Both members stay ordinary overloads at the call spelling: `g.level()` is
  the getter and `g.level(v)` the setter, dispatched by arity like any
  dot-call.

The dispatch is the dot-call's:

- **A real field of the name shadows a property** (field-first, as at a
  dot-call): `s.field` reads the field, and the property is then reachable
  only through its qualified form `Type::field(s)`.
- **A `@property` inherits through `extends`** and **binds `T` on a generic
  receiver**, like any method; a pointer receiver auto-derefs one hop
  (`p.value` is `(*p).value`).
- Declaring one is checked: `@property` applies only to a **method** (a
  qualified `fn Type::name`) with a **body** — a bare `@property` or a
  `("get")` takes **only its receiver** and returns a **value**, a `("set")`
  takes exactly **its receiver and the assigned value** — otherwise a compile
  error.

See [examples/types/properties.mc](../examples/types/properties.mc).

#### Accessors: overloading `[]`

A method annotated `@accessor` is the type's **`[]` operator**: `xs[i]` calls
`list<T>::at(xs, i)`. The annotation is `@property`'s indexed sibling — the
method is otherwise ordinary (the call spellings `xs.at(i)` and
`list<int32>::at(xs, i)` stay valid beside `[]`, and generics, inheritance,
and overload dispatch carry through), and the same bare-vs-pair split
applies. **Natively indexable types keep native `[]`**: a pointer, array,
slice, or tuple base never consults an accessor.

The indices are ordinary arguments: **any number, of any type** — `m[r, c]`
passes both, `d["key"]` indexes a dict by string. Overloads within the
family dispatch over them like any call.

The bare form returns the element, and a `-> &` return makes `xs[i]` an
assignable lvalue — `xs[i] = v` is exactly `Type::at(xs, i) = v` through the
[reference return](#reference-returns):

```c
struct grid { cells: int32[16]; }

@accessor
fn grid::at(self: &grid, r: uint64, c: uint64) -> &int32 {
    return self.cells[r * 4 + c];
}

let g: grid;
g[1, 2] = 40;    // grid::at(g, 1, 2) = 40
g[1, 2] += 2;    // 42
```

For write-path logic, `@accessor("get")` / `@accessor("set")` declare the
explicit pair: `d[k]` calls the getter, `d[k] = v` the setter — the indices
first, the assigned value **last** — and `d[k] op= v` is read-modify-write
through both (the receiver and index expressions are evaluated for each,
both reaching the same storage). The pair's rules are the property pair's:
the getter never returns `&`, a setter's return is discarded, getter-only
rejects writes (`accessor 'dict::at' is read-only`), setter-only rejects
reads (`write-only`) and `op=`, and one family cannot mix the bare form with
the pair.

Two rules are `[]`'s own:

- **One family per type**: `xs[i]` carries no method name to pick by, so all
  `@accessor` methods of one type must share one name — declaring
  `@accessor` on two differently named methods of a type is a compile error.
  A derived type reaches a base's accessor through `extends`, its own
  declaration winning the family name.
- Only an accessor takes **more than one index**; multi-index on a native
  base (`arr[0, 1]`) is a compile error.

Declaring one is checked like a property: `@accessor` applies only to a
**method** with a **body**; the bare/`("get")` forms take the receiver plus
at least one index and return a value, a `("set")` takes the receiver, at
least one index, and the assigned value. The receiver must be probeable at
the use site (an rvalue base like `f()[i]` is unsupported — bind it first).

The stdlib adopts both forms: `list<T>::at` is a bare `@accessor` (so list
elements — and `string`'s bytes, by inheritance — read and write like array
slots), and `dict<V>::at` is a get/set pair (`d[key]` reads **unchecked** —
guard with `.has` — and `d[key] = v` inserts or updates through `.set`).

See [examples/types/accessors.mc](../examples/types/accessors.mc).

#### Constructors

A method named `constructor` makes its type callable: `S(args)` is sugar for

```c
let s: S;                    // allocate (and default-initialize) the slot
S::constructor(s, args);     // construct in place
// ... S(args) evaluates to s
```

The desugaring is exact — overload resolution, `&`/`const` receiver
legality, privacy, and every diagnostic are the family call's own (arities
and argument positions count the receiver, so a mismatch reports
`argument 2` for the first written argument). The slot default-initializes
exactly as a bare `let s: S;` does: a struct with declared field defaults
starts from them, anything else starts uninitialized, and the constructor
fills it in place. `let p = S(args);` binds the constructed slot directly —
no temporary, no copy — so a `reference self` constructor writes `p`'s own storage.

```c
struct point<T> { x: T; y: T; }

fn point<T>::constructor(self: &point<T>, x: T, y: T) {
    self.x = x; self.y = y;
}
fn point<T>::constructor<U>(self: &point<T>, x: U, y: U) {  // converting
    self.x = x as T; self.y = y as T;
}

fn main() -> int32 {
    let a = point<float64>(1, 1);   // the converting ctor: T = float64 is
                                    // pinned, so the diagonal is non-viable
                                    // for int literals into float64 slots
    let b = point(1.5, 2.5);        // bare head: the arguments infer T
    let c: point<float64>;          // the desugared spelling stays valid
    point::constructor(c, 1, 1);
    return 0;
}
```

The head follows type-use spelling, plus call-side inference for a bare
generic:

- **Explicit type arguments** (`point<float64>(1, 1)`) type the receiver up
  front, so it binds the struct's parameters during the family's inference —
  exactly as the desugared call's receiver does.
- **A bare generic head** (`point(1.5, 2.5)`) spells no instantiation: the
  receiver enters overload resolution as a placeholder, the arguments (and
  the winner's declared defaults) deduce the bindings, and the winner's
  first parameter fixes the constructed type. Uninferable bindings say so
  (`cannot infer type parameter(s) T for 'point::constructor'; spell the
  instantiation, e.g. point<int32>(...)`), and a rank tie is the family's
  ordinary ambiguity error. In a no-overload message the untyped receiver
  renders as `<self>`.
- **A fully-defaulted generic** written bare is a complete type (as in
  `let b: box;`), so `box(1)` constructs `box` at its defaults.
- **Type aliases are transparent**: with `type pointf = point<float64>`,
  `pointf(1, 2)` constructs `point<float64>`; a plain alias of the bare name
  keeps the inferring behavior. A *generic* alias used bare keeps the
  type-use arity error — annotate the head (`diag<int32>(1, 2)`).
- **Any type with a declared `constructor` family is constructible** —
  builtins included: declare `fn char::constructor(self: &char, code:
  int32)` and `char(65)` works. Without a declared constructor a call
  **with arguments** is an error (`type 'int32' has no constructor` — it
  does **not** become a cast), and for a struct, `struct 'point' has no
  constructor` — the [struct literal](#structs) remains the no-constructor
  spelling. The zero-argument call never errs this way — see below.

**Every type also has an implicit empty constructor**: `T()` with no
arguments is exactly `let t: T;` — the slot, default-initialized as the
bare declaration is (a struct with declared field defaults starts from
them, anything else starts uninitialized), is the value; a `let` binding
it still schedules a declared [destructor](#destructors), so
`let p = point<float64>();` is `let p: point<float64>;` plus
`defer point<float64>::destructor(p);`. It applies to any
type the sugar head accepts — `char()`, `int32()`, `point<float64>()`, a
derived `pointf()`, an alias — and, unlike C++, **declaring constructors
does not suppress it**: a 2-argument family beside `point<float64>()`
leaves the zero-argument call default-initializing. Declared members still
win — a visible family member that accepts just the receiver (a
`(reference self)`-only constructor, or a
[collecting](#native-variadic-arguments) one
whose fixed prefix is only the receiver) claims `T()` and runs normally —
so the implicit form is strictly the fallback and no ambiguity between the
two can arise. A bare generic head with required parameters has no
arguments to infer from, so `point()` stays the cannot-infer error
(fully-defaulted generics are complete types, as always). One corollary of
the dumb desugar: a literal zero-*parameter* member
(`fn s::constructor()`) can never accept the hidden receiver, so it never
claims the call — the receiver-taking form above is the empty constructor.

Name resolution is unchanged: a same-named function, variable, constant, or
file-scoped `@static` wins unconditionally (the sugar sits where the call
would otherwise be `undefined function`), so declaring `fn point(...)`
beside `struct point` keeps calling the function. `Type::` still enforces no
`self` convention — the sugar is a dumb desugar, so a `const self` or
by-value `self` "constructor" compiles and simply initializes nothing, and a
non-void constructor's return value is discarded by `S(args)`. Explicit type
arguments at the head are the struct's; a converting constructor's own
parameters (`<U>` above) are inference-only, as at any `::` call — where the
*qualifier's* list is writable
([explicit type arguments](#explicit-type-arguments-at-a-qualified-call)):
inside a generic constructor, `point<T>::constructor(self, x as T, y as T)`
chains to the direct member at the enclosing `T`. The
[dot spelling](#calling-methods-dot-syntax) is excluded:
`p.constructor(args)` is a compile error (`'constructor' cannot be called
with method syntax; use point::constructor(p, ...)`) — the qualified
`S::constructor(p, args)` is the only callable spelling, kept mainly for
**chaining** a base's constructor from a derived body
([Inherited methods](#inherited-methods)).

See [examples/types/constructors.mc](../examples/types/constructors.mc).

#### Destructors

A method named `destructor` is the other half of the pair: when a type
declares (or [inherits](#inherited-methods)) one, the **constructor-sugar
`let`** schedules the cleanup call on the enclosing block's
[defers](#defer) —

```c
let p = point<float64>();
// == let p: point<float64>;
//    point<float64>::constructor(p);      // when a member claims the call
//    defer point<float64>::destructor(p);
```

— so the value is destroyed when its scope exits, however it exits:

```c
struct handle { fd: int32; }
fn handle::constructor(self: &handle, fd: int32) { self.fd = fd; }
fn handle::destructor(self: &handle) { close(self.fd); }

fn use_it() -> int32 {
    let h = handle(acquire());
    if (bad()) {
        return -1;              // h.fd closed here...
    }
    work(h.fd);
    return 0;                   // ...and here — never forgotten
}
```

**The trigger surface is exactly the constructor-sugar `let`**:
`let t = T(args);` and `let t = T();` (a declared or
[implicit empty](#constructors) constructor alike). Everything else is a
documented opt-out spelling that schedules nothing — manual construction
(`let t: T; T::constructor(t, ...);`), a struct-literal `let t = T{...};`,
a copy `let b = a;`, and plain assignment. An annotation that coerces the
constructed value away (boxing into `any`) binds a copy rather than the
constructed slot, so it schedules nothing either.

The scheduled call **shares the defer machinery verbatim**: it runs LIFO
with explicit `defer`s (values destroy in reverse construction order), per
iteration in a loop body, and on every unwinding exit — early `return`,
`break`, `continue`, and [bare-`try` propagation](#propagation-bare-try).
As with any defer, a [`@noreturn`](#noreturn-functions) exit runs no
destructors, and the destructor sees the value's **latest state**,
mutations after construction included.

Details and sharp edges:

- **The pair is qualified-only.** `constructor` and `destructor` cannot be
  called with [dot syntax](#calling-methods-dot-syntax): `t.destructor()`
  and `t.constructor(args)` are compile errors (`'destructor' cannot be
  called with method syntax; use point::destructor(t)`). The fully
  qualified forms `T::constructor(t, args)` / `T::destructor(t)` are the
  only callable spellings, and their main intended use is **chaining** —
  a derived body invoking its base's (below). Construction is the
  [`S(args)` sugar](#constructors), destruction is automatic. A genuine
  *field* of either name keeps its field behavior (fields shadow methods
  before the ban is judged).
- **Resolution is ordinary — a dumb desugar.** The scheduled call is the
  qualified `T::destructor(t)` over the family, so overload resolution,
  privacy, arity, and every diagnostic are the family call's own, reported
  at the `let`'s line: a family whose members all need extra non-defaulted
  arguments errors at every constructor-let (`'big::destructor' expects 2
  argument(s), got 1` — positions count the receiver), and a cross-module
  `@private` destructor makes foreign constructor-lets error with the
  usual visibility diagnostic. Extra-parameter overloads stay manually
  callable; they just cannot be automatic.
- **A const view is still destroyed.** `let p: const T = T(...);` keeps
  the read-only view for user code, but destruction is scope teardown, not
  user mutation (the C++ stance): the synthesized call alone bypasses the
  const view. A user-written `T::destructor(p)` on a const `p` keeps the
  ordinary reference-receiver error.
- **Manually destroying an auto-destructed value is undefined behavior.**
  A qualified `T::destructor(p)` (or a manual `defer T::destructor(p);`)
  beside the automatic call compiles and destroys twice — like a C
  double-free: no suppression magic, no warning. Destroy manually only
  what you constructed manually.
- **Copies are bitwise and alias.** `let b = a;` copies the fields, and
  only `a` (the constructed let) is destroyed — if the type owns a
  resource, both views name it, exactly C's problem. The opt-in
  [`-Wdestructor-copy`](#opt-in-warning-classes) flags such a copy — both an
  explicit `let b = a;` and the by-value parameter copy a plain `const x: T`
  makes — so an owning value must be taken by `const &` or handed over with
  `move(...)` (which blesses the copy).
- **Returning or emitting the whole value is a hard error** — unless the
  function is declared [`-> own`](#move-out-returns-own), the move-out
  lift. `return t;` copies the value out, then the unwinding destroys the
  original — the caller would receive a bitwise copy of already-destroyed
  state — so it is rejected (`cannot return 't': its automatic destructor
  runs as the return unwinds this scope, ...`), and so is smuggling the
  same copy through a result wrap (`return ok(t);`); `emit t;` of a local
  declared inside the [block expression](#block-expressions) likewise
  (emitting a local from *outside* the block survives the emit and stays
  an ordinary, legal copy — and the emit error has no `own` lift: a block
  expression has no signature to carry the marker). The other hatches:
  return the constructor expression directly (`return T(args);` — an
  expression-position temporary owns no automatic cleanup; only the `let`
  form does), or construct manually and own the cleanup. A **field**
  escape (`return t.data;`) is *not* caught — interior ownership is yours
  to reason about.
- **Base cleanup chains manually**, mirroring constructor chaining: a
  derived destructor that wants it ends its body with
  `base::destructor(self);` — and a generic owner destroys a generic
  member's field at the enclosing instantiation with
  [explicit type arguments](#explicit-type-arguments-at-a-qualified-call)
  (`inner<T>::destructor(self.i);`). A derived type that declares **no**
  destructor of its own inherits the base's through the
  [merged family](#inherited-methods), and the automatic call resolves it
  (receiver-only upcast, as at any inherited call).
- **Scope: stack `let`s only.** Globals and `@static` values, function
  parameters, heap values, and constructor-expression temporaries
  (`f(T(args))`, `return T(args);`) are never destroyed automatically.
  The one expression-position exception is an unadopted
  [`-> own`](#move-out-returns-own) call, whose handed-over temporary
  *is* destroyed when its statement ends — see the drop rule there.

See [examples/types/destructors.mc](../examples/types/destructors.mc).

#### Move-out returns: `-> own`

A function declared `-> own T` hands its caller an **owned value**: the
signature says, visibly, that the return transfers a resource and the
caller must clean it up. Like [`-> &`](#reference-returns), `own` is a flag on
the declaration, not part of the type (the two are mutually exclusive:
`&` lends a view of existing storage, `own` hands over a value) — and it
changes no ABI; everything below is compile-time policy.

```c
fn greeting(@nonnull who: char*) -> own string {
    let s = string("hello, ");   // schedules string::destructor(s)
    s.append(who);
    return s;                    // TRANSFER: the schedule is cancelled on
}                                // this path; the caller adopts

let msg = greeting("world");     // adopts: destroys msg at scope end,
                                 // exactly like a constructor-sugar let
```

**In the body**, returning an auto-destructed local cancels the local's
scheduled destructor *on that return path only* and transfers the cleanup
obligation — the [whole-value hard error](#destructors) is lifted exactly
here. Other exits keep the schedule: a path that returns something else
still destroys the local. The **formation rule is strict**: an unmarked
return must visibly hold the obligation it hands over —

- the constructed **local** (the transfer above),
- a fresh **constructor expression** (`return string("hi");` — the
  temporary owns no scheduled cleanup, so the obligation is minted for the
  caller), or
- a **chained own call** (`return inner();` with `inner` also `-> own` —
  the obligation flows through untouched; a bare `try inner()` unwrap of
  an own call chains the same way).

Anything else is a **plain copy** — the original stays behind, still
owned — and minting a caller obligation from it is exactly the aliasing
double-free the copies-are-bitwise stance warns about. Asserting that the
source truly relinquishes the value takes the explicit marker:

```c
fn box::pop(self: &box) -> own res {
    return move(self.r);   // "I own this and I am handing it over"
}
```

`move(v)` behaves like a builtin `fn move<T>(v: T) -> T` — the value
passes through unchanged; the call *is* the assertion — and is claimed by
call shape exactly like `ok(`/`error(`, so a bare `move` stays an
ordinary identifier. It is legal in the three places a value is relinquished
into a new home: the return value of an `-> own` function (around the whole
value or on the ok payload, `return ok(move(v));`), a `let` initializer
(`let b = move(a);`), and a by-value argument (`f(move(a))`) — where in the
latter two it blesses a bitwise copy, exempting it from
[`-Wdestructor-copy`](#opt-in-warning-classes). A nested operand or any other
position has no transfer target and is rejected. A *wrong* `move()` (the field
stays reachable and owned) is the same undefined double-free as any aliasing
copy — the marker makes the risky case visible, never silent.

**At the caller**, a `let` bound to an own call **adopts**: it schedules
`T::destructor` on its scope exactly like a constructor-sugar let (a
`const`-viewed binding adopts and is still destroyed, same stance). Every
**receiverless** consumption instead **drops**: discarding the call
(`f();`), passing it as an argument (`g(f())`), chaining off it
(`f().m()`), assigning it to an existing variable, and a
`try f() ?? fallback` mix each receive a temporary that is destroyed
automatically **when the full call chain — the statement — ends**. The
value stays alive through every call that consumes it
(`println("{}".format(test()))` destroys the value `test()` handed over
only after `println` returns), the statement's computed result lands in
its own storage first, and only then do the temporaries drop, newest
first — so for `return g(f());` the shape is exactly

```
mov  $tmp, f()
mov  $out, g($tmp)
drop $tmp        ; before the function returns, on the return path
ret  $out
```

and the destructor, running on the temporary's dedicated copy, can never
clobber the result flowing onward. A temporary is destroyed only when its
call actually executed: a ternary arm or short-circuit right operand
drops inside its own arm, and a `break`/`continue`/`return` (bare-`try`
propagation included) that abandons an in-flight expression destroys the
temporaries it had already constructed on the way out. Statement
temporaries die before the scope's `defer`s run.

Two consequences are yours to reason about. **Assignment aliases**: the
temporary drops after the statement, so the assigned variable's bitwise
copy names an already-destroyed resource — and if that variable was
itself adopted by its `let`, its scope-end destructor runs on the same
resource again (the overwritten old value, meanwhile, is never
destroyed). This is the copies-are-bitwise stance doing what it says;
`-Wown-assign` on the roadmap is the diagnostic direction for this
assignment case (distinct from the shipped
[`-Wdestructor-copy`](#opt-in-warning-classes), which flags the plain
copy-aliases-a-live-resource case, `let b = a;`).
And **the `?? fallback` mix now mirrors the bare call in let position
too**: `let v = try f() ?? fallback;` adopts — whichever value fills the
slot, the unwrapped payload or the built fallback, is destroyed at scope
end.

A **mixed overload set** (own and plain members behind one name) never
certainly hands over, so it neither adopts nor drops — the call stays a
plain copy, the same conservative judgment a `let` applies. Still plain
copies (no automatic destruction, follow-up work): an own call in a
struct-literal field initializer, `emit f();`, `return f();` from a
non-own function, and field projection (`f().field`). (F-string hole
temporaries, formerly on this list, now drop at statement end like any
collected argument's — see
[string-valued f-strings](#formatted-print--println).)

**With a result return** the ownership rides the **ok payload**:

```c
fn load(path: char*) -> own result<string, io_error> {
    let s = string("...");
    if (bad) return error(io_error::NOT_FOUND);  // error path: s destroyed
    return ok(s);                                // transfer via the payload
}

let s = try load(p);                  // adopts the unwrapped payload
let t = try load(p) except (e) { return -1; };   // adopts too
```

`return error(...)` transfers nothing (that path's locals are destroyed
normally), and an error-only `result<E>` cannot be `own` (there is no ok
payload to hand over). Note the general guard this feature also closes:
in *any* function, `return ok(local)` of an auto-destructed local is now
the same hard error as the bare `return local` — the result wrap no longer
smuggles the destroyed copy out.

`own` on a **destructor-less type is a no-op** — nothing to cancel or
adopt — which keeps generic signatures writable: `fn pool<T>::take(...) ->
own T` compiles for every `T`, and does its job exactly when `T` carries a
destructor. The flag rides `.mci` interface stubs (`fn make(v: int32) ->
own res;`) and its mismatch against a prototype is rejected like a `&`
mismatch. `@extern` functions cannot be `own` (C hands over no destructor
obligation), and neither can `@property`/`@accessor` methods (reads
through field or index syntax never transfer).

**Function-pointer types carry the marker too**: `fn(int32) -> own res`
spells an own return the way `fn(...) -> &T` spells a reference one, so a
call through the value — a factory local, a field-held callback, an
inferred `let factory = make;` (the function value derives the bit from
its declaration) — vouches for adoption exactly like a direct call.
Unlike `&`, `own` changes no calling convention; it is a **contract**,
so implicit retyping is rejected in *both* directions (dropping the
marker would silently leak the handed-over obligation, fabricating it
would destroy a value the callee never handed over), and an explicit
`as` cast is the hatch when you mean it.

See [examples/types/own_returns.mc](../examples/types/own_returns.mc) for
the feature, and [examples/types/own_drops.mc](../examples/types/own_drops.mc)
for the drop rule walked form by form with print-stamped destruction.

#### Consuming receivers: `own self`

`own` is also a **by-value parameter** marker — the receiver-side mirror of
[`-> own`](#move-out-returns-own). A method whose receiver is `own self: T`
**consumes** it: the method takes ownership of the value by **move** (never a
copy) and drops it — runs its [destructor](#destructors) — at the **end of the
body**, exactly as an owning constructor-sugar `let` would at its scope exit.
It is the fourth [receiver kind](#calling-methods-explicitly), alongside
`const self: &T`, `self: &T`, and `@nonnull self: T*`. The marker precedes the
name like `const` (`own self: T`, or the read-only-in-body `own const self: T`),
and it is not receiver-only: any by-value parameter may be `own`
(`fn drain(own b: box)`).

```c
struct adder { sum: int32; }
fn adder::destructor(self: &adder) { /* release resources */ }

fn adder::plus(own self: adder, n: int32) -> own adder {   // consume, hand back
    self.sum = self.sum + n;
    return self;                                           // transfers self out
}
fn adder::total(own self: adder) -> int32 { return self.sum; }   // consume, drop

let t = adder().plus(3).plus(4).total();                   // one value, one drop
```

Because a by-value receiver can never be a vtable entry, `own self: T` is,
unlike the reference receivers, **not dispatch-eligible** — a consuming call is
a deliberate ownership transfer to a statically known type, never resolved
dynamically. It is an ordinary call in every other respect: it may be generic,
overloaded, or reached through a function value — see the non-direct call
paths described below.

**At the call site**, the argument to an `own` parameter must be a value this
frame owns to give away — the same relinquish discipline as a `-> own` return:

- A **fresh** owned value — a constructor expression (`adder()`), an `-> own`
  call, or a dot-call's spilled rvalue receiver (`adder().plus(3)`,
  `box(4).consume()`) — is **adopted** by the callee with no `move`. This is
  what makes the builder chain above work: each step's receiver is the previous
  step's owned result. Such a fresh value is *not* additionally dropped by the
  caller, so it is destroyed exactly once, inside the callee.
- A **named owned local** is relinquished with an explicit
  [`move(x)`](#move-out-returns-own) (`adder::total(move(a))`): its scheduled
  destructor is cancelled, and reading the local afterward — a whole value, a
  field, its address — is a **use-after-move** error. A bare `adder::total(a)`,
  and equally the dot spelling `a.total()`, is refused, directing to `move(a)`;
  the relinquish must be visible, since a plain call does not otherwise read as
  an ending. (Move tracking is per binding: the same name rebound in a sibling
  scope is a fresh, un-moved value.)

Anything else whose type owns a destructor (a field extracted with
`move(p.a)`, an arbitrary copy) is not a value the frame owns and is refused
rather than risk a double free. `own` over a **destructor-less type is a
no-op** — nothing to move or drop — so it needs no `move` and passes by value
like a plain parameter. The marker rides `.mci` interface stubs
(`fn drain(own b: box) -> int32;`).

The same relinquish discipline holds on **every call path**, not just the
direct call:

- **Generic functions and methods of generic structs** may take `own`
  parameters — this is what lets a container define a *consuming* method
  (`fn vec<T>::into_sum(own self: vec<T>) -> T`). The move-in runs when the
  call's winner (and with it its `own` positions) is resolved.
- **Overloaded** functions may too, provided the members **agree on which
  positions are `own`** — a set mixing a consuming member and a copying one at
  the same name has no single caller contract and is rejected.
- A function with `own` parameters **is** a first-class **function value**:
  the move-in contract rides its type, `fn(own box) -> int32`, spelled with the
  same `own` marker. It is a **distinct type** from `fn(box) -> int32` (a
  different calling convention — one transfers ownership, the other copies), so
  neither converts to the other; a call through the value enforces the move
  exactly as a direct call does.

Not in this phase: the owned-**reference** receiver `own self: &T` (an escaping
borrow, rejected for now), and `own` parameters on `@extern`/`@asm` functions.

See [examples/types/own_receivers.mc](../examples/types/own_receivers.mc) for
the direct-call receiver, and
[examples/types/own_generic.mc](../examples/types/own_generic.mc) for the
generic-container, overloaded, and function-value forms.

#### Inherited methods

A struct that [`extends`](#structs) another **exposes its base chain's
method families**: a family call on the derived type — dot sugar or the
qualified spelling — resolves over the **merged set** of the derived type's
own members and every base hop's, the latter entering **rebased at the
declared base instantiation**. Constructors merge like any other family, so
deriving makes the base's constructors callable on the derived type:

```c
struct point<T> { x: T; y: T; }
struct pointf extends point<float64> {}

fn point<T>::constructor(self: &point<T>, x: T, y: T) {
    self.x = x; self.y = y;
}
fn pointf::constructor<U>(self: &pointf, x: U, y: U) {   // converting
    self.x = x as float64; self.y = y as float64;
}
fn point<T>::magnitude(const self: &point<T>) -> float64 {
    return sqrt(pow(self.x, 2.0) + pow(self.y, 2.0));
}

fn main() -> int32 {
    let p = pointf(1.0, 1.0);       // the INHERITED diagonal, T = float64
    let q = pointf(1, 1);           // the derived <U>: int literals never
                                    // adapt to the diagonal's float64 slots
    return p.magnitude() as int32;  // the inherited fn point<T>::magnitude
}
```

Rebasing is what makes the ranking read naturally: on `pointf`, the
inherited diagonal **is** a concrete `(float64, float64)` member — so it
outranks the derived generic `<U>` for float arguments (a concrete signature
beats a template, [as always](#function-overloading)), while for int
literals it is simply not viable and the converting `<U>` wins. The rank key
gains one component, becoming **(no-collect, tier, −hop, specificity, fixed
count)** — the *hop* is an inherited member's distance up the `extends`
chain, `0` for a member declared on the receiver type itself:

- **The tier beats the hop**: an inherited exact/concrete match beats a
  derived generic — exactness beats genericity wherever it was declared. A
  derived member never *hides* a base family (no C++ name hiding): a
  different signature simply **overloads** the merged set.
- **The hop beats specificity**: a derived member shadows a same-shape
  inherited one, and a nearer base's shadows a farther one — this is an
  **override**, and the shadowing member must carry
  [`@override`](#override-a-method) (see below).

The merged set is built per hop with **no cascade** — each base contributes
exactly the members declared on it — and membership is judged against the
declared instantiation: a base-family
[specialization](#specializing-a-method-for-one-instantiation) is inherited
only where the `extends` clause names its instantiation (`fn
point<int32>::m` never appears on `pointf`), a diagonal qualifier
(`fn pair<A, A>::m`) only where the base arguments agree, and a member whose
seeded type parameter carries a [group](#closed-type-groups) or
[bound](#bounds) the instantiation violates is filtered out (a generic
derivation carries the constraint along instead). A generic derivation stays
generic: `struct pd<T> extends point<T>` inherits `point<T>`'s members with
the receiver binding `T`, bare-head constructor inference included
(`pd(1, 2)` builds a `pd<int32>`).

**The receiver upcasts — and so does any fat reference parameter.** In the
receiver position (the first argument) of any method-family call, a derived
value passes where the resolved parameter is a declared base of its lineage: a
`self: &T` or `const self: &T` (hidden-reference) receiver **lends its base
prefix in place** — the same storage viewed as the base, so a `self: &T`
method's writes land in the derived value's leading fields. Every receiver is
reference-shaped (a by-value copy receiver is rejected precisely because it
would slice the derived value), so the base prefix is always lent, never
copied. Since [polymorphic base views](#polymorphic-base-views) (SIE-101 Stage
2) the same derived→base reference upcast applies to **any** `&<extended base>`
parameter, at any position — a reference upcast is a view, never a slice — so a
free function `fn f(const a: &A)` accepts a derived argument too. This also
covers the explicit qualified spelling: `point::magnitude(p)` accepts a
`pointf`, and a derived constructor **chains** by calling the base's directly:

```c
struct point3f extends point<float64> { z: float64; }

fn point3f::constructor(self: &point3f, x: float64, y: float64, z: float64) {
    point::constructor(self, x, y);   // the receiver upcasts: constructor chaining
    self.z = z;
}
```

A **by-value** argument still keeps the explicit `as`: `b::plus(v, w)` with a
by-value derived `w` is a type error until written `w as b`, because that
conversion is a prefix **copy** and the slice is made explicit (a `&<extended
base>` reference argument, by contrast, upcasts implicitly — it is a view). An
inherited constructor never sees the derived type's added fields — they keep
their `let s: S;` semantics (declared field defaults apply, anything else
starts uninitialized) — and an inherited method's **return type stays
spelled at the base** (`fn point<T>::flipped(...) -> point<T>` returns a
`point<float64>` on `pointf`, never a `pointf`).

Under the hood there is **one instance per base instantiation, not per
derived type**: resolution runs over the rebased view, but emission always
instantiates the *origin* — `pa.sum()` and `pb.sum()` on two structs
extending `point<int32>` call the same `point::sum<int32>` symbol through a
receiver cast. Diagnostics attribute accordingly: an ambiguity's contender
note points at the origin declaration and reads `candidate is here
(inherited from point<float64>)`.

The [bare-type-parameter base](#structs) (`struct entry<T> extends T`) does
**not** participate — there is no declared base family to inherit at the
declaration; a payload's methods are reached through the explicit upcast. A
file-scoped `@static` base method stays file-scoped (never inherited), and a
cross-module `@private` base member is filtered per file, exactly as in any
[open overload set](#function-overloading).

See [examples/types/method_inheritance.mc](../examples/types/method_inheritance.mc).

##### @override a method

A derived member that shadows a same-shape inherited one — a member whose
signature **pattern** equals one of the rebased inherited candidates — is an
**override**, and it must be marked [`@override`](#function-overloading).
The pattern is the same notion `@override` already uses for open sets: a
concrete member's resolved parameter types, or a template's
[order-independent base](#template-symbols). This is the *second mode* of the
one `@override` annotation: mode 1 replaces a same-pattern member of another
module's open overload set; mode 2 (here) declares that a derived method
deliberately overrides an inherited base member.

```c
struct b { n: int32; }
struct d extends b {}

fn b::describe(const self: &b) -> int32 { return 1; }
@override fn d::describe(const self: &d) -> int32 { return 2; }   // shadows b::describe

fn main() -> int32 {
    let v: d = { n = 0 };
    return v.describe()          // 2 — the override wins by hop
         - b::describe(v);       // 1 — the base body is still reachable qualified
}
```

Two errors enforce the marker:

- An **unmarked** derived member whose pattern matches an inherited base
  member it would shadow is the accidental-shadow error: `method 'd::describe'
  shadows the inherited base member of the same signature and must be marked
  @override`.
- An **`@override`** member that shadows no inherited base member (and has no
  cross-module mode-1 target either) overrides nothing: `@override method
  'd::note' overrides no inherited base member`.

A **differently-shaped** derived member merely *overloads* the merged family
(there is no C++ name hiding), so it needs no marker. **Constructors and
destructors are exempt**: a derived `T::constructor` / `T::destructor` shadows
the base's same-shape special member by nature (base construction and cleanup
chain manually) and neither is ever dynamically dispatched, so the marker is
neither required nor rejected on one — special members sit outside the
override system. The marker defines what "an override" *is* — the criterion
[polymorphic base views](#polymorphic-base-views) key on for dynamic dispatch.

#### Polymorphic base views

A method call through a **base-typed reference** dispatches to the runtime
object's own override. There is no `class` keyword and no vtable pointer in the
object: the dispatch table lives in the **reference**, not the value, so
objects keep their exact byte layout and value semantics.

**A fat reference carries the table.** A reference `&A` / `const &A` is a
two-word **fat pointer** `{object, table}` — the object's address plus a
pointer to its dispatch table — exactly when struct `A` is **extended** (has a
declared subtype) somewhere the forming site can see. An un-extended struct's
references stay one word (a plain pointer, zero cost), so every ordinary
`const self: &T` container method is unaffected. Fatness is a property of the
**base type**, uniform across all of its references and **independent of
whether any family is overridden** — so introducing the first override into a
hierarchy never changes a reference's width. This keeps the ABI stable:
fatness is committed at `extends` time. It is scoped per **import closure**
(like an [open overload set](#function-overloading)): a normal build sees the
whole program, while a separately compiled interface stub is pinned to the
closure it was built with (below).

**The view forms at the derived→base conversion.** Passing a derived value
where a fat `&A` is expected — a method receiver, or (since Stage 2) **any**
`&A` parameter — forms the fat view: the object pointer plus that object's
table. The reference upcast is a view, never a copy, so it is implicit at any
argument position (a by-value argument still needs an explicit
[`as`](#casts) — that conversion slices):

```c
struct a { n: int32; }             fn a::greet(const self: &a) { println("a"); }
struct b extends a { m: int32; }   @override fn b::greet(const self: &b) { println("b"); }
struct c extends b { k: int32; }   @override fn c::greet(const self: &c) { println("c"); }

fn f(const it: &a) { it.greet(); }         // dispatches on it's runtime type

fn main() -> int32 {
    let v: c = { n = 1, m = 2, k = 3 };
    f(v);                                  // prints "c" — v's table reaches C::greet
    return 0;
}
```

**Dispatch happens only where a family is overridden.** A call through a fat
view routes through the table **iff** the resolved method has an
[`@override`](#override-a-method) chain (a fixed slot, assigned at the
overload's introducing base and shared down the chain). A method that is never
overridden has no slot and stays an ordinary **direct** call — no indirection,
even through a base view. Overloading is slot-precise: two overloads of one
method name take **separate** slots, keyed by the resolved overload's
signature, so a call always dispatches the exact sibling overload resolution
picked — and if only *some* overloads of a name are overridden, the others
stay direct calls. When the receiver's concrete type is statically known (a
plain `let c: c; c.greet()`) the call is **devirtualized** to the direct call;
the indirect path is taken only for a genuine view whose runtime type is
unknown (a `&A` parameter). A base method that calls another overridden family
on `self` **re-dispatches**: `self` carries the table through, so the inner
call still reaches the runtime type's override.

**An override must be ABI-compatible with the base member.** Because every
override of a family shares that family's single table slot, an
[`@override`](#override-a-method) method must match the base member where the
slot is concerned — the slot's indirect call and the stored thunk have to agree
on every value's ABI, or the call is undefined behavior. It must **return the
same type** (a divergent return — an `int32` base overridden by a `float64` one
— would reinterpret the returned bytes), and **every parameter**, the receiver
and each argument alike, must be passed the same way: by value vs. by
reference, `const` vs. writable, `own`, `@nonnull`, `@noalias`. In particular an
override may **not** widen a read-only `const &T` parameter to a writable `&T`
one (a call dispatched through a `const` view would then mutate through a
promise not to), nor change a by-value parameter to by-reference, nor add
`@nonnull` where the base accepted null, nor add `@noalias` where the base
permits aliasing (the override body would assume non-aliasing that a call
through the base signature, which permits it, need not honor). The one safe
relaxation is *narrowing* a writable base
reference to a read-only override one (same pointer ABI, and the override merely
promises to mutate less). Every violation is a compile error.

**Copying out of a view is prefix extraction.** Reading a value *out* of a fat
view yields a plain, byte-exact base value that carries **no** table:

```c
fn f(const it: &a) {
    let copy: a = it;   // prefix extraction — a plain `a`, no view
    copy.greet();       // binds to a::greet: the dynamic type is gone
}
```

Data slicing is legal and explicit; behavioral slicing is impossible, because
the table never enters the object. Copy semantics do not depend on whether the
source was a view.

**Destructors are not dispatched (yet).** The table holds no destructor slot:
base-view destruction stays static/manual, consistent with the
constructor/destructor exemption from the [`@override`](#override-a-method)
marker. Dynamic destruction is a later stage.

Across an [interface stub](#interface-files): a `.mci`'s fatness is pinned to the
closure it was emitted from, so a base extended only in a *consumer* does not
retroactively fatten a reference the stub already declared thin — the
separately compiled object keeps the ABI it was built with. A prototype and
its definition that disagree on a reference's fatness (they can only diverge
across this boundary) are rejected as a signature mismatch, never silently
miscompiled.

**Constructs a single slot cannot represent are rejected, not miscompiled.**
**A reference return carries the view.** A `-> &T` return of a fat base is
the same two-word `{object, table}` view a fat parameter is, so a returned
reference keeps its dispatch table across the hop: a function that forwards a
view parameter (`fn relay(x: &a) -> &a { return x; }`) hands back the
*runtime* type's table, `relay(obj).kind()` dispatches the derived override,
and re-lending or re-returning the result forwards the same view. Any other
returned lvalue is an object of exactly the return's static type (the
exact-type rule), so it carries that type's own table. The lvalue surfaces
are unchanged: assignment and projection through the result consume the
object pointer exactly as a thin reference return's. **Binding the result to
a local does not preserve the view**: references are not storable types, so
`let r = relay(obj);` is copy-on-read — prefix extraction into a plain base
value carrying no table, and `r.kind()` binds statically — exactly as a
`let` from a view parameter. The view survives only while the result stays
an expression: chain, re-lend, or re-return it.

Two cases are clean compile errors for now, each liftable in a later stage: a
fat reference — a `&A` parameter or a `-> &A` return — may **not** appear in a
[function-pointer type](#function-pointers), spelled or inferred from a
function value (its width can differ across closures); and a **method-owned
generic override** (one declaring *its own* type parameter, as opposed to
merely the struct's) may **not** be dynamically dispatched through a base view
— no single slot can stand in for every instantiation of that parameter —
though it remains a legal *static* override when called on a concrete
receiver. A **struct-generic** override — one whose only type parameters are
its struct's (`gb<T>::m` over `ga<T>::m`) — dispatches normally: each concrete
struct instantiation has its own table with concrete slot types.

See [examples/types/polymorphic_views.mc](../examples/types/polymorphic_views.mc)
and [method_inheritance.mc](../examples/types/method_inheritance.mc).

### @noreturn functions

`@noreturn` marks a function that never returns to its caller — it exits,
aborts, or loops forever. The compiler then treats every direct call as
**diverging**: the rest of the block is dead, so no dummy `return` is needed
after the call (code past it is silently dropped, exactly like code after a
`return`), an all-arms-diverge `if`/`case` counts as diverging through it,
and a diverging null guard narrows (below):

```c
import "std/io";                        // exit, abort, and _Exit are @noreturn

@noreturn fn fail(code: int32) {
    exit(code);
}

fn parse(input: char*) -> int32 {
    if (input == null) fail(2);      // diverges: no return needed after it
    return input[0] as int32;        // and `input` is proven non-null here
}
```

The rules:

- **Void-only.** A `@noreturn` function cannot declare a return type
  (`@noreturn function 'f' must return void, not int32`): a call can then
  never sit in expression position, which is what makes terminating the
  caller's block mid-statement safe. `main` cannot be `@noreturn` — its
  caller is the C runtime, which expects the return.
- **No `return` in the body** — `cannot return from @noreturn function 'f'`.
  Falling off the end of the body is *not* an error (C11 `_Noreturn`
  semantics): the promise is the author's, and the compiler plants an
  [`unreachable`](#the-unreachable-statement) there, so actually reaching
  the end is **undefined behavior**. The canonical spin form
  `@noreturn fn spin() { while (true) {} }` never even gets that far:
  [constant-condition folding](#control-flow) makes the loop itself
  diverge, so nothing is planted and no trailing anything is needed — the
  planted `unreachable` covers non-loop fall-offs.
- **[Defers](#defer) do not run at a `@noreturn` call.** A call that never
  returns is not a block exit, so enclosing `defer`s are skipped on that
  path — matching C, where `exit()` does not unwind the stack.
- **Declarations of every kind.** The annotation works on definitions,
  [`@extern` declarations](#extern-declarations) (libc's `exit`, `abort`,
  and `_Exit` ship annotated), [`@asm` functions](#inline-assembly), generic
  functions (each instance is checked void; the flag rides the template),
  and [bodyless prototypes](#bodyless-fn-prototypes) —
  [interface stubs](#interface-files) re-emit the `@noreturn` prefix, and
  the prototype/definition pair check rejects a mismatch, as does an
  `@extern` redeclaration that drops the flag.
- **[Function values](#function-pointers) lose the flag.** `&f` of a
  `@noreturn` function is allowed, but the plain `fn()` type cannot carry
  the contract, so a call through the pointer is assumed to return (this
  keeps `abort` usable as an `atexit` handler; the loss is only the
  divergence convenience, never a soundness hole).

The fact is handed to LLVM as the `noreturn` function attribute, so the
optimizer drops the dead continuation paths. The stdlib packages the
report-and-abort guard as [`panic`/`assert`](#panic-and-assert), so most
programs never write their own `@noreturn` helper.

See [examples/functions/noreturn.mc](../examples/functions/noreturn.mc).

## Variadic functions

A trailing `...` after at least one named parameter makes a function
variadic. These are **C's variadic arguments** — the same ABI-compatible
mechanism C uses — so the form works both in
[`@extern` declarations](#extern-declarations) (C's `printf`) and in
functions you define. A defined variadic function can
**forward** its extra arguments to a C `v*` function (`vsnprintf`,
`vfprintf`, …) through a `va_list`:

```c
import "libc/stdio";   // @extern fn vsnprintf(..., args: va_list) -> int32;

fn logf(fmt: uint8*, ...) -> int32 {
    let buf: uint8[256];
    let ap: va_list;
    va_start(ap, fmt);                       // ap, then the last named param
    let n = vsnprintf(&buf[0], 256, fmt, ap);
    va_end(ap);
    puts(&buf[0]);
    return n;
}

logf("%s = %d (0x%X)", "answer", 42, 255);   // answer = 42 (0xFF)
```

`va_list` is the C argument-cursor type; `va_start(ap, last)` initializes it
(naming the parameter just before the `...`), and `va_end(ap)` releases it.
It is **opaque**: you can pass it to a C `v*` function but not read arguments
from it in mcc (there is no `va_arg`). The target's ABI layout is chosen
automatically (x86-64, arm64/aarch64, and Apple arm64). For variadics you
can actually *read* in mcc, see
[native variadic arguments](#native-variadic-arguments) below.

## Native variadic arguments

mcc's own variadic model is typed and readable, built on
[the `any` type](#the-any-type) and [slices](#slices): a trailing
`slice<const any>` parameter marks a **collecting function**, and
`fn f(args...)` is pure sugar for `fn f(const args: slice<const any>)`. A
call's extra arguments — everything past the fixed parameters — are each
boxed into a caller-stack `any` and passed as a read-only slice over them,
allocation-free. The callee walks the slice with `for` and recovers each
value with a [`case type`](#the-any-type) type-switch:

```c
fn join(sep: char, args...) -> int32 {
    let n: int32 = 0;
    for a in args {
        case type (a) {
            when int32 v:   n = n + v;
            when char* s:   n = n + 1;
            else:           n = n - 1;   // the any universe is open
        }
    }
    return n;
}

join(',', 1, 2, "three");   // args is a 3-element slice<const any>
join(',');                  // zero extras: an empty slice, length 0
```

The rules, all type-shaped:

- **The type is the marker.** Any function whose *last* parameter is
  `slice<const any>` collects, whether written with the sugar or spelled
  out; a `slice<const any>` in any other position is an ordinary parameter.
  The `.mci` [interface](#interface-files) renderer emits the desugared
  parameter, so the marker survives re-import with no extra machinery.
- **Pass-through.** When the argument count equals the parameter count and
  the final argument is already exactly a `slice<const any>` (or a
  `slice<any>`, which widens), it passes through uncollected — the callee
  sees the original elements, never a re-boxed slice. Anything else at that
  position collects: a single `any` becomes a one-element slice, a
  `slice<int32>` boxes as one element.
- **Boxing is the standard `any` boxing.** The boxable set and its
  escape hatches apply unchanged: a struct or array extra is a compile
  error naming the pointer escape (`&s`, `&xs[0]`). Boxes are entry
  allocas with function lifetime, so calls inside loops and `defer`
  bodies are safe; as with every slice borrow, the callee must not retain
  the slice past the call.
- **Overloads and generics collect too.** A collecting function may be
  [overloaded](#function-overloading) or share a [generic](#generics)
  name — `fn log(args...)` beside `fn log(level: int32, args...)`, or
  `fn acc<T>(seed: T, args...)`, whose `T` binds from the fixed
  arguments only (the extras are type-erased). A collecting candidate is
  viable from its fixed count up, and the ranking is simple: a candidate
  that matches **without collecting beats any that must collect**, as
  the outermost rank component regardless of tier — an exact-arity
  generic beats a concrete collecting fallback (the C++
  ellipsis-ranks-worst analogue) — and a pass-through-shaped match
  (exact arity, final argument already `slice<const any>`) counts as
  not-collecting at full specificity. Between collecting candidates,
  more fixed parameters wins; equal fixed counts with a tying
  fixed-prefix specificity is the usual ambiguity error. No boxing
  happens before the winner is known — collection is emitted from the
  already-evaluated arguments — so overload resolution never changes
  what, or in what order, a call evaluates.
- **Restrictions.** Function-pointer
  types carry no marker, so a call through a `fn(...)` value passes the
  slice explicitly. A collecting function cannot also take C varargs
  (`...`), cannot be `@extern` (C sees no `slice<const any>`), and `main`
  cannot collect; C-style `...` variadics also stay banned from overload
  sets. A `&` trailing `slice<const any>` never collects —
  `&` lends the caller's own storage — so such a function stays
  explicit-slice.

See [examples/functions/native_variadics.mc](../examples/functions/native_variadics.mc).

## Generics

Functions can take type parameters, declared in `<...>` after the name and
usable anywhere a type is expected:

```c
fn sum<T>(a: T, b: T) -> T {
    return a + b;
}

fn main() -> int32 {
    let a: uint8 = sum<uint8>(1, 2);   // explicit instantiation
    let x: int64 = 9000000000;
    let y: int64 = sum(x, 1);          // T inferred from the arguments
    return 0;
}
```

Generics compile by monomorphization: each distinct set of type arguments
stamps out its own specialized function (`sum<uint8>`, `sum<int64>`, ...),
generated on first use and reused after that — there is no boxing or runtime
dispatch. When type arguments are omitted, they are inferred from the
argument types (variables take priority over literals), and typed arguments
that disagree are an error: `conflicting types for type parameter T`.
Generic functions can call themselves recursively. See
[examples/types/generics.mc](../examples/types/generics.mc).

Generic functions with the same name form an _overload set_, dispatched by
parameter pattern — a call picks the most specific viable variant (`T*`
beats `T`, `box<T>*` beats both). This is how libraries specialize by type
shape: [lib/std/hash.mc](../lib/std/hash.mc) hashes integer keys by value (splitmix64)
and pointer keys by content (FNV-1a), and [lib/std/set.mc](../lib/std/set.mc) simply
calls `hash(key)`:

```c
fn hash<T>(key: T) -> uint64 { return splitmix64(key); }
fn hash<T>(key: T*) -> uint64 { return fnv1a(key); }
```

Imported files can extend an overload set with new variants — new
*patterns*, that is: a same-pattern template (an alpha-renamed copy or a
return-type-only variant of an existing member) is a duplicate definition
wherever it is declared, same module or another (see
[Template symbols](#template-symbols) below). Two equally specific viable
variants go to the
[subsumption tie-break](#rank-tied-templates-subsumption) — the strictly
more specialized pattern wins (`f(x: T, y: T)` beats `f(x: T, y: U)` for
agreeing arguments) — and a pair it cannot order makes the call ambiguous,
a compile error.

### Closed type groups

A type parameter may declare a **closed type group** — a pipe-separated
list of types after its name — the only types it may instantiate to:

```c
fn show<T: int32 | int16 | int8>(x: T) -> int32 { ... }     // signed
fn show<T: uint32 | uint16 | uint8>(x: T) -> int32 { ... }  // unsigned
```

Deduction is unchanged; the group is a **post-deduction viability filter**.
A call whose deduced `T` falls outside the group is a compile error at the
call site naming both — `int8 is not in the type group of 'f'
(int64 | int32)` — and an explicit type argument (`f<char>(...)`) is checked
the same way. Members are **concrete types only**: no `T*`-style patterns
and no referencing other type parameters, each member resolvable where the
template is declared and listed once (membership compares *resolved* types,
so an alias respelling a member is a duplicate, and a call deducing the
alias matches its member). The pipe over a comma is deliberate: a comma list
is ambiguous against multiple parameters and defaults, while the pipe
composes cleanly — `<T: int64 | int32 = int32, U>`, where a grouped
parameter's [default](#type-parameter-defaults) must name a group member
(checked at declaration; the priority order is otherwise unchanged, so the
default still anchors an untyped literal to a member).

Checking is **eager**: at the end of codegen, every listed member of every
grouped template is instantiated and fully type-checked whether or not it
was ever called, so a member the body does not compile for errors at the
*declaration* — the [instantiation backtrace](#instantiation-backtraces)
note names the member (`in instantiation of g<int64>`) at the template's
line. This matches the multi-type [`case type` arm](#the-any-type)
precedent and the general stance that an undefined use is a compile error.
Never-called member instances are ordinary emitted functions — dead code
the linker strips in object mode, harmless under the JIT, and groups are
small by nature. One enumeration limit: a grouped template whose *other*
parameters have neither a group nor a default cannot be enumerated (that
parameter has no closed set of types) and is checked at its call sites
only, like an ordinary generic.

The payoff is **overload partitioning**: same-pattern templates with
**disjoint** groups form a resolvable overload set — deduction plus the
group filter picks one — deliberately relaxing the same-pattern
declare-time collision above. The `show` pair is the motivating shape: a
signed/unsigned formatter split at the function level, no `case type`
needed. Same-pattern templates whose groups **overlap** (sharing any
member — a pair whose groups constrain *different* parameters overlaps
too, since each leaves the other's parameter unconstrained) still collide
at declaration, cross-module like the duplicate rule:
`function 'h<$0: int64|char>($0)' overlaps 'h<$0: int32|int64>($0)';
same-pattern overloads need disjoint type groups`. An unbounded
same-pattern template may coexist with bounded ones: overload ranking
gains a middle tier — **concrete beats bounded generic beats unbounded
generic** (see [Function overloading](#function-overloading)) — so it
ranks below the groups and catches whatever they exclude. A bounded
candidate whose group excludes the deduced type is simply not viable.

Consequently the group is part of the template's
[symbol base](#template-symbols) and collision key
(`show<$0: int32|int16|int8>($0)`): two disjoint-group same-pattern
templates are distinct symbols. `.mci` interfaces carry the group (a
generic template travels as source), so a re-imported group enforces,
partitions, and collides exactly like the original. Closed type groups are
the function-declaration counterpart of multi-type
[`case type` arms](#the-any-type) — the same bounded genericity without
interfaces, the check set written in source with no action at a distance —
and `typename(T)` composes as usual. See
[examples/types/type_groups.mc](../examples/types/type_groups.mc).

### Bounds

A type parameter may instead declare a **nominal bound** — a struct after
`extends` — constraining it to that struct and the structs in its declared
`extends` lineage:

```c
struct shape  { area: int32; }
struct circle extends shape { r: int32; }

fn describe<T extends shape>(x: T*) -> int32 { return x->area; }  // shape, circle, …
```

Like a [closed type group](#closed-type-groups), the bound is a
**post-deduction viability filter**: deduction is unchanged, and a call whose
deduced `T` is neither the bound struct nor an `extends` descendant of it is a
compile error at the call site naming both — `blob does not satisfy the bound
shape of 'describe'` — and an explicit type argument (`describe<blob>(...)`) is
checked the same way. The relation is the single
[nominal struct subtype](#structs-arrays-and-data-layout) model the upcast and
slice-borrow also use, so a struct that merely shares `shape`'s field prefix
but does not declare `extends shape` is **rejected** — the asymmetry the
nominal model exists to remove. A non-struct deduced type (say `int32`) fails
the same way. A concrete bound target must be a struct: an unknown or
non-struct target errors at the *declaration* (`int32 is not a struct; cannot
extend it`). It may be a fully-applied generic or [alias](#type-aliases)
instance (`extends pair<int32, char>`, `extends ipair<char>`), which resolves
to the underlying struct.

**A bound target may reference type parameters** — the enclosing method
qualifier's or the parameter list's own — forming a **dependent bound** that
is collected at the declaration and resolved *per call*, once deduction has
bound the parameters it names:

```c
// The stdlib shape that motivates it: accept anything that extends slice<T>,
// with T the container's own element type -- no `as` at the call site.
fn list<T>::equals<U extends slice<T>>(const self: &list<T>, const lst: U) -> bool {
    return self.equals(lst as slice<const T>);
}

let a = list<int32>(2);
let b = list<int32>(a as slice<int32>);
a.equals(b);      // T = int32 makes the bound slice<int32>; U = list<int32> satisfies it

fn f<S, T extends S>(a: S, b: T) -> int32 { ... }   // the same-list form
```

The check is unchanged, just deferred: under the deduced bindings the target
resolves (`slice<T>` at `T = char` is `slice<char>`) and the binding must
satisfy the *resolved* bound, which the rejection names — `box<char> does not
satisfy the bound slice<int32> of 'box::eq'`. While a referenced parameter is
still unbound, the bound passes (the usual lenient-trial rule), and deduction
itself is still unchanged — a parameter mentioned *only* in a bound is not
inferred from it. A dependent target that resolves to a non-struct
(`U extends T` with `T = int32`) is unsatisfiable and rejects whatever was
deduced. Inherited methods carry their dependent bounds through `extends`
with the base's parameters seeded, so a derived container's calls enforce the
same resolved bound.

**A slice bound is const-covariant, one way.** A bound of `slice<const E>`
is satisfied not only by its own lineage but by any type whose lineage
reaches `slice<E>` — adding element `const` is always safe, the same
one-way widening slice values already coerce by — so a single

```c
fn show<T extends slice<const char>>(const str: T) { ... }
```

takes the whole read-only-or-better char-run family: `slice<const char>`,
`slice<char>`, `list<char>`/`string` (and with them a rendered f-string or
`.format(...)` result). This is the signature behind `std/io`'s
`print`/`println` and `panic`/`assert` message parameters. Strictly one
way: a `slice<E>` bound still rejects `slice<const E>` (satisfying it
through the mutable spelling would launder the `const` away). And a
**string literal binds a bounded bare-`T` char-slice slot directly** — the
declared bound itself becomes the binding (`show("hi")` instantiates
`T = slice<const char>` and borrows the literal), exactly as a concrete
slice parameter takes a literal; a ternary of literals rides along.

The essential difference from a closed group is that the satisfying set is
**open-ended** — any struct, anywhere, may later `extends` the bound — so
there is no eager enumeration: the bound is checked **lazily** against each
deduced binding, at every call and instantiation site. A bound composes with a
[default](#type-parameter-defaults) (`<T extends shape = circle>`), which must
itself satisfy the bound (checked at the declaration for a concrete bound,
mirroring the closed-group member-default check; a dependent bound checks the
filled default where it resolves, like any binding). A parameter may not carry
both a bound and a group.

Bounds slot into the same **overload ranking** middle tier — **concrete beats
bounded generic beats unbounded generic** (see
[Function overloading](#function-overloading)) — so a bounded template may
coexist with an unbounded fallback that ranks a tier below and catches whatever
the bound excludes. But because an open set cannot be shown disjoint the way two
closed groups can, **two same-pattern bounded overloads collide at the
declaration** (`function 'kind<$0 extends other>($0)' overlaps 'kind<$0 extends
shape>($0)'; two same-pattern bounded overloads are not yet supported`) — one
bounded overload beside an unbounded fallback is the v1 shape; disjoint-bound
overloads are a deferred follow-up.

Consequently the bound is part of the template's
[symbol base](#template-symbols) and collision key
(`describe<$0 extends shape>($0*)`). `.mci` interfaces carry the bound (a
generic template travels as source) and pull its target struct into the stub,
so a re-imported bounded template enforces identically. Bounds are the
open-set, function-declaration sibling of
[closed type groups](#closed-type-groups) — the same bounded genericity, over a
nominal lineage rather than a fixed list — and `typename(T)` composes as usual.
See [examples/types/bounds.mc](../examples/types/bounds.mc).

### Template symbols

Every generic template links its instances by a signature-derived symbol
base spelled from the declaration alone: the name, the type parameters
alpha-renamed to positional `$i` placeholders in declaration order (a
defaulted parameter spells `$i = <default>`, a
[closed type group](#closed-type-groups) spells `$i: member|member` and a
nominal [`extends` bound](#bounds) spells `$i extends struct`, both before
the default), and the parameter patterns — `alloc<$0>(uint64)`,
`hash<$0>($0*)`, `parse<$0 = int64>(uint8*)`,
`show<$0: int32|int16|int8>($0)`, `describe<$0 extends shape>($0*)`. An
instance appends its bindings: `hash<$0>($0*)<char>`. Because the base
depends on nothing but the declaration — not on how many templates share
the name or the order imports merged them — separately compiled objects
always emit a given instance under the same `linkonce_odr` symbol and the
linker merges the copies correctly.

A `&` parameter keeps its marker in the pattern (`bump<$0>(&$0)`): a
same-shape `&`/by-value template pair is a genuine overload — an rvalue
argument filters out the `&` candidate. `const` markers and the return
type never distinguish template overloads, so two templates of one name
spelling the same base — alpha-renamed copies (`f<T>(x: T)` beside
`f<U>(x: U)`) and return-type-only variants, every call to which would be
ambiguous — are rejected at declaration:
`function 'f<$0>($0)' already defined; overloads must differ in parameter
patterns`. Diagnostics keep the source-level spelling: an
[instantiation backtrace](#instantiation-backtraces) note reads
`in instantiation of hash<char>`, never the mangled symbol.

### Type-parameter defaults

A type parameter may declare a **default** — a fallback type used when a type
argument is neither supplied nor inferred from a *typed* value. Both functions
and structs take them:

```c
fn parse<T = int64>(s: uint8*) -> T { ... }
struct range<T = int64> { start: T; stop: T; }

let n = parse("42");        // T = int64, from the default
let r: range;               // range<int64> — a bare generic name works
let s: range<int16>;        // explicit argument still wins
```

The sources that can fix a parameter apply in a strict priority order:

1. an **explicit type argument** (`parse<int16>("42")`),
2. **inference from a typed value** (`sum(x, 1)` with `x: int64`),
3. the **declared default**,
4. **untyped-constant anchoring** (a bare literal's `int32` leaning), last.

The default outranking untyped constants is the point: the fallback is
declared at the definition, not guessed from a literal at the use site. It
also means **adding a default retypes existing calls** — with
`fn f<T>(x: T)`, the call `f(0)` anchors `T = int32`; declare
`fn f<T = int64>(x: T)` and that same call becomes `f<int64>` with the
literal adapting to it. Audit untyped-literal call sites when adding a
default to an existing function. Adding a default can also make a
previously-nonviable overload viable; if two variants then tie, the call
reports the usual ambiguity error rather than silently picking one.

Defaults are **trailing-only** — every parameter after a defaulted one must
also have a default — and a default may reference only parameters declared
*before* it (`<T, U = T*>` is fine; `<T = T>` and `<T = U, U = int32>` are
parse errors). An explicit type-argument list may then omit a fully-defaulted
tail: with `fn g<T, U = int8>(x: T)`, both `g<int32, int64>(1)` and
`g<int32>(1)` are legal, and the omitted tail fills from the defaults alone,
never from inference. Omitting a parameter with no default keeps the plain
arity error. A defaulted instantiation and its explicit spelling are the same
instance — `parse("42")` and `parse<int64>("42")` share one monomorphized
function.

For a generic struct, the default also makes the bare name a complete written
type: `let r: range;`, `sizeof(range)`, and `extends range` all mean
`range<int64>` above. A [struct literal](#structs) with no typed field for a
defaulted parameter uses the default the same way — see the struct-literal
inference rules.

### Instantiation backtraces

Monomorphization means a type error can surface deep inside a template's body,
in a file you never wrote. When that happens, the error is followed by one
`note` line per instantiation frame, innermost first, tracing how the compiler
got there — each note names the instance being stamped out and the file and
line that requested it. Hashing a by-value struct, for example, fails inside
the standard library's `splitmix64<T>`, and the chain walks back out to your
call:

```
lib/std/hashing/splitmix64.mc: error: line 10: cannot cast box to uint64
lib/std/hash.mc: note: line 12: in instantiation of splitmix64<box>
yourcode.mc: note: line 5: in instantiation of hash<box>
```

Generic functions, generic structs, and [type aliases](#type-aliases) each
contribute a frame, and they interleave freely — an error reached through
`string` (an alias for `list<char>`) shows a `list<char>` frame at the alias
declaration, then a `string` frame at the use site. A
[generic alias](#generic-aliases) renders its arguments in the frame
(`in instantiation of entry<int32>`). Instantiations are
memoized, so a cached instance reports the first path that triggered it (the
same convention as C++ and Rust), and an error outside any instantiation
prints exactly as before, with no notes.

## Variables

`let` declares a variable, inferring the type from the initializer. A bare
integer constant has no definite type, so it needs an annotation or a cast —
declarations are never ambiguous. Assignment uses plain `=`.

```c
let x: int64 = 0;       // annotated
let y = 0 as int64;     // or typed by a cast
let z = 0;              // error: type of 'z' is ambiguous

let pi = 3.14;          // fine: float64 (the only float type)
let ok = true;          // fine: bool
let w = x + 1;          // fine: int64, from x
x = x + 1;
```

**Compound assignment** shortens `target = target op value` to `target op= value`,
for every arithmetic, bitwise, and shift operator:
`+=` `-=` `*=` `/=` `%=` `&=` `|=` `^=` `<<=` `>>=`. The target may be any
assignable lvalue — a variable, a pointer dereference `*p`, an array element
`a[i]`, or a struct field `s.f` / `p->f` — and the same read-only rules apply
(a `const` value, a `const` parameter, or a `slice<const T>` element is
rejected). The right-hand side is a full expression, and the result keeps the
target's type exactly as the equivalent `=` would, so `x += y` where widening
`y` would change the type still needs a cast, just like `x = x + y`.

The target is evaluated **once**, so any side effects in a complex lvalue
happen a single time — the reason to prefer `arr[next()] += 1` over spelling
out `arr[next()] = arr[next()] + 1`, which would advance `next()` twice:

```c
let x: int32 = 10;
x += 5;                 // 15
x <<= 1;                // 30
x &= 0xF;               // 14

*p -= 1;                // through a pointer
counts[digit] += 1;     // an element; digit is evaluated once
node->total *= 2;       // a field through a pointer
```

A declaration may omit the initializer if it has a type annotation. Like a
C local, the variable holds garbage until assigned — reading it first is
undefined:

```c
let n: int32;           // declared, not yet initialized
if (fancy()) { n = 1; } else { n = 2; }

let p: struct point;    // works for structs too: fill in the fields
p.x = 4;
p.y = 2;
```

A `let` also **destructures** a [tuple](#tuples) or [slice](#slices):
comma-separated binders take the source's positions in order, and a
trailing-`...` **rest binder** takes the tail — pure sugar over indexing and
slicing, `let a, rest... = t;` meaning `a = t[0]; rest = t[1:];`. Each binder
is an ordinary local typed by its position, so annotations are not accepted
(the source supplies the types), and the source is evaluated once:

```c
let q, r = divmod(9, 4);       // multiple return values, bound by name
let first, rest... = nums;     // slice: first element, plus the tail view
```

See [Tuples](#tuples) for the arity rules and [sub-slicing](#sub-slicing)
for the slice form, whose bounds are as unchecked as `s[i]`.

Variables are block-scoped, as in C: a `let` is visible only until the end of
its enclosing `{ }` — including the body of an `if`/`else` branch or a
`while`/`until` loop — so sibling and sequential blocks can reuse a name. An
inner block may **shadow** a variable from an outer one; the outer binding
returns when the block ends. Redeclaring a name in the _same_ block is an
error.

```c
let x: int32 = 1;
if (cond) {
    let x: int32 = 2;   // shadows the outer x, only within this block
    use(x);             // 2
}
use(x);                 // 1 again

while (i < n) {
    let row = grid[i];  // fresh each iteration; not visible after the loop
    i = i + 1;
}
```

A bare `{ }` is a statement too, so you can open a scope anywhere — handy for a
short-lived local (and its `defer`) without leaking it into the rest of the
function:

```c
{
    let tmp = alloc<uint8>(64);
    defer dealloc(tmp);
    fill(tmp);
}   // tmp is freed and out of scope here
```

## Constants

`const` declares a named compile-time constant — mcc's answer to C's
`#define NAME value`, but typed and scoped rather than textual. It has **no
storage**: each use is folded in at compile time.

```c
const DEBUG = 1;             // untyped int: adapts like a literal
const MAX_USERS: uint64 = 1024;
const GREETING = "hello";

let buf: int32[MAX_USERS];   // an integer const can size an array
```

The initializer must be a constant expression — literals, other constants,
`sizeof`, casts, and integer/float arithmetic — evaluated when the program
is compiled:

```c
const WIDTH  = 80;
const HEIGHT = 24;
const CELLS  = WIDTH * HEIGHT;       // 1920, folded
const ROW_BYTES = WIDTH * sizeof(int32);
```

An untyped integer const stays _adaptable_ like a literal, so it takes on
whatever integer type the context needs (`uint64`, `int32`, …) without a
cast. Add an annotation (`const N: uint8 = 4;`) to pin the type. Constants
follow the same [visibility](#visibility) rules as other declarations:
file-scoped names are shared across the program, and `@private` keeps one to
its file. Assigning to a const, or using a non-constant initializer, is a
compile error.

## Target facts

The compiler predefines two integer constants describing the target it is
building for, derived from the [target triple](../README.md#usage) (the host triple when
no `--target` is given):

| Constant      | Values                                                         |
| ------------- | -------------------------------------------------------------- |
| `TARGET_OS`   | `OS_DARWIN`, `OS_LINUX`, `OS_WINDOWS`, `OS_NONE`, `OS_UNKNOWN` |
| `TARGET_ARCH` | `ARCH_X86_64`, `ARCH_AARCH64`, `ARCH_RISCV64`, `ARCH_UNKNOWN`  |

The `OS_*`/`ARCH_*` names are constants too, so code can branch on them to pick
platform-specific bindings — for instance, the linker symbol behind a libc
stream (see [`@symbol`](#extern-declarations)):

```c
@extern @symbol("__stdoutp") let macos_stdout: struct FILE*;   // when TARGET_OS == OS_DARWIN
@extern @symbol("stdout")    let linux_stdout: struct FILE*;   // when TARGET_OS == OS_LINUX
```

`OS_NONE` is a freestanding target with no operating system — a bare-metal
triple like `aarch64-unknown-none-elf` reports `OS_NONE` and `ARCH_AARCH64`.
Such code uses no libc (so none of the stream symbols above), but `TARGET_ARCH`
still lets a kernel pick architecture-specific code like MMIO addresses and
register layouts. These names are reserved: a `const` may read them, not
redefine them.

## Conditional compilation

`@if` selects code at compile time, the way C's `#if` does — but it is
_structured_, not textual: each branch is a real brace-delimited block of the
surrounding grammar, not an arbitrary span of tokens. Only the live branch is
compiled; the dead branch is parsed (so it must be syntactically valid) but
never type-checked or emitted.

The condition is a constant expression over the [target facts](#target-facts) —
`TARGET_OS`, `TARGET_ARCH`, and the `OS_*`/`ARCH_*` constants — plus any names
defined on the command line with `-D` (see below), with comparisons,
`and`/`or`/`!`, and integer arithmetic. A nonzero result is true.

Pass `-DNAME` on the command line to define `NAME` as `1`, or `-DNAME=VALUE` to
give it an integer value; the name is then usable in `@if` conditions. As in
C's `#if`, a name with no `-D` reads as `0`, so a feature flag takes its `@else`
branch when left undefined:

```c
@if (DEBUG) {            // mcc app.mc -DDEBUG    -> compiled in
    log("tracing on");
}

@if (LOG_LEVEL >= 2) {   // mcc app.mc -DLOG_LEVEL=3
    log("verbose");
}
```

These `-D` names live only in `@if` conditions — unlike the target facts, they
are not ordinary constants, so they cannot be read from running code.

It works at the top level, to select whole declarations — the intended use is
binding a symbol that differs by platform (see [`@symbol`](#extern-declarations)):

```c
struct FILE {}

@if (TARGET_OS == OS_DARWIN) {
    @extern @symbol("__stdoutp") let stdout: struct FILE*;
} @else @if (TARGET_OS == OS_LINUX) {
    @extern @symbol("stdout")    let stdout: struct FILE*;
} @else {
    @extern let stdout: struct FILE*;
}
```

and as a statement, to select code inside a function. As a statement it does
_not_ open a scope — the chosen statements are spliced in inline, so a binding
they declare is visible afterwards:

```c
fn page_size() -> uint64 {
    @if (TARGET_ARCH == ARCH_AARCH64) {
        let size = 16384 as uint64;     // Apple silicon
    } @else {
        let size = 4096 as uint64;
    }
    return size;
}
```

`@else @if` chains, and blocks may nest. Note `@if`/`@else` are compile-time and
distinct from the runtime `if`/`else`.

A branch may also contain `import` statements, to pull in a dependency only for
the targets that need it:

```c
@if (TARGET_OS == OS_DARWIN) {
    import "platform/darwin";
} @else {
    import "platform/linux";
}
```

Only the live branch's imports are resolved, so a file named in a dead branch
need not even exist — handy for shipping per-target bindings. A branch may mix
imports with ordinary declarations. (A plain, unconditional `import` still has to
precede all declarations, as before; only a conditional one lives inside an
`@if`.) Like every `@if`, the condition sees only the target facts and `-D`
defines — not user `const`s — since imports are resolved before constants are
folded.

## Error directives

Three directives report a build's problems at compile time, before it ever
links: `@static_assert(cond, "message")` fails when a condition is false,
`@error("message")` fails unconditionally at its position, and
`@warning("message")` is `@error`'s non-fatal twin, reporting without
aborting. All live at the top level, alongside declarations.

`@static_assert` checks an invariant at compile time: a struct's layout, a
type's size or alignment, an enum value the code depends on. Its condition is
folded the way a [`const`](#constants) initializer is, so it may use
`sizeof`/`alignof`/`offsetof`, other `const`s, and `Enum::Member` values, all of
which need the type system:

```c
struct Header {
    magic: uint32;
    length: uint32;
}

// Guard the on-wire layout: catch an accidental field or padding change here,
// not as a corrupt packet at runtime.
@static_assert(sizeof(struct Header) == 8, "Header must stay 8 bytes");
@static_assert(offsetof(struct Header, length) == 4, "length must follow magic");
```

The condition must fold to an integer or `bool` constant. Any nonzero integer or
`true` passes silently; a zero or `false` fails the compile with the message:

```
example.mc: error: line 9: static assertion failed: Header must stay 8 bytes
```

A condition that folds to some other constant (a float, a string, `null`) is
rejected as ill-typed (`condition must fold to a bool or integer constant`), and
one that does not fold at all (it names a runtime variable, say) fails with the
usual "is not a constant" diagnostic.

`@error` is the unconditional twin: reaching it always fails the compile. On its
own it would just break every build, so it is meant to be guarded by an
[`@if`](#conditional-compilation), so the dead branch is dropped and the error
only fires when its branch is live. This is how a program rejects a target it
does not support:

```c
@if (TARGET_OS == OS_DARWIN) {
    import "platform/darwin";
} @else @if (TARGET_OS == OS_LINUX) {
    import "platform/linux";
} @else {
    @error("this platform is not supported");
}
```

`@warning` emits on the warning channel instead of failing: the compiler
collects each one it reaches and, once generation has succeeded, prints them
to stderr in emission order,

```
example.mc: warning: line 3: message
```

and the build carries on — the executable, object, or IR is still produced,
and under `--run` the warnings print before the program executes. An imported
`@warning` reports the file that declared it, with the same
relative-to-the-current-directory paths as errors. Like `@error`, a bare
`@warning` fires on every build, so it earns its keep guarded by an
[`@if`](#conditional-compilation) — flagging a build configuration as suspect
without rejecting it:

```c
@if (FAST_MATH and DEBUG) {
    @warning("FAST_MATH under DEBUG: results will not be reproducible");
}
```

The channel is collect-then-print: warnings are reported only *after* a
successful generation, so a build that stops with a hard error prints only
the error — any warnings collected before it are dropped with the failed
build.

The `-Werror` flag promotes warnings to the failure exit path. Every
collected warning still prints (collect-all-then-fail, not stop-at-first),
each rendered as an error line carrying a ` [-Werror]` marker:

```
example.mc: error: line 3: message [-Werror]
```

The exit status is 1 and no outputs are written — no executable, no object,
no `.mci` from `--emit-interface` — and `--run` does not execute the program.
`-Werror` is off by default; this repository's CI turns it on, so the
examples stay warning-clean.

All three directives are checked during code generation, once every type,
constant, enum, and global is known but before any function body is compiled,
and they fire in source order, so the first failure wins. A directive imported
from another module is checked too, and reports the file that defined it.
Directive messages are decoded with the same escapes as any [string](#strings)
literal, so `\n` and friends work.

See [examples/types/static_assert.mc](../examples/types/static_assert.mc) for
`@static_assert` and `@error` guarding a build, and
[examples/types/warnings.mc](../examples/types/warnings.mc) for a `-D`-gated
`@warning` and the `-Werror` promotion.

(For now these are top-level only; a statement-position form, and the
per-instantiation behavior it gives inside a generic body, are planned.)

### Opt-in warning classes

Some warnings come from analyses that can fire on perfectly legal,
C-idiomatic code, so they are grouped into named, **default-off** classes.
A class stays silent until a `-W<name>` flag enables it:

```
mcc file.mc -Wunchecked-dereference
```

The flag is repeatable, one class per flag, and `-Wall` enables every
opt-in class at once. An unknown class name is a hard error
(`mcc: error: unknown warning class 'name'`), so a typo cannot silently
enable nothing. An enabled class names its flag in each warning it prints —
the discoverability convention `[-Werror]` already established:

```
example.mc: warning: line 3: message [-Wunchecked-dereference]
```

`-Werror` composes unchanged, promoting exactly what printed: an
enabled-class warning renders as `message [-Werror=unchecked-dereference]`
and fails the build, while a *disabled* class neither prints nor fails it —
a bare `-Werror` build (this repository's CI) is unaffected by opt-in
classes it never enabled. The author-placed producers (`@warning`,
[`@deprecated`](#deprecated-functions)) stay unconditional — they are
explicit requests, not analyses — and keep their plain `[-Werror]` tail.
Filtering is print-time only, like the dedup: the collected list an
embedder reads keeps every emission, tagged with its class, and a warning
class never changes the code generated.

#### -Wunchecked-dereference

The first opt-in class warns on `*p`, `p->field`, and `p[i]` (reads,
writes, and compound assignments alike) where `p` is a nullable `T*` not
**proven non-null** at that site — "proven" being exactly the
[`@nonnull` proof relation](#nonnull-parameters), reporting instead of
rejecting:

```
example.mc: warning: line 3: dereference of a possibly-null pointer (narrow it with a null check or assert with postfix '!') [-Wunchecked-dereference]
```

What proves (and therefore silences a site): a `@nonnull` parameter, a
local or field projection flow-narrowed by a null-check guard
(`if (p != null)`, `if (b->data != null)`), a `let` binding seeded from an
always-non-null source, an array decaying to a pointer — including one
reached through a member/index chain (`grid[0][1]`, `unit.sizes[2]`, a
flexible `p->data[i]`: the array step is address arithmetic, so only the
chain's own `->` hops are sites) — and the postfix
`p!` assertion — which doubles as the per-site suppressor. Indexing a
[slice](#slices) never warns (the borrow's data pointer is the slice's
invariant), and arrays index directly. Narrowing's conservative limits
transfer as the warning's noise floor: a fact killed by an intervening
call or loop entry re-warns, and the usual fixes (re-guard, or `let`-bind
the guarded pointer) apply.

The class is off by default deliberately: mcc pointers are
nullable-by-default like C's, so a default-on warning would greet every
ported C idiom with noise. The standard library dogfoods the class: every
container (`list`, `ring`, `stack`, `queue`, `dict`, `set`) and hashing
module (`md5`, `murmur3`, `fnv1a`) compiles warn-free under it, having
asserted each invariant-backed dereference of its backing buffer with `!`,
so enabling the class on a program that imports them reports only *your*
unproven sites, never stdlib-internal ones.

See [examples/types/unchecked_dereference.mc](../examples/types/unchecked_dereference.mc)
for the class in action and each way to silence a site.

#### -Wdead-code

The generator has always silently dropped statements it can prove
unreachable: everything after a `return`, `break`, `continue`,
[`unreachable`](#the-unreachable-statement), or `emit`, after a direct call
to a [`@noreturn`](#noreturn-functions) function, and after an
`if`/`case`/`@if` statement all of whose generated paths diverge. The
`dead-code` class reports those drops instead of hiding them:

```
example.mc: warning: line 4: unreachable code: nothing runs after the 'return' above [-Wdead-code]
```

One warning per dead region, at its first statement, naming the construct
that killed it (`'break'`, `'unreachable'`, "a call to a `@noreturn`
function", "a loop that never exits", "every path through the statement
above diverges", ...). The messages never name types or callees — dead
code is dropped before it is ever type-checked, and the type-free wording
keeps a generic body's per-instantiation re-emissions byte-identical so
the print-time dedup collapses them to a single diagnostic. Like every
opt-in class, it never changes the code generated.

Code after a `break`-free `while (true)` is one of the killers:
[constant-condition folding](#control-flow) removes the loop's never-taken
exit edge, so nothing past the loop can run and the region reports as
`unreachable code: nothing runs after a loop that never exits`.

What does *not* warn, deliberately:

- **Code after a forever-loop containing a `break`** — the break keeps the
  loop's exit reachable, so the code after it is live, exactly as it runs.
- **The dead branch of an `@if`** — a not-taken compile-time branch is
  structurally unseen (never walked, never type-checked), which is its
  point; only a dead *tail inside the taken branch* warns.
- **`defer` bodies dropped because a *defer* diverged** — when one deferred
  action diverges at scope exit, the remaining registered actions are
  dropped; that is a different (planned) diagnostic, not dead code. A
  `defer` statement *in* a dead region is dead code like any other
  statement, warns, and never registers its body.

The class is default-off like the rest of the
[opt-in classes](#opt-in-warning-classes): `-Wdead-code` (or `-Wall`)
enables it, and under `-Werror` it promotes as
`[-Werror=dead-code]` — a bare `-Werror` build stays unaffected.

See [examples/control-flow/dead_code.mc](../examples/control-flow/dead_code.mc)
for each killing construct and the non-cases.

#### -Wextern-nonnull

[`@nonnull`](#nonnull-parameters) on an `@extern` declaration is a promise
about foreign C code the compiler cannot see the body of, so its call-site
enforcement is **graded** by three postures over one warning class, rather
than the flat hard error a native `@nonnull` keeps. A native `@nonnull`
never joins the class: its callee body holds the parameter as a load-bearing
non-null fact, so a possibly-null argument stays a hard error at every
posture.

Two things never grade, at any posture:

- Passing the `null` **literal** to an annotated extern slot is always a hard
  error — it is equally broken C, never porting noise.
- The LLVM `nonnull`/`dereferenceable` argument attributes are sound only
  under unconditional caller proof, so they ride an extern declare **only at
  the strict posture** (below). A native `@nonnull` always carries them.

The postures move the possibly-null case:

| posture | flag | possibly-null argument | extern LLVM hint |
| --- | --- | --- | --- |
| relaxed | *(default, no flag)* | silently accepted | not emitted |
| warn | `-Wextern-nonnull` (or `-Wall`) | `[-Wextern-nonnull]` warning | not emitted |
| strict | `-Werror=extern-nonnull`, or global `-Werror` with the class enabled | hard error | emitted |

Relaxed is the default a mechanical C port builds under with no flag at all,
so it never hits a null-proof wall on `strcpy`/`strlen`/`memcpy` calls —
strictness on the C boundary is what a codebase reaches for, not what a port
escapes from. The warn posture reports each possibly-null crossing:

```
example.mc: warning: line 5: passing a possibly-null pointer as argument 1 of 'ext': the parameter is @nonnull on an @extern declaration [-Wextern-nonnull]
```

The strict posture restores the unconditional caller proof the default
trades away, which is exactly what makes it sound to re-emit the LLVM hints
on the extern declares — so it recovers the codegen quality relaxed gives
up. It is reachable two ways: the whole-build `-Werror` promotes every *enabled*
class, so `-Werror -Wextern-nonnull` is strict — and `-Wall -Werror`, which
this repository's CI runs over every example, the wheel smoke tests, and the
`build.sh` stdlib build, is strict for all three classes at once; and the
selective `-Werror=<class>` input form makes strict a targeted posture on
the C boundary without promoting the whole build.

The annotation itself ships unconditionally in source and in
[`.mci`](#interfaces) stubs — the declared promise never varies per build;
only its enforcement does.

The [libc bindings](#reaching-libc) carry these annotations already: the
null-hostile pointer parameters of the `str*`/`mem*`, `strto*`/`ato*`,
`time`/`strftime`, and pointer-out math functions are `@nonnull`, so building
a program with `-Wextern-nonnull` enforces the C contract on every call into
them. (Slots where C gives `null` a meaning — `strtok`'s continuation, a
`strxfrm` with count `0`, `free`/`realloc`, `getenv`-adjacent probes — are
deliberately left plain.) This repository's own CI compiles the example suite
with `-Wextern-nonnull` for exactly this reason.

##### Selective -Werror=<class>

`-Werror=<name>` promotes a single warning class to error level, without the
whole-build promotion of a bare `-Werror`. It both enables the class and
marks it error-level (repeatable, one class per flag), and is general to any
registered class — `-Werror=unchecked-dereference` works the same way. An
unknown name is the same hard CLI error an unknown `-W<name>` gives
(`mcc: error: unknown warning class 'name'`). It composes with a global
`-Werror`, which still promotes every enabled class. The two spellings match
the output render that already spoke `[-Werror=<name>]`.

See [examples/systems/extern_nonnull.mc](../examples/systems/extern_nonnull.mc)
for the three postures on a foreign declaration.

#### -Wunused-result

A [`result`](#error-handling) carries either an ok value **or** an error, so
producing one in statement position and silently dropping it drops the error
on the floor — the accidental-ignore hole the error-handling design exists to
close. The class warns on exactly that: a bare expression statement whose
value is a `result<T, E>` / `result<E>`, discarded.

```
example.mc: warning: line 7: discarded result may carry an error (bind it, destructure it with 'let v, err =', handle it with 'try', or explicitly discard it with 'let _ = ...') [-Wunused-result]
```

Only a truly-dropped result warns. Every way of *consuming* one counts as
handled and stays silent: binding it (`let r = f();`), destructuring it
(`let v, err = f();`), a `try` in any ending (`try f();`,
`try f() except (err) { }`, `try f() ?? v`), passing it as an argument, or
returning it. When you mean to ignore the error deliberately, bind it to `_` —
the conventional throwaway name (mcc has no special blank identifier; `_` is
an ordinary identifier used by convention):

```c
let _ = maybe_fail();     // deliberate discard: silenced
```

Like the other opt-in classes it is default-off — `-Wunused-result` (or
`-Wall`) enables it, and under `-Werror` it promotes as
`[-Werror=unused-result]`, a bare `-Werror` build unaffected. See
[examples/types/error_handling.mc](../examples/types/error_handling.mc) for
the discard and its suppressor.

#### -Wnoreturn-own

An [`-> own`](#move-out-returns-own) value consumed in argument position is
destroyed when its statement ends — but a [`@noreturn`](#noreturn-functions)
callee's statement never ends: the call terminates the path, the queued
statement-end drop is discarded unemitted, and the value's destructor
provably never runs. The class warns on exactly that guaranteed leak:
an own temporary built for a `@noreturn` call's arguments — a direct own
argument, a [rendered f-string](#formatted-print--println) (it is a
synthesized `slice::format` own call), an f-string *hole*'s own temporary
at a `@noreturn` collector, or an own call nested inside an argument.

```
example.mc: warning: line 7: own value passed to a @noreturn function is never destroyed: the call never returns, so the value's statement-end cleanup never runs and it leaks (pass a plain value, or bind it to a let first to make the leak explicit) [-Wnoreturn-own]
```

`panic(f"x = {x}")` is the archetype ([Panic and assert](#panic-and-assert)
documents the stance): the leak is harmless by construction — the process
is dying — so the class is a *visibility* diagnostic for allocations that
were not deliberate. The detection is the drop machinery's own judgment, so
everything that never queues a drop stays silent: a plain message, a
destructor-less own value, and any own value passed to a function that
*returns* — `assert(cond, f"...")` never warns, because a passing assert's
rendered message is destroyed normally at statement end. Binding the value
to a `let` before the call silences the site (the leak then reads
explicitly at the binding: a scope that `panic` unwinds never runs its
destructors either). A call through a function-pointer *value* never
warns: `@noreturn` is not part of a
[function type](#referenceconst-carrying-function-types), so an indirect callee
is never known to diverge.

Like the other opt-in classes it is default-off — `-Wnoreturn-own` (or
`-Wall`) enables it, and under `-Werror` it promotes as
`[-Werror=noreturn-own]`, a bare `-Werror` build unaffected. See
[examples/functions/panic_assert.mc](../examples/functions/panic_assert.mc),
the class's living demo.

#### -Wdestructor-copy

mcc has no copy constructor, so a **bitwise copy** of a value whose type
declares a [`destructor`](#destructors) makes two names alias one live
resource — both would free it at cleanup, a double-free. The class warns at
the **copy site** on such a copy from a persistent lvalue:

```
example.mc: warning: line 9: a value with a destructor is copied here, aliasing a live resource (both copies would free it); hand it over with 'move(...)' or take it by 'const &' reference [-Wdestructor-copy]
```

Two copy sites fire: an explicit `let b = a;`, and a **by-value parameter**
call — the copy a plain [`const x: T`](#const-parameters) (or bare `x: T`) on
an owning type makes when passed a live variable, field, or element. The fix
is either the read-only `const &T` view (which shares the storage, no copy) or
the sanctioned relinquishing spelling `move(v)`, which blesses a deliberate
copy — `let b = move(a);` and `f(move(a))` are exempt. A fresh temporary is a
transfer or a build, not an alias, so an `-> own` call initializer
(`let s = make();`), a constructor, a literal, and an ephemeral chained
receiver never warn.

The class is the counterpart the `const`-flip needs: since a `const` struct
parameter is now a by-value copy (not the old hidden reference), an
unmigrated view-intended `const s: string` would silently copy an owning
value — this diagnostic surfaces exactly that. Default-off; `-Wdestructor-copy`
(or `-Wall`) enables it, promoting under `-Werror` as
`[-Werror=destructor-copy]`. See
[examples/types/destructors.mc](../examples/types/destructors.mc), the class's
living demo.

### Deprecated functions

`@deprecated("message")` is a declaration attribute on a function: the
function stays fully callable, but every *call site* emits a warning on the
channel above, pointing at the caller with the migration message:

```c
@deprecated("use bytecopy instead")
@inline
fn copy_bytes<T>(dst: T*, src: T*, n: uint64) {
    bytecopy(dst, src, n);
}
```

A call to `copy_bytes` now reports, at the caller's file and line:

```
main.mc: warning: line 12: 'copy_bytes' is deprecated: use bytecopy instead
```

The warning fires wherever the name resolves to the deprecated function: a
direct call, a generic call (including through an overload set — a set mixing
deprecated and current overloads warns only when resolution picks a deprecated
one), a [`for ... in`](#control-flow) loop whose `_it`/`_next` protocol
functions are deprecated, and taking the function as a
[value](#function-pointers) (`let p: fn(int32) -> int32 = f;`) — a call site
in waiting, warned at the point the value is formed since later indirect calls
cannot be attributed. The attribute combines with `@private`, `@static`,
`@extern`, `@inline`, and `@asm`, and applies to functions only for now
(types, enums, and globals later). The message decodes string escapes like any
literal, and must be non-empty.

Deprecation is per overload: in an
[overload set](#function-overloading) — open across modules — only a call
that *resolves to* the deprecated member warns; siblings stay quiet.

There is one suppression: a call made from *inside the body of a function that
is itself `@deprecated`* does not warn. A deprecation shim may delegate among
the deprecated cluster — a deprecated `writeln` forwarding to a deprecated
`writestr` — without each internal hop re-warning; only a *live* caller of a
deprecated function is warned. This holds for monomorphized deprecated generic
bodies and for function values formed inside a deprecated body too. A live
function still cannot hide a deprecated call behind a non-deprecated alias:
the exemption is exactly the enclosing-function-is-deprecated case. What *is*
folded is repetition of a single site: warnings are deduplicated at print time
on their (file, line, message), so one offending call inside a generic body
reports once, not once per instantiation. (The deduplication is print-time
only; embedders reading the collected list see every emission.) Everything
else follows the warning channel's rules: warnings print after a successful
generation, and [`-Werror`](#error-directives) promotes each to
`file: error: ... [-Werror]` and fails the build.

Deprecation travels through [interface files](#interface-files): a
generic or `@inline` function ships as verbatim source, carrying the
attribute for free, and a concrete function's bodyless prototype re-emits it
(`@deprecated("use renamed instead") fn old(x: int32) -> int32;`), so
importers of a compiled library still get warned at their own call sites.

The standard library uses this for the four renamed [memory](../lib/std/memory.mc)
forwarders — `copy_bytes`, `copy_items`, `set_bytes`, `set_items` — which
warn with their replacements (`bytecopy`, `copy`, `bytefill`, `fill`).
The terminal step of the lifecycle is the separate
[`@removed` tombstone](#removed-functions) below — a hard error at each call
site, one release before the name disappears entirely.

See [examples/types/deprecated.mc](../examples/types/deprecated.mc) for a
renamed function kept as a `@deprecated` forwarder, with the old-API call
sites (a direct call and a function value) behind a `-D`-gated `@if` branch
and the `-Werror` promotion.

### Removed functions

`@removed("message")` is the terminal state of the lifecycle above, one step
past `@deprecated`: a function goes from available, to `@deprecated(msg)`
(warns, still callable), to `@removed(msg)` (a hard compile **error** at
every call site), to finally deleted (the name gone, a generic "unknown
function"). The declaration is a *tombstone*: the implementation is gone, so
it is written bodiless — and a generic tombstone is the one generic function
that may go bodiless, since it never instantiates:

```c
@removed("use bytecopy instead")
fn copy_bytes<T>(dst: T*, src: T*, n: uint64);
```

A call to `copy_bytes` now fails the build, at the caller's file and line:

```
main.mc: error: line 12: 'copy_bytes' was removed: use bytecopy instead
```

The error fires wherever the name would resolve to the removed function — a
direct call (with or without explicit type arguments, which error before any
instantiation is attempted), a [`for ... in`](#control-flow) loop whose
`_it`/`_next` protocol functions are removed, and taking the function as a
[value](#function-pointers) — and it travels the normal error path, aborting
compilation like any compile error. There is nothing for
[`-Werror`](#error-directives) to promote: an uncalled tombstone compiles
clean and warns nothing. A removed call inside a generic body reports with
the usual [instantiation backtrace](#instantiation-backtraces) notes.

A tombstone's signature is parsed but never resolved: it registers only the
name and the message, so it stays valid even when its parameter types were
deleted along with the implementation, and one tombstone speaks for a whole
former overload set — mixing a tombstone with a live definition or a live
generic overload of the same name is a compile error at declaration time. A
local variable, constant, or file-scoped `@static` function shadows the name
exactly as it shadows a live function. And since the compiler is single-pass,
a removed call inside a generic body that is never instantiated is never
checked — consistent with every other error in an unused template.

`@removed` combines with `@private` and with `@extern` (retiring a C binding
is meaningful). It rejects `@deprecated` (removal is the lifecycle step after
deprecation — keep one), `@inline` and `@asm` (an uncallable function has no
call sites to inline into and no body to lower), and `@static` (a file-local
tombstone serves no caller in another file). A body-bearing declaration may
carry `@removed` too — the body is dead and never generated — but the
bodiless form is the idiomatic tombstone. The message decodes string escapes
like any literal and must be non-empty. Functions only for now, like
`@deprecated`.

Removal travels through [interface files](#interface-files): a generic
tombstone ships as verbatim source, and a concrete one's bodyless prototype
re-emits the attribute (`@removed("use renamed instead") fn old(x: int32) ->
int32;`, message re-escaped), so importers of a compiled library get the
targeted call-site error rather than a bare unknown-function one.

See [examples/types/removed.mc](../examples/types/removed.mc) for a renamed
function's bodiless generic tombstone next to its replacement, with the
erroring old-API calls behind a `-D`-gated `@if` branch.

## Control flow

```c
if (x > 10) {
    println("big");
} else if (x > 5) {
    println("medium");
} else {
    println("small");
}

while (x < 10) {
    x = x + 1;
}

until (x == 0) {     // inverse of while: stops when the condition is true
    x = x - 1;
}

while (true) {
    x = next();
    if (x == 0) { continue; }   // skip to the next iteration
    if (x < 0)  { break; }      // leave the loop
    handle(x);
}
```

Conditions accept `bool` or any integer (compared against zero, as in C).
A body that is a single statement does not need braces:
`if (x > 10) return x;`
`break` and `continue` apply to the innermost enclosing loop.

**Constant-condition folding.** A loop whose condition folds to always-run
at compile time — `while (true)`, `while (1)`, its dual `until (false)`, a
`const` reference, constant arithmetic (anything the
[constant folder](#constants) handles) — emits no exit edge, and when no
`break` targets the loop, no exit block at all: the loop **diverges**, like
a `return`. That lifts two checks that used to force dummy code:

```c
fn next_request() -> int32 {
    while (true) {                      // no exit edge: the loop diverges
        let r: int32 = poll();
        if (r != 0) { return r; }
    }
}                                       // no dummy trailing return needed

let first: int32 = {
    while (true) {
        let r: int32 = poll();
        if (r != 0) { emit r; }         // emit leaves by its own edge
    }
};                                      // no trailing emit needed either
```

The gate is the `break`: one anywhere in the body — including inside a
`case` arm, a nested block expression, or a `defer` — keeps the exit block,
and the code after the loop stays live (and both checks above apply again).
`return`, `emit`, `continue`, and [`@noreturn`](#noreturn-functions) calls
leave by their own edges (or not at all) and never gate the fold; a `break`
inside a *nested* loop targets that loop and does not gate the outer one.
With the exit edge gone, code after a `break`-free forever-loop can never
run — [`-Wdead-code`](#-wdead-code) reports it.

Two non-goals, deliberately: the never-runs duals (`while (false)` /
`until (true)`) keep their blocks and their fully type-checked body, like
`if (false)`; and `for` loops are untouched — every `for` form exits on a
runtime comparison, and no constant-true spelling exists. See
[examples/control-flow/forever.mc](../examples/control-flow/forever.mc).

`for x in obj` iterates anything that supplies the **`_it`/`_next` protocol** —
a pair of functions named after the iterable's struct, which the compiler
dispatches by name. For an `obj` of type `struct list<T>` it calls `list_it`
and `list_next`:

```c
fn list_it<T>(@nonnull self: struct list<T>*) -> struct iterator<list<T>>;  // make a cursor
fn list_next<T>(it: struct iterator<list<T>>*, out: T*) -> bool;    // false when done
```

The cursor is the **builtin** `struct iterator<C> { obj: C*; idx: uint64; }` —
available in every program with no import, and shared by the `list`, `set`,
`dict`, and `string` libraries rather than each defining its own. The
`pair<K, V>` the keyed containers yield from their `_next` (fields `key` and
`value`) is builtin the same way, as is the `enumerated<T>` that
[`enumerate`](#control-flow) yields. All are ordinary names, not reserved: a
user struct named `iterator`, `pair`, or `enumerated` takes precedence over the
builtin, exactly as a user-defined `range` function shadows the builtin
counting loop. The protocol itself only cares about the function names, so a
cursor of any shape works.

```c
for v in &nums {            // nums: list<int32>; v is int32, inferred from list_next
    if (v < 0) { continue; }
    if (v > 99) { break; }
    use(v);
}
```

The element type of `x` is inferred from `<struct>_next`'s out-parameter; `x` is
scoped to the loop, and `break`/`continue` work as usual. It lowers to
`{ let it = <struct>_it(obj); while (<struct>_next(&it, &x)) { ... } }`, with the
iterator held as a hidden, collision-proof temporary.

`<struct>_it` takes the container by pointer, but the `&` is **yours to choose**,
not required. A struct *value* is borrowed automatically — `for x in r` iterates
a snapshot (the value is copied once to a temporary the iterator points at),
while `for x in &r` iterates `r` itself by reference; a value already of pointer
type passes straight through. Because the snapshot is a real local, even an
rvalue is iterable: `for x in make_iter() { ... }` works, where `&` could not
take its address. For value types the two forms are indistinguishable; the
reference form matters when the body mutates the container as it goes (and, as
in C, growing a container mid-iteration invalidates an in-flight cursor either
way — the same staleness a [`&` return](#reference-returns) into container
storage suffers when a growing push lands between forming the reference and
using it).

```c
for v in nums  { ... }   // by value: iterate a snapshot of nums
for v in &nums { ... }   // by reference: iterate nums itself
```

Define `<struct>_it` and `<struct>_next` to make your own types iterable; a
struct built with [`extends`](#structs) can reuse its base's by forwarding
through an upcast. The `list`, `set`, `dict`, and `string` libraries all do
this — see [examples/control-flow/iteration.mc](../examples/control-flow/iteration.mc).

A builtin [`slice<T>`](#slices) is the exception: it iterates natively, with no
`_it`/`_next` of its own. `for x in s` (or `for x in &s`) walks the slice's
`ptr` from index `0` up to `length`.

For a plain counting loop, **`range` is builtin** — no import, no struct.
`for i in range(start, end)` counts over the half-open interval `[start, end)`,
and `for i in range(end)` counts from `0`. It lowers straight to a counter
(initialize, compare, increment), so it costs nothing beyond the loop itself.
The type of `i` is inferred from the bounds — their integer width and
signedness — or set explicitly with `range<T>(...)`:

```c
for i in range(5)        { ... }   // 0, 1, 2, 3, 4
for i in range(2, 9)     { ... }   // 2 .. 8
for i in range<int64>(n) { ... }   // i is int64
```

`i` is a fresh copy of the counter each turn (assigning to it in the body does
not change the iteration), and `range` here is a compiler builtin, not a name in
scope — but a user-defined `range` function, if any, takes precedence.

To iterate **with the position**, `enumerate` is builtin the same way:
`for e in enumerate(obj)` runs `obj`'s ordinary iteration — the `_it`/`_next`
protocol, or a slice's native walk — while keeping a counter, and each turn
yields an `enumerated<T>` (the builtin `struct enumerated<T> { index: uint64;
value: T }`) read as `e.index` / `e.value`:

```c
for e in enumerate(&nums) {                    // nums: list<int32>
    println(f"{e.index}: {e.value}");       // 0: first, 1: second, ...
}
for e in enumerate(xs as slice<char>) { ... }  // slices too; index is free
```

`obj` is borrowed exactly like a bare `for x in obj` (a value is snapshot, `&`
iterates by reference, an rvalue works), and `_next` writes straight into the
element's `value` field, so no extra copy is made per turn. The index starts at
`0` and counts every yielded element — a `continue` still consumes its
position. As with `range`, a user-defined `enumerate` function takes
precedence, and a user struct named `enumerated` shadows the builtin one
(`enumerate` then reports it cannot yield through it). `enumerate(range(...))`
is rejected — the counter *is* the value there; iterate the range directly.

`case` matches a value against a series of `when` arms, with an optional
`else:` default. The subject is evaluated once, and there is **no
fall-through** — a matching arm runs only its own statements and then the
`case` is done:

```c
case (c) {
    when 'a':           handle_a();
    when 'b':           handle_b();   // arms hold any number of statements
    when '0', '1', '2': handle_digit();   // an arm matches any listed value
    else:               handle_other();
}
```

A `when` arm may list several comma-separated values and matches if the
subject equals **any** of them. Each value may be any expression of the
subject's type (untyped constants adapt to it), and the subject can be any
type comparable with `==` — integers, `uint8` characters, pointers, `bool`,
or `float64`.
`break` and `continue` inside an arm act on the enclosing loop, not the
`case`; the no-fall-through semantics mean `break` is never needed to end
an arm.

`case type (a) { when int32 n: ... else: ... }` is the same statement shape
switching on a **type** instead of a value: its subject is an
[`any`](#the-any-type), each arm names one or more types and binds the
recovered value, and `else:` is mandatory. See [The any type](#the-any-type).

### The with statement

`with (t = v as T) body; else other;` is the checked-`as` test: it tests an
[`any`](#the-any-type) subject's boxed tag against one type and, on a match,
binds `t` to the recovered value, **scoped to the true branch** (the `else`
branch has no binding). It is pure sugar over a single-arm `case type` —
`case type (v) { when T t: body else: other }` — condensing the common
one-type unwrap to a line:

```mcc
fn describe(a: any) {
    with (n = a as int32) println(f"int32: {n}");
    else println("something else");
}
```

The pieces, each inherited from the construct it desugars to:

- The head is initializer-style and is itself the **checked context**:
  inside `with (...)`, `t = v as T` is the tag test plus bind, while `as`
  everywhere else keeps its [cast](#casts) meaning — so the **subject must
  be an `any`** (an `any*` auto-dereferences, as in `case type`), and a
  non-`any` subject is a compile error at the `with` site. The binding is
  **required**: `with (v as T)` without the `t =` does not parse. The head
  deliberately mirrors the planned bare unwrap `let t = v as T;` — the same
  spelling, with `with`/`else` supplying the mismatch handling that the
  bare form will get from a trap.
- The pattern follows the exact detection rule of
  [generic `case type` arms](#the-any-type): a type name that resolves is a
  concrete test (a single tag compare); an *unresolved* bare name introduces
  an arm-scoped type parameter — `with (v = a as T)` monomorphizes its body
  once per boxed tag in the whole program's boxed set, `with (ptr = a as T*)`
  over the pointer tags only — each copy fully type-checked, a tag the body
  does not compile for failing the compile with a note naming the type. The
  generic form keeps `case type`'s reach-the-end conservatism: a
  value-returning function still needs a statement after the `with` even
  when both branches return. Exactly **one** pattern: dispatching over
  several types is what `case type` is for.
- Both bodies take a single statement or a braced block, like `if`. The
  `else` is **optional** — where `case type`'s open tag set makes its
  `else:` arm mandatory, `with` carries its miss behavior inline: an
  unmatched tag (including a zero-filled `any`'s tag 0) runs the `else`, or
  falls through a lone `with` doing nothing. Defined behavior, not a trap.
- The checked bind is the **entire** parenthesized head: it does not
  compose with `and`/`or`, and there is no `while (t = v as T)` — keeping
  the binding's scope obvious.

`with` is a reserved word. See
[examples/types/with_unwrap.mc](../examples/types/with_unwrap.mc).

### The unreachable statement

`unreachable;` asserts that a path is never executed. The statement
**diverges**: no `return` is needed after (or instead of) it, dead code past
it is dropped like code after a `return`, and a diverging null-guard body
made of it [narrows](#nonnull-parameters). It lowers to LLVM `unreachable`,
so actually reaching it at runtime is **undefined behavior** — the optimizer
deletes the path — exactly like C's `__builtin_unreachable()`. It is an
assertion the compiler trusts, not a checked trap.

Its idiomatic home is the `else` arm of an exhaustive `case`, where it
replaces the dummy trailing return the compiler would otherwise demand:

```c
fn name(dir: int32) -> char* {      // dir is always 0..2 by construction
    case (dir) {
        when 0: return "north";
        when 1: return "east";
        when 2: return "south";
        else: unreachable;          // asserts the case is exhaustive
    }
}                                    // no unreachable dummy return needed
```

On a `case type` (whose `else:` is mandatory) it asserts a closed universe
of boxed types the same way. `unreachable` is a reserved word — it can no
longer be used as an identifier.

For a *function* that never returns rather than a path that never happens,
see [@noreturn functions](#noreturn-functions) — a `@noreturn` body that
falls off its end gets an implicit `unreachable`. See
[examples/control-flow/unreachable.mc](../examples/control-flow/unreachable.mc).

## Defer

`defer` schedules a statement (or a `{ }` block) to run when the enclosing
block exits — by _any_ path: falling off the end, a `return`, or a
`break`/`continue` out of a loop. It keeps a resource's release next to its
acquisition, so cleanup can't be forgotten on an early exit:

```c
fn process() -> int32 {
    let buffer: uint8* = alloc<uint8>(4096);
    defer dealloc(buffer);          // freed however this function returns

    if (bad()) {
        return -1;                  // buffer is still freed
    }
    use(buffer);
    return 0;                       // and here too
}
```

Multiple defers run in **reverse order** (last deferred, first to run), so
resources unwind in the opposite order they were acquired. The block form
groups several actions:

```c
let a = open(...);
defer close(a);
let b = open(...);
defer close(b);
defer {                            // runs first: close(b), then close(a)
    flush();
    sync();
}
```

A defer is tied to the block it appears in, so one inside an `if` or a loop
body fires at the end of that block — each loop iteration runs its own. The
deferred code is evaluated when it runs, not when it is scheduled, so it sees
the latest values of the variables it names (unlike Go, which snapshots the
arguments). A returned value is computed _before_ the defers run, so freeing a
buffer in a defer can't clobber what you return. A
[destructor-declaring type's](#destructors) constructor-sugar `let`
registers its automatic cleanup call as an ordinary entry on this same
stack, so values destroy LIFO, interleaved with explicit defers in
registration order.

Defers run on every **block exit** — and a call to a
[`@noreturn` function](#noreturn-functions) is not one. `exit(1);` leaves
enclosing defers unrun — automatic [destructor](#destructors) calls
included (the process ends inside the callee; there is no
return path to unwind), matching C, where `exit()` runs `atexit` handlers
but never unwinds the calling stack. Code that must clean up should
`return` an error up to `main` instead of exiting deep in the call tree. An
[`unreachable;`](#the-unreachable-statement) likewise runs no defers — a
path that never happens has nothing to unwind. See
[examples/control-flow/defer.mc](../examples/control-flow/defer.mc).

**Control flow cannot jump out of a defer body.** A defer runs while its
scope is already unwinding, so a jump that leaves the body would re-unwind
the very scope whose defers are running. Each is a compile-time error at
the offending statement:

- `defer break;` — `'break' inside a defer body cannot exit the enclosing
  loop`, and the same for `continue`;
- `return` anywhere in a defer body — `'return' inside a defer body cannot
  exit the enclosing function`;
- an `emit` targeting a [block expression](#block-expressions) outside the
  body — `'emit' inside a defer body cannot exit the enclosing block
  expression`.

The judgment resets at constructs opened *inside* the body: a loop declared
in the defer may `break`/`continue` itself, and a block expression declared
in the defer may `emit`, as usual.

## Block expressions

A `{ ... }` in expression position is a **block expression**: it runs its
statements in their own scope and yields a value with `emit`. Think of it as
an inlined, single-use, anonymous function — temporaries declared inside stay
inside, and only the `emit`ted value escapes:

```c
let value: uint64 = {
    let hi = read_hi() as uint64;
    let lo = read_lo() as uint64;
    emit (hi << 32) | lo;
};
// hi and lo don't exist out here; only `value` does
```

`emit` is to a block expression what `return` is to a function — it fills in
the block's value and jumps to the block's end. The two are orthogonal:
`emit` targets the nearest enclosing block expression, `return` always leaves
the whole function, and `break`/`continue` still act on the enclosing loop. So
a `return` inside a block expression exits the function, not just the block.

A block expression's type is the type of what it emits, and — like a function
that must `return` on every path — it must `emit` on every path that can reach
its end. An exhaustive `if`/`else` (or a `case` with an `else`) where every arm
emits is enough; otherwise a trailing `emit` supplies the fall-through value:

```c
let label: uint8* = {
    if (n < 0) emit "negative";
    else if (n == 0) emit "zero";
    else emit "positive";          // every path emits -- no trailing one needed
};
```

`emit` is only valid inside a block expression; using it in a plain block
statement or at a function's top level is an error (use `return` there). The
trivial `{ emit e; }` is just `e`, so an untyped constant still adapts to its
context (`let n: uint64 = { emit 1; };`). Any `defer` inside runs when the
block yields, before the value leaves — exactly as a function's defers run
before its `return`.

## Types

| Type                                                  | LLVM equivalent                                                   |
| ----------------------------------------------------- | ----------------------------------------------------------------- |
| `int8`, `int16`, `int32`, `int64`                     | `i8`, `i16`, `i32`, `i64` (signed)                                |
| `uint8`, `uint16`, `uint32`, `uint64`                 | `i8`, `i16`, `i32`, `i64` (unsigned)                              |
| `char` (one-byte [text](#strings); distinct from `uint8`) | `i8` (unsigned)                                              |
| `byte` (transparent alias for `uint8`; the raw memory unit) | `i8` (unsigned)                                            |
| `bool`                                                | `i1`                                                              |
| `float64`                                             | `double`                                                          |
| `T*` (any type + `*`s)                                | pointer                                                           |
| `T[N]` (fixed-size [array](#arrays))                  | `[N x T]`                                                         |
| `slice<T>` (non-owning [view](#slices))               | `{ T*, i64 }`                                                     |
| `any` (tagged [box](#the-any-type))                   | `{ i64, [2 x i64] }` (tag + 16-byte payload)                      |
| `fn(A) -> R` ([function pointer](#function-pointers)) | `R (A)*`                                                          |
| `void`                                                | `void` (return type only; `void*` is not allowed -- use `uint8*`) |

Literals with a decimal point are `float64` and `true`/`false` are `bool`.
Integer literals are written in decimal or hexadecimal (`0xFF`), and are
_untyped constants_: they adapt to the integer type
they are used with as long as the value fits (so `let x: uint64 = 5;` and
`x % 7` both work; `let y: uint8 = 300;` is a compile error). Where no
context provides a type -- most notably `let` without an annotation -- an
untyped constant is a compile error rather than silently picking one.
Where one _is_ needed but unconstrained (a variadic argument, constant
arithmetic), the default is the narrowest of `int32`, `int64`, `uint64`
that holds the value: `7` is `int32` — so `printf("%d", 7)` matches C's
`int` — while `5000000000` is `int64` and a 64-bit mask is `uint64`, with
no silent truncation. Constant integer arithmetic folds at compile time and
stays untyped, widening as needed (`1 + 5000000000` is `int64`;
`10 * sizeof(int64)` is `uint64` because `sizeof` is typed; `2 + 3` is
still untyped).

A [character literal](#strings) (`'a'`) is likewise an untyped constant, but one
that **defaults to [`char`](#strings)** when no context constrains it (so
`let c = 'a';` is a `char`, not an error), while still adapting to a
`uint8`/integer slot when one is expected (`let b: uint8 = 'a';`).

The one implicit conversion between typed values is **lossless widening inside
an expression**: when a binary operator combines two integers of the **same
signedness**, the narrower one is extended to the wider, and the result is the
wider type. Because both sides meet at the wider type, the operator stays
commutative — `a + b` and `b + a` agree — so arithmetic like `a + b * c` over
mixed widths needs no per-term casts:

```c
let a: uint64 = 1;
let b: uint32 = 2;
let c: uint16 = 3;
let r = a + b * c;         // uint64: b and c widen as the expression combines
```

Crucially, this applies **only between operands within an expression** — never
when a value crosses into a named typed slot. Assignment, `return`, and call
arguments still require the types to match (untyped constants aside), so a
widening there is explicit:

```c
let b: uint32 = 2;
let x: uint64 = b;         // error: assigning uint32 to uint64 needs `b as uint64`
let y: uint64 = b + 0;     // ok: widening happens inside the expression
```

The guiding principle: **a value never narrows or changes signedness
implicitly, and only crosses widths automatically while being combined in an
expression.** Narrowing to a smaller type, crossing between signed and
unsigned, or widening on the way into a variable/return/argument all need an
explicit `as`. So `let z: uint32 = a + b;` is an error (the `uint64` result
would narrow) and `uint32 + int32` is an error (mixed signedness). That keeps
storage boundaries deliberate while sparing you casts mid-calculation.

Shifts combine their operands by the same rule (the count widens to meet the
value), so `count << amount` works across same-signed widths. Untyped shift
constants are still sized by their result: `let x: uint64 = 1 << 40;` works (the
untyped `1` widens to hold the result), but a typed `uint32` value shifted that
far overflows its width and needs `(v as uint64) << 40`.

Signedness changes behavior, not representation: unsigned types use unsigned
division, remainder, and comparisons, and zero-extend instead of sign-extend
when promoted. Unary `-` is not allowed on unsigned values. See
[examples/basics/unsigned.mc](../examples/basics/unsigned.mc).

## Operators

By descending precedence: unary `-` `~` `!` `*` `&`, `as` casts, then `*` `/`
`%`, `+` `-`, shifts `<<` `>>`, bitwise `&`, `^`, `|`, comparisons
`<` `<=` `>` `>=`, `==` `!=`, then `and`, then `or`, the `?:` conditional, and
loosest of all `??` (the [`try` fallback](#defaulting-the--fallback) / null
coalesce), which is right-associative.
Comparisons yield `bool`; `%` and the bitwise/shift operators are
integer-only. `>>` is an arithmetic shift for signed types and logical for
unsigned. Unary `~` is bitwise complement (integer-only); `!` is logical
NOT on a `bool`. Unlike C, bitwise operators bind tighter than comparisons,
so `a & 4 == 4` means `(a & 4) == 4`. Integer constant expressions fold at
compile time.

`and` and `or` are the logical operators (there is no `&&` / `||`). They
short-circuit — the right side is evaluated only when the left does not
already decide the result — take a `bool` or integer on each side (non-zero
is true, as in a condition), and yield a `bool`. They bind looser than
comparisons, so parentheses are usually unnecessary:

```c
if (a > 0 or a < 0 and b < 0) { ... }   // a > 0 or (a < 0 and b < 0)
if (p != null and p->ready) { ... }     // p->ready read only when p != null
```

`cond ? a : b` is the conditional expression: it tests `cond` (a `bool` or
integer, as in an `if`) and yields one arm or the other — never both, so the
untaken arm's side effects do not happen. It binds looser than every binary
operator (only `??` is looser) and is right-associative, so it reads as an
`if`/`else` ladder without parentheses:

```c
let m = a > b ? a : b;                          // the larger of the two
let s = x > 0 ? 1 : x < 0 ? -1 : 0;             // x > 0 ? 1 : (x < 0 ? -1 : 0)
```

The two arms must agree on a type, the same way binary operands do: equal
types are kept, an untyped constant arm adapts to the other's type (two
untyped integer arms widen to the larger), and `null` adapts to a pointer
arm. Because the result is a runtime value rather than a literal, it is the
concrete unified type, not an adaptable constant — `let n: uint8 = c ? 1 : 2;`
needs the arms to already be `uint8`, or an `as` cast. When the condition is
itself constant the whole expression folds, so it may appear in a `const`
initializer or an `@if` condition.

One adaptation does reach through the ternary: when **every arm is a string
literal**, the whole expression adapts to a `slice<char>`/`slice<const char>`
expected from context (an argument, an annotated `let`, a `return`), exactly
as a bare literal would ([Strings](#strings)). Each arm borrows in its own
branch, so `flag ? "y" : "yes"` at a `slice<char>` parameter carries the
chosen literal's own length. An explicit [borrow](#slices) distributes the
same way — `(flag ? a : b) as slice<char>` borrows whichever owned array the
condition picks, keeping its static length.

## Casts

`expr as type` converts explicitly (there are no implicit conversions
between variables):

```c
let a: int32 = 300;
let b = a as uint8;        // truncates: 44
let c = a as int64;        // sign-extends (zero-extends from unsigned types)
let d = a as float64;      // 300.0
let e = 3.99 as int32;     // truncates toward zero: 3
let p = malloc(4) as int32*;  // pointer casts
let n = p as uint64;       // pointer <-> integer
```

`as` binds tighter than binary operators: `a + b as int64` is
`a + (b as int64)`.

Casts between structs are rejected, with three exceptions: an
[`extends`](#structs) value-upcast to a base struct, a **borrow** to a
[`slice<T>`](#slices) view (`xs as slice<T>` from an owned `list<T>` or `T[N]`),
and the layout-equivalent [tuple](#tuples) cast — `(a, b) as A` converts a
tuple to any struct with the same field types in the same order, and a struct
back to its positional form.

## Pointers

`T*` is a pointer to `T`. `&x` takes a variable's address, `*p` dereferences
(both to read and to assign), and `p[i]` indexes:

```c
fn bump(p: int32*) {
    *p = *p + 1;
}

fn main() -> int32 {
    let x = 41;
    bump(&x);                                  // x is now 42

    let nums = malloc(5 * sizeof(int32)) as int32*;
    nums[0] = 7;
    free(nums);
    return 0;
}
```

`sizeof(type)` is a compile-time `uint64` constant (pointers are 8 bytes).
`sizeof` also accepts a variable — `sizeof(v)` is the size of `v`'s type, as in
C — so you need not spell the type out. The operand is never evaluated, so it
has no side effects and folds to the same constant.

`alignof(type)` and `offsetof(struct S, field)` are the two other compile-time
`uint64` layout constants, the C counterparts of the same name. `alignof(T)` is
a type's alignment in bytes (and, like `sizeof`, accepts a variable —
`alignof(v)`); `offsetof(struct S, field)` is a field's byte offset within a
struct, honoring padding, `@packed`, and `@align`. All three fold at compile
time, so they may size arrays and initialize a `const`:

```c
struct mixed { a: uint8; b: int64; c: uint16; }   // a@0, b@8, c@16; sizeof 24

const ALIGN  = alignof(struct mixed);              // 8
const B_OFF  = offsetof(struct mixed, b);          // 8
let scratch: uint8[offsetof(struct mixed, c)];     // a 16-byte buffer
```

`uint8*` doubles as the
raw-memory pointer (C's `void*`): any pointer
implicitly coerces to it, which is why `free(nums)` works without a cast.
A [string literal](#strings) is a `char[N]` array that decays to a `char*`
(which coerces to `uint8*`), so `"hi"[0]` is the byte `104`.

### Pointer arithmetic

Pointers join the binary and compound operators, with C's element-scaled
semantics and no bespoke syntax. `p + n` and `p - n` advance a pointer by `n`
elements — `p + n` is exactly `&p[n]`, so `n` is any integer type, scaled by
`sizeof(pointee)` — and the compound forms `p += n` / `p -= n` follow:

```c
let end = p + 8;          // eight int32s past p (32 bytes)
p += 1;                   // step one element forward
let scan = &buf[0];
while (scan < end) {      // pointer ordering: the scan-loop idiom
    total = total + *scan;
    scan += 1;
}
```

`uint8*` is the raw-memory pointer, so its element size is 1 and its
arithmetic is byte arithmetic. `p - q` requires two pointers of identical type
and yields their signed element distance as an `int64` (byte distance is
`uint8*` arithmetic or a `p as uint64` round-trip). The ordering relationals
`< <= > >=` likewise require identical pointer types; `==` / `!=`, the
`!= null` checks, and the ternary work as before. In `p - q` and the
relationals a `const` qualifier on the pointee is ignored, so `int32*` and
`const int32*` compare and subtract without an explicit cast.

Pointer arithmetic is an always-non-null source: `p + n` proves non-null at a
[`@nonnull`](#nonnull-parameters) slot exactly as `&p[n]` does, and `*(p + n)`
never warns under [`-Wunchecked-dereference`](#-wunchecked-dereference) — the
derived address is proven like `*&p[n]` (v1 does not look through to the base
pointer). By the same axiom `p += n` / `p -= n` keep a narrowed local's
non-null fact (the moved pointer is the derived address `p + n` is), including
across a loop back edge — the pointer-walking scan
`let p = start!; while (p < end) { ...*p...; p += 1; }` stays warn-free on one
seed. A compound move stays rejected on a `@nonnull` parameter (which cannot
be reassigned).

Everything else keeps its rejection. Addition is pointer-left only: `p + n` is
the accepted shape and the commuted `n + p` is rejected (the pointer is the
base being advanced, and `n - p` has no meaning). `p + q`, the multiplicative
operators `* / %`, the bitwise operators, and the shifts stay unsupported on
pointers (a tag-bit trick keeps its explicit `as uint64` round-trip), function
pointers keep `==` / `!=` only, and any `null` operand is rejected. Pointer
arithmetic is a runtime expression only — it is not available inside a `const`
initializer or an [`@if`](#conditional-compilation) condition. See
[examples/memory/pointers.mc](../examples/memory/pointers.mc),
[examples/systems/byte_scan.mc](../examples/systems/byte_scan.mc), and
[lib/std/memory.mc](../lib/std/memory.mc) for a generic typed allocator.

## Function pointers

`fn(A, B) -> R` is the type of a pointer to a function taking `A, B` and
returning `R` (a missing `-> R` means `void`, as in a declaration). A bare
function name — written without the call parentheses — is a value of that
type, so functions can be stored in variables and struct fields, passed as
arguments, and returned:

```c
fn add(a: int32, b: int32) -> int32 { return a + b; }
fn sub(a: int32, b: int32) -> int32 { return a - b; }

fn apply(op: fn(int32, int32) -> int32, x: int32, y: int32) -> int32 {
    return op(x, y);            // call through the parameter
}

fn main() -> int32 {
    let op: fn(int32, int32) -> int32 = add;
    op = sub;                                    // reassignable
    return apply(op, 10, 3) + apply(add, 1, 1);
}
```

The signature must match exactly — `add` does not fit a `fn(int32) -> int32`.
`null` is a valid function pointer and they compare with `==` / `!=`, so an
optional callback works:

```c
if (cb != null) { cb(x); }
```

A trailing `...` after at least one fixed parameter makes a **variadic**
function-pointer type, `fn(A, ...) -> R` — the type of a pointer to a variadic
function, matching a C `R (*)(A, ...)`. It is distinct from the non-variadic
form (a plain function does not fit a variadic slot, or vice versa), and it is
how a variadic like `printf` is held or passed:

```c
fn run(log: fn(char*, ...) -> int32) -> int32 {
    return log("answer = %d\n", 42);    // called with varargs
}

run(printf);
```

Any expression of function-pointer type is callable, not just a variable —
a struct field, an array element, or the result of another call:

```c
widget->on_click(x);   // a callback stored in a struct
table[i](x);           // an entry in a dispatch table
chooser()(x);          // the function a call returned
```

In a type, `*` and `[N]` bind to the return type, so `fn(int32) -> int32*`
is a function returning `int32*`. Group with parentheses to bind them
outside the function type instead — `(fn(int32) -> int32)*` is a pointer to
a function pointer, and `(fn(int32) -> int32)[N]` is an array of `N`
function pointers (a dispatch table):

```c
fn add(a: int32, b: int32) -> int32 { return a + b; }
fn mul(a: int32, b: int32) -> int32 { return a * b; }

@static let ops: (fn(int32, int32) -> int32)[] = [add, mul];

fn main() -> int32 {
    return ops[0](2, 3) + ops[1](2, 3);   // 5 + 6
}
```

A function name folds to a constant address, so a `@static` table can be
initialized with one at compile time.

A `const` (or `@static let`) may also name a single function, giving a
compile-time **alias** you then call by its new name. The type is inferred from
the function, so nothing needs spelling out — even a C variadic like `printf`
aliases cleanly. (`println` is a function value too, but a call through the
value performs no [collection](#native-variadic-arguments): it takes the trailing
`slice<const any>` explicitly — see
[&/const-carrying function types](#referenceconst-carrying-function-types).)

```c
const log = printf;           // an alias; the type is inferred
const plus = add;

fn main() -> int32 {
    log("plus(2, 3) = %d\n", plus(2, 3));
    return 0;
}
```

(A `@static let` aliasing a function must either be annotated or left to infer
from the bare function name; an unannotated table literal still needs its type,
as `@static let ops: binop[] = [add, sub];` above.)

A function value is a pointer underneath, so it casts like one: `add as
uint64` is the function's address as an integer, `addr as fn(...) -> R`
turns an address back into a callable pointer, and it bitcasts to/from a
data pointer such as `uint8*`. (One exception: an `as` directly between two
function types whose `&`/`const` parameter conventions differ is rejected —
see [&/const-carrying function types](#referenceconst-carrying-function-types).)

Only a single, non-generic function has an address; a generic name like
`id` cannot be used as a value (there is no one instance to point at).

### @nonnull-carrying function types

A function type may spell a [`@nonnull`](#nonnull-parameters) contract per
parameter: `fn(@nonnull char*, @nonnull char*) -> int32` is the type of a
function whose arguments must be provably non-null. The bare name of a
`@nonnull` function infers the annotated type, and a call through the value
runs the **same call-site proof as a direct call** — flow narrowing and the
postfix `!` hatch included:

```c
fn first(@nonnull p: int32*) -> int32 { return *p; }

fn main() -> int32 {
    let f = first;      // inferred: fn(@nonnull int32*) -> int32
    let x: int32 = 5;
    return f(&x);       // &x is a proof, exactly as in first(&x)
                        // f(q) with an unproven q is the same compile
                        // error a direct call gives; f(q!) asserts
}
```

Assignability along the contract axis is **contravariant**. A plain function
value flows into an annotated slot — the annotation only adds a call-site
obligation, which a function that tolerates null meets trivially. The
reverse is rejected: binding an annotated value to a plain fn type would
let calls skip the proof, so the error explains the drop and names the
hatch. The explicit hatch is `as`: casting to the plain type strips the
contract as a free bitcast, and calls through the result skip the proof —
undefined behavior if an argument is actually null, exactly like `p!`.

```c
fn plain(p: int32*) -> int32 { return p == null ? -1 : *p; }

let g: fn(@nonnull int32*) -> int32 = plain;    // ok: adds the obligation
let h: fn(int32*) -> int32 = first;             // error: drops the contract
let k = first as fn(int32*) -> int32;           // the hatch: UB if null
```

Variance is flat: the rule applies to function values themselves, never
deeply through slices, arrays of function types, nested fn types, or `any`.
The contract is part of the type's identity — a `@static` dispatch table
typed `(fn(@nonnull int32*) -> int32)[]` accepts plain members, `.mci`
[interface files](#interface-files) spell the annotation in prototypes, a
template instantiated with `fn(@nonnull char*) -> int32` is a distinct
instance from the plain form, and a prototype must spell the contract
exactly as its definition does.

One accepted asymmetry: a function value of a `@nonnull` `@extern`
(`let f = strlen;`) carries the contract, and calls through the value check
**strictly**, while direct extern calls keep grading by the
[-Wextern-nonnull](#-wextern-nonnull) posture — an indirect call can no
longer be attributed to an extern declaration, so the graded posture cannot
apply.

`@nonnull` in a function type applies only to pointer parameters, checked
where the type is used, so a generic alias like
`type cb<T> = fn(@nonnull T) -> int32` is validated per binding
(`cb<int32*>` is fine, `cb<int32>` is rejected). It is the only
**annotation** that may appear in this position — `@noalias` is an
unchecked hint that drops silently from a function value, and `@format` is
compile-time sugar keyed to a declaration, never part of a value's type.
The `&` and `const` keywords take the same slot and spell the
hidden-reference calling conventions, described next.

See
[examples/functions/nonnull_callbacks.mc](../examples/functions/nonnull_callbacks.mc).

### Reference/const-carrying function types

A function type may also spell the [`&`](#reference-parameters) and
[`const &`](#const-parameters) reference conventions: `fn(&char) -> void`
is the type of a function whose parameter is passed as a writable pointer to
the caller's own storage, and `fn(const &struct big) -> int64` one whose
struct parameter travels by read-only hidden reference. The bare name of such a
function infers the carrying type, and a call through the value passes the
same by-reference arguments — and enforces the **same call-site rules** —
as a direct call: the argument of a `&` parameter must be the caller's
own writable lvalue of exactly the parameter's type (or a provably non-null
pointer to it, which decays), and the `const`/`@volatile`/`@packed`
rejections all apply.

```c
fn bump(a: &char) { a = a + 1; }

fn main() -> int32 {
    let f = bump;       // inferred: fn(&char) -> void
    let c: char = 'A';
    f(c);               // passes &c underneath; c is now 'B'
    return 0;           // f('x') is the same "not assignable" error
}                       // a direct bump('x') gives
```

Unlike the `@nonnull` contract, the convention is **not convertible** — in
either direction, with no `as` hatch. `fn(&char)` and `fn(char)` receive
their argument differently at the machine level (a pointer to storage
versus the value itself), so no call sequence through the wrong type could
be correct; the mismatch is rejected wherever the two meet, and the error
says why no cast is offered:

```
error: line 3: let g: expected fn(char) -> void, got fn(&char) -> void
(a reference parameter is passed by hidden reference, a different calling
convention; the types are not convertible)
```

An `as` between two function types is allowed only when their `&`/`const`
shape and their return convention match — a same-shape signature
reinterpret, including stripping a `@nonnull` contract, still works.
(Laundering through a data pointer, `f as uint8* as fn(char)`, remains
possible and is undefined behavior, exactly like forging an address with
`as fn(...)`.)

A by-value `const` carries **no** caller contract, so it drops from the type
entirely — of every type, not just scalars: `fn(const int32)` **is**
`fn(int32)` and `fn(const struct big)` **is** `fn(struct big)`, one type with
one spelling. Only the `const &T` **view** — a real pointer convention —
stays in the type, spelled `fn(const &struct big)`. This erasure is what makes
a comparator alias transparent across kinds of `T`:

```c
type cmp<T> = fn(const T, const T) -> bool;

fn less(a: int32, b: int32) -> bool { return a < b; }          // scalar T:
let ci: cmp<int32> = less;                                     // const drops

fn big_less(const a: struct big, const b: struct big) -> bool  // struct T:
    { return a.a < b.a; }                                      // const drops
let cb: cmp<struct big> = big_less;                            // by-value fits
```

Like the `@nonnull` contract, the convention is part of the type's
identity: it is spelled in `.mci` [interface files](#interface-files),
instantiates templates distinctly, and a prototype must spell it exactly as
its definition does.

The return convention is spelled the same way: `fn(...) -> &T` is the
type of a [`&`-returning](#reference-returns) function, so the last
function-value ban is gone. The bare name of a `-> &` function infers the
carrying type, and a call through the value is the same **lvalue
expression** a direct call is — assignable (`f() = v`, `f() += v`),
projectable (`f(s).field = v`, `f(t)[i] = v`), and re-lendable as another
call's `&` argument — with the same guarantees, since the callee's own
body passed the formation and storage rules when it compiled. A field-held
callee works too (`table.get(i) = v` stores through the returned
reference), and `&f()` stays rejected (the reference must not escape its
full expression). Inside another `-> &` function the value composes into
formation chains: a chain-position call through a `fn(...) -> &T`
value vouches for its storage exactly as a named `-> &` candidate does.

```c
@static let counter: int32 = 0;
fn counter_ref() -> &int32 { return counter; }

fn main() -> int32 {
    let f = counter_ref;    // inferred: fn() -> &int32
    f() = 41;               // assignment through the returned lvalue
    f() += 1;               // compound assignment
    return f();             // value context: loads (counter is 42)
}
```

Like the parameter conventions, the reference return is **not convertible** — in
either direction, with no `as` hatch: a `fn() -> &int32` call returns a
pointer to the vouched storage where a `fn() -> int32` call returns the
value itself, so no call sequence through the wrong type could be correct
(the mismatch error says a reference return is passed as a pointer to the
returned storage). `fn() -> &void` is rejected per use — there is no
storage to reference — so a generic alias like
`type getter<T> = fn() -> &T` is validated per binding, and
`-> &const T` is banned at parse time in both the declaration and the
fn-type slot (a reference return must be writable).

A [collecting](#native-variadic-arguments) function is a function value
too — its trailing `args...` parameter is sugar for `const args:
slice<const any>`, an aggregate `const`, so the value's type spells it
(`fn(const slice<const any>) -> int32`). Collection is a **direct-call**
affordance, though: a call through the value takes the trailing slice
explicitly (forwarding an existing `args` is the common shape), and the
compile-time `@format` desugars — positional `{n}` placeholders,
f-strings — do not apply through a value either (the runtime sequential
form still works, since the callee parses its format string at runtime).

See
[examples/functions/reference_callbacks.mc](../examples/functions/reference_callbacks.mc)
and
[examples/functions/reference_return_callbacks.mc](../examples/functions/reference_return_callbacks.mc).

## Arrays

`T[N]` is a fixed-size array of `N` elements, laid out inline. A local one
is stack-allocated; `@static` makes a zero-initialized file-scoped buffer.
Index with `[]`, and `sizeof` reports the whole array's bytes:

```c
fn main() -> int32 {
    let squares: int32[5];                 // five int32s on the stack
    let i: int32 = 0;
    while (i < 5) { squares[i] = i * i; i = i + 1; }
    return squares[4];                      // sizeof(int32[5]) == 20
}
```

A dimension `N` may be any constant integer expression — a literal, a `const`,
`sizeof`, an `as` cast, or arithmetic over them — evaluated at compile time, and
it must come out at least 1. So all of these are fixed-size arrays:

```c
const ROWS = 4;
const COLS = 4;
let board: int32[ROWS * COLS];      // 16
let line:  uint8[COLS + 1];         // 5, room for a trailing NUL
let words: uint8*[sizeof(int64)];   // 8
```

An array literal `[a, b, c]` (a trailing comma is allowed) initializes an
array, nesting for more dimensions. The outermost dimension can be left as
`[]` and is inferred from the literal's length:

```c
let primes: int32[] = [2, 3, 5, 7, 11];          // length inferred as 5
let grid: int32[2][2] = [[1, 2], [3, 4]];        // nested
```

An array literal can also initialize (or be `as`-borrowed into) a
[`slice<T>`](#slices), backed by a hidden array in the function frame — see
[Slices](#slices) for the positions and rules.

A local literal's elements may be any expressions; a `@static` one must be a
constant expression — literals, `const` references, `sizeof`, `as` casts, or
arithmetic — so a lookup table lives in read-only data:

```c
@static let cmds: uint8*[][2] = [
    ["help", "show this help"],
    ["quit", "exit the program"],
];
```

`len(arr)` is the element count — a compile-time constant, handy as a loop
bound and the way to read a size you let `[]` infer. It adapts to its
context like a literal, so it compares against any integer counter (`int32`
or `uint64`) without a cast, and it folds in constant expressions, so it can
size another array (`let ys: int32[len(xs)];`). The same builtin reports a
[tuple](#tuples)'s arity. For a multi-dimensional array, `len(grid)` is
the outer length and `len(grid[0])` the inner one:

```c
let i: int32 = 0;
while (i < len(cmds)) { use(cmds[i]); i = i + 1; }
```

Like C, an array decays to a pointer to its first element wherever a value is
used: it passes to a `T*` parameter, and `&arr[i]` gives an element address. A
few more rules:

- No whole-array assignment or copy.
- Arrays can be struct fields.
- In a type, `*` binds to the element — `int32*[8]` is an array of eight
  pointers; parenthesize for the other order.
- Each `N` is a positive integer literal (`[]` only as the inferred outermost
  dimension).

## Slices

`slice<T>` is a builtin **non-owning view** over a contiguous run of `T`: a
two-word value `{ data: T*; length: uint64 }`. It borrows storage it does not own
— it never allocates — so the value it views must outlive it. A slice supports a
runtime `.length`, indexing `s[i]` (reads and writes go straight through to the
borrowed storage), and native [`for x in s`](#control-flow) iteration.

A slice is constructed by an explicit **borrow** — the `as` cast applied to an
owned value:

```c
let arr: int32[4];                 // a fixed array...
let view = arr as slice<int32>;    // ...borrowed as { &arr[0], 4 }

let nums = list<int32>(8);         // ...or an owned list<T>
let s = nums as slice<int32>;      // reads { data, length }, drops capacity
```

The source is either an owned `T[N]` (giving `{ &arr[0], N }`) or a struct that
**is** a `slice<T>` or names one in its declared `extends` lineage — such as
`list<T>`, which `extends slice<T>` (its `data`/`length` are the base's fields,
followed by its own `capacity`, ignored in the borrow). The borrow follows that
declared lineage, not a coincidentally matching `{ T*, integer }` shape: a struct
laid out like a slice but without the `extends` clause does not borrow. (The
check was once structural — a pre-`extends` vestige, from before `list<T> extends
slice<T>` could be declared — now retired in favor of the nominal rule.) The
element types must match. A `char[N]` is the one
special case: as NUL-terminated [text](#strings), its borrow drops the trailing
terminator, so `length` is `N - 1` (the text, without the NUL); a `uint8[N]` raw
buffer keeps every byte. This is the one struct-producing `as` (ordinary struct
casts are rejected). A slice is a plain value: it passes to and returns from
functions by value (two words). Because it is two words it is **not** C-ABI by
value — across a C boundary, pass a `T*` and a length separately instead. See
[examples/memory/slices.mc](../examples/memory/slices.mc).

An **[array literal](#arrays)** can be borrowed directly — no named array
needed. The literal materializes a hidden backing array in the enclosing
function's frame (so the storage lives for the whole call) and the slice views
it: `[16, 31, 255] as slice<int32>` is `{ &backing[0], 3 }`. Like a string
literal, it also **adapts** from context with no `as`: at an annotated `let`,
at an array or slice element whose element type is a slice (nested literals
recurse, so one `as` covers a whole `slice<slice<int32>>`), and at a **function
argument** whose parameter is a `slice<T>` (`sum([1, 2, 3])`). See
[examples/memory/slice_literals.mc](../examples/memory/slice_literals.mc):

```c
let nums: slice<int32> = [0x10, 0x1F, 0xFF];    // adapts from the annotation
let view = [1, 2, 3] as slice<int32>;           // or borrow explicitly, anywhere
let m: slice<int32>[2] = [[1, 2], [3, 4]];      // elements adapt too
let nested = [[5, 6], [7]] as slice<slice<int32>>;
let empty: slice<int32> = [];                   // { null, 0 } — no storage at all
```

The length is the **exact element count** — an array literal carries no NUL, so
`['h', 'i'] as slice<char>` has `length == 2`. That differs from the two-step
form: bind `let cs: char[2] = ['h', 'i'];` first and `cs as slice<char>` has
`length == 1`, because a named `char[N]` is presumed NUL-terminated
[text](#strings) and its borrow drops the trailing byte. The empty literal `[]`
builds no backing array at all: it is the `{ null, 0 }` empty view. A
[ternary](#operators) whose arms are all array literals adapts as a whole, each
arm borrowing its own backing array in its own branch. A **mutable** `slice<T>`
target is fine — the backing storage is fresh and nothing else names it, so
writes go through (`let s: slice<int32> = [1, 2]; s[0] = 9;`), and copies of
the view alias the one backing array. Each literal *occurrence* owns one slot,
re-stored per execution — a view captured on one loop pass observes a later
pass's elements, like any loop local.

At a **function argument** the literal borrows into the calling frame, so the
view lives for the whole call — even against a plain (non-`&`) `slice<T>`
parameter, whose fresh backing array is writable (uniform-allow, exactly as for
a string literal). A `&slice<T>` parameter still rejects a literal: it
demands the caller's own named storage, which a literal is not. The adaptation
holds through an overload set — `f([1, 2, 3])` picks the `slice<int32>`
overload over an `int32*` one, never adapting the literal to the pointer — but a
literal argument contributes nothing to type inference, so a **generic**
`slice<T>` parameter needs `T` from an explicit type argument or a companion
argument (`count<int32>([1, 2, 3])`; bare `count([1, 2, 3])` cannot infer `T`).

A **[struct-literal](#structs) field** is another adapting position: a string or
array literal (or a ternary of them) in a field whose declared type is a char
slice / `slice<T>` borrows into that field with no `as`, and a `= default` field
whose default is such a literal borrows the same way — the field type stands in
for the annotation. As at an argument, a literal field contributes nothing to a
**generic** struct's type inference: `box { v = "hi" }` on `struct box<T> { v: T; }`
still binds `T = char*` (a bare type parameter, not a slice, is no adaptation
target), and `struct nums<T> { xs: slice<T>; }` cannot infer `T` from `nums { xs
= [1, 2] }` alone. A literal field adapts only once the field type is a concrete
slice — from the declaration, from a companion typed field that fixes the
parameter (`row { name = "x", val = seven }` on `struct row<T> { name: slice<const
char>; val: T; }` infers `T = int32` from `val`, and `name` borrows), or from
explicit type arguments. One evaluation-order seam: to infer a generic struct's
type arguments, mcc must evaluate the non-literal fields *before* borrowing the
literal ones, so in the generic-without-explicit-args case an array-literal
field's element expressions run after a later non-literal field's — the same
narrow reorder the argument path documents. String-literal fields are
side-effect-free, and the non-generic (or explicit-type-argument) path evaluates
strictly left to right, so both are order-safe. A `@static` struct or union
literal folds a string- or array-literal field to a constant `{pointer, length}`
view the same way its scalar counterpart does.

**Assignment** to an existing char-slice lvalue is another adapting position,
for string literals only. `s = "hi";` reborrows: it repoints `s` at the
literal's global string constant (dropping the NUL, so the length is the new
literal's), the same borrow a `let` or argument does. Because a string constant
is static-lifetime, the reborrow is safe even when the target outlives the
current frame, so it reaches every assignment form — a plain name, a deref
(`*out = "hi";`), an index (`a[i] = "hi";`), a member (`c.name = "hi";`, the
mirror of the `cmd { name = "hi" }` struct literal), and a reference return
(`f(...) = "hi";`) — and a ternary of string literals adapts arm by arm here
too. **Array-literal assignment is not supported** (`s = [1, 2, 3];` is a
compile error): the materialized backing array is frame-local, but an
assignment target can outlive the frame (a `&slice<T>*` out-parameter, an
outer-scope variable), so the borrowed view would dangle — the same lifetime
hazard that rejects `return [..] as slice<T>;`. The `let`/argument positions
stay safe only because the binding and its backing share a frame. See
[examples/memory/slice_assignment.mc](../examples/memory/slice_assignment.mc).

The last adapting position, again for string literals only, is a **dot-call
receiver**: `"{}{}".format(a, b)` borrows the literal into a `slice<const char>`
so a `slice::<method>` family reaches it, exactly as the explicit
`("{}{}" as slice<const char>).format(a, b)` does. The adaptation is a pure
fallback — it fires only when the literal's own `char[N]` type resolves no
method or field of that name *and* a matching `slice::<method>` exists, so a
genuine array method is never shadowed and the literal keeps its default
`char[N]`-decaying-to-`char*` type for C interop (`strlen("hi")` still passes a
`char*`). The receiver is passed as an `as slice<const char>` borrow, so the
method sees the text without its NUL. This is a string-literal receiver only: a
named `char[N]` array or a `char*` value does *not* reach slice methods by dot
(`arr.format(...)` on a named array is still the char-array call-shape error).
The two reserved method names `constructor` and `destructor` stay rejected in
dot spelling here as everywhere.

One position still does not take the shortcut. `return [1, 2] as slice<int32>;`
is a compile error — the view would point into the returning call's hidden
backing array, which dies with the return and is named by nothing else (a
*named* local's borrow, `return xs as slice<int32>`, still compiles, with the
usual care returning a borrow of a local demands). In a
`@static` initializer the elements must be constant expressions and land in
read-only data, so only the read-only form is allowed:
`@static let g: slice<const int32> = [1, 2];` becomes a constant
`{pointer, length}` view over an anonymous constant array, exactly as a
`@static` [string-literal view](#strings) borrows its string constant; a
mutable `@static slice<int32>` is rejected —
it would open a write path into read-only data — with a message pointing at
`slice<const T>`.

### Sub-slicing

A slice **sub-slices**: `s[start:end]` yields a new slice viewing the same
storage, `{ &s.data[start], end - start }`. Either bound may be omitted —
`start` defaults to `0` and `end` to `s.length` — so all four forms parse:

```c
let nums = [10, 20, 30, 40] as slice<int32>;
let mid  = nums[1:3];   // { &nums.data[1], 2 } — 20, 30
let tail = nums[1:];    // end defaults to nums.length
let head = nums[:2];    // start defaults to 0
let all  = nums[:];     // a plain copy of the view
```

The result is the receiver's type **verbatim**, so element mutability rides
the element type: a sub-slice of `slice<T>` writes through to the shared
storage, and a sub-slice of [`slice<const T>`](#read-only-slices) is
`slice<const T>` — copying the view opens no new write path. It is a plain
**rvalue** slice value: it passes as an argument, iterates with `for x in
s[1:]`, and sub-slices again (`s[1:][1:]`), but it is not an lvalue —
`s[1:3] = ...`, the compound forms, and `&s[1:3]` are all rejected. Bounds
have **index parity**: any integer type is accepted, widened internally by its
own signedness, so an `int32` start mixes freely with the defaulted `uint64`
end. And like indexing, bounds are **unchecked**: no code validates
`start <= end <= s.length`, so an out-of-range pair is undefined behavior — a
corrupt view, exactly like an out-of-range `s[i]`. `s[n:n]` is the defined
empty result `{ &s.data[n], 0 }`: the one-past-end pointer is formed but never
dereferenced, and it is deliberately *not* normalized to the empty literal's
`{ null, 0 }`.

A slice also **destructures**: `let first, rest... = s;` binds
`first = s[0]` and the trailing-`...` rest binder `rest = s[1:]` — pure
sugar over the indexing and sub-slicing above (any number of leading
binders; the rest binder is optional), with the source evaluated once. The
rules carry verbatim, so in particular **nothing checks the length**: like
`s[i]`, destructuring a source with fewer elements than binders is undefined
behavior, not an error. The rest binder is a **view** of the same storage —
writes through it reach the base, unlike a [tuple](#tuples)'s rest binder,
which copies — and binding every element leaves the defined empty tail
`{ &s[n], 0 }`.

Receivers are **slice-typed expressions only**. Everything else reaches
sub-slicing by first becoming a slice through its existing borrow spelling,
which keeps every borrow rule exactly where it lives today: a fixed array
borrows first (`(arr as slice<int32>)[1:]`, keeping the `char[N]` NUL-drop and
read-only-source rules), a `list<T>` or other slice-extending struct borrows
first (`(xs as slice<int32>)[1:]` — a struct may carry derived state beyond
the view, like a list's `capacity`, that only its author knows how to
rebuild), and a string or [array literal](#arrays) borrows first
(`("hello" as slice<char>)[1:3]`, `([1, 2, 3] as slice<int32>)[1:]`). A
non-slice receiver is a compile error suggesting the borrow. Two forms are
excluded by design, not deferred: **negative indices** (an index is a raw
element offset everywhere in mcc) and a **step** — `s[a:b:c]` is
unrepresentable in the `{ data, length }` layout, and `::` lexes as one token,
so `s[::2]` stays a parse error. A full expression parses before the slice
`:` is considered, so a ternary start binds its own `:` greedily —
`s[flag ? 1 : 2 : 3]` is `start = flag ? 1 : 2` with `end = 3`. Sub-slicing
is a runtime expression only: it does not fold in [`const`](#constants)
initializers, [`@if`](#conditional-compilation) conditions, or `@static`
initializers. A `slice<char>` sub-slice carries its exact length and no NUL at
`data + length` — already true of every borrowed slice. See
[examples/memory/sub_slices.mc](../examples/memory/sub_slices.mc).

### Read-only slices

`slice<const T>` is a **read-only** view — the element-mutability distinction
(like Rust's `&[T]` versus `&mut [T]`). Indexing reads through as usual, but the
element is not assignable: `s[i] = x` is a compile error. A loaded value is an
independent copy, so it is freely mutable (`let v = s[0]; v = v + 1;` is fine),
as is the variable of a `for x in s` loop.

```c
fn sum(s: slice<const int32>) -> int32 {   // promises not to write through s
    let total: int32 = 0;
    for v in s { total = total + v; }
    return total;
}
```

`const` only adds: a mutable `slice<T>` **widens** implicitly to its
`slice<const T>` form (the two share one layout, so the value passes through
unchanged), and a borrow of a mutable source may target either form. The reverse
is forbidden — a read-only source borrows only to `slice<const T>`, never to a
mutable `slice<T>`. A read-only source is a `slice<const T>`, a `const`
[parameter](#functions), or a `const`-typed value:

```c
let xs: int32[3];
let view = xs as slice<const int32>;   // mutable array -> read-only view
let all  = xs as slice<int32>;         // ...or a mutable one
let r: slice<const int32> = all;       // widening is implicit

fn f(const xs: int32[3]) {
    let ok  = xs as slice<const int32>;   // a const parameter stays read-only
    // let no = xs as slice<int32>;       // error: would reopen a write path
}
```

The same `const` qualifier applies to any type (`let pi: const float64 = 3.14;`
is a variable that cannot be reassigned), but the read-only slice is its main
use today.

## Structs

`struct` declares an aggregate type; fields use the same `name: type;` form
as everything else, and structs can be generic. In type positions the
`struct` keyword is optional. `->` accesses a field through a pointer, `.`
accesses a field of a struct value, and `&` takes field addresses. `null` is
an untyped pointer constant that adapts to any pointer type, and pointers
compare with `==` / `!=`.

```c
struct point {
    x: int32;
    y: int32;
}

struct node<T> {            // generic; monomorphized like functions
    value: T;
    next: struct node<T>*;  // self-reference through a pointer
}

fn main() -> int32 {
    let p = alloc<struct point>(1);
    p->x = 3;
    let copy = *p;          // dereferencing copies the struct
    let n = copy.x + 1;
    bump(&p->x);            // address of a field
    if (p != null)
        dealloc(p);
    return 0;
}
```

A **struct literal** builds a struct value in one expression:
`Name { field = value, ... }`, with the `struct` keyword optional
(`struct Name { ... }` means the same thing). Any field left out is
zero-initialized, so `point { }` is all zeros and the order of the listed
fields does not matter. Each value is checked against its field's type exactly
as an assignment would be, so untyped integer constants adapt to the field
type. The literal is an ordinary value — usable as an initializer, an
argument, a return value, or the right side of an assignment
(`*p = point { x = 1, y = 2 };`).

```c
let p = point { x = 6, y = 4 };
let q = point { x = 9 };            // y is 0
let r = node<int32> { value = 1 };  // generic, type argument given
```

The one place the keyword-free form is not allowed is the header of a
`for x in <expr> { ... }` loop, where `for x in A { ... }` would be ambiguous —
`A { ... }` could be the iterable or the loop body. There the `{` always starts
the loop body; parenthesize (`for x in (A { ... })`) or use the keyword form to
iterate a literal. The restriction ends at any inner bracket or parenthesis
(`for x in make(A { ... })` is fine), and every other position is unambiguous.

A generic struct's type arguments may be given explicitly
(`pair<int32, uint8*> { ... }`) or **inferred** from the field values,
the same way a generic function call infers from its arguments — so with a
`n: int32`, `pair { a = n, b = "x" }` deduces `A = int32`, `B = uint8*`.
Only a **typed** field value pins a parameter (and two typed fields that
disagree are an error). An untyped constant doesn't anchor a parameter — a bare
`pair { a = 0, b = "x" }` leaves `A` ambiguous, the same way `let a = 0`
is, since the constant has no type of its own to deduce, only a default it would
guess. It still _adapts_ to a parameter another field has already fixed. A
parameter no typed field determines falls back to its
[declared default](#type-parameter-defaults) when it has one — with
`struct range<T = int64>`, `range { start = 0, stop = 10 }` is
`range<int64>` and the constants adapt to it. A parameter with neither a
typed field nor a default can't be inferred, so spell those cases out with
explicit type arguments. A field whose own type is a struct takes a nested
literal.

Where the struct type is already fixed by context, the name may be **dropped**
entirely — a bare `{ field = value, ... }` takes its type from the position, the
way `[...]` and `"..."` adapt to a `slice<T>`. It is allowed in every position a
slice literal is: a type-annotated `let`, an assignment (including `*p = { ... }`,
`a[i] = { ... }`, `s.field = { ... }`, and a reference return), a `return`, a function
argument, an array or slice element, and a nested struct field. Unlike a slice
borrow a struct literal is a plain value copy, so it also adapts in a `return`
with no lifetime concern.

```c
let p: point = { x = 6, y = 4 };     // the annotation fixes the type
p = { x = 3, y = 4 };                // so does the target of an assignment
let s: seg = { a = { x = 1, y = 2 }, b = { x = 3, y = 4 } };  // nested
let ps: point[2] = [{ x = 1, y = 2 }, { x = 3, y = 4 }];      // elements
fn shifted(p: point) -> point { return { x = p.x + 1, y = p.y + 1 }; }
```

A bare literal used as an argument is resolved against the parameter's struct
type, and among **overloads** it is picked by its field names: `{ x = 1, y = 2 }`
fits a `point` parameter but not a `box` of `w`/`h`, so the call is unambiguous
(a wrong field _value_ type is still reported once the type is fixed). A bare
literal can never itself infer a generic type parameter — it carries no type — so
a plain `f({ ... })` on `fn f<T>(x: T)` is an error; the concrete struct must
come from the parameter (`fn f(p: point)`), an annotation, or the other typed
arguments.

Two positions do **not** infer a bare literal: a `for x in <expr> { ... }`
header (the same ambiguity the keyword-free form has, above) and a
[ternary](#operators) arm (`cond ? { ... } : { ... }`) — name the type there
(`cond ? point { ... } : point { ... }`). A bare literal in a position with no
struct type to take — a bare expression statement, a `sizeof`, an un-annotated
`let` — is an error that names the positions where one is expected.

A field may declare a **default value** with `name: type = expr;`. When a struct
literal omits that field, its default is used instead of zero (an explicit value
in the literal still wins); fields with no default stay zero. `extends` carries
the base's defaults down to a derived struct's literal.

```c
struct config {
    capacity: int32 = 16;     // used when `capacity` is omitted
    verbose:  int32 = 0;
    name:     uint8*;         // no default — zero (null) when omitted
}

let c = config { name = "db" };   // capacity = 16, verbose = 0
```

Declaring any default also changes a bare declaration: `let c: struct config;`
is then default-initialized (zeroed, then its defaults applied) — the same as
`let c = config { }` — rather than left uninitialized. A struct with no
defaults keeps the uninitialized behavior of a bare `let`, like any other type.

A default is an ordinary expression evaluated wherever it is applied, so it
should be self-contained — a literal, a `const`, or another in-scope value —
and not refer to the struct's own type parameters. A defaulted field that is
omitted does not take part in type-argument inference.

A struct literal whose fields are all constant expressions may initialize a
`@static` global, folded to a data constant at compile time rather than filled
in at runtime — omitted fields still zero (or take their `= default`), and
nested struct, array, and slice fields fold recursively:

```c
@static let origin: struct point  = point  { x = 0, y = 0 };
@static let setup:  struct config = config { name = "db" };  // capacity = 16
@static let unit:   struct box    = box {
    corner = point { x = 1, y = 1 },
    sizes  = [10, 20, 30],
};
@static let start:  struct point  = { x = 1, y = 2 };        // bare form works too
```

A [union literal](#unions) folds the same way. See
[examples/types/static_initializers.mc](../examples/types/static_initializers.mc).

`@align(N)` raises a struct's alignment to `N` bytes — a power of two; asking
for less than the natural alignment is an error. `sizeof` rounds up to a
multiple of the alignment, and field offsets and array strides stay
consistent with it, including when an aligned struct is nested inside
another:

```c
@align(64)
struct counter {     // sizeof is 64: one per cache line
    hits: uint64;
}
```

`@packed` is the opposite: it removes the padding between fields, placing
them at consecutive byte offsets, and drops the struct's alignment to 1 —
the layout for wire formats and file headers. Member accesses are compiled
as unaligned, but (as in C) taking a pointer _into_ a packed struct with
`&` and dereferencing it elsewhere is unsafe. `@packed` combines with
`@align(N)`, which then sets the overall alignment and rounds `sizeof`
back up:

```c
@packed
struct header {      // sizeof is 9, not 16
    tag: uint8;
    length: uint64;
}
```

`@volatile` marks a struct whose loads and stores must all happen exactly
as written — the optimizer may not elide, merge, or hoist them. This is for
memory-mapped hardware registers, where reading or writing _is_ the side
effect; it propagates through nested fields, and also applies to `@extern`
variables:

```c
@volatile
struct pl011 {       // a UART's register block; see examples/baremetal/
    dr: uint32;      // data register: write a byte to transmit
    ...
}
```

A struct can `extends` another to reuse its layout — and its
[method families](#inherited-methods). The base's fields are
placed **first**, followed by the new struct's own, so the base occupies the
start of the derived struct and a pointer to the derived struct is
layout-compatible with a pointer to the base:

```c
struct point  { x: int32; y: int32; }
struct point3 extends point { z: int32; }   // laid out as x, y, z

fn length2(p: struct point*) -> int32 { return p->x * p->x + p->y * p->y; }

fn main() -> int32 {
    let p: struct point3;
    p.x = 3; p.y = 4; p.z = 5;        // inherited fields are reached directly
    return length2(&p as struct point*);   // upcast is explicit
}
```

Because the base is a true prefix, the upcast `&p as struct point*` reads the
same storage; casting the value, `p as struct point`, copies just the base
prefix. Outside one carve-out, both are _explicit_ — a `struct point3*` is a
distinct type that won't silently pass where a `struct point*` is expected.
The carve-out is the **receiver position of a method-family call**, which
upcasts implicitly — deriving from a struct also inherits its
[methods](#inherited-methods), and there a derived receiver lends (or
prefix-copies into) the base parameter; every other position keeps the
explicit `as`. What decides whether the upcast is allowed is the
**declared `extends` lineage**, not the layout: only a struct that names the
target — transitively — in an `extends` clause upcasts to it. A struct that
merely shares the target's field prefix, with no `extends` between them, is
rejected, and two structs that extend the same base never interconvert (each
upcasts to the shared base, neither to its sibling). The prefix layout stays the
mechanism — the base's fields come first, so the upcast is a zero-cost
reinterpret — but the declared lineage is the definition. The same nominal
lineage is what a generic [`extends` bound](#bounds) ranges over
(`<T extends point>` admits `point3` but not a layout twin). Only the upcast
direction is allowed: narrowing a base value back to a derived one would read
past it. With no body of its own,
`struct meters extends int_wrapper;` is a **specialization** — a distinct
type with the base's exact layout, useful for branding values so the compiler
keeps them apart. See
[examples/types/extends.mc](../examples/types/extends.mc) for a runnable tour
of the named-base form: the prefix layout, both upcasts, defaults carrying
down, and a specialization brand.

The base's `@packed`, `@align`, and `@volatile` are **inherited**: an
extending struct is volatile if its base is, takes at least the base's
alignment, and is packed iff its base is (packing changes field offsets, so
it can't differ from the base — `@packed` on a struct whose base is not
packed is an error).

Generics work on both sides — a generic struct can extend a generic base,
which is monomorphized together with it:

```c
struct pair<K, V>  { key: K; value: V; }
struct entry<K, V> extends pair<K, V> { state: uint8; }   // key, value, state
```

See [examples/types/generic_extends.mc](../examples/types/generic_extends.mc)
for a runnable version of this shape.

The base may also be a **bare type parameter** — the intrusive-container
shape, where a generic struct embeds whatever payload it is instantiated
with and appends its own fields after it:

```c
struct linked_list_entry<T> extends T { next: linked_list_entry<T>*; }
struct linked_list<T>       { head: linked_list_entry<T>*; }
```

`linked_list_entry<mystruct>` lays out `mystruct`'s fields first and appends
the link, so an entry pointer upcasts to `mystruct*` and the payload is
reached with no indirection. Note `next` must be a **pointer**,
`linked_list_entry<T>*` — the usual self-reference-through-a-pointer rule.
The semantics is field **embedding** with prefix layout, not a named member:
the payload's fields become the entry's own (`e->value`, not
`e->payload.value`), so the explicit upcasts above apply unchanged, and the
base's field defaults and `@packed`/`@align`/`@volatile` carry down exactly
as for a named base — per instance, since each instantiation has its own
base. See
[examples/memory/intrusive_list.mc](../examples/memory/intrusive_list.mc)
for a runnable intrusive list built on this shape.

Because `T` is only known once bound, struct-ness is checked per
instantiation: `linked_list_entry<int32>` fails with `int32 is not a struct;
cannot extend it`, and a union base, a flexible-array-member base, or a
collision between the payload's field names and the extender's fail the same
way — each error carries an `in instantiation of ...` note tracing it to the
triggering request. One caveat on literals: type-argument **inference**
walks only the extender's own fields, so a literal that names base fields
needs explicit type arguments — `linked_list_entry<mystruct> { value = 5 }`
works, but `linked_list_entry { value = 5, next = null }` is an error, since
`value` is nobody's field until `T` is bound. A bodyless extender
(`struct branded<T> extends T;`) brands its payload per instantiation — each
instance a distinct type with its payload's exact layout.

This is distinct from the `T extends mystruct` [**bound**](#bounds): a bound
constrains what a caller may bind `T` to, while this uses `T` as the base —
same keyword, different position, no overlap — and the two compose as
`struct wrapper<T extends node> extends T`. Unlike a named base, a bare
type-parameter base does **not** participate in
[method inheritance](#inherited-methods): the payload is only known per
instantiation, so there is no declared base family at the declaration, and a
payload's methods are reached through the explicit upcast instead.

A struct extends a single base — named as a struct (optionally generic) or a
bare type parameter that must be bound to a struct; a pointer, array, or
function type is not a valid base.

A struct's last field may be a **flexible array member** — a trailing
`field: T[]` written with no size. It contributes **0** to `sizeof` and decays
to a `T*` pointing at the struct's tail, so a single allocation can hold the
header and a run of elements laid out contiguously after it (the C
`struct { int len; T data[]; }` idiom, without the `T[1]` "struct hack"). It
must be the struct's **last** field, with `[]` as its only dimension; a struct
that ends in one cannot be a base for `extends` (the inherited member would no
longer be last):

```c
struct packet {
    length: uint64;     // element count
    data: int32[];      // flexible array member — last field, adds 0 to sizeof
}

fn main() -> int32 {
    let n: uint64 = 4;
    // One allocation: the header plus n trailing elements.
    let p = alloc<byte>(sizeof(struct packet) + n * sizeof(int32))
        as struct packet*;
    p->length = n;
    p->data[0] = 7;            // index through the tail pointer
    return 0;
}
```

The member has no storage of its own, so it cannot be set in a struct literal
(or given a default) and cannot be borrowed as a `slice<T>` — its length is not
known statically. Reach it through `p->data`, indexing as far as the allocation
runs. See [examples/types/flexible_array_members.mc](../examples/types/flexible_array_members.mc).

The [layout constants](#pointers) describe a flexible array member precisely:
`offsetof(struct S, data)` is where its elements **begin** — the tight base for
sizing an allocation, `offsetof(struct S, data) + n * sizeof(T)` — and
`alignof(struct S)` counts the element type, so the tail is always aligned for a
`T`. `sizeof(struct S)` excludes the member (it adds 0) but is still rounded up
to the struct's alignment, so it can exceed `offsetof(struct S, data)` by a few
bytes of trailing padding; both over-allocate safely, offsetof exactly.

`sizeof` understands struct layout (including padding), so
`alloc<struct node<int32>>(n)` allocates correctly. Struct values can be
passed to and returned from functions, but not to variadic functions like
printf — pass a pointer or a field instead. See
[examples/types/structs.mc](../examples/types/structs.mc) and the data structures built on
them: the growable [lib/std/list.mc](../lib/std/list.mc), the open-addressing hash
table [lib/std/set.mc](../lib/std/set.mc) (borrowing, identity-keyed), and the
string-keyed [lib/std/dict.mc](../lib/std/dict.mc), which owns copies of its keys and
compares them by content.

## Unions

A `union` is an aggregate whose members all share one storage: its size is the
largest member's, rounded up to the union's alignment (the most-aligned
member's), and every member sits at offset 0. It is declared like a struct and
its members are read and written with the same `.`/`->` access:

```c
union value {
    i: int64;
    f: float64;
    b: uint8[8];
}

fn main() -> int32 {
    let v: union value;
    v.i = 42;                  // write one member
    return (v.i & 0xFF) as int32;
}
```

Writing one member and reading another reinterprets the same bytes. In mcc
this is **defined behavior**: a cross-member read is a byte reinterpretation
of the shared storage (deliberate type punning), with the bytes falling where
the platform's endianness puts them. That, and matching C's layout for interop
(`epoll_data`, `sigval`, and many syscall structs embed a union), is what
unions are for:

```c
fn float_bits(x: float64) -> int64 {
    let v = value { f = x };
    return v.i;                // 1.0 reads back as 0x3FF0000000000000
}
```

A union literal (`value { f = 1.0 }`, with the `union` keyword optional like
`struct`) zero-fills the storage first and sets **at most one** member, the
live one; `value { }` is all zeroes. Whole-union assignment copies all the
bytes. `sizeof`, `alignof`, and `offsetof` work as expected (`offsetof` is 0
for every member), and unions nest freely: a union can hold structs and
arrays, and a struct can hold unions.

Unions are generic like structs (`union boxed<T> { typed: T; raw: uint64; }`,
one instantiation per type argument) and take the same `@packed` (alignment
1), `@align(N)`, and `@volatile` annotations. A `const` union parameter passes
by hidden reference like a `const` struct. What a union does **not** take are
the struct-only forms: `extends` (in either direction), member defaults
(`m: T = v`), and flexible array members are all rejected.

A union literal may also initialize a `@static` global, folded to a data
constant at compile time (like a [struct-literal global](#structs)):

```c
@static let whole: union value = value { i = 42 };   // the widest member
@static let bits:  union value = value { b = [1, 0, 0, 0, 0, 0, 0, 0] };
@static let blank: union value = value { };          // all zeroes
```

The written member need not be the widest one: the constant is sized to the
whole union, the member's bytes first and the rest zero — the same storage the
runtime literal produces.

Like a by-value struct, a by-value union is not
[C-ABI compatible](../README.md#c-abi-compatibility) across the C boundary
yet; pass a pointer to it instead, as C interop code does anyway. See
[examples/types/unions.mc](../examples/types/unions.mc).

## Tuples

`tuple<A, B, ...>` is a builtin **heterogeneous, fixed-arity product**: each
position keeps its own statically-known type, accessed by position with the
existing index syntax. It is an ad-hoc [struct](#structs) without a name —
positions instead of field names, the same layout the struct with those field
types would have — for grouping a few values without declaring a one-off
struct. Its headline job is **multiple return values**:

```c
fn divmod(a: int32, b: int32) -> tuple<int32, int32> {
    return (a / b, a % b);           // built by the paren literal
}

fn main() -> int32 {
    let t = divmod(7, 2);
    return t[0] * 10 + t[1];         // 31: quotient and remainder by position
}
```

A tuple is **constructed by the paren literal** — a parenthesized expression
with a top-level comma; `(x)` stays plain grouping, so the 1-tuple spells
with a trailing comma, `(x,)`, and `()` is the empty tuple. A trailing comma
is allowed, as in array and struct literals. In a tuple-typed position
(a typed `let`, assignment, `return`, argument, element, or field) each
element lowers against its position's type exactly like a
[struct-literal](#structs) field: untyped constants adapt, and a string or
array literal in a slice-typed position borrows with no `as`. With no context
the literal fixes its own type, untyped integers anchoring to their `int32`
default. The uninitialized `let t: tuple<A, B>;` declares like a struct:

```c
let t: tuple<int64, int64> = (1, 2);            // elements coerce per position
let u = (10, 'x');                              // inferred: tuple<int32, char>
let v: tuple<slice<const char>, int32> = ("hi", 2);   // "hi" borrows in place
let w: tuple<int32, int32>;                     // declared, filled later
let grid: tuple<int32, int32>[2] = [(1, 2), (3, 4)];  // elements adapt too
```

**Indexing is compile-time only**: `t[n]` requires `n` to fold to a constant
(each position has its own type, so a runtime index would have no single
result type) and is bounds-checked at compile time — `t[2]` on a two-tuple is
an error, not UB. Elements are lvalues, so reads, writes, compound
assignment, and nesting all go by position (`t[1][0]`), and `&t[0]` follows
the same rules as a struct field's address.

**Slicing is compile-time too**: `t[n:m]` narrows to the smaller tuple of
positions `n` to `m-1` — the same half-open `[a:b]` grammar as
[sub-slicing](#sub-slicing), open ends included (`t[1:]`, `t[:2]`, and the
plain copy `t[:]`), each omitted bound folding against the arity. Unlike a
sub-slice, the result is **a new tuple value, not a view**: the kept
positions are copied (the narrowed type could not alias the source layout
anyway), so a tuple slice is never a write target — `t[0:2] = ...` is not an
assignment. Bounds must fold to constants for the same reason indices must
(they pick the result type) and are checked at compile time:
`0 <= n <= m <= arity`. The result may keep any number of positions —
`t[1:]` on a pair is the 1-tuple tail, and `t[n:n]` the empty tuple. Slicing
composes with indexing and with itself:

```c
let t = (1, 'x', 2.5, 4);
let mid  = t[1:3];          // tuple<char, float64>, values copied out
let tail = t[1:];           // tuple<char, float64, int32>
let c    = t[1:3][0];       // 'x': slice, then index the result
let u    = divmod(7, 2)[:]; // an rvalue base slices too
```

**`len(t)` is the arity** — the same builtin that counts an
[array](#arrays)'s elements, and the same kind of compile-time,
context-adapting constant: `len(())` is `0`, `len((x,))` is `1`. Arity is
purely a property of the type, so an rvalue operand needs no address —
`len(divmod(7, 2))` is `2` (the call still runs for its effects) — and
`len` folds in constant expressions, composing with the constant bounds
above: `t[len(t) - 1]` is the last position, `t[1:len(t)]` the tail.

**Destructuring binds positions to names** — no parens, one binder per
position: `let a, b = t;` declares `a` and `b` as ordinary locals holding
`t[0]` and `t[1]`. A trailing-`...` **rest binder** takes the tail instead:
`let a, rest... = t;` is `a = t[0]`, `rest = t[1:]` — the slice above, so the
tail is a **copied** smaller tuple, narrowing uniformly all the way down (on
a pair the tail is the 1-tuple, on a 1-tuple it is `tuple<>`, and a lone
`let rest... = t;` is the whole copy). Pure sugar over the constant indexing
and slicing, with their rules carried verbatim: the source evaluates once
(`let q, r = divmod(9, 4);` calls `divmod` a single time — the headline,
multiple return values bound by name at the call site), each binder takes its
position's type (annotations are rejected; a nested tuple binds whole as one
value), and binders are fresh locals, so reassigning one never touches a
`const` source. The binder count is checked against the arity: exactly equal
without a rest binder, at most the arity with one (the tail may be empty).
The same rest binder applies to [slices](#sub-slicing), where the tail is a
view of the source's storage rather than a copy.

```c
let q, r = divmod(9, 4);        // q = 2, r = 1
let t = (1, 'x', 2.5);
let a, rest... = t;             // a: int32, rest: tuple<char, float64> (a copy)
let x, y, z = t;                // no rest binder: one binder per position
```

Everything else rides the struct machinery: whole-value assignment copies,
tuples pass and return by value, a `const tuple<...>` parameter travels by
hidden reference (elements then read-only), `&` parameters lend the
caller's storage, `sizeof`/`alignof` report the struct layout (padding
included), tuples nest in arrays, structs, and other tuples, and generic
inference recurses through the shape (`fn fst<A, B>(t: tuple<A, B>) -> A`).
Two same-shape tuples are the **same type**, across functions and modules —
interned structurally, like `slice<T>` — and `.mci` interface stubs render
the type by its canonical spelling. Under [the `any` type](#the-any-type) a
tuple follows the struct rule: it boxes by reference into a `const any` (so
`println(f"{t}")` compiles, rendering the `<tuple<int32, int32>>` fallback),
recovers in a `case type` arm, and an owning `any` of it stays rejected.

**Arity runs all the way down to zero.** `tuple<T>` spells the 1-tuple
(`(x,)` constructs it, `t[0]` reads it), and `tuple<>` the empty tuple: a
zero-sized unit value on the empty-struct precedent — `sizeof` 0, constructed
by `()`, declared, assigned, passed, returned, held in arrays, fields, and
generic arguments, boxed by reference into a `const any`, and matched by a
`case type` arm like any other tuple. Indexing an empty tuple is out of
bounds (it has no positions). The unit is what generic code returning `T`
needs when `T` carries nothing, and it means a future statically-typed
variadic's `T...` expansions need no arity carve-out at all.

**A tuple casts to the layout-equivalent struct, and a struct back to its
tuple.** A tuple has exactly the layout of the struct with its element types
as fields, so the explicit [`as` cast](#casts) converts between the two:
`(a, b) as A` builds an `A` from a tuple — the literal form lowers its
elements against `A`'s field types exactly like a typed `let`, so untyped
constants adapt — and `p as tuple<...>` turns a struct value into its
positional form, which composes with destructuring to consume an existing
struct by position:

```c
struct point { x: int32; y: int32; }

let p = (3, 4) as point;                // the literal adapts to the fields
let d = divmod(7, 2) as point;          // any tuple value converts
let x, y = p as tuple<int32, int32>;    // and back: positional consumption
```

Equivalence is **exact and one level deep**: the same field types in the same
order (field names never matter), compared exactly — a field that is itself a
struct requires the *same* struct type in that position, never a recursively
equivalent tuple — and a `@packed` or `@align(N)` struct is never equivalent,
since its offsets or size diverge from the tuple's. The check runs against
the exact target type only, so two distinct structs still never cast into
each other (the nominal [`extends`](#structs) rule keeps its monopoly on
struct-to-struct casts), and a tuple never converts to another tuple type —
only the literal form adapts. A rejected cast names the first divergence
(position, field, or attribute). The result is a fresh value copy either way,
so casting a `const` source yields an ordinary mutable value, and the cast
chains: `(1, 2) as point as tuple<int32, int32>` round-trips. The empty
tuple and an empty struct convert on the same rule. A tuple in an
[`@extern`](#extern-declarations) signature needs no cast at all — it already
crosses as the layout-equivalent C struct via the existing
[struct ABI classification](#passing-structs-by-value-across-the-c-boundary).

Tuples are **not named types**: `extends tuple<...>` is rejected (declare a
struct to name the shape), and naming a tuple is the
[type alias](#type-aliases)'s job — `type polar = tuple<int64, float64>;`
works anywhere the written type does, the cast target included. `==` stays
rejected as on structs.
See [examples/types/tuples.mc](../examples/types/tuples.mc).

## The any type

`any` is the safe counterpart to a union: a builtin **tagged box** that holds
a value of any (boxable) type together with a compile-time id of that type,
so the live value is recovered checked instead of punned. It is
`{ tag: uint64; payload: 16 bytes, align 8 }` — 24 bytes, the payload sized
so a slice fits by value — and needs no declaration or import.

Values **box implicitly** wherever a typed slot expects an `any`: assignment,
argument passing, `return`, and stores into fields or elements. There is no
cast to write (and `x as any` is in fact rejected — boxing is implicit):

```c
fn describe(a: any) { ... }

fn main() -> int32 {
    let a: any = 5;        // boxes an int32 (untyped literals anchor at
                           // their default, the same rule as inference)
    a = 2.5;               // re-boxes: now a float64
    describe("hi");        // a char* boxes; each pointer type has its own tag
    return 0;
}
```

The by-value boxable set is **primitives, pointers, and slices**. A **struct**
additionally boxes — but only into a `const any` target, **by hidden
reference**: the payload holds a *pointer* to the value's existing storage
(the same convention a `const`/`&` struct parameter already travels through,
[hidden references](#functions-and-methods)), tagged as the struct type itself
(`point`, not `point*`), so `case type` recovers it as a reference with no
copy. The archetypal `const any` position is the `slice<const any>` a
[variadic](#functions-and-methods) collects into, so `println(f"{p}")` boxes
`p` by reference and dispatches it to a user `format(const value: point, …)`
overload. An rvalue struct with no storage of its own (a literal, a function
return) spills to a call-scoped temporary first; a bare variable's storage is
shared directly. Scoping the borrow to a slot that cannot outlive the call is
what keeps it sound, so an **owning** `any` of a struct — a `let a: any = s`,
a `return`, a global — is still rejected (the payload would then hold a borrow
that escapes), as are **unions** (the tag would not name the live member) and
**fixed arrays**; box a pointer explicitly for those (`&s`; for an array,
`&xs[0]`), and the compile error names the escape hatch. An `any` never boxes
another `any` (`any` to `any` is a plain copy), and an [enum](#enums) member
boxes under its underlying type's tag.

The **only** way to recover the value is a checked tag test — the
`case type` type-switch below, or its one-pattern sugar, the
[`with` statement](#the-with-statement) — with no exceptions in the
language, an unchecked `as` unwrap would be either a tag-ignoring pun or a
new trap mechanism, so there is none (and the tag/payload fields are not
readable):

```c
fn show(a: any) {
    case type (a) {
        when int32 n:       println(f"int {n}");
        when float64 f:     println(f"float {f}");
        when char* s:       println(f"string {s}");
        when slice<char> t: println(f"slice of {t.length}");
        when T* ptr:        println(f"pointer {ptr:p}");  // every other boxed pointer
        else:               println("something else");
    }
}
```

It rides the [`case`](#control-flow) statement's shape — the subject is
evaluated once, arms run without fall-through — with the type-mode specifics:

- Each arm **must bind a name**; the binding holds the recovered value,
  typed as the arm's type and scoped to the arm. A **struct** arm (`when
  point p:`) recovers the by-reference box as a read-only (`const`) alias of
  the boxing site's storage — no copy — so passing `p` on to a
  `format(const value: point)` overload shares that same storage again.
- An arm may list **several comma-separated types over one binding** —
  `when int32, int16, int8 n: printf("%d\n", n as int32);` — sharing one
  body. The binding is an implicit generic: the body compiles once per
  listed type with the binding typed as that type (never a union), each copy
  fully type-checked — a listed type for which the body doesn't compile
  (say, a call with no viable overload) is a compile error naming the
  offending type. An explicit list doesn't close the universe, so `else` (or
  later arms) is still required. See
  [examples/types/case_type_groups.mc](../examples/types/case_type_groups.mc).
- An arm may be **generic**: `when T* ptr:` matches every boxed *pointer*
  tag not claimed by an earlier arm (`T` bound to the pointee, the binding
  typed as the pointer), and `when T v:` matches every remaining boxed tag
  (`T` bound to the boxed type itself — pointer tags included, so a lone
  `when T v:` sees `v: char*` with `T = char*`). No new syntax: a bare
  arm-type name that resolves (a builtin, struct, alias, enum, or an
  enclosing generic's active binding — so inside `fn g<T>`, `when T v:`
  stays a concrete arm per instantiation) is a concrete arm, and an
  *unresolved* bare name with at most one `*` introduces an arm-scoped type
  parameter. The accepted trade-off, worth knowing: a typo like
  `when in32 n:` silently becomes a fully generic arm (later arms turning
  unreachable and per-tag type checks usually catch it). The arm is a real
  generic context, monomorphized once per matching tag drawn from the
  **whole program's boxed set** — every type a value actually boxes under
  anywhere — each copy fully type-checked, so
  `when T* ptr: handle(ptr);` dispatches into a generic `handle<T>(p: T*)`
  or an overload set per tag, and a boxed type with no viable overload or
  instantiation is a compile error at the `case type` site naming the
  offending type. Dispatch is **first-match-wins textual order** (`when
  char* s:` ahead of `when T* ptr:` keeps strings out of the fallback); an
  arm a generic arm above it subsumes — any arm after `when T v:`, a
  concrete pointer arm or a second `T*` arm after `when T* ptr:` — is a
  hard unreachable-arm error. One conservatism falls out of the deferred
  compilation: the case is assumed to reach its end, so a value-returning
  function whose every arm and `else` return still needs a statement (e.g.
  a `return`) after the `case type`. See
  [examples/types/generic_case_arms.mc](../examples/types/generic_case_arms.mc).
- `else:` is **mandatory**: the set of types an `any` can hold is open, so a
  type-switch is never exhaustive without it — and it stays required beside
  a trailing `when T v:` arm (a zero-filled `any`, tag 0, matches no arm
  and lands in `else`).
- The subject must be an `any`; an `any*` subject auto-dereferences, like
  member access through a pointer.
- Two arms naming the same type are a compile error — one arm listing a type
  twice included — as is an arm whose type could never box (a union, a fixed
  array, or `when any`). A **struct** arm is live: a struct boxes by reference
  into a `const any`, so `when point p:` recovers it (as a read-only alias of
  the boxing site's storage), even when nothing in view boxes one — it is then
  simply a dead tag.

The tag is the 64-bit FNV-1a hash of the boxed type's canonical name,
computed at compile time — no runtime registry, so tags are deterministic
across separate compilations and `case type` lowers to the same
integer-equality chain as a value `case`. A hash collision between two type
names used in one compilation is astronomically unlikely, and detected: it
fails the compile rather than corrupting a type-switch.

An `any` is an ordinary 24-byte value otherwise: pass and return it by value,
put it in struct fields and arrays (`any[N]`), point at it (`any*`), take
`sizeof(any) == 24`, use it in `.mci` [interfaces](#interface-files). A
global/`@static` `any` also takes a constant **initializer**: the same
compile-time constants a scalar global accepts fold into a constant tagged
box, under the same tags runtime boxing produces — `@static let g: any = 5;`
boxes as int32 (an untyped literal anchors at its placeholder type), a string
literal boxes as `char*`, a constant pointer cast (`0x1000 as uint32*`) under
its own pointer tag. The owning-box rules are unchanged: a struct, union,
array, or bare `null` initializer is rejected exactly as at runtime, and an
*uninitialized* global `any` is zero-filled and matches only `else`. The box
also powers
[native variadic arguments](#native-variadic-arguments): a call's extra
arguments box into a caller-stack `slice<const any>` walked exactly like
`show` above. See [examples/types/any.mc](../examples/types/any.mc).

## The typename builtin

`typename(...)` recovers the **canonical name of a type** as a string. It
mirrors [`sizeof`](#pointers) in every surface respect: the operand is a type
or, as a bare name in scope, a variable (`typename(v)` names `v`'s type; the
operand is never evaluated), and it folds at compile time — the result is an
ordinary rodata string literal, a `char*`, sharing bytes with every other
literal spelling the same characters. Zero runtime machinery, and value-level
by design: the name flows into a variable, a parameter, a struct field, a
`println`, anywhere a string literal can, including a `const` or `@static`
initializer.

```c
println(f"{typename(int64)}");          // int64
println(f"{typename(slice<int32>)}");   // slice<int32>
let x: const float64 = 1.5;
println(f"{typename(x)}");              // float64 — const strips
const NAME = typename(uint8);            // folds like sizeof does
```

The spelling is the compiler's canonical one — the same string the
[`any`](#the-any-type) tags hash, the signature mangles, and the diagnostics
use — so it is deterministic across compilations, and `typename(T)` is
precisely the preimage of a `T` value's tag. Two consequences pin the
details:

- A top-level `const` **strips**, matching what boxing does with tags:
  `typename(const int64)`, or `typename(x)` of a `const int64` variable, is
  `"int64"`.
- `typename(expr)` uses the expression's **static** type: an `any` names as
  `"any"`, never its dynamic type.

In a generic, `typename(T)` resolves per instantiation — monomorphization
gives each copy its own literal. The powerful composition is with the
[generic arms in `case type`](#the-any-type): inside `when T v:` or
`when T* ptr:` the arm is a real generic context, so `typename(T)` names the
dynamic type of the boxed `any` per tag, statically — no descriptors, no
registry:

```c
fn describe(a: any) {
    case type (a) {
        when T v: println(f"a boxed {typename(T)}");
        else:     println("nothing yet");
    }
}
```

See [examples/types/typename.mc](../examples/types/typename.mc).

## Enums

An `enum` is a named set of compile-time constants. The declaration names the
enum, an optional underlying type (defaulting to `int32`), and one or more
members, each with an explicit value:

```c
enum Color: int32 {
    Red   = 0,
    Green = 1,
    Blue  = 2,
}
```

A member is read as `Enum::Member` — `Color::Green` — and folds to a constant
of the underlying type, with no storage emitted (like a [const](#constants)).
The enum's name is also usable as a type, aliasing the underlying type, so it
can annotate variables, parameters, return types, struct fields, and arrays:

```c
fn name_of(c: Color) -> uint8* {
    case (c) {
        when Color::Red:   return "red";
        when Color::Green: return "green";
        else:              return "blue";
    }
}

let palette: Color[3] = [Color::Red, Color::Green, Color::Blue];
```

The underlying type may be any type, and a member's value any constant
expression that resolves to it — so an enum can carry flags, wide values, or
even strings:

```c
enum Flags: uint64 { A = 1 << 0, B = 1 << 1, High = 1 << 40 }
enum Msg:   uint8* { Hi = "hello", Bye = "bye" }
```

A member may reference an earlier member of the same enum (`B = E::A + 1`) or
any other constant already in scope. Members are typed exactly as the
underlying type and do not silently adapt to other types — assign across types
with an explicit [cast](#casts). An enum may be `@private` to its file or
`@static` (file-scoped, so other files may reuse the name), like a struct.

Naming another enum in the `:` slot **derives** from it: the new enum copies
the base's member table and adopts its underlying type, then adds its own
members:

```c
enum x_error:  int32   { SUCCESS = 0, NOT_FOUND = 4 }
enum x_status: x_error { RETRY = 100 }
// x_status::NOT_FOUND resolves, and folds equal to x_error::NOT_FOUND
```

An inherited member resolves through the derived scope anywhere the base's
spelling would, compile-time contexts (`@static_assert` conditions, array
dimensions) included, and a new member may reference an inherited one
(`enum b: a { Y = b::X + 1 }`). Chains are transitive: `enum c: b` where `b`
derives from `a` carries all three member sets. The base must be a previously
declared enum — earlier in the file or in an imported one — and a `@private`
base cannot be extended from another file. Redefining an inherited member in
the derived enum is an error, even with an identical value.

Only a bare, direct enum name in the slot derives. Anything else keeps its
plain meaning as an underlying type, with no member merge: a pointer to an
enum (`enum b: a*`), a `const`-qualified type, or a `type` alias to an enum
(the alias hands over the underlying type, as an alias always has, but no
members). And derivation is compile-time reuse only, with **no new type
safety**: enum values remain transparent integers of the underlying type, so
a derived value is indistinguishable from a base value or a plain integer.
The directional base/derived checking this shape suggests is the separate
nominal-enums item on the [roadmap](../ROADMAP.md#planned). See
[examples/types/derived_enums.mc](../examples/types/derived_enums.mc).

## Error handling

Recoverable errors are values: a dedicated `error` declaration names the
failure causes, and a function that can fail returns a `result` carrying
either its ok value or the error — no exceptions, no unwinding, no hidden
control flow. This is the recoverable complement of the unrecoverable
[`panic`/`assert`](#panic-and-assert) lane.

```c
error my_error {
    NOT_FOUND = "Not Found",
    IO_ERROR  = "I/O Error",
    EXHAUSTED,
}

fn find(key: int32) -> result<int64, my_error> {
    if (key == 0) { return error(my_error::NOT_FOUND); }
    return ok(compute(key));
}
```

### Error declarations

An `error` declaration is enum-like but differs from an [`enum`](#enums) on
every axis that matters for errors:

- **Nominal.** `my_error` is a distinct `int32`-backed type, not a
  transparent integer: no arithmetic, no ordering, and no implicit
  conversion to or from integers — `error(5)` and `return 1;` into a
  `my_error` both reject. Two same-shaped declarations do not mix.
- **Auto-numbered from 1.** Variants take 1, 2, 3, … in declaration order —
  always. Error values are automatic: there is no explicit `= n` form (a
  bare `= <int>` is a compile error), so the values are dense `1..N` and
  **every variant is non-zero by construction**.
- **Zero is the reserved, unnameable no-error state.** It cannot be
  declared, constructed, or named — a function that has no error to report
  returns `ok(...)`. Its only purpose is to make `if (err)` a total check
  once the binding forms land.
- **A variant may carry a display string** — `NOT_FOUND = "Not Found"` — the
  human-facing message [`error_message`](#rendering-error_name-and-error_message)
  renders, stored in the declaration and carried through `.mci` stubs. The
  `=` slot only ever sets a display string (never a value); a display string
  does not affect the numbering.

An error value supports exactly the operations a failure cause needs:
truthiness (`if (err)` tests against the zero state), `==`/`!=` against
values of the same declaration, and `case`:

```c
fn describe(e: my_error) -> int32 {
    if (e) { /* e is some cause, not the zero state */ }
    if (e == my_error::NOT_FOUND) { /* ... */ }
    case (e) {
        when my_error::NOT_FOUND: return 1;
        when my_error::IO_ERROR:  return 2;
        else:                     return 3;
    }
}
```

Reading the numeric value *out* stays an explicit escape — `err as int32`
(or `as bool` for the zero test) — like any other explicit narrowing; no
cast mints an error *from* an integer, which would name a value no variant
declares. Error values are ordinary data otherwise: they pass, return, and
sit in struct fields and arrays. An `error` declaration may be `@private`
or `@static`, like an enum, and travels verbatim through
[interface stubs](#interface-files).

Three spellings share the word and never collide: `error name { ... }` is
the declaration (a contextual introducer, like `type`), `error(e)` the
constructor below, and [`@error(msg)`](#error-directives) the compile-time
directive.

### The result type

`result<T, E>` is a builtin template type (like [`slice`](#slices) and
[`tuple`](#tuples)) carrying **either** the ok value **or** the error —
never both, never neither. `E` must be a declared `error` type; primitives,
structs, and plain enums reject at instantiation. A function that can only
fail returns the one-argument `result<E>` — the language has no `void`
type argument, here or anywhere.

```c
fn read_all(path: char*) -> result<slice<char>, my_error>;
fn flush() -> result<my_error>;
```

The layout is a one-byte tag plus the payload — a union of the two arms
for `result<T, E>` (the size of the larger, at its alignment), `E` directly
for `result<E>` — but the fields are internal: a result exposes **no**
members, no `offsetof`, and no struct-literal construction. It is an
ordinary value in every other way — returned and passed by value, stored,
copied, a struct field, a generic argument (`result<T, E>` participates in
[type-parameter inference](#generics)) — and spells itself
`result<int64, my_error>` in diagnostics and `.mci` stubs. A `const` or
`@static` global cannot hold one (a result is built at runtime).

### Construction: ok() and error()

`ok(v)`, `ok()` (for `result<E>` only), and `error(e)` are the **only**
constructors — there is no implicit coercion between `T`/`E` and
`result<...>` in either direction, so the error path is always visible at
the return site:

```c
fn find(key: int32) -> result<int64, my_error> {
    if (key == 0) { return error(my_error::NOT_FOUND); }
    return ok(compute(key));       // return compute(key); would be an error
}
```

The constructors behave as the builtin functions

```c
fn ok<T, E>(v: T) -> result<T, E>
fn error<T, E>(e: E) -> result<T, E>
```

— the argument fixes one arm and the other is a free type parameter, bound
either by the position that fixes a result type (a `return`, a typed `let`,
an assignment, a function argument, a struct field) or, when the constructor
is an arm of a larger expression, by **unifying with its sibling**. So a
ternary composes with no special handling: the `ok` arm supplies `T`, the
`error` arm supplies `E`, and neither has to be written down.

```c
fn checked(key: int32) -> result<int32, my_error> {
    return key < 0 ? error(my_error::NOT_FOUND) : ok(40 + key);
}
```

When the position *does* fix a result type, the ok value lowers against `T`
exactly like any typed position: untyped constants adapt, a bare struct
literal builds a struct `T`, a string literal borrows into a `slice<char>`
`T`. `error(e)` takes any expression of the declared error type — a member,
a parameter, a stored value — and nothing else.

A constructor that reaches no result type at all is an error (`let r =
ok(5);` needs an annotation; a bare `ok(5);` statement is rejected). And a
ternary whose two arms are the *same* constructor kind leaves one arm with no
source — `cond ? ok(1) : ok(2)` cannot know `E` — so it must be annotated
(`let r: result<int32, my_error> = ...`) or the value lifted out
(`ok(cond ? 1 : 2)`). `ok` and `error` are not keywords: only the call shape
`ok(` / `error(` is claimed, so both names remain ordinary identifiers.

### Consuming a result: the destructure

`let ret, err = f();` splits a `result<T, E>` into its two binders — the
C-flavored check style. Exactly one of the two is "live":

```c
let value, err = find(7);
if (err) { /* value is the zero value of int64 */ }
else     { /* err is the zero no-error state; value is the payload */ }
```

- On success, `ret` is the ok value and `err` is the **reserved zero
  no-error state** — falsy by construction, since every declared variant is
  non-zero — so `if (err)` is a total check for *any* declared error type.
- On failure, `err` is the error and `ret` is the **zero value of `T`**
  (zero-filled, not the stored error's bytes reinterpreted — the lowering
  branches on the tag and never reads the other union arm).

The destructure takes exactly two binders, no rest binder, and no type
annotations (each binder takes its arm's type). The error-only `result<E>`
has no ok value to bind, so it rejects here — the statement-position
`try`/`except` below is its consumer. Tuple and slice
[destructuring](#variables) is unchanged.

### Consuming a result: try ... except

`try` attempts the call that follows and hands its error to the `except`
clause: `try f() except (err) { H } [else { S }]`. The `try` binds the
call chain immediately after it (a unary-level prefix — the handler
belongs to that call, not to any larger expression around it); the binder
is parenthesized and both bodies are braced blocks:

```c
let v = try find(key) except (err) {   // err: the error value, scoped here
    return -1;                         // diverge ...
    // ... or supply a fallback:  emit 0;
} else {
    println(f"found {v}");            // the ok arm; v is in scope
};
// v is also in scope here
```

Where a value escapes — a `let` initializer or a `return` value — the
handler **must diverge** (`return`, `break`, `continue`, a call to a
`@noreturn` function such as `panic`) **or `emit` a fallback** that
coerces to `T` and stands in for the ok value. `emit` inside the handler
targets the `try` expression like a [block expression](#block-expressions)
— nested block expressions inside the handler keep their own `emit`
targets, and inside a [`defer`](#defer) body the handler's `emit` stays
legal (it targets a block opened inside the defer) while a handler that
`return`s falls to the existing defer-escape ban.

The optional `else` block is the **ok arm only** — Python's
`try`/`except`/`else`. On success it runs with the bound value in scope;
on the handler's **emit-fallback path it does not run** (a fallback is not
an ok), but code after the statement does, with the binding set to the
fallback. That corner is the one place the value is live after the
statement without `else` having run:

| Path | handler `H` | `else` `S` | code after |
|---|---|---|---|
| ok | — | runs (`ret` in scope) | runs, `ret` = payload |
| error, `H` diverges | runs | skipped | skipped |
| error, `H` emits | runs | **skipped** | runs, `ret` = fallback |

As a whole **expression statement** nothing escapes, so the handler is
obligation-free — it may fall through ("log and move on"), diverge, or
still `emit` a discarded fallback. This is also the `result<E>` consumer:

```c
try flush() except (err) { println(f"flush failed: {err as int32}"); };
```

(For a `result<E>` there is no ok value, so `emit` rejects inside the
handler, and the `let`/`return` forms reject the call outright.)

Because `try` sits at unary level, a `try ... except` also composes as an
ordinary operand — `let n = 1 + try find(k) except (err) { emit 0; };`
works, with the same diverge-or-emit obligation (an `else` there runs on
the ok arm but binds nothing — only the binding forms have a name to
share). And `except` never appears without its `try`: the un-prefixed
spelling is rejected with the fix
(`except needs try: try f() except (err) { ... }`). One C-classic corner
that buys: in `if (c) try g() except (e) { H } else { S };` the `else`
binds to the inner try's `except` clause (greedy-inner), not to the `if`.

A `try` takes exactly **one of three endings** — the handler above, or the
two below, which complete the production:

| Ending | Spelling | On error |
|---|---|---|
| nothing | `try g()` | propagate: the function returns `error(err)` |
| `??` | `try g() ?? fallback` | discard the error, default to the fallback |
| `except` | `try g() except (err) { H } [else { S }]` | handle it |

### Propagation: bare try

`try g()` with no clause propagates the error up: on failure the enclosing
function returns `error(err)`, so its return type must be a result
carrying the **same** declared error type — `result<T2, E>` for any `T2`,
or `result<E>`. Anything else (including `main`) is a compile error at the
try site naming both types
(`try propagates my_error, but this function returns int32`). There are no
error conversions: mapping to a different error type stays a handler's job
(`except (err) { return error(other_error::WRAPPED); }`).

```c
fn wrap(key: int32) -> result<int32, my_error> {
    let v = try find(key);           // on error: return error(err);
    return ok(v as int32);
}
```

On ok the expression yields `T` and composes as an ordinary operand
(`1 + try g()`). The yield is **not** implicitly wrapped: `return try g();`
in a `-> result<T, E>` function hands a bare `T` where a result is
expected — spell `return ok(try g());`. The error-only `result<E>` has no
value to yield, so bare try over one is statement position only:
`try f();` is the propagate-or-continue consumer (over a `result<T, E>`
the statement form propagates and discards the ok value). And since
propagation returns, a bare try inside a [`defer`](#defer) body is banned
like the `return` it desugars to
(`try propagation inside a defer body cannot exit the enclosing function`).

### Defaulting: the ?? fallback

`try g() ?? fallback` discards the error and supplies a default instead.
The fallback evaluates **lazily** — only on the error path, its side
effects never run on ok — and coerces to `T` (an untyped literal adapts).
Nothing escapes the expression, so the enclosing return type is never
consulted: legal in `main`, in a void function, anywhere. (A `result<E>`
has no ok value to default, so it rejects.)

The right-hand side is a full expression — an identifier, a literal, a
call `h()`, an arithmetic expression, a ternary — or an emit-block, which
may instead diverge:

```c
let v = try find(k) ?? 0;                     // scalar default
let w = try find(k) ?? base * 2;              // computed (see precedence)
let x = try find(k) ?? { warn(); emit 0; };   // do things, then default
let y = try find(k) ?? { return -1; };        // or diverge instead
```

**Precedence.** `??` binds **looser** than the ternary and every binary
operator — it is the lowest-precedence expression form (just above
assignment) — and chains **right**-associatively. So the fallback extends
greedily to the end of the expression, and to operate on the *unwrapped*
value you **parenthesize**:

```c
try g() ?? 2 + 1         // try g() ?? (2 + 1)
try g() ?? c ? a : b     // try g() ?? (c ? a : b)
try g() ?? p ?? q + 1    // try g() ?? (p ?? (q + 1))     -- right-assoc
try g() ?? v > p ?? q    // try g() ?? ((v > p) ?? q)     -- `>` binds first
(try g() ?? 0) + base    // parenthesize to add to the unwrapped value
```

The `??` directly after a bare try operand is always the try's own
fallback clause — structural, by production — and its right-hand side is
that same greedy low-precedence expression, so a trailing `?? q` nests
inside it (`try g() ?? p ?? q` is `try g() ?? (p ?? q)`); the inner `??` is
then the general coalesce, whose arms are reserved today: a result left of
`??` unwraps through `try` (`try f() ?? v`), and the pointer arm (`p ?? q`
null coalescing) arrives with the
[pointer-truthiness roadmap item](../ROADMAP.md#planned). A try takes one
ending only: `try g() ?? v except (err) { ... }` is a parse error.

### The try statement

`try (ret = f()) { B } except (err) { H }` keeps the binding inside a
block: a fresh `ret` (no `let` — the deliberate
[`with`](#the-with-statement) head spelling) scoped to `B`, which runs on
ok; on error `H` runs with `err` bound (scoped to `H`), and the handler is
**obligation-free** — fall through ("log and move on"), diverge, or do
nothing; nothing escapes the statement. There is no `else` arm: the block
already is the no-error arm.

```c
try (v = find(key)) {
    println(f"found {v}");
} except (err) {
    println(f"lookup failed: {err as int32}");
}
// v is not in scope here
```

Statement position disambiguates on the head: `try ( IDENT =` opens the
statement (assignment is not an expression, so the probe is total);
anything else after a statement-position `try` is an expression statement
— `try (r);` propagates a parenthesized operand. Arity 2 only: an
error-only `result<E>` has nothing to bind, so `try f();` or the
statement-position `except` form handle it without the binding.

### Rendering: error_name and error_message

An error carries a *meaning*, and two builtins render it to a `char*` at
runtime:

- **`error_name(err)`** yields the variant's fully qualified name —
  `"my_error::NOT_FOUND"` — the stable, programmatic identity (for logging, for
  a `{}` you drive yourself); the type prefix keeps it unambiguous across
  declarations.
- **`error_message(err)`** yields the variant's declared
  [display string](#error-declarations) when it has one, and falls back to the
  bare variant identifier (`"NOT_FOUND"`) when it does not — so a message is
  never empty for a real variant. It is the human-facing "this is why it
  failed".

```c
let value, err = find(key);
if (err) {
    println(f"{error_name(err)}: {error_message(err)}");
    // e.g.  my_error::NOT_FOUND: Not Found     (PERMISSION, with no display
    //       string, would print  my_error::PERMISSION: PERMISSION)
}
```

The operand must be a declared error value (`error_name(5)` rejects). Both
render through a compiler-synthesized per-declaration lookup keyed on the
error's value, so they cover explicit-valued and gapped variants alike; the
reserved zero no-error state (a destructured `err` on the success path) and
any unreachable value render as the empty string. `error_name` and
`error_message` are not keywords — only the call shape is claimed, so the
names stay ordinary identifiers elsewhere.

Automatic `{}` formatting of an error value — `println(f"{err}")` printing
its name directly — is a separate follow-up: the
[formatted-print](#strings-and-formatting) machinery is a closed set that
cannot yet enumerate user-declared error types, so `error_name` /
`error_message` are the shipped rendering surface (an error value still does
not box into `any`/`{}`). See
[the roadmap](../ROADMAP.md#types-and-generics).

See [examples/types/error_handling.mc](../examples/types/error_handling.mc)
for the full tour.

## Type aliases

`type <name> = <type>;` introduces a name for an existing type, usable anywhere
a type is:

```c
type byte = uint8;
type bytes = uint8*;
type callback = fn(int32, uint8**) -> int32;

struct point { x: int32; y: int32; }
type point_ref = struct point*;
```

An alias is **transparent**, not a new distinct type: `callback` _is_ the
function-pointer type it names, so a `callback` value and a matching
`fn(int32, uint8**) -> int32` value are interchangeable without a cast. Pointer
and array suffixes apply on top of the alias, so with `type bytes = uint8*;`,
`bytes*` is `uint8**`.

`type` is a contextual keyword — it only introduces an alias as a top-level
`type <name> = …`, so it remains usable as an ordinary identifier (a field,
variable, or parameter named `type`). An alias may be `@private` to its file or
`@static` (file-scoped), like a struct or enum; a cyclic alias
(`type a = b; type b = a;`) is an error.

An error inside an alias's target — say the generic struct it names fails to
instantiate — reports the alias by name in its
[instantiation backtrace](#instantiation-backtraces), so a chain through
`string` says `string`.

Transparency extends to [methods](#methods): declaring or calling
`pointf::magnitude` with `type pointf = point<float64>;` is declaring or
calling `point<float64>::magnitude` — see [methods on type aliases and
builtin types](#methods-on-type-aliases-and-builtin-types).

### Generic aliases

A `type` declaration may carry a type-parameter list, naming a *family* of
existing types — a wider generic partially applied, or a comparator shape over
any element:

```c
struct pair<A, B> { first: A; second: B; }

type entry<T> = pair<char*, T>;      // fix the key, leave the value open
type cmp<T>   = fn(T, T) -> bool;    // a comparator over any T
```

A generic alias stays **transparent**: it is a type-level function expanded at
each use, minting no monomorphized artifact of its own. `entry<int32>` *is*
`pair<char*, int32>` — the two spellings share **one** struct instantiation
(expansion happens in the type resolver, before the instantiation cache is
keyed), so a value typed `entry<int32>` and one typed `pair<char*, int32>`
combine without a cast. Everything else follows from transparency: an alias
instantiation works in an `extends` slot, appears as a field inside another
generic, and composes with the outer generic's parameters (`entry<U>` with `U`
the outer parameter).

Arity is checked at the **use site**: a bare `entry` or a wrong argument count
is an error (`type alias 'entry' expects 1 type argument(s), got 0`), replacing
the plain-alias "is not generic" message. The target resolves at the
declaration site with **only the alias's own parameters bound** — the use site
resolves the arguments, then hands over — so an outer generic's same-named
parameter never leaks into the target. The name-based cyclic-alias rule still
holds, so a self-referential generic alias
(`type node<T> = pair<T, node<T>*>;`) is an error; recursive types remain
structs' job, via the self-reference-through-a-pointer rule.

An unused parameter is accepted, as on structs and functions, but is **inert**
where a struct's is not: transparency makes `boxed<bool>` and `boxed<char>` the
*same* type (`type boxed<T> = int32;`), whereas a struct's unused-parameter
instantiations stay nominally distinct.

Alias parameters take [defaults](#type-parameter-defaults) too
(`type record<T = int64> = pair<char*, T>;`), with the same trailing-only,
earlier-parameters-only rules; a fully-defaulted tail may be omitted at the use
site, and a bare defaulted alias name is a complete written type. Parameter
**bounds** do not extend to alias parameters yet — a transparent alias mints no
instance for the eager check to attach to. The `.mci` round-trip renders the
parameter list and stops counting the alias's own parameters as external
references, mirroring structs.

## Imports

`import "file";` at the top of a file compiles another `.mc` file into the
same module. The `.mc` suffix is optional, and a file imported through
several routes (or cyclically) is only loaded once.

Imports resolve relative to the importing file first, then through the
import search path: directories added with `-I`/`--import-path` (in order),
and finally the project's [lib/](../lib/) directory, which is on the path by
default so the [standard library](../lib/README.md) is importable under its
`std/` (mcc modules) and `libc/` (C bindings) prefixes. Pass `--nostdlib` to
leave `lib/` off the path.

```c
import "std/memory";   // found in lib/std via the search path
import "libc/stdio";   // libc bindings, in lib/libc

fn main() -> int32 {
    let p = alloc<int32>(3);   // defined in lib/std/memory.mc
    ...
}
```

`import` copies the imported definitions into the module, much like a C
header. When two separately compiled objects both import the same file —
or instantiate the same generic, such as `alloc<uint8>` — that definition
lands in each object. To keep the linker from rejecting it as a duplicate,
imported and monomorphized-generic definitions are emitted with
`linkonce_odr` linkage so the identical copies merge. The file you compile
directly keeps strong linkage, so a real name clash between two such files
is still a link error.

## Interface files

`mcc src.mc --emit-interface` writes `src.mci`, an importable stub describing
the file's public surface — useful for shipping a precompiled library as an
object plus a thin interface to compile and link against, rather than the full
source:

```sh
mcc mathlib.mc -c                  # the object (mathlib.o), no linking
mcc mathlib.mc --emit-interface    # the interface (mathlib.mci)
```

A consumer `import`s the library and links the object — the object (or an
archive, or `-L dir -lname`) goes straight on the `mcc` command line and is
forwarded to the link (`mcc app.mc mathlib.o`). A bare `import "mathlib";`
resolves to `mathlib.mc` if the source is present, otherwise to `mathlib.mci` --
so the same import works whether you have the sources or just the shipped object
plus its stub. The stub mixes two forms, by whether a declaration carries a
linkable symbol:

- A concrete function becomes a [bodyless `fn` prototype](#bodyless-fn-prototypes)
  — its body lives in the object, reached by the symbol the bare name
  resolves to and called with the mcc convention, so `const`/`&`/`own`
  parameter markers are re-emitted and the passing and ownership contracts
  they imply carry over. A method's [`@override`](#override-a-method) marker
  is re-emitted too: it declares the dispatch relationship, so a consumer's
  closure keeps the family and a call through a base view still [dispatches
  the runtime override](#polymorphic-base-views) — a prototype `@override`
  is exactly this interface spelling (elsewhere a bodyless `@override` is
  rejected, since a Mode-1 replacement needs a body). (A real `@extern`
  declaration in the source stays verbatim — it keeps meaning "C calling
  convention".)
- Types, constants, and generic/`@inline` functions are emitted **in full**:
  the consumer needs their layout, value, or body to type-check and to
  re-instantiate or re-inline them (as C++ keeps templates and `inline` in
  headers).

The stub is the public surface plus its **transitive closure**: a `@private`
helper a shipped body or signature reaches is pulled in too — a `@private`
generic called by a public generic travels as source — but keeps its `@private`
marker, so it stays private to the `.mci` (the consumer uses the public API
that needs it without being able to name it). An
[overload set](#function-overloading) always travels whole: an included
function pulls in every same-name sibling, even an unreferenced `@private`
overload, because the importer derives the plain-vs-mangled symbol choice
from the set the stub (plus its import closure) shows it — the stub pins
the symbols its object was compiled with, and a consumer extending the set
never re-mangles them. Unreachable `@private`/`@static`
declarations are dropped, the original `import` lines are preserved (a
dependency's own `.mci` is imported in turn), and `@if` is already resolved, so
each interface matches the target it was generated for.

One thing cannot be expressed and raises an error: a reachable `@static`
concrete function (its symbol is file-local, so no stable name exists to
prototype). Make the helper `@private`, or generic/`@inline` so its body
travels instead.

## Visibility

Everything is public by default. Marking a function or struct `@private`
restricts it to the file that defines it — referencing it from any other
file (however it was imported) is a compile error naming the owning file:

```c
/**
 * Doubles the list's capacity. Internal; called by list<T>::push.
 */
@private
fn list<T>::grow(self: &list<T>) { ... }
```

```
error: line 5: function 'list::grow' is private to list.mc
```

`@static` goes further, like C's `static`: the name is file-scoped rather
than merely access-restricted, so it leaves the global namespace entirely.
Different files can each define their own `@static` function, struct,
generic, or variable with the same name, and a file's `@static` definition
shadows a public one imported from elsewhere. From any other file the name
is simply undefined.

The two differ in what they do to the _name_, not just who may use it. A
`@private` definition keeps its real linker symbol (`helper`) and stays in
the global namespace; `@private` only stops _other files_ from referencing
it. A `@static` definition is renamed to a file-scoped symbol (`helper@file`)
and leaves the global namespace, so several files can each carry their own
`helper` with no clash. For functions there is one softening of the
namespace rule: privacy is judged **per overload** in an
[overload set](#function-overloading), so a `@private` overload is simply
invisible to other files — foreign calls fall through to the members they
can see, and a `@private` member never collides with another module's
distinct-pattern overloads of the same name (its mangled symbol is salted
with the file stem). So reach for `@private` to hide an internal helper
that has a unique name, and for `@static` when you want a hidden helper with
a _common_ name (`init`, `dump`) that several files define independently —
and because a `@static` symbol is file-local rather than global, an unused
out-of-line copy of it can also be dead-stripped. Both compose freely with
[`@inline`](#functions), which is orthogonal: it forces the inlining either
way, and the choice of `@private` vs `@static` only governs the leftover
symbol.

`@static` on a top-level `let` makes a file-scoped variable with its own
storage that persists for the life of the program — a static counter,
buffer, or lookup table. It is zero-initialized unless given an initializer,
which may be any constant expression (a `const`, an `as` cast, `sizeof`,
arithmetic), folded at compile time like a [`const`](#constants) — so a fixed
pointer such as a memory-mapped register address works (see
[Arrays](#arrays) for a static table). With an initializer the type may be
omitted and is inferred from it, like a local `let`; without one (or for
`@extern`) the type is required:

```c
@static let calls: int32;            // starts at 0, kept across calls
@static let lookup: uint8[256];      // a static buffer
const UART_BASE: uint64 = 0x9000000;
@static let uart = UART_BASE as struct pl011*;   // type inferred: struct pl011*

fn next_id() -> int32 { calls = calls + 1; return calls; }
```

## Extern declarations

`@extern` declares a function or global variable that is _defined
elsewhere_ — in libc, or in another object linked into the program. An
extern function gives its signature and ends with `;` instead of a body; an
extern variable is a top-level `let` with a type and no initializer:

```c
@extern
fn atoi(s: uint8*) -> int32;

@extern
let optind: int32;

fn main() -> int32 {
    return atoi("41") + optind;
}
```

A trailing `...` declares a C-style variadic function, such as `printf` or a
kernel's `printk`; extra arguments follow C's promotion rules (small integers
widen to `int32`):

```c
@extern
fn printk(fmt: uint8*, ...);
```

Extern functions cannot be generic. (A `...` is also allowed on functions you
define, which can forward their extra arguments through a `va_list` but not
read them directly — see [Variadic functions](#variadic-functions).) Identical
extern declarations may appear in any number of imported files — they all name the
same symbol — but declarations that disagree about the signature are a
compile error. `@private` applies to extern declarations as usual, and
`@volatile` marks an extern variable whose accesses must not be optimized
away; `@static` cannot be combined with `@extern`, since an external
symbol's name is fixed. (The [libc bindings](#reaching-libc) are exactly
this: files full of predeclared extern functions.)

`@symbol("name")` binds an extern to a linker symbol that differs from its mcc
name — for symbols that aren't valid identifiers, are versioned, or vary by
platform:

```c
@extern @symbol("__stdoutp") let stdout: struct FILE*;   // macOS spelling
@extern @symbol("strlen") fn length(s: uint8*) -> uint64;
```

Code still refers to the declaration by its mcc name (`stdout`, `length`); only
the emitted symbol changes.

### Passing structs by value across the C boundary

An `@extern` function may take or return a `struct` (or `union`) **by value**,
and the call is lowered to the platform C ABI so the aggregate lands where the C
side expects it. This is a property of the `@extern` boundary only: mcc's own
calls keep their raw-aggregate convention (the whole struct as an LLVM
aggregate, with `const`/`&` struct parameters travelling by a
[hidden reference](#reference-parameters)), which is self-consistent but is not the C
ABI. The two conventions stay distinct.

The direction covered is **mcc calling C**. Three platform ABIs are classified,
selected by the target triple: **AArch64 (Apple/AAPCS64)**, **x86-64 System V**,
and **x86-64 Windows (Win64)**.

On **AArch64/AAPCS64** an aggregate crossing the boundary is passed as:

- **FP registers** when it is a homogeneous float aggregate — every member
  (recursively, through nested structs and arrays) the same floating type, one
  to four in all. mcc has only `float64`, so this is 1–4 `double`s, e.g. a
  `{ x: float64; y: float64; }` point.
- **General-purpose registers** otherwise, when it is 16 bytes or less (one
  register up to 8 bytes, a pair up to 16). A `union` is always classified this
  way — it is never a float aggregate.
- **Indirectly** when it is larger than 16 bytes: an argument is passed as a
  pointer to a caller-owned copy, and a return is written through a hidden
  pointer the caller allocates (the C `sret` convention), so the function itself
  returns nothing.

```c
struct div_t { quot: int32; rem: int32; }          // 8 bytes → one register
@extern @symbol("div") fn c_div(n: int32, d: int32) -> struct div_t;

fn main() -> int32 {
    let r: struct div_t = c_div(17, 5);             // quot = 3, rem = 2
    return r.quot * 10 + r.rem;
}
```

On **x86-64 System V** an aggregate of 16 bytes or less is split into eight-byte
chunks and each chunk classified: a chunk holding only floating fields rides in
an SSE register (coerced to `double`), any other chunk in a general-purpose
register (coerced to `i64`). So `{ x: float64; y: float64; }` comes back in two
SSE registers, while a `{ a: int32; b: float64; }` uses one GPR and one SSE. An
aggregate over 16 bytes is passed **`byval`** — copied onto the argument stack —
and a return over 16 bytes uses `sret`. Unlike AArch64, the frontend also tracks
how many argument registers remain: a register-class aggregate that no longer
fits the remaining registers is demoted whole to a `byval` memory argument
(never split half-in-register, half-on-stack), matching the C compiler.

On **x86-64 Windows (Win64)** an aggregate whose size is exactly 1, 2, 4, or 8
bytes rides in a single integer register (Win64 gives aggregates no SSE, so even
a float-only struct uses a GPR); any other size is passed indirectly (a pointer
to a caller copy for an argument, `sret` for a return larger than 8 bytes).

`@packed` and `@align` are honored on every target (they change the size and
field offsets the rules see; a genuinely unaligned aggregate falls back to a
memory argument rather than being miscompiled). On an unsupported target
(riscv64, or an unknown triple) an `@extern` that passes or returns a struct by
value is a compile error —

```
example.mc: error: line 2: passing a struct by value across the C boundary is not supported on target 'riscv64-unknown-linux' yet; pass a pointer instead
```

— so pass a pointer (`struct point*`) there instead. The AArch64 and System V
classifications are verified end-to-end against a linked C fixture in the test
suite; Win64, which has no CI runner, is verified by IR shape only. See
[c_struct_abi.mc](../examples/systems/c_struct_abi.mc) for a runnable example.

### Bodyless fn prototypes

A plain `fn` may also end with `;` instead of a body. Where `@extern` means
"a symbol with the **C** calling convention", a bodyless prototype means "a
concrete **mcc** function defined in another object" — the call uses the mcc
convention, so `const` struct and `&` parameters keep their
[hidden-reference passing](#reference-parameters), which `@extern` deliberately
rejects:

```c
fn bump(n: &int32);                  // defined in a linked object
fn total(const p: struct pair) -> int64;

fn main() -> int32 {
    let x: int32 = 40;
    bump(x);            // the hidden reference reaches the definition
    ...
}
```

Every signature marker (`const`, `&`, `@noalias`, `@nonnull`, and a
[`-> &` return](#reference-returns)) means exactly
what it does on a definition, and the prototype must match the definition's
signature — the convention is derived from the signature on each side
independently. Generic, `@inline`, `@asm`, and `@static` functions cannot be
prototypes (their body or symbol cannot live elsewhere).

A prototype is also a **forward declaration**: when a matching definition
appears in the same program — same file or through an import — the prototype
is checked against the definition and discarded, and the definition supplies
the body. Pairing is **per signature**: the parameter list selects the
declaration a prototype pairs with, so under
[function overloading](#function-overloading) a prototype with a different
parameter list is not a mismatch — it joins the name's overload set as its
own member. Identical prototypes collapse onto one declaration (like repeated
`@extern` declarations), and a prototype arriving after its definition is
discarded the same way. Matching within one signature is strict: the return
type (its `&` marker included), the derived `const`-struct/`&`
hidden-reference positions, the
`@noalias` and `@nonnull` markers, and the `@private` flag must all agree —
parameter names
may differ, and an `@inline` definition never pairs with a prototype (a
prototype cannot promise a body that travels). A mismatch is an error at the
second declaration, with a note citing the first:

```
mathlib.mc: error: line 12: definition of 'add' does not match its prototype
mathlib.mci: note: line 3: previous declaration of 'add' is here
```

Only prototypes pair this way. A second *definition* of the same signature
stays a duplicate-definition error, and so does a prototype against an
`@extern` declaration (a different calling convention) or an
[`@removed` tombstone](#removed-functions). A prototype never pairs with a
generic template either — beside a same-module template it is a
[mixed-set](#function-overloading) member of its own; a cross-module
template stays a collision. When a pair carries
[`@deprecated`](#deprecated-functions), the definition wins: its message —
or its absence — replaces the prototype's.

You rarely write one by hand: [interface files](#interface-files) emit
prototypes for a library's concrete functions, and the matching object
supplies the definitions at link time. Against a genuine C function, prefer
`@extern` — a plain prototype happens to match only while the signature has
no hidden-reference parameters, and nothing checks that it stays that way.

## Strings

`char` is a distinct one-byte **text** type: ABI-identical to `uint8` (an
unsigned byte), but a separate type, so a NUL-terminated string is told apart
from a raw byte buffer. A string literal is a NUL-terminated [array](#arrays)
`char[N]`, where `N` counts the trailing NUL (`"hi"` is `char[3]`). Stored in a
constant, the bytes stay a valid C string, so the array **decays to a `char*`**
wherever a pointer is used — passed to a function, returned, compared, or indexed
(`"hi"[0]` is `104`) — just like any other array. `char*` coerces to `uint8*`
like any pointer, so the libc string functions still take a string literal
directly. Literals support C's simple escape sequences — `\a` `\b` `\f` `\n` `\r`
`\t` `\v`, the quotes `\'` `\"`, `\\`, `\?`, and `\0` for NUL — plus `\e` for ESC
(a GCC/Clang extension, handy for ANSI terminal codes). Any other escape keeps
the bare character.

Because the literal carries its array type, the choice at a `let` is yours:

```c
let owned = "hi";              // char[3]: an owned, mutable copy of the bytes
let owned2: char[] = "hi";     // same, size inferred from the literal
let buf: char[8] = "hi";       // a larger owned buffer, zero-filled past "hi\0"
let p: char* = "hi";           // decays: a pointer into the shared constant (no copy)
let raw: uint8[] = "hi";       // also fine: the same bytes, as a raw buffer
```

An owned `char[N]` binding can be mutated (`owned[0] = 'H'`), measured with
[`len`](#arrays) (which counts the NUL — `len(owned)` is `3`), and
[borrowed](#slices) as a `slice<char>`. A `char[N]` is NUL-terminated text, so
the borrow **drops the terminator**: the slice spans the text, with `length` one
less than `len` (`"hi" as slice<char>` has `length == 2`, the buffer keeps its
NUL). A `uint8[N]`, by contrast, is a raw byte buffer: its `slice<uint8>` keeps
**every** byte (`['a','b','c'] as a uint8[3]` borrows to `length == 3`). The
NUL presumption is a property of the named `char[N]` *array*: a char [array
literal](#slices) borrowed directly (`['h','i'] as slice<char>`) has no NUL and
keeps its exact count, `length == 2`. The
`char*` form keeps the pointer-to-constant behavior. An explicit `char[M]`/
`uint8[M]` must be large enough to hold the bytes (NUL included). A string
literal can also be borrowed directly — `"hi" as slice<char>` — since it carries
its array type, and it **adapts** to a `slice<char>`/`slice<const char>` from
context with no `as` at all (the way an untyped constant takes its type): at a
function argument (including a `const`-by-reference slice parameter, so
`writeln("hi")` works), a `let` slot, a `return`, an **array element** whose
element type is a char slice (`let dirs: slice<char>[2] = ["bin", "usr/bin"];`,
including nested literals), a **[struct-literal](#structs) field** whose type is
a char slice (`cmd { name = "ls" }`; see [Slices](#slices) for the shared rules,
including how a literal field stays out of a generic struct's inference), or a
`@static` initializer — the
scalar `@static let g: slice<const char> = "hi";` and a `@static` array of
slices both become constant `{pointer, length}` views into the string constants
(safe: the pointee is a global constant, so there is no lifetime question). A
[ternary](#operators) whose arms are all string literals adapts as a whole —
`writeln(flag ? "y" : "yes")` — each arm borrowing in its own branch (except in
a `@static` initializer, which needs a single constant view). The
borrow drops the NUL, and only *literals* adapt — a typed owned value still
needs the explicit `as`.
See [examples/types/strings.mc](../examples/types/strings.mc) and
[examples/types/string_tables.mc](../examples/types/string_tables.mc).

A character literal in single quotes is a `char` — the byte value of a single
character, using the same escapes (`'a'`, `'\n'`, `'\0'`, `'\''`, `'\\'`). Like
an integer literal, it is an untyped constant: it **defaults to `char`** but
adapts to a `uint8`/integer slot from context (`let b: uint8 = 'a';` is fine). A
`char` *value*, though, stays distinct — converting it to `uint8` (or back) needs
an explicit `as`. Being a one-byte text value, `char` indexes, compares, and does
arithmetic:

```c
fn digit_value(c: char) -> char {
    return c - '0';      // '7' - '0' == 7
}
```

## Formatting

`import "std/format";` provides the **formatting protocol**: one
[overload set](#function-overloading),

```c
format(str: &string, value: X, const modifier: slice<char>)
```

where every member appends `value`'s rendering to a
[`string`](../lib/std/string.mc) and `modifier` steers the spelling (an empty
string picks the default). Because the modifier is a `slice<char>`, a bare
string literal adapts to it at the call, so modifiers are written inline.
The baseline members cover the built-in types:

- **Signed integers**: decimal. One [closed-group](#closed-type-groups)
  template takes `int32 | int16 | int8` and sign-extends into the concrete
  `int64` worker, so `-4` renders `-4` at every width.
- **Unsigned integers**: unsigned decimal, a `uint32 | uint16 | uint8`
  group widening into the concrete `uint64` digit worker every integer
  member funnels into.
- Integer modifiers, grammar `[0][width][x|X|b|p]`: the final letter picks
  the base — `"x"` lowercase hex, `"X"` uppercase hex, `"b"` binary, `"p"`
  pointer-style (`0x2a`) — an optional decimal width pads the rendering,
  and a leading `0` selects zero-padding (`"8x"` gives `      ff`, `"08x"`
  gives `000000ff`). A space width counts the whole field, a zero width
  the digits alone — the sign and `0x` sit outside the zeros, so `-42`
  under `"08p"` is `-0x0000002a`. A negative value renders
  sign-and-magnitude — the base applies to `|value|`, so `-4` with `"x"`
  is `-4` (render a two's-complement bit pattern by casting the bits
  unsigned first), and `int64`'s minimum renders exactly. In a
  `print`/`println` *literal* a bare all-digit width is spelled `{:6}` —
  a bare `{6}` selects an argument (see
  [positional placeholders](#formatted-print--println)).
- **`float64`**: fixed-point (`3.5` renders `3.500000`). The modifier
  grammar is `[[N].M]f`: `.M` rounds to M decimals (`".2f"` gives `3.50`,
  `".0f"` drops the point entirely), and an optional leading width N
  space-pads the whole field, sign included, right-aligned (`"8.2f"`
  gives `    3.50`). A bare `"f"` (or no modifier) renders the
  six-decimal default. The rendering is snprintf's `%*.*f`, so the
  rounding is the C library's.
- **`bool`**: `true`/`false`; `"y"` renders `y`/`n`, and `"yes"` renders
  `yes`/`no`.
- **`char`**, **`char*`**, **`slice<char>`**: appended as text. A string
  literal decays to `char*` and lands on that member, and a null `char*`
  renders `(null)`. The string members take a field-width modifier,
  grammar `[N][s][N]`: digits before the `s` right-align the text in an
  N-wide field (`"20s"`, or a bare `"20"` — spelled `{:20}` in a
  `print`/`println` literal), digits after it left-align
  (`"s20"`); text at or past the width appends unpadded. A single `char`
  ignores the modifier.
- **`slice<char*>`**: a quoted, bracketed list of the C strings,
  `["ls", "cat"]` (the modifier is ignored; elements must not be null).
  Being concrete, this member beats the generic `slice<T>` one below, so a
  bare `slice<char*>` no longer falls to the list-renderer (which would
  render the elements unquoted through the `char*` member).
- **`slice<T>`**: a bracketed list, `[1, 2, 3]`. Each element formats back
  through the overload set, so the modifier applies per element
  (`"x"` gives `[a, ff]`), nesting recurses (`slice<slice<int32>>` renders
  `[[1, 2], [3]]`), and `slice<char>` / `slice<char*>` never land here
  (their concrete members above win).
- **Everything else**: an unbounded `format<T>` fallback renders the type's
  name in angle brackets (`<uint8*>`) instead of a value.

```c
import "std/format";
import "std/string";

let s = string();
format(s, 255 as int32, "x");    // s is now "ff" — the literal adapts
```

One sharp edge, a consequence of open overloading: an untyped integer
literal is ambiguous among the integer and `char` members, so type the
value (`42 as int32`).

Overload sets are open, so **making your own type printable is writing one
`format` overload for it in your own module**: a concrete member outranks
the closed-group templates and the unbounded fallback, and it may recurse
back into the set for its fields:

```c
struct point { x: int32; y: int32; }

fn format(str: &string, value: struct point*, const modifier: slice<char>) {
    str.push('(');
    format(str, value->x, modifier);
    str.append(", ");
    format(str, value->y, modifier);
    str.push(')');
}
```

See [examples/systems/formatting.mc](../examples/systems/formatting.mc).
The protocol's consumers are just below — an f-string's `{expr}` holes,
`"...".format(args)`'s `{}` placeholders, and positional `{n}` selection
all render through this one set.

### Formatted print / println

`std/io`'s `print` / `println` **write a string** — verbatim, braces are
not placeholders, so runtime text is always safe to print. Formatting
belongs to the *producers*: an f-string renders its holes through the
`format` overload set, and `"...".format(args)` (the
[`slice::format`](#strings) method from `std/slice`) is the explicit
renderer behind it:

```c
import "std/io";

println(f"{x} + {y} = {x + y}");           // f-string interpolation (below)
println("mask {x}, ok {yes}".format(255, true));  // mask ff, ok yes
println(name);                             // a string/slice value, verbatim
println(stderr!, f"bad input: {arg}");     // any FILE* stream
```

`print`/`println` are exactly two overloads each — `print(str)` and
`print(f, str)` — one signature apiece: the string parameter is a single
`T extends slice<const char>`, whose [const-covariant bound](#bounds)
takes the whole char-run family with no `as` at the call site — a string
literal, a `slice<char>` or `slice<const char>`, an owned `string` or
`list<char>`, and with those an f-string or a `.format(...)` call's
rendering (the owned temporary is
[destroyed at statement end](#move-out-returns-own)). There are no
`println(fmt, args...)` overloads: formatting always goes through an
`@format` collector such as `slice::format`, never through the printer
itself.

In a format string, each `{[modifiers]}` placeholder renders the next
argument through the `format` overload set, passing the bracket content
verbatim as the per-type modifier — `{}` is the default rendering,
`{x}`/`{X}`/`{p}` steer integers, `{y}`/`{yes}` steer bools, and a
modifier applies per element on slices; in an f-string the same modifiers
ride after the hole's `:` (`f"{n:08x}"`). `{{` and `}}` print literal
braces. Because dispatch is the open overload set, a `format` overload you
write makes your type printable straight through `println(f"{value}")` —
boxed [by reference](#the-any-type) with no copy.

Integer placeholders already carry width and zero-padding — `f"{n:08x}"`,
`"{08x}".format(n)` — via the `[0][width][x|X|b|p]` modifier grammar,
string placeholders carry field widths — `f"{name:s20}"` — via
`[N][s][N]`, and float placeholders carry width and precision —
`f"{f:.2f}"`, `f"{f:8.2f}"` — via `[[N].M]f` (all above). libc's `printf`
remains the tool only for what the modifiers do not carry: scientific
notation (`printf("%g\n", f)` / `%e`).

**Positional placeholders** select an argument manually: in a format
string *literal*, `{n}` renders the n-th collected argument (0-based), and
a `:` separates the index from the modifiers. This is pure compile-time
sugar — the call desugars to the sequential form by duplicating or
reordering the arguments (each argument still evaluates exactly once, in
source order), so the runtime parser stays sequential-only:

```c
println("{0}, {0}!".format("again"));         // again, again!
println("{1} then {0}".format('b', 'a'));     // a then b
println("{0} is {0:x} in hex".format(255));   // {0:x} desugars to {x}
```

One string commits to one placeholder style: mixing automatic `{}` and
positional `{n}` placeholders is a compile error, and in the positional
style an out-of-range index and an argument no placeholder references are
compile errors too. Because an all-digit bracket now selects an argument,
a *bare* field width in a literal is spelled with the index-less escape
`{:N}` — `"{:2}".format(n)` desugars to the runtime `{2}` width — while
digit-leading modifiers with a base letter (`{06x}`) stay plain modifiers
and `{{`/`}}` still escape literal braces. Only a literal desugars: a
format string arriving through a variable is parsed by the runtime
machinery above, where bracket content is always modifier text (so a
runtime `{2}` is still the field width). The literal may sit behind its
`as slice<const char>` borrow — the dot-call receiver adaptation
synthesizes exactly that — so `"{0}".format(x)` and
`("{0}" as slice<const char>).format(x)` desugar alike.

The hook is the **`@format` parameter attribute**: `std/slice` declares
`fn slice::format(@format const self: slice<const char>, args...)`, and
any collecting function may opt its own format string into the desugar
the same way. `@format` is valid only on the
`slice<const char>` parameter just before the collecting `args...`, and it
travels through [interface stubs](#interface-files) like `@nonnull` does.

**String interpolation (f-strings)** writes the expressions inline: an
`f`-prefixed string literal holds `{expr}` holes. At an `@format` call's
format string it desugars at parse time into the sequential form above —
`logf(f"x = {x}")` splices like `logf("x = {}", x)` for your own `@format`
collector — and anywhere else it renders into a string value (below), so
`println(f"x = {x}")` is `println("x = {}".format(x))`. The prefix is what keeps the two brace
grammars apart: in a plain literal `{x}` is the runtime *modifier* (hex
the next argument), in an f-string it is the *expression* `x` —
`"{x}".format(x)` and `f"{x}"` are both meaningful. A `:` after the
expression carries a runtime modifier through (the hole is parsed first
and only a *leftover* colon starts the modifier, so a ternary's own colon
stays inside the expression), and the inspector form `f"{n=}"` — Python's
spelling, Python's semantics — prints the expression's verbatim source
text, whitespace and all, ahead of its value, with a modifier composing
after the `=`:

```c
let x = 255 as int32;
println(f"x is {x}, hex {x:08x}");  // x is 255, hex 000000ff
println(f"{x=}");                   // x=255
println(f"{x = }");                 // x = 255   (the spacing is yours)
println(f"{x=:08x}");               // x=000000ff
println(f"{x > 9 ? x : 0}");        // 255 — any expression goes
```

An f-string is its own placeholder style: every hole is an expression, so
it never mixes with the automatic `{}` or positional `{n}` forms, and
passing extra arguments after one at an `@format` slot is a compile error
— `logf(f"{x}", y)` has no placeholder left for `y` (at a verbatim callee
the mismatch is the ordinary no-overload error: `println(f"{x}", y)` finds
no `println(string, int32)`). `{{` and `}}` still escape literal braces
(braces inside a hole's nested string/char literals need no escape), an
empty hole `{}`, a bare `{:mods}`, and a stray or unclosed brace are
compile errors. A hole-free `f"..."` (only plain text or escaped braces,
e.g. `f"{{}}"`) keeps its f-string identity rather than degrading to a
plain literal — its escapes still collapse (`println(f"{{}}")` prints
`{}`), while the plain literal `println("{{}}")` goes out verbatim
(prints `{{}}`, as it always did), the way every plain string does.

**String-valued f-strings.** At the format string of an `@format` call
(`"...".format(...)`'s own receiver aside, your collector's `@format`
slot) the literal splices at compile time — zero-cost, and injection-free,
since the hole *values* are never re-scanned for braces. Everywhere else an f-string is a
runtime string **value**: it desugars to a synthesized
[`slice::format`](#strings) call (`f"x = {x}"` is `"x = {}".format(x)`),
rendering into an [`-> own string`](#move-out-returns-own) that flows like
any other owned value — this is what `println(f"...")` binds —

```c
let s = f"x is {x}, hex {x:08x}";  // the let adopts: destroyed at scope end
let t: string = f"{n=}";           // a typed string let adopts the same way
take(f"{x}");                      // an argument: the temporary drops at
                                   // statement end, after the callee returns
if (f"{x}".equals(s)) { ... }      // chains; the temporary drops at chain end
return f"hello {name}!";           // transfers out of an `-> own string` fn
```

The rendering needs `slice::format` in the import graph (`import
"std/slice";` — `std/io` pulls it in transitively); without it the compile
error names the import. A hole's own temporaries (`f"{make()}"`) drop at
statement end like any collected argument's, on both the splice and value
paths. There is no implicit string-to-slice coercion, so a concrete
`slice<const char>` position reports the honest mismatch — borrow
explicitly (`f"{x}" as slice<const char>`, which leaks the rendering like
any own call's `as` borrow) or bind a `let` first. Two positions a runtime
value can never fill stay compile errors: a compile-time constant (a
`const`, a global/`@static` initializer) and in-place addressing
(`len(f"...")`, `&`).

In an [overload set](#function-overloading) the **format slot wins**: a
candidate that splices the f-string in its `@format` format-string slot
filters the set before ranking, and the usual plain-literal rank runs
among those survivors — with `fn logf(msg: slice<const char>)` beside
`fn logf(@format const fmt: slice<const char>, args...)`,
`logf(f"x = {x}")` resolves to the collector even though the equivalent
plain literal would pick the verbatim member. Only when *no* candidate can
splice it does the f-string render as a value and resolution re-run over
the owned string (so `println(f"{x}")` and `panic(f"{x}")` bind the
writers' string-taking signatures). A trailing collected f-string after a
plain format string (`logf("{}", f"...")`) is an ordinary value argument:
it renders and formats as its text.

See [examples/systems/formatting.mc](../examples/systems/formatting.mc) —
the positional and f-string demos are its finale — and
[examples/types/fstring_values.mc](../examples/types/fstring_values.mc),
which walks the string-value ownership story with drop-stamped hole
temporaries.

### Panic and assert

`std/io` packages the [`@noreturn`](#noreturn-functions) guard pattern:
`panic(msg)` writes its message to standard error and aborts the process
(`abort()`, so SIGABRT — exit status 134 under a shell), and
`assert(cond, msg)` panics with `assertion failed: <message>` when its
condition is false, doing nothing otherwise:

```c
import "std/io";

fn first(p: int32*) -> int32 {
    if (p == null) {
        panic("first(): null input");   // diverges: narrows p below
    }
    return *p;                          // proven non-null here
}

fn main() -> int32 {
    let x = 41 as int32;
    assert(x > 0, "x must be positive");
    println(f"first(&x) = {first(&x)}");
    panic(f"x = {x}, giving up");       // formats explicitly — see below
}
```

Each is **one member**, and the message is **verbatim** — braces are not
placeholders, so runtime text always passes through safely (there is no
`panic(fmt, args...)` collector). Like `print`/`println`, the message
parameter is a single `T extends slice<const char>` with the
[const-covariant bound](#bounds), so a literal, a slice of either
constness, an owned `string` — and with it an f-string or `.format(...)`
rendering — all bind with no `as` at the call site.

**The blessed panic style is a plain verbatim message.** `panic(f"x =
{x}")` compiles — the f-string renders to an owned string like anywhere
else — but the rendering is **never destroyed**: `panic` diverges, so the
statement-end drop that would normally clean up an argument temporary
never runs, and the allocation leaks on the dying path. (The removed
`@format` collector always leaked the same allocation, invisibly, inside
its own body; the value spelling just makes the cost explicit at the call
site.) The process is aborting, so the leak is harmless by construction —
but format on the way down only when the dynamic value genuinely earns its
place in the message. The leak is not just documented but *diagnosable*:
the opt-in [`-Wnoreturn-own`](#-wnoreturn-own) class reports every own
value handed to a `@noreturn` callee. An `assert` message evaluates
whether or not the condition holds: a passing assert's rendered message
*is* destroyed at statement end (and never warns — `assert` returns), a
failing one aborts mid-statement and leaks it — prefer plain messages on
hot paths.

The details:

- **`panic` is `@noreturn`**, so a call diverges like a `return`: it
  satisfies missing-return analysis as a block's final statement, and the
  `if (p == null) { panic("..."); }` guard body narrows `p` for the rest
  of the scope, like any diverging guard.
- **`assert` does not narrow.** `assert(p != null, "...")` compiles, but
  facts do not flow through a call, so `p` stays unproven after it — the
  narrowing idiom remains the `panic` guard above.
- **Termination is `abort()`**, never `exit`: SIGABRT traps in a debugger
  and can leave a core dump, and no atexit handlers run mid-panic.
  [Defers](#defer) do not run on the panic path (a `@noreturn` call is not
  a block exit), but pending standard *output* is flushed first, so
  interleaved program output survives the abort.
- **`assert` is always enabled** — its condition and message evaluate
  whether or not the assertion holds, and there is no release-stripping
  mode yet (a `-D`-gated variant is a roadmap follow-up).

See [examples/functions/panic_assert.mc](../examples/functions/panic_assert.mc).

## Reaching libc

To call into the C library, import a binding module from
[lib/libc/](../lib/libc/) — `import "libc/stdio";`, `import "libc/string";`, and
so on. These are ordinary [`@extern` declarations](#extern-declarations) for the
C functions, covering most of the standard headers (the `printf`/`scanf`
families, the `str*`/`mem*` functions, `malloc`/`qsort`/`strtol`, `FILE*`
streams, math, time, errno, …); see the
[standard library index](../lib/README.md) for the full list.

```c
import "libc/stdio";
fn main() -> int32 { printf("hello\n"); return 0; }
```

For ordinary output you usually want the [`std`](../README.md#standard-library) `print` /
`println` wrappers rather than `printf` directly; the `libc/` bindings are for
reaching the rest of the C library.

The bindings' null-hostile pointer parameters are marked
[`@nonnull`](#-wextern-nonnull), so a build under `-Wextern-nonnull` enforces
the C contract at every call into them; the default build leaves them
unenforced (a mechanical C port compiles with no flag).

Anything the bindings do not cover, you can [declare yourself](#extern-declarations)
with `@extern`. Variadic arguments to functions like `printf` follow C promotion
rules (small integers are widened to `int32`). The same route reaches any other
C library: declare its functions `@extern` and link it by passing `-l<name>`
(with `-L<dir>` for its search path), or its `.o`/`.a` file directly, on the
`mcc` command line — libc and libm themselves are always linked.

## Inline assembly

`@asm` drops to the underlying machine when no instruction is reachable from
the language — there is nothing higher-level for it. It comes in two forms.

An **`@asm(...)` expression** is an inline-assembly call. The parenthesized
values are the input operands and an optional `-> type` declares the output;
the body is one string literal per instruction (joined with newlines). Inside
the template, `$out` is the output and `$0`, `$1`, … are the inputs in order:

```c
fn add(x: int64, y: int64) -> int64 {
    return @asm(x, y) -> int64 {
        "add $out, $0, $1"
    };
}
```

With no `-> type` it is a void statement (e.g. `@asm() { "nop" };`). Operands
and the output use the general-register class (`r`) and must be integers or
pointers. A register-name modifier may follow in braces and is passed straight
to LLVM — on aarch64 a bare operand is the 64-bit `x` register and `:w` selects
the 32-bit `w` name, exactly as `%w` does in C inline asm:

```c
@asm fn rev32(value: uint32) -> uint32 {
    "rev ${out:w}, ${0:w}"          // w registers; bare $out/$0 would be x
}
```

That second form, **`@asm fn`**, is sugar for a function whose body is a single
`@asm(...)` expression over its parameters: the parameters are the inputs and
the return type is the output. You do _not_ write `ret` — the function's normal
epilogue returns the value.

A **`@clobbers(...)`** clause declares the registers and flags the asm touches
beyond its operands, so the compiler keeps no live values there across it. It
follows `@asm` directly — before the operand list in the expression form, and in
the annotation stack in the `@asm fn` form — and lists `"memory"`, `"cc"`, or
register names (e.g. `"x0"`) as string literals:

```c
// reads through a pointer and orders memory, so it clobbers "memory"
fn load_acquire(addr: int64*) -> int64 {
    return @asm @clobbers("memory") (addr) -> int64 {
        "ldar $out, [$0]"
    };
}

@asm @clobbers("memory") fn barrier() {
    "dmb sy"
}
```

Following GCC, an asm with an output is assumed pure (it may be reordered or
removed if unused), while one with no output is treated as having side effects.
A `@clobbers` list constrains register allocation and ordering but does not by
itself make an asm volatile. Inline asm is inherently target-specific, so it
pairs with [`@if`](#conditional-compilation) on `TARGET_ARCH` to select
per-architecture code. It is lowered by the host architecture's assembler, so it
works on the host and on a cross `--target` of that same architecture — for
instance an aarch64 host cross-compiling an aarch64 bare-metal object (see
[examples/baremetal/](../examples/baremetal/)) — but not a foreign architecture.
Pinning an operand to a fixed physical register and `@naked` functions are on
the [roadmap](../ROADMAP.md#planned).

## Comments

```c
// line comments

/* block comments */

/**
 * Doc comments are block comments by convention, in this format:
 *
 * @param self:  array to write into
 * @param index: zero-based index; must be < self->length
 *
 * @return true on success, false if index is out of bounds
 */
```

See the [standard library index](../lib/README.md) for the modules under `lib/`,
all written in this style.
