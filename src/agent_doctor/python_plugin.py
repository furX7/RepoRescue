"""Python detection and command proposals; never execute commands."""

from pathlib import PurePosixPath

from .models import CommandProposal, DetectionResult, EnvironmentInfo, ProjectInfo
from .project import PYTHON_MANIFESTS


def detect_python_project(project: ProjectInfo) -> DetectionResult:
    """Recognize common filenames in the scanned snapshot, not file contents.

    This is a lightweight indication, not proof of a runnable Python project.
    For example, a pyproject.toml used only for tool configuration still matches.
    """
    matched_files = tuple(
        filename for filename in project.files
        if PurePosixPath(filename).name.casefold() in PYTHON_MANIFESTS
        or PurePosixPath(filename).suffix.casefold() == ".py"
    )
    return DetectionResult(
        level="likely" if matched_files else "unknown",
        matched_files=matched_files,
    )


def propose_diagnostic_commands(
    project: ProjectInfo, environment: EnvironmentInfo,
) -> tuple[CommandProposal, ...]:
    """Propose a version probe only for a likely project and available runtime.

    SAFE is a proposed risk, not execution authorization. compileall is omitted
    because it writes bytecode; test commands wait for actual framework detection.
    """
    if (
        detect_python_project(project).level != "likely"
        or not environment.python_available
        or environment.python_executable is None
    ):
        return ()

    return (CommandProposal(
        executable=str(environment.python_executable),
        arguments=("--version",),
        working_directory=project.root_path,
        source="python_plugin",
        reason="Verify that the current Python executable can start and report its version",
        risk="SAFE",
    ),)
