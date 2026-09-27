# RepoRescue v0.1 Minimum Architecture

## Status

Historical v0.1 design baseline, with implemented v0.2 startup and v0.3 extension
sections below. Current v0.3 includes the public SDK, explicit registry and
controlled installed-Pack discovery API. CLI/workflow still use Python only,
without automatic third-party loading. Package version is 0.3.0a1; JSON is 0.2.
Some planned safeguards (such as general secret masking) are not implemented.

## Status semantics — v0.4 Phase 1 / Step 1

Reporting derives one shared assessment from existing detection, environment,
diagnoses and Evidence for both CLI and JSON. No new probes, diagnoses, repair
authority or workflow stages are introduced. Findings, check coverage and
project verification are separate: a successful version or startup probe is
only evidence about that probe.

The additive JSON `assessment` reports `issues_detected`, `inconclusive` or
`no_issues_detected`, together with `incomplete`/`limited` coverage and explicit
limitations linked to Evidence. Project verification is always `unverified`
with current capabilities. Known findings take priority over incomplete checks;
an ambiguous interpreter, unsupported check or startup timeout cannot become
a positive health conclusion. Known pipeline failures are passed to both report
formats as coverage limitations. CLI and assessment share one startup-success
predicate: success status, confirmed execution and exit code 0. CLI presents
this assessment and probe outcomes.

Schema remains 0.2. Legacy `status`, diagnoses, capabilities and exit codes keep
their previous semantics; `healthy` remains only a legacy findings summary.
Consumers should use assessment when present and never infer verification from
an old report without it. See [JSON contract](json-schema.md) for exact fields.
Package version remains unchanged. Historical validation records are preserved.

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

The CLI opts in through --run-startup-probe; otherwise it displays the proposal
without running it. A library caller must obtain informed confirmation before explicitly
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

The sections from Product goal onward record the historical v0.1 design.
The extension sections below describe the implemented v0.3 foundation.

## Extension Architecture — v0.3 foundation

This section records the implemented first extraction step of v0.3, following
Small Core, Large Ecosystem. Built-in Extension Pipeline records step two, and Pack Architecture
records step three;
sections from Product goal onward retain the historical v0.1 design.

**Core owns policy and orchestration.**
**Extensions provide knowledge, not unrestricted authority.**
This is the Small Core, Large Ecosystem boundary, not a plugin runtime.

```text
RepoRescue Core
├─ Workflow
├─ Safety
├─ Data Models
└─ Extension Contracts
     ├─ Detector
     ├─ EvidenceProvider
     ├─ DiagnosisRule
     ├─ RepairPlanner
     └─ Verifier
```

`src/agent_doctor/extensions.py` defines independent structural `typing.Protocol`
contracts. A component can implement just one stage; there is no required base
class or run-everything method. `Extension` exposes metadata only. Each stage
exposes an `id` and these methods, using the existing Core models:

| Contract | Signature / result |
|---|---|
| Detector | `detect(project) -> DetectionResult` |
| EvidenceProvider | `collect(project, environment) -> Sequence[Evidence]` |
| DiagnosisRule | `diagnose(project, detection, environment, evidence, executions=()) -> (Sequence[DiagnosisResult], Sequence[Evidence])` |
| RepairPlanner | `plan(project, diagnosis, evidence) -> RepairPlan or None` |
| Verifier | `build_verification(project, diagnosis, repair_plan) -> Sequence[VerificationStep]` |

Diagnosis returns evidence as well as findings because current Python rules
derive observations from captured output and already return both. Evidence IDs
and references remain governed by Core models. Repair/verification results are
descriptions: `not_executed` / `not_run`, with no new execution authority.

There is no shared Context. Inputs are the existing project/detection/environment
snapshots, evidence and already captured execution outcomes. `ExecutionResult`
contains data and a command description, not an executor. `EnvironmentInfo`
describes the current Python runtime; it does not grant environment write access.
Future language-specific observations can be expressed through Evidence and
captured outcomes rather than assuming this Python runtime is their runtime.
Extensions must not mutate caller inputs (including the existing shallow-frozen
`Evidence.metadata` dictionary). Runtime Protocol checks establish member presence,
not signature correctness or trust; type annotations and behavior tests also matter.

`ExtensionMetadata` is a frozen Python dataclass with `id`, `name`, `version`,
`api_version`, `kind`, `capabilities`, `supported_platforms`, and `required_tools`.
Capabilities are a `frozenset[Capability]` containing only DETECT, INSPECT,
DIAGNOSE, PLAN_REPAIR, and VERIFY. Arbitrary strings are rejected. Shell, network,
installation and file writes are privileged Core-controlled actions, not extension
capabilities. There is no manifest parser.

`EXTENSION_API_VERSION = "1"` is independent of the pack's metadata version,
RepoRescue package version, and JSON schema version `0.2`. Metadata is internal
and never added to machine reports; existing Core JSON capabilities are unchanged.

`check_compatibility(metadata, ExtensionEnvironment(...))` is a pure comparison
over caller-supplied platform, available tool names and Core extension API version.
It returns a typed status: `compatible`, `api_version_mismatch`,
`unsupported_platform`, or `missing_required_tool`, including missing tool names.
The first failure wins in API/platform/tools order. Identifiers use exact canonical
names; an empty platform list supports none. The checker neither discovers tools
nor performs filesystem, process or network operations. Compatibility is not consent.

`src/agent_doctor/python_extension.py` contains `PythonCoreExtension`, a thin facade
with id `python.core`, name `Python Core Pack`, pack version `0.1`, API version `1`,
all five capabilities, platform `windows`, and required tool `python`. Its mappings are:

- Detection delegates to `detect_python_project`.
- Collection delegates to the bounded requirement and local interpreter inspectors.
- Diagnosis maps existing optional observations by evidence kind and calls the
  unchanged `diagnose`, including import/startup/timeout rules. Core supplies startup
  observations and captured executions. Additional evidence kinds are not interpreted
  by this Python facade; each future pack owns its own knowledge.
- Planning exposes the existing diagnosis's `repair_plan`.
- Verification exposes the supplied preview's `verification_steps`, or none.

At the first extraction step, workflow still called Python functions directly and owned
Project → Detection → Evidence → Diagnosis → Root Cause → Repair Plan → Verification.
The facade was initially exercised only in tests; step two connects it to workflow. Command
and filesystem policy, consent, timeout and output limits remain with Core. No
extension may bypass these controls, directly execute arbitrary shell, write project
files, alter PATH, install packages, or bypass future transaction/rollback controls.
Python-native contracts cannot sandbox hostile in-process code. Explicit discovery
now loads trusted installed Python code, as described below; no sandbox is claimed.

`ExtensionUnavailable`, `ExtensionIncompatible`, and `ExtensionFailure` carry the
extension ID and a message. They respectively mean missing prerequisites/observations,
unsupported API/platform, and a failed stage. The first step defined semantics only;
the built-in pipeline below now isolates these declared errors per extension/stage.

The test-only `FakeLanguageExtension` implements the contracts independently of Python,
proves metadata/capability/compatibility behavior, and uses snapshots without modifying
projects or receiving an executor. It is not in the runtime package or CLI.

Future Node / Java / C++ packs can implement these same stage contracts and return
Core Evidence, DiagnosisResult, RepairPlan and VerificationStep objects, without adding
a language-specific orchestration algorithm. The first extraction step did not
include registry/discovery; their implemented boundaries are described below.
Plugin folders, marketplace, installation, third-party isolation, other-language
support, repair/verification execution, rollback, GUI and LLM remain future work.
README Roadmap remains planned.

## Built-in Extension Pipeline

Core owns orchestration. Built-in extensions are statically configured in
`src/agent_doctor/extension_pipeline.py`:

```python
BUILTIN_EXTENSIONS = (PythonCoreExtension(),)
```

Python is currently the only built-in extension. Default workflow/CLI do not
call installed-Pack discovery; hosts use the explicit SDK API described below.
Plugin folders and a registry service are not implemented.

```text
Core workflow
  → scan project / inspect current runtime
  → prepare_extensions: unique IDs + Pack validation + compatibility
  → DETECT
  → INSPECT
  → Core policy / executor: existing version and startup probes
  → DIAGNOSE
  → PLAN_REPAIR (preview)
  → VERIFY (planned steps)
  → existing terminal / JSON report
```

`workflow.py` explicitly calls `run_extension_stage` once for each stage, in this
order. The helper runs extensions sequentially in tuple order and checks each
declared capability. There is no run-everything method, concurrent execution,
manager, lifecycle framework or extension-controlled workflow.

`prepare_extensions` rejects duplicate metadata IDs as a configuration error,
then captures a structurally validated PackRegistry snapshot before invoking the existing
compatibility checker for each extension. Core supplies
the canonical current platform and current Python availability without scanning
other tools. API mismatch, unsupported platform and missing required tools are
recorded with extension ID, status and missing tool names; incompatible extensions
never receive a stage call.

`PythonCoreExtension` remains a thin adapter: detection, metadata collection,
diagnosis and previews delegate to the existing functions/models. Its optional
`propose_diagnostic_commands` adapter returns the existing version-probe descriptions
during INSPECT. An internal structural Protocol checks for this optional built-in
method; the five SDK stage contracts and their capabilities are unchanged. Evidence
providers without this method still work. This adds no execution capability:
`CommandProposal` is data, never permission. Workflow has no direct dependency on
`python_plugin.py` or `diagnosis.py`. Existing startup proposals/capture remain in
the Core startup boundary, and all executions retain the unchanged allowlists,
consent, cwd/path checks, timeouts and output limits in `commands.py`.

Small internal dataclasses hold per-extension stage outputs (`ExtensionRun`),
merged results (`ExtensionRunResult`) and local failures (`ExtensionFailureInfo`).
Inputs expose snapshots, evidence and captured outcomes; no executor, shell,
filesystem service or mutable global context is passed to extensions.

Each extension owns its evidence batch and diagnoses. The diagnosis dual return
contains that extension's complete evidence batch, including derived evidence;
it replaces its collection batch to preserve the existing Python evidence order
without duplicating collected observations. Final batches and diagnoses append
in static extension order. There is no ranking, correlation or deduplication
engine. Packs must use distinct evidence IDs and return the observations needed
by their findings. Repair previews remain attached to their originating diagnosis;
a planner cannot attach a preview for another diagnosis. Verification updates
that preview's planned steps, retaining `not_executed` / `not_run`. Schema 0.2
stores verification in a repair preview, so orphan verification steps or an empty
verification list for a preview are recorded as local failures.

Extension failures are isolated at the contract boundary. Only
`ExtensionUnavailable`, `ExtensionIncompatible` and `ExtensionFailure` are caught.
A failed stage commits no partial batch; earlier completed outputs survive and
that extension's later stages are skipped. Other compatible extensions continue.
Unexpected exceptions remain visible; `KeyboardInterrupt` and `SystemExit` are
never swallowed. This is minimal in-process failure handling, not a sandbox.

Local failures remain internal in `WorkflowResult.extension_failures`. If no
diagnosis extension is usable before probes, Core skips execution; if none
completes diagnosis, it returns a controlled `WorkflowError` carrying these
failures. CLI uses its existing tool-error path (exit 2) with an explicit limitation,
instead of constructing normal findings or a healthy report. Successful extensions
still return their results when another extension fails. CLI options, package
version, terminal output on existing successful paths, JSON schema 0.2 and existing
machine-report capabilities remain unchanged; no extension metadata is serialized.

Future Node / Java / C++ packs can implement the same stage contracts and be
added to the static tuple, using this common Core sequence. They do not need a
language-specific workflow. Tool availability descriptors and any newly supported
probe policy must be supplied by Core; current execution support is still limited
to the existing Python probes. This step does not implement other languages,
dynamic enable/disable, manifest file parsing, plugin installation/marketplace,
repair execution, verification execution, transaction/rollback, GUI or LLM support.

## Pack Architecture

A **Pack** is a group of diagnostic knowledge and recovery planning capabilities
for one technical domain. It is the organizational / distribution unit of the
existing Extension contracts, not a second execution framework:

- **Extension Contract:** what capability can be provided.
- **Pack:** how domain knowledge is grouped and distributed.
- **Core:** owns orchestration, authority and safety.

```text
RepoRescue Core
│
├─ Workflow
├─ Safety / Execution
├─ Core Models
│
└─ Extension Pipeline
    │
    └─ Python Language Pack
        ├─ Detection
        ├─ Evidence
        ├─ Diagnosis Rules
        ├─ Repair Planning
        └─ Verification Planning
```

`PackKind` classifies domains: LANGUAGE, TOOLCHAIN, FRAMEWORK, ENVIRONMENT and
INTEGRATION. Only the built-in Python Language Pack is implemented. Additional
Language Packs (Node / Java / C++), Toolchain Packs (Docker / Git), Framework
Packs (FastAPI / React / Vite), Environment Packs and Integration Packs are
**planned**, not supported implementations.

Metadata reuses `ExtensionMetadata`; `kind` is a `PackKind` member. The appended
field defaults to LANGUAGE to preserve existing Python metadata constructor
calls; non-language Packs must declare their kind explicitly. `PythonCoreExtension`
retains its class name, ID `python.core`, and thin delegation to mature Python
functions. It explicitly declares LANGUAGE because its knowledge concerns Python
project detection, interpreter requirements, environment and import failures.
There is one SDK contract version, `EXTENSION_API_VERSION = "1"`; no separate
Pack API version or manifest format is introduced.

Metadata construction rejects empty/malformed IDs, empty or whitespace-only
name/version/API version, invalid kinds and invalid capability values. IDs match
`[a-z0-9][a-z0-9._-]*` and must remain stable; dots permit domain namespaces.
There is no semantic version parsing or dependency resolution. Before any
compatibility check or stage call, `prepare_extensions` retains duplicate-ID
protection and calls `validate_pack`. Each declared capability requires its
callable implementation: detect, collect, diagnose, plan or build_verification.
A stage provider also needs a valid stable ID. Errors name the Pack ID and the
missing capability/method. These checks establish configuration consistency,
not method signature correctness, trust or isolation.

Diagnosis rules belong to their Pack. One adapter can initially group existing
rules, as Python does through the unchanged `diagnose()` function. New component
IDs should be stable and namespaced by Pack (for example `python.requires-python`).
The existing `DiagnosisRule.id` identifies a rule or grouped provider;
`DiagnosisResult.source` identifies the producing rule (`rule:...`), while
`diagnosis_id` identifies its finding and links the repair preview. Existing
Python source/finding IDs are retained; there is no duplicate rule-ID field or
JSON schema change. Rules within a Pack should have distinct IDs.

Repair planners belong to their Pack and describe actions, risk and expected
changes in `RepairPlan` / `RepairAction`. They do not apply, execute, install,
write or run shell commands. Verifiers belong to their Pack and only generate
`VerificationStep` descriptions; Core decides whether and how any future repair
or verification executor runs. Neither planning stage grants authority.

Composition stays inside the Pack using ordinary objects and tuples of existing
protocol implementations. Test-only `FakeLanguagePack` and `FakeToolchainPack`
demonstrate rule/planner/verifier composition, multiple rules, and deterministic
dispatch through the existing pipeline. They are not distributed as runtime
Packs. No PackComponents container, manager, registry, loader or parallel
runtime is needed for this boundary.

Pack A must not import or manipulate Pack B's private implementation. Cooperation
uses Core models: Evidence, DiagnosisResult, RepairPlan and VerificationStep.
Any future cross-pack correlation belongs in Core; none is implemented here.
Core still chooses Compatibility → Detect → Inspect → Core safety probe →
Diagnose → Repair Plan → Verify → Report. A future Node or FastAPI Pack can
implement the same contracts without adding a language-specific workflow;
additional probe permissions and tool facts would still require Core policy.

Pack inputs are data snapshots and captured outcomes, never a CommandExecutor,
raw subprocess object, shell, mutable environment handle or arbitrary file writer.
No network, installation, transaction, rollback or consent-bypass authority is
granted. Tests verify supplied inputs and that fake planning changes neither
project nor process environment. These in-process contracts cannot prevent
hostile Python code from importing privileged APIs itself; they are not a sandbox.

This step adds no dynamic discovery, importlib/entry-point loading, plugin folder,
registry, marketplace, installation, enable/disable, pack dependency graph,
version resolver, remote/signed manifest or sandbox process. Other real Packs,
repair/verification execution, transactions, rollback, GUI and LLM remain future
work. Workflow, CLI, JSON schema 0.2, runtime dependencies and package version
are unchanged; no Pack metadata or new CLI switches are exposed.

## Public SDK surface

The experimental v0.3 author facade is `src/agent_doctor/sdk.py`, with explicit
`__all__` re-exports of selected existing contracts and Core models. API v1 is
experimental during v0.3. See [extension-sdk.md](extension-sdk.md) for the public
names, author checks/errors, safety rules and the official synthetic example.

```text
Third-party Pack
    ↓
agent_doctor.sdk
    ↓
Explicit Pack Registry (Extension Contracts + Core Models)
    ↓
Extension Pipeline
    ↓
Core Workflow (orchestration / authority / safety)
```

SDK does not expose execution authority. It has no dependency on workflow, CLI
or executor. The example imports only the SDK and standard library, and tests
explicitly supply it to the unchanged pipeline. Core runtime does not import
the example; `BUILTIN_EXTENSIONS` still contains only PythonCoreExtension.
SDK enters the wheel; example/demo/docs enter the sdist. Dynamic third-party
entry-point discovery is now an explicit SDK API; automatic CLI discovery remains disabled.

## Explicit Pack Registration / Static Registry

`src/agent_doctor/pack_registry.py` defines the public SDK's `PackRegistry`:
register, get, read-only packs and tuple snapshot, plus a default factory.
It saves explicitly supplied SDK Pack instances in registration order, rejecting
classes even when their class attributes resemble valid declarations. Structural
validation and duplicate rejection happen before insertion; ValueError leaves
membership unchanged and identifies the invalid declaration or duplicate ID.
No Pack business stage is called during registration.

The existing `BUILTIN_EXTENSIONS` tuple is the single default source.
`PackRegistry.default()` creates independent membership from it via a fixed lazy
Core import. `prepare_extensions` captures and validates its input collection
through the registry, retaining its existing duplicate configuration error.
Snapshots preserve membership/order, not immutable Pack internals. A host may
explicitly register the Example Pack and supply its snapshot to this same pipeline;
normal workflow and CLI still receive Python only. Workflow is unchanged.

Registry saves valid declarations; pipeline handles runtime compatibility and
stage dispatch; Core owns authority, policy and orchestration. Structurally valid
but incompatible API/platform/tool declarations can register and are skipped by
pipeline. Registry itself performs no discovery. There is no global mutable
registry or caller-supplied string-based import,
disk/package scanning, persistence, installer, priorities or plugin lifecycle.
Registry exposes no executor, shell, writer, environment handle or network client.

## Controlled Pack Discovery

```text
Installed Python Distribution
    ↓
reporescue.packs Entry Point
    ↓
Controlled Discovery (explicit host call)
    ↓
PackRegistry
    ↓
Extension Pipeline
    ↓
Core (authority / orchestration / safety)
```

`src/agent_doctor/pack_discovery.py` provides the SDK's
`discover_installed_packs(registry)` and small frozen result/failure types.
It queries only `importlib.metadata.entry_points(group="reporescue.packs")`,
sorts by entry-point name, distribution name and value, loads no-argument
factories, then uses the existing registry validation/atomic insertion.
No directory search, arbitrary path/import-string API, network or installer exists.

Discovery knows installed factory metadata; registry validates/stores objects;
pipeline invokes stages; Core controls authority. Duplicate IDs are recorded,
never replaced, so installed Packs cannot overwrite `python.core`. API/platform/
tool compatibility still belongs to pipeline. Query/load/factory/validation
Exceptions become concise structured results; process-control BaseExceptions
propagate. Raw exception messages/tracebacks are not returned. These results
are not added to JSON schema 0.2.

Loading an entry point and invoking a factory execute third-party Python code.
Installed sources must be trusted: validation is not a sandbox and cannot
prevent that code's own side effects. Discovery itself does not invoke Pack
business stages or grant execution handles. SDK import does not query metadata;
default registry/workflow/CLI do not call discovery. Python remains the only
default Pack, and the official example has no installed distribution entry point.

Declared pipeline errors retain Pack/stage/status identity with fixed messages,
without copying raw exception text into failure results. Completed earlier
stages survive; a failed stage publishes no partial batch. Ordinary unexpected
stage exceptions and process-control signals still propagate to the caller.

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

## v0.4 Phase 1 / Step 2: supplied error text

CLI selects one traceback or install-log path (`-` means stdin). Workflow reads
bounded UTF-8 text before running any commands and supplies it through the existing
Core Evidence channel. The Python Pack consumes the observation, reuses the import
exception parser, and retains only supported exception/pip facts in report Evidence.
Reading text creates no CommandProposal or ExecutionResult. Reporting records
external provenance and incomplete verification; logs cannot establish current
project health. Installation facts do not infer dependency/API/ABI compatibility.
No new startup entrypoints, installed-package inspection or evidence correlation
are introduced in this step.

Recoverable ingestion failures use a distinct `ingestion_limitation` observation
instead of fabricated log facts; remaining checks continue under existing finding
priority and exit-code rules. Terminal rendering neutralizes control characters
through a single helper after formatting all report text, covering derived prose
as well as original log messages. JSON retains original facts through escaping.
