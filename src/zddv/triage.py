from __future__ import annotations

from collections import OrderedDict
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Iterable


ANSI_ESCAPE = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")
FILE_LOCATION = re.compile(
    r"(?P<file>[^\s:]+\.(?:sv|svh|v|vh|cpp|cc|c|h)):(?:\d+)(?::\d+)?",
    re.IGNORECASE,
)
HEX_VALUE = re.compile(r"\b0x[0-9A-Fa-f]+\b")
TIME_LITERAL = re.compile(r"\b\d+(?:\.\d+)?\s*(?:fs|ps|ns|us|ms|s)\b", re.IGNORECASE)
VOLATILE_VALUE = re.compile(
    r"\b(seed|cycle|time|iteration|txn|transaction|expected|actual|got)"
    r"(\s*(?:=|:)?\s*)(?:0x[0-9A-Fa-f]+|\d+)",
    re.IGNORECASE,
)
WHITESPACE = re.compile(r"\s+")

FAILURE_HINTS = (
    "uvm_fatal",
    "uvm_error",
    "assertion",
    "assert",
    "%error",
    "fatal",
    "error:",
    "mismatch",
    "fail",
)


def normalize_failure_line(line: str) -> str:
    """Remove run-specific values while keeping the failure meaning readable."""
    cleaned = ANSI_ESCAPE.sub("", line).strip()
    cleaned = FILE_LOCATION.sub(lambda m: f"{m.group('file')}:#", cleaned)
    cleaned = HEX_VALUE.sub("0x#", cleaned)
    cleaned = TIME_LITERAL.sub("#time", cleaned)
    cleaned = VOLATILE_VALUE.sub(lambda m: f"{m.group(1)}{m.group(2)}#", cleaned)
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
    """Build a stable signature for FAIL/TIMEOUT results from the simulation log."""
    normalized_status = status.upper()
    if normalized_status == "PASS":
        return None
    if normalized_status == "TIMEOUT":
        return "TIMEOUT"

    path = Path(log_path)
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return f"{normalized_status}:log-unavailable"

    normalized = [normalize_failure_line(line) for line in _candidate_lines(text)]
    basis = " | ".join(line for line in normalized if line)
    if not basis:
        return f"{normalized_status}:empty-log"

    digest = hashlib.sha1(basis.encode("utf-8")).hexdigest()[:12]
    return f"{normalized_status}:{digest}:{basis[:180]}"


def group_failure_records(records: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Cluster non-passing run records by normalized log signature."""
    groups: OrderedDict[str, dict[str, Any]] = OrderedDict()

    for record in records:
        status = str(record.get("status", "")).upper()
        if status == "PASS":
            continue

        signature = record.get("failure_signature")
        if not signature:
            signature = failure_signature(record.get("log_path", ""), status)
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
                "test": record.get("test_name"),
                "seed": record.get("seed"),
                "log": record.get("log_path"),
            }
        )

    return sorted(groups.values(), key=lambda group: (-group["count"], group["signature"]))


def build_triage_report(records: Iterable[dict[str, Any]]) -> dict[str, Any]:
    selected = [record for record in records if str(record.get("status", "")).upper() != "PASS"]
    groups = group_failure_records(selected)
    return {
        "runs": len(selected),
        "groups": len(groups),
        "failure_groups": groups,
    }


def write_triage_report(records: Iterable[dict[str, Any]], output: str | Path) -> Path:
    path = Path(output).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(build_triage_report(records), indent=2), encoding="utf-8")
    return path
