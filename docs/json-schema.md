# JSON contract: schema 0.2

Schema version `0.2` is independent of package version `0.3.0a1`.
Existing fields are retained; new fields are additive. Accept unknown extra fields.

| Top-level field | Meaning |
| --- | --- |
| schema_version | Contract version string |
| generated_at | ISO 8601 generation timestamp |
| tool | name (`repo-rescue`) and package version |
| status | Legacy findings summary: healthy, issues_detected, or unknown; not project verification |
| capabilities | Fixed tool capabilities, not per-project results |
| project | ProjectInfo snapshot |
| detection | level (likely/unknown) and matched_files |
| environment | EnvironmentInfo snapshot, including launch verification |
| diagnostics | DiagnosisResult objects |
| evidence | Referenced structured observations |
| assessment | Additive, optional run assessment; authoritative coverage and verification semantics when present |

Status priority: ERROR/CRITICAL means issues_detected; otherwise unknown detection
means unknown; otherwise WARNING means issues_detected; otherwise healthy.
Healthy refers only to limited current checks. INFO safety notices alone are not
project faults. CLI exit 0 allows warnings/unknown, exit 1 denotes ERROR/CRITICAL,
and exit 2 denotes tool/usage/output errors with no fabricated diagnostic report.
Recoverable supplied-log ingestion failures are limitations, not exit-2 errors.

### Run assessment — status semantics

The legacy `status` algorithm and values above remain unchanged for consumers
that already use schema 0.2. It is a findings summary only: `healthy` never
establishes project health or completed checks. New consumers must consult
`assessment` when present; CLI uses this same assessment instead of presenting
the legacy label as a health conclusion. Old reports without this object do not
establish verification either.

`assessment` adds these fields:

- `outcome`: `issues_detected` for any ERROR/CRITICAL/WARNING diagnosis;
  otherwise `inconclusive` when a recorded check is incomplete, ambiguous or
  unavailable; otherwise `no_issues_detected` within the supported checks.
  Known findings take priority, while limitations remain visible separately.
- `verification`: currently always `unverified`. This tool does not execute
  project-wide verification. Neither Python `--version` nor startup exit 0
  establishes project health.
- `coverage`: `incomplete` when limitations are recorded, otherwise `limited`.
  There is no full-coverage or verified-health claim in either case.
- `startup_probe`: the observed `status` from startup Evidence, or
  `not_observed` when absent, plus its run-local `evidence_refs` array. A success
  label is accepted only when `executed` is true and `exit_code` is 0; otherwise
  assessment uses `outcome_not_confirmed`. The original Evidence is retained.
- `limitations`: objects with `check`, `reason` and `evidence_refs` arrays.
  Reasons retain the observed status, or use `not_observed`, `not_verified`
  or `outcome_not_confirmed` when a result cannot be established. References
  can be empty when no corresponding Evidence exists.
  Extension failures use `check: extension`, retain the known failure status as
  `reason`, and add `extension_id` and `stage` (null for compatibility failures).
  They have no fabricated Evidence references or raw exception text.

Incomplete checks include unknown project detection, unverified Python launch,
unrun/unsupported/unavailable/rejected/timed-out startup, ambiguous/unavailable
local interpreter evidence, and unsupported/unreadable/invalid Python requirement
metadata, and known Extension/Pack stage or compatibility failures. A startup
result without a confirmed execution and exit code also
remains incomplete. Missing `requires-python` is not invented as a restriction.
These notices do not create new failure diagnoses.

| Scenario without other findings | Legacy status | Assessment outcome | Coverage | Project verification |
| --- | --- | --- | --- | --- |
| Default scan; startup not run | healthy | inconclusive | incomplete | unverified |
| Local interpreter ambiguous | healthy | inconclusive | incomplete | unverified |
| Confirmed startup exit 0; no recorded limitations | healthy | no_issues_detected | limited | unverified |
| Startup timeout INFO | healthy | inconclusive | incomplete | unverified |
| ERROR/CRITICAL diagnosis | issues_detected | issues_detected | limited or incomplete | unverified |

Legacy unknown-detection priority is also retained: it can yield `unknown` with
a WARNING, while assessment reports the known issue and incomplete coverage.
CLI exit codes remain unchanged; exit 0 is not a project verification result.
This is an optional-field addition with unchanged existing fields, types, values
and meanings, so schema stays 0.2. Package and schema versions remain independent.

Capabilities are diagnosis, root_cause_analysis, repair_preview, verification_plan
(true), and repair_execution, verification_execution, rollback (false).

Diagnosis retains diagnosis_id, problem, category, severity, evidence_refs,
probable_causes, confidence, recommended_actions, source, root_cause_chain,
repair_plan. Confidence is a rule indicator, not statistical probability.
Probable causes are possibilities, not established facts.

- RootCauseStep: id, title, explanation, evidence_refs. Every generated step
  references an existing Evidence. The chain can be short when evidence is limited.
- RepairPlan: id, diagnosis_id, summary, risk, actions, verification_steps,
  execution_status (always `not_executed`). At least one verification step is required.
- RepairAction: id, description, affected_paths, reversible, requires_confirmation.
  These descriptive fields are not executable operations or permission grants.
- VerificationStep: id, description, type (command/file_check/manual), status
  (always `not_run`). These are future checks, not the already completed version probe.
- Risk: LOW/MEDIUM/HIGH. Existing manual-review previews use LOW. Python version
  mismatch previews use MEDIUM because selecting or manually provisioning an
  environment can affect dependencies. No environment operation is executed.
- Safety-policy notices have no repair plan (JSON null).

Evidence IDs are local to a diagnostic run. Execution references correspond to the
execution results available through WorkflowResult; no cross-run identity is promised.
Inspection evidence records the pre-launch snapshot; the final environment can
reflect a subsequently verified launch. Read captured observations accordingly.

Serialization uses only the standard library: dataclasses become objects, Paths
and Enum values become strings, datetimes use ISO 8601, tuples become arrays, and
None becomes null. Reports include local paths and captured output; generic secret
masking is not implemented. Review before sharing. README's excerpt omits fields;
the actual report contains the full serialized model.

Python requirement Evidence uses kind python_requirement, source pyproject.toml,
and optional metadata (an object, empty for previous evidence kinds):

- declared_python_requirement: original requires-python string, or null when unavailable.
- current_python_version: runtime version, preserving prerelease suffixes.
- status: compatible / incompatible / unsupported_requirement /
  unsupported_current_version / invalid_metadata / unreadable_metadata /
  metadata_too_large.

Only incompatible triggers the ERROR python_version rule. Unsupported/read/format
statuses are evidence notices, not project failure diagnoses. An overall healthy
status does not establish compatibility when this check is unsupported;
assessment records incomplete coverage and an inconclusive outcome unless a
diagnosis takes priority. Consult the evidence. Schema remains 0.2.

Project-local interpreter Evidence uses kind local_python_environment and source
python_plugin. Metadata contains:

- current_python_executable: current executable path or null.
- detected_local_environment: .venv/venv, an array for multiple candidates, or null.
- detected_interpreter_path: candidate path, an array for multiple candidates, or null.
- status: matched / different / ambiguous / none. An unavailable status records
  filesystem/normalization failures or an unknown current executable.

Only different produces a WARNING python_environment diagnosis, with its own
evidence-linked chain and LOW preview. Ambiguous/unavailable are assessment limitations;
matched/none are positive or neutral evidence. Existing Python version mismatch
diagnoses remain independent. Schema remains 0.2.

Python import failure Evidence uses kind `python_import_failure` and source
`captured_execution_output`. It is emitted only for an unsuccessful captured
execution and one of two explicit error-line forms. Shared metadata is:

- exception_type: `ModuleNotFoundError` or `ImportError`.
- source_stream: `stderr` or `stdout`; stderr takes precedence.
- raw_message: only the selected exception line, not a whole traceback.
- status: `missing_module` or `symbol_import_failure`.

`missing_module` adds only `missing_module`. `symbol_import_failure` adds only
`imported_symbol` and `source_module`. Import names are not mapped to package
distribution names. These additive metadata fields do not change schema 0.2.

Startup Evidence uses kind `startup_probe`, source `python_startup_probe`, and a
run-local ID `project:startup_probe`. Additive metadata includes:

- entrypoint, interpreter, cwd: path strings (interpreter can be null).
- argv: exact executable/argument array or null when unavailable.
- timeout_seconds: observation limit, default 5.
- execution_status: no_supported_entrypoint / unavailable / requires_confirmation /
  rejected / success / failed / timeout.
- exit_code: integer or null; duration_ms: number or null before an attempt.
- stdout_excerpt, stderr_excerpt: at most 4096 characters each.
- timed_out, terminated, executed, stdout_truncated, stderr_truncated: booleans
  where an executor outcome is available. executed records known launch, not side-effect absence.

Startup stdout/stderr each retain at most 64 KiB of bytes before UTF-8 decoding;
excess is continuously drained and discarded. Truncated flags are true when bytes
or excerpt characters were omitted, or pipe collection could not finish. They do
not change execution_status or establish a startup failure. Schema stays 0.2.

No supported entrypoint and requires_confirmation are not project errors. Nonzero
exit is an ERROR startup symptom, timeout is INFO and not verified success, and
exit 0 is positive evidence only. Startup/import/environment/version diagnoses
remain independent. Repair and verification remain unexecuted plans. Metadata
inspection is READ ONLY; confirmed startup is controlled project execution with
possible project-defined side effects. Schema stays 0.2.

## Supplied traceback and install-log evidence

`--traceback-file PATH` and `--install-log PATH` are mutually exclusive. Either
accepts `-` for stdin. Inputs must be UTF-8 (optional BOM), at most 256 KiB;
files must be regular files rather than directories, symlinks or junctions.
Missing, unreadable, oversized, undecodable, empty or unsupported inputs produce
an ingestion limitation and allow remaining project checks to continue.

The existing Evidence envelope is reused: kind `provided_log`, ID `input:log`,
source `user_file` or `stdin`, location the absolute input path or null for stdin,
and summary identifying externally supplied, unverified observations. Metadata:

- input_type: `traceback` / `install_log`.
- size_bytes: bytes read, including any BOM.
- status: `parsed` / `facts_only`.
- Install logs additionally contain `messages` and `patterns` arrays, retaining
  only recognized lines. Patterns: `no_matching_distribution`,
  `unsatisfied_requirement`, `ignored_python_versions`, `requires_python`.

Tracebacks reuse the existing last-supported-exception-line parser and import
diagnoses. Derived Evidence kind remains `python_import_failure`, but source is
`provided_traceback`, ID `input:log:python_import`, associated_id `input:log`, and
source_stream `user_file` / `stdin`. No ExecutionResult is invented. Existing
captured-execution IDs, gating, source_stream values and startup behavior remain
unchanged. Recognized imports yield ERROR symptoms / issues_detected, without
claiming that the underlying distribution or current runtime cause is known.

Install logs record literal pip messages only; Requires-Python and ignored
versions are not promoted to project constraints or compatibility conclusions.
These facts alone remain inconclusive and produce no speculative diagnosis.
Every supplied log contributes a `provided_log` assessment limitation with its
status and Evidence reference. It cannot verify the current environment, even
alongside successful startup. Existing findings retain precedence.

Unusable inputs instead produce kind `ingestion_limitation` with ID `input:log`,
source `user_file` / `stdin`, location the requested absolute path / null, and
metadata `input_type` plus `status`. Reasons are `missing_file`,
`permission_denied`, `read_error`, `not_regular_file`, `oversized_input`,
`invalid_utf8`, `empty`, or `unrecognized`. This observes a collection limitation,
not a failure in the supplied log: no `provided_log`, import-failure Evidence,
or speculative diagnosis is generated. Raw bodies and operating-system exception
messages are not retained. Assessment limitations use `check: log_ingestion`,
and include reason, source, input_type, requested_path and evidence_refs.
Without independently detected issues, outcome is `inconclusive`, coverage is
`incomplete`, and CLI exits 0. Existing ERROR/CRITICAL diagnoses still exit 1 and
retain `issues_detected`; limitations remain visible. Usage errors, invalid project
paths and internal workflow/output failures still exit 2.

Raw input bodies are discarded after parsing; selected exception/pip lines may
still contain sensitive text. Log commands are never evaluated or executed.
Terminal rendering applies one shared control-character escaping function at the
report boundary, including every derived diagnosis/preview and requested path.
Only line feeds remain active; ESC, other C0, DEL and C1 are displayed as visible
hex escapes. JSON serialization escapes controls and non-ASCII characters while
preserving their decoded values; terminal formatting never alters diagnosis facts.
Input parsing does not access the network, install packages or modify the project.
The usual version probe and explicitly confirmed startup probe remain separate.
New Evidence kinds/metadata and limitation values are additive under schema 0.2;
no existing field is removed or renamed.
