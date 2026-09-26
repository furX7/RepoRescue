# JSON contract: schema 0.2

Schema version `0.2` is independent of package version `0.2.0a1`.
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
- Risk: LOW/MEDIUM/HIGH. Current generated plans use LOW for manual review or
  selection of an existing interpreter. No installation risk is implied.
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
