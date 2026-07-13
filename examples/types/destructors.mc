import "std/io";

// Destructors: automatic cleanup of stack-constructed values. A method
// named `destructor` is the other half of the constructor pair
// (constructors.mc): when a type declares one (or inherits it through
// `extends`), the constructor-sugar let schedules the cleanup call on the
// enclosing block's defer stack --
//
//     let f = file(3);
//     // == let f: file;
//     //    file::constructor(f, 3);
//     //    defer file::destructor(f);
//
// -- so the value is destroyed when its scope exits, however it exits.
// The trigger surface is EXACTLY that let: `let t = T(args);` or
// `let t = T();` (a declared or implicit empty constructor alike).
// Everything else -- manual construction, a struct-literal let, a copy,
// plain assignment -- is a documented opt-out spelling that schedules
// nothing, demonstrated at the end of main.
//
// The scheduled call shares the defer machinery verbatim
// (control-flow/defer.mc): it runs LIFO with explicit defers (values
// destroy in reverse construction order), per iteration in a loop body,
// and on every unwinding exit -- early return, break, continue, bare-try
// propagation. As with any defer, a @noreturn exit runs no destructors.
//
// Prerequisites: constructors.mc for the sugar the let form rides on,
// control-flow/defer.mc for the machinery, and extends.mc plus
// method_inheritance.mc for the derived types at the end.

// ---- The RAII shape: acquire in the constructor, release in the
// ---- destructor, and no call site ever spells the cleanup

struct file {
    fd: int32;
}

fn file::constructor(mut self: file, fd: int32) {
    println("  open  fd {}", fd);
    self.fd = fd;
}

fn file::destructor(mut self: file) {
    // The destructor sees the value's LATEST state, mutations after
    // construction included (main reassigns an fd below and the close
    // line proves it).
    println("  close fd {}", self.fd);
}

// Every exit path closes every constructed handle, and no path spells the
// cleanup: the early return closes src as it unwinds, and the fall-through
// return closes dst then src, reverse construction order.
fn transfer(broken: bool) -> int32 {
    let src = file(3);
    if (broken) {
        return -1;              // src closed here...
    }
    let dst = file(4);
    return 0;                   // ...and here -- dst first, then src
}

// ---- Inheritance: the automatic call resolves through the merged
// ---- family (method_inheritance.mc), base cleanup chains MANUALLY

struct logfile extends file {
    lines: int32;
}

fn logfile::constructor(mut self: logfile, fd: int32, lines: int32) {
    file::constructor(self, fd);    // base construction chains manually...
    self.lines = lines;
}

fn logfile::destructor(mut self: logfile) {
    println("  flush {} lines", self.lines);
    file::destructor(self);         // ...and so does base cleanup
}

// A derived type that declares NO destructor of its own inherits the
// base's: the automatic call resolves through the merged family, receiver
// upcast included, so a constructed pipe still closes its fd.
struct pipe extends file;

fn main() -> int32 {
    // The RAII payoff. Both calls print their own open/close trace.
    println("transfer(true):");
    let early = transfer(true);
    println("transfer(false):");
    let full = transfer(false);
    println("  returned {} and {}", early, full);

    // One defer stack, strictly LIFO: values destroy in reverse
    // construction order, interleaved with explicit defers.
    println("LIFO with an explicit defer:");
    {
        let a = file(1);
        defer println("  explicit defer");
        let b = file(2);
    }                           // close 2, the explicit defer, close 1

    // A loop body is a scope per pass: each iteration's value is
    // destroyed at the end of that pass, before the next one opens.
    // break, continue, and bare-try propagation unwind the same way.
    println("per-iteration:");
    let i: int32 = 0;
    while (i < 3) {
        let scratch = file(10 + i);
        i += 1;
    }

    // Latest state: the close line prints 31, not the 30 it opened with.
    println("latest state:");
    {
        let log = file(30);
        log.fd = 31;
    }

    // Reverse construction order holds across the derived types too, and
    // pipe's inherited destructor closes fd 5 with the base's body.
    println("derived:");
    {
        let lg = logfile(7, 120);
        let p = pipe(5);
    }                           // close 5, then flush 120 lines + close 7

    // ---- The opt-outs: everything that is NOT the constructor-sugar
    // ---- let schedules nothing

    println("opt-outs:");

    // Manual construction: the user owns cleanup (call it, defer it, or
    // skip it). Destroy manually ONLY what you constructed manually: a
    // user-written destructor call beside an automatic one compiles and
    // destroys twice -- undefined behavior, exactly a C double-free.
    let m: file;
    file::constructor(m, 40);
    m.destructor();             // manual pairs with manual

    // A struct-literal let builds the value without the constructor
    // family and schedules nothing either.
    let lit = file{fd = 41};
    println("  fd {} was never opened, so it is never closed", lit.fd);

    // A copy is bitwise and never scheduled: no open prints for view, and
    // only the constructed original closes, once. If the type owns a
    // resource, both views name it -- exactly C's problem; copy with care.
    {
        let orig = file(42);
        let view = orig;
    }                           // one close: fd 42

    return 0;
}

// Returning or emitting the WHOLE auto-destructed local is a hard compile
// error: `return src;` inside transfer would copy the value out, then the
// unwind would destroy the original, so the caller would receive a copy of
// already-destroyed state. The message spells the hatches:
//
//     "cannot return 'src': its automatic destructor runs as the return
//      unwinds this scope, so the returned copy would escape its own
//      cleanup; return the constructor expression directly, or construct
//      manually (an uninitialized let plus a constructor call) and manage
//      cleanup yourself"
//
// `return file(9);` directly is legal -- an expression-position temporary
// owns no automatic cleanup, only the let form schedules -- and so is a
// field escape like `return src.fd;` (interior ownership is yours to
// reason about). The caller-side corollary of the first hatch: binding the
// call, `let f = make();`, is a COPY let, not constructor sugar, so the
// caller schedules nothing either -- a returned resource is managed
// manually.
//
// Two more edges. A const view is still destroyed: `let f: const file =
// file(1);` closes at scope exit, because destruction is scope teardown,
// not user mutation (a user-written f.destructor() on the const value
// keeps the ordinary mut-receiver error). And the scope is stack lets
// only: globals, @static values, parameters, heap values, and
// expression-position temporaries are never destroyed automatically.
//
// See also: constructors.mc for the sugar and its head forms;
// control-flow/defer.mc for the shared machinery and its @noreturn edge;
// extends.mc and method_inheritance.mc for the merged family the derived
// destructors resolve through; docs/language.md "Destructors" for the
// full rules.
