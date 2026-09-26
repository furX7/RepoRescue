# JSON contract: schema 0.2

Schema version `0.2` is independent of package version `0.3.0a1`.
Existing fields are retained; new fields are additive. Accept unknown extra fields.

| Top-level field | Meaning |
| --- | --- |
| schema_version | Contract version string |
| generated_at | ISO 8601 generation timestamp |
| tool | name (`repo-rescue`) and package version |
| status | healthy, issues_detected, or unknown |
| capabilities | Fixed tool capabilities, not per-project results |
| project | ProjectInfo snapshot |
| detection | level (likely/unknown) and matched_files |
| environment | EnvironmentInfo snapshot, including launch verification |
| diagnostics | DiagnosisResult objects |
| evidence | Referenced structured observations |

Status priority: ERROR/CRITICAL means issues_detected; otherwise unknown detection
means unknown; otherwise WARNING means issues_detected; otherwise healthy.
Healthy refers only to limited current checks. INFO safety notices alone are not
project faults. CLI exit 0 allows warnings/unknown, exit 1 denotes ERROR/CRITICAL,
and exit 2 denotes tool/input/output errors with no fabricated diagnostic report.

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
status does not establish compatibility when this check is unsupported; consult
the evidence. No field is removed and schema remains 0.2.

Project-local interpreter Evidence uses kind local_python_environment and source
python_plugin. Metadata contains:

- current_python_executable: current executable path or null.
- detected_local_environment: .venv/venv, an array for multiple candidates, or null.
- detected_interpreter_path: candidate path, an array for multiple candidates, or null.
- status: matched / different / ambiguous / none. An unavailable status records
  filesystem/normalization failures or an unknown current executable.

Only different produces a WARNING python_environment diagnosis, with its own
evidence-linked chain and LOW preview. Ambiguous/unavailable are limitation notices;
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
