"""Real output-heavy probes verify bounded retention and concurrent pipe drain."""

import io
import os
import sys
import tempfile
import unittest
from pathlib import Path
from threading import Event

from agent_doctor.commands import _StartupCapture, _run_startup_process, execute_startup_probe
from agent_doctor.diagnosis import diagnose
from agent_doctor.project import inspect_environment, scan_project
from agent_doctor.python_plugin import detect_python_project
from agent_doctor.startup import (
    STARTUP_CAPTURE_LIMIT_BYTES, STARTUP_EXCERPT_LIMIT_CHARS,
    collect_startup_evidence, propose_startup_probe,
)


class StartupCaptureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()

    def proposal(self, source):
        (self.root / 'main.py').write_text(source, encoding='utf-8')
        self.project = scan_project(self.root)
        self.environment = inspect_environment(self.project)
        command, self.inspection = propose_startup_probe(self.project, self.environment)
        return command

    def run_source(self, source, timeout=5.0):
        return execute_startup_probe(self.proposal(source), self.root, confirmed=True, timeout=timeout)

    def test_small_stdout_is_complete(self):
        result = self.run_source('print("small output")\n')
        self.assertEqual(result.stdout, 'small output\n')
        self.assertFalse(result.stdout_truncated)
        self.assertEqual(result.status, 'success')

    def test_small_stderr_is_complete(self):
        result = self.run_source('import sys\nsys.stderr.write("small error")\n')
        self.assertEqual(result.stderr, 'small error')
        self.assertFalse(result.stderr_truncated)

    def test_stdout_limit_is_independent(self):
        result = self.run_source(f'import sys\nsys.stdout.buffer.write(b"x" * {STARTUP_CAPTURE_LIMIT_BYTES + 8192})\n')
        self.assertTrue(result.stdout_truncated)
        self.assertFalse(result.stderr_truncated)
        self.assertEqual(len(result.stdout), STARTUP_EXCERPT_LIMIT_CHARS)
        self.assertEqual(result.status, 'success')

    def test_stderr_limit_is_independent(self):
        result = self.run_source(f'import sys\nsys.stderr.buffer.write(b"y" * {STARTUP_CAPTURE_LIMIT_BYTES + 8192})\n')
        self.assertFalse(result.stdout_truncated)
        self.assertTrue(result.stderr_truncated)
        self.assertEqual(len(result.stderr), STARTUP_EXCERPT_LIMIT_CHARS)
        self.assertEqual(result.status, 'success')

    def test_both_large_streams_are_drained_without_deadlock(self):
        result = self.run_source(
            'import sys\nfor _ in range(512):\n'
            ' sys.stdout.buffer.write(b"x" * 8192)\n'
            ' sys.stderr.buffer.write(b"y" * 8192)\n'
        )
        self.assertEqual(result.status, 'success')
        self.assertEqual(result.exit_code, 0)
        self.assertTrue(result.stdout_truncated)
        self.assertTrue(result.stderr_truncated)
        evidence = collect_startup_evidence(self.inspection, result)
        self.assertTrue(evidence.metadata['stdout_truncated'])
        self.assertTrue(evidence.metadata['stderr_truncated'])
        diagnoses, _ = diagnose(self.project, detect_python_project(self.project), self.environment,
                                (result,), startup_probe=evidence)
        self.assertEqual(diagnoses, [])

    def test_capture_byte_buffer_never_exceeds_limit(self):
        capture = _StartupCapture()
        capture.drain(io.BytesIO(b'x' * (STARTUP_CAPTURE_LIMIT_BYTES * 20)), Event())
        self.assertEqual(len(capture.data), STARTUP_CAPTURE_LIMIT_BYTES)
        self.assertTrue(capture.truncated)
        self.assertEqual(len(capture.snapshot()[0]), STARTUP_CAPTURE_LIMIT_BYTES)

    def test_exact_byte_limit_is_not_truncated(self):
        capture = _StartupCapture()
        capture.drain(io.BytesIO(b'x' * STARTUP_CAPTURE_LIMIT_BYTES), Event())
        self.assertEqual(len(capture.data), STARTUP_CAPTURE_LIMIT_BYTES)
        self.assertFalse(capture.truncated)

    def test_utf8_split_at_byte_limit_is_decoded_safely(self):
        capture = _StartupCapture()
        capture.drain(io.BytesIO(('中' * STARTUP_CAPTURE_LIMIT_BYTES).encode('utf-8')), Event())
        text, truncated = capture.snapshot()
        self.assertEqual(len(capture.data), STARTUP_CAPTURE_LIMIT_BYTES)
        self.assertTrue(truncated)
        self.assertLessEqual(len(text), STARTUP_CAPTURE_LIMIT_BYTES)

    def test_universal_newlines_are_preserved(self):
        capture = _StartupCapture()
        capture.drain(io.BytesIO(b'one\r\ntwo\rthree\n'), Event())
        self.assertEqual(capture.snapshot(), ('one\ntwo\nthree\n', False))

    def test_capture_truncation_survives_short_decoded_excerpt(self):
        # Many bytes, few decoded characters: capture truncation is independent
        # of the later excerpt limit. This also protects flag propagation.
        command = self.proposal('pass\n')
        from subprocess import CompletedProcess
        from unittest.mock import patch
        completed = CompletedProcess([], 0, 'short', '')
        completed.stdout_truncated = True
        completed.stderr_truncated = False
        with patch('agent_doctor.commands._run_startup_process', return_value=completed):
            result = execute_startup_probe(command, self.root, confirmed=True)
        self.assertTrue(result.stdout_truncated)
        self.assertTrue(collect_startup_evidence(self.inspection, result).metadata['stdout_truncated'])

    def test_nonzero_exit_after_large_output_is_preserved(self):
        result = self.run_source(f'import sys\nsys.stderr.buffer.write(b"x" * {STARTUP_CAPTURE_LIMIT_BYTES * 4})\nsys.exit(7)\n')
        self.assertEqual(result.status, 'failed')
        self.assertEqual(result.exit_code, 7)
        self.assertTrue(result.stderr_truncated)

    def test_timeout_with_large_output_is_bounded(self):
        result = self.run_source(
            'import sys\nwhile True:\n'
            ' sys.stdout.buffer.write(b"x" * 8192)\n'
            ' sys.stderr.buffer.write(b"y" * 8192)\n', timeout=1.0,
        )
        self.assertEqual(result.status, 'timeout')
        self.assertTrue(result.terminated)
        self.assertTrue(result.stdout_truncated)
        self.assertTrue(result.stderr_truncated)
        self.assertLess(result.duration_seconds, 4.5)

    def test_internal_process_output_is_already_bounded_before_excerpt(self):
        command = self.proposal(f'import sys\nsys.stdout.buffer.write(b"x" * {STARTUP_CAPTURE_LIMIT_BYTES * 8})\n')
        completed = _run_startup_process([sys.executable, *command.arguments], cwd=self.root,
                                         env=os.environ.copy(), timeout=5.0)
        self.assertEqual(len(completed.stdout.encode('utf-8')), STARTUP_CAPTURE_LIMIT_BYTES)
        self.assertTrue(completed.stdout_truncated)


if __name__ == '__main__':
    unittest.main()
