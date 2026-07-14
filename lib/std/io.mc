import "std/string";
import "std/format";

// print/println write a string -- verbatim, no placeholder scanning, so
// runtime text is always safe to print. Formatting is the producers' job:
// an f-string (`println(f"x = {x}")`) renders its holes through the
// std/format overload set, and `"...".format(args)` (std/slice) is the
// explicit spelling for a runtime format string. libc's printf remains the
// tool for the formatting the `{...}` modifiers do not carry (scientific
// `%g`/`%e` notation).

/**
 * Writes a string's bytes verbatim -- braces are not placeholders, the
 * content goes out as-is, so runtime text is always safe. The one-argument
 * form targets standard output; pass a FILE* (such as `stderr!`) to write
 * to another stream. `T` is bounded to `slice<const char>`, and the bound
 * is const-covariant, so one signature takes the whole family: a string
 * literal, a `slice<char>` or `slice<const char>`, a `string` or
 * `list<char>` -- and with them an f-string (`print(f"x = {x}")`) or a
 * `"...".format(args)` rendering, whose owned temporary is destroyed at
 * statement end.
 *
 * @param f:   destination stream; the form without it writes to stdout
 * @param str: the bytes to write, unchanged
 */
@inline
fn print<T extends slice<const char>>(const str: T) {
    print(stdout!, str);
}

@inline
fn print<T extends slice<const char>>(@nonnull f: FILE*, const str: T) {
    let view = str as slice<const char>;
    fwrite(view.data as byte*, sizeof(char), view.length, f);
}

/**
 * Verbatim print followed by a newline: writes str's bytes as-is and then
 * a '\n', to standard output or the given FILE*. This is the replacement
 * for the deprecated `writeln`. As with `print`, the one `T` bounded to
 * `slice<const char>` binds every char-run type -- literals, slices of
 * either constness, `string`/`list<char>`, f-strings, `.format` results.
 *
 * @param f:   destination stream; the form without it writes to stdout
 * @param str: the bytes to write, unchanged
 */
@inline
fn println<T extends slice<const char>>(const str: T) {
    println(stdout!, str);
}

@inline
fn println<T extends slice<const char>>(@nonnull f: FILE*, const str: T) {
    print(f, str);
    fputc('\n' as int32, f);
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
 * The message parameter takes the same const-covariant char-run family as
 * `print`, so `panic(f"x = {x}")` compiles -- but prefer a plain verbatim
 * message: the process is dying, so a rendering built for the panic path
 * (an f-string, a `.format` result) is never destroyed. Formatting on the
 * way down is an explicit, visible cost, not the blessed style.
 *
 * @param msg: message text, written as-is
 */
@noreturn
fn panic<T extends slice<const char>>(const msg: T) {
    fail(msg as slice<const char>);
}

/**
 * Panics with `assertion failed: msg` when cond is false; does nothing
 * otherwise. Always enabled. The check does not flow-narrow pointers --
 * facts do not cross the call -- so a narrowing null guard stays
 * `if (p == null) { panic("..."); }`.
 *
 * The message takes the same char-run family as `panic`, and it is built
 * whether or not the assertion holds -- an f-string message renders on
 * every pass (and is destroyed at statement end only when the assertion
 * holds; a failing assert aborts mid-statement). Prefer a plain verbatim
 * message on hot paths.
 *
 * @param cond: condition expected to hold
 * @param msg:  message text, written as-is on failure
 */
fn assert<T extends slice<const char>>(const cond: bool, const msg: T) {
    if (!cond) {
        fail("assertion failed: ", msg as slice<const char>);
    }
}
