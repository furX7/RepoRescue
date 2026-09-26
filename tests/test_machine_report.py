"""Agent-facing status, capabilities, and non-execution contracts."""

import io
import tomllib
import unittest
from contextlib import redirect_stdout
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import agent_doctor
from agent_doctor.cli import main
from agent_doctor.diagnosis import diagnose
from agent_doctor.models import DetectionResult, DiagnosisResult, EnvironmentInfo, ProjectInfo
from agent_doctor.report import build_json_report, render_terminal_report


class MachineReportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.project = ProjectInfo(Path("sample"), datetime.now(timezone.utc))
        self.detection = DetectionResult("likely", ("main.py",))
        self.environment = EnvironmentInfo(Path("python.exe"), "3.13.1", True, ())

    def report(self, diagnostics=(), detection=None):
        return build_json_report(
            self.project, self.detection if detection is None else detection,
            self.environment, diagnostics, (),
        )

    def diagnostic(self, severity):
        return DiagnosisResult("Observed issue", "environment", severity, 0.9, "rule:sample")

    def test_tool_name_and_version(self) -> None:
        self.assertEqual(self.report()["tool"], {
            "name": "repo-rescue", "version": agent_doctor.__version__,
        })

    def test_schema_version_is_separate_from_package_version(self) -> None:
        report = self.report()
        self.assertEqual(report["schema_version"], "0.2")
        self.assertEqual(report["tool"]["version"], "0.2.0a1")

    def test_no_error_or_warning_is_healthy_with_limited_scope(self) -> None:
        self.assertEqual(self.report()["status"], "healthy")

    def test_unknown_detection_is_unknown(self) -> None:
        self.assertEqual(self.report(detection=DetectionResult("unknown", ()))["status"], "unknown")

    def test_error_takes_priority_over_unknown(self) -> None:
        for severity in ("ERROR", "CRITICAL"):
            with self.subTest(severity=severity):
                self.assertEqual(self.report(
                    (self.diagnostic(severity),), DetectionResult("unknown", ()),
                )["status"], "issues_detected")

    def test_warning_is_issue_for_identified_project(self) -> None:
        self.assertEqual(self.report((self.diagnostic("WARNING"),))["status"], "issues_detected")

    def test_info_does_not_become_project_fault(self) -> None:
        safety = replace(self.diagnostic("INFO"), category="tool/safety")
        self.assertEqual(self.report((safety,))["status"], "healthy")

    def test_capabilities_are_fixed_and_do_not_allow_execution(self) -> None:
        expected = {
            "diagnosis": True, "root_cause_analysis": True, "repair_preview": True,
            "repair_execution": False, "verification_plan": True,
            "verification_execution": False, "rollback": False,
        }
        for report in (self.report(), self.report((self.diagnostic("ERROR"),)),
                       self.report(detection=DetectionResult("unknown", ()))):
            self.assertEqual(report["capabilities"], expected)

    def test_mutating_report_does_not_change_program_capabilities(self) -> None:
        report = self.report()
        report["capabilities"]["repair_execution"] = True
        self.assertFalse(self.report()["capabilities"]["repair_execution"])

    def test_every_preview_and_verification_step_is_explicitly_not_executed(self) -> None:
        results, evidence = diagnose(
            self.project, DetectionResult("unknown", ()),
            replace(self.environment, python_available=False),
        )
        report = build_json_report(self.project, self.detection, self.environment, results, evidence)
        for diagnostic in report["diagnostics"]:
            plan = diagnostic["repair_plan"]
            self.assertEqual(plan["execution_status"], "not_executed")
            self.assertTrue(plan["verification_steps"])
            self.assertTrue(all(step["status"] == "not_run" for step in plan["verification_steps"]))
            self.assertNotIn("fixed", plan)
            self.assertNotIn("repair_success", plan)

    def test_models_cannot_masquerade_as_executed(self) -> None:
        results, _ = diagnose(self.project, DetectionResult("unknown", ()), self.environment)
        plan = results[0].repair_plan
        with self.assertRaises(ValueError):
            replace(plan, execution_status="executed")
        with self.assertRaises(ValueError):
            replace(plan.verification_steps[0], status="passed")

    def test_cli_version_needs_no_project_argument(self) -> None:
        stdout = io.StringIO()
        with redirect_stdout(stdout), self.assertRaises(SystemExit) as result:
            main(["--version"])
        self.assertEqual(result.exception.code, 0)
        self.assertEqual(stdout.getvalue().strip(), f"repo-rescue {agent_doctor.__version__}")

    def test_package_uses_single_version_source_and_mit_metadata(self) -> None:
        path = Path(__file__).resolve().parents[1] / "pyproject.toml"
        config = tomllib.loads(path.read_text(encoding="utf-8"))
        self.assertIn("version", config["project"]["dynamic"])
        self.assertEqual(config["tool"]["setuptools"]["dynamic"]["version"]["attr"], "agent_doctor.__version__")
        self.assertEqual(agent_doctor.__version__, "0.2.0a1")
        self.assertEqual(config["project"]["license"], "MIT")
        self.assertEqual(config["project"]["dependencies"], [])

    def test_distribution_scripts_and_repository_urls(self) -> None:
        root = Path(__file__).resolve().parents[1]
        config = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
        self.assertEqual(config["project"]["name"], "repo-rescue")
        self.assertEqual(config["project"]["scripts"], {
            "repo-rescue": "agent_doctor.cli:main",
            "agent-doctor": "agent_doctor.cli:main",
        })
        self.assertEqual(config["project"]["urls"], {
            "Homepage": "https://github.com/furX7/RepoRescue",
            "Repository": "https://github.com/furX7/RepoRescue",
            "Issues": "https://github.com/furX7/RepoRescue/issues",
        })

    def test_help_uses_new_cli_brand(self) -> None:
        stdout = io.StringIO()
        with redirect_stdout(stdout), self.assertRaises(SystemExit) as result:
            main(["--help"])
        self.assertEqual(result.exception.code, 0)
        self.assertIn("usage: repo-rescue", stdout.getvalue())
        self.assertNotIn("agent-doctor", stdout.getvalue())

    def test_readme_uses_public_brand_and_documents_internal_package(self) -> None:
        root = Path(__file__).resolve().parents[1]
        readme = (root / "README.md").read_text(encoding="utf-8")
        self.assertTrue(readme.startswith("# RepoRescue\n"))
        self.assertEqual(readme.count("Agent Doctor"), 1)
        self.assertIn("Internal Python package: `agent_doctor`", readme)
        self.assertIn("repo-rescue.exe --version", readme)
        self.assertIn("repo_rescue-0.2.0a1-py3-none-any.whl", readme)
        self.assertIn("https://github.com/furX7/RepoRescue", readme)

    def test_terminal_states_read_only_preview_and_limited_checks(self) -> None:
        results, _ = diagnose(self.project, DetectionResult("unknown", ()), self.environment)
        for diagnostics in ((), results):
            text = render_terminal_report(self.project, self.detection, self.environment, diagnostics)
            self.assertIn("READ ONLY", text)
            self.assertIn("Repair preview only", text)
            self.assertIn("Verification steps were not run", text)
            self.assertIn("RepoRescue currently performs limited checks.", text)
            self.assertNotIn("Your project is healthy", text)


if __name__ == "__main__":
    unittest.main()
