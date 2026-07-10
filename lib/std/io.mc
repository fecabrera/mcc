import "std/string";
import "std/format";

// print/println format with `{}` placeholders: each `{[modifiers]}` renders
// the next variadic argument through the std/format overload set,
// type-driven -- no `%`-letters. The legacy printf-style pair below is kept
// behind -D PRINTF_PRINTLN=1 for programs mid-migration; libc's printf
// remains the tool for the formatting the `{...}` modifiers do not carry
// yet (float precision).
@if (PRINTF_PRINTLN) {
    /**
     * Formats according to format and writes the result to standard output
     * (no trailing newline). Thin wrapper around vprintf.
     *
     * @param format: printf-style format string (stb_sprintf grammar; floats disabled)
     * @param ...:    variadic arguments matching the format specifiers
     */
    fn print(format: char*, ...) {
        let args: va_list;
        va_start(args, format);
        vprintf(format, args);
        va_end(args);
    }

    /**
     * Like print, but appends a newline after the formatted output.
     *
     * @param format: printf-style format string (stb_sprintf grammar; floats disabled)
     * @param ...:    variadic arguments matching the format specifiers
     */
    fn println(format: char*, ...) {
        let args: va_list;
        va_start(args, format);
        vprintf(format, args);
        va_end(args);
        putchar('\n' as int32);   // char literals are char; putchar takes int32
    }
} @else {
    /**
     * Renders fmt into str: literal text copied through, each `{[modifiers]}`
     * placeholder replaced by the next argument's `format(...)` rendering,
     * with the bracket content passed verbatim as its modifier. `{{` and
     * `}}` escape literal braces.
     *
     * @param str:  destination string
     * @param fmt:  format string with `{[modifiers]}` placeholders
     * @param args: values rendered in sequence, one per placeholder
     */
    @private
    fn format_args(mut str: string, const fmt: slice<const char>, args...) {
        let i: uint64 = 0;
        let bracket_open = false;
        let bracket_closed = false;

        let modifier: string;
        string_init(modifier);
        defer string_destroy(modifier);

        for c in fmt {
            case (c) {
            when '{':
                if (bracket_open) {
                    string_push(str, c);
                    bracket_open = false;
                    continue;
                }

                bracket_open = true;
            when '}':
                if (bracket_closed) {
                    string_push(str, c);
                    bracket_closed = false;
                    continue;
                }

                if (!bracket_open) {
                    bracket_closed = true;
                    continue;
                }

                bracket_open = false;

                if (i < args.length) {
                    with (t = args[i] as T) {
                        format(str, t, modifier as slice<char>);
                    }

                    string_reset(modifier);
                    i += 1;
                }
            else:
                if (bracket_open) {
                    string_push(modifier, c);
                } else {
                    string_push(str, c);
                }
            }
        }
    }

    /**
     * Formats according to fmt and writes the result to standard output (no
     * trailing newline).
     *
     * Each `{}` placeholder renders the next argument through the
     * std/format overload set (type-driven), and `{modifiers}` passes the
     * bracket content through as the per-type modifier -- `{x}`/`{X}`/`{p}`
     * on integers, `{y}`/`{yes}` on bools, applied per element on slices.
     * `{{`/`}}` print literal braces. Making your own type printable is one
     * `format` overload in your module (the set is open).
     *
     * @param fmt:  format string with `{[modifiers]}` placeholders
     * @param args: values rendered in sequence, one per placeholder
     */
    fn print(const fmt: slice<const char>, args...) {
        let str: string;
        string_init(str);
        defer string_destroy(str);
        format_args(str, fmt, args);
        writestr(str);   // the generic writestr takes the string directly
    }

    /**
     * Like print, but appends a newline after the formatted output.
     *
     * @param fmt:  format string with `{[modifiers]}` placeholders
     * @param args: values rendered in sequence, one per placeholder
     */
    fn println(const fmt: slice<const char>, args...) {
        let str: string;
        string_init(str);
        defer string_destroy(str);
        format_args(str, fmt, args);
        string_push(str, '\n');
        writestr(str);
    }
}

/**
 * Writes a single byte to standard output.
 *
 * @param c: byte to write
 */
@inline
fn writechar(const c: char) {
    putchar(c as int32);
}

/**
 * Writes a string's bytes to standard output (its `length` bytes from
 * `data`). `T` is bounded to `slice<char>`, so `str` may be a `string`, a
 * `list<char>`, or a `slice<char>` -- anything that `extends` the char
 * slice binds `T` and writes with no explicit `as` at the call site,
 * re-lending into the concrete member below.
 *
 * @param str: any value whose type extends `slice<char>` to write
 */
@inline
fn writestr<T extends slice<char>>(const str: T) {
    writestr(str as slice<const char>);
}

/**
 * Writes a string slice's bytes to standard output: the concrete member a
 * string literal adapts to directly.
 *
 * @param str: slice to write
 */
@inline
fn writestr(const str: const slice<const char>) {
    fwrite(str.data as byte*, sizeof(char), str.length, stdout);
}

/**
 * Like the generic writestr, followed by a newline: a `string`,
 * `list<char>`, or `slice<char>` writes a whole line with no explicit
 * borrow at the call site.
 *
 * @param str: any value whose type extends `slice<char>` to write
 */
@inline
fn writeln<T extends slice<char>>(const str: T) {
    writestr(str);
    writechar('\n');
}

/**
 * Writes a string slice to standard output followed by a newline: the
 * concrete member a string literal adapts to directly.
 *
 * @param str: slice to write
 */
@inline
fn writeln(const str: slice<const char>) {
    writestr(str);
    writechar('\n');
}

/**
 * Exchanges two values in place.
 *
 * Both parameters are `mut`, so the swap happens in the caller's storage
 * with no pointers at the call site: `swap(x, y);`.
 *
 * @param a: first value
 * @param b: second value
 */
@inline
fn swap<T>(mut a: T, mut b: T) {
    let t = a;
    a = b;
    b = t;
}

/**
 * Stores value into dst and returns the previous value of dst.
 *
 * @param dst:   destination, updated in the caller's storage
 * @param value: value to store
 *
 * @return the value dst held before the call
 */
@inline
fn replace<T>(mut dst: T, value: T) -> T {
    let old = dst;
    dst = value;
    return old;
}
