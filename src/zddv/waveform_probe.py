from __future__ import annotations

import json
from pathlib import Path
import re
from typing import Any

from zddv.config import ProjectConfig
from zddv.waveform import build_waveform_index, select_waveform_run


_SCALAR_CHANGE_RE = re.compile(r"^(?P<value>[01xXzZ])(?P<id>\S+)$")
_VECTOR_CHANGE_RE = re.compile(r"^[bB](?P<value>[01xXzZ]+)\s+(?P<id>\S+)$")
_REAL_CHANGE_RE = re.compile(r"^[rR](?P<value>[^\s]+)\s+(?P<id>\S+)$")


def _parse_vcd_change(line: str) -> tuple[str, str] | None:
    scalar = _SCALAR_CHANGE_RE.match(line)
    vector = None if scalar is not None else _VECTOR_CHANGE_RE.match(line)
    real = None if scalar is not None or vector is not None else _REAL_CHANGE_RE.match(line)
    match = scalar or vector or real
    if match is None:
        return None
    return match.group("id"), match.group("value")


def _decode_sample_value(value: str) -> int | str:
    normalized = value.strip().lower()
    if normalized and all(bit in "01" for bit in normalized):
        return int(normalized, 2)
    return normalized


def _clock_edge(previous: Any, current: Any, edge: str) -> bool:
    if edge == "rising":
        return previous == 0 and current == 1
    if edge == "falling":
        return previous == 1 and current == 0
    if edge == "both":
        return previous in (0, 1) and current in (0, 1) and previous != current
    raise ValueError(f"Unsupported clock edge: {edge}")


def _resolve_signal(
    query: str,
    signals: list[dict[str, Any]],
) -> dict[str, Any]:
    exact = [signal for signal in signals if signal["path"] == query]
    if len(exact) == 1:
        return exact[0]

    by_name = [signal for signal in signals if signal["name"] == query]
    if len(by_name) == 1:
        return by_name[0]
    if len(by_name) > 1:
        choices = ", ".join(sorted(str(signal["path"]) for signal in by_name))
        raise RuntimeError(
            f"Waveform signal '{query}' is ambiguous; use a full path: {choices}"
        )

    raise RuntimeError(f"Waveform signal '{query}' was not found.")


def _iter_vcd_body(path: Path):
    in_body = False
    saw_enddefinitions = False

    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for raw_line in handle:
            line = raw_line.strip()

            if not in_body:
                if "$enddefinitions" in line:
                    saw_enddefinitions = True
                    tail = line.split("$enddefinitions", 1)[1]
                    if "$end" in tail:
                        in_body = True
                    continue
                if saw_enddefinitions and "$end" in line:
                    in_body = True
                    continue
                continue

            if line:
                yield line

    if not in_body:
        raise RuntimeError(
            f"VCD header is incomplete: '$enddefinitions $end' was not found in {path}"
        )


def probe_vcd_signals(
    path: str | Path,
    signals: list[str],
    *,
    start_time: int | None = None,
    end_time: int | None = None,
    max_changes: int = 10_000,
) -> dict[str, Any]:
    """Stream selected VCD signal changes without loading the whole waveform."""

    if not signals:
        raise ValueError("At least one waveform signal must be requested.")
    if max_changes < 1:
        raise ValueError("max_changes must be >= 1")
    if start_time is not None and start_time < 0:
        raise ValueError("start_time must be >= 0")
    if end_time is not None and end_time < 0:
        raise ValueError("end_time must be >= 0")
    if start_time is not None and end_time is not None and start_time > end_time:
        raise ValueError("start_time must be <= end_time")

    source = Path(path).resolve()
    index = build_waveform_index(source)
    if index["format"] != "vcd" or index["parse_status"] != "indexed":
        raise RuntimeError("Waveform probing currently requires an indexed VCD artifact.")

    resolved: list[dict[str, Any]] = []
    seen_paths: set[str] = set()
    for query in signals:
        signal = _resolve_signal(query, index["signals"])
        signal_path = str(signal["path"])
        if signal_path in seen_paths:
            continue
        seen_paths.add(signal_path)
        resolved.append({"query": query, **signal})

    id_to_paths: dict[str, list[str]] = {}
    result_by_path: dict[str, dict[str, Any]] = {}
    for signal in resolved:
        signal_path = str(signal["path"])
        id_to_paths.setdefault(str(signal["id_code"]), []).append(signal_path)
        result_by_path[signal_path] = {
            "query": signal["query"],
            "path": signal_path,
            "scope": signal["scope"],
            "name": signal["name"],
            "width": signal["width"],
            "range": signal["range"],
            "var_type": signal["var_type"],
            "id_code": signal["id_code"],
            "changes": [],
            "truncated": False,
        }

    current_time = 0
    for line in _iter_vcd_body(source):
        if line.startswith("#"):
            try:
                current_time = int(line[1:].strip())
            except ValueError:
                continue
            if end_time is not None and current_time > end_time:
                break
            continue

        change = _parse_vcd_change(line)
        if change is None:
            continue

        id_code, value = change
        target_paths = id_to_paths.get(id_code)
        if not target_paths:
            continue
        if start_time is not None and current_time < start_time:
            continue
        if end_time is not None and current_time > end_time:
            continue

        for signal_path in target_paths:
            entry = result_by_path[signal_path]
            if len(entry["changes"]) >= max_changes:
                entry["truncated"] = True
                continue
            entry["changes"].append({"time": current_time, "value": value})

    result_signals = [result_by_path[str(signal["path"])] for signal in resolved]
    return {
        "schema_version": 1,
        "format": "vcd",
        "artifact": index["artifact"],
        "timescale": index.get("timescale"),
        "window": {
            "start_time": start_time,
            "end_time": end_time,
            "max_changes_per_signal": max_changes,
        },
        "signals": result_signals,
        "summary": {
            "signals": len(result_signals),
            "total_changes": sum(len(signal["changes"]) for signal in result_signals),
            "truncated_signals": sum(
                1 for signal in result_signals if signal["truncated"]
            ),
        },
    }


def sample_vcd_on_clock(
    path: str | Path,
    *,
    clock: str,
    signals: list[str],
    edge: str = "rising",
) -> dict[str, Any]:
    """Stream selected VCD signals and sample their settled values on clock edges."""

    if edge not in {"rising", "falling", "both"}:
        raise ValueError("edge must be one of: rising, falling, both")

    source = Path(path).resolve()
    index = build_waveform_index(source)
    if index["format"] != "vcd" or index["parse_status"] != "indexed":
        raise RuntimeError("Clock-edge sampling currently requires an indexed VCD artifact.")

    requested = [clock, *signals]
    resolved: list[dict[str, Any]] = []
    seen_paths: set[str] = set()
    for query in requested:
        signal = _resolve_signal(query, index["signals"])
        signal_path = str(signal["path"])
        if signal_path in seen_paths:
            continue
        seen_paths.add(signal_path)
        resolved.append({"query": query, **signal})

    clock_signal = _resolve_signal(clock, index["signals"])
    clock_path = str(clock_signal["path"])
    clock_id = str(clock_signal["id_code"])

    id_to_paths: dict[str, list[str]] = {}
    path_to_id: dict[str, str] = {}
    for signal in resolved:
        signal_path = str(signal["path"])
        id_code = str(signal["id_code"])
        id_to_paths.setdefault(id_code, []).append(signal_path)
        path_to_id[signal_path] = id_code

    current_values: dict[str, int | str] = {}
    pending: dict[str, int | str] = {}
    current_time = 0
    samples: list[dict[str, Any]] = []

    def flush_time() -> None:
        nonlocal pending
        previous_clock = current_values.get(clock_id)
        current_values.update(pending)
        pending = {}
        current_clock = current_values.get(clock_id)
        if not _clock_edge(previous_clock, current_clock, edge):
            return

        values: dict[str, int | str] = {}
        for signal_path, id_code in path_to_id.items():
            value = current_values.get(id_code)
            if value is not None:
                values[signal_path] = value
        samples.append(
            {
                "cycle": len(samples),
                "time": current_time,
                "values": values,
            }
        )

    for line in _iter_vcd_body(source):
        if line.startswith("#"):
            try:
                next_time = int(line[1:].strip())
            except ValueError:
                continue
            flush_time()
            current_time = next_time
            continue

        change = _parse_vcd_change(line)
        if change is None:
            continue
        id_code, raw_value = change
        if id_code in id_to_paths:
            pending[id_code] = _decode_sample_value(raw_value)

    flush_time()

    return {
        "schema_version": 1,
        "format": "vcd",
        "artifact": index["artifact"],
        "timescale": index.get("timescale"),
        "clock": {
            "query": clock,
            "path": clock_path,
            "edge": edge,
        },
        "signals": [
            {
                "query": signal["query"],
                "path": signal["path"],
                "scope": signal["scope"],
                "name": signal["name"],
                "width": signal["width"],
                "range": signal["range"],
                "var_type": signal["var_type"],
                "id_code": signal["id_code"],
            }
            for signal in resolved
        ],
        "samples": samples,
        "summary": {
            "signals": len(resolved),
            "samples": len(samples),
        },
    }


def write_waveform_probe(
    project: ProjectConfig,
    signals: list[str],
    *,
    run_id: str | None = None,
    input_path: str | Path | None = None,
    start_time: int | None = None,
    end_time: int | None = None,
    max_changes: int = 10_000,
    output: str | Path | None = None,
) -> dict[str, Any]:
    if run_id is not None and input_path is not None:
        raise ValueError("run_id and input_path are mutually exclusive")

    selected_run: dict[str, Any] | None = None
    if input_path is None:
        selected_run = select_waveform_run(project, run_id=run_id)
        waveform_path = Path(str(selected_run["waveform_path"])).resolve()
        effective_run_id = str(selected_run["run_id"])
    else:
        waveform_path = Path(input_path)
        if not waveform_path.is_absolute():
            waveform_path = project.root / waveform_path
        waveform_path = waveform_path.resolve()
        effective_run_id = None

    result = probe_vcd_signals(
        waveform_path,
        signals,
        start_time=start_time,
        end_time=end_time,
        max_changes=max_changes,
    )
    result["project"] = project.name
    result["run_id"] = effective_run_id
    try:
        result["artifact"]["project_path"] = str(
            waveform_path.relative_to(project.root.resolve())
        )
    except ValueError:
        result["artifact"]["project_path"] = str(waveform_path)

    out_dir = (project.root / ".zddv" / "waveforms" / "probes").resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    if output is None:
        label = effective_run_id or waveform_path.stem
        destination = out_dir / f"{label}.json"
    else:
        destination = Path(output)
        if not destination.is_absolute():
            destination = project.root / destination
        destination = destination.resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)

    destination.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")

    latest_path = out_dir / "latest.json"
    if output is None:
        latest_path.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")

    return {
        **result,
        "path": str(destination),
        "latest_path": str(latest_path) if output is None else None,
        "selected_run": selected_run,
    }
