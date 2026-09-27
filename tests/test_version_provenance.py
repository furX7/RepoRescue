"""Version sources stay literal, bounded, distinct and linked to their origins."""

from contextlib import redirect_stdout
from dataclasses import replace
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from agent_doctor.cli import main
from agent_doctor.models import Evidence
from agent_doctor.project import scan_project
from agent_doctor.python_evidence import collect_import_evidence
from agent_doctor.version_provenance import (
    collect_project_provenance, extract_resolved_versions, finalize,
    installed_provenance, MAX_SOURCE_BYTES, MAX_SOURCES, MAX_RECORDS,
)
from agent_doctor.workflow import run_workflow


class VersionProvenanceTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name).resolve()
        self.root = self.base / 'project'
        self.root.mkdir()
        (self.root / 'main.py').write_text('raise RuntimeError("never execute")')
        self.discovery = patch('agent_doctor.python_evidence.discover_metadata', return_value=([], ()))
        self.mock_discovery = self.discovery.start()
        self.addCleanup(self.discovery.stop)

    def write(self, name, text):
        file = self.root / name
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(text, encoding='utf-8')
        return file

    def collect(self):
        return collect_project_provenance(scan_project(self.root))

    def provenance(self, result):
        return [e for e in result.evidence if e.kind == 'python_version_provenance']

    def metadata(self, name='fixture-dist', version='3.0', status='available', path=None):
        return {'facts': {'name': name, 'installed_version': version, 'status': status,
                          'metadata_location': path or str(self.base / 'site/fixture/METADATA'),
                          'dist_info_location': str(self.base / 'site/fixture.dist-info')}, 'modules': ('fixture',)}

    def test_three_versions_distinct_without_diagnosis(self):
        self.write('requirements.txt', 'fixture-dist>=1.0\n')
        self.write('uv.lock', 'version=1\n[[package]]\nname="fixture-dist"\nversion="2.0"\nsource={registry="https://example.invalid"}\n')
        self.mock_discovery.return_value = ([self.metadata()], ())
        result = run_workflow(self.root)
        facts = {e.metadata['provenance']: e for e in self.provenance(result)}
        self.assertEqual({k: v.metadata['value'] for k, v in facts.items()},
                         {'declared': '>=1.0', 'locked': '2.0', 'installed': '3.0'})
        self.assertEqual(result.diagnostics, ())
        self.assertEqual(facts['installed'].metadata['runtime_scope'], 'current_interpreter_only')
        refs = {e.evidence_id for e in result.evidence}
        self.assertIn(facts['installed'].metadata['source_evidence_ref'], refs)

    def test_declared_only(self):
        self.write('requirements.txt', 'example>=1')
        facts = self.collect()
        self.assertEqual([(e.metadata['provenance'], e.metadata['value']) for e in facts], [('declared', '>=1')])

    def test_locked_only(self):
        self.write('poetry.lock', '[[package]]\nname="example"\nversion="2.0"')
        self.assertEqual(self.collect()[0].metadata['provenance'], 'locked')

    def test_installed_only_via_import_target(self):
        self.mock_discovery.return_value = ([self.metadata()], ())
        log = self.base / 'traceback.txt'
        log.write_text("ModuleNotFoundError: No module named 'fixture'")
        result = run_workflow(self.root, traceback_file=log)
        facts = self.provenance(result)
        self.assertEqual([e.metadata['provenance'] for e in facts], ['installed'])
        self.assertEqual(facts[0].metadata['value'], '3.0')

    def test_resolved_dry_run_log_keeps_reference_and_scope(self):
        log = self.base / 'install.txt'
        log.write_text('Would install fixture-dist-1.2.3\n')
        result = run_workflow(self.root, install_log=log)
        item = next(e for e in self.provenance(result) if e.metadata['provenance'] == 'resolved')
        self.assertEqual(item.metadata['value'], '1.2.3')
        self.assertEqual(item.metadata['source_evidence_ref'], 'input:log')
        self.assertEqual(item.metadata['runtime_scope'], 'supplied_log_only')
        self.assertEqual(item.location, str(log))
        self.assertEqual(result.diagnostics, ())
        self.assertEqual(result.report['schema_version'], '0.2')

    def test_download_collecting_successful_install_not_resolved(self):
        for text in ('Collecting example==1.2', 'Downloading example-1.2.whl',
                     'Successfully installed example-1.2', 'ERROR: Would install example-1.2',
                     '$ echo Would install example-1.2', 'Would install example-latest'):
            with self.subTest(text=text):
                self.assertEqual(extract_resolved_versions(text), ())

    def test_requirements_conflicts_preserve_each_path(self):
        paths = [self.write('requirements.txt', 'example==1'), self.write('requirements-dev.txt', 'example==2')]
        facts = self.collect()
        self.assertEqual({e.metadata['status'] for e in facts}, {'ambiguous'})
        self.assertEqual({e.location for e in facts}, {str(p) for p in paths})
        self.assertEqual({e.metadata['value'] for e in facts}, {'==1', '==2'})

    def test_lock_conflicts_keep_origin(self):
        self.write('uv.lock', '[[package]]\nname="example"\nversion="1"')
        self.write('poetry.lock', '[[package]]\nname="example"\nversion="2"')
        facts = self.collect()
        self.assertEqual({e.source for e in facts}, {'uv.lock', 'poetry.lock'})
        self.assertEqual({e.metadata['status'] for e in facts}, {'ambiguous'})

    def test_identical_duplicates_keep_rows(self):
        self.write('requirements.txt', 'example==1\nexample==1')
        facts = self.collect()
        self.assertEqual(len(facts), 2)
        self.assertEqual({e.metadata['status'] for e in facts}, {'available'})
        self.assertEqual(len({e.evidence_id for e in facts}), 2)
        self.assertEqual({e.metadata['evidence_origin'] for e in facts}, {'line:1', 'line:2'})

    def test_pep503_names_do_not_strip_separators(self):
        self.write('requirements.txt', 'Foo_Bar==1\nfoo.bar==2\nfoobar==3\nfoo-bar-extra==4')
        facts = self.collect()
        self.assertEqual([e.metadata['normalized_name'] for e in facts], ['foo-bar', 'foo-bar', 'foobar', 'foo-bar-extra'])
        self.assertEqual([e.metadata['status'] for e in facts], ['ambiguous', 'ambiguous', 'available', 'available'])

    def test_pyproject_pep621_and_optional(self):
        self.write('pyproject.toml', '[project]\ndependencies=["example>=1", "demo[extra]==2"]\n[project.optional-dependencies]\ntest=["pytest>=8"]')
        facts = self.collect()
        self.assertEqual([e.metadata['name'] for e in facts], ['example', 'demo', 'pytest'])
        self.assertEqual(facts[1].metadata['extras'], 'extra')
        self.assertIn('optional-dependencies.test', facts[2].metadata['evidence_origin'])

    def test_setup_cfg(self):
        self.write('setup.cfg', '[options]\ninstall_requires=\n example>=1\n[options.extras_require]\ntest=\n demo==2\n')
        self.assertEqual([e.metadata['value'] for e in self.collect()], ['>=1', '==2'])

    def test_pipfile_lock(self):
        self.write('Pipfile.lock', '{"default":{"example":{"version":"==1.2"}},"develop":{"demo":{"version":"==2.0"}}}')
        self.assertEqual([e.metadata['value'] for e in self.collect()], ['1.2', '2.0'])

    def test_malformed_sources_degrade(self):
        for name, text in [('pyproject.toml', '[broken'), ('setup.cfg', 'no section'),
                           ('uv.lock', '[[package]]\nname=5'), ('poetry.lock', 'package="wrong"'),
                           ('Pipfile.lock', '{invalid')]:
            with self.subTest(name=name):
                file = self.write(name, text)
                facts = self.collect()
                self.assertTrue(facts)
                self.assertNotIn('available', {e.metadata['status'] for e in facts})
                file.unlink()

    def test_oversized_source(self):
        self.write('requirements.txt', 'x' * (MAX_SOURCE_BYTES + 1))
        self.assertEqual(self.collect()[0].metadata['status'], 'unavailable')

    def test_invalid_utf8(self):
        self.write('requirements.txt', '').write_bytes(b'\xff')
        self.assertEqual(self.collect()[0].metadata['error_type'], 'UnicodeDecodeError')

    def test_remote_requirements_not_fetched(self):
        self.write('requirements.txt', '-r https://example.invalid/deps.txt\ndemo @ https://example.invalid/demo.whl')
        facts = self.collect()
        self.assertEqual({e.metadata['status'] for e in facts}, {'unknown'})
        self.assertTrue(all(e.metadata['value'] is None for e in facts))

    def test_includes_not_followed(self):
        self.write('requirements.txt', '-r ../../private.txt')
        self.assertEqual(self.collect()[0].metadata['status'], 'unknown')

    def test_setup_py_never_executed(self):
        marker = self.base / 'marker'
        self.write('setup.py', f'from pathlib import Path\nPath({str(marker)!r}).touch()')
        self.assertEqual(self.collect()[0].metadata['status'], 'unavailable')
        self.assertFalse(marker.exists())

    def test_dynamic_dependencies_unknown(self):
        self.write('pyproject.toml', '[project]\ndynamic=["dependencies"]')
        self.assertEqual(self.collect()[0].metadata['status'], 'unknown')

    def test_unsupported_poetry_declaration(self):
        self.write('pyproject.toml', '[tool.poetry.dependencies]\nexample="^1"')
        self.assertEqual(self.collect()[0].metadata['status'], 'unavailable')

    def test_marker_remains_unevaluated(self):
        self.write('requirements.txt', 'example==1; python_version < "3.8"')
        item = self.collect()[0]
        self.assertEqual(item.metadata['status'], 'unknown')
        self.assertEqual(item.metadata['marker'], 'python_version < "3.8"')

    def test_unpinned_requirement_is_declaration_only(self):
        self.write('requirements.txt', 'example')
        self.assertEqual(self.collect()[0].metadata['value'], '')

    def test_local_uv_package_not_registry_pin(self):
        self.write('uv.lock', '[[package]]\nname="example"\nversion="1"\nsource={editable="."}')
        item = self.collect()[0]
        self.assertIsNone(item.metadata['value'])
        self.assertEqual(item.metadata['status'], 'unknown')

    def test_no_target_no_distribution_scan(self):
        self.write('unrelated.txt', 'example==1')
        run_workflow(self.root)
        self.mock_discovery.assert_not_called()

    def test_empty_manifest_no_distribution_scan(self):
        self.write('pyproject.toml', '[project]\ndependencies=[]')
        run_workflow(self.root)
        self.mock_discovery.assert_not_called()

    def test_scan_depth_and_exclusions(self):
        self.write('child/requirements-dev.txt', 'example==1')
        self.write('child/deep/requirements.txt', 'secret==2')
        self.write('.venv/requirements.txt', 'secret==3')
        self.assertEqual([e.metadata['name'] for e in self.collect()], ['example'])

    def test_forged_snapshot_path_escape_rejected(self):
        outside = self.base / 'requirements.txt'
        outside.write_text('private==1')
        project = replace(scan_project(self.root), files=('../requirements.txt',))
        with patch.object(Path, 'open', side_effect=AssertionError('must not read')):
            facts = collect_project_provenance(project)
        self.assertEqual(facts[0].metadata['status'], 'unavailable')

    def test_replaced_source_link_rejected(self):
        file = self.write('requirements.txt', 'example==1')
        project = scan_project(self.root)
        original = Path.is_symlink
        with patch.object(Path, 'is_symlink', lambda p: p == file or original(p)):
            self.assertEqual(collect_project_provenance(project)[0].metadata['status'], 'unavailable')

    def test_source_count_limit(self):
        project = replace(scan_project(self.root), files=tuple(f'child{i}/requirements.txt' for i in range(MAX_SOURCES + 1)))
        self.assertEqual(collect_project_provenance(project)[0].metadata['limitation'], 'source_limit')

    def test_record_limit_degrades(self):
        self.write('requirements.txt', 'example==1\n' * (MAX_RECORDS + 1))
        self.assertEqual(self.collect()[0].metadata['status'], 'unavailable')

    def test_source_limit_does_not_drop_log_or_installed_target(self):
        for index in range(MAX_SOURCES + 1):
            self.write(f'child{index}/requirements.txt', 'unread==1')
        log = self.base / 'install.txt'
        log.write_text('Would install example-1.2')
        self.mock_discovery.return_value = ([self.metadata(name='example', version='3')], ())
        facts = self.provenance(run_workflow(self.root, install_log=log))
        self.assertEqual([(e.metadata['provenance'], e.metadata['value']) for e in facts],
                         [('declared', None), ('resolved', '1.2'), ('installed', '3')])
        self.assertEqual(facts[0].metadata['limitation'], 'source_limit')
        self.mock_discovery.assert_called_once()

    def test_lock_only_source_limit_keeps_correct_category(self):
        project = replace(scan_project(self.root), files=tuple(f'child{i}/uv.lock' for i in range(MAX_SOURCES + 1)))
        facts = collect_project_provenance(project)
        self.assertEqual([e.metadata['provenance'] for e in facts], ['locked'])

    def test_resolved_line_limit(self):
        self.assertEqual(extract_resolved_versions('Would install ' + 'example-1 ' * 1000), ())

    def test_resolved_record_limit(self):
        self.assertEqual(extract_resolved_versions('Would install example-1\n' * (MAX_RECORDS + 1)), ())

    def test_duplicate_json_keys_rejected(self):
        self.write('Pipfile.lock', '{"default":{"example":{"version":"==1","version":"==2"}}}')
        self.assertEqual(self.collect()[0].metadata['status'], 'unavailable')

    def test_metadata_failure_not_available(self):
        self.write('requirements.txt', 'fixture-dist==1')
        self.mock_discovery.return_value = ([self.metadata(status='unavailable')], ('ValueError',))
        result = run_workflow(self.root)
        installed = next(e for e in self.provenance(result) if e.metadata['provenance'] == 'installed')
        self.assertEqual(installed.metadata['status'], 'unavailable')
        self.assertEqual(result.report['assessment']['coverage'], 'incomplete')

    def test_multiple_installed_sources_not_merged(self):
        self.mock_discovery.return_value = ([self.metadata(), self.metadata(version='4.0', path='other/METADATA')], ())
        self.write('requirements.txt', 'fixture-dist==1')
        facts = [e for e in self.provenance(run_workflow(self.root)) if e.metadata['provenance'] == 'installed']
        self.assertEqual(len(facts), 2)
        self.assertEqual({e.metadata['status'] for e in facts}, {'ambiguous'})

    def test_missing_installed_not_absence_claim(self):
        self.write('requirements.txt', 'example==1')
        facts = self.provenance(run_workflow(self.root))
        self.assertEqual(facts[-1].metadata['status'], 'unknown')
        self.assertIsNone(facts[-1].metadata['value'])

    def test_existing_error_wins_over_provenance_limitation(self):
        self.write('requirements.txt', '-r unknown.txt')
        log = self.base / 'traceback.txt'
        log.write_text("ModuleNotFoundError: No module named 'missing'")
        result = run_workflow(self.root, traceback_file=log)
        self.assertEqual(result.report['assessment']['outcome'], 'issues_detected')
        self.assertIn('python_version_provenance', {e['check'] for e in result.report['assessment']['limitations']})

    def test_cli_json_and_terminal_escape(self):
        self.write('requirements.txt', 'example==1\x1b[31m')
        output = io.StringIO()
        destination = self.base / 'report.json'
        with redirect_stdout(output):
            code = main([str(self.root), '--output', str(destination)])
        self.assertEqual(code, 0)
        report = json.loads(destination.read_text())
        self.assertEqual(report['schema_version'], '0.2')
        result = run_workflow(self.root)
        self.assertNotIn('\x1b', result.terminal_report)
        self.assertEqual(result.report['status'], 'healthy')

    def test_cli_existing_error_exit_code(self):
        log = self.base / 'traceback.txt'
        log.write_text("ModuleNotFoundError: No module named 'missing'")
        self.write('requirements.txt', '-r unknown')
        with redirect_stdout(io.StringIO()):
            self.assertEqual(main([str(self.root), '--traceback-file', str(log), '--output', str(self.base / 'report.json')]), 1)

    def test_invalid_wildcard_not_available(self):
        self.write('requirements.txt', 'example>=1.*\nother~=2.*')
        self.assertEqual({e.metadata['status'] for e in self.collect()}, {'unknown'})

    def test_requirement_continuation_not_separate_dependency(self):
        self.write('requirements.txt', 'example==1 \\\n --hash=sha256:123\nother==2')
        facts = self.collect()
        self.assertEqual(len(facts), 2)
        self.assertEqual(facts[0].metadata['status'], 'unknown')
        self.assertEqual(facts[1].metadata['name'], 'other')

    def test_raw_url_not_named_dependency(self):
        self.write('requirements.txt', 'https://example.invalid/pkg.whl')
        self.assertIsNone(self.collect()[0].metadata['name'])
        run_workflow(self.root)
        self.mock_discovery.assert_not_called()

    def test_unknown_lock_structure_and_versions(self):
        for name, text in [('uv.lock', 'version=999\npackage=[]'),
                           ('poetry.lock', 'package=[]\n[metadata]\nlock-version="999"'),
                           ('Pipfile.lock', '{}')]:
            with self.subTest(name=name):
                file = self.write(name, text)
                self.assertEqual(self.collect()[0].metadata['status'], 'unavailable')
                file.unlink()

    def test_deterministic_provenance(self):
        self.write('requirements.txt', 'demo==2\nexample==1')
        self.assertEqual(self.collect(), self.collect())

    def test_distribution_name_target_not_module_name_guess(self):
        self.mock_discovery.return_value = ([self.metadata(name='fixture', version='1'), self.metadata()], ())
        facts = collect_import_evidence((), ('fixture-dist',))
        self.assertEqual([e.metadata['name'] for e in facts if e.kind == 'python_distribution'], ['fixture-dist'])
        self.assertFalse(any(e.kind == 'python_module_origin' for e in facts))


if __name__ == '__main__':
    unittest.main()
