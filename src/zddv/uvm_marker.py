from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any
import uuid

from zddv.config import ProjectConfig
from zddv.storage import get_run_record
from zddv.uvm_item import analyze_uvm_item_file
from zddv.uvm_sequence import analyze_uvm_sequence_file


_SEQUENCE_MARKER = "ZDDV_UVM_SEQUENCE"
_ITEM_MARKER = "ZDDV_UVM_ITEM"


def _marker_payload(
    line: str,
    *,
    marker: str,
    line_number: int,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    index = line.find(marker)
    if index < 0:
        return None, None

    payload_text = line[index + len(marker) :].strip()
    if not payload_text:
        return None, {
            "line": line_number,
            "marker": marker,
            "code": "EMPTY_MARKER_PAYLOAD",
            "message": f"{marker} must be followed by a JSON object",
        }

    try:
        payload = json.loads(payload_text)
    except json.JSONDecodeError as exc:
        return None, {
            "line": line_number,
            "marker": marker,
            "code": "INVALID_MARKER_JSON",
            "message": str(exc),
        }

    if not isinstance(payload, dict):
        return None, {
            "line": line_number,
            "marker": marker,
            "code": "MARKER_PAYLOAD_NOT_OBJECT",
            "message": f"{marker} payload must be a JSON object",
        }

    metadata = payload.get("metadata")
    if metadata is None:
        metadata = {}
    if not isinstance(metadata, dict):
        return None, {
            "line": line_number,
            "marker": marker,
            "code": "MARKER_METADATA_NOT_OBJECT",
            "message": f"{marker} metadata must be a JSON object when present",
        }

    normalized = dict(payload)
    normalized["metadata"] = {
        **metadata,
        "log_line": line_number,
        "marker": marker,
    }
    return normalized, None


def parse_uvm_marker_text(
    text: str,
    *,
    source: str = "uvm-marker-log",
) -> dict[str, Any]:
    if not isinstance(source, str) or not source.strip():
        raise ValueError("source must be a non-empty string")
    selected_source = source.strip()

    sequence_events: list[dict[str, Any]] = []
    item_events: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []

    for line_number, line in enumerate(text.splitlines(), start=1):
        marker_hits = [
            marker
            for marker in (_SEQUENCE_MARKER, _ITEM_MARKER)
            if marker in line
        ]
        if not marker_hits:
            continue

        if len(marker_hits) > 1:
            errors.append(
                {
                    "line": line_number,
                    "marker": "multiple",
                    "code": "AMBIGUOUS_MARKER_LINE",
                    "message": "A log line must contain exactly one ZDDV UVM marker",
                }
            )
            continue

        marker = marker_hits[0]
        payload, error = _marker_payload(
            line,
            marker=marker,
            line_number=line_number,
        )
        if error is not None:
            errors.append(error)
            continue
        assert payload is not None

        if marker == _SEQUENCE_MARKER:
            sequence_events.append(payload)
        else:
            item_events.append(payload)

    return {
        "analysis": "uvm_marker_log",
        "source": selected_source,
        "status": "FAIL" if errors else "PASS",
        "summary": {
            "sequence_events": len(sequence_events),
            "item_events": len(item_events),
            "markers": len(sequence_events) + len(item_events),
            "parse_errors": len(errors),
        },
        "sequence_trace": {
            "source": selected_source,
            "events": sequence_events,
        },
        "item_trace": {
            "source": selected_source,
            "events": item_events,
        },
        "parse_errors": errors,
        "limitations": [
            "Only explicit ZDDV_UVM_SEQUENCE and ZDDV_UVM_ITEM JSON markers are consumed.",
            "The adapter does not guess sequence or item lifecycle semantics from ordinary vendor/UVM report text.",
            "Domain validation is delegated to the existing UVM sequence and item analyzers after marker extraction.",
        ],
    }


def parse_uvm_marker_file(
    path: str | Path,
    *,
    source: str = "uvm-marker-log",
) -> dict[str, Any]:
    input_path = Path(path)
    return parse_uvm_marker_text(
        input_path.read_text(encoding="utf-8", errors="replace"),
        source=source,
    )


def analyze_uvm_marker_log(
    project: ProjectConfig,
    path: str | Path | None,
    *,
    source: str | None = None,
    output: str | Path = ".zddv/uvm/markers/latest.json",
    run_id: str | None = None,
) -> dict[str, Any]:
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

    selected_source = source
    if selected_source is None:
        if run_record is not None:
            selected_source = f"{run_record['simulator']}-uvm-marker"
        else:
            selected_source = "uvm-marker-log"

    parsed = parse_uvm_marker_file(input_path, source=selected_source)

    created_at = datetime.now(timezone.utc).isoformat()
    snapshot_id = (
        datetime.now(timezone.utc).strftime("uvm-marker-%Y%m%dT%H%M%S")
        + "-"
        + uuid.uuid4().hex[:8]
    )

    marker_dir = (project.root / ".zddv" / "uvm" / "markers").resolve()
    marker_dir.mkdir(parents=True, exist_ok=True)

    sequence_trace_path: Path | None = None
    item_trace_path: Path | None = None
    sequence_report: dict[str, Any] | None = None
    item_report: dict[str, Any] | None = None
    analysis_errors: list[dict[str, str]] = []

    if parsed["sequence_trace"]["events"]:
        sequence_trace_path = marker_dir / f"{snapshot_id}-sequence.json"
        sequence_trace_path.write_text(
            json.dumps(parsed["sequence_trace"], indent=2) + "\n",
            encoding="utf-8",
        )
        try:
            sequence_report = analyze_uvm_sequence_file(
                project,
                sequence_trace_path,
                source=selected_source,
                run_id=run_id,
            )
        except ValueError as exc:
            analysis_errors.append(
                {
                    "kind": "sequence",
                    "message": str(exc),
                }
            )

    if parsed["item_trace"]["events"]:
        item_trace_path = marker_dir / f"{snapshot_id}-item.json"
        item_trace_path.write_text(
            json.dumps(parsed["item_trace"], indent=2) + "\n",
            encoding="utf-8",
        )
        try:
            item_report = analyze_uvm_item_file(
                project,
                item_trace_path,
                source=selected_source,
                run_id=run_id,
            )
        except ValueError as exc:
            analysis_errors.append(
                {
                    "kind": "item",
                    "message": str(exc),
                }
            )

    child_failed = any(
        report is not None and report["status"] != "PASS"
        for report in (sequence_report, item_report)
    )
    status = (
        "FAIL"
        if parsed["parse_errors"] or analysis_errors or child_failed
        else "PASS"
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
        "input_path": str(input_path),
        "analysis": parsed["analysis"],
        "source": parsed["source"],
        "status": status,
        "summary": {
            **parsed["summary"],
            "sequence_analysis": sequence_report is not None,
            "item_analysis": item_report is not None,
            "analysis_errors": len(analysis_errors),
        },
        "parse_errors": parsed["parse_errors"],
        "analysis_errors": analysis_errors,
        "sequence_trace_path": (
            str(sequence_trace_path) if sequence_trace_path is not None else None
        ),
        "item_trace_path": (
            str(item_trace_path) if item_trace_path is not None else None
        ),
        "sequence_snapshot_id": (
            sequence_report.get("snapshot_id") if sequence_report is not None else None
        ),
        "item_snapshot_id": (
            item_report.get("snapshot_id") if item_report is not None else None
        ),
        "limitations": parsed["limitations"],
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
    serialized = json.dumps(record, indent=2) + "\n"
    destination.write_text(serialized, encoding="utf-8")
    return record
