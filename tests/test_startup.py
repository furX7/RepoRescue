"""Startup fixtures exercise default refusal, exact argv, and bounded outcomes."""

import json
import io
import os
import subprocess
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import MagicMock, patch

from agent_doctor.commands import execute_command, execute_startup_probe
from agent_doctor.diagnosis import diagnose
from agent_doctor.project import inspect_environment, scan_project
from agent_doctor.python_plugin import detect_python_project
from agent_doctor.report import build_json_report, render_terminal_report
from agent_doctor.startup import collect_startup_evidence, propose_startup_probe
from agent_doctor.workflow import run_workflow


class StartupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.entry = self.root / 'main.py'

    def proposal(self, content="print('fixture')\n"):
        self.entry.write_text(content, encoding='utf-8')
        project = scan_project(self.root)
        environment = inspect_environment(project)
        command, inspection = propose_startup_probe(project, environment)
        return command, inspection, project, environment

    def outcome(self, code=1, stdout='', stderr=''):
        command, inspection, project, environment = self.proposal()
        with patch('agent_doctor.commands._run_startup_process', return_value=
                   subprocess.CompletedProcess([], code, stdout, stderr)):
            execution = execute_startup_probe(command, self.root, confirmed=True)
        evidence = collect_startup_evidence(inspection, execution)
        diagnoses, collected = diagnose(project, detect_python_project(project), environment,
                                       (execution,), startup_probe=evidence)
        return execution, diagnoses, collected, project, environment

    def test_root_main_detected_caution_and_exact_argv(self):
        command, evidence, _, _ = self.proposal()
        self.assertEqual(command.risk, 'CAUTION')
        self.assertEqual(command.executable, sys.executable)
        self.assertEqual(command.arguments, (str(self.entry),))
        self.assertEqual(evidence.metadata['execution_status'], 'requires_confirmation')

    def test_no_supported_entrypoint_is_not_project_error(self):
        project = scan_project(self.root)
        environment = inspect_environment(project)
        command, evidence = propose_startup_probe(project, environment)
        self.assertIsNone(command)
        self.assertEqual(evidence.metadata['execution_status'], 'no_supported_entrypoint')
        diagnoses, _ = diagnose(project, detect_python_project(project), environment,
                                startup_probe=evidence)
        self.assertFalse(any(item.category == 'startup' for item in diagnoses))

    def test_other_entry_names_are_not_supported(self):
        for name in ('app.py', 'run.py', 'server.py', '__main__.py'):
            (self.root / name).touch()
        project = scan_project(self.root)
        command, _ = propose_startup_probe(project, inspect_environment(project))
        self.assertIsNone(command)

    def test_main_directory_is_unavailable(self):
        self.entry.mkdir()
        project = scan_project(self.root)
        command, evidence = propose_startup_probe(project, inspect_environment(project))
        self.assertIsNone(command)
        self.assertEqual(evidence.metadata['execution_status'], 'unavailable')

    def test_resolved_entrypoint_escape_is_rejected(self):
        command, _, _, _ = self.proposal()
        original = Path.resolve
        def resolve(path, *args, **kwargs):
            return self.root.parent / 'outside.py' if path == self.entry else original(path, *args, **kwargs)
        with patch.object(Path, 'resolve', resolve), patch('agent_doctor.commands._run_startup_process') as run:
            result = execute_startup_probe(command, self.root, confirmed=True)
        self.assertEqual(result.status, 'rejected')
        run.assert_not_called()

    def test_symlink_outside_project_is_unavailable(self):
        with tempfile.TemporaryDirectory() as outside:
            target = Path(outside) / 'outside.py'
            target.touch()
            try:
                self.entry.symlink_to(target)
            except OSError as error:
                self.skipTest(f'Symlink creation unavailable: {error}')
            project = scan_project(self.root)
            command, evidence = propose_startup_probe(project, inspect_environment(project))
            self.assertIsNone(command)
            self.assertEqual(evidence.metadata['execution_status'], 'unavailable')

    def test_unreadable_entrypoint_never_runs(self):
        command, _, project, environment = self.proposal()
        with patch.object(Path, 'open', side_effect=PermissionError('denied')):
            proposal, evidence = propose_startup_probe(project, environment)
            self.assertIsNone(proposal)
            self.assertEqual(evidence.metadata['execution_status'], 'unavailable')
            with patch('agent_doctor.commands._run_startup_process') as run:
                result = execute_startup_probe(command, self.root, confirmed=True)
        self.assertEqual(result.status, 'rejected')
        run.assert_not_called()

    def test_default_confirmation_does_not_execute(self):
        command, _, _, _ = self.proposal()
        with patch('agent_doctor.commands._run_startup_process') as run:
            result = execute_startup_probe(command, self.root)
        run.assert_not_called()
        self.assertEqual(result.status, 'requires_confirmation')

    def test_truthy_value_is_not_explicit_confirmation(self):
        command, _, _, _ = self.proposal()
        with patch('agent_doctor.commands._run_startup_process') as run:
            result = execute_startup_probe(command, self.root, confirmed='yes')
        run.assert_not_called()
        self.assertEqual(result.status, 'requires_confirmation')

    def test_confirmed_invocation_contract_and_environment_copy(self):
        command, _, _, _ = self.proposal()
        before = dict(os.environ)
        with patch('agent_doctor.commands._run_startup_process', return_value=
                   subprocess.CompletedProcess([], 0, 'out', 'err')) as run:
            result = execute_startup_probe(command, self.root, confirmed=True)
        args, kwargs = run.call_args
        self.assertEqual(args[0], [str(Path(sys.executable).resolve()), str(self.entry)])
        self.assertEqual(kwargs['cwd'], self.root)
        self.assertEqual(kwargs['timeout'], 5.0)
        self.assertEqual(kwargs['env'], before)
        self.assertIsNot(kwargs['env'], os.environ)
        self.assertEqual(result.stdout, 'out')
        self.assertEqual(result.stderr, 'err')
        self.assertEqual(dict(os.environ), before)

    def test_exit_zero_is_positive_evidence_not_diagnosis(self):
        execution, diagnoses, evidence, _, _ = self.outcome(code=0)
        self.assertEqual(execution.status, 'success')
        self.assertFalse(any(item.category == 'startup' for item in diagnoses))
        self.assertEqual(evidence[-1].metadata['execution_status'], 'success')

    def test_nonzero_exit_evidence_diagnosis_and_preview(self):
        execution, diagnoses, evidence, _, _ = self.outcome(code=7, stderr='failure')
        item = next(item for item in diagnoses if item.category == 'startup')
        self.assertEqual(execution.exit_code, 7)
        self.assertEqual(item.severity, 'ERROR')
        self.assertIn('non-zero', item.problem)
        self.assertEqual(item.repair_plan.risk, 'LOW')
        self.assertTrue(item.repair_plan.verification_steps)
        ids = {item.evidence_id for item in evidence}
        for step in item.root_cause_chain:
            self.assertTrue(set(step.evidence_refs) <= ids)
        self.assertEqual(evidence[-1].metadata['exit_code'], 7)

    def test_timeout_is_observation_not_startup_failure(self):
        command, inspection, project, environment = self.proposal()
        timeout_error = subprocess.TimeoutExpired([], 5, output=b'partial', stderr=b'err')
        timeout_error.terminated = True
        with patch('agent_doctor.commands._run_startup_process', side_effect=timeout_error):
            result = execute_startup_probe(command, self.root, confirmed=True)
        self.assertEqual(result.status, 'timeout')
        self.assertTrue(result.terminated)
        self.assertEqual(result.stdout, 'partial')
        evidence = collect_startup_evidence(inspection, result)
        diagnoses, _ = diagnose(project, detect_python_project(project), environment,
                                (result,), startup_probe=evidence)
        item = next(item for item in diagnoses if item.category == 'startup')
        self.assertEqual(item.severity, 'INFO')
        self.assertIn('observation window', item.problem)
        self.assertNotIn('failed to start', repr(item).lower())
        self.assertIsNone(item.repair_plan)

    def test_size_limited_excerpts(self):
        result, _, evidence, _, _ = self.outcome(stdout='x' * 5000, stderr='y' * 5000)
        self.assertTrue(result.stdout_truncated)
        self.assertLessEqual(len(evidence[-1].metadata['stdout_excerpt']), 4096)
        self.assertLessEqual(len(evidence[-1].metadata['stderr_excerpt']), 4096)

    def test_timeout_kills_and_waits_direct_child(self):
        command, _, _, _ = self.proposal()
        process = MagicMock()
        process.stdout = io.BytesIO(b'partial')
        process.stderr = io.BytesIO(b'error')
        process.wait.side_effect = [subprocess.TimeoutExpired([], 0.1), None]
        process.poll.return_value = -9
        with patch('subprocess.Popen', return_value=process) as popen:
            result = execute_startup_probe(command, self.root, confirmed=True, timeout=0.1)
        process.kill.assert_called_once()
        self.assertEqual(process.wait.call_count, 2)
        self.assertEqual(process.wait.call_args.kwargs['timeout'], 1.0)
        self.assertIs(popen.call_args.kwargs['shell'], False)
        self.assertEqual(popen.call_args.kwargs['stdin'], subprocess.DEVNULL)
        self.assertTrue(result.terminated)
        self.assertEqual(result.stdout, 'partial')

    def test_real_confirmed_benign_fixture_preserves_project_and_environment(self):
        command, _, _, _ = self.proposal("print('probe complete')\n")
        before = (self.entry.read_bytes(), self.entry.stat().st_mtime_ns, list(self.root.iterdir()))
        environment = dict(os.environ)
        result = execute_startup_probe(command, self.root, confirmed=True)
        self.assertEqual(result.status, 'success')
        self.assertEqual(result.stdout.strip(), 'probe complete')
        self.assertEqual((self.entry.read_bytes(), self.entry.stat().st_mtime_ns,
                          list(self.root.iterdir())), before)
        self.assertEqual(dict(os.environ), environment)

    def test_real_sleep_fixture_timeout(self):
        command, _, _, _ = self.proposal('import time\nprint("started", flush=True)\ntime.sleep(30)\n')
        result = execute_startup_probe(command, self.root, confirmed=True, timeout=0.5)
        self.assertEqual(result.status, 'timeout')
        self.assertTrue(result.terminated)
        self.assertIn('started', result.stdout)

    def test_launch_oserror_has_no_false_process_exit(self):
        command, inspection, _, _ = self.proposal()
        with patch('agent_doctor.commands._run_startup_process', side_effect=PermissionError('denied')):
            result = execute_startup_probe(command, self.root, confirmed=True)
        evidence = collect_startup_evidence(inspection, result)
        self.assertEqual(result.status, 'failed')
        self.assertIsNone(result.exit_code)
        self.assertFalse(evidence.metadata['executed'])

    def test_all_supported_diagnoses_remain_independent(self):
        self.proposal()
        (self.root / 'pyproject.toml').write_text('[project]\nrequires-python = ">=99.0"\n', encoding='utf-8')
        interpreter = self.root / '.venv' / 'Scripts' / 'python.exe'
        interpreter.parent.mkdir(parents=True)
        interpreter.touch()
        with patch('agent_doctor.commands.subprocess.run', return_value=subprocess.CompletedProcess([], 0, 'Python', '')), patch(
            'agent_doctor.commands._run_startup_process', return_value=
            subprocess.CompletedProcess([], 1, '', "ModuleNotFoundError: No module named 'requests'")):
            result = run_workflow(self.root, confirm_startup=True)
        self.assertEqual({item.category for item in result.diagnostics},
                         {'python_environment', 'python_version', 'python_import', 'startup'})

    def test_confirmation_and_missing_entry_have_no_startup_diagnosis(self):
        self.proposal()
        result = run_workflow(self.root)
        self.assertFalse(any(item.category == 'startup' for item in result.diagnostics))
        self.entry.unlink()
        result = run_workflow(self.root)
        self.assertIn('No supported startup entrypoint', result.terminal_report)
        self.assertFalse(any(item.category == 'startup' for item in result.diagnostics))

    def test_no_runtime_dependencies_added(self):
        import tomllib
        config = Path(__file__).resolve().parents[1] / 'pyproject.toml'
        self.assertEqual(tomllib.loads(config.read_text(encoding='utf-8'))['project']['dependencies'], [])

    def test_import_rules_are_reused_independently(self):
        for message in ("ModuleNotFoundError: No module named 'requests'",
                        "ImportError: cannot import name 'foo' from 'bar'"):
            with self.subTest(message=message):
                _, diagnoses, _, _, _ = self.outcome(stderr=message)
                self.assertEqual({item.category for item in diagnoses}, {'startup', 'python_import'})
                startup = next(item for item in diagnoses if item.category == 'startup')
                self.assertNotIn('dependency', repr(startup.root_cause_chain).lower())

    def test_arbitrary_argv_and_executables_are_rejected(self):
        command, _, _, _ = self.proposal()
        variants = [replace(command, arguments=args) for args in (
            ('main.py',), (str(self.entry), '--extra'), ('-c', 'print(1)'),
            ('-m', 'pip', 'install', 'requests'), (str(self.entry) + ' && del x',))]
        variants += [replace(command, executable=name) for name in ('python', 'cmd', 'powershell')]
        variants += [replace(command, risk=risk) for risk in ('SAFE', 'DANGEROUS')]
        variants += [replace(command, working_directory=self.root.parent)]
        with patch('agent_doctor.commands._run_startup_process') as run:
            for variant in variants:
                with self.subTest(variant=variant):
                    self.assertEqual(execute_startup_probe(variant, self.root, confirmed=True).status, 'rejected')
        run.assert_not_called()

    def test_existing_executor_does_not_allow_startup(self):
        command, _, _, _ = self.proposal()
        self.assertEqual(execute_command(command, self.root).status, 'rejected')

    def test_timeout_limits_rejected(self):
        command, _, _, _ = self.proposal()
        with patch('agent_doctor.commands._run_startup_process') as run:
            for timeout in (0, -1, 6, float('nan'), float('inf')):
                self.assertEqual(execute_startup_probe(command, self.root, confirmed=True,
                                                      timeout=timeout).status, 'rejected')
        run.assert_not_called()

    def test_default_workflow_does_not_run_project_or_change_files(self):
        self.proposal("raise RuntimeError('must not run')\n")
        before = (self.entry.read_bytes(), self.entry.stat().st_mtime_ns, list(self.root.iterdir()))
        result = run_workflow(self.root)
        self.assertEqual(result.execution_results[-1].status, 'requires_confirmation')
        self.assertEqual((self.entry.read_bytes(), self.entry.stat().st_mtime_ns,
                          list(self.root.iterdir())), before)
        self.assertIn('No startup probe was executed', result.terminal_report)
        self.assertFalse(any(item.category == 'startup' for item in result.diagnostics))

    def test_confirmed_fixture_workflow_and_launch_state(self):
        self.proposal()
        with patch('agent_doctor.commands.subprocess.run', return_value=subprocess.CompletedProcess([], 0, 'Python', '')), patch(
            'agent_doctor.commands._run_startup_process', return_value=subprocess.CompletedProcess([], 2, '', 'startup error')):
            result = run_workflow(self.root, confirm_startup=True)
        self.assertTrue(result.environment.python_callable)
        self.assertEqual(result.execution_results[-1].status, 'failed')
        self.assertIn('project-defined side effects', result.terminal_report)

    def test_json_contract_and_terminal(self):
        _, diagnoses, evidence, project, environment = self.outcome(stderr='failure')
        report = build_json_report(project, detect_python_project(project), environment,
                                   diagnoses, evidence)
        payload = json.loads(json.dumps(report))
        self.assertEqual(payload['schema_version'], '0.2')
        startup = next(item for item in payload['evidence'] if item['kind'] == 'startup_probe')
        self.assertEqual(startup['metadata']['argv'], [sys.executable, str(self.entry)])
        terminal = render_terminal_report(project, detect_python_project(project), environment,
                                          diagnoses, evidence=evidence)
        self.assertIn('Startup probe exited with code 1', terminal)
        self.assertIn('project-defined side effects', terminal)


    def test_descendant_pipe_cleanup_is_bounded(self):
        command, _, _, _ = self.proposal()
        process = MagicMock()
        process.stdout = io.BytesIO(b'')
        process.stderr = io.BytesIO(b'')
        process.poll.return_value = -9
        reader = MagicMock()
        reader.is_alive.return_value = True
        with patch('subprocess.Popen', return_value=process), patch('agent_doctor.commands.Thread', return_value=reader):
            result = execute_startup_probe(command, self.root, confirmed=True, timeout=0.1)
        self.assertTrue(all(call.args[0] <= 1.0 for call in reader.join.call_args_list))
        self.assertTrue(result.stdout_truncated)
        self.assertTrue(result.stderr_truncated)
        self.assertTrue(result.terminated)
        self.assertFalse(process.stdout.closed)

    def test_termination_failure_is_not_reported_as_terminated(self):
        command, _, _, _ = self.proposal()
        process = MagicMock()
        process.stdout = io.BytesIO(b'')
        process.stderr = io.BytesIO(b'')
        process.wait.side_effect = subprocess.TimeoutExpired([], 0.1)
        process.kill.side_effect = PermissionError('denied')
        process.poll.return_value = None
        with patch('subprocess.Popen', return_value=process):
            result = execute_startup_probe(command, self.root, confirmed=True, timeout=0.1)
        self.assertEqual(result.status, 'timeout')
        self.assertFalse(result.terminated)
        self.assertIn('could not be confirmed', result.message)


if __name__ == '__main__':
    unittest.main()
