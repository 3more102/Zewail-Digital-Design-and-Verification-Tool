from __future__ import annotations

import json
from pathlib import Path

import pytest

from zddv.cli import main
from zddv.config import initialize_project
from zddv.storage import (
    list_uvm_item_handshake_snapshots,
    list_uvm_sequence_lifecycle_snapshots,
    record_run,
)
from zddv.uvm_marker import analyze_uvm_marker_log, has_explicit_uvm_markers


def _sequence_marker(state: str) -> str:
    return "ZDDV_UVM_SEQUENCE " + json.dumps(
        {
            "sequence_id": "seq-7",
            "sequence": "smoke_seq",
            "sequencer": "uvm_test_top.env.sqr",
            "state": state,
            "time": "10 ns",
        },
        separators=(",", ":"),
    )


def _item_marker(event: str) -> str:
    return "ZDDV_UVM_ITEM " + json.dumps(
        {
            "item_id": "item-3",
            "event": event,
            "sequence_id": "seq-7",
            "sequence": "smoke_seq",
            "sequencer": "uvm_test_top.env.sqr",
            "item": "req",
            "transaction_id": 3,
            "time": "20 ns",
        },
        separators=(",", ":"),
    )


def _marker_log() -> str:
    return "\n".join(
        [
            "# simulator banner",
            _sequence_marker("UVM_BODY"),
            _sequence_marker("UVM_ENDED"),
            _sequence_marker("UVM_POST_START"),
            _sequence_marker("UVM_FINISHED"),
            _item_marker("GRANT"),
            _item_marker("REQUEST"),
            _item_marker("ITEM_DONE"),
        ]
    ) + "\n"


def _record_run(project, run_id: str, log: Path) -> None:
    record_run(
        project,
        {
            "run_id": run_id,
            "created_at": "2026-09-22T10:00:00+00:00",
            "project": project.name,
            "simulator": "questa",
            "simulator_version": "Questa test",
            "top": "tb_top",
            "test": "marker_case",
            "seed": 17,
            "status": "PASS",
            "returncode": 0,
            "duration_ms": 1.0,
            "run_dir": str(log.parent),
            "log": str(log),
            "waveform": None,
            "coverage": None,
            "timeout_s": 10.0,
            "command": ["vsim"],
            "plusargs": [],
        },
    )


def test_explicit_marker_detection_is_exact_to_supported_contracts():
    assert has_explicit_uvm_markers("ZDDV_UVM_SEQUENCE {}")
    assert has_explicit_uvm_markers("prefix ZDDV_UVM_ITEM {} suffix")
    assert not has_explicit_uvm_markers("ZDDV_UVM_UNKNOWN {}")
    assert not has_explicit_uvm_markers("UVM_INFO ordinary report")


def test_marker_adapter_reuses_sequence_and_item_analyzers(tmp_path: Path):
    project = initialize_project(tmp_path / "demo")
    log = project.root / "uvm-marker.log"
    log.write_text(_marker_log(), encoding="utf-8")

    result = analyze_uvm_marker_log(project, log, source="unit-marker")

    assert result["status"] == "PASS"
    assert result["summary"]["sequence_marker_lines"] == 4
    assert result["summary"]["item_marker_lines"] == 3
    assert result["summary"]["sequence_events"] == 4
    assert result["summary"]["item_events"] == 3
    assert result["sequence_snapshot_id"]
    assert result["item_snapshot_id"]

    sequences = list_uvm_sequence_lifecycle_snapshots(project, limit=10)
    items = list_uvm_item_handshake_snapshots(project, limit=10)
    assert len(sequences) == 1
    assert len(items) == 1
    assert sequences[0]["finished_count"] == 1
    assert items[0]["completed_count"] == 1


def test_marker_adapter_rejects_no_evidence_log(tmp_path: Path):
    project = initialize_project(tmp_path / "demo")
    log = project.root / "plain.log"
    log.write_text("simulation complete\nZDDV_UVM_UNKNOWN {}\n", encoding="utf-8")

    with pytest.raises(ValueError, match="No explicit ZDDV_UVM_SEQUENCE or ZDDV_UVM_ITEM"):
        analyze_uvm_marker_log(project, log)


def test_marker_adapter_records_malformed_child_without_raising(tmp_path: Path):
    project = initialize_project(tmp_path / "demo")
    log = project.root / "bad-marker.log"
    log.write_text(
        'ZDDV_UVM_SEQUENCE {"sequence_id":\n' + _item_marker("GRANT") + "\n",
        encoding="utf-8",
    )

    result = analyze_uvm_marker_log(project, log, source="bad-marker")

    assert result["status"] == "FAIL"
    assert result["summary"]["analysis_errors"] == 1
    assert result["analysis_errors"][0]["kind"] == "sequence"
    assert result["item_snapshot_id"]


def test_marker_adapter_uses_recorded_run_log_and_correlates_children(tmp_path: Path):
    project = initialize_project(tmp_path / "demo")
    run_id = "run-marker"
    run_dir = project.root / ".zddv" / "runs" / run_id
    run_dir.mkdir(parents=True)
    log = run_dir / "simulation.log"
    log.write_text(_marker_log(), encoding="utf-8")
    _record_run(project, run_id, log)

    result = analyze_uvm_marker_log(project, None, run_id=run_id)

    assert result["status"] == "PASS"
    assert result["run_id"] == run_id
    assert result["simulator"] == "questa"

    sequences = list_uvm_sequence_lifecycle_snapshots(project, limit=10, run_id=run_id)
    items = list_uvm_item_handshake_snapshots(project, limit=10, run_id=run_id)
    assert len(sequences) == 1
    assert len(items) == 1
    assert sequences[0]["run_id"] == run_id
    assert items[0]["run_id"] == run_id


def test_marker_cli_uses_recorded_run_log(tmp_path: Path, capsys):
    project = initialize_project(tmp_path / "demo")
    run_id = "run-marker-cli"
    run_dir = project.root / ".zddv" / "runs" / run_id
    run_dir.mkdir(parents=True)
    log = run_dir / "simulation.log"
    log.write_text(_marker_log(), encoding="utf-8")
    _record_run(project, run_id, log)

    rc = main(
        [
            "--project",
            str(project.root),
            "uvm-marker-analyze",
            "--run",
            run_id,
        ]
    )

    assert rc == 0
    output = capsys.readouterr().out
    assert "UVM MARKER PASS" in output
    assert "sequence-markers=4" in output
    assert "item-markers=3" in output
    assert f"Run: {run_id}" in output
