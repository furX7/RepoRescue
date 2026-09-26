"""Contract implementations, compatibility, and unchanged Python boundaries."""

import io
import os
import tempfile
import tomllib
import unittest
from collections.abc import Sequence
from contextlib import redirect_stdout
from dataclasses import FrozenInstanceError, replace
from pathlib import Path
from unittest.mock import patch

from agent_doctor import __version__
from agent_doctor.cli import main
from agent_doctor.diagnosis import diagnose
from agent_doctor.extensions import (
    Capability, CompatibilityStatus, Detector, DiagnosisRule, EvidenceProvider,
    EXTENSION_API_VERSION, Extension, ExtensionEnvironment, ExtensionError,
    ExtensionFailure, ExtensionIncompatible, ExtensionMetadata,
    ExtensionUnavailable, RepairPlanner, Verifier, check_compatibility,
)
from agent_doctor.models import (
    CommandProposal, DetectionResult, DiagnosisResult, EnvironmentInfo, Evidence,
    ExecutionResult, ProjectInfo, RepairAction, RepairPlan, RootCauseStep,
    VerificationStep,
)
from agent_doctor.project import inspect_environment, scan_project
from agent_doctor.python_extension import PythonCoreExtension
from agent_doctor.python_plugin import (
    detect_python_project, inspect_local_python_environment,
    inspect_python_requirement,
)
from agent_doctor.startup import collect_startup_evidence, propose_startup_probe
from agent_doctor.workflow import run_workflow


class FakeLanguageExtension:
    """Test-only language knowledge; no filesystem or execution operations."""

    metadata = ExtensionMetadata(
        id="test.fake", name="Fake Language Pack", version="0.1",
        api_version=EXTENSION_API_VERSION, capabilities=frozenset(Capability),
        supported_platforms=("windows",), required_tools=("fake-tool",),
    )
    id = metadata.id

    def detect(self, project: ProjectInfo) -> DetectionResult:
        matched = tuple(name for name in project.files if name.endswith(".fake"))
        return DetectionResult("likely" if matched else "unknown", matched)

    def collect(
        self, project: ProjectInfo, environment: EnvironmentInfo,
    ) -> tuple[Evidence, ...]:
        return (Evidence("fake:markers", "fake_markers", self.id,
                         "Markers in the supplied project snapshot",
                         metadata={"files": self.detect(project).matched_files}),)

    def diagnose(
        self, project: ProjectInfo, detection: DetectionResult,
        environment: EnvironmentInfo, evidence: Sequence[Evidence],
        executions: Sequence[ExecutionResult] = (),
    ) -> tuple[Sequence[DiagnosisResult], Sequence[Evidence]]:
        if detection.level == "likely":
            return (), tuple(evidence)
        references = (evidence[0].evidence_id,)
        finding = DiagnosisResult(
            "No fake markers in snapshot", "fake_detection", "WARNING", 0.5,
            self.id, evidence_refs=references, diagnosis_id="fake:unknown",
            root_cause_chain=(RootCauseStep(
                "fake:no_markers", "No markers", "The snapshot has no .fake files",
                references,
            ),),
        )
        return (finding,), tuple(evidence)

    def plan(
        self, project: ProjectInfo, diagnosis: DiagnosisResult,
        evidence: Sequence[Evidence],
    ) -> RepairPlan | None:
        return RepairPlan(
            "fake:preview", diagnosis.diagnosis_id, "Review project selection", "LOW",
            (RepairAction("fake:review", "Review the selected root", (), True, True),),
            (VerificationStep("fake:rescan", "Review a new snapshot", "file_check"),),
        )

    def build_verification(
        self, project: ProjectInfo, diagnosis: DiagnosisResult,
        repair_plan: RepairPlan | None,
    ) -> tuple[VerificationStep, ...]:
        return () if repair_plan is None else repair_plan.verification_steps


class ExtensionContractTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.project = scan_project(self.root)
        self.environment = inspect_environment(self.project)
        self.fake = FakeLanguageExtension()

    def fake_finding(self):
        evidence = self.fake.collect(self.project, self.environment)
        findings, _ = self.fake.diagnose(
            self.project, self.fake.detect(self.project), self.environment, evidence,
        )
        return findings[0], evidence

    def test_detector_can_be_implemented_independently(self):
        class MarkersOnly:
            id = "test.markers"

            def detect(self, project: ProjectInfo) -> DetectionResult:
                return DetectionResult("unknown", ())

        detector: Detector = MarkersOnly()
        self.assertIsInstance(detector, Detector)
        self.assertNotIsInstance(detector, EvidenceProvider)
        self.assertEqual(detector.detect(self.project), DetectionResult("unknown", ()))

    def test_evidence_provider_protocol(self):
        provider: EvidenceProvider = self.fake
        self.assertIsInstance(provider, EvidenceProvider)
        evidence = provider.collect(self.project, self.environment)
        self.assertEqual(evidence[0].metadata["files"], ())

    def test_diagnosis_rule_protocol_and_evidence_references(self):
        rule: DiagnosisRule = self.fake
        self.assertIsInstance(rule, DiagnosisRule)
        findings, evidence = rule.diagnose(
            self.project, self.fake.detect(self.project), self.environment,
            self.fake.collect(self.project, self.environment),
        )
        self.assertEqual(findings[0].evidence_refs, (evidence[0].evidence_id,))
        self.assertEqual(findings[0].root_cause_chain[0].evidence_refs,
                         findings[0].evidence_refs)

    def test_repair_planner_protocol_is_preview_only(self):
        planner: RepairPlanner = self.fake
        self.assertIsInstance(planner, RepairPlanner)
        finding, evidence = self.fake_finding()
        plan = planner.plan(self.project, finding, evidence)
        self.assertEqual(plan.diagnosis_id, finding.diagnosis_id)
        self.assertEqual(plan.execution_status, "not_executed")
        self.assertTrue(plan.actions[0].requires_confirmation)

    def test_verifier_protocol_never_executes(self):
        verifier: Verifier = self.fake
        self.assertIsInstance(verifier, Verifier)
        finding, evidence = self.fake_finding()
        plan = self.fake.plan(self.project, finding, evidence)
        steps = verifier.build_verification(self.project, finding, plan)
        self.assertEqual(steps, plan.verification_steps)
        self.assertTrue(all(step.status == "not_run" for step in steps))

    def test_extension_metadata_protocol(self):
        extension: Extension = self.fake
        self.assertIsInstance(extension, Extension)
        metadata = extension.metadata
        self.assertEqual((metadata.id, metadata.name, metadata.version, metadata.api_version),
                         ("test.fake", "Fake Language Pack", "0.1", "1"))

    def test_capabilities_have_only_knowledge_stages(self):
        self.assertEqual({item.value for item in Capability},
                         {"detect", "inspect", "diagnose", "plan_repair", "verify"})
        self.assertIn(Capability.DETECT, self.fake.metadata.capabilities)
        self.assertNotIn("detect", self.fake.metadata.capabilities)
        self.assertIsInstance(self.fake.metadata.capabilities, frozenset)

    def test_metadata_rejects_arbitrary_capability_strings(self):
        with self.assertRaises(TypeError):
            replace(self.fake.metadata, capabilities=frozenset({"shell"}))
        with self.assertRaises(TypeError):
            replace(self.fake.metadata, capabilities={Capability.DETECT})

    def test_platforms_and_required_tools_are_declared(self):
        self.assertEqual(self.fake.metadata.supported_platforms, ("windows",))
        self.assertEqual(self.fake.metadata.required_tools, ("fake-tool",))

    def test_metadata_and_environment_are_immutable(self):
        descriptor = ExtensionEnvironment("windows", frozenset({"fake-tool"}))
        with self.assertRaises(FrozenInstanceError):
            self.fake.metadata.version = "2"
        with self.assertRaises(FrozenInstanceError):
            descriptor.platform = "linux"

    def test_api_package_and_json_versions_are_separate(self):
        self.assertEqual(EXTENSION_API_VERSION, "1")
        self.assertNotEqual(EXTENSION_API_VERSION, __version__)
        self.assertNotEqual(EXTENSION_API_VERSION, "0.2")
        self.assertEqual(self.fake.metadata.version, "0.1")
        self.assertEqual(ExtensionEnvironment("windows", frozenset()).api_version, "1")

    def test_fake_stages_cannot_execute_or_modify_project(self):
        target = self.root / "keep.txt"
        target.write_bytes(b"unchanged")

        def snapshot():
            return {path.relative_to(self.root).as_posix():
                    (path.read_bytes(), path.stat().st_mtime_ns) if path.is_file() else None
                    for path in self.root.rglob("*")}

        before, environment_before = snapshot(), dict(os.environ)
        with patch("subprocess.run", side_effect=AssertionError("no process")), \
                patch("subprocess.Popen", side_effect=AssertionError("no process")):
            finding, evidence = self.fake_finding()
            plan = self.fake.plan(self.project, finding, evidence)
            self.fake.build_verification(self.project, finding, plan)
        self.assertEqual(snapshot(), before)
        self.assertEqual(dict(os.environ), environment_before)

    def test_stage_inputs_expose_snapshots_not_executor(self):
        # Exercise the complete fake through the published signatures: no
        # service handle or arbitrary executor is needed or provided.
        finding, evidence = self.fake_finding()
        plan = self.fake.plan(self.project, finding, evidence)
        self.assertTrue(self.fake.build_verification(self.project, finding, plan))
        for value in (self.project, self.environment, finding, plan, *evidence):
            self.assertFalse(hasattr(value, "execute"))
            self.assertFalse(hasattr(value, "executor"))
            self.assertFalse(hasattr(value, "shell"))

    def test_extension_error_semantics_are_local(self):
        for error_type in (ExtensionUnavailable, ExtensionIncompatible, ExtensionFailure):
            with self.subTest(error_type=error_type):
                error = error_type("test.fake", "stage unavailable")
                self.assertIsInstance(error, ExtensionError)
                self.assertEqual(error.extension_id, "test.fake")
                self.assertEqual(str(error), "stage unavailable")


class CompatibilityTests(unittest.TestCase):
    def setUp(self):
        self.metadata = FakeLanguageExtension.metadata
        self.environment = ExtensionEnvironment("windows", frozenset({"fake-tool"}))

    def test_compatible(self):
        result = check_compatibility(self.metadata, self.environment)
        self.assertTrue(result.compatible)
        self.assertEqual(result.status, CompatibilityStatus.COMPATIBLE)
        self.assertEqual(result.missing_tools, ())

    def test_api_version_mismatch(self):
        result = check_compatibility(replace(self.metadata, api_version="2"), self.environment)
        self.assertFalse(result.compatible)
        self.assertEqual(result.status, CompatibilityStatus.API_VERSION_MISMATCH)

    def test_unsupported_platform(self):
        result = check_compatibility(self.metadata, replace(self.environment, platform="linux"))
        self.assertEqual(result.status, CompatibilityStatus.UNSUPPORTED_PLATFORM)
        self.assertFalse(result.compatible)

    def test_missing_required_tools_are_reported_in_declaration_order(self):
        metadata = replace(self.metadata, required_tools=("first", "fake-tool", "last"))
        result = check_compatibility(metadata, self.environment)
        self.assertEqual(result.status, CompatibilityStatus.MISSING_REQUIRED_TOOL)
        self.assertEqual(result.missing_tools, ("first", "last"))
        self.assertFalse(result.compatible)

    def test_no_required_tools(self):
        result = check_compatibility(replace(self.metadata, required_tools=()),
                                     replace(self.environment, available_tools=frozenset()))
        self.assertTrue(result.compatible)

    def test_empty_platforms_do_not_mean_all_platforms(self):
        result = check_compatibility(replace(self.metadata, supported_platforms=()), self.environment)
        self.assertEqual(result.status, CompatibilityStatus.UNSUPPORTED_PLATFORM)

    def test_failure_precedence(self):
        metadata = replace(self.metadata, api_version="2")
        environment = ExtensionEnvironment("linux", frozenset())
        self.assertEqual(check_compatibility(metadata, environment).status,
                         CompatibilityStatus.API_VERSION_MISMATCH)
        self.assertEqual(check_compatibility(self.metadata, environment).status,
                         CompatibilityStatus.UNSUPPORTED_PLATFORM)

    def test_core_api_version_is_supplied_separately(self):
        result = check_compatibility(self.metadata, replace(self.environment, api_version="next"))
        self.assertEqual(result.status, CompatibilityStatus.API_VERSION_MISMATCH)

    def test_checker_uses_supplied_facts_without_discovery(self):
        with patch("shutil.which", side_effect=AssertionError("no tool scan")), \
                patch("subprocess.run", side_effect=AssertionError("no process")), \
                patch("socket.create_connection", side_effect=AssertionError("no network")):
            self.assertTrue(check_compatibility(self.metadata, self.environment).compatible)


class PythonExtensionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.pack = PythonCoreExtension()

    def test_python_pack_implements_all_stage_contracts(self):
        for contract in (Extension, Detector, EvidenceProvider, DiagnosisRule, RepairPlanner, Verifier):
            with self.subTest(contract=contract):
                self.assertIsInstance(self.pack, contract)
        self.assertEqual(self.pack.id, "python.core")
        self.assertEqual(self.pack.metadata.capabilities, frozenset(Capability))
        self.assertTrue(check_compatibility(
            self.pack.metadata, ExtensionEnvironment("windows", frozenset({"python"})),
        ).compatible)

    def test_adapter_matches_existing_detection_and_inspection(self):
        (self.root / "pyproject.toml").write_text("[project]\nrequires-python='>=999.0'\n",
                                                encoding="utf-8")
        interpreter = self.root / ".venv" / "Scripts" / "python.exe"
        interpreter.parent.mkdir(parents=True)
        interpreter.write_bytes(b"not executed")
        project = scan_project(self.root)
        environment = inspect_environment(project)
        self.assertEqual(self.pack.detect(project), detect_python_project(project))
        self.assertEqual(self.pack.collect(project, environment), (
            inspect_local_python_environment(project, environment),
            inspect_python_requirement(project, environment),
        ))

    def test_adapter_omits_absent_requirement(self):
        project = scan_project(self.root)
        environment = inspect_environment(project)
        self.assertEqual(self.pack.collect(project, environment),
                         (inspect_local_python_environment(project, environment),))

    def test_adapter_preserves_all_existing_rules_and_previews(self):
        (self.root / "main.py").write_bytes(b"must not execute")
        (self.root / "pyproject.toml").write_text("[project]\nrequires-python='>=999.0'\n",
                                                encoding="utf-8")
        interpreter = self.root / ".venv" / "Scripts" / "python.exe"
        interpreter.parent.mkdir(parents=True)
        interpreter.write_bytes(b"must not execute")
        project = scan_project(self.root)
        environment = inspect_environment(project)
        detection = self.pack.detect(project)
        command, startup = propose_startup_probe(project, environment)
        outputs = (
            ("failed", 1, "ModuleNotFoundError: No module named 'missing_example'", False),
            ("failed", 1, "ImportError: cannot import name 'missing_symbol' from 'helper'", False),
            ("failed", 1, "RuntimeError: fixture failure", False),
            ("timeout", None, "", True),
        )
        for status, code, stderr, timed_out in outputs:
            with self.subTest(stderr=stderr, status=status):
                execution = ExecutionResult(command, code, "", stderr, 0.01,
                                            status=status, timed_out=timed_out, terminated=timed_out)
                startup_evidence = collect_startup_evidence(startup, execution)
                evidence = (*self.pack.collect(project, environment), startup_evidence)
                before = [dict(item.metadata) for item in evidence]
                expected = diagnose(
                    project, detection, environment, (execution,),
                    local_environment=evidence[0], python_requirement=evidence[1],
                    startup_probe=startup_evidence,
                )
                with patch("subprocess.run", side_effect=AssertionError("no process")), \
                        patch("subprocess.Popen", side_effect=AssertionError("no process")):
                    actual = self.pack.diagnose(project, detection, environment, evidence, (execution,))
                self.assertEqual(actual, expected)
                self.assertEqual([dict(item.metadata) for item in evidence], before)
                self.assertIn("python_version", {item.category for item in actual[0]})
                self.assertIn("python_environment", {item.category for item in actual[0]})
                if stderr.startswith(("ModuleNotFoundError", "ImportError")):
                    self.assertIn("python_import", {item.category for item in actual[0]})
                for finding in actual[0]:
                    plan = self.pack.plan(project, finding, actual[1])
                    self.assertIs(plan, finding.repair_plan)
                    steps = self.pack.build_verification(project, finding, plan)
                    self.assertEqual(steps, () if plan is None else plan.verification_steps)

    def test_adapter_preserves_version_probe_failure_and_safety_observations(self):
        project = scan_project(self.root)
        environment = inspect_environment(project)
        command = CommandProposal(str(environment.python_executable), ("--version",),
                                  self.root, "python_plugin", "version", "SAFE")
        detection = self.pack.detect(project)
        for status in ("rejected", "requires_confirmation", "failed", "timeout"):
            with self.subTest(status=status):
                execution = ExecutionResult(command, None, "", "", 0, status=status,
                                            timed_out=status == "timeout")
                self.assertEqual(
                    self.pack.diagnose(project, detection, environment, (), (execution,)),
                    diagnose(project, detection, environment, (execution,)),
                )

    def test_official_workflow_invokes_facade_only_static_builtins(self):
        detect = PythonCoreExtension.detect
        with patch.object(PythonCoreExtension, "detect", autospec=True, side_effect=detect) as called, \
                patch.object(FakeLanguageExtension, "detect", side_effect=AssertionError("fake invoked")):
            result = run_workflow(self.root)
        called.assert_called_once()
        self.assertEqual(result.detection.level, "unknown")
        self.assertEqual(result.report["schema_version"], "0.2")
        self.assertNotIn("extensions", result.report)
        self.assertEqual(result.report["capabilities"], {
            "diagnosis": True, "root_cause_analysis": True, "repair_preview": True,
            "repair_execution": False, "verification_plan": True,
            "verification_execution": False, "rollback": False,
        })

    def test_cli_help_version_and_default_path_remain_unchanged(self):
        for option in ("--help", "--version"):
            with self.subTest(option=option):
                output = io.StringIO()
                with redirect_stdout(output), self.assertRaises(SystemExit) as stopped:
                    main([option])
                self.assertEqual(stopped.exception.code, 0)
                if option == "--help":
                    self.assertIn("--run-startup-probe", output.getvalue())
                    for forbidden in ("--plugin", "--extension", "--list-plugins", "test.fake"):
                        self.assertNotIn(forbidden, output.getvalue())
                else:
                    self.assertIn(__version__, output.getvalue())
        detect = PythonCoreExtension.detect
        with redirect_stdout(io.StringIO()), patch.object(
            PythonCoreExtension, "detect", autospec=True, side_effect=detect,
        ) as called:
            self.assertEqual(main([str(self.root)]), 0)
        called.assert_called_once()

    def test_runtime_dependencies_remain_empty(self):
        path = Path(__file__).resolve().parents[1] / "pyproject.toml"
        configuration = tomllib.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(configuration["project"]["dependencies"], [])


if __name__ == "__main__":
    unittest.main()
