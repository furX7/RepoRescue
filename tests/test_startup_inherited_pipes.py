"""Real inherited pipes must not replace direct-child outcomes or leak readers."""

import ctypes
import gc
import io
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from agent_doctor.cli import main
from agent_doctor.commands import _run_startup_process, execute_startup_probe
from agent_doctor.project import inspect_environment, scan_project
from agent_doctor.startup import collect_startup_evidence, propose_startup_probe
from agent_doctor.workflow import run_workflow


class StartupInheritedPipeTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.holders = []
        self.addCleanup(self.release_holders)

    def release_holders(self):
        # Only our controlled descendants observe these markers. No tree kill.
        for release, _, _ in self.holders:
            release.touch()
        for _, ready, done in self.holders:
            if ready.exists():
                self.wait_for(done)
                if os.name == 'nt':
                    # A final marker precedes OS exit; wait for our child to
                    # release its cwd handle before TemporaryDirectory cleanup.
                    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
                    kernel.OpenProcess.argtypes = (ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong)
                    kernel.OpenProcess.restype = ctypes.c_void_p
                    kernel.WaitForSingleObject.argtypes = (ctypes.c_void_p, ctypes.c_ulong)
                    kernel.CloseHandle.argtypes = (ctypes.c_void_p,)
                    handle = kernel.OpenProcess(0x00100000, False, int(ready.read_text()))
                    if handle:
                        try:
                            self.assertEqual(kernel.WaitForSingleObject(handle, 5000), 0)
                        finally:
                            kernel.CloseHandle(handle)
                    else:
                        self.assertEqual(ctypes.get_last_error(), 87)

    def wait_for(self, marker):
        deadline = time.monotonic() + 5
        while not marker.exists() and time.monotonic() < deadline:
            time.sleep(.01)
        self.assertTrue(marker.exists(), f'Controlled child did not signal {marker.name}')

    def fixture(self, code, *, held='stdout'):
        number = len(self.holders)
        release, ready, done = (self.root / f'{name}-{number}' for name in ('release', 'ready', 'done'))
        self.holders.append((release, ready, done))
        child = (
            'import os,pathlib,time\n'
            f'release=pathlib.Path({str(release)!r})\n'
            f'pathlib.Path({str(ready)!r}).write_text(str(os.getpid()))\n'
            'deadline=time.monotonic()+30\n'
            'while not release.exists() and time.monotonic()<deadline: time.sleep(.01)\n'
            f'pathlib.Path({str(done)!r}).touch()\n'
        )
        stdout = 'None' if held in ('stdout', 'both') else 'subprocess.DEVNULL'
        stderr = 'None' if held in ('stderr', 'both') else 'subprocess.DEVNULL'
        source = (
            'import pathlib,subprocess,sys,time\n'
            f'child=subprocess.Popen([sys.executable,"-I","-B","-c",{child!r}],'
            f'stdin=subprocess.DEVNULL,stdout={stdout},stderr={stderr},shell=False)\n'
            f'ready=pathlib.Path({str(ready)!r})\n'
            'deadline=time.monotonic()+3\n'
            'while not ready.exists() and time.monotonic()<deadline: time.sleep(.01)\n'
            'if not ready.exists(): raise RuntimeError("child not ready")\n'
            'print("parent stdout",flush=True)\n'
            'print("parent stderr",file=sys.stderr,flush=True)\n'
            f'sys.exit({code})\n'
        )
        (self.root / 'main.py').write_text(source, encoding='utf-8')
        return release, ready, done

    def probe(self, timeout=3):
        project = scan_project(self.root)
        command, inspection = propose_startup_probe(project, inspect_environment(project))
        result = execute_startup_probe(command, self.root, confirmed=True, timeout=timeout)
        return result, collect_startup_evidence(inspection, result, timeout=timeout)

    def assert_capture_limited(self, result, evidence, code):
        self.assertEqual(result.exit_code, code)
        self.assertEqual(result.status, 'success' if code == 0 else 'failed')
        self.assertFalse(result.timed_out)
        self.assertFalse(result.terminated)
        self.assertTrue(result.capture_timed_out)
        self.assertIs(evidence.metadata['capture_timed_out'], True)
        self.assertEqual(evidence.metadata['exit_code'], code)
        self.assertTrue(evidence.metadata['executed'])
        self.assertLess(result.duration_seconds, 5)
        self.assertEqual(result.stdout, 'parent stdout\n')
        self.assertEqual(result.stderr, 'parent stderr\n')

    def test_exit_seven_with_inherited_stdout_retains_error_and_assessment(self):
        _, ready, done = self.fixture(7)
        result = run_workflow(self.root, confirm_startup=True)
        startup = next(item for item in result.execution_results if item.command.source == 'python_startup_probe')
        evidence = next(item for item in result.evidence if item.kind == 'startup_probe')
        self.assert_capture_limited(startup, evidence, 7)
        self.assertTrue(ready.exists())
        self.assertFalse(done.exists())
        self.assertTrue(startup.stdout_truncated)
        self.assertFalse(startup.stderr_truncated)
        diagnosis = next(item for item in result.diagnostics if item.category == 'startup')
        self.assertEqual(diagnosis.severity, 'ERROR')
        self.assertIn('7', diagnosis.root_cause_chain[-1].explanation + diagnosis.root_cause_chain[-1].title)
        self.assertEqual(result.report['schema_version'], '0.2')
        self.assertEqual(result.report['assessment']['outcome'], 'issues_detected')
        self.assertIn({'check': 'startup_capture', 'reason': 'timeout',
                       'evidence_refs': ['project:startup_probe']}, result.report['assessment']['limitations'])

    def test_exit_zero_with_inherited_stderr_is_inconclusive_not_failure(self):
        _, _, done = self.fixture(0, held='stderr')
        result = run_workflow(self.root, confirm_startup=True)
        startup = next(item for item in result.execution_results if item.command.source == 'python_startup_probe')
        evidence = next(item for item in result.evidence if item.kind == 'startup_probe')
        self.assert_capture_limited(startup, evidence, 0)
        self.assertFalse(done.exists())
        self.assertFalse(startup.stdout_truncated)
        self.assertTrue(startup.stderr_truncated)
        self.assertFalse(any(item.category == 'startup' for item in result.diagnostics))
        self.assertEqual(result.report['assessment']['outcome'], 'inconclusive')
        self.assertIn('Startup output capture timed out', result.terminal_report)

    def test_cli_nonzero_capture_timeout_preserves_exit_one(self):
        self.fixture(7, held='both')
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = main([str(self.root), '--run-startup-probe'])
        self.assertEqual(code, 1)
        self.assertIn('Result: Issues detected', out.getvalue())
        self.assertIn('Startup output capture timed out', out.getvalue())
        self.assertEqual(err.getvalue(), '')

    def test_real_process_timeout_keeps_original_observation_semantics(self):
        (self.root / 'main.py').write_text('import time\nprint("running",flush=True)\ntime.sleep(30)\n')
        result, evidence = self.probe(timeout=.5)
        self.assertEqual(result.status, 'timeout')
        self.assertIsNone(result.exit_code)
        self.assertTrue(result.timed_out)
        self.assertTrue(result.terminated)
        self.assertFalse(result.capture_timed_out)
        self.assertEqual(result.stdout, 'running\n')
        self.assertTrue(evidence.metadata['timed_out'])
        self.assertLess(result.duration_seconds, 3.5)

    def test_process_timeout_and_inherited_pipe_timeout_are_both_visible(self):
        self.fixture(0, held='both')
        source = (self.root / 'main.py').read_text().replace('sys.exit(0)', 'time.sleep(30)')
        (self.root / 'main.py').write_text(source)
        result, evidence = self.probe(timeout=1)
        self.assertEqual(result.status, 'timeout')
        self.assertIsNone(result.exit_code)
        self.assertTrue(result.timed_out)
        self.assertTrue(result.terminated)
        self.assertTrue(result.capture_timed_out)
        self.assertTrue(evidence.metadata['capture_timed_out'])
        self.assertTrue(result.stdout_truncated)
        self.assertTrue(result.stderr_truncated)
        self.assertLess(result.duration_seconds, 4)

    def test_completed_readers_preserve_exact_output_and_parent_handles_close(self):
        (self.root / 'main.py').write_text('import sys\nprint("out")\nprint("err",file=sys.stderr)\n')
        launched = []
        original = subprocess.Popen
        def record(*args, **kwargs):
            process = original(*args, **kwargs)
            launched.append(process)
            return process
        with patch('agent_doctor.commands.subprocess.Popen', side_effect=record):
            result, _ = self.probe()
        self.assertEqual(result.exit_code, 0)
        self.assertEqual((result.stdout, result.stderr), ('out\n', 'err\n'))
        self.assertFalse(result.capture_timed_out)
        self.assertFalse(result.stdout_truncated)
        self.assertFalse(result.stderr_truncated)
        self.assertTrue(launched[0].stdout.closed)
        self.assertTrue(launched[0].stderr.closed)
        self.assertEqual(launched[0].poll(), 0)

    def test_nonblocking_setup_failure_still_releases_direct_child_and_pipes(self):
        (self.root / 'main.py').write_text('import time\ntime.sleep(30)\n')
        launched = []
        original = subprocess.Popen
        def record(*args, **kwargs):
            process = original(*args, **kwargs)
            launched.append(process)
            return process
        with patch('agent_doctor.commands.subprocess.Popen', side_effect=record), patch(
                'agent_doctor.commands.os.set_blocking', side_effect=OSError('setup failed')):
            result, _ = self.probe()
        self.assertEqual(result.status, 'failed')
        self.assertIsNone(result.exit_code)
        self.assertFalse(result.timed_out)
        self.assertLess(result.duration_seconds, 2)
        self.assertIsNotNone(launched[0].poll())
        self.assertTrue(launched[0].stdout.closed)
        self.assertTrue(launched[0].stderr.closed)

    def resource_count(self):
        if os.name == 'nt':
            kernel = ctypes.WinDLL('kernel32', use_last_error=True)
            kernel.GetCurrentProcess.restype = ctypes.c_void_p
            kernel.GetProcessHandleCount.argtypes = (ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong))
            count = ctypes.c_ulong()
            self.assertTrue(kernel.GetProcessHandleCount(kernel.GetCurrentProcess(), ctypes.byref(count)))
            return count.value
        if Path('/proc/self/fd').is_dir():
            return len(os.listdir('/proc/self/fd'))
        return None

    def test_repeated_inherited_pipes_do_not_accumulate_readers_or_handles(self):
        # Warm platform subprocess bookkeeping before counting caller resources.
        _run_startup_process([sys.executable, '-I', '-B', '-c', 'pass'],
                             cwd=self.root, env=os.environ.copy(), timeout=3)
        gc.collect()
        threads = set(threading.enumerate())
        resources = self.resource_count()
        for _ in range(4):
            self.fixture(7, held='both')
            result, evidence = self.probe()
            self.assert_capture_limited(result, evidence, 7)
            self.assertEqual(set(threading.enumerate()), threads)
            gc.collect()
            current = self.resource_count()
            if resources is not None:
                self.assertLessEqual(current, resources + 2)
        self.assertTrue(all(not done.exists() for _, _, done in self.holders))


if __name__ == '__main__':
    unittest.main()
