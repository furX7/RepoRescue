"""User-supplied logs are bounded observations, never executable instructions."""

import io
import json
from pathlib import Path
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr
from unittest.mock import patch

from agent_doctor.cli import main
from agent_doctor.log_input import MAX_INPUT_BYTES, read_log
from agent_doctor.workflow import WorkflowError, run_workflow
from agent_doctor.report import safe_terminal_text


class LogInputTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.base = Path(directory.name)
        self.project = self.base / 'project'
        self.project.mkdir()
        (self.project / 'main.py').write_text("raise RuntimeError('do not run')")
        self.log = self.base / 'error.log'

    def run_log(self, text, *, install=False, **options):
        self.log.write_text(text, encoding='utf-8')
        key = 'install_log' if install else 'traceback_file'
        return run_workflow(self.project, **{key: self.log}, **options)

    def input_evidence(self, result):
        return next(e for e in result.evidence if e.kind in ('provided_log', 'ingestion_limitation'))

    def test_traceback_missing_module_without_startup(self):
        result = self.run_log("Traceback (most recent call last):\nModuleNotFoundError: No module named 'missing_lib'\n")
        diagnostic = next(d for d in result.diagnostics if d.category == 'python_import')
        self.assertEqual(diagnostic.source, 'rule:python_module_not_found')
        self.assertEqual(result.report['assessment']['outcome'], 'issues_detected')
        self.assertEqual(result.execution_results[-1].status, 'requires_confirmation')
        imported = next(e for e in result.evidence if e.kind == 'python_import_failure')
        self.assertEqual(imported.source, 'provided_traceback')
        self.assertEqual(imported.associated_id, 'input:log')
        self.assertEqual(self.input_evidence(result).location, str(self.log.absolute()))
        self.assertNotIn('text', self.input_evidence(result).metadata)

    def test_traceback_missing_symbol(self):
        result = self.run_log("ImportError: cannot import name 'ColorPrimaries' from 'av' (av/__init__.py)")
        self.assertEqual(result.diagnostics[0].source, 'rule:python_symbol_import_failure')
        self.assertIn('Import symbol: ColorPrimaries', result.terminal_report)
        self.assertEqual(result.diagnostics[0].probable_causes, ())

    def test_pip_no_matching_distribution(self):
        result = self.run_log('ERROR: No matching distribution found for tensorflow', install=True)
        item = self.input_evidence(result)
        self.assertEqual(item.metadata['patterns'], ('no_matching_distribution',))
        self.assertEqual(result.report['assessment']['outcome'], 'inconclusive')
        self.assertIn('No matching distribution found for tensorflow', result.terminal_report)
        self.assertEqual(result.diagnostics, ())

    def test_unsatisfied_requirement(self):
        result = self.run_log('ERROR: Could not find a version that satisfies the requirement example (from versions: none)', install=True)
        self.assertEqual(self.input_evidence(result).metadata['patterns'], ('unsatisfied_requirement',))

    def test_requires_python_ignored_versions(self):
        result = self.run_log('ERROR: Ignored the following versions that require a different python version: 1.0 Requires-Python >=3.8,<3.13\nRequires-Python: >=3.8\nERROR: No matching distribution found for example', install=True)
        self.assertEqual(self.input_evidence(result).metadata['patterns'],
                         ('ignored_python_versions', 'requires_python', 'no_matching_distribution'))
        self.assertEqual(result.report['assessment']['outcome'], 'inconclusive')
        self.assertFalse(any(d.category == 'python_version' for d in result.diagnostics))

    def test_empty_log(self):
        result = self.run_log('')
        self.assertEqual(self.input_evidence(result).metadata['status'], 'empty')
        self.assertEqual(result.report['assessment']['outcome'], 'inconclusive')

    def test_malformed_log(self):
        result = self.run_log("$ python -c 'print(42)'\nImportError: unexpected data")
        self.assertEqual(self.input_evidence(result).metadata['status'], 'unrecognized')
        self.assertEqual(result.diagnostics, ())

    def test_whitespace_log_is_empty(self):
        result = self.run_log(' \n\t')
        self.assertEqual(self.input_evidence(result).metadata['status'], 'empty')

    def assert_ingestion_failure(self, result, reason):
        item = self.input_evidence(result)
        self.assertEqual(item.kind, 'ingestion_limitation')
        self.assertEqual(item.metadata['status'], reason)
        self.assertFalse(any(e.kind in ('provided_log', 'python_import_failure') for e in result.evidence))
        self.assertEqual(result.report['assessment']['outcome'], 'inconclusive')
        self.assertEqual(result.report['assessment']['coverage'], 'incomplete')
        limitation = next(l for l in result.report['assessment']['limitations'] if l['check'] == 'log_ingestion')
        self.assertEqual(limitation['reason'], reason)
        self.assertEqual(limitation['source'], item.source)
        self.assertEqual(limitation['requested_path'], item.location)
        self.assertIn(reason, result.terminal_report)
        self.assertTrue(result.environment.python_callable)
        self.assertEqual(result.diagnostics, ())

    def test_oversized_file_limitation_checks_continue(self):
        self.log.write_bytes(b'x' * (MAX_INPUT_BYTES + 1))
        self.assert_ingestion_failure(run_workflow(self.project, traceback_file=self.log), 'oversized_input')

    def test_missing_file_limitation(self):
        self.assert_ingestion_failure(run_workflow(self.project, install_log=self.log), 'missing_file')

    def test_directory_limitation(self):
        item = read_log(self.project, 'traceback')
        self.assertEqual(item.kind, 'ingestion_limitation')
        self.assertEqual(item.metadata['status'], 'not_regular_file')

    def test_invalid_utf8_limitation(self):
        self.log.write_bytes(b'\xff')
        self.assert_ingestion_failure(run_workflow(self.project, install_log=self.log), 'invalid_utf8')

    def test_log_commands_never_executed_or_project_changed(self):
        marker = self.project / 'executed.txt'
        before = (self.project / 'main.py').read_bytes()
        text = f"python -c \"open(r'{marker}', 'w').write('executed')\"\nModuleNotFoundError: No module named 'missing_lib'"
        result = self.run_log(text)
        self.assertFalse(marker.exists())
        self.assertEqual(before, (self.project / 'main.py').read_bytes())
        self.assertTrue(all(e.command.arguments == ('--version',) or e.status == 'requires_confirmation'
                            for e in result.execution_results))
        self.assertNotIn('executed.txt', json.dumps(result.report))

    def test_stdin_traceback(self):
        with patch('sys.stdin', io.StringIO("ModuleNotFoundError: No module named 'stdin_lib'")):
            result = run_workflow(self.project, traceback_file='-')
        item = self.input_evidence(result)
        self.assertEqual(item.source, 'stdin')
        self.assertIsNone(item.location)
        self.assertEqual(result.report['assessment']['outcome'], 'issues_detected')

    def test_stdin_install_log(self):
        with patch('sys.stdin', io.StringIO('ERROR: No matching distribution found for demo')):
            result = run_workflow(self.project, install_log='-')
        self.assertEqual(self.input_evidence(result).metadata['status'], 'facts_only')

    def test_binary_stdin_with_utf8_bom(self):
        stream = io.TextIOWrapper(io.BytesIO(b"\xef\xbb\xbfModuleNotFoundError: No module named 'binary_lib'"), encoding='utf-8')
        with stream, patch('sys.stdin', stream):
            result = run_workflow(self.project, traceback_file='-')
        self.assertIn('binary_lib', result.terminal_report)

    def test_oversized_stdin(self):
        with patch('sys.stdin', io.StringIO('x' * (MAX_INPUT_BYTES + 1))):
            self.assert_ingestion_failure(run_workflow(self.project, traceback_file='-'), 'oversized_input')

    def test_exact_limit_accepted(self):
        self.log.write_bytes(b'x' * MAX_INPUT_BYTES)
        self.assertEqual(read_log(self.log, 'traceback').metadata['size_bytes'], MAX_INPUT_BYTES)

    def test_multibyte_limit_is_bytes(self):
        with patch('sys.stdin', io.StringIO('中' * (MAX_INPUT_BYTES // 3 + 1))):
            self.assert_ingestion_failure(run_workflow(self.project, traceback_file='-'), 'oversized_input')

    def test_cli_json_agree_and_keep_schema(self):
        self.log.write_text("ModuleNotFoundError: No module named 'cli_lib'")
        output = self.base / 'report.json'
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            code = main([str(self.project), '--traceback-file', str(self.log), '--output', str(output)])
        report = json.loads(output.read_text())
        self.assertEqual(code, 1)
        self.assertEqual(report['schema_version'], '0.2')
        self.assertEqual(report['status'], 'issues_detected')
        self.assertEqual(report['assessment']['outcome'], 'issues_detected')
        self.assertIn('Issues detected', stdout.getvalue())
        self.assertIn('cli_lib', stdout.getvalue())

    def test_cli_missing_file_exit_zero(self):
        with redirect_stderr(io.StringIO()), redirect_stdout(io.StringIO()):
            self.assertEqual(main([str(self.project), '--install-log', str(self.log)]), 0)

    def test_install_cli_json_agree_on_inconclusive(self):
        self.log.write_text('ERROR: No matching distribution found for demo')
        output = self.base / 'install.json'
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            code = main([str(self.project), '--install-log', str(self.log), '--output', str(output)])
        report = json.loads(output.read_text())
        self.assertEqual(code, 0)
        self.assertEqual(report['assessment']['outcome'], 'inconclusive')
        self.assertIn('Inconclusive', stdout.getvalue())

    def test_supplied_and_startup_import_failures_have_distinct_refs(self):
        (self.project / 'main.py').write_text('import definitely_missing_startup_module')
        result = self.run_log("ModuleNotFoundError: No module named 'reported_module'", confirm_startup=True)
        imports = [d for d in result.diagnostics if d.category == 'python_import']
        self.assertEqual(len(imports), 2)
        self.assertEqual(len({d.diagnosis_id for d in imports}), 2)
        self.assertEqual(len({d.evidence_refs for d in imports}), 2)
        self.assertEqual(result.report['assessment']['outcome'], 'issues_detected')

    def test_cli_inputs_mutually_exclusive(self):
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error:
            main([str(self.project), '--traceback-file', '-', '--install-log', '-'])
        self.assertEqual(error.exception.code, 2)

    def test_workflow_inputs_mutually_exclusive(self):
        with self.assertRaisesRegex(WorkflowError, 'Choose either'):
            run_workflow(self.project, traceback_file='-', install_log='-')

    def test_install_facts_remain_inconclusive_after_successful_startup(self):
        (self.project / 'main.py').write_text('pass')
        result = self.run_log('ERROR: No matching distribution found for demo', install=True, confirm_startup=True)
        self.assertEqual(result.report['assessment']['startup_probe']['status'], 'success')
        self.assertEqual(result.report['assessment']['outcome'], 'inconclusive')
        self.assertTrue(any(l['check'] == 'provided_log' and l['reason'] == 'facts_only'
                            for l in result.report['assessment']['limitations']))

    def test_install_ansi_is_visible_not_active(self):
        result = self.run_log('ERROR: No matching distribution found for demo\x1b[2J', install=True)
        self.assertNotIn('\x1b', result.terminal_report)
        self.assertIn(r'\x1b[2J', result.terminal_report)
        self.assertEqual(result.report['assessment']['outcome'], 'inconclusive')
        self.assertIn('\x1b[2J', self.input_evidence(result).metadata['messages'][0])

    def test_traceback_controls_in_all_derived_terminal_text(self):
        result = self.run_log("ImportError: cannot import name 'symbol\x1b[2J\x9b2J\x00' from 'example'")
        self.assertNotIn('\x1b', result.terminal_report)
        self.assertNotIn('\x9b', result.terminal_report)
        self.assertNotIn('\x00', result.terminal_report)
        self.assertIn(r'symbol\x1b[2J\x9b2J\x00', result.terminal_report)
        self.assertEqual(result.report['assessment']['outcome'], 'issues_detected')
        self.assertEqual(result.diagnostics[0].source, 'rule:python_symbol_import_failure')

    def test_all_c0_c1_controls_are_neutralized_except_newline(self):
        controls = ''.join(chr(n) for n in (*range(32), *range(127, 160)))
        displayed = safe_terminal_text(controls)
        self.assertEqual([c for c in displayed if ord(c) < 32 or 127 <= ord(c) <= 159], ['\n'])
        self.assertEqual(safe_terminal_text('normal text\nnext line'), 'normal text\nnext line')

    def test_permission_error_is_limitation(self):
        self.log.write_text('unreadable')
        with patch('agent_doctor.log_input.Path.open', side_effect=PermissionError('denied')):
            result = run_workflow(self.project, install_log=self.log)
        self.assert_ingestion_failure(result, 'permission_denied')

    def test_ingestion_failure_cli_json_agreement(self):
        output = self.base / 'limited.json'
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = main([str(self.project), '--install-log', str(self.log), '--output', str(output)])
        report = json.loads(output.read_text())
        self.assertEqual(code, 0)
        self.assertEqual(stderr.getvalue(), '')
        self.assertEqual(report['assessment']['outcome'], 'inconclusive')
        self.assertEqual(report['assessment']['coverage'], 'incomplete')
        limitation = next(l for l in report['assessment']['limitations'] if l['check'] == 'log_ingestion')
        self.assertEqual(limitation['requested_path'], str(self.log.absolute()))
        self.assertIn('Inconclusive', stdout.getvalue())
        self.assertIn('missing_file', stdout.getvalue())

    def test_json_controls_are_escaped_without_changing_facts(self):
        text = 'ERROR: No matching distribution found for demo\x1b[2J\x9b2J'
        self.log.write_text(text, encoding='utf-8')
        output = self.base / 'safe.json'
        result = run_workflow(self.project, output, install_log=self.log)
        serialized = output.read_text(encoding='utf-8')
        self.assertNotIn('\x1b', serialized)
        self.assertNotIn('\x9b', serialized)
        self.assertIn(r'\u001b', serialized)
        self.assertIn(r'\u009b', serialized)
        self.assertEqual(json.loads(serialized), result.report)

    def test_empty_and_unusable_are_only_limitations(self):
        for text, reason in (('', 'empty'), ('not a supported traceback', 'unrecognized')):
            with self.subTest(reason=reason):
                self.assert_ingestion_failure(self.run_log(text), reason)

    def test_missing_log_does_not_hide_existing_startup_error(self):
        result = run_workflow(self.project, traceback_file=self.log, confirm_startup=True)
        self.assertEqual(result.report['assessment']['outcome'], 'issues_detected')
        self.assertEqual(result.report['assessment']['coverage'], 'incomplete')
        self.assertTrue(any(l['check'] == 'log_ingestion' for l in result.report['assessment']['limitations']))
        self.assertTrue(any(d.category == 'startup' and d.severity == 'ERROR' for d in result.diagnostics))

    def test_oversized_binary_stdin_read_is_bounded(self):
        stream = io.BytesIO(b'x' * (MAX_INPUT_BYTES * 2))
        stdin = io.TextIOWrapper(stream, encoding='utf-8')
        with patch('sys.stdin', stdin):
            item = read_log('-', 'traceback')
        self.assertEqual(stream.tell(), MAX_INPUT_BYTES + 1)
        self.assertEqual(item.kind, 'ingestion_limitation')
        self.assertEqual(item.metadata['status'], 'oversized_input')
        stdin.close()

    def test_invalid_project_remains_exit_two_with_ingestion_failure(self):
        with redirect_stderr(io.StringIO()), redirect_stdout(io.StringIO()):
            code = main([str(self.base / 'missing-project'), '--install-log', str(self.log)])
        self.assertEqual(code, 2)
