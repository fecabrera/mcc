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
with `&`. For a **struct** parameter, `const` also changes how it is passed —
by a hidden pointer to the caller's storage instead of a by-value copy. You get
value semantics (the callee sees the struct, never mutates the caller's) without
hand-writing a pointer or paying for the copy:

```c
struct matrix { m: float64[16]; }

fn trace(const a: struct matrix) -> float64 {   // passed by hidden reference
    return a.m[0] + a.m[5] + a.m[10] + a.m[15];
}
```

The hidden reference shares the argument's storage when it has an address (a
variable, a field); a temporary argument (a struct returned by value, say) is
spilled to a stack slot first. `const` works on generic parameters too.

`const` on a **pointer** parameter freezes the pointer itself, not what it
points at — `const p: struct node*` means `p = ...` is rejected but `p->next =
...` is fine, the same distinction as C's `node* const` versus `const node*`.
On a scalar it simply makes the parameter read-only.

`const` is not allowed on `@extern` parameters (the hidden-reference ABI would
not match the C function). A function with a `const` struct parameter also
cannot be used as a function value (`let f = trace;`), because a plain
`fn(struct matrix) -> ...` pointer type cannot express the hidden-reference
calling convention. This is a limitation of the bare `fn(...)` type, not of the
ABI — the two conventions are distinct at the machine level; a future
`fn(const T)` function-pointer type could carry the convention and lift the
restriction.

### mut parameters

A parameter marked `mut` is the writable dual of `const`: it is passed by
hidden reference to the caller's storage — for **every** type, scalars
included, since that is the only way a write can reach the caller — and the
callee's assignments land in the caller's variable. Reading it loads the
current value (copy on read); `&` on it is rejected, so the reference can
never outlive the call. It is the memory-safe replacement for an out-pointer
parameter:

```c
fn find(key: int32, mut out: int32) -> bool {   // instead of out: int32*
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
and `uint32` may share bits, but not a `mut` reference). A literal, a plain
call result (a [`mut` return](#mut-returns) re-lends instead), a `const`
parameter, a read-only `const T` lvalue, `@volatile` storage, and a
`@packed` field (whose alignment is not guaranteed) are all rejected.

Inside the body a `mut` parameter behaves like the variable it references:
assignment and compound assignment write through, a struct's fields project
(`p.x = 3` writes the caller's field), and it can be **re-lent** — passed
onward as another function's `mut` argument (recursion included), which
forwards the reference without letting it escape. Two `mut` parameters may
alias the same variable; as with two pointers, the last write wins.

`mut` works on generic parameters (`fn swap<T>(mut a: T, mut b: T)`), with
the argument's type matching the instantiated parameter exactly. Overloads of
one generic name may freely mix `mut` and non-`mut` positions — for example a
`mut`-taking overload next to a pointer-taking one:

```c
fn set<T>(mut a: T) { a = 7 as T; }    // for the caller's own variable
fn set<T>(p: T*)    { *p = 9 as T; }   // for storage reached by pointer
```

The call resolves the overload in a defined order:

1. **Shape** — candidates whose parameter patterns the argument types cannot
   match are dropped, and a candidate that is `mut` at a position receiving
   something that is not an lvalue (a literal, a call result, an `&x`, a bare
   function name) is dropped with them.
2. **Specificity** — among the viable candidates the most specific parameter
   patterns win (`T*` beats `T`, concrete types beat both), exactly as for
   overloads without `mut`.
3. A remaining tie is an error, and lvalue-ness never breaks it: the
   same-shape pair `fn f<T>(mut a: T)` / `fn f<T>(a: T)` is ambiguous for an
   lvalue argument (an rvalue picks the non-`mut` one, the only viable
   candidate).

The argument is still evaluated exactly once, before the winner is known: at
a position any candidate marks `mut`, an lvalue's address is formed up front
and its value read through that address, so the callee's writes land in the
caller's storage when a `mut` overload wins, and the single read keeps the
storage's semantics (a `@volatile` lvalue gets a volatile load) when a
non-`mut` one does. The writability rules above are judged against the
**chosen** overload only: a `const` parameter, a read-only `const T` lvalue,
`@volatile` storage, or a `@packed` field is a fine argument when a non-`mut`
overload wins, and remains an error when a `mut` one does.

Like `const`, `mut` is not allowed on `@extern` parameters (the
hidden-reference ABI would not match the C function), and a function with a
`mut` parameter cannot be used as a function value or exported to an
[interface file](#interface-files) — the bare `fn(...)` type cannot express
the convention (the same liftable limitation as `const`, see above).

See [examples/functions/mut_params.mc](../examples/functions/mut_params.mc)
and, for overloads mixing `mut`,
[examples/functions/mut_overloads.mc](../examples/functions/mut_overloads.mc).

### mut returns

A function declared `-> mut T` returns an **lvalue**: a reference to
caller-reachable storage of type `T`, rather than a copy of the value. The
call expression is then usable on both sides of `=` — it is the accessor
shape (`_at`-style element access without handing out a raw `T*`):

```c
struct buf { data: char*; length: uint64; }

fn buf_at(mut self: struct buf, i: uint64) -> mut char {
    return self.data[i];       // formed from the mut receiver: legal
}

fn bump(mut c: char) { c += 1; }

fn main() -> int32 {
    let bytes: char[4];
    bytes[0] = 'a'; bytes[1] = 'b'; bytes[2] = 'c'; bytes[3] = '\0';
    let b = struct buf { data = &bytes[0], length = 3 };
    buf_at(b, 0) = '/';         // assignment through the returned lvalue
    buf_at(b, 1) += 1;          // compound assignment (addressed once)
    bump(buf_at(b, 2));         // re-lent as a mut argument
    let c = buf_at(b, 0);       // value context: loads the current value
    return c == '/' ? 0 : 1;
}
```

There are no reference locals: a `mut` return is **consumed at the call
expression**. In value position it loads; on the lvalue side it is an
assignment or compound-assignment target, a base for projections
(`f(s).field = v`, and `f(s)[i] = v` through a `-> mut T*` result), and
re-lendable as another call's `mut` argument on both call paths (concrete
and generic/overloaded). `&f(...)` is rejected — the reference must not
outlive the full expression, the same non-escape guarantee a `mut`
parameter carries.

**The formation rule.** Without a lifetime system, what keeps the reference
from dangling is a strict, checkable rule at the callee's `return`: the
returned lvalue may only be formed from a **`mut` or pointer parameter or a
global**, traced through member accesses (`.`/`->`), elements,
dereferences, and calls that themselves return `mut` (composition:
`return buf_at(self, 0);` is fine). Everything rooted in the call's own
frame is rejected:

- a **local** — even a provably-safe alias like `let d = self.data;
  return d[i];` is rejected (inline the chain into the return expression);
- a **by-value parameter** (its storage is the call's frame copy);
- a **`const` parameter** (read-only, wherever the chain crosses);
- the **pointer parameter itself**: `return p` would reference the
  parameter's own frame slot and is rejected, while `return *p`,
  `return p[i]`, and `return p->f` reach the storage the caller handed in
  and are legal. A `mut` parameter *is* legal as the returned lvalue itself
  (`return x;`) — it already names the caller's storage.

Casts, arithmetic, `null`, and calls without a `mut` return are never part
of a legal chain. The storage rules `mut` arguments obey apply at the
`return` too: `@volatile` storage, `@packed` fields, and read-only
`const T` lvalues are rejected, and the lvalue's type must match the
declared return **exactly** (the caller writes through the reference, so
nothing adapts or widens).

`-> mut` works on generics (`fn pick<T>(mut a: T, mut b: T, f: bool) ->
mut T`), with the formation and void rules checked per instance. It is
rejected on `@extern` and `@asm` functions (the pointer-typed return would
change the C calling convention), on `main`, and on `void` (there is no
storage to reference); a `-> mut` function cannot become a [function
value](#function-pointers) (the plain `fn(...) -> T` type cannot express
it), and overloads differing only in `-> mut` collide as duplicates, like
any return-type-only pair. In an [interface file](#interface-files) the
marker is re-emitted on the prototype and must match the definition
exactly.

One programmer's-problem caveat, the same one [container
cursors](#control-flow) have: a `mut` return that points into a
container's heap storage is a borrow of that storage — an operation that
reallocates it (a growing `list_push`, `string_cat`, ...) within the same
full expression, or between forming the reference and the surrounding
statement's store, invalidates the reference. The formation rule prevents
frame escapes, not heap staleness; keep the access and the mutation in
separate statements, exactly as with an in-flight cursor.

See [examples/functions/mut_returns.mc](../examples/functions/mut_returns.mc).

### Pointer decay into const/mut parameters

A `T*` argument at a `const T` (struct) or `mut T` slot implicitly
dereferences — the pointer **decays** — so the callee sees the pointee,
read-only or writable, without the caller writing `*var`. A stack value and a
heap pointer then call the same function identically:

```c
import "memory";

struct point { x: int32; y: int32; }

fn shift(mut p: struct point, const by: struct point) {
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

Mechanically the feature is cheap: a `const` struct parameter and a `mut`
parameter already travel as a hidden reference, so decay forwards the pointer
value instead of forming `&lvalue`. That is also why an **rvalue** `T*` may
decay into `mut T` — the pointee is real storage even when the pointer
expression is a temporary (`shift(&a, ...)`, a call result, a `p!`) —
deliberately unlike the plain rule that a `mut` argument must be an lvalue.

A decay is a **two-sided promise**. The callee's side is the receiver
contract already in the declaration: `const` will not write through the
reference, `mut` writes through it and never lets it escape. The caller's
side is a value-supplier promise in the `@nonnull` family: the pointer must
be **provably non-null**, because a `const`/`mut` reference is never null by
construction. The proof is the same machinery `@nonnull` uses — an `&x`, a
`@nonnull` parameter, a local seeded or narrowed by a null check, or the
postfix `p!` assertion:

```c
fn consume(p: struct point*, const by: struct point) {
    if (p == null) { return; }
    shift(p, by);               // narrowed: proven for this whole scope
}
```

An unproven pointer at a decaying slot is a compile error naming the fix:

```
example.mc: error: line 3: cannot pass a possibly-null point* as argument 1
of 'shift': decaying into a mut point parameter forms a reference, which is
never null (narrow with a null check or assert with postfix '!')
```

The explicit spelling `shift(*p, ...)` also stays legal and needs no proof:
the dereference is visible at the call site and carries the usual
null-dereference responsibility, exactly as it did before decay existed.

The rule is fenced in four ways:

- **Hidden-reference slots only.** `const` struct parameters and `mut`
  parameters of any type. A `const` scalar parameter is a by-value copy with
  no reference behind it, and a plain by-value `T` parameter still needs an
  explicit `*var` — the copy stays visible.
- **Exactly one level.** `T*` decays to `const`/`mut T`; a `T**` decays only
  to `const`/`mut T*` (its pointee is itself a pointer), never twice.
- **Proven non-null**, as above. A string literal never decays into `mut` —
  its bytes live in a constant global.
- **An exact match beats a decayed one.** Under overloading, decayed
  readings enter resolution only when no candidate matches the pointer type
  directly, so `fn f(x: T*)` beside `fn f(mut x: T)` stays unambiguous.

Generic inference participates: at a `const`/`mut` slot, unification also
tries the argument's pointee against the parameter pattern, one level down,
so a `list<int32>*` at `mut self: list<T>` binds `T = int32` (previously
"cannot infer type parameter(s) T"). Facts about the *pointer's own storage*
are irrelevant to the callee — a `const` or `@volatile` pointer variable
decays fine (the load of the pointer itself honors them) — and because the
pointer is passed **by value**, a flow-narrowed non-null fact survives the
call, unlike lending the pointer variable itself as `mut`. A decayed
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
It changes **no ABI**, unlike `const`/`mut`, so it is allowed on `@extern`
declarations too — the [libc bindings](#reaching-libc) mark `memcpy`, `strcpy`,
and the rest of the C11 `restrict` family this way (but not `memmove`, whose
regions may overlap by design).

The promise is **unchecked**: if the pointers actually overlap, the behavior is
undefined, exactly as with C's `restrict`. mcc does not verify it and does not
warn when the same variable is passed to two `@noalias` parameters. `@noalias`
is only a pointer-parameter annotation: it is rejected on a non-pointer
parameter, on a `mut` parameter (aliasing two `mut` parameters is allowed by
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
(local, `@static`, or global), transitively a `@nonnull` parameter of
the calling function (so a `@nonnull` callee forwards its own parameter
onward with no check), a plain pointer local or a pointer-typed field
projection flow-narrowed by a null check (below), an `as` cast to a
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
  into one), `mut` parameters never carry a *name* fact (a callee taking
  two `mut` references can alias one, so a call could null it without
  naming it here), and index expressions like `a[i]` carry no fact —
  assert those with `!` instead.
- **Taking `&p` anywhere in the function disables narrowing of `p`**
  entirely: once its address exists, a stored pointer could null `p`
  without ever naming it.
- **The fact dies on anything that could null the variable**: reassigning
  `p` (including `p += n`), passing `p` as a `mut` argument, or a shadowing
  `let p`. An invalidation inside a nested block persists outward, and it is
  path-insensitive: invalidating `p` in one branch of an inner `if` drops
  the fact for the code after it, whichever branch runs.
- **A loop drops exactly the facts it could invalidate.** A loop's body and
  condition re-run on the back edge, where a later iteration may already
  have nulled the pointer, so at loop entry (`while`, `until`, `for`) a
  pre-scan of the whole loop kills the facts for every name the loop
  reassigns (`p = ...`, `p += n`), shadows with a `let p`, or lends as a
  bare `mut` argument, anywhere in the subtree (nested branches, `case`
  arms, `defer` bodies, and both branches of an `@if` included; `mut`
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
`let s: uint8* = "...";`, `let q = p!;`) starts narrowed, under the same
eligibility rules, and dies on the same events.

**Projection facts.** The same guard shapes also narrow a pointer-typed
*field projection*: `if (b->data != null)` proves `b->data` in the then
branch, a diverging `if (b->data == null)` proves it for the remainder,
loop headers and exit conditions prove it the same way, and `and`/`or`
chains thread projections and bare names together
(`if (b == null or b->data == null) { return -1; }` proves both). A
proven projection crosses `@nonnull` slots and decays into `const`/`mut`
parameters exactly like a proven local. The fact is keyed by the access
path, so any depth works (`p->inner->data`), `.` and `->` spell the same
fact, and `(*b).data` is the same fact as `b->data`. The base must be a
local variable; `mut` and `@nonnull` parameters are fine as *bases*
(`fn f(mut b: Buf)` may guard and use `b.data`), while globals,
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
  `mut` parameter or a global, nothing opaque (`@asm`, a call through
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
- **on the base**: reassigning, shadowing, or `mut`-lending `b` kills
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
it), cannot be passed as a `mut` argument (the callee writes through a
hidden reference and could store null into the parameter's storage), and a
shadowing `let` of the same name is a fresh, unproven binding. A
function with `@nonnull` parameters cannot be used as a function value — the
plain `fn(...)` type cannot carry the contract, and a call through the
pointer would skip the proof check.

Like `@noalias`, the annotation precedes any `const` (`@nonnull const p: T*`
composes; the two annotations combine in either order), changes **no ABI**,
and so is allowed on `@extern` declarations and rides along on
[interface files](#interface-files). It is rejected on non-pointer
parameters, on `mut` (a `mut` parameter is passed by reference and is never
null), and on `@asm` functions. At the LLVM level the established fact is
handed to the optimizer as the `nonnull` and `dereferenceable(sizeof(T))`
argument attributes.

**The standard library is annotated.** The data, source, key, and
destination pointer parameters of the stdlib declare their contracts with
`@nonnull`: the `memory` copy/fill family (`bytecopy`, `copy`, `bytezero`,
`zero`, `bytefill`, `fill`), the `hashing/` digests (`md5`, `crc32`,
`murmur3`), `dict`'s string keys (`dict_set`/`dict_get`/`dict_remove`),
and the raw-array source overloads of `list_init` and `string_init`.
Passing an unproven pointer to any of them is a compile error instead of
a latent crash. A stack buffer (`&x`, an array) or a string literal is
already a proof; a heap buffer needs a one-line diverging guard after the
allocation (`if (p == null) return 1;`), which loops that do not touch
the pointer preserve. Container `self` parameters are `mut`/`const`
receivers, where non-null holds by construction, so they carry the
guarantee without annotations; a heap `list<T>*` or `dict<V>*` reaches
them through the same one-line guard, by
[decaying](#pointer-decay-into-constmut-parameters) into the receiver
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
fn counter_init(mut self: counter)                          // zeroed
fn counter_init(mut self: counter, start: int32)            // seeded
fn counter_init(mut self: counter, start: int32, step: int32)
```

Resolution follows the same order as
[generic overload sets](#generics) — viability by argument shape, then the
most specific candidate — and **resolution is by arguments only**. The rules
that keep it C-simple:

- **Overloads must differ in parameter types.** Two overloads may not differ
  solely in return type; that stays a duplicate definition. Nor solely in
  `const`/`mut` markers on the same types (a same-type `mut`/non-`mut` pair
  is uncallable under the resolution rules — an rvalue argument filters out
  the `mut` candidate, and an lvalue keeps both in a same-shape tie — so
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
  citing both declaration sites), and a name growing into a set moves its
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
  variadic functions (the viability filter matches arity exactly),
  functions with a `va_list` parameter, `@extern`/`@symbol` functions
  (their C symbol is fixed), and `@static` functions.

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
(`f<int32>(...)`) select among the generic candidates only. Two same-tier
candidates of equal specificity stay the
ambiguity error, which is also the enforced collision rule between the
classes (a generic whose substituted parameter list duplicates a concrete
one is not statically detectable in general). The concrete side of a mixed
set keeps the concrete rules: `main`, variadic, and `va_list`-taking
functions cannot join, whichever side declares first. The symbol choice
counts concrete signatures alone, so one concrete member beside a template
still keeps its plain, C-linkable symbol.

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
member, say) still collides; a planned `@override` annotation covers that
last case.

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

See [examples/functions/overloading.mc](../examples/functions/overloading.mc)
and [examples/functions/open_overloads.mc](../examples/functions/open_overloads.mc).

### @noreturn functions

`@noreturn` marks a function that never returns to its caller — it exits,
aborts, or loops forever. The compiler then treats every direct call as
**diverging**: the rest of the block is dead, so no dummy `return` is needed
after the call (code past it is silently dropped, exactly like code after a
`return`), an all-arms-diverge `if`/`case` counts as diverging through it,
and a diverging null guard narrows (below):

```c
import "std";                        // exit, abort, and _Exit are @noreturn

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
  the end is **undefined behavior**. This is also what makes the canonical
  spin form legal — `@noreturn fn spin() { while (true) {} }` needs no
  unreachable trailing return.
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
optimizer drops the dead continuation paths.

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
- **v1 restrictions.** A collecting function cannot be
  [overloaded](#function-overloading) or share a
  [generic](#generics) name (collection runs on the direct-call path only;
  generics and overload sets come in a later stage). Function-pointer
  types carry no marker, so a call through a `fn(...)` value passes the
  slice explicitly. A collecting function cannot also take C varargs
  (`...`), cannot be `@extern` (C sees no `slice<const any>`), and `main`
  cannot collect. A `mut` trailing `slice<const any>` never collects —
  `mut` lends the caller's own storage — so such a function stays
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
shape: [libmc/hash.mc](../libmc/hash.mc) hashes integer keys by value (splitmix64)
and pointer keys by content (FNV-1a), and [libmc/set.mc](../libmc/set.mc) simply
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
variants make the call ambiguous — a compile error.

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
the same way. The bound target must be a concrete struct: an unknown or
non-struct target errors at the *declaration* (`int32 is not a struct; cannot
extend it`), and it may not reference a type parameter — `<S, T extends S>` is
a deliberately deferred follow-up. It may be a fully-applied generic or
[alias](#type-aliases) instance (`extends pair<int32, V>`, `extends
ipair<char>`), which resolves to the underlying struct.

The essential difference from a closed group is that the satisfying set is
**open-ended** — any struct, anywhere, may later `extends` the bound — so
there is no eager enumeration: the bound is checked **lazily** against each
deduced binding, at every call and instantiation site. A bound composes with a
[default](#type-parameter-defaults) (`<T extends shape = circle>`), which must
itself satisfy the bound (checked at the declaration, mirroring the closed-group
member-default check). A parameter may not carry both a bound and a group.

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

A `mut` parameter keeps its marker in the pattern (`bump<$0>(mut $0)`): a
same-shape `mut`/by-value template pair is a genuine overload — an rvalue
argument filters out the `mut` candidate. `const` markers and the return
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
libmc/hashing/splitmix64.mc: error: line 10: cannot cast box to uint64
libmc/hash.mc: note: line 12: in instantiation of splitmix64<box>
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
always-non-null source, an array decaying to a pointer, and the postfix
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
unproven sites, never libmc-internal ones.

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
function", "every path through the statement above diverges", ...). The
messages never name types or callees — dead code is dropped before it is
ever type-checked, and the type-free wording keeps a generic body's
per-instantiation re-emissions byte-identical so the print-time dedup
collapses them to a single diagnostic. Like every opt-in class, it never
changes the code generated.

What does *not* warn, deliberately:

- **Code after `while (true)`** — the generator still emits the loop's
  exit edge, so the following code is structurally reachable today. The
  constant-condition loop folding item on the roadmap will extend the
  class's reach here.
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
up. It is reachable two ways: the whole-build `-Werror` (this repository's CI
runs it) promotes every *enabled* class, so `-Werror -Wextern-nonnull` is
strict; and the selective `-Werror=<class>` input form makes strict a
targeted posture on the C boundary without promoting the whole build.

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

There is no suppression: every call site warns, even one inside another
deprecated function — a migration cannot hide behind a second alias. What *is*
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

The standard library uses this for the four renamed [memory](../libmc/memory.mc)
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

`for x in obj` iterates anything that supplies the **`_it`/`_next` protocol** —
a pair of functions named after the iterable's struct, which the compiler
dispatches by name. For an `obj` of type `struct list<T>` it calls `list_it`
and `list_next`:

```c
fn list_it<T>(self: struct list<T>*) -> struct iterator<list<T>>;  // make a cursor
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
way — the same staleness a [`mut` return](#mut-returns) into container
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
    println("%llu: %d", e.index, e.value);     // 0: first, 1: second, ...
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
    with (n = a as int32) println("int32: %d", n);
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
buffer in a defer can't clobber what you return.

Defers run on every **block exit** — and a call to a
[`@noreturn` function](#noreturn-functions) is not one. `exit(1);` leaves
enclosing defers unrun (the process ends inside the callee; there is no
return path to unwind), matching C, where `exit()` runs `atexit` handlers
but never unwinds the calling stack. Code that must clean up should
`return` an error up to `main` instead of exiting deep in the call tree. An
[`unreachable;`](#the-unreachable-statement) likewise runs no defers — a
path that never happens has nothing to unwind. See
[examples/control-flow/defer.mc](../examples/control-flow/defer.mc).

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
`<` `<=` `>` `>=`, `==` `!=`, then `and`, then `or`, and loosest of all the
`?:` conditional.
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
untaken arm's side effects do not happen. It is the loosest operator and
right-associative, so it reads as an `if`/`else` ladder without parentheses:

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

Casts between structs are rejected, with two exceptions: an
[`extends`](#structs) value-upcast to a base struct, and a **borrow** to a
[`slice<T>`](#slices) view (`xs as slice<T>` from an owned `list<T>` or `T[N]`).

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
pointer). `p += n` is an ordinary reassignment: it drops a narrowed local's
non-null fact, and stays rejected on a `@nonnull` parameter (which cannot be
reassigned).

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
[libmc/memory.mc](../libmc/memory.mc) for a generic typed allocator.

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
the function, so nothing needs spelling out — even a variadic like `println`
aliases cleanly:

```c
const log = println;          // an alias; the type is inferred
const plus = add;

fn main() -> int32 {
    log("plus(2, 3) = %d", plus(2, 3));
    return 0;
}
```

(A `@static let` aliasing a function must either be annotated or left to infer
from the bare function name; an unannotated table literal still needs its type,
as `@static let ops: binop[] = [add, sub];` above.)

A function value is a pointer underneath, so it casts like one: `add as
uint64` is the function's address as an integer, `addr as fn(...) -> R`
turns an address back into a callable pointer, and it bitcasts to/from a
data pointer such as `uint8*`.

Only a single, non-generic function has an address; a generic name like
`id` cannot be used as a value (there is no one instance to point at).

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
or `uint64`) without a cast. For a multi-dimensional array, `len(grid)` is
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

let nums: struct list<int32>;      // ...or an owned list<T>
list_init(nums, 8);
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
view lives for the whole call — even against a plain (non-`mut`) `slice<T>`
parameter, whose fresh backing array is writable (uniform-allow, exactly as for
a string literal). A `mut slice<T>` parameter still rejects a literal: it
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

A struct can `extends` another to reuse its layout. The base's fields are
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
prefix. Both are _explicit_ — there is no implicit upcast, so a
`struct point3*` is a distinct type that won't silently pass where a
`struct point*` is expected. What decides whether the upcast is allowed is the
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

This is distinct from the planned `T extends mystruct` **bound**: a bound
constrains what a caller may bind `T` to, while this uses `T` as the base —
same keyword, different position, no overlap — and the two will compose as
`struct wrapper<T extends node> extends T`. The usual `extends` non-goals
carry over: no method inheritance (a payload's methods will be reached
through the upcast) and no constructor chaining.

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
them: the growable [libmc/list.mc](../libmc/list.mc), the open-addressing hash
table [libmc/set.mc](../libmc/set.mc) (borrowing, identity-keyed), and the
string-keyed [libmc/dict.mc](../libmc/dict.mc), which owns copies of its keys and
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

The boxable set is **primitives, pointers, and slices**. Structs, unions, and
arrays do not box — by value the payload would be unbounded, by pointer the
lifetime would go implicit — so box a pointer explicitly instead (`&s`; for
an array, `&xs[0]`): the compile error names the escape hatch. An `any` never
boxes another `any` (`any` to `any` is a plain copy), and an
[enum](#enums) member boxes under its underlying type's tag.

The **only** way to recover the value is a checked tag test — the
`case type` type-switch below, or its one-pattern sugar, the
[`with` statement](#the-with-statement) — with no exceptions in the
language, an unchecked `as` unwrap would be either a tag-ignoring pun or a
new trap mechanism, so there is none (and the tag/payload fields are not
readable):

```c
fn show(a: any) {
    case type (a) {
        when int32 n:       println("int %d", n);
        when float64 f:     println("float %f", f);
        when char* s:       println("string %s", s);
        when slice<char> t: println("slice of %llu", t.length);
        when T* ptr:        println("pointer %p", ptr);  // every other boxed pointer
        else:               println("something else");
    }
}
```

It rides the [`case`](#control-flow) statement's shape — the subject is
evaluated once, arms run without fall-through — with the type-mode specifics:

- Each arm **must bind a name**; the binding holds the recovered value,
  typed as the arm's type and scoped to the arm.
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
  twice included — as is an arm whose type could never box (a struct arm, or
  `when any`).

The tag is the 64-bit FNV-1a hash of the boxed type's canonical name,
computed at compile time — no runtime registry, so tags are deterministic
across separate compilations and `case type` lowers to the same
integer-equality chain as a value `case`. A hash collision between two type
names used in one compilation is astronomically unlikely, and detected: it
fails the compile rather than corrupting a type-switch.

An `any` is an ordinary 24-byte value otherwise: pass and return it by value,
put it in struct fields and arrays (`any[N]`), point at it (`any*`), take
`sizeof(any) == 24`, use it in `.mci` [interfaces](#interface-files). One gap,
the same shape as the union one: a global/`@static` `any` **initializer** is
not supported yet — assign at runtime instead (an uninitialized global `any`
is zero-filled and matches only `else`). The box also powers
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
println("%s", typename(int64));          // int64
println("%s", typename(slice<int32>));   // slice<int32>
let x: const float64 = 1.5;
println("%s", typename(x));              // float64 — const strips
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
        when T v: println("a boxed %s", typename(T));
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
and finally the project's [libmc/](../libmc/) directory, which is on the path by
default so the [standard library](../libmc/README.md) is importable by bare name.
Pass `--nostdlib` to leave `libmc/` off the path.

```c
import "memory";       // found in libmc/ via the search path
import "libc/stdio";   // libc bindings, also in libmc/

fn main() -> int32 {
    let p = alloc<int32>(3);   // defined in libmc/memory.mc
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
  resolves to and called with the mcc convention, so `const`/`mut` parameter
  markers are re-emitted and the hidden-reference passing they imply carries
  over. (A real `@extern` declaration in the source stays verbatim — it keeps
  meaning "C calling convention".)
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
 * Doubles the list's capacity. Internal; called by list_push.
 */
@private
fn list_grow<T>(mut self: struct list<T>) { ... }
```

```
error: line 5: function 'list_grow' is private to list.mc
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

### Bodyless fn prototypes

A plain `fn` may also end with `;` instead of a body. Where `@extern` means
"a symbol with the **C** calling convention", a bodyless prototype means "a
concrete **mcc** function defined in another object" — the call uses the mcc
convention, so `const` struct and `mut` parameters keep their
[hidden-reference passing](#mut-parameters), which `@extern` deliberately
rejects:

```c
fn bump(mut n: int32);                  // defined in a linked object
fn total(const p: struct pair) -> int64;

fn main() -> int32 {
    let x: int32 = 40;
    bump(x);            // the hidden reference reaches the definition
    ...
}
```

Every signature marker (`const`, `mut`, `@noalias`, `@nonnull`, and a
[`-> mut` return](#mut-returns)) means exactly
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
type (its `mut` marker included), the derived `const`-struct/`mut`
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

`import "format";` provides the **formatting protocol**: one
[overload set](#function-overloading),

```c
format(mut str: string, value: X, const modifier: slice<char>)
```

where every member appends `value`'s rendering to a
[`string`](../libmc/string.mc) and `modifier` steers the spelling (an empty
string picks the default). Because the modifier is a `slice<char>`, a bare
string literal adapts to it at the call, so modifiers are written inline.
The baseline members cover the built-in types:

- **Signed integers**: decimal. One [closed-group](#closed-type-groups)
  template takes `int32 | int16 | int8` and sign-extends into the concrete
  `int64` worker, so `-4` renders `-4` at every width.
- **Unsigned integers**: unsigned decimal, one group for
  `uint64 | uint32 | uint16 | uint8`.
- Integer modifiers: `"x"` lowercase hex, `"X"` uppercase hex, `"p"`
  pointer-style (`0x2a`). A negative narrow value was already sign-extended
  when the modifier applies, so its hex is the full 64-bit two's-complement
  pattern (`-4 as int32` with `"x"` is `fffffffffffffffc`).
- **`float64`**: fixed-point (`3.5` renders `3.500000`).
- **`bool`**: `true`/`false`; `"y"` renders `y`/`n`, and `"yes"` renders
  `yes`/`no`.
- **`char`**, **`char*`**, **`slice<char>`**: appended as text (the
  modifier is ignored). A string literal decays to `char*` and lands on
  that member.
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
import "format";
import "string";

let s: struct string;
string_init(s);
format(s, 255 as int32, "x");    // s is now "ff" — the literal adapts
```

One sharp edge, a consequence of open overloading: an untyped integer
literal is ambiguous between the `int64` and `char` members, so type the
value (`42 as int32`).

Overload sets are open, so **making your own type printable is writing one
`format` overload for it in your own module**: a concrete member outranks
the closed-group templates and the unbounded fallback, and it may recurse
back into the set for its fields:

```c
struct point { x: int32; y: int32; }

fn format(mut str: string, value: struct point*, const modifier: slice<char>) {
    string_push(str, '(');
    format(str, value->x, modifier);
    string_append(str, ", ");
    format(str, value->y, modifier);
    string_push(str, ')');
}
```

See [examples/systems/formatting.mc](../examples/systems/formatting.mc).

## Reaching libc

To call into the C library, import a binding module from
[libmc/libc/](../libmc/libc/) — `import "libc/stdio";`, `import "libc/string";`, and
so on. These are ordinary [`@extern` declarations](#extern-declarations) for the
C functions, covering most of the standard headers (the `printf`/`scanf`
families, the `str*`/`mem*` functions, `malloc`/`qsort`/`strtol`, `FILE*`
streams, math, time, errno, …); see the
[standard library index](../libmc/README.md) for the full list.

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

See the [standard library index](../libmc/README.md) for the modules under `libmc/`,
all written in this style.
