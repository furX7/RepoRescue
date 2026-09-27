"""Bounded, read-only text intake. Log text never becomes a command proposal."""

from pathlib import Path
import re
import sys

from .models import Evidence
from .compatibility_evidence import extract_wheel_log_facts
from .version_provenance import extract_resolved_versions


MAX_INPUT_BYTES = 256 * 1024


def ingestion_limitation(source: str, location: str | None, input_type: str, reason: str) -> Evidence:
    """Evidence of a tool limitation, never evidence of a logged failure."""
    return Evidence(
        evidence_id="input:log", kind="ingestion_limitation", source=source,
        location=location, summary=f"Log ingestion incomplete: {reason}.",
        metadata={"input_type": input_type, "status": reason},
    )


def read_log(path: str | Path, input_type: str) -> Evidence:
    """Read one UTF-8 regular file or stdin ('-'), rejecting excess bytes."""
    if input_type not in ("traceback", "install_log"):
        raise ValueError("Unsupported log input type")
    source = "stdin" if str(path) == "-" else "user_file"
    location = None if source == "stdin" else str(Path(path).absolute())
    def failed(reason: str) -> Evidence:
        return ingestion_limitation(source, location, input_type, reason)
    try:
        if source == "stdin":
            stream = getattr(sys.stdin, "buffer", sys.stdin)
            data = stream.read(MAX_INPUT_BYTES + 1)
            if isinstance(data, str):
                data = data.encode("utf-8")
        else:
            file = Path(location)
            if file.is_symlink() or file.is_junction():
                return failed("not_regular_file")
            if not file.exists():
                return failed("missing_file")
            if not file.is_file():
                return failed("not_regular_file")
            with file.open("rb") as stream:
                data = stream.read(MAX_INPUT_BYTES + 1)
    except PermissionError:
        return failed("permission_denied")
    except OSError:
        return failed("read_error")
    except UnicodeError:
        return failed("invalid_utf8")
    if len(data) > MAX_INPUT_BYTES:
        return failed("oversized_input")
    try:
        text = data.decode("utf-8-sig")
    except UnicodeError:
        return failed("invalid_utf8")
    if not text.strip():
        return failed("empty")
    return Evidence(
        evidence_id="input:log", kind="provided_log", source=source,
        location=location,
        summary=f"User-provided {input_type}; current project execution is not verified by this text.",
        metadata={"input_type": input_type, "size_bytes": len(data), "text": text},
    )


def extract_install_facts(text: str) -> dict:
    """Keep only explicit pip facts; do not infer Python/artifact compatibility."""
    messages, patterns = [], []
    for line in text.splitlines():
        line = line.strip()
        message = re.sub(r"^ERROR:\s*", "", line)
        if message.startswith("No matching distribution found for "):
            pattern = "no_matching_distribution"
        elif message.startswith("Could not find a version that satisfies the requirement "):
            pattern = "unsatisfied_requirement"
        elif message.startswith("Ignored the following versions that require a different python version:"):
            pattern = "ignored_python_versions"
        elif message.startswith("Requires-Python:"):
            pattern = "requires_python"
        else:
            continue
        messages.append(line)
        patterns.append(pattern)
    return {"messages": tuple(messages), "patterns": tuple(patterns),
            "resolved_versions": extract_resolved_versions(text), **extract_wheel_log_facts(text)}
