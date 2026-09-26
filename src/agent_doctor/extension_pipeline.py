"""Static built-in stage dispatch; Core supplies data and controls the order.

No discovery, executor, filesystem access or workflow lives here. Each extension
owns its evidence batch and diagnoses; completed batches are appended in static
extension order. Only declared extension errors are isolated.
"""

from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import Literal, Protocol, cast, runtime_checkable

from .extensions import (
    Capability, CompatibilityStatus, Detector, DiagnosisRule, EvidenceProvider,
    Extension, ExtensionEnvironment, ExtensionFailure, ExtensionIncompatible,
    ExtensionUnavailable, RepairPlanner, Verifier, check_compatibility,
)
from .models import (
    CommandProposal, DetectionResult, DiagnosisResult, EnvironmentInfo, Evidence, ExecutionResult,
    ProjectInfo,
)
from .python_extension import PythonCoreExtension


BUILTIN_EXTENSIONS: tuple[Extension, ...] = (PythonCoreExtension(),)


@runtime_checkable
class _DiagnosticCommandProvider(Protocol):
    """Optional built-in inspection adapter, not an execution capability."""

    def propose_diagnostic_commands(
        self, project: ProjectInfo, environment: EnvironmentInfo,
    ) -> Sequence[CommandProposal]: ...


@dataclass(frozen=True)
class ExtensionFailureInfo:
    extension_id: str
    stage: Capability | None
    status: CompatibilityStatus | Literal["unavailable", "incompatible", "failure"]
    message: str
    missing_tools: tuple[str, ...] = ()


@dataclass(frozen=True)
class ExtensionRun:
    extension: Extension
    detection: DetectionResult = DetectionResult("unknown", ())
    evidence: tuple[Evidence, ...] = ()
    diagnoses: tuple[DiagnosisResult, ...] = ()
    failures: tuple[ExtensionFailureInfo, ...] = ()
    command_proposals: tuple[CommandProposal, ...] = ()


@dataclass(frozen=True)
class ExtensionRunResult:
    evidence: tuple[Evidence, ...]
    diagnoses: tuple[DiagnosisResult, ...]
    failures: tuple[ExtensionFailureInfo, ...]
    command_proposals: tuple[CommandProposal, ...]


def prepare_extensions(
    environment: ExtensionEnvironment, extensions: Sequence[Extension] | None = None,
) -> tuple[ExtensionRun, ...]:
    """Validate static IDs and check compatibility before any stage call."""
    extensions = BUILTIN_EXTENSIONS if extensions is None else extensions
    identifiers = [extension.metadata.id for extension in extensions]
    if len(set(identifiers)) != len(identifiers):
        raise ValueError("Duplicate built-in extension id")

    runs = []
    for extension in extensions:
        result = check_compatibility(extension.metadata, environment)
        failures = () if result.compatible else (ExtensionFailureInfo(
            extension.metadata.id, None, result.status,
            f"Built-in extension compatibility: {result.status.value}", result.missing_tools,
        ),)
        runs.append(ExtensionRun(extension, failures=failures))
    return tuple(runs)


def run_extension_stage(
    runs: Sequence[ExtensionRun], stage: Capability, project: ProjectInfo,
    environment: EnvironmentInfo, *, executions: Sequence[ExecutionResult] = (),
    core_evidence: Sequence[Evidence] = (),
) -> tuple[ExtensionRun, ...]:
    """Run one Core-selected stage sequentially, skipping failed extensions.

    A stage commits its batch only after all its calls complete. On a declared
    error, keep prior completed batches, record the failure and skip later stages
    for that extension. Unexpected exceptions and process-control signals escape.
    """
    results = []
    for run in runs:
        if run.failures or stage not in run.extension.metadata.capabilities:
            results.append(run)
            continue
        try:
            result = _invoke_stage(run, stage, project, environment, executions, core_evidence)
        except (ExtensionUnavailable, ExtensionIncompatible, ExtensionFailure) as error:
            status: Literal["unavailable", "incompatible", "failure"] = "failure"
            if isinstance(error, ExtensionUnavailable):
                status = "unavailable"
            elif isinstance(error, ExtensionIncompatible):
                status = "incompatible"
            failure = ExtensionFailureInfo(run.extension.metadata.id, stage, status, str(error))
            result = replace(run, failures=(*run.failures, failure))
        results.append(result)
    return tuple(results)


def _invoke_stage(
    run: ExtensionRun, stage: Capability, project: ProjectInfo,
    environment: EnvironmentInfo, executions: Sequence[ExecutionResult],
    core_evidence: Sequence[Evidence],
) -> ExtensionRun:
    extension = run.extension
    if stage is Capability.DETECT:
        return replace(run, detection=cast(Detector, extension).detect(project))
    if stage is Capability.INSPECT:
        evidence = tuple(cast(EvidenceProvider, extension).collect(project, environment))
        proposals = tuple(extension.propose_diagnostic_commands(project, environment)) \
            if isinstance(extension, _DiagnosticCommandProvider) else ()
        return replace(run, evidence=evidence, command_proposals=proposals)
    if stage is Capability.DIAGNOSE:
        diagnoses, evidence = cast(DiagnosisRule, extension).diagnose(
            project, run.detection, environment, (*run.evidence, *core_evidence), executions,
        )
        # The existing dual-return contract supplies the complete evidence batch
        # for this extension, not a delta to concatenate with its collection.
        return replace(run, diagnoses=tuple(diagnoses), evidence=tuple(evidence))

    diagnoses = []
    for diagnosis in run.diagnoses:
        if stage is Capability.PLAN_REPAIR:
            plan = cast(RepairPlanner, extension).plan(project, diagnosis, run.evidence)
            if plan is not None and plan.diagnosis_id != diagnosis.diagnosis_id:
                raise ExtensionFailure(extension.metadata.id, "Repair preview refers to another diagnosis")
            diagnosis = replace(diagnosis, repair_plan=plan)
        elif stage is Capability.VERIFY:
            plan = diagnosis.repair_plan
            if plan is not None and plan.diagnosis_id != diagnosis.diagnosis_id:
                raise ExtensionFailure(extension.metadata.id, "Repair preview refers to another diagnosis")
            steps = tuple(cast(Verifier, extension).build_verification(project, diagnosis, plan))
            if plan is None and steps:
                raise ExtensionFailure(extension.metadata.id, "Verification steps require a repair preview")
            if plan is not None:
                if not steps:
                    raise ExtensionFailure(extension.metadata.id, "Repair preview requires verification steps")
                diagnosis = replace(diagnosis, repair_plan=replace(plan, verification_steps=steps))
        diagnoses.append(diagnosis)
    return replace(run, diagnoses=tuple(diagnoses))


def merge_detection(runs: Sequence[ExtensionRun]) -> DetectionResult:
    """Append filename matches; do not rank extensions or infer correlations."""
    return DetectionResult(
        "likely" if any(run.detection.level == "likely" for run in runs) else "unknown",
        tuple(name for run in runs for name in run.detection.matched_files),
    )


def merge_results(runs: Sequence[ExtensionRun]) -> ExtensionRunResult:
    return ExtensionRunResult(
        tuple(item for run in runs for item in run.evidence),
        tuple(item for run in runs for item in run.diagnoses),
        tuple(item for run in runs for item in run.failures),
        tuple(item for run in runs for item in run.command_proposals),
    )
