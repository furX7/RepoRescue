"""Core coordination of static extensions and existing controlled probes."""

from dataclasses import dataclass, replace
from pathlib import Path
import sys
from typing import Any

from .commands import execute_command, execute_startup_probe
from .extension_pipeline import (
    ExtensionFailureInfo, ExtensionRun, merge_detection, merge_results,
    prepare_extensions, run_extension_stage,
)
from .extensions import Capability, CompatibilityStatus, ExtensionEnvironment
from .models import (
    DetectionResult, DiagnosisResult, EnvironmentInfo, Evidence,
    ExecutionResult, ProjectInfo,
)
from .log_input import read_log
from .project import inspect_environment, scan_project
from .report import build_json_report, render_terminal_report, write_json_report
from .startup import collect_startup_evidence, propose_startup_probe


class WorkflowError(RuntimeError):
    """RepoRescue could not complete the requested diagnostic task."""

    def __init__(
        self, message: str, *, extension_failures: tuple[ExtensionFailureInfo, ...] = (),
    ) -> None:
        self.extension_failures = extension_failures
        super().__init__(message)


def _require_diagnosis_extension(runs: tuple[ExtensionRun, ...]) -> None:
    if any(not run.failures and Capability.DIAGNOSE in run.extension.metadata.capabilities
           for run in runs):
        return
    failures = merge_results(runs).failures
    reasons = "; ".join(
        f"{item.extension_id}: "
        f"{item.status.value if isinstance(item.status, CompatibilityStatus) else item.status}"
        for item in failures
    )
    raise WorkflowError(
        "No built-in diagnosis extension is available" + (f" ({reasons})" if reasons else ""),
        extension_failures=failures,
    )


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
    extension_failures: tuple[ExtensionFailureInfo, ...] = ()


def run_workflow(
    project_path: str | Path, output_path: str | Path | None = None,
    *, confirm_startup: bool = False,
    traceback_file: str | Path | None = None,
    install_log: str | Path | None = None,
) -> WorkflowResult:
    """Diagnose once; write a report only when the caller supplies a path.

    Startup is proposal-only by default. A caller passing confirm_startup=True
    must first obtain informed approval for the exact proposal and its side effects.
    The CLI's --run-startup-probe flag supplies this explicit confirmation.
    """
    try:
        if traceback_file is not None and install_log is not None:
            raise ValueError("Choose either traceback_file or install_log")
        provided_logs = ()
        if traceback_file is not None or install_log is not None:
            provided_logs = (read_log(
                traceback_file if traceback_file is not None else install_log,
                "traceback" if traceback_file is not None else "install_log",
            ),)
        project = scan_project(project_path)
        inspected_environment = inspect_environment(project)
        extension_environment = ExtensionEnvironment(
            platform={"win32": "windows", "darwin": "macos"}.get(sys.platform, sys.platform),
            available_tools=frozenset({"python"} if inspected_environment.python_available else ()),
        )
        runs = prepare_extensions(extension_environment)
        runs = run_extension_stage(runs, Capability.DETECT, project, inspected_environment)
        detection = merge_detection(runs)
        runs = run_extension_stage(runs, Capability.INSPECT, project, inspected_environment)
        _require_diagnosis_extension(runs)
        proposals = merge_results(runs).command_proposals
        executions = tuple(execute_command(command, project.root_path) for command in proposals)
        startup_command, startup_observation = propose_startup_probe(project, inspected_environment)
        if startup_command is not None:
            startup_execution = execute_startup_probe(
                startup_command, project.root_path, confirmed=confirm_startup,
            )
            startup_observation = collect_startup_evidence(startup_observation, startup_execution)
            executions += (startup_execution,)
        runs = run_extension_stage(
            runs, Capability.DIAGNOSE, project, inspected_environment,
            executions=executions, core_evidence=(startup_observation, *provided_logs),
        )
        _require_diagnosis_extension(runs)
        runs = run_extension_stage(runs, Capability.PLAN_REPAIR, project, inspected_environment)
        runs = run_extension_stage(runs, Capability.VERIFY, project, inspected_environment)
        extension_result = merge_results(runs)
        diagnostics, evidence = extension_result.diagnoses, extension_result.evidence

        # Only the version probe can update interpreter launch validation.
        environment = inspected_environment
        for execution in executions:
            if execution.command.arguments != ('--version',):
                continue
            if execution.status == "success" and execution.exit_code == 0:
                environment = replace(environment, python_callable=True)
            elif execution.status in ("failed", "timeout"):
                environment = replace(environment, python_callable=False)

        report = build_json_report(
            project, detection, environment, diagnostics, evidence,
            extension_failures=extension_result.failures,
        )
        terminal = render_terminal_report(
            project, detection, environment, diagnostics, evidence=evidence,
            extension_failures=extension_result.failures,
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
        extension_failures=extension_result.failures,
    )
