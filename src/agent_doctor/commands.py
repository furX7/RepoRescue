"""Execute exact version or separately confirmed root-main startup probes.

This is a small allowlist, not a sandbox. Interpreter installation and the
caller-supplied project root are trusted. Path checks do not prevent concurrent
filesystem changes. Startup execution may have project-defined side effects.
No installation or repair is allowed.
"""

import subprocess
import sys
import os
from math import isfinite
from pathlib import Path
from time import perf_counter, sleep

from .models import CommandProposal, ExecutionResult
from .startup import (
    STARTUP_CAPTURE_LIMIT_BYTES, STARTUP_EXCERPT_LIMIT_CHARS,
    STARTUP_TIMEOUT_SECONDS, validate_startup_entrypoint,
)


OUTPUT_LIMIT = 4096
MAX_TIMEOUT_SECONDS = 30.0
_STARTUP_CAPTURE_TIMEOUT_SECONDS = 1.0


def _output_text(value: str | bytes | None) -> str:
    # TimeoutExpired can carry bytes even when the subprocess used text mode.
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value or ""


def execute_command(
    command: CommandProposal, project_root: str | Path, *, timeout: float = 5.0,
) -> ExecutionResult:
    """Validate the proposal independently, then execute only an allowed SAFE probe.

    subprocess.run kills and waits for the direct child on timeout. Captured
    output is capped in the returned result, not in the process pipe buffer.
    No approval token or CAUTION execution capability is implemented here.
    """
    started = perf_counter()

    def result(
        status, message="", *, exit_code=None, stdout="", stderr="",
        timed_out=False, terminated=False,
    ) -> ExecutionResult:
        out, err = _output_text(stdout), _output_text(stderr)
        return ExecutionResult(
            command=command, exit_code=exit_code,
            stdout=out[:OUTPUT_LIMIT], stderr=err[:OUTPUT_LIMIT],
            duration_seconds=perf_counter() - started,
            timed_out=timed_out, terminated=terminated,
            stdout_truncated=len(out) > OUTPUT_LIMIT,
            stderr_truncated=len(err) > OUTPUT_LIMIT,
            status=status, message=message,
        )

    if command.risk == "DANGEROUS":
        return result("rejected", "DANGEROUS commands are prohibited")
    if command.risk not in ("SAFE", "CAUTION"):
        return result("rejected", "Unknown risk level")
    if not isfinite(timeout) or not 0 < timeout <= MAX_TIMEOUT_SECONDS:
        return result("rejected", "timeout must be positive and at most 30 seconds")
    if command.arguments != ("--version",):
        return result("rejected", "Only the single --version argument is allowed")

    try:
        executable = Path(command.executable)
        current_python = Path(sys.executable)
        root = Path(project_root)
        cwd = command.working_directory
        if not executable.is_absolute() or not current_python.is_absolute():
            return result("rejected", "An absolute current-Python executable path is required")
        executable = executable.resolve(strict=True)
        if executable != current_python.resolve(strict=True) or not executable.is_file():
            return result("rejected", "Executable is not the current Python interpreter")
        if not root.is_absolute() or not cwd.is_absolute():
            return result("rejected", "Project root and working directory must be absolute")
        root, cwd = root.resolve(strict=True), cwd.resolve(strict=True)
        if not root.is_dir() or not cwd.is_dir() or not cwd.is_relative_to(root):
            return result("rejected", "Working directory must be a directory within the project")
    except (OSError, ValueError, RuntimeError) as error:
        return result("rejected", f"Invalid executable or directory: {error}")

    if command.risk == "CAUTION":
        return result("requires_confirmation", "User confirmation is required; command was not run")

    try:
        completed = subprocess.run(
            [str(executable), "--version"],
            cwd=cwd, shell=False, stdin=subprocess.DEVNULL,
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as error:
        return result(
            "timeout", "Python version probe timed out; direct child was killed and waited for",
            stdout=error.output, stderr=error.stderr, timed_out=True, terminated=True,
        )
    except OSError as error:
        return result("failed", f"Could not start or complete Python version probe: {error}")

    return result(
        "success" if completed.returncode == 0 else "failed",
        "" if completed.returncode == 0 else "Python version probe returned a nonzero exit code",
        exit_code=completed.returncode, stdout=completed.stdout, stderr=completed.stderr,
    )


def execute_startup_probe(
    command: CommandProposal, project_root: str | Path, *, confirmed: bool = False,
    timeout: float = STARTUP_TIMEOUT_SECONDS,
) -> ExecutionResult:
    """A separate allowlist for absolute current Python + absolute root main.py.

    The caller must obtain informed confirmation for this exact proposal before
    passing True. No shell, extra argv, stdin, or environment override is accepted.
    Direct-child termination and output cleanup have finite waits; descendants
    are not supervised. Each stream retains at most 64 KiB while excess is drained.
    """
    started = perf_counter()

    def result(status, message='', *, code=None, out='', err='', timed_out=False, terminated=False,
               out_truncated=False, err_truncated=False, capture_timed_out=False):
        stdout, stderr = _output_text(out), _output_text(err)
        return ExecutionResult(
            command=command, exit_code=code,
            stdout=stdout[:STARTUP_EXCERPT_LIMIT_CHARS], stderr=stderr[:STARTUP_EXCERPT_LIMIT_CHARS],
            duration_seconds=perf_counter() - started, status=status, message=message,
            timed_out=timed_out, terminated=terminated, capture_timed_out=capture_timed_out,
            stdout_truncated=out_truncated or len(stdout) > STARTUP_EXCERPT_LIMIT_CHARS,
            stderr_truncated=err_truncated or len(stderr) > STARTUP_EXCERPT_LIMIT_CHARS,
        )

    if command.risk != 'CAUTION':
        return result('rejected', 'Startup probes require CAUTION risk; DANGEROUS is prohibited')
    if not isfinite(timeout) or not 0 < timeout <= STARTUP_TIMEOUT_SECONDS:
        return result('rejected', 'Startup observation timeout must be positive and at most 5 seconds')
    try:
        root = Path(project_root)
        executable = Path(command.executable)
        if not root.is_absolute() or not command.working_directory.is_absolute():
            raise ValueError('Absolute root and working directory required')
        root = root.resolve(strict=True)
        if not root.is_dir() or command.working_directory.resolve(strict=True) != root:
            raise ValueError('Startup working directory must equal the project root')
        if not executable.is_absolute() or not Path(sys.executable).is_absolute():
            raise ValueError('Absolute current Python executable required')
        executable = executable.resolve(strict=True)
        if executable != Path(sys.executable).resolve(strict=True) or not executable.is_file():
            raise ValueError('Only the current Python interpreter is permitted')
        entrypoint = root / 'main.py'
        if command.arguments != (str(entrypoint),):
            raise ValueError('Only the absolute root main.py argument is permitted')
        validate_startup_entrypoint(root)
    except (OSError, ValueError, RuntimeError) as error:
        return result('rejected', f'Unsupported or unavailable startup command: {error}')
    if confirmed is not True:
        return result('requires_confirmation', 'Project code execution requires explicit confirmation; not run')
    try:
        completed = _run_startup_process(
            [str(executable), str(entrypoint)], cwd=root, env=os.environ.copy(), timeout=timeout,
        )
    except subprocess.TimeoutExpired as error:
        terminated = getattr(error, 'terminated', False)
        message = ('Observation window ended; direct child termination confirmed' if terminated
                   else 'Observation window ended; direct child termination could not be confirmed')
        return result('timeout', message, out=error.output, err=error.stderr,
                      timed_out=True, terminated=terminated,
                      out_truncated=getattr(error, 'stdout_truncated', False),
                      err_truncated=getattr(error, 'stderr_truncated', False),
                      capture_timed_out=getattr(error, 'capture_timed_out', False))
    except OSError as error:
        return result('failed', f'Could not launch or complete startup probe: {error}')
    return result('success' if completed.returncode == 0 else 'failed',
                  code=completed.returncode, out=completed.stdout, err=completed.stderr,
                  out_truncated=getattr(completed, 'stdout_truncated', False),
                  err_truncated=getattr(completed, 'stderr_truncated', False),
                  capture_timed_out=getattr(completed, 'capture_timed_out', False))


class _StartupCapture:
    """Bounded prefix collected by the caller from a nonblocking raw pipe."""

    def __init__(self):
        self.data = bytearray()
        self.truncated = False
        self.complete = False

    def read(self, stream):
        """Read at most one chunk so neither stream nor the deadline can starve."""
        if self.complete:
            return False
        try:
            chunk = stream.read(8192)
        except BlockingIOError:
            return False
        except OSError:
            self.truncated = True
            self.complete = True
            return False
        if chunk is None:
            return False
        if not chunk:
            self.complete = True
            return False
        remaining = STARTUP_CAPTURE_LIMIT_BYTES - len(self.data)
        self.data.extend(chunk[:remaining])
        self.truncated |= len(chunk) > remaining
        return True

    def snapshot(self):
        text = self.data.decode('utf-8', errors='replace')
        return text.replace('\r\n', '\n').replace('\r', '\n'), self.truncated


def _run_startup_process(argv, *, cwd, env, timeout):
    """Observe the direct child separately from bounded output collection.

    Python 3.12+ supports nonblocking pipes on Windows. No background readers
    remain after return. Descendants are not terminated or supervised; their
    inherited write handles can cause capture timeout without process timeout.
    """
    process = subprocess.Popen(
        argv, cwd=cwd, env=env, shell=False, stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0,
    )
    streams = (process.stdout, process.stderr)
    captures = (_StartupCapture(), _StartupCapture())
    process_deadline = perf_counter() + timeout
    capture_deadline = None
    timed_out = False
    terminated = False
    capture_timed_out = False
    try:
        for stream in streams:
            os.set_blocking(stream.fileno(), False)
        while True:
            progressed = False
            for capture, stream in zip(captures, streams):
                progressed |= capture.read(stream)
            code = process.poll()
            now = perf_counter()
            if code is not None and capture_deadline is None:
                capture_deadline = now + _STARTUP_CAPTURE_TIMEOUT_SECONDS
            elif code is None and capture_deadline is None and now >= process_deadline:
                # Only an observed running direct child incurs process timeout.
                timed_out = True
                try:
                    process.kill()
                    process.wait(timeout=1.0)
                except (OSError, subprocess.TimeoutExpired):
                    pass
                terminated = process.poll() is not None
                capture_deadline = perf_counter() + _STARTUP_CAPTURE_TIMEOUT_SECONDS
            if (code is not None or timed_out) and all(c.complete for c in captures):
                break
            if capture_deadline is not None and perf_counter() >= capture_deadline:
                capture_timed_out = any(not c.complete for c in captures)
                for capture in captures:
                    capture.truncated |= not capture.complete
                break
            if not progressed:
                sleep(0.005)
    except BaseException:
        # Failed nonblocking setup or interrupted collection must release the
        # direct child as well as caller-owned handles, with the same finite wait.
        if process.poll() is None:
            try:
                process.kill()
                process.wait(timeout=1.0)
            except (OSError, subprocess.TimeoutExpired):
                pass
        raise
    finally:
        for stream in streams:
            stream.close()

    out, out_truncated = captures[0].snapshot()
    err, err_truncated = captures[1].snapshot()
    if timed_out:
        error = subprocess.TimeoutExpired(argv, timeout, output=out, stderr=err)
        error.terminated = terminated
        error.stdout_truncated = out_truncated
        error.stderr_truncated = err_truncated
        error.capture_timed_out = capture_timed_out
        raise error
    completed = subprocess.CompletedProcess(argv, code, out, err)
    completed.stdout_truncated = out_truncated
    completed.stderr_truncated = err_truncated
    completed.capture_timed_out = capture_timed_out
    return completed
