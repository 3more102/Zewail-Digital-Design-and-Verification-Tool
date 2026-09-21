from __future__ import annotations

import re
from typing import Any


_ASSERTION_RE = re.compile(
    r"^(?:\\[(?P<time>[^\\]]+)\\]\\s+)?"
    r"%(?P<severity>Error|Fatal|Warning):\\s+"
    r"(?P<source>.*?):(?P<line>\\d+)(?::(?P<column>\\d+))?:\\s+"
    r"Assertion failed"
    r"(?: in (?P<scope>[^:]+))?"
    r"(?::\\s*(?P<message>.*))?$"
)


def parse_verilator_assertions(text: str) -> list[dict[str, Any]]:
    """Parse runtime assertion diagnostics emitted by Verilator.

    Verilator commonly emits assertion failures in the form::

        [42] %Error: top.sv:17: Assertion failed in TOP.tb.a_ready: message

    Continuation lines that are indented are folded into the event message.
    """
    lines = text.splitlines()
    events: list[dict[str, Any]] = []
    i = 0

    while i < len(lines):
        match = _ASSERTION_RE.match(lines[i].rstrip())
        if match is None:
            i += 1
            continue

        message = (match.group("message") or "").strip()
        continuation: list[str] = []
        j = i + 1
        while j < len(lines):
            line = lines[j]
            if not line.strip():
                break
            if _ASSERTION_RE.match(line.rstrip()):
                break
            if re.match(r"^\\s*%(?:Error|Fatal|Warning):", line):
                break
            if line[:1].isspace():
                continuation.append(line.strip())
                j += 1
                continue
            break

        if continuation:
            message = " ".join(part for part in [message, *continuation] if part)

        scope = (match.group("scope") or "").strip() or None
        assertion_name = scope.rsplit(".", 1)[-1] if scope else None
        column = match.group("column")

        events.append(
            {
                "event_index": len(events),
                "time": (match.group("time") or "").strip() or None,
                "severity": match.group("severity").upper(),
                "source_path": match.group("source").strip(),
                "source_line": int(match.group("line")),
                "source_column": int(column) if column is not None else None,
                "scope": scope,
                "assertion_name": assertion_name,
                "message": message,
            }
        )
        i = j

    return events
