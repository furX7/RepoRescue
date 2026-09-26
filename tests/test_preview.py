"""Evidence-bound previews remain descriptive and preserve old report fields."""

import json
import unittest
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from agent_doctor.diagnosis import diagnose
from agent_doctor.models import (
    CommandProposal, DetectionResult, EnvironmentInfo, ExecutionResult,
    ProjectInfo, RepairPlan, RootCauseStep, VerificationStep,
)
from agent_doctor.report import build_json_report, render_terminal_report


class PreviewTests(unittest.TestCase):
    def setUp(self) -> None:
        self.project = ProjectInfo(Path("sample"), datetime.now(timezone.utc), files=("main.py",))
        self.detection = DetectionResult("likely", ("main.py",))
        self.environment = EnvironmentInfo(Path("python.exe"), "3.13.1", True, ())
        self.command = CommandProposal(
            "python.exe", ("--version",), self.project.root_path,
            "python_plugin", "Check version", "SAFE",
        )
        self.failed = ExecutionResult(self.command, 1, "", "", 0.1, status="failed")

    def run_rules(self, *, detection=None, environment=None, executions=()):
        return diagnose(
            self.project, self.detection if detection is None else detection,
            self.environment if environment is None else environment, executions,
        )

    def test_unknown_detection_has_one_supported_step(self) -> None:
        results, _ = self.run_rules(detection=DetectionResult("unknown", ()))
        diagnosis = results[0]
        self.assertEqual(len(diagnosis.root_cause_chain), 1)
        self.assertEqual(diagnosis.root_cause_chain[0].evidence_refs, ("detection:python",))
        self.assertIn("deeper files are not ruled out", diagnosis.root_cause_chain[0].explanation)
        self.assertEqual(diagnosis.repair_plan.verification_steps[0].type, "file_check")

    def test_unavailable_environment_has_chain_and_existing_interpreter_preview(self) -> None:
        results, _ = self.run_rules(environment=replace(self.environment, python_available=False))
        diagnosis = results[0]
        self.assertEqual(len(diagnosis.root_cause_chain), 2)
        self.assertTrue(all(step.evidence_refs == ("environment:python",) for step in diagnosis.root_cause_chain))
        self.assertIn("existing", diagnosis.repair_plan.actions[0].description)
        self.assertEqual({step.type for step in diagnosis.repair_plan.verification_steps}, {"manual", "command"})

    def test_probe_failure_and_timeout_have_linked_repair_previews(self) -> None:
        for outcome in (self.failed, replace(self.failed, status="timeout", timed_out=True, exit_code=None)):
            with self.subTest(status=outcome.status):
                results, _ = self.run_rules(executions=(outcome,))
                diagnosis = results[0]
                self.assertEqual(diagnosis.repair_plan.diagnosis_id, diagnosis.diagnosis_id)
                self.assertEqual(diagnosis.repair_plan.risk, "LOW")
                self.assertTrue(diagnosis.repair_plan.verification_steps)
                self.assertIn("exit code 0", diagnosis.repair_plan.verification_steps[0].description)
                self.assertTrue(all(action.requires_confirmation for action in diagnosis.repair_plan.actions))

    def test_safety_rejection_has_no_repair_plan(self) -> None:
        results, _ = self.run_rules(executions=(replace(self.failed, status="rejected", exit_code=None),))
        self.assertEqual(results[0].category, "tool/safety")
        self.assertIsNone(results[0].repair_plan)
        self.assertEqual(results[0].root_cause_chain, ())

    def test_all_root_steps_reference_existing_evidence(self) -> None:
        results, evidence = self.run_rules(
            detection=DetectionResult("unknown", ()),
            environment=replace(self.environment, python_available=False),
            executions=(self.failed,),
        )
        ids = {item.evidence_id for item in evidence}
        for diagnosis in results:
            for step in diagnosis.root_cause_chain:
                self.assertTrue(step.evidence_refs)
                self.assertTrue(set(step.evidence_refs).issubset(ids))

    def test_nonzero_exit_does_not_assert_launch_or_installation_defect(self) -> None:
        results, _ = self.run_rules(executions=(replace(self.failed, stderr="installation corrupted; PATH error"),))
        diagnosis = results[0]
        text = " ".join(step.title + " " + step.explanation for step in diagnosis.root_cause_chain).lower()
        self.assertNotIn("corrupted", text)
        self.assertNotIn("path error", text)
        self.assertNotIn("could not be launched", text)
        self.assertEqual(diagnosis.probable_causes, ())
        self.assertIn("exit_code=1", text)

    def test_json_keeps_legacy_fields_and_adds_nested_preview(self) -> None:
        results, evidence = self.run_rules(executions=(self.failed,))
        report = build_json_report(self.project, self.detection, self.environment, results, evidence)
        item = json.loads(json.dumps(report))["diagnostics"][0]
        self.assertEqual(report["schema_version"], "0.2")
        for name in (
            "problem", "category", "severity", "confidence", "source", "evidence_refs",
            "probable_causes", "recommended_actions",
        ):
            self.assertIn(name, item)
            expected = getattr(results[0], name)
            self.assertEqual(item[name], list(expected) if isinstance(expected, tuple) else expected)
        self.assertEqual(item["root_cause_chain"][0]["evidence_refs"], ["execution:0"])
        self.assertEqual(item["repair_plan"]["diagnosis_id"], item["diagnosis_id"])
        self.assertTrue(item["repair_plan"]["verification_steps"])

    def test_terminal_adds_three_short_preview_lines(self) -> None:
        results, _ = self.run_rules(executions=(self.failed,))
        text = render_terminal_report(self.project, self.detection, self.environment, results)
        self.assertIn("Root cause:", text)
        self.assertIn("Suggested repair (preview only, LOW):", text)
        self.assertIn("Verification (planned, not run):", text)
        self.assertEqual(sum(line.startswith(("  Root cause:", "  Suggested repair", "  Verification"))
                             for line in text.splitlines()), 3)

    def test_normal_and_pending_results_have_no_false_plan(self) -> None:
        for executions in ((), (replace(self.failed, status="success", exit_code=0),),
                           (replace(self.failed, status="requires_confirmation", exit_code=None),)):
            with self.subTest(executions=executions):
                self.assertEqual(self.run_rules(executions=executions)[0], [])

    def test_preview_creation_does_not_perform_actions_or_modify_inputs(self) -> None:
        with patch.object(Path, "open", side_effect=AssertionError("file write/read")):
            with patch("subprocess.Popen", side_effect=AssertionError("command executed")):
                results, evidence = self.run_rules(executions=(self.failed,))
                before = results.copy()
                build_json_report(self.project, self.detection, self.environment, results, evidence)
                render_terminal_report(self.project, self.detection, self.environment, results)
        self.assertEqual(results, before)
        self.assertEqual(self.failed.status, "failed")

    def test_model_constraints_for_evidence_risk_and_verification(self) -> None:
        with self.assertRaisesRegex(ValueError, "reference evidence"):
            RootCauseStep("id", "title", "explanation", ())
        step = VerificationStep("verify", "Manual check", "manual")
        for risk in ("LOW", "MEDIUM", "HIGH"):
            self.assertEqual(RepairPlan("plan", "diagnosis", "summary", risk, (), (step,)).risk, risk)
        with self.assertRaisesRegex(ValueError, "Repair risk"):
            RepairPlan("plan", "diagnosis", "summary", "SAFE", (), (step,))
        with self.assertRaisesRegex(ValueError, "verification step"):
            RepairPlan("plan", "diagnosis", "summary", "LOW", (), ())
        for kind in ("command", "file_check", "manual"):
            self.assertEqual(VerificationStep("verify", "description", kind).type, kind)
        with self.assertRaises(ValueError):
            VerificationStep("verify", "description", "repair")

    def test_multiple_failed_probes_have_distinct_linked_ids(self) -> None:
        results, _ = self.run_rules(executions=(self.failed, self.failed))
        self.assertEqual(len({item.diagnosis_id for item in results}), 2)
        self.assertEqual(len({item.repair_plan.id for item in results}), 2)
        for index, result in enumerate(results):
            self.assertEqual(result.repair_plan.diagnosis_id, result.diagnosis_id)
            self.assertEqual(result.root_cause_chain[0].evidence_refs, (f"execution:{index}",))


if __name__ == "__main__":
    unittest.main()
