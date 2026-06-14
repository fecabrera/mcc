# Enables coverage measurement in subprocesses. Python imports `sitecustomize`
# automatically at startup, so putting this directory on PYTHONPATH makes every
# spawned interpreter -- e.g. the `python -m mcc` runs in tests/test_cli.py --
# start recording coverage. coverage.process_startup() only does so when the
# COVERAGE_PROCESS_START environment variable points at the config; otherwise it
# is a harmless no-op, so this is safe to leave on PYTHONPATH.
import coverage

coverage.process_startup()
