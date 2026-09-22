from __future__ import annotations

import json
from pathlib import Path

from zddv.cli import main
from zddv.config import initialize_project
from zddv.storage import (
    list_uvm_item_handshake_snapshots,
    list_uvm_sequence_lifecycle_snapshots,
    record_run,
)
from zddv.uvm_marker import analyze_uvm_marker_log, parse_uvm_marker_text


def _marker_log() -> str:
    sequence_states = [
        "UVM_CREATED",
        "UVM_PRE_START",
        "UVM_BODY",
        "UVM_ENDED",
        "UVM_POST_START",
        "UVM_FINISHED",
    ]
    lines = [
        "# simulator banner",
        *[
            "ZDDV_UVM_SEQUENCE "
            + json.dumps(
                {
                    "sequence_id": "seq-7",
                    "sequence": "smoke_seq",
                    "sequencer": "uvm_test_top.env.sqr",
                    "state": state,
                    "time": f"{index * 10}ns",
                }
            )
            for index, state in enumerate(sequence_states)
        ],
        "# ZDDV_UVM_ITEM "
        + json.dumps(
            {
                "item_id": "item-3",
                "event": "GRANT",
                "sequence_id": "seq-7",
                "sequence": "smoke_seq",
                "sequencer": "uvm_test_top.env.sqr",
                "item": "req",
                "transaction_id": 3,
                "time": "70ns",
            }
        ),
        "ZDDV_UVM_ITEM "
        + json.dumps(
            {
                "item_id": "item-3",
                "event": "REQUEST",
                "sequence_id": "seq-7",
                "sequence": "smoke_seq",
                "sequencer": "uvm_test_top.env.sqr",
                "item": "req",
                "transaction_id": 3,
                "time": "80ns",
            }
        ),
        "ZDDV_UVM_ITEM "
        + json.dumps(
            {
                "item_id": "item-3",
                "event": "ITEM_DONE",
                "sequence_id": "seq-7",
                "sequence": "smoke_seq",
                "sequencer": "uvm_test_top.env.sqr",
                "item": "req",
                "transaction_id": 3,
                "time": "90ns",
            }
        ),
    ]
    return "\n".join(lines) + "\n"


def test_parse_marker_text_extracts_sequence_and_item_events():
    result = parse_uvm_marker_text(_marker_log(), source="test-marker")

    assert result["status"] == "PASS"
    assert result["summary"]["sequence_events"] == 6
    assert result["summary"]["item_events"] == 3
    assert result["summary"]["parse_errors"] == 0

    sequence_event = result["sequence_trace"]["events"][0]
    item_event = result["item_trace"]["events"][0]
    assert sequence_event["metadata"]["marker"] == "ZDDV_UVM_SEQUENCE"
    assert sequence_event["metadata"]["log_line"] == 2
    assert item_event["metadata"]["marker"] == "ZDDV_UVM_ITEM"


def test_parse_marker_text_reports_invalid_json():
    result = parse_uvm_marker_text(
        'ZDDV_UVM_ITEM {"item_id": "broken"\n',
        source="broken-marker",
    )

    assert result["status"] == "FAIL"
    assert result["summary"]["markers"] == 0
    assert result["summary"]["parse_errors"] == 1
    assert result["parse_errors"][0]["code"] == "INVALID_MARKER_JSON"


def test_marker_analysis_feeds_existing_analyzers(tmp_path: Path):
    project = initialize_project(tmp_path / "demo")
    log = project.root / "uvm-marker.log"
    log.write_text(_marker_log(), encoding="utf-8")

    result = analyze_uvm_marker_log(project, log, source="unit-marker")

    assert result["status"] == "PASS"
    assert result["summary"]["sequence_analysis"] is True
    assert result["summary"]["item_analysis"] is True
    assert Path(result["sequence_trace_path"]).is_file()
    assert Path(result["item_trace_path"]).is_file()

    sequence_rows = list_uvm_sequence_lifecycle_snapshots(project, limit=10)
    item_rows = list_uvm_item_handshake_snapshots(project, limit=10)
    assert len(sequence_rows) == 1
    assert sequence_rows[0]["finished_count"] == 1
    assert len(item_rows) == 1
    assert item_rows[0]["completed_count"] == 1


def test_marker_cli_uses_recorded_run_log(tmp_path: Path, capsys):
    project = initialize_project(tmp_path / "demo")
    run_id = "run-marker"
    run_dir = project.root / ".zddv" / "runs" / run_id
    run_dir.mkdir(parents=True)
    log = run_dir / "simulation.log"
    log.write_text(_marker_log(), encoding="utf-8")

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
            "run_dir": str(run_dir),
            "log": str(log),
            "waveform": None,
            "coverage": None,
            "timeout_s": 10.0,
            "command": ["vsim"],
            "plusargs": [],
        },
    )

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
    assert "sequence-events=6" in output
    assert "item-events=3" in output

    sequence_rows = list_uvm_sequence_lifecycle_snapshots(
        project,
        limit=10,
        run_id=run_id,
    )
    item_rows = list_uvm_item_handshake_snapshots(
        project,
        limit=10,
        run_id=run_id,
    )
    assert len(sequence_rows) == 1
    assert sequence_rows[0]["run_id"] == run_id
    assert len(item_rows) == 1
    assert item_rows[0]["run_id"] == run_id
