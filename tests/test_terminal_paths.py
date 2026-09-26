"""Path privacy belongs to terminal presentation, never execution or machine data."""
import copy
import io
import json
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from dataclasses import replace
from pathlib import Path, PurePosixPath, PureWindowsPath
from unittest.mock import patch

from agent_doctor.cli import main
from agent_doctor.models import Evidence, RootCauseStep, DiagnosisResult
from agent_doctor.report import _display_path, render_terminal_report
from agent_doctor.workflow import run_workflow
from tests.test_failure_fixtures import FIXTURES


class TerminalPathTests(unittest.TestCase):
    def test_windows_project_local_interpreter(self):
        self.assertEqual(_display_path(r'D:\work\project\.venv\Scripts\python.exe', project_root=r'D:\work\project', cwd=r'D:\work'), r'.venv\Scripts\python.exe')

    def test_windows_workspace_interpreter(self):
        self.assertEqual(_display_path(r'D:\agentDoctor\.venv\Scripts\python.exe', project_root=r'D:\agentDoctor\examples\fixtures\missing-module', cwd=r'D:\agentDoctor'), r'.venv\Scripts\python.exe')

    def test_windows_case_and_mixed_separators(self):
        self.assertEqual(_display_path('d:/WORK/Project/.venv\\Scripts/python.exe', project_root=r'D:\work\project', cwd=r'D:\WORK'), r'.venv\Scripts\python.exe')

    def test_external_windows_user_home_is_hidden(self):
        self.assertEqual(_display_path(r'C:\Users\Alice\AppData\Local\Python313\python.exe', project_root=r'D:\work\project', cwd=r'D:\work', external_label='external interpreter'), 'external interpreter: python.exe')

    def test_posix_project_local_interpreter(self):
        self.assertEqual(_display_path(PurePosixPath('/work/project/.venv/bin/python'), project_root=PurePosixPath('/work/project'), cwd=PurePosixPath('/work')), '.venv/bin/python')

    def test_external_posix_user_home_is_hidden(self):
        self.assertEqual(_display_path('/home/alice/python/bin/python', project_root='/work/project', cwd='/work', external_label='external interpreter'), 'external interpreter: python')

    def test_spaces_and_chinese_paths(self):
        for base in ('Project with spaces', '\u9879\u76ee \u6f14\u793a'):
            with self.subTest(base=base):
                root = PureWindowsPath('D:/work') / base
                self.assertEqual(_display_path(root / '.venv/Scripts/python.exe', project_root=root, cwd=PureWindowsPath('D:/work')), r'.venv\Scripts\python.exe')
                posix = PurePosixPath('/work') / base
                self.assertEqual(_display_path(posix / '.venv/bin/python', project_root=posix, cwd='/work'), '.venv/bin/python')

    def test_prefix_sibling_is_not_a_child(self):
        self.assertEqual(_display_path(r'D:\work\project-other\python.exe', project_root=r'D:\work\project', cwd=r'D:\elsewhere', external_label='external interpreter'), 'external interpreter: python.exe')

    def test_unrelated_cwd_is_not_an_interpreter_workspace(self):
        self.assertEqual(_display_path('/tmp/tools/python', project_root='/work/project', cwd='/tmp', external_label='external interpreter'), 'external interpreter: python')

    def test_filesystem_root_is_not_a_workspace(self):
        for path, root, cwd in (('/home/alice/python', '/work/project', '/'), (r'C:\Users\Alice\python.exe', r'C:\work\project', 'C:/')):
            with self.subTest(path=path):
                self.assertTrue(_display_path(path, project_root=root, cwd=cwd, external_label='external interpreter').startswith('external interpreter:'))

    def test_relative_path_is_preserved(self):
        self.assertEqual(_display_path(PurePosixPath('../tools/python'), project_root='/work/project', cwd='/work'), '../tools/python')

    def test_relative_cli_project_path_and_resolved_internal_path(self):
        results = []
        def record(*args, **kwargs):
            result = run_workflow(*args, **kwargs)
            results.append(result)
            return result
        output = io.StringIO()
        with redirect_stdout(output), patch('agent_doctor.cli.run_workflow', side_effect=record):
            self.assertEqual(main(['examples/fixtures/missing-module']), 0)
        self.assertIn(f'Project: {Path("examples/fixtures/missing-module")}', output.getvalue())
        self.assertNotIn(str(FIXTURES.parent.parent), output.getvalue())
        self.assertEqual(results[0].project.root_path, (FIXTURES / 'missing-module').resolve())
        self.assertTrue(results[0].project.root_path.is_absolute())
        self.assertEqual(results[0].execution_results[-1].status, 'requires_confirmation')

    def test_relative_project_spelling_with_spaces_and_chinese(self):
        result = run_workflow(FIXTURES / 'missing-module')
        for spelling in ('examples/project with spaces', 'examples/\u9879\u76ee \u6f14\u793a'):
            with self.subTest(spelling=spelling):
                text = render_terminal_report(result.project, result.detection, result.environment, (), requested_project_path=spelling)
                self.assertIn(f'Project: {Path(spelling)}', text)

    def test_project_absolute_external_path_hides_home(self):
        result = run_workflow(FIXTURES / 'missing-module')
        for root, cwd, expected in ((PureWindowsPath('C:/Users/Alice/private project'), PureWindowsPath('D:/work'), r'...\private project'), (PurePosixPath('/home/alice/private project'), PurePosixPath('/work'), '.../private project')):
            project = replace(result.project, root_path=root)
            text = render_terminal_report(project, result.detection, result.environment, (), cwd=cwd)
            self.assertIn(f'Project: {expected}', text)
            self.assertNotIn(str(root), text)
            self.assertNotIn('Alice', text)
            self.assertNotIn('/home/alice', text)

    def test_executor_retains_absolute_executable_argv_and_cwd(self):
        with patch('agent_doctor.commands._run_startup_process', return_value=subprocess.CompletedProcess([], 0, '', '')) as runner:
            result = run_workflow('examples/fixtures/missing-module', confirm_startup=True)
        args, kwargs = runner.call_args
        self.assertEqual(args[0], [str(Path(sys.executable).resolve()), str((FIXTURES / 'missing-module/main.py').resolve())])
        self.assertEqual(kwargs['cwd'], (FIXTURES / 'missing-module').resolve())
        self.assertEqual(result.environment.python_executable, Path(sys.executable))

    def test_known_paths_in_diagnosis_prose_and_evidence_are_display_only(self):
        result = run_workflow(FIXTURES / 'missing-module')
        executable = PureWindowsPath('C:/Users/Alice/Python313/python.exe')
        local = PureWindowsPath('D:/work/project/.venv/Scripts/python.exe')
        project = replace(result.project, root_path=PureWindowsPath('D:/work/project'))
        environment = replace(result.environment, python_executable=executable)
        diagnosis = DiagnosisResult('Interpreter differs', 'python_environment', 'WARNING', 1, 'test', evidence_refs=('local',), root_cause_chain=(RootCauseStep('current', f'Current interpreter: {executable.as_posix()}', 'identity', ('local',)),))
        evidence = [Evidence('local', 'local_python_environment', 'test', 'different', metadata={'status':'different','detected_local_environment':'.venv','detected_interpreter_path':str(local),'current_python_executable':str(executable)})]
        before = copy.deepcopy(evidence)
        text = render_terminal_report(project, result.detection, environment, (diagnosis,), evidence=evidence, cwd=PureWindowsPath('D:/work'))
        self.assertIn('external interpreter: python.exe', text)
        self.assertIn(r'Detected interpreter: .venv\Scripts\python.exe', text)
        self.assertNotIn('Alice', text)
        self.assertNotIn('D:\\work', text)
        self.assertEqual(evidence, before)
        self.assertEqual(environment.python_executable, executable)
        self.assertEqual(diagnosis.root_cause_chain[0].title, f'Current interpreter: {executable.as_posix()}')

    def test_json_contract_absolute_paths_and_evidence_unchanged(self):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / 'report.json'
            result = run_workflow('examples/fixtures/missing-module', output)
            before = copy.deepcopy(result.report)
            evidence_before = copy.deepcopy(result.evidence)
            render_terminal_report(result.project, result.detection, result.environment, result.diagnostics, evidence=result.evidence, requested_project_path='examples/fixtures/missing-module')
            payload = json.loads(output.read_text(encoding='utf-8'))
        self.assertEqual(result.report, before)
        self.assertEqual(result.evidence, evidence_before)
        self.assertEqual(payload, before)
        self.assertEqual(payload['schema_version'], '0.2')
        self.assertEqual(payload['project']['root_path'], str(result.project.root_path))
        self.assertEqual(payload['environment']['python_executable'], str(result.environment.python_executable))
        startup = next(item for item in payload['evidence'] if item['kind'] == 'startup_probe')
        self.assertEqual(startup['metadata']['entrypoint'], str(result.project.root_path / 'main.py'))
        self.assertEqual(startup['metadata']['cwd'], str(result.project.root_path))
        self.assertEqual(startup['metadata']['argv'], [str(result.environment.python_executable), str(result.project.root_path / 'main.py')])
