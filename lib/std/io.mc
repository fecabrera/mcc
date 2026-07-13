import "std/string";
import "std/format";

// print/println format with `{}` placeholders: each `{[modifiers]}` renders
// the next variadic argument through the std/format overload set,
// type-driven -- no `%`-letters. libc's printf remains the tool for the
// formatting the `{...}` modifiers do not carry (scientific `%g`/`%e`
// notation).

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
fn format_args(mut str: string, @format const fmt: slice<const char>, args...) {
    let i: uint64 = 0;
    let bracket_open = false;
    let bracket_closed = false;

    let modifier = string();

    for c in fmt {
        case (c) {
        when '{':
            if (bracket_open) {
                str.push(c);
                bracket_open = false;
                continue;
            }

            bracket_open = true;
        when '}':
            if (bracket_closed) {
                str.push(c);
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

                modifier.reset();
                i += 1;
            }
        else:
            if (bracket_open) {
                modifier.push(c);
            } else {
                str.push(c);
            }
        }
    }
}

/**
 * Writes a string's bytes verbatim -- no `{}` formatting, the content
 * goes out as-is. The one-argument forms target standard output; pass a
 * FILE* (such as `stderr!`) to write to another stream. `T` is bounded to
 * `slice<char>`, so a `string`, `list<char>`, or `slice<char>` binds
 * without an explicit `as` at the call site. Reach for the @format `print`
 * below when you want `{}` placeholders instead.
 *
 * @param f:   destination stream; the forms without it write to stdout
 * @param str: the bytes to write, unchanged
 */
@inline
fn print(const str: const slice<const char>) {
    print(stdout!, str);
}

@inline
fn print<T extends slice<char>>(const str: T) {
    print(stdout!, str as slice<const char>);
}

@inline
fn print(@nonnull f: FILE*, const str: const slice<const char>) {
    fwrite(str.data as byte*, sizeof(char), str.length, f);
}

@inline
fn print<T extends slice<char>>(@nonnull f: FILE*, const str: T) {
    print(f, str as slice<const char>);
}

/**
 * Formats according to fmt and writes the result to standard output, or
 * to the given FILE* with the two-argument form (no trailing newline).
 *
 * Each `{}` placeholder renders the next argument through the
 * std/format overload set (type-driven), and `{modifiers}` passes the
 * bracket content through as the per-type modifier -- `{x}`/`{X}`/`{p}`
 * on integers, `{.2f}`/`{8.2f}` on floats, `{y}`/`{yes}` on bools,
 * applied per element on slices.
 * `{{`/`}}` print literal braces. Making your own type printable is one
 * `format` overload in your module (the set is open).
 *
 * The parameter is @format, so a string *literal* also takes positional
 * `{n[:modifiers]}` placeholders, desugared at compile time to the
 * sequential form above (`{:modifiers}` spells a bare all-digit
 * modifier, e.g. the `{:2}` field width), and an f-string interpolates
 * expressions directly: `print(f"x = {x}")` desugars to the same
 * sequential form, its `{expr[:modifiers]}` holes becoming the
 * arguments (`{expr=}` labels the value with the expression's own
 * spelling).
 *
 * @param fmt:  format string with `{[modifiers]}` placeholders
 * @param args: values rendered in sequence, one per placeholder
 */
fn print(@format const fmt: slice<const char>, args...) {
    print(stdout!, fmt, args);
}

fn print(@nonnull f: FILE*, @format const fmt: slice<const char>, args...) {
    let str = string();
    format_args(str, fmt, args);
    print(f, str);   // hand the finished string to the verbatim printer
}

/**
 * Verbatim print followed by a newline: writes str's bytes as-is and then
 * a '\n', to standard output or the given FILE*. This is the replacement
 * for the deprecated `writeln`. As with verbatim `print`, `T` binds any
 * `slice<char>`-extending type, and the @format `println` below is the
 * one to use for `{}` placeholders.
 *
 * @param f:   destination stream; the forms without it write to stdout
 * @param str: the bytes to write, unchanged
 */
@inline
fn println(const str: const slice<const char>) {
    println(stdout!, str);
}

@inline
fn println<T extends slice<char>>(const str: T) {
    println(stdout!, str as slice<const char>);
}

@inline
fn println(@nonnull f: FILE*, const str: const slice<const char>) {
    print(f, str);
    fputc('\n' as int32, f);
}

@inline
fn println<T extends slice<char>>(@nonnull f: FILE*, const str: T) {
    println(f, str as slice<const char>);
}

/**
 * Like the @format print, but appends a newline after the formatted
 * output; the two-argument form targets a given FILE* instead of stdout.
 *
 * @param fmt:  format string with `{[modifiers]}` placeholders
 * @param args: values rendered in sequence, one per placeholder
 */
fn println(@format const fmt: slice<const char>, args...) {
    println(stdout!, fmt, args);
}

fn println(@nonnull f: FILE*, @format const fmt: slice<const char>, args...) {
    let str = string();
    format_args(str, fmt, args);
    str.push('\n');
    print(f, str);
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
@deprecated("use print() instead")
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
@deprecated("use print() instead")
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
@deprecated("use println() instead")
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
@deprecated("use println() instead")
@inline
fn writeln(const str: slice<const char>) {
    writestr(str);
    writechar('\n');
}

/**
 * Writes prefix then msg to standard error as one line, then aborts the
 * process: SIGABRT, no defers, no atexit handlers. Pending standard
 * output is flushed first so it survives the abort.
 *
 * @param prefix: diagnostic prefix, e.g. "panic: "
 * @param msg:    rendered message text
 */
@private @noreturn
fn fail(const msg: slice<const char>) {
    fflush(stdout);
    println(stderr!, msg);
    abort();
}

@private @noreturn
fn fail(const prefix: slice<const char>, const msg: slice<const char>) {
    fflush(stdout);
    print(stderr!, prefix);
    println(stderr!, msg);
    abort();
}

/**
 * Writes `msg` to standard error and aborts (SIGABRT, exit status
 * 134 under a shell). The message is written verbatim -- braces are not
 * placeholders here, so runtime text is always safe. A panic call
 * diverges like a `return` (@noreturn), so an
 * `if (p == null) { panic("..."); }` guard narrows p for the rest of the
 * scope; enclosing defers do not run on the panic path.
 *
 * @param msg: message text, written as-is
 */
@noreturn
fn panic(const msg: slice<const char>) {
    fail(msg);
}

/**
 * Formats according to fmt -- `{}` placeholders through the std/format
 * overload set, positional `{n}` and f-string literals included -- then
 * writes the rendering to standard error and aborts
 * (SIGABRT), like the verbatim member above.
 *
 * @param fmt:  format string with `{[modifiers]}` placeholders
 * @param args: values rendered in sequence, one per placeholder
 */
@noreturn
fn panic(@format const fmt: slice<const char>, args...) {
    let str = string();
    format_args(str, fmt, args);
    fail(str as slice<char>);
}

/**
 * Panics with `assertion failed: msg` when cond is false; does nothing
 * otherwise. Always enabled. The check does not flow-narrow pointers --
 * facts do not cross the call -- so a narrowing null guard stays
 * `if (p == null) { panic("..."); }`.
 *
 * @param cond: condition expected to hold
 * @param msg:  message text, written as-is on failure
 */
fn assert(const cond: bool, const msg: slice<const char>) {
    if (!cond) {
        fail("assertion failed: ", msg);
    }
}

/**
 * Like the verbatim assert, but the message formats: `{}` placeholders
 * through the std/format overload set, positional `{n}` and f-string
 * literals included. The arguments are evaluated whether or not the
 * assertion holds; only the rendering is skipped.
 *
 * @param cond: condition expected to hold
 * @param fmt:  format string with `{[modifiers]}` placeholders
 * @param args: values rendered in sequence, one per placeholder
 */
fn assert(const cond: bool, @format const fmt: slice<const char>, args...) {
    if (!cond) {
        let str = string();
        format_args(str, fmt, args);
        fail("assertion failed: ", str as slice<char>);
    }
}
