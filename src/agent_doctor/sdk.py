"""v0.3 foundation public extension surface; API v1 is experimental.

Re-exported contracts and data retain their Core identities. Core owns policy,
consent and execution; this module grants no execution authority or sandbox.
See docs/extension-sdk.md for author guidance and current limitations.
"""

from .extensions import (
    EXTENSION_API_VERSION, Capability, PackKind, ExtensionMetadata,
    Extension, Detector, EvidenceProvider, DiagnosisRule, RepairPlanner, Verifier,
    ExtensionUnavailable, ExtensionIncompatible, ExtensionFailure,
    validate_pack, validate_pack_id,
)
from .models import (
    ProjectInfo, DetectionResult, EnvironmentInfo, Evidence, DiagnosisResult,
    RootCauseStep, RepairPlan, RepairAction, VerificationStep, ExecutionResult, CommandProposal,
)


__all__ = (
    "EXTENSION_API_VERSION", "Capability", "PackKind", "ExtensionMetadata",
    "Extension", "Detector", "EvidenceProvider", "DiagnosisRule", "RepairPlanner", "Verifier",
    "ProjectInfo", "DetectionResult", "EnvironmentInfo", "Evidence", "DiagnosisResult",
    "RootCauseStep", "RepairPlan", "RepairAction", "VerificationStep", "ExecutionResult", "CommandProposal",
    "ExtensionUnavailable", "ExtensionIncompatible", "ExtensionFailure", "validate_pack", "validate_pack_id",
)
