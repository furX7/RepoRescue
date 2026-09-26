"""Explicit registration, stable snapshots and unchanged Core dispatch."""

import ast
from contextlib import ExitStack
from dataclasses import replace
import os
from pathlib import Path
import socket
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from agent_doctor.sdk import Capability, ExtensionMetadata, PackRegistry
from agent_doctor.extension_pipeline import (
    BUILTIN_EXTENSIONS, merge_results, prepare_extensions, run_extension_stage,
)
from agent_doctor.extensions import CompatibilityStatus, ExtensionEnvironment
from agent_doctor.project import inspect_environment, scan_project
from agent_doctor.python_extension import PythonCoreExtension
from agent_doctor.workflow import run_workflow
from examples.extensions.example_language_pack import ExampleLanguagePack


ROOT = Path(__file__).resolve().parents[1]


class SuppliedMetadataPack(ExampleLanguagePack):
    def __init__(self, metadata):
        self._metadata = metadata

    @property
    def metadata(self):
        return self._metadata


class RegistrationTests(unittest.TestCase):
    def setUp(self):
        self.registry = PackRegistry.default()

    def test_registry_is_public_through_sdk(self):
        from agent_doctor.pack_registry import PackRegistry as InternalRegistry
        self.assertIs(PackRegistry, InternalRegistry)

    def test_default_contains_only_the_existing_python_builtin(self):
        self.assertEqual([pack.metadata.id for pack in self.registry.packs], ["python.core"])
        self.assertEqual(self.registry.snapshot(), BUILTIN_EXTENSIONS)
        self.assertIsNone(self.registry.get("example.language"))

    def test_instances_do_not_share_registration_state(self):
        self.registry.register(ExampleLanguagePack())
        another = PackRegistry.default()
        self.assertEqual([pack.id for pack in another.snapshot()], ["python.core"])

    def test_empty_registry_is_explicitly_empty(self):
        self.assertEqual(PackRegistry().snapshot(), ())

    def test_register_preserves_object_identity_and_order(self):
        example = ExampleLanguagePack()
        self.registry.register(example)
        self.assertEqual([pack.id for pack in self.registry.packs], ["python.core", "example.language"])
        self.assertIs(self.registry.get("example.language"), example)

    def test_constructor_accepts_explicit_objects_in_order(self):
        example, python = ExampleLanguagePack(), PythonCoreExtension()
        self.assertEqual(PackRegistry((example, python)).snapshot(), (example, python))

    def test_duplicate_id_is_rejected_without_replacing_the_original(self):
        original = self.registry.get("python.core")
        before = self.registry.snapshot()
        with self.assertRaisesRegex(ValueError, "Duplicate pack ID.*python.core"):
            self.registry.register(PythonCoreExtension())
        self.assertEqual(self.registry.snapshot(), before)
        self.assertIs(self.registry.get("python.core"), original)

    def test_registering_the_same_object_twice_is_also_rejected(self):
        example = ExampleLanguagePack()
        self.registry.register(example)
        with self.assertRaisesRegex(ValueError, "Duplicate pack ID.*example.language"):
            self.registry.register(example)
        self.assertEqual(len(self.registry.snapshot()), 2)

    def test_missing_or_non_sdk_metadata_is_rejected_atomically(self):
        before = self.registry.snapshot()
        for pack in (object(), "example.language", SimpleNamespace(metadata=None),
                     SuppliedMetadataPack(SimpleNamespace(id="example.language"))):
            with self.subTest(pack=type(pack).__name__), self.assertRaisesRegex(ValueError, "metadata"):
                self.registry.register(pack)
            self.assertEqual(self.registry.snapshot(), before)

    def test_metadata_is_revalidated_even_if_construction_checks_were_bypassed(self):
        cases = (("id", ""), ("id", "Bad/id"), ("name", ""), ("version", ""),
                 ("api_version", ""), ("api_version", 1), ("kind", "language"),
                 ("capabilities", {Capability.DETECT}), ("capabilities", frozenset({"detect"})))
        before = self.registry.snapshot()
        for field, value in cases:
            metadata = replace(ExampleLanguagePack().metadata)
            object.__setattr__(metadata, field, value)
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                self.registry.register(SuppliedMetadataPack(metadata))
            self.assertEqual(self.registry.snapshot(), before)

    def test_platform_and_tool_declarations_need_structural_tuple_fields(self):
        before = self.registry.snapshot()
        for field in ("supported_platforms", "required_tools"):
            for value in ("python", ["python"], (1,), ("",)):
                metadata = replace(ExampleLanguagePack().metadata, **{field: value})
                with self.subTest(field=field, value=value), self.assertRaisesRegex(ValueError, field):
                    self.registry.register(SuppliedMetadataPack(metadata))
                self.assertEqual(self.registry.snapshot(), before)

    def test_all_declared_capabilities_require_callable_implementations(self):
        before = self.registry.snapshot()
        for method in ("detect", "collect", "diagnose", "plan", "build_verification"):
            for value in (None, "not callable"):
                pack = ExampleLanguagePack()
                setattr(pack, method, value)
                with self.subTest(method=method, value=value), self.assertRaisesRegex(ValueError, method):
                    self.registry.register(pack)
                self.assertEqual(self.registry.snapshot(), before)

    def test_rejected_registration_does_not_reserve_an_id(self):
        invalid = ExampleLanguagePack()
        invalid.collect = None
        with self.assertRaises(ValueError):
            self.registry.register(invalid)
        valid = ExampleLanguagePack()
        self.registry.register(valid)
        self.assertIs(self.registry.get(valid.id), valid)

    def test_pack_with_no_declared_stages_can_be_registered(self):
        pack = SimpleNamespace(metadata=ExtensionMetadata(
            "test.metadata-only", "Metadata only", "0.1", "1", frozenset(), (),
        ))
        self.registry.register(pack)
        self.assertIs(self.registry.get("test.metadata-only"), pack)

    def test_snapshot_is_an_immutable_tuple_and_property_is_read_only(self):
        snapshot = self.registry.snapshot()
        self.assertIsInstance(snapshot, tuple)
        with self.assertRaises(TypeError):
            snapshot[0] = ExampleLanguagePack()
        with self.assertRaises(AttributeError):
            self.registry.packs = ()
        self.assertEqual(self.registry.snapshot(), snapshot)

    def test_old_snapshot_is_not_changed_by_later_registration(self):
        before = self.registry.snapshot()
        self.registry.register(ExampleLanguagePack())
        self.assertEqual([pack.id for pack in before], ["python.core"])
        self.assertEqual([pack.id for pack in self.registry.snapshot()], ["python.core", "example.language"])

    def test_mutating_a_list_copy_does_not_modify_registry(self):
        copy = list(self.registry.packs)
        copy.clear()
        self.assertEqual([pack.id for pack in self.registry.snapshot()], ["python.core"])

    def test_get_returns_none_for_missing_id(self):
        self.assertIsNone(self.registry.get("missing.pack"))
        self.assertIsNone(self.registry.get(""))

    def test_registration_does_not_call_any_business_stage(self):
        with ExitStack() as stack:
            methods = [stack.enter_context(patch.object(
                ExampleLanguagePack, method, side_effect=AssertionError("stage ran during registration"),
            )) for method in ("detect", "collect", "diagnose", "plan", "build_verification")]
            example = ExampleLanguagePack()
            self.registry.register(example)
        for method in methods:
            method.assert_not_called()
        self.assertIs(self.registry.get(example.id), example)

    def test_registration_does_not_filter_runtime_compatibility(self):
        for changes in ({"api_version": "2"}, {"supported_platforms": ("linux",)},
                        {"required_tools": ("unavailable-test-tool",)}):
            registry = PackRegistry()
            pack = SuppliedMetadataPack(replace(ExampleLanguagePack().metadata, **changes))
            with self.subTest(changes=changes), patch("agent_doctor.extension_pipeline.check_compatibility") as checker:
                registry.register(pack)
            checker.assert_not_called()
            self.assertIs(registry.get(pack.id), pack)

    def test_registry_has_no_execution_or_lifecycle_api(self):
        for name in ("run", "diagnose", "execute", "shell", "executor", "subprocess", "write",
                     "environment", "network", "unregister", "enable", "disable", "reload"):
            self.assertFalse(hasattr(self.registry, name), name)

    def test_registration_has_no_project_environment_process_or_network_side_effects(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "keep.txt"
            path.write_bytes(b"unchanged")
            before = path.read_bytes(), path.stat().st_mtime_ns
            environment_before = dict(os.environ)
            with patch.object(subprocess, "run", side_effect=AssertionError("process")), \
                    patch.object(subprocess, "Popen", side_effect=AssertionError("process")), \
                    patch.object(os, "system", side_effect=AssertionError("shell")), \
                    patch.object(socket, "socket", side_effect=AssertionError("network")), \
                    patch.object(Path, "write_text", side_effect=AssertionError("write")), \
                    patch.object(Path, "write_bytes", side_effect=AssertionError("write")), \
                    patch.object(Path, "unlink", side_effect=AssertionError("delete")), \
                    patch("builtins.open", side_effect=AssertionError("file access")):
                registry = PackRegistry.default()
                registry.register(ExampleLanguagePack())
                registry.get("example.language")
                registry.snapshot()
            self.assertEqual((path.read_bytes(), path.stat().st_mtime_ns), before)
            self.assertEqual(list(Path(directory).iterdir()), [path])
            self.assertEqual(dict(os.environ), environment_before)

    def test_registry_contains_no_dynamic_loading_or_discovery(self):
        source = (ROOT / "src/agent_doctor/pack_registry.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        allowed = {"collections.abc", "dataclasses", "extensions", "extension_pipeline"}
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                self.assertIn(node.module, allowed)
            self.assertNotIsInstance(node, ast.Import)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                self.assertNotIn(node.func.id, ("eval", "exec", "__import__", "open"))
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                self.assertNotIn(node.func.attr, ("import_module", "entry_points", "glob", "rglob", "iterdir"))


class RegistryPipelineTests(unittest.TestCase):
    def setUp(self):
        self.project = scan_project(ROOT / "examples/extension-demo")
        self.environment = inspect_environment(self.project)
        self.descriptor = ExtensionEnvironment("windows", frozenset({"python"}))

    def complete(self, snapshot):
        runs = prepare_extensions(self.descriptor, snapshot)
        for stage in Capability:
            runs = run_extension_stage(runs, stage, self.project, self.environment)
        return runs

    def test_example_can_be_registered_and_run_on_official_demo(self):
        registry = PackRegistry.default()
        registry.register(ExampleLanguagePack())
        runs = self.complete(registry.snapshot())
        result = merge_results(runs)
        self.assertEqual(result.failures, ())
        self.assertEqual([run.extension.id for run in runs], ["python.core", "example.language"])
        self.assertEqual([item.source for item in result.diagnoses],
                         ["rule:python_detection_unknown", "rule:example.marker-detected"])
        finding = result.diagnoses[1]
        self.assertEqual(finding.evidence_refs, ("example.language:marker",))
        self.assertEqual(finding.repair_plan.execution_status, "not_executed")
        self.assertEqual(finding.repair_plan.verification_steps[0].status, "not_run")

    def test_reversed_registration_order_determines_merged_result_order(self):
        registry = PackRegistry((ExampleLanguagePack(), PythonCoreExtension()))
        runs = self.complete(registry.snapshot())
        self.assertEqual([run.extension.id for run in runs], ["example.language", "python.core"])
        self.assertEqual([item.source for item in merge_results(runs).diagnoses],
                         ["rule:example.marker-detected", "rule:python_detection_unknown"])
        self.assertEqual(merge_results(runs).evidence[0].source, "example.language")

    def test_default_dispatch_and_python_results_are_unchanged(self):
        default = prepare_extensions(self.descriptor)
        registered = prepare_extensions(self.descriptor, PackRegistry.default().snapshot())
        self.assertEqual(default, registered)
        original = self.complete(BUILTIN_EXTENSIONS)[0]
        registry = PackRegistry.default()
        registry.register(ExampleLanguagePack())
        python = self.complete(registry.snapshot())[0]
        self.assertEqual((python.detection, python.evidence, python.diagnoses, python.command_proposals),
                         (original.detection, original.evidence, original.diagnoses, original.command_proposals))

    def test_later_registration_does_not_change_an_already_prepared_run(self):
        registry = PackRegistry.default()
        snapshot = registry.snapshot()
        runs = prepare_extensions(self.descriptor, snapshot)
        registry.register(ExampleLanguagePack())
        runs = run_extension_stage(runs, Capability.DETECT, self.project, self.environment)
        self.assertEqual([run.extension.id for run in runs], ["python.core"])
        self.assertEqual(len(registry.snapshot()), 2)

    def test_runtime_compatibility_is_checked_by_pipeline(self):
        cases = (({"api_version": "2"}, CompatibilityStatus.API_VERSION_MISMATCH),
                 ({"supported_platforms": ("linux",)}, CompatibilityStatus.UNSUPPORTED_PLATFORM),
                 ({"required_tools": ("unavailable-test-tool",)}, CompatibilityStatus.MISSING_REQUIRED_TOOL))
        for changes, status in cases:
            registry = PackRegistry()
            pack = SuppliedMetadataPack(replace(ExampleLanguagePack().metadata, **changes))
            registry.register(pack)
            with self.subTest(status=status), patch.object(pack, "detect", side_effect=AssertionError("incompatible stage")):
                runs = self.complete(registry.snapshot())
            self.assertEqual(runs[0].failures[0].status, status)
            self.assertEqual(runs[0].diagnoses, ())

    def test_registration_does_not_change_production_defaults_or_json(self):
        registry = PackRegistry.default()
        registry.register(ExampleLanguagePack())
        self.assertEqual([pack.id for pack in BUILTIN_EXTENSIONS], ["python.core"])
        self.assertEqual([pack.id for pack in PackRegistry.default().snapshot()], ["python.core"])
        with patch.object(ExampleLanguagePack, "detect", side_effect=AssertionError("example auto-loaded")):
            result = run_workflow(self.project.root_path)
        self.assertEqual(result.report["schema_version"], "0.2")
        self.assertFalse(any(finding.source.startswith("rule:example.") for finding in result.diagnostics))
        for field in ("registry", "packs", "loaded_extensions", "pack_metadata"):
            self.assertNotIn(field, result.report)


if __name__ == "__main__":
    unittest.main()
