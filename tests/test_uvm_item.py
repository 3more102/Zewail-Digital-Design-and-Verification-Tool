from __future__ import annotations

import json
from pathlib import Path

from zddv.cli import main
from zddv.config import initialize_project
from zddv.storage import (
    list_uvm_item_handshake_events,
    list_uvm_item_handshake_snapshots,
    record_run,
)
from zddv.uvm_item import (
    analyze_uvm_item_file,
    parse_uvm_item_data,
    parse_uvm_item_log_text,
)


def _event(
    item_id: str,
    event: str,
    *,
    sequence_id: str = "seq-1",
    sequence: str = "axi_write_seq",
    sequencer: str = "uvm_test_top.env.seqr",
    item: str = "axi_item",
    transaction_id: int | str = 7,
    time: str | None = None,
) -> dict[str, object]:
    return {
        "item_id": item_id,
        "event": event,
        "sequence_id": sequence_id,
        "sequence": sequence,
        "sequencer": sequencer,
        "item": item,
        "transaction_id": transaction_id,
        "time": time,
    }


def test_parses_complete_item_handshake_with_optional_response():
    result = parse_uvm_item_data(
        {
            "source": "unit-test",
            "events": [
                _event("item-1", "GRANT"),
                _event("item-1", "REQUEST"),
                _event("item-1", "ITEM_DONE"),
                _event("item-1", "RESPONSE"),
                _event("item-2", "GRANT", transaction_id=8),
                _event("item-2", "REQUEST", transaction_id=8),
                _event("item-2", "ITEM_DONE", transaction_id=8),
            ],
        }
    )

    assert result["status"] == "PASS"
    assert result["source"] == "unit-test"
    assert result["summary"] == {
        "items": 2,
        "events": 7,
        "violations": 0,
        "granted": 2,
        "requested": 2,
        "completed": 2,
        "responded": 1,
        "active": 0,
        "partial": 0,
    }


def test_accepts_partial_trace_without_inventing_failure():
    result = parse_uvm_item_data(
        {
            "events": [
                _event("item-1", "REQUEST"),
                _event("item-1", "ITEM_DONE"),
                _event("item-2", "ITEM_DONE", transaction_id=8),
            ]
        }
    )

    assert result["status"] == "PASS"
    assert result["summary"]["completed"] == 2
    assert result["summary"]["partial"] == 2
    assert all(item["partial"] for item in result["items"])


def test_detects_backward_and_duplicate_handshake_evidence():
    result = parse_uvm_item_data(
        {
            "events": [
                _event("done-early", "GRANT"),
                _event("done-early", "ITEM_DONE"),
                _event("rsp-early", "GRANT", transaction_id=8),
                _event("rsp-early", "RESPONSE", transaction_id=8),
                _event("late-grant", "REQUEST", transaction_id=9),
                _event("late-grant", "GRANT", transaction_id=9),
                _event("dup-request", "GRANT", transaction_id=10),
                _event("dup-request", "REQUEST", transaction_id=10),
                _event("dup-request", "REQUEST", transaction_id=10),
            ]
        }
    )

    assert result["status"] == "FAIL"
    assert [item["code"] for item in result["violations"]] == [
        "ITEM_DONE_BEFORE_REQUEST",
        "RESPONSE_BEFORE_REQUEST",
        "LATE_GRANT",
        "DUPLICATE_REQUEST",
    ]


def test_detects_item_identity_changes():
    result = parse_uvm_item_data(
        {
            "events": [
                _event("item-1", "GRANT", transaction_id=11),
                _event(
                    "item-1",
                    "REQUEST",
                    sequence_id="seq-other",
                    transaction_id=12,
                ),
            ]
        }
    )

    assert result["status"] == "FAIL"
    assert [item["code"] for item in result["violations"]] == [
        "IDENTITY_CHANGED",
        "IDENTITY_CHANGED",
    ]


def test_rejects_unknown_item_event():
    try:
        parse_uvm_item_data({"events": [_event("item-1", "NOT_AN_EVENT")]})
    except ValueError as exc:
        assert "event must be one of" in str(exc)
    else:
        raise AssertionError("Expected ValueError for unknown UVM item event")


def test_analyze_writes_snapshot_and_latest_report(tmp_path: Path):
    project = initialize_project(tmp_path / "demo")
    trace = project.root / "items.json"
    trace.write_text(
        json.dumps(
            {
                "source": "uvm-helper",
                "events": [
                    _event("item-1", "GRANT", time="1 ns"),
                    _event("item-1", "REQUEST", time="1 ns"),
                    _event("item-1", "ITEM_DONE", time="8 ns"),
                ],
            }
        ),
        encoding="utf-8",
    )

    result = analyze_uvm_item_file(project, trace)

    assert result["status"] == "PASS"
    assert Path(result["normalized_path"]).is_file()
    assert Path(result["report_path"]).is_file()
    payload = json.loads(Path(result["report_path"]).read_text(encoding="utf-8"))
    assert payload["analysis"] == "uvm_item_handshake"
    assert payload["summary"]["completed"] == 1

    snapshots = list_uvm_item_handshake_snapshots(project, limit=10)
    assert len(snapshots) == 1
    assert snapshots[0]["snapshot_id"] == result["snapshot_id"]
    assert snapshots[0]["item_count"] == 1
    assert snapshots[0]["completed_count"] == 1

    events = list_uvm_item_handshake_events(project, result["snapshot_id"])
    assert len(events) == 3
    assert events[1]["event"] == "REQUEST"
    assert events[1]["item_id"] == "item-1"


def test_cli_analyzes_item_handshake_trace(tmp_path: Path):
    project = initialize_project(tmp_path / "demo")
    trace = project.root / "items.json"
    trace.write_text(
        json.dumps(
            {
                "events": [
                    _event("item-1", "GRANT"),
                    _event("item-1", "REQUEST"),
                    _event("item-1", "ITEM_DONE"),
                ]
            }
        ),
        encoding="utf-8",
    )

    rc = main(
        [
            "--project",
            str(project.root),
            "uvm-item-analyze",
            str(trace),
            "--source",
            "cli-test",
        ]
    )

    assert rc == 0
    latest = project.root / ".zddv" / "uvm" / "items" / "latest.json"
    payload = json.loads(latest.read_text(encoding="utf-8"))
    assert payload["source"] == "cli-test"
    assert payload["summary"]["violations"] == 0


def _record_run(project, run_id: str) -> None:
    run_dir = project.root / ".zddv" / "runs" / run_id
    run_dir.mkdir(parents=True)
    log = run_dir / "simulation.log"
    log.write_text("simulation complete\n", encoding="utf-8")
    record_run(
        project,
        {
            "run_id": run_id,
            "created_at": "2026-09-22T09:00:00+00:00",
            "project": project.name,
            "simulator": "questa",
            "simulator_version": "Questa test",
            "top": "tb_top",
            "test": "item_case",
            "seed": 29,
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


def test_cli_item_analysis_history_and_run_correlation(tmp_path: Path, capsys):
    project = initialize_project(tmp_path / "demo")
    _record_run(project, "run-item")
    trace = project.root / "items.json"
    trace.write_text(
        json.dumps(
            {
                "events": [
                    _event("item-1", "GRANT"),
                    _event("item-1", "REQUEST"),
                    _event("item-1", "ITEM_DONE"),
                ]
            }
        ),
        encoding="utf-8",
    )

    rc = main(
        [
            "--project",
            str(project.root),
            "uvm-item-analyze",
            str(trace),
            "--run",
            "run-item",
        ]
    )
    assert rc == 0
    output = capsys.readouterr().out
    assert "UVM ITEM PASS" in output
    assert "completed=1" in output
    assert "Run: run-item" in output

    rows = list_uvm_item_handshake_snapshots(
        project,
        limit=10,
        run_id="run-item",
    )
    assert len(rows) == 1
    assert rows[0]["run_id"] == "run-item"

    rc = main(
        [
            "--project",
            str(project.root),
            "uvm-item-history",
            "--run",
            "run-item",
        ]
    )
    assert rc == 0
    history = capsys.readouterr().out
    assert "run-item" in history
    assert "PASS" in history
    assert "1/1/1/0" in history



def test_parses_explicit_uvm_item_report_messages():
    result = parse_uvm_item_log_text(
        "\n".join(
            [
                "UVM_INFO @ 10 ns: uvm_test_top.env.seqr@@axi_write_seq [ZDDV_ITEM] event=GRANT item_id=item-1 sequence_id=seq-1 sequence=axi_write_seq sequencer=uvm_test_top.env.seqr item=axi_item transaction_id=7 lane=3",
                "UVM_INFO @ 10 ns: uvm_test_top.env.seqr@@axi_write_seq [ZDDV_ITEM] event=REQUEST item_id=item-1 sequence_id=seq-1 sequence=axi_write_seq sequencer=uvm_test_top.env.seqr item=axi_item transaction_id=7",
                "UVM_INFO @ 20 ns: uvm_test_top.env.driver [ZDDV_ITEM] event=ITEM_DONE item_id=item-1 sequence_id=seq-1 sequence=axi_write_seq sequencer=uvm_test_top.env.seqr item=axi_item transaction_id=7",
                "UVM_INFO @ 21 ns: uvm_test_top.env.seqr@@axi_write_seq [ZDDV_ITEM] event=RESPONSE item_id=item-1 sequence_id=seq-1 sequence=axi_write_seq sequencer=uvm_test_top.env.seqr item=axi_item transaction_id=7",
            ]
        ),
        source="unit-log",
    )

    assert result["status"] == "PASS"
    assert result["summary"]["items"] == 1
    assert result["summary"]["events"] == 4
    assert result["adapter"]["report_id"] == "ZDDV_ITEM"
    assert result["adapter"]["matched_messages"] == 4
    assert result["events"][0]["time"] == "10 ns"
    assert result["events"][0]["metadata"]["lane"] == "3"
    assert result["events"][0]["metadata"]["log_line"] == 1
    assert result["events"][2]["metadata"]["uvm_component"] == "uvm_test_top.env.driver"


def test_uvm_item_report_adapter_rejects_missing_required_fields():
    try:
        parse_uvm_item_log_text(
            "UVM_INFO @ 1 ns: uvm_test_top.env.seqr [ZDDV_ITEM] event=GRANT"
        )
    except ValueError as exc:
        assert "event=<...> and item_id=<...>" in str(exc)
    else:
        raise AssertionError("Expected ValueError for incomplete ZDDV_ITEM report")


def test_cli_uvm_item_log_uses_recorded_run_log(tmp_path: Path, capsys):
    project = initialize_project(tmp_path / "demo")
    _record_run(project, "run-item-log")
    log = project.root / ".zddv" / "runs" / "run-item-log" / "simulation.log"
    log.write_text(
        "\n".join(
            [
                "UVM_INFO @ 2 ns: uvm_test_top.env.seqr@@smoke_seq [ZDDV_ITEM] event=GRANT item_id=req-1 sequence_id=seq-9 sequence=smoke_seq sequencer=uvm_test_top.env.seqr item=req transaction_id=19",
                "UVM_INFO @ 2 ns: uvm_test_top.env.seqr@@smoke_seq [ZDDV_ITEM] event=REQUEST item_id=req-1 sequence_id=seq-9 sequence=smoke_seq sequencer=uvm_test_top.env.seqr item=req transaction_id=19",
                "UVM_INFO @ 8 ns: uvm_test_top.env.driver [ZDDV_ITEM] event=ITEM_DONE item_id=req-1 sequence_id=seq-9 sequence=smoke_seq sequencer=uvm_test_top.env.seqr item=req transaction_id=19",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    rc = main(
        [
            "--project",
            str(project.root),
            "uvm-item-log",
            "--run",
            "run-item-log",
        ]
    )

    assert rc == 0
    output = capsys.readouterr().out
    assert "UVM ITEM PASS" in output
    assert "Adapter: uvm_report_id report-id=ZDDV_ITEM matched=3" in output
    assert "Run: run-item-log" in output

    rows = list_uvm_item_handshake_snapshots(
        project,
        limit=10,
        run_id="run-item-log",
    )
    assert len(rows) == 1
    assert rows[0]["item_count"] == 1
    assert rows[0]["completed_count"] == 1
