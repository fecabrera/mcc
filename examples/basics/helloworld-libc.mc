// The same program as helloworld.mc, written straight against libc: import the
// stdio bindings and call printf directly, instead of the std print/println
// wrappers. Handy when you want C's formatting without pulling in std.
import "libc/stdio";

fn main() -> int32 {
    printf("hello, world\n");

    return 0;
}
