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


_AXI4_LEGAL_CACHE_ENCODINGS = frozenset({
    0x0, 0x1, 0x2, 0x3, 0x6, 0x7, 0xA, 0xB, 0xE, 0xF
})


def _decode_axi4_cache_attributes(value: Any) -> dict[str, Any] | None:
    scalar = _scalar(value)
    if not isinstance(scalar, int) or not 0 <= scalar <= 0xF:
        return None
    return {
        "encoding": scalar,
        "bufferable": bool(scalar & 0x1),
        "modifiable": bool(scalar & 0x2),
        "read_allocate": bool(scalar & 0x4),
        "write_allocate": bool(scalar & 0x8),
        "cache_lookup_required": bool(scalar & 0xC),
        "reserved": scalar not in _AXI4_LEGAL_CACHE_ENCODINGS,
    }


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
    if "TIME" in upper:
        sample["time"] = upper["TIME"]

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

    raw_data_width_bits = payload.get("data_width_bits")
    if raw_data_width_bits is None:
        data_width_bits: int | None = None
        data_bus_bytes: int | None = None
    else:
        if (
            isinstance(raw_data_width_bits, bool)
            or not isinstance(raw_data_width_bits, int)
            or raw_data_width_bits <= 0
            or raw_data_width_bits % 8
        ):
            raise ValueError("data_width_bits must be a positive multiple of 8")
        data_width_bits = raw_data_width_bits
        data_bus_bytes = data_width_bits // 8

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
    exclusive_read_monitors: dict[int, dict[str, Any]] = {}
    region_by_4kb: dict[int, int] = {}

    issued_write_bursts = 0
    issued_read_bursts = 0
    observed_write_beats = 0
    observed_read_beats = 0
    issued_exclusive_reads = 0
    issued_exclusive_writes = 0
    matched_exclusive_writes = 0

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

    def validate_address_sidebands(
        sample: dict[str, Any],
        prefix: str,
        *,
        valid_addr: bool,
        addr: Any,
    ) -> dict[str, Any]:
        widths = {
            "CACHE": 4,
            "PROT": 3,
            "QOS": 4,
            "REGION": 4,
        }
        values: dict[str, Any] = {}
        for suffix, width in widths.items():
            field = f"{prefix}{suffix}"
            value = sample.get(field)
            values[suffix.lower()] = value
            if field not in sample:
                continue
            limit = (1 << width) - 1
            if not isinstance(value, int) or not 0 <= value <= limit:
                add_violation(
                    "invalid_address_sideband",
                    sample,
                    f"{field} must fit in {width} bits",
                    channel=prefix,
                    signal=field,
                    expected=f"0..{limit}",
                    actual=value,
                )
            elif suffix == "CACHE" and value not in _AXI4_LEGAL_CACHE_ENCODINGS:
                add_violation(
                    "reserved_cache_encoding",
                    sample,
                    f"{field} uses a reserved AXI4 AxCACHE encoding",
                    channel=prefix,
                    signal=field,
                    expected="0x0/0x1/0x2/0x3/0x6/0x7/0xA/0xB/0xE/0xF",
                    actual=value,
                )

        values["cache_attributes"] = _decode_axi4_cache_attributes(values["cache"])
        region = values["region"]
        if valid_addr and isinstance(region, int) and 0 <= region <= 0xF:
            page_base = addr & ~0xFFF
            previous = region_by_4kb.get(page_base)
            if previous is None:
                region_by_4kb[page_base] = region
            elif previous != region:
                add_violation(
                    "region_changed_within_4kb",
                    sample,
                    "AxREGION must remain constant within a 4KB address space",
                    channel=prefix,
                    signal=f"{prefix}REGION",
                    expected=previous,
                    actual=region,
                )
        return values

    def make_request(sample: dict[str, Any], prefix: str, index: int) -> dict[str, Any]:
        tx_id = id_value(sample, f"{prefix}ID", prefix)
        addr = sample.get(f"{prefix}ADDR")
        length = sample.get(f"{prefix}LEN")
        size = sample.get(f"{prefix}SIZE")
        burst_code, burst = _decode_burst(sample.get(f"{prefix}BURST"))
        lock = bool(sample.get(f"{prefix}LOCK", False))

        valid_addr = isinstance(addr, int) and addr >= 0
        if not valid_addr:
            add_violation(
                "invalid_burst_address", sample,
                f"{prefix}ADDR must be a non-negative integer",
                channel=prefix, signal=f"{prefix}ADDR",
                expected="non-negative integer", actual=addr,
            )

        sidebands = validate_address_sidebands(
            sample,
            prefix,
            valid_addr=valid_addr,
            addr=addr,
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

        if (
            data_bus_bytes is not None
            and beat_bytes is not None
            and beat_bytes > data_bus_bytes
        ):
            add_violation(
                "transfer_size_exceeds_data_bus",
                sample,
                f"{prefix}SIZE selects {beat_bytes} bytes on a {data_bus_bytes}-byte data bus",
                channel=prefix,
                signal=f"{prefix}SIZE",
                expected=f"transfer size <= {data_bus_bytes} bytes",
                actual=beat_bytes,
            )

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

        exclusive_total_bytes = (
            beats * beat_bytes
            if lock and beat_bytes is not None
            else None
        )
        if lock:
            if beats > 16:
                add_violation(
                    "exclusive_burst_too_long", sample,
                    "Exclusive accesses must not exceed 16 transfers",
                    channel=prefix, signal=f"{prefix}LEN",
                    expected="1..16 transfers", actual=beats,
                )
            if exclusive_total_bytes is not None:
                if (
                    exclusive_total_bytes <= 0
                    or exclusive_total_bytes & (exclusive_total_bytes - 1)
                ):
                    add_violation(
                        "exclusive_size_not_power_of_two", sample,
                        "Exclusive access byte count must be a power of two",
                        channel=prefix,
                        expected="1/2/4/8/16/32/64/128 bytes",
                        actual=exclusive_total_bytes,
                    )
                if exclusive_total_bytes > 128:
                    add_violation(
                        "exclusive_size_exceeds_128_bytes", sample,
                        "Exclusive accesses must not exceed 128 bytes",
                        channel=prefix,
                        expected="<=128 bytes",
                        actual=exclusive_total_bytes,
                    )
                if (
                    valid_addr
                    and exclusive_total_bytes > 0
                    and addr % exclusive_total_bytes
                ):
                    add_violation(
                        "exclusive_address_unaligned", sample,
                        "Exclusive access address must align to the total transaction size",
                        channel=prefix, signal=f"{prefix}ADDR",
                        expected=f"multiple of {exclusive_total_bytes}",
                        actual=addr,
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
            "lock": lock,
            "exclusive_total_bytes": exclusive_total_bytes,
            "cycle": sample["cycle"],
            "sample_index": sample["sample_index"],
            "time": sample.get("time"),
            "region": sidebands["region"],
            "cache": sidebands["cache"],
            "cache_attributes": sidebands["cache_attributes"],
            "prot": sidebands["prot"],
            "qos": sidebands["qos"],
        }

    def exclusive_attribute_mismatches(
        read: dict[str, Any],
        write: dict[str, Any],
    ) -> list[str]:
        mismatches: list[str] = []
        for field in ("address", "len", "size", "burst", "region", "cache", "prot"):
            read_value = read.get(field)
            write_value = write.get(field)
            if (
                read_value is not None
                and write_value is not None
                and read_value != write_value
            ):
                mismatches.append(field)
        return mismatches

    def classify_exclusive_write(
        request: dict[str, Any],
        sample: dict[str, Any],
    ) -> None:
        nonlocal matched_exclusive_writes
        request["exclusive_pair_status"] = "not_exclusive"
        request["exclusive_pair_mismatches"] = []
        if not request["lock"]:
            return

        monitor = exclusive_read_monitors.get(request["id"])
        if monitor is None:
            request["exclusive_pair_status"] = "no_prior_exclusive_read"
            return

        mismatches = exclusive_attribute_mismatches(monitor, request)
        request["exclusive_pair_mismatches"] = mismatches
        if mismatches:
            request["exclusive_pair_status"] = "attributes_mismatch"
            return

        if not monitor.get("exclusive_completed", False):
            request["exclusive_pair_status"] = "read_incomplete"
            add_violation(
                "exclusive_write_before_read_complete",
                sample,
                "Exclusive write started before the matching exclusive read completed",
                channel="AW",
                transaction_index=request["index"],
            )
            return

        request["exclusive_pair_status"] = "matched"
        request["exclusive_read_response_class"] = monitor.get(
            "exclusive_read_response_class"
        )
        matched_exclusive_writes += 1
        exclusive_read_monitors.pop(request["id"], None)

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

    def validate_write_strobes(
        request: dict[str, Any],
        beats: list[dict[str, Any]],
    ) -> None:
        if data_bus_bytes is None:
            return

        beat_bytes = request.get("beat_bytes")
        if (
            not isinstance(beat_bytes, int)
            or beat_bytes <= 0
            or beat_bytes > data_bus_bytes
        ):
            return

        addresses = burst_addresses(request, len(beats))
        if addresses is None:
            return

        bus_mask = (1 << data_bus_bytes) - 1
        for beat_index, (beat, beat_addr) in enumerate(zip(beats, addresses)):
            strobe = beat.get("strb")
            if not isinstance(strobe, int) or strobe < 0 or strobe > bus_mask:
                add_violation(
                    "invalid_write_strobe",
                    beat["sample"],
                    f"WSTRB on write beat {beat_index} must fit the {data_bus_bytes}-byte data bus",
                    channel="W",
                    transaction_index=request["index"],
                    signal="WSTRB",
                    expected=f"0x0..0x{bus_mask:X}",
                    actual=strobe,
                )
                beat["allowed_strb_mask"] = None
                continue

            aligned_addr = beat_addr - (beat_addr % beat_bytes)
            lower_lane = beat_addr % data_bus_bytes
            upper_lane = (aligned_addr + beat_bytes - 1) % data_bus_bytes
            if upper_lane < lower_lane:
                beat["allowed_strb_mask"] = None
                continue

            allowed_mask = (
                ((1 << (upper_lane - lower_lane + 1)) - 1) << lower_lane
            )
            beat["allowed_strb_mask"] = allowed_mask
            if strobe & ~allowed_mask:
                add_violation(
                    "write_strobe_outside_transfer",
                    beat["sample"],
                    f"WSTRB asserts byte lanes outside write beat {beat_index}'s address/size window",
                    channel="W",
                    transaction_index=request["index"],
                    signal="WSTRB",
                    expected=f"subset of 0x{allowed_mask:X}",
                    actual=f"0x{strobe:X}",
                )

    def pair_write_bursts() -> None:
        while aw_queue and completed_w_bursts:
            request = aw_queue.popleft()
            beats = completed_w_bursts.popleft()
            validate_write_strobes(request, beats)
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
        if request["lock"]:
            request["exclusive_completed"] = True
        tx = {
            "index": len(transactions),
            "protocol_index": request["index"],
            "direction": "READ",
            "id": request["id"],
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
            "exclusive": request["lock"],
        }
        if request["lock"]:
            tx["exclusive_total_bytes"] = request["exclusive_total_bytes"]
            tx["exclusive_response_class"] = request.get(
                "exclusive_read_response_class"
            )
        if request.get("time") is not None:
            tx["ar_time"] = request["time"]
        if beats and beats[-1].get("time") is not None:
            tx["response_time"] = beats[-1]["time"]
        read_times = [beat.get("time") for beat in beats]
        if any(value is not None for value in read_times):
            tx["read_times"] = read_times
        for key in ("cache", "prot", "qos", "region"):
            if request.get(key) is not None:
                tx[key] = request[key]
                tx[f"ar{key}"] = request[key]
        if request.get("cache_attributes") is not None:
            tx["cache_attributes"] = request["cache_attributes"]
        transactions.append(tx)

    for sample in samples:
        aw_hs = channel_event(sample, "AW")
        w_hs = channel_event(sample, "W")
        b_hs = channel_event(sample, "B")
        ar_hs = channel_event(sample, "AR")
        r_hs = channel_event(sample, "R")

        if ar_hs:
            request = make_request(sample, "AR", issued_read_bursts)
            request["beats"] = []
            if request["lock"]:
                issued_exclusive_reads += 1
                request["exclusive_completed"] = False
                request["exclusive_read_response_class"] = None
                request["exclusive_response_mix_reported"] = False
                exclusive_read_monitors[request["id"]] = request
            pending_reads[request["id"]].append(request)
            issued_read_bursts += 1

        if aw_hs:
            request = make_request(sample, "AW", issued_write_bursts)
            if request["lock"]:
                issued_exclusive_writes += 1
                classify_exclusive_write(request, sample)
            aw_queue.append(request)
            issued_write_bursts += 1

        if w_hs:
            observed_write_beats += 1
            beat = {
                "sample": sample,
                "cycle": sample["cycle"],
                "time": sample.get("time"),
                "data": sample.get("WDATA"),
                "strb": sample.get("WSTRB"),
                "last": bool(sample.get("WLAST", False)),
            }
            current_w_beats.append(beat)
            if beat["last"]:
                completed_w_bursts.append(list(current_w_beats))
                current_w_beats.clear()

        pair_write_bursts()

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
                if (
                    request["lock"]
                    and code == 1
                    and request.get("exclusive_pair_status") != "matched"
                ):
                    add_violation(
                        "exokay_without_matching_exclusive_read",
                        sample,
                        "EXOKAY write response requires a completed matching exclusive read sequence",
                        channel="B",
                        transaction_index=request["index"],
                        signal="BRESP",
                        expected="OKAY/SLVERR/DECERR for an unmatched exclusive write",
                        actual=label,
                    )
                if (
                    request["lock"]
                    and code == 1
                    and request.get("exclusive_pair_status") == "matched"
                    and request.get("exclusive_read_response_class") == "OKAY"
                ):
                    add_violation(
                        "exclusive_write_exokay_after_okay_read",
                        sample,
                        "Exclusive write returned EXOKAY after the matching exclusive read reported OKAY",
                        channel="B",
                        transaction_index=request["index"],
                        signal="BRESP",
                        expected="OKAY/SLVERR/DECERR",
                        actual=label,
                    )
                beats = pending["beats"]
                tx = {
                    "index": len(transactions),
                    "protocol_index": request["index"],
                    "direction": "WRITE",
                    "id": request["id"],
                    "address": request["address"],
                    "burst": request["burst"],
                    "length": request["len"],
                    "size": request["size"],
                    "beats_expected": request["beats_expected"],
                    "beats_observed": len(beats),
                    "beat_addresses": burst_addresses(request, len(beats)),
                    "write_data": [beat["data"] for beat in beats],
                    "write_strobes": [beat["strb"] for beat in beats],
                    "write_strobe_allowed_masks": (
                        [beat.get("allowed_strb_mask") for beat in beats]
                        if data_bus_bytes is not None
                        else None
                    ),
                    "response": label,
                    "response_code": code,
                    "aw_cycle": request["cycle"],
                    "w_cycles": [beat["cycle"] for beat in beats],
                    "response_cycle": sample["cycle"],
                    "exclusive": request["lock"],
                }
                if request["lock"]:
                    tx["exclusive_total_bytes"] = request["exclusive_total_bytes"]
                    tx["exclusive_pair_status"] = request.get(
                        "exclusive_pair_status"
                    )
                    tx["exclusive_pair_mismatches"] = request.get(
                        "exclusive_pair_mismatches", []
                    )
                if request.get("time") is not None:
                    tx["aw_time"] = request["time"]
                w_times = [beat.get("time") for beat in beats]
                if any(value is not None for value in w_times):
                    tx["w_times"] = w_times
                if sample.get("time") is not None:
                    tx["response_time"] = sample["time"]
                for key in ("cache", "prot", "qos", "region"):
                    if request.get(key) is not None:
                        tx[key] = request[key]
                        tx[f"aw{key}"] = request[key]
                if request.get("cache_attributes") is not None:
                    tx["cache_attributes"] = request["cache_attributes"]
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
                if request["lock"] and code in {0, 1}:
                    response_class = "EXOKAY" if code == 1 else "OKAY"
                    previous_class = request.get(
                        "exclusive_read_response_class"
                    )
                    if previous_class is None:
                        request["exclusive_read_response_class"] = response_class
                    elif (
                        previous_class != response_class
                        and not request.get(
                            "exclusive_response_mix_reported", False
                        )
                    ):
                        add_violation(
                            "exclusive_read_mixed_okay_exokay",
                            sample,
                            "Exclusive read must not mix OKAY and EXOKAY response beats",
                            channel="R",
                            transaction_index=request["index"],
                            signal="RRESP",
                            expected=previous_class,
                            actual=response_class,
                        )
                        request["exclusive_response_mix_reported"] = True
                beat = {
                    "cycle": sample["cycle"],
                    "time": sample.get("time"),
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

    result = {
        "protocol": "AXI4",
        "analysis_level": "normalized_cycle_trace_burst_exclusive_sideband_semantics_wstrb",
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
            "exclusive_reads": issued_exclusive_reads,
            "exclusive_writes": issued_exclusive_writes,
            "matched_exclusive_writes": matched_exclusive_writes,
            "channel_stall_cycles": channel_stall_cycles,
            "violations": len(violations),
        },
        "transactions": transactions,
        "violations": violations,
        "limitations": [
            "Core AXI4 burst, ID, ordering, handshake, response, and 4KB-boundary rules are modeled.",
            "Core AXI4 exclusive size/alignment, sequence timing, response-class, and observable read/write pairing checks are modeled.",
            "AXI4 address-sideband widths are checked for AxCACHE, AxPROT, AxQOS, and AxREGION; reserved AXI4 AxCACHE encodings are rejected and B/M/RA/WA semantics are decoded; AxREGION is checked for 4KB-space consistency.",
            "When data_width_bits is known, AxSIZE is bounded by the interface data width and WSTRB is checked against the legal byte lanes for every accepted write beat.",
            "Normalized traces without data_width_bits cannot prove data-bus-width or WSTRB byte-lane legality.",
            "Topology-dependent AxCACHE reachability and cross-master memory-attribute consistency, ACE coherency, AXI5 additions, USER sidebands, and QoS policy are not modeled.",
            "VCD waveform extraction samples the configured AXI4 scope on ACLK edges before applying this normalized analyzer.",
        ],
    }
    if data_width_bits is not None:
        result["data_width_bits"] = data_width_bits
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

    destination = Path(output) if output is not None else Path(".zddv/protocols/axi4/latest.json")
    if not destination.is_absolute():
        destination = project.root / destination
    destination = destination.resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    report["report_path"] = str(destination)
    return report
