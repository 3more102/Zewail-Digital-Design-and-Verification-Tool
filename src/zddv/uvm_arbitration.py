from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any
import uuid

from zddv.config import ProjectConfig
from zddv.storage import get_run_record


_UVM_ARBITRATION_MODES = (
    "UVM_SEQ_ARB_FIFO",
    "UVM_SEQ_ARB_WEIGHTED",
    "UVM_SEQ_ARB_RANDOM",
    "UVM_SEQ_ARB_STRICT_FIFO",
    "UVM_SEQ_ARB_STRICT_RANDOM",
    "UVM_SEQ_ARB_USER",
)
_LOCAL_UNSPECIFIED_MODE = "UNSPECIFIED"


def _require_text(value: Any, *, field: str, index: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"rounds[{index}].{field} must be a non-empty string")
    return value.strip()


def _optional_text(value: Any, *, field: str, index: int) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"rounds[{index}].{field} must be null or a non-empty string")
    return value.strip()


def _normalize_mode(value: Any, *, field: str) -> str:
    if value is None:
        return _LOCAL_UNSPECIFIED_MODE
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    mode = value.strip().upper()
    allowed = set(_UVM_ARBITRATION_MODES) | {_LOCAL_UNSPECIFIED_MODE}
    if mode not in allowed:
        raise ValueError(
            f"{field} must be one of: "
            f"{', '.join((*_UVM_ARBITRATION_MODES, _LOCAL_UNSPECIFIED_MODE))}"
        )
    return mode


def _normalize_contender(
    item: Any,
    *,
    round_index: int,
    contender_index: int,
) -> dict[str, Any]:
    if not isinstance(item, dict):
        raise ValueError(
            f"rounds[{round_index}].contenders[{contender_index}] must be an object"
        )

    sequence_id = item.get("sequence_id")
    if not isinstance(sequence_id, str) or not sequence_id.strip():
        raise ValueError(
            f"rounds[{round_index}].contenders[{contender_index}].sequence_id "
            "must be a non-empty string"
        )

    sequence = item.get("sequence")
    if sequence is not None and (
        not isinstance(sequence, str) or not sequence.strip()
    ):
        raise ValueError(
            f"rounds[{round_index}].contenders[{contender_index}].sequence "
            "must be null or a non-empty string"
        )

    priority = item.get("priority")
    if priority is not None and (
        isinstance(priority, bool) or not isinstance(priority, int)
    ):
        raise ValueError(
            f"rounds[{round_index}].contenders[{contender_index}].priority "
            "must be null or an integer"
        )

    request_order = item.get("request_order")
    if request_order is not None and (
        isinstance(request_order, bool)
        or not isinstance(request_order, int)
        or request_order < 0
    ):
        raise ValueError(
            f"rounds[{round_index}].contenders[{contender_index}].request_order "
            "must be null or a non-negative integer"
        )

    metadata = item.get("metadata", {})
    if metadata is None:
        metadata = {}
    if not isinstance(metadata, dict):
        raise ValueError(
            f"rounds[{round_index}].contenders[{contender_index}].metadata "
            "must be an object"
        )

    return {
        "sequence_id": sequence_id.strip(),
        "sequence": sequence.strip() if isinstance(sequence, str) else None,
        "priority": int(priority) if priority is not None else None,
        "request_order": int(request_order) if request_order is not None else None,
        "metadata": dict(metadata),
    }


def _normalize_round(
    item: Any,
    *,
    index: int,
    default_mode: str,
) -> dict[str, Any]:
    if not isinstance(item, dict):
        raise ValueError(f"rounds[{index}] must be an object")

    raw_contenders = item.get("contenders")
    if not isinstance(raw_contenders, list) or not raw_contenders:
        raise ValueError(f"rounds[{index}].contenders must be a non-empty array")

    metadata = item.get("metadata", {})
    if metadata is None:
        metadata = {}
    if not isinstance(metadata, dict):
        raise ValueError(f"rounds[{index}].metadata must be an object")

    mode = _normalize_mode(
        item.get("mode", default_mode),
        field=f"rounds[{index}].mode",
    )
    contenders = [
        _normalize_contender(
            candidate,
            round_index=index,
            contender_index=candidate_index,
        )
        for candidate_index, candidate in enumerate(raw_contenders)
    ]

    return {
        "round_index": index,
        "round_id": _require_text(
            item.get("round_id"),
            field="round_id",
            index=index,
        ),
        "sequencer": _require_text(
            item.get("sequencer"),
            field="sequencer",
            index=index,
        ),
        "mode": mode,
        "winner_sequence_id": _require_text(
            item.get("winner_sequence_id"),
            field="winner_sequence_id",
            index=index,
        ),
        "time": _optional_text(
            item.get("time"),
            field="time",
            index=index,
        ),
        "contenders": contenders,
        "metadata": dict(metadata),
    }


def parse_uvm_arbitration_data(
    payload: Any,
    *,
    source: str | None = None,
) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("UVM arbitration trace must be a JSON object")

    raw_rounds = payload.get("rounds")
    if not isinstance(raw_rounds, list):
        raise ValueError("UVM arbitration trace must contain a rounds array")

    selected_source = source or payload.get("source") or "uvm-arbitration-json"
    if not isinstance(selected_source, str) or not selected_source.strip():
        raise ValueError("source must be a non-empty string")
    selected_source = selected_source.strip()

    default_mode = _normalize_mode(payload.get("mode"), field="mode")

    fairness_limit = payload.get("fairness_max_wait_rounds")
    if fairness_limit is not None and (
        isinstance(fairness_limit, bool)
        or not isinstance(fairness_limit, int)
        or fairness_limit < 0
    ):
        raise ValueError(
            "fairness_max_wait_rounds must be null or a non-negative integer"
        )

    rounds = [
        _normalize_round(
            item,
            index=index,
            default_mode=default_mode,
        )
        for index, item in enumerate(raw_rounds)
    ]

    violations: list[dict[str, Any]] = []
    seen_round_ids: set[str] = set()
    sequence_names: dict[tuple[str, str], str] = {}
    metrics: dict[tuple[str, str], dict[str, Any]] = {}
    fairness_reported: set[tuple[str, str]] = set()

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
                f"Arbitration round ID {round_id} was observed more than once",
            )
        seen_round_ids.add(round_id)

        contenders = round_data["contenders"]
        contender_ids = [candidate["sequence_id"] for candidate in contenders]
        unique_ids = set(contender_ids)
        duplicate_ids = sorted(
            {
                sequence_id
                for sequence_id in contender_ids
                if contender_ids.count(sequence_id) > 1
            }
        )
        for sequence_id in duplicate_ids:
            add_violation(
                "DUPLICATE_CONTENDER",
                round_data,
                f"Sequence {sequence_id} appears more than once "
                "in the same arbitration round",
                sequence_id=sequence_id,
            )

        winner = round_data["winner_sequence_id"]
        winner_is_contender = winner in unique_ids
        if not winner_is_contender:
            add_violation(
                "WINNER_NOT_CONTENDER",
                round_data,
                f"Winner {winner} is not present in the contender set",
                sequence_id=winner,
            )

        sequencer = round_data["sequencer"]
        for candidate in contenders:
            key = (sequencer, candidate["sequence_id"])
            name = candidate.get("sequence")
            previous_name = sequence_names.get(key)
            if (
                name is not None
                and previous_name is not None
                and name != previous_name
            ):
                add_violation(
                    "SEQUENCE_NAME_CHANGED",
                    round_data,
                    f"Sequence ID {candidate['sequence_id']} changed name "
                    f"from {previous_name} to {name}",
                    sequence_id=candidate["sequence_id"],
                )
            elif name is not None and previous_name is None:
                sequence_names[key] = name

        unique_contenders: list[dict[str, Any]] = []
        seen_in_round: set[str] = set()
        for candidate in contenders:
            if candidate["sequence_id"] in seen_in_round:
                continue
            seen_in_round.add(candidate["sequence_id"])
            unique_contenders.append(candidate)

        mode = round_data["mode"]
        if winner_is_contender and unique_contenders:
            if mode == "UVM_SEQ_ARB_FIFO" and all(
                candidate["request_order"] is not None
                for candidate in unique_contenders
            ):
                earliest = min(
                    candidate["request_order"]
                    for candidate in unique_contenders
                )
                eligible_winners = {
                    candidate["sequence_id"]
                    for candidate in unique_contenders
                    if candidate["request_order"] == earliest
                }
                if winner not in eligible_winners:
                    add_violation(
                        "FIFO_ORDER_MISMATCH",
                        round_data,
                        f"FIFO winner {winner} was not among the earliest "
                        "request-order contenders",
                        sequence_id=winner,
                    )

            if mode in {
                "UVM_SEQ_ARB_STRICT_FIFO",
                "UVM_SEQ_ARB_STRICT_RANDOM",
            } and all(
                candidate["priority"] is not None
                for candidate in unique_contenders
            ):
                highest_priority = max(
                    candidate["priority"]
                    for candidate in unique_contenders
                )
                highest_priority_ids = {
                    candidate["sequence_id"]
                    for candidate in unique_contenders
                    if candidate["priority"] == highest_priority
                }
                if winner not in highest_priority_ids:
                    add_violation(
                        "STRICT_PRIORITY_MISMATCH",
                        round_data,
                        f"Strict arbitration winner {winner} did not have "
                        f"the highest observed priority {highest_priority}",
                        sequence_id=winner,
                    )
                elif mode == "UVM_SEQ_ARB_STRICT_FIFO":
                    highest_priority_candidates = [
                        candidate
                        for candidate in unique_contenders
                        if candidate["priority"] == highest_priority
                    ]
                    if all(
                        candidate["request_order"] is not None
                        for candidate in highest_priority_candidates
                    ):
                        earliest = min(
                            candidate["request_order"]
                            for candidate in highest_priority_candidates
                        )
                        eligible_winners = {
                            candidate["sequence_id"]
                            for candidate in highest_priority_candidates
                            if candidate["request_order"] == earliest
                        }
                        if winner not in eligible_winners:
                            add_violation(
                                "STRICT_FIFO_ORDER_MISMATCH",
                                round_data,
                                f"Strict-FIFO winner {winner} was not earliest "
                                "among the highest-priority contenders",
                                sequence_id=winner,
                            )

        active_keys = {
            (sequencer, sequence_id)
            for sequence_id in unique_ids
        }
        for key, state in metrics.items():
            if key[0] == sequencer and key not in active_keys:
                state["current_wait_rounds"] = 0

        for candidate in unique_contenders:
            key = (sequencer, candidate["sequence_id"])
            state = metrics.get(key)
            if state is None:
                state = {
                    "sequencer": sequencer,
                    "sequence_id": candidate["sequence_id"],
                    "sequence": candidate.get("sequence"),
                    "eligible_rounds": 0,
                    "grants": 0,
                    "misses": 0,
                    "current_wait_rounds": 0,
                    "max_wait_rounds": 0,
                    "first_round_index": int(round_data["round_index"]),
                    "last_round_index": int(round_data["round_index"]),
                }
                metrics[key] = state
            elif (
                state.get("sequence") is None
                and candidate.get("sequence") is not None
            ):
                state["sequence"] = candidate["sequence"]

            state["eligible_rounds"] += 1
            state["last_round_index"] = int(round_data["round_index"])
            if (
                winner_is_contender
                and candidate["sequence_id"] == winner
            ):
                state["grants"] += 1
                state["current_wait_rounds"] = 0
            else:
                state["misses"] += 1
                state["current_wait_rounds"] += 1
                state["max_wait_rounds"] = max(
                    state["max_wait_rounds"],
                    state["current_wait_rounds"],
                )
                if (
                    fairness_limit is not None
                    and state["current_wait_rounds"] > fairness_limit
                    and key not in fairness_reported
                ):
                    add_violation(
                        "FAIRNESS_WAIT_EXCEEDED",
                        round_data,
                        f"Sequence {candidate['sequence_id']} waited "
                        f"{state['current_wait_rounds']} consecutive eligible "
                        "arbitration round(s), exceeding configured limit "
                        f"{fairness_limit}",
                        sequence_id=candidate["sequence_id"],
                    )
                    fairness_reported.add(key)

    fairness_sequences: list[dict[str, Any]] = []
    for state in metrics.values():
        eligible = int(state["eligible_rounds"])
        grants = int(state["grants"])
        fairness_sequences.append(
            {
                **state,
                "grant_rate": (
                    round((grants / eligible) * 100.0, 3)
                    if eligible
                    else 0.0
                ),
            }
        )

    fairness_sequences.sort(
        key=lambda item: (item["sequencer"], item["sequence_id"])
    )
    observed_max_wait = max(
        (
            int(item["max_wait_rounds"])
            for item in fairness_sequences
        ),
        default=0,
    )
    sequencers = sorted(
        {round_data["sequencer"] for round_data in rounds}
    )
    contended_rounds = sum(
        len(
            {
                candidate["sequence_id"]
                for candidate in round_data["contenders"]
            }
        )
        > 1
        for round_data in rounds
    )

    return {
        "analysis": "uvm_sequencer_arbitration",
        "source": selected_source,
        "status": "FAIL" if violations else "PASS",
        "summary": {
            "rounds": len(rounds),
            "sequencers": len(sequencers),
            "sequences": len(fairness_sequences),
            "contended_rounds": int(contended_rounds),
            "uncontended_rounds": len(rounds) - int(contended_rounds),
            "violations": len(violations),
            "observed_max_wait_rounds": observed_max_wait,
            "fairness_failures": sum(
                violation["code"] == "FAIRNESS_WAIT_EXCEEDED"
                for violation in violations
            ),
        },
        "supported_modes": list(_UVM_ARBITRATION_MODES),
        "default_mode": default_mode,
        "fairness": {
            "max_wait_rounds_limit": fairness_limit,
            "observed_max_wait_rounds": observed_max_wait,
            "sequences": fairness_sequences,
        },
        "rounds": rounds,
        "violations": violations,
        "limitations": [
            "Input is explicit normalized arbitration evidence; vendor simulator "
            "logs and internal sequencer queues are not inferred.",
            "Fairness metrics count only rounds where a sequence is explicitly "
            "listed as an eligible contender; absence from a contender set resets "
            "its consecutive-wait streak.",
            "Fairness becomes a pass/fail property only when "
            "fairness_max_wait_rounds is explicitly configured by the trace "
            "producer or user.",
            "FIFO ordering is checked only when request_order is explicit for "
            "every contender in a round; strict-mode priority is checked only "
            "when priority is explicit for every contender.",
            "Random, weighted, and user-defined arbitration choices are retained "
            "as evidence but are not statistically or semantically proven from "
            "a single selected winner.",
            "SQLite persistence/history and automatic simulator instrumentation "
            "are outside this foundation slice.",
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

    report = parse_uvm_arbitration_file(
        input_path,
        source=source,
    )

    created_at = datetime.now(timezone.utc).isoformat()
    snapshot_id = (
        datetime.now(timezone.utc).strftime("uvm-arb-%Y%m%dT%H%M%S")
        + "-"
        + uuid.uuid4().hex[:8]
    )

    snapshot_dir = (
        project.root
        / ".zddv"
        / "uvm"
        / "arbitration"
        / "snapshots"
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
