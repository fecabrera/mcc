import "std/io";
import "std/memory";
import "std/list";

// `defer` schedules an action to run when the enclosing block exits -- however
// it exits: falling off the end, a return, or a break/continue out of a loop.
// Deferred actions run in reverse order (last deferred, first run), so cleanup
// unwinds in the opposite order of acquisition.

fn process(n: int32) -> int32 {
    let buffer: byte* = alloc<byte>(64)!;  // `byte` is the alias for uint8: raw memory
    defer dealloc(buffer);            // freed no matter which return we take

    if (n < 0) {
        return -1;                    // the defer still frees buffer
    }

    // A second resource: its defer runs before buffer's (reverse order).
    let scratch: byte* = alloc<byte>(16);
    defer dealloc(scratch);

    buffer[0] = 'O';
    buffer[1] = 'K';
    buffer[2] = 0;
    println("%s", buffer);                     // both buffers freed as this returns
    return n * 2;
}

// Defers nest: a defer block can hold its own defers. This cleanup block frees
// every element of a dynamic array -- advancing the index with an inner
// `defer i = i + 1` that runs at the end of each loop pass -- then destroys the
// array itself once the loop is done.
fn build_labels(n: uint64) {
    let labels: list<byte*>;
    list_init(labels, n);
    defer {
        let i: uint64 = 0;
        while (i < labels.length) {
            defer i += 1;                 // bump at the end of each iteration
            println("  free %s", labels.data![i]);
            dealloc(labels.data![i]);
        }
        list_destroy(labels);               // runs after the loop, last of all
    }

    let i: uint64 = 0;
    while (i < n) {
        let label: byte* = alloc<byte>(2)!;
        label[0] = ('a' as byte) + (i as byte);   // raw byte buffer
        label[1] = 0;
        list_push(labels, label);
        i += 1;
    }
    println("built %llu labels", labels.length);
    // falling off the end here runs the cleanup block above
}

fn main() -> int32 {
    // The block form defers several statements at once.
    defer {
        println("cleaning up");
        println("goodbye");
    }

    println("process(7)  -> %d", process(7));
    println("process(-1) -> %d", process(-1));

    // A defer inside a loop body runs every iteration, at the end of that pass.
    let i: int32 = 0;
    while (i < 3) {
        defer print("| ");          // marks the end of each iteration
        print("step %d ", i);
        i += 1;
    }
    println("");

    // A defer block can itself contain defers -- they nest.
    build_labels(3);

    return 0;   // the block defer ("cleaning up" / "goodbye") runs here, last
}

// Defers run on every *block exit*, and a call that never returns is not
// one: `exit(1);` or any other @noreturn call leaves enclosing defers
// unrun, matching C, where exit() never unwinds the calling stack. Code
// that must clean up should return an error up to main instead of exiting
// deep in the call tree. See functions/noreturn.mc.
