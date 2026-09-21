from __future__ import annotations

from pathlib import Path
import re

from zddv.config import ProjectConfig
from zddv.storage import record_assertion_events


_ASSERTION_MARKER = re.compile(
    r"^ZDDV_ASSERT\s+(?P<name>\S+)\s+(?P<status>PASS|FAIL)"
    r"(?:\s+(?P<message>.*))?$"
)


def parse_assertion_log(path: str | Path) -> list[dict]:
    source = Path(path)
    events: list[dict] = []
    for line_number, raw_line in enumerate(
        source.read_text(encoding="utf-8", errors="replace").splitlines(),
        start=1,
    ):
        match = _ASSERTION_MARKER.match(raw_line.strip())
        if not match:
            continue
        message = (match.group("message") or "").strip()
        events.append(
            {
                "event_index": len(events),
                "assertion_name": match.group("name"),
                "status": match.group("status"),
                "message": message or None,
                "log_line": line_number,
            }
        )
    return events


def ingest_assertion_log(
    project: ProjectConfig,
    *,
    run_id: str,
    log_path: str | Path,
    created_at: str,
) -> list[dict]:
    source = Path(log_path)
    events = parse_assertion_log(source)
    if not events:
        return []

    normalized = [
        {
            **event,
            "run_id": run_id,
            "created_at": created_at,
            "log_path": str(source),
        }
        for event in events
    ]
    record_assertion_events(project, normalized)
    return normalized
