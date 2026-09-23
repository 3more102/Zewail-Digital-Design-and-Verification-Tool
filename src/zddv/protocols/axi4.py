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
            "AWLOCK", "AWCACHE", "AWPROT", "AWQOS", "AWREGION", "AWUSER",
        ),
        "required": ("AWADDR", "AWLEN", "AWSIZE", "AWBURST"),
    },
    "W": {
        "valid": "WVALID",
        "ready": "WREADY",
        "payload": ("WDATA", "WSTRB", "WLAST", "WUSER"),
        "required": ("WDATA", "WSTRB", "WLAST"),
    },
    "B": {
        "valid": "BVALID",
        "ready": "BREADY",
        "payload": ("BID", "BRESP", "BUSER"),
        "required": ("BRESP",),
    },
    "AR": {
        "valid": "ARVALID",
        "ready": "ARREADY",
        "payload": (
            "ARID", "ARADDR", "ARLEN", "ARSIZE", "ARBURST",
            "ARLOCK", "ARCACHE", "ARPROT", "ARQOS", "ARREGION", "ARUSER",
        ),
        "required": ("ARADDR", "ARLEN", "ARSIZE", "ARBURST"),
    },
    "R": {
        "valid": "RVALID",
        "ready": "RREADY",
        "payload": ("RID", "RDATA", "RRESP", "RLAST", "RUSER"),
        "required": ("RDATA", "RRESP", "RLAST"),
    },
}

_RESPONSE_NAMES = {0: "OKAY", 1: "EXOKAY", 2: "SLVERR", 3: "DECERR"}
_RESPONSE_CODES = {name: code for code, name in _RESPONSE_NAMES.items()}
_BURST_NAMES = {0: "FIXED", 1: "INCR", 2: "WRAP"}
_BURST_CODES = {name: code for code, name in _BURST_NAMES.items()}

# AMBA AXI4 Issue H, A9.3 master-interface defaults. These are applied only
# when the normalized trace explicitly declares the corresponding master
# output physically absent; ordinary missing sample fields are never guessed.
_MASTER_DEFAULTABLE_SIGNALS = frozenset({
    "AWID", "AWREGION", "AWLEN", "AWSIZE", "AWBURST", "AWLOCK",
    "AWCACHE", "AWQOS", "WSTRB",
    "ARID", "ARREGION", "ARLEN", "ARSIZE", "ARBURST", "ARLOCK",
    "ARCACHE", "ARQOS",
})


_AXI4_USER_SIGNALS = frozenset({
    "AWUSER", "WUSER", "BUSER", "ARUSER", "RUSER",
})

_AXI4_ID_WIDTH_PROPERTIES = {
    "ID_W_WIDTH": ("AWID", "BID"),
    "ID_R_WIDTH": ("ARID", "RID"),
}


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


def _axi4_cache_memory_class(value: int) -> str | None:
    """Return the AXI4 memory-class family observable from one AxCACHE value."""
    if value == 0x0:
        return "device_non_bufferable"
    if value == 0x1:
        return "device_bufferable"
    if value == 0x2:
        return "normal_non_cacheable_non_bufferable"
    if value == 0x3:
        return "normal_non_cacheable_bufferable"
    if value in {0x6, 0xA, 0xE}:
        return "write_through"
    if value in {0x7, 0xB, 0xF}:
        return "write_back"
    return None


def _decode_axi4_cache_attributes(
    value: Any,
    *,
    prefix: str,
) -> dict[str, Any] | None:
    scalar = _scalar(value)
    if not isinstance(scalar, int) or not 0 <= scalar <= 0xF:
        return None
    if prefix not in {"AR", "AW"}:
        raise ValueError("AXI4 cache direction prefix must be AR or AW")

    # AXI4 Issue H, Tables A4-3/A4-4:
    #   AWCACHE[3] = Allocate,       AWCACHE[2] = Other Allocate
    #   ARCACHE[2] = Allocate,       ARCACHE[3] = Other Allocate
    # The legacy read_allocate/write_allocate keys below intentionally retain
    # their raw bit-position meaning for backward compatibility.
    allocate_bit = 2 if prefix == "AR" else 3
    other_allocate_bit = 3 if prefix == "AR" else 2
    return {
        "encoding": scalar,
        "direction": "read" if prefix == "AR" else "write",
        "bufferable": bool(scalar & 0x1),
        "modifiable": bool(scalar & 0x2),
        "read_allocate": bool(scalar & 0x4),
        "write_allocate": bool(scalar & 0x8),
        "allocate": bool(scalar & (1 << allocate_bit)),
        "other_allocate": bool(scalar & (1 << other_allocate_bit)),
        "allocate_bit": allocate_bit,
        "other_allocate_bit": other_allocate_bit,
        "cache_lookup_required": bool(scalar & 0xC),
        "memory_class": _axi4_cache_memory_class(scalar),
        "reserved": scalar not in _AXI4_LEGAL_CACHE_ENCODINGS,
    }


def _decode_axi4_prot_attributes(value: Any) -> dict[str, Any] | None:
    scalar = _scalar(value)
    if not isinstance(scalar, int) or not 0 <= scalar <= 0x7:
        return None
    return {
        "encoding": scalar,
        "privileged": bool(scalar & 0x1),
        "non_secure": bool(scalar & 0x2),
        "instruction": bool(scalar & 0x4),
        "privilege_class": "privileged" if scalar & 0x1 else "unprivileged",
        "security_class": "non-secure" if scalar & 0x2 else "secure",
        "access_class": "instruction" if scalar & 0x4 else "data",
        "access_is_hint": True,
    }


def _decode_axi4_qos_attributes(value: Any) -> dict[str, Any] | None:
    """Decode protocol-defined AxQOS evidence without inferring a system QoS policy."""
    scalar = _scalar(value)
    if not isinstance(scalar, int) or not 0 <= scalar <= 0xF:
        return None
    return {
        "encoding": scalar,
        "is_default_no_qos": scalar == 0,
        "recommended_priority": scalar,
        "higher_value_recommended_higher_priority": True,
        "exact_use_protocol_defined": False,
        "axi_ordering_rules_take_precedence": True,
    }


def _decode_axi4_region_attributes(value: Any) -> dict[str, Any] | None:
    """Decode observable AxREGION evidence without assuming system topology."""
    scalar = _scalar(value)
    if not isinstance(scalar, int) or not 0 <= scalar <= 0xF:
        return None
    return {
        "encoding": scalar,
        "is_default": scalar == 0,
        "identifier_width_bits": 4,
        "identifier_capacity": 16,
        "can_decode_higher_order_address_bits": True,
        "creates_independent_address_space": False,
        "downstream_address_decode_requirement_requires_topology": True,
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


def _master_default_signals(
    payload: dict[str, Any],
    raw_samples: list[Any],
    *,
    data_bus_bytes: int | None,
) -> dict[str, Any]:
    raw_absent = payload.get("absent_master_signals", [])
    if raw_absent is None:
        raw_absent = []
    if not isinstance(raw_absent, list) or any(
        not isinstance(item, str) or not item.strip()
        for item in raw_absent
    ):
        raise ValueError("absent_master_signals must be a list of non-empty signal names")

    absent: list[str] = []
    seen: set[str] = set()
    for item in raw_absent:
        name = item.strip().upper()
        if name not in _MASTER_DEFAULTABLE_SIGNALS:
            raise ValueError(
                f"{name} has no supported AXI4 master-interface default in this analyzer"
            )
        if name in seen:
            raise ValueError(f"absent_master_signals contains duplicate {name}")
        seen.add(name)
        absent.append(name)

    width_dependent = {"AWSIZE", "ARSIZE", "WSTRB"} & seen
    if width_dependent and data_bus_bytes is None:
        names = ", ".join(sorted(width_dependent))
        raise ValueError(
            f"data_width_bits is required to default width-dependent signal(s): {names}"
        )

    for index, raw in enumerate(raw_samples):
        if not isinstance(raw, dict):
            continue
        observed = {str(key).upper() for key in raw}
        conflict = sorted(observed & seen)
        if conflict:
            raise ValueError(
                "signal declared absent_master_signals is present in sample "
                f"{index}: {', '.join(conflict)}"
            )

    defaults: dict[str, Any] = {
        "AWID": 0,
        "AWREGION": 0,
        "AWLEN": 0,
        "AWBURST": 1,
        "AWLOCK": 0,
        "AWCACHE": 0,
        "AWQOS": 0,
        "ARID": 0,
        "ARREGION": 0,
        "ARLEN": 0,
        "ARBURST": 1,
        "ARLOCK": 0,
        "ARCACHE": 0,
        "ARQOS": 0,
    }
    if data_bus_bytes is not None:
        size_encoding = data_bus_bytes.bit_length() - 1
        defaults["AWSIZE"] = size_encoding
        defaults["ARSIZE"] = size_encoding
        defaults["WSTRB"] = (1 << data_bus_bytes) - 1

    return {name: defaults[name] for name in absent}


def _configured_id_widths(payload: dict[str, Any]) -> dict[str, int]:
    """Normalize explicit AXI4 transaction-ID width interface metadata."""
    raw = payload.get("id_widths", {})
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ValueError(
            "id_widths must be an object mapping ID_W_WIDTH/ID_R_WIDTH "
            "to integer bit widths in the range 0..32"
        )

    widths: dict[str, int] = {}
    for raw_name, raw_width in raw.items():
        name = str(raw_name).strip().upper()
        if name not in _AXI4_ID_WIDTH_PROPERTIES:
            raise ValueError(
                f"unsupported AXI4 ID width property in id_widths: {raw_name!r}"
            )
        if name in widths:
            raise ValueError(
                f"id_widths contains duplicate property after normalization: {name}"
            )
        if isinstance(raw_width, bool):
            raise ValueError(
                f"id_widths[{name}] must be an integer bit width in the range 0..32"
            )
        width = _scalar(raw_width)
        if not isinstance(width, int) or not 0 <= width <= 32:
            raise ValueError(
                f"id_widths[{name}] must be an integer bit width in the range 0..32"
            )
        widths[name] = width

    return {name: widths[name] for name in sorted(widths)}


def _configured_user_signal_widths(payload: dict[str, Any]) -> dict[str, int]:
    """Normalize explicit interface metadata for implementation-defined USER signals."""
    raw = payload.get("user_signal_widths", {})
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ValueError(
            "user_signal_widths must be an object mapping AXI4 USER signal names "
            "to non-negative bit widths"
        )

    widths: dict[str, int] = {}
    for raw_name, raw_width in raw.items():
        name = str(raw_name).strip().upper()
        if name not in _AXI4_USER_SIGNALS:
            raise ValueError(
                f"unsupported AXI4 USER signal in user_signal_widths: {raw_name!r}"
            )
        if name in widths:
            raise ValueError(
                f"user_signal_widths contains duplicate signal after normalization: {name}"
            )
        if isinstance(raw_width, bool):
            raise ValueError(
                f"user_signal_widths[{name}] must be a non-negative integer bit width"
            )
        width = _scalar(raw_width)
        if not isinstance(width, int) or width < 0:
            raise ValueError(
                f"user_signal_widths[{name}] must be a non-negative integer bit width"
            )
        widths[name] = width

    normalized = {name: widths[name] for name in sorted(widths)}

    request_widths = {
        normalized[name]
        for name in ("AWUSER", "ARUSER")
        if name in normalized
    }
    if len(request_widths) > 1:
        raise ValueError(
            "user_signal_widths must use the same request width for AWUSER and ARUSER "
            "when both are specified (AXI USER_REQ_WIDTH)"
        )

    if all(name in normalized for name in ("WUSER", "BUSER", "RUSER")):
        expected_ruser_width = normalized["WUSER"] + normalized["BUSER"]
        if normalized["RUSER"] != expected_ruser_width:
            raise ValueError(
                "user_signal_widths RUSER must equal WUSER + BUSER when all three "
                "are specified (AXI USER_DATA_WIDTH + USER_RESP_WIDTH)"
            )

    return normalized


def _normalize_sample(
    raw: dict[str, Any],
    index: int,
    *,
    master_defaults: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError(f"AXI4 sample {index} must be an object")

    upper = {str(key).upper(): value for key, value in raw.items()}
    if master_defaults:
        upper.update(master_defaults)
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
        "AWPROT", "AWQOS", "AWREGION", "AWUSER", "WDATA", "WSTRB", "WUSER",
        "BID", "BRESP", "BUSER", "ARID", "ARADDR", "ARLEN", "ARSIZE",
        "ARBURST", "ARCACHE", "ARPROT", "ARQOS", "ARREGION", "ARUSER",
        "RID", "RDATA", "RRESP", "RUSER",
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

    data_width_bits = payload.get("data_width_bits")
    data_bus_bytes: int | None = None
    if data_width_bits is not None:
        data_width_bits = _scalar(data_width_bits)
        legal_data_widths = {8, 16, 32, 64, 128, 256, 512, 1024}
        if (
            not isinstance(data_width_bits, int)
            or data_width_bits not in legal_data_widths
        ):
            raise ValueError(
                "data_width_bits must be one of "
                "8, 16, 32, 64, 128, 256, 512, or 1024"
            )
        data_bus_bytes = data_width_bits // 8

    address_width_bits = payload.get("address_width_bits")
    if address_width_bits is not None:
        if isinstance(address_width_bits, bool):
            raise ValueError(
                "address_width_bits must be an integer bit width in the range 1..64"
            )
        address_width_bits = _scalar(address_width_bits)
        if (
            not isinstance(address_width_bits, int)
            or not 1 <= address_width_bits <= 64
        ):
            raise ValueError(
                "address_width_bits must be an integer bit width in the range 1..64"
            )

    id_widths = _configured_id_widths(payload)
    user_signal_widths = _configured_user_signal_widths(payload)
    master_defaults = _master_default_signals(
        payload,
        raw_samples,
        data_bus_bytes=data_bus_bytes,
    )
    for property_name, manager_signal in (
        ("ID_W_WIDTH", "AWID"),
        ("ID_R_WIDTH", "ARID"),
    ):
        configured_width = id_widths.get(property_name)
        if (
            configured_width is not None
            and configured_width > 0
            and manager_signal in master_defaults
        ):
            raise ValueError(
                f"id_widths[{property_name}]={configured_width} requires "
                f"{manager_signal} to be present, but absent_master_signals "
                f"declares it physically absent"
            )
    samples = [
        _normalize_sample(
            sample,
            index,
            master_defaults=master_defaults,
        )
        for index, sample in enumerate(raw_samples)
    ]
    violations: list[dict[str, Any]] = []
    advisories: list[dict[str, Any]] = []
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

    def add_advisory(
        code: str,
        message: str,
        *,
        transaction_index: int | None = None,
        signal: str | None = None,
        expected: Any = None,
        actual: Any = None,
    ) -> None:
        entry: dict[str, Any] = {
            "code": code,
            "kind": "recommendation",
            "message": message,
        }
        if transaction_index is not None:
            entry["transaction_index"] = transaction_index
        if signal is not None:
            entry["signal"] = signal
            entry["expected"] = expected
            entry["actual"] = actual
        advisories.append(entry)

    def validate_user_sidebands(sample: dict[str, Any]) -> None:
        channels = {
            "AWUSER": "AW",
            "WUSER": "W",
            "BUSER": "B",
            "ARUSER": "AR",
            "RUSER": "R",
        }
        for signal, width in user_signal_widths.items():
            if signal not in sample:
                continue
            value = sample[signal]
            channel = channels[signal]
            if width == 0:
                add_violation(
                    "user_sideband_present_when_width_zero",
                    sample,
                    f"{signal} is present although interface metadata declares a 0-bit width",
                    channel=channel,
                    signal=signal,
                    expected="signal absent",
                    actual=value,
                )
                continue
            if not isinstance(value, int) or value < 0 or value >= (1 << width):
                add_violation(
                    "invalid_user_sideband_width",
                    sample,
                    f"{signal} does not fit the configured USER signal width",
                    channel=channel,
                    signal=signal,
                    expected=f"unsigned {width}-bit value (0..{(1 << width) - 1})",
                    actual=value,
                )

    def evaluate_user_signal_guidance() -> None:
        request_signal = (
            "AWUSER"
            if "AWUSER" in user_signal_widths
            else "ARUSER"
            if "ARUSER" in user_signal_widths
            else None
        )
        if request_signal is not None:
            request_width = user_signal_widths[request_signal]
            if request_width > 128:
                add_advisory(
                    "user_req_width_above_guidance",
                    "USER_REQ_WIDTH exceeds the Arm guidance maximum of 128 bits",
                    signal=request_signal,
                    expected="0..128 bits (guidance)",
                    actual=request_width,
                )

        data_width = user_signal_widths.get("WUSER")
        if data_width is not None and data_width_bits is not None:
            if data_width > data_width_bits // 2:
                add_advisory(
                    "user_data_width_above_guidance",
                    "USER_DATA_WIDTH exceeds the Arm guidance maximum of DATA_WIDTH/2",
                    signal="WUSER",
                    expected=f"0..{data_width_bits // 2} bits (guidance)",
                    actual=data_width,
                )
            if (
                data_width > 0
                and data_bus_bytes is not None
                and data_width % data_bus_bytes != 0
            ):
                add_advisory(
                    "user_data_width_granularity_recommendation",
                    "USER_DATA_WIDTH is recommended to be an integer multiple of the data-bus width in bytes",
                    signal="WUSER",
                    expected=f"multiple of {data_bus_bytes} bits (recommendation)",
                    actual=data_width,
                )

        response_width = user_signal_widths.get("BUSER")
        if response_width is not None and response_width > 16:
            add_advisory(
                "user_resp_width_above_guidance",
                "USER_RESP_WIDTH exceeds the Arm guidance maximum of 16 bits",
                signal="BUSER",
                expected="0..16 bits (guidance)",
                actual=response_width,
            )

    evaluate_user_signal_guidance()

    def validate_configured_id_signals(
        sample: dict[str, Any],
        raw_sample: dict[str, Any],
    ) -> None:
        observed = {str(key).upper() for key in raw_sample}
        channels = {
            "AWID": "AW",
            "BID": "B",
            "ARID": "AR",
            "RID": "R",
        }
        for property_name, signals in _AXI4_ID_WIDTH_PROPERTIES.items():
            if property_name not in id_widths:
                continue
            width = id_widths[property_name]
            for signal in signals:
                channel = channels[signal]
                if width == 0:
                    if signal in observed:
                        add_violation(
                            "id_signal_present_when_width_zero",
                            sample,
                            f"{signal} is physically observed although {property_name}=0 "
                            "declares the ID signal absent",
                            channel=channel,
                            signal=signal,
                            expected="signal absent",
                            actual=sample.get(signal),
                        )
                    continue

                if not sample[_CHANNELS[channel]["valid"]]:
                    continue

                if signal not in observed:
                    add_violation(
                        "missing_transaction_id",
                        sample,
                        f"{signal} is required while "
                        f"{_CHANNELS[channel]['valid']} is asserted because "
                        f"{property_name}={width}",
                        channel=channel,
                        signal=signal,
                        expected=f"unsigned {width}-bit transaction ID",
                        actual=None,
                    )
                    continue

                value = sample.get(signal)
                if not isinstance(value, int) or value < 0:
                    add_violation(
                        "invalid_transaction_id",
                        sample,
                        f"{signal} must be a non-negative integer",
                        channel=channel,
                        signal=signal,
                        expected="non-negative integer",
                        actual=value,
                    )
                elif value >= (1 << width):
                    add_violation(
                        "invalid_transaction_id_width",
                        sample,
                        f"{signal} does not fit {property_name}={width}",
                        channel=channel,
                        signal=signal,
                        expected=f"0..{(1 << width) - 1}",
                        actual=value,
                    )

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
        property_name = (
            "ID_W_WIDTH" if channel in {"AW", "B"} else "ID_R_WIDTH"
        )
        configured_width = id_widths.get(property_name)

        if configured_width == 0:
            return 0

        value = sample.get(field, 0)
        if configured_width is not None:
            # Explicit ID metadata is validated against raw physical evidence on
            # every VALID cycle before handshake reconstruction. Keep transaction
            # reconstruction deterministic without emitting duplicate violations.
            if (
                not isinstance(value, int)
                or value < 0
                or value >= (1 << configured_width)
            ):
                return 0
            return value

        if not isinstance(value, int) or value < 0:
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

        return value

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

        values["cache_attributes"] = _decode_axi4_cache_attributes(
            values["cache"],
            prefix=prefix,
        )
        values["prot_attributes"] = _decode_axi4_prot_attributes(values["prot"])
        values["qos_attributes"] = _decode_axi4_qos_attributes(values["qos"])
        values["region_attributes"] = _decode_axi4_region_attributes(values["region"])
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
        elif (
            address_width_bits is not None
            and addr >= (1 << address_width_bits)
        ):
            add_violation(
                "invalid_address_width",
                sample,
                f"{prefix}ADDR does not fit ADDR_WIDTH={address_width_bits}",
                channel=prefix,
                signal=f"{prefix}ADDR",
                expected=f"0..{(1 << address_width_bits) - 1}",
                actual=addr,
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
                "transfer_size_exceeds_data_bus_width",
                sample,
                f"{prefix}SIZE selects a transfer wider than the data channel",
                channel=prefix,
                signal=f"{prefix}SIZE",
                expected=f"<= {data_bus_bytes} byte(s) per transfer",
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
                # An unaligned FIXED transfer only accesses the remaining byte
                # lanes in its naturally aligned transfer container.  Using
                # addr + beat_bytes - 1 here would invent bytes beyond that
                # container and can falsely report a 4KB crossing at 0x...FFF.
                aligned = addr - (addr % beat_bytes)
                end_addr = aligned + beat_bytes - 1
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
            "prot_attributes": sidebands["prot_attributes"],
            "qos": sidebands["qos"],
            "qos_attributes": sidebands["qos_attributes"],
            "region_attributes": sidebands["region_attributes"],
            "user": sample.get(f"{prefix}USER"),
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

    def write_strobe_mask(
        request: dict[str, Any],
        beat_index: int,
    ) -> int | None:
        if data_bus_bytes is None:
            return None
        addr = request["address"]
        beat_bytes = request["beat_bytes"]
        burst = request["burst"]
        if (
            not isinstance(addr, int)
            or beat_bytes is None
            or beat_bytes > data_bus_bytes
            or burst not in {"FIXED", "INCR", "WRAP"}
        ):
            return None

        addresses = burst_addresses(request, beat_index + 1)
        if not addresses:
            return None
        beat_addr = addresses[beat_index]
        lower_lane = beat_addr % data_bus_bytes

        unaligned_window = bool(addr % beat_bytes) and (
            beat_index == 0 or burst == "FIXED"
        )
        if unaligned_window:
            aligned_addr = addr - (addr % beat_bytes)
            upper_lane = (
                aligned_addr
                + beat_bytes
                - 1
                - (addr // data_bus_bytes) * data_bus_bytes
            )
        else:
            upper_lane = lower_lane + beat_bytes - 1

        if upper_lane < lower_lane or upper_lane >= data_bus_bytes:
            return None
        return ((1 << (upper_lane - lower_lane + 1)) - 1) << lower_lane

    def validate_write_strobes(
        request: dict[str, Any],
        beats: list[dict[str, Any]],
    ) -> None:
        if data_bus_bytes is None:
            return
        full_mask = (1 << data_bus_bytes) - 1
        for beat_index, beat in enumerate(beats):
            sample = beat["sample"]
            strobe = beat["strb"]
            if (
                not isinstance(strobe, int)
                or strobe < 0
                or strobe & ~full_mask
            ):
                add_violation(
                    "invalid_write_strobe",
                    sample,
                    "WSTRB must fit the byte-lane width of the AXI data channel",
                    channel="W",
                    transaction_index=request["index"],
                    signal="WSTRB",
                    expected=f"0x0..0x{full_mask:X}",
                    actual=strobe,
                )
                continue

            allowed_mask = write_strobe_mask(request, beat_index)
            beat["allowed_strobe_mask"] = allowed_mask
            if allowed_mask is not None and strobe & ~allowed_mask:
                add_violation(
                    "write_strobe_outside_transfer_lanes",
                    sample,
                    "WSTRB asserted byte lane(s) outside the address/size data window",
                    channel="W",
                    transaction_index=request["index"],
                    signal="WSTRB",
                    expected=f"subset of 0x{allowed_mask:X}",
                    actual=strobe,
                )

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
            validate_write_strobes(request, beats)
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
        if request.get("prot_attributes") is not None:
            tx["prot_attributes"] = request["prot_attributes"]
        if request.get("qos_attributes") is not None:
            tx["qos_attributes"] = request["qos_attributes"]
        if request.get("region_attributes") is not None:
            tx["region_attributes"] = request["region_attributes"]
        if request.get("user") is not None:
            tx["aruser"] = request["user"]
        r_users = [beat.get("user") for beat in beats]
        if any(value is not None for value in r_users):
            tx["ruser"] = r_users

        response_width = user_signal_widths.get("BUSER")
        ruser_width = user_signal_widths.get("RUSER")
        if (
            isinstance(response_width, int)
            and response_width > 0
            and isinstance(ruser_width, int)
            and ruser_width >= response_width
            and len(r_users) > 1
            and all(isinstance(value, int) for value in r_users)
        ):
            response_mask = (1 << response_width) - 1
            response_bits = [int(value) & response_mask for value in r_users]
            if len(set(response_bits)) > 1:
                add_advisory(
                    "ruser_response_bits_vary_across_read_beats",
                    "Arm recommends that User response bits keep the same value on every beat of a read response",
                    transaction_index=tx["index"],
                    signal="RUSER",
                    expected=f"lower {response_width} response bits stable across all read beats",
                    actual=response_bits,
                )

        transactions.append(tx)

    for sample, raw_sample in zip(samples, raw_samples):
        validate_user_sidebands(sample)
        validate_configured_id_signals(sample, raw_sample)
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
                "user": sample.get("WUSER"),
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
                    "response": label,
                    "response_code": code,
                    "aw_cycle": request["cycle"],
                    "w_cycles": [beat["cycle"] for beat in beats],
                    "response_cycle": sample["cycle"],
                    "exclusive": request["lock"],
                }
                if data_bus_bytes is not None:
                    tx["allowed_write_strobes"] = [
                        beat.get("allowed_strobe_mask") for beat in beats
                    ]
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
                if request.get("prot_attributes") is not None:
                    tx["prot_attributes"] = request["prot_attributes"]
                if request.get("qos_attributes") is not None:
                    tx["qos_attributes"] = request["qos_attributes"]
                if request.get("region_attributes") is not None:
                    tx["region_attributes"] = request["region_attributes"]
                if request.get("user") is not None:
                    tx["awuser"] = request["user"]
                w_users = [beat.get("user") for beat in beats]
                if any(value is not None for value in w_users):
                    tx["wuser"] = w_users
                if sample.get("BUSER") is not None:
                    tx["buser"] = sample["BUSER"]
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
                    "user": sample.get("RUSER"),
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
        "analysis_level": "normalized_cycle_trace_burst_exclusive_sideband_qos_region_semantics_address_width_id_widths_user_widths_user_guidance_write_strobes_master_defaults",
        "source": str(payload.get("source", "normalized-trace")),
        "data_width_bits": data_width_bits,
        "address_width_bits": address_width_bits,
        "id_widths": id_widths,
        "user_signal_widths": user_signal_widths,
        "absent_master_signals": sorted(master_defaults),
        "master_signal_defaults": {
            name: master_defaults[name] for name in sorted(master_defaults)
        },
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
            "advisories": len(advisories),
        },
        "transactions": transactions,
        "violations": violations,
        "advisories": advisories,
        "limitations": [
            "Core AXI4 burst, ID, ordering, handshake, response, and 4KB-boundary rules are modeled.",
            "Core AXI4 exclusive size/alignment, sequence timing, response-class, and observable read/write pairing checks are modeled.",
            "AXI4 address-sideband widths are checked for AxCACHE, AxPROT, AxQOS, and AxREGION; reserved AXI4 AxCACHE encodings are rejected, B/M plus direction-aware Allocate/Other-Allocate and memory-class evidence are decoded while legacy RA/WA bit fields remain available, AxPROT privilege/security/access semantics and protocol-defined AxQOS recommendation/default evidence are decoded, and AxREGION default/capacity/address-space evidence plus 4KB-space consistency are modeled.",
            "Optional AWUSER/ARUSER/WUSER/RUSER/BUSER values are preserved when observed and participate in channel payload-stability checks under backpressure.",
            "Optional id_widths metadata validates the AXI ID_W_WIDTH relationship across AWID/BID and ID_R_WIDTH across ARID/RID, including the protocol-defined width-zero signal-absence rule, without inferring interface widths from observed transaction values.",
            "Optional address_width_bits metadata validates AWADDR/ARADDR values against the AXI ADDR_WIDTH interface property in the 1..64-bit range; missing metadata remains unknown.",
            "AXI4 master-interface default values are applied only for signals explicitly declared in absent_master_signals; ordinary missing trace fields are never interpreted as proof that an interface signal is absent.",
            "When data_width_bits is known, AxSIZE is bounded by the data-channel width and WSTRB is checked against the legal byte lanes for narrow and unaligned writes.",
            "USER signal meaning remains implementation-defined; optional user_signal_widths interface metadata enables width/presence validation plus the AXI USER_REQ_WIDTH and RUSER composition relationships without assigning semantics to USER bits. Arm USER width maxima/granularity and multi-beat response-bit recommendations are reported as non-failing advisories, not protocol violations.",
            "Topology-dependent AxCACHE reachability, cross-master memory-attribute consistency, the AxREGION downstream-address-decode placement requirement, ACE coherency, AXI5 additions, and system-specific QoS scheduling policy are not modeled without explicit system topology metadata.",
            "VCD waveform extraction samples the configured AXI4 scope on ACLK edges before applying this normalized analyzer.",
        ],
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

    destination = Path(output) if output is not None else Path(".zddv/protocols/axi4/latest.json")
    if not destination.is_absolute():
        destination = project.root / destination
    destination = destination.resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    report["report_path"] = str(destination)
    return report
