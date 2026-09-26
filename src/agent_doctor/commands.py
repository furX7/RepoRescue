"""Execute only the current interpreter's version probe, without a shell.

This is a small allowlist, not a sandbox. Interpreter installation and the
caller-supplied project root are trusted. Path checks do not prevent concurrent
filesystem changes. No project code, installation, or repair is allowed.
"""

import subprocess
import sys
from math import isfinite
from pathlib import Path
from time import perf_counter

from .models import CommandProposal, ExecutionResult


OUTPUT_LIMIT = 4096
MAX_TIMEOUT_SECONDS = 30.0


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
