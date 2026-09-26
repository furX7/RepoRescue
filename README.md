# RepoRescue

[English](README.md) | [简体中文](README.zh-CN.md)

Evidence-driven diagnosis and recovery planning for broken development projects.

RepoRescue helps explain why a Python project may not run by connecting observed
evidence to diagnoses and root cause chains. It produces repair previews and
verification plans so developers can review what to change and how to check it.

**v0.2.0-alpha.2 · Windows First · Python only · READ ONLY by default · MIT**

Safe by default. Project code execution requires explicit opt-in.
RepoRescue does not automatically apply repairs or install packages.

## Demo

![RepoRescue diagnosing a Python missing-module failure](docs/assets/repo-rescue-demo.gif)

Diagnosing a deterministic missing-module fixture with the installed CLI.

## Why RepoRescue

Tracebacks, runtime errors, and environment errors provide useful symptoms.
RepoRescue organizes the evidence it supports into a reviewable path:

Evidence → Diagnosis → Root Cause Chain → Repair Preview → Verification Plan.

Causes stay limited to what was observed. A root cause chain may explain a
consequence without establishing its underlying cause; a clean report does not
prove that a project works.

## Quick Start (Windows)

Use Python 3.12 or newer. From the source checkout, create and activate an environment:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install .
repo-rescue --help
repo-rescue --version
```

Inspect a project without executing its code:

```powershell
repo-rescue "C:\work\sample"
```

Explicitly confirm the supported startup probe for this invocation:

```powershell
repo-rescue "C:\work\sample" --run-startup-probe
```

**`--run-startup-probe` executes project code.** It confirms a CAUTION-risk probe
of root-level `main.py`, without an interactive prompt. Project code may have its
own file, network, or service side effects. The flag does not bypass safety checks.

Without activation, use `.\.venv\Scripts\repo-rescue.exe` instead of `repo-rescue`.
For example, `.\.venv\Scripts\repo-rescue.exe --version` works without activation.
After installation, `python -B -m agent_doctor.cli <project>` is another entry point.
The package version is `0.2.0a2`. Internal Python package: `agent_doctor`.
Former working name: Agent Doctor. The temporary `agent-doctor` compatibility
alias delegates to the same CLI.

If a release wheel is available from GitHub Releases, download it and install the local file:

```powershell
python -m pip install <path-to-downloaded-wheel>
```

### Exit codes

- `0`: completed without ERROR/CRITICAL diagnoses; warnings may exist.
- `1`: completed with ERROR/CRITICAL diagnoses.
- `2`: invalid arguments/input, output-write failure, or tool failure.

## Quick Demo

From the repository root, after installation and environment activation:

```powershell
repo-rescue examples/fixtures/missing-module --run-startup-probe
```

Selected lines from real CLI output; omitted lines are not rewritten:

```text
[CAUTION] Executing project code for the startup probe.
Project code may have its own side effects.
Project: examples\fixtures\missing-module
Findings:
[ERROR] Python import: Python could not import the module 'reporescue_fixture_missing_dependency_xyz'.
[ERROR] Startup probe exited with code 1.
  Missing import: reporescue_fixture_missing_dependency_xyz
Root cause:
  ModuleNotFoundError names 'reporescue_fixture_missing_dependency_xyz' -> The requested import 'reporescue_fixture_missing_dependency_xyz' did not complete -> The providing distribution and underlying cause are not established
Repair preview:
  Suggested repair (preview only, MEDIUM): Confirm which distribution provides the missing import module and ensure it is available in the intended Python environment.
Verification (planned, not run):
  - Using the intended interpreter, verify that importing 'reporescue_fixture_missing_dependency_xyz' completes successfully.
  - After explicit confirmation, rerun the same supported startup probe.
No repair actions were executed.
```

The demo fixture is deterministic and does not use network access or persistent
file writes. It finishes in a few seconds, needs no extra package installation or
interactive confirmation, and does not create `__pycache__`. Exit code 1 is expected.
Without the flag, startup remains `requires_confirmation` and project code is not run.

## Current Capabilities

- Shallow Python project detection: root and direct children, at most 1,000 entries;
  `likely` / `unknown` indicate filename evidence, not proof of application readiness.
- Current-interpreter inspection and controlled `--version` execution.
- Root `pyproject.toml` `[project].requires-python` compatibility diagnosis using
  a conservative subset of final-version comparisons, not all of PEP 440.
  Supports `>=`, `>`, `<=`, `<`, `==`, `!=`, comma combinations, and
  `major.minor[.patch]`; equality/exclusion also support a trailing `.*`.
  Unsupported constraints such as `~=` or prereleases produce a limitation notice
  rather than a guessed mismatch.
- Project-local `.venv` / `venv` interpreter detection and identity comparison:
  `Scripts/python.exe` and `bin/python` file presence only; candidates are not run.
  A differing interpreter is a WARNING; ambiguous candidates are not selected.
- Recognition of explicit `ModuleNotFoundError: No module named ...` and limited
  `ImportError: cannot import name ... from ...` lines in captured output.
  Import names are not mapped to PyPI distributions or assumed to prove a missing package.
- Controlled startup probing of root-level `main.py`, with explicit confirmation,
  non-zero exit diagnosis, timeout observation, and bounded stdout/stderr capture.
- Evidence-linked root cause chains, repair previews, and planned verification steps.
- Human-readable terminal reports with concise known-path presentation, plus
  machine-readable JSON that retains real paths for diagnosis and automation.

Scope is Windows First and Python only. Python 3.13.1 has been tested; the minimum
3.12 and Linux/macOS have not been separately verified for this alpha.

## Supported Diagnostics

| Problem or operation | Current support |
| --- | --- |
| Python version mismatch | Supported within the documented constraint subset |
| Local interpreter mismatch | Supported as a WARNING; not proof of project failure |
| `ModuleNotFoundError` | Supported for the explicit missing-module exception line |
| `ImportError` symbol failure | Limited to the explicit cannot-import-name exception line |
| Startup non-zero exit | Supported as an ERROR symptom; independent import diagnoses remain separate |
| Startup timeout | Supported as an INFO observation; not proof of a hang or readiness |
| Automatic repair / package installation | Not implemented |
| Verification execution / rollback | Not implemented |

## Safety

- **Default diagnosis does not execute project code.** RepoRescue itself does not
  modify user project files, change dependencies, run tests/builds, or apply repairs.
  The current Python `--version` probe is separate from project execution.
- **Startup requires explicit opt-in.** Only the absolute current Python interpreter
  plus absolute root-level `main.py` is supported. The executor independently checks
  exact argv, working directory, and resolved entrypoint. No arbitrary shell commands,
  extra arguments, alternative entrypoints, or environment overrides are accepted.
  Execution uses `shell=False`, disabled stdin, and a copy of the current environment;
  `.env` is not read. No supported entrypoint is a capability limitation.
- **Startup has risk level CAUTION.** Executed project code may write files/bytecode,
  access the network, start services, or block. RepoRescue does not automatically
  run pip, install packages, change dependency metadata, or repair the project.
- **Observation is bounded.** The startup window is 5 seconds. Timeout stops the
  direct child and may require up to two additional seconds for termination/output
  cleanup. Descendants are not supervised. Startup capture retains at most 64 KiB
  per stream; returned excerpts are capped at 4,096 characters per stream.
- **Results describe limited observations.** Exit code 0 establishes only successful
  probe completion. Timeout may be normal for a long-running application. Structured
  Evidence records what was observed; repair plans remain `not_executed`, and
  verification steps remain `not_run`.
- **These controls are not a sandbox.** There is no generic secret masking,
  process-tree/resource isolation, or protection against concurrent path changes.
  Terminal path simplification is presentation only. JSON and captured output may
  contain local paths or sensitive data; review reports before sharing.

See [SECURITY.md](SECURITY.md) for private vulnerability-reporting guidance.

## Failure Fixtures

[examples/fixtures](examples/fixtures/README.md) contains deterministic broken
projects for regression tests, demos, and release verification, not production examples:

- `python-version-mismatch`
- `missing-module`
- `symbol-import-failure`
- `startup-nonzero`
- `startup-timeout`
- `combined-failures`

These fixtures need no network or third-party package installation. Review their
small sources before explicitly confirming startup. The symbol-import fixture
suppresses local bytecode; the timeout fixture deliberately exceeds the observation window.

## JSON / Automation

Write a structured report to a new file:

```powershell
repo-rescue "C:\work\sample" --output "C:\work\report.json"
```

Without `--output`, no report file is written. The parent directory must already
exist; an existing report is never overwritten. A write failure may leave a partial new file.

JSON schema **`0.2`** is separate from package version `0.2.0a2`. Reports include
project/environment details, structured Evidence, diagnoses, root cause chains,
repair previews, and verification plans. See the [JSON contract](docs/json-schema.md).
The `healthy` status means only that current limited checks found no issue; it is
not a claim of overall project health. INFO alone does not indicate a project failure.

JSON can support automation and CI, and is designed to support future agent-native
integrations. Agent integrations and MCP are **not implemented**; there is no current
Codex, Claude Code, or Cursor integration.

## Roadmap

Future / planned scope; none of the following is implemented or promised by this alpha:

- Planned: deepen Python diagnostics.
- Planned: Extension SDK / Packs.
- Planned: Node / Java / C++ / Docker support.
- Planned: Safe Repair Executor.
- Planned: verification execution and rollback.
- Planned: RepoRescue Desktop, a GUI / EXE for users less familiar with the CLI.
- Planned: agent integrations.

The current tool has no plugin SDK, GUI, other-language support, or LLM dependency.

## Development

From the repository root:

```powershell
python -m unittest discover
```

Tests require no installation or manual `PYTHONPATH`. Default-workflow tests do not
execute project code; startup tests explicitly confirm tiny audited fixtures and
check that fixture files and modification times stay unchanged.

The CLI coordinates scan/detect, inspection/proposals, controlled execution,
evidence-based diagnosis, and terminal/JSON reporting. The
[original architecture](docs/architecture.md) documents the historical v0.1 baseline;
[docs](docs/) also contains the JSON contract and release review.

The experimental [Extension SDK foundation](docs/extension-sdk.md) includes an
[official example Pack](examples/extensions/example_language_pack.py). Runtime
defaults remain built-in only. Hosts can explicitly discover installed Pack entry
points; discovery is not automatically enabled in CLI, with no marketplace or installer.
Loading an entry point and invoking its factory execute third-party Python code.
Use trusted Packs only; SDK validation is not a sandbox or a grant of Core execution authority.

The [GIF generator](tools/generate_demo_gif.py) uses an already installed project
CLI and Pillow as a local documentation tool. Pillow is not a runtime dependency;
missing Pillow produces a clear message instead of installing it.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Keep changes focused, verify their behavior,
and include evidence of the result. Runtime dependencies remain empty.
Source repository: [furX7/RepoRescue](https://github.com/furX7/RepoRescue).

## License

[MIT](LICENSE). Copyright (c) 2026 RepoRescue contributors.
