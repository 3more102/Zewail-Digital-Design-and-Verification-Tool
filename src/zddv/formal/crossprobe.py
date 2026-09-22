from __future__ import annotations

from collections import Counter
import hashlib
import json
from pathlib import Path
from typing import Any, Iterable, Mapping

from zddv.config import ProjectConfig
from zddv.connectivity import build_connectivity_index, write_connectivity_index
from zddv.crossprobe import build_crossprobe
from zddv.design_index import build_design_index, write_design_index

from .counterexample import normalize_formal_counterexample


def _select_trace_signals(
    signals: list[dict[str, Any]],
    requested: Iterable[str],
) -> list[dict[str, Any]]:
    queries = [str(item).strip() for item in requested if str(item).strip()]
    if not queries:
        return list(signals)

    selected: list[dict[str, Any]] = []
    seen: set[str] = set()
    for query in queries:
        exact = [item for item in signals if item["name"] == query]
        if len(exact) == 1:
            matches = exact
        else:
            short = [
                item
                for item in signals
                if str(item["name"]).rsplit(".", 1)[-1] == query
            ]
            if len(short) > 1:
                choices = ", ".join(sorted(str(item["name"]) for item in short))
                raise RuntimeError(
                    f"Formal trace signal '{query}' is ambiguous; use a full path: {choices}"
                )
            matches = short

        if not matches:
            raise RuntimeError(f"Formal trace signal '{query}' was not found.")

        signal = matches[0]
        name = str(signal["name"])
        if name in seen:
            continue
        seen.add(name)
        selected.append(signal)

    return selected


def _formal_signal_as_waveform_signal(signal: Mapping[str, Any]) -> dict[str, Any]:
    path = str(signal["name"])
    metadata = signal.get("metadata", {})
    if not isinstance(metadata, Mapping):
        metadata = {}

    inferred_scope, separator, inferred_name = path.rpartition(".")
    scope = str(metadata.get("scope") or inferred_scope)
    name = str(metadata.get("reference") or (inferred_name if separator else path))

    return {
        "path": path,
        "scope": scope,
        "name": name,
        "width": signal.get("width"),
        "var_type": metadata.get("var_type"),
        "reference": metadata.get("reference"),
        "range": metadata.get("range"),
        "id_code": metadata.get("id_code"),
    }


def build_formal_trace_crossprobe(
    project: ProjectConfig,
    trace: Mapping[str, Any],
    *,
    signals: Iterable[str] = (),
    max_signals: int = 256,
    design_index: dict[str, Any] | None = None,
    connectivity_index: dict[str, Any] | None = None,
    trace_artifact: str | None = None,
) -> dict[str, Any]:
    """Cross-probe normalized formal trace signals into RTL/source evidence."""

    if max_signals < 1:
        raise ValueError("max_signals must be >= 1")

    normalized = normalize_formal_counterexample(trace)
    selected = _select_trace_signals(list(normalized["signals"]), signals)
    if len(selected) > max_signals:
        raise RuntimeError(
            f"Formal trace selection contains {len(selected)} signals, "
            f"exceeding max_signals={max_signals}; select signals explicitly "
            "or increase the evidence bound."
        )

    design = design_index or build_design_index(project)
    connectivity = connectivity_index or build_connectivity_index(project)

    waveform_signals = [_formal_signal_as_waveform_signal(item) for item in selected]
    waveform_index = {
        "parse_status": "indexed",
        "run_id": None,
        "format": "formal-trace",
        "artifact": trace_artifact,
        "signals": waveform_signals,
    }

    results: list[dict[str, Any]] = []
    for formal_signal in selected:
        query = str(formal_signal["name"])
        try:
            crossprobe = build_crossprobe(
                project,
                query,
                waveform_index,
                design_index=design,
                connectivity_index=connectivity,
            )
        except (RuntimeError, ValueError) as exc:
            results.append(
                {
                    "signal": formal_signal,
                    "status": "ERROR",
                    "signal_match": None,
                    "hierarchy": None,
                    "source": None,
                    "connectivity": None,
                    "note": str(exc),
                }
            )
            continue

        results.append(
            {
                "signal": formal_signal,
                "status": crossprobe["status"],
                "signal_match": crossprobe["signal_match"],
                "hierarchy": crossprobe["hierarchy"],
                "source": crossprobe["source"],
                "connectivity": crossprobe["connectivity"],
                "note": crossprobe["note"],
            }
        )

    statuses = Counter(item["status"] for item in results)
    return {
        "schema_version": 1,
        "analysis": "formal_trace_crossprobe",
        "project": project.name,
        "property": normalized["property"],
        "property_kind": normalized["property_kind"],
        "trace_kind": normalized["trace_kind"],
        "source": normalized["source"],
        "time_unit": normalized["time_unit"],
        "trace_artifact": trace_artifact,
        "summary": {
            "signals": len(results),
            "matched": statuses.get("MATCHED", 0),
            "partial": statuses.get("PARTIAL", 0),
            "errors": statuses.get("ERROR", 0),
        },
        "signals": results,
        "limitations": [
            "Cross-probing uses the existing source-level hierarchy/connectivity model.",
            "Generate/elaboration choices, macros, binds, interfaces/modports, and complex lvalues may require simulator AST enrichment.",
            "A source match is structural evidence only; it does not identify the semantic root cause of a formal failure.",
        ],
    }


def write_formal_trace_crossprobe_report(
    project: ProjectConfig,
    path: str | Path,
    *,
    signals: Iterable[str] = (),
    max_signals: int = 256,
    output: str | Path = ".zddv/formal/crossprobe/latest.json",
) -> dict[str, Any]:
    input_path = Path(path)
    if not input_path.is_absolute():
        input_path = project.root / input_path
    input_path = input_path.resolve()
    if not input_path.is_file():
        raise FileNotFoundError(input_path)

    raw = input_path.read_bytes()
    payload = json.loads(raw.decode("utf-8"))
    if not isinstance(payload, Mapping):
        raise ValueError("formal trace cross-probe input must be a JSON object")

    design = write_design_index(project)
    connectivity = write_connectivity_index(project)
    report = build_formal_trace_crossprobe(
        project,
        payload,
        signals=signals,
        max_signals=max_signals,
        design_index=design,
        connectivity_index=connectivity,
        trace_artifact=str(input_path),
    )

    report_path = Path(output)
    if not report_path.is_absolute():
        report_path = project.root / report_path
    report_path = report_path.resolve()
    report_path.parent.mkdir(parents=True, exist_ok=True)

    report.update(
        {
            "input_path": str(input_path),
            "input_sha256": hashlib.sha256(raw).hexdigest(),
            "design_index_path": design["path"],
            "connectivity_index_path": connectivity["path"],
            "report_path": str(report_path),
        }
    )
    report_path.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return report
