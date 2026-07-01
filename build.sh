#!/bin/bash
CC=${CC:-cc}
AR=${AR:-ar}
MCC="python -m mcc"
MCFLAGS="--nostdlib -I libmc"

run_echo() {
    echo "$@"
    $@ || exit 1
}

compile() {
    run_echo $MCC $MCFLAGS -c $1
    run_echo $MCC $MCFLAGS --emit-interface $1 -o "lib/${1%.mc}.mci"
}

link_shared() {
    run_echo $CC -shared -o lib/libmc.so $@
}

link_static() {
    run_echo $AR -rc lib/libmc.a $@
}

mkdir -p lib/
mkdir -p lib/libmc
mkdir -p lib/libmc/hashing
mkdir -p lib/libmc/libc

for file in libmc/*.mc; do compile $file; done
for file in libmc/**/*.mc; do compile $file; done

link_shared libmc/*.o libmc/**/*.o
link_static libmc/*.o libmc/**/*.o
