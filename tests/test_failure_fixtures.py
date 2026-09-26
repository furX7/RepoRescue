"""Run the repository's audited tiny broken projects, without golden snapshots."""

import ast
import json
import unittest
from pathlib import Path

from agent_doctor.workflow import run_workflow


FIXTURES = Path(__file__).resolve().parents[1] / 'examples' / 'fixtures'
NAMES = ('python-version-mismatch', 'missing-module', 'symbol-import-failure',
         'startup-nonzero', 'startup-timeout', 'combined-failures')
MISSING_MODULE = 'reporescue_fixture_missing_dependency_xyz'


def snapshot():
    return {path.relative_to(FIXTURES).as_posix():
            (path.read_bytes(), path.stat().st_mtime_ns) if path.is_file() else None
            for path in FIXTURES.rglob('*')}


def audit_sources():
    """Fail closed on imports/calls/assignments outside these reviewed fixtures."""
    for path in FIXTURES.rglob('*.py'):
        tree = ast.parse(path.read_text(encoding='utf-8'))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                assert all(alias.name in ('sys', 'time', MISSING_MODULE) and alias.asname is None
                           for alias in node.names), path
            if isinstance(node, ast.ImportFrom):
                assert node.module == 'helper' and node.level == 0, path
                assert [(alias.name, alias.asname) for alias in node.names] == [('missing_symbol', None)], path
            if isinstance(node, ast.Call):
                name = ast.unparse(node.func)
                assert name in ('print', 'RuntimeError', 'time.sleep'), path
                if name == 'time.sleep':
                    assert len(node.args) == 1 and isinstance(node.args[0], ast.Constant), path
                    assert node.args[0].value == 10 and not node.keywords, path
            if isinstance(node, ast.Assign):
                assert len(node.targets) == 1, path
                name = ast.unparse(node.targets[0])
                assert name in ('existing_symbol', 'sys.dont_write_bytecode'), path
                assert isinstance(node.value, ast.Constant), path
                assert node.value.value == (True if name == 'sys.dont_write_bytecode' else 1), path
            assert not isinstance(node, (ast.While, ast.For, ast.AsyncFor, ast.With, ast.AsyncWith,
                                         ast.FunctionDef, ast.ClassDef, ast.AugAssign, ast.Delete,
                                         ast.ListComp, ast.DictComp, ast.SetComp, ast.GeneratorExp)), path


class FailureFixtureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        audit_sources()  # Review before executing repository code.
        cls.before = snapshot()
        cls.results = {name: run_workflow(FIXTURES / name,
                                         confirm_startup=name != 'python-version-mismatch')
                       for name in NAMES}
        cls.after = snapshot()

    def categories(self, name):
        return {item.category for item in self.results[name].diagnostics}

    def startup_evidence(self, name):
        return next(item for item in self.results[name].evidence if item.kind == 'startup_probe')

    def test_directories_and_readmes_exist(self):
        self.assertTrue((FIXTURES / 'README.md').is_file())
        for name in NAMES:
            with self.subTest(name=name):
                self.assertTrue((FIXTURES / name / 'main.py').is_file())
                self.assertTrue((FIXTURES / name / 'README.md').is_file())

    def test_version_mismatch_without_startup(self):
        self.assertEqual(self.categories('python-version-mismatch'), {'python_version'})
        self.assertEqual(self.startup_evidence('python-version-mismatch').metadata['execution_status'],
                         'requires_confirmation')

    def test_missing_module_has_import_and_startup_diagnoses(self):
        self.assertEqual(self.categories('missing-module'), {'python_import', 'startup'})
        evidence = next(item for item in self.results['missing-module'].evidence
                        if item.kind == 'python_import_failure')
        self.assertEqual(evidence.metadata['missing_module'], MISSING_MODULE)
        self.assertEqual(evidence.metadata['status'], 'missing_module')

    def test_missing_module_real_nonzero_exit(self):
        evidence = self.startup_evidence('missing-module')
        self.assertEqual(evidence.metadata['execution_status'], 'failed')
        self.assertNotEqual(evidence.metadata['exit_code'], 0)
        self.assertIn('ModuleNotFoundError', evidence.metadata['stderr_excerpt'])

    def test_real_symbol_import_is_recognized(self):
        self.assertEqual(self.categories('symbol-import-failure'), {'python_import', 'startup'})
        evidence = next(item for item in self.results['symbol-import-failure'].evidence
                        if item.kind == 'python_import_failure')
        self.assertEqual(evidence.metadata['exception_type'], 'ImportError')
        self.assertEqual(evidence.metadata['imported_symbol'], 'missing_symbol')
        self.assertEqual(evidence.metadata['source_module'], 'helper')
        self.assertEqual(evidence.metadata['status'], 'symbol_import_failure')
        self.assertNotIn('missing_module', evidence.metadata)

    def test_symbol_import_does_not_claim_missing_package(self):
        for item in self.results['symbol-import-failure'].diagnostics:
            self.assertNotIn('missing dependency', repr(item).lower())
            self.assertNotIn('missing package', repr(item).lower())

    def test_runtime_error_has_only_startup_diagnosis(self):
        self.assertEqual(self.categories('startup-nonzero'), {'startup'})
        item = self.results['startup-nonzero'].diagnostics[0]
        self.assertEqual(item.severity, 'ERROR')
        self.assertIn('non-zero', item.problem)

    def test_timeout_records_termination_and_info(self):
        evidence = self.startup_evidence('startup-timeout')
        self.assertEqual(evidence.metadata['execution_status'], 'timeout')
        self.assertEqual(evidence.metadata['timeout_seconds'], 5.0)
        self.assertTrue(evidence.metadata['terminated'])
        self.assertEqual(self.categories('startup-timeout'), {'startup'})
        item = self.results['startup-timeout'].diagnostics[0]
        self.assertEqual(item.severity, 'INFO')
        self.assertIn('observation window', item.problem)
        self.assertIsNone(item.repair_plan)
        terminal = self.results['startup-timeout'].terminal_report
        self.assertNotIn('completed successfully', terminal)
        self.assertNotIn('Application failed to start.', terminal)
        self.assertNotIn('deadlock detected', terminal.lower())

    def test_combined_diagnoses_are_independent(self):
        result = self.results['combined-failures']
        self.assertEqual(self.categories('combined-failures'), {'python_version', 'python_import', 'startup'})
        refs = [set(item.evidence_refs) for item in result.diagnostics]
        for index, current in enumerate(refs):
            for other in refs[index + 1:]:
                self.assertTrue(current.isdisjoint(other))
        for item in result.diagnostics:
            self.assertTrue(all(set(step.evidence_refs) <= set(item.evidence_refs)
                                for step in item.root_cause_chain))

    def test_sources_have_only_audited_safe_operations(self):
        audit_sources()

    def test_fixtures_files_bytes_and_mtimes_unchanged(self):
        self.assertEqual(self.before, self.after)
        self.assertEqual(snapshot(), self.before)
        self.assertFalse(any(path.name == '__pycache__' for path in FIXTURES.rglob('*')))

    def test_json_relationships_and_plan_status(self):
        for name, result in self.results.items():
            with self.subTest(name=name):
                payload = json.loads(json.dumps(result.report))
                self.assertEqual(payload['schema_version'], '0.2')
                ids = {item['evidence_id'] for item in payload['evidence']}
                for item in payload['diagnostics']:
                    self.assertTrue(set(item['evidence_refs']) <= ids)
                    for step in item['root_cause_chain']:
                        self.assertTrue(set(step['evidence_refs']) <= ids)
                    if item['repair_plan']:
                        self.assertEqual(item['repair_plan']['execution_status'], 'not_executed')
                        self.assertTrue(item['repair_plan']['verification_steps'])
                        self.assertTrue(all(step['status'] == 'not_run'
                                            for step in item['repair_plan']['verification_steps']))

    def test_json_excerpts_are_small_and_untruncated(self):
        for name in NAMES:
            metadata = self.startup_evidence(name).metadata
            self.assertLessEqual(len(metadata['stdout_excerpt']), 4096)
            self.assertLessEqual(len(metadata['stderr_excerpt']), 4096)
            self.assertFalse(metadata.get('stdout_truncated', False))
            self.assertFalse(metadata.get('stderr_truncated', False))

    def test_terminal_preview_is_present_without_overclaims(self):
        terminal = self.results['missing-module'].terminal_report
        for text in ('python_import', 'Root cause:', 'Suggested repair', 'Verification', MISSING_MODULE):
            self.assertIn(text, terminal)
        self.assertNotIn('definitely missing', terminal)
        self.assertNotIn('project is healthy', terminal.lower())

    def test_terminal_import_error_is_not_expanded_twice(self):
        result = self.results['missing-module']
        evidence = next(item for item in result.evidence if item.kind == 'python_import_failure')
        self.assertEqual(result.terminal_report.count(evidence.metadata['raw_message']), 1)
        self.assertIn(evidence.metadata['raw_message'], self.startup_evidence('missing-module').metadata['stderr_excerpt'])
        self.assertNotIn('.; ', result.terminal_report)


if __name__ == '__main__':
    unittest.main()
