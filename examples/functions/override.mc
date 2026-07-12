import "std/io";

// @override: replace a same-pattern member of an open overload set declared
// in another module. Open sets (open_overloads.mc) let any module ADD an
// overload; the one thing adding cannot do is REPLACE a member that already
// covers a shape, because a second same-pattern definition collides as a
// duplicate. @override is the escape valve for exactly that case: it
// suppresses the collision and drops the original, so only this body is
// emitted under the member's shared symbol -- a global replacement, in effect
// everywhere the original was, regardless of import order.
//
// It is needed only for a SAME-PATTERN, same-rank member. Replacing
// group-covered or generic-covered behavior needs no annotation: a plain
// concrete overload already outranks a stdlib template (see the point member
// in systems/formatting.mc). What ordinary ranking cannot do is replace the
// stdlib's own concrete member, or its unbounded <typename> fallback, with one
// of the same pattern -- and that is what @override does.
//
// The two targets below are both members of std/format's `format` set (the
// protocol behind println's `{}`): a concrete `bool` formatter and the
// unbounded `<typename>` fallback. Because the replacement is global, even
// println's dispatch -- which resolves inside the stdlib module -- picks up
// these bodies through the shared symbols.

struct point {
    x: int32;
    y: int32;
}

// Replace the stdlib's concrete bool formatter (format.mc renders
// true/false or, with a modifier, y/n or yes/no). Same pattern as the
// original -- (string, bool, slice<char>) -- so @override is required; a
// plain overload here would be a duplicate-definition error. Cross-module and
// source-visible (format.mc is compiled from source alongside this file),
// which is what @override needs: no target, a same-file target, or a
// prototype-only target is a compile error.
@override
fn format(mut str: string, value: bool, const modifier: slice<char>) {
    string_append(str, value ? "ON" : "OFF");
}

// Replace the unbounded `<typename>` fallback -- the member that renders any
// type with no formatter of its own. Any struct without a concrete overload
// lands here, so `point` renders through this body. A type WITH a better
// candidate never reaches the fallback, so the replacement leaves int, bool
// (its concrete override above), slices, and the rest untouched.
@override
fn format<T>(mut str: string, value: T, const modifier: slice<char>) {
    string_append(str, "<unprintable>");
}

fn main() -> int32 {
    // The int keeps its own concrete formatter (it outranks the fallback):
    // only bool and the fallback were replaced.
    println("count = {}", 3);

    // bool now renders through the override, everywhere -- this direct call
    // and println's internal dispatch alike.
    println("ready = {}", true);
    println("ready = {y}", false);   // the modifier reaches the override too

    // point has no concrete formatter, so it falls to the overridden
    // <typename> fallback.
    let p = point { x = 4, y = 9 };
    println("origin = {}", p);

    return 0;
}

// Rules (docs/language.md, "Function overloading"): @override needs exactly
// one source-visible, body-bearing, cross-module target of the same pattern.
// A missing target (typo protection), a same-file target (@override replaces
// ANOTHER module's member), a prototype-only target (its body is in an object
// that already defines the symbol), or two @override of one pattern are all
// compile errors. @override does not combine with @extern, @static, @removed,
// a bodyless prototype, or (for now) @private.
//
// See also: open_overloads.mc (joining a set -- what @override extends),
// systems/formatting.mc (the format protocol and adding a struct member),
// types/removed.mc (@removed, the sibling value-supplier annotation).
