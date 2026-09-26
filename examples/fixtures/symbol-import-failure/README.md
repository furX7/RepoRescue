# Symbol Import Failure Fixture

Purpose: helper.py exists but does not define missing_symbol.

Expected after confirmed execution: python_import ERROR with symbol_import_failure
evidence, plus startup ERROR. This is not a missing-package conclusion.

Safety: no network, file writes, subprocesses, or third-party dependencies.
The entrypoint disables bytecode before importing the local helper.

Run: `repo-rescue examples/fixtures/symbol-import-failure` only inspects and proposes.
After source review, use the explicitly confirmed API shown in the parent README,
substituting this fixture path. There is no CLI confirmation flag.
