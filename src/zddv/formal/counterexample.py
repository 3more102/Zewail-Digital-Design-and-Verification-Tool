from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from zddv.config import ProjectConfig


COUNTEREXAMPLE_SCHEMA = "zddv.formal.counterexample.v1"
_TRACE_KINDS = {"counterexample", "witness"}
_PROPERTY_KINDS = {"assert", "cover"}


def _normalize_signal(item: Any, *, index: int) -> dict[str, Any]:
    if isinstance(item, str):
        name = item.strip()
        width = None
        metadata: dict[str, Any] = {}
    elif isinstance(item, Mapping):
        name = str(item.get("name", "")).strip()
        raw_width = item.get("width")
        width = None if raw_width is None else int(raw_width)
        raw_metadata = item.get("metadata", {})
        if not isinstance(raw_metadata, Mapping):
            raise ValueError(f"signal {index} metadata must be an object")
        metadata = dict(raw_metadata)
    else:
        raise ValueError(f"signal {index} must be a string or object")

    if not name:
        raise ValueError(f"signal {index} name must not be empty")
    if width is not None and width < 1:
        raise ValueError(f"signal {name!r} width must be >= 1")

    return {
        "name": name,
        "width": width,
        "metadata": metadata,
    }


def _normalize_value(value: Any, *, signal: str, step: int) -> str:
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, str):
        if not value:
            raise ValueError(
                f"step {step} signal {signal!r} value must not be empty"
            )
        return value
    raise ValueError(
        f"step {step} signal {signal!r} value must be a string, integer, or boolean"
    )


def normalize_formal_counterexample(
    payload: Mapping[str, Any],
    *,
    source: str | None = None,
) -> dict[str, Any]:
    """Normalize a simulator-independent formal counterexample/witness trace."""

    if not isinstance(payload, Mapping):
        raise ValueError("formal counterexample payload must be a JSON object")

    property_name = str(payload.get("property", "")).strip()
    if not property_name:
        raise ValueError("formal counterexample property must not be empty")

    property_kind = str(payload.get("property_kind", "")).strip().lower()
    if property_kind not in _PROPERTY_KINDS:
        allowed = ", ".join(sorted(_PROPERTY_KINDS))
        raise ValueError(
            f"formal counterexample property_kind must be one of: {allowed}"
        )

    default_trace_kind = "counterexample" if property_kind == "assert" else "witness"
    trace_kind = str(payload.get("trace_kind", default_trace_kind)).strip().lower()
    if trace_kind not in _TRACE_KINDS:
        allowed = ", ".join(sorted(_TRACE_KINDS))
        raise ValueError(f"formal trace_kind must be one of: {allowed}")

    selected_source = source or str(payload.get("source", "normalized-json")).strip()
    if not selected_source:
        raise ValueError("formal counterexample source must not be empty")

    time_unit_raw = payload.get("time_unit")
    time_unit = None if time_unit_raw is None else str(time_unit_raw).strip()
    if time_unit == "":
        raise ValueError("formal counterexample time_unit must not be empty")

    raw_metadata = payload.get("metadata", {})
    if not isinstance(raw_metadata, Mapping):
        raise ValueError("formal counterexample metadata must be an object")

    raw_signals = payload.get("signals", [])
    if raw_signals is None:
        raw_signals = []
    if not isinstance(raw_signals, list):
        raise ValueError("formal counterexample signals must be a list")

    signals: list[dict[str, Any]] = []
    signal_names: set[str] = set()
    for index, item in enumerate(raw_signals):
        signal = _normalize_signal(item, index=index)
        name = signal["name"]
        if name in signal_names:
            raise ValueError(f"duplicate formal counterexample signal: {name}")
        signal_names.add(name)
        signals.append(signal)

    raw_steps = payload.get("steps")
    if not isinstance(raw_steps, list) or not raw_steps:
        raise ValueError("formal counterexample steps must be a non-empty list")

    steps: list[dict[str, Any]] = []
    inferred_signal_order: list[str] = []
    inferred_signal_names: set[str] = set()
    previous_step: int | None = None
    previous_time: float | int | None = None

    for position, item in enumerate(raw_steps):
        if not isinstance(item, Mapping):
            raise ValueError(f"formal counterexample step {position} must be an object")

        step_index_raw = item.get("step", item.get("index", position))
        step_index = int(step_index_raw)
        if step_index < 0:
            raise ValueError("formal counterexample step index must be >= 0")
        if previous_step is not None and step_index <= previous_step:
            raise ValueError("formal counterexample step indices must be strictly increasing")
        previous_step = step_index

        time_raw = item.get("time")
        time_value: float | int | None
        if time_raw is None:
            time_value = None
        elif isinstance(time_raw, bool) or not isinstance(time_raw, (int, float)):
            raise ValueError(f"formal counterexample step {step_index} time must be numeric")
        else:
            time_value = time_raw
            if time_value < 0:
                raise ValueError(f"formal counterexample step {step_index} time must be >= 0")
            if previous_time is not None and time_value < previous_time:
                raise ValueError("formal counterexample times must be non-decreasing")
            previous_time = time_value

        cycle_raw = item.get("cycle")
        cycle = None if cycle_raw is None else int(cycle_raw)
        if cycle is not None and cycle < 0:
            raise ValueError(f"formal counterexample step {step_index} cycle must be >= 0")

        raw_values = item.get("values")
        if not isinstance(raw_values, Mapping) or not raw_values:
            raise ValueError(
                f"formal counterexample step {step_index} values must be a non-empty object"
            )

        values: dict[str, str] = {}
        for raw_name, raw_value in raw_values.items():
            name = str(raw_name).strip()
            if not name:
                raise ValueError(
                    f"formal counterexample step {step_index} has an empty signal name"
                )
            if signal_names and name not in signal_names:
                raise ValueError(
                    f"formal counterexample step {step_index} references undeclared signal {name!r}"
                )
            if not signal_names and name not in inferred_signal_names:
                inferred_signal_names.add(name)
                inferred_signal_order.append(name)
            values[name] = _normalize_value(
                raw_value,
                signal=name,
                step=step_index,
            )

        raw_step_metadata = item.get("metadata", {})
        if not isinstance(raw_step_metadata, Mapping):
            raise ValueError(
                f"formal counterexample step {step_index} metadata must be an object"
            )

        steps.append(
            {
                "step": step_index,
                "time": time_value,
                "cycle": cycle,
                "values": values,
                "metadata": dict(raw_step_metadata),
            }
        )

    if not signals:
        signals = [
            {"name": name, "width": None, "metadata": {}}
            for name in inferred_signal_order
        ]
        signal_names = set(inferred_signal_order)

    complete_steps = sum(set(step["values"]) == signal_names for step in steps)
    times = [step["time"] for step in steps if step["time"] is not None]
    cycles = [step["cycle"] for step in steps if step["cycle"] is not None]

    return {
        "schema": COUNTEREXAMPLE_SCHEMA,
        "analysis": "formal_counterexample",
        "property": property_name,
        "property_kind": property_kind,
        "trace_kind": trace_kind,
        "source": selected_source,
        "time_unit": time_unit,
        "summary": {
            "signals": len(signals),
            "steps": len(steps),
            "complete_signal_steps": complete_steps,
            "partial_signal_steps": len(steps) - complete_steps,
            "first_time": times[0] if times else None,
            "last_time": times[-1] if times else None,
            "first_cycle": cycles[0] if cycles else None,
            "last_cycle": cycles[-1] if cycles else None,
        },
        "signals": signals,
        "steps": steps,
        "metadata": dict(raw_metadata),
        "limitations": [
            "This is a normalized evidence container, not a proof-engine parser.",
            "Signal values are preserved as textual logic tokens without inventing radix or signedness.",
            "Tool-specific engines must explicitly translate native counterexample or witness artifacts into this contract.",
        ],
    }


def ingest_formal_counterexample(
    project: ProjectConfig,
    path: str | Path,
    *,
    source: str | None = None,
    output: str | Path = ".zddv/formal/counterexamples/latest.json",
) -> dict[str, Any]:
    input_path = Path(path)
    if not input_path.is_absolute():
        input_path = project.root / input_path
    input_path = input_path.resolve()
    if not input_path.is_file():
        raise FileNotFoundError(input_path)

    raw_bytes = input_path.read_bytes()
    payload = json.loads(raw_bytes.decode("utf-8"))
    normalized = normalize_formal_counterexample(payload, source=source)

    destination = Path(output)
    if not destination.is_absolute():
        destination = project.root / destination
    destination = destination.resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)

    record = {
        **normalized,
        "project": project.name,
        "input_path": str(input_path),
        "input_sha256": hashlib.sha256(raw_bytes).hexdigest(),
        "normalized_path": str(destination),
    }
    destination.write_text(
        json.dumps(record, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return record
