from __future__ import annotations

import json
from pathlib import Path
import re
from typing import Any

from zddv.config import ProjectConfig
from zddv.connectivity import build_connectivity_index, signal_navigation, write_connectivity_index
from zddv.design_index import build_design_index, write_design_index
from zddv.waveform import write_waveform_index


_DECLARATION_KEYWORD_RE = re.compile(
    r"\b(?:input|output|inout|wire|wand|wor|tri|logic|reg|bit|byte|shortint|"
    r"int|longint|integer|time|realtime|event|genvar)\b"
)


def _hierarchy_nodes(node: dict[str, Any]) -> list[dict[str, Any]]:
    nodes = [node]
    for child in node.get("children", []):
        nodes.extend(_hierarchy_nodes(child))
    return nodes


def _match_waveform_signal(
    signals: list[dict[str, Any]],
    query: str,
) -> tuple[dict[str, Any], str]:
    normalized = query.strip()
    if not normalized:
        raise ValueError("Signal query must not be empty")

    exact = [item for item in signals if item.get("path") == normalized]
    if len(exact) == 1:
        return exact[0], "exact"

    suffix = [
        item
        for item in signals
        if str(item.get("path", "")).endswith("." + normalized)
    ]
    if len(suffix) == 1:
        return suffix[0], "path-suffix"

    by_name = [item for item in signals if item.get("name") == normalized]
    if len(by_name) == 1:
        return by_name[0], "unique-name"

    candidates = suffix or by_name
    if candidates:
        names = ", ".join(str(item.get("path")) for item in candidates[:8])
        extra = "" if len(candidates) <= 8 else f", ... (+{len(candidates) - 8} more)"
        raise RuntimeError(
            f"Signal query '{normalized}' is ambiguous: {names}{extra}. "
            "Use a longer hierarchical path."
        )

    raise RuntimeError(f"Signal '{normalized}' was not found in the waveform index.")


def _match_hierarchy_scope(
    scope: str,
    hierarchy: dict[str, Any],
) -> tuple[dict[str, Any] | None, str | None]:
    resolved = [
        node
        for node in _hierarchy_nodes(hierarchy)
        if node.get("resolved", False) and node.get("path")
    ]

    exact = [node for node in resolved if node["path"] == scope]
    if len(exact) == 1:
        return exact[0], "exact"

    suffix = [
        node
        for node in resolved
        if scope.endswith("." + str(node["path"]))
    ]
    if not suffix:
        return None, None

    longest = max(len(str(node["path"])) for node in suffix)
    best = [node for node in suffix if len(str(node["path"])) == longest]
    if len(best) == 1:
        return best[0], "scope-suffix"
    return None, None


def _design_unit_for_node(
    node: dict[str, Any],
    design_index: dict[str, Any],
) -> dict[str, Any] | None:
    node_type = node.get("type")
    node_file = node.get("file")
    matches = [
        unit
        for unit in design_index.get("units", [])
        if unit.get("name") == node_type
        and (node_file is None or unit.get("file") == node_file)
    ]
    if len(matches) == 1:
        return matches[0]
    return None


def _source_path(project: ProjectConfig, file_value: str) -> Path:
    path = Path(file_value)
    if not path.is_absolute():
        path = project.root / path
    return path.resolve()


def _find_source_declaration(
    project: ProjectConfig,
    unit: dict[str, Any],
    signal_name: str,
) -> dict[str, Any] | None:
    file_value = str(unit["file"])
    source = _source_path(project, file_value)
    if not source.exists():
        return None

    lines = source.read_text(encoding="utf-8", errors="replace").splitlines()
    start = max(1, int(unit.get("line", 1)))
    end = min(len(lines), int(unit.get("end_line", len(lines))))
    token_re = re.compile(
        rf"(?<![A-Za-z0-9_$]){re.escape(signal_name)}(?![A-Za-z0-9_$])"
    )

    for line_number in range(start, end + 1):
        text = lines[line_number - 1]
        token = token_re.search(text)
        if token is None:
            continue
        prefix = text[: token.start()]
        if _DECLARATION_KEYWORD_RE.search(prefix) is None:
            continue
        return {
            "file": file_value,
            "line": line_number,
            "text": text.strip(),
            "confidence": "declaration-line",
        }

    return None


def build_crossprobe(
    project: ProjectConfig,
    signal_query: str,
    waveform_index: dict[str, Any],
    *,
    design_index: dict[str, Any] | None = None,
    connectivity_index: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Correlate a waveform signal with source-level design hierarchy metadata."""
    if waveform_index.get("parse_status") != "indexed":
        raise RuntimeError(
            "Cross-probing requires a signal-indexed waveform. "
            f"Current parse status: {waveform_index.get('parse_status', 'unknown')}"
        )

    design = design_index or build_design_index(project)
    signal, signal_match = _match_waveform_signal(
        list(waveform_index.get("signals", [])),
        signal_query,
    )
    scope = str(signal.get("scope", ""))
    hierarchy_node, hierarchy_match = _match_hierarchy_scope(
        scope,
        design["hierarchy"],
    )

    source: dict[str, Any] | None = None
    connectivity_payload: dict[str, Any] | None = None
    status = "PARTIAL"
    note: str | None = None

    if hierarchy_node is None:
        note = (
            "The waveform signal was found, but its scope could not be mapped "
            "to the source-level hierarchy."
        )
    else:
        unit = _design_unit_for_node(hierarchy_node, design)
        if unit is None:
            note = (
                "The waveform scope matched the hierarchy, but the design unit "
                "could not be resolved uniquely."
            )
        else:
            declaration = _find_source_declaration(
                project,
                unit,
                str(signal.get("name", "")),
            )
            source = {
                "unit": unit["name"],
                "kind": unit["kind"],
                "file": unit["file"],
                "unit_line": unit["line"],
                "unit_end_line": unit["end_line"],
                "declaration": declaration,
            }

            connectivity = connectivity_index or build_connectivity_index(project)
            try:
                navigation = signal_navigation(
                    connectivity,
                    unit=str(unit["name"]),
                    signal=str(signal.get("name", "")),
                )
            except ValueError:
                navigation = None
            if navigation is not None:
                connectivity_payload = {
                    "analysis_level": connectivity.get("analysis_level"),
                    "unit": navigation["unit"],
                    "signal": navigation["signal"],
                    "drivers": navigation["drivers"],
                    "loads": navigation["loads"],
                }

            if declaration is not None:
                status = "MATCHED"
            else:
                note = (
                    "The waveform scope matched a source design unit, but no "
                    "same-line SystemVerilog declaration was found for the signal."
                )

    hierarchy_payload = None
    if hierarchy_node is not None:
        hierarchy_payload = {
            "waveform_scope": scope,
            "design_path": hierarchy_node["path"],
            "instance": hierarchy_node["instance"],
            "type": hierarchy_node["type"],
            "file": hierarchy_node.get("file"),
            "line": hierarchy_node.get("line"),
            "match": hierarchy_match,
        }

    return {
        "schema_version": 1,
        "project": project.name,
        "status": status,
        "query": signal_query,
        "signal_match": signal_match,
        "waveform": {
            "run_id": waveform_index.get("run_id"),
            "format": waveform_index.get("format"),
            "artifact": waveform_index.get("artifact"),
            "signal": signal,
        },
        "hierarchy": hierarchy_payload,
        "source": source,
        "connectivity": connectivity_payload,
        "note": note,
    }


def write_crossprobe_report(
    project: ProjectConfig,
    signal_query: str,
    *,
    run_id: str | None = None,
    input_path: str | Path | None = None,
    output: str | Path = ".zddv/debug/crossprobe.json",
) -> dict[str, Any]:
    design = write_design_index(project)
    connectivity = write_connectivity_index(project)
    waveform = write_waveform_index(
        project,
        run_id=run_id,
        input_path=input_path,
    )
    report = build_crossprobe(
        project,
        signal_query,
        waveform,
        design_index=design,
        connectivity_index=connectivity,
    )
    report["design_index_path"] = design["path"]
    report["connectivity_index_path"] = connectivity["path"]
    report["waveform_index_path"] = waveform["path"]

    destination = Path(output)
    if not destination.is_absolute():
        destination = project.root / destination
    destination = destination.resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return {**report, "report_path": str(destination)}
