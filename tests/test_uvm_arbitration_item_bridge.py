from __future__ import annotations

import json
from pathlib import Path

from zddv.cli import main
from zddv.config import initialize_project
from zddv.storage import list_uvm_arbitration_snapshots
from zddv.uvm_arbitration import (
    analyze_uvm_arbitration_item_file,
    derive_uvm_item_arbitration_data,
    parse_uvm_arbitration_data,
)
from zddv.uvm_item import parse_uvm_item_data, parse_uvm_item_log_text


def _event(
    item_id: str,
    event: str,
    *,
    sequence_id: str | None,
    sequence: str | None,
    sequencer: str | None = "uvm_test_top.env.seqr",
    time: str | None = None,
    priority: int | None = None,
    request_order: int | None = None,
    arbitration_mode: str | None = None,
) -> dict[str, object]:
    metadata: dict[str, object] = {}
    if priority is not None:
        metadata["priority"] = priority
    if request_order is not None:
        metadata["request_order"] = request_order
    if arbitration_mode is not None:
        metadata["arbitration_mode"] = arbitration_mode
    return {
        "item_id": item_id,
        "event": event,
        "sequence_id": sequence_id,
        "sequence": sequence,
        "sequencer": sequencer,
        "item": "axi_item",
        "transaction_id": item_id,
        "time": time,
        "metadata": metadata,
    }


def _item_trace() -> dict[str, object]:
    return {
        "source": "bridge-test",
        "events": [
            _event(
                "item-b",
                "ARB_REQUEST",
                sequence_id="seq-b",
                sequence="read_seq",
                time="8 ns",
                priority=100,
                request_order=0,
            ),
            _event(
                "item-a",
                "ARB_REQUEST",
                sequence_id="seq-a",
                sequence="write_seq",
                time="9 ns",
                priority=100,
                request_order=1,
            ),
            _event(
                "item-a",
                "GRANT",
                sequence_id="seq-a",
                sequence="write_seq",
                time="10 ns",
            ),
            _event(
                "item-c",
                "ARB_REQUEST",
                sequence_id="seq-c",
                sequence="control_seq",
                time="15 ns",
                priority=50,
                request_order=2,
            ),
            _event(
                "item-c",
                "GRANT",
                sequence_id="seq-c",
                sequence="control_seq",
                time="16 ns",
            ),
            _event(
                "item-b",
                "GRANT",
                sequence_id="seq-b",
                sequence="read_seq",
                time="19 ns",
            ),
        ],
    }


def test_bridge_derives_complete_contender_sets_for_existing_fairness_core():
    item_report = parse_uvm_item_data(_item_trace())
    derived = derive_uvm_item_arbitration_data(item_report)

    assert derived["adapter"]["requests_seen"] == 3
    assert derived["adapter"]["grants_seen"] == 3
    assert derived["adapter"]["decisions_emitted"] == 3
    assert [
        [contender["request_id"] for contender in decision["contenders"]]
        for decision in derived["decisions"]
    ] == [
        ["item-b", "item-a"],
        ["item-b", "item-c"],
        ["item-b"],
    ]
    assert derived["decisions"][0]["granted_request_id"] == "item-a"
    assert derived["decisions"][0]["contenders"][0]["priority"] == 100
    assert derived["decisions"][0]["contenders"][0]["request_order"] == 0

    arbitration = parse_uvm_arbitration_data(
        {
            "source": derived["source"],
            "decisions": derived["decisions"],
        },
        fairness_bound=2,
    )
    assert arbitration["status"] == "PASS"
    assert arbitration["summary"]["decisions"] == 3
    assert arbitration["summary"]["max_wait_decisions"] == 2
    item_b = next(
        request
        for request in arbitration["requests"]
        if request["request_id"] == "item-b"
    )
    assert item_b["lost_decisions"] == 2
    assert item_b["granted"] is True


def test_bridge_reuses_fairness_bound_instead_of_implementing_a_second_policy():
    item_report = parse_uvm_item_data(_item_trace())
    derived = derive_uvm_item_arbitration_data(item_report)
    arbitration = parse_uvm_arbitration_data(
        {
            "source": derived["source"],
            "decisions": derived["decisions"],
        },
        fairness_bound=1,
    )

    assert arbitration["status"] == "FAIL"
    assert arbitration["summary"]["fairness_violations"] == 1
    assert "FAIRNESS_BOUND_EXCEEDED" in {
        violation["code"] for violation in arbitration["violations"]
    }


def test_bridge_skips_decision_when_any_pending_contender_identity_is_incomplete():
    item_report = parse_uvm_item_data(
        {
            "events": [
                _event(
                    "item-unknown",
                    "ARB_REQUEST",
                    sequence_id=None,
                    sequence=None,
                    time="1 ns",
                ),
                _event(
                    "item-a",
                    "ARB_REQUEST",
                    sequence_id="seq-a",
                    sequence="write_seq",
                    time="2 ns",
                ),
                _event(
                    "item-a",
                    "GRANT",
                    sequence_id="seq-a",
                    sequence="write_seq",
                    time="3 ns",
                ),
            ]
        }
    )
    derived = derive_uvm_item_arbitration_data(item_report)

    assert derived["decisions"] == []
    assert derived["adapter"]["incomplete_requests"] == 1
    assert derived["adapter"]["skipped_incomplete_decisions"] == 1
    assert derived["adapter"]["pending_requests"] == 1


def test_bridge_preserves_explicit_log_line_provenance():
    events = _item_trace()["events"]
    text = "\n".join(
        "ZDDV_UVM_ITEM " + json.dumps(event, separators=(",", ":"))
        for event in events
    )
    item_report = parse_uvm_item_log_text(text, source="marker-bridge")
    derived = derive_uvm_item_arbitration_data(item_report)

    assert derived["decisions"][0]["contenders"][0]["metadata"]["log_line"] == 1
    assert derived["decisions"][0]["metadata"]["grant_log_line"] == 3


def test_bridge_propagates_policy_only_when_explicitly_instrumented():
    payload = _item_trace()
    events = payload["events"]
    assert isinstance(events, list)
    events[2]["metadata"]["arbitration_mode"] = "UVM_SEQ_ARB_FIFO"

    item_report = parse_uvm_item_data(payload)
    derived = derive_uvm_item_arbitration_data(item_report)
    arbitration = parse_uvm_arbitration_data(
        {
            "source": derived["source"],
            "decisions": derived["decisions"],
        }
    )

    assert arbitration["status"] == "FAIL"
    assert arbitration["decisions"][0]["mode"] == "UVM_SEQ_ARB_FIFO"
    assert arbitration["decisions"][0]["policy_check"]["checked"] is True
    assert "FIFO_ORDER_MISMATCH" in {
        violation["code"] for violation in arbitration["violations"]
    }
    assert arbitration["decisions"][1]["mode"] == "UNSPECIFIED"
    assert arbitration["decisions"][1]["policy_check"]["checked"] is False


def test_analyze_item_trace_bridge_persists_existing_arbitration_history(tmp_path: Path):
    project = initialize_project(tmp_path / "demo")
    trace = project.root / "item-arbitration.json"
    trace.write_text(json.dumps(_item_trace()), encoding="utf-8")

    result = analyze_uvm_arbitration_item_file(
        project,
        trace,
        fairness_bound=2,
    )

    assert result["status"] == "PASS"
    assert result["input_mode"] == "uvm-item-trace-bridge"
    assert result["adapter"]["decisions_emitted"] == 3
    assert result["input_path"] == str(trace.resolve())
    assert Path(result["report_path"]).is_file()
    rows = list_uvm_arbitration_snapshots(project, limit=10)
    assert len(rows) == 1
    assert rows[0]["snapshot_id"] == result["snapshot_id"]
    assert rows[0]["decision_count"] == 3
    assert rows[0]["max_wait_decisions"] == 2


def test_cli_item_trace_bridge_uses_existing_fairness_violation_path(
    tmp_path: Path,
    capsys,
):
    project = initialize_project(tmp_path / "demo")
    trace = project.root / "item-arbitration.json"
    trace.write_text(json.dumps(_item_trace()), encoding="utf-8")

    rc = main(
        [
            "--project",
            str(project.root),
            "uvm-arbitration-analyze",
            str(trace),
            "--item-trace",
            "--fairness-bound",
            "1",
        ]
    )

    assert rc == 1
    output = capsys.readouterr().out
    assert "UVM ARBITRATION FAIL" in output
    assert "Item bridge:" in output
    assert "decisions=3" in output
    assert "FAIRNESS_BOUND_EXCEEDED" in output
