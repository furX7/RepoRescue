"""Pack classification, declarations, composition and Core authority boundary."""

import json
import os
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from agent_doctor.extension_pipeline import (
    BUILTIN_EXTENSIONS, merge_results, prepare_extensions, run_extension_stage,
)
from agent_doctor.extensions import (
    Capability, CompatibilityStatus, DiagnosisRule, EXTENSION_API_VERSION,
    ExtensionEnvironment, ExtensionMetadata, PackKind, RepairPlanner, Verifier,
    validate_pack, validate_pack_id,
)
from agent_doctor.models import (
    DetectionResult, DiagnosisResult, Evidence, RepairAction, RepairPlan,
    RootCauseStep, VerificationStep,
)
from agent_doctor.project import inspect_environment, scan_project
from agent_doctor.python_extension import PythonCoreExtension
from agent_doctor.workflow import WorkflowError, run_workflow


class FakeDiagnosisRule:
    def __init__(self, identifier):
        self.id = identifier

    def diagnose(self, project, detection, environment, evidence, executions=()):
        references = (evidence[0].evidence_id,)
        finding = DiagnosisResult(
            "Review fake markers", "fake", "WARNING", 0.5, "rule:" + self.id,
            evidence_refs=references, diagnosis_id=self.id + ":finding",
            root_cause_chain=(RootCauseStep(
                self.id + ":observation", "Fake markers", "Review the supplied snapshot", references,
            ),),
        )
        return (finding,), tuple(evidence)


class FakeRepairPlanner:
    def __init__(self, identifier):
        self.id = identifier

    def plan(self, project, diagnosis, evidence):
        return RepairPlan(
            diagnosis.diagnosis_id + ":preview", diagnosis.diagnosis_id, "Review only", "LOW",
            (RepairAction(self.id + ":review", "Review the selected project", (), True, True),),
            (VerificationStep(self.id + ":initial", "Review again", "manual"),),
        )


class FakeVerifier:
    def __init__(self, identifier):
        self.id = identifier

    def build_verification(self, project, diagnosis, repair_plan):
        return (VerificationStep(diagnosis.diagnosis_id + ":verify", "Review a new snapshot", "file_check"),)


class FakeLanguagePack:
    """Test-only composition; there is no second Pack execution framework."""

    def __init__(self, identifier="test.fake-language", kind=PackKind.LANGUAGE):
        self.metadata = ExtensionMetadata(
            identifier, "Fake Pack", "0.1", "1", frozenset(Capability), ("windows",), kind=kind,
        )
        self.diagnosis_rules = (FakeDiagnosisRule(identifier + ".markers"),)
        self.repair_planners = (FakeRepairPlanner(identifier + ".review"),)
        self.verifiers = (FakeVerifier(identifier + ".rescan"),)
        self.received_inputs = []

    @property
    def id(self):
        return self.metadata.id

    def detect(self, project):
        self.received_inputs.append((project,))
        matched = tuple(name for name in project.files if name.endswith(".fake"))
        return DetectionResult("likely" if matched else "unknown", matched)

    def collect(self, project, environment):
        self.received_inputs.append((project, environment))
        return (Evidence(self.id + ":markers", "fake_markers", self.id, "Supplied filenames",
                         metadata={"files": project.files}),)

    def diagnose(self, project, detection, environment, evidence, executions=()):
        self.received_inputs.append((project, detection, environment, evidence, executions))
        findings = []
        batch = tuple(evidence)
        for rule in self.diagnosis_rules:
            diagnoses, batch = rule.diagnose(project, detection, environment, batch, executions)
            findings.extend(diagnoses)
        return tuple(findings), tuple(batch)

    def plan(self, project, diagnosis, evidence):
        self.received_inputs.append((project, diagnosis, evidence))
        return self.repair_planners[0].plan(project, diagnosis, evidence)

    def build_verification(self, project, diagnosis, repair_plan):
        self.received_inputs.append((project, diagnosis, repair_plan))
        return self.verifiers[0].build_verification(project, diagnosis, repair_plan)


class FakeToolchainPack(FakeLanguagePack):
    def __init__(self):
        super().__init__("test.fake-toolchain", PackKind.TOOLCHAIN)


class PackMetadataTests(unittest.TestCase):
    def setUp(self):
        self.metadata = FakeLanguagePack().metadata

    def test_five_pack_kinds(self):
        self.assertEqual({member.name for member in PackKind},
                         {"LANGUAGE", "TOOLCHAIN", "FRAMEWORK", "ENVIRONMENT", "INTEGRATION"})

    def test_python_is_the_builtin_language_pack(self):
        pack = BUILTIN_EXTENSIONS[0]
        self.assertIsInstance(pack, PythonCoreExtension)
        self.assertEqual(pack.id, "python.core")
        self.assertIs(pack.metadata.kind, PackKind.LANGUAGE)
        self.assertEqual(pack.metadata.api_version, EXTENSION_API_VERSION)

    def test_metadata_is_reused_and_all_fields_remain_available(self):
        self.assertIsInstance(self.metadata, ExtensionMetadata)
        self.assertEqual(self.metadata.id, "test.fake-language")
        self.assertEqual(self.metadata.name, "Fake Pack")
        self.assertEqual(self.metadata.version, "0.1")
        self.assertEqual(self.metadata.api_version, "1")
        self.assertIs(self.metadata.kind, PackKind.LANGUAGE)
        self.assertEqual(self.metadata.capabilities, frozenset(Capability))
        self.assertEqual(self.metadata.supported_platforms, ("windows",))
        self.assertEqual(self.metadata.required_tools, ())

    def test_previous_constructor_shape_retains_language_default(self):
        metadata = ExtensionMetadata("test.previous", "Previous", "0.1", "1", frozenset(), ("windows",))
        self.assertIs(metadata.kind, PackKind.LANGUAGE)

    def test_valid_machine_readable_pack_ids(self):
        for identifier in ("python.core", "node.core", "java.core", "cpp.core", "docker.core",
                           "git.core", "fastapi.core", "vite.core", "a", "0-pack.test_1"):
            with self.subTest(identifier=identifier):
                validate_pack_id(identifier)
                self.assertEqual(replace(self.metadata, id=identifier).id, identifier)

    def test_malformed_pack_ids_are_rejected(self):
        for identifier in ("Python.core", "test/id", "test:id", " test.core", "test.core ",
                           "test core", "-test", ".test", "测试.core", "test.core\n"):
            with self.subTest(identifier=identifier):
                with self.assertRaisesRegex(ValueError, "id"):
                    replace(self.metadata, id=identifier)

    def test_empty_id_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "id"):
            replace(self.metadata, id="")

    def test_empty_name_is_rejected(self):
        for value in ("", " \t\n"):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "name"):
                replace(self.metadata, name=value)

    def test_empty_version_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "version"):
            replace(self.metadata, version="")

    def test_empty_api_version_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "api_version"):
            replace(self.metadata, api_version="")

    def test_invalid_pack_kind_is_rejected(self):
        for kind in ("language", "unknown", None, Capability.DETECT):
            with self.subTest(kind=kind), self.assertRaisesRegex(TypeError, "PackKind"):
                replace(self.metadata, kind=kind)

    def test_non_string_identity_fields_are_rejected(self):
        for field in ("id", "name", "version", "api_version"):
            with self.subTest(field=field), self.assertRaises(ValueError):
                replace(self.metadata, **{field: None})


class PackValidationTests(unittest.TestCase):
    def setUp(self):
        self.pack = FakeLanguagePack()
        self.environment = ExtensionEnvironment("windows", frozenset({"python"}))

    def missing_method(self, capability, method):
        pack = SimpleNamespace(metadata=replace(self.pack.metadata, capabilities=frozenset({capability})),
                               id=self.pack.id)
        with self.assertRaises(ValueError) as error:
            validate_pack(pack)
        self.assertIn(self.pack.id, str(error.exception))
        self.assertIn(capability.value, str(error.exception))
        self.assertIn(method, str(error.exception))

    def test_consistent_declarations_pass(self):
        validate_pack(self.pack)
        validate_pack(PythonCoreExtension())

    def test_detect_requires_detector(self):
        self.missing_method(Capability.DETECT, "detect")

    def test_inspect_requires_evidence_provider(self):
        self.missing_method(Capability.INSPECT, "collect")

    def test_diagnose_requires_diagnosis_rule(self):
        self.missing_method(Capability.DIAGNOSE, "diagnose")

    def test_plan_repair_requires_planner(self):
        self.missing_method(Capability.PLAN_REPAIR, "plan")

    def test_verify_requires_verifier(self):
        self.missing_method(Capability.VERIFY, "build_verification")

    def test_non_callable_implementations_are_rejected(self):
        for capability, method in ((Capability.DETECT, "detect"), (Capability.INSPECT, "collect"),
                                  (Capability.DIAGNOSE, "diagnose"), (Capability.PLAN_REPAIR, "plan"),
                                  (Capability.VERIFY, "build_verification")):
            with self.subTest(capability=capability):
                pack = SimpleNamespace(metadata=replace(self.pack.metadata, capabilities=frozenset({capability})),
                                       id=self.pack.id, **{method: "not callable"})
                with self.assertRaisesRegex(ValueError, method):
                    validate_pack(pack)

    def test_validator_checks_only_declared_capabilities(self):
        pack = SimpleNamespace(metadata=replace(self.pack.metadata, capabilities=frozenset()))
        validate_pack(pack)

    def test_stage_provider_needs_a_stable_id(self):
        for identifier in (None, "", "bad id"):
            with self.subTest(identifier=identifier):
                pack = SimpleNamespace(metadata=replace(self.pack.metadata, capabilities=frozenset({Capability.DETECT})),
                                       id=identifier, detect=lambda project: DetectionResult("unknown", ()))
                with self.assertRaisesRegex(ValueError, self.pack.id):
                    validate_pack(pack)

    def test_pipeline_validates_before_compatibility_and_stage_calls(self):
        self.pack.collect = None
        with patch("agent_doctor.extension_pipeline.check_compatibility") as checker:
            with self.assertRaisesRegex(ValueError, "collect"):
                prepare_extensions(self.environment, (self.pack,))
        checker.assert_not_called()
        self.assertEqual(self.pack.received_inputs, [])

    def test_duplicate_protection_still_applies(self):
        with self.assertRaisesRegex(ValueError, "Duplicate built-in extension id"):
            prepare_extensions(self.environment, (self.pack, FakeLanguagePack()))

    def test_compatibility_filtering_still_applies(self):
        self.pack.metadata = replace(self.pack.metadata, required_tools=("fake-missing-tool",))
        run = prepare_extensions(self.environment, (self.pack,))[0]
        self.assertEqual(run.failures[0].status, CompatibilityStatus.MISSING_REQUIRED_TOOL)
        self.assertEqual(self.pack.received_inputs, [])


class PackBoundaryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.project = scan_project(self.root)
        self.environment = inspect_environment(self.project)
        self.descriptor = ExtensionEnvironment("windows", frozenset({"python"}))

    def complete(self, *packs):
        runs = prepare_extensions(self.descriptor, packs)
        for stage in Capability:
            runs = run_extension_stage(runs, stage, self.project, self.environment)
        return merge_results(runs)

    def test_fake_language_pack_composes_existing_rule_planner_and_verifier_contracts(self):
        pack = FakeLanguagePack()
        self.assertIsInstance(pack.diagnosis_rules[0], DiagnosisRule)
        self.assertIsInstance(pack.repair_planners[0], RepairPlanner)
        self.assertIsInstance(pack.verifiers[0], Verifier)
        result = self.complete(pack)
        finding = result.diagnoses[0]
        self.assertEqual(result.failures, ())
        self.assertEqual(finding.source, "rule:" + pack.diagnosis_rules[0].id)
        self.assertEqual(finding.repair_plan.diagnosis_id, finding.diagnosis_id)
        self.assertEqual(finding.repair_plan.execution_status, "not_executed")
        self.assertEqual(finding.repair_plan.verification_steps[0].status, "not_run")

    def test_pack_can_group_multiple_rules_without_core_changes(self):
        pack = FakeLanguagePack()
        pack.diagnosis_rules += (FakeDiagnosisRule(pack.id + ".another-rule"),)
        result = self.complete(pack)
        self.assertEqual([finding.source for finding in result.diagnoses],
                         ["rule:" + rule.id for rule in pack.diagnosis_rules])
        self.assertEqual(len({finding.diagnosis_id for finding in result.diagnoses}), 2)

    def test_rule_provider_ids_and_existing_source_identity_are_stable(self):
        pack = FakeLanguagePack()
        for rule in pack.diagnosis_rules:
            validate_pack_id(rule.id)
            self.assertTrue(rule.id.startswith(pack.id + "."))
        first, second = self.complete(pack), self.complete(pack)
        self.assertEqual(first.diagnoses[0].source, second.diagnoses[0].source)
        python_pack = PythonCoreExtension()
        self.assertEqual(python_pack.id, "python.core")
        findings, _ = python_pack.diagnose(self.project, python_pack.detect(self.project), self.environment, ())
        self.assertEqual(findings[0].source, "rule:python_detection_unknown")
        self.assertEqual(findings[0].diagnosis_id, "python_detection_unknown")

    def test_different_pack_kinds_keep_deterministic_pipeline_order(self):
        language, toolchain = FakeLanguagePack(), FakeToolchainPack()
        result = self.complete(toolchain, language)
        self.assertIs(toolchain.metadata.kind, PackKind.TOOLCHAIN)
        self.assertEqual([item.source for item in result.evidence], [toolchain.id, language.id])
        self.assertEqual([item.source for item in result.diagnoses],
                         ["rule:" + toolchain.diagnosis_rules[0].id, "rule:" + language.diagnosis_rules[0].id])

    def test_pack_receives_no_privileged_objects_and_leaves_project_and_environment_unchanged(self):
        target = self.root / "keep.txt"
        target.write_bytes(b"unchanged")
        before = (target.read_bytes(), target.stat().st_mtime_ns)
        environment_before = dict(os.environ)
        pack = FakeLanguagePack()
        with patch("subprocess.run", side_effect=AssertionError("no process")), \
                patch("subprocess.Popen", side_effect=AssertionError("no process")), \
                patch("socket.create_connection", side_effect=AssertionError("no network")):
            result = self.complete(pack)
        self.assertEqual((target.read_bytes(), target.stat().st_mtime_ns), before)
        self.assertEqual(list(self.root.iterdir()), [target])
        self.assertEqual(dict(os.environ), environment_before)
        self.assertTrue(all(plan.repair_plan.execution_status == "not_executed" for plan in result.diagnoses))
        for inputs in pack.received_inputs:
            for value in inputs:
                self.assertFalse(callable(value))
                self.assertIsNot(value, os.environ)
                for name in ("executor", "CommandExecutor", "shell", "subprocess", "write", "install", "execute"):
                    self.assertFalse(hasattr(value, name))
        for provider in (*pack.diagnosis_rules, *pack.repair_planners, *pack.verifiers):
            for name in ("apply", "execute", "install", "write", "run_shell"):
                self.assertFalse(hasattr(provider, name))

    def test_invalid_pack_configuration_uses_existing_cli_tool_error_boundary(self):
        pack = FakeLanguagePack()
        pack.diagnose = None
        with patch("agent_doctor.extension_pipeline.BUILTIN_EXTENSIONS", (pack,)):
            with self.assertRaisesRegex(WorkflowError, "diagnose"):
                run_workflow(self.root)

    def test_pack_metadata_is_not_added_to_json_schema(self):
        result = run_workflow(self.root)
        self.assertEqual(result.report["schema_version"], "0.2")
        for field in ("packs", "extensions", "rule_ids", "pack_kind", "extension_api_version"):
            self.assertNotIn(field, result.report)
        self.assertNotIn('"kind": "language"', json.dumps(result.report))

    def test_fake_packs_are_not_in_runtime_distribution_or_builtins(self):
        self.assertEqual([extension.id for extension in BUILTIN_EXTENSIONS], ["python.core"])
        self.assertTrue(FakeLanguagePack.__module__.startswith("tests."))
        self.assertTrue(FakeToolchainPack.__module__.startswith("tests."))


if __name__ == "__main__":
    unittest.main()
