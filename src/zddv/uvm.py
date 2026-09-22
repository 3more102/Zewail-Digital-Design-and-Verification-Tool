from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import re
from typing import Any
import uuid

from zddv.config import ProjectConfig
from zddv.storage import get_run_record, record_uvm_log_snapshot


_SEVERITIES = ("UVM_INFO", "UVM_WARNING", "UVM_ERROR", "UVM_FATAL")
_MESSAGE_RE = re.compile(
    r"^\s*(?:#\s*)?(?P<severity>UVM_(?:INFO|WARNING|ERROR|FATAL))\b(?P<body>.*)$"
)
_SUMMARY_COUNT_RE = re.compile(
    r"^\s*(?:#\s*)?(?P<severity>UVM_(?:INFO|WARNING|ERROR|FATAL))"
    r"\s*:\s*(?P<count>\d+)\s*$"
)
_REPORT_ID_RE = re.compile(r"\[(?P<report_id>[^\]]+)\]")
_TIME_RE = re.compile(r"@\s*(?P<time>[^:]+):\s*(?P<tail>.*)$")
_SOURCE_RE = re.compile(
    r"(?P<source>(?:[A-Za-z]:)?[^\s]+?\.(?:sv|svh|v|vh)\(\d+\))",
    re.IGNORECASE,
)
_RUNNING_TEST_RE = re.compile(
    r"\bRunning\s+test\s+(?P<test>[A-Za-z_][A-Za-z0-9_$:.-]*)",
    re.IGNORECASE,
)
_PHASE_TRACE_RE = re.compile(
    r"^Phase '(?P<phase>[^']+)' \(id=(?P<phase_id>\d+)\) (?P<detail>.*)$"
)
_PHASE_STATE_BY_REPORT_ID = {
    "PH/TRC/SCHEDULED": "SCHEDULED",
    "PH/TRC/STRT": "STARTED",
    "PH_READY_TO_END": "READY_TO_END",
    "PH_END": "ENDED",
    "PH/TRC/DONE": "DONE",
}
_OBJECTION_DIRECT_RE = re.compile(
    r"^Object (?P<object>\S+) (?P<action>raised|dropped) (?P<delta>\d+) "
    r"(?P<objection>.+?) objection\(s\)"
    r"(?: \((?P<description>.*?)\))?: "
    r"count=(?P<count>\d+)\s+total=(?P<total>\d+)$"
)
_OBJECTION_PROPAGATED_RE = re.compile(
    r"^Object (?P<object>\S+) (?P<verb>added|subtracted) (?P<delta>\d+) "
    r"(?P<objection>.+?) objection\(s\) (?P<direction>to|from) its total "
    r"\((?P<action>raised|dropped) from source object (?P<source>[^,)]+)"
    r"(?:, (?P<description>.*?))?\): "
    r"count=(?P<count>\d+)\s+total=(?P<total>\d+)$"
)


def _empty_counts() -> dict[str, int]:
    return {severity: 0 for severity in _SEVERITIES}


def _strip_simulator_prefix(line: str) -> str:
    text = line.strip()
    if text.startswith("#"):
        return text[1:].lstrip()
    return text


def _parse_message(
    line: str,
    *,
    line_number: int,
    event_index: int,
) -> dict[str, Any] | None:
    if _SUMMARY_COUNT_RE.match(line):
        return None

    match = _MESSAGE_RE.match(line)
    if not match:
        return None

    severity = match.group("severity")
    body = match.group("body").strip()

    source_match = _SOURCE_RE.search(body)
    source_location = source_match.group("source") if source_match else None

    time_match = _TIME_RE.search(body)
    time_text = time_match.group("time").strip() if time_match else None
    tail = time_match.group("tail").strip() if time_match else body

    id_match = _REPORT_ID_RE.search(tail)
    if id_match:
        report_id = id_match.group("report_id").strip() or None
        component = tail[: id_match.start()].strip() or None
        message = tail[id_match.end() :].strip() or None
    else:
        report_id = None
        component = None
        message = tail.strip() or None

    return {
        "event_index": event_index,
        "severity": severity,
        "report_id": report_id,
        "component": component,
        "message": message,
        "time": time_text,
        "source_location": source_location,
        "log_line": line_number,
        "raw": _strip_simulator_prefix(line),
    }


def _parse_phase_event(event: dict[str, Any]) -> dict[str, Any] | None:
    report_id = event.get("report_id")
    state = _PHASE_STATE_BY_REPORT_ID.get(str(report_id))
    message = event.get("message")
    if state is None or not isinstance(message, str):
        return None

    match = _PHASE_TRACE_RE.match(message)
    if not match:
        return None

    detail = match.group("detail").strip()
    return {
        "event_index": int(event["event_index"]),
        "report_id": report_id,
        "phase": match.group("phase"),
        "phase_id": int(match.group("phase_id")),
        "state": state,
        "premature": state == "ENDED" and "PREMATURELY" in detail.upper(),
        "time": event.get("time"),
        "log_line": int(event["log_line"]),
        "detail": detail,
    }


def _parse_objection_event(event: dict[str, Any]) -> dict[str, Any] | None:
    if event.get("report_id") != "OBJTN_TRC":
        return None
    message = event.get("message")
    if not isinstance(message, str):
        return None

    match = _OBJECTION_DIRECT_RE.match(message)
    propagated = False
    if match is None:
        match = _OBJECTION_PROPAGATED_RE.match(message)
        propagated = match is not None
    if match is None:
        return None

    groups = match.groupdict()
    return {
        "event_index": int(event["event_index"]),
        "action": groups["action"],
        "object": groups["object"],
        "source_object": (groups.get("source") or groups["object"]).strip(),
        "objection": groups["objection"].strip(),
        "delta": int(groups["delta"]),
        "count": int(groups["count"]),
        "total": int(groups["total"]),
        "description": (groups.get("description") or "").strip() or None,
        "propagated": propagated,
        "time": event.get("time"),
        "log_line": int(event["log_line"]),
    }


def _extract_lifecycle(messages: list[dict[str, Any]]) -> dict[str, Any]:
    phase_events: list[dict[str, Any]] = []
    objection_events: list[dict[str, Any]] = []

    for event in messages:
        phase_event = _parse_phase_event(event)
        if phase_event is not None:
            phase_events.append(phase_event)

        objection_event = _parse_objection_event(event)
        if objection_event is not None:
            objection_events.append(objection_event)

    return {
        "phase_trace_detected": any(
            event.get("report_id") in _PHASE_STATE_BY_REPORT_ID
            or str(event.get("report_id") or "").startswith("PH/TRC/")
            for event in messages
        ),
        "objection_trace_detected": any(
            event.get("report_id") == "OBJTN_TRC"
            for event in messages
        ),
        "phase_event_count": len(phase_events),
        "objection_event_count": len(objection_events),
        "objection_raise_events": sum(
            1 for event in objection_events if event["action"] == "raised"
        ),
        "objection_drop_events": sum(
            1 for event in objection_events if event["action"] == "dropped"
        ),
        "phases": sorted({event["phase"] for event in phase_events}),
        "phase_events": phase_events,
        "objection_events": objection_events,
    }


def parse_uvm_log_text(text: str, *, source: str = "uvm-log") -> dict[str, Any]:
    lines = text.splitlines()
    summary_header_index: int | None = None
    for index, line in enumerate(lines):
        if "UVM Report Summary" in _strip_simulator_prefix(line):
            summary_header_index = index

    messages: list[dict[str, Any]] = []
    observed_counts = _empty_counts()
    test_name: str | None = None

    for line_number, line in enumerate(lines, start=1):
        event = _parse_message(
            line,
            line_number=line_number,
            event_index=len(messages),
        )
        if event is None:
            continue
        messages.append(event)
        observed_counts[event["severity"]] += 1

        if event.get("report_id") == "RNTST" and event.get("message"):
            test_match = _RUNNING_TEST_RE.search(str(event["message"]))
            if test_match:
                test_name = test_match.group("test").rstrip(".")

    if test_name is None:
        for line in lines:
            test_match = _RUNNING_TEST_RE.search(_strip_simulator_prefix(line))
            if test_match:
                test_name = test_match.group("test").rstrip(".")
                break

    summary_counts: dict[str, int] = {}
    if summary_header_index is not None:
        for line in lines[summary_header_index + 1 :]:
            match = _SUMMARY_COUNT_RE.match(line)
            if match:
                summary_counts[match.group("severity")] = int(match.group("count"))

    summary_complete = all(name in summary_counts for name in _SEVERITIES)
    if summary_complete:
        selected_counts = {
            name: int(summary_counts[name])
            for name in _SEVERITIES
        }
        count_source = "uvm_report_summary"
    else:
        selected_counts = dict(observed_counts)
        count_source = "observed_messages"

    status = (
        "FAIL"
        if selected_counts["UVM_ERROR"] > 0 or selected_counts["UVM_FATAL"] > 0
        else "PASS"
    )
    lifecycle = _extract_lifecycle(messages)

    return {
        "analysis": "uvm_log",
        "source": source,
        "status": status,
        "test_name": test_name,
        "report_summary_detected": summary_header_index is not None,
        "report_summary_complete": summary_complete,
        "count_source": count_source,
        "classification_policy": {
            "pass": "UVM_ERROR == 0 and UVM_FATAL == 0",
            "warnings_fail": False,
        },
        "summary": {
            "infos": selected_counts["UVM_INFO"],
            "warnings": selected_counts["UVM_WARNING"],
            "errors": selected_counts["UVM_ERROR"],
            "fatals": selected_counts["UVM_FATAL"],
            "total_reports": sum(selected_counts.values()),
            "observed_messages": len(messages),
        },
        "severity_counts": selected_counts,
        "observed_severity_counts": observed_counts,
        "report_summary_counts": (
            {name: int(summary_counts.get(name, 0)) for name in _SEVERITIES}
            if summary_header_index is not None
            else None
        ),
        "messages": messages,
        "lifecycle": lifecycle,
        "limitations": [
            "This parser normalizes standard UVM report messages and the final severity summary without depending on a simulator vendor.",
            "The final complete UVM Report Summary is authoritative for severity counts when present; otherwise visible report messages are counted.",
            "Phase-state and objection activity are normalized when standard +UVM_PHASE_TRACE and +UVM_OBJECTION_TRACE report messages are present.",
            "Sequence and transaction lifecycle reconstruction are not yet modeled because no equivalent portable trace stream is assumed.",
            "When linked to a recorded ZDDV run, simulator status and return code are retained as separate evidence from the UVM severity verdict.",
        ],
    }


def parse_uvm_log(path: str | Path, *, source: str = "uvm-log") -> dict[str, Any]:
    input_path = Path(path)
    return parse_uvm_log_text(
        input_path.read_text(encoding="utf-8", errors="replace"),
        source=source,
    )


def analyze_uvm_log(
    project: ProjectConfig,
    path: str | Path | None,
    *,
    source: str | None = None,
    output: str | Path = ".zddv/uvm/latest.json",
    run_id: str | None = None,
) -> dict[str, Any]:
    run_record: dict[str, Any] | None = None
    if run_id is not None:
        run_record = get_run_record(project, run_id)
        if run_record is None:
            raise ValueError(f"Unknown run ID: {run_id}")

    if path is None:
        if run_record is None:
            raise ValueError("A UVM log path or --run must be provided")
        input_path = Path(run_record["log_path"])
    else:
        input_path = Path(path)
        if not input_path.is_absolute():
            input_path = project.root / input_path

    input_path = input_path.resolve()
    if not input_path.is_file():
        raise FileNotFoundError(input_path)

    report = parse_uvm_log(
        input_path,
        source=source or (
            str(run_record["simulator"])
            if run_record is not None
            else "uvm-log"
        ),
    )

    created_at = datetime.now(timezone.utc).isoformat()
    snapshot_id = (
        datetime.now(timezone.utc).strftime("uvm-%Y%m%dT%H%M%S")
        + "-"
        + uuid.uuid4().hex[:8]
    )

    snapshot_dir = (project.root / ".zddv" / "uvm" / "snapshots").resolve()
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    normalized_path = snapshot_dir / f"{snapshot_id}.json"

    destination = Path(output)
    if not destination.is_absolute():
        destination = project.root / destination
    destination = destination.resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)

    record = {
        "snapshot_id": snapshot_id,
        "created_at": created_at,
        "project": project.name,
        "input_path": str(input_path),
        **report,
    }
    if run_record is not None:
        record.update(
            {
                "run_id": run_record["run_id"],
                "run_status": run_record["status"],
                "run_returncode": int(run_record["returncode"]),
                "simulator": run_record["simulator"],
            }
        )
    record["normalized_path"] = str(normalized_path)
    record["report_path"] = str(destination)

    serialized = json.dumps(record, indent=2) + "\n"
    normalized_path.write_text(serialized, encoding="utf-8")
    destination.write_text(serialized, encoding="utf-8")
    record_uvm_log_snapshot(project, record)
    return record

