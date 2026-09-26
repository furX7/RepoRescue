"""Step 3 checks use filesystem fixtures and runtime mocks, never commands."""

import os
import sys
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from agent_doctor.models import EnvironmentInfo, ProjectInfo
from agent_doctor.project import inspect_environment
from agent_doctor.python_plugin import propose_diagnostic_commands


class EnvironmentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.project = ProjectInfo(
            root_path=self.root, scanned_at=datetime.now(timezone.utc),
            files=("main.py",),
        )
        self.environment = EnvironmentInfo(
            python_executable=Path(sys.executable), python_version="3.13.1",
            python_available=True, dependency_manifests=(),
        )

    def test_current_python_available_without_launching(self) -> None:
        with patch("agent_doctor.project.sys.executable", str(self.root / "python.exe")):
            with patch.object(Path, "is_file", return_value=True):
                with patch("agent_doctor.project.sys.version_info", (3, 13, 1)):
                    result = inspect_environment(self.project)
        self.assertTrue(result.python_available)
        self.assertEqual(result.python_executable, self.root / "python.exe")
        self.assertEqual(result.python_version, "3.13.1")
        self.assertIsNone(result.python_callable)

    def test_unavailable_python_path(self) -> None:
        with patch("agent_doctor.project.sys.executable", str(self.root / "missing.exe")):
            result = inspect_environment(self.project)
        self.assertFalse(result.python_available)
        self.assertIsNone(result.python_callable)

    def test_empty_executable_metadata(self) -> None:
        with patch("agent_doctor.project.sys.executable", ""):
            result = inspect_environment(self.project)
        self.assertFalse(result.python_available)
        self.assertIsNone(result.python_executable)

    def test_executable_check_error_is_unavailable(self) -> None:
        with patch.object(Path, "is_file", side_effect=PermissionError):
            self.assertFalse(inspect_environment(self.project).python_available)

    def test_manifest_collection_uses_snapshot_without_reading_contents(self) -> None:
        project = replace(self.project, manifests=(
            "requirements.txt", "pyproject.toml", "setup.cfg", "setup.py",
            "requirements.txt", "package.json",
        ))
        with patch.object(Path, "open", side_effect=AssertionError("content read")):
            result = inspect_environment(project)
        self.assertEqual(result.dependency_manifests, (
            "pyproject.toml", "requirements.txt", "setup.cfg", "setup.py",
        ))
        self.assertEqual(inspect_environment(self.project).dependency_manifests, ())

    def test_each_python_feature_proposes_exactly_one_version_command(self) -> None:
        for files in (
            ("main.py",), ("src/app.py",), ("pyproject.toml",),
            ("requirements.txt",), ("setup.py",), ("setup.cfg",),
            ("pyproject.toml", "requirements.txt", "main.py"),
        ):
            with self.subTest(files=files):
                commands = propose_diagnostic_commands(
                    replace(self.project, files=files), self.environment,
                )
                self.assertEqual(len(commands), 1)
                command = commands[0]
                self.assertEqual(command.executable, str(self.environment.python_executable))
                self.assertEqual(command.arguments, ("--version",))
                self.assertEqual(command.working_directory, self.root)
                self.assertEqual(command.source, "python_plugin")
                self.assertEqual(command.risk, "SAFE")
                self.assertTrue(command.reason)

    def test_no_commands_for_unknown_project(self) -> None:
        for files in ((), ("package.json", "README.md")):
            with self.subTest(files=files):
                self.assertEqual(propose_diagnostic_commands(
                    replace(self.project, files=files), self.environment,
                ), ())

    def test_no_commands_for_unavailable_or_missing_interpreter(self) -> None:
        for environment in (
            replace(self.environment, python_available=False),
            replace(self.environment, python_executable=None),
        ):
            with self.subTest(environment=environment):
                self.assertEqual(propose_diagnostic_commands(self.project, environment), ())

    def test_repeated_features_do_not_duplicate_commands(self) -> None:
        project = replace(self.project, files=("main.py", "main.py", "setup.py", "setup.py"))
        self.assertEqual(len(propose_diagnostic_commands(project, self.environment)), 1)

    def test_no_install_destructive_or_bytecode_writing_commands(self) -> None:
        commands = propose_diagnostic_commands(self.project, self.environment)
        for command in commands:
            self.assertNotEqual(command.risk, "DANGEROUS")
            self.assertFalse({"pip", "install", "compileall", "pytest", "rm", "del"}
                             .intersection(command.arguments))
            self.assertEqual(command.arguments, ("--version",))

    def test_inspection_and_proposals_have_no_execution_or_environment_side_effects(self) -> None:
        before = dict(os.environ)
        with patch("subprocess.Popen", side_effect=AssertionError("process launched")):
            with patch("os.system", side_effect=AssertionError("shell invoked")):
                with patch.object(Path, "open", side_effect=AssertionError("content read")):
                    result = inspect_environment(self.project)
                    propose_diagnostic_commands(self.project, result)
        self.assertEqual(dict(os.environ), before)


if __name__ == "__main__":
    unittest.main()
