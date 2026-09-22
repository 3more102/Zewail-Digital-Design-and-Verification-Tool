from __future__ import annotations

from datetime import datetime, timezone
import json
import shlex
from pathlib import Path
from typing import Any
import uuid

from zddv.config import ProjectConfig
from zddv.storage import get_run_record, record_uvm_item_handshake_snapshot
from zddv.uvm import parse_uvm_log_text


_ITEM_EVENTS = ("GRANT", "REQUEST", "ITEM_DONE", "RESPONSE")
_IDENTITY_FIELDS = ("sequence_id", "sequence", "sequencer", "item", "transaction_id")
_ITEM_REPORT_ID = "ZDDV_ITEM"
_ITEM_REPORT_FIELDS = {
    "event",
    "item_id",
    "sequence_id",
    "sequence",
    "sequencer",
    "item",
    "transaction_id",
    "time",
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


def _optional_identifier(value: Any, *, field: str, index: int) -> str | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise ValueError(
            f"events[{index}].{field} must be null, an integer, or a non-empty string"
        )
    normalized = str(value).strip()
    if not normalized:
        raise ValueError(
            f"events[{index}].{field} must be null, an integer, or a non-empty string"
        )
    return normalized


def _normalize_event(item: Any, *, index: int) -> dict[str, Any]:
    if not isinstance(item, dict):
        raise ValueError(f"events[{index}] must be an object")

    event_type = _require_text(item.get("event"), field="event", index=index).upper()
    if event_type not in _ITEM_EVENTS:
        raise ValueError(
            f"events[{index}].event must be one of: {', '.join(_ITEM_EVENTS)}"
        )

    metadata = item.get("metadata", {})
    if metadata is None:
        metadata = {}
    if not isinstance(metadata, dict):
        raise ValueError(f"events[{index}].metadata must be an object")

    return {
        "event_index": index,
        "item_id": _require_text(item.get("item_id"), field="item_id", index=index),
        "event": event_type,
        "sequence_id": _optional_text(
            item.get("sequence_id"), field="sequence_id", index=index
        ),
        "sequence": _optional_text(item.get("sequence"), field="sequence", index=index),
        "sequencer": _optional_text(
            item.get("sequencer"), field="sequencer", index=index
        ),
        "item": _optional_text(item.get("item"), field="item", index=index),
        "transaction_id": _optional_identifier(
            item.get("transaction_id"), field="transaction_id", index=index
        ),
        "time": _optional_text(item.get("time"), field="time", index=index),
        "metadata": dict(metadata),
    }


def parse_uvm_item_data(
    payload: Any,
    *,
    source: str | None = None,
) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("UVM item trace must be a JSON object")

    raw_events = payload.get("events")
    if not isinstance(raw_events, list):
        raise ValueError("UVM item trace must contain an events array")

    selected_source = source or payload.get("source") or "uvm-item-json"
    if not isinstance(selected_source, str) or not selected_source.strip():
        raise ValueError("source must be a non-empty string")
    selected_source = selected_source.strip()

    events = [_normalize_event(item, index=index) for index, item in enumerate(raw_events)]
    violations: list[dict[str, Any]] = []
    items: dict[str, dict[str, Any]] = {}

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
                "item_id": event["item_id"],
                "event": event["event"],
                "message": message,
            }
        )

    for event in events:
        item_id = event["item_id"]
        instance = items.get(item_id)
        if instance is None:
            instance = {
                "item_id": item_id,
                "sequence_id": event.get("sequence_id"),
                "sequence": event.get("sequence"),
                "sequencer": event.get("sequencer"),
                "item": event.get("item"),
                "transaction_id": event.get("transaction_id"),
                "events": [],
                "event_indices": [],
                "partial": event["event"] != "GRANT",
            }
            items[item_id] = instance
        else:
            for field in _IDENTITY_FIELDS:
                current = event.get(field)
                previous = instance.get(field)
                if current is not None and previous is not None and current != previous:
                    add_violation(
                        "IDENTITY_CHANGED",
                        event,
                        f"Item {item_id} changed {field} from {previous} to {current}",
                    )
                elif previous is None and current is not None:
                    instance[field] = current

        observed = instance["events"]
        event_type = event["event"]

        if event_type in observed:
            add_violation(
                f"DUPLICATE_{event_type}",
                event,
                f"Item {item_id} observed {event_type} more than once",
            )

        if event_type == "GRANT":
            if any(name in observed for name in ("REQUEST", "ITEM_DONE", "RESPONSE")):
                add_violation(
                    "LATE_GRANT",
                    event,
                    f"Item {item_id} observed GRANT after later handshake evidence",
                )

        elif event_type == "REQUEST":
            if "ITEM_DONE" in observed:
                add_violation(
                    "REQUEST_AFTER_ITEM_DONE",
                    event,
                    f"Item {item_id} observed REQUEST after ITEM_DONE",
                )
            if "RESPONSE" in observed:
                add_violation(
                    "REQUEST_AFTER_RESPONSE",
                    event,
                    f"Item {item_id} observed REQUEST after RESPONSE",
                )
            if "GRANT" not in observed:
                instance["partial"] = True

        elif event_type == "ITEM_DONE":
            if "REQUEST" not in observed:
                if observed:
                    add_violation(
                        "ITEM_DONE_BEFORE_REQUEST",
                        event,
                        f"Item {item_id} observed ITEM_DONE before REQUEST",
                    )
                else:
                    instance["partial"] = True

        elif event_type == "RESPONSE":
            if "REQUEST" not in observed:
                if observed:
                    add_violation(
                        "RESPONSE_BEFORE_REQUEST",
                        event,
                        f"Item {item_id} observed RESPONSE before REQUEST",
                    )
                else:
                    instance["partial"] = True

        observed.append(event_type)
        instance["event_indices"].append(event["event_index"])

    normalized_items: list[dict[str, Any]] = []
    for item in items.values():
        observed = item["events"]
        normalized_items.append(
            {
                **item,
                "granted": "GRANT" in observed,
                "requested": "REQUEST" in observed,
                "completed": "ITEM_DONE" in observed,
                "responded": "RESPONSE" in observed,
                "active": "ITEM_DONE" not in observed,
                "first_event": observed[0] if observed else None,
                "last_event": observed[-1] if observed else None,
            }
        )

    return {
        "analysis": "uvm_item_handshake",
        "source": selected_source,
        "status": "FAIL" if violations else "PASS",
        "summary": {
            "items": len(normalized_items),
            "events": len(events),
            "violations": len(violations),
            "granted": sum(item["granted"] for item in normalized_items),
            "requested": sum(item["requested"] for item in normalized_items),
            "completed": sum(item["completed"] for item in normalized_items),
            "responded": sum(item["responded"] for item in normalized_items),
            "active": sum(item["active"] for item in normalized_items),
            "partial": sum(item["partial"] for item in normalized_items),
        },
        "event_types": list(_ITEM_EVENTS),
        "events": events,
        "items": normalized_items,
        "violations": violations,
        "limitations": [
            "Input is explicit normalized handshake evidence; vendor simulator logs are not guessed or reinterpreted.",
            "A trace that begins at REQUEST, ITEM_DONE, or RESPONSE is retained as partial evidence rather than failed solely for missing earlier events.",
            "ITEM_DONE is treated as driver-completion evidence; RESPONSE is optional and is not required for an item to be complete.",
            "Response payload comparison, arbitration priority/fairness, request/grant timing, and delta-cycle constraints are outside this foundation.",
            "SQLite persistence stores normalized snapshot summaries and event evidence; vendor-specific automatic instrumentation remains outside this layer.",
        ],
    }


def parse_uvm_item_file(
    path: str | Path,
    *,
    source: str | None = None,
) -> dict[str, Any]:
    input_path = Path(path)
    payload = json.loads(input_path.read_text(encoding="utf-8"))
    return parse_uvm_item_data(payload, source=source)


def _parse_uvm_item_report_event(message: dict[str, Any]) -> dict[str, Any] | None:
    if message.get("report_id") != _ITEM_REPORT_ID:
        return None

    payload = str(message.get("message") or "").strip()
    log_line = int(message.get("log_line") or 0)
    if not payload:
        raise ValueError(f"{_ITEM_REPORT_ID} message at log line {log_line} is empty")

    try:
        tokens = shlex.split(payload)
    except ValueError as exc:
        raise ValueError(
            f"Invalid {_ITEM_REPORT_ID} payload at log line {log_line}: {exc}"
        ) from exc

    fields: dict[str, str] = {}
    metadata: dict[str, Any] = {
        "log_line": log_line,
        "severity": message.get("severity"),
    }
    if message.get("component") is not None:
        metadata["uvm_component"] = message["component"]
    if message.get("source_location") is not None:
        metadata["source_location"] = message["source_location"]

    for token in tokens:
        if "=" not in token:
            raise ValueError(
                f"Invalid {_ITEM_REPORT_ID} token at log line {log_line}: {token!r}"
            )
        key, value = token.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key or not value:
            raise ValueError(
                f"Invalid {_ITEM_REPORT_ID} token at log line {log_line}: {token!r}"
            )
        if key in fields or key in metadata:
            raise ValueError(
                f"Duplicate {_ITEM_REPORT_ID} key {key!r} at log line {log_line}"
            )
        if key in _ITEM_REPORT_FIELDS:
            fields[key] = value
        else:
            metadata[key] = value

    if "event" not in fields or "item_id" not in fields:
        raise ValueError(
            f"{_ITEM_REPORT_ID} message at log line {log_line} must include "
            "event=<...> and item_id=<...>"
        )
    if "time" not in fields and message.get("time") is not None:
        fields["time"] = str(message["time"])

    return {
        **fields,
        "metadata": metadata,
    }


def parse_uvm_item_log_text(
    text: str,
    *,
    source: str = "uvm-item-report",
) -> dict[str, Any]:
    uvm_report = parse_uvm_log_text(text, source=source)
    events: list[dict[str, Any]] = []
    for message in uvm_report["messages"]:
        event = _parse_uvm_item_report_event(message)
        if event is not None:
            events.append(event)

    if not events:
        raise ValueError(
            f"No [{_ITEM_REPORT_ID}] UVM report messages found in the input log"
        )

    report = parse_uvm_item_data(
        {
            "source": source,
            "events": events,
        },
        source=source,
    )
    report["adapter"] = {
        "kind": "uvm_report_id",
        "report_id": _ITEM_REPORT_ID,
        "matched_messages": len(events),
        "uvm_log_status": uvm_report["status"],
        "uvm_test_name": uvm_report.get("test_name"),
    }
    return report


def parse_uvm_item_log(
    path: str | Path,
    *,
    source: str = "uvm-item-report",
) -> dict[str, Any]:
    input_path = Path(path)
    return parse_uvm_item_log_text(
        input_path.read_text(encoding="utf-8", errors="replace"),
        source=source,
    )


def _persist_uvm_item_report(
    project: ProjectConfig,
    input_path: Path,
    report: dict[str, Any],
    *,
    output: str | Path,
    run_record: dict[str, Any] | None,
) -> dict[str, Any]:
    created_at = datetime.now(timezone.utc).isoformat()
    snapshot_id = (
        datetime.now(timezone.utc).strftime("uvm-item-%Y%m%dT%H%M%S")
        + "-"
        + uuid.uuid4().hex[:8]
    )

    snapshot_dir = (project.root / ".zddv" / "uvm" / "items" / "snapshots").resolve()
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
    record_uvm_item_handshake_snapshot(project, record)
    return record


def analyze_uvm_item_file(
    project: ProjectConfig,
    path: str | Path,
    *,
    source: str | None = None,
    output: str | Path = ".zddv/uvm/items/latest.json",
    run_id: str | None = None,
) -> dict[str, Any]:
    input_path = Path(path)
    if not input_path.is_absolute():
        input_path = project.root / input_path
    input_path = input_path.resolve()
    if not input_path.is_file():
        raise FileNotFoundError(input_path)

    run_record: dict[str, Any] | None = None
    if run_id is not None:
        run_record = get_run_record(project, run_id)
        if run_record is None:
            raise ValueError(f"Unknown run ID: {run_id}")

    report = parse_uvm_item_file(input_path, source=source)
    return _persist_uvm_item_report(
        project,
        input_path,
        report,
        output=output,
        run_record=run_record,
    )


def analyze_uvm_item_log(
    project: ProjectConfig,
    path: str | Path | None,
    *,
    source: str | None = None,
    output: str | Path = ".zddv/uvm/items/latest.json",
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

    selected_source = source or (
        str(run_record["simulator"]) if run_record is not None else "uvm-item-report"
    )
    report = parse_uvm_item_log(input_path, source=selected_source)
    return _persist_uvm_item_report(
        project,
        input_path,
        report,
        output=output,
        run_record=run_record,
    )
