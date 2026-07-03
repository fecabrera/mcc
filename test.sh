#!/bin/bash
# Smoke-test the packaged wheel the way a user (or the Homebrew formula) does:
# build the wheel, install it -- with its dependencies -- into a throwaway
# virtualenv, and compile a small program from a directory other than the repo
# root, so `import "std"` resolves only through the bundled stdlib (mcc/libmc
# inside the install), never the repo's own libmc/ tree. Guards against a
# packaging slip -- a missing subpackage, a renamed stdlib directory, an absent
# data file -- that the source-tree test suite cannot see. Mirrors the smoke
# job in .github/workflows/ci.yml.
PYTHON=python
DIST=dist
VENV="$(mktemp -d)"
WORK="$(mktemp -d)"

run_echo() {
    echo "$@"
    $@ || exit 1
}

# Clean up the throwaway venv and work dir on exit (dist/ is a build product,
# kept like build.sh's lib/).
trap 'rm -rf "$VENV" "$WORK"' EXIT

# Build the wheel, then install that exact file into a clean venv -- so the
# source tree is never on the path and only the packaged layout is exercised.
run_echo pip wheel . --no-deps -w $DIST
run_echo $PYTHON -m venv "$VENV"
run_echo "$VENV/bin/pip" install $DIST/*.whl

# A minimal program, compiled from outside the repo root.
cat > "$WORK/hi.mc" <<'MC'
import "std";
fn main() -> int32 { println("packaged ok: %d", 30); return 0; }
MC
cd "$WORK" || exit 1

run_echo "$VENV/bin/mcc" hi.mc -Werror --run      # JIT: codegen + the bundled stdlib
run_echo "$VENV/bin/mcc" hi.mc -Werror -o hi      # native: object emission and linking
run_echo ./hi

echo "smoke test passed"
