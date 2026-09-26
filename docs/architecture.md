# RepoRescue v0.1 Minimum Architecture

## Status

Historical v0.1 design baseline. Current implemented behavior, limitations, and
v0.2.0-alpha.2 preview additions are documented in README and json-schema.md.
Some planned safeguards (such as general secret masking) are not implemented.

## Current alpha startup boundary

Metadata inspection and the default CLI remain READ ONLY. A separate, explicitly
confirmed project execution probe may execute root-level main.py. RepoRescue itself
does not modify project files; project-defined file/network/service side effects
are possible. Do not describe confirmed execution as absolutely read-only.

The small startup.py module proposes the entrypoint and collects structured outcomes.
commands.py independently revalidates absolute current Python, exact single absolute
root main.py argument, cwd equal to root, readability, and resolved path containment.
Only CAUTION is accepted; DANGEROUS and arbitrary argv remain prohibited. The existing
version-only executor does not gain an approval bypass or general command support.

The CLI has no confirmation interaction and only displays the proposal. A library
caller must obtain informed confirmation for the exact proposal before explicitly
setting confirm_startup=True. The workflow coordinates; diagnosis consumes startup
Evidence and reuses independent import rules. Reports only present outcomes.

The observation window defaults to 5 seconds and cannot exceed 5. Timeout is an INFO
observation, not proof of failure or readiness. Only the direct child is killed and
waited for; up to one second each is allowed for termination and output cleanup.
Unconfirmed termination is explicitly recorded. Descendants and their inherited pipes
are not supervised and may leave background output readers. Two local threads continuously
drain startup stdout/stderr in fixed 8 KiB reads, retaining at most 64 KiB per stream
and discarding excess. Excerpts are capped at 4096 characters; truncation does not
change execution status. Concurrent path replacement remains a limitation. No sandbox,
service manager, framework detector, repair executor, or new dependency is added.

Absent main.py is a tool coverage limitation; unconfirmed proposals are not diagnoses.
Nonzero startup exit produces an ERROR symptom with its own evidence chain and LOW
repair preview. python_import, python_environment, and python_version stay independent.

This document records the approved v0.1 architecture. It defines design only; implementation is gated on the user's explicit `START IMPLEMENTATION` instruction.

## Product goal

Given a project path, RepoRescue scans the project, identifies a Python project, inspects the environment, proposes and safely executes approved diagnostic commands, captures evidence, performs rule-based diagnosis, and presents a report.

## v0.1 scope

- Windows First. Platform-specific operations must stay behind small, clear boundaries so later platform support does not require rewriting the diagnostic workflow. Linux and macOS are not formally supported in v0.1.
- Local CLI interface.
- Python project diagnosis only. Python is the first built-in diagnostic plugin; the RepoRescue core implementation language does not limit future project languages.
- Rule-based diagnosis works without an API key or network model service.
- No concrete LLM provider in v0.1. A provider interface may be introduced when an actual provider is needed.
- Read-only with respect to the target project: no source edits, dependency installation, system configuration changes, or file deletion.
- Controlled command execution for necessary environment checks and diagnostic/build/test/start commands, subject to risk policy and explicit user approval as described below.
- Terminal summary by default. JSON report output is available when the user explicitly supplies `--output`.
- No report is written into the target project by default.

## Minimum architecture

```text
CLI
  → Workflow
      → Project scan and Python detection
      → Environment inspection and command proposals
      → Command risk policy and approval
      → Controlled command execution
      → Evidence creation
      → Rule-based diagnosis
      → Terminal summary / optional JSON report
```

The CLI is an interface layer. The workflow and diagnostic logic must not depend on CLI parsing or presentation. Keep the first implementation small: ordinary functions and a few structured data objects are preferred over multiple abstraction layers, service frameworks, or empty extension modules.

## Minimum source layout

```text
repo-rescue/
├── pyproject.toml
├── README.md
├── src/
│   └── agent_doctor/
│       ├── __init__.py
│       ├── cli.py
│       ├── workflow.py
│       ├── project.py
│       ├── commands.py
│       ├── diagnosis.py
│       ├── report.py
│       └── python_plugin.py
└── tests/
    ├── test_project.py
    ├── test_commands.py
    ├── test_diagnosis.py
    └── test_workflow.py
```

This is the proposed implementation layout, not a request to create source files now. Do not create empty directories or modules for future features. Split modules only when real implementation needs make their responsibilities hard to understand or test.

## Module responsibilities

| Module | Single responsibility |
|---|---|
| `cli.py` | Parse options, show commands requiring approval, obtain the user's decision, and present terminal or JSON output. |
| `workflow.py` | Run the diagnostic steps in order, pass structured values between them, and report task outcomes. |
| `project.py` | Validate the project path, scan required project files, and collect project/environment information. |
| `python_plugin.py` | Detect Python projects and propose relevant dependency checks and diagnostic commands. |
| `commands.py` | Classify risk, enforce command policy and execution limits, run approved commands, and capture process results. |
| `diagnosis.py` | Produce rule-based diagnoses from structured evidence. |
| `report.py` | Render a terminal summary or JSON report. |

The Python plugin proposes commands but cannot execute them or bypass command policy. With only one built-in plugin, the workflow calls it directly; a registry is unnecessary in v0.1.

## Core data flow and objects

```text
Project path
  → ProjectInfo
  → Python detection and command proposals
  → risk classification and approval
  → ExecutionResult
  → Evidence
  → DiagnosisResult
  → terminal summary / optional JSON
```

Use small structured objects rather than passing core facts as prose:

- `ProjectInfo`: normalized root path, scan time, and relevant file/manifest summaries.
- `EnvironmentInfo`: available relevant tools and versions, with inspection outcomes.
- `CommandProposal`: executable, argument list, working directory, source, reason, and proposed risk.
- `ExecutionResult`: exit code, stdout/stderr or bounded references, duration, timeout/termination and truncation status.
- `Evidence`: evidence type, source, associated object, location or bounded content summary, and masking status.
- `DiagnosisResult`: problem, category, severity, evidence references, probable causes, confidence, recommended actions, and source.
- `DiagnosticContext`: a lightweight grouping of task-scoped data/references. It must not become a store for all file contents, logs, or workflow behavior.

Avoid a general event bus, database, evidence service, or state-machine framework in v0.1. Add them only when an observed requirement justifies the complexity.

## Command execution policy

Risk levels:

- **SAFE**: may run automatically only when it is within the explicit allowlist and execution limits.
- **CAUTION**: do not run automatically by default. The CLI may show the exact command, working directory, risk level, and reason, then request explicit user confirmation.
- **DANGEROUS**: always reject in v0.1.

Build, test, start, and other commands that may execute project code must never run silently. Before execution, show the command, working directory, risk level, and reason and obtain user confirmation. A plugin proposal is not authorization. Unknown or unclassifiable commands must not be treated as SAFE.

The command runner must use an executable plus argument array, not shell-string concatenation. It must validate project/work paths, reject path escape, apply timeouts, capture stdout/stderr/exit code, attempt process termination on timeout, bound output and other practical resources, and mask secrets in collected output. It must not install global or project dependencies, change system configuration, or delete files.

These controls do not provide a full security sandbox: project build/test commands can execute arbitrary project code. Keep policy conservative and make the command and its purpose visible before approval. Strong OS/container isolation is future scope.

## Diagnosis and repair boundary

The v0.1 diagnosis flow is rule-based and consumes structured evidence. It has no required LLM, API key, or network dependency. The architecture should permit a future `DiagnosisProvider` interface, but no concrete provider or multi-provider aggregation framework is needed now.

Diagnosis only produces findings and recommendations. It cannot write files or invoke repair operations. v0.1 is READ ONLY. Repair planning, approval-bound patches, execution, verification, and rollback are future work.

## Future scope — not implemented in v0.1

- Node.js, Java, C/C++, Docker, MCP, and AI Agent project plugins.
- Linux and macOS platform support.
- Concrete LLM providers (OpenAI, Anthropic, Gemini, local models) and model-based diagnosis.
- MCP server/tool adapter, IDE plugin, Desktop GUI, Web UI, and REST API.
- Repair planner/executor, file changes, verification, and rollback.
- Third-party plugin loading, plugin marketplace, database, remote execution, and distributed workflow.
- Markdown report format.

Keep boundaries clear enough that these can be added when needed, but do not implement placeholders, empty interfaces, or infrastructure for them in advance.

## Minimum implementation sequence for Phase 1

1. Define the small structured objects and errors used by the workflow.
2. Validate project paths, scan relevant files, and detect Python projects.
3. Inspect the environment and produce Python diagnostic command proposals.
4. Implement risk classification, explicit approval, bounded execution, and result capture.
5. Build evidence and implement basic rule-based diagnosis.
6. Produce terminal output and optional JSON via `--output`.
7. Verify the end-to-end read-only diagnostic flow and module boundaries.

Phase 1 implementation must not begin until the user explicitly sends `START IMPLEMENTATION`.

## Architecture review

- **Small core:** one workflow, one built-in plugin, a small set of modules, structured objects, and a CLI. No framework or service decomposition is required.
- **Clear dependency direction:** CLI calls workflow; workflow coordinates modules; plugin proposes; command policy/executor controls execution; diagnosis consumes evidence; report formats results. No module needs another module's mutable internals.
- **Extensibility without completeness:** language, platform, provider, and UI boundaries remain understandable, but future implementations are not prebuilt.
- **Safety:** dangerous commands are rejected, caution and project-code commands require visible user approval, execution is bounded, outputs are masked, and the target project is not modified by RepoRescue.
- **Testability:** filesystem access, process execution, platform-specific behavior, and the Python plugin can be replaced or isolated in tests. No LLM mock is needed until a provider exists.
- **Known limitation:** command controls are not equivalent to sandboxing arbitrary project code; this limitation must remain explicit.
