# Real failure fixtures

These small, deterministic broken Python projects support regression tests,
manual demos, and release verification. They need no network or third-party packages.

| Fixture | Purpose | Expected diagnosis | Requires project execution? | Side effects |
| --- | --- | --- | --- | --- |
| python-version-mismatch | Declares Python <3.0 | python_version ERROR | No | None |
| missing-module | Imports a unique unavailable module | python_import ERROR + startup ERROR | Yes | Captured output only |
| symbol-import-failure | Requests an absent symbol from local helper | python_import ERROR + startup ERROR | Yes | Captured output only; local bytecode disabled |
| startup-nonzero | Raises an intentional RuntimeError | startup ERROR | Yes | Captured output only |
| startup-timeout | Sleeps 10 seconds | startup INFO, timeout evidence | Yes | Brief wait; direct child stopped |
| combined-failures | Version mismatch plus absent import | python_version + python_import + startup ERROR | Yes | Captured output only |

Run `repo-rescue examples/fixtures/<name>` from the repository root after installing
RepoRescue. The CLI only inspects metadata and displays a CAUTION startup proposal;
it has no confirmation flag and never executes these projects.

After reviewing a fixture's tiny source and explicitly confirming its execution,
use the existing library API under the intended interpreter. For example:

```powershell
python -c "from agent_doctor.workflow import run_workflow; print(run_workflow('examples/fixtures/missing-module', confirm_startup=True).terminal_report)"
```

The execution fixtures run their own project code, designed without networking,
persistent writes, subprocesses, environment changes, or dependency installation.
The symbol fixture disables local bytecode before importing helper.py. Startup
timeout is an observation limit, not proof of failure, deadlock, or readiness.
The unique missing module is intentionally not supplied; do not install a package
to repair a fixture. No fake interpreter or virtual environment is committed.

Recommended future GIF candidate: **missing-module**. It fails quickly and shows
an evidence-linked import diagnosis, a repair preview, and verification plan without
installation. Recording and images are outside this fixture change.
