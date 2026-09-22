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

_STRICT_MODES = {"UVM_SEQ_ARB_STRICT_FIFO", "UVM_SEQ_ARB_STRICT_RANDOM"}
_FIFO_MODES = {"UVM_SEQ_ARB_FIFO", "UVM_SEQ_ARB_STRICT_FIFO"}


def _require_text(value: Any, *, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    return value.strip()


def _optional_text(value: Any, *, field: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be null or a non-empty string")
    return value.strip()


def _optional_int(value: Any, *, field: str, minimum: int | None = None) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field} must be null or an integer")
    if minimum is not None and value < minimum:
        raise ValueError(f"{field} must be >= {minimum}")
    return int(value)


def _metadata(value: Any, *, field: str) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError(f"{field} must be an object")
    return dict(value)


def _normalize_candidate(item: Any, *, round_index: int, candidate_index: int) -> dict[str, Any]:
    if not isinstance(item, dict):
        raise ValueError(
            f"rounds[{round_index}].candidates[{candidate_index}] must be an object"
        )
    prefix = f"rounds[{round_index}].candidates[{candidate_index}]"
    return {
        "candidate_index": candidate_index,
        "sequence_id": _require_text(item.get("sequence_id"), field=f"{prefix}.sequence_id"),
        "sequence": _optional_text(item.get("sequence"), field=f"{prefix}.sequence"),
        "request_order": _optional_int(
            item.get("request_order"),
            field=f"{prefix}.request_order",
            minimum=0,
        ),
        "priority": _optional_int(item.get("priority"), field=f"{prefix}.priority"),
        "metadata": _metadata(item.get("metadata"), field=f"{prefix}.metadata"),
    }


def _normalize_round(item: Any, *, index: int) -> dict[str, Any]:
    if not isinstance(item, dict):
        raise ValueError(f"rounds[{index}] must be an object")
    candidates = item.get("candidates")
    if not isinstance(candidates, list) or not candidates:
        raise ValueError(f"rounds[{index}].candidates must be a non-empty array")
    return {
        "round_index": index,
        "round_id": _require_text(item.get("round_id"), field=f"rounds[{index}].round_id"),
        "sequencer": _require_text(item.get("sequencer"), field=f"rounds[{index}].sequencer"),
        "winner_sequence_id": _require_text(
            item.get("winner_sequence_id"),
            field=f"rounds[{index}].winner_sequence_id",
        ),
        "time": _optional_text(item.get("time"), field=f"rounds[{index}].time"),
        "metadata": _metadata(item.get("metadata"), field=f"rounds[{index}].metadata"),
        "candidates": [
            _normalize_candidate(candidate, round_index=index, candidate_index=candidate_index)
            for candidate_index, candidate in enumerate(candidates)
        ],
    }


def parse_uvm_arbitration_data(
    payload: Any,
    *,
    source: str | None = None,
) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("UVM arbitration trace must be a JSON object")

    mode = _require_text(payload.get("mode"), field="mode").upper()
    if mode not in _ARBITRATION_MODES:
        raise ValueError(f"mode must be one of: {', '.join(_ARBITRATION_MODES)}")

    selected_source = source or payload.get("source") or "uvm-arbitration-json"
    selected_source = _require_text(selected_source, field="source")

    raw_rounds = payload.get("rounds")
    if not isinstance(raw_rounds, list):
        raise ValueError("UVM arbitration trace must contain a rounds array")

    rounds = [_normalize_round(item, index=index) for index, item in enumerate(raw_rounds)]
    violations: list[dict[str, Any]] = []
    seen_round_ids: set[str] = set()

    def add_violation(
        code: str,
        round_data: dict[str, Any],
        message: str,
        *,
        sequence_id: str | None = None,
    ) -> None:
        violations.append(
            {
                "violation_index": len(violations),
                "code": code,
                "round_index": int(round_data["round_index"]),
                "round_id": round_data["round_id"],
                "sequencer": round_data["sequencer"],
                "sequence_id": sequence_id,
                "message": message,
            }
        )

    for round_data in rounds:
        round_id = round_data["round_id"]
        if round_id in seen_round_ids:
            add_violation(
                "DUPLICATE_ROUND_ID",
                round_data,
                f"Round ID {round_id} was observed more than once",
            )
        seen_round_ids.add(round_id)

        candidates = round_data["candidates"]
        by_id: dict[str, dict[str, Any]] = {}
        for candidate in candidates:
            sequence_id = candidate["sequence_id"]
            if sequence_id in by_id:
                add_violation(
                    "DUPLICATE_CANDIDATE",
                    round_data,
                    f"Sequence {sequence_id} appears more than once in one arbitration round",
                    sequence_id=sequence_id,
                )
            else:
                by_id[sequence_id] = candidate

        winner_id = round_data["winner_sequence_id"]
        winner = by_id.get(winner_id)
        if winner is None:
            add_violation(
                "WINNER_NOT_ELIGIBLE",
                round_data,
                f"Winner {winner_id} is not present in the eligible candidate set",
                sequence_id=winner_id,
            )
            continue

        if mode in _FIFO_MODES:
            missing = [c["sequence_id"] for c in candidates if c["request_order"] is None]
            if missing:
                add_violation(
                    "MISSING_REQUEST_ORDER",
                    round_data,
                    "FIFO arbitration requires request_order evidence for every candidate: "
                    + ", ".join(missing),
                )
            else:
                request_orders = [int(c["request_order"]) for c in candidates]
                if len(set(request_orders)) != len(request_orders):
                    add_violation(
                        "AMBIGUOUS_REQUEST_ORDER",
                        round_data,
                        "FIFO arbitration requires unique request_order values within a round",
                    )

        if mode in _STRICT_MODES:
            missing = [c["sequence_id"] for c in candidates if c["priority"] is None]
            if missing:
                add_violation(
                    "MISSING_PRIORITY",
                    round_data,
                    "Strict arbitration requires priority evidence for every candidate: "
                    + ", ".join(missing),
                )

        has_required_order = all(c["request_order"] is not None for c in candidates)
        unique_order = len({c["request_order"] for c in candidates}) == len(candidates)
        has_required_priority = all(c["priority"] is not None for c in candidates)

        if mode == "UVM_SEQ_ARB_FIFO" and has_required_order and unique_order:
            expected = min(candidates, key=lambda c: int(c["request_order"]))
            if winner_id != expected["sequence_id"]:
                add_violation(
                    "FIFO_ORDER_VIOLATION",
                    round_data,
                    f"FIFO winner must be {expected['sequence_id']} from the earliest request_order",
                    sequence_id=winner_id,
                )

        elif mode == "UVM_SEQ_ARB_STRICT_FIFO" and (
            has_required_order and unique_order and has_required_priority
        ):
            highest_priority = max(int(c["priority"]) for c in candidates)
            highest = [
                c for c in candidates if int(c["priority"]) == highest_priority
            ]
            expected = min(highest, key=lambda c: int(c["request_order"]))
            if winner_id != expected["sequence_id"]:
                add_violation(
                    "STRICT_FIFO_VIOLATION",
                    round_data,
                    f"STRICT_FIFO winner must be {expected['sequence_id']} "
                    f"(priority={highest_priority}, earliest request_order among that priority)",
                    sequence_id=winner_id,
                )

        elif mode == "UVM_SEQ_ARB_STRICT_RANDOM" and has_required_priority:
            highest_priority = max(int(c["priority"]) for c in candidates)
            if int(winner["priority"]) != highest_priority:
                add_violation(
                    "STRICT_RANDOM_PRIORITY_VIOLATION",
                    round_data,
                    f"STRICT_RANDOM winner must be selected from priority {highest_priority}",
                    sequence_id=winner_id,
                )

    sequence_metrics: dict[tuple[str, str], dict[str, Any]] = {}
    for round_data in rounds:
        sequencer = round_data["sequencer"]
        winner_id = round_data["winner_sequence_id"]
        for candidate in round_data["candidates"]:
            key = (sequencer, candidate["sequence_id"])
            metric = sequence_metrics.get(key)
            if metric is None:
                metric = {
                    "sequencer": sequencer,
                    "sequence_id": candidate["sequence_id"],
                    "sequence": candidate.get("sequence"),
                    "eligible_rounds": 0,
                    "wins": 0,
                    "current_wait_rounds": 0,
                    "max_wait_rounds": 0,
                    "first_round_index": round_data["round_index"],
                    "last_round_index": round_data["round_index"],
                }
                sequence_metrics[key] = metric
            elif metric.get("sequence") is None and candidate.get("sequence") is not None:
                metric["sequence"] = candidate["sequence"]

            metric["eligible_rounds"] += 1
            metric["last_round_index"] = round_data["round_index"]
            if candidate["sequence_id"] == winner_id:
                metric["wins"] += 1
                metric["current_wait_rounds"] = 0
            else:
                metric["current_wait_rounds"] += 1
                metric["max_wait_rounds"] = max(
                    metric["max_wait_rounds"],
                    metric["current_wait_rounds"],
                )

    metrics = sorted(
        sequence_metrics.values(),
        key=lambda item: (item["sequencer"], item["sequence_id"]),
    )
    max_wait_rounds = max(
        (int(item["max_wait_rounds"]) for item in metrics),
        default=0,
    )

    return {
        "analysis": "uvm_sequencer_arbitration",
        "source": selected_source,
        "mode": mode,
        "status": "FAIL" if violations else "PASS",
        "summary": {
            "rounds": len(rounds),
            "sequences": len(metrics),
            "violations": len(violations),
            "max_wait_rounds": max_wait_rounds,
        },
        "rounds": rounds,
        "sequence_metrics": metrics,
        "violations": violations,
        "limitations": [
            "Input is explicit normalized arbitration evidence; vendor simulator logs are not guessed or reinterpreted.",
            "FIFO and strict-priority ordering are validated only when the required request_order and priority evidence is present.",
            "RANDOM, WEIGHTED, and USER winner choice is retained as evidence but a single observed draw is not classified as fair or unfair.",
            "Wait-round metrics count observed eligible arbitration opportunities and are evidence, not a fairness verdict.",
            "Locks, grabs, is_relevant filtering, and automatic instrumentation must be reflected by the producer in the eligible candidate set.",
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

    snapshot_dir = (project.root / ".zddv" / "uvm" / "arbitration" / "snapshots").resolve()
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
