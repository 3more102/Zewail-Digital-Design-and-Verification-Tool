from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from zddv.config import ProjectConfig


def _logic(value: Any, *, name: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"0", "false", "low"}:
            return False
        if normalized in {"1", "true", "high"}:
            return True
    raise ValueError(f"{name} must be a 0/1 logic value, got {value!r}")


def _integer(value: Any, *, name: str) -> int:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value.strip(), 0)
        except ValueError:
            pass
    raise ValueError(f"{name} must be an integer value, got {value!r}")


def _gray(binary: int) -> int:
    return binary ^ (binary >> 1)


def _hamming_distance(left: int, right: int) -> int:
    return (left ^ right).bit_count()


def _normalize_event(
    raw: dict[str, Any],
    index: int,
    *,
    domain: str,
    blocked_name: str,
    pointer_mask: int,
) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError(f"{domain} event {index} must be an object")

    lower = {str(key).lower(): value for key, value in raw.items()}
    event: dict[str, Any] = {
        "index": index,
        "cycle": lower.get("cycle", index),
        "reset": _logic(lower.get("reset", False), name=f"{domain}.reset"),
    }
    if "time" in lower:
        event["time"] = lower["time"]

    required = (
        "binary_before",
        "binary_after",
        "gray_before",
        "gray_after",
    )
    missing = [name for name in required if name not in lower]
    if missing:
        raise ValueError(
            f"{domain} event {index} is missing required field(s): "
            + ", ".join(missing)
        )

    for name in required:
        value = _integer(lower[name], name=f"{domain}.{name}")
        if value < 0 or value > pointer_mask:
            raise ValueError(
                f"{domain}.{name}={value} does not fit the configured pointer width"
            )
        event[name] = value

    if event["reset"]:
        event["request"] = _logic(
            lower.get("request", False),
            name=f"{domain}.request",
        )
        event["blocked"] = _logic(
            lower.get(blocked_name, lower.get("blocked", False)),
            name=f"{domain}.{blocked_name}",
        )
        event["accepted"] = _logic(
            lower.get("accepted", False),
            name=f"{domain}.accepted",
        )
        return event

    for name in ("request", "accepted"):
        if name not in lower:
            raise ValueError(f"{domain} event {index} is missing required field: {name}")

    blocked_value = lower.get(blocked_name, lower.get("blocked"))
    if blocked_value is None:
        raise ValueError(
            f"{domain} event {index} is missing required field: {blocked_name}"
        )

    event["request"] = _logic(lower["request"], name=f"{domain}.request")
    event["blocked"] = _logic(blocked_value, name=f"{domain}.{blocked_name}")
    event["accepted"] = _logic(lower["accepted"], name=f"{domain}.accepted")
    return event


def analyze_async_fifo_trace(payload: dict[str, Any]) -> dict[str, Any]:
    """Check dynamic asynchronous-FIFO pointer and flow-control invariants.

    The normalized event model is intentionally local to each clock domain. It
    verifies local binary/Gray pointer behavior and full/empty blocking semantics.
    It does not claim static CDC signoff, metastability analysis, synchronizer MTBF
    analysis, or prove the physical implementation of synchronizer chains.
    """
    if not isinstance(payload, dict):
        raise ValueError("Async FIFO CDC trace must be a JSON object")

    pointer_width = _integer(payload.get("pointer_width"), name="pointer_width")
    if pointer_width < 2 or pointer_width > 64:
        raise ValueError("pointer_width must be in range 2..64")

    raw_write = payload.get("write_events", [])
    raw_read = payload.get("read_events", [])
    if not isinstance(raw_write, list):
        raise ValueError("write_events must be a list")
    if not isinstance(raw_read, list):
        raise ValueError("read_events must be a list")
    if not raw_write and not raw_read:
        raise ValueError("Async FIFO CDC trace must contain write_events or read_events")

    pointer_modulus = 1 << pointer_width
    pointer_mask = pointer_modulus - 1

    domains = (
        (
            "write",
            "full",
            [
                _normalize_event(
                    event,
                    index,
                    domain="write",
                    blocked_name="full",
                    pointer_mask=pointer_mask,
                )
                for index, event in enumerate(raw_write)
            ],
        ),
        (
            "read",
            "empty",
            [
                _normalize_event(
                    event,
                    index,
                    domain="read",
                    blocked_name="empty",
                    pointer_mask=pointer_mask,
                )
                for index, event in enumerate(raw_read)
            ],
        ),
    )

    violations: list[dict[str, Any]] = []
    normalized_events: dict[str, list[dict[str, Any]]] = {
        "write": [],
        "read": [],
    }
    counters = {
        "write_events": len(raw_write),
        "read_events": len(raw_read),
        "accepted_writes": 0,
        "accepted_reads": 0,
        "blocked_write_requests": 0,
        "blocked_read_requests": 0,
        "reset_events": 0,
    }

    def add_violation(
        code: str,
        event: dict[str, Any],
        message: str,
        *,
        domain: str,
        signal: str | None = None,
        expected: Any = None,
        actual: Any = None,
    ) -> None:
        item: dict[str, Any] = {
            "code": code,
            "domain": domain,
            "event_index": event["index"],
            "cycle": event["cycle"],
            "message": message,
        }
        if event.get("time") is not None:
            item["time"] = event["time"]
        if signal is not None:
            item["signal"] = signal
            item["expected"] = expected
            item["actual"] = actual
        violations.append(item)

    for domain, blocked_name, events in domains:
        accepted_counter = (
            "accepted_writes" if domain == "write" else "accepted_reads"
        )
        blocked_counter = (
            "blocked_write_requests"
            if domain == "write"
            else "blocked_read_requests"
        )

        previous_after: int | None = None
        previous_gray_after: int | None = None

        for event in events:
            normalized_events[domain].append(dict(event))
            before = event["binary_before"]
            after = event["binary_after"]
            gray_before = event["gray_before"]
            gray_after = event["gray_after"]

            if previous_after is not None and before != previous_after:
                add_violation(
                    "binary_trace_discontinuity",
                    event,
                    "binary_before does not match the previous event binary_after",
                    domain=domain,
                    signal="binary_before",
                    expected=previous_after,
                    actual=before,
                )
            if previous_gray_after is not None and gray_before != previous_gray_after:
                add_violation(
                    "gray_trace_discontinuity",
                    event,
                    "gray_before does not match the previous event gray_after",
                    domain=domain,
                    signal="gray_before",
                    expected=previous_gray_after,
                    actual=gray_before,
                )

            expected_gray_before = _gray(before)
            expected_gray_after = _gray(after)
            if gray_before != expected_gray_before:
                add_violation(
                    "gray_encoding_mismatch",
                    event,
                    "gray_before is not the Gray encoding of binary_before",
                    domain=domain,
                    signal="gray_before",
                    expected=expected_gray_before,
                    actual=gray_before,
                )
            if gray_after != expected_gray_after:
                add_violation(
                    "gray_encoding_mismatch",
                    event,
                    "gray_after is not the Gray encoding of binary_after",
                    domain=domain,
                    signal="gray_after",
                    expected=expected_gray_after,
                    actual=gray_after,
                )

            if event["reset"]:
                counters["reset_events"] += 1
                if after != 0:
                    add_violation(
                        "reset_binary_not_zero",
                        event,
                        "local binary pointer must be zero after a normalized reset event",
                        domain=domain,
                        signal="binary_after",
                        expected=0,
                        actual=after,
                    )
                if gray_after != 0:
                    add_violation(
                        "reset_gray_not_zero",
                        event,
                        "local Gray pointer must be zero after a normalized reset event",
                        domain=domain,
                        signal="gray_after",
                        expected=0,
                        actual=gray_after,
                    )
                previous_after = after
                previous_gray_after = gray_after
                continue

            request = event["request"]
            blocked = event["blocked"]
            accepted = event["accepted"]
            expected_accepted = request and not blocked

            if request and blocked:
                counters[blocked_counter] += 1
            if accepted:
                counters[accepted_counter] += 1

            if accepted != expected_accepted:
                add_violation(
                    "acceptance_mismatch",
                    event,
                    (
                        f"accepted must equal request && !{blocked_name} "
                        "in the normalized local-domain event model"
                    ),
                    domain=domain,
                    signal="accepted",
                    expected=expected_accepted,
                    actual=accepted,
                )

            if accepted:
                expected_after = (before + 1) & pointer_mask
                if after != expected_after:
                    add_violation(
                        "binary_pointer_increment_error",
                        event,
                        "accepted operation must increment the local binary pointer by one",
                        domain=domain,
                        signal="binary_after",
                        expected=expected_after,
                        actual=after,
                    )
            elif after != before:
                add_violation(
                    "binary_pointer_changed_without_accept",
                    event,
                    "local binary pointer changed without an accepted FIFO operation",
                    domain=domain,
                    signal="binary_after",
                    expected=before,
                    actual=after,
                )

            gray_distance = _hamming_distance(gray_before, gray_after)
            expected_distance = 1 if accepted else 0
            if gray_distance != expected_distance:
                add_violation(
                    "gray_transition_error",
                    event,
                    (
                        "local Gray pointer must change by exactly one bit on an "
                        "accepted operation and remain stable otherwise"
                    ),
                    domain=domain,
                    signal="gray_after",
                    expected=f"Hamming distance {expected_distance}",
                    actual=f"Hamming distance {gray_distance}",
                )

            previous_after = after
            previous_gray_after = gray_after

    result = {
        "analysis": "async_fifo_cdc_dynamic",
        "source": str(payload.get("source", "normalized-event-trace")),
        "status": "PASS" if not violations else "FAIL",
        "scope": {
            "pointer_width": pointer_width,
            "pointer_modulus": pointer_modulus,
            "checks": [
                "local_binary_pointer_progression",
                "binary_to_gray_encoding",
                "one_bit_local_gray_transition",
                "full_empty_blocking_semantics",
                "trace_continuity",
                "normalized_reset_zeroing",
            ],
            "not_static_cdc_signoff": True,
        },
        "summary": {
            **counters,
            "events": len(raw_write) + len(raw_read),
            "violations": len(violations),
        },
        "events": normalized_events,
        "violations": violations,
    }
    return result


def analyze_async_fifo_file(
    project: ProjectConfig,
    path: str | Path,
    *,
    output: str | Path | None = None,
) -> dict[str, Any]:
    source = Path(path)
    if not source.is_absolute():
        source = project.root / source
    source = source.resolve()

    payload = json.loads(source.read_text(encoding="utf-8"))
    report = analyze_async_fifo_trace(payload)
    report["input_path"] = str(source)

    destination = (
        Path(output)
        if output is not None
        else Path(".zddv/cdc/async-fifo/latest.json")
    )
    if not destination.is_absolute():
        destination = project.root / destination
    destination = destination.resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    report["report_path"] = str(destination)
    return report
