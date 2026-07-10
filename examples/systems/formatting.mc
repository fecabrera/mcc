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
// renders slices (nested too), hits the <typename> fallback, and then makes
// its own struct printable by declaring one more overload into the set.
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
    string_push(str, '(');
    format(str, value!->x, modifier);
    string_append(str, ", ");
    format(str, value!->y, modifier);
    string_push(str, ')');
}

// A string is a growable list<char> ({data, length}, no NUL terminator);
// printf's %.*s prints exactly length bytes. The reset keeps the buffer
// for the next line.
fn show(label: char*, mut line: string) {
    printf("%-10s%.*s\n", label, line.length as int32, line.data);
    string_reset(line);
}

fn main() -> int32 {
    let line: string;
    string_init(line);
    defer string_destroy(line);

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

    // println is this same set behind `{}` placeholders: each `{[modifiers]}`
    // renders the next argument, the bracket content arriving verbatim as the
    // modifier. The point* overload above answers the last placeholder.
    println("println:  {} {x} {yes} {x}", -4, 255 as uint8, true, &p);

    return 0;
}

// See also: io.mc (the raw printf side of formatted output),
// functions/open_overloads.mc (the open-set mechanics this protocol rides
// on), types/type_groups.mc (the closed signed/unsigned groups behind the
// integer members), memory/slices.mc (the slice views rendered here).
// Full reference: docs/language.md, "Formatting".
