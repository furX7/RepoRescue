"""Fixture metadata/spec lookups prove facts without running package code."""

import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
import zipfile
from contextlib import redirect_stdout
from unittest.mock import patch

from agent_doctor.cli import main
from agent_doctor.models import Evidence
from agent_doctor.python_evidence import collect_import_evidence, interpreter_evidence, discover_metadata
from agent_doctor.workflow import run_workflow
from agent_doctor.project import scan_project, inspect_environment
from agent_doctor.models import DetectionResult
from agent_doctor.report import build_json_report
from dataclasses import replace


class PythonEvidenceTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.site = self.root / 'site-packages'
        self.site.mkdir()
        # Keep real stdlib paths, but isolate installed metadata to this fixture.
        for target, value in (
            ('sys.path', [path for path in sys.path if 'site-packages' not in path and 'dist-packages' not in path]),
            ('agent_doctor.python_evidence.sysconfig.get_path', str(self.site)),
            ('agent_doctor.python_evidence.site.getsitepackages', [str(self.site)]),
            ('agent_doctor.python_evidence.site.getusersitepackages', str(self.site)),
        ):
            mock = patch(target, value) if target == 'sys.path' else patch(target, return_value=value)
            mock.start()
            self.addCleanup(mock.stop)
        self.project = self.root / 'project'
        self.project.mkdir()
        (self.project / 'main.py').write_text('pass')
        self.marker = self.root / 'executed.txt'
        self.log = self.root / 'traceback.txt'

    def module(self, name, *, package=False, namespace=False):
        if package or namespace:
            folder = self.site / name
            folder.mkdir(parents=True, exist_ok=True)
            file = folder / '__init__.py'
            if namespace:
                return folder
        else:
            file = self.site / (name + '.py')
        file.write_text(f"from pathlib import Path\nPath({str(self.marker)!r}).write_text('executed')\nraise RuntimeError('must not run')")
        return file

    def distribution(self, name='fixture-dist', version='1.2.3', module='fixture_pkg'):
        folder = self.site / (name.replace('-', '_') + '-' + version + '.dist-info')
        folder.mkdir()
        (folder / 'METADATA').write_text(f'Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n')
        (folder / 'top_level.txt').write_text(module + '\n')
        (folder / 'RECORD').write_text(f'{folder.name}/METADATA,,\n')
        return folder

    def target(self, module='fixture_pkg', *, symbol=False, ref='input:log:python_import'):
        return Evidence(ref, 'python_import_failure', 'provided_traceback', 'explicit import exception',
                        metadata={'source_module' if symbol else 'missing_module': module})

    def collect(self, module='fixture_pkg', **kwargs):
        with patch('sys.path', [str(self.site), *sys.path]):
            return collect_import_evidence((self.target(module, **kwargs),))

    def item(self, items, kind):
        return next(item for item in items if item.kind == kind)

    def test_installed_distribution_name_version_and_metadata_location(self):
        folder = self.distribution()
        self.module('fixture_pkg', package=True)
        item = self.item(self.collect(), 'python_distribution')
        self.assertEqual(item.metadata['name'], 'fixture-dist')
        self.assertEqual(item.metadata['installed_version'], '1.2.3')
        self.assertEqual(item.metadata['dist_info_location'], str(folder))
        self.assertEqual(item.metadata['metadata_location'], str(folder / 'METADATA'))

    def test_package_origin_and_search_locations(self):
        file = self.module('fixture_pkg', package=True)
        item = self.item(self.collect(), 'python_module_origin')
        self.assertEqual(item.metadata['origin'], str(file))
        self.assertEqual(item.metadata['search_locations'], (str(file.parent),))
        self.assertEqual(item.metadata['module_type'], 'package')

    def test_single_file_origin(self):
        file = self.module('fixture_pkg')
        item = self.item(self.collect(), 'python_module_origin')
        self.assertEqual(item.metadata['origin'], str(file))
        self.assertEqual(item.metadata['module_type'], 'module')

    def test_namespace_package(self):
        folder = self.module('fixture_pkg', namespace=True)
        item = self.item(self.collect(), 'python_module_origin')
        self.assertEqual(item.metadata['module_type'], 'namespace')
        self.assertIsNone(item.metadata['origin'])
        self.assertEqual(item.metadata['search_locations'], (str(folder),))

    def test_nested_namespace_package(self):
        self.module('fixture_pkg', namespace=True)
        folder = self.module('fixture_pkg/child', namespace=True)
        item = self.item(self.collect('fixture_pkg.child'), 'python_module_origin')
        self.assertEqual(item.metadata['module_type'], 'namespace')
        self.assertEqual(item.metadata['search_locations'], (str(folder),))

    def test_builtin_module(self):
        item = self.item(self.collect('sys'), 'python_module_origin')
        self.assertEqual(item.metadata['module_type'], 'builtin')
        self.assertEqual(item.metadata['origin'], 'built-in')

    def test_frozen_module(self):
        item = self.item(self.collect('__hello__'), 'python_module_origin')
        self.assertEqual(item.metadata['module_type'], 'frozen')
        self.assertEqual(item.metadata['origin'], 'frozen')

    def test_dotted_frozen_module(self):
        item = self.item(self.collect('__phello__.spam'), 'python_module_origin')
        self.assertEqual(item.metadata['module_type'], 'frozen')
        self.assertEqual(item.metadata['origin'], 'frozen')

    def test_zip_package_parent_and_child_are_not_executed(self):
        archive = self.root / 'modules.zip'
        code = f"open({str(self.marker)!r}, 'w').write('executed')"
        with zipfile.ZipFile(archive, 'w') as output:
            output.writestr('fixture_pkg/__init__.py', code)
            output.writestr('fixture_pkg/child.py', code)
        # Isolate spec lookup from metadata's cached ZIP readers on Windows.
        with patch('sys.path', [str(archive), *sys.path]), \
                patch('agent_doctor.python_evidence.discover_metadata', return_value=([], ())):
            items = collect_import_evidence((self.target('fixture_pkg.child'),))
        item = self.item(items, 'python_module_origin')
        self.assertEqual(item.metadata['status'], 'available')
        self.assertTrue(item.metadata['origin'].replace('\\', '/').endswith('modules.zip/fixture_pkg/child.py'))
        self.assertFalse(self.marker.exists())

    def test_missing_module_target_is_not_installation_inference(self):
        item = self.item(self.collect('not_installed.child'), 'python_module_origin')
        self.assertEqual(item.metadata['module'], 'not_installed.child')
        self.assertEqual(item.metadata['status'], 'not_found')
        self.assertEqual(item.metadata['trigger_evidence_refs'], ('input:log:python_import',))

    def test_cannot_import_name_uses_source_package(self):
        self.module('fixture_pkg', package=True)
        item = self.item(self.collect(symbol=True), 'python_module_origin')
        self.assertEqual(item.metadata['module'], 'fixture_pkg')
        self.assertEqual(item.metadata['status'], 'available')

    def test_one_distribution_mapping(self):
        self.distribution()
        item = self.item(self.collect('fixture_pkg.child'), 'python_distribution_mapping')
        self.assertEqual(item.metadata['candidate_distributions'], ('fixture-dist',))
        self.assertEqual(item.metadata['status'], 'available')

    def test_multiple_distributions_keep_all_candidates(self):
        self.distribution()
        self.distribution('other-dist', '4.0')
        items = self.collect()
        item = self.item(items, 'python_distribution_mapping')
        self.assertEqual(item.metadata['status'], 'ambiguous')
        self.assertEqual(item.metadata['candidate_distributions'], ('fixture-dist', 'other-dist'))
        self.assertEqual(len([e for e in items if e.kind == 'python_distribution']), 2)

    def test_no_mapping_is_unknown_not_missing_distribution(self):
        self.module('fixture_pkg')
        items = self.collect()
        item = self.item(items, 'python_distribution_mapping')
        self.assertEqual(item.metadata['status'], 'unknown')
        self.assertEqual(item.metadata['candidate_distributions'], ())
        self.assertFalse(any(e.kind == 'python_distribution' for e in items))

    def test_metadata_exception_degrades_gracefully(self):
        with patch('agent_doctor.python_evidence.discover_metadata', side_effect=RuntimeError('secret text')):
            items = self.collect()
        item = self.item(items, 'python_distribution_mapping')
        self.assertEqual(item.metadata['status'], 'unavailable')
        self.assertEqual(item.metadata['discovery_errors'], ('RuntimeError',))
        self.assertNotIn('secret text', repr(items))

    def test_distribution_exception_preserves_candidate(self):
        folder = self.distribution()
        (folder / 'METADATA').write_text('Metadata-Version: 2.1\nName: fixture-dist\n')
        items = self.collect()
        self.assertEqual(self.item(items, 'python_distribution_mapping').metadata['candidate_distributions'], ('fixture-dist',))
        self.assertEqual(self.item(items, 'python_distribution').metadata['status'], 'unavailable')

    def test_spec_exception_is_unavailable(self):
        with patch('agent_doctor.python_evidence.find_module_spec', side_effect=OSError('lookup failed')):
            item = self.item(self.collect(), 'python_module_origin')
        self.assertEqual(item.metadata['status'], 'unavailable')

    def test_target_package_and_child_code_never_execute(self):
        self.module('fixture_pkg', package=True)
        file = self.module('fixture_pkg/child')
        item = self.item(self.collect('fixture_pkg.child'), 'python_module_origin')
        self.assertEqual(item.metadata['origin'], str(file))
        self.assertFalse(self.marker.exists())
        self.assertNotIn('fixture_pkg', sys.modules)
        self.assertNotIn('fixture_pkg.child', sys.modules)

    def test_custom_import_hooks_are_not_called(self):
        class DangerousFinder:
            def find_spec(self, *args):
                raise AssertionError('custom hook executed')
        self.module('fixture_pkg')
        with patch('sys.meta_path', [DangerousFinder()]), patch('sys.path_hooks', [DangerousFinder()]):
            item = self.item(self.collect(), 'python_module_origin')
        self.assertEqual(item.metadata['status'], 'available')

    def test_interpreter_facts(self):
        item = interpreter_evidence()
        self.assertEqual(item.metadata['executable'], sys.executable)
        self.assertEqual(item.metadata['version'], sys.version)
        self.assertEqual(item.metadata['prefix'], sys.prefix)
        self.assertEqual(item.metadata['base_prefix'], sys.base_prefix)
        self.assertEqual(item.metadata['is_venv'], sys.prefix != sys.base_prefix)
        self.assertIn(item.metadata['pointer_bits'], (32, 64))

    def test_venv_and_site_packages_path_facts(self):
        self.module('fixture_pkg')
        with patch('sys.prefix', str(self.root)), patch('sys.base_prefix', str(self.root / 'base')), \
                patch('agent_doctor.python_evidence.sysconfig.get_path', return_value=str(self.site)):
            item = self.item(self.collect(), 'python_module_origin')
            interpreter = interpreter_evidence()
        self.assertTrue(interpreter.metadata['is_venv'])
        self.assertEqual(item.metadata['venv_paths'], (str(self.site / 'fixture_pkg.py'),))
        self.assertEqual(item.metadata['site_packages_paths'], (str(self.site / 'fixture_pkg.py'),))

    def test_no_target_does_not_scan_metadata(self):
        with patch('agent_doctor.python_evidence.discover_metadata') as lookup:
            self.assertEqual(collect_import_evidence(()), ())
        lookup.assert_not_called()

    def test_duplicate_targets_are_collected_once(self):
        with patch('sys.path', [str(self.site), *sys.path]):
            items = collect_import_evidence((self.target(), self.target(ref='execution:1:python_import')))
        self.assertEqual(len([e for e in items if e.kind == 'python_module_origin']), 1)
        self.assertEqual(self.item(items, 'python_module_origin').metadata['trigger_evidence_refs'],
                         ('input:log:python_import', 'execution:1:python_import'))

    def test_workflow_traceback_adds_facts_without_new_diagnoses(self):
        self.distribution()
        self.module('fixture_pkg', package=True)
        self.log.write_text("ImportError: cannot import name 'missing' from 'fixture_pkg'")
        with patch('sys.path', [str(self.site), *sys.path]):
            result = run_workflow(self.project, traceback_file=self.log)
        self.assertEqual([d.category for d in result.diagnostics], ['python_import'])
        self.assertEqual(result.diagnostics[0].probable_causes, ())
        self.assertTrue(any(e.kind == 'python_distribution' for e in result.evidence))
        self.assertFalse(self.marker.exists())

    def test_successful_startup_without_import_target_skips_metadata(self):
        with patch('agent_doctor.python_evidence.discover_metadata') as lookup:
            result = run_workflow(self.project, confirm_startup=True)
        lookup.assert_not_called()
        self.assertEqual(result.report['assessment']['outcome'], 'no_issues_detected')

    def test_cli_json_compatible_and_origin_path_is_redacted(self):
        self.module('fixture_pkg')
        self.distribution()
        self.log.write_text("ImportError: cannot import name 'missing' from 'fixture_pkg'")
        output = self.root / 'report.json'
        stdout = io.StringIO()
        with patch('sys.path', [str(self.site), *sys.path]), redirect_stdout(stdout):
            code = main([str(self.project), '--traceback-file', str(self.log), '--output', str(output)])
        report = json.loads(output.read_text(encoding='utf-8'))
        self.assertEqual(code, 1)
        self.assertEqual(report['schema_version'], '0.2')
        self.assertEqual(report['assessment']['outcome'], 'issues_detected')
        self.assertIn('external path: fixture_pkg.py', stdout.getvalue())
        self.assertNotIn(str(self.site), stdout.getvalue())
        self.assertEqual(next(e for e in report['evidence'] if e['kind'] == 'python_distribution')['metadata']['installed_version'], '1.2.3')

    def test_startup_import_failure_triggers_current_runtime_evidence(self):
        (self.project / 'main.py').write_text('import missing_startup_package')
        with patch('sys.path', [str(self.site), *sys.path]):
            result = run_workflow(self.project, confirm_startup=True)
        item = self.item(result.evidence, 'python_module_origin')
        self.assertEqual(item.metadata['module'], 'missing_startup_package')
        self.assertEqual(result.report['assessment']['outcome'], 'issues_detected')

    def assessment(self, observations):
        project = scan_project(self.project)
        environment = replace(inspect_environment(project), python_callable=True)
        startup = Evidence('project:startup_probe', 'startup_probe', 'python_startup_probe', 'success',
                           metadata={'execution_status': 'success', 'executed': True, 'exit_code': 0})
        return build_json_report(project, DetectionResult('likely', ('main.py',)), environment,
                                 (), (startup, interpreter_evidence(), *observations))['assessment']

    def test_ambiguous_mapping_causes_incomplete_assessment_without_diagnosis(self):
        self.module('fixture_pkg')
        self.distribution()
        self.distribution('other-dist', '4.0')
        assessment = self.assessment(self.collect())
        self.assertEqual(assessment['outcome'], 'inconclusive')
        self.assertEqual(assessment['coverage'], 'incomplete')
        self.assertTrue(any(l['check'] == 'python_distribution_mapping' and l['reason'] == 'ambiguous'
                            for l in assessment['limitations']))

    def test_unavailable_metadata_is_limitation_not_project_failure(self):
        self.module('fixture_pkg')
        with patch('agent_doctor.python_evidence.discover_metadata', side_effect=RuntimeError):
            assessment = self.assessment(self.collect())
        self.assertEqual(assessment['outcome'], 'inconclusive')
        self.assertTrue(any(l['reason'] == 'unavailable' for l in assessment['limitations']))

    def test_unknown_mapping_keeps_inconclusive_even_when_module_exists(self):
        self.module('fixture_pkg')
        assessment = self.assessment(self.collect())
        self.assertEqual(assessment['outcome'], 'inconclusive')
        self.assertTrue(any(l['reason'] == 'unknown' for l in assessment['limitations']))

    def test_invalid_mapping_is_unavailable(self):
        folder = self.distribution()
        (folder / 'top_level.txt').write_text('not-valid/module')
        item = self.item(self.collect(), 'python_distribution_mapping')
        self.assertEqual(item.metadata['status'], 'unavailable')
        self.assertEqual(item.metadata['candidate_distributions'], ())

    def test_namespace_locations_from_multiple_roots(self):
        folder = self.module('fixture_pkg', namespace=True)
        other_root = self.root / 'other-site'
        other_namespace = other_root / 'fixture_pkg'
        other_namespace.mkdir(parents=True)
        with patch('sys.path', [str(self.site), str(other_root), *sys.path]):
            items = collect_import_evidence((self.target(),))
        self.assertEqual(self.item(items, 'python_module_origin').metadata['search_locations'],
                         (str(folder), str(other_namespace)))

    def test_distribution_location_unknown_does_not_invent_a_path(self):
        folder = self.distribution()
        (folder / 'METADATA').unlink()
        with patch('sys.path', [str(self.site), *sys.path]):
            records, errors = discover_metadata()
        self.assertTrue(errors)
        facts = next(record['facts'] for record in records if record['facts']['name'] is None)
        self.assertEqual(facts['status'], 'unavailable')
        self.assertIsNone(facts['metadata_location'])
        self.assertIsNone(facts['dist_info_location'])

    def test_malicious_metadata_finder_is_never_called(self):
        calls = []
        class MaliciousFinder:
            def find_distributions(self, *args, **kwargs):
                calls.append('metadata')
                raise AssertionError('third-party code executed')
            def find_spec(self, *args, **kwargs):
                calls.append('import')
                raise AssertionError('third-party import hook executed')
        self.module('fixture_pkg', package=True)
        self.distribution()
        with patch('importlib.metadata.packages_distributions', side_effect=AssertionError('unsafe API')), \
                patch('importlib.metadata.distribution', side_effect=AssertionError('unsafe API')), \
                patch('sys.meta_path', [MaliciousFinder(), *sys.meta_path]), \
                patch('sys.path_hooks', [MaliciousFinder()]):
            items = self.collect()
        self.assertEqual(calls, [])
        self.assertEqual(self.item(items, 'python_distribution').metadata['installed_version'], '1.2.3')
        self.assertFalse(self.marker.exists())

    def test_record_cannot_borrow_another_distribution_metadata_path(self):
        folder_a = self.distribution()
        folder_b = self.distribution('other-dist', '9.0', module='other_pkg')
        (folder_a / 'RECORD').write_text(f'{folder_b.name}/METADATA,,\n')
        items = self.collect()
        item = self.item(items, 'python_distribution')
        self.assertEqual(item.metadata['name'], 'fixture-dist')
        self.assertEqual(item.metadata['installed_version'], '1.2.3')
        self.assertEqual(item.metadata['metadata_location'], str(folder_a / 'METADATA'))
        self.assertEqual(item.metadata['dist_info_location'], str(folder_a))
        self.assertNotEqual(item.metadata['metadata_location'], str(folder_b / 'METADATA'))
        self.assertEqual(item.metadata['status'], 'available')

    def test_record_fallback_maps_package_and_single_file(self):
        folder = self.distribution()
        (folder / 'top_level.txt').unlink()
        (folder / 'RECORD').write_text(f'fixture_pkg/__init__.py,,\nstandalone.py,,\n{folder.name}/METADATA,,\n')
        for module in ('fixture_pkg', 'standalone'):
            with self.subTest(module=module):
                mapping = self.item(self.collect(module), 'python_distribution_mapping')
                self.assertEqual(mapping.metadata['candidate_distributions'], ('fixture-dist',))
                self.assertEqual(mapping.metadata['status'], 'available')

    def test_top_level_metadata_takes_precedence_over_record(self):
        folder = self.distribution(module='fixture_pkg')
        (folder / 'RECORD').write_text('other_pkg/__init__.py,,\n')
        mapping = self.item(self.collect('other_pkg'), 'python_distribution_mapping')
        self.assertEqual(mapping.metadata['status'], 'unknown')
        self.assertEqual(mapping.metadata['candidate_distributions'], ())

    def test_record_data_directory_is_not_a_module_mapping(self):
        folder = self.distribution()
        (folder / 'top_level.txt').unlink()
        (folder / 'RECORD').write_text('resource_data/settings.txt,,\n')
        mapping = self.item(self.collect('resource_data'), 'python_distribution_mapping')
        self.assertEqual(mapping.metadata['status'], 'unknown')
        self.assertEqual(mapping.metadata['candidate_distributions'], ())

    def test_same_name_in_distinct_directories_remains_ambiguous(self):
        self.distribution(version='1.0')
        self.distribution(version='2.0')
        items = self.collect()
        mapping = self.item(items, 'python_distribution_mapping')
        self.assertEqual(mapping.metadata['status'], 'ambiguous')
        self.assertEqual(len(mapping.metadata['candidate_distribution_refs']), 2)
        distributions = [item for item in items if item.kind == 'python_distribution']
        self.assertEqual({item.metadata['installed_version'] for item in distributions}, {'1.0', '2.0'})
        self.assertEqual(len({item.metadata['metadata_location'] for item in distributions}), 2)

    def test_egg_info_directory_owns_pkg_info(self):
        folder = self.site / 'fixture_dist.egg-info'
        folder.mkdir()
        (folder / 'PKG-INFO').write_text('Metadata-Version: 2.1\nName: fixture-dist\nVersion: 3.0\n')
        (folder / 'top_level.txt').write_text('fixture_pkg\n')
        item = self.item(self.collect(), 'python_distribution')
        self.assertEqual(item.metadata['installed_version'], '3.0')
        self.assertEqual(item.metadata['metadata_location'], str(folder / 'PKG-INFO'))
        self.assertIsNone(item.metadata['dist_info_location'])

    def test_error_with_ambiguous_metadata_keeps_cli_exit_one(self):
        self.module('fixture_pkg')
        self.distribution()
        self.distribution('other-dist', '4.0')
        self.log.write_text("ImportError: cannot import name 'missing' from 'fixture_pkg'")
        output = self.root / 'ambiguous.json'
        with patch('sys.path', [str(self.site), *sys.path]), redirect_stdout(io.StringIO()):
            code = main([str(self.project), '--traceback-file', str(self.log), '--output', str(output)])
        report = json.loads(output.read_text(encoding='utf-8'))
        self.assertEqual(code, 1)
        self.assertEqual(report['assessment']['outcome'], 'issues_detected')
        self.assertEqual(report['assessment']['coverage'], 'incomplete')
        self.assertTrue(any(l['reason'] == 'ambiguous' for l in report['assessment']['limitations']))

    def test_metadata_read_error_only_reduces_coverage(self):
        self.module('fixture_pkg')
        self.distribution()
        with patch('agent_doctor.python_evidence._metadata_text', side_effect=PermissionError):
            items = self.collect()
        assessment = self.assessment(items)
        self.assertEqual(assessment['outcome'], 'inconclusive')
        self.assertEqual(assessment['coverage'], 'incomplete')
        self.assertFalse(any(e.kind == 'python_distribution' for e in items))

    def test_record_unsafe_paths_never_produce_available_mapping(self):
        folder = self.distribution()
        for entry in ('victim/../../outside.py', '../../pkg.py', '/absolute/pkg.py',
                      'C:/outside/pkg.py', r'\\server\share\pkg.py', 'victim//child.py',
                      'victim/./child.py', 'victim/child.py ', 'victim/child.py:stream'):
            with self.subTest(entry=entry):
                (folder / 'RECORD').write_text(entry + ',,\n')
                items = self.collect('victim')
                self.assertEqual(self.item(items, 'python_distribution_mapping').metadata['status'], 'unavailable')
                self.assertFalse(any(e.kind == 'python_distribution' for e in items))

    def test_record_malformed_csv_is_unavailable_even_with_top_level(self):
        folder = self.distribution()
        for text in ('"fixture_pkg/__init__.py', '"fixture_pkg/__init__.py"garbage,,\n',
                     'fixture_pkg/__init__.py,\n', 'fixture_pkg/__init__.py,,invalid\n', 'fixture_pkg/__init__.py,bad"hash,1\n',
                     'fixture_pkg/__init__.py,,1\nfixture_pkg/__init__.py,,2\n'):
            with self.subTest(text=text):
                (folder / 'RECORD').write_text(text)
                self.assertEqual(self.item(self.collect(), 'python_distribution_mapping').metadata['status'], 'unavailable')

    def test_sources_unsafe_paths_do_not_override_authoritative_top_level(self):
        folder = self.distribution()
        (folder / 'RECORD').unlink()
        for entry in ('victim/../../outside.py', '../../pkg.py', '/absolute/pkg.py', 'C:/pkg.py'):
            with self.subTest(entry=entry):
                (folder / 'SOURCES.txt').write_text(entry + '\n')
                self.assertEqual(self.item(self.collect(), 'python_distribution_mapping').metadata['status'], 'available')

    def test_sources_legal_paths_do_not_establish_ownership(self):
        folder = self.distribution()
        (folder / 'top_level.txt').unlink()
        (folder / 'RECORD').unlink()
        (folder / 'SOURCES.txt').write_text('fixture_pkg/__init__.py\nstandalone.py\nREADME.md\n')
        for module in ('fixture_pkg.child', 'standalone'):
            with self.subTest(module=module):
                mapping = self.item(self.collect(module), 'python_distribution_mapping')
                self.assertEqual(mapping.metadata['status'], 'unknown')
                self.assertEqual(mapping.metadata['candidate_distributions'], ())

    def test_identical_duplicate_headers_are_accepted(self):
        folder = self.distribution()
        (folder / 'METADATA').write_text('Name: fixture-dist\nName: fixture-dist\nVersion: 1.0\nVersion: 1.0\n')
        self.assertEqual(self.item(self.collect(), 'python_distribution').metadata['status'], 'available')

    def test_conflicting_headers_never_select_first_value(self):
        folder = self.distribution()
        for text, field in (('Name: fixture-dist\nName: other\nVersion: 1.0\n', 'name'),
                            ('Name: fixture-dist\nVersion: 1.0\nVersion: 2.0\n', 'installed_version')):
            with self.subTest(field=field):
                (folder / 'METADATA').write_text(text)
                with patch('sys.path', [str(self.site)]):
                    records, errors = discover_metadata()
                self.assertTrue(errors)
                self.assertIsNone(records[0]['facts'][field])
                self.assertEqual(records[0]['facts']['status'], 'unavailable')
                self.assertEqual(self.item(self.collect(), 'python_distribution_mapping').metadata['status'], 'unavailable')

    def test_malformed_and_missing_headers_degrade_without_guessing(self):
        folder = self.distribution()
        for text in ('Name: fixture-dist\nMalformed header\nVersion: 1.0\n',
                     'Name: fixture-dist\n', 'Version: 1.0\n', 'Name: fixture-dist\nVersion: 1.0\x1b\n'):
            with self.subTest(text=text):
                (folder / 'METADATA').write_text(text)
                self.assertEqual(self.item(self.collect(), 'python_distribution_mapping').metadata['status'], 'unavailable')

    def test_pkg_info_conflicts_are_unavailable(self):
        folder = self.site / 'fixture.egg-info'
        folder.mkdir()
        (folder / 'PKG-INFO').write_text('Name: fixture\nVersion: 1\nVersion: 2\n')
        (folder / 'top_level.txt').write_text('fixture_pkg\n')
        items = self.collect()
        distribution = self.item(items, 'python_distribution')
        self.assertIsNone(distribution.metadata['installed_version'])
        self.assertEqual(distribution.metadata['status'], 'unavailable')

    def test_metadata_byte_limit_reduces_coverage_without_false_distribution(self):
        self.distribution()
        with patch('agent_doctor.python_evidence.MAX_METADATA_BYTES', 16):
            items = self.collect()
        mapping = self.item(items, 'python_distribution_mapping')
        self.assertEqual(mapping.metadata['status'], 'unavailable')
        self.assertIn('MetadataLimitExceeded', mapping.metadata['discovery_errors'])
        self.assertFalse(any(e.kind == 'python_distribution' for e in items))
        self.assertEqual(self.assessment(items)['coverage'], 'incomplete')

    def test_file_list_byte_limits_are_unavailable(self):
        folder = self.distribution()
        for filename in ('RECORD',):
            with self.subTest(filename=filename):
                (folder / filename).write_text('fixture_pkg/__init__.py' + (',,' if filename == 'RECORD' else '') + '\n')
                with patch('agent_doctor.python_evidence.MAX_FILE_LIST_BYTES', 8):
                    items = self.collect()
                self.assertEqual(self.item(items, 'python_distribution_mapping').metadata['status'], 'unavailable')
                (folder / filename).unlink()

    def test_candidate_limit_discards_partial_directory_results(self):
        self.distribution()
        self.distribution('other-dist')
        with patch('agent_doctor.python_evidence.MAX_METADATA_CANDIDATES', 1):
            items = self.collect()
        self.assertEqual(self.item(items, 'python_distribution_mapping').metadata['status'], 'unavailable')
        self.assertFalse(any(e.kind == 'python_distribution' for e in items))

    def test_directory_entry_limit_discards_partial_results(self):
        self.distribution()
        (self.site / 'extra').touch()
        with patch('agent_doctor.python_evidence.MAX_DIRECTORY_ENTRIES', 1):
            items = self.collect()
        self.assertEqual(self.item(items, 'python_distribution_mapping').metadata['status'], 'unavailable')
        self.assertFalse(any(e.kind == 'python_distribution' for e in items))

    def test_search_root_limit_is_graceful(self):
        with patch('agent_doctor.python_evidence.MAX_SEARCH_ROOTS', 0):
            items = self.collect()
        self.assertEqual(self.item(items, 'python_distribution_mapping').metadata['status'], 'unavailable')

    def test_linked_metadata_is_not_read(self):
        folder = self.distribution()
        original = Path.is_symlink
        def linked(path):
            return path == folder or original(path)
        with patch('sys.path', [str(self.site)]), patch.object(Path, 'is_symlink', linked), patch('agent_doctor.python_evidence._metadata_text') as read:
            items = self.collect()
        read.assert_not_called()
        self.assertEqual(self.item(items, 'python_distribution_mapping').metadata['status'], 'unavailable')

    def test_linked_search_root_is_not_scanned(self):
        self.distribution()
        original = Path.is_junction
        def linked(path):
            return path == self.site or original(path)
        with patch('sys.path', [str(self.site)]), patch.object(Path, 'is_junction', linked), \
                patch('agent_doctor.python_evidence.os.scandir') as scan:
            items = collect_import_evidence((self.target(),))
        scan.assert_not_called()
        self.assertEqual(self.item(items, 'python_distribution_mapping').metadata['status'], 'unavailable')

    def test_missing_header_fields_remain_null(self):
        folder = self.distribution()
        for text, field in (('Name: fixture-dist\n', 'installed_version'), ('Version: 1.0\n', 'name')):
            with self.subTest(field=field):
                (folder / 'METADATA').write_text(text)
                with patch('sys.path', [str(self.site)]):
                    records, errors = discover_metadata()
                self.assertIsNone(records[0]['facts'][field])
                self.assertEqual(records[0]['facts']['status'], 'unavailable')
                self.assertTrue(errors)

    def test_file_list_targets_are_never_opened(self):
        folder = self.distribution()
        (folder / 'top_level.txt').unlink()
        (folder / 'RECORD').write_text('fixture_pkg/__init__.py,,\n')
        original = Path.open
        opened = []
        def audit(path, *args, **kwargs):
            opened.append(path)
            return original(path, *args, **kwargs)
        with patch('sys.path', [str(self.site)]), patch.object(Path, 'open', audit):
            records, errors = discover_metadata()
        self.assertEqual(errors, ())
        self.assertEqual(records[0]['modules'], ('fixture_pkg',))
        self.assertEqual(set(opened), {folder / 'METADATA', folder / 'RECORD'})

    def source_layout(self):
        folder = self.distribution(name='real-dist', module='real_pkg')
        (folder / 'top_level.txt').unlink()
        (folder / 'RECORD').unlink()
        (folder / 'SOURCES.txt').write_text('src/real_pkg/__init__.py\ndocs/conf.py\n')
        return folder

    def test_source_src_prefix_is_not_a_distribution_candidate(self):
        self.source_layout()
        mapping = self.item(self.collect('src'), 'python_distribution_mapping')
        self.assertEqual(mapping.metadata['status'], 'unknown')
        self.assertEqual(mapping.metadata['candidate_distributions'], ())

    def test_source_docs_prefix_is_not_a_distribution_candidate(self):
        self.source_layout()
        mapping = self.item(self.collect('docs'), 'python_distribution_mapping')
        self.assertEqual(mapping.metadata['status'], 'unknown')
        self.assertEqual(mapping.metadata['candidate_distributions'], ())

    def test_source_layout_alone_keeps_installed_package_ownership_unknown(self):
        self.source_layout()
        self.module('real_pkg', package=True)
        items = self.collect('real_pkg')
        self.assertEqual(self.item(items, 'python_module_origin').metadata['status'], 'available')
        self.assertEqual(self.item(items, 'python_distribution_mapping').metadata['status'], 'unknown')
        self.assertFalse(any(e.kind == 'python_distribution' for e in items))

    def test_top_level_maps_real_package_despite_source_layout(self):
        folder = self.source_layout()
        (folder / 'top_level.txt').write_text('real_pkg\n')
        mapping = self.item(self.collect('real_pkg'), 'python_distribution_mapping')
        self.assertEqual(mapping.metadata['status'], 'available')
        self.assertEqual(mapping.metadata['candidate_distributions'], ('real-dist',))

    def test_sources_content_is_never_read(self):
        folder = self.source_layout()
        original = Path.open
        def guard(path, *args, **kwargs):
            if path.name == 'SOURCES.txt':
                raise AssertionError('source manifest must not be consumed')
            return original(path, *args, **kwargs)
        with patch('sys.path', [str(self.site)]), patch.object(Path, 'open', guard):
            items = self.collect('src')
        self.assertEqual(self.item(items, 'python_distribution_mapping').metadata['status'], 'unknown')
