"""Thin coordination of the existing v0.1 diagnostic steps."""

from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from .commands import execute_command, execute_startup_probe
from .diagnosis import diagnose
from .models import (
    DetectionResult, DiagnosisResult, EnvironmentInfo, Evidence,
    ExecutionResult, ProjectInfo,
)
from .project import inspect_environment, scan_project
from .python_plugin import (
    detect_python_project, inspect_local_python_environment,
    inspect_python_requirement, propose_diagnostic_commands,
)
from .report import build_json_report, render_terminal_report, write_json_report
from .startup import collect_startup_evidence, propose_startup_probe


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
    *, confirm_startup: bool = False,
) -> WorkflowResult:
    """Diagnose once; write a report only when the caller supplies a path.

    Startup is proposal-only by default. A caller passing confirm_startup=True
    must first obtain informed approval for the exact proposal and its side effects.
    The CLI's --run-startup-probe flag supplies this explicit confirmation.
    """
    try:
        project = scan_project(project_path)
        detection = detect_python_project(project)
        inspected_environment = inspect_environment(project)
        local_environment = inspect_local_python_environment(project, inspected_environment)
        requirement_evidence = inspect_python_requirement(project, inspected_environment)
        proposals = propose_diagnostic_commands(project, inspected_environment)
        executions = tuple(execute_command(command, project.root_path) for command in proposals)
        startup_command, startup_observation = propose_startup_probe(project, inspected_environment)
        if startup_command is not None:
            startup_execution = execute_startup_probe(
                startup_command, project.root_path, confirmed=confirm_startup,
            )
            startup_observation = collect_startup_evidence(startup_observation, startup_execution)
            executions += (startup_execution,)
        diagnostics, evidence = diagnose(
            project, detection, inspected_environment, executions,
            python_requirement=requirement_evidence, local_environment=local_environment,
            startup_probe=startup_observation,
        )

        # Only the version probe can update interpreter launch validation.
        environment = inspected_environment
        for execution in executions:
            if execution.command.arguments != ('--version',):
                continue
            if execution.status == "success" and execution.exit_code == 0:
                environment = replace(environment, python_callable=True)
            elif execution.status in ("failed", "timeout"):
                environment = replace(environment, python_callable=False)

        report = build_json_report(project, detection, environment, diagnostics, evidence)
        terminal = render_terminal_report(
            project, detection, environment, diagnostics, evidence=evidence,
            requested_project_path=project_path,
        )
        saved_path = write_json_report(report, output_path)
    except (OSError, ValueError) as error:
        raise WorkflowError(str(error)) from error

    return WorkflowResult(
        project=project, detection=detection, environment=environment,
        execution_results=executions, evidence=tuple(evidence),
        diagnostics=tuple(diagnostics), report=report,
        terminal_report=terminal, output_path=saved_path,
    )
