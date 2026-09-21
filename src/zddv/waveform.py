from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

from zddv.config import ProjectConfig
from zddv.storage import get_run_record, latest_waveform_run


_SCALAR_PREFIXES = {"0", "1", "x", "X", "z", "Z"}
_VECTOR_PREFIXES = {"b", "B", "r", "R", "s", "S"}


def _directive(lines: list[str], start: int) -> tuple[str, int]:
    parts = [lines[start].strip()]
    index = start + 1
    while "$end" not in parts[-1] and index < len(lines):
        parts.append(lines[index].strip())
        index += 1
    return " ".join(part for part in parts if part), index


def parse_vcd_index(path: str | Path) -> dict[str, Any]:
    """Index VCD scopes/signals and activity without loading waveform values."""
    source = Path(path)
    lines = source.read_text(encoding="utf-8", errors="replace").splitlines()

    scopes: list[str] = []
    seen_scopes: set[str] = set()
    signals: list[dict[str, Any]] = []
    code_to_signals: dict[str, list[int]] = {}
    timescale: str | None = None

    index = 0
    data_start = len(lines)
    while index < len(lines):
        raw = lines[index].strip()
        if not raw:
            index += 1
            continue
        if not raw.startswith("$"):
            index += 1
            continue

        directive, next_index = _directive(lines, index)
        tokens = directive.split()
        keyword = tokens[0] if tokens else ""

        if keyword == "$timescale" and "$end" in tokens:
            end = tokens.index("$end")
            timescale = " ".join(tokens[1:end]).strip() or None
        elif keyword == "$scope" and len(tokens) >= 4:
            scope_name = tokens[2]
            scopes.append(scope_name)
            seen_scopes.add(".".join(scopes))
        elif keyword == "$upscope":
            if scopes:
                scopes.pop()
        elif keyword == "$var" and len(tokens) >= 6:
            try:
                width = int(tokens[2])
            except ValueError:
                width = 0
            id_code = tokens[3]
            reference = tokens[4]
            range_text = " ".join(tokens[5 : tokens.index("$end")]).strip()
            scope = ".".join(scopes)
            full_name = f"{scope}.{reference}" if scope else reference
            signal = {
                "scope": scope,
                "reference": reference,
                "full_name": full_name,
                "var_type": tokens[1],
                "width": width,
                "id_code": id_code,
                "range": range_text or None,
                "changes": 0,
                "first_activity": None,
                "last_activity": None,
            }
            signal_index = len(signals)
            signals.append(signal)
            code_to_signals.setdefault(id_code, []).append(signal_index)
        elif keyword == "$enddefinitions":
            data_start = next_index
            break

        index = next_index

    current_time = 0
    first_timestamp: int | None = None
    last_timestamp = 0
    value_changes = 0
    index = data_start

    while index < len(lines):
        raw = lines[index].strip()
        if not raw:
            index += 1
            continue

        if raw.startswith("#"):
            try:
                current_time = int(raw[1:].strip())
            except ValueError:
                index += 1
                continue
            if first_timestamp is None:
                first_timestamp = current_time
            last_timestamp = max(last_timestamp, current_time)
            index += 1
            continue

        if raw.startswith("$"):
            _, index = _directive(lines, index)
            continue

        id_code: str | None = None
        if raw[0] in _SCALAR_PREFIXES:
            id_code = raw[1:].strip()
        elif raw[0] in _VECTOR_PREFIXES:
            parts = raw[1:].strip().split(None, 1)
            if len(parts) == 2:
                id_code = parts[1].strip()

        if id_code and id_code in code_to_signals:
            value_changes += 1
            for signal_index in code_to_signals[id_code]:
                signal = signals[signal_index]
                signal["changes"] += 1
                if signal["first_activity"] is None:
                    signal["first_activity"] = current_time
                signal["last_activity"] = current_time

        index += 1

    signals.sort(key=lambda item: item["full_name"])
    start_time = first_timestamp if first_timestamp is not None else 0
    return {
        "schema_version": 1,
        "format": "vcd",
        "waveform_path": str(source.resolve()),
        "timescale": timescale,
        "start_time": start_time,
        "end_time": last_timestamp,
        "duration_ticks": max(0, last_timestamp - start_time),
        "signals": signals,
        "summary": {
            "signals": len(signals),
            "scopes": len(seen_scopes),
            "value_changes": value_changes,
        },
    }


def index_run_waveform(
    project: ProjectConfig,
    *,
    run_id: str | None = None,
) -> dict[str, Any]:
    run = get_run_record(project, run_id) if run_id else latest_waveform_run(project)
    if run is None:
        if run_id:
            raise RuntimeError(f"Run not found: {run_id}")
        raise RuntimeError("No recorded run with a waveform was found.")

    waveform_value = run.get("waveform_path")
    if not waveform_value:
        raise RuntimeError(f"Run {run['run_id']} has no waveform artifact.")

    waveform_path = Path(waveform_value)
    if not waveform_path.exists():
        raise FileNotFoundError(waveform_path)
    if waveform_path.suffix.lower() != ".vcd":
        raise RuntimeError(
            "Waveform indexing currently supports VCD files only; "
            f"got {waveform_path.suffix or '(no extension)'}."
        )

    result = parse_vcd_index(waveform_path)
    result.update(
        {
            "run_id": run["run_id"],
            "indexed_at": datetime.now(timezone.utc).isoformat(),
        }
    )

    output = Path(run["run_dir"]) / "waveform.index.json"
    output.write_text(json.dumps(result, indent=2), encoding="utf-8")
    result["index_path"] = str(output)
    return result
