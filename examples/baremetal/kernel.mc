/**
 * A freestanding kernel for qemu's aarch64 "virt" board: no libc, no OS --
 * just MMIO writes to the PL011 UART. See README.md for how to build it
 * with --target and link and run it with the aarch64-elf toolchain.
 */

/**
 * The PL011 UART's register block, memory-mapped at 0x09000000 on the virt
 * board. @volatile keeps every load and store in the emitted code; without
 * it the optimizer would hoist the flag-register read out of the busy-wait
 * loop and spin forever.
 */
@volatile
struct pl011 {
    dr: uint32;          // data register: write a byte to transmit
    rsr: uint32;         // receive status / error clear
    reserved1: uint64;
    reserved2: uint64;
    fr: uint32;          // flag register: bit 5 is "transmit FIFO full"
}

fn uart() -> struct pl011* {
    return 0x09000000 as struct pl011*;
}

/**
 * Wait for an event (`wfe`) -- a low-power hint to idle until something
 * happens. An `@asm fn` is sugar for a function whose body is a single
 * inline-asm instruction; with no operands and no return it is just `wfe`
 * followed by the compiler's `ret`. Inline asm uses the host architecture's
 * assembler, so it works in this same-arch cross build (aarch64 host,
 * aarch64 target).
 */
@asm fn cpu_relax() { "wfe" }

fn put_char(c: uint8) {
    until ((uart()->fr & 0x20) == 0) {   // wait for room in the FIFO
        cpu_relax();
    }
    uart()->dr = c as uint32;
}

fn print(s: uint8*) {
    let i = 0 as uint64;
    while (s[i] != 0) {
        put_char(s[i]);
        i = i + 1;
    }
}

fn kmain() {
    print("hello from bare-metal mcc\n");
    while (true) {}                      // nothing to return to
}
