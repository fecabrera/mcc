import "libc/stdio";   // printf/puts and @extern fn vsnprintf(..., args: va_list)

// A trailing `...` makes a function variadic. mcc can't read the extra
// arguments directly (there is no va_arg), but it can FORWARD them to a C
// v* function through a va_list -- here, vsnprintf formats into a buffer.
//
// va_start(ap, fmt) initializes the cursor (naming the parameter just before
// the ...), and va_end(ap) releases it. va_list is opaque and its layout is
// chosen for the target platform.
fn logf(level: char*, fmt: char*, ...) -> int32 {
    let buf: char[256];
    let ap: va_list;
    va_start(ap, fmt);
    let n = vsnprintf(&buf[0], 256, fmt, ap);
    va_end(ap);
    printf("[%s] %s\n", level, &buf[0]);
    return n;
}

fn main() -> int32 {
    logf("info", "starting up, pid %d", 4071);
    logf("warn", "%s = %d (0x%X)", "answer", 42, 255);
    let written = logf("info", "%d%% complete", 100);
    printf("last message was %d chars\n", written);
    return 0;
}
