"""Python filename detection, bounded requirement inspection, and proposals."""

import re
import tomllib
from pathlib import PurePosixPath

from .models import CommandProposal, DetectionResult, EnvironmentInfo, Evidence, ProjectInfo
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


def compare_python_requirement(requirement: str, current_version: str) -> str:
    """Compare final major.minor[.patch] releases, not general PEP 440 versions.

    Support >=, >, <=, <, ==, != and comma conjunctions; ==/!= additionally
    accept major.minor[.patch].*. Validate ALL clauses before comparing.
    Unknown syntax or non-final current versions never establish compatibility.
    """
    version_pattern = r"[0-9]{1,4}(?:\.[0-9]{1,4}){1,2}"
    clauses = []
    for clause in requirement.split(","):
        match = re.fullmatch(r"\s*(>=|<=|==|!=|>|<)\s*(" + version_pattern + r")(\.\*)?\s*", clause)
        if match is None:
            return "unsupported_requirement"
        operator, release, wildcard = match.groups()
        if wildcard and operator not in ("==", "!="):
            return "unsupported_requirement"
        target = tuple(int(part) for part in release.split("."))
        clauses.append((operator, target, bool(wildcard)))

    if re.fullmatch(version_pattern, current_version) is None:
        return "unsupported_current_version"
    current = tuple(int(part) for part in current_version.split("."))
    current += (0,) * (3 - len(current))
    for operator, target, wildcard in clauses:
        if wildcard:
            equal = current[:len(target)] == target
            satisfied = equal if operator == "==" else not equal
        else:
            target += (0,) * (3 - len(target))
            satisfied = {
                ">=": current >= target, ">": current > target,
                "<=": current <= target, "<": current < target,
                "==": current == target, "!=": current != target,
            }[operator]
        if not satisfied:
            return "incompatible"
    return "compatible"


def inspect_python_requirement(project: ProjectInfo, environment: EnvironmentInfo) -> Evidence | None:
    """Read only the scanned root pyproject.toml, bounded to 64 KiB.

    Missing declarations are silent. Read/format/unsupported cases are structured
    notices, not evidence of a Python version mismatch. Never expose TOML contents
    or parser exceptions in reports, and do not follow replaced symlinks/junctions.
    """
    filename = next((
        name for name in project.files
        if len(PurePosixPath(name).parts) == 1
        and PurePosixPath(name).name.casefold() == "pyproject.toml"
    ), None)
    if filename is None:
        return None
    path = project.root_path / filename

    def observation(status: str, summary: str, requirement: str | None = None) -> Evidence:
        return Evidence(
            evidence_id="project:python_requirement", kind="python_requirement",
            source="pyproject.toml", summary=summary, location=str(path),
            metadata={
                "declared_python_requirement": requirement,
                "current_python_version": environment.python_version,
                "status": status,
            },
        )

    try:
        if (path.is_symlink() or path.is_junction()
                or path.resolve(strict=True).parent != project.root_path or not path.is_file()):
            return observation("unreadable_metadata", "The root Python requirement metadata could not be safely read.")
        with path.open("rb") as stream:
            content = stream.read(65536 + 1)
    except OSError:
        return observation("unreadable_metadata", "The root Python requirement metadata could not be safely read.")
    if len(content) > 65536:
        return observation("metadata_too_large", "Python requirement inspection skipped: pyproject.toml exceeds the 64 KiB read limit.")
    try:
        document = tomllib.loads(content.decode("utf-8"))
    except (ValueError, RecursionError):
        return observation("invalid_metadata", "Python requirement inspection unavailable: pyproject.toml is not valid UTF-8 TOML.")
    table = document.get("project", {})
    if not isinstance(table, dict):
        return observation("invalid_metadata", "Python requirement inspection unavailable: project metadata must be a table.")
    if "requires-python" not in table:
        return None
    requirement = table["requires-python"]
    if not isinstance(requirement, str):
        return observation("invalid_metadata", "Python requirement inspection unavailable: requires-python must be a string.")

    status = compare_python_requirement(requirement, environment.python_version)
    summaries = {
        "compatible": "The current interpreter satisfies the supported Python requirement.",
        "incompatible": "The current interpreter does not satisfy the declared Python requirement.",
        "unsupported_requirement": "Requirement syntax is not supported by the current v0.2-alpha parser.",
        "unsupported_current_version": "Current Python version syntax is not supported by the current v0.2-alpha parser.",
    }
    return observation(status, summaries[status], requirement)
