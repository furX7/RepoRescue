"""Render supplied diagnostic data as a terminal summary or JSON report.

This module does not inspect projects, run commands, or alter diagnosis results.
"""

import json
import re
from dataclasses import fields, is_dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path, PurePath, PurePosixPath, PureWindowsPath
from typing import Any, Mapping, Sequence

from . import __version__
from .models import (
    DetectionResult,
    DiagnosisResult,
    EnvironmentInfo,
    Evidence,
    ProjectInfo,
)


_CAPABILITIES = {
    "diagnosis": True,
    "root_cause_analysis": True,
    "repair_preview": True,
    "repair_execution": False,
    "verification_plan": True,
    "verification_execution": False,
    "rollback": False,
}


class ReportWriteError(OSError):
    """A requested JSON report could not be created safely."""


def _json_value(value: Any) -> Any:
    """Convert standard Python data types into JSON-compatible values."""
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value) and not isinstance(value, type):
        return {field.name: _json_value(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, (tuple, list)):
        return [_json_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise TypeError(f"Unsupported report value: {type(value).__name__}")


def build_json_report(
    project: ProjectInfo,
    detection: DetectionResult,
    environment: EnvironmentInfo,
    diagnostics: Sequence[DiagnosisResult],
    evidence: Sequence[Evidence],
    *,
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    """Build schema 0.2, retaining all previous fields and adding explicit capabilities.

    healthy means no issue found by these limited checks, not overall project health.
    Tool errors are handled by the CLI (exit 2), not turned into project reports.
    """
    timestamp = generated_at or datetime.now(timezone.utc)
    if any(item.severity in ("ERROR", "CRITICAL") for item in diagnostics):
        status = "issues_detected"
    elif detection.level == "unknown":
        status = "unknown"
    elif any(item.severity == "WARNING" for item in diagnostics):
        status = "issues_detected"
    else:
        status = "healthy"
    report = {
        "schema_version": "0.2",
        "generated_at": timestamp,
        "tool": {"name": "repo-rescue", "version": __version__},
        "status": status,
        "capabilities": _CAPABILITIES.copy(),
        "project": project,
        "detection": detection,
        "environment": environment,
        "diagnostics": tuple(diagnostics),
        "evidence": tuple(evidence),
    }
    return _json_value(report)


def _presentation_path(value: str | PurePath) -> PurePath:
    """Interpret both Windows and POSIX spellings without touching the filesystem."""
    if isinstance(value, PurePosixPath):
        return PurePosixPath(value)
    text = str(value)
    if isinstance(value, PureWindowsPath) or PureWindowsPath(text).drive or "\\" in text:
        return PureWindowsPath(text)
    return PurePosixPath(text)


def _relative_display(path: PurePath, base: PurePath) -> str | None:
    try:
        return str(path.relative_to(base))
    except ValueError:
        return None


def _display_path(
    value: str | PurePath, *, project_root: str | PurePath,
    cwd: str | PurePath, external_label: str = "external path",
) -> str:
    """Display only; never resolve a path or return it to an executor."""
    path, root, workspace = map(_presentation_path, (value, project_root, cwd))
    if not path.is_absolute():
        return str(path)
    relative = _relative_display(path, root)
    if relative is not None:
        return relative
    # cwd is a useful workspace anchor only when it contains this project.
    # A filesystem root must not expose arbitrary external directory trees.
    if workspace != type(workspace)(workspace.anchor) and _relative_display(root, workspace) is not None:
        relative = _relative_display(path, workspace)
        if relative is not None:
            return relative
    return f"{external_label}: {path.name}"


def _terminal_paths(
    text: str, project: ProjectInfo, environment: EnvironmentInfo,
    evidence: Sequence[Evidence], requested_project_path: str | PurePath | None,
    cwd: str | PurePath,
) -> str:
    """Format known structured paths in terminal text without changing source data."""
    root, workspace = map(_presentation_path, (project.root_path, cwd))
    requested = _presentation_path(requested_project_path) if requested_project_path is not None else root
    if not requested.is_absolute():
        requested = type(root)(requested)
    workspace_project = None
    if workspace != type(workspace)(workspace.anchor):
        workspace_project = _relative_display(root, workspace)
    project_display = str(requested) if not requested.is_absolute() else (
        workspace_project or str(type(root)("...") / root.name)
    )
    replacements = {root: project_display}
    if environment.python_executable is not None:
        interpreter = _presentation_path(environment.python_executable)
        replacements[interpreter] = _display_path(
            interpreter, project_root=root, cwd=workspace, external_label="external interpreter",
        )
    for item in evidence:
        for key in ('entrypoint', 'detected_interpreter_path', 'current_python_executable', 'interpreter'):
            value = item.metadata.get(key)
            values = value if isinstance(value, (tuple, list)) else (value,)
            for value in values:
                if not isinstance(value, (str, PurePath)):
                    continue
                path = _presentation_path(value)
                if path.is_absolute():
                    replacements.setdefault(path, _display_path(
                        path, project_root=root, cwd=workspace,
                        external_label='external interpreter' if key != 'entrypoint' else 'external path',
                    ))
    # Exact known path spellings may occur inside diagnosis/verification prose.
    # These substitutions are presentation only; ancestry was decided by pathlib.
    patterns = []
    for path, display in sorted(replacements.items(), key=lambda item: len(str(item[0])), reverse=True):
        spellings = dict.fromkeys((str(path), path.as_posix()))
        pattern = '(?:' + '|'.join(re.escape(spelling) for spelling in spellings) + r')(?![\w.-])'
        if isinstance(path, PureWindowsPath):
            pattern = f"(?i:{pattern})"
        patterns.append((pattern, display))
    if patterns:
        combined = '|'.join(f"(?P<p{index}>{pattern})" for index, (pattern, _) in enumerate(patterns))
        text = re.sub(combined, lambda match: patterns[int(match.lastgroup[1:])][1], text)
    return text


def render_terminal_report(
    project: ProjectInfo,
    detection: DetectionResult,
    environment: EnvironmentInfo,
    diagnostics: Sequence[DiagnosisResult],
    *, evidence: Sequence[Evidence] = (),
    requested_project_path: str | PurePath | None = None,
    cwd: str | PurePath | None = None,
) -> str:
    """Render a concise summary without expanding evidence bodies."""
    if environment.python_available:
        availability = "available"
    else:
        availability = "unavailable"
    version = environment.python_version or "unknown version"
    cwd = cwd if cwd is not None else Path.cwd()
    if environment.python_executable is not None:
        executable = _display_path(
            environment.python_executable, project_root=project.root_path,
            cwd=cwd, external_label="external interpreter",
        )
        interpreter_display = executable if executable.startswith("external interpreter:") else f"executable: {executable}"
    else:
        interpreter_display = "executable: not found"
    if environment.python_callable is True:
        launch = "launch verified"
    elif environment.python_callable is False:
        launch = "launch failed"
    else:
        launch = "launch not verified"

    lines = [
        "RepoRescue",
        f"Project: {project.root_path}",
        f"Detection: {detection.level}",
        f"Python Environment: {availability}; Python {version}; {launch}; {interpreter_display}",
        "", "Findings:",
    ]
    # Sort only the presentation; JSON and independent diagnoses stay unchanged.
    priority = {"CRITICAL": 0, "ERROR": 1, "WARNING": 2, "INFO": 3}
    ordered = sorted(diagnostics, key=lambda item: priority.get(item.severity, 4))
    labels = {
        "python_import": "Python import", "python_version": "Python version",
        "project_detection": "Project detection", "environment": "Python environment",
        "tool/safety": "Safety", "startup": "Startup probe",
        "python_environment": "Python environment",
    }
    startup = next((item for item in evidence if item.kind == 'startup_probe'), None)
    for diagnosis in ordered:
        problem = diagnosis.problem
        if diagnosis.category == 'startup' and startup is not None:
            metadata = startup.metadata
            if metadata.get('execution_status') == 'timeout':
                window = metadata['timeout_seconds']
                problem = f"Startup probe exceeded the {window:g} second observation window."
            elif metadata.get('exit_code') is not None:
                problem = f"Startup probe exited with code {metadata['exit_code']}."
        label = labels.get(diagnosis.category, diagnosis.category.replace('_', ' ').capitalize())
        if diagnosis.category == 'startup' and startup is not None:
            lines.append(f"[{diagnosis.severity}] {problem}")
        else:
            lines.append(f"[{diagnosis.severity}] {label}: {problem}")
    if not ordered:
        if startup is not None and startup.metadata.get('execution_status') == 'success':
            lines.append('No additional findings from the current limited checks.')
        else:
            lines.append('No problems detected by the current checks.')

    # Import details are shown once, beside findings; raw tracebacks stay in JSON.
    for item in evidence:
        if item.kind == 'python_import_failure':
            if item.metadata.get('status') == 'missing_module':
                lines.append(f"  Missing import: {item.metadata['missing_module']}")
            elif item.metadata.get('status') == 'symbol_import_failure':
                lines.append(f"  Import module: {item.metadata['source_module']}")
                lines.append(f"  Import symbol: {item.metadata['imported_symbol']}")
            lines.append(f"  Import evidence: {item.metadata['raw_message']}")

    causes = [item for item in ordered if item.root_cause_chain]
    if causes:
        lines.extend(('', 'Root cause:'))
        for diagnosis in causes:
            lines.append('  ' + ' -> '.join(step.title for step in diagnosis.root_cause_chain))
    plans = [item.repair_plan for item in ordered if item.repair_plan is not None]
    if plans:
        lines.extend(('', 'Repair preview:'))
        for plan in plans:
            lines.append(f"  Suggested repair (preview only, {plan.risk}): {plan.summary}")
        lines.extend(('', 'Verification (planned, not run):'))
        for plan in plans:
            lines.extend(f"  - {step.description}" for step in plan.verification_steps)
    actions = [item for item in ordered if item.repair_plan is None and item.recommended_actions]
    if actions:
        lines.append('Recommended actions:')
        for item in actions:
            lines.extend(f"  - {action}" for action in item.recommended_actions)
    lines.append('')
    for item in evidence:
        if item.kind == 'startup_probe':
            status = item.metadata.get('execution_status')
            if status == 'requires_confirmation':
                lines.append('[CAUTION] Startup probe available; project code execution requires confirmation.')
                lines.append(f"Entrypoint: {item.metadata['entrypoint']}")
                lines.append('Use --run-startup-probe to execute the supported probe.')
                lines.append('No startup probe was executed.')
            elif status == 'no_supported_entrypoint':
                lines.append('No supported startup entrypoint was detected.')
                lines.append('Current alpha supports only root-level main.py.')
            else:
                if status not in ('success', 'failed', 'timeout'):
                    lines.append('Startup probe is unavailable or was rejected by the safety policy.')
                if status in ('success', 'failed', 'timeout'):
                    if item.metadata.get('stderr_excerpt'):
                        excerpt = str(item.metadata['stderr_excerpt'])
                        import_already_shown = any(
                            observed.kind == 'python_import_failure'
                            and observed.metadata.get('raw_message')
                            and observed.metadata['raw_message'] in excerpt
                            for observed in evidence
                        )
                        if not import_already_shown:
                            lines.append('Startup stderr excerpt: ' + excerpt[:512])
                    if status == 'success':
                        lines.append('Startup probe completed successfully.')
                    if status == 'timeout':
                        stopped = ('The direct child was stopped after timeout.' if item.metadata.get('terminated')
                                   else 'Direct-child termination could not be confirmed.')
                        lines.append('This may be normal for a long-running application.')
                        lines.append(stopped)
                else:
                    lines.append('No startup probe was executed.')
        if item.kind == "python_requirement" and item.metadata.get("status") not in ("compatible", "incompatible"):
            lines.append("Python requirement check (tool limitation / metadata notice): " + item.summary)
        if item.kind == "local_python_environment":
            status = item.metadata.get("status")
            if status == "different":
                lines.append(f"Detected environment: {item.metadata['detected_local_environment']}")
                lines.append(f"Detected interpreter: {item.metadata['detected_interpreter_path']}")
            elif status in ("ambiguous", "unavailable"):
                lines.append("Local environment check (limitation): " + item.summary)
    if any(item.kind == 'startup_probe' and item.metadata.get('executed') is True for item in evidence):
        lines.append('RepoRescue itself does not modify project files. A startup probe may execute project code and project-defined side effects are possible.')
        lines.append('Repair preview only. Verification steps were not run; RepoRescue did not install dependencies.')
    else:
        lines.append("READ ONLY: Repair preview only. Verification steps were not run; no dependency changes were made.")
    lines.append("No repair actions were executed.")
    lines.append("RepoRescue currently performs limited checks.")
    return _terminal_paths(
        "\n".join(lines), project, environment, evidence,
        requested_project_path, cwd if cwd is not None else Path.cwd(),
    )


def write_json_report(
    report: Mapping[str, Any], output_path: str | Path | None,
) -> Path | None:
    """Write UTF-8 JSON only to an explicitly supplied, not-yet-existing path.

    Parent directories are not created and an existing path is never overwritten.
    Passing None is a no-op. Returns the created path for caller confirmation.
    """
    if output_path is None:
        return None

    path = Path(output_path)
    try:
        text = json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
        content = text.encode("utf-8")
    except (TypeError, ValueError, UnicodeError) as error:
        raise ReportWriteError(f"Could not serialize JSON report: {error}") from error

    try:
        with path.open("xb") as output:
            output.write(content)
    except FileExistsError as error:
        raise ReportWriteError(f"Report already exists; refusing to overwrite: {path}") from error
    except OSError as error:
        raise ReportWriteError(f"Could not write report to {path}: {error}") from error
    return path
