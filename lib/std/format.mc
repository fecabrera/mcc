import "std/string";
import "std/equality";
import "libc/stdio";
import "libc/stdlib";

// The formatting protocol's baseline overload set: every member appends
// `value`'s rendering to `str`, steered by `modifier` (`""` for the
// default; a string literal adapts directly, so `format(s, 255 as int32,
// "x")` works as-is — the value must be typed, an untyped 255 is ambiguous
// among the integer and char members). std/io's print/println dispatch
// here: each `{[modifiers]}` placeholder renders its argument through this
// set, the bracket content arriving verbatim as the modifier. Overload
// sets are open, so making a type printable is adding one `format`
// overload for it in your own module — a concrete member outranks the
// closed-group templates and the unbounded fallback.

// Scratch space for the one snprintf-rendered member (float64).
@private const MAX_BUF_LEN = 256;

// Digit tables for the hand-rolled integer worker, lowercase / uppercase.
@private const _hex = "0123456789abcdef";
@private const _hexu = "0123456789ABCDEF";

// The integer modifier mini-parser's states, one per grammar position:
// `[0][width][x|X|b|p]` — the zero-pad flag, the width digits, then the
// base letter (see _format below).
@private
enum int_fmt_state {
    PADDING = 0,
    LENGTH = 1,
    FORMAT = 2,
}

@private
enum str_fmt_state {
    LPADDING = 0,
    FORMAT = 1,
    RPADDING = 2,
}

// The float modifier mini-parser's states, one per grammar position:
// `[[N].M]f` — the field-width digits, then the decimal count behind the
// '.' (see the float64 member below).
@private
enum float_fmt_state {
    LENGTH = 0,
    PRECISION = 1,
}

/**
 * Fallback for types with no formatter: appends the type's name in angle
 * brackets (e.g. `<uint8*>`) instead of a value.
 *
 * Unbounded, so it loses to every concrete and closed-group overload in
 * the set and only catches what they don't.
 *
 * @param str:      destination string
 * @param value:    unused; only its type is rendered
 * @param modifier: ignored
 */
fn format<T>(mut str: string, value: T, const modifier: slice<char>) {
    string_push(str, '<');
    string_append(str, typename(T)!);
    string_push(str, '>');
}

/**
 * Appends a signed integer's rendering to str.
 *
 * One closed-group overload covers the narrow signed widths by
 * sign-extending into the int64 worker below; the unsigned group further
 * down takes the unsigned ones, so `{}` dispatch picks the right default
 * conversion at the function level.
 *
 * @param str:      destination string
 * @param value:    signed integer to render
 * @param modifier: `[0][width][x|X|b|p]` (see _format below); no base
 *                  letter renders signed decimal
 */
@inline
fn format<T: int32 | int16 | int8>(mut str: string, value: T, const modifier: slice<char>) {
    format(str, value as int64, modifier);
}

/**
 * Appends an int64's rendering to str.
 *
 * The signed worker: the closed-group overload above funnels the narrow
 * widths here, already sign-extended. Renders sign-and-magnitude — a
 * leading '-' and then |value| through the shared digit worker — so a
 * base modifier applies to the magnitude: `-4` with "x" is `-4`, never a
 * two's-complement bit pattern (render the pattern by casting the bits
 * unsigned instead). The magnitude is taken by the two's-complement
 * negation `(~num) + 1` in uint64 space, so int64's minimum — which has
 * no int64 magnitude — still renders exactly.
 *
 * @param str:      destination string
 * @param value:    signed integer to render
 * @param modifier: `[0][width][x|X|b|p]` (see _format below); no base
 *                  letter renders signed decimal
 */
fn format(mut str: string, value: int64, const modifier: slice<char>) {
    let neg = false;
    let num = value as uint64;
    if (value < 0) {
        neg = true;
        num = (~num) + 1;
    }
    _format(str, num, modifier, neg);
}

/**
 * Appends an unsigned integer's rendering to str.
 *
 * The unsigned half of the closed-group pair: the narrow widths widen
 * into the concrete uint64 worker below, mirroring the signed funnel.
 *
 * @param str:      destination string
 * @param value:    unsigned integer to render
 * @param modifier: `[0][width][x|X|b|p]` (see _format below); no base
 *                  letter renders unsigned decimal
 */
@inline
fn format<T: uint32 | uint16 | uint8>(mut str: string, value: T, const modifier: slice<char>) {
    format(str, value as uint64, modifier);
}

/**
 * Appends a uint64's rendering to str: the public face of the digit
 * worker every integer member funnels into.
 *
 * @param str:      destination string
 * @param value:    unsigned integer to render
 * @param modifier: `[0][width][x|X|b|p]` (see _format below); no base
 *                  letter renders decimal
 */
@inline
fn format(mut str: string, value: uint64, const modifier: slice<char>) {
    _format(str, value, modifier, false);
}

/**
 * The integer digit worker: renders a magnitude in the modifier's base,
 * signed and padded, and appends it to str.
 *
 * Hand-rolled, no snprintf round-trip: digits are produced last-first
 * into a scratch string in the chosen base, then appended in reverse.
 *
 * The modifier grammar is `[0][width][x|X|b|p]`, parsed by the
 * int_fmt_state machine: an optional leading '0' selects zero-padding,
 * an optional decimal width pads the rendering, and the final letter
 * picks the base — "x"/"X" lower/uppercase hex, "b" binary, "p"
 * pointer-style ("0x" + lowercase hex); no letter renders decimal. The
 * two pads count differently: a space pad ("8x") widens the whole field
 * (sign and "0x" included), while a zero pad ("08x") widens the digits
 * alone, the sign and "0x" sitting outside the zeros — `-42` under "08p"
 * is `-0x0000002a`.
 *
 * @param str:      destination string
 * @param value:    the magnitude to render, already made unsigned
 * @param modifier: `[0][width][x|X|b|p]`, as above
 * @param neg:      whether to render a leading '-' before the magnitude
 */
@private
fn _format(mut str: string, value: uint64, const modifier: slice<char>, neg: bool) {
    let base: uint64 = 10;
    let pointer = false;
    let mayus = false;
    let zero = false;
    let padding: uint32 = 0;
    let state = int_fmt_state::PADDING;

    let i: uint64 = 0;
    while (i < modifier.length) {
        let c = modifier[i];
        case (state) {
        when int_fmt_state::PADDING:
            if (c == '0') {
                zero = true;
            } else if (c > '0' and c <= '9') {
                state = int_fmt_state::LENGTH;
                continue;
            } else {
                state = int_fmt_state::FORMAT;
                continue;
            }
        when int_fmt_state::LENGTH:
            if (c >= '0' and c <= '9') {
                padding *= 10;
                padding += (c - '0') as uint32;
            } else {
                state = int_fmt_state::FORMAT;
                continue;
            }
        when int_fmt_state::FORMAT:
            if (c == 'p') {
                pointer = true;
                base = 16;
            } else if (c == 'b') {
                base = 2;
            } else if (c == 'x') {
                base = 16;
            } else if (c == 'X') {
                base = 16;
                mayus = true;
            }
            break;
        }
        i += 1;
    }

    let buf: string;
    string_init(buf);
    defer string_destroy(buf);

    let num = value;
    while (true) {
        string_push(buf, mayus ? _hexu![num % base] : _hex![num % base]);
        num = num / base;
        if (num == 0) break;
    }

    if (neg) {
        if (pointer) {
            if (zero) {
                string_push(str, '-');
                string_append(str, "0x");
            } else {
                string_append(buf, "x0");
                string_push(buf, '-');
            }
        } else {
            if (zero) {
                string_push(str, '-');
            } else {
                string_push(buf, '-');
            }
        }
    } else if (pointer) {
        if (zero) {
            string_append(str, "0x");
        } else {
            string_append(buf, "x0");
        }
    }
    
    if (padding > buf.length) {
        for i in range(padding - buf.length) {
            string_push(str, zero ? '0' : ' ');
        }
    }

    for i in range(buf.length) {
        string_push(str, buf.data![buf.length - i - 1]);
    }
}

/**
 * Appends a float64's fixed-point rendering to str.
 *
 * The modifier grammar is `[[N].M]f`, parsed by the float_fmt_state
 * machine: a '.' and a decimal count round the value to M decimals
 * (".2f" renders `3.50`, ".0f" drops the point entirely), and an
 * optional leading decimal width space-pads the whole field, sign
 * included ("8.2f" renders `    3.50`). An empty modifier — or a bare
 * "f" — renders the six-decimal default. The rendering stays
 * snprintf's: the parsed width and precision feed a `%*.*f`, so the
 * rounding is the C library's.
 *
 * @param str:      destination string
 * @param value:    value to render
 * @param modifier: `[[N].M]f`, as above; `""` renders the six-decimal
 *                  default
 */
fn format(mut str: string, value: float64, const modifier: slice<char>) {
    let width: int32 = 0;
    let precision: int32 = 6;
    let state = float_fmt_state::LENGTH;

    let i: uint64 = 0;
    while (i < modifier.length) {
        let c = modifier[i];
        case (state) {
        when float_fmt_state::LENGTH:
            if (c >= '0' and c <= '9') {
                width *= 10;
                width += (c - '0') as int32;
            } else if (c == '.') {
                precision = 0;
                state = float_fmt_state::PRECISION;
            } else {
                break;
            }
        when float_fmt_state::PRECISION:
            if (c >= '0' and c <= '9') {
                precision *= 10;
                precision += (c - '0') as int32;
            } else {
                break;
            }
        }
        i += 1;
    }

    let buf: char[MAX_BUF_LEN];
    snprintf(buf, MAX_BUF_LEN, "%*.*f", width, precision, value);
    string_append(str, buf);
}

/**
 * Appends a bool's rendering to str.
 *
 * @param str:      destination string
 * @param value:    value to render
 * @param modifier: "y" renders y/n, "yes" renders yes/no; anything else
 *                  renders true/false
 */
fn format(mut str: string, value: bool, const modifier: slice<char>) {
    if (equals(modifier, "y"))
        string_append(str, value ? "y" : "n");
    else if (equals(modifier, "yes"))
        string_append(str, value ? "yes" : "no");
    else
        string_append(str, value ? "true" : "false");
}

/**
 * Appends a slice's elements as a bracketed list, `[1, 2, 3]`.
 *
 * Each element formats through the overload set again, so nesting works
 * (`slice<slice<int32>>` renders `[[..], [..]]`) and elements with no
 * formatter fall back to `<typename>`. `slice<char>` and `slice<char*>`
 * never land here: their concrete overloads below beat this generic one
 * and render text and a quoted list respectively.
 *
 * @param str:      destination string
 * @param value:    slice whose elements to render
 * @param modifier: applied to every element (e.g. "x" renders each
 *                  integer as hex)
 */
fn format<T>(mut str: string, const value: slice<T>, const modifier: slice<char>) {
    string_push(str, '[');
    for item in enumerate(value) {
        if (item.index > 0) string_append(str, ", ");
        format(str, item.value, modifier);
    }
    string_push(str, ']');
}

/**
 * Appends a string slice's bytes to str, space-padded to a field width.
 *
 * The modifier grammar is `[N][s][N]`, parsed by the str_fmt_state
 * machine: digits before the `s` right-align the text in an N-wide field
 * (`"20s"`, or a bare `"20"`), digits after it left-align (`"s20"`).
 * Text already at or past the width appends unpadded; an empty modifier
 * appends the bytes verbatim.
 *
 * @param str:      destination string
 * @param value:    slice to append (its `length` bytes, no NUL needed)
 * @param modifier: `[N][s][N]`, as above; `""` appends unpadded
 */
@inline
fn format(mut str: string, const value: slice<const char>, const modifier: slice<char>) {
    let state = str_fmt_state::LPADDING;
    let l_pad: uint64 = 0;
    let r_pad: uint64 = 0;
    
    let i: uint64 = 0;
    while (i < modifier.length) {
        let c = modifier[i];
        case (state) {
        when str_fmt_state::LPADDING:
            if (c >= '0' and c <= '9') {
                l_pad *= 10;
                l_pad += (c - '0') as uint64;
            } else {
                state = str_fmt_state::FORMAT;
                continue;
            }
        when str_fmt_state::FORMAT:
            state = str_fmt_state::RPADDING;
            if (l_pad) break;
        when str_fmt_state::RPADDING:
            if (c >= '0' and c <= '9') {
                r_pad *= 10;
                r_pad += (c - '0') as uint64;
            } else {
                break;
            }
        }
        i += 1;
    }

    if(l_pad > value.length) {
        for i in range(l_pad - value.length) {
            string_push(str, ' ');
        }
    }

    string_append(str, value);

    if(r_pad > value.length) {
        for i in range(r_pad - value.length) {
            string_push(str, ' ');
        }
    }
}

/**
 * Appends a C string's bytes to str, space-padded to a field width.
 *
 * A null pointer renders `(null)` (no longer undefined); anything else
 * wraps in a strlen-measured slice and delegates to the string-slice
 * member above, so the same `[N][s][N]` width grammar applies.
 *
 * @param str:      destination string
 * @param value:    NUL-terminated string, or null for `(null)`
 * @param modifier: `[N][s][N]` (see the string-slice member above)
 */
@inline
fn format(mut str: string, value: char*, const modifier: slice<char>) {
    if (value == null) {
        string_append(str, "(null)");
        return;
    }

    let s = slice<char> {
        data = value,
        length = strlen(value),
    };
    format(str, s as slice<const char>, modifier);
}

/**
 * Appends a slice of C strings as a quoted, bracketed list, `["ls", "cat"]`.
 *
 * Concrete, so it beats the generic slice list-renderer above, which
 * would render the elements unquoted through the char* member.
 *
 * @param str:      destination string
 * @param value:    slice of NUL-terminated strings; elements must not be
 *                  null (asserted with the `!` hatch, undefined if one is)
 * @param modifier: ignored
 */
fn format(mut str: string, const value: slice<char*>, const modifier: slice<char>) {
    string_push(str, '[');
    for item in enumerate(value) {
        if (item.index > 0) string_append(str, ", ");
        string_push(str, '"');
        string_append(str, item.value!);
        string_push(str, '"');
    }
    string_push(str, ']');
}

/**
 * Appends a single character to str.
 *
 * @param str:      destination string
 * @param value:    character to append
 * @param modifier: ignored
 */
@inline
fn format(mut str: string, value: char, const modifier: slice<char>) {
    string_push(str, value);
}
