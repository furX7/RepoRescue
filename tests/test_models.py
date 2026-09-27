"""Contract tests for Step 1 data structures, using only the standard library."""

import unittest
from dataclasses import FrozenInstanceError, asdict, replace
from datetime import datetime, timezone
from pathlib import Path

from agent_doctor.models import (
    CommandProposal,
    DetectionResult,
    DiagnosisResult,
    Evidence,
    ExecutionResult,
    ProjectInfo,
)


class ModelTests(unittest.TestCase):
    def setUp(self) -> None:
        self.command = CommandProposal(
            executable="python",
            arguments=("--version",),
            working_directory=Path("example-project"),
            source="python_plugin",
            reason="Inspect the Python version",
        )
        self.diagnosis = DiagnosisResult(
            problem="Required runtime unavailable",
            category="environment",
            severity="ERROR",
            confidence=0.9,
            source="rule:missing_runtime",
        )

    def test_project_snapshot_preserves_caller_supplied_metadata(self) -> None:
        timestamp = datetime(2026, 9, 26, tzinfo=timezone.utc)
        project = ProjectInfo(
            root_path=Path("example-project"),
            scanned_at=timestamp,
            files=("pyproject.toml", "app.py"),
            manifests=("pyproject.toml",),
        )
        self.assertEqual(project.scanned_at, timestamp)
        self.assertEqual(project.files, ("pyproject.toml", "app.py"))
        self.assertEqual(project.manifests, ("pyproject.toml",))

    def test_detection_result_contains_only_level_and_matched_files(self) -> None:
        for result in (
            DetectionResult("likely", ("main.py",)),
            DetectionResult("unknown", ()),
        ):
            with self.subTest(level=result.level):
                self.assertEqual(asdict(result), {
                    "level": result.level, "matched_files": result.matched_files,
                })

    def test_detection_level_rejects_unsupported_values(self) -> None:
        for level in ("confirmed", "LIKELY", "", None, 0.9):
            with self.subTest(level=level), self.assertRaises(ValueError):
                DetectionResult(level, ())

    def test_unknown_command_defaults_to_caution(self) -> None:
        self.assertEqual(self.command.risk, "CAUTION")

    def test_command_preserves_argument_boundaries(self) -> None:
        command = replace(self.command, arguments=("a b", "$(something)", "&"))
        self.assertEqual(command.arguments, ("a b", "$(something)", "&"))

    def test_command_risk_contract(self) -> None:
        for risk in ("SAFE", "CAUTION", "DANGEROUS"):
            with self.subTest(risk=risk):
                self.assertEqual(replace(self.command, risk=risk).risk, risk)
        for risk in ("UNKNOWN", "safe", "", None):
            with self.subTest(risk=risk), self.assertRaises(ValueError):
                replace(self.command, risk=risk)

    def test_process_outcome_preserves_separate_streams_and_exit_code(self) -> None:
        result = ExecutionResult(self.command, 1, "out", "error", 0.25, status="failed")
        self.assertEqual(result.stdout, "out")
        self.assertEqual(result.stderr, "error")
        self.assertEqual(result.exit_code, 1)
        self.assertFalse(result.timed_out)

    def test_capture_timeout_is_additive_and_preserves_positional_contract(self) -> None:
        legacy = ExecutionResult(self.command, 7, "out", "err", .1,
                                 False, False, False, False, "failed", "message")
        self.assertFalse(legacy.capture_timed_out)
        self.assertEqual(legacy.status, "failed")
        self.assertEqual(legacy.message, "message")
        limited = replace(legacy, capture_timed_out=True)
        self.assertEqual(limited.exit_code, 7)
        self.assertFalse(limited.timed_out)
        self.assertEqual(limited.status, "failed")

    def test_timeout_does_not_imply_confirmed_termination(self) -> None:
        result = ExecutionResult(
            self.command, None, "partial", "", 10.0,
            timed_out=True, stdout_truncated=True, status="timeout",
        )
        self.assertIsNone(result.exit_code)
        self.assertTrue(result.timed_out)
        self.assertFalse(result.terminated)
        self.assertTrue(result.stdout_truncated)
        self.assertFalse(result.stderr_truncated)

    def test_execution_status_contract(self) -> None:
        for status in ("success", "rejected", "requires_confirmation", "timeout", "failed"):
            with self.subTest(status=status):
                result = ExecutionResult(self.command, None, "", "", 0, status=status)
                self.assertEqual(result.status, status)
        with self.assertRaises(ValueError):
            ExecutionResult(self.command, None, "", "", 0, status="unknown")

    def test_duration_rejects_negative_or_nonfinite_values(self) -> None:
        for duration in (-0.1, float("nan"), float("inf"), -float("inf")):
            with self.subTest(duration=duration), self.assertRaises(ValueError):
                ExecutionResult(self.command, 0, "", "", duration)
        self.assertEqual(
            ExecutionResult(self.command, 0, "", "", 0).duration_seconds, 0,
        )

    def test_diagnosis_references_evidence_without_embedding_it(self) -> None:
        evidence = Evidence(
            evidence_id="environment:python", kind="environment",
            source="inspection", summary="Python not found",
            associated_id="command:python-version", location="PATH", masked=True,
        )
        diagnosis = replace(self.diagnosis, evidence_refs=(evidence.evidence_id,))
        self.assertEqual(diagnosis.evidence_refs, ("environment:python",))
        self.assertTrue(evidence.masked)
        self.assertEqual(evidence.associated_id, "command:python-version")

    def test_confidence_accepts_boundaries_and_rejects_invalid_values(self) -> None:
        for confidence in (0, 0.5, 1):
            with self.subTest(confidence=confidence):
                self.assertEqual(
                    replace(self.diagnosis, confidence=confidence).confidence,
                    confidence,
                )
        for confidence in (-0.1, 1.1, float("nan"), float("inf")):
            with self.subTest(confidence=confidence), self.assertRaises(ValueError):
                replace(self.diagnosis, confidence=confidence)

    def test_diagnosis_severity_contract(self) -> None:
        for severity in ("INFO", "WARNING", "ERROR", "CRITICAL"):
            with self.subTest(severity=severity):
                self.assertEqual(
                    replace(self.diagnosis, severity=severity).severity, severity,
                )
        with self.assertRaises(ValueError):
            replace(self.diagnosis, severity="UNKNOWN")

    def test_snapshots_cannot_be_reassigned(self) -> None:
        with self.assertRaises(FrozenInstanceError):
            self.command.risk = "SAFE"
        self.assertEqual(replace(self.diagnosis, problem="Another issue").evidence_refs, ())
        self.assertEqual(self.diagnosis.probable_causes, ())

    def test_diagnosis_has_plain_structured_data(self) -> None:
        data = asdict(replace(
            self.diagnosis,
            probable_causes=("Python executable absent from PATH",),
            recommended_actions=("Check your Python installation",),
        ))
        self.assertEqual(data["source"], "rule:missing_runtime")
        self.assertEqual(data["recommended_actions"], ("Check your Python installation",))
        self.assertNotIn("operations", data)


if __name__ == "__main__":
    unittest.main()
