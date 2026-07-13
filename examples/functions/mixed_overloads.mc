import "std/io";

// Mixed generic/concrete overload sets: a generic template and concrete
// functions may share one name, forming a single overload set (from any
// module too, sets being open: open_overloads.mc joins a concrete set
// cross-module).
// Candidates rank by (is-concrete, specificity): a concrete member wins any
// call it matches exactly, and the generic tier covers everything else. The
// motivating shape is fast paths plus a catch-all: hand-tuned members for
// the common types, one template for the rest.
// Builds on overloading.mc (concrete sets) and mut_overloads.mc (resolution
// inside a generic set); generic functions themselves are covered in
// types/generics.mc.

fn describe(x: int32)   -> int32 { return 1; }   // concrete fast path
fn describe(x: float64) -> int32 { return 2; }   // concrete fast path
fn describe<T>(x: T)    -> int32 { return 3; }   // generic catch-all
fn describe<T>(x: T*)   -> int32 { return 4; }   // generic, any pointer

// The concrete tier ranks first, so a generic whose substituted parameter
// list would TIE a concrete member never gets the call: describe(n) below
// instantiates nothing, even though describe<T> at T=int32 has the same
// effective signature. (That shadowed instantiation is also why the two
// classes never collide as definitions: the concrete member simply
// outranks it.) Within one tier the shipped rules stand mostly unchanged,
// but a rank tie has one last arbiter: subsumption orders a strictly more
// specialized pattern ahead (overload_subsumption.mc), and only a cohort it
// cannot order is still
//     error: call to 'describe' is ambiguous between overloads

fn main() -> int32 {
    let n: int32 = 9;
    let f: float64 = 2.5;
    let b: bool = true;

    println("describe(n)        -> member {}", describe(n));   // exact int32: concrete wins
    println("describe(f)        -> member {}", describe(f));   // exact float64: concrete wins
    println("describe(b)        -> member {}", describe(b));   // no bool member: the generic covers it
    println("describe(&f)       -> member {}", describe(&f));  // generic tier, T* beats T

    // Explicit type arguments select among the GENERIC candidates only:
    // the concrete int32 member is skipped even on its exact type.
    println("describe<int32>(9) -> member {}", describe<int32>(9));

    return 0;
}

// The set is open: any module may add members, generic or concrete, and
// the whole-program union resolves under the same rank (open_overloads.mc
// shows the cross-module join). Symbol choice counts the
// concrete signatures alone: the two concrete members here link as
// "describe(int32)" and "describe(float64)", but a template beside a single
// concrete member leaves that member's plain C-linkable symbol intact.

// See also: overloading.mc (concrete sets and their rules), mut_overloads.mc
// (specificity and mut inside a generic set), overload_subsumption.mc (the
// subsumption tie-break among rank-tied templates), types/generics.mc
// (monomorphization), types/type_groups.mc (closed type groups adding a
// bounded tier between the concrete and generic ranks here),
// native_variadics.mc (collecting `args...` members joining a set, ranked
// below anything that matches without collecting). Full rules:
// docs/language.md, "Function overloading".
