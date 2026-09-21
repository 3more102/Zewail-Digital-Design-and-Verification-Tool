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
        "payload": (
            "AWID", "AWADDR", "AWLEN", "AWSIZE", "AWBURST",
            "AWLOCK", "AWCACHE", "AWPROT", "AWQOS",
        ),
        "required": ("AWADDR", "AWLEN", "AWSIZE", "AWBURST"),
    },
    "W": {
        "valid": "WVALID",
        "ready": "WREADY",
        "payload": ("WDATA", "WSTRB", "WLAST"),
        "required": ("WDATA", "WSTRB", "WLAST"),
    },
    "B": {
        "valid": "BVALID",
        "ready": "BREADY",
        "payload": ("BID", "BRESP"),
        "required": ("BRESP",),
    },
    "AR": {
        "valid": "ARVALID",
        "ready": "ARREADY",
        "payload": (
            "ARID", "ARADDR", "ARLEN", "ARSIZE", "ARBURST",
            "ARLOCK", "ARCACHE", "ARPROT", "ARQOS",
        ),
        "required": ("ARADDR", "ARLEN", "ARSIZE", "ARBURST"),
    },
    "R": {
        "valid": "RVALID",
        "ready": "RREADY",
        "payload": ("RID", "RDATA", "RRESP", "RLAST"),
        "required": ("RDATA", "RRESP", "RLAST"),
    },
}

_RESPONSES = {
    0: "OKAY",
    1: "EXOKAY",
    2: "SLVERR",
    3: "DECERR",
}
_RESPONSE_NAMES = {name: code for code, name in _RESPONSES.items()}
_BURSTS = {
    0: "FIXED",
    1: "INCR",
    2: "WRAP",
    3: "RESERVED",
}
_BURST_NAMES = {name: code for code, name in _BURSTS.items()}


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


def _integer(value: Any) -> int | None:
    normalized = _scalar(value)
    if isinstance(normalized, int):
        return normalized
    return None


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


def _decode_burst(value: Any) -> tuple[int | None, str]:
    if value is None:
        return None, "MISSING"
    if isinstance(value, str):
        normalized = value.strip().upper()
        if normalized in _BURST_NAMES:
            code = _BURST_NAMES[normalized]
            return code, normalized
    normalized = _scalar(value)
    if isinstance(normalized, int) and normalized in _BURSTS:
        return normalized, _BURSTS[normalized]
    return None, str(value)


def _normalize_sample(raw: dict[str, Any], index: int) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError(f"AXI4 sample {index} must be an object")

    upper = {str(key).upper(): value for key, value in raw.items()}
    sample: dict[str, Any] = {
        "sample_index": index,
        "cycle": upper.get("CYCLE", index),
    }
    if "TIME" in upper:
        sample["time"] = upper["TIME"]

    for channel in _CHANNELS.values():
        for name in (channel["valid"], channel["ready"]):
            if name in sample:
                continue
            sample[name] = _logic(upper[name], name=name) if name in upper else False

    payload_names = {
        name
        for channel in _CHANNELS.values()
        for name in channel["payload"]
    }
    for name in payload_names:
        if name not in upper:
            continue
        if name in {"WLAST", "RLAST"}:
            sample[name] = _logic(upper[name], name=name)
        else:
            sample[name] = _scalar(upper[name])
    return sample


def _beat_address(
    start_address: int,
    beat_index: int,
    bytes_per_beat: int,
    burst: str,
    burst_beats: int,
) -> int:
    if burst == "FIXED":
        return start_address
    if burst == "INCR":
        if beat_index == 0:
            return start_address
        aligned = (start_address // bytes_per_beat) * bytes_per_beat
        return aligned + beat_index * bytes_per_beat
    if burst == "WRAP":
        span = burst_beats * bytes_per_beat
        wrap_base = (start_address // span) * span
        return wrap_base + (
            (start_address - wrap_base + beat_index * bytes_per_beat) % span
        )
    return start_address


def analyze_axi4_trace(payload: dict[str, Any]) -> dict[str, Any]:
    """Reconstruct AXI4 read/write bursts from normalized clock-edge samples."""
    if not isinstance(payload, dict):
        raise ValueError("AXI4 trace must be a JSON object")
    raw_samples = payload.get("samples")
    if not isinstance(raw_samples, list):
        raise ValueError("AXI4 trace must contain a 'samples' list")

    samples = [
        _normalize_sample(sample, index)
        for index, sample in enumerate(raw_samples)
    ]
    transactions: list[dict[str, Any]] = []
    violations: list[dict[str, Any]] = []

    aw_queue: deque[dict[str, Any]] = deque()
    completed_w_bursts: deque[list[dict[str, Any]]] = deque()
    active_w_beats: list[dict[str, Any]] = []
    pending_writes: list[dict[str, Any]] = []
    pending_reads: list[dict[str, Any]] = []

    channel_state: dict[str, dict[str, Any] | None] = {
        name: None for name in _CHANNELS
    }
    stall_cycles = {name: 0 for name in _CHANNELS}
    issued_writes = 0
    issued_reads = 0
    error_responses = 0

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
        if "time" in sample:
            entry["time"] = sample["time"]
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
                    (
                        f"{spec['valid']} deasserted before "
                        f"{spec['ready']} completed the handshake"
                    ),
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
                        (
                            f"{field} disappeared while "
                            f"{spec['valid']} remained asserted"
                        ),
                        channel=channel_name,
                        signal=field,
                        expected=expected,
                        actual=None,
                    )
                elif sample[field] != expected:
                    add_violation(
                        "payload_changed_while_stalled",
                        sample,
                        (
                            f"{field} changed before the "
                            f"{channel_name} handshake completed"
                        ),
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
                    (
                        f"{field} must be valid while "
                        f"{spec['valid']} is asserted"
                    ),
                    channel=channel_name,
                    signal=field,
                )

        if ready:
            return True, True

        channel_state[channel_name] = {
            "payload": {
                field: sample[field]
                for field in spec["payload"]
                if field in sample
            }
        }
        return False, True

    def validate_response(
        sample: dict[str, Any],
        *,
        channel: str,
        field: str,
        transaction_index: int | None,
    ) -> tuple[int | None, str]:
        code, label = _decode_response(sample.get(field))
        if code is None:
            add_violation(
                "invalid_response",
                sample,
                f"{field} is not a valid AXI4 response",
                channel=channel,
                transaction_index=transaction_index,
                signal=field,
                expected="OKAY/EXOKAY/SLVERR/DECERR",
                actual=sample.get(field),
            )
        return code, label

    def validate_burst(
        sample: dict[str, Any],
        *,
        prefix: str,
        transaction_index: int,
    ) -> dict[str, Any]:
        channel = prefix
        address = _integer(sample.get(f"{prefix}ADDR"))
        length = _integer(sample.get(f"{prefix}LEN"))
        size = _integer(sample.get(f"{prefix}SIZE"))
        burst_code, burst = _decode_burst(sample.get(f"{prefix}BURST"))
        tx_id = _scalar(sample.get(f"{prefix}ID", 0))

        beats: int | None = None
        if length is None or not (0 <= length <= 255):
            add_violation(
                "invalid_burst_length",
                sample,
                f"{prefix}LEN must be an 8-bit value from 0 to 255",
                channel=channel,
                transaction_index=transaction_index,
                signal=f"{prefix}LEN",
                expected="0..255",
                actual=sample.get(f"{prefix}LEN"),
            )
        else:
            beats = length + 1

        bytes_per_beat: int | None = None
        if size is None or not (0 <= size <= 7):
            add_violation(
                "invalid_burst_size",
                sample,
                (
                    f"{prefix}SIZE must encode 1 to 128 "
                    "bytes per transfer"
                ),
                channel=channel,
                transaction_index=transaction_index,
                signal=f"{prefix}SIZE",
                expected="0..7",
                actual=sample.get(f"{prefix}SIZE"),
            )
        else:
            bytes_per_beat = 1 << size

        if burst_code is None:
            add_violation(
                "invalid_burst_type",
                sample,
                f"{prefix}BURST is not a valid AXI4 burst encoding",
                channel=channel,
                transaction_index=transaction_index,
                signal=f"{prefix}BURST",
                expected="FIXED/INCR/WRAP",
                actual=sample.get(f"{prefix}BURST"),
            )
        elif burst_code == 3:
            add_violation(
                "reserved_burst_type",
                sample,
                f"{prefix}BURST uses the reserved encoding 0b11",
                channel=channel,
                transaction_index=transaction_index,
                signal=f"{prefix}BURST",
                expected="FIXED/INCR/WRAP",
                actual=burst,
            )

        if (
            beats is not None
            and burst in {"FIXED", "WRAP"}
            and beats > 16
        ):
            add_violation(
                "burst_length_not_supported",
                sample,
                f"AXI4 {burst} bursts are limited to 16 transfers",
                channel=channel,
                transaction_index=transaction_index,
                signal=f"{prefix}LEN",
                expected="1..16 transfers",
                actual=beats,
            )

        if (
            beats is not None
            and burst == "WRAP"
            and beats not in {2, 4, 8, 16}
        ):
            add_violation(
                "invalid_wrap_length",
                sample,
                (
                    "AXI4 WRAP bursts must contain "
                    "2, 4, 8, or 16 transfers"
                ),
                channel=channel,
                transaction_index=transaction_index,
                signal=f"{prefix}LEN",
                expected="2/4/8/16 transfers",
                actual=beats,
            )

        if (
            burst == "WRAP"
            and address is not None
            and bytes_per_beat is not None
            and address % bytes_per_beat != 0
        ):
            add_violation(
                "wrap_address_unaligned",
                sample,
                (
                    "AXI4 WRAP start address must be "
                    "aligned to the transfer size"
                ),
                channel=channel,
                transaction_index=transaction_index,
                signal=f"{prefix}ADDR",
                expected=f"multiple of {bytes_per_beat}",
                actual=address,
            )

        beat_addresses: list[int] = []
        if (
            address is not None
            and beats is not None
            and bytes_per_beat is not None
            and burst in {"FIXED", "INCR", "WRAP"}
        ):
            beat_addresses = [
                _beat_address(
                    address,
                    beat,
                    bytes_per_beat,
                    burst,
                    beats,
                )
                for beat in range(beats)
            ]
            touched_pages = {
                page
                for beat_address in beat_addresses
                for page in (
                    beat_address // 4096,
                    (beat_address + bytes_per_beat - 1) // 4096,
                )
            }
            if len(touched_pages) > 1:
                add_violation(
                    "burst_crosses_4kb",
                    sample,
                    (
                        "AXI4 bursts must not cross a "
                        "4KB address boundary"
                    ),
                    channel=channel,
                    transaction_index=transaction_index,
                    signal=f"{prefix}ADDR",
                    expected="single 4KB region",
                    actual=address,
                )

        return {
            "index": transaction_index,
            "id": tx_id,
            "sample_index": sample["sample_index"],
            "cycle": sample["cycle"],
            "time": sample.get("time"),
            "address": address,
            "len": length,
            "beats_expected": beats,
            "size": size,
            "bytes_per_beat": bytes_per_beat,
            "burst": burst,
            "burst_code": burst_code,
            "beat_addresses": beat_addresses,
        }

    def find_by_id(
        items: list[dict[str, Any]],
        tx_id: Any,
    ) -> int | None:
        for index, item in enumerate(items):
            if item["id"] == tx_id:
                return index
        return None

    def pair_write_bursts(sample: dict[str, Any]) -> None:
        while aw_queue and completed_w_bursts:
            request = aw_queue.popleft()
            beats = completed_w_bursts.popleft()
            expected = request.get("beats_expected")
            actual = len(beats)
            if expected is not None and actual != expected:
                code = "wlast_early" if actual < expected else "wlast_late"
                add_violation(
                    code,
                    beats[-1] if beats else sample,
                    (
                        f"WLAST terminated a write burst after "
                        f"{actual} beat(s); expected {expected}"
                    ),
                    channel="W",
                    transaction_index=request["index"],
                    signal="WLAST",
                    expected=expected,
                    actual=actual,
                )

            for beat_index, beat in enumerate(beats):
                beat["beat"] = beat_index
                if beat_index < len(request["beat_addresses"]):
                    beat["address"] = request["beat_addresses"][beat_index]

            request["data_beats"] = beats
            request["request_complete_sample_index"] = max(
                request["sample_index"],
                (
                    beats[-1]["sample_index"]
                    if beats
                    else request["sample_index"]
                ),
            )
            pending_writes.append(request)

    for sample in samples:
        events = {
            channel: channel_event(sample, channel)
            for channel in _CHANNELS
        }

        aw_hs, _ = events["AW"]
        w_hs, _ = events["W"]
        b_hs, b_started = events["B"]
        ar_hs, _ = events["AR"]
        r_hs, r_started = events["R"]

        if aw_hs:
            request = validate_burst(
                sample,
                prefix="AW",
                transaction_index=issued_writes,
            )
            issued_writes += 1
            aw_queue.append(request)

        if w_hs:
            beat = {
                "sample_index": sample["sample_index"],
                "cycle": sample["cycle"],
                "time": sample.get("time"),
                "data": sample.get("WDATA"),
                "strb": sample.get("WSTRB"),
                "last": bool(sample.get("WLAST", False)),
            }
            active_w_beats.append(beat)
            if beat["last"]:
                completed_w_bursts.append(active_w_beats)
                active_w_beats = []

        pair_write_bursts(sample)

        if ar_hs:
            request = validate_burst(
                sample,
                prefix="AR",
                transaction_index=issued_reads,
            )
            issued_reads += 1
            request["data_beats"] = []
            pending_reads.append(request)

        if b_started:
            tx_id = _scalar(sample.get("BID", 0))
            match = find_by_id(pending_writes, tx_id)
            tx_index = (
                pending_writes[match]["index"]
                if match is not None
                else None
            )
            validate_response(
                sample,
                channel="B",
                field="BRESP",
                transaction_index=tx_index,
            )
            if match is None:
                add_violation(
                    "write_response_before_request",
                    sample,
                    (
                        f"BVALID for BID={tx_id!r} was asserted "
                        "before a matching write burst completed"
                    ),
                    channel="B",
                )

        if r_started:
            tx_id = _scalar(sample.get("RID", 0))
            match = find_by_id(pending_reads, tx_id)
            tx_index = (
                pending_reads[match]["index"]
                if match is not None
                else None
            )
            validate_response(
                sample,
                channel="R",
                field="RRESP",
                transaction_index=tx_index,
            )
            if match is None:
                add_violation(
                    "read_data_before_request",
                    sample,
                    (
                        f"RVALID for RID={tx_id!r} was asserted "
                        "before a matching read address was accepted"
                    ),
                    channel="R",
                )

        if b_hs:
            tx_id = _scalar(sample.get("BID", 0))
            match = find_by_id(pending_writes, tx_id)
            if match is not None:
                request = pending_writes.pop(match)
                code, label = _decode_response(sample.get("BRESP"))
                if code in {2, 3}:
                    error_responses += 1

                transaction = {
                    "index": len(transactions),
                    "protocol_index": request["index"],
                    "direction": "WRITE",
                    "id": request["id"],
                    "address": request["address"],
                    "len": request["len"],
                    "beats_expected": request["beats_expected"],
                    "size": request["size"],
                    "bytes_per_beat": request["bytes_per_beat"],
                    "burst": request["burst"],
                    "aw_cycle": request["cycle"],
                    "data_beats": request["data_beats"],
                    "response": label,
                    "response_code": code,
                    "response_cycle": sample["cycle"],
                }
                if request.get("time") is not None:
                    transaction["aw_time"] = request["time"]
                if sample.get("time") is not None:
                    transaction["response_time"] = sample["time"]
                transactions.append(transaction)

        if r_hs:
            tx_id = _scalar(sample.get("RID", 0))
            match = find_by_id(pending_reads, tx_id)
            if match is not None:
                request = pending_reads[match]
                code, label = _decode_response(sample.get("RRESP"))
                if code in {2, 3}:
                    error_responses += 1

                beat_index = len(request["data_beats"])
                beat = {
                    "beat": beat_index,
                    "cycle": sample["cycle"],
                    "time": sample.get("time"),
                    "data": sample.get("RDATA"),
                    "response": label,
                    "response_code": code,
                    "last": bool(sample.get("RLAST", False)),
                }
                if beat_index < len(request["beat_addresses"]):
                    beat["address"] = request["beat_addresses"][beat_index]
                request["data_beats"].append(beat)

                expected = request.get("beats_expected")
                actual = len(request["data_beats"])
                is_last = beat["last"]

                if expected is not None and actual == expected and not is_last:
                    add_violation(
                        "missing_rlast_on_final_beat",
                        sample,
                        (
                            "RLAST was not asserted on expected "
                            f"final read beat {expected}"
                        ),
                        channel="R",
                        transaction_index=request["index"],
                        signal="RLAST",
                        expected=1,
                        actual=0,
                    )

                if is_last:
                    if expected is not None and actual != expected:
                        code_name = (
                            "rlast_early"
                            if actual < expected
                            else "rlast_late"
                        )
                        add_violation(
                            code_name,
                            sample,
                            (
                                "RLAST terminated a read burst after "
                                f"{actual} beat(s); expected {expected}"
                            ),
                            channel="R",
                            transaction_index=request["index"],
                            signal="RLAST",
                            expected=expected,
                            actual=actual,
                        )

                    request = pending_reads.pop(match)
                    transaction = {
                        "index": len(transactions),
                        "protocol_index": request["index"],
                        "direction": "READ",
                        "id": request["id"],
                        "address": request["address"],
                        "len": request["len"],
                        "beats_expected": request["beats_expected"],
                        "size": request["size"],
                        "bytes_per_beat": request["bytes_per_beat"],
                        "burst": request["burst"],
                        "ar_cycle": request["cycle"],
                        "data_beats": request["data_beats"],
                        "response_cycle": sample["cycle"],
                    }
                    if request.get("time") is not None:
                        transaction["ar_time"] = request["time"]
                    if sample.get("time") is not None:
                        transaction["response_time"] = sample["time"]
                    transactions.append(transaction)

    if samples:
        last = samples[-1]

        if active_w_beats:
            add_violation(
                "missing_wlast",
                last,
                (
                    "Trace ended with "
                    f"{len(active_w_beats)} accepted write beat(s) "
                    "and no WLAST"
                ),
                channel="W",
                signal="WLAST",
                expected=1,
                actual=0,
            )

        for request in aw_queue:
            add_violation(
                "missing_write_data",
                last,
                (
                    "Accepted write address did not receive a complete "
                    "WLAST-terminated data burst"
                ),
                channel="W",
                transaction_index=request["index"],
            )

        for beats in completed_w_bursts:
            add_violation(
                "write_data_without_address",
                beats[-1] if beats else last,
                (
                    "Accepted WLAST-terminated data burst has no "
                    "matching write address"
                ),
                channel="W",
            )

        for request in pending_writes:
            add_violation(
                "missing_write_response",
                last,
                (
                    "Completed write burst did not receive a "
                    "matching BID response before trace end"
                ),
                channel="B",
                transaction_index=request["index"],
            )

        for request in pending_reads:
            expected = request.get("beats_expected")
            actual = len(request["data_beats"])
            add_violation(
                "missing_read_completion",
                last,
                (
                    "Read burst ended incomplete with "
                    f"{actual} beat(s) accepted; expected {expected}"
                ),
                channel="R",
                transaction_index=request["index"],
            )

    writes = [
        tx for tx in transactions
        if tx["direction"] == "WRITE"
    ]
    reads = [
        tx for tx in transactions
        if tx["direction"] == "READ"
    ]

    return {
        "protocol": "AXI4",
        "source": str(payload.get("source", "normalized-trace")),
        "status": "PASS" if not violations else "FAIL",
        "summary": {
            "samples": len(samples),
            "issued_write_bursts": issued_writes,
            "issued_read_bursts": issued_reads,
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


def analyze_axi4_file(
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
    report = analyze_axi4_trace(payload)
    report["input_path"] = str(source)

    destination = (
        Path(output)
        if output is not None
        else Path(".zddv/protocols/axi4/latest.json")
    )
    if not destination.is_absolute():
        destination = project.root / destination
    destination = destination.resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(report, indent=2) + "\n",
        encoding="utf-8",
    )
    report["report_path"] = str(destination)
    return report
