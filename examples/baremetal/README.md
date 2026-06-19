# Bare metal

A freestanding kernel for qemu's aarch64 `virt` board — no libc, no OS. It
prints over the PL011 UART by writing to a `@volatile` memory-mapped
register struct, cross-compiled with `--target` and linked with the GNU
bare-metal toolchain.

## Prerequisites

```bash
brew install aarch64-elf-gcc qemu
```

## Build and run

```bash
pipenv run python -m mcc examples/baremetal/kernel.mc --target aarch64-unknown-none-elf -o kernel.o
aarch64-elf-gcc -nostdlib -Ttext=0x40100000 examples/baremetal/start.S kernel.o -o kernel.elf
qemu-system-aarch64 -M virt -cpu cortex-a53 -nographic -kernel kernel.elf
```

```
hello from bare-metal mcc
```

(Exit qemu with `Ctrl-A` then `X`.)

## How it fits together

- **[kernel.mc](kernel.mc)** — the kernel. The PL011's registers are
  described as a `@volatile` struct so the busy-wait on the flag register
  is re-read on every iteration; without it the optimizer would hoist the
  load and spin forever. The libc bindings under `lib/libc/` are useless
  here -- there is no libc on bare metal -- so `@extern` is how you would
  declare your own runtime's functions instead.
- **[start.S](start.S)** — a five-instruction boot stub. qemu starts the
  CPU with no stack pointer, so it sets one and calls `kmain`. Assembled
  and linked by `aarch64-elf-gcc` in the same command. It stays a separate
  `.S` file because mcc's [inline assembly](../../README.md#inline-assembly)
  is host-target only for now, and a no-prologue `_start` that sets `sp` and
  writes its own control flow needs the `@naked` form — both are on the
  [roadmap](../../README.md#roadmap).
- **`--target aarch64-unknown-none-elf`** — makes `mcc` emit an ELF object
  for the bare-metal triple instead of linking a host executable.
- **`-Ttext=0x40100000`** — the `virt` board's RAM starts at `0x40000000`
  (lower addresses are flash and MMIO, including the UART at
  `0x09000000`), so the kernel is linked — and the stack placed — in RAM.
