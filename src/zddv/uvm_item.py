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
_ITEM_LOG_MARKER = "ZDDV_UVM_ITEM"


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


def _reconstruct_observed_arbitration(
    events: list[dict[str, Any]],
) -> dict[str, Any]:
    grants: list[dict[str, Any]] = []
    sequencers: dict[str, dict[str, Any]] = {}
    observed_sequence_paths: set[tuple[str | None, str]] = set()
    unscoped_grants = 0
    unidentified_sequence_grants = 0

    for event in events:
        if event["event"] != "GRANT":
            continue

        sequencer = event.get("sequencer")
        sequence_id = event.get("sequence_id")
        if sequencer is None:
            unscoped_grants += 1
        if sequence_id is None:
            unidentified_sequence_grants += 1
        else:
            observed_sequence_paths.add((sequencer, sequence_id))

        grant = {
            "grant_index": len(grants),
            "event_index": int(event["event_index"]),
            "item_id": event["item_id"],
            "sequence_id": sequence_id,
            "sequence": event.get("sequence"),
            "sequencer": sequencer,
            "item": event.get("item"),
            "transaction_id": event.get("transaction_id"),
            "time": event.get("time"),
        }
        grants.append(grant)

        key = sequencer or "<unknown>"
        entry = sequencers.get(key)
        if entry is None:
            entry = {
                "sequencer": sequencer,
                "grant_events": 0,
                "sequence_ids": [],
                "sequence_switches": 0,
                "known_adjacent_grant_pairs": 0,
                "longest_known_sequence_streak": 0,
                "_previous_sequence_id": None,
                "_has_previous_grant": False,
                "_current_streak": 0,
            }
            sequencers[key] = entry

        previous_sequence_id = entry["_previous_sequence_id"]
        if (
            entry["_has_previous_grant"]
            and previous_sequence_id is not None
            and sequence_id is not None
        ):
            entry["known_adjacent_grant_pairs"] += 1
            if previous_sequence_id != sequence_id:
                entry["sequence_switches"] += 1

        if sequence_id is None:
            entry["_current_streak"] = 0
        elif previous_sequence_id == sequence_id and entry["_has_previous_grant"]:
            entry["_current_streak"] += 1
        else:
            entry["_current_streak"] = 1
        entry["longest_known_sequence_streak"] = max(
            entry["longest_known_sequence_streak"],
            entry["_current_streak"],
        )

        if sequence_id is not None and sequence_id not in entry["sequence_ids"]:
            entry["sequence_ids"].append(sequence_id)
        entry["grant_events"] += 1
        entry["_previous_sequence_id"] = sequence_id
        entry["_has_previous_grant"] = True

    normalized_sequencers: list[dict[str, Any]] = []
    for entry in sequencers.values():
        normalized_sequencers.append(
            {
                key: value
                for key, value in entry.items()
                if not key.startswith("_")
            }
        )

    return {
        "model": "observed_grant_order",
        "summary": {
            "grant_events": len(grants),
            "sequencers_observed": len(normalized_sequencers),
            "sequence_ids_observed": len(observed_sequence_paths),
            "sequence_switches": sum(
                int(item["sequence_switches"]) for item in normalized_sequencers
            ),
            "unscoped_grant_events": unscoped_grants,
            "unidentified_sequence_grant_events": unidentified_sequence_grants,
        },
        "grants": grants,
        "sequencers": normalized_sequencers,
        "limitations": [
            "Grant order is reconstructed only from explicit GRANT events in trace order.",
            "No waiting queue, arbitration mode, priority, lock state, or fairness policy is inferred.",
            "Missing sequencer or sequence identity remains explicit and does not create synthetic context.",
        ],
    }


def _reconstruct_request_arbitration(
    events: list[dict[str, Any]],
    *,
    max_bypass: int | None = None,
) -> dict[str, Any]:
    records: dict[str, dict[str, Any]] = {}
    pending: dict[str, dict[str, Any]] = {}
    seen_requests: set[str] = set()
    seen_grants: set[str] = set()

    matched_grants = 0
    grants_without_request_evidence = 0
    contended_grants = 0
    max_pending = 0
    max_pending_by_sequencer: dict[str, int] = {}

    def sequencer_key(value: str | None) -> str:
        return value if value is not None else "<unknown>"

    for event in events:
        item_id = event["item_id"]
        event_type = event["event"]
        sequencer = event.get("sequencer")
        seq_key = sequencer_key(sequencer)

        if event_type == "ARB_REQUEST":
            if item_id in seen_requests:
                continue
            seen_requests.add(item_id)
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
            records[item_id] = record
            pending[item_id] = record

            seq_pending = sum(
                sequencer_key(candidate.get("sequencer")) == seq_key
                for candidate in pending.values()
            )
            max_pending = max(max_pending, seq_pending)
            max_pending_by_sequencer[seq_key] = max(
                max_pending_by_sequencer.get(seq_key, 0),
                seq_pending,
            )
            continue

        if event_type != "GRANT" or item_id in seen_grants:
            continue
        seen_grants.add(item_id)

        record = pending.get(item_id)
        if record is None:
            grants_without_request_evidence += 1
            continue

        contenders = [
            candidate
            for candidate in pending.values()
            if sequencer_key(candidate.get("sequencer")) == seq_key
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

    sequence_rows: dict[tuple[str, str], dict[str, Any]] = {}
    grants_by_sequencer: dict[str, int] = {}
    for record in records.values():
        seq_key = sequencer_key(record.get("sequencer"))
        sequence_key = (
            record.get("sequence_id")
            or record.get("sequence")
            or "<unknown-sequence>"
        )
        key = (seq_key, sequence_key)
        row = sequence_rows.get(key)
        if row is None:
            row = {
                "sequencer": record.get("sequencer"),
                "sequence_id": record.get("sequence_id"),
                "sequence": record.get("sequence"),
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
            grants_by_sequencer[seq_key] = grants_by_sequencer.get(seq_key, 0) + 1

    normalized_sequences = sorted(
        sequence_rows.values(),
        key=lambda row: (
            row["sequencer"] or "",
            row["sequence_id"] or row["sequence"] or "",
        ),
    )
    for row in normalized_sequences:
        total_grants = grants_by_sequencer.get(
            sequencer_key(row.get("sequencer")),
            0,
        )
        row["grant_share"] = (
            100.0 * row["grants"] / total_grants if total_grants else 0.0
        )

    return {
        "available": bool(records),
        "policy": {"max_bypass": max_bypass},
        "summary": {
            "requests": len(records),
            "matched_grants": matched_grants,
            "grants_without_request_evidence": grants_without_request_evidence,
            "contended_grants": contended_grants,
            "pending_requests": len(pending),
            "max_pending": max_pending,
            "max_bypass": max(
                (int(record["bypasses"]) for record in records.values()),
                default=0,
            ),
        },
        "max_pending_by_sequencer": dict(sorted(max_pending_by_sequencer.items())),
        "requests": list(records.values()),
        "sequences": normalized_sequences,
        "limitations": [
            "Request-side contention is reconstructed only from explicit ARB_REQUEST events.",
            "Bypass counts measure competing grants observed while a request remains pending on the same sequencer.",
            "The optional max_bypass value is a user-supplied verification policy, not a universal UVM fairness rule.",
        ],
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
        if (
            isinstance(max_bypass, bool)
            or not isinstance(max_bypass, int)
            or max_bypass < 0
        ):
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
                        f"Item {item_id} observed REQUEST before its arbitration grant",
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

    arbitration = _reconstruct_observed_arbitration(events)
    request_evidence = _reconstruct_request_arbitration(
        events,
        max_bypass=max_bypass,
    )
    arbitration["request_evidence"] = request_evidence

    if max_bypass is not None:
        events_by_index = {
            int(event["event_index"]): event
            for event in events
        }
        for request in request_evidence["requests"]:
            if int(request["bypasses"]) <= max_bypass:
                continue
            anchor_index = (
                request["grant_event_index"]
                if request["grant_event_index"] is not None
                else request["request_event_index"]
            )
            anchor = events_by_index[int(anchor_index)]
            add_violation(
                "ARBITRATION_BYPASS_LIMIT",
                anchor,
                (
                    f"Item {request['item_id']} was bypassed by "
                    f"{request['bypasses']} competing grant(s), exceeding "
                    f"the configured maximum of {max_bypass}"
                ),
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
        "arbitration": arbitration,
        "violations": violations,
        "limitations": [
            "Input is explicit normalized handshake evidence; vendor simulator logs are not guessed or reinterpreted.",
            "A trace that begins at REQUEST, ITEM_DONE, or RESPONSE is retained as partial evidence rather than failed solely for missing earlier events.",
            "ITEM_DONE is treated as driver-completion evidence; RESPONSE is optional and is not required for an item to be complete.",
            "Observed GRANT order is reconstructed per sequencer without inferring arbitration mode, priority, lock state, or fairness from grant order alone.",
            "Waiting/contended request evidence is reconstructed only when explicit ARB_REQUEST events are present.",
            "The optional max_bypass policy is user supplied and is not a universal UVM fairness rule.",
            "Request/grant delta-cycle timing and vendor-specific automatic instrumentation remain outside this layer.",
            "SQLite persistence stores normalized snapshot summaries, event evidence, and detected violation rows.",
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
    return parse_uvm_item_data(
        payload,
        source=source,
        max_bypass=max_bypass,
    )


def parse_uvm_item_log_text(
    text: str,
    *,
    source: str = "uvm-item-log-marker",
) -> dict[str, Any]:
    """Parse explicit ZDDV_UVM_ITEM JSON markers from arbitrary simulator log text."""
    events: list[dict[str, Any]] = []
    marker_lines: list[int] = []

    for line_number, line in enumerate(text.splitlines(), start=1):
        marker_index = line.find(_ITEM_LOG_MARKER)
        if marker_index < 0:
            continue

        payload_text = line[marker_index + len(_ITEM_LOG_MARKER) :].strip()
        if not payload_text:
            raise ValueError(
                f"{_ITEM_LOG_MARKER} marker at line {line_number} has no JSON payload"
            )
        try:
            payload = json.loads(payload_text)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"{_ITEM_LOG_MARKER} marker at line {line_number} has invalid JSON"
            ) from exc
        if not isinstance(payload, dict):
            raise ValueError(
                f"{_ITEM_LOG_MARKER} marker at line {line_number} must contain a JSON object"
            )

        metadata = payload.get("metadata", {})
        if metadata is None:
            metadata = {}
        if not isinstance(metadata, dict):
            raise ValueError(
                f"{_ITEM_LOG_MARKER} marker at line {line_number} metadata must be an object"
            )

        event = dict(payload)
        event_metadata = dict(metadata)
        event_metadata["log_line"] = line_number
        event["metadata"] = event_metadata
        events.append(event)
        marker_lines.append(line_number)

    if not events:
        raise ValueError(f"No {_ITEM_LOG_MARKER} markers found in log")

    report = parse_uvm_item_data({"source": source, "events": events}, source=source)
    report["input_mode"] = "explicit-log-marker"
    report["marker"] = _ITEM_LOG_MARKER
    report["marker_lines"] = marker_lines
    report["limitations"] = [
        *report["limitations"],
        (
            "Log ingestion recognizes only explicit ZDDV_UVM_ITEM JSON markers; "
            "ordinary simulator or UVM text is not reinterpreted as item-handshake evidence."
        ),
    ]
    return report


def parse_uvm_item_log(
    path: str | Path,
    *,
    source: str = "uvm-item-log-marker",
) -> dict[str, Any]:
    input_path = Path(path)
    return parse_uvm_item_log_text(
        input_path.read_text(encoding="utf-8", errors="replace"),
        source=source,
    )


def _resolve_item_run(
    project: ProjectConfig,
    run_id: str | None,
) -> dict[str, Any] | None:
    if run_id is None:
        return None
    run_record = get_run_record(project, run_id)
    if run_record is None:
        raise ValueError(f"Unknown run ID: {run_id}")
    return run_record


def _persist_uvm_item_analysis(
    project: ProjectConfig,
    report: dict[str, Any],
    input_path: Path,
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
    max_bypass: int | None = None,
) -> dict[str, Any]:
    input_path = Path(path)
    if not input_path.is_absolute():
        input_path = project.root / input_path
    input_path = input_path.resolve()
    if not input_path.is_file():
        raise FileNotFoundError(input_path)

    run_record = _resolve_item_run(project, run_id)
    report = parse_uvm_item_file(
        input_path,
        source=source,
        max_bypass=max_bypass,
    )
    return _persist_uvm_item_analysis(
        project,
        report,
        input_path,
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
    """Analyze explicit item markers from a log and persist the shared item report."""
    run_record = _resolve_item_run(project, run_id)
    if path is None:
        if run_record is None:
            raise ValueError("A UVM item log path or --run must be provided")
        input_path = Path(run_record["log_path"])
    else:
        input_path = Path(path)
        if not input_path.is_absolute():
            input_path = project.root / input_path
    input_path = input_path.resolve()
    if not input_path.is_file():
        raise FileNotFoundError(input_path)

    selected_source = source or (
        f"{run_record['simulator']}-uvm-item-log"
        if run_record is not None
        else "uvm-item-log-marker"
    )
    report = parse_uvm_item_log(input_path, source=selected_source)
    return _persist_uvm_item_analysis(
        project,
        report,
        input_path,
        output=output,
        run_record=run_record,
    )
