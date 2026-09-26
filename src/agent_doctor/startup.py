"""One root-level startup proposal and its structured observations; no execution."""

from pathlib import Path

from .models import CommandProposal, EnvironmentInfo, Evidence, ExecutionResult, ProjectInfo


STARTUP_TIMEOUT_SECONDS = 5.0
STARTUP_CAPTURE_LIMIT_BYTES = 64 * 1024
STARTUP_EXCERPT_LIMIT_CHARS = 4096


def validate_startup_entrypoint(root: Path) -> Path:
    """Require a readable root main.py whose resolved path stays at the root.

    Rechecked by the executor. This does not prevent concurrent path replacement.
    """
    entrypoint = root / 'main.py'
    resolved = entrypoint.resolve(strict=True)
    if resolved.parent != root or not resolved.is_file():
        raise ValueError('main.py must resolve to a file directly within the project root')
    with entrypoint.open('rb'):
        pass  # Check access without reading project contents.
    return entrypoint


def propose_startup_probe(
    project: ProjectInfo, environment: EnvironmentInfo,
) -> tuple[CommandProposal | None, Evidence]:
    root = project.root_path
    entrypoint = root / 'main.py'
    metadata = {
        'entrypoint': str(entrypoint),
        'interpreter': str(environment.python_executable) if environment.python_executable else None,
        'argv': None,
        'cwd': str(root),
        'timeout_seconds': STARTUP_TIMEOUT_SECONDS,
        'execution_status': 'no_supported_entrypoint',
        'exit_code': None,
        'duration_ms': None,
        'stdout_excerpt': '',
        'stderr_excerpt': '',
    }
    command = None
    summary = 'No supported startup entrypoint was detected; only root-level main.py is supported.'
    try:
        # lexists distinguishes a broken link from an absent entrypoint.
        if entrypoint.exists() or entrypoint.is_symlink():
            validate_startup_entrypoint(root)
            if not environment.python_available or environment.python_executable is None:
                raise ValueError('Current Python interpreter is unavailable')
            command = CommandProposal(
                executable=str(environment.python_executable), arguments=(str(entrypoint),),
                working_directory=root, source='python_startup_probe', risk='CAUTION',
                reason='Executes project code; project-defined file, network, service, and blocking side effects are possible',
            )
            metadata['argv'] = (command.executable, *command.arguments)
            metadata['execution_status'] = 'requires_confirmation'
            summary = 'Startup probe is available but requires explicit confirmation of project code execution.'
    except (OSError, ValueError, RuntimeError):
        metadata['execution_status'] = 'unavailable'
        summary = 'The root startup entrypoint or interpreter could not be safely used; no startup probe was executed.'
    return command, Evidence(
        evidence_id='project:startup_probe', kind='startup_probe', source='python_startup_probe',
        summary=summary, location=str(entrypoint), metadata=metadata,
    )


def collect_startup_evidence(
    inspection: Evidence, execution: ExecutionResult, *, timeout: float = STARTUP_TIMEOUT_SECONDS,
) -> Evidence:
    """Retain bounded excerpts and execution facts without mutating inspection."""
    metadata = dict(inspection.metadata)
    metadata.update({
        'argv': (execution.command.executable, *execution.command.arguments),
        'cwd': str(execution.command.working_directory),
        'timeout_seconds': timeout,
        'execution_status': execution.status,
        'exit_code': execution.exit_code,
        'duration_ms': round(execution.duration_seconds * 1000, 3),
        'stdout_excerpt': execution.stdout[:STARTUP_EXCERPT_LIMIT_CHARS],
        'stderr_excerpt': execution.stderr[:STARTUP_EXCERPT_LIMIT_CHARS],
        'terminated': execution.terminated,
        'timed_out': execution.timed_out,
        'executed': execution.status in ('success', 'timeout') or (
            execution.status == 'failed' and execution.exit_code is not None
        ),
        'stdout_truncated': execution.stdout_truncated or len(execution.stdout) > STARTUP_EXCERPT_LIMIT_CHARS,
        'stderr_truncated': execution.stderr_truncated or len(execution.stderr) > STARTUP_EXCERPT_LIMIT_CHARS,
    })
    return Evidence(
        evidence_id=inspection.evidence_id, kind=inspection.kind, source=inspection.source,
        summary=f'Supported startup probe status={execution.status}; exit_code={execution.exit_code}.',
        location=inspection.location, metadata=metadata,
    )
