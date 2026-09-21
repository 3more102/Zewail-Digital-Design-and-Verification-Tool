from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable

from zddv.waveform import parse_vcd_header


def _decode_vcd_value(raw: str) -> int | str:
    value = raw.strip().lower()
    if not value:
        return ""
    if all(bit in "01" for bit in value):
        return int(value, 2)
    return value


def _parse_value_change(line: str) -> tuple[str, int | str] | None:
    text = line.strip()
    if not text or text.startswith("$"):
        return None

    first = text[0]
    if first in "01xXzZ":
        code = text[1:].strip()
        if not code:
            return None
        return code, _decode_vcd_value(first)

    if first in "bB":
        parts = text[1:].split(None, 1)
        if len(parts) != 2:
            return None
        bits, code = parts
        code = code.strip()
        if not code:
            return None
        return code, _decode_vcd_value(bits)

    return None


def _clock_edge(previous: Any, current: Any, edge: str) -> bool:
    if edge == "rising":
        return previous == 0 and current == 1
    if edge == "falling":
        return previous == 1 and current == 0
    if edge == "both":
        return previous in (0, 1) and current in (0, 1) and previous != current
    raise ValueError(f"Unsupported clock edge: {edge}")


def sample_vcd_on_clock(
    path: str | Path,
    *,
    scope: str,
    clock: str,
    signals: Iterable[str],
    edge: str = "rising",
    required: Iterable[str] = (),
) -> dict[str, Any]:
    """Sample selected VCD signals after all changes at each selected clock edge.

    The sampler intentionally parses only the chosen signal IDs from the value-change
    stream. It does not build a full in-memory waveform.
    """
    source = Path(path).resolve()
    if not source.exists():
        raise FileNotFoundError(f"Waveform file not found: {source}")
    if source.suffix.lower() != ".vcd":
        raise RuntimeError("Clocked waveform extraction currently requires a VCD file.")

    header = parse_vcd_header(source)
    by_name = {
        item["name"]: item
        for item in header["signals"]
        if item["scope"] == scope
    }

    requested = []
    for name in [clock, *signals]:
        if name not in requested:
            requested.append(name)

    missing_required = [
        name for name in [clock, *required]
        if name not in by_name
    ]
    if missing_required:
        joined = ", ".join(missing_required)
        raise RuntimeError(
            f"VCD scope '{scope}' is missing required signal(s): {joined}"
        )

    selected = {
        name: by_name[name]
        for name in requested
        if name in by_name
    }
    clock_id = selected[clock]["id_code"]
    id_to_names: dict[str, list[str]] = {}
    for name, item in selected.items():
        id_to_names.setdefault(item["id_code"], []).append(name)

    current_values: dict[str, int | str] = {}
    pending: dict[str, int | str] = {}
    current_time = 0
    header_done = False
    samples: list[dict[str, Any]] = []

    def flush_time() -> None:
        nonlocal pending
        previous_clock = current_values.get(clock_id)
        current_values.update(pending)
        pending = {}
        current_clock = current_values.get(clock_id)
        if not _clock_edge(previous_clock, current_clock, edge):
            return

        sample: dict[str, Any] = {
            "cycle": len(samples),
            "time": current_time,
        }
        for name, item in selected.items():
            value = current_values.get(item["id_code"])
            if value is not None:
                sample[name] = value
        samples.append(sample)

    with source.open("r", encoding="utf-8", errors="replace") as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if not header_done:
                if "$enddefinitions" in line:
                    header_done = True
                continue

            if not line:
                continue
            if line.startswith("#"):
                try:
                    next_time = int(line[1:].strip())
                except ValueError as exc:
                    raise RuntimeError(
                        f"Invalid VCD timestamp line: {raw_line.rstrip()}"
                    ) from exc
                flush_time()
                current_time = next_time
                continue

            parsed = _parse_value_change(line)
            if parsed is None:
                continue
            code, value = parsed
            if code in id_to_names:
                pending[code] = value

    flush_time()

    return {
        "source": "vcd-clock-sample",
        "waveform": {
            "path": str(source),
            "timescale": header.get("timescale"),
            "scope": scope,
            "clock": clock,
            "edge": edge,
        },
        "available_signals": sorted(by_name),
        "sampled_signals": sorted(selected),
        "samples": samples,
    }
