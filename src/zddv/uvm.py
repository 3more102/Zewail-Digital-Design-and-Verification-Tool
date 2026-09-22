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

_PHASE_MESSAGE_RE = re.compile(
    r"\bPhase\s+['‘’\"](?P<phase>[^'‘’\"]+)['‘’\"]\s*"
    r"(?:\(id=(?P<phase_id>\d+)\))?\s*(?P<detail>.*)$",
    re.IGNORECASE,
)
_OBJECTION_MESSAGE_RE = re.compile(
    r"\bObject\s+(?P<object>\S+)\s+"
    r"(?P<action>raised|dropped|added|subtracted|all_dropped)\s+"
    r"(?P<count>\d+)\s+objection\(s\)(?P<context>.*?)"
    r":\s*count=(?P<object_count>-?\d+)\s+total=(?P<total>-?\d+)",
    re.IGNORECASE,
)
_PHASE_ACTIONS = {
    "STRT": "START",
    "DONE": "DONE",
}
_OBJECTION_ACTIONS = {
    "RAISED": "RAISE",
    "DROPPED": "DROP",
    "ADDED": "ADD",
    "SUBTRACTED": "SUBTRACT",
    "ALL_DROPPED": "ALL_DROPPED",
}


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



def _extract_lifecycle_events(
    messages: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []

    for message in messages:
        report_id = str(message.get("report_id") or "")
        text = str(message.get("message") or "")
        component = message.get("component")

        if report_id.startswith("PH/TRC/"):
            match = _PHASE_MESSAGE_RE.search(text)
            if match:
                raw_action = report_id.removeprefix("PH/TRC/")
                metadata: dict[str, Any] = {
                    "trace_action": raw_action,
                }
                if match.group("phase_id"):
                    metadata["phase_id"] = int(match.group("phase_id"))
                detail = match.group("detail").strip()
                if detail:
                    metadata["detail"] = detail
                events.append(
                    {
                        "event_index": len(events),
                        "kind": "phase",
                        "action": _PHASE_ACTIONS.get(raw_action, raw_action),
                        "name": match.group("phase"),
                        "component": component,
                        "time": message.get("time"),
                        "report_id": report_id,
                        "description": detail or None,
                        "count": None,
                        "total": None,
                        "log_line": int(message["log_line"]),
                        "raw": message["raw"],
                        "metadata": metadata,
                    }
                )

        if report_id == "OBJTN_TRC":
            match = _OBJECTION_MESSAGE_RE.search(text)
            if match:
                context = match.group("context").strip()
                description: str | None = None
                if "(" in context and ")" in context:
                    description = context[context.find("(") + 1 : context.rfind(")")].strip() or None
                action = match.group("action").upper()
                events.append(
                    {
                        "event_index": len(events),
                        "kind": "objection",
                        "action": _OBJECTION_ACTIONS.get(action, action),
                        "name": match.group("object"),
                        "component": component,
                        "time": message.get("time"),
                        "report_id": report_id,
                        "description": description,
                        "count": int(match.group("object_count")),
                        "total": int(match.group("total")),
                        "log_line": int(message["log_line"]),
                        "raw": message["raw"],
                        "metadata": {
                            "delta": int(match.group("count")),
                            "context": context or None,
                        },
                    }
                )

        if isinstance(component, str) and "@@" in component:
            sequencer, sequence = component.split("@@", 1)
            if sequence:
                events.append(
                    {
                        "event_index": len(events),
                        "kind": "sequence",
                        "action": "REPORT",
                        "name": sequence,
                        "component": component,
                        "time": message.get("time"),
                        "report_id": report_id or None,
                        "description": text or None,
                        "count": None,
                        "total": None,
                        "log_line": int(message["log_line"]),
                        "raw": message["raw"],
                        "metadata": {
                            "sequencer": sequencer or None,
                            "evidence": "uvm_report_context",
                        },
                    }
                )

    return events


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

    lifecycle_events = _extract_lifecycle_events(messages)
    lifecycle_summary = {
        "phase_events": sum(1 for item in lifecycle_events if item["kind"] == "phase"),
        "objection_events": sum(1 for item in lifecycle_events if item["kind"] == "objection"),
        "sequence_events": sum(1 for item in lifecycle_events if item["kind"] == "sequence"),
    }
    lifecycle_summary["total_events"] = sum(lifecycle_summary.values())

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
        "lifecycle_summary": lifecycle_summary,
        "lifecycle_events": lifecycle_events,
        "limitations": [
            "This parser normalizes standard UVM report messages and the final severity summary without depending on a simulator vendor.",
            "The final complete UVM Report Summary is authoritative for severity counts when present; otherwise visible report messages are counted.",
            "Phase events require UVM phase-trace reports such as those enabled by +UVM_PHASE_TRACE; objection events require objection-trace reports such as those enabled by +UVM_OBJECTION_TRACE.",
            "Standard UVM has no universal sequence-trace plusarg; sequence activity is therefore recorded only when an explicit @@ sequence context is already present in a report component path, and does not claim sequence start/end semantics.",
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

