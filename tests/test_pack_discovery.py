"""Installed metadata discovery using safe test doubles, never installed plugins."""

import ast
from contextlib import ExitStack, redirect_stdout, redirect_stderr
from dataclasses import FrozenInstanceError, replace
import io
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from agent_doctor.sdk import (
    PACK_ENTRY_POINT_GROUP, DiscoveryFailure, DiscoveryResult, PackRegistry,
    discover_installed_packs,
)
from agent_doctor.cli import main
from agent_doctor.extension_pipeline import prepare_extensions
from agent_doctor.extensions import CompatibilityStatus, ExtensionEnvironment
from examples.extensions.example_language_pack import ExampleLanguagePack


ROOT = Path(__file__).resolve().parents[1]
QUERY = "agent_doctor.pack_discovery.metadata.entry_points"


class MetadataPack(ExampleLanguagePack):
    def __init__(self, **changes):
        self._metadata = replace(super().metadata, **changes)

    @property
    def metadata(self):
        return self._metadata


class FakeEntryPoint:
    def __init__(self, name, target=ExampleLanguagePack, distribution="test-pack", value="test_package:build_pack",
                 load_error=None, trace=None):
        self.name, self.value = name, value
        self.dist = SimpleNamespace(name=distribution) if distribution is not None else None
        self.target, self.load_error, self.trace = target, load_error, trace

    def load(self):
        if self.trace is not None:
            self.trace.append((self.name, self.dist.name if self.dist else "", self.value))
        if self.load_error is not None:
            raise self.load_error
        return self.target


class DiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.registry = PackRegistry.default()

    def discover(self, *points):
        with patch(QUERY, return_value=points) as query:
            result = discover_installed_packs(self.registry)
        query.assert_called_once_with(group="reporescue.packs")
        return result

    def test_group_is_fixed_and_only_that_group_is_queried(self):
        self.assertEqual(PACK_ENTRY_POINT_GROUP, "reporescue.packs")
        self.discover()

    def test_empty_environment_returns_empty_success(self):
        result = self.discover()
        self.assertEqual(result, DiscoveryResult((), ()))
        self.assertEqual([pack.metadata.id for pack in self.registry.snapshot()], ["python.core"])

    def test_valid_factory_is_loaded_invoked_without_arguments_and_registered(self):
        pack = ExampleLanguagePack()
        calls = []
        def factory():
            calls.append(())
            return pack
        result = self.discover(FakeEntryPoint("example", target=factory))
        self.assertEqual(calls, [()])
        self.assertEqual(result.registered_ids, ("example.language",))
        self.assertEqual(result.failures, ())
        self.assertIs(self.registry.get("example.language"), pack)

    def test_non_callable_targets_including_direct_pack_objects_are_rejected(self):
        for target in (None, 1, "test_package:build_pack", ExampleLanguagePack()):
            with self.subTest(target=type(target).__name__):
                result = self.discover(FakeEntryPoint("bad-target", target=target))
                self.assertEqual(result.failures[0].code, "invalid_factory")
                self.assertEqual(result.failures[0].status, "failed")
        self.assertEqual(len(self.registry.snapshot()), 1)

    def test_load_import_error_is_recorded_with_entry_point_and_distribution(self):
        result = self.discover(FakeEntryPoint("broken-import", load_error=ImportError("synthetic import error")))
        failure, = result.failures
        self.assertEqual((failure.entry_point, failure.distribution, failure.code),
                         ("broken-import", "test-pack", "load_failed"))
        self.assertEqual(len(result.failed), 1)

    def test_load_runtime_error_is_isolated(self):
        result = self.discover(FakeEntryPoint("broken", load_error=RuntimeError("synthetic error")),
                               FakeEntryPoint("good"))
        self.assertEqual(result.registered_ids, ("example.language",))
        self.assertEqual(result.failures[0].code, "load_failed")

    def test_factory_runtime_error_is_isolated(self):
        def broken():
            raise RuntimeError("synthetic factory error")
        result = self.discover(FakeEntryPoint("a-broken", target=broken), FakeEntryPoint("b-good"))
        self.assertEqual(result.registered_ids, ("example.language",))
        self.assertEqual(result.failures[0].code, "factory_failed")

    def test_factory_that_requires_arguments_fails_without_argument_injection(self):
        result = self.discover(FakeEntryPoint("requires-args", target=lambda unexpected: ExampleLanguagePack()))
        self.assertEqual(result.failures[0].code, "factory_failed")

    def test_invalid_factory_results_are_rejected_by_existing_registry(self):
        for target in (object(), None, SimpleNamespace(metadata=None)):
            with self.subTest(target=type(target).__name__):
                result = self.discover(FakeEntryPoint("invalid", target=lambda: target))
                self.assertEqual(result.failures[0].code, "invalid_pack")
        self.assertEqual(len(self.registry.snapshot()), 1)

    def test_capability_validation_is_not_bypassed(self):
        pack = ExampleLanguagePack()
        pack.collect = None
        before = self.registry.snapshot()
        result = self.discover(FakeEntryPoint("invalid-capability", target=lambda: pack))
        self.assertEqual(result.failures[0].code, "invalid_pack")
        self.assertEqual(self.registry.snapshot(), before)

    def test_register_method_is_reused(self):
        with patch.object(self.registry, "register", wraps=self.registry.register) as register:
            self.discover(FakeEntryPoint("example"))
        register.assert_called_once()

    def test_duplicate_third_party_id_is_skipped_and_recorded(self):
        trace = []
        first, later = ExampleLanguagePack(), ExampleLanguagePack()
        result = self.discover(FakeEntryPoint("b", target=lambda: later, trace=trace),
                               FakeEntryPoint("a", target=lambda: first, trace=trace))
        self.assertEqual([item[0] for item in trace], ["a", "b"])
        self.assertIs(self.registry.get("example.language"), first)
        self.assertEqual(result.registered_ids, ("example.language",))
        self.assertEqual(result.failures[0].code, "duplicate_pack_id")
        self.assertEqual(result.failures[0].status, "skipped")
        self.assertEqual(result.skipped, result.failures)
        self.assertEqual(result.failed, ())

    def test_third_party_cannot_overwrite_python_builtin(self):
        original = self.registry.get("python.core")
        result = self.discover(FakeEntryPoint("collision", target=lambda: MetadataPack(id="python.core")))
        self.assertEqual(result.registered_ids, ())
        self.assertEqual(result.failures[0].code, "duplicate_pack_id")
        self.assertIs(self.registry.get("python.core"), original)

    def test_one_failure_does_not_stop_valid_factories_on_either_side(self):
        result = self.discover(FakeEntryPoint("c-good", target=lambda: MetadataPack(id="test.last")),
                               FakeEntryPoint("b-broken", load_error=ImportError()),
                               FakeEntryPoint("a-good"))
        self.assertEqual(result.registered_ids, ("example.language", "test.last"))
        self.assertEqual([pack.id for pack in self.registry.snapshot()],
                         ["python.core", "example.language", "test.last"])
        self.assertEqual(len(result.failed), 1)

    def test_order_is_name_then_distribution_then_value(self):
        def run(points):
            trace = []
            self.registry = PackRegistry()
            for point in points:
                point.trace = trace
            result = self.discover(*points)
            return trace, result
        points = [FakeEntryPoint("z", target=lambda: MetadataPack(id="test.z")),
                  FakeEntryPoint("a", distribution="z-dist", target=lambda: MetadataPack(id="test.dist-z")),
                  FakeEntryPoint("a", distribution="a-dist", value="test_package:b", target=lambda: MetadataPack(id="test.b")),
                  FakeEntryPoint("a", distribution="a-dist", value="test_package:a", target=lambda: MetadataPack(id="test.a"))]
        first = run(points)
        second = run(list(reversed(points)))
        self.assertEqual(first, second)
        self.assertEqual(first[1].registered_ids, ("test.a", "test.b", "test.dist-z", "test.z"))

    def test_process_control_exceptions_propagate_from_load(self):
        for exception in (KeyboardInterrupt(), SystemExit(3), GeneratorExit()):
            with self.subTest(exception=type(exception).__name__), self.assertRaises(type(exception)):
                self.discover(FakeEntryPoint("control", load_error=exception))

    def test_process_control_exceptions_propagate_from_factory(self):
        for exception in (KeyboardInterrupt(), SystemExit(3), GeneratorExit()):
            def factory():
                raise exception
            with self.subTest(exception=type(exception).__name__), self.assertRaises(type(exception)):
                self.discover(FakeEntryPoint("control", target=factory))

    def test_process_control_exceptions_propagate_from_metadata_query_and_registration(self):
        for exception in (KeyboardInterrupt(), SystemExit(3), GeneratorExit()):
            with self.subTest(boundary="query", exception=type(exception).__name__), patch(QUERY, side_effect=exception):
                with self.assertRaises(type(exception)):
                    discover_installed_packs(self.registry)
            with self.subTest(boundary="registration", exception=type(exception).__name__), \
                    patch.object(self.registry, "register", side_effect=exception):
                with self.assertRaises(type(exception)):
                    self.discover(FakeEntryPoint("control"))

    def test_registration_failure_is_atomic_and_later_candidate_can_reuse_id(self):
        invalid = ExampleLanguagePack()
        invalid.diagnose = None
        result = self.discover(FakeEntryPoint("a-invalid", target=lambda: invalid), FakeEntryPoint("b-valid"))
        self.assertEqual(result.registered_ids, ("example.language",))
        self.assertEqual(result.failures[0].code, "invalid_pack")
        self.assertEqual(len(self.registry.snapshot()), 2)

    def test_discovery_does_not_call_pack_business_stages(self):
        with ExitStack() as stack:
            methods = [stack.enter_context(patch.object(ExampleLanguagePack, method, side_effect=AssertionError("business stage")))
                       for method in ("detect", "collect", "diagnose", "plan", "build_verification")]
            result = self.discover(FakeEntryPoint("example"))
        self.assertEqual(result.registered_ids, ("example.language",))
        for method in methods:
            method.assert_not_called()

    def test_api_mismatch_is_registered_then_filtered_by_pipeline(self):
        result = self.discover(FakeEntryPoint("api-mismatch", target=lambda: MetadataPack(api_version="2")))
        self.assertEqual(result.registered_ids, ("example.language",))
        self.assertEqual(result.failures, ())
        runs = prepare_extensions(ExtensionEnvironment("windows", frozenset({"python"})), self.registry.snapshot())
        self.assertEqual(runs[1].failures[0].status, CompatibilityStatus.API_VERSION_MISMATCH)

    def test_missing_tool_is_registered_then_filtered_by_pipeline(self):
        result = self.discover(FakeEntryPoint("missing-tool", target=lambda: MetadataPack(required_tools=("test-tool",))))
        self.assertEqual(result.registered_ids, ("example.language",))
        self.assertEqual(result.failures, ())
        runs = prepare_extensions(ExtensionEnvironment("windows", frozenset({"python"})), self.registry.snapshot())
        self.assertEqual(runs[1].failures[0].status, CompatibilityStatus.MISSING_REQUIRED_TOOL)

    def test_raw_exception_paths_secrets_and_tracebacks_are_not_returned(self):
        path = str(ROOT / "synthetic-home" / "site-packages" / "broken.py")
        secret = "synthetic-private-value"
        error = RuntimeError(path + " " + secret)
        def broken():
            raise error
        with patch.object(self.registry, "register", side_effect=error):
            registration = self.discover(FakeEntryPoint("registration"))
        query = None
        with patch(QUERY, side_effect=error):
            query = discover_installed_packs(self.registry)
        for result in (self.discover(FakeEntryPoint("load", load_error=error)),
                       self.discover(FakeEntryPoint("factory", target=broken)), registration, query):
            text = repr(result)
            self.assertNotIn(path, text)
            self.assertNotIn(secret, text)
            self.assertNotIn("Traceback", text)

    def test_unavailable_distribution_identity_is_optional(self):
        result = self.discover(FakeEntryPoint("missing-dist", distribution=None, load_error=ImportError()))
        self.assertIsNone(result.failures[0].distribution)

    def test_unreadable_distribution_metadata_does_not_abort_loading(self):
        class UnreadableDistribution:
            @property
            def name(self):
                raise RuntimeError("synthetic private metadata")
        point = FakeEntryPoint("example")
        point.dist = UnreadableDistribution()
        self.assertEqual(self.discover(point).registered_ids, ("example.language",))

    def test_unsafe_identity_labels_are_omitted(self):
        point = FakeEntryPoint("synthetic/path", distribution="synthetic\\path", load_error=ImportError())
        failure, = self.discover(point).failures
        self.assertIsNone(failure.entry_point)
        self.assertIsNone(failure.distribution)

    def test_query_failure_is_reported_without_registry_changes(self):
        before = self.registry.snapshot()
        with patch(QUERY, side_effect=RuntimeError("synthetic metadata failure")):
            result = discover_installed_packs(self.registry)
        self.assertEqual(result.failures[0].code, "query_failed")
        self.assertEqual(self.registry.snapshot(), before)

    def test_malformed_entry_point_metadata_is_recorded_and_other_candidates_continue(self):
        bad = FakeEntryPoint("bad")
        bad.value = None
        result = self.discover(bad, FakeEntryPoint("good"))
        self.assertEqual(result.failures[0].code, "metadata_failed")
        self.assertEqual(result.registered_ids, ("example.language",))

    def test_result_and_failure_are_immutable(self):
        result = self.discover(FakeEntryPoint("bad", load_error=ImportError()))
        self.assertIsInstance(result, DiscoveryResult)
        self.assertIsInstance(result.failures[0], DiscoveryFailure)
        self.assertIsInstance(result.registered_ids, tuple)
        with self.assertRaises(FrozenInstanceError):
            result.failures[0].message = "changed"
        with self.assertRaises(FrozenInstanceError):
            result.registered_ids = ()

    def test_discovery_requires_a_registry_not_a_path_or_import_string(self):
        with patch(QUERY) as query:
            for value in (None, "test_package:build_pack", Path("synthetic.py")):
                with self.subTest(value=type(value).__name__), self.assertRaises(TypeError):
                    discover_installed_packs(value)
        query.assert_not_called()

    def test_no_process_network_scanning_writes_environment_or_path_mutation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "keep.txt"
            path.write_bytes(b"unchanged")
            before = path.read_bytes(), path.stat().st_mtime_ns
            environment_before, sys_path_before = dict(os.environ), list(sys.path)
            with ExitStack() as stack:
                for target in ("subprocess.run", "subprocess.Popen", "os.system", "socket.socket",
                               "socket.create_connection", "os.walk", "os.scandir", "glob.glob",
                               "pathlib.Path.glob", "pathlib.Path.rglob", "pathlib.Path.iterdir",
                               "pathlib.Path.write_text", "pathlib.Path.write_bytes", "pathlib.Path.unlink"):
                    stack.enter_context(patch(target, side_effect=AssertionError("forbidden operation")))
                result = self.discover(FakeEntryPoint("example"))
            self.assertEqual(result.registered_ids, ("example.language",))
            self.assertEqual((path.read_bytes(), path.stat().st_mtime_ns), before)
            self.assertEqual(list(root.iterdir()), [path])
            self.assertEqual(dict(os.environ), environment_before)
            self.assertEqual(sys.path, sys_path_before)

    def test_default_registry_and_cli_never_query_third_party_entry_points(self):
        with tempfile.TemporaryDirectory() as directory, patch(QUERY, side_effect=AssertionError("automatic discovery")) as query:
            registry = PackRegistry.default()
            output, errors = io.StringIO(), io.StringIO()
            with redirect_stdout(output), redirect_stderr(errors):
                code = main([directory])
            self.assertEqual(code, 0)  # WARNING alone does not make the existing CLI fail.
            self.assertIn("[WARNING]", output.getvalue())
            self.assertEqual(errors.getvalue(), "")
            self.assertEqual([pack.id for pack in registry.snapshot()], ["python.core"])
        query.assert_not_called()

    def test_discovery_of_one_registry_does_not_change_default_registry(self):
        self.discover(FakeEntryPoint("example"))
        self.assertEqual([pack.id for pack in PackRegistry.default().snapshot()], ["python.core"])

    def test_example_is_not_a_runtime_distribution_entry_point(self):
        import tomllib
        config = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        self.assertNotIn("reporescue.packs", config["project"].get("entry-points", {}))

    def test_sdk_does_not_expose_loader_details_or_execution_authority(self):
        from agent_doctor import sdk
        for name in ("EntryPoint", "metadata", "CommandExecutor", "run_workflow", "prepare_extensions", "subprocess"):
            self.assertFalse(hasattr(sdk, name), name)

    def test_source_uses_only_metadata_loading_without_extra_import_or_scan_mechanisms(self):
        tree = ast.parse((ROOT / "src/agent_doctor/pack_discovery.py").read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ExceptHandler):
                self.assertIsInstance(node.type, ast.Name)
                self.assertEqual(node.type.id, "Exception")
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                self.assertNotIn(node.func.id, ("eval", "exec", "__import__", "open"))
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                self.assertNotIn(node.func.attr, ("import_module", "walk", "glob", "rglob", "iterdir", "install", "execute", "run", "Popen"))


if __name__ == "__main__":
    unittest.main()
