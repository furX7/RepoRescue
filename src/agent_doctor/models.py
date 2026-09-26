"""Shared diagnostic data; no filesystem, process, or CLI operations.

Paths and scan timestamps are supplied by callers. Command proposals are
descriptions, not permission to execute. Output masking and path validation
belong to the later collection and execution steps.
"""

from dataclasses import dataclass, field
from datetime import datetime
from math import isfinite
from pathlib import Path
from typing import Literal


@dataclass(frozen=True)
class ProjectInfo:
    """Snapshot metadata with file paths relative to the project root."""

    root_path: Path
    scanned_at: datetime
    files: tuple[str, ...] = ()
    manifests: tuple[str, ...] = ()


@dataclass(frozen=True)
class DetectionResult:
    """Filename evidence only; unknown does not rule out an unscanned project."""

    level: Literal["likely", "unknown"]
    matched_files: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.level not in ("likely", "unknown"):
            raise ValueError("level must be likely or unknown")


@dataclass(frozen=True)
class EnvironmentInfo:
    """Current runtime metadata, not the project's selected interpreter.

    python_available means an interpreter path was reported and exists as a
    file. python_callable is None until a controlled launch is performed; file
    existence alone does not establish that a new process can start normally.
    dependency_manifests records filenames, not parsed or installed dependencies.
    """

    python_executable: Path | None
    python_version: str
    python_available: bool
    dependency_manifests: tuple[str, ...]
    python_callable: bool | None = None


@dataclass(frozen=True)
class CommandProposal:
    """An executable and separate arguments awaiting policy/approval checks."""

    executable: str
    arguments: tuple[str, ...]
    working_directory: Path
    source: str
    reason: str
    risk: Literal["SAFE", "CAUTION", "DANGEROUS"] = "CAUTION"

    def __post_init__(self) -> None:
        if self.risk not in ("SAFE", "CAUTION", "DANGEROUS"):
            raise ValueError("risk must be SAFE, CAUTION, or DANGEROUS")


@dataclass(frozen=True)
class ExecutionResult:
    """Captured process outcome; None means no exit code was available.

    terminated records confirmed termination, not merely a termination attempt.
    Output size limits and masking must be enforced by the future executor.
    """

    command: CommandProposal
    exit_code: int | None
    stdout: str
    stderr: str
    duration_seconds: float
    timed_out: bool = False
    terminated: bool = False
    stdout_truncated: bool = False
    stderr_truncated: bool = False
    status: Literal["success", "rejected", "requires_confirmation", "timeout", "failed"] = "success"
    message: str = ""

    def __post_init__(self) -> None:
        if self.status not in ("success", "rejected", "requires_confirmation", "timeout", "failed"):
            raise ValueError("Invalid execution status")
        if not isfinite(self.duration_seconds) or self.duration_seconds < 0:
            raise ValueError("duration_seconds must be finite and non-negative")


@dataclass(frozen=True)
class Evidence:
    """A referenced observation; kind/source identify its structured origin.

    summary is explanatory text, not a substitute for the associated object's
    structured facts. masked indicates redaction, not a guarantee of secrecy.
    """

    evidence_id: str
    kind: str
    source: str
    summary: str
    associated_id: str | None = None
    location: str | None = None
    masked: bool = False
    metadata: dict[str, str | int | float | bool | tuple[str, ...] | None] = field(default_factory=dict)


@dataclass(frozen=True)
class RootCauseStep:
    """An evidence-supported observation/consequence, not an inferred hidden cause."""

    id: str
    title: str
    explanation: str
    evidence_refs: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.evidence_refs:
            raise ValueError("Root cause steps must reference evidence")


@dataclass(frozen=True)
class RepairAction:
    """Descriptive preview only; affected_paths never authorizes a file write."""

    id: str
    description: str
    affected_paths: tuple[str, ...]
    reversible: bool
    requires_confirmation: bool


@dataclass(frozen=True)
class VerificationStep:
    """A proposed check, never an executable command or automatic operation."""

    id: str
    description: str
    type: Literal["command", "file_check", "manual"]
    status: Literal["not_run"] = "not_run"

    def __post_init__(self) -> None:
        if self.type not in ("command", "file_check", "manual"):
            raise ValueError("Verification type must be command, file_check, or manual")
        if self.status != "not_run":
            raise ValueError("Verification plans must remain not_run")


@dataclass(frozen=True)
class RepairPlan:
    """Read-only preview with explicit verification; no execution capability."""

    id: str
    diagnosis_id: str
    summary: str
    risk: Literal["LOW", "MEDIUM", "HIGH"]
    actions: tuple[RepairAction, ...]
    verification_steps: tuple[VerificationStep, ...]
    execution_status: Literal["not_executed"] = "not_executed"

    def __post_init__(self) -> None:
        if self.risk not in ("LOW", "MEDIUM", "HIGH"):
            raise ValueError("Repair risk must be LOW, MEDIUM, or HIGH")
        if not self.verification_steps:
            raise ValueError("Repair previews must include a verification step")
        if self.execution_status != "not_executed":
            raise ValueError("Repair previews must remain not_executed")


@dataclass(frozen=True)
class DiagnosisResult:
    """Evidence-backed diagnosis with optional descriptive repair preview."""

    problem: str
    category: str
    severity: Literal["INFO", "WARNING", "ERROR", "CRITICAL"]
    confidence: float
    source: str
    evidence_refs: tuple[str, ...] = ()
    probable_causes: tuple[str, ...] = ()
    recommended_actions: tuple[str, ...] = ()
    diagnosis_id: str = ""
    root_cause_chain: tuple[RootCauseStep, ...] = ()
    repair_plan: RepairPlan | None = None

    def __post_init__(self) -> None:
        if self.severity not in ("INFO", "WARNING", "ERROR", "CRITICAL"):
            raise ValueError("severity must be INFO, WARNING, ERROR, or CRITICAL")
        if not isfinite(self.confidence) or not 0 <= self.confidence <= 1:
            raise ValueError("confidence must be finite and between 0 and 1")
