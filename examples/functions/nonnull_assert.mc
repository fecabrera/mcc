import "std";
import "memory";

// The postfix `p!` non-null assertion: the escape hatch into @nonnull.
// A @nonnull parameter (see nonnull.mc) only accepts arguments the compiler
// can prove non-null, and a heap pointer or a returned `T*` carries no such
// proof. Where a null-check guard cannot narrow it either (see
// nonnull_narrowing.mc), `p!` is the programmer's explicit assertion that
// the pointer is non-null: it evaluates to its operand unchanged, emits no
// instructions and
// no runtime check, and the assertion alone satisfies the @nonnull proof.
// The compiler trusts it, so asserting a pointer that is actually null is
// undefined behavior. (`null!` itself is rejected outright:
// "cannot assert null as non-null".)
// Prerequisites: nonnull.mc; heap allocation from memory/pointers.mc.
fn first(@nonnull p: int32*) -> int32 {
    return *p; // no null check: every call site proved (or asserted) non-null
}

fn main() -> int32 {
    // The canonical case: alloc<T> returns a plain int32*, which cannot
    // cross into a @nonnull slot on its own.
    let p = alloc<int32>(1);
    *p = 42;

    // first(p);         // error: a plain int32* carries no proof
    let a = first(p!);   // the assertion is the whole proof, at zero cost
    println("a = %d", a);

    // `p!` is legal anywhere as identity, not only in @nonnull argument
    // position:
    let b = *(p!);

    // The assertion covers exactly the expression it wraps, but a `let`
    // initialized from it seeds a narrowed fact (see nonnull_narrowing.mc),
    // so one hatch at the binding covers every later use of the binding:
    let q = p!;
    let c = first(q);    // ok: q started narrowed and nothing nulled it

    // Lexing gotcha: `!=` lexes greedily as one token, so `p != q` is always
    // the comparison. Asserting and then comparing needs parens:
    let same = (p!) == q;
    println("b = %d, c = %d, same = %d", b, c, same);

    dealloc(p);
    return 0;
}

// The hatch's home ground is what narrowing cannot track at all: globals,
// member and index expressions like s.p or a[i], and a returned pointer used
// once, where a guard would be noise.
// See also: nonnull.mc for @nonnull parameters and the always-non-null proof
// sources that need no assertion; nonnull_narrowing.mc for the null-check
// guards that avoid the hatch in idiomatic code; nonnull_loops.mc for
// narrowed facts crossing loops (guards cover loops now, so the hatch is a
// last resort there too); memory/pointers.mc for heap allocation and the
// pointer basics; memory/nonnull_heap_buffers.mc for the one-guard migration
// of heap buffers across the stdlib's @nonnull contracts.
