from __future__ import annotations

import json
from pathlib import Path

from zddv.cli import main
from zddv.config import initialize_project
from zddv.storage import (
    list_uvm_item_handshake_events,
    list_uvm_item_handshake_snapshots,
)
from zddv.uvm_item import analyze_uvm_item_file, parse_uvm_item_data


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


def test_analyze_persists_item_snapshot_and_events(tmp_path: Path):
    project = initialize_project(tmp_path / "demo")
    trace = project.root / "items.json"
    trace.write_text(
        json.dumps(
            {
                "source": "sqlite-test",
                "events": [
                    _event("item-1", "GRANT", transaction_id=41, time="2 ns"),
                    _event("item-1", "REQUEST", transaction_id=41, time="3 ns"),
                    _event("item-1", "ITEM_DONE", transaction_id=41, time="9 ns"),
                    _event("item-2", "REQUEST", transaction_id=42, time="10 ns"),
                ],
            }
        ),
        encoding="utf-8",
    )

    result = analyze_uvm_item_file(project, trace)

    rows = list_uvm_item_handshake_snapshots(project)
    assert len(rows) == 1
    row = rows[0]
    assert row["snapshot_id"] == result["snapshot_id"]
    assert row["status"] == "PASS"
    assert row["item_count"] == 2
    assert row["event_count"] == 4
    assert row["completed_count"] == 1
    assert row["partial_count"] == 1

    events = list_uvm_item_handshake_events(project, result["snapshot_id"])
    assert [event["event"] for event in events] == [
        "GRANT",
        "REQUEST",
        "ITEM_DONE",
        "REQUEST",
    ]
    assert events[0]["transaction_id"] == "41"
    assert list_uvm_item_handshake_events(
        project,
        result["snapshot_id"],
        item_id="item-2",
    )[0]["event"] == "REQUEST"


def test_cli_lists_persisted_item_history(tmp_path: Path, capsys):
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
    analyze_uvm_item_file(project, trace)

    rc = main(
        [
            "--project",
            str(project.root),
            "uvm-item-history",
            "--status",
            "PASS",
            "--limit",
            "5",
        ]
    )

    assert rc == 0
    output = capsys.readouterr().out
    assert "STATUS" in output
    assert "PASS" in output
    assert "1/1/1/0" in output

