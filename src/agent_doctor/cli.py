"""Minimal command-line adapter for RepoRescue."""

import argparse
import sys
from collections.abc import Sequence

from . import __version__
from .workflow import WorkflowError, run_workflow


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="repo-rescue", description="Limited, read-only Python project diagnosis (Windows First).",
    )
    parser.add_argument("project_path", help="Project directory to inspect")
    parser.add_argument("--output", help="Create a JSON report at this path; never overwrite")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    arguments = parser.parse_args(argv)
    try:
        result = run_workflow(arguments.project_path, arguments.output)
        print(result.terminal_report)
        if result.output_path is not None:
            print(f"JSON report saved: {result.output_path}")
    except WorkflowError as error:
        print(f"RepoRescue error: {error}", file=sys.stderr)
        return 2
    except Exception as error:
        print(f"RepoRescue internal error: {error}", file=sys.stderr)
        return 2

    return 1 if any(item.severity in ("ERROR", "CRITICAL") for item in result.diagnostics) else 0


if __name__ == "__main__":
    raise SystemExit(main())
