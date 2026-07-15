import "std/io";

// String-valued f-strings: outside an @format call's format string, an
// f-string is a runtime string VALUE. The literal desugars to a
// synthesized `slice::format` call -- f"x = {x}" IS "x = {}".format(x),
// an `-> own string` -- so the rendering flows like any other owned
// value (own_returns.mc): a let adopts it, an argument's temporary
// drops at statement end, methods chain off it, and a `return`
// transfers it out of an `-> own string` function. At a surviving
// @format slot (a "...".format(args) literal, or your own collector's)
// the compile-time splice still wins; the value path is everywhere
// else -- println, panic, and assert included, since std/io's writers
// are all verbatim single-string members with no format slot at all.
//
// Prerequisites: strings.mc (text basics), own_returns.mc (the own
// obligation, adoption, transfer), own_drops.mc (statement-end drops;
// its stamp style is reused here), and systems/formatting.mc for the
// hole grammar itself ({expr}, {expr:modifiers}, the {n=} inspector --
// a forward reference, systems/ comes later in the tour).
//
//   pipenv run python -m mcc examples/types/fstring_values.mc --run

// A drop-stamped resource to render inside holes: mk(id) hands over an
// own probe (own_returns.mc), and the format overload makes it
// printable (systems/formatting.mc), so the stamp shows exactly when
// the rendering machinery lets a hole's own temporary go.
struct probe {
    id: int32;
}

fn probe::constructor(self: &probe, id: int32) {
    self.id = id;
}

fn probe::destructor(self: &probe) {
    println(f"  drop {self.id}");
}

fn mk(id: int32) -> own probe {
    return probe(id);
}

fn format(str: &string, const value: &probe, const modifier: &slice<char>) {
    format(str, value.id, modifier);
}

// A string-taking consumer for the argument-position demo: the rendered
// temporary binds a `const string` parameter directly.
fn takes(const s: &const string) {
    println(f"  takes: {s as slice<const char>}");
}

// `return f"..."` from an `-> own string` function is a transfer
// source: the synthesized call chains the obligation through, no drop
// in this frame, and the caller's let adopts it (own_returns.mc).
fn greet(name: slice<const char>) -> own string {
    return f"hello {name}!";
}

// A concrete slice position for the escape-hatch demo.
fn show(const s: &slice<const char>) {
    print("  got ");
    println(s);
}

fn main() -> int32 {
    let x = 255 as int32;

    // ADOPTION: a let adopts the rendered string, destroying it at
    // scope end like any adopted own value. The typed spelling
    // `let t: string = f"..."` adopts the same way.
    println("adopt:");
    let s = f"x is {x}, hex {x:08x}";
    println(s);                       // -> x is 255, hex 000000ff
    let t: string = f"{x + 1=}";
    println(t);                       // -> x + 1=256

    // ZERO HOLES: a hole-free f-string still renders (a terse heap
    // constructor) and keeps its f-string identity -- its brace escapes
    // collapse, where a plain literal goes out verbatim (as always).
    println("zero-hole:");
    let h = f"no holes";
    println(h);                       // -> no holes
    println(f"  {{}}");               // ->   {}    (escapes collapse)
    println("  {{}}");                // ->   {{}}  (verbatim, braces and all)

    // ARGUMENT POSITION never adopts: the rendered temporary -- and the
    // own probe its hole handed over -- is destroyed at statement end,
    // after the callee returns, so the drop stamp lands below takes's
    // line. This hole own-temporary drop closed the follow-up
    // own_drops.mc recorded; it lands the same way at a compile-time
    // splice ("value {}".format(mk(3))).
    println("argument:");
    takes(f"value {mk(3)}");          // takes: value 3, then drop 3

    // CHAINING: an f-string receiver is an rvalue; the rendering spills
    // once, the method chains off it, and the temporary drops when the
    // full chain ends.
    println("chain:");
    if (f"{x}".equals("255"))
        println("  chain ok");

    // TRANSFER: greet's return hands the rendering out; the adopting
    // let here closes it at this scope's end.
    println("transfer:");
    let g = greet("world");
    println(g);                       // -> hello world!

    // DISCARD: a statement that renders and moves on destroys
    // everything it built at its own end -- the string and the hole's
    // probe alike.
    println("discard:");
    f"{mk(4)}";                       // drop 4

    // THE SPLICE STILL WINS: at an @format format-string slot -- a
    // .format literal, or a collector you declare -- the literal splices
    // at compile time, zero-cost and injection-free (hole values are
    // never re-scanned for braces). Past a plain format string a
    // collected f-string is an ordinary value argument: it renders
    // first, then formats as its text.
    println("splice:");
    println("  wrapped: {}".format(f"x = {x}"));  // -> wrapped: x = 255

    // THE ESCAPE HATCH: there is no implicit string-to-slice coercion,
    // so a concrete slice<const char> position reports the honest
    // mismatch -- show(f"{x}") is
    //   error: line N: f-string value: expected slice<const char>, got list<char>
    // The explicit borrow renders and lends the string's leading view.
    // Like any own call's `as` borrow it LEAKS the rendering (nothing
    // adopted it), so keep it for one-offs and prefer the adopting let.
    println("hatch:");
    show(f"{6 * 7} borrowed" as slice<const char>);   // -> got 42 borrowed

    println("done");
    return 0;
}

// Two positions a runtime rendering can never fill stay compile errors,
// both reported as "an f-string renders at runtime into an owned
// string, so it cannot form a compile-time constant or be addressed in
// place; bind it to a let first":
//   const S = f"{1 as int32}";       a const needs a compile-time value
//   @static let g: string = f"..";   ditto a global initializer
//   len(f"..") / &f".."              in-place addressing: the rendering
//                                    has no storage to lend; bind a let
//                                    and use .length
// And the value path needs the renderer in the import graph: without
// std/slice (std/io pulls it in transitively) the miss is "an f-string
// used as a value renders through 'slice::format'; import \"std/slice\"
// (or \"std/io\", which pulls it in) to build the string".
//
// See also: own_returns.mc for the obligation, adoption, and transfer
// this rides; own_drops.mc for the statement-end drop schedule (this
// feature closed its f-string-hole follow-up); strings.mc for the text
// basics; systems/formatting.mc for the hole grammar and the format
// overload set behind the rendering. Full rules: docs/language.md,
// "Formatted print / println", the "String-valued f-strings" note.
