from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from zddv.config import ProjectConfig
from zddv.connectivity import write_connectivity_index
from zddv.crossprobe import build_crossprobe
from zddv.design_index import write_design_index


def _formal_trace_waveform_index(
    project: ProjectConfig,
    trace: Mapping[str, Any],
) -> dict[str, Any]:
    """Adapt a normalized formal trace to the existing cross-probe signal contract."""

    analysis = str(trace.get("analysis", "")).strip()
    if analysis != "formal_counterexample":
        raise ValueError(
            "Formal trace cross-probing requires a normalized formal counterexample/witness"
        )

    raw_signals = trace.get("signals")
    if not isinstance(raw_signals, list) or not raw_signals:
        raise ValueError("Normalized formal trace has no signal catalog")

    signals: list[dict[str, Any]] = []
    for index, raw_signal in enumerate(raw_signals):
        if not isinstance(raw_signal, Mapping):
            raise ValueError(f"formal trace signal {index} must be an object")

        path = str(raw_signal.get("name", "")).strip()
        if not path:
            raise ValueError(f"formal trace signal {index} has no name")

        raw_metadata = raw_signal.get("metadata", {})
        metadata = dict(raw_metadata) if isinstance(raw_metadata, Mapping) else {}
        scope = str(metadata.get("scope") or path.rpartition(".")[0]).strip()
        reference = str(
            metadata.get("reference") or path.rsplit(".", 1)[-1]
        ).strip()
        if not reference:
            reference = path.rsplit(".", 1)[-1]

        signals.append(
            {
                "path": path,
                "name": reference,
                "scope": scope,
                "width": raw_signal.get("width"),
                "formal_metadata": metadata,
            }
        )

    raw_metadata = trace.get("metadata", {})
    trace_metadata = (
        dict(raw_metadata) if isinstance(raw_metadata, Mapping) else {}
    )
    artifact = (
        trace.get("input_path")
        or trace_metadata.get("waveform_path")
        or trace.get("normalized_path")
    )

    return {
        "parse_status": "indexed",
        "project": project.name,
        "run_id": None,
        "format": "normalized-formal-vcd",
        "artifact": None if artifact is None else str(artifact),
        "signals": signals,
    }


def build_formal_trace_crossprobe(
    project: ProjectConfig,
    signal_query: str,
    trace: Mapping[str, Any],
    *,
    design_index: dict[str, Any] | None = None,
    connectivity_index: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Map one normalized formal-trace signal back to source RTL evidence."""

    waveform_index = _formal_trace_waveform_index(project, trace)
    report = build_crossprobe(
        project,
        signal_query,
        waveform_index,
        design_index=design_index,
        connectivity_index=connectivity_index,
    )
    report["analysis"] = "formal_trace_crossprobe"
    report["formal_trace"] = {
        "schema": trace.get("schema"),
        "property": trace.get("property"),
        "property_kind": trace.get("property_kind"),
        "trace_kind": trace.get("trace_kind"),
        "source": trace.get("source"),
        "time_unit": trace.get("time_unit"),
        "summary": trace.get("summary"),
        "input_path": trace.get("input_path"),
        "input_sha256": trace.get("input_sha256"),
        "normalized_path": trace.get("normalized_path"),
    }
    return report


def write_formal_trace_crossprobe_report(
    project: ProjectConfig,
    trace_path: str | Path,
    signal_query: str,
    *,
    output: str | Path = ".zddv/formal/crossprobe.json",
) -> dict[str, Any]:
    """Load a normalized formal trace and write one source cross-probe report."""

    source = Path(trace_path)
    if not source.is_absolute():
        source = project.root / source
    source = source.resolve()
    if not source.is_file():
        raise FileNotFoundError(source)

    with source.open("r", encoding="utf-8") as handle:
        trace = json.load(handle)
    if not isinstance(trace, Mapping):
        raise ValueError("Normalized formal trace JSON must be an object")

    design = write_design_index(project)
    connectivity = write_connectivity_index(project)
    report = build_formal_trace_crossprobe(
        project,
        signal_query,
        trace,
        design_index=design,
        connectivity_index=connectivity,
    )
    report["formal_trace_path"] = str(source)
    report["design_index_path"] = design["path"]
    report["connectivity_index_path"] = connectivity["path"]

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
