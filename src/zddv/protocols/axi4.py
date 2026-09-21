from __future__ import annotations

from collections import Counter, defaultdict, deque
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

    for name in ("WLAST", "RLAST"):
        if name in upper:
            sample[name] = _logic(upper[name], name=name)

    for name in (
        "AWID", "AWADDR", "AWLEN", "AWSIZE", "AWBURST",
        "AWLOCK", "AWCACHE", "AWPROT", "AWQOS",
        "WDATA", "WSTRB",
        "BID", "BRESP",
        "ARID", "ARADDR", "ARLEN", "ARSIZE", "ARBURST",
        "ARLOCK", "ARCACHE", "ARPROT", "ARQOS",
        "RID", "RDATA", "RRESP",
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
            return _RESPONSE_NAMES[normalized], normalized
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
            return _BURST_NAMES[normalized], normalized
    normalized = _scalar(value)
    if isinstance(normalized, int) and normalized in _BURSTS:
        return normalized, _BURSTS[normalized]
    return None, str(value)


def _transaction_id(sample: dict[str, Any], field: str) -> Any:
    return sample.get(field, 0)


def _beat_addresses(
    address: int,
    *,
    beats: int,
    size: int,
    burst: str,
) -> list[int]:
    bytes_per_beat = 1 << size
    if burst == "FIXED":
        return [address] * beats
    if burst == "INCR":
        aligned = address - (address % bytes_per_beat)
        if beats == 1:
            return [address]
        return [address] + [
            aligned + beat * bytes_per_beat
            for beat in range(1, beats)
        ]
    if burst == "WRAP":
        total_bytes = beats * bytes_per_beat
        wrap_boundary = (address // total_bytes) * total_bytes
        addresses: list[int] = []
        current = address
        for _ in range(beats):
            addresses.append(current)
            current += bytes_per_beat
            if current >= wrap_boundary + total_bytes:
                current = wrap_boundary
        return addresses
    return []


def _crosses_4kb(
    address: int,
    *,
    beats: int,
    size: int,
    burst: str,
) -> bool:
    bytes_per_beat = 1 << size
    addresses = _beat_addresses(address, beats=beats, size=size, burst=burst)
    if not addresses:
        return False

    first_page = address >> 12
    for beat_index, beat_address in enumerate(addresses):
        if burst == "INCR" and beat_index == 0:
            aligned = beat_address - (beat_address % bytes_per_beat)
            last_byte = aligned + bytes_per_beat - 1
            first_byte = beat_address
        else:
            first_byte = beat_address
            last_byte = beat_address + bytes_per_beat - 1
        if (first_byte >> 12) != first_page or (last_byte >> 12) != first_page:
            return True
    return False


def analyze_axi4_trace(payload: dict[str, Any]) -> dict[str, Any]:
    """Reconstruct classic AXI4 bursts from normalized clock-edge samples.

    This milestone covers the core five-channel AXI4 burst protocol:
    IDs, LEN/SIZE/BURST, independent address/data handshakes, WLAST/RLAST,
    response correlation, same-ID ordering, 4KB burst boundaries, and
    VALID/payload stability while READY is LOW.

    Read-data chunking and other optional post-AXI4 extensions are deliberately
    outside this normalized trace contract.
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

    aw_queue: deque[dict[str, Any]] = deque()
    complete_w_bursts: deque[dict[str, Any]] = deque()
    current_w_beats: list[dict[str, Any]] = []

    pending_write_responses: dict[Any, deque[dict[str, Any]]] = defaultdict(deque)
    pending_reads: dict[Any, deque[dict[str, Any]]] = defaultdict(deque)

    issued_writes = 0
    issued_reads = 0
    accepted_write_beats = 0
    accepted_read_beats = 0

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
        tx_id: Any = None,
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
        if tx_id is not None:
            entry["id"] = tx_id
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
            }
        }
        return False, True

    def validate_response(
        sample: dict[str, Any],
        *,
        channel: str,
        field: str,
        tx_id: Any,
    ) -> tuple[int | None, str]:
        code, label = _decode_response(sample.get(field))
        if code is None:
            add_violation(
                "invalid_response",
                sample,
                f"{field} is not a valid AXI4 response",
                channel=channel,
                signal=field,
                expected="OKAY/EXOKAY/SLVERR/DECERR",
                actual=sample.get(field),
                tx_id=tx_id,
            )
        return code, label

    def make_address_request(
        sample: dict[str, Any],
        *,
        prefix: str,
        protocol_index: int,
    ) -> dict[str, Any]:
        channel = prefix
        tx_id = _transaction_id(sample, f"{prefix}ID")
        address = sample.get(f"{prefix}ADDR")
        length = sample.get(f"{prefix}LEN")
        size = sample.get(f"{prefix}SIZE")
        burst_code, burst_name = _decode_burst(sample.get(f"{prefix}BURST"))

        valid_address = isinstance(address, int) and address >= 0
        valid_length = isinstance(length, int) and 0 <= length <= 255
        valid_size = isinstance(size, int) and 0 <= size <= 7

        if not valid_address:
            add_violation(
                "invalid_burst_address",
                sample,
                f"{prefix}ADDR must be a non-negative integer",
                channel=channel,
                signal=f"{prefix}ADDR",
                actual=address,
                tx_id=tx_id,
            )
        if not valid_length:
            add_violation(
                "invalid_burst_length",
                sample,
                f"{prefix}LEN must be in the AXI4 range 0..255",
                channel=channel,
                signal=f"{prefix}LEN",
                actual=length,
                tx_id=tx_id,
            )
        if not valid_size:
            add_violation(
                "invalid_burst_size",
                sample,
                f"{prefix}SIZE must be in the AXI4 range 0..7",
                channel=channel,
                signal=f"{prefix}SIZE",
                actual=size,
                tx_id=tx_id,
            )
        if burst_code is None:
            add_violation(
                "reserved_burst_type",
                sample,
                f"{prefix}BURST must encode FIXED, INCR, or WRAP",
                channel=channel,
                signal=f"{prefix}BURST",
                actual=sample.get(f"{prefix}BURST"),
                tx_id=tx_id,
            )

        beats = length + 1 if valid_length else None
        bytes_per_beat = 1 << size if valid_size else None

        if beats is not None and burst_name in {"FIXED", "WRAP"} and beats > 16:
            add_violation(
                "burst_length_not_supported",
                sample,
                f"{burst_name} bursts are limited to 16 transfers",
                channel=channel,
                signal=f"{prefix}LEN",
                expected="0..15",
                actual=length,
                tx_id=tx_id,
            )
        if beats is not None and burst_name == "WRAP" and beats not in {2, 4, 8, 16}:
            add_violation(
                "invalid_wrap_length",
                sample,
                "WRAP burst length must be 2, 4, 8, or 16 transfers",
                channel=channel,
                signal=f"{prefix}LEN",
                expected="1,3,7,15",
                actual=length,
                tx_id=tx_id,
            )
        if (
            valid_address
            and bytes_per_beat is not None
            and burst_name == "WRAP"
            and address % bytes_per_beat != 0
        ):
            add_violation(
                "wrap_address_unaligned",
                sample,
                "WRAP start address must be aligned to the transfer size",
                channel=channel,
                signal=f"{prefix}ADDR",
                expected=f"multiple of {bytes_per_beat}",
                actual=address,
                tx_id=tx_id,
            )
        if (
            valid_address
            and beats is not None
            and valid_size
            and burst_name in _BURST_NAMES
            and _crosses_4kb(address, beats=beats, size=size, burst=burst_name)
        ):
            add_violation(
                "burst_crosses_4kb",
                sample,
                "AXI4 burst crosses a 4KB address boundary",
                channel=channel,
                signal=f"{prefix}ADDR",
                actual=address,
                tx_id=tx_id,
            )

        request = {
            "protocol_index": protocol_index,
            "id": tx_id,
            "sample_index": sample["sample_index"],
            "cycle": sample["cycle"],
            "time": sample.get("time"),
            "address": address,
            "len": length,
            "size": size,
            "burst": burst_name,
            "burst_code": burst_code,
            "expected_beats": beats,
            "bytes_per_beat": bytes_per_beat,
            "beats": [],
        }
        if (
            valid_address
            and beats is not None
            and valid_size
            and burst_name in _BURST_NAMES
        ):
            request["beat_addresses"] = _beat_addresses(
                address,
                beats=beats,
                size=size,
                burst=burst_name,
            )
        return request

    def complete_write_pairs(sample: dict[str, Any]) -> None:
        nonlocal issued_writes
        while aw_queue and complete_w_bursts:
            request = aw_queue.popleft()
            wburst = complete_w_bursts.popleft()
            beats = wburst["beats"]
            request["beats"] = beats
            expected = request.get("expected_beats")
            actual = len(beats)

            if isinstance(expected, int) and actual < expected:
                add_violation(
                    "wlast_early",
                    sample,
                    f"WLAST ended the write burst after {actual} beat(s); AWLEN requires {expected}",
                    channel="W",
                    transaction_index=request["protocol_index"],
                    expected=expected,
                    actual=actual,
                    tx_id=request["id"],
                )
            elif isinstance(expected, int) and actual > expected:
                add_violation(
                    "wlast_late",
                    sample,
                    f"WLAST ended the write burst after {actual} beat(s); AWLEN requires {expected}",
                    channel="W",
                    transaction_index=request["protocol_index"],
                    expected=expected,
                    actual=actual,
                    tx_id=request["id"],
                )

            request["data_complete_sample_index"] = wburst["sample_index"]
            request["data_complete_cycle"] = wburst["cycle"]
            request["data_complete_time"] = wburst.get("time")
            pending_write_responses[request["id"]].append(request)
            issued_writes += 1

    def finish_read(
        request: dict[str, Any],
        sample: dict[str, Any],
    ) -> None:
        response_labels = [beat["response"] for beat in request["beats"]]
        response_codes = [beat["response_code"] for beat in request["beats"]]
        transaction: dict[str, Any] = {
            "index": len(transactions),
            "protocol_index": request["protocol_index"],
            "direction": "READ",
            "id": request["id"],
            "address": request["address"],
            "len": request["len"],
            "size": request["size"],
            "burst": request["burst"],
            "burst_code": request["burst_code"],
            "expected_beats": request["expected_beats"],
            "completed_beats": len(request["beats"]),
            "ar_cycle": request["cycle"],
            "response_cycle": sample["cycle"],
            "read_beats": request["beats"],
            "responses": response_labels,
            "response_codes": response_codes,
        }
        if "beat_addresses" in request:
            transaction["beat_addresses"] = request["beat_addresses"]
        if request.get("time") is not None:
            transaction["ar_time"] = request["time"]
        if sample.get("time") is not None:
            transaction["response_time"] = sample["time"]
        transactions.append(transaction)

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
            aw_queue.append(
                make_address_request(
                    sample,
                    prefix="AW",
                    protocol_index=issued_writes + len(aw_queue),
                )
            )

        if w_hs:
            accepted_write_beats += 1
            beat = {
                "beat_index": len(current_w_beats),
                "sample_index": sample["sample_index"],
                "cycle": sample["cycle"],
                "time": sample.get("time"),
                "data": sample.get("WDATA"),
                "strb": sample.get("WSTRB"),
                "last": bool(sample.get("WLAST", False)),
            }
            current_w_beats.append(beat)
            if beat["last"]:
                complete_w_bursts.append(
                    {
                        "beats": current_w_beats,
                        "sample_index": sample["sample_index"],
                        "cycle": sample["cycle"],
                        "time": sample.get("time"),
                    }
                )
                current_w_beats = []

        complete_write_pairs(sample)

        if ar_hs:
            request = make_address_request(
                sample,
                prefix="AR",
                protocol_index=issued_reads,
            )
            pending_reads[request["id"]].append(request)
            issued_reads += 1

        if b_started:
            bid = _transaction_id(sample, "BID")
            validate_response(sample, channel="B", field="BRESP", tx_id=bid)
            if not pending_write_responses.get(bid):
                add_violation(
                    "write_response_before_request",
                    sample,
                    "BVALID was asserted before a complete write burst with matching BID was accepted",
                    channel="B",
                    tx_id=bid,
                )

        if r_started:
            rid = _transaction_id(sample, "RID")
            validate_response(sample, channel="R", field="RRESP", tx_id=rid)
            if not pending_reads.get(rid):
                add_violation(
                    "read_data_before_request",
                    sample,
                    "RVALID was asserted before a read request with matching RID was accepted",
                    channel="R",
                    tx_id=rid,
                )

        if b_hs:
            bid = _transaction_id(sample, "BID")
            queue = pending_write_responses.get(bid)
            if queue:
                request = queue.popleft()
                if not queue:
                    pending_write_responses.pop(bid, None)
                response_code, response = _decode_response(sample.get("BRESP"))
                transaction: dict[str, Any] = {
                    "index": len(transactions),
                    "protocol_index": request["protocol_index"],
                    "direction": "WRITE",
                    "id": request["id"],
                    "address": request["address"],
                    "len": request["len"],
                    "size": request["size"],
                    "burst": request["burst"],
                    "burst_code": request["burst_code"],
                    "expected_beats": request["expected_beats"],
                    "completed_beats": len(request["beats"]),
                    "aw_cycle": request["cycle"],
                    "data_complete_cycle": request["data_complete_cycle"],
                    "response_cycle": sample["cycle"],
                    "write_beats": request["beats"],
                    "response": response,
                    "response_code": response_code,
                }
                if "beat_addresses" in request:
                    transaction["beat_addresses"] = request["beat_addresses"]
                if request.get("time") is not None:
                    transaction["aw_time"] = request["time"]
                if request.get("data_complete_time") is not None:
                    transaction["data_complete_time"] = request["data_complete_time"]
                if sample.get("time") is not None:
                    transaction["response_time"] = sample["time"]
                transactions.append(transaction)
            elif not b_started:
                add_violation(
                    "write_response_without_request",
                    sample,
                    "Write response handshake occurred without a pending write burst for BID",
                    channel="B",
                    tx_id=bid,
                )

        if r_hs:
            accepted_read_beats += 1
            rid = _transaction_id(sample, "RID")
            queue = pending_reads.get(rid)
            if queue:
                request = queue[0]
                response_code, response = _decode_response(sample.get("RRESP"))
                beat = {
                    "beat_index": len(request["beats"]),
                    "sample_index": sample["sample_index"],
                    "cycle": sample["cycle"],
                    "time": sample.get("time"),
                    "data": sample.get("RDATA"),
                    "response": response,
                    "response_code": response_code,
                    "last": bool(sample.get("RLAST", False)),
                }
                request["beats"].append(beat)
                expected = request.get("expected_beats")
                actual = len(request["beats"])
                should_finish = False

                if isinstance(expected, int):
                    if beat["last"] and actual < expected:
                        add_violation(
                            "rlast_early",
                            sample,
                            f"RLAST ended the read burst after {actual} beat(s); ARLEN requires {expected}",
                            channel="R",
                            transaction_index=request["protocol_index"],
                            expected=expected,
                            actual=actual,
                            tx_id=rid,
                        )
                        should_finish = True
                    elif actual == expected:
                        if not beat["last"]:
                            add_violation(
                                "rlast_missing",
                                sample,
                                f"Expected RLAST on read beat {expected}",
                                channel="R",
                                transaction_index=request["protocol_index"],
                                signal="RLAST",
                                expected=1,
                                actual=0,
                                tx_id=rid,
                            )
                        should_finish = True
                    elif actual > expected:
                        add_violation(
                            "read_burst_too_long",
                            sample,
                            f"Read burst exceeded ARLEN-defined length of {expected} beat(s)",
                            channel="R",
                            transaction_index=request["protocol_index"],
                            expected=expected,
                            actual=actual,
                            tx_id=rid,
                        )
                        should_finish = bool(beat["last"])
                elif beat["last"]:
                    should_finish = True

                if should_finish:
                    queue.popleft()
                    if not queue:
                        pending_reads.pop(rid, None)
                    finish_read(request, sample)
            elif not r_started:
                add_violation(
                    "read_data_without_request",
                    sample,
                    "Read data handshake occurred without a pending read request for RID",
                    channel="R",
                    tx_id=rid,
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

        if current_w_beats:
            add_violation(
                "missing_wlast",
                last,
                f"Trace ended with {len(current_w_beats)} accepted write beat(s) and no WLAST",
                channel="W",
                expected="WLAST=1 on final write beat",
                actual="trace ended",
            )

        for request in aw_queue:
            add_violation(
                "write_address_without_complete_data",
                last,
                "Accepted write address did not receive a complete WLAST-terminated data burst",
                channel="W",
                transaction_index=request["protocol_index"],
                tx_id=request["id"],
            )

        for burst in complete_w_bursts:
            add_violation(
                "write_data_without_address",
                last,
                f"Accepted WLAST-terminated write burst with {len(burst['beats'])} beat(s) had no write address",
                channel="W",
            )

        for tx_id, queue in pending_write_responses.items():
            for request in queue:
                add_violation(
                    "missing_write_response",
                    last,
                    "Completed write burst did not receive a B response before trace end",
                    channel="B",
                    transaction_index=request["protocol_index"],
                    tx_id=tx_id,
                )

        for tx_id, queue in pending_reads.items():
            for request in queue:
                add_violation(
                    "missing_read_data",
                    last,
                    f"Read request completed only {len(request['beats'])} of "
                    f"{request.get('expected_beats')} expected beat(s)",
                    channel="R",
                    transaction_index=request["protocol_index"],
                    tx_id=tx_id,
                )

    reads = [tx for tx in transactions if tx["direction"] == "READ"]
    writes = [tx for tx in transactions if tx["direction"] == "WRITE"]
    burst_counts = Counter(tx.get("burst", "UNKNOWN") for tx in transactions)
    error_responses = 0
    for tx in transactions:
        if tx["direction"] == "WRITE":
            if tx.get("response") in {"SLVERR", "DECERR"}:
                error_responses += 1
        else:
            if any(response in {"SLVERR", "DECERR"} for response in tx.get("responses", [])):
                error_responses += 1

    result = {
        "protocol": "AXI4",
        "profile": "classic-burst-core",
        "source": str(payload.get("source", "normalized-trace")),
        "status": "PASS" if not violations else "FAIL",
        "summary": {
            "samples": len(samples),
            "issued_write_requests": issued_writes,
            "issued_read_requests": issued_reads,
            "completed_transactions": len(transactions),
            "reads": len(reads),
            "writes": len(writes),
            "accepted_write_beats": accepted_write_beats,
            "accepted_read_beats": accepted_read_beats,
            "error_responses": error_responses,
            "violations": len(violations),
            "burst_types": {
                name: burst_counts.get(name, 0)
                for name in ("FIXED", "INCR", "WRAP")
            },
            "channel_stall_cycles": stall_cycles,
        },
        "transactions": transactions,
        "violations": violations,
    }
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
