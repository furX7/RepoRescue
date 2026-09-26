"""Internal extension contracts; no loading, execution, or report changes.

Core owns ordering, policy, consent, timeouts, resource limits and the JSON
contract. Extensions provide knowledge and descriptive plans, never authority
to execute commands or mutate projects. These types are not a Python sandbox.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum
from typing import Protocol, runtime_checkable

from .models import (
    DetectionResult, DiagnosisResult, EnvironmentInfo, Evidence, ExecutionResult,
    ProjectInfo, RepairPlan, VerificationStep,
)


EXTENSION_API_VERSION = "1"


class Capability(Enum):
    DETECT = "detect"
    INSPECT = "inspect"
    DIAGNOSE = "diagnose"
    PLAN_REPAIR = "plan_repair"
    VERIFY = "verify"


@dataclass(frozen=True)
class ExtensionMetadata:
    """Python-native declaration, separate from package and JSON versions.

    Platform and tool identifiers are exact, canonical names (e.g. windows,
    python). Tool requirements express availability only, not version ranges.
    """

    id: str
    name: str
    version: str
    api_version: str
    capabilities: frozenset[Capability]
    supported_platforms: tuple[str, ...]
    required_tools: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.capabilities, frozenset) or any(
            not isinstance(item, Capability) for item in self.capabilities
        ):
            raise TypeError("capabilities must be a frozenset of Capability members")


@dataclass(frozen=True)
class ExtensionEnvironment:
    """Core-supplied compatibility facts; no tool discovery or live handles."""

    platform: str
    available_tools: frozenset[str]
    api_version: str = EXTENSION_API_VERSION


class CompatibilityStatus(Enum):
    COMPATIBLE = "compatible"
    API_VERSION_MISMATCH = "api_version_mismatch"
    UNSUPPORTED_PLATFORM = "unsupported_platform"
    MISSING_REQUIRED_TOOL = "missing_required_tool"


@dataclass(frozen=True)
class CompatibilityResult:
    status: CompatibilityStatus
    missing_tools: tuple[str, ...] = ()

    @property
    def compatible(self) -> bool:
        return self.status is CompatibilityStatus.COMPATIBLE


def check_compatibility(
    metadata: ExtensionMetadata, environment: ExtensionEnvironment,
) -> CompatibilityResult:
    """Compare supplied facts only; first failure wins: API, platform, tools.

    Compatibility is eligibility, not approval, trust, or execution authority.
    An empty platform declaration supports no platforms.
    """
    if metadata.api_version != environment.api_version:
        return CompatibilityResult(CompatibilityStatus.API_VERSION_MISMATCH)
    if environment.platform not in metadata.supported_platforms:
        return CompatibilityResult(CompatibilityStatus.UNSUPPORTED_PLATFORM)
    missing = tuple(tool for tool in metadata.required_tools
                    if tool not in environment.available_tools)
    if missing:
        return CompatibilityResult(CompatibilityStatus.MISSING_REQUIRED_TOOL, missing)
    return CompatibilityResult(CompatibilityStatus.COMPATIBLE)


class ExtensionError(RuntimeError):
    """Extension-local outcome, not a WorkflowError or project diagnosis.

    Future Core invocation boundaries must isolate these errors per extension
    and stage and translate unexpected exceptions into ExtensionFailure. No
    invocation/isolation runtime is installed by this contract extraction.
    """

    def __init__(self, extension_id: str, message: str) -> None:
        self.extension_id = extension_id
        super().__init__(message)


class ExtensionUnavailable(ExtensionError):
    """Required tool or observation is unavailable; skip the affected stage."""


class ExtensionIncompatible(ExtensionError):
    """API or platform is unsupported; skip the affected extension."""


class ExtensionFailure(ExtensionError):
    """Extension stage failed; preserve other extensions' results."""


@runtime_checkable
class Extension(Protocol):
    @property
    def metadata(self) -> ExtensionMetadata: ...


@runtime_checkable
class Detector(Protocol):
    @property
    def id(self) -> str: ...

    def detect(self, project: ProjectInfo) -> DetectionResult: ...


@runtime_checkable
class EvidenceProvider(Protocol):
    @property
    def id(self) -> str: ...

    def collect(
        self, project: ProjectInfo, environment: EnvironmentInfo,
    ) -> Sequence[Evidence]: ...


@runtime_checkable
class DiagnosisRule(Protocol):
    @property
    def id(self) -> str: ...

    def diagnose(
        self, project: ProjectInfo, detection: DetectionResult,
        environment: EnvironmentInfo, evidence: Sequence[Evidence],
        executions: Sequence[ExecutionResult] = (),
    ) -> tuple[Sequence[DiagnosisResult], Sequence[Evidence]]:
        """Return findings and their evidence, including derived observations.

        executions contains already captured Core-controlled outcomes, never
        an executor. Do not mutate supplied snapshots or Evidence.metadata.
        """
        ...


@runtime_checkable
class RepairPlanner(Protocol):
    @property
    def id(self) -> str: ...

    def plan(
        self, project: ProjectInfo, diagnosis: DiagnosisResult,
        evidence: Sequence[Evidence],
    ) -> RepairPlan | None: ...


@runtime_checkable
class Verifier(Protocol):
    @property
    def id(self) -> str: ...

    def build_verification(
        self, project: ProjectInfo, diagnosis: DiagnosisResult,
        repair_plan: RepairPlan | None,
    ) -> Sequence[VerificationStep]: ...
