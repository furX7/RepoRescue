"""Allowlist and failure-path tests plus a real Python --version probe."""

import os
import subprocess
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import MagicMock, patch

from agent_doctor.commands import OUTPUT_LIMIT, execute_command
from agent_doctor.models import CommandProposal


class CommandTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.command = CommandProposal(
            executable=sys.executable, arguments=("--version",),
            working_directory=self.root, source="python_plugin",
            reason="Check Python version", risk="SAFE",
        )

    def assert_not_run(self, command, expected="rejected", **kwargs) -> None:
        with patch("agent_doctor.commands.subprocess.run") as run:
            result = execute_command(command, self.root, **kwargs)
        run.assert_not_called()
        self.assertEqual(result.status, expected)
        self.assertIsNone(result.exit_code)
        self.assertTrue(result.message)

    def test_real_current_python_version_is_read_only(self) -> None:
        before = dict(os.environ)
        result = execute_command(self.command, self.root)
        self.assertEqual(result.status, "success")
        self.assertEqual(result.exit_code, 0)
        self.assertIn("Python", result.stdout + result.stderr)
        self.assertGreaterEqual(result.duration_seconds, 0)
        self.assertFalse(result.timed_out)
        self.assertEqual(list(self.root.iterdir()), [])
        self.assertEqual(dict(os.environ), before)

    def test_capture_and_process_invocation_contract(self) -> None:
        completed = subprocess.CompletedProcess([], 0, "version out", "version err")
        with patch("agent_doctor.commands.subprocess.run", return_value=completed) as run:
            result = execute_command(self.command, self.root, timeout=2.0)
        self.assertEqual(result.stdout, "version out")
        self.assertEqual(result.stderr, "version err")
        self.assertEqual(result.exit_code, 0)
        args, kwargs = run.call_args
        self.assertEqual(args[0], [str(Path(sys.executable).resolve()), "--version"])
        self.assertIsInstance(args[0], list)
        self.assertIs(kwargs["shell"], False)
        self.assertEqual(kwargs["cwd"], self.root)
        self.assertEqual(kwargs["timeout"], 2.0)
        self.assertEqual(kwargs["stdin"], subprocess.DEVNULL)
        self.assertTrue(kwargs["capture_output"])
        self.assertNotIn("env", kwargs)

    def test_nonzero_exit_is_structured_failure(self) -> None:
        with patch("agent_doctor.commands.subprocess.run", return_value=
                   subprocess.CompletedProcess([], 7, "out", "err")):
            result = execute_command(self.command, self.root)
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.exit_code, 7)
        self.assertEqual(result.stderr, "err")

    def test_timeout_kills_and_waits_for_direct_child(self) -> None:
        process = MagicMock()
        process.__enter__.return_value = process
        process.communicate.side_effect = [
            subprocess.TimeoutExpired([sys.executable, "--version"], 0.1),
            (b"partial out", b"partial err"),
        ]
        process.poll.return_value = -9
        # Real Popen's context exit waits for the child on Windows as well.
        def close_process(*args):
            process.wait()
            return False

        process.__exit__.side_effect = close_process
        # Use real subprocess.run's cleanup with a fake process, not a real hung command.
        with patch("subprocess.Popen", return_value=process):
            result = execute_command(self.command, self.root, timeout=0.1)
        process.kill.assert_called_once()
        process.wait.assert_called_once()
        self.assertEqual(result.status, "timeout")
        self.assertTrue(result.timed_out)
        self.assertTrue(result.terminated)
        self.assertEqual(result.stdout, "partial out")
        self.assertEqual(result.stderr, "partial err")
        self.assertIsNone(result.exit_code)

    def test_dangerous_never_runs(self) -> None:
        self.assert_not_run(replace(self.command, risk="DANGEROUS"))

    def test_caution_requires_confirmation_without_execution(self) -> None:
        self.assert_not_run(replace(self.command, risk="CAUTION"), "requires_confirmation")

    def test_unknown_safe_executable_is_rejected(self) -> None:
        self.assert_not_run(replace(self.command, executable="unknown"))
        executable = self.root / "fake-python.exe"
        executable.touch()
        self.assert_not_run(replace(self.command, executable=str(executable)))

    def test_python_alias_is_not_allowed(self) -> None:
        self.assert_not_run(replace(self.command, executable="python"))

    def test_shell_and_project_commands_cannot_bypass_safe_label(self) -> None:
        for arguments in (
            ("--version", "|", "more"), ("--version", "&&", "whoami"),
            ("--version", "||", "whoami"), ("--version", ";", "whoami"),
            ("--version > out.txt",), ("--version", ">", "out.txt"),
            ("/c", "python --version"), ("-Command", "python --version"),
            ("-m", "pip", "install", "pytest"), ("-m", "compileall"),
            ("-m", "pytest"), ("-c", "print('code')"), ("main.py",),
            ("--version && del file",), (),
        ):
            with self.subTest(arguments=arguments):
                self.assert_not_run(replace(self.command, arguments=arguments))
        for executable in ("cmd", "powershell", "pip", "del", "rm"):
            with self.subTest(executable=executable):
                self.assert_not_run(replace(self.command, executable=executable))

    def test_unknown_caution_does_not_get_confirmation_path(self) -> None:
        self.assert_not_run(replace(self.command, risk="CAUTION", arguments=("main.py",)))

    def test_working_directory_outside_project_is_rejected(self) -> None:
        self.assert_not_run(replace(self.command, working_directory=self.root.parent))
        self.assert_not_run(replace(self.command, working_directory=self.root / ".."))

    def test_subdirectory_inside_project_is_allowed(self) -> None:
        child = self.root / "src"
        child.mkdir()
        with patch("agent_doctor.commands.subprocess.run", return_value=
                   subprocess.CompletedProcess([], 0, "", "")) as run:
            result = execute_command(replace(self.command, working_directory=child), self.root)
        self.assertEqual(result.status, "success")
        self.assertEqual(run.call_args.kwargs["cwd"], child)

    def test_resolved_directory_escape_is_rejected(self) -> None:
        link = self.root / "link"
        link.mkdir()
        real_resolve = Path.resolve

        def resolve(path, *args, **kwargs):
            return self.root.parent if path == link else real_resolve(path, *args, **kwargs)

        with patch.object(Path, "resolve", resolve):
            self.assert_not_run(replace(self.command, working_directory=link))

    def test_missing_file_or_relative_working_directory_is_rejected(self) -> None:
        self.assert_not_run(replace(self.command, working_directory=self.root / "missing"))
        self.assert_not_run(replace(self.command, working_directory=Path(".")))
        file = self.root / "file"
        file.touch()
        self.assert_not_run(replace(self.command, working_directory=file))

    def test_invalid_project_root_is_rejected(self) -> None:
        for root in (Path("."), self.root / "missing", Path(sys.executable)):
            with self.subTest(root=root), patch("agent_doctor.commands.subprocess.run") as run:
                self.assertEqual(execute_command(self.command, root).status, "rejected")
                run.assert_not_called()

    def test_invalid_timeout_is_rejected(self) -> None:
        for timeout in (0, -1, 31, float("inf"), float("nan")):
            with self.subTest(timeout=timeout):
                self.assert_not_run(self.command, timeout=timeout)

    def test_start_failure_returns_structured_result(self) -> None:
        with patch("agent_doctor.commands.subprocess.run", side_effect=PermissionError("denied")):
            result = execute_command(self.command, self.root)
        self.assertEqual(result.status, "failed")
        self.assertIsNone(result.exit_code)
        self.assertIn("denied", result.message)
        self.assertFalse(result.terminated)

    def test_returned_output_is_capped_with_truncation_flags(self) -> None:
        with patch("agent_doctor.commands.subprocess.run", return_value=
                   subprocess.CompletedProcess([], 0, "x" * (OUTPUT_LIMIT + 1), "err")):
            result = execute_command(self.command, self.root)
        self.assertEqual(len(result.stdout), OUTPUT_LIMIT)
        self.assertTrue(result.stdout_truncated)
        self.assertFalse(result.stderr_truncated)


if __name__ == "__main__":
    unittest.main()
