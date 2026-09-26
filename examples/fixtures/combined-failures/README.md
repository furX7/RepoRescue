# Combined Failures Fixture

Purpose: independently declares an incompatible Python requirement and imports an
intentionally unavailable unique module.

Expected after confirmed execution: python_version, python_import, and startup
ERROR diagnoses. No conclusion that the version mismatch caused the import error.

Safety: no network, file writes, subprocesses, or third-party dependencies.

Run: `repo-rescue examples/fixtures/combined-failures` detects the version mismatch
but only proposes startup. After source review, use the explicitly confirmed API
shown in the parent README, substituting this path. No CLI confirmation flag exists.
