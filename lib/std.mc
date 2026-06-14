import "libc/stdio";

fn print(format: uint8*, ...) {
    let args: va_list;
    va_start(args, format);
    vprintf(format, args);
    va_end(args);
}

fn println(format: uint8*, ...) {
    let args: va_list;
    va_start(args, format);
    vprintf(format, args);
    va_end(args);
    putchar('\n' as int32);   // char literals are uint8; putchar takes int32
}