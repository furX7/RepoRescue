"""Filesystem fixture tests for shallow scanning and Python detection."""

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from agent_doctor.project import EXCLUDED_DIRECTORIES, scan_project
from agent_doctor.models import DetectionResult, ProjectInfo
from agent_doctor.python_plugin import detect_python_project


class ProjectTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def create_file(self, relative: str) -> Path:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"not executable project code")
        return path

    def test_missing_path(self) -> None:
        with self.assertRaises(FileNotFoundError):
            scan_project(self.root / "missing")

    def test_file_is_not_a_project_directory(self) -> None:
        with self.assertRaises(NotADirectoryError):
            scan_project(self.create_file("app.py"))

    def test_empty_directory(self) -> None:
        project = scan_project(self.root)
        self.assertEqual(project.files, ())
        self.assertEqual(project.manifests, ())
        self.assertEqual(detect_python_project(project), DetectionResult("unknown", ()))

    def test_virtual_environment_and_cache_roots_are_rejected_before_scanning(self) -> None:
        for name in (".venv", "venv", "__pycache__", "VENV", "__PYCACHE__"):
            with self.subTest(name=name):
                directory = self.root / name
                directory.mkdir(exist_ok=True)
                (directory / "main.py").touch()
                with patch("agent_doctor.project.os.scandir") as scandir:
                    with self.assertRaisesRegex(ValueError, "not a project root"):
                        scan_project(directory)
                    scandir.assert_not_called()

    def test_root_rejection_uses_normalized_path(self) -> None:
        directory = self.root / ".venv"
        (directory / "child").mkdir(parents=True)
        with self.assertRaisesRegex(ValueError, "not a project root"):
            scan_project(directory / "child" / "..")

    def test_common_python_project(self) -> None:
        self.create_file("pyproject.toml")
        self.create_file("requirements.txt")
        self.create_file("src/app.py")
        self.create_file("README.md")
        project = scan_project(self.root)
        self.assertEqual(project.files, (
            "pyproject.toml", "requirements.txt", "src/app.py",
        ))
        self.assertEqual(project.manifests, ("pyproject.toml", "requirements.txt"))
        self.assertEqual(detect_python_project(project), DetectionResult(
            "likely", ("pyproject.toml", "requirements.txt", "src/app.py"),
        ))

    def test_each_manifest_can_identify_a_project(self) -> None:
        for name in ("pyproject.toml", "requirements.txt", "setup.py", "setup.cfg"):
            with self.subTest(name=name):
                directory = self.root / name.replace(".", "-")
                directory.mkdir()
                (directory / name).touch()
                project = scan_project(directory)
                self.assertEqual(project.manifests, (name,))
                self.assertEqual(detect_python_project(project), DetectionResult("likely", (name,)))

    def test_python_file_without_manifest(self) -> None:
        self.create_file("main.py")
        project = scan_project(self.root)
        self.assertEqual(project.manifests, ())
        self.assertEqual(detect_python_project(project), DetectionResult("likely", ("main.py",)))

    def test_direct_child_python_file_is_detected(self) -> None:
        self.create_file("src/main.py")
        self.assertEqual(detect_python_project(scan_project(self.root)), DetectionResult(
            "likely", ("src/main.py",),
        ))

    def test_excluded_directories_are_not_opened(self) -> None:
        for name in EXCLUDED_DIRECTORIES:
            self.create_file(f"{name}/hidden.py")
            self.create_file(f"{name}/requirements.txt")
        import os
        real_scandir = os.scandir
        opened = []

        def track_scandir(path):
            opened.append(Path(path))
            return real_scandir(path)

        with patch("agent_doctor.project.os.scandir", side_effect=track_scandir):
            project = scan_project(self.root)
        self.assertEqual(opened, [self.root.resolve()])
        self.assertEqual(project.files, ())
        self.assertEqual(detect_python_project(project), DetectionResult("unknown", ()))

    def test_deeply_nested_files_are_not_scanned(self) -> None:
        self.create_file("src/package/main.py")
        project = scan_project(self.root)
        self.assertEqual(project.files, ())
        self.assertEqual(detect_python_project(project), DetectionResult("unknown", ()))

    def test_non_python_project(self) -> None:
        self.create_file("package.json")
        self.create_file("src/main.js")
        self.create_file("README.md")
        self.assertEqual(detect_python_project(scan_project(self.root)), DetectionResult("unknown", ()))

    def test_path_is_normalized_and_timestamp_is_aware(self) -> None:
        (self.root / "child").mkdir()
        project = scan_project(str(self.root / "child" / ".."))
        self.assertEqual(project.root_path, self.root.resolve())
        self.assertTrue(project.root_path.is_absolute())
        self.assertIsNotNone(project.scanned_at.utcoffset())

    def test_marker_named_directory_is_not_a_file(self) -> None:
        (self.root / "setup.py").mkdir()
        self.assertEqual(detect_python_project(scan_project(self.root)), DetectionResult("unknown", ()))

    def test_file_contents_are_not_read(self) -> None:
        self.create_file("pyproject.toml")
        with patch.object(Path, "open", side_effect=AssertionError("content read")):
            self.assertEqual(detect_python_project(scan_project(self.root)), DetectionResult(
                "likely", ("pyproject.toml",),
            ))

    def test_detection_reports_only_matching_files(self) -> None:
        project = ProjectInfo(
            root_path=self.root,
            scanned_at=datetime.now(timezone.utc),
            files=("README.md", "src/app.py", "requirements.txt", "package.json"),
        )
        self.assertEqual(detect_python_project(project), DetectionResult(
            "likely", ("src/app.py", "requirements.txt"),
        ))

    def test_scan_budget_rejects_incomplete_snapshots(self) -> None:
        self.create_file("a.txt")
        self.create_file("b.py")
        with self.assertRaisesRegex(ValueError, "exceeded 1 entries"):
            scan_project(self.root, max_entries=1)
        with self.assertRaisesRegex(ValueError, "must be positive"):
            scan_project(self.root, max_entries=0)

    def test_directory_read_errors_propagate(self) -> None:
        with patch("agent_doctor.project.os.scandir", side_effect=PermissionError):
            with self.assertRaises(PermissionError):
                scan_project(self.root)

    def test_windows_junction_is_not_scanned(self) -> None:
        self.create_file("linked/main.py")
        with patch.object(Path, "is_junction", return_value=True):
            self.assertEqual(scan_project(self.root).files, ())


if __name__ == "__main__":
    unittest.main()
