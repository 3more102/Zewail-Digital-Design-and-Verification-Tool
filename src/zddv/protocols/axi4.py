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
            "AWLOCK", "AWCACHE", "AWPROT", "AWQOS", "AWREGION",
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
            "ARLOCK", "ARCACHE", "ARPROT", "ARQOS", "ARREGION",
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

_RESPONSE_NAMES = {0: "OKAY", 1: "EXOKAY", 2: "SLVERR", 3: "DECERR"}
_RESPONSE_CODES = {name: code for code, name in _RESPONSE_NAMES.items()}
_BURST_NAMES = {0: "FIXED", 1: "INCR", 2: "WRAP"}
_BURST_CODES = {name: code for code, name in _BURST_NAMES.items()}


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


def _decode_response(value: Any) -> tuple[int | None, str]:
    if isinstance(value, str):
        name = value.strip().upper()
        if name in _RESPONSE_CODES:
            return _RESPONSE_CODES[name], name
    scalar = _scalar(value)
    if isinstance(scalar, int) and scalar in _RESPONSE_NAMES:
        return scalar, _RESPONSE_NAMES[scalar]
    return None, "MISSING" if value is None else str(value)


def _decode_burst(value: Any) -> tuple[int | None, str]:
    if isinstance(value, str):
        name = value.strip().upper()
        if name in _BURST_CODES:
            return _BURST_CODES[name], name
    scalar = _scalar(value)
    if isinstance(scalar, int) and scalar in _BURST_NAMES:
        return scalar, _BURST_NAMES[scalar]
    return None, "INVALID" if value is None else str(value)


def _normalize_sample(raw: dict[str, Any], index: int) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError(f"AXI4 sample {index} must be an object")

    upper = {str(key).upper(): value for key, value in raw.items()}
    sample: dict[str, Any] = {
        "sample_index": index,
        "cycle": upper.get("CYCLE", index),
    }

    for spec in _CHANNELS.values():
        for name in (spec["valid"], spec["ready"]):
            if name not in sample:
                sample[name] = _logic(upper[name], name=name) if name in upper else False

    for name in ("WLAST", "RLAST", "AWLOCK", "ARLOCK"):
        if name in upper:
            sample[name] = _logic(upper[name], name=name)

    for name in (
        "AWID", "AWADDR", "AWLEN", "AWSIZE", "AWBURST", "AWCACHE",
        "AWPROT", "AWQOS", "AWREGION", "WDATA", "WSTRB", "BID", "BRESP",
        "ARID", "ARADDR", "ARLEN", "ARSIZE", "ARBURST", "ARCACHE",
        "ARPROT", "ARQOS", "ARREGION", "RID", "RDATA", "RRESP",
    ):
        if name in upper:
            sample[name] = _scalar(upper[name])

    return sample


def analyze_axi4_trace(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("AXI4 trace must be a JSON object")
    raw_samples = payload.get("samples")
    if not isinstance(raw_samples, list):
        raise ValueError("AXI4 trace must contain a 'samples' list")

    samples = [_normalize_sample(sample, index) for index, sample in enumerate(raw_samples)]
    violations: list[dict[str, Any]] = []
    transactions: list[dict[str, Any]] = []

    channel_state: dict[str, dict[str, Any] | None] = {
        name: None for name in _CHANNELS
    }
    channel_stall_cycles = {name: 0 for name in _CHANNELS}

    aw_queue: deque[dict[str, Any]] = deque()
    completed_w_bursts: deque[list[dict[str, Any]]] = deque()
    current_w_beats: list[dict[str, Any]] = []
    pending_write_responses: dict[int, deque[dict[str, Any]]] = defaultdict(deque)
    pending_reads: dict[int, deque[dict[str, Any]]] = defaultdict(deque)
    exclusive_monitors: dict[int, dict[str, Any]] = {}

    issued_write_bursts = 0
    issued_read_bursts = 0
    observed_write_beats = 0
    observed_read_beats = 0
    exclusive_read_bursts = 0
    exclusive_write_bursts = 0
    exclusive_write_successes = 0
    exclusive_write_failures = 0

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

    def channel_event(sample: dict[str, Any], channel: str) -> bool:
        spec = _CHANNELS[channel]
        valid = bool(sample[spec["valid"]])
        ready = bool(sample[spec["ready"]])
        state = channel_state[channel]

        if valid and not ready:
            channel_stall_cycles[channel] += 1

        if state is not None:
            if not valid:
                add_violation(
                    "valid_dropped_before_handshake",
                    sample,
                    f"{spec['valid']} deasserted before {spec['ready']} completed the handshake",
                    channel=channel,
                    signal=spec["valid"],
                    expected=1,
                    actual=0,
                )
                channel_state[channel] = None
                return False

            for field, expected in state["payload"].items():
                actual = sample.get(field)
                if field not in sample:
                    add_violation(
                        "payload_missing_while_stalled",
                        sample,
                        f"{field} disappeared while {spec['valid']} remained asserted",
                        channel=channel,
                        signal=field,
                        expected=expected,
                        actual=None,
                    )
                elif actual != expected:
                    add_violation(
                        "payload_changed_while_stalled",
                        sample,
                        f"{field} changed before the {channel} handshake completed",
                        channel=channel,
                        signal=field,
                        expected=expected,
                        actual=actual,
                    )

            if ready:
                channel_state[channel] = None
                return True
            return False

        if not valid:
            return False

        for field in spec["required"]:
            if field not in sample:
                add_violation(
                    "missing_channel_payload",
                    sample,
                    f"{field} must be valid while {spec['valid']} is asserted",
                    channel=channel,
                    signal=field,
                )

        if ready:
            return True

        channel_state[channel] = {
            "payload": {
                field: sample[field]
                for field in spec["payload"]
                if field in sample
            }
        }
        return False

    def id_value(sample: dict[str, Any], field: str, channel: str) -> int:
        value = sample.get(field, 0)
        if isinstance(value, int) and value >= 0:
            return value
        add_violation(
            "invalid_transaction_id",
            sample,
            f"{field} must be a non-negative integer",
            channel=channel,
            signal=field,
            expected="non-negative integer",
            actual=value,
        )
        return 0

    def validate_response(
        sample: dict[str, Any],
        field: str,
        channel: str,
        *,
        exclusive: bool | None = None,
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
            )
        elif code == 1 and exclusive is False:
            add_violation(
                "exokay_without_exclusive_request",
                sample,
                f"{field}=EXOKAY requires a corresponding exclusive request",
                channel=channel,
                signal=field,
                expected="non-EXOKAY response",
                actual=label,
            )
        return code, label

    def make_request(sample: dict[str, Any], prefix: str, index: int) -> dict[str, Any]:
        tx_id = id_value(sample, f"{prefix}ID", prefix)
        addr = sample.get(f"{prefix}ADDR")
        length = sample.get(f"{prefix}LEN")
        size = sample.get(f"{prefix}SIZE")
        burst_code, burst = _decode_burst(sample.get(f"{prefix}BURST"))

        valid_addr = isinstance(addr, int) and addr >= 0
        if not valid_addr:
            add_violation(
                "invalid_burst_address", sample,
                f"{prefix}ADDR must be a non-negative integer",
                channel=prefix, signal=f"{prefix}ADDR",
                expected="non-negative integer", actual=addr,
            )

        valid_len = isinstance(length, int) and 0 <= length <= 255
        if not valid_len:
            add_violation(
                "invalid_burst_length", sample,
                f"{prefix}LEN must be in the range 0..255",
                channel=prefix, signal=f"{prefix}LEN",
                expected="0..255", actual=length,
            )

        valid_size = isinstance(size, int) and 0 <= size <= 7
        if not valid_size:
            add_violation(
                "invalid_burst_size", sample,
                f"{prefix}SIZE must be in the range 0..7",
                channel=prefix, signal=f"{prefix}SIZE",
                expected="0..7", actual=size,
            )

        if burst_code is None:
            add_violation(
                "invalid_burst_type", sample,
                f"{prefix}BURST must be FIXED, INCR, or WRAP",
                channel=prefix, signal=f"{prefix}BURST",
                expected="FIXED/INCR/WRAP", actual=sample.get(f"{prefix}BURST"),
            )

        beats = length + 1 if valid_len else 1
        beat_bytes = 1 << size if valid_size else None

        if burst in {"FIXED", "WRAP"} and beats > 16:
            add_violation(
                "burst_length_not_supported_for_type", sample,
                f"{burst} bursts are limited to 16 transfers in AXI4",
                channel=prefix, signal=f"{prefix}LEN",
                expected="0..15", actual=length,
            )

        if burst == "WRAP":
            if beats not in {2, 4, 8, 16}:
                add_violation(
                    "invalid_wrap_length", sample,
                    "WRAP burst length must be 2, 4, 8, or 16 transfers",
                    channel=prefix, signal=f"{prefix}LEN",
                    expected="1/3/7/15 encoded length", actual=length,
                )
            if valid_addr and beat_bytes is not None and addr % beat_bytes:
                add_violation(
                    "unaligned_wrap_address", sample,
                    "WRAP burst start address must align to the transfer size",
                    channel=prefix, signal=f"{prefix}ADDR",
                    expected=f"multiple of {beat_bytes}", actual=addr,
                )

        if valid_addr and beat_bytes is not None and burst in {"FIXED", "INCR", "WRAP"}:
            if burst == "FIXED":
                end_addr = addr + beat_bytes - 1
            elif burst == "INCR":
                aligned = addr - (addr % beat_bytes)
                end_addr = aligned + beats * beat_bytes - 1
            else:
                span = beats * beat_bytes
                wrap_base = (addr // span) * span
                end_addr = wrap_base + span - 1
            if addr // 4096 != end_addr // 4096:
                add_violation(
                    "burst_crosses_4kb_boundary", sample,
                    "AXI burst must not cross a 4KB address boundary",
                    channel=prefix, signal=f"{prefix}ADDR",
                    expected="burst contained within one 4KB region", actual=addr,
                )

        exclusive = bool(sample.get(f"{prefix}LOCK", False))
        if exclusive and valid_addr and beat_bytes is not None:
            total_bytes = beats * beat_bytes
            if beats > 16:
                add_violation(
                    "exclusive_burst_too_long", sample,
                    "Exclusive access burst must not exceed 16 transfers",
                    channel=prefix, signal=f"{prefix}LEN",
                    expected="0..15", actual=length,
                )
            if total_bytes > 128:
                add_violation(
                    "exclusive_access_too_large", sample,
                    "Exclusive access must transfer no more than 128 bytes",
                    channel=prefix,
                    expected="<=128 bytes", actual=total_bytes,
                )
            if total_bytes <= 0 or total_bytes & (total_bytes - 1):
                add_violation(
                    "exclusive_access_size_not_power_of_two", sample,
                    "Exclusive access total byte count must be a power of two",
                    channel=prefix,
                    expected="1,2,4,8,16,32,64,128 bytes", actual=total_bytes,
                )
            if total_bytes > 0 and addr % total_bytes:
                add_violation(
                    "exclusive_address_misaligned", sample,
                    "Exclusive access address must align to the total bytes in the transaction",
                    channel=prefix, signal=f"{prefix}ADDR",
                    expected=f"multiple of {total_bytes}", actual=addr,
                )

        return {
            "index": index,
            "id": tx_id,
            "address": addr,
            "len": length,
            "size": size,
            "burst": burst,
            "beats_expected": beats,
            "beat_bytes": beat_bytes,
            "lock": exclusive,
            "cycle": sample["cycle"],
            "sample_index": sample["sample_index"],
            "cache": sample.get(f"{prefix}CACHE"),
            "prot": sample.get(f"{prefix}PROT"),
            "region": sample.get(f"{prefix}REGION"),
        }

    def exclusive_signature(request: dict[str, Any]) -> tuple[Any, ...]:
        return (
            request["id"],
            request["address"],
            request.get("region"),
            request["len"],
            request["size"],
            request["burst"],
            request["lock"],
            request.get("cache"),
            request.get("prot"),
        )

    def pair_exclusive_write(
        request: dict[str, Any],
        sample: dict[str, Any],
    ) -> None:
        monitor = exclusive_monitors.pop(request["id"], None)
        request["exclusive_pair_matched"] = False
        request["exclusive_read_outcome"] = None

        if monitor is None:
            add_violation(
                "exclusive_write_without_completed_read", sample,
                "Exclusive write started without a completed exclusive read using the same ID",
                channel="AW", transaction_index=request["index"],
            )
            return

        request["exclusive_read_outcome"] = monitor["outcome"]
        if exclusive_signature(request) != monitor["signature"]:
            add_violation(
                "exclusive_sequence_attribute_mismatch", sample,
                "Exclusive write attributes do not match the preceding exclusive read",
                channel="AW", transaction_index=request["index"],
                expected=monitor["signature"], actual=exclusive_signature(request),
            )
            return

        request["exclusive_pair_matched"] = True

    def burst_addresses(request: dict[str, Any], count: int) -> list[int] | None:
        addr = request["address"]
        beat_bytes = request["beat_bytes"]
        burst = request["burst"]
        beats = request["beats_expected"]
        if not isinstance(addr, int) or beat_bytes is None or burst not in {"FIXED", "INCR", "WRAP"}:
            return None
        if burst == "FIXED":
            return [addr for _ in range(count)]
        if burst == "INCR":
            aligned = addr - (addr % beat_bytes)
            return [
                addr if index == 0 else aligned + index * beat_bytes
                for index in range(count)
            ]
        span = beats * beat_bytes
        wrap_base = (addr // span) * span
        wrap_top = wrap_base + span
        result: list[int] = []
        current = addr
        for _ in range(count):
            result.append(current)
            current += beat_bytes
            if current >= wrap_top:
                current = wrap_base
        return result

    def pair_write_bursts() -> None:
        while aw_queue and completed_w_bursts:
            request = aw_queue.popleft()
            beats = completed_w_bursts.popleft()
            expected = request["beats_expected"]
            observed = len(beats)
            if observed < expected:
                add_violation(
                    "wlast_early", beats[-1]["sample"],
                    "WLAST asserted before the AWLEN-defined final write beat",
                    channel="W", transaction_index=request["index"],
                    signal="WLAST", expected=f"beat {expected}", actual=f"beat {observed}",
                )
            elif observed > expected:
                add_violation(
                    "wlast_late", beats[-1]["sample"],
                    "WLAST asserted after the AWLEN-defined final write beat",
                    channel="W", transaction_index=request["index"],
                    signal="WLAST", expected=f"beat {expected}", actual=f"beat {observed}",
                )
            pending_write_responses[request["id"]].append(
                {"request": request, "beats": beats}
            )

    def complete_read(request: dict[str, Any], response_cycle: Any) -> None:
        beats = request["beats"]
        tx = {
            "index": len(transactions),
            "protocol_index": request["index"],
            "direction": "READ",
            "id": request["id"],
            "exclusive": request["lock"],
            "address": request["address"],
            "burst": request["burst"],
            "length": request["len"],
            "size": request["size"],
            "beats_expected": request["beats_expected"],
            "beats_observed": len(beats),
            "beat_addresses": burst_addresses(request, len(beats)),
            "read_data": [beat["data"] for beat in beats],
            "responses": [beat["response"] for beat in beats],
            "ar_cycle": request["cycle"],
            "response_cycle": response_cycle,
        }
        if request.get("prot") is not None:
            tx["arprot"] = request["prot"]
        if request.get("cache") is not None:
            tx["arcache"] = request["cache"]
        if request.get("region") is not None:
            tx["arregion"] = request["region"]

        if request["lock"]:
            labels = {
                beat["response"]
                for beat in beats
                if beat["response"] in {"OKAY", "EXOKAY"}
            }
            if labels == {"OKAY", "EXOKAY"}:
                add_violation(
                    "exclusive_read_mixed_okay_exokay", beats[-1]["sample"],
                    "An exclusive read burst must not mix OKAY and EXOKAY responses",
                    channel="R", transaction_index=request["index"],
                )
                outcome = "MIXED"
            elif "EXOKAY" in labels:
                outcome = "EXOKAY"
            elif "OKAY" in labels:
                outcome = "OKAY"
            else:
                outcome = "ERROR"

            tx["exclusive_read_outcome"] = outcome
            exclusive_monitors[request["id"]] = {
                "signature": exclusive_signature(request),
                "outcome": outcome,
                "transaction_index": request["index"],
            }

        transactions.append(tx)

    for sample in samples:
        aw_hs = channel_event(sample, "AW")
        w_hs = channel_event(sample, "W")
        b_hs = channel_event(sample, "B")
        ar_hs = channel_event(sample, "AR")
        r_hs = channel_event(sample, "R")

        if aw_hs:
            request = make_request(sample, "AW", issued_write_bursts)
            if request["lock"]:
                exclusive_write_bursts += 1
                pair_exclusive_write(request, sample)
            aw_queue.append(request)
            issued_write_bursts += 1

        if w_hs:
            observed_write_beats += 1
            beat = {
                "sample": sample,
                "cycle": sample["cycle"],
                "data": sample.get("WDATA"),
                "strb": sample.get("WSTRB"),
                "last": bool(sample.get("WLAST", False)),
            }
            current_w_beats.append(beat)
            if beat["last"]:
                completed_w_bursts.append(list(current_w_beats))
                current_w_beats.clear()

        pair_write_bursts()

        if ar_hs:
            request = make_request(sample, "AR", issued_read_bursts)
            request["beats"] = []
            if request["lock"]:
                exclusive_read_bursts += 1
                # A later exclusive read with the same ID replaces the old monitor.
                exclusive_monitors.pop(request["id"], None)
            pending_reads[request["id"]].append(request)
            issued_read_bursts += 1

        if b_hs:
            bid = id_value(sample, "BID", "B")
            queue = pending_write_responses.get(bid)
            if not queue:
                add_violation(
                    "write_response_without_completed_burst", sample,
                    f"B response ID {bid} has no completed write burst awaiting a response",
                    channel="B",
                )
                validate_response(sample, "BRESP", "B")
            else:
                pending = queue.popleft()
                request = pending["request"]
                code, label = validate_response(
                    sample, "BRESP", "B", exclusive=request["lock"]
                )
                beats = pending["beats"]
                if request["lock"]:
                    if label == "EXOKAY":
                        exclusive_write_successes += 1
                        if not request.get("exclusive_pair_matched", False):
                            add_violation(
                                "exclusive_write_success_without_matching_read", sample,
                                "EXOKAY write response requires a matching completed exclusive read",
                                channel="B", transaction_index=request["index"],
                            )
                    elif label == "OKAY":
                        exclusive_write_failures += 1

                tx = {
                    "index": len(transactions),
                    "protocol_index": request["index"],
                    "direction": "WRITE",
                    "id": request["id"],
                    "exclusive": request["lock"],
                    "address": request["address"],
                    "burst": request["burst"],
                    "length": request["len"],
                    "size": request["size"],
                    "beats_expected": request["beats_expected"],
                    "beats_observed": len(beats),
                    "beat_addresses": burst_addresses(request, len(beats)),
                    "write_data": [beat["data"] for beat in beats],
                    "write_strobes": [beat["strb"] for beat in beats],
                    "response": label,
                    "response_code": code,
                    "aw_cycle": request["cycle"],
                    "w_cycles": [beat["cycle"] for beat in beats],
                    "response_cycle": sample["cycle"],
                }
                if request.get("prot") is not None:
                    tx["awprot"] = request["prot"]
                if request.get("cache") is not None:
                    tx["awcache"] = request["cache"]
                if request.get("region") is not None:
                    tx["awregion"] = request["region"]
                if request["lock"]:
                    tx["exclusive_pair_matched"] = request.get(
                        "exclusive_pair_matched", False
                    )
                    tx["exclusive_read_outcome"] = request.get(
                        "exclusive_read_outcome"
                    )
                transactions.append(tx)

        if r_hs:
            observed_read_beats += 1
            rid = id_value(sample, "RID", "R")
            queue = pending_reads.get(rid)
            if not queue:
                add_violation(
                    "read_data_without_request", sample,
                    f"R beat ID {rid} has no pending read request", channel="R",
                )
                validate_response(sample, "RRESP", "R")
            else:
                request = queue[0]
                code, label = validate_response(
                    sample, "RRESP", "R", exclusive=request["lock"]
                )
                beat = {
                    "sample": sample,
                    "cycle": sample["cycle"],
                    "data": sample.get("RDATA"),
                    "response": label,
                    "response_code": code,
                    "last": bool(sample.get("RLAST", False)),
                }
                request["beats"].append(beat)
                observed = len(request["beats"])
                expected = request["beats_expected"]

                if observed < expected and beat["last"]:
                    add_violation(
                        "rlast_early", sample,
                        "RLAST asserted before the ARLEN-defined final read beat",
                        channel="R", transaction_index=request["index"],
                        signal="RLAST", expected=f"beat {expected}", actual=f"beat {observed}",
                    )
                    queue.popleft()
                    complete_read(request, sample["cycle"])
                elif observed == expected:
                    if beat["last"]:
                        queue.popleft()
                        complete_read(request, sample["cycle"])
                    else:
                        add_violation(
                            "rlast_missing_on_final_beat", sample,
                            "RLAST must be asserted on the ARLEN-defined final read beat",
                            channel="R", transaction_index=request["index"],
                            signal="RLAST", expected=True, actual=False,
                        )
                elif observed > expected:
                    add_violation(
                        "extra_read_beat", sample,
                        "Read burst contains more beats than ARLEN permits",
                        channel="R", transaction_index=request["index"],
                        expected=expected, actual=observed,
                    )
                    if beat["last"]:
                        queue.popleft()
                        complete_read(request, sample["cycle"])

    if samples:
        last = samples[-1]
        for channel, state in channel_state.items():
            if state is not None:
                add_violation(
                    "channel_stalled_at_end", last,
                    f"Trace ended while {channel} VALID was waiting for READY",
                    channel=channel,
                )
        for request in aw_queue:
            add_violation(
                "write_address_without_data_burst", last,
                "Accepted AW request was not paired with a WLAST-terminated data burst",
                channel="AW", transaction_index=request["index"],
            )
        for beats in completed_w_bursts:
            add_violation(
                "write_data_burst_without_address", last,
                "WLAST-terminated write data burst has no accepted AW request",
                channel="W", actual=len(beats),
            )
        if current_w_beats:
            add_violation(
                "incomplete_write_data_burst", last,
                "Trace ended with write data beats that never asserted WLAST",
                channel="W", actual=len(current_w_beats),
            )
        for queue in pending_write_responses.values():
            for pending in queue:
                request = pending["request"]
                add_violation(
                    "missing_write_response", last,
                    "Completed write burst did not receive a B response",
                    channel="B", transaction_index=request["index"],
                )
        for queue in pending_reads.values():
            for request in queue:
                add_violation(
                    "missing_read_completion", last,
                    "Read request did not complete with an RLAST-terminated response",
                    channel="R", transaction_index=request["index"],
                    expected=request["beats_expected"], actual=len(request["beats"]),
                )

    writes = [tx for tx in transactions if tx["direction"] == "WRITE"]
    reads = [tx for tx in transactions if tx["direction"] == "READ"]
    write_error_responses = sum(
        1 for tx in writes if tx.get("response") in {"SLVERR", "DECERR"}
    )
    read_error_beats = sum(
        1 for tx in reads for response in tx.get("responses", [])
        if response in {"SLVERR", "DECERR"}
    )

    return {
        "protocol": "AXI4",
        "analysis_level": "normalized_cycle_trace_burst_foundation",
        "source": str(payload.get("source", "normalized-trace")),
        "status": "PASS" if not violations else "FAIL",
        "summary": {
            "samples": len(samples),
            "issued_write_bursts": issued_write_bursts,
            "issued_read_bursts": issued_read_bursts,
            "completed_writes": len(writes),
            "completed_reads": len(reads),
            "completed_transactions": len(transactions),
            "write_beats": observed_write_beats,
            "read_beats": observed_read_beats,
            "write_error_responses": write_error_responses,
            "read_error_beats": read_error_beats,
            "exclusive_read_bursts": exclusive_read_bursts,
            "exclusive_write_bursts": exclusive_write_bursts,
            "exclusive_write_successes": exclusive_write_successes,
            "exclusive_write_failures": exclusive_write_failures,
            "channel_stall_cycles": channel_stall_cycles,
            "violations": len(violations),
        },
        "transactions": transactions,
        "violations": violations,
        "limitations": [
            "Core AXI4 burst, ID, ordering, handshake, response, 4KB-boundary, and exclusive-access sequence rules are modeled.",
            "Exclusive AxCACHE reachability depends on system topology and is reported but not inferred from a trace alone.",
            "ACE coherency, AXI5 additions, USER sidebands, and QoS policy semantics are not modeled.",
            "Waveform extraction is separate; this analyzer consumes normalized ACLK-edge samples.",
        ],
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

    destination = Path(output) if output is not None else Path(".zddv/protocols/axi4/latest.json")
    if not destination.is_absolute():
        destination = project.root / destination
    destination = destination.resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    report["report_path"] = str(destination)
    return report
