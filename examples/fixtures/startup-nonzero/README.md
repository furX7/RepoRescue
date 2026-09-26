# Startup Nonzero Fixture

Purpose: raises an intentional RuntimeError and exits quickly with a nonzero status.

Expected after confirmed execution: startup ERROR; no python_import diagnosis.

Safety: no network, file writes, subprocesses, or third-party dependencies.

Run: `repo-rescue examples/fixtures/startup-nonzero` only inspects and proposes.
After source review, use the explicitly confirmed API shown in the parent README,
substituting this fixture path. There is no CLI confirmation flag.
