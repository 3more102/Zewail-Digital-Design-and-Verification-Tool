from __future__ import annotations

from pathlib import Path
from typing import Any

from zddv.config import ProjectConfig
from zddv.uvm_item import analyze_uvm_item_log
from zddv.uvm_sequence import analyze_uvm_sequence_log


ITEM_MARKER = "ZDDV_UVM_ITEM"
SEQUENCE_MARKER = "ZDDV_UVM_SEQUENCE"


def _analysis_result(report: dict[str, Any]) -> dict[str, Any]:
    return {
        "detected": True,
        "status": str(report["status"]),
        "snapshot_id": report.get("snapshot_id"),
        "report_path": report.get("report_path"),
        "marker_count": len(report.get("marker_lines", [])),
    }


def _error_result(exc: OSError | ValueError) -> dict[str, Any]:
    return {
        "detected": True,
        "status": "ERROR",
        "error": str(exc),
    }


def ingest_uvm_marker_evidence(
    project: ProjectConfig,
    *,
    run_id: str,
    log_path: str | Path,
) -> dict[str, Any]:
    """Auto-ingest explicit ZDDV UVM markers from one recorded simulation log.

    Ordinary simulator/UVM prose is never interpreted as item or sequence evidence.
    A malformed explicit marker is reported as ingestion metadata rather than raised
    through the simulator backend, so the simulator's PASS/FAIL/TIMEOUT result stays
    authoritative.
    """
    path = Path(log_path).resolve()
    text = path.read_text(encoding="utf-8", errors="replace")

    result: dict[str, Any] = {
        "detected": False,
        "status": "NONE",
        "item": {"detected": False, "status": "NONE"},
        "sequence": {"detected": False, "status": "NONE"},
    }

    if ITEM_MARKER in text:
        result["detected"] = True
        try:
            item_report = analyze_uvm_item_log(
                project,
                path,
                run_id=run_id,
            )
        except (OSError, ValueError) as exc:
            result["item"] = _error_result(exc)
        else:
            result["item"] = _analysis_result(item_report)

    if SEQUENCE_MARKER in text:
        result["detected"] = True
        try:
            sequence_report = analyze_uvm_sequence_log(
                project,
                path,
                run_id=run_id,
            )
        except (OSError, ValueError) as exc:
            result["sequence"] = _error_result(exc)
        else:
            result["sequence"] = _analysis_result(sequence_report)

    if not result["detected"]:
        return result

    statuses = [
        evidence["status"]
        for evidence in (result["item"], result["sequence"])
        if evidence["detected"]
    ]
    if "ERROR" in statuses:
        result["status"] = "ERROR"
    elif "FAIL" in statuses:
        result["status"] = "FAIL"
    else:
        result["status"] = "PASS"
    return result
