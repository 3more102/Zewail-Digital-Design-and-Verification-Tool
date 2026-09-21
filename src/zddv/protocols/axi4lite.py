from __future__ import annotations

from collections import deque
import json
from pathlib import Path
from typing import Any

from zddv.config import ProjectConfig


_CHANNELS = {
    "AW": {
        "valid": "AWVALID",
        "ready": "AWREADY",
        "payload": ("AWADDR", "AWPROT"),
        "required": ("AWADDR",),
    },
    "W": {
        "valid": "WVALID",
        "ready": "WREADY",
        "payload": ("WDATA", "WSTRB"),
        "required": ("WDATA", "WSTRB"),
    },
    "B": {
        "valid": "BVALID",
        "ready": "BREADY",
        "payload": ("BRESP",),
        "required": ("BRESP",),
    },
    "AR": {
        "valid": "ARVALID",
        "ready": "ARREADY",
        "payload": ("ARADDR", "ARPROT"),
        "required": ("ARADDR",),
    },
    "R": {
        "valid": "RVALID",
        "ready": "RREADY",
        "payload": ("RDATA", "RRESP"),
        "required": ("RDATA", "RRESP"),
    },
}

_RESPONSES = {
    0: "OKAY",
    1: "EXOKAY",
    2: "SLVERR",
    3: "DECERR",
}
_RESPONSE_NAMES = {name: code for code, name in _RESPONSES.items()}


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


def _scalar(value: Any) -> Any:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        text = value.strip()
        try:
            return int(text, 0)
        except ValueError:
            return text
    return value


def _normalize_sample(raw: dict[str, Any], index: int) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError(f"AXI4-Lite sample {index} must be an object")

    upper = {str(key).upper(): value for key, value in raw.items()}
    sample: dict[str, Any] = {
        "sample_index": index,
        "cycle": upper.get("CYCLE", index),
    }

    for channel in _CHANNELS.values():
        for name in (channel["valid"], channel["ready"]):
            if name in sample:
                continue
            if name in upper:
                sample[name] = _logic(upper[name], name=name)
            else:
                sample[name] = False

    for name in (
        "AWADDR",
        "AWPROT",
        "WDATA",
        "WSTRB",
        "BRESP",
        "ARADDR",
        "ARPROT",
        "RDATA",
        "RRESP",
    ):
        if name in upper:
            sample[name] = _scalar(upper[name])
    return sample


def _decode_response(value: Any) -> tuple[int | None, str]:
    if value is None:
        return None, "MISSING"
    if isinstance(value, str):
        normalized = value.strip().upper()
        if normalized in _RESPONSE_NAMES:
            code = _RESPONSE_NAMES[normalized]
            return code, normalized
    normalized = _scalar(value)
    if isinstance(normalized, int) and normalized in _RESPONSES:
        return normalized, _RESPONSES[normalized]
    return None, str(value)


def analyze_axi4lite_trace(payload: dict[str, Any]) -> dict[str, Any]:
    """Reconstruct AXI4-Lite transactions from clock-edge samples.

    The analyzer treats the five AXI4-Lite channels independently, enforces
    VALID/payload stability while READY is LOW, pairs write address/data in
    acceptance order, and pairs responses in-order because AXI4-Lite has no IDs.
    """
    if not isinstance(payload, dict):
        raise ValueError("AXI4-Lite trace must be a JSON object")
    raw_samples = payload.get("samples")
    if not isinstance(raw_samples, list):
        raise ValueError("AXI4-Lite trace must contain a 'samples' list")

    samples = [_normalize_sample(sample, index) for index, sample in enumerate(raw_samples)]
    transactions: list[dict[str, Any]] = []
    violations: list[dict[str, Any]] = []

    aw_queue: deque[dict[str, Any]] = deque()
    w_queue: deque[dict[str, Any]] = deque()
    pending_writes: deque[dict[str, Any]] = deque()
    pending_reads: deque[dict[str, Any]] = deque()

    channel_state: dict[str, dict[str, Any] | None] = {
        name: None for name in _CHANNELS
    }
    stall_cycles = {name: 0 for name in _CHANNELS}
    issued_writes = 0
    issued_reads = 0

    def add_violation(
        code: str,
        sample: dict[str, Any],
        message: str,
        *,
        channel: str | None = None,
        transaction_index: int | None = None,
        signal: str | None = None,
        expected: Any = None,
        actual: Any = None,
    ) -> None:
        entry: dict[str, Any] = {
            "code": code,
            "sample_index": sample["sample_index"],
            "cycle": sample["cycle"],
            "message": message,
        }
        if channel is not None:
            entry["channel"] = channel
        if transaction_index is not None:
            entry["transaction_index"] = transaction_index
        if signal is not None:
            entry["signal"] = signal
            entry["expected"] = expected
            entry["actual"] = actual
        violations.append(entry)

    def channel_event(
        sample: dict[str, Any],
        channel_name: str,
    ) -> tuple[bool, bool]:
        spec = _CHANNELS[channel_name]
        valid = bool(sample[spec["valid"]])
        ready = bool(sample[spec["ready"]])
        state = channel_state[channel_name]

        if valid and not ready:
            stall_cycles[channel_name] += 1

        if state is not None:
            if not valid:
                add_violation(
                    "valid_dropped_before_handshake",
                    sample,
                    f"{spec['valid']} deasserted before {spec['ready']} completed the handshake",
                    channel=channel_name,
                    signal=spec["valid"],
                    expected=1,
                    actual=0,
                )
                channel_state[channel_name] = None
                return False, False

            for field, expected in state["payload"].items():
                if field not in sample:
                    add_violation(
                        "payload_missing_while_stalled",
                        sample,
                        f"{field} disappeared while {spec['valid']} remained asserted",
                        channel=channel_name,
                        signal=field,
                        expected=expected,
                        actual=None,
                    )
                    continue
                if sample[field] != expected:
                    add_violation(
                        "payload_changed_while_stalled",
                        sample,
                        f"{field} changed before the {channel_name} handshake completed",
                        channel=channel_name,
                        signal=field,
                        expected=expected,
                        actual=sample[field],
                    )

            if ready:
                channel_state[channel_name] = None
                return True, False
            return False, False

        if not valid:
            return False, False

        for field in spec["required"]:
            if field not in sample:
                add_violation(
                    "missing_channel_payload",
                    sample,
                    f"{field} must be valid while {spec['valid']} is asserted",
                    channel=channel_name,
                    signal=field,
                )

        if ready:
            return True, True

        channel_state[channel_name] = {
            "start_sample_index": sample["sample_index"],
            "start_cycle": sample["cycle"],
            "payload": {
                field: sample[field]
                for field in spec["payload"]
                if field in sample
            },
        }
        return False, True

    def validate_response(
        sample: dict[str, Any],
        *,
        channel: str,
        field: str,
        transaction_index: int | None = None,
    ) -> tuple[int | None, str]:
        code, label = _decode_response(sample.get(field))
        if code == 1:
            add_violation(
                "exclusive_response_not_supported",
                sample,
                f"{field}=EXOKAY is not supported by AXI4-Lite",
                channel=channel,
                transaction_index=transaction_index,
                signal=field,
                expected="OKAY/SLVERR/DECERR",
                actual=label,
            )
        elif code is None:
            add_violation(
                "invalid_response",
                sample,
                f"{field} is not a valid AXI4-Lite response",
                channel=channel,
                transaction_index=transaction_index,
                signal=field,
                expected="OKAY/SLVERR/DECERR",
                actual=sample.get(field),
            )
        return code, label

    for sample in samples:
        events: dict[str, tuple[bool, bool]] = {
            channel: channel_event(sample, channel)
            for channel in _CHANNELS
        }

        aw_hs, _ = events["AW"]
        w_hs, _ = events["W"]
        b_hs, b_started = events["B"]
        ar_hs, _ = events["AR"]
        r_hs, r_started = events["R"]

        if aw_hs:
            aw_queue.append(
                {
                    "sample_index": sample["sample_index"],
                    "cycle": sample["cycle"],
                    "address": sample.get("AWADDR"),
                    "prot": sample.get("AWPROT"),
                }
            )
        if w_hs:
            w_queue.append(
                {
                    "sample_index": sample["sample_index"],
                    "cycle": sample["cycle"],
                    "data": sample.get("WDATA"),
                    "strb": sample.get("WSTRB"),
                }
            )

        while aw_queue and w_queue:
            aw = aw_queue.popleft()
            w = w_queue.popleft()
            pending_writes.append(
                {
                    "index": issued_writes,
                    "aw": aw,
                    "w": w,
                    "request_sample_index": max(aw["sample_index"], w["sample_index"]),
                }
            )
            issued_writes += 1

        if ar_hs:
            pending_reads.append(
                {
                    "index": issued_reads,
                    "sample_index": sample["sample_index"],
                    "cycle": sample["cycle"],
                    "address": sample.get("ARADDR"),
                    "prot": sample.get("ARPROT"),
                }
            )
            issued_reads += 1

        if b_started:
            tx_index = pending_writes[0]["index"] if pending_writes else None
            validate_response(
                sample,
                channel="B",
                field="BRESP",
                transaction_index=tx_index,
            )
            if not pending_writes:
                add_violation(
                    "write_response_before_request",
                    sample,
                    "BVALID was asserted before a complete write request was accepted",
                    channel="B",
                )

        if r_started:
            tx_index = pending_reads[0]["index"] if pending_reads else None
            validate_response(
                sample,
                channel="R",
                field="RRESP",
                transaction_index=tx_index,
            )
            if not pending_reads:
                add_violation(
                    "read_response_before_request",
                    sample,
                    "RVALID was asserted before a read address was accepted",
                    channel="R",
                )

        if b_hs:
            if pending_writes:
                request = pending_writes.popleft()
                code, label = _decode_response(sample.get("BRESP"))
                aw = request["aw"]
                w = request["w"]
                transaction = {
                    "index": len(transactions),
                    "protocol_index": request["index"],
                    "direction": "WRITE",
                    "address": aw["address"],
                    "write_data": w["data"],
                    "wstrb": w["strb"],
                    "aw_cycle": aw["cycle"],
                    "w_cycle": w["cycle"],
                    "response_cycle": sample["cycle"],
                    "response": label,
                    "response_code": code,
                    "response_wait_samples": max(
                        0,
                        sample["sample_index"] - request["request_sample_index"],
                    ),
                }
                if aw.get("prot") is not None:
                    transaction["awprot"] = aw["prot"]
                transactions.append(transaction)
            elif not b_started:
                add_violation(
                    "write_response_without_request",
                    sample,
                    "Write response handshake occurred without a pending write request",
                    channel="B",
                )

        if r_hs:
            if pending_reads:
                request = pending_reads.popleft()
                code, label = _decode_response(sample.get("RRESP"))
                transaction = {
                    "index": len(transactions),
                    "protocol_index": request["index"],
                    "direction": "READ",
                    "address": request["address"],
                    "read_data": sample.get("RDATA"),
                    "ar_cycle": request["cycle"],
                    "response_cycle": sample["cycle"],
                    "response": label,
                    "response_code": code,
                    "response_wait_samples": max(
                        0,
                        sample["sample_index"] - request["sample_index"],
                    ),
                }
                if request.get("prot") is not None:
                    transaction["arprot"] = request["prot"]
                transactions.append(transaction)
            elif not r_started:
                add_violation(
                    "read_response_without_request",
                    sample,
                    "Read data handshake occurred without a pending read request",
                    channel="R",
                )

    if samples:
        last = samples[-1]

        for channel_name, state in channel_state.items():
            if state is None:
                continue
            add_violation(
                "channel_stalled_at_end",
                last,
                f"Trace ended while {channel_name} VALID was waiting for READY",
                channel=channel_name,
            )

        for _item in aw_queue:
            add_violation(
                "write_address_without_data",
                last,
                "Accepted write address was not paired with accepted write data before trace end",
                channel="AW",
            )
        for _item in w_queue:
            add_violation(
                "write_data_without_address",
                last,
                "Accepted write data was not paired with an accepted write address before trace end",
                channel="W",
            )
        for request in pending_writes:
            add_violation(
                "missing_write_response",
                last,
                "Accepted write request did not receive a write response before trace end",
                channel="B",
                transaction_index=request["index"],
            )
        for request in pending_reads:
            add_violation(
                "missing_read_response",
                last,
                "Accepted read request did not receive read data before trace end",
                channel="R",
                transaction_index=request["index"],
            )

    writes = [tx for tx in transactions if tx["direction"] == "WRITE"]
    reads = [tx for tx in transactions if tx["direction"] == "READ"]
    error_responses = sum(
        1 for tx in transactions if tx.get("response") in {"SLVERR", "DECERR"}
    )

    return {
        "protocol": "AXI4-Lite",
        "source": str(payload.get("source", "normalized-trace")),
        "status": "PASS" if not violations else "FAIL",
        "summary": {
            "samples": len(samples),
            "issued_write_requests": issued_writes,
            "issued_read_requests": issued_reads,
            "completed_transactions": len(transactions),
            "reads": len(reads),
            "writes": len(writes),
            "error_responses": error_responses,
            "violations": len(violations),
            "channel_stall_cycles": stall_cycles,
        },
        "transactions": transactions,
        "violations": violations,
    }


def analyze_axi4lite_file(
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
    report = analyze_axi4lite_trace(payload)
    report["input_path"] = str(source)

    destination = (
        Path(output)
        if output is not None
        else Path(".zddv/protocols/axi4lite/latest.json")
    )
    if not destination.is_absolute():
        destination = project.root / destination
    destination = destination.resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    report["report_path"] = str(destination)
    return report
