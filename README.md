# RepoRescue

Evidence-driven diagnosis and recovery planning for broken development projects.

RepoRescue diagnoses why a development project may not run, explains the root
cause chain, and generates safe repair and verification plans without modifying
the project. Current evidence coverage is limited to shallow Python markers,
the interpreter version probe, the root pyproject.toml Python requirement, and
fixed project-local interpreter paths.

RepoRescue 是面向开发者和 AI 编程 Agent 的项目故障诊断工具，基于结构化证据进行诊断，并提供根因链、修复方案预览和验证计划。当前版本为 READ ONLY：不会自动修改项目、安装依赖或执行修复；目前为 Windows First、Python only。

**v0.2.0-alpha · Windows First · Python only · READ ONLY · MIT**

Source repository: [furX7/RepoRescue](https://github.com/furX7/RepoRescue).

Current checks scan filenames, inspect the root Python requirement, and probe
the interpreter running RepoRescue.
They do not execute project code. A clean report does not prove a project works.

## Why

Evidence → Diagnosis → Root Cause Chain → Repair Preview → Verification Plan.
Each problem references evidence. Causes remain limited to what was observed;
repair and verification descriptions are plans, not executed operations.

## Quick start (Windows)

Use Python 3.12 or newer. From the source directory:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install .
.\.venv\Scripts\repo-rescue.exe --help
.\.venv\Scripts\repo-rescue.exe --version
.\.venv\Scripts\repo-rescue.exe "C:\work\sample"
.\.venv\Scripts\repo-rescue.exe "C:\work\sample" --output "C:\work\report.json"
```

Source installation:

```powershell
python -m pip install .
```

To install a release wheel, download the `.whl` file from GitHub Releases, then run:

```powershell
python -m pip install <path-to-downloaded-wheel>
```

The package version is `0.2.0a1` (Python's spelling of this alpha release).
External brand: RepoRescue. Internal Python package: `agent_doctor`.
Former working name: Agent Doctor. The `agent-doctor` CLI is retained as a temporary
compatibility alias; it displays the RepoRescue brand and delegates to the same entry point.
With an activated environment, use `repo-rescue <project_path>`.
After installation, `python -B -m agent_doctor.cli <project_path>` also works.
From a source checkout without installation:

```powershell
python -B -c "import sys; sys.path.insert(0, 'src'); from agent_doctor.cli import main; sys.exit(main(sys.argv[1:]))" "C:\work\sample"
```

Without `--output`, no report file is written. Explicit output creates a new UTF-8
JSON file; the parent directory must exist, and existing files are never overwritten.
Installation is an explicit user action. RepoRescue never installs dependencies.

### Exit codes

- `0`: completed without ERROR/CRITICAL diagnostics; warnings may exist.
- `1`: completed with ERROR/CRITICAL diagnostics.
- `2`: invalid arguments/input, output-write failure, or tool failure.

## Example terminal report

An empty directory can produce this report (interpreter details vary):

```text
RepoRescue
Project: C:\work\sample
Detection: unknown
Python Environment: available; Python 3.13.1; launch not verified; executable: C:\Python\python.exe

Diagnostics:
- [WARNING] project_detection: The shallow scan found no Python project markers
  Recommended actions:
  - Check that the selected directory is the intended project root
  - Check whether Python files are below the shallow scan depth
  - Check for common Python project files
  Evidence: detection:python
  Root cause: Shallow scan found no Python markers
  Suggested repair (preview only, LOW): Review the selected project root and Python file locations
  Verification (planned, not run): Rescan the confirmed root and review actual matched Python filenames within the supported depth.

READ ONLY: Repair preview only. Verification steps were not run.
RepoRescue currently performs limited checks.
```

`unknown` means no markers were found within the scan limits, not that the directory
cannot contain a Python project. No-problem reports explicitly mention limited checks.

## Current capabilities

- Shallow Python marker detection (`likely` / `unknown`).
- Current interpreter and dependency-manifest filename inspection.
- Root pyproject.toml [project].requires-python compatibility checks.
- Project-local .venv/venv interpreter identity checks (file presence only).
- Controlled current-interpreter `--version` execution.
- A few structured, evidence-backed diagnosis rules.
- Short evidence-referenced root cause chains.
- Descriptive repair previews with planned verification steps.
- Concise terminal output and agent-readable JSON.

## Safety and limitations

- READ ONLY target handling: no source changes, installation, deletion, project-code
  execution, tests, builds, startup, or target bytecode generation.
- The executor independently validates exact argv and working directory. Its only
  executable operation is `<current Python executable> --version`, with `shell=False`
  and a timeout. Unknown SAFE commands are rejected; CAUTION does not execute;
  DANGEROUS is rejected. No interactive approval bypass exists.
- Repair plans always say `execution_status: not_executed`; verification steps say
  `status: not_run`. They grant no permission to execute anything.
- Scans root and direct children, at most 1,000 entries. Skips `.git`, `.venv`, `venv`,
  `node_modules`, `__pycache__`, symlinks and junctions. Virtual-environment/cache roots
  are rejected. Weak filename markers can give false positives; deeper projects can
  be missed. Only the root pyproject.toml is read (up to 64 KiB) for
  [project].requires-python; dependency contents/conflicts are not analyzed.
- The interpreter is the one running RepoRescue, not an automatically selected
  project environment. Launch verification is only a version probe.
- No arbitrary traceback, import-error, pytest, dependency, or application diagnosis.
- Confidence is a rule indicator, not a statistical probability. Chains may describe
  consequences without establishing an underlying cause.
- Windows First; Python 3.13.1 was tested. The minimum 3.12 and Linux/macOS have not
  been separately verified for this release.
- Controls are not a sandbox: they trust the current interpreter, offer no generic
  secret masking, hard capture-memory bound, process-tree/resource isolation, or
  protection against concurrent filesystem changes. Returned output is capped.
- Explicit report-write failure can leave a partial new file. Reports contain local
  paths and captured output; review them before sharing.
- No LLM, repair execution, verification execution, rollback, other languages,
  Markdown/HTML/database reports, MCP, GUI, Web UI, or IDE integration.

## Python version requirement check

Final release versions major.minor[.patch] support >=, >, <=, <, ==, != and comma
combinations. Equality/exclusion also support a trailing .* after major.minor or
major.minor.patch, such as ==3.12.* or >=3.11,!=3.12.*. Versions are compared
numerically, padding missing patch components with zero.

This is a conservative subset, not full PEP 440: no ~=, ===, epochs, prerelease,
dev/post/local versions, environment markers, or ==3.*. Non-final current
interpreters also remain unsupported. Unsupported syntax is recorded in evidence
and shown as a tool limitation, never guessed as a mismatch. Missing declarations
produce no version diagnosis. Read errors, oversized files, or invalid TOML
produce metadata notices without version-fault conclusions.

A supported mismatch produces an ERROR, evidence-linked chain, and a MEDIUM-risk
preview to select or manually prepare a compatible environment. Environment
selection/provisioning can affect dependencies; it is not guaranteed reversible.
Two planned checks cover the selected interpreter's version and requirement
comparison. Neither plan is executed; automatic environment changes never occur.

## Project-local interpreter check

Only root .venv and venv candidates are probed: Scripts/python.exe and bin/python.
An empty directory is not a candidate. These interpreter files are never read or
executed, and their presence does not prove they work or are intended for the project.
Recognizing POSIX filenames does not change the Windows First platform scope.

One candidate with a different current interpreter produces a WARNING, not proof
of project failure. Paths are made absolute, parent aliases and executable links
are resolved, and Windows comparisons ignore case. Interpreter directory identity
is retained: a POSIX venv link to base Python is still a different environment
from running that base interpreter outside the venv.

Multiple candidate files are ambiguous; no interpreter is chosen or wrong-interpreter
diagnosis generated. Matching or absent candidates produce evidence only. Filesystem
or normalization errors leave comparison unavailable and produce a limitation notice.
No conda, pyenv, Poetry/uv cache, nested or custom environment paths are inspected.

The LOW-risk preview asks the user to confirm intent, manually select the interpreter,
and rerun diagnosis. Planned verification checks sys.executable, normalized identity,
and existing Python version checks. It does not activate an environment, start a
shell, change PATH, install dependencies, or restart RepoRescue.

## Machine report contract

JSON schema version `0.2` is separate from tool/package version `0.2.0a1`.
Existing diagnosis fields are retained; consumers should accept additive fields.
See [JSON contract](docs/json-schema.md) for field definitions.

Overall status uses this priority:

1. ERROR/CRITICAL → `issues_detected`.
2. Otherwise unknown detection → `unknown`.
3. Otherwise WARNING → `issues_detected`.
4. Otherwise → `healthy` (only within the current limited checks).

INFO safety notices alone do not indicate project failure. Tool failures return
exit code 2 instead of generating a fabricated project report.
Capabilities describe this tool version, not a project's health.

Simplified JSON excerpt (project/environment and some diagnosis fields omitted):

```json
{
  "schema_version": "0.2",
  "tool": {"name": "repo-rescue", "version": "0.2.0a1"},
  "status": "unknown",
  "capabilities": {
    "diagnosis": true,
    "root_cause_analysis": true,
    "repair_preview": true,
    "verification_plan": true,
    "repair_execution": false,
    "verification_execution": false,
    "rollback": false
  },
  "diagnostics": [{
    "diagnosis_id": "python_detection_unknown",
    "problem": "The shallow scan found no Python project markers",
    "root_cause_chain": [{"id": "detection:no_markers", "title": "Shallow scan found no Python markers", "evidence_refs": ["detection:python"]}],
    "repair_plan": {
      "execution_status": "not_executed",
      "verification_steps": [{"type": "file_check", "description": "Rescan the confirmed root and review actual matched Python filenames within the supported depth.", "status": "not_run"}]
    }
  }],
  "evidence": [{"evidence_id": "detection:python"}]
}
```

The environment probe already run during diagnosis is distinct from future repair
verification steps, which are not run.

## Architecture

```text
CLI → workflow → scan/detect → inspect/propose → controlled executor
             → evidence + rule diagnosis → terminal/JSON report
```

Shared dataclasses keep the modules simple. Diagnosis cannot modify files; report
code only presents results. No registry, generic rule engine, or repair executor.
The [original design](docs/architecture.md) is a historical v0.1 baseline.

## Future scope (not implemented)

Future work may include repair execution and verification, rollback, additional
rule packs, Node.js/Java/C++/Docker, Linux/macOS, optional LLM providers, MCP/agent
tool integration, GUI/Web/IDE interfaces. None is promised by this alpha release.

## Contributing and tests

Fork, create a branch, make a small focused change with tests, and submit a PR.
See [CONTRIBUTING](CONTRIBUTING.md) and [SECURITY](SECURITY.md).

```powershell
python -m unittest discover
```

Run from the repository root; no installation or manual PYTHONPATH is required.
Integration tests use isolated fixtures and never execute their source code.

## License

[MIT](LICENSE). Copyright (c) 2026 RepoRescue contributors.
