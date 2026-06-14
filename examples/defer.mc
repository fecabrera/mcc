import "libc/stdio";
import "memory";

// `defer` schedules an action to run when the enclosing block exits -- however
// it exits: falling off the end, a return, or a break/continue out of a loop.
// Deferred actions run in reverse order (last deferred, first run), so cleanup
// unwinds in the opposite order of acquisition.

fn process(n: int32) -> int32 {
    let buffer: uint8* = alloc<uint8>(64);
    defer dealloc(buffer);            // freed no matter which return we take

    if (n < 0) {
        return -1;                    // the defer still frees buffer
    }

    // A second resource: its defer runs before buffer's (reverse order).
    let scratch: uint8* = alloc<uint8>(16);
    defer dealloc(scratch);

    buffer[0] = 'O';
    buffer[1] = 'K';
    buffer[2] = 0;
    puts(buffer);                     // both buffers freed as this returns
    return n * 2;
}

fn main() -> int32 {
    // The block form defers several statements at once.
    defer {
        puts("cleaning up");
        puts("goodbye");
    }

    printf("process(7)  -> %d\n", process(7));
    printf("process(-1) -> %d\n", process(-1));

    // A defer inside a loop body runs every iteration, at the end of that pass.
    let i: int32 = 0;
    while (i < 3) {
        defer printf("| ");          // marks the end of each iteration
        printf("step %d ", i);
        i = i + 1;
    }
    printf("\n");

    return 0;   // the block defer ("cleaning up" / "goodbye") runs here, last
}
