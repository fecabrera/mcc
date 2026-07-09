#!/bin/bash
# Remove build artifacts: stray objects/interfaces dropped in the source tree
# (mcc -c writes a .o next to its source), plus the precompiled stdlib output.
find lib -name '*.o' -delete
find lib -name '*.mci' -delete
rm -rf dist/lib
