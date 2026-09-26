"""Reporting tests cover representation, serialization, and safe file creation."""

import json
import tempfile
import unittest
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path

from agent_doctor.models import (
    DetectionResult, DiagnosisResult, EnvironmentInfo, Evidence, ProjectInfo,
)
from agent_doctor.report import (
    ReportWriteError,
    _json_value,
    build_json_report,
    render_terminal_report,
    write_json_report,
)


class SampleSeverity(Enum):
    WARNING = "WARNING"


@dataclass(frozen=True)
class SerializationSample:
    severity: SampleSeverity
    path: Path
    generated_at: datetime
    values: tuple[str | None, ...]


class ReportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.timestamp = datetime(2026, 9, 26, 12, 30, tzinfo=timezone.utc)
        self.project = ProjectInfo(
            root_path=self.root, scanned_at=self.timestamp,
            files=("main.py",), manifests=(),
        )
        self.environment = EnvironmentInfo(
            python_executable=Path("C:/Python/python.exe"),
            python_version="3.13.1", python_available=True,
            dependency_manifests=(), python_callable=True,
        )
        self.detection = DetectionResult("likely", ("main.py",))
        self.diagnostics = (
            DiagnosisResult(
                problem="Python probe failed", category="environment", severity="ERROR",
                confidence=0.9, source="rule:probe", evidence_refs=("execution:0",),
                recommended_actions=("Check Python",),
            ),
            DiagnosisResult(
                problem="Scan was limited", category="project_detection", severity="WARNING",
                confidence=0.5, source="rule:scan", evidence_refs=("detection:python",),
            ),
            DiagnosisResult(
                problem="Command was blocked", category="tool/safety", severity="INFO",
                confidence=0.9, source="rule:policy", evidence_refs=("execution:1",),
            ),
        )
        self.evidence = (
            Evidence("execution:0", "execution", "executor", "exit 1", "execution:0"),
            Evidence("detection:python", "detection", "python_plugin", "likely", masked=True),
        )

    def test_terminal_report_shows_severity_category_problem_actions_and_refs(self) -> None:
        text = render_terminal_report(
            self.project, self.detection, self.environment, self.diagnostics,
        )
        for value in (
            "[ERROR] environment: Python probe failed",
            "[WARNING] project_detection: Scan was limited",
            "[INFO] tool/safety: Command was blocked",
            "Recommended actions:", "Check Python", "Evidence: execution:0",
            "Detection: likely", "launch verified",
            "RepoRescue currently performs limited checks.",
        ):
            with self.subTest(value=value):
                self.assertIn(value, text)
        self.assertNotIn("exit 1", text)

    def test_empty_terminal_report_states_limited_scope(self) -> None:
        text = render_terminal_report(
            self.project, self.detection, self.environment, (),
        )
        self.assertIn("No problems detected by the current checks.", text)
        self.assertIn("RepoRescue currently performs limited checks.", text)
        self.assertNotIn("healthy", text.lower())

    def test_unknown_detection_is_rendered_as_unknown(self) -> None:
        text = render_terminal_report(
            self.project, DetectionResult("unknown", ()), self.environment, (),
        )
        self.assertIn("Detection: unknown", text)
        self.assertNotIn("Confirmed", text)

    def test_json_report_loads_and_has_stable_top_level_schema(self) -> None:
        report = build_json_report(
            self.project, self.detection, self.environment,
            self.diagnostics, self.evidence, generated_at=self.timestamp,
        )
        encoded = json.dumps(report, ensure_ascii=False)
        loaded = json.loads(encoded)
        self.assertEqual(tuple(loaded), (
            "schema_version", "generated_at", "tool", "status", "capabilities", "project", "detection",
            "environment", "diagnostics", "evidence",
        ))
        self.assertEqual(loaded["schema_version"], "0.2")
        self.assertEqual(loaded["generated_at"], self.timestamp.isoformat())

    def test_path_datetime_tuples_none_and_enum_serialize(self) -> None:
        sample = SerializationSample(
            SampleSeverity.WARNING, self.root, self.timestamp, ("一", None),
        )
        converted = _json_value(sample)
        self.assertEqual(converted["severity"], "WARNING")
        self.assertEqual(converted["path"], str(self.root))
        self.assertEqual(converted["generated_at"], self.timestamp.isoformat())
        self.assertEqual(converted["values"], ["一", None])
        self.assertIsNone(converted["values"][1])

    def test_none_environment_fields_remain_null(self) -> None:
        environment = EnvironmentInfo(None, "", False, (), python_callable=None)
        report = build_json_report(
            self.project, DetectionResult("unknown", ()), environment, (), (),
            generated_at=self.timestamp,
        )
        self.assertIsNone(report["environment"]["python_executable"])
        self.assertIsNone(report["environment"]["python_callable"])

    def test_evidence_and_diagnosis_links_are_preserved(self) -> None:
        report = build_json_report(
            self.project, self.detection, self.environment,
            self.diagnostics, self.evidence, generated_at=self.timestamp,
        )
        self.assertEqual(report["diagnostics"][0]["evidence_refs"], ["execution:0"])
        self.assertEqual(report["evidence"][0]["evidence_id"], "execution:0")
        self.assertEqual(report["evidence"][0]["associated_id"], "execution:0")
        self.assertTrue(report["evidence"][1]["masked"])

    def test_json_writer_creates_requested_path_as_utf8(self) -> None:
        report = build_json_report(
            self.project, self.detection, self.environment, self.diagnostics,
            self.evidence, generated_at=self.timestamp,
        )
        path = self.root / "中文报告.json"
        self.assertEqual(write_json_report(report, path), path)
        raw = path.read_bytes()
        self.assertIn("Python probe failed".encode("utf-8"), raw)
        self.assertEqual(json.loads(raw.decode("utf-8")), report)

    def test_no_path_is_a_noop(self) -> None:
        self.assertIsNone(write_json_report({"ignored": object()}, None))
        self.assertEqual(list(self.root.iterdir()), [])

    def test_existing_file_is_never_overwritten(self) -> None:
        path = self.root / "report.json"
        path.write_text("existing", encoding="utf-8")
        with self.assertRaisesRegex(ReportWriteError, "refusing to overwrite"):
            write_json_report({"new": True}, path)
        self.assertEqual(path.read_text(encoding="utf-8"), "existing")

    def test_invalid_output_path_has_clear_error(self) -> None:
        with self.assertRaisesRegex(ReportWriteError, "Could not write report"):
            write_json_report({"ok": True}, self.root / "missing" / "report.json")
        directory = self.root / "output-directory"
        directory.mkdir()
        with self.assertRaisesRegex(ReportWriteError, "Could not write report"):
            write_json_report({"ok": True}, directory)

    def test_unsupported_json_value_has_serialization_error(self) -> None:
        with self.assertRaisesRegex(ReportWriteError, "Could not serialize"):
            write_json_report({"bad": object()}, self.root / "bad.json")
        self.assertFalse((self.root / "bad.json").exists())

    def test_rendering_and_serialization_do_not_mutate_inputs(self) -> None:
        diagnostics = list(self.diagnostics)
        evidence = list(self.evidence)
        before_diagnostics, before_evidence = diagnostics.copy(), evidence.copy()
        render_terminal_report(self.project, self.detection, self.environment, diagnostics)
        build_json_report(
            self.project, self.detection, self.environment, diagnostics, evidence,
            generated_at=self.timestamp,
        )
        self.assertEqual(diagnostics, before_diagnostics)
        self.assertEqual(evidence, before_evidence)


if __name__ == "__main__":
    unittest.main()
