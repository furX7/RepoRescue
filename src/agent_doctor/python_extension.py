"""Thin contract facade for the built-in Python Language Pack."""

from collections.abc import Sequence

from .diagnosis import diagnose
from .python_evidence import interpreter_evidence, collect_import_evidence
from .extensions import Capability, EXTENSION_API_VERSION, ExtensionMetadata, PackKind
from .models import (
    CommandProposal, DetectionResult, DiagnosisResult, EnvironmentInfo, Evidence, ExecutionResult,
    ProjectInfo, RepairPlan, VerificationStep,
)
from .python_plugin import (
    detect_python_project, inspect_local_python_environment,
    inspect_python_requirement, propose_diagnostic_commands,
)


class PythonCoreExtension:
    """Built-in Language Pack grouping Python knowledge through one adapter.

    Collection reuses the bounded read-only metadata inspectors. Diagnosis
    delegates to the existing combined diagnosis/evidence function. Startup
    observations and captured executions must be supplied by Core. Version probe
    proposals delegate to the existing function; no probe is executed here.
    Rules, repair planning and verification planning belong to this Pack.
    Plans/verification are existing previews; execution authority stays in Core.
    """

    @property
    def metadata(self) -> ExtensionMetadata:
        return ExtensionMetadata(
            id="python.core", name="Python Core Pack", version="0.1",
            api_version=EXTENSION_API_VERSION,
            capabilities=frozenset(Capability), supported_platforms=("windows",),
            required_tools=("python",), kind=PackKind.LANGUAGE,
        )

    @property
    def id(self) -> str:
        return self.metadata.id

    def detect(self, project: ProjectInfo) -> DetectionResult:
        return detect_python_project(project)

    def collect(
        self, project: ProjectInfo, environment: EnvironmentInfo,
    ) -> tuple[Evidence, ...]:
        local = inspect_local_python_environment(project, environment)
        requirement = inspect_python_requirement(project, environment)
        return (local,) if requirement is None else (local, requirement)

    def diagnose(
        self, project: ProjectInfo, detection: DetectionResult,
        environment: EnvironmentInfo, evidence: Sequence[Evidence],
        executions: Sequence[ExecutionResult] = (),
    ) -> tuple[Sequence[DiagnosisResult], Sequence[Evidence]]:
        # Python-specific rules consume supplied observations as data only.
        def observation(kind: str) -> Evidence | None:
            return next((item for item in evidence if item.kind == kind), None)

        diagnoses, observations = diagnose(
            project, detection, environment, executions,
            python_requirement=observation("python_requirement"),
            local_environment=observation("local_python_environment"),
            startup_probe=observation("startup_probe"),
            provided_logs=tuple(item for item in evidence if item.kind in ("provided_log", "ingestion_limitation")),
        )
        observations.append(interpreter_evidence())
        observations.extend(collect_import_evidence(observations))
        return diagnoses, observations

    def propose_diagnostic_commands(
        self, project: ProjectInfo, environment: EnvironmentInfo,
    ) -> tuple[CommandProposal, ...]:
        return propose_diagnostic_commands(project, environment)

    def plan(
        self, project: ProjectInfo, diagnosis: DiagnosisResult,
        evidence: Sequence[Evidence],
    ) -> RepairPlan | None:
        return diagnosis.repair_plan

    def build_verification(
        self, project: ProjectInfo, diagnosis: DiagnosisResult,
        repair_plan: RepairPlan | None,
    ) -> tuple[VerificationStep, ...]:
        return () if repair_plan is None else repair_plan.verification_steps
