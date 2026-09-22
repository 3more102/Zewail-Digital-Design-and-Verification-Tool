from __future__ import annotations

import json
from pathlib import Path

from zddv.cli import main
from zddv.config import initialize_project
from zddv.storage import (
    list_uvm_sequence_events,
    list_uvm_sequence_snapshots,
    record_run,
)
from zddv.uvm_sequence import analyze_uvm_sequence_file, parse_uvm_sequence_data


def _event(
    sequence_id: str,
    sequence: str,
    state: str,
    *,
    sequencer: str = "uvm_test_top.env.seqr",
    parent_sequence_id: str | None = None,
    time: str | None = None,
) -> dict[str, object]:
    return {
        "sequence_id": sequence_id,
        "sequence": sequence,
        "sequencer": sequencer,
        "parent_sequence_id": parent_sequence_id,
        "state": state,
        "time": time,
    }


def test_parses_complete_sequence_lifecycle_and_optional_callbacks():
    result = parse_uvm_sequence_data(
        {
            "source": "unit-test",
            "events": [
                _event("seq-1", "axi_write_seq", "UVM_CREATED"),
                _event("seq-1", "axi_write_seq", "UVM_PRE_START"),
                _event("seq-1", "axi_write_seq", "UVM_PRE_BODY"),
                _event("seq-1", "axi_write_seq", "UVM_BODY"),
                _event("seq-1", "axi_write_seq", "UVM_ENDED"),
                _event("seq-1", "axi_write_seq", "UVM_POST_BODY"),
                _event("seq-1", "axi_write_seq", "UVM_POST_START"),
                _event("seq-1", "axi_write_seq", "UVM_FINISHED"),
                _event("seq-2", "axi_read_seq", "UVM_CREATED"),
                _event("seq-2", "axi_read_seq", "UVM_PRE_START"),
                _event("seq-2", "axi_read_seq", "UVM_BODY"),
                _event("seq-2", "axi_read_seq", "UVM_ENDED"),
                _event("seq-2", "axi_read_seq", "UVM_POST_START"),
                _event("seq-2", "axi_read_seq", "UVM_FINISHED"),
            ],
        }
    )

    assert result["status"] == "PASS"
    assert result["source"] == "unit-test"
    assert result["summary"] == {
        "sequences": 2,
        "events": 14,
        "violations": 0,
        "finished": 2,
        "stopped": 0,
        "active": 0,
        "complete": 2,
        "nested": 0,
        "unresolved_parents": 0,
    }
    assert result["sequences"][0]["terminal_state"] == "UVM_FINISHED"
    assert result["sequences"][1]["states"] == [
        "UVM_CREATED",
        "UVM_PRE_START",
        "UVM_BODY",
        "UVM_ENDED",
        "UVM_POST_START",
        "UVM_FINISHED",
    ]


def test_accepts_killed_and_partial_sequence_traces_without_inventing_failure():
    result = parse_uvm_sequence_data(
        {
            "events": [
                _event("killed", "timeout_seq", "UVM_PRE_START"),
                _event("killed", "timeout_seq", "UVM_BODY"),
                _event("killed", "timeout_seq", "UVM_STOPPED"),
                _event("partial", "background_seq", "UVM_BODY"),
                _event("partial", "background_seq", "UVM_ENDED"),
            ]
        }
    )

    assert result["status"] == "PASS"
    assert result["summary"]["stopped"] == 1
    assert result["summary"]["active"] == 1
    assert result["summary"]["complete"] == 0


def test_detects_invalid_transition_and_event_after_terminal():
    result = parse_uvm_sequence_data(
        {
            "events": [
                _event("seq-1", "bad_seq", "UVM_CREATED"),
                _event("seq-1", "bad_seq", "UVM_BODY"),
                _event("seq-1", "bad_seq", "UVM_FINISHED"),
                _event("seq-1", "bad_seq", "UVM_BODY"),
            ]
        }
    )

    assert result["status"] == "FAIL"
    assert [item["code"] for item in result["violations"]] == [
        "INVALID_STATE_TRANSITION",
        "INVALID_STATE_TRANSITION",
        "EVENT_AFTER_TERMINAL",
    ]


def test_tracks_nested_sequences_and_unresolved_parent_evidence():
    result = parse_uvm_sequence_data(
        {
            "events": [
                _event("parent", "virtual_seq", "UVM_BODY"),
                _event(
                    "child",
                    "write_seq",
                    "UVM_BODY",
                    parent_sequence_id="parent",
                ),
                _event(
                    "orphan",
                    "read_seq",
                    "UVM_BODY",
                    parent_sequence_id="missing-parent",
                ),
            ]
        }
    )

    assert result["status"] == "PASS"
    assert result["summary"]["nested"] == 2
    assert result["summary"]["unresolved_parents"] == 1
    child = next(item for item in result["sequences"] if item["sequence_id"] == "child")
    assert child["parent_observed"] is True


def test_rejects_unknown_sequence_state():
    try:
        parse_uvm_sequence_data(
            {"events": [_event("seq-1", "bad_seq", "UVM_NOT_A_STATE")]}
        )
    except ValueError as exc:
        assert "state must be one of" in str(exc)
    else:
        raise AssertionError("Expected ValueError for unknown UVM sequence state")


def test_analyze_persists_sequence_snapshot_and_events(tmp_path: Path):
    project = initialize_project(tmp_path / "demo")
    trace = project.root / "sequence.json"
    trace.write_text(
        json.dumps(
            {
                "source": "uvm-helper",
                "events": [
                    _event("seq-1", "smoke_seq", "UVM_CREATED", time="0 ns"),
                    _event("seq-1", "smoke_seq", "UVM_PRE_START", time="0 ns"),
                    _event("seq-1", "smoke_seq", "UVM_BODY", time="1 ns"),
                    _event("seq-1", "smoke_seq", "UVM_ENDED", time="10 ns"),
                    _event("seq-1", "smoke_seq", "UVM_POST_START", time="10 ns"),
                    _event("seq-1", "smoke_seq", "UVM_FINISHED", time="10 ns"),
                ],
            }
        ),
        encoding="utf-8",
    )

    result = analyze_uvm_sequence_file(project, trace)

    assert result["status"] == "PASS"
    assert Path(result["normalized_path"]).is_file()
    assert Path(result["report_path"]).is_file()

    snapshots = list_uvm_sequence_snapshots(project, limit=10)
    assert len(snapshots) == 1
    assert snapshots[0]["snapshot_id"] == result["snapshot_id"]
    assert snapshots[0]["sequence_count"] == 1
    assert snapshots[0]["finished_count"] == 1

    events = list_uvm_sequence_events(project, result["snapshot_id"])
    assert len(events) == 6
    assert events[2]["state"] == "UVM_BODY"
    assert events[2]["sequence_name"] == "smoke_seq"


def _record_run(project, run_id: str) -> None:
    run_dir = project.root / ".zddv" / "runs" / run_id
    run_dir.mkdir(parents=True)
    log = run_dir / "simulation.log"
    log.write_text("simulation complete\n", encoding="utf-8")
    record_run(
        project,
        {
            "run_id": run_id,
            "created_at": "2026-09-22T06:00:00+00:00",
            "project": project.name,
            "simulator": "questa",
            "simulator_version": "Questa test",
            "top": "tb_top",
            "test": "sequence_case",
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


def test_cli_sequence_analysis_history_and_run_correlation(tmp_path: Path, capsys):
    project = initialize_project(tmp_path / "demo")
    _record_run(project, "run-seq")
    trace = project.root / "sequence.json"
    trace.write_text(
        json.dumps(
            {
                "events": [
                    _event("seq-1", "cli_seq", "UVM_BODY"),
                    _event("seq-1", "cli_seq", "UVM_ENDED"),
                    _event("seq-1", "cli_seq", "UVM_POST_START"),
                    _event("seq-1", "cli_seq", "UVM_FINISHED"),
                ]
            }
        ),
        encoding="utf-8",
    )

    rc = main(
        [
            "--project",
            str(project.root),
            "uvm-sequence-analyze",
            str(trace),
            "--run",
            "run-seq",
        ]
    )
    assert rc == 0
    output = capsys.readouterr().out
    assert "UVM SEQUENCE PASS" in output
    assert "finished=1 stopped=0 active=0" in output
    assert "Run: run-seq" in output

    rc = main(
        [
            "--project",
            str(project.root),
            "uvm-sequence-history",
            "--run",
            "run-seq",
        ]
    )
    assert rc == 0
    history = capsys.readouterr().out
    assert "run-seq" in history
    assert "PASS" in history
