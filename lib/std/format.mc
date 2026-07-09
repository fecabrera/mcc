import "std/string";
import "std/equality";
import "libc/stdio";

// The formatting protocol's baseline overload set: every member appends
// `value`'s rendering to `str`, steered by `modifier` (`""` for the
// default; a string literal adapts directly, so `format(s, 255, "x")`
// works as-is). Overload sets are open, so making a type printable is
// adding one `format` overload for it in your own module — a concrete
// member outranks the closed-group templates and the unbounded fallback.

// Scratch space for one snprintf-rendered value.
@private const MAX_BUF_LEN = 256;

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
 * @param modifier: "p" pointer-style, "x" lowercase hex, "X" uppercase
 *                  hex; anything else renders signed decimal
 */
@inline
fn format<T: int32 | int16 | int8>(mut str: string, value: T, const modifier: slice<char>) {
    format(str, value as int64, modifier);
}

/**
 * Appends an int64's rendering to str.
 *
 * The signed worker: the closed-group overload above funnels the narrow
 * widths here, already sign-extended.
 *
 * @param str:      destination string
 * @param value:    signed integer to render
 * @param modifier: "p" pointer-style, "x" lowercase hex, "X" uppercase
 *                  hex; anything else renders signed decimal
 */
fn format(mut str: string, value: int64, const modifier: slice<char>) {
    let buf: char[MAX_BUF_LEN];
    
    if (equals(modifier, "p"))
        snprintf(buf, MAX_BUF_LEN, "%p", value);
    else if (equals(modifier, "x"))
        snprintf(buf, MAX_BUF_LEN, "%llx", value);
    else if (equals(modifier, "X"))
        snprintf(buf, MAX_BUF_LEN, "%llX", value);
    else
        snprintf(buf, MAX_BUF_LEN, "%lld", value);
    
    string_append(str, buf);
}

/**
 * Appends an unsigned integer's rendering to str.
 *
 * The unsigned half of the closed-group pair; only the default conversion
 * differs, and uint64 needs no widening, so one group takes all four
 * widths with no separate worker.
 *
 * @param str:      destination string
 * @param value:    unsigned integer to render
 * @param modifier: "p" pointer-style, "x" lowercase hex, "X" uppercase
 *                  hex; anything else renders unsigned decimal
 */
fn format<T: uint64 | uint32 | uint16 | uint8>(mut str: string, value: T, const modifier: slice<char>) {
    let buf: char[MAX_BUF_LEN];

    if (equals(modifier, "p"))
        snprintf(buf, MAX_BUF_LEN, "%p", value);
    else if (equals(modifier, "x"))
        snprintf(buf, MAX_BUF_LEN, "%llx", value);
    else if (equals(modifier, "X"))
        snprintf(buf, MAX_BUF_LEN, "%llX", value);
    else
        snprintf(buf, MAX_BUF_LEN, "%llu", value);
    
    string_append(str, buf);
}

/**
 * Appends a float64's fixed-point rendering to str.
 *
 * @param str:      destination string
 * @param value:    value to render
 * @param modifier: ignored
 */
fn format(mut str: string, value: float64, const modifier: slice<char>) {
    let buf: char[MAX_BUF_LEN];
    snprintf(buf, MAX_BUF_LEN, "%f", value);
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
 * Appends a string slice's bytes to str.
 *
 * @param str:      destination string
 * @param value:    slice to append (its `length` bytes, no NUL needed)
 * @param modifier: ignored
 */
@inline
fn format(mut str: string, const value: slice<const char>, const modifier: slice<char>) {
    string_append(str, value);
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
 * Appends a C string's bytes to str.
 *
 * @param str:      destination string
 * @param value:    NUL-terminated string; must not be null (asserted with
 *                  the `!` hatch, undefined if it is)
 * @param modifier: ignored
 */
@inline
fn format(mut str: string, value: char*, const modifier: slice<char>) {
    string_append(str, value!);
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
