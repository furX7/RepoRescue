"""Project-local interpreter observations, previews and read-only integration."""
import json
import os
import sys
import tempfile
import tomllib
import unittest
from contextlib import chdir
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from agent_doctor.diagnosis import diagnose
from agent_doctor.models import DetectionResult, EnvironmentInfo
from agent_doctor.project import scan_project
from agent_doctor.python_plugin import inspect_local_python_environment, inspect_python_requirement
from agent_doctor.report import build_json_report, render_terminal_report
from agent_doctor.workflow import run_workflow


class LocalEnvironmentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "main.py").write_text("raise RuntimeError('do not execute')", encoding="utf-8")
        self.project = scan_project(self.root)
        self.environment = EnvironmentInfo(Path(sys.executable), "3.13.1", True, ())

    def candidate(self, name=".venv", layout="Scripts/python.exe"):
        path = self.root / name / layout
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"fixture, not an executable")
        return path

    def inspect(self, current=None):
        environment = self.environment if current is None else replace(self.environment, python_executable=current)
        return inspect_local_python_environment(self.project, environment)

    def diagnoses(self, item):
        return diagnose(self.project, DetectionResult("likely", ("main.py",)), self.environment,
                        local_environment=item)

    def test_windows_candidates_different(self):
        for name in (".venv", "venv"):
            with self.subTest(name=name):
                path = self.candidate(name)
                item = self.inspect()
                self.assertEqual(item.metadata["status"], "different")
                self.assertEqual(item.metadata["detected_local_environment"], name)
                self.assertEqual(item.metadata["detected_interpreter_path"], str(path))
                path.unlink()

    def test_posix_candidates(self):
        for name in (".venv", "venv"):
            with self.subTest(name=name):
                path = self.candidate(name, "bin/python")
                self.assertEqual(self.inspect().metadata["status"], "different")
                path.unlink()

    def test_current_matches_candidate_and_no_problem(self):
        item = self.inspect(self.candidate())
        self.assertEqual(item.metadata["status"], "matched")
        self.assertEqual(self.diagnoses(item)[0], [])

    def test_no_environment_is_positive_evidence_only(self):
        item = self.inspect()
        self.assertEqual(item.metadata["status"], "none")
        self.assertIsNone(item.metadata["detected_local_environment"])
        self.assertIsNone(item.metadata["detected_interpreter_path"])
        diagnoses, evidence = self.diagnoses(item)
        self.assertEqual(diagnoses, [])
        self.assertIn(item, evidence)

    def test_empty_directory_is_not_an_environment(self):
        (self.root / ".venv").mkdir()
        self.assertEqual(self.inspect().metadata["status"], "none")

    def test_interpreter_directory_is_not_a_real_file(self):
        (self.root / ".venv/Scripts/python.exe").mkdir(parents=True)
        self.assertEqual(self.inspect().metadata["status"], "none")

    def test_two_environments_are_ambiguous_even_if_current_matches_one(self):
        first, second = self.candidate(), self.candidate("venv")
        item = self.inspect(first)
        self.assertEqual(item.metadata["status"], "ambiguous")
        self.assertEqual(item.metadata["detected_local_environment"], (".venv", "venv"))
        self.assertEqual(item.metadata["detected_interpreter_path"], (str(first), str(second)))
        self.assertEqual(self.diagnoses(item)[0], [])

    def test_two_layouts_in_one_environment_are_also_ambiguous(self):
        self.candidate()
        self.candidate(layout="bin/python")
        self.assertEqual(self.inspect().metadata["status"], "ambiguous")

    def test_relative_current_path_normalizes_to_matching_candidate(self):
        self.candidate()
        with chdir(self.root):
            item = self.inspect(Path(".venv/Scripts/python.exe"))
        self.assertEqual(item.metadata["status"], "matched")

    @unittest.skipUnless(os.name == "nt", "Windows path case semantics")
    def test_windows_case_is_ignored(self):
        self.assertEqual(self.inspect(Path(str(self.candidate()).upper())).metadata["status"], "matched")

    def test_parent_directory_alias_resolves_to_same_environment(self):
        candidate = self.candidate()
        current = self.root / "alias/python.exe"
        original = Path.resolve
        def resolve(path, *args, **kwargs):
            if path == current:
                return candidate
            if path == current.parent:
                return candidate.parent
            return original(path, *args, **kwargs)
        with patch.object(Path, "resolve", resolve):
            self.assertEqual(self.inspect(current).metadata["status"], "matched")

    def test_symlink_to_base_python_does_not_match_system_environment(self):
        candidate = self.candidate(layout="bin/python")
        current = self.root / "system/python"
        original = Path.resolve
        def resolve(path, *args, **kwargs):
            if path == candidate:
                return current
            return original(path, *args, **kwargs)
        with patch.object(Path, "resolve", resolve):
            self.assertEqual(self.inspect(current).metadata["status"], "different")

    def test_probe_error_is_unavailable_not_wrong_interpreter(self):
        with patch.object(Path, "is_file", side_effect=PermissionError("private details")):
            item = self.inspect()
        self.assertEqual(item.metadata["status"], "unavailable")
        self.assertEqual(self.diagnoses(item)[0], [])
        self.assertNotIn("private details", item.summary)

    def test_unknown_current_interpreter_is_not_called_wrong(self):
        self.candidate()
        item = inspect_local_python_environment(
            self.project, replace(self.environment, python_executable=None))
        self.assertEqual(item.metadata["status"], "unavailable")
        self.assertEqual(self.diagnoses(item)[0], [])

    def test_different_produces_warning_and_only_supported_causes(self):
        self.candidate()
        diagnoses, _ = self.diagnoses(self.inspect())
        self.assertEqual(len(diagnoses), 1)
        result = diagnoses[0]
        self.assertEqual((result.category, result.severity), ("python_environment", "WARNING"))
        self.assertEqual(result.probable_causes, ())
        for claim in ("definitely", "PATH is wrong", "IDE is wrong", "not activated", "pip"):
            self.assertNotIn(claim, str(result))

    def test_chain_refs_and_preview_verification_are_unexecuted(self):
        self.candidate()
        item = self.inspect()
        diagnoses, evidence = self.diagnoses(item)
        diagnosis = diagnoses[0]
        self.assertEqual(len(diagnosis.root_cause_chain), 3)
        self.assertTrue(all(step.evidence_refs == (item.evidence_id,) for step in diagnosis.root_cause_chain))
        self.assertIn(item, evidence)
        plan = diagnosis.repair_plan
        self.assertEqual(plan.risk, "LOW")
        self.assertEqual(plan.execution_status, "not_executed")
        self.assertEqual(len(plan.verification_steps), 3)
        self.assertTrue(all(step.status == "not_run" for step in plan.verification_steps))
        self.assertTrue(all(action.requires_confirmation for action in plan.actions))

    def test_version_and_environment_diagnoses_remain_independent(self):
        self.candidate()
        (self.root / "pyproject.toml").write_text(
            '[project]\nrequires-python = ">=3.10,<3.13"\n', encoding="utf-8")
        project = scan_project(self.root)
        local = self.inspect()
        requirement = inspect_python_requirement(project, self.environment)
        diagnoses, _ = diagnose(project, DetectionResult("likely", ("main.py",)),
                                self.environment, python_requirement=requirement, local_environment=local)
        self.assertEqual({d.category for d in diagnoses}, {"python_version", "python_environment"})
        for result in diagnoses:
            expected = local.evidence_id if result.category == "python_environment" else requirement.evidence_id
            self.assertEqual(result.evidence_refs, (expected,))
            self.assertTrue(all(step.evidence_refs == (expected,) for step in result.root_cause_chain))

    def test_json_schema_and_candidate_arrays(self):
        self.candidate()
        self.candidate("venv")
        item = self.inspect()
        diagnoses, evidence = self.diagnoses(item)
        report = build_json_report(self.project, DetectionResult("likely", ("main.py",)),
                                   self.environment, diagnoses, evidence)
        loaded = json.loads(json.dumps(report))
        self.assertEqual(loaded["schema_version"], "0.2")
        local = next(e for e in loaded["evidence"] if e["kind"] == "local_python_environment")
        self.assertEqual(local["metadata"]["detected_local_environment"], [".venv", "venv"])
        self.assertEqual(local["metadata"]["current_python_executable"], str(self.environment.python_executable))

    def test_terminal_different_and_ambiguous(self):
        path = self.candidate()
        item = self.inspect()
        diagnoses, evidence = self.diagnoses(item)
        terminal = render_terminal_report(self.project, DetectionResult("likely", ("main.py",)),
                                         self.environment, diagnoses, evidence=evidence)
        self.assertIn("[WARNING] Python environment", terminal)
        self.assertIn(str(path), terminal)
        self.assertIn("Detected environment: .venv", terminal)
        self.assertIn("READ ONLY", terminal)
        self.candidate("venv")
        item = self.inspect()
        terminal = render_terminal_report(self.project, DetectionResult("likely", ("main.py",)),
                                         self.environment, (), evidence=(item,))
        self.assertIn("Multiple local Python environments", terminal)

    def test_no_nested_or_custom_environment_probe(self):
        self.candidate("custom-env")
        self.candidate("child/.venv")
        self.assertEqual(self.inspect().metadata["status"], "none")

    def test_inspection_does_not_read_content_or_run_processes(self):
        self.candidate()
        with patch.object(Path, "open", side_effect=AssertionError("content read")), \
             patch("subprocess.Popen", side_effect=AssertionError("process started")):
            self.assertEqual(self.inspect().metadata["status"], "different")

    def test_real_workflow_files_mtimes_environment_and_commands_unchanged(self):
        self.candidate()
        def snapshot():
            return {str(p.relative_to(self.root)): (p.read_bytes(), p.stat().st_mtime_ns)
                    if p.is_file() else None for p in self.root.rglob("*")}
        before, env = snapshot(), dict(os.environ)
        result = run_workflow(self.root)
        self.assertEqual(snapshot(), before)
        self.assertEqual(dict(os.environ), env)
        self.assertEqual(len(result.execution_results), 2)
        self.assertEqual(result.execution_results[1].status, 'requires_confirmation')
        self.assertEqual(result.execution_results[0].command.executable, sys.executable)
        self.assertEqual(result.execution_results[0].command.arguments, ("--version",))
        self.assertEqual(result.diagnostics[0].category, "python_environment")

    def test_runtime_dependencies_remain_empty(self):
        config = tomllib.loads((Path(__file__).resolve().parents[1] / "pyproject.toml").read_text(encoding="utf-8"))
        self.assertEqual(config["project"]["dependencies"], [])


if __name__ == "__main__":
    unittest.main()
