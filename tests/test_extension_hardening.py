"""Malformed declarations and interrupted batches at the existing Pack seams."""

from dataclasses import replace
from datetime import datetime, timezone
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from agent_doctor.sdk import (
    Capability, ExtensionFailure, ExtensionIncompatible, ExtensionUnavailable,
    PackRegistry, discover_installed_packs,
)
from agent_doctor.extension_pipeline import (
    merge_detection, merge_results, prepare_extensions, run_extension_stage,
)
from agent_doctor.extensions import CompatibilityStatus, ExtensionEnvironment
from agent_doctor.models import EnvironmentInfo, ProjectInfo
from tests.test_extension_pipeline import RecordingExtension, STAGES
from tests.test_pack_discovery import FakeEntryPoint, QUERY


ROOT = Path(__file__).resolve().parents[1]


class ClassPack(RecordingExtension):
    # A class can accidentally pass callable-presence validation, yet its
    # inherited stage methods are unbound. Factories must return instances.
    metadata = RecordingExtension("test.class").metadata
    id = metadata.id


class SuppliedMetadataPack(RecordingExtension):
    def __init__(self, metadata):
        self.metadata = metadata


class DiscoveryHardeningTests(unittest.TestCase):
    def discover(self, *points, registry=None):
        registry = PackRegistry.default() if registry is None else registry
        before = registry.snapshot()
        with patch(QUERY, return_value=points) as query:
            result = discover_installed_packs(registry)
        query.assert_called_once_with(group="reporescue.packs")
        self.assertEqual(registry.snapshot()[:len(before)], before)
        return result, registry

    def test_malformed_declarations_fail_atomically_without_reserving_id(self):
        cases = (
            ("id", ""), ("id", "Bad/id"), ("name", ""), ("name", " "),
            ("version", ""), ("api_version", ""), ("kind", "language"),
            ("capabilities", {"detect"}), ("capabilities", frozenset({"detect"})),
            ("required_tools", None), ("required_tools", "python"),
            ("required_tools", ("",)), ("required_tools", (7,)),
            ("supported_platforms", None), ("supported_platforms", ["windows"]),
            ("supported_platforms", (" ",)), ("supported_platforms", (None,)),
        )
        for field, value in cases:
            with self.subTest(field=field, value=value):
                pack = RecordingExtension("test.malformed")
                object.__setattr__(pack.metadata, field, value)
                result, registry = self.discover(
                    FakeEntryPoint("a-invalid", target=lambda: pack),
                    FakeEntryPoint("b-valid", target=lambda: RecordingExtension("test.malformed")),
                )
                self.assertEqual(result.failures[0].code, "invalid_pack")
                self.assertEqual(result.registered_ids, ("test.malformed",))
                self.assertEqual(len(registry.snapshot()), 2)

    def test_missing_wrong_metadata_and_plain_factory_results_are_isolated(self):
        for pack in (None, object(), SimpleNamespace(), SuppliedMetadataPack(None),
                     SuppliedMetadataPack("metadata"), SuppliedMetadataPack(SimpleNamespace(id="test.pack"))):
            with self.subTest(pack=type(pack).__name__):
                result, registry = self.discover(
                    FakeEntryPoint("a-invalid", target=lambda: pack),
                    FakeEntryPoint("b-valid"),
                )
                self.assertEqual(result.failures[0].code, "invalid_pack")
                self.assertEqual(result.registered_ids, ("example.language",))
                self.assertEqual(len(registry.snapshot()), 2)

    def test_registry_rejects_pack_class_without_reserving_its_id(self):
        registry = PackRegistry.default()
        before = registry.snapshot()
        with self.assertRaisesRegex(ValueError, "instance"):
            registry.register(ClassPack)
        self.assertEqual(registry.snapshot(), before)
        registry.register(ClassPack("test.class"))
        self.assertIsInstance(registry.get("test.class"), ClassPack)

    def test_factory_returning_class_is_invalid_and_next_instance_can_register(self):
        result, registry = self.discover(
            FakeEntryPoint("a-class", target=lambda: ClassPack),
            FakeEntryPoint("b-instance", target=lambda: ClassPack("test.class")),
        )
        self.assertEqual(result.registered_ids, ("test.class",))
        self.assertEqual(result.failures[0].code, "invalid_pack")
        self.assertIsInstance(registry.get("test.class"), ClassPack)

    def test_class_matching_existing_id_is_invalid_not_a_duplicate_skip(self):
        registry = PackRegistry((ClassPack("test.class"),))
        result, _ = self.discover(FakeEntryPoint("class", target=lambda: ClassPack), registry=registry)
        self.assertEqual(result.failed[0].code, "invalid_pack")
        self.assertEqual(result.skipped, ())

    def test_callable_instance_is_a_valid_no_argument_factory(self):
        class CallableFactory:
            def __init__(self):
                self.calls = 0

            def __call__(self):
                self.calls += 1
                return RecordingExtension("test.callable")

        factory = CallableFactory()
        result, registry = self.discover(FakeEntryPoint("callable", target=factory))
        self.assertEqual(factory.calls, 1)
        self.assertEqual(result.registered_ids, ("test.callable",))
        self.assertEqual(registry.get("test.callable").trace, [])

    def test_metadata_accessor_and_declared_method_getter_errors_are_isolated(self):
        class MetadataError:
            @property
            def metadata(self):
                raise RuntimeError("synthetic accessor failure")

        class MethodError(RecordingExtension):
            @property
            def collect(self):
                raise TypeError("synthetic method getter failure")

        for pack in (MetadataError(), MethodError("test.getter")):
            with self.subTest(pack=type(pack).__name__):
                result, registry = self.discover(FakeEntryPoint("a-bad", target=lambda: pack),
                                                 FakeEntryPoint("b-good"))
                self.assertEqual(result.failures[0].code, "invalid_pack")
                self.assertEqual(result.registered_ids, ("example.language",))
                self.assertEqual(len(registry.snapshot()), 2)

    def test_metadata_and_method_getter_control_signals_propagate(self):
        for signal in (KeyboardInterrupt, SystemExit, GeneratorExit):
            for field in ("metadata", "collect"):
                with self.subTest(signal=signal, field=field):
                    class GetterError:
                        @property
                        def metadata(self):
                            if field == "metadata":
                                raise signal()
                            return RecordingExtension("test.getter").metadata

                        @property
                        def collect(self):
                            raise signal()

                        id = "test.getter"
                        detect = diagnose = plan = build_verification = lambda *args: None

                    registry = PackRegistry.default()
                    before = registry.snapshot()
                    with patch(QUERY, return_value=(FakeEntryPoint("getter", target=GetterError),)):
                        with self.assertRaises(signal):
                            discover_installed_packs(registry)
                    self.assertEqual(registry.snapshot(), before)

    def test_load_and_factory_exception_matrix_keeps_both_surrounding_successes(self):
        for error_type in (ImportError, ModuleNotFoundError, RuntimeError, ValueError, TypeError):
            for boundary in ("load", "factory"):
                with self.subTest(error=error_type, boundary=boundary):
                    def broken():
                        raise error_type("synthetic failure")

                    bad = FakeEntryPoint("b-bad", load_error=error_type()) if boundary == "load" else \
                        FakeEntryPoint("b-bad", target=broken)
                    result, registry = self.discover(
                        FakeEntryPoint("c-good", target=lambda: RecordingExtension("test.last")),
                        bad, FakeEntryPoint("a-good", target=lambda: RecordingExtension("test.first")),
                    )
                    self.assertEqual(result.registered_ids, ("test.first", "test.last"))
                    self.assertEqual(result.failures[0].code, boundary + "_failed")
                    self.assertEqual([pack.id for pack in registry.snapshot()],
                                     ["python.core", "test.first", "test.last"])

    def test_duplicate_entry_point_names_use_distribution_and_value_order(self):
        points = (
            FakeEntryPoint("same", distribution="z-pack", value="synthetic:z",
                           target=lambda: RecordingExtension("test.same")),
            FakeEntryPoint("same", distribution="a-pack", value="synthetic:z",
                           target=lambda: RecordingExtension("test.same")),
            FakeEntryPoint("same", distribution="a-pack", value="synthetic:a",
                           target=lambda: RecordingExtension("test.same")),
        )
        results = []
        for candidates in (points, tuple(reversed(points))):
            result, registry = self.discover(*candidates)
            self.assertEqual(result.registered_ids, ("test.same",))
            self.assertEqual(len(result.skipped), 2)
            self.assertEqual(registry.get("test.same").trace, [])
            results.append(result)
        self.assertEqual(results[0], results[1])

    def test_missing_distribution_and_odd_values_do_not_create_extra_load_paths(self):
        for distribution in (None, SimpleNamespace(name=None), object()):
            point = FakeEntryPoint("odd", value="synthetic:not a module/path")
            point.dist = distribution
            result, _ = self.discover(point)
            self.assertEqual(result.registered_ids, ("example.language",))
        point = FakeEntryPoint("invalid-value", value=object())
        result, _ = self.discover(point, FakeEntryPoint("valid"))
        self.assertEqual(result.failures[0].code, "metadata_failed")
        self.assertEqual(result.registered_ids, ("example.language",))

    def test_entry_point_metadata_getter_failure_isolated_and_signals_propagate(self):
        class BadPoint:
            @property
            def name(self):
                raise error()

        error = RuntimeError
        result, _ = self.discover(BadPoint(), FakeEntryPoint("valid"))
        self.assertEqual(result.failures[0].code, "metadata_failed")
        self.assertEqual(result.registered_ids, ("example.language",))
        for error in (KeyboardInterrupt, SystemExit, GeneratorExit):
            with self.subTest(error=error), self.assertRaises(error):
                self.discover(BadPoint())

    def test_discovery_query_failure_is_not_retried(self):
        registry = PackRegistry.default()
        with patch(QUERY, side_effect=RuntimeError("synthetic query error")) as query:
            result = discover_installed_packs(registry)
        query.assert_called_once_with(group="reporescue.packs")
        self.assertEqual(result.failed[0].code, "query_failed")
        self.assertEqual([pack.id for pack in registry.snapshot()], ["python.core"])

    def test_interrupt_preserves_prior_registration_and_does_not_load_later_candidate(self):
        for signal in (KeyboardInterrupt, SystemExit, GeneratorExit):
            registry = PackRegistry.default()
            trace = []
            points = (FakeEntryPoint("a-good", target=lambda: RecordingExtension("test.first"), trace=trace),
                      FakeEntryPoint("b-interrupt", load_error=signal(), trace=trace),
                      FakeEntryPoint("c-later", trace=trace))
            with self.subTest(signal=signal), patch(QUERY, return_value=points), self.assertRaises(signal):
                discover_installed_packs(registry)
            self.assertEqual([pack.id for pack in registry.snapshot()], ["python.core", "test.first"])
            self.assertEqual([item[0] for item in trace], ["a-good", "b-interrupt"])

    def test_failure_results_do_not_stringify_third_party_exceptions(self):
        class UnprintableError(RuntimeError):
            def __str__(self):
                raise AssertionError("raw error must not be formatted")

        def broken():
            raise UnprintableError()

        result, _ = self.discover(FakeEntryPoint("load", load_error=UnprintableError()),
                                 FakeEntryPoint("factory", target=broken), FakeEntryPoint("valid"))
        self.assertEqual(result.registered_ids, ("example.language",))
        self.assertEqual({failure.code for failure in result.failed}, {"load_failed", "factory_failed"})


class PipelineHardeningTests(unittest.TestCase):
    def setUp(self):
        self.project = ProjectInfo(ROOT / "synthetic-project", datetime(2026, 1, 1, tzinfo=timezone.utc))
        self.environment = EnvironmentInfo(None, "3.12", False, ())
        self.compatibility = ExtensionEnvironment("windows", frozenset({"python"}))

    def advance(self, runs, stages=STAGES):
        for stage in stages:
            runs = run_extension_stage(runs, stage, self.project, self.environment)
        return runs

    def test_middle_pack_failure_at_each_stage_preserves_surrounding_packs(self):
        for stage in STAGES:
            with self.subTest(stage=stage):
                packs = (RecordingExtension("test.first"),
                         RecordingExtension("test.bad", errors={stage: ExtensionFailure("test.bad", "synthetic")}),
                         RecordingExtension("test.last"))
                runs = self.advance(prepare_extensions(self.compatibility, packs))
                self.assertEqual([run.failures[0].extension_id for run in runs if run.failures], ["test.bad"])
                for index in (0, 2):
                    self.assertEqual(runs[index].failures, ())
                    self.assertEqual(packs[index].trace[-1], (packs[index].id, Capability.VERIFY))
                    self.assertEqual(runs[index].diagnoses[0].repair_plan.verification_steps[0].status, "not_run")

    def test_inspection_generator_failure_commits_no_partial_evidence(self):
        pack = RecordingExtension()
        original = pack.collect

        def interrupted(*args):
            yield from original(*args)
            raise ExtensionFailure(pack.id, "synthetic incomplete evidence")

        pack.collect = interrupted
        run = self.advance(prepare_extensions(self.compatibility, (pack,)))[0]
        self.assertEqual(run.evidence, ())
        self.assertEqual(run.diagnoses, ())
        self.assertEqual(run.failures[0].stage, Capability.INSPECT)

    def test_diagnosis_generator_failure_preserves_only_prior_evidence(self):
        pack = RecordingExtension()
        runs = self.advance(prepare_extensions(self.compatibility, (pack,)), STAGES[:2])
        before = runs[0]
        original = pack.diagnose

        def interrupted(*args):
            diagnoses, evidence = original(*args)
            def findings():
                yield from diagnoses
                raise ExtensionFailure(pack.id, "synthetic incomplete diagnosis")
            return findings(), evidence

        pack.diagnose = interrupted
        after = self.advance(runs, STAGES[2:])[0]
        self.assertEqual(after.evidence, before.evidence)
        self.assertEqual(after.diagnoses, before.diagnoses)
        self.assertEqual(after.failures[0].stage, Capability.DIAGNOSE)

    def test_late_planner_or_verifier_failure_discards_entire_stage_batch(self):
        for stage, method in ((Capability.PLAN_REPAIR, "plan"), (Capability.VERIFY, "build_verification")):
            with self.subTest(stage=stage):
                pack = RecordingExtension()
                preceding = STAGES[:STAGES.index(stage)]
                runs = self.advance(prepare_extensions(self.compatibility, (pack,)), preceding)
                first = runs[0].diagnoses[0]
                second = replace(first, diagnosis_id=first.diagnosis_id + ".second")
                if second.repair_plan is not None:
                    second = replace(second, repair_plan=replace(
                        second.repair_plan, diagnosis_id=second.diagnosis_id,
                    ))
                runs = (replace(runs[0], diagnoses=(first, second)),)
                original = getattr(pack, method)
                calls = []

                def interrupted(project, diagnosis, data):
                    calls.append(diagnosis.diagnosis_id)
                    if diagnosis is second:
                        raise ExtensionFailure(pack.id, "synthetic second finding failure")
                    return original(project, diagnosis, data)

                setattr(pack, method, interrupted)
                after = run_extension_stage(runs, stage, self.project, self.environment)[0]
                self.assertEqual(after.diagnoses, runs[0].diagnoses)
                self.assertEqual(after.evidence, runs[0].evidence)
                self.assertEqual(after.failures[0].stage, stage)
                self.assertEqual(calls, [first.diagnosis_id, second.diagnosis_id])

    def test_runtime_compatibility_matrix_does_not_change_registration(self):
        changes = ({}, {"api_version": "2"}, {"supported_platforms": ("linux",)},
                   {"required_tools": ("synthetic-tool",)})
        expected = (None, CompatibilityStatus.API_VERSION_MISMATCH,
                    CompatibilityStatus.UNSUPPORTED_PLATFORM, CompatibilityStatus.MISSING_REQUIRED_TOOL)
        for fields, status in zip(changes, expected):
            with self.subTest(fields=fields):
                pack = RecordingExtension()
                pack.metadata = replace(pack.metadata, **fields)
                registry = PackRegistry((pack,))
                runs = self.advance(prepare_extensions(self.compatibility, registry.snapshot()))
                self.assertIs(registry.get(pack.id), pack)
                if status is None:
                    self.assertEqual(runs[0].failures, ())
                    self.assertEqual(pack.trace[-1], (pack.id, Capability.VERIFY))
                else:
                    self.assertEqual(runs[0].failures[0].status, status)
                    self.assertEqual(pack.trace, [])

    def test_repeated_full_pipeline_results_have_identical_order_and_plans(self):
        registry = PackRegistry((RecordingExtension("test.first"), RecordingExtension("test.last")))
        first = self.advance(prepare_extensions(self.compatibility, registry.snapshot()))
        second = self.advance(prepare_extensions(self.compatibility, registry.snapshot()))
        self.assertEqual(merge_detection(first), merge_detection(second))
        self.assertEqual(merge_results(first), merge_results(second))
        result = merge_results(first)
        self.assertEqual([item.source for item in result.diagnoses], ["test.first", "test.last"])
        self.assertEqual([item.evidence_id for item in result.evidence],
                         ["test.first:collected", "test.first:derived", "test.last:collected", "test.last:derived"])
        self.assertEqual([item.repair_plan.id for item in result.diagnoses],
                         ["test.first:plan", "test.last:plan"])

    def test_pipeline_control_signals_and_unexpected_errors_propagate_at_every_stage(self):
        for error in (KeyboardInterrupt, SystemExit, GeneratorExit, RuntimeError):
            for stage in STAGES:
                with self.subTest(error=error, stage=stage):
                    pack = RecordingExtension(errors={stage: error()})
                    with self.assertRaises(error):
                        self.advance(prepare_extensions(self.compatibility, (pack,)))

    def test_declared_failure_results_do_not_expose_raw_exception_text(self):
        private_text = str(ROOT / "synthetic-home" / "site-packages" / "private.py") + " synthetic-private-value"
        for error in (ExtensionFailure, ExtensionUnavailable, ExtensionIncompatible):
            for stage in STAGES:
                with self.subTest(error=error, stage=stage):
                    pack = RecordingExtension(errors={stage: error("test.first", private_text)})
                    result = merge_results(self.advance(prepare_extensions(self.compatibility, (pack,))))
                    self.assertNotIn(private_text, repr(result.failures))
                    self.assertNotIn("synthetic-private-value", result.failures[0].message)
                    self.assertEqual(result.failures[0].stage, stage)


class ImportIsolationTests(unittest.TestCase):
    def test_sdk_and_discovery_import_without_loading_packs_or_mutating_environment(self):
        script = """
from importlib import metadata
import os, subprocess, sys
from unittest.mock import patch
sys.path.insert(0, sys.argv[1])
environment, paths = dict(os.environ), list(sys.path)
with patch.object(metadata, 'entry_points', side_effect=AssertionError('automatic discovery')), \\
     patch.object(subprocess, 'Popen', side_effect=AssertionError('automatic execution')), \\
     patch.object(os, 'system', side_effect=AssertionError('automatic shell')):
    import agent_doctor.pack_discovery
    import agent_doctor.sdk as sdk
    for name in sdk.__all__:
        assert getattr(sdk, name) is not None
assert dict(os.environ) == environment and sys.path == paths
assert all('agent_doctor.' + name not in sys.modules for name in
           ('extension_pipeline', 'python_extension', 'workflow', 'cli', 'commands'))
"""
        process = subprocess.run([sys.executable, "-I", "-B", "-c", script, str(ROOT / "src")],
                                 capture_output=True, text=True, timeout=15)
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertEqual(process.stdout, "")


if __name__ == "__main__":
    unittest.main()
