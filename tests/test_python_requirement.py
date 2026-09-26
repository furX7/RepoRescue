"""Bounded project requirement inspection and evidence-backed version diagnosis."""

import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from agent_doctor.cli import main
from agent_doctor.diagnosis import diagnose
from agent_doctor.models import DetectionResult, EnvironmentInfo, ExecutionResult
from agent_doctor.project import inspect_environment, scan_project
from agent_doctor.python_plugin import (
    compare_python_requirement, inspect_python_requirement, propose_diagnostic_commands,
)
from agent_doctor.report import build_json_report, render_terminal_report
from agent_doctor.workflow import run_workflow


class PythonRequirementTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.environment = EnvironmentInfo(Path(sys.executable), "3.13.1", True, ())

    def write_requirement(self, value):
        (self.root / "pyproject.toml").write_text(
            "[project]\nrequires-python = " + json.dumps(value) + "\n", encoding="utf-8",
        )
        return scan_project(self.root)

    def inspect(self, value, version="3.13.1"):
        project = self.write_requirement(value)
        return inspect_python_requirement(project, replace(self.environment, python_version=version))

    def diagnostic(self, value):
        project = self.write_requirement(value)
        evidence = inspect_python_requirement(project, self.environment)
        diagnoses, all_evidence = diagnose(
            project, DetectionResult("likely", ("pyproject.toml",)),
            self.environment, python_requirement=evidence,
        )
        return project, evidence, diagnoses, all_evidence

    def test_release_comparison_and_combined_bounds(self):
        cases = (
            (">=3.10", "3.13.1", "compatible"),
            (">=3.10,<3.13", "3.13.1", "incompatible"),
            ("==3.13.1", "3.13.1", "compatible"),
            (">=3.14", "3.13.1", "incompatible"),
            ("<=3.12", "3.13.1", "incompatible"),
            (">3.13", "3.13.1", "compatible"),
            ("<=3.13.1", "3.13.1", "compatible"),
            (">=3.11, <3.14, !=3.12.*", "3.13.1", "compatible"),
            ("==3.12.*", "3.12.9", "compatible"),
            ("==3.12.*", "3.13.0", "incompatible"),
            (">=3.11,!=3.12.*", "3.12.1", "incompatible"),
            ("!=3.13.1", "3.13.1", "incompatible"),
        )
        for requirement, version, expected in cases:
            with self.subTest(requirement=requirement, version=version):
                self.assertEqual(compare_python_requirement(requirement, version), expected)

    def test_release_boundaries_and_numeric_order(self):
        cases = (
            ("==3.13", "3.13.0", "compatible"),
            ("==3.13", "3.13.1", "incompatible"),
            ("<3.13", "3.13.0", "incompatible"),
            (">3.13", "3.13.0", "incompatible"),
            (">=3.9", "3.10.0", "compatible"),
        )
        for requirement, version, expected in cases:
            with self.subTest(requirement=requirement, version=version):
                self.assertEqual(compare_python_requirement(requirement, version), expected)

    def test_unsupported_syntax_never_becomes_incompatible_even_after_false_clause(self):
        for requirement in ("~=3.12", ">=3.14,~=3.12", ">=3.10rc1", "==3.13+local",
                            "===3.13", "==1!3.13", "==3.*", ">=3.12.*", "",
                            ">=3.10,", ">=3.10; os_name == 'nt'", ">=3." + "1" * 5000):
            with self.subTest(requirement=requirement):
                self.assertEqual(compare_python_requirement(requirement, "3.13.1"), "unsupported_requirement")

    def test_non_final_or_unrecognized_current_version_is_not_guessed(self):
        for version in ("3.13.0rc1", "3.13.0a1", "3.13.0+local", "", "Python 3.13.1"):
            with self.subTest(version=version):
                self.assertEqual(compare_python_requirement(">=3.10", version), "unsupported_current_version")

    def test_no_pyproject_has_no_requirement_evidence(self):
        self.assertIsNone(inspect_python_requirement(scan_project(self.root), self.environment))

    def test_missing_field_has_no_requirement_evidence(self):
        (self.root / "pyproject.toml").write_text("[project]\nname = 'example'\n", encoding="utf-8")
        self.assertIsNone(inspect_python_requirement(scan_project(self.root), self.environment))

    def test_child_pyproject_is_not_read(self):
        child = self.root / "child"
        child.mkdir()
        (child / "pyproject.toml").write_text("[project]\nrequires-python = '>=4.0'\n", encoding="utf-8")
        with patch.object(Path, "open", side_effect=AssertionError("child content read")):
            self.assertIsNone(inspect_python_requirement(scan_project(self.root), self.environment))

    def test_other_manifest_contents_are_not_read(self):
        for name in ("requirements.txt", "setup.py", "setup.cfg", "poetry.lock", "uv.lock", "Pipfile"):
            (self.root / name).write_text("do not read", encoding="utf-8")
        with patch.object(Path, "open", side_effect=AssertionError("other manifest read")):
            self.assertIsNone(inspect_python_requirement(scan_project(self.root), self.environment))

    def test_malformed_toml_has_structured_notice_not_version_fault(self):
        (self.root / "pyproject.toml").write_text("[project\n", encoding="utf-8")
        project = scan_project(self.root)
        item = inspect_python_requirement(project, self.environment)
        self.assertEqual(item.metadata["status"], "invalid_metadata")
        diagnoses, evidence = diagnose(project, DetectionResult("likely", ("pyproject.toml",)),
                                       self.environment, python_requirement=item)
        self.assertEqual(diagnoses, [])
        self.assertIn(item, evidence)

    def test_wrong_field_type_is_not_a_mismatch(self):
        (self.root / "pyproject.toml").write_text("[project]\nrequires-python = 313\n", encoding="utf-8")
        item = inspect_python_requirement(scan_project(self.root), self.environment)
        self.assertEqual(item.metadata["status"], "invalid_metadata")
        self.assertIsNone(item.metadata["declared_python_requirement"])

    def test_invalid_utf8_is_metadata_notice(self):
        (self.root / "pyproject.toml").write_bytes(b"\xff")
        item = inspect_python_requirement(scan_project(self.root), self.environment)
        self.assertEqual(item.metadata["status"], "invalid_metadata")

    def test_large_metadata_is_not_parsed_or_reported_as_mismatch(self):
        (self.root / "pyproject.toml").write_bytes(b"#" * (65536 + 1))
        item = inspect_python_requirement(scan_project(self.root), self.environment)
        self.assertEqual(item.metadata["status"], "metadata_too_large")

    def test_read_failure_is_structured_and_does_not_crash(self):
        project = self.write_requirement(">=3.10")
        with patch.object(Path, "open", side_effect=PermissionError("private file details")):
            item = inspect_python_requirement(project, self.environment)
        self.assertEqual(item.metadata["status"], "unreadable_metadata")
        self.assertNotIn("private file details", item.summary)

    def test_changed_symlink_or_escaped_metadata_is_not_opened(self):
        project = self.write_requirement(">=3.10")
        with patch.object(Path, "is_symlink", return_value=True), \
             patch.object(Path, "open", side_effect=AssertionError("symlink read")):
            item = inspect_python_requirement(project, self.environment)
        self.assertEqual(item.metadata["status"], "unreadable_metadata")

    def test_escaped_metadata_is_not_opened(self):
        project = self.write_requirement(">=3.10")
        with patch.object(Path, "resolve", return_value=self.root.parent / "outside.toml"), \
             patch.object(Path, "open", side_effect=AssertionError("escaped path read")):
            item = inspect_python_requirement(project, self.environment)
        self.assertEqual(item.metadata["status"], "unreadable_metadata")

    def test_large_toml_integer_is_metadata_notice_not_a_crash(self):
        (self.root / "pyproject.toml").write_text("[project]\nrequires-python = " + "1" * 5000, encoding="utf-8")
        item = inspect_python_requirement(scan_project(self.root), self.environment)
        self.assertEqual(item.metadata["status"], "invalid_metadata")

    def test_structured_evidence_preserves_declared_and_current_versions(self):
        item = self.inspect(" >=3.10, <3.13 ")
        self.assertEqual(item.source, "pyproject.toml")
        self.assertEqual(item.metadata, {
            "declared_python_requirement": " >=3.10, <3.13 ",
            "current_python_version": "3.13.1",
            "status": "incompatible",
        })

    def test_incompatible_produces_error_and_short_referenced_chain(self):
        _, item, diagnoses, evidence = self.diagnostic(">=3.10,<3.13")
        self.assertEqual(len(diagnoses), 1)
        diagnosis = diagnoses[0]
        self.assertEqual((diagnosis.category, diagnosis.severity), ("python_version", "ERROR"))
        self.assertIn(item.evidence_id, diagnosis.evidence_refs)
        self.assertEqual(len(diagnosis.root_cause_chain), 3)
        ids = {entry.evidence_id for entry in evidence}
        for step in diagnosis.root_cause_chain:
            self.assertTrue(step.evidence_refs)
            self.assertTrue(set(step.evidence_refs) <= ids)
        output = str(diagnosis).lower()
        for unsupported_claim in ("corrupted", "broken", "path is wrong", "installation damaged"):
            self.assertNotIn(unsupported_claim, output)

    def test_repair_is_medium_risk_preview_with_two_unrun_checks(self):
        _, _, diagnoses, _ = self.diagnostic(">=3.10,<3.13")
        plan = diagnoses[0].repair_plan
        self.assertEqual(plan.risk, "MEDIUM")
        self.assertEqual(plan.execution_status, "not_executed")
        self.assertTrue(all(action.requires_confirmation for action in plan.actions))
        self.assertEqual(len(plan.verification_steps), 2)
        self.assertEqual(plan.verification_steps[0].type, "command")
        self.assertTrue(all(step.status == "not_run" for step in plan.verification_steps))

    def test_compatible_retains_evidence_without_problem(self):
        _, item, diagnoses, evidence = self.diagnostic(">=3.10")
        self.assertEqual(item.metadata["status"], "compatible")
        self.assertEqual(diagnoses, [])
        self.assertIn(item, evidence)

    def test_unsupported_is_tool_limitation_without_problem_or_repair(self):
        project, item, diagnoses, evidence = self.diagnostic("~=3.12")
        self.assertEqual(item.metadata["status"], "unsupported_requirement")
        self.assertEqual(diagnoses, [])
        terminal = render_terminal_report(project, DetectionResult("likely", ("pyproject.toml",)),
                                         self.environment, diagnoses, evidence=evidence)
        self.assertIn("not supported", terminal)
        self.assertIn("tool limitation", terminal)

    def test_json_keeps_schema_and_structured_comparison(self):
        project, item, diagnoses, evidence = self.diagnostic(">=3.10,<3.13")
        report = build_json_report(project, DetectionResult("likely", ("pyproject.toml",)),
                                   self.environment, diagnoses, evidence)
        loaded = json.loads(json.dumps(report))
        self.assertEqual(loaded["schema_version"], "0.2")
        self.assertEqual(loaded["evidence"][-1]["metadata"], item.metadata)
        self.assertEqual(loaded["status"], "issues_detected")
        self.assertEqual(loaded["diagnostics"][0]["repair_plan"]["execution_status"], "not_executed")

    def test_workflow_reads_requirement_and_only_runs_existing_version_probe(self):
        self.write_requirement(">=3.10,<3.13")
        with patch("agent_doctor.workflow.inspect_environment", return_value=self.environment), \
             patch("agent_doctor.workflow.execute_command") as execute:
            command = propose_diagnostic_commands(scan_project(self.root), self.environment)[0]
            execute.return_value = ExecutionResult(command, 0, "Python 3.13.1", "", 0.01)
            result = run_workflow(self.root)
        self.assertEqual(execute.call_count, 1)
        self.assertEqual(execute.call_args.args[0].arguments, ("--version",))
        self.assertEqual(result.diagnostics[0].category, "python_version")
        self.assertIn("Current interpreter: 3.13.1", result.terminal_report)
        self.assertIn("not run", result.terminal_report)

    def test_cli_mismatch_exits_one(self):
        self.write_requirement(">=3.10,<3.13")
        with patch("agent_doctor.workflow.inspect_environment", return_value=self.environment), \
             redirect_stdout(io.StringIO()) as output:
            self.assertEqual(main([str(self.root)]), 1)
        self.assertIn("[ERROR] python_version", output.getvalue())

    def test_real_workflow_keeps_files_bytes_mtimes_and_environment(self):
        self.write_requirement(">=3.10,<3.13")
        (self.root / "main.py").write_text("raise RuntimeError('must not execute')\n", encoding="utf-8")
        def snapshot():
            return {str(p.relative_to(self.root)): (p.read_bytes(), p.stat().st_mtime_ns)
                    for p in self.root.rglob("*") if p.is_file()}
        before, env = snapshot(), dict(os.environ)
        result = run_workflow(self.root)
        self.assertEqual(snapshot(), before)
        self.assertEqual(dict(os.environ), env)
        self.assertFalse((self.root / "__pycache__").exists())
        self.assertEqual(len(result.execution_results), 2)
        self.assertEqual(result.execution_results[1].status, 'requires_confirmation')

    def test_inspection_preserves_non_final_runtime_version(self):
        class PreviewVersion(tuple):
            releaselevel = "candidate"
            serial = 1
        with patch("agent_doctor.project.sys.version_info", PreviewVersion((3, 13, 0))):
            environment = inspect_environment(scan_project(self.root))
        self.assertEqual(environment.python_version, "3.13.0rc1")


if __name__ == "__main__":
    unittest.main()
