// @extern declares a function or global defined elsewhere -- in libc, or in
// another object linked into the program: give the signature and end with `;`.
// The libmc/libc/ modules are ready-made @extern bindings (import "libc/stdio";);
// declare your own here when you need something they do not cover.
@extern fn strlen(s: char*) -> uint64;
@extern fn putchar(c: int32) -> int32;

// A trailing `...` declares a C-style variadic function, like printf or a
// kernel's printk. Extra arguments follow C promotion rules.
@extern fn printf(fmt: char*, ...) -> int32;

// snprintf writes into a buffer; with a null buffer and size 0 it just
// returns how many bytes the formatted string would need.
@extern fn snprintf(buf: char*, n: uint64, fmt: char*, ...) -> int32;

fn main() -> int32 {
    let msg: char* = "hello, extern";
    printf("strlen(\"%s\") = %llu\n", msg, strlen(msg));

    // The variadic snprintf, measuring a formatted string's length.
    let width: int32 = snprintf(null, 0, "%d-%d-%d", 4 as int32, 2 as int32, 7 as int32);
    printf("\"4-2-7\" needs %d bytes\n", width);

    // putchar is an ordinary extern function called like any other.
    putchar(33);   // '!'
    putchar(10);   // newline
    return 0;
}

// See also: interfaces.mc -- for a function defined in another *mcc* object,
// a bodyless `fn` prototype keeps the mcc calling convention (mut/const
// markers included), where @extern means the C one. extern_nonnull.mc adds
// `@nonnull` to an @extern declaration: the graded null-proof promise on a
// foreign C parameter.
