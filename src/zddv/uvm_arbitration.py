from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any
import uuid

from zddv.config import ProjectConfig
from zddv.storage import get_run_record, record_uvm_arbitration_snapshot


_IDENTITY_FIELDS = ("sequence_id", "sequence", "item_id", "priority", "sequencer")
_UVM_ARBITRATION_MODES = (
    "UVM_SEQ_ARB_FIFO",
    "UVM_SEQ_ARB_WEIGHTED",
    "UVM_SEQ_ARB_RANDOM",
    "UVM_SEQ_ARB_STRICT_FIFO",
    "UVM_SEQ_ARB_STRICT_RANDOM",
    "UVM_SEQ_ARB_USER",
)
_LOCAL_UNSPECIFIED_MODE = "UNSPECIFIED"
_POLICY_VIOLATION_CODES = {
    "FIFO_ORDER_VIOLATION",
    "STRICT_PRIORITY_VIOLATION",
    "STRICT_FIFO_ORDER_VIOLATION",
}


def _require_text(value: Any, *, field: str, context: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{context}.{field} must be a non-empty string")
    return value.strip()


def _optional_text(value: Any, *, field: str, context: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{context}.{field} must be null or a non-empty string")
    return value.strip()


def _optional_int(value: Any, *, field: str, context: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{context}.{field} must be null or an integer")
    return int(value)


def _optional_nonnegative_int(value: Any, *, field: str, context: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{context}.{field} must be null or an integer >= 0")
    return int(value)


def _normalize_mode(value: Any, *, context: str) -> str:
    if value is None:
        return _LOCAL_UNSPECIFIED_MODE
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{context} must be a non-empty string")
    mode = value.strip().upper()
    allowed = set(_UVM_ARBITRATION_MODES) | {_LOCAL_UNSPECIFIED_MODE}
    if mode not in allowed:
        raise ValueError(
            f"{context} must be one of: "
            f"{', '.join((*_UVM_ARBITRATION_MODES, _LOCAL_UNSPECIFIED_MODE))}"
        )
    return mode


def _fairness_bound(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError("fairness_bound must be null or an integer >= 0")
    return int(value)


def _normalize_contender(
    item: Any,
    *,
    decision_index: int,
    contender_index: int,
    sequencer: str,
) -> dict[str, Any]:
    context = f"decisions[{decision_index}].contenders[{contender_index}]"
    if not isinstance(item, dict):
        raise ValueError(f"{context} must be an object")
    metadata = item.get("metadata", {})
    if metadata is None:
        metadata = {}
    if not isinstance(metadata, dict):
        raise ValueError(f"{context}.metadata must be an object")
    return {
        "contender_index": contender_index,
        "request_id": _require_text(item.get("request_id"), field="request_id", context=context),
        "sequence_id": _require_text(item.get("sequence_id"), field="sequence_id", context=context),
        "sequence": _require_text(item.get("sequence"), field="sequence", context=context),
        "item_id": _optional_text(item.get("item_id"), field="item_id", context=context),
        "priority": _optional_int(item.get("priority"), field="priority", context=context),
        "request_order": _optional_nonnegative_int(
            item.get("request_order"),
            field="request_order",
            context=context,
        ),
        "sequencer": sequencer,
        "metadata": dict(metadata),
    }


def _normalize_decision(
    item: Any,
    *,
    index: int,
    default_mode: str,
) -> dict[str, Any]:
    context = f"decisions[{index}]"
    if not isinstance(item, dict):
        raise ValueError(f"{context} must be an object")
    sequencer = _require_text(item.get("sequencer"), field="sequencer", context=context)
    raw_contenders = item.get("contenders")
    if not isinstance(raw_contenders, list) or not raw_contenders:
        raise ValueError(f"{context}.contenders must be a non-empty array")
    metadata = item.get("metadata", {})
    if metadata is None:
        metadata = {}
    if not isinstance(metadata, dict):
        raise ValueError(f"{context}.metadata must be an object")
    return {
        "decision_index": index,
        "decision_id": _require_text(item.get("decision_id"), field="decision_id", context=context),
        "sequencer": sequencer,
        "mode": _normalize_mode(
            item.get("mode", default_mode),
            context=f"{context}.mode",
        ),
        "granted_request_id": _require_text(
            item.get("granted_request_id"),
            field="granted_request_id",
            context=context,
        ),
        "time": _optional_text(item.get("time"), field="time", context=context),
        "contenders": [
            _normalize_contender(
                contender,
                decision_index=index,
                contender_index=contender_index,
                sequencer=sequencer,
            )
            for contender_index, contender in enumerate(raw_contenders)
        ],
        "metadata": dict(metadata),
    }


def parse_uvm_arbitration_data(
    payload: Any,
    *,
    source: str | None = None,
    fairness_bound: int | None = None,
) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("UVM arbitration trace must be a JSON object")
    raw_decisions = payload.get("decisions")
    if not isinstance(raw_decisions, list):
        raise ValueError("UVM arbitration trace must contain a decisions array")

    selected_source = source or payload.get("source") or "uvm-arbitration-json"
    if not isinstance(selected_source, str) or not selected_source.strip():
        raise ValueError("source must be a non-empty string")
    selected_source = selected_source.strip()

    bound = _fairness_bound(
        fairness_bound if fairness_bound is not None else payload.get("fairness_bound")
    )
    default_mode = _normalize_mode(payload.get("mode"), context="mode")
    decisions = [
        _normalize_decision(item, index=index, default_mode=default_mode)
        for index, item in enumerate(raw_decisions)
    ]

    violations: list[dict[str, Any]] = []
    policy_checks: list[dict[str, Any]] = []
    requests: dict[str, dict[str, Any]] = {}
    decision_ids: set[str] = set()
    fairness_reported: set[str] = set()

    def add_violation(
        code: str,
        decision: dict[str, Any],
        message: str,
        *,
        request_id: str | None = None,
    ) -> None:
        violations.append(
            {
                "violation_index": len(violations),
                "code": code,
                "decision_index": int(decision["decision_index"]),
                "decision_id": decision["decision_id"],
                "request_id": request_id,
                "message": message,
            }
        )

    for decision in decisions:
        decision_id = decision["decision_id"]
        if decision_id in decision_ids:
            add_violation(
                "DUPLICATE_DECISION_ID",
                decision,
                f"Decision ID {decision_id} appeared more than once",
            )
        decision_ids.add(decision_id)

        seen_in_decision: set[str] = set()
        contender_ids: set[str] = set()
        for contender in decision["contenders"]:
            request_id = contender["request_id"]
            contender_ids.add(request_id)
            if request_id in seen_in_decision:
                add_violation(
                    "DUPLICATE_CONTENDER",
                    decision,
                    f"Request {request_id} appears more than once in one arbitration decision",
                    request_id=request_id,
                )
                continue
            seen_in_decision.add(request_id)

            request = requests.get(request_id)
            if request is None:
                request = {
                    "request_id": request_id,
                    "sequence_id": contender["sequence_id"],
                    "sequence": contender["sequence"],
                    "item_id": contender.get("item_id"),
                    "priority": contender.get("priority"),
                    "sequencer": contender["sequencer"],
                    "decision_indices": [],
                    "exposure_count": 0,
                    "lost_decisions": 0,
                    "granted": False,
                    "grant_decision_index": None,
                    "grant_decision_id": None,
                }
                requests[request_id] = request
            else:
                for field in _IDENTITY_FIELDS:
                    current = contender.get(field)
                    previous = request.get(field)
                    if current is not None and previous is not None and current != previous:
                        add_violation(
                            "REQUEST_IDENTITY_CHANGED",
                            decision,
                            f"Request {request_id} changed {field} from {previous} to {current}",
                            request_id=request_id,
                        )
                    elif previous is None and current is not None:
                        request[field] = current

            if request["granted"]:
                add_violation(
                    "GRANTED_REQUEST_REAPPEARED",
                    decision,
                    f"Request {request_id} reappeared after it was already granted",
                    request_id=request_id,
                )

            request["decision_indices"].append(decision["decision_index"])
            request["exposure_count"] += 1

        granted_id = decision["granted_request_id"]
        if granted_id not in contender_ids:
            add_violation(
                "GRANT_NOT_A_CONTENDER",
                decision,
                f"Granted request {granted_id} is not present in the contender set",
                request_id=granted_id,
            )
            granted_id = None

        unique_contenders = []
        policy_seen: set[str] = set()
        for contender in decision["contenders"]:
            if contender["request_id"] in policy_seen:
                continue
            policy_seen.add(contender["request_id"])
            unique_contenders.append(contender)

        policy = {
            "decision_index": int(decision["decision_index"]),
            "decision_id": decision["decision_id"],
            "mode": decision["mode"],
            "check_status": "OBSERVATIONAL",
            "checked_constraints": [],
            "missing_evidence": [],
            "eligible_request_ids": [item["request_id"] for item in unique_contenders],
            "expected_request_ids": None,
            "highest_priority": None,
            "earliest_request_order": None,
            "violation_codes": [],
        }

        def policy_violation(code: str, message: str) -> None:
            add_violation(
                code,
                decision,
                message,
                request_id=decision["granted_request_id"],
            )
            policy["violation_codes"].append(code)

        mode = decision["mode"]
        if granted_id is None:
            policy["check_status"] = "SKIPPED"
            policy["missing_evidence"].append("valid_grant")
        elif mode == "UVM_SEQ_ARB_FIFO":
            if all(item.get("request_order") is not None for item in unique_contenders):
                earliest = min(int(item["request_order"]) for item in unique_contenders)
                expected = [
                    item["request_id"]
                    for item in unique_contenders
                    if int(item["request_order"]) == earliest
                ]
                policy["check_status"] = "CHECKED"
                policy["checked_constraints"].append("fifo_request_order")
                policy["earliest_request_order"] = earliest
                policy["expected_request_ids"] = expected
                if granted_id not in expected:
                    policy_violation(
                        "FIFO_ORDER_VIOLATION",
                        f"FIFO grant {granted_id} did not select an earliest explicit request_order={earliest} contender",
                    )
            else:
                policy["check_status"] = "PARTIAL"
                policy["missing_evidence"].append("request_order")
        elif mode in {"UVM_SEQ_ARB_STRICT_FIFO", "UVM_SEQ_ARB_STRICT_RANDOM"}:
            if all(item.get("priority") is not None for item in unique_contenders):
                highest = max(int(item["priority"]) for item in unique_contenders)
                highest_items = [
                    item for item in unique_contenders if int(item["priority"]) == highest
                ]
                highest_ids = [item["request_id"] for item in highest_items]
                policy["highest_priority"] = highest
                policy["eligible_request_ids"] = highest_ids
                policy["checked_constraints"].append("highest_priority")
                if granted_id not in highest_ids:
                    policy_violation(
                        "STRICT_PRIORITY_VIOLATION",
                        f"Strict arbitration grant {granted_id} is not in the highest explicit priority={highest} contender set",
                    )

                if mode == "UVM_SEQ_ARB_STRICT_RANDOM":
                    policy["check_status"] = "CHECKED"
                    policy["expected_request_ids"] = highest_ids
                elif len(highest_items) == 1:
                    policy["check_status"] = "CHECKED"
                    policy["expected_request_ids"] = highest_ids
                elif all(item.get("request_order") is not None for item in highest_items):
                    earliest = min(int(item["request_order"]) for item in highest_items)
                    expected = [
                        item["request_id"]
                        for item in highest_items
                        if int(item["request_order"]) == earliest
                    ]
                    policy["check_status"] = "CHECKED"
                    policy["checked_constraints"].append("strict_fifo_request_order")
                    policy["earliest_request_order"] = earliest
                    policy["expected_request_ids"] = expected
                    if granted_id in highest_ids and granted_id not in expected:
                        policy_violation(
                            "STRICT_FIFO_ORDER_VIOLATION",
                            f"STRICT_FIFO grant {granted_id} did not select an earliest explicit request_order={earliest} highest-priority contender",
                        )
                else:
                    policy["check_status"] = "PARTIAL"
                    policy["expected_request_ids"] = highest_ids
                    policy["missing_evidence"].append("request_order")
            else:
                policy["check_status"] = "PARTIAL"
                policy["missing_evidence"].append("priority")
        elif mode in {
            _LOCAL_UNSPECIFIED_MODE,
            "UVM_SEQ_ARB_RANDOM",
            "UVM_SEQ_ARB_WEIGHTED",
            "UVM_SEQ_ARB_USER",
        }:
            policy["check_status"] = "OBSERVATIONAL"

        policy_checks.append(policy)

        for request_id in seen_in_decision:
            request = requests[request_id]
            if request_id == granted_id:
                if request["granted"]:
                    add_violation(
                        "REQUEST_GRANTED_TWICE",
                        decision,
                        f"Request {request_id} was granted more than once",
                        request_id=request_id,
                    )
                else:
                    request["granted"] = True
                    request["grant_decision_index"] = decision["decision_index"]
                    request["grant_decision_id"] = decision["decision_id"]
            else:
                request["lost_decisions"] += 1
                if (
                    bound is not None
                    and request["lost_decisions"] > bound
                    and request_id not in fairness_reported
                ):
                    add_violation(
                        "FAIRNESS_BOUND_EXCEEDED",
                        decision,
                        f"Request {request_id} lost {request['lost_decisions']} observed arbitration decisions, exceeding fairness_bound={bound}",
                        request_id=request_id,
                    )
                    fairness_reported.add(request_id)

    normalized_requests = [
        {
            **request,
            "pending": not request["granted"],
        }
        for request in requests.values()
    ]
    grant_counts_by_sequence: dict[str, int] = {}
    for request in normalized_requests:
        if request["granted"]:
            sequence = request["sequence"]
            grant_counts_by_sequence[sequence] = grant_counts_by_sequence.get(sequence, 0) + 1

    fairness_violations = sum(
        violation["code"] == "FAIRNESS_BOUND_EXCEEDED" for violation in violations
    )
    policy_violations = sum(
        violation["code"] in _POLICY_VIOLATION_CODES for violation in violations
    )
    return {
        "analysis": "uvm_arbitration_fairness",
        "source": selected_source,
        "mode": default_mode,
        "status": "FAIL" if violations else "PASS",
        "fairness_bound": bound,
        "summary": {
            "decisions": len(decisions),
            "requests": len(normalized_requests),
            "grants": sum(request["granted"] for request in normalized_requests),
            "pending": sum(request["pending"] for request in normalized_requests),
            "violations": len(violations),
            "fairness_violations": fairness_violations,
            "policy_violations": policy_violations,
            "policy_checked_decisions": sum(
                item["check_status"] == "CHECKED" for item in policy_checks
            ),
            "policy_partial_decisions": sum(
                item["check_status"] == "PARTIAL" for item in policy_checks
            ),
            "policy_observational_decisions": sum(
                item["check_status"] == "OBSERVATIONAL" for item in policy_checks
            ),
            "max_wait_decisions": max(
                (int(request["lost_decisions"]) for request in normalized_requests),
                default=0,
            ),
        },
        "grant_counts_by_sequence": dict(sorted(grant_counts_by_sequence.items())),
        "policy_checks": policy_checks,
        "decisions": decisions,
        "requests": normalized_requests,
        "violations": violations,
        "limitations": [
            "Input is explicit normalized arbitration evidence; vendor simulator logs are not guessed or reinterpreted.",
            "fairness_bound is a project-defined maximum observed losing-decision count, not an Accellera UVM fairness guarantee or policy default.",
            "UVM arbitration policy is checked only when mode and the required priority/request_order evidence are explicit in the trace.",
            "Random, weighted, user-defined, and unspecified selections remain observational; ZDDV does not invent a deterministic winner.",
            "This layer does not infer hidden queues, lock/grab state, weighted/random selection probabilities, delta-cycle timing, or transaction payload semantics.",
        ],
    }


def parse_uvm_arbitration_file(
    path: str | Path,
    *,
    source: str | None = None,
    fairness_bound: int | None = None,
) -> dict[str, Any]:
    input_path = Path(path)
    payload = json.loads(input_path.read_text(encoding="utf-8"))
    return parse_uvm_arbitration_data(
        payload,
        source=source,
        fairness_bound=fairness_bound,
    )


def analyze_uvm_arbitration_file(
    project: ProjectConfig,
    path: str | Path,
    *,
    source: str | None = None,
    fairness_bound: int | None = None,
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
        fairness_bound=fairness_bound,
    )
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
