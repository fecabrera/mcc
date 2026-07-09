import "std/io";

// `with` is the single-pattern unwrap statement: pure sugar over a one-arm
// `case type`. `with (n = v as int32) body; else other;` evaluates the
// `any` subject once and compares its tag; on a match it binds `n` to the
// recovered value, scoped to the true branch only (the else branch has no
// binding). The `with (...)` head is itself the checked context: inside it
// `n = v as int32` is the tag test plus bind, while `as` everywhere else
// keeps its plain cast semantics; the spelling deliberately mirrors the
// planned bare unwrap `let n = v as int32;`, with `with`/`else` supplying
// the mismatch handling here. The binding is required: `with (v as int32)`
// without the `n =` is a parse error. Unlike `case type`, the `else` is
// optional: an unmatched tag takes the else when there is one, and falls
// through a lone `with` doing nothing -- defined behavior, not a trap.
// Bodies are a single statement or a braced block, like `if`. The head
// takes exactly one pattern; there is no `and`/`or` composition in the head
// and no `while (t = v as T)`. The subject must be an `any` (an `any*`
// auto-dereferences, as in `case type`), and `with` is a reserved keyword.
// Prerequisites: any.mc for the box, implicit boxing, and the multi-arm
// `case type` this desugars to; generic_case_arms.mc for the pattern rule
// the generic forms below reuse.

struct point {
    x: int32;
    y: int32;
}

// The pattern type follows the exact generic case-type arm detection rule:
// a name that resolves is a concrete test (a single tag compare), and an
// unresolved bare name introduces an arm-scoped type parameter. `as T*`
// matches boxed pointer tags only, with T bound to the pointee, so this
// body compiles once per pointer tag the whole program boxes; any
// non-pointer tag falls through the lone `with` to the 0 below.
fn pointee_width(a: any) -> int32 {
    with (p = a as T*) return sizeof(T) as int32;
    // A generic-pattern `with` is conservatively assumed to reach its end
    // (the same rule as generic case-type arms), so this trailing return is
    // required even when every monomorphized copy above returns.
    return 0;
}

// `as T` is the value form: it matches every boxed tag, with T bound to the
// boxed type itself (pointer tags included, were any left unmatched).
fn payload_width(a: any) -> int32 {
    with (v = a as T) return sizeof(T) as int32;
    return 0;   // reached only by an unmatched tag, e.g. a zeroed box
}

// A zero-filled box holds tag 0, which matches no pattern.
@static let empty: any;

fn main() -> int32 {
    let a: any = 42;

    // Concrete pattern with else: one tag compare. The binding `n` exists
    // only in the true branch; the else has no binding at all.
    with (n = a as int32) println("int32: {}", n);
    else println("not an int32");

    // Re-box and test again: the same head now takes the else.
    a = 2.5;
    with (n = a as int32) println("int32: {}", n);
    else println("not an int32");

    // A braced block body, like `if`.
    with (f = a as float64) {
        let doubled = f * 2.0;
        println("float64 doubled: {}", doubled);
    }

    // A lone `with` on an unmatched tag falls through doing nothing --
    // this line prints nothing and execution just continues.
    with (b = a as bool) println("never printed");

    // Tag 0 is an unmatched tag like any other: with an else it takes the
    // else, and a lone `with` on it would fall through silently.
    with (n = empty as int32) println("int32: {}", n);
    else println("empty box");

    // The generic pointer pattern: one body copy per boxed pointer tag
    // (point* and char* here), and a non-pointer tag skips the body.
    let origin = point { x = 3, y = 4 };
    println("pointee_width(&origin) = {}", pointee_width(&origin));
    println("pointee_width(\"hi\")    = {}", pointee_width("hi"));
    println("pointee_width(42)      = {}", pointee_width(42));

    // The generic value pattern: T binds to each boxed type in turn.
    println("payload_width(42)      = {}", payload_width(42));
    println("payload_width(2.5)     = {}", payload_width(2.5));
    println("payload_width(false)   = {}", payload_width(false));

    return 0;
}

// See also: any.mc for the box itself and the multi-arm `case type` a
// `with` desugars to (reach for `case type` the moment a second pattern
// appears); generic_case_arms.mc for the concrete-vs-generic detection
// rule, the per-tag monomorphization model, and first-match-wins ordering;
// case_type_groups.mc for one body over several named types, which the
// single-pattern `with` head cannot express.
