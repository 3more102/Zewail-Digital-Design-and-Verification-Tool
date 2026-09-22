from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from zddv.config import ProjectConfig
from zddv.storage import get_run_record
from zddv.uvm_item import analyze_uvm_item_file


DEFAULT_UVM_ITEM_MARKER = "ZDDV_UVM_ITEM"


def _resolve_project_path(project: ProjectConfig, path: str | Path) -> Path:
    resolved = Path(path)
    if not resolved.is_absolute():
        resolved = project.root / resolved
    return resolved.resolve()


def _resolve_log_path(
    project: ProjectConfig,
    path: str | Path | None,
    *,
    run_id: str | None,
) -> Path:
    if path is None:
        if run_id is None:
            raise ValueError("path is required unless run_id is supplied")
        run_record = get_run_record(project, run_id)
        if run_record is None:
            raise ValueError(f"Unknown run ID: {run_id}")
        path = run_record["log_path"]

    resolved = _resolve_project_path(project, path)
    if not resolved.is_file():
        raise FileNotFoundError(resolved)
    return resolved


def extract_uvm_item_markers(
    path: str | Path,
    *,
    source: str | None = None,
    marker: str = DEFAULT_UVM_ITEM_MARKER,
) -> dict[str, Any]:
    log_path = Path(path).resolve()
    if not log_path.is_file():
        raise FileNotFoundError(log_path)

    selected_marker = marker.strip()
    if not selected_marker:
        raise ValueError("marker must be a non-empty string")

    selected_source = (source or "uvm-item-log-marker").strip()
    if not selected_source:
        raise ValueError("source must be a non-empty string")

    events: list[dict[str, Any]] = []
    for line_number, raw_line in enumerate(
        log_path.read_text(encoding="utf-8").splitlines(),
        start=1,
    ):
        marker_index = raw_line.find(selected_marker)
        if marker_index < 0:
            continue

        payload_text = raw_line[marker_index + len(selected_marker) :].strip()
        if payload_text.startswith(":"):
            payload_text = payload_text[1:].lstrip()
        if not payload_text:
            raise ValueError(
                f"{log_path}:{line_number}: empty JSON payload after {selected_marker}"
            )

        try:
            payload = json.loads(payload_text)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"{log_path}:{line_number}: invalid JSON after "
                f"{selected_marker}: {exc.msg}"
            ) from exc

        if not isinstance(payload, dict):
            raise ValueError(
                f"{log_path}:{line_number}: marker payload must be a JSON object"
            )

        metadata = payload.get("metadata")
        if metadata is None:
            metadata = {}
        if not isinstance(metadata, dict):
            raise ValueError(
                f"{log_path}:{line_number}: marker metadata must be a JSON object"
            )

        event = dict(payload)
        event_metadata = dict(metadata)
        event_metadata.setdefault("log_line", line_number)
        event["metadata"] = event_metadata
        events.append(event)

    if not events:
        raise ValueError(
            f"No {selected_marker} marker events found in {log_path}"
        )

    return {
        "source": selected_source,
        "source_log": str(log_path),
        "marker": selected_marker,
        "events": events,
    }


def write_uvm_item_marker_trace(
    project: ProjectConfig,
    path: str | Path,
    *,
    source: str | None = None,
    marker: str = DEFAULT_UVM_ITEM_MARKER,
    output: str | Path = ".zddv/uvm/items/extracted/latest.json",
) -> dict[str, Any]:
    log_path = _resolve_project_path(project, path)
    trace = extract_uvm_item_markers(
        log_path,
        source=source,
        marker=marker,
    )

    destination = _resolve_project_path(project, output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(trace, indent=2) + "\n", encoding="utf-8")

    return {
        **trace,
        "trace_path": str(destination),
    }


def analyze_uvm_item_log(
    project: ProjectConfig,
    path: str | Path | None = None,
    *,
    source: str | None = None,
    marker: str = DEFAULT_UVM_ITEM_MARKER,
    trace_output: str | Path = ".zddv/uvm/items/extracted/latest.json",
    output: str | Path = ".zddv/uvm/items/latest.json",
    run_id: str | None = None,
) -> dict[str, Any]:
    log_path = _resolve_log_path(project, path, run_id=run_id)
    trace = write_uvm_item_marker_trace(
        project,
        log_path,
        source=source,
        marker=marker,
        output=trace_output,
    )

    result = analyze_uvm_item_file(
        project,
        trace["trace_path"],
        source=source or trace["source"],
        output=output,
        run_id=run_id,
    )
    result["source_log_path"] = str(log_path)
    result["marker"] = trace["marker"]
    result["marker_trace_path"] = trace["trace_path"]
    result["marker_events"] = len(trace["events"])

    serialized = json.dumps(result, indent=2) + "\n"
    for key in ("normalized_path", "report_path"):
        artifact = Path(result[key])
        artifact.write_text(serialized, encoding="utf-8")

    return result
