"""Rules must rely on structured evidence and avoid unsupported root causes."""

import unittest
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from agent_doctor.diagnosis import diagnose
from agent_doctor.models import (
    CommandProposal, DetectionResult, EnvironmentInfo, ExecutionResult, ProjectInfo,
)


class DiagnosisTests(unittest.TestCase):
    def setUp(self) -> None:
        self.project = ProjectInfo(
            root_path=Path("example-project"), scanned_at=datetime.now(timezone.utc),
            files=("main.py",),
        )
        self.detection = DetectionResult("likely", ("main.py",))
        self.environment = EnvironmentInfo(
            python_executable=Path("python.exe"), python_version="3.13.1",
            python_available=True, dependency_manifests=(),
        )
        self.command = CommandProposal(
            executable="python.exe", arguments=("--version",),
            working_directory=self.project.root_path, source="python_plugin",
            reason="Check Python version", risk="SAFE",
        )
        self.success = ExecutionResult(self.command, 0, "Python 3.13.1", "", 0.1)

    def run_rules(self, *, detection=None, environment=None, executions=()):
        return diagnose(
            self.project,
            self.detection if detection is None else detection,
            self.environment if environment is None else environment,
            executions,
        )

    def test_unknown_detection_reports_scan_limit_not_non_python_certainty(self) -> None:
        results, evidence = self.run_rules(detection=DetectionResult("unknown", ()))
        self.assertEqual(len(results), 1)
        result = results[0]
        self.assertEqual(result.category, "project_detection")
        self.assertEqual(result.severity, "WARNING")
        self.assertEqual(result.problem, "The shallow scan found no Python project markers")
        self.assertEqual(result.evidence_refs, ("detection:python",))
        self.assertEqual(result.probable_causes, ())
        self.assertTrue(any("depth" in action for action in result.recommended_actions))
        self.assertIn("level=unknown", evidence[0].summary)

    def test_likely_detection_alone_produces_no_problem(self) -> None:
        results, evidence = self.run_rules()
        self.assertEqual(results, [])
        self.assertIn("main.py", evidence[0].summary)

    def test_unavailable_executable(self) -> None:
        results, evidence = self.run_rules(environment=replace(
            self.environment, python_available=False,
        ))
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].category, "environment")
        self.assertEqual(results[0].severity, "ERROR")
        self.assertEqual(results[0].evidence_refs, ("environment:python",))
        self.assertIn("available=False", evidence[1].summary)

    def test_missing_executable_is_unavailable(self) -> None:
        results, _ = self.run_rules(environment=replace(
            self.environment, python_executable=None,
        ))
        self.assertEqual(results[0].source, "rule:python_environment_unavailable")

    def test_explicit_uncallable_environment(self) -> None:
        results, _ = self.run_rules(environment=replace(
            self.environment, python_callable=False,
        ))
        self.assertEqual(results[0].source, "rule:python_environment_unavailable")

    def test_unverified_callable_is_not_a_failure(self) -> None:
        self.assertIsNone(self.environment.python_callable)
        self.assertEqual(self.run_rules()[0], [])

    def test_success_preserves_positive_evidence_without_problem(self) -> None:
        results, evidence = self.run_rules(executions=(self.success,))
        self.assertEqual(results, [])
        self.assertEqual(evidence[-1].evidence_id, "execution:0")
        self.assertEqual(evidence[-1].associated_id, "execution:0")
        self.assertIn("status=success; exit_code=0", evidence[-1].summary)

    def test_failed_probe_produces_evidence_backed_problem(self) -> None:
        failed = replace(self.success, status="failed", exit_code=None, message="launch denied")
        results, evidence = self.run_rules(executions=(failed,))
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].source, "rule:python_version_probe_failed")
        self.assertEqual(results[0].evidence_refs, ("execution:0",))
        self.assertEqual(results[0].category, "environment")
        self.assertEqual(results[0].severity, "ERROR")
        self.assertIn("status=failed", evidence[-1].summary)

    def test_timeout_probe(self) -> None:
        timeout = replace(self.success, status="timeout", exit_code=None, timed_out=True)
        results, evidence = self.run_rules(executions=(timeout,))
        self.assertEqual(results[0].source, "rule:python_version_probe_failed")
        self.assertIn("timed_out=True", evidence[-1].summary)

    def test_nonzero_exit_triggers_failure_even_with_success_label(self) -> None:
        results, _ = self.run_rules(executions=(replace(self.success, exit_code=1),))
        self.assertEqual(results[0].source, "rule:python_version_probe_failed")

    def test_rejected_is_safety_information_not_project_failure(self) -> None:
        rejected = replace(
            self.success, command=replace(self.command, arguments=("main.py",)),
            status="rejected", exit_code=None,
        )
        results, _ = self.run_rules(executions=(rejected,))
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].category, "tool/safety")
        self.assertEqual(results[0].severity, "INFO")
        self.assertEqual(results[0].evidence_refs, ("execution:0",))

    def test_confirmation_is_evidence_only(self) -> None:
        pending = replace(self.success, status="requires_confirmation", exit_code=None)
        results, evidence = self.run_rules(executions=(pending,))
        self.assertEqual(results, [])
        self.assertIn("status=requires_confirmation", evidence[-1].summary)

    def test_every_diagnosis_ref_resolves_to_retained_evidence(self) -> None:
        results, evidence = self.run_rules(
            detection=DetectionResult("unknown", ()),
            environment=replace(self.environment, python_available=False),
            executions=(replace(self.success, status="failed", exit_code=1),),
        )
        references = {item.evidence_id for item in evidence}
        self.assertEqual(len(references), len(evidence))
        for result in results:
            self.assertTrue(result.evidence_refs)
            self.assertTrue(set(result.evidence_refs).issubset(references))
            self.assertTrue(result.source.startswith("rule:"))

    def test_failure_does_not_assert_a_specific_root_cause(self) -> None:
        failed = replace(self.success, status="failed", exit_code=1,
                         stderr="Python installation corrupted")
        results, _ = self.run_rules(executions=(failed,))
        self.assertEqual(results[0].probable_causes, ())
        self.assertNotIn("corrupted", results[0].problem)
        self.assertNotIn("install", " ".join(results[0].recommended_actions).lower())

    def test_multiple_independent_problems(self) -> None:
        results, _ = self.run_rules(
            detection=DetectionResult("unknown", ()),
            environment=replace(self.environment, python_available=False),
        )
        self.assertEqual(len(results), 2)
        self.assertEqual({result.source for result in results}, {
            "rule:python_detection_unknown", "rule:python_environment_unavailable",
        })

    def test_normal_inputs_produce_empty_problem_list(self) -> None:
        results, _ = self.run_rules(executions=(self.success,))
        self.assertEqual(results, [])

    def test_unrelated_command_failure_is_not_python_probe_failure(self) -> None:
        for command in (
            replace(self.command, arguments=("-m", "pytest")),
            replace(self.command, executable="other-python.exe"),
            replace(self.command, risk="CAUTION"),
        ):
            with self.subTest(command=command):
                failed = replace(self.success, command=command, status="failed", exit_code=1)
                results, evidence = self.run_rules(executions=(failed,))
                self.assertEqual(results, [])
                self.assertEqual(evidence[-1].evidence_id, "execution:0")

    def test_output_text_does_not_override_successful_structured_status(self) -> None:
        result = replace(self.success, stdout="Traceback", stderr="fatal import error")
        self.assertEqual(self.run_rules(executions=(result,))[0], [])

    def test_execution_evidence_refs_identify_input_indices(self) -> None:
        results, evidence = self.run_rules(executions=(
            self.success, replace(self.success, status="failed", exit_code=1),
        ))
        self.assertEqual(results[0].evidence_refs, ("execution:1",))
        self.assertEqual(evidence[-1].associated_id, "execution:1")

    def test_diagnosis_performs_no_file_or_process_operations(self) -> None:
        with patch.object(Path, "open", side_effect=AssertionError("file opened")):
            with patch.object(Path, "resolve", side_effect=AssertionError("path inspected")):
                with patch("subprocess.Popen", side_effect=AssertionError("process launched")):
                    results, _ = self.run_rules(executions=(self.success,))
        self.assertEqual(results, [])


if __name__ == "__main__":
    unittest.main()
