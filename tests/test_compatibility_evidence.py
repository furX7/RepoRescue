"""Step 5 facts do not execute code or become cross-evidence diagnoses."""

from contextlib import redirect_stdout
from dataclasses import replace
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from agent_doctor.cli import main
from agent_doctor.compatibility_evidence import (
    collect_compatibility_evidence, expand_tags, wheel_filename,
    extract_wheel_log_facts, tag_check, check_tags, runtime_facts,
    MAX_BYTES, MAX_TAGS, MAX_TARGETS, MAX_AST_NODES,
)
from agent_doctor.models import Evidence
from agent_doctor.workflow import run_workflow


def windows_runtime(**updates):
    return dict({'implementation': 'cpython', 'python_version': (3, 13, 0),
                 'sys_platform': 'win32', 'platform_tag': 'win_amd64',
                 'native_abi': 'cp313', 'gil_disabled': 0, 'debug_build': 0,
                 'pointer_bits': 64,
                 'status': 'available'}, **updates)


class CompatibilityTagTests(unittest.TestCase):
    def test_pure_python_matches_without_runtime_claim(self):
        facts = check_tags(('py3-none-any',), windows_runtime())
        self.assertEqual(facts['tag_check'], 'tag_match')
        self.assertEqual(facts['runtime_compatibility'], 'unknown')

    def test_native_cpython_windows_match(self):
        self.assertEqual(tag_check('cp313-cp313-win_amd64', windows_runtime()), 'tag_match')

    def test_python_minor_mismatch(self):
        self.assertEqual(tag_check('cp312-cp312-win_amd64', windows_runtime()), 'tag_mismatch')

    def test_architecture_mismatch(self):
        for target in ('win32', 'win_arm64'):
            self.assertEqual(tag_check('cp313-cp313-' + target, windows_runtime()), 'tag_mismatch')

    def test_os_mismatch(self):
        for target in ('linux_x86_64', 'manylinux_2_17_x86_64', 'musllinux_1_2_x86_64', 'macosx_11_0_arm64'):
            self.assertEqual(tag_check('cp313-cp313-' + target, windows_runtime()), 'tag_mismatch')

    def test_abi3_lower_bound(self):
        self.assertEqual(tag_check('cp38-abi3-win_amd64', windows_runtime()), 'tag_match')
        self.assertEqual(tag_check('cp314-abi3-win_amd64', windows_runtime()), 'tag_mismatch')

    def test_free_threaded_abi3_unknown(self):
        self.assertEqual(tag_check('cp38-abi3-win_amd64', windows_runtime(gil_disabled=1, native_abi='cp313t')), 'unknown')

    def test_native_unknown_abi_not_claimed(self):
        self.assertEqual(tag_check('cp313-cp313-win_amd64', windows_runtime(native_abi=None)), 'unknown')

    def test_gil_and_free_threaded_native_abis_are_distinct(self):
        self.assertEqual(tag_check('cp313-cp313t-win_amd64', windows_runtime()), 'tag_mismatch')
        self.assertEqual(tag_check('cp313-cp313t-win_amd64', windows_runtime(gil_disabled=1, native_abi='cp313t')), 'tag_match')

    def test_debug_build_has_distinct_observation(self):
        runtime = windows_runtime(native_abi='cp313d', debug_build=1)
        self.assertEqual(tag_check('cp313-cp313d-win_amd64', runtime), 'tag_match')
        self.assertEqual(tag_check('cp313-cp313-win_amd64', runtime), 'tag_match')

    def test_manylinux_libc_not_guessed(self):
        runtime = windows_runtime(sys_platform='linux', platform_tag='linux_x86_64')
        self.assertEqual(tag_check('cp313-cp313-manylinux_2_17_x86_64', runtime), 'unknown')
        self.assertEqual(tag_check('cp313-cp313-musllinux_1_2_x86_64', runtime), 'unknown')

    def test_linux_simple_platform_exact(self):
        runtime = windows_runtime(sys_platform='linux', platform_tag='linux_x86_64')
        self.assertEqual(tag_check('cp313-cp313-linux_x86_64', runtime), 'tag_match')

    def test_linux_kernel_architecture_does_not_prove_interpreter_bitness(self):
        for arch in ('x86_64', 'aarch64'):
            runtime = windows_runtime(sys_platform='linux', platform_tag='linux_' + arch, pointer_bits=32)
            self.assertEqual(tag_check('cp313-cp313-linux_' + arch, runtime), 'unknown')

    def test_cross_build_platform_cannot_match_current_runtime(self):
        runtime = windows_runtime(sys_platform='linux', platform_tag='linux_aarch64', platform_tag_overridden=True)
        self.assertEqual(tag_check('cp313-cp313-linux_aarch64', runtime), 'unknown')
        self.assertEqual(tag_check('py3-none-any', runtime), 'tag_match')

    def test_linux_bitness_unknown_not_available_tag_match(self):
        runtime = windows_runtime(sys_platform='linux', platform_tag='linux_x86_64', pointer_bits=None)
        self.assertEqual(tag_check('cp313-cp313-linux_x86_64', runtime), 'unknown')

    def test_macos_deployment_target_not_guessed(self):
        runtime = windows_runtime(sys_platform='darwin', platform_tag='macosx_15_0_arm64')
        self.assertEqual(tag_check('cp313-cp313-macosx_11_0_universal2', runtime), 'unknown')

    def test_unrecognized_tag_families_unknown(self):
        for tag in ('pp310-pypy310_pp73-win_amd64', 'alien-none-any', 'cp313-unknown-win_amd64'):
            self.assertEqual(tag_check(tag, windows_runtime()), 'unknown')

    def test_native_any_not_universal_claim(self):
        self.assertEqual(tag_check('cp313-cp313-any', windows_runtime()), 'unknown')

    def test_tag_alternatives(self):
        self.assertEqual(check_tags(('py2-none-any', 'py3-none-any'), windows_runtime())['tag_check'], 'tag_match')
        self.assertEqual(check_tags(('py2-none-any', 'alien-none-any'), windows_runtime())['tag_check'], 'unknown')

    def test_compressed_tags_cartesian(self):
        self.assertEqual(expand_tags(('py2.py3-none-any',)), ('py2-none-any', 'py3-none-any'))

    def test_compression_limit_and_invalid_tags(self):
        for raw in ('../cp313-none-any', 'cp313-none', 'cp313-none-any\x1b',
                    '.'.join(f'py{i}' for i in range(MAX_TAGS + 1)) + '-none-any'):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                expand_tags((raw,))

    def test_filename_build_and_identity(self):
        facts = wheel_filename('Foo_Bar-1.2.3-1abc-py2.py3-none-any.whl')
        self.assertEqual((facts['name'], facts['version'], facts['build']), ('Foo_Bar', '1.2.3', '1abc'))
        self.assertEqual(facts['tags'], ('py2-none-any', 'py3-none-any'))

    def test_filename_rejects_path_and_malformed(self):
        for file in ('../../foo-1-py3-none-any.whl', 'foo-1-build-py3-none-any.whl',
                     'foo-latest-py3-none-any.whl', 'foo-bar-1-py3-none-any.whl', 'foo-1-py3-none-any.whl.exe'):
            with self.subTest(file=file), self.assertRaises(ValueError):
                wheel_filename(file)

    def test_runtime_facts_current_only(self):
        facts = runtime_facts()
        self.assertEqual(facts['interpreter_ref'], 'environment:interpreter')
        self.assertEqual(facts['runtime_scope'], 'current_interpreter_only')
        self.assertIn('soabi', facts)
        self.assertIn('platform_tag', facts)

    def test_runtime_failure_graceful(self):
        with patch('agent_doctor.compatibility_evidence.sysconfig._CONFIG_VARS', {}), patch('agent_doctor.compatibility_evidence.sysconfig.get_platform', side_effect=ValueError('private')):
            facts = runtime_facts()
        self.assertEqual(facts['status'], 'unavailable')
        self.assertNotIn('private', str(facts))

    def test_uninitialized_build_config_does_not_initialize_or_import(self):
        with patch('agent_doctor.compatibility_evidence.sysconfig._CONFIG_VARS', None), patch('agent_doctor.compatibility_evidence.sysconfig.get_config_var') as get_var:
            facts = runtime_facts()
        self.assertEqual(facts['status'], 'unavailable')
        self.assertEqual(facts['limitation'], 'build_configuration_not_initialized')
        self.assertIsNone(facts['native_abi'])
        get_var.assert_not_called()

    def test_runtime_records_cross_build_override_as_unproven(self):
        import os
        with patch('agent_doctor.compatibility_evidence.sys.platform', 'linux'), patch.dict(os.environ, {'_PYTHON_HOST_PLATFORM': 'linux-aarch64'}), patch('agent_doctor.compatibility_evidence.sysconfig.get_platform', return_value='linux-aarch64'):
            facts = runtime_facts()
        self.assertTrue(facts['platform_tag_overridden'])
        self.assertEqual(tag_check('cp313-cp313-linux_aarch64', facts), 'unknown')

    def test_no_targets_no_runtime_or_source_read(self):
        with patch('agent_doctor.compatibility_evidence.runtime_facts') as runtime, patch.object(Path, 'open') as read:
            self.assertEqual(collect_compatibility_evidence(()), ())
        runtime.assert_not_called()
        read.assert_not_called()


class CompatibilitySourceTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.dist = self.root / 'fixture-1.dist-info'
        self.dist.mkdir()
        self.wheel = self.dist / 'WHEEL'
        self.source = self.root / 'fixture.py'
        self.distribution = Evidence('distribution:0', 'python_distribution', 'stdlib:filesystem_metadata', 'fixture',
                                     metadata={'name': 'fixture', 'status': 'available',
                                               'metadata_location': str(self.dist / 'METADATA'),
                                               'dist_info_location': str(self.dist)})
        self.origin = Evidence('module:fixture', 'python_module_origin', 'stdlib:importlib.machinery', 'source',
                              metadata={'module': 'fixture', 'origin': str(self.source), 'status': 'available'})
        self.request = Evidence('input:log:python_import', 'python_import_failure', 'provided_traceback', 'failure',
                               metadata={'source_module': 'fixture', 'imported_symbol': 'Feature'})
        mock = patch('agent_doctor.compatibility_evidence.runtime_facts', return_value=windows_runtime())
        mock.start()
        self.addCleanup(mock.stop)

    def collect(self, *items):
        return collect_compatibility_evidence(items)

    def wheel_text(self, tags='py3-none-any', **updates):
        values = dict({'version': '1.0', 'pure': 'true', 'tags': tags}, **updates)
        return f"Wheel-Version: {values['version']}\nRoot-Is-Purelib: {values['pure']}\n" + ''.join(f'Tag: {tag}\n' for tag in values['tags'].split('\n'))

    def wheel_item(self, text=None, distribution=None):
        if text is not None:
            self.wheel.write_text(text)
        return self.collect(distribution or self.distribution)[1]

    def api_item(self, text=None, request=None, origin=None):
        if text is not None:
            self.source.write_text(text, encoding='utf-8')
        return self.collect(request or self.request, origin or self.origin)[1]

    def test_installed_wheel_retains_owner_ref(self):
        item = self.wheel_item(self.wheel_text())
        self.assertEqual(item.metadata['status'], 'available')
        self.assertEqual(item.metadata['tag_check'], 'tag_match')
        self.assertEqual(item.metadata['source_evidence_ref'], 'distribution:0')
        self.assertEqual(item.location, str(self.wheel))
        self.assertEqual(item.metadata['runtime_compatibility'], 'unknown')

    def test_missing_wheel_is_limitation(self):
        self.assertEqual(self.wheel_item().metadata['status'], 'unavailable')

    def test_malformed_wheel_headers(self):
        for text in ('no header', 'Wheel-Version: 1.0\nTag: py3-none-any\n',
                     self.wheel_text() + 'Wheel-Version: 2.0\n', self.wheel_text() + '\nbody'):
            with self.subTest(text=text):
                self.assertEqual(self.wheel_item(text).metadata['status'], 'unavailable')

    def test_identical_duplicate_required_headers_are_not_available(self):
        for header in ('Wheel-Version: 1.0\n', 'Root-Is-Purelib: true\n'):
            with self.subTest(header=header):
                self.assertEqual(self.wheel_item(self.wheel_text() + header).metadata['status'], 'unavailable')

    def test_unsupported_wheel_version(self):
        self.assertEqual(self.wheel_item(self.wheel_text(version='2.0')).metadata['status'], 'unavailable')

    def test_duplicate_tags_preserved_and_deduplicated_for_checks(self):
        item = self.wheel_item(self.wheel_text(tags='py3-none-any\npy3-none-any'))
        self.assertEqual(len(item.metadata['raw_tags']), 2)
        self.assertEqual(item.metadata['tags'], ('py3-none-any',))

    def test_available_tags_unknown_native_runtime(self):
        item = self.wheel_item(self.wheel_text(tags='cp313-unknown-win_amd64', pure='false'))
        self.assertEqual(item.metadata['status'], 'available')
        self.assertEqual(item.metadata['tag_check'], 'unknown')

    def test_wheel_not_borrowed_from_other_metadata(self):
        other = self.root / 'other.dist-info'
        other.mkdir()
        (other / 'WHEEL').write_text(self.wheel_text())
        bad = replace(self.distribution, metadata=dict(self.distribution.metadata, metadata_location=str(other / 'METADATA')))
        with patch.object(Path, 'open', side_effect=AssertionError('must not open')):
            item = self.wheel_item(distribution=bad)
        self.assertEqual(item.metadata['status'], 'unavailable')

    def test_linked_wheel_rejected(self):
        self.wheel.write_text(self.wheel_text())
        original = Path.is_symlink
        with patch.object(Path, 'is_symlink', lambda p: p == self.wheel or original(p)):
            self.assertEqual(self.wheel_item().metadata['status'], 'unavailable')

    def test_linked_parent_rejected(self):
        self.wheel.write_text(self.wheel_text())
        original = Path.is_junction
        with patch.object(Path, 'is_junction', lambda p: p == self.dist or original(p)):
            self.assertEqual(self.wheel_item().metadata['status'], 'unavailable')

    def test_oversized_wheel_and_api_source(self):
        self.assertEqual(self.wheel_item('x' * (MAX_BYTES + 1)).metadata['status'], 'unavailable')
        self.assertEqual(self.api_item('#' * (MAX_BYTES + 1)).metadata['status'], 'unavailable')

    def test_invalid_utf8_wheel(self):
        self.wheel.write_bytes(b'\xff')
        self.assertEqual(self.wheel_item().metadata['status'], 'unavailable')

    def test_unavailable_distribution_not_available_wheel(self):
        bad = replace(self.distribution, metadata=dict(self.distribution.metadata, status='unavailable'))
        item = self.wheel_item(self.wheel_text(), bad)
        self.assertEqual(item.metadata['status'], 'unknown')

    def test_duplicate_distribution_origins_kept(self):
        self.wheel.write_text(self.wheel_text())
        second = replace(self.distribution, evidence_id='distribution:1')
        items = self.collect(self.distribution, second)
        self.assertEqual([e.metadata['source_evidence_ref'] for e in items[1:]], ['distribution:0', 'distribution:1'])
        self.assertEqual(len({e.evidence_id for e in items}), len(items))

    def test_api_static_binding_not_execution_or_compatibility(self):
        marker = self.root / 'executed'
        item = self.api_item(f'from pathlib import Path\nPath({str(marker)!r}).touch()\nclass Feature: pass\n')
        self.assertEqual(item.metadata['symbol_observation'], 'direct_syntax_seen')
        self.assertEqual(item.metadata['api_compatibility'], 'unknown')
        self.assertFalse(marker.exists())
        self.assertEqual(item.metadata['module_origin_ref'], 'module:fixture')

    def test_api_missing_static_name_not_proof_absent(self):
        item = self.api_item('def __getattr__(name): return object()\n')
        self.assertEqual(item.metadata['status'], 'available')
        self.assertEqual(item.metadata['symbol_observation'], 'not_seen_in_direct_syntax')
        self.assertEqual(item.metadata['api_compatibility'], 'unknown')

    def test_api_conditional_and_wildcard_not_evaluated(self):
        item = self.api_item('if True:\n Feature = 1\nfrom somewhere import *\n')
        self.assertEqual(item.metadata['direct_syntax_bindings'], ())

    def test_literal_all_is_syntax_only(self):
        item = self.api_item('__all__=["Feature"]\n')
        self.assertTrue(item.metadata['literal_all_observations'][0]['contains_symbol'])
        self.assertEqual(item.metadata['direct_syntax_bindings'], ())
        self.assertEqual(item.metadata['api_compatibility'], 'unknown')

    def test_annotations_and_decorators_not_evaluated(self):
        item = self.api_item('@undefined()\ndef Feature(x: missing()): pass\n')
        self.assertEqual(item.metadata['direct_syntax_bindings'][0]['syntax'], 'FunctionDef')

    def test_api_source_coding_cookie_supported(self):
        self.source.write_bytes(b'# coding: latin-1\nFeature="\xe9"\n')
        self.assertEqual(self.api_item().metadata['status'], 'available')

    def test_hostile_encoding_cookie_never_calls_registered_codec_search(self):
        import codecs
        calls = []
        def search(name):
            calls.append(name)
            return None
        codecs.register(search)
        try:
            item = self.api_item('# coding: step5_hostile_codec_probe_927\nFeature=1\n')
            self.assertEqual(item.metadata['status'], 'unavailable')
            self.assertEqual(calls, [])
        finally:
            codecs.unregister(search)

    def test_source_encoding_aliases_bom_and_conflicts(self):
        for data in (b'\xef\xbb\xbf# coding: utf-8\nFeature=1\n',
                     b'#!/usr/bin/python\n# coding: iso-8859-1\nFeature="\xe9"\n',
                     b'# coding: ascii\nFeature=1\n'):
            with self.subTest(data=data):
                self.source.write_bytes(data)
                self.assertEqual(self.api_item().metadata['status'], 'available')
        for data in (b'\xef\xbb\xbf# coding: latin-1\nFeature=1\n',
                     b'# coding: cp1252\nFeature=1\n'):
            with self.subTest(data=data):
                self.source.write_bytes(data)
                self.assertEqual(self.api_item().metadata['status'], 'unavailable')

    def test_syntax_error_and_ast_node_limit(self):
        self.assertEqual(self.api_item('def Feature(').metadata['status'], 'unavailable')
        self.assertEqual(self.api_item('x=1\n' * MAX_AST_NODES).metadata['status'], 'unavailable')

    def test_non_python_origin_not_loaded(self):
        for value in ('built-in', 'frozen', str(self.root / 'fixture.pyd'), None):
            with self.subTest(value=value):
                origin = replace(self.origin, metadata=dict(self.origin.metadata, origin=value))
                self.assertEqual(self.api_item(origin=origin).metadata['status'], 'unavailable')

    def test_target_limit_explicit(self):
        items = self.collect(*(replace(self.distribution, evidence_id=f'distribution:{i}') for i in range(MAX_TARGETS + 1)))
        self.assertEqual(items[-1].metadata['limitation'], 'target_limit')
        self.assertEqual(len(items), MAX_TARGETS + 2)

    def test_path_traversal_source_not_read(self):
        origin = replace(self.origin, metadata=dict(self.origin.metadata, origin=str(self.dist / '..' / 'fixture.py')))
        with patch.object(Path, 'open', side_effect=AssertionError('must not open')):
            self.assertEqual(self.api_item(origin=origin).metadata['status'], 'unavailable')


class CompatibilityWorkflowTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.project = self.root / 'project'
        self.project.mkdir()
        (self.project / 'main.py').write_text('raise RuntimeError("must not run")')
        self.log = self.root / 'install.log'
        mock = patch('agent_doctor.python_evidence.discover_metadata', return_value=([], ()))
        self.discover = mock.start()
        self.addCleanup(mock.stop)

    def test_supplied_rejection_not_current_runtime_diagnosis(self):
        self.log.write_text('ERROR: fixture-1-cp312-cp312-win32.whl is not a supported wheel on this platform.')
        with patch('agent_doctor.compatibility_evidence.runtime_facts', return_value=windows_runtime()):
            result = run_workflow(self.project, install_log=self.log)
        wheel = next(e for e in result.evidence if e.kind == 'python_wheel')
        self.assertEqual(wheel.metadata['observation'], 'logged_rejection')
        self.assertEqual(wheel.metadata['source_evidence_ref'], 'input:log')
        self.assertEqual(wheel.metadata['tag_check'], 'tag_mismatch')
        self.assertEqual(wheel.metadata['runtime_scope'], 'supplied_log_only')
        self.assertEqual(result.diagnostics, ())
        self.assertEqual(result.report['assessment']['outcome'], 'inconclusive')
        self.discover.assert_not_called()

    def test_log_artifact_is_not_resolved_or_installed(self):
        self.log.write_text('Downloading fixture-1-py3-none-any.whl (12 kB)')
        result = run_workflow(self.project, install_log=self.log)
        self.assertTrue(any(e.kind == 'python_wheel' for e in result.evidence))
        self.assertFalse(any(e.kind in ('python_version_provenance', 'python_distribution') for e in result.evidence))

    def test_real_pip_indentation_preserves_artifact_token_and_line(self):
        for line in ('  Downloading fixture-1-py3-none-any.whl (12 kB)',
                     '  Using cached fixture-1-py3-none-any.whl (12 kB)',
                     '\tProcessing /tmp/fixture-1-py3-none-any.whl',
                     '  ERROR: fixture-1-py3-none-any.whl is not a supported wheel on this platform.'):
            with self.subTest(line=line):
                facts = extract_wheel_log_facts('pip preamble\n' + line)
                self.assertEqual(len(facts['wheel_artifacts']), 1)
                item = facts['wheel_artifacts'][0]
                self.assertEqual(item['evidence_origin'], 'line:2')
                self.assertEqual(item['filename'], 'fixture-1-py3-none-any.whl')
                self.assertEqual(item['artifact_token'], '/tmp/fixture-1-py3-none-any.whl' if 'Processing' in line else 'fixture-1-py3-none-any.whl')

    def test_indented_cached_wheel_reaches_workflow_without_provenance_claim(self):
        self.log.write_text('  Using cached fixture-1-py3-none-any.whl (12 kB)')
        result = run_workflow(self.project, install_log=self.log)
        wheel = next(e for e in result.evidence if e.kind == 'python_wheel')
        self.assertEqual(wheel.metadata['observation'], 'artifact_mention')
        self.assertEqual(wheel.metadata['source_evidence_ref'], 'input:log')
        self.assertFalse(any(e.kind == 'python_version_provenance' for e in result.evidence))
        self.discover.assert_not_called()

    def test_log_commands_and_remote_url_never_executed_or_opened(self):
        facts = extract_wheel_log_facts('$ curl https://example.invalid/a-1-py3-none-any.whl\nDownloading https://example.invalid/a-1-py3-none-any.whl')
        self.assertEqual(len(facts['wheel_artifacts']), 1)
        self.assertEqual(facts['wheel_artifacts'][0]['filename'], 'a-1-py3-none-any.whl')

    def test_malformed_logged_wheel_unknown(self):
        self.log.write_text('Processing unsupported.whl')
        result = run_workflow(self.project, install_log=self.log)
        wheel = next(e for e in result.evidence if e.kind == 'python_wheel')
        self.assertEqual(wheel.metadata['status'], 'unknown')

    def test_log_artifact_count_limit(self):
        facts = extract_wheel_log_facts('Processing a-1-py3-none-any.whl\n' * (MAX_TARGETS + 1))
        self.assertEqual(facts['wheel_artifacts'], ())
        self.assertEqual(facts['wheel_limitations'], ('artifact_limit',))

    def test_multiple_logs_share_target_budget(self):
        facts = extract_wheel_log_facts('Processing a-1-py3-none-any.whl\n' * MAX_TARGETS)
        logs = [Evidence(f'log:{i}', 'provided_log', 'stdin', 'log',
                         metadata=dict(facts, input_type='install_log')) for i in range(2)]
        with patch('agent_doctor.compatibility_evidence.runtime_facts', return_value=windows_runtime()):
            items = collect_compatibility_evidence(logs)
        self.assertEqual(len(items), MAX_TARGETS + 2)
        self.assertEqual(items[-1].metadata['limitation'], 'target_limit')

    def test_real_metadata_and_static_api_flow_without_execution(self):
        site = self.root / 'site'
        site.mkdir()
        marker = self.root / 'executed'
        source = site / 'fixture.py'
        source.write_text(f'from pathlib import Path\nPath({str(marker)!r}).touch()\nclass Feature: pass\n')
        directory = site / 'fixture-1.dist-info'
        directory.mkdir()
        (directory / 'METADATA').write_text('Name: fixture\nVersion: 1\n')
        (directory / 'top_level.txt').write_text('fixture\n')
        (directory / 'WHEEL').write_text('Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n')
        self.log.write_text("ImportError: cannot import name 'Feature' from 'fixture'")
        records = [{'facts': {'name': 'fixture', 'installed_version': '1', 'status': 'available',
                              'metadata_location': str(directory / 'METADATA'),
                              'dist_info_location': str(directory)}, 'modules': ('fixture',)}]
        self.discover.return_value = (records, ())
        import sys
        with patch('sys.path', [str(site), *sys.path]):
            result = run_workflow(self.project, traceback_file=self.log)
        api = next(e for e in result.evidence if e.kind == 'python_api')
        wheel = next(e for e in result.evidence if e.kind == 'python_wheel')
        self.assertEqual(api.metadata['symbol_observation'], 'direct_syntax_seen')
        self.assertEqual(wheel.metadata['tag_check'], 'tag_match')
        self.assertEqual(result.report['assessment']['outcome'], 'issues_detected')
        self.assertEqual(len(result.diagnostics), 1)
        self.assertFalse(marker.exists())
        limitations = result.report['assessment']['limitations']
        self.assertIn('static_syntax_only', {item['reason'] for item in limitations})
        refs = {e.evidence_id for e in result.evidence}
        for item in (api, wheel):
            self.assertIn(item.metadata['source_evidence_ref'], refs)

    def test_terminal_control_escaping_with_wheel_log(self):
        self.log.write_text('Processing https://example.invalid/\x1b/a-1-py3-none-any.whl')
        result = run_workflow(self.project, install_log=self.log)
        self.assertNotIn('\x1b', result.terminal_report)
        self.assertTrue(any(e.kind == 'python_wheel' for e in result.evidence))

    def test_cli_schema_and_exit_code_unchanged(self):
        self.log.write_text('Processing a-1-py3-none-any.whl')
        output = self.root / 'report.json'
        with redirect_stdout(io.StringIO()):
            self.assertEqual(main([str(self.project), '--install-log', str(self.log), '--output', str(output)]), 0)
        report = json.loads(output.read_text())
        self.assertEqual(report['schema_version'], '0.2')
        self.assertEqual(report['status'], 'healthy')

    def test_no_dependency_or_symbol_target_no_new_scan(self):
        result = run_workflow(self.project)
        self.assertFalse(any(e.kind in ('python_api', 'python_wheel', 'python_abi_platform') for e in result.evidence))
        self.discover.assert_not_called()

    def test_fresh_artifact_only_workflow_does_not_run_external_import_finder(self):
        import subprocess
        import sys
        self.log.write_text('Processing f-1-py3-none-any.whl')
        probe = '''import json, sys
sys.path.insert(0, sys.argv[1])
from agent_doctor.workflow import run_workflow
import agent_doctor.python_extension as python_extension
if sys.argv[4]=='off':
    python_extension.collect_compatibility_evidence=lambda evidence: ()
calls=[]
class ProbeFinder:
    def find_spec(self, fullname, path=None, target=None):
        calls.append(fullname)
        return None
finder=ProbeFinder()
sys.meta_path.insert(0,finder)
try:
    result=run_workflow(sys.argv[2],install_log=sys.argv[3])
finally:
    sys.meta_path.remove(finder)
print(json.dumps({'calls':calls,'wheel_count':sum(e.kind=='python_wheel' for e in result.evidence)}))
'''
        source = Path(__file__).resolve().parents[1] / 'src'
        def run_probe(mode):
            result = subprocess.run([sys.executable, '-I', '-c', probe, str(source), str(self.project), str(self.log), mode],
                                    capture_output=True, text=True, timeout=15, check=True)
            return json.loads(result.stdout)
        baseline, enabled = run_probe('off'), run_probe('on')
        # Existing log intake/reporting can load their own stdlib modules. The
        # new collector must add zero finder callbacks to that causal baseline.
        self.assertEqual(enabled['calls'], baseline['calls'])
        self.assertNotIn('_sysconfig', enabled['calls'])
        self.assertEqual(baseline['wheel_count'], 0)
        self.assertEqual(enabled['wheel_count'], 1)

    def test_symbol_error_and_api_limitation_keep_error(self):
        self.log.write_text("ImportError: cannot import name 'Feature' from 'fixture'")
        result = run_workflow(self.project, traceback_file=self.log)
        self.assertEqual(result.report['assessment']['outcome'], 'issues_detected')
        self.assertTrue(any(e.kind == 'python_api' and e.metadata['status'] == 'unavailable' for e in result.evidence))


if __name__ == '__main__':
    unittest.main()
