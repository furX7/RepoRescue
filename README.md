# RepoRescue

[English](README.md) | [简体中文](README.zh-CN.md)

Explain why a Python project may not run, with evidence-linked diagnoses and
repair plans you can review.

**v0.3.0-alpha.1 · Windows First · Python only · READ ONLY by default · MIT**

<details>
<summary>🇨🇳 简体中文快速说明</summary>

RepoRescue 帮你把 Python 项目的故障线索整理成“证据 → 诊断 → 根因链 → 修复预览 → 验证计划”，
方便判断下一步该检查什么，而不是只看一段 traceback。它不会自动执行修复。

**当前可诊断：** Python 版本约束不匹配、本地解释器不匹配、缺失模块、有限形式的符号导入失败，
以及显式启动探测的非零退出和超时。超时只是观察结果，不代表项目卡死。

**最小用法：** 在 Windows 上使用 Python 3.12 或更新版本，从本仓库根目录运行：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install .
.\.venv\Scripts\repo-rescue.exe "C:\work\sample"
```

将 `C:\work\sample` 换成项目目录。默认不执行项目代码；需要启动探测时，才显式添加
`--run-startup-probe`。该参数会执行根目录 `main.py`，项目代码可能写文件、联网或启动服务。

**主要限制：** Windows First、真实内置诊断仍是 Python only；修复和验证只生成计划。
CLI 不自动加载第三方 Pack，验证不是沙箱。分享 JSON 或捕获输出前，请检查敏感信息。

[完整中文版](README.zh-CN.md)提供全部用法、限制和 Pack 开发说明。

</details>

## Demo

![RepoRescue diagnosing a Python missing-module failure](docs/assets/repo-rescue-demo.gif)

Diagnosing a deterministic missing-module fixture with the installed CLI.

## What is RepoRescue

RepoRescue connects supported environment and runtime observations to a reviewable
explanation of a Python project failure. It helps you decide what to check next,
what a repair would involve, and how to verify it:

Evidence → Diagnosis → Root Cause Chain → Repair Preview → Verification Plan.

A traceback names a symptom; a diagnosis links that symptom to the evidence
actually captured. Underlying causes stay qualified when evidence is incomplete.
A clean report does not prove that the project works.

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
The package version is `0.3.0a1`. Internal Python package: `agent_doctor`.
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

## Current Diagnostics

| Observation | What RepoRescue reports |
| --- | --- |
| `requires-python` mismatch | ERROR within the supported version-constraint subset |
| Project-local interpreter mismatch | WARNING; not proof of project failure |
| `ModuleNotFoundError` | The explicit missing-module exception line |
| `ImportError` symbol failure | Limited to the explicit cannot-import-name exception line |
| Startup non-zero exit | ERROR symptom; import diagnoses remain independent |
| Startup timeout | INFO observation; not proof of a hang or readiness |

Project detection is shallow: root and direct children, at most 1,000 entries.
`likely` / `unknown` reflect filenames, not application readiness. RepoRescue
inspects the current interpreter and may run its controlled `--version` probe.
Local `.venv` / `venv` candidates (`Scripts/python.exe` or `bin/python`) are
compared by identity, never executed; ambiguous candidates are not selected.

Root `pyproject.toml` version checks support `>=`, `>`, `<=`, `<`, `==`, `!=`,
comma combinations and `major.minor[.patch]`; equality/exclusion also support `.*`.
This is not all of PEP 440: `~=`, prereleases and other unsupported constraints
produce a limitation notice instead of a guessed mismatch. Import names are not
mapped to PyPI distributions or assumed to prove a missing package.

Windows Python 3.12 and 3.13 have passed GitHub Actions; local validation uses
Python 3.13.1. Linux/macOS have not been separately verified for this alpha.
There are no runtime dependencies.

## Example / Output Flow

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

### Failure fixtures

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

JSON schema **`0.2`** is separate from package version `0.3.0a1`. Reports include
project/environment details, structured Evidence, diagnoses, root cause chains,
repair previews, and verification plans. See the [JSON contract](docs/json-schema.md).
The legacy `healthy` status means only that current limited checks found no issue.
The additive `assessment` reports findings, incomplete/limited coverage and explicit
limitations; CLI presents the same result. Default scans, ambiguous interpreters
and startup timeouts are inconclusive. Even startup exit 0 leaves project
verification `unverified`; it establishes only probe success. INFO alone does not
indicate a project failure. Schema and existing status/exit-code meanings are retained.

JSON can support automation and CI, and is designed to support future agent-native
integrations. Agent integrations and MCP are **not implemented**; there is no current
Codex, Claude Code, or Cursor integration.

## Safety & Limitations

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

Automatic repair, verification execution and rollback are not implemented.
Current real diagnostic support is Python only; see the roadmap for future scope.

## Extend RepoRescue

v0.3 implements an experimental extension foundation for developers who want
to supply domain-specific diagnostic knowledge. Ordinary CLI users can use the
commands above without writing or loading a Pack.

The programmatic path is:

Public SDK → explicit registration / controlled installed-package discovery
→ Extension Pipeline → Core.

Discovery populates a registry; a host explicitly supplies its snapshot to the
Core pipeline. These APIs do not add Pack-loading flags to the CLI.

### Public SDK

Use `agent_doctor.sdk` rather than private RepoRescue modules. Its explicit public
surface re-exports metadata, capability protocols, Core data models and author
validation/error types. `EXTENSION_API_VERSION = "1"` is experimental during v0.3;
it is separate from package version `0.3.0a1` and JSON schema `0.2`.

### Pack model

A Pack groups knowledge for a technical domain; an Extension contract defines a
capability. Metadata declares its ID, `PackKind`, API version, supported platforms,
required tools and `Capability` set.

| Contract | Pack responsibility |
| --- | --- |
| Detector | Identify supported project markers |
| EvidenceProvider | Collect observations |
| DiagnosisRule | Produce findings linked to evidence |
| RepairPlanner | Describe repairs and risk |
| Verifier | Describe verification checks |

Packs supply knowledge and plans. Core retains ordering, compatibility checks,
safety, consent, resource limits and execution authority. `python.core` is the
only real built-in language Pack; other kinds do not imply implemented support.

### PackRegistry

`PackRegistry()` starts empty; `PackRegistry.default()` contains only `python.core`.
Register explicit Pack instances with `register(pack)`. Validation and duplicate-ID
checks happen before insertion, so a rejected registration leaves membership
unchanged. Registration does not call Pack business stages.

Use `snapshot()` to capture registration order. Core's pipeline checks runtime
compatibility; see the SDK guide for declaration and data-handling rules.

### Controlled discovery

A host can explicitly discover already installed, trusted Pack factories:

```python
from agent_doctor.sdk import PackRegistry, discover_installed_packs

registry = PackRegistry.default()
result = discover_installed_packs(registry)
snapshot = registry.snapshot()
```

Only installed entry points in `reporescue.packs` are queried. Their no-argument
factories return Pack instances. Ordering is deterministic, ordinary discovery
failures are isolated, and duplicates cannot replace an existing Pack.
This is not an installer, marketplace or enable/disable manager. It does not scan
plugin directories, query the network, or run Pack diagnostic stages.

**The CLI does not automatically discover or load third-party Packs.**
SDK import and default registry creation do not trigger discovery.

### Minimal example Pack

The [official example](examples/extensions/example_language_pack.py) implements
`example.language` using only the public SDK and standard library. It observes
`.reporescue-example` and produces evidence, an INFO diagnosis, and descriptive
repair/verification plans. It does not execute commands or change the project.

The [SDK demo](examples/extension-demo/README.md) is synthetic and separate from
failure fixtures. The example is not a default Pack or an installed wheel module.
From a source checkout, this demonstrates explicit registry use:

```python
from agent_doctor.sdk import PackRegistry
from examples.extensions.example_language_pack import ExampleLanguagePack

registry = PackRegistry.default()
registry.register(ExampleLanguagePack())
snapshot = registry.snapshot()
```

### Trust boundary and documentation

**Third-party Packs are executable Python code. Validation is not a sandbox.**
Entry-point imports and factories execute that code. The SDK grants no Core
executor, arbitrary shell or repair authority, but Python code can import other
APIs and cause its own side effects. Install and load only trusted Packs.

Start with the [SDK guide](docs/extension-sdk.md) for author contracts and integration
examples. The [architecture](docs/architecture.md) separates the implemented v0.3
foundation from the historical v0.1 design. See [release notes](docs/releases/v0.3.0-alpha.1.md),
[CHANGELOG](CHANGELOG.md) and [docs](docs/) for compatibility and release context.

## Roadmap

Future / planned scope; none of the following is implemented or promised by this alpha:

- Planned: deepen Python diagnostics.
- Planned: Node / Java / C++ / Docker support.
- Planned: Safe Repair Executor.
- Planned: verification execution and rollback.
- Planned: RepoRescue Desktop, a GUI / EXE for users less familiar with the CLI.
- Planned: agent integrations.

The current tool has no GUI, other-language support, or LLM dependency.

## Contributing

From the repository root:

```powershell
python -m unittest discover
```

Tests require no installation or manual `PYTHONPATH`. Default-workflow tests do not
execute project code; startup tests explicitly confirm tiny audited fixtures and
check that fixture files and modification times stay unchanged.

The [GIF generator](tools/generate_demo_gif.py) uses an already installed project
CLI and Pillow as a local documentation tool. Pillow is not a runtime dependency;
missing Pillow produces a clear message instead of installing it.

See [CONTRIBUTING.md](CONTRIBUTING.md). Keep changes focused, verify their behavior,
and include evidence of the result. Runtime dependencies remain empty.
Source repository: [furX7/RepoRescue](https://github.com/furX7/RepoRescue).

## Security

For vulnerability-reporting guidance, see [SECURITY.md](SECURITY.md).
Arrange a private channel before sharing sensitive details; do not post credentials
or private user data in public issues.

## License

[MIT](LICENSE). Copyright (c) 2026 RepoRescue contributors.
