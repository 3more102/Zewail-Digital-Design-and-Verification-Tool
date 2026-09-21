from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from zddv.config import ProjectConfig
from zddv.connectivity import signal_navigation, write_connectivity_index
from zddv.design_index import write_design_index
from zddv.waveform import write_waveform_index


def _hierarchy_paths_for_unit(node: dict[str, Any], unit: str) -> list[str]:
    paths: list[str] = []

    def visit(item: dict[str, Any]) -> None:
        if item.get("resolved", True) and item.get("type") == unit:
            path = str(item.get("path") or "")
            if path:
                paths.append(path)
        for child in item.get("children", []):
            visit(child)

    visit(node)
    return sorted(set(paths))


def _match_waveform_signals(
    waveform_index: dict[str, Any],
    *,
    signal: str,
    hierarchy_paths: list[str],
) -> list[dict[str, Any]]:
    if waveform_index.get("parse_status") != "indexed":
        return []

    candidates = [f"{path}.{signal}" for path in hierarchy_paths]
    matches: list[dict[str, Any]] = []
    seen: set[str] = set()

    for wave_signal in waveform_index.get("signals", []):
        wave_path = str(wave_signal.get("path") or "")
        for candidate in candidates:
            match_kind: str | None = None
            if wave_path == candidate:
                match_kind = "exact-hierarchy"
            elif wave_path.endswith("." + candidate):
                match_kind = "hierarchy-suffix"
            if match_kind is None:
                continue
            if wave_path in seen:
                break
            seen.add(wave_path)
            matches.append(
                {
                    "path": wave_path,
                    "name": wave_signal.get("name"),
                    "width": wave_signal.get("width"),
                    "range": wave_signal.get("range"),
                    "id_code": wave_signal.get("id_code"),
                    "match": match_kind,
                    "source_candidate": candidate,
                }
            )
            break

    if matches:
        return sorted(matches, key=lambda item: (item["path"], item["match"]))

    for wave_signal in waveform_index.get("signals", []):
        if str(wave_signal.get("name") or "") != signal:
            continue
        wave_path = str(wave_signal.get("path") or "")
        if not wave_path or wave_path in seen:
            continue
        seen.add(wave_path)
        matches.append(
            {
                "path": wave_path,
                "name": wave_signal.get("name"),
                "width": wave_signal.get("width"),
                "range": wave_signal.get("range"),
                "id_code": wave_signal.get("id_code"),
                "match": "basename-fallback",
                "source_candidate": None,
            }
        )

    return sorted(matches, key=lambda item: item["path"])


def build_source_waveform_crossprobe(
    project: ProjectConfig,
    *,
    signal: str,
    unit: str | None = None,
    run_id: str | None = None,
    input_path: str | Path | None = None,
) -> dict[str, Any]:
    selected_unit = unit or project.top
    design = write_design_index(project)
    connectivity = write_connectivity_index(project)
    navigation = signal_navigation(
        connectivity,
        unit=selected_unit,
        signal=signal,
    )
    waveform = write_waveform_index(
        project,
        run_id=run_id,
        input_path=input_path,
    )

    hierarchy_paths = _hierarchy_paths_for_unit(
        design["hierarchy"],
        selected_unit,
    )
    waveform_matches = _match_waveform_signals(
        waveform,
        signal=signal,
        hierarchy_paths=hierarchy_paths,
    )

    hierarchy_match_count = sum(
        item["match"] in {"exact-hierarchy", "hierarchy-suffix"}
        for item in waveform_matches
    )
    fallback_match_count = sum(
        item["match"] == "basename-fallback"
        for item in waveform_matches
    )

    if waveform.get("parse_status") != "indexed":
        match_status = "waveform-not-indexed"
    elif hierarchy_match_count:
        match_status = "hierarchy-matched"
    elif fallback_match_count == 1:
        match_status = "basename-fallback-unique"
    elif fallback_match_count > 1:
        match_status = "basename-fallback-ambiguous"
    else:
        match_status = "no-waveform-match"

    return {
        "schema_version": 1,
        "project": project.name,
        "unit": selected_unit,
        "signal": signal,
        "source": {
            "hierarchy_paths": hierarchy_paths,
            "drivers": navigation["drivers"],
            "loads": navigation["loads"],
        },
        "waveform": {
            "run_id": waveform.get("run_id"),
            "format": waveform.get("format"),
            "parse_status": waveform.get("parse_status"),
            "timescale": waveform.get("timescale"),
            "artifact": waveform.get("artifact"),
            "matches": waveform_matches,
        },
        "match_status": match_status,
        "summary": {
            "hierarchy_instances": len(hierarchy_paths),
            "drivers": len(navigation["drivers"]),
            "loads": len(navigation["loads"]),
            "waveform_matches": len(waveform_matches),
            "hierarchy_matches": hierarchy_match_count,
            "basename_fallback_matches": fallback_match_count,
        },
        "limitations": [
            "Hierarchy mapping is source-level and does not resolve generate/parameter specialization.",
            "Hierarchy-suffix matching tolerates simulator wrapper scopes but does not guess renamed signals.",
            "Basename fallback is reported explicitly and can be ambiguous; it is not treated as proof of identity.",
        ],
    }


def write_source_waveform_crossprobe(
    project: ProjectConfig,
    *,
    signal: str,
    unit: str | None = None,
    run_id: str | None = None,
    input_path: str | Path | None = None,
    output: str | Path = ".zddv/debug/crossprobe.json",
) -> dict[str, Any]:
    report = build_source_waveform_crossprobe(
        project,
        signal=signal,
        unit=unit,
        run_id=run_id,
        input_path=input_path,
    )

    path = Path(output)
    if not path.is_absolute():
        path = project.root / path
    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return {**report, "path": str(path)}
