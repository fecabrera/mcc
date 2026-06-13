/* Boot stub: qemu starts the CPU here with no stack pointer set, so give it
   one (RAM on the virt board starts at 0x40000000; the stack grows down
   from the load address) and call into the compiled kernel. */
.section .text.boot
.global _start
_start:
    ldr x0, =0x40100000
    mov sp, x0
    bl  kmain
1:  wfe
    b   1b
