"""Thin contract facade for the built-in Python pack; not a new workflow."""

from collections.abc import Sequence

from .diagnosis import diagnose
from .extensions import Capability, EXTENSION_API_VERSION, ExtensionMetadata
from .models import (
    CommandProposal, DetectionResult, DiagnosisResult, EnvironmentInfo, Evidence, ExecutionResult,
    ProjectInfo, RepairPlan, VerificationStep,
)
from .python_plugin import (
    detect_python_project, inspect_local_python_environment,
    inspect_python_requirement, propose_diagnostic_commands,
)


class PythonCoreExtension:
    """Express existing Python knowledge without copying or moving rules.

    Collection reuses the bounded read-only metadata inspectors. Diagnosis
    delegates to the existing combined diagnosis/evidence function. Startup
    observations and captured executions must be supplied by Core. Version probe
    proposals delegate to the existing function; no probe is executed here.
    Plans/verification are existing previews.
    """

    @property
    def metadata(self) -> ExtensionMetadata:
        return ExtensionMetadata(
            id="python.core", name="Python Core Pack", version="0.1",
            api_version=EXTENSION_API_VERSION,
            capabilities=frozenset(Capability), supported_platforms=("windows",),
            required_tools=("python",),
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
        # These are the three existing optional observation inputs. General
        # evidence processing remains each future pack's responsibility.
        def observation(kind: str) -> Evidence | None:
            return next((item for item in evidence if item.kind == kind), None)

        return diagnose(
            project, detection, environment, executions,
            python_requirement=observation("python_requirement"),
            local_environment=observation("local_python_environment"),
            startup_probe=observation("startup_probe"),
        )

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
