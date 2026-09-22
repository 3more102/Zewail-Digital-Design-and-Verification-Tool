from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any
import uuid

from zddv.config import ProjectConfig
from zddv.storage import get_run_record, record_uvm_arbitration_snapshot


_ARBITRATION_MODES = (
    "UVM_SEQ_ARB_FIFO",
    "UVM_SEQ_ARB_WEIGHTED",
    "UVM_SEQ_ARB_RANDOM",
    "UVM_SEQ_ARB_STRICT_FIFO",
    "UVM_SEQ_ARB_STRICT_RANDOM",
    "UVM_SEQ_ARB_USER",
)
_EVENTS = ("REQUEST", "GRANT")
_CORE_FIELDS = {
    "request_id",
    "event",
    "sequence_id",
    "sequence",
    "sequencer",
    "priority",
    "time",
}


def _require_text(value: object, field: str) -> str:
    text = str(value).strip() if value is not None else ""
    if not text:
        raise ValueError(f"{field} must be a non-empty string")
    return text


def _normalize_priority(value: object) -> int:
    if value is None:
        return 100
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("priority must be an integer") from exc


def _normalize_limit(value: object | None) -> int | None:
    if value is None:
        return None
    try:
        limit = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("max_bypass must be an integer") from exc
    if limit < 0:
        raise ValueError("max_bypass must be >= 0")
    return limit


def parse_uvm_arbitration_data(
    payload: dict[str, Any],
    *,
    source: str | None = None,
    max_bypass: int | None = None,
) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("UVM arbitration trace must be a JSON object")

    mode = str(payload.get("mode", "UVM_SEQ_ARB_FIFO")).strip().upper()
    if mode not in _ARBITRATION_MODES:
        raise ValueError(
            "mode must be one of: " + ", ".join(_ARBITRATION_MODES)
        )

    events_raw = payload.get("events")
    if not isinstance(events_raw, list):
        raise ValueError("events must be a JSON array")

    effective_limit = _normalize_limit(
        max_bypass if max_bypass is not None else payload.get("max_bypass")
    )

    normalized_events: list[dict[str, Any]] = []
    requests: dict[str, dict[str, Any]] = {}
    pending: list[str] = []
    decisions: list[dict[str, Any]] = []
    violations: list[dict[str, Any]] = []
    fairness_reported: set[str] = set()
    sequences: set[str] = set()

    def add_violation(
        code: str,
        event_index: int,
        request_id: str,
        message: str,
    ) -> None:
        violations.append(
            {
                "code": code,
                "event_index": event_index,
                "request_id": request_id,
                "message": message,
            }
        )

    for event_index, raw in enumerate(events_raw):
        if not isinstance(raw, dict):
            raise ValueError(f"events[{event_index}] must be a JSON object")

        event = str(raw.get("event", "")).strip().upper()
        if event not in _EVENTS:
            raise ValueError(
                f"events[{event_index}].event must be one of: "
                + ", ".join(_EVENTS)
            )

        request_id = _require_text(
            raw.get("request_id"), f"events[{event_index}].request_id"
        )
        sequence_id = raw.get("sequence_id")
        if sequence_id is not None:
            sequence_id = _require_text(
                sequence_id, f"events[{event_index}].sequence_id"
            )
            sequences.add(sequence_id)

        priority = _normalize_priority(raw.get("priority"))
        normalized = {
            "event_index": event_index,
            "request_id": request_id,
            "event": event,
            "sequence_id": sequence_id,
            "sequence": raw.get("sequence"),
            "sequencer": raw.get("sequencer"),
            "priority": priority,
            "time": raw.get("time"),
            "metadata": {
                key: value for key, value in raw.items() if key not in _CORE_FIELDS
            },
        }
        normalized_events.append(normalized)

        if event == "REQUEST":
            if request_id in requests:
                add_violation(
                    "DUPLICATE_REQUEST",
                    event_index,
                    request_id,
                    "The same arbitration request_id was submitted more than once.",
                )
                continue

            if sequence_id is None:
                raise ValueError(
                    f"events[{event_index}].sequence_id is required for REQUEST"
                )

            state = {
                "request_id": request_id,
                "sequence_id": sequence_id,
                "sequence": raw.get("sequence"),
                "sequencer": raw.get("sequencer"),
                "priority": priority,
                "request_event_index": event_index,
                "grant_event_index": None,
                "request_time": raw.get("time"),
                "grant_time": None,
                "bypass_count": 0,
            }
            requests[request_id] = state
            pending.append(request_id)
            continue

        state = requests.get(request_id)
        if state is None:
            add_violation(
                "GRANT_WITHOUT_REQUEST",
                event_index,
                request_id,
                "Grant evidence has no earlier matching arbitration request.",
            )
            continue

        if state["grant_event_index"] is not None:
            add_violation(
                "DUPLICATE_GRANT",
                event_index,
                request_id,
                "The same arbitration request_id was granted more than once.",
            )
            continue

        if sequence_id is not None and sequence_id != state["sequence_id"]:
            add_violation(
                "IDENTITY_CHANGED",
                event_index,
                request_id,
                "sequence_id changed between REQUEST and GRANT.",
            )
        if raw.get("sequencer") is not None and raw.get("sequencer") != state["sequencer"]:
            add_violation(
                "IDENTITY_CHANGED",
                event_index,
                request_id,
                "sequencer changed between REQUEST and GRANT.",
            )
        if "priority" in raw and priority != state["priority"]:
            add_violation(
                "PRIORITY_CHANGED",
                event_index,
                request_id,
                "priority changed between REQUEST and GRANT.",
            )

        candidate_ids = [
            item
            for item in pending
            if requests[item]["sequencer"] == state["sequencer"]
        ]
        candidate_states = [requests[item] for item in candidate_ids]
        highest_priority = (
            max(item["priority"] for item in candidate_states)
            if candidate_states
            else None
        )
        expected_request_id: str | None = None
        eligible_request_ids = list(candidate_ids)

        if mode == "UVM_SEQ_ARB_FIFO" and candidate_ids:
            expected_request_id = candidate_ids[0]
            if request_id != expected_request_id:
                add_violation(
                    "FIFO_ORDER_VIOLATION",
                    event_index,
                    request_id,
                    f"FIFO arbitration expected request {expected_request_id}.",
                )
        elif mode == "UVM_SEQ_ARB_STRICT_FIFO" and candidate_states:
            strict = [
                item
                for item in candidate_states
                if item["priority"] == highest_priority
            ]
            eligible_request_ids = [item["request_id"] for item in strict]
            expected_request_id = strict[0]["request_id"]
            if request_id != expected_request_id:
                add_violation(
                    "STRICT_FIFO_ORDER_VIOLATION",
                    event_index,
                    request_id,
                    "STRICT_FIFO must grant the oldest pending request "
                    "at the highest priority.",
                )
        elif mode == "UVM_SEQ_ARB_STRICT_RANDOM" and candidate_states:
            eligible_request_ids = [
                item["request_id"]
                for item in candidate_states
                if item["priority"] == highest_priority
            ]
            if request_id not in eligible_request_ids:
                add_violation(
                    "STRICT_PRIORITY_VIOLATION",
                    event_index,
                    request_id,
                    "STRICT_RANDOM must choose from the highest-priority "
                    "pending requests.",
                )

        decisions.append(
            {
                "event_index": event_index,
                "request_id": request_id,
                "sequencer": state["sequencer"],
                "mode": mode,
                "pending_request_ids": candidate_ids,
                "eligible_request_ids": eligible_request_ids,
                "expected_request_id": expected_request_id,
                "highest_priority": highest_priority,
            }
        )

        for other_id in list(pending):
            if other_id == request_id:
                continue
            other = requests[other_id]
            if other["sequencer"] != state["sequencer"]:
                continue
            other["bypass_count"] += 1
            if (
                effective_limit is not None
                and other["bypass_count"] > effective_limit
                and other_id not in fairness_reported
            ):
                add_violation(
                    "FAIRNESS_BYPASS_LIMIT",
                    event_index,
                    other_id,
                    f"Pending request exceeded max_bypass={effective_limit}.",
                )
                fairness_reported.add(other_id)

        state["grant_event_index"] = event_index
        state["grant_time"] = raw.get("time")
        if request_id in pending:
            pending.remove(request_id)

    request_rows: list[dict[str, Any]] = []
    for state in requests.values():
        row = dict(state)
        row["status"] = (
            "GRANTED" if state["grant_event_index"] is not None else "PENDING"
        )
        request_rows.append(row)

    max_bypass_observed = max(
        (row["bypass_count"] for row in request_rows),
        default=0,
    )
    deterministic_mode = mode in {
        "UVM_SEQ_ARB_FIFO",
        "UVM_SEQ_ARB_STRICT_FIFO",
        "UVM_SEQ_ARB_STRICT_RANDOM",
    }

    return {
        "analysis": "uvm_arbitration",
        "source": source or str(payload.get("source") or "normalized-trace"),
        "mode": mode,
        "status": "FAIL" if violations else "PASS",
        "summary": {
            "requests": len(request_rows),
            "grants": sum(
                1 for row in request_rows if row["grant_event_index"] is not None
            ),
            "pending": len(pending),
            "sequences": len(sequences),
            "decisions": len(decisions),
            "violations": len(violations),
            "max_bypass_observed": max_bypass_observed,
            "max_bypass_limit": effective_limit,
        },
        "events": normalized_events,
        "requests": request_rows,
        "decisions": decisions,
        "violations": violations,
        "notes": [
            "REQUEST/GRANT here are explicit sequencer-arbitration evidence, not the sequence-item REQUEST event used by uvm-item-analyze.",
            "FIFO is checked in queue order; STRICT_FIFO checks highest priority then FIFO; STRICT_RANDOM checks highest-priority eligibility only.",
            "RANDOM, WEIGHTED, and USER choices are retained as observational evidence because a finite trace does not prove a deterministic winner.",
            "max_bypass is an optional ZDDV fairness policy threshold, not a UVM-standard starvation guarantee.",
            "Pending requests at the end of a finite trace are retained as partial evidence and do not fail analysis by themselves.",
            (
                "Arbitration snapshots, events, decisions, and violations are persisted "
                "in SQLite; automatic vendor/instrumentation adapters are deferred."
            ),
        ],
        "selection_policy": (
            "deterministic-checks" if deterministic_mode else "observational"
        ),
    }


def parse_uvm_arbitration_file(
    path: str | Path,
    *,
    source: str | None = None,
    max_bypass: int | None = None,
) -> dict[str, Any]:
    input_path = Path(path)
    payload = json.loads(input_path.read_text(encoding="utf-8"))
    return parse_uvm_arbitration_data(
        payload,
        source=source,
        max_bypass=max_bypass,
    )


def analyze_uvm_arbitration_file(
    project: ProjectConfig,
    path: str | Path,
    *,
    source: str | None = None,
    output: str | Path = ".zddv/uvm/arbitration/latest.json",
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

    report = parse_uvm_arbitration_file(
        input_path,
        source=source,
        max_bypass=max_bypass,
    )

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
    record_uvm_arbitration_snapshot(project, record)
    return record
