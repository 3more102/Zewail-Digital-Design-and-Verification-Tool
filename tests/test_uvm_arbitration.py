from __future__ import annotations

import json
from pathlib import Path

from zddv.config import initialize_project
from zddv.uvm_arbitration import (
    analyze_uvm_arbitration_file,
    parse_uvm_arbitration_data,
)


def _event(
    request_id: str,
    event: str,
    *,
    sequence_id: str,
    priority: int = 100,
    time: str | None = None,
) -> dict[str, object]:
    return {
        "request_id": request_id,
        "event": event,
        "sequence_id": sequence_id,
        "sequencer": "uvm_test_top.env.seqr",
        "priority": priority,
        "time": time,
    }


def test_fifo_grants_requests_in_queue_order():
    result = parse_uvm_arbitration_data(
        {
            "mode": "UVM_SEQ_ARB_FIFO",
            "events": [
                _event("r1", "REQUEST", sequence_id="seq-a"),
                _event("r2", "REQUEST", sequence_id="seq-b"),
                _event("r1", "GRANT", sequence_id="seq-a"),
                _event("r2", "GRANT", sequence_id="seq-b"),
            ],
        }
    )

    assert result["status"] == "PASS"
    assert result["summary"]["requests"] == 2
    assert result["summary"]["grants"] == 2
    assert result["summary"]["pending"] == 0
    assert result["decisions"][0]["expected_request_id"] == "r1"


def test_fifo_detects_out_of_order_grant():
    result = parse_uvm_arbitration_data(
        {
            "mode": "UVM_SEQ_ARB_FIFO",
            "events": [
                _event("r1", "REQUEST", sequence_id="seq-a"),
                _event("r2", "REQUEST", sequence_id="seq-b"),
                _event("r2", "GRANT", sequence_id="seq-b"),
            ],
        }
    )

    assert result["status"] == "FAIL"
    assert [item["code"] for item in result["violations"]] == [
        "FIFO_ORDER_VIOLATION"
    ]


def test_strict_fifo_prefers_highest_priority_then_fifo():
    result = parse_uvm_arbitration_data(
        {
            "mode": "UVM_SEQ_ARB_STRICT_FIFO",
            "events": [
                _event("r1", "REQUEST", sequence_id="seq-a", priority=100),
                _event("r2", "REQUEST", sequence_id="seq-b", priority=200),
                _event("r3", "REQUEST", sequence_id="seq-c", priority=200),
                _event("r2", "GRANT", sequence_id="seq-b", priority=200),
                _event("r3", "GRANT", sequence_id="seq-c", priority=200),
                _event("r1", "GRANT", sequence_id="seq-a", priority=100),
            ],
        }
    )

    assert result["status"] == "PASS"
    assert result["decisions"][0]["expected_request_id"] == "r2"
    assert result["decisions"][0]["highest_priority"] == 200


def test_strict_random_rejects_lower_priority_choice():
    result = parse_uvm_arbitration_data(
        {
            "mode": "UVM_SEQ_ARB_STRICT_RANDOM",
            "events": [
                _event("r1", "REQUEST", sequence_id="seq-a", priority=100),
                _event("r2", "REQUEST", sequence_id="seq-b", priority=300),
                _event("r1", "GRANT", sequence_id="seq-a", priority=100),
            ],
        }
    )

    assert result["status"] == "FAIL"
    assert result["violations"][0]["code"] == "STRICT_PRIORITY_VIOLATION"
    assert result["decisions"][0]["eligible_request_ids"] == ["r2"]


def test_random_mode_is_observational_without_fairness_policy():
    result = parse_uvm_arbitration_data(
        {
            "mode": "UVM_SEQ_ARB_RANDOM",
            "events": [
                _event("r1", "REQUEST", sequence_id="seq-a"),
                _event("r2", "REQUEST", sequence_id="seq-b"),
                _event("r2", "GRANT", sequence_id="seq-b"),
            ],
        }
    )

    assert result["status"] == "PASS"
    assert result["selection_policy"] == "observational"
    assert result["summary"]["max_bypass_observed"] == 1
    assert result["summary"]["pending"] == 1


def test_optional_fairness_policy_flags_excessive_bypass():
    result = parse_uvm_arbitration_data(
        {
            "mode": "UVM_SEQ_ARB_RANDOM",
            "max_bypass": 1,
            "events": [
                _event("r1", "REQUEST", sequence_id="seq-a"),
                _event("r2", "REQUEST", sequence_id="seq-b"),
                _event("r3", "REQUEST", sequence_id="seq-c"),
                _event("r2", "GRANT", sequence_id="seq-b"),
                _event("r3", "GRANT", sequence_id="seq-c"),
            ],
        }
    )

    assert result["status"] == "FAIL"
    assert result["summary"]["max_bypass_observed"] == 2
    assert [item["code"] for item in result["violations"]] == [
        "FAIRNESS_BYPASS_LIMIT"
    ]
    assert result["violations"][0]["request_id"] == "r1"


def test_grant_without_request_and_identity_change_are_reported():
    result = parse_uvm_arbitration_data(
        {
            "events": [
                _event("missing", "GRANT", sequence_id="seq-x"),
                _event("r1", "REQUEST", sequence_id="seq-a"),
                _event("r1", "GRANT", sequence_id="seq-b"),
            ],
        }
    )

    assert result["status"] == "FAIL"
    assert [item["code"] for item in result["violations"]] == [
        "GRANT_WITHOUT_REQUEST",
        "IDENTITY_CHANGED",
    ]


def test_analyze_writes_snapshot_and_latest_report(tmp_path: Path):
    project = initialize_project(tmp_path / "demo")
    trace = project.root / "uvm-arbitration.json"
    trace.write_text(
        json.dumps(
            {
                "source": "uvm-helper",
                "mode": "UVM_SEQ_ARB_FIFO",
                "events": [
                    _event("r1", "REQUEST", sequence_id="seq-a", time="1 ns"),
                    _event("r1", "GRANT", sequence_id="seq-a", time="2 ns"),
                ],
            }
        ),
        encoding="utf-8",
    )

    result = analyze_uvm_arbitration_file(project, trace)

    assert result["status"] == "PASS"
    assert Path(result["normalized_path"]).is_file()
    assert Path(result["report_path"]).is_file()
    payload = json.loads(Path(result["report_path"]).read_text(encoding="utf-8"))
    assert payload["analysis"] == "uvm_arbitration"
    assert payload["summary"]["grants"] == 1
