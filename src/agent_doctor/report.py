"""Render supplied diagnostic data as a terminal summary or JSON report.

This module does not inspect projects, run commands, or alter diagnosis results.
"""

import json
from dataclasses import fields, is_dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
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


def render_terminal_report(
    project: ProjectInfo,
    detection: DetectionResult,
    environment: EnvironmentInfo,
    diagnostics: Sequence[DiagnosisResult],
    *, evidence: Sequence[Evidence] = (),
) -> str:
    """Render a concise summary without expanding evidence bodies."""
    if environment.python_available:
        availability = "available"
    else:
        availability = "unavailable"
    version = environment.python_version or "unknown version"
    executable = str(environment.python_executable) if environment.python_executable else "not found"
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
        f"Python Environment: {availability}; Python {version}; {launch}; executable: {executable}",
        "Diagnostics:",
    ]
    if diagnostics:
        for diagnosis in diagnostics:
            lines.append(
                f"- [{diagnosis.severity}] {diagnosis.category}: {diagnosis.problem}"
            )
            if diagnosis.recommended_actions:
                lines.append("  Recommended actions:")
                lines.extend(f"  - {action}" for action in diagnosis.recommended_actions)
            if diagnosis.evidence_refs:
                lines.append(f"  Evidence: {', '.join(diagnosis.evidence_refs)}")
            if diagnosis.root_cause_chain:
                lines.append("  Root cause: " + " -> ".join(step.title for step in diagnosis.root_cause_chain))
            if diagnosis.repair_plan is not None:
                lines.append(f"  Suggested repair (preview only, {diagnosis.repair_plan.risk}): {diagnosis.repair_plan.summary}")
                lines.append("  Verification (planned, not run): " + "; ".join(
                    step.description.rstrip('.') for step in diagnosis.repair_plan.verification_steps
                ))
    else:
        lines.append("- No problems detected by the current checks.")
    for item in evidence:
        if item.kind == 'startup_probe':
            status = item.metadata.get('execution_status')
            if status == 'requires_confirmation':
                lines.append('[CAUTION] Startup probe available; project code execution requires confirmation.')
                lines.append(f"Entrypoint: {item.metadata['entrypoint']}")
                lines.append(f"Command argv: {item.metadata['argv']}")
                lines.append(f"Working directory: {item.metadata['cwd']}")
                lines.append('Risk: CAUTION; reason: project code may write files, use the network, start services, or block.')
                lines.append('No startup probe was executed.')
            elif status == 'no_supported_entrypoint':
                lines.append('No supported startup entrypoint was detected; only root-level main.py is supported.')
            else:
                lines.append(f'Startup probe: {status}; entrypoint: {item.metadata["entrypoint"]}')
                if status in ('success', 'failed', 'timeout'):
                    lines.append(f"Observation window: {item.metadata['timeout_seconds']} seconds; exit code: {item.metadata['exit_code']}")
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
                        lines.append('The supported startup probe completed successfully; this does not establish project health.')
                    if status == 'timeout':
                        stopped = ('The direct child was stopped after timeout.' if item.metadata.get('terminated')
                                   else 'Direct-child termination could not be confirmed.')
                        lines.append(stopped + ' This does not prove that the application failed to start.')
                else:
                    lines.append('No startup probe was executed.')
        if item.kind == "python_import_failure":
            if item.metadata.get("status") == "missing_module":
                lines.append(f"Missing import: {item.metadata['missing_module']}")
            elif item.metadata.get("status") == "symbol_import_failure":
                lines.append(f"Import module: {item.metadata['source_module']}")
                lines.append(f"Import symbol: {item.metadata['imported_symbol']}")
            lines.append(f"Import evidence: {item.metadata['raw_message']}")
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
    lines.append("RepoRescue currently performs limited checks.")
    return "\n".join(lines)


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
