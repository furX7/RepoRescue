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
    logs = parser.add_mutually_exclusive_group()
    logs.add_argument("--traceback-file", help="Read a UTF-8 Python traceback (max 256 KiB); '-' reads stdin")
    logs.add_argument("--install-log", help="Read a UTF-8 pip failure log (max 256 KiB); '-' reads stdin")
    parser.add_argument(
        "--run-startup-probe", action="store_true",
        help="Execute the supported project startup probe. This runs project code "
             "and may have project-defined side effects.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    arguments = parser.parse_args(argv)
    try:
        if arguments.run_startup_probe:
            print("[CAUTION] Executing project code for the startup probe.", flush=True)
            print("Project code may have its own side effects.", flush=True)
        log_options = {}
        if arguments.traceback_file is not None:
            log_options["traceback_file"] = arguments.traceback_file
        if arguments.install_log is not None:
            log_options["install_log"] = arguments.install_log
        result = run_workflow(
            arguments.project_path, arguments.output,
            confirm_startup=arguments.run_startup_probe,
            **log_options,
        )
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
