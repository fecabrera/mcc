#!/bin/bash
# Build the experimental precompiled standard library into dist/lib: one object
# per source module plus its .mci interface stub, then a static (.a) and shared
# library. Sources live under lib/ (lib/std mcc modules, lib/libc C bindings);
# --nostdlib -I lib resolves the std/ and libc/ import prefixes against them.
# -Wall -Werror keeps the stdlib itself warn-free under every opt-in class,
# matching the example-compile loop in CI. Builds ok today; linking a program
# against the archive is not ready yet.
CC=${CC:-cc}
AR=${AR:-ar}
MCC="python -m mcc"
MCFLAGS="--nostdlib -I lib -Wall -Werror"
OUT=dist/lib

run_echo() {
    echo "$@"
    $@ || exit 1
}

# Compile one lib/<sub>/<name>.mc into dist/lib/<sub>/<name>.{o,mci}.
compile() {
    rel=${1#lib/}
    obj="$OUT/${rel%.mc}.o"
    mci="$OUT/${rel%.mc}.mci"
    mkdir -p "$(dirname "$obj")"
    run_echo $MCC $MCFLAGS -c $1 -o "$obj"
    run_echo $MCC $MCFLAGS --emit-interface $1 -o "$mci"
}

mkdir -p "$OUT"

for file in lib/std/*.mc lib/std/hashing/*.mc lib/libc/*.mc; do compile "$file"; done

objs=$(find "$OUT" -name '*.o' | sort)
run_echo $CC -shared -o "$OUT/libmc.so" $objs
run_echo $AR -rc "$OUT/libmc.a" $objs
