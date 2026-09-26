# Missing Module Fixture

Purpose: imports a unique module intentionally absent from this fixture.

Expected after confirmed execution: python_import ERROR naming
`reporescue_fixture_missing_dependency_xyz`, plus nonzero startup ERROR.
This does not establish a missing PyPI distribution.

Safety: no network, file writes, subprocesses, or third-party dependencies.

Run: `repo-rescue examples/fixtures/missing-module` only inspects and proposes.
For the real failure, review the source and run:

```powershell
repo-rescue examples/fixtures/missing-module --run-startup-probe
```

The flag explicitly confirms the supported startup probe, without an interactive prompt.
