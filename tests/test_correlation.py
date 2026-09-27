"""Adversarial contracts for Step 6: uncertainty, traceability and no execution."""

from contextlib import redirect_stdout
from dataclasses import replace
from datetime import datetime, timezone
import io
import json
from pathlib import Path
import random
import tempfile
import unittest
from unittest.mock import patch

from agent_doctor.correlation import (
    apply_correlations, accept_enrichment, CorrelationProposal, EvidenceIndex, valid_result, MAX_EVIDENCE, MAX_DIAGNOSES,
)
from agent_doctor.models import (
    Evidence, DiagnosisResult, CorrelationResult, RootCauseStep,
    ProjectInfo, DetectionResult, EnvironmentInfo,
)
from agent_doctor.python_correlation import correlate_python, _path, _within
from agent_doctor.report import build_json_report, render_terminal_report
from agent_doctor.workflow import run_workflow
from agent_doctor.cli import main
from agent_doctor.python_extension import PythonCoreExtension
from agent_doctor.extensions import ExtensionFailure, ExtensionUnavailable, ExtensionIncompatible


def fact(identifier, kind, **metadata):
    return Evidence(identifier, kind, 'test', 'Literal fixture', metadata=metadata)


def fixture(symbol=True, native=False):
    failure = replace(fact('failure', 'python_import_failure',
        source_module='demo' if symbol else None, missing_module=None if symbol else 'demo',
        imported_symbol='wanted' if symbol else None,
        status='symbol_import_failure' if symbol else 'missing_module',
        raw_message="ImportError: cannot import name 'wanted' from 'demo'",
        runtime_executable='C:/runtime/python.exe'), source='captured_execution_output', associated_id='execution')
    diagnosis = DiagnosisResult('Observed import failure', 'python_import', 'ERROR', 1.0, 'rule:fixture',
        evidence_refs=('failure',), diagnosis_id='failure',
        root_cause_chain=(RootCauseStep('original', 'Original failure', 'Observed exception.', ('failure',)),))
    items = [failure, fact('execution', 'execution', executable='C:/runtime/python.exe'),
        fact('interpreter', 'python_interpreter', status='available', executable='C:/runtime/python.exe', prefix='C:/runtime'),
        fact('local', 'local_python_environment', status='different', current_python_executable='C:/runtime/python.exe',
             detected_local_environment='.venv',
             detected_interpreter_path='C:/project/.venv/Scripts/python.exe'),
        fact('origin', 'python_module_origin', status='available', module='demo', trigger_evidence_refs=('failure',),
             origin='C:/runtime/Lib/site-packages/demo/native.pyd' if native else 'C:/runtime/Lib/site-packages/demo/__init__.py'),
        fact('mapping', 'python_distribution_mapping', status='available', module='demo', candidate_distribution_refs=('distribution',)),
        fact('distribution', 'python_distribution', status='available', name='Demo', installed_version='2.0', interpreter_ref='interpreter'),
        fact('api', 'python_api', status='available', module='demo', requested_symbol='wanted', source_evidence_ref='failure',
             module_origin_ref='origin', symbol_observation='not_seen_in_direct_syntax', api_compatibility='unknown'),
        fact('runtime', 'python_abi_platform', status='available', interpreter_ref='interpreter'),
        fact('wheel', 'python_wheel', status='available', name='Demo', source_evidence_ref='distribution',
             runtime_evidence_ref='runtime', compatibility_scope='metadata_tags_only', tag_check='tag_mismatch', runtime_compatibility='unknown')]
    return diagnosis, items


def updated(items, identifier, **metadata):
    return [replace(item, metadata=dict(item.metadata, **metadata)) if item.evidence_id == identifier else item for item in items]


def correlations(diagnosis):
    return {item.diagnosis_type: item for item in diagnosis.correlations}


class PythonCorrelationTests(unittest.TestCase):
    def setUp(self):
        self.diagnosis, self.items = fixture()

    def run_rule(self, items=None, diagnosis=None):
        return correlate_python((diagnosis or self.diagnosis,), self.items if items is None else items)[0]

    def test_complete_environment_chain_retains_intention_limitation(self):
        result = self.run_rule()
        corr = correlations(result)['interpreter_environment']
        self.assertEqual(corr.status, 'partial')
        self.assertEqual(len(corr.root_cause_chain), 3)
        self.assertIn('project_environment_intention_unproven', corr.limitations)
        self.assertEqual(corr.confidence, 'medium')

    def test_missing_origin_does_not_infer_environment_cause(self):
        result = self.run_rule([item for item in self.items if item.evidence_id != 'origin'])
        corr = correlations(result)['interpreter_environment']
        self.assertEqual(corr.status, 'inconclusive')
        self.assertEqual(corr.root_cause_chain, ())

    def test_origin_inside_project_environment_not_wrong_environment(self):
        corr = correlations(self.run_rule(updated(self.items, 'origin', origin='C:/project/.venv/Lib/demo.py')))['interpreter_environment']
        self.assertEqual(corr.root_cause_chain, ())

    def test_prefix_neighbor_is_not_membership(self):
        corr = correlations(self.run_rule(updated(self.items, 'origin', origin='C:/runtime-other/demo.py')))['interpreter_environment']
        self.assertEqual(corr.root_cause_chain, ())

    def test_provided_traceback_cannot_be_assigned_to_current_interpreter(self):
        items = [replace(item, source='provided_traceback') if item.evidence_id == 'failure' else item for item in self.items]
        for corr in self.run_rule(items).correlations:
            self.assertEqual(corr.root_cause_chain, ())

    def test_different_execution_interpreter_prevents_current_environment_chain(self):
        corr = correlations(self.run_rule(updated(self.items, 'execution', executable='C:/other/python.exe')))['interpreter_environment']
        self.assertEqual(corr.root_cause_chain, ())

    def test_origin_requires_failure_trigger_reference(self):
        corr = correlations(self.run_rule(updated(self.items, 'origin', trigger_evidence_refs=('unrelated',))))['interpreter_environment']
        self.assertEqual(corr.root_cause_chain, ())

    def test_native_tag_mismatch_is_only_eligibility_context(self):
        diagnosis, items = fixture(native=True)
        corr = correlations(self.run_rule(items, diagnosis))['native_artifact']
        self.assertEqual(corr.status, 'partial')
        self.assertEqual(len(corr.root_cause_chain), 2)
        self.assertIn('wheel_tags_do_not_prove_native_binary_abi', corr.limitations)
        self.assertNotIn('ABI incompatible', corr.explanation)

    def test_native_missing_wheel_never_infers_abi(self):
        diagnosis, items = fixture(symbol=False, native=True)
        corr = correlations(self.run_rule([item for item in items if item.evidence_id != 'wheel'], diagnosis))['native_artifact']
        self.assertEqual(corr.root_cause_chain, ())
        self.assertIn('explicit_abi_compatibility_evidence_missing', corr.limitations)

    def test_missing_private_module_has_no_abi_chain(self):
        diagnosis, items = fixture(symbol=False)
        result = self.run_rule(updated(items, 'failure', missing_module='_demo'), diagnosis)
        self.assertNotIn('native_artifact', correlations(result))

    def test_matching_tags_do_not_explain_native_import_failure(self):
        diagnosis, items = fixture(native=True)
        corr = correlations(self.run_rule(updated(items, 'wheel', tag_check='tag_match'), diagnosis))['native_artifact']
        self.assertEqual(corr.root_cause_chain, ())

    def test_unknown_tags_do_not_explain_native_import_failure(self):
        diagnosis, items = fixture(native=True)
        corr = correlations(self.run_rule(updated(items, 'wheel', tag_check='unknown'), diagnosis))['native_artifact']
        self.assertEqual(corr.root_cause_chain, ())

    def test_wrong_wheel_runtime_reference_rejected(self):
        diagnosis, items = fixture(native=True)
        corr = correlations(self.run_rule(updated(items, 'wheel', runtime_evidence_ref='unrelated'), diagnosis))['native_artifact']
        self.assertEqual(corr.root_cause_chain, ())

    def test_wheel_log_artifact_not_installed_binary(self):
        diagnosis, items = fixture(native=True)
        corr = correlations(self.run_rule(updated(items, 'wheel', source_evidence_ref='log'), diagnosis))['native_artifact']
        self.assertEqual(corr.root_cause_chain, ())

    def test_static_symbol_absence_never_becomes_runtime_api_unavailable(self):
        corr = correlations(self.run_rule())['dependency_api']
        self.assertEqual(corr.status, 'partial')
        self.assertIn('static_syntax_cannot_establish_runtime_exports', corr.limitations)
        self.assertNotEqual(corr.status, 'correlated')

    def test_api_unavailable_means_collection_failure_not_symbol_absence(self):
        corr = correlations(self.run_rule(updated(self.items, 'api', status='unavailable')))['dependency_api']
        self.assertEqual(corr.root_cause_chain, ())

    def test_api_unknown_does_not_generate_api_cause(self):
        corr = correlations(self.run_rule(updated(self.items, 'api', status='unknown')))['dependency_api']
        self.assertEqual(corr.status, 'inconclusive')

    def test_api_symbol_identity_must_match_failure(self):
        corr = correlations(self.run_rule(updated(self.items, 'api', requested_symbol='other')))['dependency_api']
        self.assertEqual(corr.root_cause_chain, ())

    def test_api_origin_identity_must_match(self):
        corr = correlations(self.run_rule(updated(self.items, 'api', module_origin_ref='other')))['dependency_api']
        self.assertEqual(corr.root_cause_chain, ())

    def test_ambiguous_distribution_does_not_choose_candidate(self):
        corr = correlations(self.run_rule(updated(self.items, 'mapping', status='ambiguous', candidate_distribution_refs=('distribution', 'other'))))['dependency_api']
        self.assertEqual(corr.status, 'ambiguous')
        self.assertEqual(corr.root_cause_chain, ())

    def test_missing_distribution_reference_not_used(self):
        corr = correlations(self.run_rule(updated(self.items, 'mapping', candidate_distribution_refs=('missing',))))['dependency_api']
        self.assertEqual(corr.root_cause_chain, ())

    def test_distribution_unavailable_not_upgraded(self):
        corr = correlations(self.run_rule(updated(self.items, 'distribution', status='unavailable')))['dependency_api']
        self.assertEqual(corr.root_cause_chain, ())

    def test_conflicting_api_rows_are_ambiguous(self):
        items = [*self.items, replace(next(item for item in self.items if item.evidence_id == 'api'), evidence_id='api2')]
        corr = correlations(self.run_rule(items))['dependency_api']
        self.assertEqual(corr.status, 'ambiguous')
        self.assertEqual(corr.root_cause_chain, ())

    def test_different_provenance_categories_are_context_only(self):
        items = self.items + [fact('declared', 'python_version_provenance', name='demo', provenance='declared', value='>=1', status='available'),
                             fact('locked', 'python_version_provenance', name='demo', provenance='locked', value='1.0', status='available'),
                             fact('installed', 'python_version_provenance', name='demo', provenance='installed', value='2.0', status='available')]
        corr = correlations(self.run_rule(items))['version_provenance']
        self.assertEqual(corr.status, 'partial')
        self.assertIn('version_values_are_context_only', corr.limitations)
        self.assertTrue({'declared', 'locked', 'installed'} <= set(corr.evidence_refs))

    def test_conflicting_provenance_all_sources_retained(self):
        items = self.items + [fact('pin1', 'python_version_provenance', name='demo', provenance='locked', value='1.0', status='available'),
                             fact('pin2', 'python_version_provenance', name='Demo', provenance='locked', value='2.0', status='available')]
        corr = correlations(self.run_rule(items))['version_provenance']
        self.assertEqual(corr.status, 'ambiguous')
        self.assertEqual(corr.root_cause_chain, ())
        self.assertTrue({'pin1', 'pin2'} <= set(corr.evidence_refs))

    def test_unavailable_provenance_is_inconclusive(self):
        items = self.items + [fact('pin', 'python_version_provenance', name='demo', provenance='locked', value=None, status='unavailable')]
        corr = correlations(self.run_rule(items))['version_provenance']
        self.assertEqual(corr.status, 'inconclusive')
        self.assertIn('version_provenance_incomplete', corr.limitations)

    def test_startup_links_only_same_confirmed_execution(self):
        execution = next(item for item in self.items if item.evidence_id == 'execution')
        items = [replace(item, location='C:/project', metadata=dict(item.metadata, arguments=('C:/project/main.py',)))
                 if item is execution else item for item in self.items]
        startup = fact('startup', 'startup_probe', executed=True, execution_status='failed', exit_code=1,
                       argv=('C:/runtime/python.exe', 'C:/project/main.py'), cwd='C:/project')
        result = self.run_rule([*items, startup])
        corr = correlations(result)['interpreter_environment']
        self.assertIn('startup', corr.evidence_refs)
        self.assertEqual(corr.root_cause_chain[0].relationship, 'observed')
        self.assertIn('share the captured execution', corr.root_cause_chain[0].title)
        for key, value in (('executed', False), ('cwd', 'C:/other'), ('argv', ('C:/runtime/python.exe', 'C:/project/other.py')),
                           ('execution_status', 'timeout'), ('exit_code', 0)):
            changed = replace(startup, metadata=dict(startup.metadata, **{key: value}))
            rejected = correlations(self.run_rule([*items, changed]))['interpreter_environment']
            self.assertNotIn('startup', rejected.evidence_refs)

    def test_malformed_trigger_refs_not_used(self):
        corr = correlations(self.run_rule(updated(self.items, 'origin', trigger_evidence_refs=None)))['interpreter_environment']
        self.assertEqual(corr.root_cause_chain, ())

    def test_normalization_does_not_merge_distinct_names(self):
        items = self.items + [fact('alien', 'python_version_provenance', name='demo2', provenance='locked', value='3', status='available')]
        self.assertNotIn('version_provenance', correlations(self.run_rule(items)))

    def test_version_differences_without_failure_create_no_diagnosis(self):
        items = [item for item in self.items if item.kind != 'python_import_failure']
        items += [fact('pin', 'python_version_provenance', name='demo', provenance='locked', value='1.0', status='available')]
        self.assertEqual(correlate_python((), items), ())

    def test_existing_fields_severity_confidence_and_plan_preserved(self):
        result = self.run_rule()
        for key in ('problem', 'category', 'severity', 'confidence', 'source', 'repair_plan', 'recommended_actions', 'probable_causes'):
            self.assertEqual(getattr(result, key), getattr(self.diagnosis, key))
        self.assertEqual(result.root_cause_chain[0], self.diagnosis.root_cause_chain[0])

    def test_order_independent_and_stable_ids(self):
        expected = self.run_rule()
        for seed in range(10):
            items = list(self.items)
            random.Random(seed).shuffle(items)
            self.assertEqual(self.run_rule(items), expected)

    def test_repeat_enrichment_is_idempotent(self):
        first = self.run_rule()
        self.assertEqual(self.run_rule(diagnosis=first), first)

    def test_every_ref_exists_every_parent_precedes_child(self):
        result = self.run_rule()
        index = EvidenceIndex(self.items)
        self.assertTrue(set(result.evidence_refs) <= index.items.keys())
        for corr in result.correlations:
            self.assertTrue(valid_result(corr, index))
        self.assertEqual(len({step.id for step in result.root_cause_chain}), len(result.root_cause_chain))

    def test_duplicate_evidence_ids_do_not_select_payload(self):
        items = [*self.items, replace(self.items[0], metadata=dict(self.items[0].metadata, source_module='different'))]
        self.assertEqual(self.run_rule(items), self.diagnosis)

    def test_duplicate_diagnosis_ids_do_not_enrich(self):
        self.assertEqual(correlate_python((self.diagnosis, self.diagnosis), self.items), (self.diagnosis, self.diagnosis))

    def test_resource_limit_preserves_original_diagnosis(self):
        items = self.items + [fact(f'extra:{i}', 'other') for i in range(MAX_EVIDENCE)]
        self.assertEqual(self.run_rule(items), self.diagnosis)

    def test_diagnosis_count_limit_preserves_input(self):
        diagnoses = tuple(replace(self.diagnosis, diagnosis_id=f'd:{i}') for i in range(MAX_DIAGNOSES + 1))
        self.assertEqual(correlate_python(diagnoses, self.items), diagnoses)

    def test_no_files_processes_or_import_calls(self):
        with patch('builtins.open', side_effect=AssertionError('I/O')), patch('subprocess.run', side_effect=AssertionError('execution')), patch('importlib.import_module', side_effect=AssertionError('import')):
            self.assertTrue(self.run_rule().correlations)

    def test_path_controls_relative_and_traversal_rejected(self):
        for value in ('relative/file', 'C:/runtime/../project/demo.py', 'C:/runtime/\x1bfile', None, 12):
            self.assertIsNone(_path(value))

    def test_windows_case_and_component_membership(self):
        self.assertTrue(_within('C:/RUNTIME/Lib/demo.py', 'c:/runtime'))
        self.assertFalse(_within('C:/runtime-other/demo.py', 'C:/runtime'))


class CoreCorrelationTests(unittest.TestCase):
    def setUp(self):
        self.diagnosis, self.items = fixture()
        self.result = correlations(correlate_python((self.diagnosis,), self.items)[0])['dependency_api']

    def apply(self, result):
        return apply_correlations((self.diagnosis,), self.items, (CorrelationProposal('failure', result),))[0]

    def test_dangling_evidence_rejected(self):
        self.assertEqual(self.apply(replace(self.result, evidence_refs=(*self.result.evidence_refs, 'missing'))), self.diagnosis)

    def test_cycle_rejected(self):
        steps = self.result.root_cause_chain
        self.assertEqual(self.apply(replace(self.result, root_cause_chain=(replace(steps[0], parent_id=steps[-1].id), *steps[1:]))), self.diagnosis)

    def test_duplicate_steps_rejected(self):
        step = self.result.root_cause_chain[0]
        self.assertEqual(self.apply(replace(self.result, root_cause_chain=(step, step))), self.diagnosis)

    def test_step_reference_not_in_correlation_rejected(self):
        step = self.result.root_cause_chain[0]
        self.assertEqual(self.apply(replace(self.result, root_cause_chain=(replace(step, evidence_refs=('wheel',)),))), self.diagnosis)

    def test_cannot_attach_to_unrelated_diagnosis(self):
        result = replace(self.result, evidence_refs=tuple(ref for ref in self.result.evidence_refs if ref != 'failure'), root_cause_chain=())
        self.assertEqual(self.apply(result), self.diagnosis)

    def test_duplicate_proposals_rejected(self):
        proposal = CorrelationProposal('failure', self.result)
        self.assertEqual(apply_correlations((self.diagnosis,), self.items, (proposal, proposal)), (self.diagnosis,))

    def test_optional_model_confidence_is_discrete(self):
        with self.assertRaises(ValueError):
            replace(self.result, confidence=0.99)

    def test_correlated_evidence_is_not_new_raw_evidence(self):
        before = list(self.items)
        self.apply(self.result)
        self.assertEqual(self.items, before)

    def test_core_hook_cannot_raise_severity_or_rewrite_finding(self):
        enriched = correlate_python((self.diagnosis,), self.items)[0]
        for key, value in (('severity', 'CRITICAL'), ('problem', 'Unproven new cause'), ('confidence', 0.5), ('recommended_actions', ('install',))):
            result = accept_enrichment((self.diagnosis,), (replace(enriched, **{key: value}),), self.items)
            self.assertEqual(result, (self.diagnosis,))

    def test_core_hook_cannot_remove_diagnosis(self):
        self.assertEqual(accept_enrichment((self.diagnosis,), (), self.items), (self.diagnosis,))

    def test_core_hook_cannot_add_error(self):
        new = replace(correlate_python((self.diagnosis,), self.items)[0], diagnosis_id='new')
        self.assertEqual(accept_enrichment((self.diagnosis,), (self.diagnosis, new), self.items), (self.diagnosis,))

    def test_core_hook_cannot_append_unreferenced_step(self):
        enriched = correlate_python((self.diagnosis,), self.items)[0]
        wrong = replace(enriched, root_cause_chain=(*enriched.root_cause_chain, RootCauseStep('extra', 'Guess', 'Guess', ('failure',))))
        self.assertEqual(accept_enrichment((self.diagnosis,), (wrong,), self.items), (self.diagnosis,))

    def test_core_hook_accepts_validated_enrichment(self):
        enriched = correlate_python((self.diagnosis,), self.items)[0]
        self.assertEqual(accept_enrichment((self.diagnosis,), (enriched,), self.items), (enriched,))


class ArtifactCorrelationTests(unittest.TestCase):
    def setUp(self):
        self.log = fact('log', 'provided_log', input_type='install_log', status='facts_only',
            patterns=('no_matching_distribution',), messages=('ERROR: No matching distribution found for Demo',))
        self.runtime = fact('runtime', 'python_abi_platform', status='available')
        self.wheel = fact('wheel', 'python_wheel', status='available', name='demo', source_evidence_ref='log',
            tag_check='tag_mismatch', runtime_evidence_ref='runtime', compatibility_scope='metadata_tags_only')

    def test_same_log_artifact_exclusions_current_runtime_only(self):
        result = correlate_python((), (self.log, self.runtime, self.wheel))
        self.assertEqual(len(result), 1)
        corr = result[0].correlations[0]
        self.assertEqual(corr.status, 'partial')
        self.assertEqual(len(corr.root_cause_chain), 2)
        self.assertIn('supplied_log_runtime_unproven', corr.limitations)
        self.assertIn('observed_artifacts_not_complete_index', corr.limitations)
        self.assertEqual(result[0].severity, 'INFO')

    def test_no_compatibility_evidence_retains_step2_facts_only(self):
        self.assertEqual(correlate_python((), (self.log, self.runtime)), ())

    def test_unrelated_wheel_name_not_used(self):
        wheel = replace(self.wheel, metadata=dict(self.wheel.metadata, name='different'))
        self.assertEqual(correlate_python((), (self.log, self.runtime, wheel)), ())

    def test_wheel_from_other_log_not_used(self):
        wheel = replace(self.wheel, metadata=dict(self.wheel.metadata, source_evidence_ref='other'))
        self.assertEqual(correlate_python((), (self.log, self.runtime, wheel)), ())

    def test_unknown_artifact_not_excluded(self):
        wheel = replace(self.wheel, metadata=dict(self.wheel.metadata, tag_check='unknown'))
        corr = correlate_python((), (self.log, self.runtime, wheel))[0].correlations[0]
        self.assertEqual(len(corr.root_cause_chain), 1)

    def test_conflicting_log_requirements_ambiguous(self):
        log = replace(self.log, metadata=dict(self.log.metadata, messages=(*self.log.metadata['messages'], 'ERROR: No matching distribution found for Other')))
        corr = correlate_python((), (log, self.runtime, self.wheel))[0].correlations[0]
        self.assertEqual(corr.status, 'ambiguous')
        self.assertEqual(corr.root_cause_chain, ())

    def test_overbudget_messages_do_not_choose_a_prefix(self):
        messages = ('ERROR: No matching distribution found for Demo',) * 128
        conflict = 'ERROR: No matching distribution found for Other'
        for rows in ((*messages, conflict), (conflict, *messages)):
            log = replace(self.log, metadata=dict(self.log.metadata, messages=rows))
            self.assertEqual(correlate_python((), (log, self.runtime, self.wheel)), ())

    def test_oversized_or_invalid_message_rejects_entire_artifact_context(self):
        prefix = 'ERROR: No matching distribution found for Other=='
        boundary = prefix + '1' * (4096 - len(prefix))
        base = self.log.metadata['messages']
        exact = replace(self.log, metadata=dict(self.log.metadata, messages=(*base, boundary)))
        self.assertEqual(correlate_python((), (exact, self.runtime, self.wheel))[0].correlations[0].status, 'ambiguous')
        for invalid in (boundary + '1', None):
            for rows in ((*base, invalid), (invalid, *base)):
                log = replace(self.log, metadata=dict(self.log.metadata, messages=rows))
                self.assertEqual(correlate_python((), (log, self.runtime, self.wheel)), ())


class CorrelationReportTests(unittest.TestCase):
    def setUp(self):
        diagnosis, self.items = fixture()
        self.diagnoses = correlate_python((diagnosis,), self.items)
        self.project = ProjectInfo(Path('C:/project'), datetime.now(timezone.utc))
        self.detection = DetectionResult('likely', ('main.py',))
        self.environment = EnvironmentInfo(Path('C:/runtime/python.exe'), '3.13', True, (), True)

    def test_json_refs_schema_assessment_and_exit_semantics(self):
        report = build_json_report(self.project, self.detection, self.environment, self.diagnoses, self.items)
        self.assertEqual(report['schema_version'], '0.2')
        self.assertEqual(report['status'], 'issues_detected')
        self.assertEqual(report['assessment']['outcome'], 'issues_detected')
        self.assertEqual(report['assessment']['coverage'], 'incomplete')
        self.assertTrue(any(item['check'] == 'correlation' for item in report['assessment']['limitations']))
        json.dumps(report)

    def test_terminal_json_correlation_titles_and_refs_agree(self):
        terminal = render_terminal_report(self.project, self.detection, self.environment, self.diagnoses, evidence=self.items)
        for corr in self.diagnoses[0].correlations:
            self.assertIn(corr.title, terminal)
            self.assertIn('Correlation evidence: ' + ', '.join(corr.evidence_refs), terminal)

    def test_terminal_controls_escaped(self):
        corr = replace(self.diagnoses[0].correlations[0], title='evil\x1b[2J')
        diagnoses = (replace(self.diagnoses[0], correlations=(corr,)),)
        terminal = render_terminal_report(self.project, self.detection, self.environment, diagnoses, evidence=self.items)
        self.assertNotIn('\x1b', terminal)
        self.assertIn('\\x1b', terminal)

    def test_actual_traceback_workflow_inconclusive_cause_preserves_error(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'main.py').write_text('raise AssertionError("must not execute")')
            log = root / 'trace.txt'
            log.write_text("ImportError: cannot import name 'wanted' from 'missing_step6_module'")
            result = run_workflow(root, traceback_file=log)
            self.assertEqual(result.report['status'], 'issues_detected')
            refs = {item.evidence_id for item in result.evidence}
            for diagnosis in result.diagnostics:
                for corr in diagnosis.correlations:
                    self.assertTrue(set(corr.evidence_refs) <= refs)
                    self.assertNotEqual(corr.status, 'correlated')

    def test_actual_install_cli_exit_zero_for_historical_log_context(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'main.py').write_text('raise AssertionError("must not execute")')
            log = root / 'install.txt'
            log.write_text('Processing demo-1.0-py3-none-any.whl\nERROR: No matching distribution found for demo')
            output = root / 'report.json'
            with redirect_stdout(io.StringIO()):
                code = main([str(root), '--install-log', str(log), '--output', str(output)])
            report = json.loads(output.read_text())
            self.assertEqual(code, 0)
            self.assertEqual(report['schema_version'], '0.2')
            self.assertEqual(report['assessment']['outcome'], 'inconclusive')
            self.assertEqual(report['diagnostics'][0]['severity'], 'INFO')
            self.assertEqual(report['diagnostics'][0]['correlations'][0]['status'], 'partial')

    def test_actual_install_log_overbudget_conflict_order_is_stable(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'main.py').write_text('pass')
            log = root / 'install.txt'
            messages = ['ERROR: No matching distribution found for demo'] * 128
            conflict = 'ERROR: No matching distribution found for other'
            for rows in ((*messages, conflict), (conflict, *messages)):
                log.write_text('Processing demo-1.0-py3-none-any.whl\n' + '\n'.join(rows))
                result = run_workflow(root, install_log=log)
                self.assertEqual(result.diagnostics, ())
                item = next(e for e in result.evidence if e.kind == 'provided_log')
                self.assertEqual(len(item.metadata['messages']), 129)
                self.assertEqual(result.report['assessment']['outcome'], 'inconclusive')

    def test_actual_install_log_oversized_fact_preserves_all_original_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'main.py').write_text('pass')
            log = root / 'install.txt'
            normal = 'ERROR: No matching distribution found for demo'
            oversized = 'ERROR: No matching distribution found for other==' + '1' * 4096
            for rows in ((normal, oversized), (oversized, normal)):
                log.write_text('Processing demo-1.0-py3-none-any.whl\n' + '\n'.join(rows))
                result = run_workflow(root, install_log=log)
                self.assertEqual(result.diagnostics, ())
                item = next(e for e in result.evidence if e.kind == 'provided_log')
                self.assertEqual(item.metadata['messages'], rows)
                self.assertEqual(result.report['assessment']['outcome'], 'inconclusive')

    def test_declared_correlation_failures_preserve_completed_diagnosis(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'main.py').write_text('pass')
            log = root / 'trace.txt'
            log.write_text("ImportError: cannot import name 'wanted' from 'missing_step6_module'")
            with patch.object(PythonCoreExtension, 'correlate', side_effect=lambda diagnoses, evidence: diagnoses):
                baseline = run_workflow(root, traceback_file=log)
            for error in (ExtensionFailure, ExtensionUnavailable, ExtensionIncompatible):
                with patch('agent_doctor.workflow.execute_command', return_value=baseline.execution_results[0]), patch('agent_doctor.workflow.execute_startup_probe', return_value=baseline.execution_results[-1]), patch.object(PythonCoreExtension, 'correlate', side_effect=error('python.core', 'secret error text')):
                    result = run_workflow(root, traceback_file=log)
                self.assertEqual(result.evidence, baseline.evidence)
                self.assertEqual(result.report['status'], 'issues_detected')
                self.assertEqual(len(result.diagnostics), len(baseline.diagnostics))
                for old, new in zip(baseline.diagnostics, result.diagnostics):
                    self.assertEqual(replace(new, correlations=()), old)
                    self.assertEqual(new.correlations[0].status, 'inconclusive')
                self.assertNotIn('secret error text', result.terminal_report)
                self.assertTrue(any(item['check'] == 'correlation' for item in result.report['assessment']['limitations']))

    def test_unexpected_correlation_exception_retains_existing_exception_policy(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'main.py').write_text('pass')
            with patch.object(PythonCoreExtension, 'correlate', side_effect=RuntimeError('programmer bug')):
                with self.assertRaises(RuntimeError):
                    run_workflow(root)
