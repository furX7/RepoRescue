"""End-to-end fixtures verify coordination, CLI errors, and project protection."""

import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from agent_doctor.cli import main
from agent_doctor.project import inspect_environment, scan_project
from agent_doctor.python_plugin import propose_diagnostic_commands
from agent_doctor.workflow import WorkflowError, run_workflow


class WorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.project = self.base / "project"
        self.project.mkdir()

    def add_file(self, name="main.py", content=b"raise RuntimeError('must never execute')"):
        file = self.project / name
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_bytes(content)
        return file

    def run_cli(self, *args):
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = main(list(args))
        return code, stdout.getvalue(), stderr.getvalue()

    def test_main_py_project_real_version_probe(self) -> None:
        self.add_file()
        result = run_workflow(self.project)
        self.assertEqual(result.detection.level, "likely")
        self.assertEqual(len(result.execution_results), 2)
        self.assertEqual(result.execution_results[1].status, 'requires_confirmation')
        self.assertEqual(result.execution_results[0].status, "success")
        self.assertEqual(result.execution_results[0].command.arguments, ("--version",))
        self.assertTrue(result.environment.python_callable)
        self.assertIn("launch verified", result.terminal_report)
        self.assertEqual(result.diagnostics, ())
        self.assertIsNone(result.output_path)

    def test_pyproject_only_project(self) -> None:
        self.add_file("pyproject.toml", b"[project]\nname = 'sample'\n")
        result = run_workflow(self.project)
        self.assertEqual(result.detection.matched_files, ("pyproject.toml",))
        self.assertTrue(result.environment.python_callable)

    def test_empty_directory_has_warning_without_execution(self) -> None:
        with patch("agent_doctor.workflow.execute_command") as execute:
            result = run_workflow(self.project)
        execute.assert_not_called()
        self.assertEqual(result.detection.level, "unknown")
        self.assertIsNone(result.environment.python_callable)
        self.assertEqual(result.diagnostics[0].severity, "WARNING")

    def test_non_python_project(self) -> None:
        self.add_file("main.js")
        result = run_workflow(self.project)
        self.assertEqual(result.detection.level, "unknown")
        self.assertEqual(result.execution_results, ())

    def test_invalid_path_is_tool_error(self) -> None:
        with self.assertRaises(WorkflowError):
            run_workflow(self.base / "missing")

    def test_virtual_environment_root_is_tool_error(self) -> None:
        venv = self.project / ".venv"
        venv.mkdir()
        with self.assertRaisesRegex(WorkflowError, "not a project root"):
            run_workflow(venv)

    def test_version_failure_is_diagnosis_not_workflow_error(self) -> None:
        self.add_file()
        with patch("agent_doctor.commands.subprocess.run", side_effect=PermissionError("denied")):
            result = run_workflow(self.project)
        self.assertFalse(result.environment.python_callable)
        self.assertEqual(len(result.diagnostics), 1)
        self.assertEqual(result.diagnostics[0].source, "rule:python_version_probe_failed")
        self.assertIn("launch failed", result.terminal_report)
        self.assertIn("status=failed", next(item.summary for item in result.evidence if item.evidence_id == 'execution:0'))

    def test_timeout_is_diagnosis_and_updates_environment(self) -> None:
        self.add_file()
        with patch("agent_doctor.commands.subprocess.run", side_effect=
                   subprocess.TimeoutExpired([sys.executable, "--version"], 5)):
            result = run_workflow(self.project)
        self.assertFalse(result.environment.python_callable)
        self.assertEqual(result.execution_results[0].status, "timeout")
        self.assertEqual(result.diagnostics[0].severity, "ERROR")
        self.assertIn("timed_out=True", next(item.summary for item in result.evidence if item.evidence_id == 'execution:0'))

    def test_nonzero_exit_is_diagnosis(self) -> None:
        self.add_file()
        with patch("agent_doctor.commands.subprocess.run", return_value=
                   subprocess.CompletedProcess([], 1, "", "error")):
            result = run_workflow(self.project)
        self.assertFalse(result.environment.python_callable)
        self.assertEqual(result.diagnostics[0].severity, "ERROR")

    def test_caution_and_dangerous_use_executor_without_bypass(self) -> None:
        self.add_file()
        project = scan_project(self.project)
        proposal = propose_diagnostic_commands(project, inspect_environment(project))[0]
        for risk, status in (("CAUTION", "requires_confirmation"), ("DANGEROUS", "rejected")):
            with self.subTest(risk=risk):
                with patch("agent_doctor.python_extension.PythonCoreExtension.propose_diagnostic_commands",
                           return_value=(replace(proposal, risk=risk),)):
                    with patch("agent_doctor.commands.subprocess.run") as run:
                        result = run_workflow(self.project)
                run.assert_not_called()
                self.assertEqual(result.execution_results[0].status, status)
                self.assertIsNone(result.environment.python_callable)

    def test_cli_normal_output_and_exit_zero(self) -> None:
        self.add_file()
        code, stdout, stderr = self.run_cli(str(self.project))
        self.assertEqual(code, 0)
        self.assertIn("RepoRescue\nProject:", stdout)
        self.assertIn("No problems detected by the current checks.", stdout)
        self.assertIn("limited checks", stdout)
        self.assertEqual(stderr, "")

    def test_cli_warning_only_exits_zero(self) -> None:
        code, stdout, _ = self.run_cli(str(self.project))
        self.assertEqual(code, 0)
        self.assertIn("[WARNING]", stdout)

    def test_cli_error_diagnosis_exits_one(self) -> None:
        self.add_file()
        with patch("agent_doctor.commands.subprocess.run", side_effect=OSError("launch failed")):
            code, stdout, stderr = self.run_cli(str(self.project))
        self.assertEqual(code, 1)
        self.assertIn("[ERROR] Python environment", stdout)
        self.assertEqual(stderr, "")

    def test_cli_invalid_path_exits_two(self) -> None:
        code, stdout, stderr = self.run_cli(str(self.base / "missing"))
        self.assertEqual(code, 2)
        self.assertEqual(stdout, "")
        self.assertIn("RepoRescue error:", stderr)

    def test_cli_argument_error_exits_two(self) -> None:
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error:
            main([])
        self.assertEqual(error.exception.code, 2)

    def test_cli_internal_failure_exits_two(self) -> None:
        with patch("agent_doctor.cli.run_workflow", side_effect=RuntimeError("unexpected")):
            code, _, stderr = self.run_cli(str(self.project))
        self.assertEqual(code, 2)
        self.assertIn("internal error: unexpected", stderr)

    def test_cli_output_creates_json(self) -> None:
        self.add_file()
        output = self.base / "报告.json"
        code, stdout, stderr = self.run_cli(str(self.project), "--output", str(output))
        self.assertEqual(code, 0)
        self.assertEqual(stderr, "")
        self.assertIn("JSON report saved:", stdout)
        report = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(report["schema_version"], "0.2")
        self.assertTrue(report["environment"]["python_callable"])
        self.assertEqual(report["diagnostics"], [])

    def test_existing_output_is_not_overwritten_and_exits_two(self) -> None:
        output = self.base / "report.json"
        output.write_bytes(b"original")
        code, _, stderr = self.run_cli(str(self.project), "--output", str(output))
        self.assertEqual(code, 2)
        self.assertIn("refusing to overwrite", stderr)
        self.assertEqual(output.read_bytes(), b"original")

    def test_missing_output_parent_is_tool_error(self) -> None:
        code, _, stderr = self.run_cli(
            str(self.project), "--output", str(self.base / "missing" / "report.json"),
        )
        self.assertEqual(code, 2)
        self.assertIn("Could not write report", stderr)

    def test_project_files_contents_and_mtimes_stay_unchanged(self) -> None:
        self.add_file()
        self.add_file("pyproject.toml", b"[project]\n")
        self.add_file("src/helper.py")
        self.add_file(".venv/site.py")
        self.add_file("notes.txt", b"keep unchanged")

        def snapshot():
            return {
                path.relative_to(self.project).as_posix():
                (path.read_bytes(), path.stat().st_mtime_ns) if path.is_file() else None
                for path in self.project.rglob("*")
            }

        before, environment_before = snapshot(), dict(os.environ)
        result = run_workflow(self.project)
        self.assertEqual(snapshot(), before)
        self.assertEqual(dict(os.environ), environment_before)
        self.assertFalse(any(path.name == "__pycache__" for path in self.project.rglob("*")))
        self.assertEqual(result.execution_results[0].status, "success")

    def test_real_module_cli_subprocess(self) -> None:
        self.add_file()
        # Make src available without changing environment variables or installing.
        source = str(Path(__file__).resolve().parents[1] / "src")
        launcher = (
            "import sys; sys.path.insert(0, sys.argv[1]); "
            "from agent_doctor.cli import main; sys.exit(main(sys.argv[2:]))"
        )
        completed = subprocess.run(
            [sys.executable, "-B", "-c", launcher, source, str(self.project)],
            capture_output=True, text=True, timeout=10, shell=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("Detection: likely", completed.stdout)
        self.assertIn("launch verified", completed.stdout)


if __name__ == "__main__":
    unittest.main()
