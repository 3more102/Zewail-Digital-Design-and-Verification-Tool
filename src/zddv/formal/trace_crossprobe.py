from __future__ import annotations

from collections import Counter
import json
from pathlib import Path
from typing import Any, Iterable

from zddv.config import ProjectConfig
from zddv.connectivity import write_connectivity_index
from zddv.crossprobe import build_crossprobe
from zddv.design_index import write_design_index
from zddv.formal.counterexample import normalize_formal_counterexample


def _resolve_input_path(project: ProjectConfig, path: str | Path) -> Path:
    source = Path(path)
    if not source.is_absolute():
        source = project.root / source
    source = source.resolve()
    if not source.is_file():
        raise FileNotFoundError(source)
    return source


def _load_normalized_trace(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ValueError("Formal trace JSON must be an object")
    return normalize_formal_counterexample(payload)


def _signal_short_name(signal: dict[str, Any]) -> str:
    metadata = signal.get("metadata", {})
    if isinstance(metadata, dict):
        reference = str(metadata.get("reference", "")).strip()
        if reference:
            return reference
    name = str(signal.get("name", "")).strip()
    return name.rsplit(".", 1)[-1]


def _select_trace_signals(
    signals: list[dict[str, Any]],
    selectors: Iterable[str],
    *,
    max_signals: int,
) -> list[dict[str, Any]]:
    if max_signals < 1:
        raise ValueError("max_signals must be >= 1")

    queries = [str(item).strip() for item in selectors if str(item).strip()]
    if not queries:
        if len(signals) > max_signals:
            raise RuntimeError(
                f"Formal trace has {len(signals)} signals, exceeding "
                f"max_signals={max_signals}; select signals explicitly or raise the limit."
            )
        return list(signals)

    selected: list[dict[str, Any]] = []
    selected_names: set[str] = set()
    for query in queries:
        exact = [item for item in signals if str(item.get("name", "")) == query]
        if len(exact) == 1:
            matches = exact
        else:
            short = [item for item in signals if _signal_short_name(item) == query]
            if len(short) > 1:
                choices = ", ".join(
                    sorted(str(item.get("name", "")) for item in short[:8])
                )
                extra = "" if len(short) <= 8 else f", ... (+{len(short) - 8} more)"
                raise RuntimeError(
                    f"Formal trace signal '{query}' is ambiguous: {choices}{extra}. "
                    "Use a full hierarchical signal path."
                )
            matches = short

        if not matches:
            raise RuntimeError(f"Formal trace signal '{query}' was not found.")

        signal = matches[0]
        name = str(signal["name"])
        if name in selected_names:
            continue
        selected_names.add(name)
        selected.append(signal)

    if len(selected) > max_signals:
        raise RuntimeError(
            f"Selected {len(selected)} formal signals, exceeding max_signals={max_signals}."
        )
    return selected


def _as_waveform_signal(signal: dict[str, Any]) -> dict[str, Any]:
    path = str(signal["name"]).strip()
    metadata = signal.get("metadata", {})
    if not isinstance(metadata, dict):
        metadata = {}

    reference = str(metadata.get("reference", "")).strip()
    name = reference or path.rsplit(".", 1)[-1]

    scope = str(metadata.get("scope", "")).strip()
    if not scope and "." in path:
        scope = path.rsplit(".", 1)[0]

    return {
        "path": path,
        "scope": scope,
        "name": name,
        "width": signal.get("width"),
        "range": metadata.get("range"),
        "var_type": metadata.get("var_type"),
        "id_code": metadata.get("id_code"),
    }


def build_formal_trace_crossprobe(
    project: ProjectConfig,
    trace: dict[str, Any],
    *,
    selectors: Iterable[str] = (),
    max_signals: int = 256,
    artifact: str | None = None,
    design_index: dict[str, Any] | None = None,
    connectivity_index: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Cross-probe normalized formal trace signals into source hierarchy/connectivity."""

    signals = _select_trace_signals(
        list(trace.get("signals", [])),
        selectors,
        max_signals=max_signals,
    )

    waveform_index = {
        "parse_status": "indexed",
        "run_id": None,
        "format": "FORMAL_TRACE",
        "artifact": artifact,
        "signals": [_as_waveform_signal(signal) for signal in signals],
    }

    rows: list[dict[str, Any]] = []
    for signal in signals:
        name = str(signal["name"])
        try:
            crossprobe = build_crossprobe(
                project,
                name,
                waveform_index,
                design_index=design_index,
                connectivity_index=connectivity_index,
            )
        except (RuntimeError, ValueError) as exc:
            rows.append(
                {
                    "signal": name,
                    "status": "UNRESOLVED",
                    "error": str(exc),
                    "crossprobe": None,
                }
            )
            continue

        rows.append(
            {
                "signal": name,
                "status": crossprobe["status"],
                "error": None,
                "crossprobe": crossprobe,
            }
        )

    statuses = Counter(row["status"] for row in rows)
    return {
        "analysis": "formal_trace_crossprobe",
        "property": trace["property"],
        "property_kind": trace["property_kind"],
        "trace_kind": trace["trace_kind"],
        "source": trace["source"],
        "trace_schema": trace["schema"],
        "artifact": artifact,
        "summary": {
            "selected_signals": len(rows),
            "matched": statuses.get("MATCHED", 0),
            "partial": statuses.get("PARTIAL", 0),
            "unresolved": statuses.get("UNRESOLVED", 0),
        },
        "signals": rows,
        "limitations": [
            "Cross-probing uses ZDDV's source-level hierarchy and structural connectivity model.",
            "A MATCHED result identifies a source declaration; it does not prove semantic equivalence after elaboration or synthesis.",
            "Generated hierarchy, aliases, optimized signals, and vendor-native debug mappings may require future elaboration-aware adapters.",
        ],
    }


def write_formal_trace_crossprobe_report(
    project: ProjectConfig,
    path: str | Path,
    *,
    selectors: Iterable[str] = (),
    max_signals: int = 256,
    output: str | Path = ".zddv/formal/crossprobe/latest.json",
) -> dict[str, Any]:
    input_path = _resolve_input_path(project, path)
    trace = _load_normalized_trace(input_path)

    design = write_design_index(project)
    connectivity = write_connectivity_index(project)
    report = build_formal_trace_crossprobe(
        project,
        trace,
        selectors=selectors,
        max_signals=max_signals,
        artifact=str(input_path),
        design_index=design,
        connectivity_index=connectivity,
    )
    report["input_path"] = str(input_path)
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
