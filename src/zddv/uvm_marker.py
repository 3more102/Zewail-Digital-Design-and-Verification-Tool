from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any
import uuid

from zddv.config import ProjectConfig
from zddv.storage import get_run_record
from zddv.uvm_item import analyze_uvm_item_log
from zddv.uvm_sequence import analyze_uvm_sequence_log


SEQUENCE_MARKER = "ZDDV_UVM_SEQUENCE"
ITEM_MARKER = "ZDDV_UVM_ITEM"


def has_explicit_uvm_markers(text: str) -> bool:
    """Return True only for the explicit marker contracts ZDDV understands."""
    return SEQUENCE_MARKER in text or ITEM_MARKER in text


def _resolve_marker_input(
    project: ProjectConfig,
    path: str | Path | None,
    *,
    run_id: str | None,
) -> tuple[Path, dict[str, Any] | None]:
    run_record: dict[str, Any] | None = None
    if run_id is not None:
        run_record = get_run_record(project, run_id)
        if run_record is None:
            raise ValueError(f"Unknown run ID: {run_id}")

    if path is None:
        if run_record is None:
            raise ValueError("A simulation log path or --run must be provided")
        input_path = Path(run_record["log_path"])
    else:
        input_path = Path(path)
        if not input_path.is_absolute():
            input_path = project.root / input_path

    input_path = input_path.resolve()
    if not input_path.is_file():
        raise FileNotFoundError(input_path)
    return input_path, run_record


def analyze_uvm_marker_log(
    project: ProjectConfig,
    path: str | Path | None,
    *,
    source: str | None = None,
    output: str | Path = ".zddv/uvm/markers/latest.json",
    run_id: str | None = None,
) -> dict[str, Any]:
    """Analyze explicit ZDDV UVM sequence/item markers from one simulation log."""
    input_path, run_record = _resolve_marker_input(project, path, run_id=run_id)
    text = input_path.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()
    sequence_marker_lines = [
        index for index, line in enumerate(lines, start=1) if SEQUENCE_MARKER in line
    ]
    item_marker_lines = [
        index for index, line in enumerate(lines, start=1) if ITEM_MARKER in line
    ]

    if not sequence_marker_lines and not item_marker_lines:
        raise ValueError(
            "No explicit ZDDV_UVM_SEQUENCE or ZDDV_UVM_ITEM markers found in log"
        )

    selected_source = source or (
        f"{run_record['simulator']}-uvm-marker"
        if run_record is not None
        else "uvm-marker-log"
    )

    sequence_report: dict[str, Any] | None = None
    item_report: dict[str, Any] | None = None
    analysis_errors: list[dict[str, Any]] = []

    if sequence_marker_lines:
        try:
            sequence_report = analyze_uvm_sequence_log(
                project,
                input_path,
                source=f"{selected_source}-sequence",
                run_id=run_id,
            )
        except ValueError as exc:
            analysis_errors.append(
                {
                    "kind": "sequence",
                    "marker": SEQUENCE_MARKER,
                    "message": str(exc),
                }
            )

    if item_marker_lines:
        try:
            item_report = analyze_uvm_item_log(
                project,
                input_path,
                source=f"{selected_source}-item",
                run_id=run_id,
            )
        except ValueError as exc:
            analysis_errors.append(
                {
                    "kind": "item",
                    "marker": ITEM_MARKER,
                    "message": str(exc),
                }
            )

    child_failed = any(
        report is not None and report["status"] != "PASS"
        for report in (sequence_report, item_report)
    )
    status = "FAIL" if analysis_errors or child_failed else "PASS"

    created_at = datetime.now(timezone.utc).isoformat()
    snapshot_id = (
        datetime.now(timezone.utc).strftime("uvm-marker-%Y%m%dT%H%M%S")
        + "-"
        + uuid.uuid4().hex[:8]
    )

    destination = Path(output)
    if not destination.is_absolute():
        destination = project.root / destination
    destination = destination.resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)

    record: dict[str, Any] = {
        "snapshot_id": snapshot_id,
        "created_at": created_at,
        "project": project.name,
        "analysis": "uvm_marker_log",
        "source": selected_source,
        "status": status,
        "input_path": str(input_path),
        "summary": {
            "sequence_events": (
                int(sequence_report["summary"]["events"])
                if sequence_report is not None
                else 0
            ),
            "item_events": (
                int(item_report["summary"]["events"])
                if item_report is not None
                else 0
            ),
            "sequence_marker_lines": len(sequence_marker_lines),
            "item_marker_lines": len(item_marker_lines),
            "markers": len(sequence_marker_lines) + len(item_marker_lines),
            "analysis_errors": len(analysis_errors),
            "sequence_analysis": sequence_report is not None,
            "item_analysis": item_report is not None,
        },
        "marker_lines": {
            "sequence": sequence_marker_lines,
            "item": item_marker_lines,
        },
        "analysis_errors": analysis_errors,
        "sequence_snapshot_id": (
            sequence_report.get("snapshot_id") if sequence_report is not None else None
        ),
        "item_snapshot_id": (
            item_report.get("snapshot_id") if item_report is not None else None
        ),
        "limitations": [
            "Only explicit ZDDV_UVM_SEQUENCE and ZDDV_UVM_ITEM JSON markers are consumed.",
            "Ordinary simulator/UVM prose is never reinterpreted as sequence or item lifecycle evidence.",
            "Malformed explicit markers fail marker analysis but do not change the recorded simulator run result.",
        ],
    }
    if run_record is not None:
        record.update(
            {
                "run_id": run_record["run_id"],
                "run_status": run_record["status"],
                "run_returncode": int(run_record["returncode"]),
                "simulator": run_record["simulator"],
            }
        )

    record["report_path"] = str(destination)
    destination.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    return record
