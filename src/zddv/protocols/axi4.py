from __future__ import annotations

from collections import defaultdict, deque
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

    for spec in _CHANNELS.values():
        for name in (spec["valid"], spec["ready"]):
            if name in sample:
                continue
            sample[name] = _logic(upper[name], name=name) if name in upper else False

    scalar_fields = {
        field
        for spec in _CHANNELS.values()
        for field in spec["payload"]
    }
    for name in scalar_fields:
        if name == "WLAST" or name == "RLAST":
            if name in upper:
                sample[name] = _logic(upper[name], name=name)
        elif name in upper:
            sample[name] = _scalar(upper[name])

    return sample


def _decode_response(value: Any) -> tuple[int | None, str]:
    if value is None:
        return None, "MISSING"
    if isinstance(value, str):
        normalized = value.strip().upper()
        if normalized in _RESPONSE_NAMES:
            return _RESPONSE_NAMES[normalized], normalized
    normalized = _scalar(value)
    if isinstance(normalized, int) and normalized in _RESPONSES:
        return normalized, _RESPONSES[normalized]
    return None, str(value)


def _decode_burst(value: Any) -> tuple[int | None, str]:
    if isinstance(value, str):
        normalized = value.strip().upper()
        if normalized in _BURST_NAMES:
            return _BURST_NAMES[normalized], normalized
    normalized = _scalar(value)
    if isinstance(normalized, int) and normalized in _BURSTS:
        return normalized, _BURSTS[normalized]
    return None, str(value)


def analyze_axi4_trace(payload: dict[str, Any]) -> dict[str, Any]:
    """Reconstruct full AXI4 bursts from normalized clock-edge samples.

    The analyzer models the five independent AXI4 channels, validates
    VALID/payload stability under backpressure, reconstructs write bursts in AW
    acceptance order (AXI4 has no WID), correlates B responses by BID, and
    reconstructs/interleaves read data by RID. It checks AxLEN/AxSIZE/AxBURST
    legality, WLAST/RLAST placement, response IDs, and 4KB burst boundaries.
    """
    if not isinstance(payload, dict):
        raise ValueError("AXI4 trace must be a JSON object")
    raw_samples = payload.get("samples")
    if not isinstance(raw_samples, list):
        raise ValueError("AXI4 trace must contain a 'samples' list")

    samples = [_normalize_sample(item, index) for index, item in enumerate(raw_samples)]
    transactions: list[dict[str, Any]] = []
    violations: list[dict[str, Any]] = []

    channel_state: dict[str, dict[str, Any] | None] = {
        name: None for name in _CHANNELS
    }
    stall_cycles = {name: 0 for name in _CHANNELS}

    write_bursts: deque[dict[str, Any]] = deque()
    early_write_beats: deque[dict[str, Any]] = deque()
    pending_b: dict[Any, deque[dict[str, Any]]] = defaultdict(deque)
    pending_reads: dict[Any, deque[dict[str, Any]]] = defaultdict(deque)

    issued_writes = 0
    issued_reads = 0
    completed_write_data = 0
    write_beats = 0
    read_beats = 0
    error_response_beats = 0

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

    def channel_event(sample: dict[str, Any], channel_name: str) -> tuple[bool, bool]:
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
                elif sample[field] != expected:
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
            "payload": {
                field: sample[field]
                for field in spec["payload"]
                if field in sample
            },
        }
        return False, True

    def int_field(
        sample: dict[str, Any],
        name: str,
        *,
        channel: str,
        default: int,
        minimum: int,
        maximum: int,
    ) -> int:
        value = sample.get(name, default)
        normalized = _scalar(value)
        if not isinstance(normalized, int) or not (minimum <= normalized <= maximum):
            add_violation(
                f"invalid_{name.lower()}",
                sample,
                f"{name} must be an integer in range {minimum}..{maximum}",
                channel=channel,
                signal=name,
                expected=f"{minimum}..{maximum}",
                actual=value,
            )
            return default
        return normalized

    def make_address_request(
        sample: dict[str, Any],
        *,
        prefix: str,
        direction: str,
        protocol_index: int,
    ) -> dict[str, Any]:
        channel = prefix
        axi_id = _scalar(sample.get(f"{prefix}ID", 0))
        address = int_field(
            sample,
            f"{prefix}ADDR",
            channel=channel,
            default=0,
            minimum=0,
            maximum=(1 << 64) - 1,
        )
        axlen = int_field(
            sample,
            f"{prefix}LEN",
            channel=channel,
            default=0,
            minimum=0,
            maximum=255,
        )
        axsize = int_field(
            sample,
            f"{prefix}SIZE",
            channel=channel,
            default=0,
            minimum=0,
            maximum=7,
        )
        burst_code, burst_name = _decode_burst(sample.get(f"{prefix}BURST"))
        if burst_code is None:
            add_violation(
                "invalid_burst_type",
                sample,
                f"{prefix}BURST must be FIXED, INCR, or WRAP",
                channel=channel,
                signal=f"{prefix}BURST",
                expected="FIXED/INCR/WRAP",
                actual=sample.get(f"{prefix}BURST"),
            )
            burst_code, burst_name = 1, "INCR"

        beats = axlen + 1
        bytes_per_beat = 1 << axsize

        if burst_name != "INCR" and beats > 16:
            add_violation(
                "burst_too_long_for_type",
                sample,
                f"{burst_name} bursts are limited to 16 transfers in AXI4",
                channel=channel,
                signal=f"{prefix}LEN",
                expected="0..15",
                actual=axlen,
            )
        if burst_name == "WRAP" and beats not in {2, 4, 8, 16}:
            add_violation(
                "invalid_wrap_length",
                sample,
                "WRAP burst length must be 2, 4, 8, or 16 transfers",
                channel=channel,
                signal=f"{prefix}LEN",
                expected="1,3,7,15",
                actual=axlen,
            )

        aligned = address - (address % bytes_per_beat)
        if burst_name == "FIXED":
            low_address = address
            high_address = aligned + bytes_per_beat - 1
        elif burst_name == "WRAP":
            span = beats * bytes_per_beat
            wrap_base = (address // span) * span
            low_address = wrap_base
            high_address = wrap_base + span - 1
        else:
            low_address = address
            high_address = aligned + beats * bytes_per_beat - 1

        if low_address // 4096 != high_address // 4096:
            add_violation(
                "burst_crosses_4kb_boundary",
                sample,
                "AXI4 burst address range crosses a 4KB boundary",
                channel=channel,
                signal=f"{prefix}ADDR",
                expected="single 4KB region",
                actual=f"0x{low_address:x}..0x{high_address:x}",
            )

        request = {
            "protocol_index": protocol_index,
            "direction": direction,
            "id": axi_id,
            "address": address,
            "len": axlen,
            "size": axsize,
            "bytes_per_beat": bytes_per_beat,
            "burst": burst_name,
            "burst_code": burst_code,
            "expected_beats": beats,
            "address_cycle": sample["cycle"],
            "address_sample_index": sample["sample_index"],
            "beats": [],
        }
        if sample.get("time") is not None:
            request["address_time"] = sample["time"]
        for suffix in ("LOCK", "CACHE", "PROT", "QOS"):
            name = f"{prefix}{suffix}"
            if name in sample:
                request[name.lower()] = sample[name]
        return request

    def make_write_beat(sample: dict[str, Any]) -> dict[str, Any]:
        return {
            "sample": sample,
            "cycle": sample["cycle"],
            "sample_index": sample["sample_index"],
            "time": sample.get("time"),
            "data": sample.get("WDATA"),
            "strb": sample.get("WSTRB"),
            "last": bool(sample.get("WLAST", False)),
        }

    def attach_write_beat(beat: dict[str, Any]) -> None:
        nonlocal completed_write_data
        if not write_bursts:
            early_write_beats.append(beat)
            return

        request = write_bursts[0]
        beat_index = len(request["beats"])
        expected_last = beat_index == request["expected_beats"] - 1
        actual_last = bool(beat["last"])
        sample = beat["sample"]

        if actual_last and not expected_last:
            add_violation(
                "wlast_early",
                sample,
                "WLAST asserted before the final beat declared by AWLEN",
                channel="W",
                transaction_index=request["protocol_index"],
                signal="WLAST",
                expected=0,
                actual=1,
            )
        if expected_last and not actual_last:
            add_violation(
                "wlast_missing",
                sample,
                "WLAST must be asserted on the final write beat declared by AWLEN",
                channel="W",
                transaction_index=request["protocol_index"],
                signal="WLAST",
                expected=1,
                actual=0,
            )

        stored = {key: value for key, value in beat.items() if key != "sample"}
        stored["index"] = beat_index
        request["beats"].append(stored)

        if len(request["beats"]) == request["expected_beats"]:
            write_bursts.popleft()
            request["data_complete_cycle"] = beat["cycle"]
            request["data_complete_sample_index"] = beat["sample_index"]
            if beat.get("time") is not None:
                request["data_complete_time"] = beat["time"]
            pending_b[request["id"]].append(request)
            completed_write_data += 1

    def drain_early_write_beats() -> None:
        while early_write_beats and write_bursts:
            beat = early_write_beats.popleft()
            attach_write_beat(beat)

    def response_value(
        sample: dict[str, Any],
        *,
        field: str,
        channel: str,
        transaction_index: int | None,
    ) -> tuple[int | None, str]:
        code, label = _decode_response(sample.get(field))
        if code is None:
            add_violation(
                "invalid_response",
                sample,
                f"{field} is not a valid AXI response",
                channel=channel,
                transaction_index=transaction_index,
                signal=field,
                expected="OKAY/EXOKAY/SLVERR/DECERR",
                actual=sample.get(field),
            )
        return code, label

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
            request = make_address_request(
                sample,
                prefix="AW",
                direction="WRITE",
                protocol_index=issued_writes,
            )
            write_bursts.append(request)
            issued_writes += 1
            drain_early_write_beats()

        if w_hs:
            write_beats += 1
            attach_write_beat(make_write_beat(sample))

        if ar_hs:
            request = make_address_request(
                sample,
                prefix="AR",
                direction="READ",
                protocol_index=issued_reads,
            )
            pending_reads[request["id"]].append(request)
            issued_reads += 1

        if b_started:
            bid = _scalar(sample.get("BID", 0))
            tx_index = pending_b[bid][0]["protocol_index"] if pending_b.get(bid) else None
            response_value(
                sample,
                field="BRESP",
                channel="B",
                transaction_index=tx_index,
            )
            if not pending_b.get(bid):
                code = "write_response_unknown_id" if any(pending_b.values()) else "write_response_before_data_complete"
                add_violation(
                    code,
                    sample,
                    f"BVALID for BID={bid!r} has no completed write burst awaiting a response",
                    channel="B",
                    signal="BID",
                    actual=bid,
                )

        if b_hs:
            bid = _scalar(sample.get("BID", 0))
            if pending_b.get(bid):
                request = pending_b[bid].popleft()
                code, label = _decode_response(sample.get("BRESP"))
                tx = {
                    "index": len(transactions),
                    "protocol_index": request["protocol_index"],
                    "direction": "WRITE",
                    "id": request["id"],
                    "address": request["address"],
                    "len": request["len"],
                    "size": request["size"],
                    "bytes_per_beat": request["bytes_per_beat"],
                    "burst": request["burst"],
                    "beats": request["beats"],
                    "beat_count": len(request["beats"]),
                    "address_cycle": request["address_cycle"],
                    "data_complete_cycle": request["data_complete_cycle"],
                    "response_cycle": sample["cycle"],
                    "response": label,
                    "response_code": code,
                }
                if request.get("address_time") is not None:
                    tx["address_time"] = request["address_time"]
                if request.get("data_complete_time") is not None:
                    tx["data_complete_time"] = request["data_complete_time"]
                if sample.get("time") is not None:
                    tx["response_time"] = sample["time"]
                transactions.append(tx)
                if label in {"SLVERR", "DECERR"}:
                    error_response_beats += 1
            elif not b_started:
                add_violation(
                    "write_response_without_request",
                    sample,
                    f"Write response handshake for BID={bid!r} has no matching write burst",
                    channel="B",
                    signal="BID",
                    actual=bid,
                )

        if r_started:
            rid = _scalar(sample.get("RID", 0))
            request = pending_reads[rid][0] if pending_reads.get(rid) else None
            tx_index = request["protocol_index"] if request is not None else None
            response_value(
                sample,
                field="RRESP",
                channel="R",
                transaction_index=tx_index,
            )
            if request is None:
                code = "read_data_unknown_id" if any(pending_reads.values()) else "read_data_before_request"
                add_violation(
                    code,
                    sample,
                    f"RVALID for RID={rid!r} has no pending read burst",
                    channel="R",
                    signal="RID",
                    actual=rid,
                )

        if r_hs:
            rid = _scalar(sample.get("RID", 0))
            if pending_reads.get(rid):
                request = pending_reads[rid][0]
                beat_index = len(request["beats"])
                expected_last = beat_index == request["expected_beats"] - 1
                actual_last = bool(sample.get("RLAST", False))
                code, label = _decode_response(sample.get("RRESP"))

                if actual_last and not expected_last:
                    add_violation(
                        "rlast_early",
                        sample,
                        "RLAST asserted before the final beat declared by ARLEN",
                        channel="R",
                        transaction_index=request["protocol_index"],
                        signal="RLAST",
                        expected=0,
                        actual=1,
                    )
                if expected_last and not actual_last:
                    add_violation(
                        "rlast_missing",
                        sample,
                        "RLAST must be asserted on the final read beat declared by ARLEN",
                        channel="R",
                        transaction_index=request["protocol_index"],
                        signal="RLAST",
                        expected=1,
                        actual=0,
                    )

                beat = {
                    "index": beat_index,
                    "cycle": sample["cycle"],
                    "sample_index": sample["sample_index"],
                    "data": sample.get("RDATA"),
                    "response": label,
                    "response_code": code,
                    "last": actual_last,
                }
                if sample.get("time") is not None:
                    beat["time"] = sample["time"]
                request["beats"].append(beat)
                read_beats += 1
                if label in {"SLVERR", "DECERR"}:
                    error_response_beats += 1

                if len(request["beats"]) == request["expected_beats"]:
                    pending_reads[rid].popleft()
                    tx = {
                        "index": len(transactions),
                        "protocol_index": request["protocol_index"],
                        "direction": "READ",
                        "id": request["id"],
                        "address": request["address"],
                        "len": request["len"],
                        "size": request["size"],
                        "bytes_per_beat": request["bytes_per_beat"],
                        "burst": request["burst"],
                        "beats": request["beats"],
                        "beat_count": len(request["beats"]),
                        "address_cycle": request["address_cycle"],
                        "data_complete_cycle": sample["cycle"],
                    }
                    if request.get("address_time") is not None:
                        tx["address_time"] = request["address_time"]
                    if sample.get("time") is not None:
                        tx["data_complete_time"] = sample["time"]
                    transactions.append(tx)
            elif not r_started:
                add_violation(
                    "read_data_without_request",
                    sample,
                    f"Read data handshake for RID={rid!r} has no matching read burst",
                    channel="R",
                    signal="RID",
                    actual=rid,
                )

    if samples:
        last = samples[-1]

        for channel_name, state in channel_state.items():
            if state is not None:
                add_violation(
                    "channel_stalled_at_end",
                    last,
                    f"Trace ended while {channel_name} VALID was waiting for READY",
                    channel=channel_name,
                )

        if early_write_beats:
            add_violation(
                "write_data_without_address",
                last,
                f"{len(early_write_beats)} accepted write beat(s) were never associated with an AW burst",
                channel="W",
            )

        for request in write_bursts:
            add_violation(
                "incomplete_write_burst",
                last,
                (
                    f"Write burst ID={request['id']!r} ended with "
                    f"{len(request['beats'])}/{request['expected_beats']} data beat(s)"
                ),
                channel="W",
                transaction_index=request["protocol_index"],
            )

        for axi_id, queue in pending_b.items():
            for request in queue:
                add_violation(
                    "missing_write_response",
                    last,
                    f"Completed write burst ID={axi_id!r} did not receive a B response",
                    channel="B",
                    transaction_index=request["protocol_index"],
                )

        for axi_id, queue in pending_reads.items():
            for request in queue:
                add_violation(
                    "incomplete_read_burst",
                    last,
                    (
                        f"Read burst ID={axi_id!r} ended with "
                        f"{len(request['beats'])}/{request['expected_beats']} data beat(s)"
                    ),
                    channel="R",
                    transaction_index=request["protocol_index"],
                )

    writes = [tx for tx in transactions if tx["direction"] == "WRITE"]
    reads = [tx for tx in transactions if tx["direction"] == "READ"]

    result = {
        "protocol": "AXI4",
        "source": str(payload.get("source", "normalized-trace")),
        "status": "PASS" if not violations else "FAIL",
        "summary": {
            "samples": len(samples),
            "issued_write_bursts": issued_writes,
            "issued_read_bursts": issued_reads,
            "write_data_completed_bursts": completed_write_data,
            "completed_transactions": len(transactions),
            "reads": len(reads),
            "writes": len(writes),
            "write_beats": write_beats,
            "read_beats": read_beats,
            "error_response_beats": error_response_beats,
            "violations": len(violations),
            "channel_stall_cycles": stall_cycles,
        },
        "transactions": transactions,
        "violations": violations,
    }
    if isinstance(payload.get("waveform"), dict):
        result["waveform"] = payload["waveform"]
    return result


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
    destination.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    report["report_path"] = str(destination)
    return report
