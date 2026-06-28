import "libc/stdio";

// `@if` selects code at compile time, like C's `#if` -- but structured: each
// branch is a real block, and only the live one is compiled. The condition is
// a constant expression over the target facts the compiler predefines:
// TARGET_OS / TARGET_ARCH and the OS_* / ARCH_* constants.

// At the top level, `@if` selects whole declarations. Here it picks the name
// of the operating system we are building for. `@else @if` chains.
@if (TARGET_OS == OS_DARWIN) {
    const OS_NAME = "macOS";
} @else @if (TARGET_OS == OS_LINUX) {
    const OS_NAME = "Linux";
} @else @if (TARGET_OS == OS_WINDOWS) {
    const OS_NAME = "Windows";
} @else @if (TARGET_OS == OS_NONE) {
    const OS_NAME = "bare metal";   // a freestanding target, no OS
} @else {
    const OS_NAME = "an unknown OS";
}

// The classic use pairs `@if` with @symbol to bind a name spelled differently
// per platform behind one mcc name. libmc/libc/stdio.mc does exactly this for the
// standard streams -- libc's stdout is the symbol `__stdoutp` on macOS but
// `stdout` on Linux -- so the `stdout` we use below is already the right one
// for this target, with no per-platform code here.

fn main() -> int32 {
    // As a statement, `@if` selects code inside a function. It does not open a
    // scope: the binding chosen below is visible after the block.
    @if (TARGET_ARCH == ARCH_AARCH64) {
        let word_bits = 64 as int32;     // arm64
    } @else @if (TARGET_ARCH == ARCH_X86_64) {
        let word_bits = 64 as int32;     // x86-64
    } @else {
        let word_bits = 0 as int32;      // unknown / 32-bit
    }

    fprintf(stdout, "built for %s, %d-bit words\n", OS_NAME, word_bits);
    return 0;
}
