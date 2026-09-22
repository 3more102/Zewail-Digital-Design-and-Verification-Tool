from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any
import uuid

from zddv.config import ProjectConfig
from zddv.storage import get_run_record, record_uvm_sequence_lifecycle_snapshot


_SEQUENCE_STATES = (
    "UVM_CREATED",
    "UVM_PRE_START",
    "UVM_PRE_BODY",
    "UVM_BODY",
    "UVM_ENDED",
    "UVM_POST_BODY",
    "UVM_POST_START",
    "UVM_STOPPED",
    "UVM_FINISHED",
)

_TERMINAL_STATES = {"UVM_STOPPED", "UVM_FINISHED"}
_SEQUENCE_LOG_MARKER = "ZDDV_UVM_SEQUENCE"

# UVM pre_body/post_body are optional when start(..., call_pre_post=0).
_ALLOWED_TRANSITIONS = {
    "UVM_CREATED": {"UVM_PRE_START", "UVM_STOPPED"},
    "UVM_PRE_START": {"UVM_PRE_BODY", "UVM_BODY", "UVM_STOPPED"},
    "UVM_PRE_BODY": {"UVM_BODY", "UVM_STOPPED"},
    "UVM_BODY": {"UVM_ENDED", "UVM_STOPPED"},
    "UVM_ENDED": {"UVM_POST_BODY", "UVM_POST_START", "UVM_STOPPED"},
    "UVM_POST_BODY": {"UVM_POST_START", "UVM_STOPPED"},
    "UVM_POST_START": {"UVM_FINISHED", "UVM_STOPPED"},
    "UVM_STOPPED": set(),
    "UVM_FINISHED": set(),
}


def _require_text(value: Any, *, field: str, index: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"events[{index}].{field} must be a non-empty string")
    return value.strip()


def _optional_text(value: Any, *, field: str, index: int) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"events[{index}].{field} must be null or a non-empty string")
    return value.strip()


def _normalize_event(item: Any, *, index: int) -> dict[str, Any]:
    if not isinstance(item, dict):
        raise ValueError(f"events[{index}] must be an object")

    sequence_id = _require_text(item.get("sequence_id"), field="sequence_id", index=index)
    sequence = _require_text(item.get("sequence"), field="sequence", index=index)
    state = _require_text(item.get("state"), field="state", index=index).upper()
    if state not in _SEQUENCE_STATES:
        raise ValueError(
            f"events[{index}].state must be one of: {', '.join(_SEQUENCE_STATES)}"
        )

    metadata = item.get("metadata", {})
    if metadata is None:
        metadata = {}
    if not isinstance(metadata, dict):
        raise ValueError(f"events[{index}].metadata must be an object")

    parent_sequence_id = _optional_text(
        item.get("parent_sequence_id"),
        field="parent_sequence_id",
        index=index,
    )

    return {
        "event_index": index,
        "sequence_id": sequence_id,
        "sequence": sequence,
        "sequencer": _optional_text(
            item.get("sequencer"),
            field="sequencer",
            index=index,
        ),
        "parent_sequence_id": parent_sequence_id,
        "state": state,
        "time": _optional_text(item.get("time"), field="time", index=index),
        "metadata": dict(metadata),
    }


def parse_uvm_sequence_data(
    payload: Any,
    *,
    source: str | None = None,
) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("UVM sequence trace must be a JSON object")

    raw_events = payload.get("events")
    if not isinstance(raw_events, list):
        raise ValueError("UVM sequence trace must contain an events array")

    selected_source = source or payload.get("source") or "uvm-sequence-json"
    if not isinstance(selected_source, str) or not selected_source.strip():
        raise ValueError("source must be a non-empty string")
    selected_source = selected_source.strip()

    events = [_normalize_event(item, index=index) for index, item in enumerate(raw_events)]
    violations: list[dict[str, Any]] = []
    instances: dict[str, dict[str, Any]] = {}

    def add_violation(
        code: str,
        event: dict[str, Any],
        message: str,
    ) -> None:
        violations.append(
            {
                "violation_index": len(violations),
                "code": code,
                "event_index": int(event["event_index"]),
                "sequence_id": event["sequence_id"],
                "sequence": event["sequence"],
                "state": event["state"],
                "message": message,
            }
        )

    for event in events:
        sequence_id = event["sequence_id"]
        parent_id = event.get("parent_sequence_id")
        if parent_id == sequence_id:
            add_violation(
                "SELF_PARENT",
                event,
                f"Sequence {sequence_id} cannot be its own parent",
            )

        instance = instances.get(sequence_id)
        if instance is None:
            instance = {
                "sequence_id": sequence_id,
                "sequence": event["sequence"],
                "sequencer": event.get("sequencer"),
                "parent_sequence_id": parent_id,
                "states": [],
                "event_indices": [],
            }
            instances[sequence_id] = instance
        else:
            if event["sequence"] != instance["sequence"]:
                add_violation(
                    "SEQUENCE_NAME_CHANGED",
                    event,
                    f"Sequence ID {sequence_id} changed name from "
                    f"{instance['sequence']} to {event['sequence']}",
                )
            current_sequencer = event.get("sequencer")
            if (
                current_sequencer is not None
                and instance.get("sequencer") is not None
                and current_sequencer != instance["sequencer"]
            ):
                add_violation(
                    "SEQUENCER_CHANGED",
                    event,
                    f"Sequence ID {sequence_id} changed sequencer from "
                    f"{instance['sequencer']} to {current_sequencer}",
                )
            elif instance.get("sequencer") is None and current_sequencer is not None:
                instance["sequencer"] = current_sequencer

            if (
                parent_id is not None
                and instance.get("parent_sequence_id") is not None
                and parent_id != instance["parent_sequence_id"]
            ):
                add_violation(
                    "PARENT_CHANGED",
                    event,
                    f"Sequence ID {sequence_id} changed parent from "
                    f"{instance['parent_sequence_id']} to {parent_id}",
                )
            elif instance.get("parent_sequence_id") is None and parent_id is not None:
                instance["parent_sequence_id"] = parent_id

        states = instance["states"]
        if states:
            previous = states[-1]
            current = event["state"]
            if previous in _TERMINAL_STATES:
                add_violation(
                    "EVENT_AFTER_TERMINAL",
                    event,
                    f"State {current} observed after terminal state {previous}",
                )
            elif current == previous:
                add_violation(
                    "DUPLICATE_STATE",
                    event,
                    f"State {current} was reported twice consecutively",
                )
            elif current not in _ALLOWED_TRANSITIONS[previous]:
                add_violation(
                    "INVALID_STATE_TRANSITION",
                    event,
                    f"Invalid UVM sequence transition {previous} -> {current}",
                )

        instance["states"].append(event["state"])
        instance["event_indices"].append(int(event["event_index"]))

    known_ids = set(instances)
    normalized_instances: list[dict[str, Any]] = []
    for instance in instances.values():
        states = list(instance["states"])
        terminal_state = states[-1] if states and states[-1] in _TERMINAL_STATES else None
        parent_id = instance.get("parent_sequence_id")
        normalized_instances.append(
            {
                **instance,
                "first_state": states[0] if states else None,
                "last_state": states[-1] if states else None,
                "terminal_state": terminal_state,
                "complete": bool(
                    states
                    and states[0] == "UVM_CREATED"
                    and terminal_state is not None
                ),
                "parent_observed": parent_id is None or parent_id in known_ids,
            }
        )

    finished = sum(
        item["terminal_state"] == "UVM_FINISHED"
        for item in normalized_instances
    )
    stopped = sum(
        item["terminal_state"] == "UVM_STOPPED"
        for item in normalized_instances
    )
    active = len(normalized_instances) - finished - stopped
    unresolved_parents = sum(
        not item["parent_observed"]
        for item in normalized_instances
    )
    complete = sum(item["complete"] for item in normalized_instances)

    return {
        "analysis": "uvm_sequence_lifecycle",
        "source": selected_source,
        "status": "FAIL" if violations else "PASS",
        "summary": {
            "sequences": len(normalized_instances),
            "events": len(events),
            "violations": len(violations),
            "finished": int(finished),
            "stopped": int(stopped),
            "active": int(active),
            "complete": int(complete),
            "nested": sum(
                item.get("parent_sequence_id") is not None
                for item in normalized_instances
            ),
            "unresolved_parents": int(unresolved_parents),
        },
        "states": list(_SEQUENCE_STATES),
        "events": events,
        "sequences": normalized_instances,
        "violations": violations,
        "limitations": [
            "Sequence lifecycle input is an explicit normalized event contract, not inferred from vendor log text.",
            "Partial traces are allowed and remain visible as active or incomplete sequences rather than being treated as failures.",
            "Only impossible/backward lifecycle transitions and inconsistent sequence identity are classified as violations.",
            "Item-level arbitration, request/grant timing, driver completion, and transaction payload semantics are not reconstructed by this layer.",
        ],
    }


def parse_uvm_sequence_file(
    path: str | Path,
    *,
    source: str | None = None,
) -> dict[str, Any]:
    input_path = Path(path)
    payload = json.loads(input_path.read_text(encoding="utf-8"))
    return parse_uvm_sequence_data(payload, source=source)


def parse_uvm_sequence_log_text(
    text: str,
    *,
    source: str = "uvm-sequence-log-marker",
) -> dict[str, Any]:
    """Parse explicit ZDDV_UVM_SEQUENCE JSON markers from simulator log text."""
    events: list[dict[str, Any]] = []
    marker_lines: list[int] = []

    for line_number, line in enumerate(text.splitlines(), start=1):
        marker_index = line.find(_SEQUENCE_LOG_MARKER)
        if marker_index < 0:
            continue

        payload_text = line[marker_index + len(_SEQUENCE_LOG_MARKER) :].strip()
        if not payload_text:
            raise ValueError(
                f"{_SEQUENCE_LOG_MARKER} marker at line {line_number} has no JSON payload"
            )
        try:
            payload = json.loads(payload_text)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"{_SEQUENCE_LOG_MARKER} marker at line {line_number} has invalid JSON"
            ) from exc
        if not isinstance(payload, dict):
            raise ValueError(
                f"{_SEQUENCE_LOG_MARKER} marker at line {line_number} "
                "must contain a JSON object"
            )

        metadata = payload.get("metadata", {})
        if metadata is None:
            metadata = {}
        if not isinstance(metadata, dict):
            raise ValueError(
                f"{_SEQUENCE_LOG_MARKER} marker at line {line_number} "
                "metadata must be an object"
            )

        event = dict(payload)
        event_metadata = dict(metadata)
        event_metadata["log_line"] = line_number
        event["metadata"] = event_metadata
        events.append(event)
        marker_lines.append(line_number)

    if not events:
        raise ValueError(f"No {_SEQUENCE_LOG_MARKER} markers found in log")

    report = parse_uvm_sequence_data(
        {"source": source, "events": events},
        source=source,
    )
    report["input_mode"] = "explicit-log-marker"
    report["marker"] = _SEQUENCE_LOG_MARKER
    report["marker_lines"] = marker_lines
    report["limitations"] = [
        *report["limitations"],
        (
            "Log ingestion recognizes only explicit ZDDV_UVM_SEQUENCE JSON markers; "
            "ordinary simulator or UVM text is not reinterpreted as lifecycle evidence."
        ),
    ]
    return report


def parse_uvm_sequence_log(
    path: str | Path,
    *,
    source: str = "uvm-sequence-log-marker",
) -> dict[str, Any]:
    input_path = Path(path)
    return parse_uvm_sequence_log_text(
        input_path.read_text(encoding="utf-8", errors="replace"),
        source=source,
    )


def _resolve_sequence_run(
    project: ProjectConfig,
    run_id: str | None,
) -> dict[str, Any] | None:
    if run_id is None:
        return None
    run_record = get_run_record(project, run_id)
    if run_record is None:
        raise ValueError(f"Unknown run ID: {run_id}")
    return run_record


def _persist_uvm_sequence_analysis(
    project: ProjectConfig,
    report: dict[str, Any],
    input_path: Path,
    *,
    output: str | Path,
    run_record: dict[str, Any] | None,
) -> dict[str, Any]:
    created_at = datetime.now(timezone.utc).isoformat()
    snapshot_id = (
        datetime.now(timezone.utc).strftime("uvm-seq-%Y%m%dT%H%M%S")
        + "-"
        + uuid.uuid4().hex[:8]
    )

    snapshot_dir = (project.root / ".zddv" / "uvm" / "sequences" / "snapshots").resolve()
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    normalized_path = snapshot_dir / f"{snapshot_id}.json"

    destination = Path(output)
    if not destination.is_absolute():
        destination = project.root / destination
    destination = destination.resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)

    record: dict[str, Any] = {
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
    record_uvm_sequence_lifecycle_snapshot(project, record)
    return record


def analyze_uvm_sequence_file(
    project: ProjectConfig,
    path: str | Path,
    *,
    source: str | None = None,
    output: str | Path = ".zddv/uvm/sequences/latest.json",
    run_id: str | None = None,
) -> dict[str, Any]:
    input_path = Path(path)
    if not input_path.is_absolute():
        input_path = project.root / input_path
    input_path = input_path.resolve()
    if not input_path.is_file():
        raise FileNotFoundError(input_path)

    run_record = _resolve_sequence_run(project, run_id)
    report = parse_uvm_sequence_file(input_path, source=source)
    return _persist_uvm_sequence_analysis(
        project,
        report,
        input_path,
        output=output,
        run_record=run_record,
    )


def analyze_uvm_sequence_log(
    project: ProjectConfig,
    path: str | Path | None,
    *,
    source: str | None = None,
    output: str | Path = ".zddv/uvm/sequences/latest.json",
    run_id: str | None = None,
) -> dict[str, Any]:
    """Analyze explicit sequence markers from a log and persist the shared report."""
    run_record = _resolve_sequence_run(project, run_id)

    if path is None:
        if run_record is None:
            raise ValueError("A UVM sequence log path or --run must be provided")
        input_path = Path(run_record["log_path"])
    else:
        input_path = Path(path)
        if not input_path.is_absolute():
            input_path = project.root / input_path

    input_path = input_path.resolve()
    if not input_path.is_file():
        raise FileNotFoundError(input_path)

    selected_source = source or (
        f"{run_record['simulator']}-uvm-sequence-log"
        if run_record is not None
        else "uvm-sequence-log-marker"
    )
    report = parse_uvm_sequence_log(input_path, source=selected_source)
    return _persist_uvm_sequence_analysis(
        project,
        report,
        input_path,
        output=output,
        run_record=run_record,
    )
