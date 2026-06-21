import "string";
import "libc/stdio";

/**
 * Formats according to format and writes the result to standard output (no
 * trailing newline). Thin wrapper around vprintf.
 *
 * @todo: use `const format: struct string` instead.
 *
 * @param format: printf-style format string (stb_sprintf grammar; floats disabled)
 * @param ...:    variadic arguments matching the format specifiers
 */
fn print(format: uint8*, ...) {
    let args: va_list;
    va_start(args, format);
    vprintf(format, args);
    va_end(args);
}

/**
 * Like print, but appends a newline after the formatted output.
 * 
 * @todo: use `const format: struct string` instead.
 *
 * @param format: printf-style format string (stb_sprintf grammar; floats disabled)
 * @param ...:    variadic arguments matching the format specifiers
 */
fn println(format: uint8*, ...) {
    let args: va_list;
    va_start(args, format);
    vprintf(format, args);
    va_end(args);
    putchar('\n' as int32);   // char literals are uint8; putchar takes int32
}

/**
 * Writes a single byte to standard output.
 *
 * @param c: byte to write
 */
fn writechar(c: uint8) {
    putchar(c as int32);
}

/**
 * Writes a string's bytes to standard output (its `length` bytes from `data`).
 *
 * @param str: string to write
 */
fn writestr(const str: struct string) {
    fwrite(str.data, sizeof(uint8), str.length, stdout);
}

/**
 * Writes a string to standard output followed by a newline.
 *
 * @param str: string to write
 */
fn writeln(const str: struct string) {
    writestr(str);
    writechar('\n');
}