from __future__ import annotations

from collections.abc import Iterable, Mapping
import hashlib
import json
from pathlib import Path
from typing import Any

from zddv.config import ProjectConfig
from zddv.crossprobe import build_crossprobe
from zddv.design_index import write_design_index
from zddv.connectivity import write_connectivity_index

from .counterexample import normalize_formal_counterexample


def _formal_signal_catalog(trace: Mapping[str, Any]) -> list[dict[str, Any]]:
    catalog: list[dict[str, Any]] = []
    for signal in trace["signals"]:
        path = str(signal["name"])
        metadata = signal.get("metadata", {})
        if not isinstance(metadata, Mapping):
            metadata = {}

        raw_reference = metadata.get("reference")
        name = (
            str(raw_reference).strip()
            if raw_reference is not None and str(raw_reference).strip()
            else path.rsplit(".", 1)[-1]
        )
        raw_scope = metadata.get("scope")
        scope = (
            str(raw_scope).strip()
            if raw_scope is not None
            else (path.rsplit(".", 1)[0] if "." in path else "")
        )

        catalog.append(
            {
                "path": path,
                "name": name,
                "scope": scope,
                "width": signal.get("width"),
                "formal_metadata": dict(metadata),
            }
        )
    return catalog


def build_formal_trace_crossprobe(
    project: ProjectConfig,
    trace_payload: Mapping[str, Any],
    signal_queries: Iterable[str],
    *,
    trace_path: str | Path | None = None,
    design_index: dict[str, Any] | None = None,
    connectivity_index: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Cross-probe selected normalized formal-trace signals into RTL/source evidence."""

    queries: list[str] = []
    seen: set[str] = set()
    for raw in signal_queries:
        query = str(raw).strip()
        if not query:
            raise ValueError("formal trace signal query must not be empty")
        if query in seen:
            continue
        seen.add(query)
        queries.append(query)
    if not queries:
        raise ValueError("at least one formal trace signal query is required")

    normalized = normalize_formal_counterexample(
        trace_payload,
        source=str(trace_payload.get("source", "normalized-json")).strip() or "normalized-json",
    )
    catalog = _formal_signal_catalog(normalized)
    if not catalog:
        raise RuntimeError("normalized formal trace contains no signals")

    artifact = None if trace_path is None else str(Path(trace_path).resolve())
    waveform_index = {
        "parse_status": "indexed",
        "run_id": None,
        "format": "FORMAL_TRACE",
        "artifact": artifact,
        "signals": catalog,
    }

    results: list[dict[str, Any]] = []
    for query in queries:
        crossprobe = build_crossprobe(
            project,
            query,
            waveform_index,
            design_index=design_index,
            connectivity_index=connectivity_index,
        )
        results.append(
            {
                "query": query,
                "status": crossprobe["status"],
                "signal_match": crossprobe["signal_match"],
                "trace_signal": crossprobe["waveform"]["signal"],
                "hierarchy": crossprobe.get("hierarchy"),
                "source": crossprobe.get("source"),
                "connectivity": crossprobe.get("connectivity"),
                "note": crossprobe.get("note"),
            }
        )

    matched = sum(item["status"] == "MATCHED" for item in results)
    partial = sum(item["status"] == "PARTIAL" for item in results)
    return {
        "schema_version": 1,
        "analysis": "formal_trace_crossprobe",
        "project": project.name,
        "trace": {
            "path": artifact,
            "property": normalized["property"],
            "property_kind": normalized["property_kind"],
            "trace_kind": normalized["trace_kind"],
            "source": normalized["source"],
            "time_unit": normalized.get("time_unit"),
            "signal_count": len(catalog),
            "step_count": len(normalized["steps"]),
        },
        "summary": {
            "queries": len(results),
            "matched": matched,
            "partial": partial,
        },
        "signals": results,
    }


def write_formal_trace_crossprobe_report(
    project: ProjectConfig,
    path: str | Path,
    signal_queries: Iterable[str],
    *,
    output: str | Path = ".zddv/debug/formal-trace-crossprobe.json",
) -> dict[str, Any]:
    input_path = Path(path)
    if not input_path.is_absolute():
        input_path = project.root / input_path
    input_path = input_path.resolve()
    if not input_path.is_file():
        raise FileNotFoundError(input_path)

    raw_bytes = input_path.read_bytes()
    payload = json.loads(raw_bytes.decode("utf-8"))
    if not isinstance(payload, Mapping):
        raise ValueError("normalized formal trace must be a JSON object")

    design = write_design_index(project)
    connectivity = write_connectivity_index(project)
    report = build_formal_trace_crossprobe(
        project,
        payload,
        signal_queries,
        trace_path=input_path,
        design_index=design,
        connectivity_index=connectivity,
    )
    report["trace"]["normalized_trace_sha256"] = hashlib.sha256(raw_bytes).hexdigest()
    report["trace"]["native_input_sha256"] = payload.get("input_sha256")
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
