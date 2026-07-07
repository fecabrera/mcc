import "format";
import "string";
import "libc/stdio";

// The stdlib `format` module: the formatting protocol's baseline overload
// set. Every member has the shape
//
//     format(mut str: string, value: X, const modifier: string)
//
// and appends value's rendering to str, with modifier steering the spelling
// ("" picks the default). This file makes direct format() calls over mixed
// value types, steers integers with ":x" / ":p", renders slices (nested
// too), hits the <typename> fallback, and then makes its own struct
// printable by declaring one more overload into the set.
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
fn format(mut str: string, value: struct point*, const modifier: string) {
    string_push(str, '(');
    format(str, value->x, modifier);
    string_append(str, ", ");
    format(str, value->y, modifier);
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
    // The modifier parameter is a string *value*: a bare ":x" literal at a
    // format call stays a char* and matches no member. Build each modifier
    // once with string_init (the literal adapts there) and reuse it.
    let plain: string;
    string_init(plain);              // empty modifier: every member's default
    defer string_destroy(plain);
    let hex: string;
    string_init(hex, ":x");
    defer string_destroy(hex);
    let ptr_style: string;
    string_init(ptr_style, ":p");
    defer string_destroy(ptr_style);
    let yesno: string;
    string_init(yesno, ":yes");
    defer string_destroy(yesno);

    let line: string;
    string_init(line);
    defer string_destroy(line);

    // Mixed values into one string, one call per value. Integer values must
    // be typed: an untyped 42 is ambiguous between the int64 and char
    // members. -4 as int32 rides the closed signed group, which
    // sign-extends into the concrete int64 worker.
    format(line, -4 as int32, plain);        // -4
    format(line, ' ', plain);                // char: appended as-is
    format(line, 3.5, plain);                // float64: 3.500000
    format(line, ' ', plain);
    format(line, true, yesno);               // ":yes" spells it yes
    format(line, ' ', plain);
    format(line, "text", plain);             // a literal decays to char*
    show("mixed:", line);

    // Integer modifiers: ":x" lowercase hex, ":X" uppercase, ":p"
    // pointer-style. A negative narrow value was already sign-extended when
    // the modifier applies, so its hex is the full 64-bit two's-complement
    // pattern.
    format(line, 255 as uint8, hex);         // unsigned group: ff
    format(line, ' ', plain);
    format(line, -4 as int32, hex);          // fffffffffffffffc
    format(line, ' ', plain);
    format(line, 42 as int64, ptr_style);    // 0x2a
    show("hex:", line);

    // slice<T> renders a bracketed list. Each element re-enters the set, so
    // the modifier applies per element and nesting recurses.
    let bytes: int32[3] = [10, 255, 3];
    format(line, bytes as slice<int32>, hex);    // [a, ff, 3]
    show("slice:", line);

    let head: int32[3] = [1, 2, 3];
    let tail: int32[2] = [4, 5];
    let rows: slice<int32>[2] = [head as slice<int32>, tail as slice<int32>];
    format(line, rows as slice<slice<int32>>, plain);    // [[1, 2, 3], [4, 5]]
    show("nested:", line);

    // A type with no member lands on the unbounded format<T> fallback,
    // which renders the type's name instead of a value.
    let b: uint8 = 7;
    format(line, &b, plain);                 // <uint8*>
    show("fallback:", line);

    // The overload declared above: point is now as printable as the
    // builtins, and the caller's modifier reaches its fields.
    let p = point { x = 3, y = 255 };
    format(line, &p, plain);                 // (3, 255)
    format(line, ' ', plain);
    format(line, &p, hex);                   // (3, ff)
    show("point:", line);

    return 0;
}

// See also: io.mc (the raw printf side of formatted output),
// functions/open_overloads.mc (the open-set mechanics this protocol rides
// on), types/type_groups.mc (the closed signed/unsigned groups behind the
// integer members), memory/slices.mc (the slice views rendered here).
// Full reference: docs/language.md, "Formatting".
