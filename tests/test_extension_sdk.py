"""Public SDK imports and explicit execution of the official synthetic Pack."""

import ast
import importlib
import os
import socket
import subprocess
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from agent_doctor import extensions, models, sdk
from agent_doctor.extension_pipeline import (
    BUILTIN_EXTENSIONS, merge_results, prepare_extensions, run_extension_stage,
)
from agent_doctor.extensions import ExtensionEnvironment, check_compatibility
from agent_doctor.project import inspect_environment, scan_project
from agent_doctor.python_extension import PythonCoreExtension
from agent_doctor.workflow import run_workflow
from examples.extensions.example_language_pack import ExampleLanguagePack


ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "examples" / "extensions" / "example_language_pack.py"
CONTRACT_NAMES = (
    "EXTENSION_API_VERSION", "Capability", "PackKind", "ExtensionMetadata",
    "Extension", "Detector", "EvidenceProvider", "DiagnosisRule", "RepairPlanner", "Verifier",
    "ExtensionUnavailable", "ExtensionIncompatible", "ExtensionFailure", "validate_pack", "validate_pack_id",
)
MODEL_NAMES = (
    "ProjectInfo", "DetectionResult", "EnvironmentInfo", "Evidence", "DiagnosisResult",
    "RootCauseStep", "RepairPlan", "RepairAction", "VerificationStep", "ExecutionResult", "CommandProposal",
)
DISCOVERY_NAMES = ("PACK_ENTRY_POINT_GROUP", "DiscoveryFailure", "DiscoveryResult", "discover_installed_packs")


class SDKExportTests(unittest.TestCase):
    def test_public_module_is_importable(self):
        self.assertIs(importlib.import_module("agent_doctor.sdk"), sdk)

    def test_all_is_explicit_and_contains_only_selected_names(self):
        self.assertEqual(set(sdk.__all__), set(CONTRACT_NAMES + MODEL_NAMES + ("PackRegistry",) + DISCOVERY_NAMES))
        self.assertEqual(len(sdk.__all__), len(set(sdk.__all__)))
        self.assertEqual({name for name in vars(sdk) if not name.startswith("_")}, set(sdk.__all__))

    def test_contracts_are_reexports_and_importable(self):
        from agent_doctor.sdk import (
            EXTENSION_API_VERSION, Capability, PackKind, ExtensionMetadata,
            Extension, Detector, EvidenceProvider, DiagnosisRule, RepairPlanner, Verifier,
        )
        imported = (EXTENSION_API_VERSION, Capability, PackKind, ExtensionMetadata,
                    Extension, Detector, EvidenceProvider, DiagnosisRule, RepairPlanner, Verifier)
        for name, value in zip(CONTRACT_NAMES[:10], imported):
            with self.subTest(name=name):
                self.assertIs(value, getattr(extensions, name))
        self.assertEqual(EXTENSION_API_VERSION, "1")

    def test_models_are_reexports_and_importable(self):
        from agent_doctor.sdk import (
            ProjectInfo, DetectionResult, EnvironmentInfo, Evidence, DiagnosisResult,
            RootCauseStep, RepairPlan, RepairAction, VerificationStep, ExecutionResult, CommandProposal,
        )
        imported = (ProjectInfo, DetectionResult, EnvironmentInfo, Evidence, DiagnosisResult,
                    RootCauseStep, RepairPlan, RepairAction, VerificationStep, ExecutionResult, CommandProposal)
        for name, value in zip(MODEL_NAMES, imported):
            with self.subTest(name=name):
                self.assertIs(value, getattr(models, name))

    def test_author_validation_and_errors_reuse_existing_contracts(self):
        for name in CONTRACT_NAMES[10:]:
            with self.subTest(name=name):
                self.assertIs(getattr(sdk, name), getattr(extensions, name))

    def test_privileged_and_internal_names_cannot_be_imported(self):
        for name in ("CommandExecutor", "Workflow", "PythonCoreExtension", "run_workflow",
                     "subprocess", "os", "shell", "startup", "format_report", "main",
                     "BUILTIN_EXTENSIONS", "prepare_extensions"):
            with self.subTest(name=name), self.assertRaises(ImportError):
                exec("from agent_doctor.sdk import " + name, {})

    def test_sdk_depends_only_on_contracts_models_registration_and_discovery(self):
        tree = ast.parse((ROOT / "src" / "agent_doctor" / "sdk.py").read_text(encoding="utf-8"))
        imports = [node for node in ast.walk(tree) if isinstance(node, (ast.Import, ast.ImportFrom))]
        self.assertTrue(imports)
        for node in imports:
            self.assertIsInstance(node, ast.ImportFrom)
            self.assertEqual(node.level, 1)
            self.assertIn(node.module, ("extensions", "models", "pack_registry", "pack_discovery"))
            if node.module == "pack_registry":
                self.assertEqual([alias.name for alias in node.names], ["PackRegistry"])
            if node.module == "pack_discovery":
                self.assertEqual([alias.name for alias in node.names], list(DISCOVERY_NAMES))
            self.assertNotIn("*", [alias.name for alias in node.names])


class ExamplePackTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.marker = self.root / ".reporescue-example"
        self.marker.write_text("SDK example only\n", encoding="utf-8")
        self.project = scan_project(self.root)
        self.environment = inspect_environment(self.project)
        self.descriptor = ExtensionEnvironment("windows", frozenset())
        self.pack = ExampleLanguagePack()

    def stages(self, *packs):
        runs = prepare_extensions(self.descriptor, packs)
        for stage in sdk.Capability:
            runs = run_extension_stage(runs, stage, self.project, self.environment)
        return runs

    def finding(self):
        evidence = self.pack.collect(self.project, self.environment)
        findings, _ = self.pack.diagnose(self.project, self.pack.detect(self.project), self.environment, evidence)
        return findings[0], evidence

    def test_example_imports_only_sdk_and_standard_library(self):
        tree = ast.parse(EXAMPLE.read_text(encoding="utf-8"))
        imports = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                self.assertEqual(node.level, 0)
                imports.append(node.module)
            elif isinstance(node, ast.Import):
                imports.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                self.assertNotIn(node.func.id, ("__import__", "eval", "exec", "compile"))
        self.assertIn("agent_doctor.sdk", imports)
        for name in imports:
            self.assertTrue(name == "agent_doctor.sdk" or name.split(".")[0] in sys.stdlib_module_names, name)
            self.assertNotIn(name.split(".")[0], ("subprocess", "socket", "os", "urllib", "http", "importlib"))

    def test_metadata_identifies_synthetic_language_pack(self):
        metadata = self.pack.metadata
        self.assertEqual(metadata.id, "example.language")
        self.assertEqual(self.pack.id, metadata.id)
        self.assertEqual(metadata.name, "Example Language Pack")
        self.assertEqual(metadata.version, "0.1")
        self.assertEqual(metadata.api_version, "1")
        self.assertIs(metadata.kind, sdk.PackKind.LANGUAGE)
        self.assertEqual(metadata.capabilities, frozenset(sdk.Capability))
        self.assertEqual(metadata.supported_platforms, ("windows",))
        self.assertEqual(metadata.required_tools, ())

    def test_example_id_is_valid(self):
        sdk.validate_pack_id(self.pack.id)
        sdk.validate_pack_id("example.marker-detected")

    def test_example_implements_existing_protocols(self):
        for contract in (sdk.Extension, sdk.Detector, sdk.EvidenceProvider,
                         sdk.DiagnosisRule, sdk.RepairPlanner, sdk.Verifier):
            with self.subTest(contract=contract):
                self.assertIsInstance(self.pack, contract)

    def test_example_passes_author_validation(self):
        sdk.validate_pack(self.pack)

    def test_example_is_compatible_without_external_tools(self):
        self.assertTrue(check_compatibility(self.pack.metadata, self.descriptor).compatible)

    def test_root_marker_is_detected_without_python_manifest(self):
        self.assertEqual(self.project.files, ())
        self.assertEqual(self.pack.detect(self.project), sdk.DetectionResult("likely", (".reporescue-example",)))

    def test_missing_marker_produces_no_example_results(self):
        self.marker.unlink()
        self.assertEqual(self.pack.detect(self.project), sdk.DetectionResult("unknown", ()))
        self.assertEqual(self.pack.collect(self.project, self.environment), ())
        result = merge_results(self.stages(self.pack))
        self.assertEqual((result.evidence, result.diagnoses, result.failures), ((), (), ()))

    def test_nested_marker_does_not_identify_the_root(self):
        self.marker.unlink()
        child = self.root / "child"
        child.mkdir()
        (child / self.marker.name).write_text("SDK example only", encoding="utf-8")
        self.assertEqual(self.pack.detect(self.project).level, "unknown")

    def test_directory_named_as_marker_is_not_a_marker_file(self):
        self.marker.unlink()
        self.marker.mkdir()
        self.assertEqual(self.pack.collect(self.project, self.environment), ())

    def test_evidence_has_stable_identity_and_relative_location(self):
        evidence, = self.pack.collect(self.project, self.environment)
        self.assertEqual(evidence.evidence_id, "example.language:marker")
        self.assertEqual(evidence.kind, "example_marker")
        self.assertEqual(evidence.source, "example.language")
        self.assertEqual(evidence.location, ".reporescue-example")
        self.assertEqual(evidence.metadata, {"marker": ".reporescue-example"})

    def test_diagnosis_is_info_and_references_marker_evidence(self):
        finding, evidence = self.finding()
        self.assertEqual(finding.severity, "INFO")
        self.assertEqual(finding.source, "rule:example.marker-detected")
        self.assertEqual(finding.diagnosis_id, "example.marker-detected")
        self.assertEqual(finding.evidence_refs, (evidence[0].evidence_id,))
        self.assertIsNone(finding.repair_plan)

    def test_diagnosis_requires_collected_example_evidence(self):
        findings, batch = self.pack.diagnose(self.project, self.pack.detect(self.project), self.environment, ())
        self.assertEqual((findings, batch), ((), ()))

    def test_diagnosis_preserves_the_complete_input_evidence_batch(self):
        own = self.pack.collect(self.project, self.environment)
        other = sdk.Evidence("other:fact", "other", "test.other", "Unrelated", metadata={"value": True})
        supplied = (other, *own)
        _, batch = self.pack.diagnose(self.project, self.pack.detect(self.project), self.environment, supplied)
        self.assertEqual(batch, supplied)
        self.assertIs(batch[0], other)
        self.assertEqual(other.metadata, {"value": True})

    def test_repair_is_descriptive_and_never_executed(self):
        finding, evidence = self.finding()
        plan = self.pack.plan(self.project, finding, evidence)
        self.assertEqual(plan.diagnosis_id, finding.diagnosis_id)
        self.assertEqual(plan.execution_status, "not_executed")
        self.assertEqual(plan.risk, "LOW")
        self.assertEqual(plan.actions[0].affected_paths, (".reporescue-example",))
        self.assertTrue(plan.actions[0].requires_confirmation)
        for name in ("apply", "execute", "install", "write", "run_shell"):
            self.assertFalse(hasattr(self.pack, name))

    def test_planner_does_not_plan_for_another_rule(self):
        finding, evidence = self.finding()
        self.assertIsNone(self.pack.plan(self.project, replace(finding, diagnosis_id="other.rule"), evidence))

    def test_verification_is_manual_and_not_run(self):
        finding, evidence = self.finding()
        plan = self.pack.plan(self.project, finding, evidence)
        steps = self.pack.build_verification(self.project, finding, plan)
        self.assertEqual(steps, plan.verification_steps)
        self.assertEqual(steps[0].type, "manual")
        self.assertEqual(steps[0].status, "not_run")
        self.assertEqual(self.pack.build_verification(self.project, finding, None), ())

    def test_existing_pipeline_runs_example_explicitly(self):
        result = merge_results(self.stages(self.pack))
        self.assertEqual(result.failures, ())
        self.assertEqual(len(result.diagnoses), 1)
        self.assertEqual(result.diagnoses[0].repair_plan.execution_status, "not_executed")

    def test_pipeline_order_and_python_results_are_preserved(self):
        descriptor = replace(self.descriptor, available_tools=frozenset({"python"}))
        def run(packs):
            runs = prepare_extensions(descriptor, packs)
            for stage in sdk.Capability:
                runs = run_extension_stage(runs, stage, self.project, self.environment)
            return runs
        python_only, = run((PythonCoreExtension(),))
        for packs in ((PythonCoreExtension(), self.pack), (self.pack, PythonCoreExtension())):
            with self.subTest(order=[pack.id for pack in packs]):
                runs = run(packs)
                python_run = next(item for item in runs if item.extension.id == "python.core")
                self.assertEqual(python_run.detection, python_only.detection)
                self.assertEqual(python_run.evidence, python_only.evidence)
                self.assertEqual(python_run.diagnoses, python_only.diagnoses)
                expected_sources = ["rule:python_detection_unknown", "rule:example.marker-detected"]
                if packs[0] is self.pack:
                    expected_sources.reverse()
                self.assertEqual([item.source for item in merge_results(runs).diagnoses],
                                 expected_sources)
                self.assertEqual([run.extension.id for run in runs], [pack.id for pack in packs])

    def test_repeated_runs_are_deterministic(self):
        self.assertEqual(merge_results(self.stages(self.pack)), merge_results(self.stages(self.pack)))

    def test_public_contract_errors_use_existing_pipeline_isolation(self):
        class UnavailableExample(ExampleLanguagePack):
            def collect(self, project, environment):
                raise sdk.ExtensionUnavailable(self.id, "Example observation unavailable")
        result = merge_results(self.stages(UnavailableExample()))
        self.assertEqual(result.failures[0].status, "unavailable")
        self.assertEqual(result.diagnoses, ())

    def test_example_has_no_process_network_or_mutation_side_effects(self):
        def snapshot():
            return {path.relative_to(self.root).as_posix():
                    (path.read_bytes(), path.stat().st_mtime_ns) if path.is_file() else None
                    for path in self.root.rglob("*")}
        before, environment_before = snapshot(), dict(os.environ)
        self.assertEqual(prepare_extensions(self.descriptor, (self.pack,))[0].command_proposals, ())
        with patch.object(subprocess, "run", side_effect=AssertionError("process")), \
                patch.object(subprocess, "Popen", side_effect=AssertionError("process")), \
                patch.object(os, "system", side_effect=AssertionError("shell")), \
                patch.object(socket, "socket", side_effect=AssertionError("network")), \
                patch.object(socket, "create_connection", side_effect=AssertionError("network")), \
                patch.object(Path, "write_text", side_effect=AssertionError("write")), \
                patch.object(Path, "write_bytes", side_effect=AssertionError("write")), \
                patch.object(Path, "unlink", side_effect=AssertionError("delete")), \
                patch("builtins.open", side_effect=AssertionError("file access")):
            self.stages(self.pack)
        self.assertEqual(snapshot(), before)
        self.assertEqual(dict(os.environ), environment_before)

    def test_pipeline_supplies_only_data_inputs(self):
        received = []
        class RecordingExample(ExampleLanguagePack):
            def diagnose(self, project, detection, environment, evidence, executions=()):
                received.extend((project, detection, environment, *evidence, *executions))
                return super().diagnose(project, detection, environment, evidence, executions)
        self.stages(RecordingExample())
        self.assertEqual([type(value) for value in received],
                         [sdk.ProjectInfo, sdk.DetectionResult, sdk.EnvironmentInfo, sdk.Evidence])
        for value in received:
            self.assertIsNot(value, os.environ)
            for name in ("execute", "shell", "executor", "subprocess", "write"):
                self.assertFalse(hasattr(value, name))

    def test_example_is_not_builtin_or_used_by_default_workflow(self):
        self.assertEqual([pack.id for pack in BUILTIN_EXTENSIONS], ["python.core"])
        with patch.object(ExampleLanguagePack, "detect", side_effect=AssertionError("example auto-loaded")):
            result = run_workflow(self.root)
        self.assertEqual(result.report["schema_version"], "0.2")
        self.assertFalse(any(item.source.startswith("rule:example.") for item in result.diagnostics))
        for name in ("sdk_version", "packs", "pack_kind", "rule_ids", "loaded_extensions"):
            self.assertNotIn(name, result.report)

    def test_official_demo_is_separate_and_contains_only_marker_and_readme(self):
        demo = ROOT / "examples" / "extension-demo"
        self.assertEqual({path.name for path in demo.iterdir()}, {".reporescue-example", "README.md"})
        self.assertIn("SDK example only", (demo / "README.md").read_text(encoding="utf-8"))
        self.assertEqual(self.pack.detect(scan_project(demo)).level, "likely")


if __name__ == "__main__":
    unittest.main()
