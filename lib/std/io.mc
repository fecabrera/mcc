import "std/string";
import "std/format";

@if (NATIVE_PRINTLN) {
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
                        format(str, t, (modifier as slice<char>)[1:]);
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
}

/**
 * Formats according to format and writes the result to standard output (no
 * trailing newline). Thin wrapper around vprintf.
 *
 * @todo: use `const format: struct string` instead.
 *
 * @param format: printf-style format string (stb_sprintf grammar; floats disabled)
 * @param ...:    variadic arguments matching the format specifiers
 */
@if (NATIVE_PRINTLN) {
    fn print(const format: slice<const char>, args...) {
        let str: string;
        string_init(str);
        defer string_destroy(str);
        format_args(str, format, args);
        writestr(str as slice<char>);
    }
} @else {
    fn print(format: char*, ...) {
        let args: va_list;
        va_start(args, format);
        vprintf(format, args);
        va_end(args);
    }
}

/**
 * Like print, but appends a newline after the formatted output.
 * 
 * @todo: use `const format: struct string` instead.
 *
 * @param format: printf-style format string (stb_sprintf grammar; floats disabled)
 * @param ...:    variadic arguments matching the format specifiers
 */
@if (NATIVE_PRINTLN) {
    fn println(const fmt: slice<const char>, args...) {
        let str: string;
        string_init(str);
        defer string_destroy(str);
        format_args(str, fmt, args);
        string_push(str, '\n');
        writestr(str as slice<char>);
    }
} @else {
    fn println(format: char*, ...) {
        let args: va_list;
        va_start(args, format);
        vprintf(format, args);
        va_end(args);
        putchar('\n' as int32);   // char literals are char; putchar takes int32
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
 * Writes a string's bytes to standard output (its `length` bytes from `data`).
 *
 * @param str: string to write
 */
@inline
fn writestr(const str: slice<const char>) {
    fwrite(str.data as byte*, sizeof(char), str.length, stdout);
}

/**
 * Writes a string to standard output followed by a newline.
 *
 * @param str: string to write
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
