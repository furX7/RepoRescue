"""SDK example only: a synthetic marker Pack, never a default built-in.

Reads only the existence of one root marker file. All repair and verification
outputs are descriptions. No commands, network, writes or execution are used.
"""

from collections.abc import Sequence

from agent_doctor.sdk import (
    EXTENSION_API_VERSION, Capability, PackKind, ExtensionMetadata,
    ProjectInfo, DetectionResult, EnvironmentInfo, Evidence, DiagnosisResult,
    ExecutionResult, RepairPlan, RepairAction, VerificationStep,
)


class ExampleLanguagePack:
    """Implements all five contracts using SDK types and standard Python only."""

    MARKER = ".reporescue-example"
    RULE_ID = "example.marker-detected"
    EVIDENCE_ID = "example.language:marker"

    @property
    def metadata(self) -> ExtensionMetadata:
        return ExtensionMetadata(
            id="example.language", name="Example Language Pack", version="0.1",
            api_version=EXTENSION_API_VERSION, kind=PackKind.LANGUAGE,
            capabilities=frozenset(Capability), supported_platforms=("windows",), required_tools=(),
        )

    @property
    def id(self) -> str:
        return self.metadata.id

    def detect(self, project: ProjectInfo) -> DetectionResult:
        present = (project.root_path / self.MARKER).is_file()
        return DetectionResult("likely" if present else "unknown", (self.MARKER,) if present else ())

    def collect(self, project: ProjectInfo, environment: EnvironmentInfo) -> tuple[Evidence, ...]:
        if self.detect(project).level != "likely":
            return ()
        return (Evidence(
            evidence_id=self.EVIDENCE_ID, kind="example_marker", source=self.id,
            summary="SDK example marker detected.", location=self.MARKER,
            metadata={"marker": self.MARKER},
        ),)

    def diagnose(
        self, project: ProjectInfo, detection: DetectionResult,
        environment: EnvironmentInfo, evidence: Sequence[Evidence],
        executions: Sequence[ExecutionResult] = (),
    ) -> tuple[tuple[DiagnosisResult, ...], tuple[Evidence, ...]]:
        batch = tuple(evidence)
        marker = next((item for item in batch if item.evidence_id == self.EVIDENCE_ID
                       and item.kind == "example_marker" and item.source == self.id), None)
        if detection.level != "likely" or marker is None:
            return (), batch
        return (DiagnosisResult(
            problem="SDK example marker is present; this is a demonstration, not a project failure.",
            category="example", severity="INFO", confidence=1.0,
            source="rule:" + self.RULE_ID, diagnosis_id=self.RULE_ID,
            evidence_refs=(marker.evidence_id,),
            recommended_actions=("Review the marker's purpose manually.",),
        ),), batch

    def plan(
        self, project: ProjectInfo, diagnosis: DiagnosisResult, evidence: Sequence[Evidence],
    ) -> RepairPlan | None:
        if diagnosis.diagnosis_id != self.RULE_ID or diagnosis.source != "rule:" + self.RULE_ID:
            return None
        return RepairPlan(
            id=self.RULE_ID + ":review", diagnosis_id=diagnosis.diagnosis_id,
            summary="Manually review the SDK example marker.", risk="LOW",
            actions=(RepairAction(
                id=self.RULE_ID + ":inspect", description="Inspect the marker and its documented purpose.",
                affected_paths=(self.MARKER,), reversible=True, requires_confirmation=True,
            ),),
            verification_steps=(VerificationStep(
                id=self.RULE_ID + ":verify", description="Manually confirm the marker is intentional.",
                type="manual",
            ),),
        )

    def build_verification(
        self, project: ProjectInfo, diagnosis: DiagnosisResult, repair_plan: RepairPlan | None,
    ) -> tuple[VerificationStep, ...]:
        return () if repair_plan is None else repair_plan.verification_steps
