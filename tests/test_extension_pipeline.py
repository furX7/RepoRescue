"""Static stage dispatch, local failures, Core ordering and preview ownership."""

import io
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from agent_doctor.cli import main
from agent_doctor.extension_pipeline import (
    BUILTIN_EXTENSIONS, merge_detection, merge_results, prepare_extensions,
    run_extension_stage,
)
from agent_doctor.extensions import (
    Capability, CompatibilityStatus, ExtensionEnvironment, ExtensionFailure,
    ExtensionIncompatible, ExtensionMetadata, ExtensionUnavailable,
    check_compatibility,
)
from agent_doctor.models import (
    CommandProposal, DetectionResult, DiagnosisResult, Evidence, RepairAction,
    RepairPlan, VerificationStep,
)
from agent_doctor.project import inspect_environment, scan_project
from agent_doctor.python_extension import PythonCoreExtension
from agent_doctor.workflow import WorkflowError, run_workflow


STAGES = (Capability.DETECT, Capability.INSPECT, Capability.DIAGNOSE,
          Capability.PLAN_REPAIR, Capability.VERIFY)


class RecordingExtension:
    """Test-only knowledge pack, implemented without a Python rule or executor."""

    def __init__(self, identifier="test.first", *, trace=None, errors=None):
        self.metadata = ExtensionMetadata(
            identifier, "Recording Pack", "0.1", "1", frozenset(Capability),
            ("windows",), (),
        )
        self.trace = [] if trace is None else trace
        self.errors = {} if errors is None else errors
        self.inputs = []
        self.proposals = ()

    @property
    def id(self):
        return self.metadata.id

    def record(self, stage, *inputs):
        self.trace.append((self.id, stage))
        self.inputs.append(inputs)
        if stage in self.errors:
            raise self.errors[stage]

    def detect(self, project):
        self.record(Capability.DETECT, project)
        return DetectionResult("likely", (self.id + ".marker",))

    def collect(self, project, environment):
        self.record(Capability.INSPECT, project, environment)
        return (Evidence(self.id + ":collected", "test", self.id, "Snapshot observation"),)

    def propose_diagnostic_commands(self, project, environment):
        self.record("propose", project, environment)
        return self.proposals

    def diagnose(self, project, detection, environment, evidence, executions=()):
        self.record(Capability.DIAGNOSE, project, detection, environment, evidence, executions)
        own = tuple(item for item in evidence if item.source == self.id)
        derived = Evidence(self.id + ":derived", "test", self.id, "Derived observation")
        finding = DiagnosisResult(
            "Test finding", "test", "WARNING", 0.5, self.id,
            evidence_refs=(own[0].evidence_id, derived.evidence_id), diagnosis_id=self.id + ":finding",
        )
        return (finding,), (*own, derived)

    def plan(self, project, diagnosis, evidence):
        self.record(Capability.PLAN_REPAIR, project, diagnosis, evidence)
        return RepairPlan(
            self.id + ":plan", diagnosis.diagnosis_id, "Review only", "LOW",
            (RepairAction(self.id + ":review", "Review observations", (), True, True),),
            (VerificationStep(self.id + ":initial", "Initial preview", "manual"),),
        )

    def build_verification(self, project, diagnosis, repair_plan):
        self.record(Capability.VERIFY, project, diagnosis, repair_plan)
        return (VerificationStep(self.id + ":verify", "Review the intended observation", "manual"),)


class PipelineTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.project = scan_project(self.root)
        self.environment = inspect_environment(self.project)
        self.descriptor = ExtensionEnvironment("windows", frozenset({"python"}))

    def prepare(self, *extensions):
        return prepare_extensions(self.descriptor, extensions)

    def advance(self, runs, *stages):
        for stage in stages:
            runs = run_extension_stage(runs, stage, self.project, self.environment)
        return runs

    def complete(self, *extensions):
        return self.advance(self.prepare(*extensions), *STAGES)

    def test_static_builtin_list_contains_only_python(self):
        self.assertIsInstance(BUILTIN_EXTENSIONS, tuple)
        self.assertEqual(len(BUILTIN_EXTENSIONS), 1)
        self.assertIsInstance(BUILTIN_EXTENSIONS[0], PythonCoreExtension)

    def test_builtin_metadata(self):
        metadata = BUILTIN_EXTENSIONS[0].metadata
        self.assertEqual((metadata.id, metadata.name, metadata.version, metadata.api_version),
                         ("python.core", "Python Core Pack", "0.1", "1"))
        self.assertEqual(metadata.supported_platforms, ("windows",))
        self.assertEqual(metadata.required_tools, ("python",))

    def test_duplicate_ids_rejected_before_compatibility_or_calls(self):
        first, duplicate = RecordingExtension(), RecordingExtension()
        with patch("agent_doctor.extension_pipeline.check_compatibility") as checker:
            with self.assertRaisesRegex(ValueError, "Duplicate built-in extension id"):
                self.prepare(first, duplicate)
        checker.assert_not_called()
        self.assertEqual(first.trace, [])
        self.assertEqual(duplicate.trace, [])

    def test_compatibility_checker_is_used(self):
        extension = RecordingExtension()
        with patch("agent_doctor.extension_pipeline.check_compatibility", wraps=check_compatibility) as checker:
            runs = self.prepare(extension)
        checker.assert_called_once_with(extension.metadata, self.descriptor)
        self.assertEqual(runs[0].failures, ())

    def test_all_incompatible_reasons_skip_every_stage(self):
        cases = (
            ({"api_version": "2"}, CompatibilityStatus.API_VERSION_MISMATCH),
            ({"supported_platforms": ("linux",)}, CompatibilityStatus.UNSUPPORTED_PLATFORM),
            ({"required_tools": ("missing-tool",)}, CompatibilityStatus.MISSING_REQUIRED_TOOL),
        )
        for changes, status in cases:
            with self.subTest(status=status):
                extension = RecordingExtension()
                extension.metadata = replace(extension.metadata, **changes)
                run = self.complete(extension)[0]
                self.assertEqual(extension.trace, [])
                self.assertEqual(run.failures[0].status, status)
                self.assertIsNone(run.failures[0].stage)
                self.assertEqual(run.failures[0].extension_id, extension.id)
                if status is CompatibilityStatus.MISSING_REQUIRED_TOOL:
                    self.assertEqual(run.failures[0].missing_tools, ("missing-tool",))

    def test_detector_called_through_stage(self):
        extension = RecordingExtension()
        runs = self.advance(self.prepare(extension), Capability.DETECT)
        self.assertEqual(extension.trace, [(extension.id, Capability.DETECT)])
        self.assertEqual(runs[0].detection, DetectionResult("likely", (extension.id + ".marker",)))

    def test_evidence_provider_and_pure_proposals_called_through_inspect(self):
        extension = RecordingExtension()
        run = self.advance(self.prepare(extension), Capability.INSPECT)[0]
        self.assertEqual(extension.trace, [(extension.id, Capability.INSPECT), (extension.id, "propose")])
        self.assertEqual(run.evidence[0].evidence_id, extension.id + ":collected")
        self.assertEqual(run.command_proposals, ())

    def test_inspection_does_not_require_optional_proposal_method(self):
        class EvidenceOnly:
            metadata = replace(RecordingExtension().metadata, capabilities=frozenset({Capability.INSPECT}))
            id = metadata.id

            def collect(self, project, environment):
                return (Evidence("only:evidence", "test", self.id, "Snapshot only"),)

        run = self.advance(self.prepare(EvidenceOnly()), Capability.INSPECT)[0]
        self.assertEqual(run.evidence[0].evidence_id, "only:evidence")
        self.assertEqual(run.command_proposals, ())

    def test_diagnosis_dual_return_preserves_derived_evidence_without_duplicates(self):
        extension = RecordingExtension()
        runs = self.advance(self.prepare(extension), *STAGES[:3])
        result = merge_results(runs)
        self.assertEqual([item.evidence_id for item in result.evidence],
                         [extension.id + ":collected", extension.id + ":derived"])
        self.assertEqual(result.diagnoses[0].evidence_refs,
                         tuple(item.evidence_id for item in result.evidence))

    def test_repair_planner_result_attached_to_its_diagnosis(self):
        run = self.advance(self.prepare(RecordingExtension()), *STAGES[:4])[0]
        finding = run.diagnoses[0]
        self.assertEqual(finding.repair_plan.diagnosis_id, finding.diagnosis_id)
        self.assertEqual(finding.repair_plan.execution_status, "not_executed")

    def test_verifier_result_replaces_initial_steps_on_its_plan(self):
        extension = RecordingExtension()
        finding = self.complete(extension)[0].diagnoses[0]
        self.assertEqual(finding.repair_plan.verification_steps[0].id, extension.id + ":verify")
        self.assertEqual(finding.repair_plan.verification_steps[0].status, "not_run")
        self.assertEqual(finding.repair_plan.diagnosis_id, finding.diagnosis_id)

    def test_stages_do_not_call_other_stages_implicitly(self):
        extension = RecordingExtension()
        self.advance(self.prepare(extension), Capability.DETECT)
        self.assertEqual(len(extension.trace), 1)

    def test_capabilities_gate_dispatch(self):
        extension = RecordingExtension()
        extension.metadata = replace(extension.metadata, capabilities=frozenset({Capability.DETECT}))
        self.complete(extension)
        self.assertEqual(extension.trace, [(extension.id, Capability.DETECT)])

    def test_deterministic_extension_and_stage_order(self):
        trace = []
        first = RecordingExtension("test.first", trace=trace)
        second = RecordingExtension("test.second", trace=trace)
        self.complete(first, second)
        expected = []
        for stage in STAGES:
            for extension in (first, second):
                expected.append((extension.id, stage))
                if stage is Capability.INSPECT:
                    expected.append((extension.id, "propose"))
        self.assertEqual(trace, expected)

    def test_evidence_and_diagnosis_append_order(self):
        result = merge_results(self.complete(RecordingExtension("first"), RecordingExtension("second")))
        self.assertEqual([item.evidence_id for item in result.evidence],
                         ["first:collected", "first:derived", "second:collected", "second:derived"])
        self.assertEqual([item.source for item in result.diagnoses], ["first", "second"])

    def test_detection_match_order(self):
        runs = self.advance(self.prepare(RecordingExtension("first"), RecordingExtension("second")),
                            Capability.DETECT)
        self.assertEqual(merge_detection(runs), DetectionResult("likely", ("first.marker", "second.marker")))

    def test_command_proposals_append_in_extension_order_without_execution(self):
        first, second = RecordingExtension("first"), RecordingExtension("second")
        first.proposals = (CommandProposal("first-tool", (), self.root, first.id, "inspection"),)
        second.proposals = (CommandProposal("second-tool", (), self.root, second.id, "inspection"),)
        with patch("subprocess.run", side_effect=AssertionError("no execution")), \
                patch("subprocess.Popen", side_effect=AssertionError("no execution")):
            runs = self.advance(self.prepare(first, second), Capability.INSPECT)
        self.assertEqual(merge_results(runs).command_proposals, (*first.proposals, *second.proposals))

    def test_declared_errors_are_isolated_at_every_stage(self):
        for error_type, status in ((ExtensionUnavailable, "unavailable"),
                                   (ExtensionIncompatible, "incompatible"), (ExtensionFailure, "failure")):
            for stage in STAGES:
                with self.subTest(error_type=error_type, stage=stage):
                    broken = RecordingExtension("broken", errors={stage: error_type("broken", "local failure")})
                    good = RecordingExtension("good")
                    runs = self.complete(broken, good)
                    failure = merge_results(runs).failures[0]
                    self.assertEqual((failure.extension_id, failure.stage, failure.status, failure.message),
                                     ("broken", stage, status, f"Extension stage reported {status}."))
                    self.assertEqual(runs[1].failures, ())
                    self.assertEqual(runs[1].diagnoses[0].source, "good")
                    self.assertEqual(good.trace[-1], ("good", Capability.VERIFY))
                    self.assertEqual(broken.trace[-1], ("broken", stage))

    def test_prior_completed_outputs_survive_a_later_failure(self):
        broken = RecordingExtension(errors={Capability.DIAGNOSE: ExtensionFailure("test.first", "failed")})
        result = merge_results(self.complete(broken))
        self.assertEqual(len(result.evidence), 1)
        self.assertEqual(result.diagnoses, ())

    def test_optional_proposal_error_is_an_inspection_failure(self):
        broken = RecordingExtension(errors={"propose": ExtensionUnavailable("test.first", "no probe")})
        result = merge_results(self.complete(broken))
        self.assertEqual(result.failures[0].stage, Capability.INSPECT)
        self.assertEqual(result.evidence, ())  # No partially completed stage batch.
        self.assertEqual(result.command_proposals, ())

    def test_keyboard_interrupt_and_system_exit_are_never_swallowed(self):
        for error_type in (KeyboardInterrupt, SystemExit):
            for stage in STAGES:
                with self.subTest(error_type=error_type, stage=stage):
                    extension = RecordingExtension(errors={stage: error_type()})
                    with self.assertRaises(error_type):
                        self.complete(extension)

    def test_unexpected_exceptions_are_not_silently_swallowed(self):
        extension = RecordingExtension(errors={Capability.DETECT: RuntimeError("programming error")})
        with self.assertRaisesRegex(RuntimeError, "programming error"):
            self.complete(extension)

    def test_wrong_plan_diagnosis_is_rejected(self):
        extension = RecordingExtension()
        original = extension.plan
        extension.plan = lambda *args: replace(original(*args), diagnosis_id="another:finding")
        result = merge_results(self.complete(extension))
        self.assertEqual(result.failures[0].stage, Capability.PLAN_REPAIR)
        self.assertIsNone(result.diagnoses[0].repair_plan)

        # A verifier-only pack can receive an embedded preview from diagnosis;
        # the same ownership check must apply without a planner capability.
        extension = RecordingExtension()
        extension.metadata = replace(extension.metadata,
                                     capabilities=frozenset(Capability) - {Capability.PLAN_REPAIR})
        diagnose = extension.diagnose

        def embedded_preview(*args):
            findings, evidence = diagnose(*args)
            finding = findings[0]
            plan = RepairPlan("wrong:plan", "another:finding", "Preview", "LOW", (),
                              (VerificationStep("wrong:verify", "Review", "manual"),))
            return (replace(finding, repair_plan=plan),), evidence

        extension.diagnose = embedded_preview
        result = merge_results(self.complete(extension))
        self.assertEqual(result.failures[0].stage, Capability.VERIFY)

    def test_empty_verification_is_rejected_without_destroying_prior_preview(self):
        extension = RecordingExtension()
        extension.build_verification = lambda *args: ()
        result = merge_results(self.complete(extension))
        self.assertEqual(result.failures[0].stage, Capability.VERIFY)
        self.assertEqual(result.diagnoses[0].repair_plan.verification_steps[0].status, "not_run")

    def test_planless_verification_is_not_silently_lost(self):
        extension = RecordingExtension()
        extension.plan = lambda *args: None
        result = merge_results(self.complete(extension))
        self.assertEqual(result.failures[0].stage, Capability.VERIFY)
        self.assertIsNone(result.diagnoses[0].repair_plan)

    def test_no_executor_or_project_mutation(self):
        extension = RecordingExtension()
        target = self.root / "keep.txt"
        target.write_bytes(b"unchanged")
        before, environment = (target.read_bytes(), target.stat().st_mtime_ns), dict(os.environ)
        with patch("subprocess.run", side_effect=AssertionError("no process")), \
                patch("subprocess.Popen", side_effect=AssertionError("no process")):
            self.complete(extension)
        self.assertEqual((target.read_bytes(), target.stat().st_mtime_ns), before)
        self.assertEqual(list(self.root.iterdir()), [target])
        self.assertEqual(dict(os.environ), environment)
        for inputs in extension.inputs:
            for value in inputs:
                self.assertFalse(callable(value))
                for forbidden in ("executor", "shell", "execute"):
                    self.assertFalse(hasattr(value, forbidden))


class PipelineWorkflowTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()

    def test_workflow_explicitly_controls_the_five_stages(self):
        with patch("agent_doctor.workflow.run_extension_stage", wraps=run_extension_stage) as dispatch:
            result = run_workflow(self.root)
        self.assertEqual([call.args[1] for call in dispatch.call_args_list], list(STAGES))
        self.assertEqual(result.extension_failures, ())

    def test_python_facade_all_five_methods_are_used(self):
        called = []
        originals = {name: getattr(PythonCoreExtension, name) for name in (
            "detect", "collect", "diagnose", "plan", "build_verification",
        )}

        def recording(name):
            def invoke(instance, *args, **kwargs):
                called.append(name)
                return originals[name](instance, *args, **kwargs)
            return invoke

        from contextlib import ExitStack
        with ExitStack() as stack:
            for name in originals:
                stack.enter_context(patch.object(PythonCoreExtension, name, recording(name)))
            run_workflow(self.root)
        self.assertEqual(called, list(originals))

    def test_core_executes_proposals_between_inspection_and_diagnosis(self):
        (self.root / "main.py").write_bytes(b"raise RuntimeError('must not run')")
        events = []
        from agent_doctor.commands import execute_command

        def dispatch(runs, stage, *args, **kwargs):
            events.append(stage)
            return run_extension_stage(runs, stage, *args, **kwargs)

        def execute(*args, **kwargs):
            events.append("execute")
            return execute_command(*args, **kwargs)

        with patch("agent_doctor.workflow.run_extension_stage", side_effect=dispatch), \
                patch("agent_doctor.workflow.execute_command", side_effect=execute), \
                patch("agent_doctor.commands._run_startup_process") as startup:
            run_workflow(self.root)
        startup.assert_not_called()
        self.assertEqual(events, [Capability.DETECT, Capability.INSPECT, "execute", *STAGES[2:]])

    def test_workflow_is_generic_for_two_statically_supplied_packs(self):
        first, second = RecordingExtension("first"), RecordingExtension("second")
        with patch("agent_doctor.extension_pipeline.BUILTIN_EXTENSIONS", (first, second)):
            result = run_workflow(self.root)
        self.assertEqual([item.source for item in result.diagnostics], ["first", "second"])
        self.assertEqual(result.detection.matched_files, ("first.marker", "second.marker"))
        self.assertEqual(result.extension_failures, ())

    def test_workflow_returns_other_pack_results_after_local_failure(self):
        broken = RecordingExtension("broken", errors={Capability.DIAGNOSE: ExtensionFailure("broken", "failed")})
        with patch("agent_doctor.extension_pipeline.BUILTIN_EXTENSIONS", (broken, RecordingExtension("good"))):
            result = run_workflow(self.root)
        self.assertEqual([item.source for item in result.diagnostics], ["good"])
        self.assertEqual(result.extension_failures[0].extension_id, "broken")
        self.assertNotIn("extensions", result.report)

    def test_only_failed_pack_returns_controlled_limitation_not_extension_exception(self):
        for error_type in (ExtensionUnavailable, ExtensionIncompatible, ExtensionFailure):
            with self.subTest(error_type=error_type):
                broken = RecordingExtension(errors={Capability.DIAGNOSE: error_type("test.first", "failed")})
                with patch("agent_doctor.extension_pipeline.BUILTIN_EXTENSIONS", (broken,)):
                    with self.assertRaisesRegex(WorkflowError, "No built-in diagnosis extension") as stopped:
                        run_workflow(self.root)
                self.assertEqual(stopped.exception.extension_failures[0].stage, Capability.DIAGNOSE)

    def test_incompatible_only_pack_is_a_cli_tool_error_without_execution(self):
        extension = RecordingExtension()
        extension.metadata = replace(extension.metadata, api_version="2")
        output, error = io.StringIO(), io.StringIO()
        with patch("agent_doctor.extension_pipeline.BUILTIN_EXTENSIONS", (extension,)), \
                patch("agent_doctor.workflow.execute_command") as execute, \
                patch("agent_doctor.workflow.execute_startup_probe") as startup, \
                redirect_stdout(output), redirect_stderr(error):
            code = main([str(self.root)])
        self.assertEqual(code, 2)
        self.assertEqual(output.getvalue(), "")
        self.assertIn("api_version_mismatch", error.getvalue())
        self.assertNotIn("internal error", error.getvalue())
        execute.assert_not_called()
        startup.assert_not_called()

    def test_missing_python_tool_is_reported_without_running_extension(self):
        from agent_doctor.project import inspect_environment
        project = scan_project(self.root)
        environment = replace(inspect_environment(project), python_available=False, python_executable=None)
        with patch("agent_doctor.workflow.inspect_environment", return_value=environment), \
                patch.object(PythonCoreExtension, "detect") as detect:
            with self.assertRaises(WorkflowError) as stopped:
                run_workflow(self.root)
        detect.assert_not_called()
        self.assertEqual(stopped.exception.extension_failures[0].status,
                         CompatibilityStatus.MISSING_REQUIRED_TOOL)

    def test_duplicate_builtin_configuration_is_a_controlled_workflow_error(self):
        extension = RecordingExtension()
        with patch("agent_doctor.extension_pipeline.BUILTIN_EXTENSIONS", (extension, extension)):
            with self.assertRaisesRegex(WorkflowError, "Duplicate built-in extension id"):
                run_workflow(self.root)

    def test_internal_failures_do_not_extend_machine_schema(self):
        broken = RecordingExtension(errors={Capability.VERIFY: ExtensionFailure("test.first", "failed")})
        with patch("agent_doctor.extension_pipeline.BUILTIN_EXTENSIONS", (broken,)):
            result = run_workflow(self.root)
        self.assertEqual(result.report["schema_version"], "0.2")
        self.assertTrue(result.extension_failures)
        for field in ("extensions", "plugins", "packs", "extension_api_version", "extension_failures"):
            self.assertNotIn(field, result.report)


if __name__ == "__main__":
    unittest.main()
