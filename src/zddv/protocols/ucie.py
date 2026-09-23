from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from zddv.config import ProjectConfig


_PUBLIC_PROFILE = "public-flit-68-256"
_PUBLIC_FLIT_SIZES = {68, 256}
_ACK_NAK_VALUES = {"ACK", "NAK"}
_REFERENCES = [
    "https://www.uciexpress.org/specifications",
    "https://www.uciexpress.org/post/introduction-to-ucie-webinar-q-a-recap",
    "https://www.uciexpress.org/post/ucie-3-0-specification-redefining-chiplet-interconnects",
]
_PUBLIC_SPEC_VERSIONS = {"1.0", "1.1", "2.0", "3.0"}
_PUBLIC_MAX_DATA_RATE_GT_S = {
    "1.0": 32.0,
    "1.1": 32.0,
    "2.0": 32.0,
    "3.0": 64.0,
}


def _integer(value: Any) -> int | None:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        text = value.strip()
        try:
            return int(text, 0)
        except ValueError:
            return None
    return None


def _boolean(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "pass", "ok"}:
            return True
        if normalized in {"0", "false", "fail", "error"}:
            return False
    return None


def _normalize_negotiated_parameters(payload: dict[str, Any]) -> dict[str, Any]:
    raw = payload.get("negotiated")
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ValueError("UCIe 'negotiated' parameters must be an object")

    result: dict[str, Any] = {}
    for name in (
        "width",
        "lane_numbering",
        "frequency_gt_s",
        "data_rate_gt_s",
        "protocol",
        "spec_version",
    ):
        if name in raw:
            result[name] = raw[name]

    # Preserve the historical frequency_gt_s field while exposing the correctly
    # named data-rate alias for new traces. GT/s is a transfer rate, not a clock
    # frequency.
    if "data_rate_gt_s" not in result and "frequency_gt_s" in result:
        result["data_rate_gt_s"] = result["frequency_gt_s"]
    return result


def analyze_ucie_trace(payload: dict[str, Any]) -> dict[str, Any]:
    """Analyze a public-facts-based normalized UCIe FLIT trace.

    This foundation deliberately validates only a small, public trace contract:
    68-byte or 256-byte FLIT records, a modeled ACK/NAK header indication,
    explicit CRC health, direction, and monotonic trace ordering. CRC failures
    and NAK observations are link-health events, not by themselves protocol
    violations.

    It is not a UCIe conformance checker and does not encode specification-only
    retry, PHY, training-state, mapping, or CRC-polynomial rules.
    """
    if not isinstance(payload, dict):
        raise ValueError("UCIe trace must be a JSON object")

    profile = str(payload.get("profile", _PUBLIC_PROFILE)).strip().lower()
    if profile != _PUBLIC_PROFILE:
        raise ValueError(
            f"Unsupported UCIe trace profile: {profile!r}; "
            f"supported profile is {_PUBLIC_PROFILE!r}"
        )

    raw_flits = payload.get("flits")
    if not isinstance(raw_flits, list):
        raise ValueError("UCIe trace must contain a 'flits' list")

    negotiated = _normalize_negotiated_parameters(payload)
    flits: list[dict[str, Any]] = []
    violations: list[dict[str, Any]] = []
    last_cycle: int | None = None
    last_time: int | float | None = None

    def add_violation(
        code: str,
        index: int,
        message: str,
        *,
        field: str | None = None,
        expected: Any = None,
        actual: Any = None,
    ) -> None:
        entry: dict[str, Any] = {
            "code": code,
            "flit_index": index,
            "message": message,
        }
        if field is not None:
            entry["field"] = field
            entry["expected"] = expected
            entry["actual"] = actual
        violations.append(entry)

    def add_negotiation_violation(
        code: str,
        message: str,
        *,
        field: str,
        expected: Any,
        actual: Any,
    ) -> None:
        violations.append(
            {
                "code": code,
                "scope": "negotiated",
                "message": message,
                "field": field,
                "expected": expected,
                "actual": actual,
            }
        )

    spec_version = negotiated.get("spec_version")
    if spec_version is not None:
        canonical_version = str(spec_version).strip()
        if canonical_version in {"1", "2", "3"}:
            canonical_version = f"{canonical_version}.0"
        negotiated["spec_version"] = canonical_version
        if canonical_version not in _PUBLIC_SPEC_VERSIONS:
            add_negotiation_violation(
                "unsupported_public_spec_version",
                "spec_version is outside the public UCIe generations modeled by this analyzer",
                field="spec_version",
                expected="1.0/1.1/2.0/3.0",
                actual=spec_version,
            )

    rate_raw = negotiated.get("data_rate_gt_s")
    if rate_raw is not None:
        try:
            rate = float(rate_raw)
        except (TypeError, ValueError):
            rate = None
        if rate is None or rate <= 0:
            add_negotiation_violation(
                "invalid_negotiated_data_rate",
                "negotiated data rate must be a positive numeric GT/s value",
                field="data_rate_gt_s",
                expected="positive number",
                actual=rate_raw,
            )
        else:
            negotiated["data_rate_gt_s"] = rate
            canonical_version = negotiated.get("spec_version")
            ceiling = _PUBLIC_MAX_DATA_RATE_GT_S.get(canonical_version)
            if ceiling is not None:
                negotiated["public_generation_max_data_rate_gt_s"] = ceiling
                if rate > ceiling:
                    add_negotiation_violation(
                        "data_rate_exceeds_public_generation",
                        "negotiated data rate exceeds the public maximum for the declared UCIe generation",
                        field="data_rate_gt_s",
                        expected=f"<= {ceiling:g} GT/s for UCIe {canonical_version}",
                        actual=rate,
                    )

    for index, raw in enumerate(raw_flits):
        if not isinstance(raw, dict):
            raise ValueError(f"UCIe flit {index} must be an object")

        cycle = _integer(raw.get("cycle", index))
        if cycle is None or cycle < 0:
            add_violation(
                "invalid_cycle",
                index,
                "cycle must be a non-negative integer",
                field="cycle",
                expected="non-negative integer",
                actual=raw.get("cycle"),
            )
        elif last_cycle is not None and cycle < last_cycle:
            add_violation(
                "non_monotonic_cycle",
                index,
                "FLIT cycles must be monotonic in trace order",
                field="cycle",
                expected=f">= {last_cycle}",
                actual=cycle,
            )
        if cycle is not None and cycle >= 0:
            last_cycle = cycle

        timestamp = raw.get("time")
        if timestamp is not None:
            if not isinstance(timestamp, (int, float)) or isinstance(timestamp, bool):
                add_violation(
                    "invalid_time",
                    index,
                    "time must be numeric when provided",
                    field="time",
                    expected="number",
                    actual=timestamp,
                )
            elif last_time is not None and timestamp < last_time:
                add_violation(
                    "non_monotonic_time",
                    index,
                    "FLIT timestamps must be monotonic in trace order",
                    field="time",
                    expected=f">= {last_time}",
                    actual=timestamp,
                )
            else:
                last_time = timestamp

        direction = str(raw.get("direction", "")).strip().upper()
        if direction not in {"TX", "RX"}:
            add_violation(
                "invalid_direction",
                index,
                "direction must be TX or RX in the normalized trace",
                field="direction",
                expected="TX/RX",
                actual=raw.get("direction"),
            )

        size_bytes = _integer(raw.get("size_bytes"))
        if size_bytes not in _PUBLIC_FLIT_SIZES:
            add_violation(
                "unsupported_public_flit_size",
                index,
                "public FLIT profile accepts 68-byte or 256-byte FLIT records",
                field="size_bytes",
                expected="68 or 256",
                actual=raw.get("size_bytes"),
            )

        header_bytes = _integer(raw.get("header_bytes", 2))
        if header_bytes != 2:
            add_violation(
                "public_header_size_mismatch",
                index,
                "public FLIT profile models ACK/NAK inside a 2-byte header",
                field="header_bytes",
                expected=2,
                actual=raw.get("header_bytes"),
            )

        ack_nak = str(raw.get("ack_nak", "")).strip().upper()
        if ack_nak not in _ACK_NAK_VALUES:
            add_violation(
                "invalid_ack_nak",
                index,
                "ack_nak must be explicitly normalized as ACK or NAK",
                field="ack_nak",
                expected="ACK/NAK",
                actual=raw.get("ack_nak"),
            )

        crc_ok = _boolean(raw.get("crc_ok"))
        if crc_ok is None:
            add_violation(
                "missing_or_invalid_crc_status",
                index,
                "crc_ok must explicitly report monitor CRC health as a boolean",
                field="crc_ok",
                expected="boolean",
                actual=raw.get("crc_ok"),
            )

        payload_bytes = None
        if "payload_bytes" in raw:
            payload_bytes = _integer(raw.get("payload_bytes"))
            if (
                payload_bytes is None
                or payload_bytes < 0
                or (isinstance(size_bytes, int) and payload_bytes > size_bytes)
            ):
                add_violation(
                    "invalid_payload_size",
                    index,
                    "payload_bytes must be non-negative and no larger than the FLIT record",
                    field="payload_bytes",
                    expected=f"0..{size_bytes}" if isinstance(size_bytes, int) else "non-negative",
                    actual=raw.get("payload_bytes"),
                )

        normalized: dict[str, Any] = {
            "index": index,
            "cycle": cycle,
            "direction": direction,
            "size_bytes": size_bytes,
            "header_bytes": header_bytes,
            "ack_nak": ack_nak,
            "crc_ok": crc_ok,
        }
        if timestamp is not None:
            normalized["time"] = timestamp
        if payload_bytes is not None:
            normalized["payload_bytes"] = payload_bytes
        if "tag" in raw:
            normalized["tag"] = raw["tag"]
        flits.append(normalized)

    tx_flits = sum(1 for item in flits if item["direction"] == "TX")
    rx_flits = sum(1 for item in flits if item["direction"] == "RX")
    ack_flits = sum(1 for item in flits if item["ack_nak"] == "ACK")
    nak_flits = sum(1 for item in flits if item["ack_nak"] == "NAK")
    crc_errors = sum(1 for item in flits if item["crc_ok"] is False)
    clean_crc = sum(1 for item in flits if item["crc_ok"] is True)
    health = "DEGRADED" if (nak_flits or crc_errors) else "CLEAN"

    result = {
        "protocol": "UCIe",
        "analysis_level": "public_ucie_flit_trace_health_foundation",
        "profile": _PUBLIC_PROFILE,
        "source": str(payload.get("source", "normalized-trace")),
        "status": "PASS" if not violations else "FAIL",
        "health": health,
        "summary": {
            "flits": len(flits),
            "tx_flits": tx_flits,
            "rx_flits": rx_flits,
            "ack_flits": ack_flits,
            "nak_flits": nak_flits,
            "crc_ok_flits": clean_crc,
            "crc_error_flits": crc_errors,
            "violations": len(violations),
            "flit_sizes": {
                "68": sum(1 for item in flits if item["size_bytes"] == 68),
                "256": sum(1 for item in flits if item["size_bytes"] == 256),
            },
        },
        "negotiated": negotiated,
        "flits": flits,
        "violations": violations,
        "references": list(_REFERENCES),
        "limitations": [
            "This is a public-facts-based normalized trace and link-health foundation, not a UCIe conformance checker.",
            "Optional spec_version/data-rate checks enforce only public generation ceilings (32 GT/s through UCIe 2.0 and 64 GT/s in UCIe 3.0), not complete speed negotiation legality.",
            "NAK observations and CRC failures are reported as link-health events; retry correctness is not inferred.",
            "PHY electrical behavior, link-training state timing, lane repair, protocol mappings, exact CRC construction, and specification-only rules are not modeled.",
            "The public FLIT profile is intentionally limited to the 68-byte and 256-byte formats documented by the UCIe Consortium public Q&A.",
        ],
    }
    return result


def analyze_ucie_file(
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
    report = analyze_ucie_trace(payload)
    report["input_path"] = str(source)

    destination = (
        Path(output)
        if output is not None
        else Path(".zddv/protocols/ucie/latest.json")
    )
    if not destination.is_absolute():
        destination = project.root / destination
    destination = destination.resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    report["report_path"] = str(destination)
    return report
