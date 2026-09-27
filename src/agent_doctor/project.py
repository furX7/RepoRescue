"""Read-only, bounded project discovery without reading file contents."""

import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from .models import EnvironmentInfo, ProjectInfo
from .version_provenance import is_source


PYTHON_MANIFESTS = frozenset({
    "pyproject.toml", "requirements.txt", "setup.py", "setup.cfg",
})
EXCLUDED_DIRECTORIES = frozenset({
    ".git", ".venv", "venv", "node_modules", "__pycache__",
})


def scan_project(path: str | Path, *, max_entries: int = 1000) -> ProjectInfo:
    """Scan the root and direct child directories for Python marker files.

    Return sorted paths relative to a normalized, absolute project root.
    Do not follow child symlinks or Windows junctions. Propagate filesystem
    errors rather than reporting an unreadable directory as an empty project.
    Raise ValueError if the entry budget is exceeded, avoiding a silently
    incomplete snapshot. The budget includes directories and unrelated files.
    """
    if max_entries < 1:
        raise ValueError("max_entries must be positive")

    root = Path(path).expanduser().resolve(strict=True)
    if not root.is_dir():
        raise NotADirectoryError(f"Project path is not a directory: {root}")
    if root.name.casefold() in {".venv", "venv", "__pycache__"}:
        raise ValueError(f"Virtual environment or cache is not a project root: {root}")

    scanned_at = datetime.now(timezone.utc)
    files: list[str] = []
    manifests: list[str] = []
    directories = [(root, 0)]
    visited_entries = 0

    # Appending only at depth zero guarantees a maximum of one child level.
    for directory, depth in directories:
        with os.scandir(directory) as entries:
            for entry in entries:
                visited_entries += 1
                if visited_entries > max_entries:
                    raise ValueError(f"Project scan exceeded {max_entries} entries")
                entry_path = Path(entry.path)
                if entry.is_symlink() or entry_path.is_junction():
                    continue
                if entry.is_dir(follow_symlinks=False):
                    if depth == 0 and entry.name.casefold() not in EXCLUDED_DIRECTORIES:
                        directories.append((entry_path, 1))
                    continue
                if not entry.is_file(follow_symlinks=False):
                    continue

                name = entry.name.casefold()
                is_manifest = name in PYTHON_MANIFESTS
                if is_manifest or is_source(entry.name) or entry_path.suffix.casefold() == ".py":
                    relative_path = entry_path.relative_to(root).as_posix()
                    files.append(relative_path)
                    if is_manifest:
                        manifests.append(relative_path)

    return ProjectInfo(
        root_path=root,
        scanned_at=scanned_at,
        files=tuple(sorted(files)),
        manifests=tuple(sorted(manifests)),
    )


def inspect_environment(project: ProjectInfo) -> EnvironmentInfo:
    """Inspect current Python metadata and scanned manifest names only.

    No process is launched, environment variable changed, or file content read.
    Runtime access is confined here rather than to the workflow or plugin.
    """
    executable = Path(sys.executable) if sys.executable else None
    try:
        available = executable is not None and executable.is_file()
    except OSError:
        available = False

    version = ".".join(str(part) for part in sys.version_info[:3])
    releaselevel = getattr(sys.version_info, "releaselevel", "final")
    if releaselevel != "final":
        suffix = {"alpha": "a", "beta": "b", "candidate": "rc"}.get(releaselevel, releaselevel)
        version += suffix + str(getattr(sys.version_info, "serial", 0))

    return EnvironmentInfo(
        python_executable=executable,
        python_version=version,
        python_available=available,
        dependency_manifests=tuple(sorted({
            filename for filename in project.manifests
            if Path(filename).name.casefold() in PYTHON_MANIFESTS
        })),
    )
