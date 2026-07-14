import "std/io";
import "std/char";

// Dot calls: `recv.method(args)` is sugar for `Type::method(recv, args)`,
// where `Type` is the receiver's type. The receiver passes VERBATIM as the
// first argument, so overload resolution, `mut`-receiver legality, and
// every diagnostic are the desugared call's own -- the dot adds no
// machinery of its own, and the qualified spelling stays valid beside it.
// What the dot DOES add: one hop of pointer auto-deref, evaluate-once
// receivers (which is what makes chaining work), and left-to-right reading
// order. There are no bound-method values: `r.area` without the call keeps
// the plain field diagnostics.
//
// Prerequisites: methods.mc for the qualified families the sugar calls
// into, generic_methods.mc for receiver-driven inference, and
// functions/mut_returns.mc for the `-> mut` accessors used near the end.
// Alias and builtin receivers ride method_alias.mc's qualifier chase.

struct rect {
    w: int32;
    h: int32;
}

fn rect::area(const self: rect) -> int32 {
    return self.w * self.h;
}

fn rect::scale(mut self: rect, factor: int32) {
    self.w *= factor;
    self.h *= factor;
}

// An overload set dispatches at a dot call exactly as at a `::` call: the
// receiver is simply argument one.
fn rect::grow(mut self: rect, by: int32) {
    self.w += by;
    self.h += by;
}

fn rect::grow(mut self: rect, dw: int32, dh: int32) {
    self.w += dw;
    self.h += dh;
}

// A mut-returning method: `return self` re-lends the receiver's storage
// (functions/mut_returns.mc), which is what lets a chain keep mutating.
fn rect::itself(mut self: rect) -> mut rect {
    return self;
}

// A factory returning by value: its result is the temporary the rvalue
// rules below exercise.
fn mk(w: int32, h: int32) -> rect {
    let r: rect = { w = w, h = h };
    return r;
}

// ---- Fields shadow methods ----

// `handler` declares BOTH a fn-typed field `cb` and a method `handler::cb`.
// At a dot call the field wins; the method stays reachable by its
// qualified name.
struct handler {
    cb: fn(int32) -> int32;
}

fn handler::cb(const self: handler, v: int32) -> int32 {
    return v * 100;
}

fn double_it(v: int32) -> int32 {
    return v * 2;
}

// ---- mut-returning accessors: dot calls as lvalues ----

struct list4 {
    data: int32[4];
}

fn list4::fill(mut self: list4, v: int32) {
    for i in range(4) {
        self.data[i] = v;
    }
}

fn list4::at(mut self: list4, i: int32) -> mut int32 {
    return self.data[i];
}

struct wrap {
    l: list4;
}

fn wrap::view(mut self: wrap) -> mut list4 {
    return self.l;
}

// The mut-return formation rule walks through a dot call too: `self` is a
// mut parameter, `view()` re-lends it, `at(1)` re-lends again.
fn wrap::second(mut self: wrap) -> mut int32 {
    return self.view().at(1);
}

// ---- Generic and builtin receivers ----

struct point<T> {
    x: T;
    y: T;
}

fn point<T>::sum(const self: point<T>) -> T {
    return self.x + self.y;
}

// A builtin generic qualifier (method_alias.mc): declared once, then any
// slice<int32> receiver dispatches it.
fn slice<T>::first(const xs: slice<const T>) -> T {
    return xs[0];
}

fn main() -> int32 {
    // The desugar itself: r.area() IS rect::area(r), and a `mut self`
    // method mutates the receiver exactly as the qualified call does.
    let r: rect = { w = 3, h = 4 };
    println(f"r.area() = {r.area()}");                 // 12
    r.scale(2);
    rect::scale(r, 2);      // the qualified spelling stays valid beside it
    println(f"scaled twice: {r.w} x {r.h}");         // 12 x 16

    // Overloads dispatch by the full desugared argument list.
    r.grow(1);              // rect::grow(r, 1)
    r.grow(10, 0);          // rect::grow(r, 10, 0)
    println(f"grown: {r.w} x {r.h}");                // 23 x 17

    // A pointer receiver auto-derefs EXACTLY one hop: q.scale(2) is
    // rect::scale(*q, 2). Fields of the pointee still need `->`, and `->`
    // stays fields-only ("q->area()" is "struct rect has no field 'area'",
    // as before the sugar); a rect** receiver errors: "rect** is not a
    // struct".
    let q = &r;
    q.scale(2);
    println(f"through q: {q->w} x {q->h}");          // 46 x 34

    // Fields shadow methods: `cb` names a field here, so h.cb(5) calls the
    // fn value stored in it. The method is not gone, just unsugared. Only
    // a name that is NEITHER field nor method gets the combined error,
    // "struct 'rect' has no field or method 'nope'"; a bare access like
    // `h.cb` (no call) is always the field -- no bound-method values.
    let h: handler = { cb = double_it };
    println(f"h.cb(5) = {h.cb(5)} (field)");           // 10
    println(f"handler::cb(h, 5) = {handler::cb(h, 5)} (method)");  // 500

    // An rvalue receiver evaluates once into a hidden CONST local, so a
    // const-self method on a temporary is fine...
    println(f"mk(2, 5).area() = {mk(2, 5).area()}");   // 10
    // ...but a mut-self method on one is the desugared call's own error --
    // `mk(2, 5).scale(3)` is "cannot pass a read-only const rect as a mut
    // argument": the mutation would vanish with the temporary. A
    // mut-RETURNING receiver re-lends its lvalue instead, so this chain
    // writes r2's own storage:
    let r2: rect = { w = 1, h = 1 };
    r2.itself().grow(4);
    println(f"re-lent chain: {r2.w} x {r2.h}");      // 5 x 5

    // A mut-returning dot call is an lvalue: assignable, compound-
    // assignable, and chainable as a store target.
    let l: list4;
    l.fill(0);
    l.at(0) = 9;
    l.at(0) += 1;
    println(f"l.at(0) -> {l.data[0]}");                // 10

    let a: wrap;
    a.l.fill(0);            // the receiver can itself be a field access
    a.view().at(2) = 7;     // chained store target
    a.second() = 5;         // and the formation-chained accessor
    println("a.l.data = [{}, {}, {}, {}]".format(
            a.l.data[0], a.l.data[1], a.l.data[2], a.l.data[3]));  // 0 5 7 0

    // A generic receiver binds T exactly as the `::` call does -- and a
    // dot call never spells type arguments (`p.m<int32>(...)` reads `<` as
    // a comparison and fails on the bare member access): method type
    // parameters are inference-only, as at a `::` call.
    let p: point<int32> = { x = 3, y = 4 };
    println(f"p.sum() = {p.sum()}");                   // 7

    // Builtin receivers dispatch their canonical family: with std/char
    // imported, 'q'.upper() is char::upper('q'), and each link of a chain
    // is just the next call's receiver.
    println("'q'.upper() = '{}'".format('q'.upper()));         // Q
    println("chained: '{}'".format('a'.upper().lower()));      // a

    let arr: int32[4] = [11, 22, 33, 44];
    println(f"first = {(arr as slice<int32>).first()}");  // 11

    // A STRING LITERAL receiver adapts to slice<const char> so the slice
    // family reaches it: "hello".first() is slice<char>::first("hello" as
    // slice<const char>), the same borrow types.strings.mc documents at a
    // let, an argument, or a struct field -- now at a dot-call receiver too.
    // The adaptation is a pure fallback (only when the literal's own char[N]
    // resolves no method of the name) and literal-only: it never shadows the
    // char* decay a C binding needs (strlen("hi") still passes a pointer),
    // and a NAMED char[N] receiver does not adapt.
    println("\"hello\".first() = '{}'".format("hello".first()));  // h

    // f-string holes take dot calls like any expression.
    println(f"{p.sum() = }");                           // p.sum() = 7

    return 0;
}

// See also: methods.mc and generic_methods.mc for the qualified families
// every dot call desugars into; method_specialization.mc and
// method_partial_specialization.mc for the ranking a dot call inherits
// unchanged; method_alias.mc for the qualifier chase behind alias and
// builtin receivers; constructors.mc for `S(args)`, the head-side sibling
// of this call-side sugar; functions/mut_returns.mc for the `-> mut`
// formation and re-lending rules the lvalue dot calls ride on;
// method_inheritance.mc for a dot call resolving over a base's family
// through `extends`; systems/char_methods.mc for the std/char family
// behind 'q'.upper().
