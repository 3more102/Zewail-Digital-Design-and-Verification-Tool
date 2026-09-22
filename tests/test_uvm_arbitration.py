from __future__ import annotations

import json
from pathlib import Path

from zddv.cli import main
from zddv.config import initialize_project
from zddv.uvm_arbitration import (
    analyze_uvm_arbitration_file,
    parse_uvm_arbitration_data,
)


def _event(
    request_id: str,
    event: str,
    *,
    sequence_id: str = "seq-a",
    sequence: str = "traffic_seq",
    sequencer: str = "env.agent.seqr",
    item_id: str | None = None,
    priority: int | None = 100,
    lock_request: bool | None = False,
    time: str | None = None,
) -> dict:
    return {
        "request_id": request_id,
        "event": event,
        "sequence_id": sequence_id,
        "sequence": sequence,
        "sequencer": sequencer,
        "item_id": item_id,
        "priority": priority,
        "lock_request": lock_request,
        "time": time,
    }


def test_reconstructs_observed_contention_windows():
    result = parse_uvm_arbitration_data(
        {
            "source": "uvm-helper",
            "arbitration_mode": "UVM_SEQ_ARB_FIFO",
            "events": [
                _event("req-a", "WAIT_FOR_GRANT", sequence_id="seq-a", time="10 ns"),
                _event("req-b", "WAIT_FOR_GRANT", sequence_id="seq-b", time="10 ns"),
                _event("req-b", "GRANT", sequence_id="seq-b", time="11 ns"),
                _event(
                    "req-b",
                    "SEND_REQUEST",
                    sequence_id="seq-b",
                    item_id="item-b",
                    time="11 ns",
                ),
                _event(
                    "req-b",
                    "ITEM_DONE",
                    sequence_id="seq-b",
                    item_id="item-b",
                    time="20 ns",
                ),
                _event("req-a", "GRANT", sequence_id="seq-a", time="21 ns"),
                _event(
                    "req-a",
                    "SEND_REQUEST",
                    sequence_id="seq-a",
                    item_id="item-a",
                    time="21 ns",
                ),
                _event(
                    "req-a",
                    "ITEM_DONE",
                    sequence_id="seq-a",
                    item_id="item-a",
                    time="30 ns",
                ),
            ],
        }
    )

    assert result["status"] == "PASS"
    assert result["summary"]["requests"] == 2
    assert result["summary"]["grant_windows"] == 2
    assert result["summary"]["contended_grants"] == 1
    assert result["summary"]["max_observed_contenders"] == 2
    assert result["summary"]["pending"] == 0

    first, second = result["grant_windows"]
    assert first["request_id"] == "req-b"
    assert first["contenders"] == ["req-a", "req-b"]
    assert first["contender_count"] == 2
    assert first["contended"] is True
    assert first["event_distance"] == 1

    assert second["request_id"] == "req-a"
    assert second["contenders"] == ["req-a"]
    assert second["contender_count"] == 1
    assert second["contended"] is False


def test_send_before_grant_is_a_violation():
    result = parse_uvm_arbitration_data(
        {
            "events": [
                _event("req-a", "WAIT_FOR_GRANT"),
                _event("req-a", "SEND_REQUEST", item_id="item-a"),
            ]
        }
    )

    assert result["status"] == "FAIL"
    assert result["summary"]["violations"] == 1
    assert result["violations"][0]["code"] == "SEND_BEFORE_GRANT"
    assert result["summary"]["pending"] == 1


def test_partial_trace_starting_at_grant_is_retained():
    result = parse_uvm_arbitration_data(
        {
            "events": [
                _event("req-a", "GRANT"),
                _event("req-a", "SEND_REQUEST", item_id="item-a"),
                _event("req-a", "ITEM_DONE", item_id="item-a"),
            ]
        }
    )

    assert result["status"] == "PASS"
    assert result["summary"]["partial"] == 1
    assert result["grant_windows"][0]["request_observed"] is False
    assert result["grant_windows"][0]["contender_count"] is None


def test_duplicate_grant_fails():
    result = parse_uvm_arbitration_data(
        {
            "events": [
                _event("req-a", "WAIT_FOR_GRANT"),
                _event("req-a", "GRANT"),
                _event("req-a", "GRANT"),
            ]
        }
    )

    assert result["status"] == "FAIL"
    codes = {violation["code"] for violation in result["violations"]}
    assert "DUPLICATE_GRANT" in codes
    assert "GRANT_NOT_PENDING" in codes


def test_analyze_writes_latest_and_snapshot(tmp_path: Path):
    project = initialize_project(tmp_path / "demo")
    trace = project.root / "arbitration.json"
    trace.write_text(
        json.dumps(
            {
                "events": [
                    _event("req-a", "WAIT_FOR_GRANT"),
                    _event("req-a", "GRANT"),
                    _event("req-a", "SEND_REQUEST", item_id="item-a"),
                    _event("req-a", "ITEM_DONE", item_id="item-a"),
                ]
            }
        ),
        encoding="utf-8",
    )

    result = analyze_uvm_arbitration_file(project, trace)

    assert result["status"] == "PASS"
    assert Path(result["report_path"]).is_file()
    assert Path(result["normalized_path"]).is_file()

    payload = json.loads(Path(result["report_path"]).read_text(encoding="utf-8"))
    assert payload["analysis"] == "uvm_sequence_item_arbitration"
    assert payload["summary"]["granted"] == 1
    assert payload["summary"]["completed"] == 1


def test_cli_uvm_arbitration_analyze(tmp_path: Path, capsys):
    project = initialize_project(tmp_path / "demo")
    trace = project.root / "arbitration-cli.json"
    trace.write_text(
        json.dumps(
            {
                "events": [
                    _event("req-a", "WAIT_FOR_GRANT"),
                    _event("req-a", "GRANT"),
                    _event("req-a", "SEND_REQUEST", item_id="item-a"),
                    _event("req-a", "ITEM_DONE", item_id="item-a"),
                ]
            }
        ),
        encoding="utf-8",
    )

    rc = main(
        [
            "--project",
            str(project.root),
            "uvm-arbitration-analyze",
            str(trace),
        ]
    )

    assert rc == 0
    output = capsys.readouterr().out
    assert "UVM ARBITRATION PASS" in output
    assert "contended-grants=0" in output
