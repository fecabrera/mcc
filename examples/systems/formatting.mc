import "std/format";
import "std/string";
import "std/io";
import "libc/stdio";

// The stdlib `format` module: the formatting protocol's baseline overload
// set. Every member has the shape
//
//     format(mut str: string, value: X, const modifier: slice<char>)
//
// and appends value's rendering to str, with modifier steering the spelling
// ("" picks the default). Because the modifier is a char slice, a bare
// string literal adapts to it directly at the call. This file makes direct
// format() calls over mixed value types, steers integers with "x" / "p",
// rounds and pads floats with ".2f" / "8.2f",
// renders slices (nested too), hits the <typename> fallback, and then makes
// its own struct printable by declaring one more overload into the set.
// The finale moves to the consumers: print/println write ONE string
// verbatim, so formatting stays with the producers -- the `.format`
// method behind `{}` placeholders and their positional `{n}` sugar
// (duplicate, reorder, `{0:x}`, and the `{:N}` width escape), and
// f-string interpolation: `{expr}` holes written inline, with the
// Python-style `{n=}` inspector, rendering to a string value println
// binds directly.
// Builds on io.mc (raw printf, used here to print the results),
// functions/open_overloads.mc (how a module joins a foreign overload set),
// types/type_groups.mc (the closed integer groups behind the set), and
// memory/slices.mc.

struct point {
    x: int32;
    y: int32;
}

// The protocol move: one concrete overload in this module and point is
// printable. A concrete member outranks format.mc's closed-group templates
// and its unbounded fallback, and the field calls re-enter the
// whole-program set, so the caller's modifier steers the fields too.
fn format(mut str: string, value: struct point*, const modifier: slice<char>) {
    str.push('(');
    format(str, value!->x, modifier);
    str.append(", ");
    format(str, value!->y, modifier);
    str.push(')');
}

// A string is a growable list<char> ({data, length}, no NUL terminator);
// printf's %.*s prints exactly length bytes. The reset keeps the buffer
// for the next line.
fn show(label: char*, mut line: string) {
    printf("%-10s%.*s\n", label, line.length as int32, line.data);
    line.reset();
}

fn main() -> int32 {
    let line = string();

    // Mixed values into one string, one call per value. A bare "" is the
    // default modifier, adapting to the slice<char> parameter directly.
    // Integer values must be typed: an untyped 42 is ambiguous between the
    // int64 and char members. -4 as int32 rides the closed signed group,
    // which sign-extends into the concrete int64 worker.
    format(line, -4 as int32, "");           // -4
    format(line, ' ', "");                   // char: appended as-is
    format(line, 3.5, "");                   // float64: 3.500000
    format(line, ' ', "");
    format(line, true, "yes");               // "yes" spells it yes
    format(line, ' ', "");
    format(line, "text", "");                // a literal decays to char*
    show("mixed:", line);

    // Integer modifiers, passed as bare literals -- the grammar is
    // [0][width][x|X|b|p]: "x" lowercase hex, "X" uppercase, "b" binary,
    // "p" pointer-style, with an optional width ("6x" pads the field with
    // spaces) and a leading 0 for zero-padding ("06x" pads the digits; the
    // sign and "0x" sit outside the zeros). A negative value renders
    // sign-and-magnitude -- the base applies to |value| -- so its hex is
    // '-' and the magnitude's digits, never a two's-complement pattern.
    format(line, 255 as uint8, "x");         // unsigned group: ff
    format(line, ' ', "");
    format(line, -4 as int32, "x");          // -4
    format(line, ' ', "");
    format(line, 5 as int32, "b");           // 101
    format(line, ' ', "");
    format(line, 42 as int64, "p");          // 0x2a
    format(line, ' ', "");
    format(line, 255 as int32, "06x");       // 0000ff
    show("hex:", line);

    // Float modifiers -- the grammar is [[N].M]f: ".M" rounds to M
    // decimals (".0f" drops the point entirely), and an optional leading
    // width N space-pads the whole field, sign included, right-aligned.
    // A bare "f" (or "") keeps the six-decimal default seen above. The
    // rendering is snprintf's %*.*f, so the rounding is the C library's;
    // scientific notation is the one float spelling still left to raw
    // printf (%g / %e).
    format(line, 3.14159, ".2f");            // 3.14
    format(line, ' ', "");
    format(line, 3.7, ".0f");                // 4
    format(line, ' ', "");
    format(line, -3.5, "8.2f");              // "   -3.50": 8 wide, sign inside
    show("float:", line);

    // slice<T> renders a bracketed list. Each element re-enters the set, so
    // the modifier applies per element and nesting recurses.
    let bytes: int32[3] = [10, 255, 3];
    format(line, bytes as slice<int32>, "x");    // [a, ff, 3]
    show("slice:", line);

    let head: int32[3] = [1, 2, 3];
    let tail: int32[2] = [4, 5];
    let rows: slice<int32>[2] = [head as slice<int32>, tail as slice<int32>];
    format(line, rows as slice<slice<int32>>, "");    // [[1, 2, 3], [4, 5]]
    show("nested:", line);

    // slice<char*> has its own concrete member that beats the generic list
    // renderer, quoting each C string (argv-style).
    let cmd: char*[3] = ["cp", "-r", "src"];
    format(line, cmd as slice<char*>, "");    // ["cp", "-r", "src"]
    show("argv:", line);

    // A type with no member lands on the unbounded format<T> fallback,
    // which renders the type's name instead of a value.
    let b: uint8 = 7;
    format(line, &b, "");                    // <uint8*>
    show("fallback:", line);

    // The overload declared above: point is now as printable as the
    // builtins, and the caller's modifier reaches its fields.
    let p = point { x = 3, y = 255 };
    format(line, &p, "");                    // (3, 255)
    format(line, ' ', "");
    format(line, &p, "x");                   // (3, ff)
    show("point:", line);

    // The `.format` method (std/slice) is this same set behind `{}`
    // placeholders: each `{[modifiers]}` renders the next argument, the
    // bracket content arriving verbatim as the modifier -- the float
    // precision `{.2f}` and string width `{s6}` included -- into an owned
    // string. println itself formats nothing: it writes one string
    // verbatim, braces and all, which is exactly what .format hands it.
    // The point* overload above answers the last placeholder.
    println("format:   {} {x} {.2f} {yes} {s6}| {x}".format(
            -4, 255 as uint8, 3.5, true, "mc", &p));

    // Positional placeholders: when the format string is a *literal*, `{n}`
    // selects the n-th argument (0-based) instead of taking the next one.
    // This is compile-time sugar: the call desugars to the sequential form
    // by duplicating or reordering the arguments (each argument still
    // evaluates once, in source order), so the runtime parser above never
    // sees an index. The dot call's own receiver borrow is seen through:
    // "...".format(...) desugars exactly like the explicit
    // ("..." as slice<const char>).format(...) would.
    println("repeat:   {0} and {0} and {0}".format(42)); // one arg, three renderings
    println("reorder:  {1} before {0}".format("second", "first"));

    // A colon separates the index from the modifiers, and the modifier text
    // is the same grammar as ever: {0:x} desugars to {x} on a duplicated
    // argument.
    println("hex:      {0} is {0:x}, padded {0:06x}".format(255));

    // The digits claimed by argument selection cost one spelling: a *bare*
    // field width in a literal is now the index-less escape {:N}, which
    // desugars to the runtime {2} width (an f-string spells the same width
    // as a plain colon modifier, {expr:2} -- control-flow/while.mc's table
    // does). Digit-leading modifiers with a base letter ({06x}) stay plain
    // modifiers.
    println("width:    [{:2}] [{:2}]".format(3, 9));     // [ 3] [ 9]
    println("width:    [{1:2}] [{0:2}]".format(3, 9));   // [ 9] [ 3]

    // One literal commits to one style, and the positional style must be
    // total. Each of these is a compile error:
    //   "{} {0}".format(1)       mixes automatic {} with positional {0}
    //                            ({:N} counts as automatic, so the two
    //                            width lines above cannot merge into one)
    //   "{2}".format(1, 2)       index out of range: arguments are 0 and 1
    //   "{0}".format(1, 2)       the 2 is never referenced
    // A format string arriving through a variable is untouched: it hits the
    // sequential runtime parser, where {2} is always the field width.

    // F-strings write the expressions inline: an f-prefixed literal holds
    // `{expr}` holes. At an @format call's format string (a .format
    // literal, or your own collector's) it splices at compile time into
    // the sequential form above. println has no format slot, so there the
    // literal renders instead into a string VALUE -- a synthesized .format
    // call, f"n is {n}" IS "n is {}".format(n), an owned string println
    // binds directly. Either way the runtime parser never sees the holes.
    let n = 7 as int32;
    println(f"fstring:  n is {n}, twice {n * 2}");

    // The f prefix is what keeps the two brace grammars apart: in the plain
    // literals above {x} was the hex *modifier*, here it is the *expression*
    // x. A colon left over after the expression parse carries a runtime
    // modifier through ({x:08x} renders like {08x} did) -- and only a
    // *leftover* colon starts it, so a ternary's own colon stays inside
    // the hole, with or without a modifier after the expression.
    let hx = 255 as int32;
    println(f"fmod:     {hx:08x} then {n > 5 ? hx : n} as hex {n > 5 ? hx : n:x}");

    // The inspector, Python's spelling and semantics: {n=} splices the
    // hole's verbatim source text, up to and including the =, in front of
    // the value -- whitespace preserved, so {n = } keeps your spaces. A
    // modifier still composes after the =. An == is consumed by the
    // expression parse (a lone trailing = is what marks the inspector),
    // so {n == 7} is the comparison and {n == 7=} labels it.
    println(f"inspect:  {n=} {n = } {hx=:08x}");
    println(f"compare:  {n == 7} vs {n == 7=}");

    // {{ and }} still escape literal braces (braces inside a hole's nested
    // string or char literals need no escape at all).
    println(f"brace:    {{{n}}}");                       // {7}

    // The rendering is a first-class owned string, so it flows past
    // println too: a let adopts it (destroyed at scope end), methods
    // chain off the rvalue, an argument's temporary drops at statement
    // end. types/fstring_values.mc walks that ownership story end to end.
    let kept = f"kept:     n is {n}, hex {hx:x}";
    println(kept);
    if (f"{n * 2}".equals("14"))         // an rvalue receiver: renders once,
        println("chain:    equals ok");  // drops when the chain ends

    // An f-string is its own placeholder style. Each of these is a
    // compile error:
    //   logf(f"{n}", 9)         at an @format collector of your own
    //                           (fn logf(@format const fmt: ..., args...))
    //                           the holes already supply every argument:
    //                           "'logf' takes no arguments after an
    //                           f-string" -- and {} / {n} never mix in. At
    //                           println, a verbatim writer, the same
    //                           mistake is the plain no-overload error:
    //                           no println(list<char>, int32) exists
    //   println(f"{}")          a hole must hold an expression: empty {},
    //                           bare {:x}, a stray } or an unclosed { all
    //                           reject
    // A hole-free f"abc" keeps its f-string identity: it still renders (a
    // heap string), and its {{ }} escapes still collapse, where the plain
    // literal "{{}}" goes out verbatim (as it always did) like every
    // other string.

    return 0;
}

// See also: io.mc (the raw printf side of formatted output),
// functions/open_overloads.mc (the open-set mechanics this protocol rides
// on), types/type_groups.mc (the closed signed/unsigned groups behind the
// integer members), memory/slices.mc (the slice views rendered here),
// types/fstring_values.mc (the f-string as an owned string value: adoption,
// drops, chaining, transfer, and the `as slice<const char>` hatch).
// Full reference: docs/language.md, "Formatting".
