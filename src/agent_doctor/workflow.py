"""Thin coordination of the existing v0.1 diagnostic steps."""

from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from .commands import execute_command
from .diagnosis import diagnose
from .models import (
    DetectionResult, DiagnosisResult, EnvironmentInfo, Evidence,
    ExecutionResult, ProjectInfo,
)
from .project import inspect_environment, scan_project
from .python_plugin import detect_python_project, propose_diagnostic_commands
from .report import build_json_report, render_terminal_report, write_json_report


class WorkflowError(RuntimeError):
    """RepoRescue could not complete the requested diagnostic task."""


@dataclass(frozen=True)
class WorkflowResult:
    project: ProjectInfo
    detection: DetectionResult
    environment: EnvironmentInfo
    execution_results: tuple[ExecutionResult, ...]
    evidence: tuple[Evidence, ...]
    diagnostics: tuple[DiagnosisResult, ...]
    report: dict[str, Any]
    terminal_report: str
    output_path: Path | None


def run_workflow(
    project_path: str | Path, output_path: str | Path | None = None,
) -> WorkflowResult:
    """Diagnose once; write a report only when the caller supplies a path.

    Policy decisions belong entirely to execute_command. CAUTION and DANGEROUS
    results are retained without an approval bypass. Inspection evidence captures
    the pre-launch snapshot; execution evidence captures the actual launch outcome.
    """
    try:
        project = scan_project(project_path)
        detection = detect_python_project(project)
        inspected_environment = inspect_environment(project)
        proposals = propose_diagnostic_commands(project, inspected_environment)
        executions = tuple(execute_command(command, project.root_path) for command in proposals)
        diagnostics, evidence = diagnose(project, detection, inspected_environment, executions)

        # The current plugin proposes at most one operation: Python --version.
        # Derive presentation state from its actual result, never from log text.
        environment = inspected_environment
        for execution in executions:
            if execution.status == "success" and execution.exit_code == 0:
                environment = replace(environment, python_callable=True)
            elif execution.status in ("failed", "timeout"):
                environment = replace(environment, python_callable=False)

        report = build_json_report(project, detection, environment, diagnostics, evidence)
        terminal = render_terminal_report(project, detection, environment, diagnostics)
        saved_path = write_json_report(report, output_path)
    except (OSError, ValueError) as error:
        raise WorkflowError(str(error)) from error

    return WorkflowResult(
        project=project, detection=detection, environment=environment,
        execution_results=executions, evidence=tuple(evidence),
        diagnostics=tuple(diagnostics), report=report,
        terminal_report=terminal, output_path=saved_path,
    )
