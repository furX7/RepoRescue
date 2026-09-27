"""Run-level assessment must not turn a limited probe into project health."""

import io
import json
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from agent_doctor.cli import main
from agent_doctor.extension_pipeline import ExtensionFailureInfo
from agent_doctor.extensions import Capability, ExtensionFailure, ExtensionUnavailable
from agent_doctor.models import DetectionResult, DiagnosisResult, Evidence
from agent_doctor.python_extension import PythonCoreExtension
from agent_doctor.report import build_json_report, render_terminal_report
from agent_doctor.workflow import run_workflow
from tests.test_extension_pipeline import RecordingExtension


class StatusSemanticsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        (self.root / "main.py").write_text("print('probe only')\n", encoding="utf-8")

    def startup(self, code=0, stderr=""):
        with patch("agent_doctor.commands._run_startup_process",
                   return_value=subprocess.CompletedProcess([], code, "", stderr)):
            return run_workflow(self.root, confirm_startup=True)

    def cli(self, *arguments):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = main([str(self.root), *arguments])
        return code, out.getvalue(), err.getvalue()

    def assert_unverified(self, report):
        self.assertEqual(report["assessment"]["verification"], "unverified")

    def test_default_no_findings_is_incomplete_and_unverified(self):
        with patch("agent_doctor.commands._run_startup_process") as runner:
            result = run_workflow(self.root)
        runner.assert_not_called()
        self.assertEqual(result.diagnostics, ())
        self.assertEqual(result.report["status"], "healthy")
        assessment = result.report["assessment"]
        self.assertEqual(assessment["outcome"], "inconclusive")
        self.assertEqual(assessment["coverage"], "incomplete")
        self.assertEqual(assessment["startup_probe"]["status"], "requires_confirmation")
        self.assert_unverified(result.report)

    def test_missing_entrypoint_is_limitation_not_failure(self):
        (self.root / "main.py").rename(self.root / "app.py")
        with patch("agent_doctor.commands._run_startup_process") as runner:
            result = run_workflow(self.root, confirm_startup=True)
        runner.assert_not_called()
        self.assertEqual(result.diagnostics, ())
        self.assertEqual(result.report["assessment"]["outcome"], "inconclusive")
        self.assertEqual(result.report["assessment"]["startup_probe"]["status"], "no_supported_entrypoint")

    def test_ambiguous_interpreter_survives_successful_probe(self):
        for name in (".venv", "venv"):
            candidate = self.root / name / "Scripts" / "python.exe"
            candidate.parent.mkdir(parents=True)
            candidate.write_bytes(b"not executed")
        result = self.startup()
        self.assertEqual(result.diagnostics, ())
        self.assertEqual(result.report["assessment"]["outcome"], "inconclusive")
        self.assertIn({"check": "local_python_environment", "reason": "ambiguous",
                       "evidence_refs": ["environment:project_local"]},
                      result.report["assessment"]["limitations"])
        self.assertIn("Result: Inconclusive", result.terminal_report)
        self.assert_unverified(result.report)

    def test_success_means_only_probe_success_with_limited_coverage(self):
        result = self.startup()
        assessment = result.report["assessment"]
        self.assertEqual(result.report["status"], "healthy")
        self.assertEqual(assessment["outcome"], "no_issues_detected")
        self.assertEqual(assessment["coverage"], "limited")
        self.assertEqual(assessment["startup_probe"]["status"], "success")
        self.assertEqual(assessment["limitations"], [])
        self.assert_unverified(result.report)
        self.assertIn("Startup probe completed successfully.", result.terminal_report)
        self.assertIn("Project verification: unverified", result.terminal_report)
        self.assertNotIn("healthy", result.terminal_report.lower())

    def test_startup_failure_retains_error_and_failure_semantics(self):
        result = self.startup(3, "application error")
        self.assertEqual(result.report["status"], "issues_detected")
        self.assertEqual(result.report["assessment"]["outcome"], "issues_detected")
        self.assertEqual(result.report["assessment"]["startup_probe"]["status"], "failed")
        self.assertTrue(any(item.severity == "ERROR" for item in result.diagnostics))
        self.assert_unverified(result.report)

    def test_timeout_info_is_inconclusive_not_fault_or_success(self):
        timeout = subprocess.TimeoutExpired([], 5)
        timeout.terminated = True
        with patch("agent_doctor.commands._run_startup_process", side_effect=timeout):
            result = run_workflow(self.root, confirm_startup=True)
        self.assertEqual({item.severity for item in result.diagnostics}, {"INFO"})
        self.assertEqual(result.report["status"], "healthy")
        self.assertEqual(result.report["assessment"]["outcome"], "inconclusive")
        self.assertEqual(result.report["assessment"]["startup_probe"]["status"], "timeout")
        self.assertIn("Result: Inconclusive", result.terminal_report)
        self.assertNotIn("completed successfully", result.terminal_report)

    def test_existing_error_overrides_incomplete_checks(self):
        (self.root / "pyproject.toml").write_text('[project]\nrequires-python="<3.0"\n')
        result = run_workflow(self.root)
        self.assertEqual(result.report["status"], "issues_detected")
        self.assertEqual(result.report["assessment"]["outcome"], "issues_detected")
        self.assertEqual(result.report["assessment"]["coverage"], "incomplete")
        self.assertIn("Result: Issues detected", result.terminal_report)

    def test_cli_default_and_saved_json_agree(self):
        output = self.root / "report.json"
        code, text, error = self.cli("--output", str(output))
        report = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(code, 0)
        self.assertEqual(error, "")
        self.assertEqual(report["assessment"]["outcome"], "inconclusive")
        self.assertIn("Result: Inconclusive", text)
        self.assertIn("Project verification: unverified", text)
        self.assertNotIn("healthy", text.lower())

    def test_cli_success_never_claims_project_verification(self):
        with patch("agent_doctor.commands._run_startup_process",
                   return_value=subprocess.CompletedProcess([], 0, "", "")):
            code, text, error = self.cli("--run-startup-probe")
        self.assertEqual(code, 0)
        self.assertEqual(error, "")
        self.assertIn("Result: No issues detected within the supported checks.", text)
        self.assertIn("Project verification: unverified", text)
        self.assertIn("Startup probe completed successfully.", text)

    def test_cli_error_keeps_exit_one(self):
        with patch("agent_doctor.commands._run_startup_process",
                   return_value=subprocess.CompletedProcess([], 1, "", "startup error")):
            code, text, error = self.cli("--run-startup-probe")
        self.assertEqual(code, 1)
        self.assertEqual(error, "")
        self.assertIn("Result: Issues detected", text)

    def test_cli_timeout_keeps_exit_zero_with_inconclusive_result(self):
        with patch("agent_doctor.commands._run_startup_process",
                   side_effect=subprocess.TimeoutExpired([], 5)):
            code, text, error = self.cli("--run-startup-probe")
        self.assertEqual(code, 0)
        self.assertEqual(error, "")
        self.assertIn("Result: Inconclusive", text)
        self.assertIn("[INFO]", text)
        self.assertNotIn("[ERROR]", text)

    def report_with(self, result, *, diagnostics=None, evidence=None, detection=None, environment=None):
        return build_json_report(
            result.project, detection or result.detection, environment or result.environment,
            result.diagnostics if diagnostics is None else diagnostics,
            result.evidence if evidence is None else evidence,
        )

    def test_legacy_fields_schema_capabilities_and_evidence_are_preserved(self):
        result = self.startup()
        report = result.report
        legacy_keys = {"schema_version", "generated_at", "tool", "status", "capabilities",
                       "project", "detection", "environment", "diagnostics", "evidence"}
        self.assertEqual(set(report) - {"assessment"}, legacy_keys)
        self.assertEqual(report["schema_version"], "0.2")
        self.assertEqual(report["tool"]["version"], "0.3.0a1")
        self.assertFalse(report["capabilities"]["verification_execution"])
        self.assertFalse(report["capabilities"]["repair_execution"])
        self.assertEqual(report["diagnostics"], [])
        startup = next(item for item in report["evidence"] if item["kind"] == "startup_probe")
        self.assertEqual(startup["metadata"]["exit_code"], 0)
        self.assertIs(startup["metadata"]["executed"], True)

    def test_assessment_references_are_run_local_and_do_not_mutate_evidence(self):
        result = run_workflow(self.root)
        report = result.report
        ids = {item.evidence_id for item in result.evidence}
        for observation in [report["assessment"]["startup_probe"], *report["assessment"]["limitations"]]:
            self.assertTrue(set(observation["evidence_refs"]) <= ids)
        startup = next(item for item in result.evidence if item.kind == "startup_probe")
        report["assessment"]["startup_probe"]["status"] = "changed"
        self.assertEqual(startup.metadata["execution_status"], "requires_confirmation")

    def test_metadata_and_local_environment_limitations_are_inconclusive(self):
        result = self.startup()
        cases = (("local_python_environment", "ambiguous"),
                 ("local_python_environment", "unavailable"),
                 ("python_requirement", "unsupported_requirement"),
                 ("python_requirement", "unsupported_current_version"),
                 ("python_requirement", "invalid_metadata"),
                 ("python_requirement", "unreadable_metadata"),
                 ("python_requirement", "metadata_too_large"))
        for kind, status in cases:
            with self.subTest(kind=kind, status=status):
                observation = Evidence("limitation", kind, "test", "limited", metadata={"status": status})
                report = self.report_with(result, evidence=(*result.evidence, observation))
                self.assertEqual(report["status"], "healthy")
                self.assertEqual(report["assessment"]["outcome"], "inconclusive")
                self.assertEqual(report["assessment"]["limitations"][-1]["reason"], status)

    def test_missing_or_blocked_startup_is_incomplete(self):
        result = self.startup()
        other = tuple(item for item in result.evidence if item.kind != "startup_probe")
        for status in (None, "requires_confirmation", "unavailable", "rejected", "no_supported_entrypoint"):
            with self.subTest(status=status):
                observations = other if status is None else (*other, Evidence(
                    "probe", "startup_probe", "test", "limited", metadata={"execution_status": status}))
                report = self.report_with(result, evidence=observations)
                self.assertEqual(report["assessment"]["outcome"], "inconclusive")
                self.assertEqual(report["assessment"]["coverage"], "incomplete")

    def test_unconfirmed_launch_is_incomplete_even_after_probe_success(self):
        result = self.startup()
        report = self.report_with(result, environment=replace(result.environment, python_callable=None))
        self.assertEqual(report["assessment"]["outcome"], "inconclusive")
        self.assertEqual(report["assessment"]["limitations"][0]["check"], "python_launch")

    def test_unknown_detection_and_error_priority_remain_distinct(self):
        result = self.startup()
        unknown = DetectionResult("unknown", ())
        report = self.report_with(result, detection=unknown)
        self.assertEqual(report["status"], "unknown")
        self.assertEqual(report["assessment"]["outcome"], "inconclusive")
        for severity in ("ERROR", "CRITICAL", "WARNING"):
            with self.subTest(severity=severity):
                finding = DiagnosisResult("Observed issue", "environment", severity, 1, "test")
                report = self.report_with(result, detection=unknown, diagnostics=(finding,))
                self.assertEqual(report["status"], "unknown" if severity == "WARNING" else "issues_detected")
                self.assertEqual(report["assessment"]["outcome"], "issues_detected")

    def test_unrelated_info_does_not_become_a_fault(self):
        result = self.startup()
        notice = DiagnosisResult("Notice", "tool/safety", "INFO", 1, "test")
        report = self.report_with(result, diagnostics=(notice,))
        self.assertEqual(report["status"], "healthy")
        self.assertEqual(report["assessment"]["outcome"], "no_issues_detected")
        self.assert_unverified(report)

    def test_probe_without_confirmed_execution_cannot_complete_checks(self):
        result = self.startup()
        other = tuple(item for item in result.evidence if item.kind != "startup_probe")
        for status in ("success", "failed"):
            observation = Evidence("probe", "startup_probe", "test", "outcome", metadata={
                "execution_status": status, "exit_code": None, "executed": False})
            with self.subTest(status=status):
                report = self.report_with(result, evidence=(*other, observation))
                self.assertEqual(report["assessment"]["outcome"], "inconclusive")
                self.assertEqual(report["assessment"]["limitations"][0]["reason"], "outcome_not_confirmed")

    def test_terminal_and_json_use_the_same_assessment_for_ambiguity(self):
        result = self.startup()
        observation = Evidence("local", "local_python_environment", "test", "ambiguous", metadata={"status": "ambiguous"})
        evidence = (*result.evidence, observation)
        report = self.report_with(result, evidence=evidence)
        terminal = render_terminal_report(result.project, result.detection, result.environment, (), evidence=evidence)
        self.assertEqual(report["assessment"]["outcome"], "inconclusive")
        self.assertIn("Result: Inconclusive", terminal)
        self.assertNotIn("healthy", terminal.lower())

    def test_extension_failure_and_successful_startup_are_inconclusive(self):
        for stage, error in ((Capability.INSPECT, ExtensionUnavailable),
                             (Capability.DIAGNOSE, ExtensionFailure)):
            with self.subTest(stage=stage):
                broken = RecordingExtension("broken", errors={stage: error("broken", "unavailable")})
                with patch("agent_doctor.extension_pipeline.BUILTIN_EXTENSIONS", (broken, PythonCoreExtension())):
                    result = self.startup()
                self.assertEqual(result.diagnostics, ())
                self.assertEqual(len(result.extension_failures), 1)
                self.assertEqual(result.report["assessment"]["startup_probe"]["status"], "success")
                self.assertEqual(result.report["assessment"]["outcome"], "inconclusive")
                self.assertEqual(result.report["assessment"]["coverage"], "incomplete")
                limitation = result.report["assessment"]["limitations"][0]
                self.assertEqual(limitation["extension_id"], "broken")
                self.assertEqual(limitation["stage"], stage.value)
                self.assertEqual(limitation["reason"], result.extension_failures[0].status)
                self.assertIn("Result: Inconclusive", result.terminal_report)
                self.assertIn("Extension check (limitation): broken", result.terminal_report)

    def test_extension_limitations_keep_identity_without_exposing_exception_text(self):
        result = self.startup()
        failures = (ExtensionFailureInfo("pack", None, "incompatible", "private exception text"),)
        report = build_json_report(result.project, result.detection, result.environment, (), result.evidence,
                                   extension_failures=failures)
        terminal = render_terminal_report(result.project, result.detection, result.environment, (),
                                          evidence=result.evidence, extension_failures=failures)
        self.assertEqual(report["assessment"]["limitations"], [{
            "check": "extension", "reason": "incompatible", "extension_id": "pack",
            "stage": None, "evidence_refs": [],
        }])
        self.assertEqual(report["assessment"]["coverage"], "incomplete")
        self.assertNotIn("private exception text", json.dumps(report))
        self.assertNotIn("private exception text", terminal)

    def assert_startup_not_successful(self, *, executed, exit_code):
        result = self.startup()
        observation = Evidence("probe", "startup_probe", "test", "unconfirmed", metadata={
            "execution_status": "success", "executed": executed, "exit_code": exit_code})
        report = self.report_with(result, evidence=(observation,))
        terminal = render_terminal_report(result.project, result.detection, result.environment, (),
                                          evidence=(observation,))
        self.assertEqual(report["assessment"]["outcome"], "inconclusive")
        self.assertEqual(report["assessment"]["startup_probe"]["status"], "outcome_not_confirmed")
        self.assertNotIn("completed successfully", terminal)
        self.assertIn("Startup probe success could not be confirmed.", terminal)
        self.assertIn("Result: Inconclusive", terminal)

    def test_success_label_with_executed_false_is_not_probe_success(self):
        self.assert_startup_not_successful(executed=False, exit_code=0)

    def test_success_label_without_exit_code_is_not_probe_success(self):
        self.assert_startup_not_successful(executed=True, exit_code=None)

    def test_success_label_with_nonzero_exit_code_is_not_probe_success(self):
        self.assert_startup_not_successful(executed=True, exit_code=3)
