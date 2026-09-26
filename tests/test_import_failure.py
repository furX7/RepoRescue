import json
import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from agent_doctor.diagnosis import analyze_import_failure, diagnose
from agent_doctor.models import (
    CommandProposal,
    DetectionResult,
    EnvironmentInfo,
    Evidence,
    ExecutionResult,
    ProjectInfo,
)
from agent_doctor.report import build_json_report, render_terminal_report


class ImportFailureDiagnosisTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path("C:/example/project")
        self.project = ProjectInfo(
            root_path=self.root,
            scanned_at=datetime.now(),
            files=("main.py",),
        )
        self.detection = DetectionResult(level="likely", matched_files=("main.py",))
        self.environment = EnvironmentInfo(
            python_executable=Path("C:/Python/python.exe"),
            python_version="3.12.0",
            python_available=True,
            python_callable=True,
            dependency_manifests=(),
        )

    def execution(
        self,
        *,
        stderr: str = "",
        stdout: str = "",
        status: str = "failed",
        exit_code: int | None = 1,
    ) -> ExecutionResult:
        return ExecutionResult(
            command=CommandProposal(
                executable="C:/Python/python.exe",
                arguments=("app.py",),
                working_directory=self.root,
                source="test_fixture",
                risk="CAUTION",
                reason="Fixture representing previously captured project output",
            ),
            exit_code=exit_code,
            stdout=stdout,
            stderr=stderr,
            duration_seconds=0.1,
            status=status,
        )

    def import_diagnosis(self, execution: ExecutionResult):
        diagnoses, evidence = diagnose(
            self.project, self.detection, self.environment, (execution,)
        )
        import_diagnoses = [item for item in diagnoses if item.category == "python_import"]
        return import_diagnoses, evidence

    def test_single_quoted_missing_module_is_recognized(self) -> None:
        evidence = analyze_import_failure(
            self.execution(stderr="ModuleNotFoundError: No module named 'requests'"), 0
        )
        self.assertEqual(evidence.metadata["missing_module"], "requests")

    def test_double_quoted_missing_module_is_recognized(self) -> None:
        evidence = analyze_import_failure(
            self.execution(stderr='ModuleNotFoundError: No module named "requests"'), 0
        )
        self.assertEqual(evidence.metadata["missing_module"], "requests")

    def test_dotted_module_name_is_preserved(self) -> None:
        evidence = analyze_import_failure(
            self.execution(stderr="ModuleNotFoundError: No module named 'package.submodule'"), 0
        )
        self.assertEqual(evidence.metadata["missing_module"], "package.submodule")

    def test_symbol_import_failure_is_recognized_separately(self) -> None:
        evidence = analyze_import_failure(
            self.execution(stderr="ImportError: cannot import name 'Widget' from 'package.api'"), 0
        )
        self.assertEqual(evidence.metadata["status"], "symbol_import_failure")
        self.assertEqual(evidence.metadata["imported_symbol"], "Widget")
        self.assertEqual(evidence.metadata["source_module"], "package.api")
        self.assertNotIn("missing_module", evidence.metadata)

    def test_unrelated_import_error_is_not_classified(self) -> None:
        evidence = analyze_import_failure(
            self.execution(stderr="ImportError: DLL load failed while importing native"), 0
        )
        self.assertIsNone(evidence)

    def test_unrelated_stderr_does_not_create_import_diagnosis(self) -> None:
        diagnoses, _ = self.import_diagnosis(
            self.execution(stderr="application failed for an unrelated reason")
        )
        self.assertEqual(diagnoses, [])

    def test_stdout_is_supported_for_previously_captured_output(self) -> None:
        evidence = analyze_import_failure(
            self.execution(stdout="ModuleNotFoundError: No module named 'requests'"), 0
        )
        self.assertEqual(evidence.metadata["source_stream"], "stdout")

    def test_stderr_is_preferred_over_stdout(self) -> None:
        evidence = analyze_import_failure(
            self.execution(
                stderr="ModuleNotFoundError: No module named 'stderr_name'",
                stdout="ModuleNotFoundError: No module named 'stdout_name'",
            ),
            0,
        )
        self.assertEqual(evidence.metadata["missing_module"], "stderr_name")

    def test_last_explicit_match_in_stream_is_selected(self) -> None:
        evidence = analyze_import_failure(
            self.execution(
                stderr=(
                    "ModuleNotFoundError: No module named 'first'\n"
                    "ModuleNotFoundError: No module named 'last'"
                )
            ),
            0,
        )
        self.assertEqual(evidence.metadata["missing_module"], "last")

    def test_evidence_contains_only_the_error_line_and_structured_fields(self) -> None:
        raw = "ModuleNotFoundError: No module named 'requests'"
        evidence = analyze_import_failure(
            self.execution(stderr=f"Traceback (most recent call last):\n  frame\n{raw}\n"), 3
        )
        self.assertEqual(evidence.evidence_id, "execution:3:python_import")
        self.assertEqual(evidence.associated_id, "execution:3")
        self.assertEqual(
            evidence.metadata,
            {
                "exception_type": "ModuleNotFoundError",
                "missing_module": "requests",
                "source_stream": "stderr",
                "raw_message": raw,
                "status": "missing_module",
            },
        )

    def test_missing_module_creates_error_diagnosis(self) -> None:
        diagnoses, _ = self.import_diagnosis(
            self.execution(stderr="ModuleNotFoundError: No module named 'requests'")
        )
        self.assertEqual(len(diagnoses), 1)
        self.assertEqual(diagnoses[0].severity, "ERROR")
        self.assertEqual(diagnoses[0].category, "python_import")
        self.assertIn("requests", diagnoses[0].problem)

    def test_symbol_failure_is_not_called_missing_dependency(self) -> None:
        diagnoses, _ = self.import_diagnosis(
            self.execution(stderr="ImportError: cannot import name 'Widget' from 'package'")
        )
        self.assertEqual(len(diagnoses), 1)
        combined = repr(diagnoses[0]).lower()
        self.assertNotIn("missing dependency", combined)
        self.assertNotIn("missing_module", combined)

    def test_diagnosis_and_root_steps_reference_existing_evidence(self) -> None:
        diagnoses, evidence = self.import_diagnosis(
            self.execution(stderr="ModuleNotFoundError: No module named 'requests'")
        )
        evidence_ids = {item.evidence_id for item in evidence}
        diagnosis = diagnoses[0]
        self.assertTrue(set(diagnosis.evidence_refs) <= evidence_ids)
        self.assertGreaterEqual(len(diagnosis.root_cause_chain), 1)
        for step in diagnosis.root_cause_chain:
            self.assertTrue(set(step.evidence_refs) <= evidence_ids)

    def test_root_cause_chain_stays_with_observed_facts(self) -> None:
        diagnoses, _ = self.import_diagnosis(
            self.execution(stderr="ModuleNotFoundError: No module named 'requests'")
        )
        chain = " ".join(
            f"{step.title} {step.explanation}" for step in diagnoses[0].root_cause_chain
        ).lower()
        self.assertNotIn("installation is damaged", chain)
        self.assertNotIn("path is broken", chain)
        self.assertNotIn("requests distribution is missing", chain)

    def test_repair_preview_is_medium_risk_and_never_executes(self) -> None:
        diagnoses, _ = self.import_diagnosis(
            self.execution(stderr="ModuleNotFoundError: No module named 'requests'")
        )
        plan = diagnoses[0].repair_plan
        self.assertEqual(plan.risk, "MEDIUM")
        self.assertTrue(all(action.requires_confirmation for action in plan.actions))
        self.assertTrue(all(step.status == "not_run" for step in plan.verification_steps))

    def test_repair_preview_has_verification_steps(self) -> None:
        diagnoses, _ = self.import_diagnosis(
            self.execution(stderr="ModuleNotFoundError: No module named 'requests'")
        )
        self.assertGreaterEqual(len(diagnoses[0].repair_plan.verification_steps), 1)

    def test_repair_preview_contains_no_pip_command(self) -> None:
        diagnoses, _ = self.import_diagnosis(
            self.execution(stderr="ModuleNotFoundError: No module named 'requests'")
        )
        self.assertNotIn("pip install", repr(diagnoses[0].repair_plan).lower())

    def test_no_import_to_distribution_mapping_is_guessed(self) -> None:
        mappings = {"PIL": "Pillow", "yaml": "PyYAML", "cv2": "opencv-python"}
        for imported_name, distribution_name in mappings.items():
            with self.subTest(imported_name=imported_name):
                diagnoses, _ = self.import_diagnosis(
                    self.execution(
                        stderr=f"ModuleNotFoundError: No module named '{imported_name}'"
                    )
                )
                self.assertNotIn(distribution_name.lower(), repr(diagnoses[0]).lower())

    def test_import_diagnosis_can_coexist_with_other_diagnoses(self) -> None:
        requirement = Evidence(
            evidence_id="python_requirement:pyproject",
            kind="python_requirement",
            source="python_plugin",
            summary="incompatible",
            metadata={
                "status": "incompatible",
                "declared_python_requirement": ">=3.13",
                "current_python_version": "3.12.0",
            },
        )
        local = Evidence(
            evidence_id="local_environment:venv",
            kind="local_environment",
            source="python_plugin",
            summary="different",
            metadata={
                "status": "different",
                "detected_local_environment": "venv",
                "detected_interpreter_path": "C:/example/project/venv/Scripts/python.exe",
                "current_python_executable": "C:/Python/python.exe",
            },
        )
        diagnoses, _ = diagnose(
            self.project,
            self.detection,
            self.environment,
            (self.execution(stderr="ModuleNotFoundError: No module named 'requests'"),),
            python_requirement=requirement,
            local_environment=local,
        )
        self.assertEqual(
            {item.category for item in diagnoses},
            {"python_import", "python_version", "python_environment"},
        )

    def test_json_preserves_import_evidence_and_preview(self) -> None:
        diagnoses, evidence = self.import_diagnosis(
            self.execution(stderr="ModuleNotFoundError: No module named 'requests'")
        )
        payload = json.loads(
            json.dumps(build_json_report(
                self.project, self.detection, self.environment, diagnoses, evidence
            ))
        )
        self.assertEqual(payload["schema_version"], "0.2")
        item = next(item for item in payload["diagnostics"] if item["category"] == "python_import")
        self.assertIn("root_cause_chain", item)
        self.assertIn("repair_plan", item)
        import_evidence = next(
            item for item in payload["evidence"] if item["kind"] == "python_import_failure"
        )
        self.assertEqual(import_evidence["metadata"]["missing_module"], "requests")

    def test_terminal_report_shows_concise_import_preview(self) -> None:
        diagnoses, evidence = self.import_diagnosis(
            self.execution(stderr="ModuleNotFoundError: No module named 'requests'")
        )
        report = render_terminal_report(
            self.project, self.detection, self.environment, diagnoses, evidence=evidence
        )
        self.assertIn("Python import", report)
        self.assertIn("requests", report)
        self.assertIn("Root cause:", report)
        self.assertIn("Suggested repair", report)
        self.assertIn("Verification", report)

    def test_normal_execution_has_no_import_preview(self) -> None:
        diagnoses, evidence = self.import_diagnosis(
            self.execution(status="success", exit_code=0, stdout="application output")
        )
        self.assertEqual(diagnoses, [])
        self.assertFalse(any(item.kind == "python_import_failure" for item in evidence))

    def test_rejected_output_is_not_analyzed_as_a_project_failure(self) -> None:
        diagnoses, evidence = self.import_diagnosis(
            self.execution(
                status="rejected",
                exit_code=None,
                stderr="ModuleNotFoundError: No module named 'requests'",
            )
        )
        self.assertFalse(any(item.category == "python_import" for item in diagnoses))
        self.assertFalse(any(item.kind == "python_import_failure" for item in evidence))

    def test_successful_execution_does_not_treat_exception_shaped_log_as_failure(self) -> None:
        diagnoses, evidence = self.import_diagnosis(
            self.execution(
                status="success",
                exit_code=0,
                stderr="ModuleNotFoundError: No module named 'text_in_a_log'",
            )
        )
        self.assertFalse(any(item.category == "python_import" for item in diagnoses))
        self.assertFalse(any(item.kind == "python_import_failure" for item in evidence))

    def test_terminal_report_labels_symbol_and_source_module(self) -> None:
        diagnoses, evidence = self.import_diagnosis(
            self.execution(stderr="ImportError: cannot import name 'Widget' from 'package.api'")
        )
        report = render_terminal_report(
            self.project, self.detection, self.environment, diagnoses, evidence=evidence
        )
        self.assertIn("Import module: package.api", report)
        self.assertIn("Import symbol: Widget", report)
        self.assertNotIn("missing dependency", report.lower())

    def test_analysis_does_not_mutate_target_or_environment(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            target = root / "main.py"
            target.write_text("print('unchanged')\n", encoding="utf-8")
            before = (target.read_bytes(), target.stat().st_mtime_ns, sorted(root.iterdir()))
            environment_before = dict(os.environ)
            project = ProjectInfo(
                root_path=root,
                scanned_at=datetime.now(),
                files=("main.py",),
            )
            execution = self.execution(
                stderr="ModuleNotFoundError: No module named 'requests'"
            )
            diagnose(project, self.detection, self.environment, (execution,))
            after = (target.read_bytes(), target.stat().st_mtime_ns, sorted(root.iterdir()))
            self.assertEqual(before, after)
            self.assertEqual(environment_before, dict(os.environ))


    def test_symbol_import_with_standard_location_suffix(self) -> None:
        evidence = analyze_import_failure(
            self.execution(stderr="ImportError: cannot import name 'Widget' from 'package.api' (C:\\project\\package\\api.py)"), 0
        )
        self.assertEqual(evidence.metadata['status'], 'symbol_import_failure')
        self.assertEqual(evidence.metadata['source_module'], 'package.api')
        self.assertEqual(evidence.metadata['imported_symbol'], 'Widget')
        self.assertNotIn('missing_module', evidence.metadata)


    def test_symbol_import_with_location_and_python_suggestion(self) -> None:
        evidence = analyze_import_failure(
            self.execution(stderr="ImportError: cannot import name 'missing_symbol' from 'helper' (C:\\project\\helper.py). Did you mean: 'existing_symbol'?"), 0
        )
        self.assertEqual(evidence.metadata['imported_symbol'], 'missing_symbol')
        self.assertNotIn('existing_symbol', repr(evidence.metadata.get('imported_symbol')))


if __name__ == "__main__":
    unittest.main()
