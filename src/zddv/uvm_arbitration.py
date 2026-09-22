from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any
import uuid

from zddv.config import ProjectConfig
from zddv.storage import get_run_record


_ARBITRATION_EVENTS = (
    "WAIT_FOR_GRANT",
    "GRANT",
    "SEND_REQUEST",
    "ITEM_DONE",
    "RESPONSE",
)

_IDENTITY_FIELDS = (
    "sequence_id",
    "sequence",
    "sequencer",
    "item_id",
    "transaction_id",
    "priority",
)


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


def _optional_priority(value: Any, *, index: int) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"events[{index}].priority must be null or an integer")
    return value


def _optional_bool(value: Any, *, field: str, index: int) -> bool | None:
    if value is None:
        return None
    if not isinstance(value, bool):
        raise ValueError(f"events[{index}].{field} must be null or a boolean")
    return value


def _normalize_event(item: Any, *, index: int) -> dict[str, Any]:
    if not isinstance(item, dict):
        raise ValueError(f"events[{index}] must be an object")

    event_type = _require_text(item.get("event"), field="event", index=index).upper()
    if event_type not in _ARBITRATION_EVENTS:
        raise ValueError(
            f"events[{index}].event must be one of: {', '.join(_ARBITRATION_EVENTS)}"
        )

    metadata = item.get("metadata", {})
    if metadata is None:
        metadata = {}
    if not isinstance(metadata, dict):
        raise ValueError(f"events[{index}].metadata must be an object")

    return {
        "event_index": index,
        "request_id": _require_text(
            item.get("request_id"), field="request_id", index=index
        ),
        "event": event_type,
        "sequence_id": _require_text(
            item.get("sequence_id"), field="sequence_id", index=index
        ),
        "sequence": _optional_text(item.get("sequence"), field="sequence", index=index),
        "sequencer": _require_text(
            item.get("sequencer"), field="sequencer", index=index
        ),
        "item_id": _optional_text(item.get("item_id"), field="item_id", index=index),
        "transaction_id": _optional_identifier(
            item.get("transaction_id"), field="transaction_id", index=index
        ),
        "priority": _optional_priority(item.get("priority"), index=index),
        "lock_request": _optional_bool(
            item.get("lock_request"), field="lock_request", index=index
        ),
        "time": _optional_text(item.get("time"), field="time", index=index),
        "metadata": dict(metadata),
    }


def parse_uvm_arbitration_data(
    payload: Any,
    *,
    source: str | None = None,
) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("UVM arbitration trace must be a JSON object")

    raw_events = payload.get("events")
    if not isinstance(raw_events, list):
        raise ValueError("UVM arbitration trace must contain an events array")

    selected_source = source or payload.get("source") or "uvm-arbitration-json"
    if not isinstance(selected_source, str) or not selected_source.strip():
        raise ValueError("source must be a non-empty string")
    selected_source = selected_source.strip()

    arbitration_mode = payload.get("arbitration_mode")
    if arbitration_mode is not None:
        if not isinstance(arbitration_mode, str) or not arbitration_mode.strip():
            raise ValueError("arbitration_mode must be null or a non-empty string")
        arbitration_mode = arbitration_mode.strip()

    events = [_normalize_event(item, index=index) for index, item in enumerate(raw_events)]
    violations: list[dict[str, Any]] = []
    requests: dict[str, dict[str, Any]] = {}
    pending_by_sequencer: dict[str, list[str]] = defaultdict(list)
    grant_windows: list[dict[str, Any]] = []

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
                "request_id": event["request_id"],
                "sequence_id": event["sequence_id"],
                "sequencer": event["sequencer"],
                "event": event["event"],
                "message": message,
            }
        )

    for event in events:
        request_id = event["request_id"]
        instance = requests.get(request_id)
        if instance is None:
            instance = {
                "request_id": request_id,
                "sequence_id": event["sequence_id"],
                "sequence": event.get("sequence"),
                "sequencer": event["sequencer"],
                "item_id": event.get("item_id"),
                "transaction_id": event.get("transaction_id"),
                "priority": event.get("priority"),
                "lock_request": event.get("lock_request"),
                "events": [],
                "event_indices": [],
                "partial": event["event"] != "WAIT_FOR_GRANT",
            }
            requests[request_id] = instance
        else:
            for field in _IDENTITY_FIELDS:
                current = event.get(field)
                previous = instance.get(field)
                if current is not None and previous is not None and current != previous:
                    add_violation(
                        "IDENTITY_CHANGED",
                        event,
                        f"Request {request_id} changed {field} from {previous} to {current}",
                    )
                elif previous is None and current is not None:
                    instance[field] = current

            current_lock = event.get("lock_request")
            if (
                current_lock is not None
                and instance.get("lock_request") is not None
                and current_lock != instance["lock_request"]
            ):
                add_violation(
                    "IDENTITY_CHANGED",
                    event,
                    f"Request {request_id} changed lock_request from "
                    f"{instance['lock_request']} to {current_lock}",
                )
            elif instance.get("lock_request") is None and current_lock is not None:
                instance["lock_request"] = current_lock

        observed = instance["events"]
        event_type = event["event"]

        if event_type in observed:
            add_violation(
                f"DUPLICATE_{event_type}",
                event,
                f"Request {request_id} observed {event_type} more than once",
            )

        if event_type == "WAIT_FOR_GRANT":
            if observed:
                add_violation(
                    "LATE_WAIT_FOR_GRANT",
                    event,
                    f"Request {request_id} observed WAIT_FOR_GRANT after later evidence",
                )
            pending = pending_by_sequencer[event["sequencer"]]
            if request_id not in pending:
                pending.append(request_id)

        elif event_type == "GRANT":
            pending = pending_by_sequencer[event["sequencer"]]
            request_observed = "WAIT_FOR_GRANT" in observed
            contenders = list(pending)

            if request_observed and request_id not in pending:
                add_violation(
                    "GRANT_NOT_PENDING",
                    event,
                    f"Request {request_id} was granted after leaving the observed pending set",
                )
            elif not request_observed:
                instance["partial"] = True

            grant_windows.append(
                {
                    "grant_index": len(grant_windows),
                    "event_index": int(event["event_index"]),
                    "request_id": request_id,
                    "sequence_id": instance["sequence_id"],
                    "sequence": instance.get("sequence"),
                    "sequencer": instance["sequencer"],
                    "priority": instance.get("priority"),
                    "lock_request": instance.get("lock_request"),
                    "request_event_index": (
                        instance["event_indices"][observed.index("WAIT_FOR_GRANT")]
                        if request_observed
                        else None
                    ),
                    "event_distance": (
                        int(event["event_index"])
                        - int(instance["event_indices"][observed.index("WAIT_FOR_GRANT")])
                        if request_observed
                        else None
                    ),
                    "request_observed": request_observed,
                    "contenders": contenders,
                    "contender_count": len(contenders) if request_observed else None,
                    "contended": len(contenders) > 1 if request_observed else None,
                }
            )
            if request_id in pending:
                pending.remove(request_id)

        elif event_type == "SEND_REQUEST":
            if "GRANT" not in observed:
                if "WAIT_FOR_GRANT" in observed:
                    add_violation(
                        "SEND_BEFORE_GRANT",
                        event,
                        f"Request {request_id} sent an item before an observed GRANT",
                    )
                else:
                    instance["partial"] = True

        elif event_type == "ITEM_DONE":
            if "SEND_REQUEST" not in observed:
                if observed:
                    add_violation(
                        "ITEM_DONE_BEFORE_SEND_REQUEST",
                        event,
                        f"Request {request_id} observed ITEM_DONE before SEND_REQUEST",
                    )
                else:
                    instance["partial"] = True

        elif event_type == "RESPONSE":
            if "SEND_REQUEST" not in observed:
                if observed:
                    add_violation(
                        "RESPONSE_BEFORE_SEND_REQUEST",
                        event,
                        f"Request {request_id} observed RESPONSE before SEND_REQUEST",
                    )
                else:
                    instance["partial"] = True

        observed.append(event_type)
        instance["event_indices"].append(event["event_index"])

    normalized_requests: list[dict[str, Any]] = []
    for item in requests.values():
        observed = item["events"]
        normalized_requests.append(
            {
                **item,
                "waited": "WAIT_FOR_GRANT" in observed,
                "granted": "GRANT" in observed,
                "sent": "SEND_REQUEST" in observed,
                "completed": "ITEM_DONE" in observed,
                "responded": "RESPONSE" in observed,
                "pending": "WAIT_FOR_GRANT" in observed and "GRANT" not in observed,
                "granted_unsent": "GRANT" in observed and "SEND_REQUEST" not in observed,
                "active": "SEND_REQUEST" in observed and "ITEM_DONE" not in observed,
                "first_event": observed[0] if observed else None,
                "last_event": observed[-1] if observed else None,
            }
        )

    pending_at_end = {
        sequencer: list(request_ids)
        for sequencer, request_ids in pending_by_sequencer.items()
        if request_ids
    }

    observed_counts = [
        int(window["contender_count"])
        for window in grant_windows
        if window["contender_count"] is not None
    ]

    return {
        "analysis": "uvm_sequence_item_arbitration",
        "source": selected_source,
        "arbitration_mode": arbitration_mode,
        "status": "FAIL" if violations else "PASS",
        "summary": {
            "requests": len(normalized_requests),
            "events": len(events),
            "violations": len(violations),
            "waited": sum(item["waited"] for item in normalized_requests),
            "granted": sum(item["granted"] for item in normalized_requests),
            "sent": sum(item["sent"] for item in normalized_requests),
            "completed": sum(item["completed"] for item in normalized_requests),
            "responded": sum(item["responded"] for item in normalized_requests),
            "pending": sum(item["pending"] for item in normalized_requests),
            "granted_unsent": sum(
                item["granted_unsent"] for item in normalized_requests
            ),
            "active": sum(item["active"] for item in normalized_requests),
            "partial": sum(item["partial"] for item in normalized_requests),
            "grant_windows": len(grant_windows),
            "contended_grants": sum(
                window["contended"] is True for window in grant_windows
            ),
            "max_observed_contenders": max(observed_counts, default=0),
        },
        "event_types": list(_ARBITRATION_EVENTS),
        "events": events,
        "requests": normalized_requests,
        "grant_windows": grant_windows,
        "pending_at_end": pending_at_end,
        "violations": violations,
        "limitations": [
            "Input is explicit normalized UVM arbitration evidence; vendor logs are not guessed or reinterpreted.",
            "Observed pending requests reconstruct contention windows but do not prove the sequencer's arbitration policy, fairness, relevance filtering, or lock/grab behavior.",
            "A trace that begins after WAIT_FOR_GRANT is retained as partial evidence rather than failed solely for missing earlier events.",
            "event_distance is an event-index distance, not simulator time or a delta-cycle timing proof.",
            "UVM requires SEND_REQUEST after WAIT_FOR_GRANT has returned; exact no-delay/delta-cycle timing is not inferred from free-form time strings.",
            "This foundation writes JSON evidence only; SQLite persistence/history and automatic instrumentation are deferred.",
        ],
    }


def parse_uvm_arbitration_file(
    path: str | Path,
    *,
    source: str | None = None,
) -> dict[str, Any]:
    input_path = Path(path)
    payload = json.loads(input_path.read_text(encoding="utf-8"))
    return parse_uvm_arbitration_data(payload, source=source)


def analyze_uvm_arbitration_file(
    project: ProjectConfig,
    path: str | Path,
    *,
    source: str | None = None,
    output: str | Path = ".zddv/uvm/arbitration/latest.json",
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

    report = parse_uvm_arbitration_file(input_path, source=source)

    created_at = datetime.now(timezone.utc).isoformat()
    snapshot_id = (
        datetime.now(timezone.utc).strftime("uvm-arb-%Y%m%dT%H%M%S")
        + "-"
        + uuid.uuid4().hex[:8]
    )

    snapshot_dir = (
        project.root / ".zddv" / "uvm" / "arbitration" / "snapshots"
    ).resolve()
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
    return record
