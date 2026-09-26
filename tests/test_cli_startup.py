"""CLI opt-in acceptance tests, including the real audited demo and policy boundary."""
import io
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stdout, redirect_stderr
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from agent_doctor.cli import main
from agent_doctor.models import DiagnosisResult
from agent_doctor.project import scan_project, inspect_environment
from agent_doctor.python_plugin import detect_python_project
from agent_doctor.report import render_terminal_report
from agent_doctor.startup import propose_startup_probe
from agent_doctor.workflow import run_workflow
from tests.test_failure_fixtures import FIXTURES, MISSING_MODULE, audit_sources, snapshot


def invoke(*args):
    stdout, stderr = io.StringIO(), io.StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        code = main([str(arg) for arg in args])
    return code, stdout.getvalue(), stderr.getvalue()


class StartupCLITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        audit_sources()
        cls.before = snapshot()
        cls.environment = dict(os.environ)
        start = time.monotonic()
        results = []

        def record(*args, **kwargs):
            result = run_workflow(*args, **kwargs)
            results.append(result)
            return result

        with patch('agent_doctor.cli.run_workflow', side_effect=record):
            cls.demo = invoke(FIXTURES / 'missing-module', '--run-startup-probe')
        cls.duration = time.monotonic() - start
        cls.result = results[0]
        cls.after = snapshot()

    def test_flag_help_and_side_effect_description(self):
        output = io.StringIO()
        with redirect_stdout(output), self.assertRaises(SystemExit) as exit:
            main(['--help'])
        self.assertEqual(exit.exception.code, 0)
        help = ' '.join(output.getvalue().split())
        self.assertIn('--run-startup-probe', help)
        self.assertIn('Execute the supported project startup probe.', help)
        self.assertIn('This runs project code and may have project-defined side effects.', help)

    def test_default_never_runs_startup(self):
        with patch('agent_doctor.commands._run_startup_process') as runner:
            result = run_workflow(FIXTURES / 'missing-module')
            code, output, error = invoke(FIXTURES / 'missing-module')
        runner.assert_not_called()
        self.assertEqual(code, 0)
        self.assertEqual(error, '')
        self.assertIn('Use --run-startup-probe', output)
        self.assertNotIn('Executing project code', output)
        self.assertEqual(result.execution_results[-1].status, 'requires_confirmation')

    def test_flag_maps_to_explicit_confirmation(self):
        result = run_workflow(FIXTURES / 'missing-module')
        with patch('agent_doctor.cli.run_workflow', return_value=result) as workflow:
            invoke(FIXTURES / 'missing-module', '--run-startup-probe')
        workflow.assert_called_once_with(str(FIXTURES / 'missing-module'), None, confirm_startup=True)

    def test_cli_default_passes_false_confirmation(self):
        result = run_workflow(FIXTURES / 'missing-module')
        with patch('agent_doctor.cli.run_workflow', return_value=result) as workflow:
            invoke(FIXTURES / 'missing-module')
        self.assertIs(workflow.call_args.kwargs['confirm_startup'], False)

    def test_notice_is_flushed_before_startup_and_no_prompt(self):
        stdout = io.StringIO()
        def runner(*args, **kwargs):
            self.assertIn('[CAUTION] Executing project code for the startup probe.', stdout.getvalue())
            self.assertIn('Project code may have its own side effects.', stdout.getvalue())
            return subprocess.CompletedProcess(args[0], 0, '', '')
        with redirect_stdout(stdout), patch('builtins.input', side_effect=AssertionError('no prompt')), patch('agent_doctor.commands._run_startup_process', side_effect=runner) as run:
            self.assertEqual(main([str(FIXTURES / 'missing-module'), '--run-startup-probe']), 0)
        run.assert_called_once()

    def test_flag_preserves_exact_command_and_environment(self):
        before = dict(os.environ)
        with patch('agent_doctor.commands._run_startup_process', return_value=subprocess.CompletedProcess([], 0, '', '')) as run:
            invoke(FIXTURES / 'missing-module', '--run-startup-probe')
        args, kwargs = run.call_args
        self.assertEqual(args[0], [str(Path(sys.executable).resolve()), str(FIXTURES / 'missing-module' / 'main.py')])
        self.assertEqual(kwargs['cwd'], FIXTURES / 'missing-module')
        self.assertEqual(kwargs['timeout'], 5)
        self.assertEqual(kwargs['env'], before)
        self.assertIsNot(kwargs['env'], os.environ)
        self.assertEqual(dict(os.environ), before)

    def test_confirmation_does_not_expand_allowlist(self):
        root = FIXTURES / 'missing-module'
        project = scan_project(root)
        command, evidence = propose_startup_probe(project, inspect_environment(project))
        mutations = (
            replace(command, executable='arbitrary-executable'),
            replace(command, executable='python'),
            replace(command, arguments=command.arguments + ('--extra',)),
            replace(command, arguments=('main.py',)),
            replace(command, arguments=(str(root / 'app.py'),)),
            replace(command, arguments=(str(root / 'main.py') + '; rm -rf anything',)),
            replace(command, working_directory=root.parent),
            replace(command, risk='SAFE'),
        )
        for mutated in mutations:
            with self.subTest(command=mutated), patch('agent_doctor.workflow.propose_startup_probe', return_value=(mutated, evidence)), patch('agent_doctor.commands._run_startup_process') as runner:
                results = []
                def record(*args, **kwargs):
                    result = run_workflow(*args, **kwargs)
                    results.append(result)
                    return result
                with patch('agent_doctor.cli.run_workflow', side_effect=record):
                    invoke(root, '--run-startup-probe')
                runner.assert_not_called()
                self.assertEqual(results[0].execution_results[-1].status, 'rejected')

    def test_arbitrary_cli_arguments_are_usage_errors(self):
        for extra in (['rm -rf anything'], ['--command', 'python'], ['--env', 'X=1'], ['--run-startup-probe=python'], ['--', 'python', 'main.py']):
            with self.subTest(extra=extra), redirect_stderr(io.StringIO()), patch('agent_doctor.cli.run_workflow') as workflow, self.assertRaises(SystemExit) as exit:
                main([str(FIXTURES / 'missing-module'), '--run-startup-probe', *extra])
            self.assertEqual(exit.exception.code, 2)
            workflow.assert_not_called()

    def test_missing_module_demo_finishes_quickly(self):
        self.assertLess(self.duration, 5)
        self.assertEqual(self.demo[0], 1)
        self.assertEqual(self.demo[2], '')

    def test_missing_module_has_specific_independent_diagnosis(self):
        diagnoses = [item for item in self.result.diagnostics if item.category == 'python_import']
        self.assertEqual(len(diagnoses), 1)
        self.assertEqual(diagnoses[0].severity, 'ERROR')
        self.assertIn(MISSING_MODULE, diagnoses[0].problem)

    def test_missing_module_has_startup_nonzero(self):
        self.assertEqual(self.result.execution_results[-1].exit_code, 1)
        self.assertEqual(self.result.execution_results[-1].status, 'failed')
        self.assertEqual({item.category for item in self.result.diagnostics}, {'python_import', 'startup'})
        self.assertIn('[ERROR] Startup probe exited with code 1.', self.demo[1])

    def test_terminal_sections_order_and_no_machine_fields(self):
        output = self.demo[1]
        headings = ('Project:', 'Findings:', 'Root cause:', 'Repair preview:', 'Verification (planned, not run):')
        positions = [output.index(heading) for heading in headings]
        self.assertEqual(positions, sorted(positions))
        for field in ('python_import:', 'execution_status', 'stdout_excerpt', 'Command argv:', 'Evidence: execution:'):
            self.assertNotIn(field, output)
        self.assertIn('No repair actions were executed.', output)

    def test_terminal_does_not_repeat_traceback_or_exception_line(self):
        raw = next(item.metadata['raw_message'] for item in self.result.evidence if item.kind == 'python_import_failure')
        self.assertEqual(self.demo[1].count(raw), 1)
        self.assertNotIn('Traceback (most recent call last)', self.demo[1])
        self.assertIn('Traceback (most recent call last)', self.result.execution_results[-1].stderr)

    def test_terminal_errors_precede_info_without_mutating_diagnoses(self):
        project = self.result.project
        diagnoses = [DiagnosisResult('Info', 'tool/safety', 'INFO', 1, 'test'), DiagnosisResult('Error', 'environment', 'ERROR', 1, 'test')]
        before = list(diagnoses)
        output = render_terminal_report(project, self.result.detection, self.result.environment, diagnoses)
        self.assertLess(output.index('[ERROR]'), output.index('[INFO]'))
        self.assertEqual(diagnoses, before)

    def test_timeout_wording_is_conservative(self):
        error = subprocess.TimeoutExpired([], 5)
        error.terminated = True
        with patch('agent_doctor.commands._run_startup_process', side_effect=error):
            code, output, stderr = invoke(FIXTURES / 'startup-timeout', '--run-startup-probe')
        self.assertEqual(code, 0)
        self.assertEqual(stderr, '')
        self.assertIn('[INFO] Startup probe exceeded the 5 second observation window.', output)
        self.assertIn('This may be normal for a long-running application.', output)
        for claim in ('[ERROR]', 'hang', 'deadlock', 'failed to start', 'healthy', 'completed successfully'):
            self.assertNotIn(claim, output)

    def test_success_only_claims_probe_completion(self):
        with patch('agent_doctor.commands._run_startup_process', return_value=subprocess.CompletedProcess([], 0, '', '')):
            code, output, _ = invoke(FIXTURES / 'missing-module', '--run-startup-probe')
        self.assertEqual(code, 0)
        self.assertIn('Startup probe completed successfully.', output)
        for claim in ('healthy', 'Everything works', 'No problems', 'READ ONLY:'):
            self.assertNotIn(claim, output)

    def test_no_root_main_is_capability_limitation(self):
        with tempfile.TemporaryDirectory() as temp:
            Path(temp, 'app.py').write_text("raise RuntimeError('must not execute')")
            with patch('agent_doctor.commands._run_startup_process') as runner:
                code, output, stderr = invoke(temp, '--run-startup-probe')
        runner.assert_not_called()
        self.assertEqual(code, 0)
        self.assertEqual(stderr, '')
        self.assertIn('No supported startup entrypoint was detected.', output)
        self.assertIn('Current alpha supports only root-level main.py.', output)

    def test_demo_file_list_contents_and_mtimes_unchanged(self):
        self.assertEqual(self.before, self.after)
        self.assertEqual(snapshot(), self.before)
        self.assertFalse(any(path.name == '__pycache__' for path in FIXTURES.rglob('*')))

    def test_demo_does_not_change_environment(self):
        self.assertEqual(dict(os.environ), self.environment)

    def test_json_schema_and_no_cli_protocol_fields(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp, 'report.json')
            code, _, error = invoke(FIXTURES / 'missing-module', '--run-startup-probe', '--output', path)
            payload = json.loads(path.read_text(encoding='utf-8'))
        self.assertEqual(code, 1)
        self.assertEqual(error, '')
        self.assertEqual(payload['schema_version'], '0.2')
        self.assertNotIn('cli_confirmed', json.dumps(payload))
        self.assertEqual({item['category'] for item in payload['diagnostics']}, {'python_import', 'startup'})
        self.assertEqual(set(payload), set(self.result.report))

    def test_real_source_cli_help(self):
        source = str(Path(__file__).resolve().parents[1] / 'src')
        launcher = 'import sys; sys.path.insert(0, sys.argv[1]); from agent_doctor.cli import main; sys.exit(main(sys.argv[2:]))'
        result = subprocess.run([sys.executable, '-B', '-c', launcher, source, '--help'], capture_output=True, text=True, timeout=10, shell=False)
        self.assertEqual(result.returncode, 0)
        self.assertIn('--run-startup-probe', result.stdout)
        self.assertEqual(result.stderr, '')
