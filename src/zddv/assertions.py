from __future__ import annotations

import re
from typing import Any


_STRUCTURED_RE = re.compile(
    r"^\s*ZDDV_ASSERT\s+(?P<status>PASS|FAIL)\s+"
    r"(?P<name>[^\s:]+)(?:\s*::\s*(?P<message>.*))?\s*$",
    re.IGNORECASE,
)

_VERILATOR_RE = re.compile(
    r"^\s*(?:\[(?P<time>[^\]]+)\]\s*)?"
    r"(?:%Error(?:-[A-Za-z0-9_]+)?:\s*)?"
    r"(?:(?P<file>.+?):(?P<line>\d+)(?::(?P<column>\d+))?:\s*)?"
    r"Assertion failed(?: in (?P<scope>[^:]+))?:?\s*(?P<message>.*)$",
    re.IGNORECASE,
)


def parse_assertion_events(text: str) -> list[dict[str, Any]]:
    """Normalize native assertion failures and portable ZDDV markers."""
    events: list[dict[str, Any]] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue

        structured = _STRUCTURED_RE.match(line)
        if structured:
            events.append(
                {
                    "status": structured.group("status").upper(),
                    "property_name": structured.group("name"),
                    "scope": None,
                    "source_file": None,
                    "source_line": None,
                    "source_column": None,
                    "sim_time": None,
                    "message": (structured.group("message") or "").strip(),
                    "raw_text": raw_line,
                    "parser": "zddv-marker",
                }
            )
            continue

        native = _VERILATOR_RE.match(line)
        if native:
            events.append(
                {
                    "status": "FAIL",
                    "property_name": None,
                    "scope": (native.group("scope") or "").strip() or None,
                    "source_file": (native.group("file") or "").strip() or None,
                    "source_line": int(native.group("line")) if native.group("line") else None,
                    "source_column": int(native.group("column")) if native.group("column") else None,
                    "sim_time": (native.group("time") or "").strip() or None,
                    "message": (native.group("message") or "").strip(),
                    "raw_text": raw_line,
                    "parser": "verilator",
                }
            )
    return events


def assertion_summary(events: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "total": len(events),
        "passed": sum(event["status"] == "PASS" for event in events),
        "failed": sum(event["status"] == "FAIL" for event in events),
    }
