from __future__ import annotations

from collections import OrderedDict
import hashlib
from pathlib import Path
import re
from typing import Iterable


ANSI_ESCAPE = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")
NUMBER = re.compile(r"(?<![A-Za-z_])(?:0x[0-9A-Fa-f]+|\d+)(?![A-Za-z_])")
WHITESPACE = re.compile(r"\s+")

# Ordered from strongest to weakest so assertion/fatal messages win over generic FAIL text.
FAILURE_HINTS = (
    "assertion",
    "assert",
    "%error",
    "fatal",
    "error:",
    "uvm_error",
    "uvm_fatal",
    "fail",
)


def normalize_failure_line(line: str) -> str:
    """Normalize volatile values while keeping the semantic failure message readable."""
    cleaned = ANSI_ESCAPE.sub("", line).strip()
    cleaned = NUMBER.sub("#", cleaned)
    cleaned = WHITESPACE.sub(" ", cleaned)
    return cleaned


def _candidate_lines(text: str) -> list[str]:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return []

    chosen: list[str] = []
    for hint in FAILURE_HINTS:
        for line in lines:
            if hint in line.lower() and line not in chosen:
                chosen.append(line)
                if len(chosen) == 2:
                    return chosen
    return chosen or lines[-2:]


def failure_signature(log_path: str | Path, status: str) -> str | None:
    """Return a stable, human-readable signature for a non-passing run."""
    status = status.upper()
    if status == "PASS":
        return None
    if status == "TIMEOUT":
        return "TIMEOUT"

    path = Path(log_path)
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return f"{status}:log-unavailable"

    normalized = [normalize_failure_line(line) for line in _candidate_lines(text)]
    basis = " | ".join(line for line in normalized if line)
    if not basis:
        return f"{status}:empty-log"

    digest = hashlib.sha1(basis.encode("utf-8")).hexdigest()[:12]
    preview = basis[:180]
    return f"{status}:{digest}:{preview}"


def group_failure_records(records: Iterable[dict]) -> list[dict]:
    """Group regression run records by failure signature, preserving first-seen order."""
    groups: OrderedDict[str, dict] = OrderedDict()
    for record in records:
        status = str(record.get("status", "")).upper()
        if status == "PASS":
            continue

        signature = record.get("failure_signature")
        if not signature:
            signature = failure_signature(record.get("log", ""), status)
        signature = signature or f"{status}:unknown"

        group = groups.setdefault(
            signature,
            {
                "signature": signature,
                "status": status,
                "count": 0,
                "runs": [],
            },
        )
        group["count"] += 1
        group["runs"].append(
            {
                "run_id": record.get("run_id"),
                "test": record.get("test"),
                "seed": record.get("seed"),
                "log": record.get("log"),
            }
        )

    return list(groups.values())
