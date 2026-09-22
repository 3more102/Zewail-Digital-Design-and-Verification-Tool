from __future__ import annotations

import json
from pathlib import Path

from zddv.config import initialize_project
from zddv.storage import (
    list_uvm_item_handshake_snapshots,
    list_uvm_sequence_lifecycle_snapshots,
    record_run,
)
from zddv.uvm_auto import ingest_uvm_marker_evidence


def _record_run(project, run_id: str, log: Path) -> None:
    run_dir = log.parent
    record_run(
        project,
        {
            "run_id": run_id,
            "created_at": "2026-09-22T10:30:00+00:00",
            "project": project.name,
            "simulator": "questa",
            "simulator_version": "Questa test",
            "top": "tb_top",
            "test": "marker_case",
            "seed": 37,
            "status": "PASS",
            "returncode": 0,
            "duration_ms": 1.0,
            "run_dir": str(run_dir),
            "log": str(log),
            "waveform": None,
            "coverage": None,
            "timeout_s": 10.0,
            "command": ["vsim"],
            "plusargs": [],
        },
    )


def _marker(name: str, payload: dict[str, object]) -> str:
    return name + " " + json.dumps(payload, separators=(",", ":"))


def test_no_explicit_markers_is_noop(tmp_path: Path):
    project = initialize_project(tmp_path / "demo")
    run_dir = project.root / ".zddv" / "runs" / "run-none"
    run_dir.mkdir(parents=True)
    log = run_dir / "simulation.log"
    log.write_text("UVM_INFO @ 1: reporter [SMOKE] ordinary UVM text\n", encoding="utf-8")
    _record_run(project, "run-none", log)

    result = ingest_uvm_marker_evidence(
        project,
        run_id="run-none",
        log_path=log,
    )

    assert result == {
        "detected": False,
        "status": "NONE",
        "item": {"detected": False, "status": "NONE"},
        "sequence": {"detected": False, "status": "NONE"},
    }
    assert list_uvm_item_handshake_snapshots(project, limit=10) == []
    assert list_uvm_sequence_lifecycle_snapshots(project, limit=10) == []


def test_auto_ingests_item_and_sequence_markers_with_run_correlation(tmp_path: Path):
    project = initialize_project(tmp_path / "demo")
    run_dir = project.root / ".zddv" / "runs" / "run-markers"
    run_dir.mkdir(parents=True)
    log = run_dir / "simulation.log"

    item_base = {
        "item_id": "item-1",
        "sequence_id": "seq-1",
        "sequence": "write_seq",
        "sequencer": "uvm_test_top.env.seqr",
        "item": "axi_item",
        "transaction_id": 11,
    }
    sequence_base = {
        "sequence_id": "seq-1",
        "sequence": "write_seq",
        "sequencer": "uvm_test_top.env.seqr",
    }
    lines = [
        "# simulator banner",
        _marker("ZDDV_UVM_ITEM", {**item_base, "event": "GRANT", "time": "1 ns"}),
        _marker("ZDDV_UVM_ITEM", {**item_base, "event": "REQUEST", "time": "1 ns"}),
        _marker("ZDDV_UVM_ITEM", {**item_base, "event": "ITEM_DONE", "time": "8 ns"}),
        _marker("ZDDV_UVM_SEQUENCE", {**sequence_base, "state": "UVM_BODY", "time": "1 ns"}),
        _marker("ZDDV_UVM_SEQUENCE", {**sequence_base, "state": "UVM_ENDED", "time": "8 ns"}),
        _marker("ZDDV_UVM_SEQUENCE", {**sequence_base, "state": "UVM_POST_START", "time": "8 ns"}),
        _marker("ZDDV_UVM_SEQUENCE", {**sequence_base, "state": "UVM_FINISHED", "time": "8 ns"}),
    ]
    log.write_text("\n".join(lines) + "\n", encoding="utf-8")
    _record_run(project, "run-markers", log)

    result = ingest_uvm_marker_evidence(
        project,
        run_id="run-markers",
        log_path=log,
    )

    assert result["status"] == "PASS"
    assert result["item"]["status"] == "PASS"
    assert result["item"]["marker_count"] == 3
    assert result["sequence"]["status"] == "PASS"
    assert result["sequence"]["marker_count"] == 4
    assert Path(result["item"]["report_path"]).is_file()
    assert Path(result["sequence"]["report_path"]).is_file()

    item_rows = list_uvm_item_handshake_snapshots(
        project,
        limit=10,
        run_id="run-markers",
    )
    sequence_rows = list_uvm_sequence_lifecycle_snapshots(
        project,
        limit=10,
        run_id="run-markers",
    )
    assert len(item_rows) == 1
    assert item_rows[0]["run_id"] == "run-markers"
    assert len(sequence_rows) == 1
    assert sequence_rows[0]["run_id"] == "run-markers"


def test_malformed_explicit_marker_is_reported_without_raising(tmp_path: Path):
    project = initialize_project(tmp_path / "demo")
    run_dir = project.root / ".zddv" / "runs" / "run-bad-marker"
    run_dir.mkdir(parents=True)
    log = run_dir / "simulation.log"
    log.write_text('ZDDV_UVM_ITEM {"item_id":\n', encoding="utf-8")
    _record_run(project, "run-bad-marker", log)

    result = ingest_uvm_marker_evidence(
        project,
        run_id="run-bad-marker",
        log_path=log,
    )

    assert result["detected"] is True
    assert result["status"] == "ERROR"
    assert result["item"]["status"] == "ERROR"
    assert "line 1" in result["item"]["error"]
    assert result["sequence"] == {"detected": False, "status": "NONE"}


def test_semantic_marker_violation_is_fail_not_ingestion_error(tmp_path: Path):
    project = initialize_project(tmp_path / "demo")
    run_dir = project.root / ".zddv" / "runs" / "run-fail-marker"
    run_dir.mkdir(parents=True)
    log = run_dir / "simulation.log"

    base = {
        "item_id": "item-1",
        "sequence_id": "seq-1",
        "sequence": "write_seq",
        "sequencer": "uvm_test_top.env.seqr",
        "item": "axi_item",
        "transaction_id": 12,
    }
    log.write_text(
        "\n".join(
            [
                _marker("ZDDV_UVM_ITEM", {**base, "event": "REQUEST"}),
                _marker("ZDDV_UVM_ITEM", {**base, "event": "GRANT"}),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    _record_run(project, "run-fail-marker", log)

    result = ingest_uvm_marker_evidence(
        project,
        run_id="run-fail-marker",
        log_path=log,
    )

    assert result["status"] == "FAIL"
    assert result["item"]["status"] == "FAIL"
    assert "error" not in result["item"]
