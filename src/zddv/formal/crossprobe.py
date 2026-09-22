from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable, Mapping

from zddv.config import ProjectConfig
from zddv.connectivity import write_connectivity_index
from zddv.crossprobe import build_crossprobe
from zddv.design_index import write_design_index
from zddv.formal.counterexample import COUNTEREXAMPLE_SCHEMA


def _require_trace_record(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise ValueError("formal trace payload must be an object")

    record = dict(payload)
    if record.get("schema") != COUNTEREXAMPLE_SCHEMA:
        raise ValueError(
            f"formal trace schema must be {COUNTEREXAMPLE_SCHEMA!r}"
        )
    if record.get("analysis") != "formal_counterexample":
        raise ValueError("formal trace analysis must be 'formal_counterexample'")

    signals = record.get("signals")
    if not isinstance(signals, list) or not signals:
        raise ValueError("formal trace must contain at least one signal")
    return record


def _waveform_signal(item: Any, *, index: int) -> dict[str, Any]:
    if not isinstance(item, Mapping):
        raise ValueError(f"formal trace signal {index} must be an object")

    path = str(item.get("name", "")).strip()
    if not path:
        raise ValueError(f"formal trace signal {index} name must not be empty")

    metadata = item.get("metadata", {})
    if not isinstance(metadata, Mapping):
        raise ValueError(f"formal trace signal {path!r} metadata must be an object")

    scope_raw = metadata.get("scope")
    if scope_raw is None:
        scope = path.rsplit(".", 1)[0] if "." in path else ""
    else:
        scope = str(scope_raw).strip()

    reference_raw = metadata.get("reference")
    if reference_raw is None:
        name = path.rsplit(".", 1)[-1]
    else:
        name = str(reference_raw).strip() or path.rsplit(".", 1)[-1]

    width_raw = item.get("width")
    width = None if width_raw is None else int(width_raw)

    return {
        "path": path,
        "scope": scope,
        "name": name,
        "reference": name,
        "range": metadata.get("range"),
        "var_type": metadata.get("var_type"),
        "width": width,
        "id_code": metadata.get("id_code"),
    }


def formal_trace_waveform_index(record: Mapping[str, Any]) -> dict[str, Any]:
    """Adapt a normalized formal trace's signal catalog to the debug crossprobe API."""

    trace = _require_trace_record(record)
    signals = [
        _waveform_signal(item, index=index)
        for index, item in enumerate(trace["signals"])
    ]

    artifact_path = trace.get("input_path") or trace.get("metadata", {}).get("waveform_path")
    artifact = {
        "path": artifact_path,
        "sha256": trace.get("input_sha256"),
    }

    return {
        "schema_version": 1,
        "project": trace.get("project"),
        "run_id": None,
        "format": "formal-normalized",
        "parse_status": "indexed",
        "artifact": artifact,
        "date": None,
        "version": None,
        "timescale": trace.get("time_unit"),
        "scopes": [],
        "signals": signals,
        "summary": {
            "scopes": len({signal["scope"] for signal in signals if signal["scope"]}),
            "signals": len(signals),
            "unique_value_ids": len(
                {
                    signal["id_code"]
                    for signal in signals
                    if signal["id_code"] is not None
                }
            ),
            "declared_bits": sum(
                int(signal["width"])
                for signal in signals
                if signal["width"] is not None
            ),
        },
    }


def _selected_queries(
    waveform_index: Mapping[str, Any],
    requested: Iterable[str],
    *,
    max_signals: int,
) -> list[str]:
    if max_signals < 1:
        raise ValueError("max_signals must be >= 1")

    queries = [str(item).strip() for item in requested if str(item).strip()]
    if queries:
        if len(queries) > max_signals:
            raise RuntimeError(
                f"formal crossprobe requested {len(queries)} signals; "
                f"max_signals={max_signals}"
            )
        return queries

    signals = list(waveform_index.get("signals", []))
    if len(signals) > max_signals:
        raise RuntimeError(
            f"formal trace contains {len(signals)} signals; "
            f"use --signal or increase --max-signals above {max_signals}"
        )
    return [str(signal["path"]) for signal in signals]


def crossprobe_formal_trace(
    project: ProjectConfig,
    record: Mapping[str, Any],
    *,
    signals: Iterable[str] = (),
    max_signals: int = 256,
) -> dict[str, Any]:
    """Cross-probe normalized formal-trace signals into RTL hierarchy/source evidence."""

    trace = _require_trace_record(record)
    waveform = formal_trace_waveform_index(trace)
    queries = _selected_queries(waveform, signals, max_signals=max_signals)

    design = write_design_index(project)
    connectivity = write_connectivity_index(project)

    results = [
        build_crossprobe(
            project,
            query,
            waveform,
            design_index=design,
            connectivity_index=connectivity,
        )
        for query in queries
    ]

    matched = sum(item["status"] == "MATCHED" for item in results)
    partial = sum(item["status"] == "PARTIAL" for item in results)

    return {
        "schema_version": 1,
        "analysis": "formal_trace_crossprobe",
        "project": project.name,
        "property": trace["property"],
        "property_kind": trace["property_kind"],
        "trace_kind": trace["trace_kind"],
        "source": trace["source"],
        "input_path": trace.get("input_path"),
        "input_sha256": trace.get("input_sha256"),
        "summary": {
            "signals": len(results),
            "matched": matched,
            "partial": partial,
        },
        "results": results,
        "design_index_path": design["path"],
        "connectivity_index_path": connectivity["path"],
    }


def write_formal_trace_crossprobe(
    project: ProjectConfig,
    path: str | Path,
    *,
    signals: Iterable[str] = (),
    max_signals: int = 256,
    output: str | Path = ".zddv/formal/crossprobe.json",
) -> dict[str, Any]:
    input_path = Path(path)
    if not input_path.is_absolute():
        input_path = project.root / input_path
    input_path = input_path.resolve()
    if not input_path.is_file():
        raise FileNotFoundError(input_path)

    payload = json.loads(input_path.read_text(encoding="utf-8"))
    report = crossprobe_formal_trace(
        project,
        payload,
        signals=signals,
        max_signals=max_signals,
    )
    report["trace_record_path"] = str(input_path)

    destination = Path(output)
    if not destination.is_absolute():
        destination = project.root / destination
    destination = destination.resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return {**report, "report_path": str(destination)}
