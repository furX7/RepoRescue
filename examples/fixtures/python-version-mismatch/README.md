# Python Version Mismatch Fixture

Purpose: declares Python <3.0, incompatible with every supported Python 3.12+ runtime.

Expected findings: python_version ERROR. Startup execution is unnecessary.

Safety: no network, file writes, subprocesses, or third-party dependencies.

Run: `repo-rescue examples/fixtures/python-version-mismatch`.
The CLI inspects metadata and only proposes startup; no project code is executed.
