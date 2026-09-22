from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any
import uuid

from zddv.config import ProjectConfig
from zddv.storage import get_run_record, record_uvm_item_handshake_snapshot


_ITEM_EVENTS = ("ARB_REQUEST", "GRANT", "REQUEST", "ITEM_DONE", "RESPONSE")
_IDENTITY_FIELDS = ("sequence_id", "sequence", "sequencer", "item", "transaction_id")


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
    max_bypass: int | None = None,
) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("UVM item trace must be a JSON object")

    raw_events = payload.get("events")
    if not isinstance(raw_events, list):
        raise ValueError("UVM item trace must contain an events array")

    if max_bypass is not None:
        if isinstance(max_bypass, bool) or not isinstance(max_bypass, int) or max_bypass < 0:
            raise ValueError("max_bypass must be a non-negative integer or null")

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
                "partial": event["event"] not in {"ARB_REQUEST", "GRANT"},
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

        if event_type == "ARB_REQUEST":
            if any(name in observed for name in ("GRANT", "REQUEST", "ITEM_DONE", "RESPONSE")):
                add_violation(
                    "LATE_ARB_REQUEST",
                    event,
                    f"Item {item_id} observed ARB_REQUEST after later handshake evidence",
                )

        elif event_type == "GRANT":
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
                if "ARB_REQUEST" in observed:
                    add_violation(
                        "REQUEST_BEFORE_GRANT",
                        event,
                        f"Item {item_id} observed REQUEST before the requested arbitration grant",
                    )
                else:
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


    arbitration_records: dict[str, dict[str, Any]] = {}
    pending: dict[str, dict[str, Any]] = {}
    seen_arb_requests: set[str] = set()
    seen_grants: set[str] = set()
    matched_grants = 0
    unmatched_grants = 0
    contended_grants = 0
    max_pending = 0
    max_pending_by_sequencer: dict[str, int] = {}

    for event in events:
        item_id = event["item_id"]
        event_type = event["event"]
        sequencer = event.get("sequencer") or "<unknown>"

        if event_type == "ARB_REQUEST":
            if item_id in seen_arb_requests:
                continue
            seen_arb_requests.add(item_id)
            record = {
                "item_id": item_id,
                "sequence_id": event.get("sequence_id"),
                "sequence": event.get("sequence"),
                "sequencer": sequencer,
                "item": event.get("item"),
                "transaction_id": event.get("transaction_id"),
                "request_event_index": int(event["event_index"]),
                "grant_event_index": None,
                "bypasses": 0,
                "granted": False,
                "pending": True,
            }
            arbitration_records[item_id] = record
            pending[item_id] = record
            seq_pending = sum(
                candidate["sequencer"] == sequencer for candidate in pending.values()
            )
            max_pending = max(max_pending, seq_pending)
            max_pending_by_sequencer[sequencer] = max(
                max_pending_by_sequencer.get(sequencer, 0),
                seq_pending,
            )
            continue

        if event_type != "GRANT" or item_id in seen_grants:
            continue
        seen_grants.add(item_id)

        record = pending.get(item_id)
        if record is None:
            unmatched_grants += 1
            continue

        contenders = [
            candidate
            for candidate in pending.values()
            if candidate["sequencer"] == record["sequencer"]
        ]
        if len(contenders) > 1:
            contended_grants += 1
        for candidate in contenders:
            if candidate["item_id"] != item_id:
                candidate["bypasses"] += 1

        record["granted"] = True
        record["pending"] = False
        record["grant_event_index"] = int(event["event_index"])
        matched_grants += 1
        pending.pop(item_id, None)

    event_by_index = {int(event["event_index"]): event for event in events}
    if max_bypass is not None:
        for record in arbitration_records.values():
            if int(record["bypasses"]) <= max_bypass:
                continue
            anchor_index = (
                record["grant_event_index"]
                if record["grant_event_index"] is not None
                else record["request_event_index"]
            )
            anchor = event_by_index[int(anchor_index)]
            add_violation(
                "ARBITRATION_BYPASS_LIMIT",
                anchor,
                (
                    f"Item {record['item_id']} was bypassed by "
                    f"{record['bypasses']} competing grant(s), exceeding "
                    f"the configured maximum of {max_bypass}"
                ),
            )

    sequence_rows: dict[tuple[str, str], dict[str, Any]] = {}
    grants_by_sequencer: dict[str, int] = {}
    for record in arbitration_records.values():
        sequence_key = (
            record["sequence_id"]
            or record["sequence"]
            or "<unknown-sequence>"
        )
        key = (record["sequencer"], sequence_key)
        row = sequence_rows.get(key)
        if row is None:
            row = {
                "sequencer": record["sequencer"],
                "sequence_id": record["sequence_id"],
                "sequence": record["sequence"],
                "requests": 0,
                "grants": 0,
                "pending": 0,
                "bypasses": 0,
                "max_bypass": 0,
                "grant_share": 0.0,
            }
            sequence_rows[key] = row
        row["requests"] += 1
        row["grants"] += int(record["granted"])
        row["pending"] += int(record["pending"])
        row["bypasses"] += int(record["bypasses"])
        row["max_bypass"] = max(row["max_bypass"], int(record["bypasses"]))
        if record["granted"]:
            grants_by_sequencer[record["sequencer"]] = (
                grants_by_sequencer.get(record["sequencer"], 0) + 1
            )

    for row in sequence_rows.values():
        total = grants_by_sequencer.get(row["sequencer"], 0)
        row["grant_share"] = 100.0 * row["grants"] / total if total else 0.0

    arbitration = {
        "available": bool(arbitration_records),
        "policy": {"max_bypass": max_bypass},
        "summary": {
            "requests": len(arbitration_records),
            "matched_grants": matched_grants,
            "grants_without_request_evidence": unmatched_grants,
            "contended_grants": contended_grants,
            "pending_requests": len(pending),
            "max_pending": max_pending,
            "max_bypass": max(
                (int(record["bypasses"]) for record in arbitration_records.values()),
                default=0,
            ),
        },
        "max_pending_by_sequencer": dict(sorted(max_pending_by_sequencer.items())),
        "requests": list(arbitration_records.values()),
        "sequences": sorted(
            sequence_rows.values(),
            key=lambda row: (row["sequencer"], row["sequence_id"] or row["sequence"] or ""),
        ),
    }

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
        "arbitration": arbitration,
        "limitations": [
            "Input is explicit normalized handshake evidence; vendor simulator logs are not guessed or reinterpreted.",
            "A trace that begins at REQUEST, ITEM_DONE, or RESPONSE is retained as partial evidence rather than failed solely for missing earlier events.",
            "ITEM_DONE is treated as driver-completion evidence; RESPONSE is optional and is not required for an item to be complete.",
            "Fairness evidence requires explicit ARB_REQUEST events; vendor arbitration policy is not inferred from grant order alone.",
            "The optional max_bypass policy is a user-supplied bound on competing grants while a request waits, not a universal UVM fairness rule.",
            "Response payload comparison, request/grant delta-cycle timing, and vendor-specific automatic instrumentation remain outside this layer.",
            "SQLite persistence stores normalized snapshot summaries and event evidence.",
        ],
    }


def parse_uvm_item_file(
    path: str | Path,
    *,
    source: str | None = None,
    max_bypass: int | None = None,
) -> dict[str, Any]:
    input_path = Path(path)
    payload = json.loads(input_path.read_text(encoding="utf-8"))
    return parse_uvm_item_data(payload, source=source, max_bypass=max_bypass)


def analyze_uvm_item_file(
    project: ProjectConfig,
    path: str | Path,
    *,
    source: str | None = None,
    output: str | Path = ".zddv/uvm/items/latest.json",
    run_id: str | None = None,
    max_bypass: int | None = None,
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

    report = parse_uvm_item_file(
        input_path,
        source=source,
        max_bypass=max_bypass,
    )

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
